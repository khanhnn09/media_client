"""Làn upscale 1080p chạy SONG SONG với vòng task VEO (2026-09-30).

Theo yêu cầu user: *"coi upscale là luồng chạy song song ko ảnh hưởng luồng
video có thì cứ nhận"* + *"nếu cùng project cứ gửi api ngay trong tab đó"*.

Trước đây `worker._process_upscale_jobs()` chạy TUẦN TỰ sau mỗi lô task video,
còn phải điều hướng tab VEO sang project Flow chứa video — task video mới phải
chờ upscale xong và ngược lại.

Giờ upscale là 1 thread riêng, điều khiển trình duyệt qua websocket CDP (giống
làn Omni, xem `cdp_tab.py`) nên không bao giờ tranh phiên Selenium của VEO:
  - Video nằm ĐÚNG project Flow tab VEO đang đứng → gửi API ngay trong tab VEO
    (1 client CDP thứ 2 gắn vào cùng tab; chỉ `Runtime.evaluate`, KHÔNG điều
    hướng, KHÔNG tải lại — không ảnh hưởng gì task đang render).
  - Khác project (hoặc tab VEO chưa có session batchexecute / reCAPTCHA bị bẫy)
    → mở 1 tab Flow PHỤ riêng cho upscale, đứng ở project chứa video.

Gửi lệnh cách nhau `upscale_gap_secs`, poll `jwpduf` 10s/lần, xong thì
`as29s` lấy URL → `POST /task/<id>/upscale_result` (backend tải 1080p, xoá 720p).
Heartbeat vẫn là của vòng VEO (kể cả lúc chỉ giữ kết nối), làn này không tự heartbeat.
"""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import unquote

from . import flow_be
from .cdp_tab import CdpTab, CdpError, create_target, close_target, debug_port_of
from .config import FLOW_SERVER

POLL_SECS = 10
JOB_TIMEOUT_SECS = 900
# Nhận thêm việc chỉ khi hàng đợi + đang chạy dưới mức này. Backend đánh dấu
# việc 'processing' ngay lúc giao và thu hồi sau 30 phút — ôm quá nhiều sẽ bị
# giao lại trùng trước khi kịp làm.
MAX_BACKLOG = 10
SESSION_TTL_SECS = 1200
SESSION_WAIT_SECS = 25

_PROJECT_RE = re.compile(r'/project/([^/?#]+)')


def _async_expr(body_js: str, args: list) -> str:
    """Bọc đoạn JS kiểu `execute_async_script` (đọc `arguments`, gọi callback
    cuối) thành 1 Promise để chạy qua CDP `Runtime.evaluate(awaitPromise)`."""
    return ('new Promise(function(__done){(function(){%s}).apply(null, %s.concat([__done]));})'
            % (body_js, json.dumps(args)))


class _Ctx:
    """1 tab dùng để gọi API: tab VEO (dùng chung) hoặc tab phụ của làn."""

    def __init__(self, kind: str, tab: CdpTab, project_id: str, session: dict):
        self.kind = kind            # 'veo' | 'own'
        self.tab = tab
        self.project_id = project_id
        self.session = session
        self.tiles: dict = {}
        self.tiles_at = 0.0


class UpscaleLane:
    def __init__(self, worker):
        self.w = worker
        from .chrome_utils import _chrome_debug_port
        self.port = debug_port_of(worker.driver, _chrome_debug_port(worker.profile_id))
        # Gọi ở LUỒNG CHÍNH (có Selenium) — lấy handle tab VEO 1 lần.
        self.veo_handle = worker.driver.current_window_handle
        self._veo_tab: CdpTab | None = None
        self._veo_broken_until = 0.0     # tab VEO dùng không được (reCAPTCHA bẫy…) → tạm dùng tab phụ
        self._own_tab: CdpTab | None = None
        self._own_target = None
        self._own_pid = ''
        self._own_session = None
        self._own_session_at = 0.0
        self._lock = threading.Lock()
        self._queue: list = []
        self._running: list = []         # [{job, up, t0, ctx}]
        self._ids: set = set()
        self._sending = False
        self._halt = threading.Event()
        self._thread = None
        self._last_send = 0.0

    # ── trạng thái cho worker / dispatcher ──────────────────────────────────
    @property
    def busy(self) -> bool:
        return bool(self._queue or self._running or self._sending)

    def can_accept(self) -> bool:
        return len(self._ids) < MAX_BACKLOG

    def _log(self, level, msg):
        self.w._log(level, f'[upscale] {msg}')

    def _stopped(self) -> bool:
        return self.w._stop.is_set() or self._halt.is_set()

    # ── nhận việc (gọi từ heartbeat của vòng VEO) ───────────────────────────
    def add_jobs(self, jobs: list):
        with self._lock:
            new = [j for j in jobs if j.get('taskId') and j['taskId'] not in self._ids]
            for j in new:
                self._ids.add(j['taskId'])
                self._queue.append(j)
        if not new:
            return
        self._log('info', f'← Nhận {len(new)} việc upscale 1080p: '
                          + ', '.join(f"#{j['taskId']}" for j in new))
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name=f'upscale-{self.w.profile_id}')
            self._thread.start()

    def stop(self):
        self._halt.set()
        for tab in (self._veo_tab, self._own_tab):
            if tab:
                tab.close()
        if self._own_target:
            close_target(self.port, self._own_target)
            self._own_target = None

    # ── vòng lặp ───────────────────────────────────────────────────────────
    def _loop(self):
        gap = 10.0
        last_poll = 0.0
        while not self._stopped():
            try:
                gap = float(self.w._server_settings.get('upscale_gap_secs', 10) or 10)
            except Exception:
                pass
            now = time.time()
            if self._queue and now - self._last_send >= gap:
                with self._lock:
                    job = self._queue.pop(0) if self._queue else None
                if job:
                    self._sending = True
                    try:
                        self._send(job)
                    finally:
                        self._sending = False
                    self._last_send = time.time()
            if self._running and time.time() - last_poll >= POLL_SECS:
                last_poll = time.time()
                self._poll()
            self._halt.wait(1)
        for r in list(self._running):
            self._fail(r['job'], 'worker dừng giữa lúc upscale')
        for j in list(self._queue):
            self._fail(j, 'worker dừng trước khi kịp upscale')

    # ── báo kết quả ────────────────────────────────────────────────────────
    def _done(self, job):
        with self._lock:
            self._ids.discard(job.get('taskId'))

    def _report(self, task_id, **body):
        try:
            return self.w._req('POST', f'{FLOW_SERVER}/api/media/task/{task_id}/upscale_result',
                               body={'machineCode': self.w.machine_code, **body})
        except Exception as e:
            self._log('warn', f'#{task_id}: gửi kết quả về server lỗi: {e}')
            return None

    def _fail(self, job, msg):
        self._done(job)
        self._log('warn', f'✗ Upscale #{job["taskId"]} {job.get("nameId") or ""}: {msg}')
        self._report(job['taskId'], error=msg)

    # ── JS qua CDP ─────────────────────────────────────────────────────────
    def _be_call(self, ctx: _Ctx, rpc_id: str, args: list, timeout: int = 30):
        from .worker import BE_CALL_JS
        f_req = json.dumps([[[rpc_id, json.dumps(args), None, 'generic']]])
        s = ctx.session
        res = ctx.tab.evaluate(_async_expr(BE_CALL_JS, [f_req, s['at'], s['bl'], s['sid'], rpc_id]),
                               timeout=timeout + 5)
        if not res or not res.get('ok'):
            raise RuntimeError(f'RPC {rpc_id} lỗi mạng: {(res or {}).get("error")}')
        if res.get('status') != 200:
            raise RuntimeError(f'RPC {rpc_id} trả status {res.get("status")}')
        return self.w._batchexecute_parse(res.get('body'), rpc_id)

    def _captcha(self, ctx: _Ctx) -> str:
        from .worker import RECAPTCHA_FETCH_JS, LABS_RECAPTCHA_SITE_KEY
        info = ctx.tab.evaluate(_async_expr(RECAPTCHA_FETCH_JS,
                                            [LABS_RECAPTCHA_SITE_KEY, 30000, 'VIDEO_GENERATION']),
                                timeout=45) or {}
        token = info.get('token') or ''
        if info.get('error') == 'RC_TRAPPED' and ctx.kind == 'veo':
            # Tab VEO không được tải lại từ đây — chuyển sang tab phụ (có guard).
            self._veo_broken_until = time.time() + 600
        if not token:
            raise RuntimeError(f'không lấy được reCAPTCHA ({info.get("error") or "rỗng"})')
        return token

    @staticmethod
    def _session_from_calls(sample) -> dict | None:
        if not sample:
            return None
        url, req_body = sample.get('url') or '', sample.get('reqBody') or ''
        m_bl = re.search(r'[?&]bl=([^&]+)', url)
        m_sid = re.search(r'[?&]f\.sid=([^&]+)', url)
        m_at = re.search(r'(?:^|&)at=([^&]+)', req_body)
        if not (m_bl and m_sid and m_at):
            return None
        return {'bl': unquote(m_bl.group(1)), 'sid': unquote(m_sid.group(1)),
                'at': unquote(m_at.group(1))}

    def _read_session(self, tab: CdpTab) -> dict | None:
        try:
            sample = tab.evaluate('(window.__beCalls && window.__beCalls.length) '
                                  '? window.__beCalls[window.__beCalls.length - 1] : null',
                                  timeout=10, await_promise=False)
        except CdpError:
            return None
        return self._session_from_calls(sample)

    # ── chọn tab ───────────────────────────────────────────────────────────
    def _veo_ctx(self, fid: str) -> _Ctx | None:
        """Tab VEO nếu nó đang đứng đúng project `fid` và đã có session."""
        if time.time() < self._veo_broken_until:
            return None
        try:
            if not self._veo_tab:
                self._veo_tab = CdpTab(self.port, self.veo_handle).connect()
            href = self._veo_tab.url()
        except Exception:
            self._veo_tab = None
            return None
        m = _PROJECT_RE.search(href or '')
        pid = (m.group(1) if m else '').lower()
        if not pid or (fid and pid != fid):
            return None
        sess = self._read_session(self._veo_tab)
        if not sess:
            cached = getattr(self.w, '_be_session_cache', None)
            if cached and time.time() - cached.get('_at', 0) < SESSION_TTL_SECS:
                sess = cached
        if not sess:
            return None
        return _Ctx('veo', self._veo_tab, pid, sess)

    def _own_ctx(self, fid: str) -> _Ctx | None:
        """Tab phụ của làn, đứng ở project `fid`. Không đổi project khi tab phụ
        còn việc đang chạy ở project khác (trả None → việc chờ lượt sau)."""
        from .worker import BE_INTERCEPTOR_JS, RECAPTCHA_GUARD_JS
        if not fid:
            return None
        if self._own_pid and self._own_pid != fid and any(
                r['ctx'].kind == 'own' for r in self._running):
            return None
        if not self._own_tab:
            self._own_target = create_target(self.port, 'about:blank')
            self._own_tab = CdpTab(self.port, self._own_target).connect()
            self._own_tab.send('Page.enable')
            for src in (RECAPTCHA_GUARD_JS, BE_INTERCEPTOR_JS):
                self._own_tab.send('Page.addScriptToEvaluateOnNewDocument', {'source': src})
            self._own_pid = ''
            self._log('info', 'Mở tab Flow phụ cho upscale (video khác project với tab VEO)')
        fresh = time.time() - self._own_session_at < SESSION_TTL_SECS
        if self._own_pid != fid or not self._own_session or not fresh:
            url = self._own_tab.navigate(f'https://flow.google.com/project/{fid}', wait=40)
            if 'accounts.google.com' in url:
                raise RuntimeError('tab phụ bị đá ra trang đăng nhập Google')
            if fid not in (url or '').lower():
                raise RuntimeError(f'không vào được project Flow {fid}')
            self._own_pid, self._own_session = fid, None
            deadline = time.time() + SESSION_WAIT_SECS
            while time.time() < deadline and not self._own_session:
                self._own_session = self._read_session(self._own_tab)
                if not self._own_session:
                    time.sleep(1)
            if not self._own_session:
                raise RuntimeError('tab phụ không bắt được session batchexecute')
            self._own_session_at = time.time()
        return _Ctx('own', self._own_tab, fid, self._own_session)

    def _ctx_for(self, fid: str) -> _Ctx | None:
        ctx = self._veo_ctx(fid)
        if ctx:
            return ctx
        if not fid:
            raise RuntimeError('video không có id project Flow và tab VEO không dùng được')
        return self._own_ctx(fid)

    def _tile_of(self, ctx: _Ctx, name: str) -> str:
        """uuid media (meta[4]) → tile id (entry[0]) — p0UkFb cần cả 2."""
        for attempt in (0, 1):
            if attempt or not ctx.tiles or time.time() - ctx.tiles_at > 60:
                data = self._be_call(ctx, 'Zzl0ze',
                                     [f'projects/{ctx.project_id}', None, None, None, [1]])
                ctx.tiles = {}
                for entry in (((data or [None, []])[1]) or []):
                    try:
                        meta = entry[3]
                        if isinstance(meta, list) and len(meta) > 4 and meta[4]:
                            ctx.tiles[meta[4]] = entry[0]
                    except Exception:
                        continue
                ctx.tiles_at = time.time()
            if name in ctx.tiles:
                return ctx.tiles[name]
        return ''

    # ── gửi / poll / hoàn tất ──────────────────────────────────────────────
    def _send(self, job: dict):
        fid = str(job.get('flowProjectId') or '').lower()
        name = job.get('name') or ''
        try:
            ctx = self._ctx_for(fid)
            if ctx is None:
                # Tab phụ đang bận project khác — để việc này chờ lượt sau.
                with self._lock:
                    self._queue.append(job)
                return
            tile = self._tile_of(ctx, name)
            if not tile:
                return self._fail(job, f'không tìm thấy video {name[:8]}… trong project Flow (đã bị xoá?)')
            captcha = self._captcha(ctx)
            args = flow_be.build_upsample_video_args(
                ctx.project_id, captcha, name, tile, aspect_ratio=job.get('aspectRatio') or '16:9',
                model_key=flow_be.UPSAMPLE_MODEL_KEYS['1080p'])
            where = 'tab VEO' if ctx.kind == 'veo' else 'tab phụ'
            self._log('info', f'▶ Upscale 1080p #{job["taskId"]} {job.get("nameId") or ""} ({where})')
            payload = self._be_call(ctx, flow_be.RPC_UPSAMPLE_VIDEO, args, timeout=60)
            up = flow_be.parse_upsample_result(payload) if payload else ''
            if not up:
                return self._fail(job, f'Flow không nhận lệnh upscale — {str(payload)[:160]}')
            self._running.append({'job': job, 'up': up, 't0': time.time(), 'ctx': ctx})
        except Exception as e:
            self._fail(job, f'gửi lệnh upscale lỗi: {getattr(e, "reason", e)}')

    def _poll(self):
        groups: dict = {}
        for r in self._running:
            groups.setdefault(id(r['ctx'].tab), []).append(r)
        still = []
        for rows in groups.values():
            ctx = rows[0]['ctx']
            try:
                payload = self._be_call(ctx, flow_be.RPC_CHECK_VIDEO_STATUS,
                                        [None, None, [[r['up']] for r in rows]])
            except Exception as e:
                self._log('warn', f'poll trạng thái lỗi: {e}')
                still.extend(rows)
                continue
            for r in rows:
                st = flow_be.parse_video_status(payload, r['up']) if payload else None
                if st == 3:
                    self._finish(r)
                elif st in (None, 1, 2):
                    if time.time() - r['t0'] < JOB_TIMEOUT_SECS:
                        still.append(r)
                    else:
                        self._fail(r['job'], f'quá {JOB_TIMEOUT_SECS}s chưa upscale xong')
                else:
                    self._fail(r['job'], f'Flow báo upscale lỗi (trạng thái {st})')
        self._running = still

    def _finish(self, r: dict):
        job = r['job']
        try:
            data = self._be_call(r['ctx'], 'as29s', [r['up']])
            url = flow_be.find_video_url(data) if data else ''
            if not url:
                raise RuntimeError('không lấy được URL bản 1080p')
            res = self._report(job['taskId'], name=job.get('name'), upscaledName=r['up'],
                               url=url, resolution='1080p')
            if isinstance(res, dict) and res.get('success') is False:
                raise RuntimeError(res.get('error') or 'server không lưu được bản 1080p')
            self._done(job)
            self._log('ok', f'✔ Upscale 1080p xong #{job["taskId"]} {job.get("nameId") or ""}')
        except Exception as e:
            self._fail(job, str(e))
