"""Làn "Omni On Workspace" chạy KÈM worker VEO (2026-09-27).

Profile VEO (`worker_mode` api/dom) bật `omni_enabled` → worker mở thêm 1 tab
Google Vids (docs.google.com/videos) và chạy 1 làn nhận task RIÊNG:
  • 1 bên tab Flow chạy VEO như cũ,
  • 1 bên tab Vids chạy Omni — CHỈ nhận task VIDEO.

Làn Omni heartbeat bằng machine code RIÊNG (`selenium-<id>-omni`, taskMode
`video_only`) nên backend giao task + đếm in-flight TÁCH BIỆT với làn VEO;
2 làn cạnh tranh cùng hàng đợi `tasks_media_flow` qua `FOR UPDATE SKIP LOCKED`
sẵn có — task đã bị làn này nhận thì làn kia không nhận trùng.

Tab Omni điều khiển qua websocket CDP riêng (`server/cdp_tab.py`), KHÔNG qua
phiên Selenium của VEO — nên chạy song song thật, không phải `switch_to.window`.
Mọi request (upload ảnh / generate / tải mp4) bắn bằng `fetch()` trong trang
(xem `server/omni_be.py`). Bước DUY NHẤT cần Selenium là đăng nhập lại khi phiên
Google mất — làn chỉ bật cờ `need_relogin`, worker xử lý ở luồng chính giữa 2
lượt heartbeat VEO (`relogin_from_main_thread()`).

⚠️ Làn này KHÔNG động vào state của worker VEO (bộ đếm batch, escalation,
`_task_start`, `pm.set_status`…) — chỉ dùng chung `_log`, `_req`,
`_upload_video_result`, `pm.bump_task_stat`.
"""
from __future__ import annotations

import base64
import json
import os
import random
import threading
import time
from pathlib import Path

from . import omni_be
from .cdp_tab import CdpTab, CdpError, debug_port_of
from .config import FLOW_SERVER, POLL_INTERVAL, req_lib
from .local_settings import get_local_settings
from .managers import pm
from .run_hours import in_run_hours

# Lỗi tầng tài khoản — nghỉ lâu hơn thay vì đốt task liên tục.
_ACCOUNT_BLOCK_HINTS = ('UNUSUAL_ACTIVITY', 'RESOURCE_EXHAUSTED', 'QUOTA', 'PERMISSION_DENIED',
                        'NO_SAPISID_COOKIE', 'HTTP 403')

# (2026-09-28) Proxy XOAY đổi IP giữa chừng → kết nối đang mở bị đứt (generate
# chờ ~30-60s nên rất dễ dính). Lỗi kiểu này là TẠM THỜI: chờ proxy ổn định, nạp
# lại trang Vids rồi làm lại ngay trong làn, không báo lỗi task về server.
_TRANSIENT_HINTS = ('Failed to fetch', 'NetworkError', 'fetch error', 'ERR_', 'network',
                    'Load failed', 'HTTP 0', 'HTTP 401', 'HTTP 502', 'HTTP 503', 'HTTP 504',
                    'HTTP 500', 'không phản hồi', 'Connection', 'timed out', 'Timeout')
_MAX_TASK_RETRIES = 3          # số lần làm lại 1 task khi gặp lỗi mạng tạm thời
_RETRY_WAIT_SECS = 10          # chờ proxy xoay xong IP mới trước khi làm lại
_STEP_RETRIES = 3              # thử lại từng bước nhỏ (upload 1 ảnh, tải mp4, gửi kết quả)


def _is_transient(msg: str) -> bool:
    m = str(msg or '')
    if any(h in m for h in _ACCOUNT_BLOCK_HINTS):
        return False
    return any(h.lower() in m.lower() for h in _TRANSIENT_HINTS)
_ACCOUNT_BACKOFF_SECS = 600
# (2026-10-03) REQUEST_REFUSED (HTTP 400, espresso-pa) liên tiếp từng này lần → coi như
# Google đang chặn tài khoản/fingerprint: nghỉ + đổi fingerprint (nếu bật cài đặt
# `cloak_rotate_fp_on_block`). 1 lần lẻ thường chỉ là prompt/ảnh bị bộ lọc nội dung
# từ chối — đổi fingerprint vì 1 prompt xấu là không có tác dụng và làm mất phiên.
_REFUSED_ROTATE_AFTER = 2
_GENERATE_TIMEOUT_SECS = omni_be.GENERATE_TIMEOUT_SECS + 15   # JS tự huỷ fetch trước mốc này
_PAGE_MAX_AGE_SECS = 1800     # nạp lại trang Vids định kỳ (token docs-est có hạn)
# (2026-09-29) Task Omni lỗi → quay về trang chủ Vids tạo tài liệu MỚI, các task
# sau chạy trên tài liệu mới đó. Không tạo liên tục quá nhịp này.
_NEW_DOC_MIN_GAP_SECS = 60
_NEW_DOC_BTN_SELECTOR = '.docs-homescreen-templates-templateview'
# Lối tắt tạo tài liệu Vids trống (tương đương vids.new) — fallback cuối khi bấm nút không ăn.
_NEW_DOC_CREATE_URL = 'https://docs.google.com/videos/create'
# Tạo tài liệu mới thất bại liên tiếp ngần này lần → thôi, chạy tiếp trên tài liệu
# cũ (trước đây làn đứng im vĩnh viễn, cứ 60s lại báo lỗi tạo tài liệu).
_NEW_DOC_MAX_FAILS = 3


# Tài liệu Vids làm ngữ cảnh — BẮT BUỘC: generate text→video KHÔNG kèm
# ngữ cảnh tài liệu thì server treo không trả (đã thử thật 2026-09-27, >10 phút),
# kèm thì ~30s. Tạo 1 lần/profile rồi nhớ URL ở file cục bộ (gitignore).
_DOCS_FILE = Path(__file__).resolve().parent.parent / 'omni_docs.json'
_docs_lock = threading.Lock()


def _load_docs() -> dict:
    try:
        return json.loads(_DOCS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_doc(key: str, url: str):
    with _docs_lock:
        d = _load_docs()
        d[key] = url
        tmp = _DOCS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, _DOCS_FILE)


def _doc_key(profile: dict) -> str:
    return f'{profile.get("id")}|{(profile.get("account_email") or "").strip().lower()}'


def ensure_vids_doc(worker, log=None) -> str | None:
    """Đưa tab HIỆN TẠI của `worker.driver` vào 1 tài liệu Vids (có check đăng
    nhập như VEO). Dùng lại tài liệu đã nhớ; chưa có thì bấm "Bắt đầu một video
    mới" để tạo. Trả URL tài liệu, hoặc None nếu thất bại. Chạy ở LUỒNG CHÍNH
    (Selenium) — cũng dùng bởi tests/_test_omni_workspace.py."""
    log = log or (lambda lv, m: worker._log(lv, f'[omni] {m}'))
    d = worker.driver
    key = _doc_key(worker.profile)
    saved = _load_docs().get(key)
    if saved:
        if not worker._ensure_google_login(saved):
            return None
        time.sleep(3)
        cur = d.current_url or ''
        if '/videos/' in cur and '/d/' in cur:
            return cur
        log('warn', f'Tài liệu Vids đã nhớ không mở được ({cur[:80]}) — tạo tài liệu mới')
    if not worker._ensure_google_login(omni_be.VIDS_HOME_URL):
        return None
    btn = None
    for _ in range(20):
        btn = d.execute_script("return document.querySelector('.docs-homescreen-templates-templateview')")
        if btn:
            break
        time.sleep(1)
    if not btn:
        log('error', 'Không thấy nút "Bắt đầu một video mới" trên trang chủ Vids')
        return None
    worker._cdp_click_el(btn)
    t0 = time.time()
    while time.time() - t0 < 40:
        cur = d.current_url or ''
        if '/d/' in cur:
            _save_doc(key, cur.split('?')[0].split('#')[0])
            log('ok', f'Đã tạo tài liệu Vids làm ngữ cảnh: {cur[:90]}')
            time.sleep(5)
            return cur
        time.sleep(1)
    log('error', 'Bấm "Bắt đầu một video mới" nhưng không vào được tài liệu nào')
    return None


def omni_enabled_for(profile: dict) -> bool:
    try:
        return bool(int(profile.get('omni_enabled') or 0))
    except (TypeError, ValueError):
        return False


def veo_enabled_for(profile: dict) -> bool:
    """(2026-09-28) Tab VEO có chạy không. Chỉ tắt được khi Omni đang bật —
    tắt cả 2 thì coi như vẫn chạy VEO (không để profile đứng không)."""
    try:
        v = profile.get('veo_enabled')
        on = True if v is None else bool(int(v))
    except (TypeError, ValueError):
        on = True
    return on or not omni_enabled_for(profile)


class OmniLane:
    def __init__(self, worker):
        self.w = worker
        self.profile_id = worker.profile_id
        self.machine_code = f'{worker.machine_code}-omni'
        self.handle = None          # window handle == CDP target id
        self.tab: CdpTab | None = None
        self.need_relogin = False
        self.doc_url = None
        self._thread = None
        self._halt = threading.Event()     # dừng riêng làn (worker thoát vì ngủ…)
        self._backoff_until = 0.0
        self._page_loaded_at = 0.0
        self.done_count = 0
        self.error_count = 0
        self.inflight = 0              # số task đang tạo — dispatcher không đóng profile khi > 0
        self.need_new_doc = False      # có task lỗi → tạo tài liệu Vids mới trước lô kế tiếp
        self._refused_streak = 0       # số task liên tiếp bị REQUEST_REFUSED
        self._new_doc_fails = 0
        self._new_doc_at = 0.0

    # ── log ────────────────────────────────────────────────────────────────
    def _log(self, level, msg):
        self.w._log(level, f'[omni] {msg}')

    # ── khởi động (LUỒNG CHÍNH, có Selenium) ─────────────────────────────────
    def start(self) -> bool:
        """Mở tab Vids + check đăng nhập như VEO, rồi chạy làn ở thread riêng.
        Gọi ở luồng chính của worker, SAU khi tab VEO đã sẵn sàng."""
        d = self.w.driver
        try:
            veo_handle = d.current_window_handle
            d.switch_to.new_window('tab')
            self.doc_url = ensure_vids_doc(self.w)
            ok = bool(self.doc_url)
            self.handle = d.current_window_handle
            d.switch_to.window(veo_handle)
        except Exception as e:
            self._log('error', f'Không mở được tab Vids: {e}')
            return False
        if not ok:
            self._log('error', 'Đăng nhập Google cho tab Vids thất bại — làn Omni sẽ chờ đăng nhập lại')
            self.need_relogin = True
        port = self._real_debug_port()
        self.tab = CdpTab(port, self.handle)
        try:
            self.tab.connect()
        except Exception as e:
            self._log('error', f'Không gắn được CDP vào tab Vids (port {port}): {e} — tắt làn Omni')
            return False
        self._page_loaded_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f'omni-{self.profile_id}')
        self._thread.start()
        self._log('ok', f'Tab Vids sẵn sàng — làn Omni nhận task VIDEO ({self.machine_code})')
        return True

    def _real_debug_port(self) -> int:
        from .chrome_utils import _chrome_debug_port
        return debug_port_of(self.w.driver, _chrome_debug_port(self.profile_id))

    def relogin_from_main_thread(self):
        """Worker gọi ở luồng chính khi `need_relogin` — dùng Selenium đăng nhập lại
        trong tab Vids rồi trả driver về tab VEO."""
        d = self.w.driver
        try:
            veo_handle = d.current_window_handle
            d.switch_to.window(self.handle)
            self.doc_url = ensure_vids_doc(self.w) or self.doc_url
            ok = bool(self.doc_url) and 'accounts.google.com' not in (d.current_url or '')
            self.handle = d.current_window_handle
            d.switch_to.window(veo_handle)
        except Exception as e:
            self._log('warn', f'Đăng nhập lại tab Vids lỗi: {e}')
            return
        if ok:
            if self.tab and self.tab.target_id != self.handle:
                self.tab.close()
                self.tab = CdpTab(self.tab.port, self.handle)
            self.need_relogin = False
            self._page_loaded_at = time.time()
            self._log('ok', 'Đã đăng nhập lại tab Vids')

    def _stopped(self) -> bool:
        return self.w._stop.is_set() or self._halt.is_set()

    def stop(self):
        """Dừng làn (worker gọi trong `finally` TRƯỚC `driver.quit()`)."""
        self._halt.set()
        try:
            if self.tab:
                self.tab.close()
        except Exception:
            pass

    @property
    def busy(self) -> bool:
        return self.inflight > 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── heartbeat riêng của làn ─────────────────────────────────────────────
    def _heartbeat(self, running: int = 0, keepalive_only: bool = False) -> list:
        try:
            profile = pm.get(self.profile_id) or self.w.profile
            name = (profile.get('display_name') or '').strip() or profile.get('profile_name', '')
            n = self._max_concurrent()
            body = {'machineCode': self.machine_code, 'runningCount': running, 'waitingCount': 0,
                    'taskMode': 'video_only', 'displayName': f'[OMNI] {name}',
                    'machineType': 'selenium_profile', 'maxConcurrent': n}
            outside = not keepalive_only and not in_run_hours(profile)
            if keepalive_only or outside:
                body['keepaliveOnly'] = True
            if self.w._bind_mode_enabled():
                body['bindProjectEmail'] = True
                body['accountEmail'] = (profile.get('account_email') or '').strip()
            r = self.w._req('POST', f'{FLOW_SERVER}/api/media/heartbeat', quiet=True, body=body) or {}
            if keepalive_only or outside:
                return []
            tasks = r.get('tasks') or ([r['task']] if r.get('task') else [])
            # Phòng backend cũ bỏ qua taskMode — Omni chỉ làm video.
            video = [t for t in tasks if 'video' in (t.get('mode') or '').lower()]
            for t in tasks:
                if t not in video:
                    self._report_error(t['id'], f'Làn Omni chỉ nhận task video (mode={t.get("mode")})')
            if video:
                self._log('info', f'← Heartbeat: nhận {len(video)} task ({", ".join("#%s" % t["id"] for t in video)})')
            return video
        except Exception as e:
            self._log('warn', f'Heartbeat lỗi: {e}')
            return []

    def _max_concurrent(self) -> int:
        try:
            return max(1, min(int(get_local_settings().get('omni_max_concurrent') or 1), 5))
        except Exception:
            return 1

    def _report_error(self, task_id, message: str):
        self.error_count += 1
        try:
            pm.bump_task_stat(self.profile_id, 'error')
        except Exception:
            pass
        try:
            self.w._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                        body={'taskId': task_id, 'machineCode': self.machine_code,
                              'errorMessage': f'[Omni] {message}'})
        except Exception:
            pass

    # ── vòng lặp ────────────────────────────────────────────────────────────
    def _loop(self):
        stop = self._halt
        while not self._stopped():
            try:
                if self.need_relogin or time.time() < self._backoff_until:
                    self._heartbeat(keepalive_only=True)
                    stop.wait(POLL_INTERVAL)
                    continue
                if self.need_new_doc and not self._rotate_doc():
                    if self._new_doc_fails >= _NEW_DOC_MAX_FAILS:
                        self._log('warn', f'Tạo tài liệu Vids mới thất bại {self._new_doc_fails} lần '
                                          f'liên tiếp — chạy tiếp trên tài liệu cũ')
                        self.need_new_doc = False
                        self._new_doc_fails = 0
                    else:
                        self._heartbeat(keepalive_only=True)
                        stop.wait(POLL_INTERVAL)
                        continue
                if not self._ensure_page():
                    stop.wait(POLL_INTERVAL)
                    continue
                tasks = self._heartbeat()
                if not tasks:
                    stop.wait(POLL_INTERVAL)
                    continue
                self._run_batch(tasks)
            except Exception as e:
                self._log('error', f'Vòng lặp lỗi: {e}')
                stop.wait(POLL_INTERVAL)
        self._log('info', f'Làn Omni dừng (xong {self.done_count}, lỗi {self.error_count})')

    def _ensure_page(self) -> bool:
        """Tab còn sống + đang ở Vids + còn phiên Google. Tự nạp lại định kỳ."""
        try:
            url = self.tab.url()
            if not url:
                self.tab.close()
                self.tab.connect()
                url = self.tab.url()
            stale = time.time() - self._page_loaded_at > _PAGE_MAX_AGE_SECS
            if '/videos/' not in url or '/d/' not in url or stale:
                url = self.tab.navigate(self.doc_url or omni_be.VIDS_HOME_URL, wait=40)
                self._page_loaded_at = time.time()
            if 'accounts.google.com' in url:
                self._log('warn', 'Tab Vids bị đá ra trang đăng nhập — chờ đăng nhập lại')
                self.need_relogin = True
                return False
            return True
        except CdpError as e:
            self._log('warn', f'Tab Vids không phản hồi: {e}')
            return False

    def _state(self) -> dict:
        st = self.tab.evaluate(omni_be.call_expr('state'), timeout=30) or {}
        if not st.get('hasSapisid'):
            self.need_relogin = True
            raise RuntimeError('Trang Vids không có cookie SAPISID (mất đăng nhập Google)')
        if not st.get('apiKey'):
            raise RuntimeError('Không đọc được API key genai trong trang Vids')
        if not st.get('docId'):
            raise RuntimeError('Tab Omni không đứng ở tài liệu Vids nào (cần ngữ cảnh tài liệu)')
        return st

    def _run_batch(self, tasks: list):
        """Chuẩn bị + bắn generate cho mọi task (không chờ), rồi chờ song song."""
        self.inflight = len(tasks)
        try:
            self._run_batch_inner(tasks)
        finally:
            self.inflight = 0

    def _run_batch_inner(self, tasks: list):
        try:
            st = self._state()
        except Exception as e:
            for t in tasks:        # đã nhận từ heartbeat — phải trả lại để server giao lại
                self._fail(t, e)
            return
        pending = {}                     # key -> (task, t0, lần_thử)
        self._st = st
        dom_tasks = []
        for t in tasks:
            if self._stopped():
                self._report_error(t['id'], 'Worker dừng trước khi chạy task')
                continue
            if self._use_dom(t):
                dom_tasks.append(t)         # chạy tuần tự sau khi bắn xong các task API
                continue
            self._start_task(t, pending, attempt=0, first=True)
        last_ka = time.time()
        for t in dom_tasks:                  # DOM: từng task một (panel chỉ có 1 tiến trình)
            if self._stopped():
                self._report_error(t['id'], 'Worker dừng trước khi chạy task')
                continue
            self._run_task_dom(t)
            self._heartbeat(running=len(pending), keepalive_only=True)
        while pending and not self._stopped():
            time.sleep(3)
            for key in list(pending):
                t, t0, attempt = pending[key]
                try:
                    g = self.tab.evaluate(omni_be.call_expr('poll', key), timeout=30) or {}
                except CdpError as e:
                    pending.pop(key)
                    self._retry_or_fail(t, e, pending, attempt)
                    continue
                if g.get('state') == 'running':
                    if time.time() - t0 > _GENERATE_TIMEOUT_SECS:
                        pending.pop(key)
                        self._retry_or_fail(t, f'Timeout: quá {_GENERATE_TIMEOUT_SECS}s chưa có kết quả',
                                            pending, attempt)
                    continue
                pending.pop(key)
                if g.get('state') == 'missing':
                    # Trang Vids vừa tải lại (vd sau khi proxy đổi IP) — kết quả cũ mất.
                    self._retry_or_fail(t, 'fetch error: mất kết quả generate (trang Vids vừa tải lại)',
                                        pending, attempt)
                    continue
                try:
                    self._finish(t, g, time.time() - t0)
                except Exception as e:
                    self._retry_or_fail(t, e, pending, attempt)
            if time.time() - last_ka >= POLL_INTERVAL:
                self._heartbeat(running=len(pending), keepalive_only=True)
                last_ka = time.time()
        for key, (t, _t0, _a) in pending.items():
            self._report_error(t['id'], 'Worker dừng giữa lúc tạo video')

    # ── độ phân giải + chế độ ───────────────────────────────────────────────
    def _resolution(self, task: dict) -> str:
        """Setting `omni_resolution`: 'project' (mặc định) = theo `videoResolution` của task
        (project), hoặc ép '720p'/'1080p'. 1080p tạo thẳng 1920x1080 — không cần upscale."""
        mode = str(get_local_settings().get('omni_resolution') or 'project').lower()
        if mode in ('720p', '1080p'):
            return mode
        return omni_be.normalize_resolution(task.get('videoResolution') or task.get('video_resolution'))

    def _use_dom(self, task: dict) -> bool:
        """Setting `omni_mode` = 'dom' → tạo bằng giao diện (kể cả task có ảnh thành phần)."""
        return str(get_local_settings().get('omni_mode') or 'api').lower() == 'dom'

    def _run_task_dom(self, t: dict):
        """Tạo 1 task bằng giao diện Vids (tuần tự, 1 task/lần — panel chỉ có 1 tiến trình)."""
        from .omni_dom import OmniDom, OmniDomError, OmniContentRejected
        task_id = t['id']
        prompt = (t.get('prompt_text') or t.get('title') or '').strip()
        self._log('info', f'▶ Task #{task_id} [DOM] mode={t.get("mode")} "{prompt[:70]}"')
        self.w._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                    body={'taskId': task_id, 'machineCode': self.machine_code})
        t0 = time.time()
        try:
            dom = OmniDom(self.tab, self._log)
            urls = self._source_urls(t)[:omni_be.MAX_INGREDIENTS]
            images = []
            for i, u in enumerate(urls):
                r = req_lib.get(u, timeout=60)
                r.raise_for_status()
                mime = ((getattr(r, 'headers', None) or {}).get('content-type') or 'image/png').split(';')[0]
                images.append((r.content, mime, f'ref_{i + 1}.' + ('jpg' if 'jpeg' in mime else 'png')))
            # Lưu đầu vào để nếu Vids từ chối nội dung còn có prompt + ảnh mà xem (logs/omni_refused/).
            self._inputs = getattr(self, '_inputs', {})
            self._inputs[task_id] = {'prompt': prompt, 'mode': t.get('mode'), 'aspect_ratio': t.get('aspect_ratio'),
                                     'duration': t.get('omniDuration') or t.get('video_duration'),
                                     'images': [(u, m, b) for u, (b, m, _n) in zip(urls, images)]}
            portrait = omni_be.aspect_code(t.get('aspect_ratio')) == omni_be.ASPECT_PORTRAIT
            url = dom.generate(prompt, portrait, self._resolution(t),
                               omni_be.clamp_duration(t.get('omniDuration') or t.get('video_duration')
                                                      or omni_be.DEFAULT_DURATION),
                               timeout=_GENERATE_TIMEOUT_SECS, images=images)
            self._finish(t, {'status': 200, 'text': url}, time.time() - t0)
        except OmniContentRejected as e:
            self._reject(t, e)
        except OmniDomError as e:
            self._fail(t, e)
        except Exception as e:
            self._retry_or_fail(t, e, {}, 0)

    def _start_task(self, t: dict, pending: dict, attempt: int, first: bool = False):
        try:
            key = self._kickoff(t, self._st, first=first)
            pending[key] = (t, time.time(), attempt)
        except Exception as e:
            self._retry_or_fail(t, e, pending, attempt)

    def _retry_or_fail(self, t: dict, err, pending: dict, attempt: int):
        """Lỗi mạng tạm thời (proxy xoay đổi IP…) → chờ, nạp lại trang, làm lại task
        NGAY trong làn (tối đa `_MAX_TASK_RETRIES` lần). Lỗi khác → báo lỗi về server."""
        msg = str(err)
        if attempt >= _MAX_TASK_RETRIES or not _is_transient(msg) or self._stopped():
            self._fail(t, msg if attempt == 0 else f'{msg} (sau {attempt} lần làm lại)')
            return
        self._log('warn', f'↻ Task #{t["id"]}: lỗi mạng tạm thời ({msg[:120]}) — '
                          f'chờ {_RETRY_WAIT_SECS}s (proxy đổi IP?) rồi làm lại '
                          f'{attempt + 1}/{_MAX_TASK_RETRIES}')
        self._halt.wait(_RETRY_WAIT_SECS)
        # Nạp lại trang sẽ làm MẤT kết quả các task khác đang tạo trong cùng tab —
        # chỉ nạp khi tab rảnh, hoặc khi lỗi đòi phải có trang mới (401/trang đã mất).
        if not pending or 'HTTP 401' in msg or 'mất kết quả' in msg:
            self._refresh_page()
        self._start_task(t, pending, attempt + 1)

    def _rotate_doc(self) -> bool:
        """(2026-09-29) Có task lỗi → về trang chủ Vids (`VIDS_HOME_URL`), bấm
        "Bắt đầu một video mới" để tạo tài liệu MỚI, lưu lại (`omni_docs.json`)
        để mọi task sau — kể cả lần mở tool sau — chạy trên tài liệu mới này.
        Chạy qua CDP của tab Omni, không đụng phiên Selenium của VEO. Chỉ gọi khi
        tab rảnh (giữa 2 lô) vì điều hướng làm mất kết quả task đang tạo."""
        now = time.time()
        if now - self._new_doc_at < _NEW_DOC_MIN_GAP_SECS:
            return False
        self._new_doc_at = now
        old = self.doc_url or ''
        self._log('info', f'Có task lỗi — về {omni_be.VIDS_HOME_URL} tạo tài liệu Vids mới')
        try:
            url = self.tab.navigate(omni_be.VIDS_HOME_URL, wait=40)
            if 'accounts.google.com' in url:
                self._log('warn', 'Trang chủ Vids đá ra trang đăng nhập — chờ đăng nhập lại')
                self.need_relogin = True
                return False
            rect = None
            for _ in range(20):
                rect = self.tab.evaluate(
                    "(()=>{const e=document.querySelector(%s);if(!e)return null;"
                    "e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();"
                    "return r.width&&r.height?{x:r.left+r.width/2,y:r.top+r.height/2}:null})()"
                    % json.dumps(_NEW_DOC_BTN_SELECTOR), timeout=10, await_promise=False)
                if rect:
                    break
                time.sleep(1)
            if not rect:
                self._new_doc_fails += 1
                self._log('error', 'Không thấy nút "Bắt đầu một video mới" trên trang chủ Vids')
                return False
            # (2026-09-29) Tab Omni nằm NỀN (driver quay về tab VEO) — Chrome hay bỏ qua
            # chuột CDP gửi vào tab nền, nên bấm xong mà không đổi trang. Thử lần lượt:
            # chuột CDP (đủ field `buttons`) → sự kiện chuột JS ngay trên phần tử →
            # mở thẳng URL tạo tài liệu. KHÔNG đưa tab Omni lên trước vì tab VEO sẽ
            # thành tab nền và dính đúng lỗi này.
            x, y = rect['x'], rect['y']
            self.tab.send('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y})
            for t, b in (('mousePressed', 1), ('mouseReleased', 0)):
                self.tab.send('Input.dispatchMouseEvent',
                              {'type': t, 'x': x, 'y': y, 'button': 'left', 'buttons': b, 'clickCount': 1})
            new = self._wait_new_doc(old, 12)
            if not new:
                self._log('warn', 'Chuột CDP không mở được tài liệu (tab nền?) — thử sự kiện chuột JS')
                self.tab.evaluate(
                    "(()=>{const e=document.querySelector(%s);if(!e)return false;"
                    "const r=e.getBoundingClientRect(),o={bubbles:true,cancelable:true,view:window,"
                    "clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};"
                    "for(const t of ['pointerover','mouseover','pointerdown','mousedown'])"
                    "e.dispatchEvent(new (t.startsWith('pointer')?PointerEvent:MouseEvent)(t,{...o,buttons:1}));"
                    "for(const t of ['pointerup','mouseup','click'])"
                    "e.dispatchEvent(new (t.startsWith('pointer')?PointerEvent:MouseEvent)(t,{...o,buttons:0}));"
                    "return true})()" % json.dumps(_NEW_DOC_BTN_SELECTOR), timeout=10, await_promise=False)
                new = self._wait_new_doc(old, 12)
            if not new:
                self._log('warn', f'Vẫn chưa vào tài liệu mới — thử mở thẳng {_NEW_DOC_CREATE_URL}')
                self.tab.navigate(_NEW_DOC_CREATE_URL, wait=40)
                new = self._wait_new_doc(old, 20)
            if not new:
                self._new_doc_fails += 1
                self._log('error', f'Bấm "Bắt đầu một video mới" nhưng không vào được tài liệu mới '
                                   f'(lần {self._new_doc_fails}/{_NEW_DOC_MAX_FAILS})')
                return False
            time.sleep(5)                       # chờ tài liệu tải xong (docs-est, api key)
            _save_doc(_doc_key(self.w.profile), new)
            self.doc_url = new
            self._page_loaded_at = time.time()
            self.need_new_doc = False
            self._new_doc_fails = 0
            self._log('ok', f'Đã tạo tài liệu Vids mới — các task sau chạy trên: {new}')
            return True
        except Exception as e:
            self._new_doc_fails += 1
            self._log('warn', f'Tạo tài liệu Vids mới lỗi: {e}')
            return False

    def _wait_new_doc(self, old: str, secs: float) -> str:
        """Chờ tab Omni vào 1 tài liệu Vids KHÁC `old`; trả URL gọn hoặc ''."""
        t0 = time.time()
        while time.time() - t0 < secs:
            cur = self.tab.url()
            if '/videos/' in cur and '/d/' in cur and cur.split('?')[0] != old.split('?')[0]:
                return cur.split('?')[0].split('#')[0]
            time.sleep(1)
        return ''

    def _refresh_page(self):
        """Nạp lại tab Vids + đọc lại token. Sau khi đổi IP, request cũ trong trang
        đã chết; nạp lại để có trang/kết nối mới sạch."""
        try:
            self.tab.navigate(self.doc_url or omni_be.VIDS_HOME_URL, wait=40)
            self._page_loaded_at = time.time()
            self._st = self._state()
        except Exception as e:
            self._log('warn', f'Nạp lại tab Vids lỗi: {e}')

    def _kickoff(self, task: dict, st: dict, first: bool = True) -> str:
        task_id = task['id']
        prompt = (task.get('prompt_text') or task.get('title') or '').strip()
        if first:
            self._log('info', f'▶ Task #{task_id} mode={task.get("mode")} "{prompt[:70]}"')
            self.w._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                        body={'taskId': task_id, 'machineCode': self.machine_code})

        urls = self._source_urls(task)
        if len(urls) > omni_be.MAX_INGREDIENTS:
            self._log('warn', f'Task #{task_id}: {len(urls)} ảnh — Omni chỉ nhận '
                              f'{omni_be.MAX_INGREDIENTS}, dùng {omni_be.MAX_INGREDIENTS} ảnh đầu')
            urls = urls[:omni_be.MAX_INGREDIENTS]
        ings = []
        self._inputs = getattr(self, '_inputs', {})
        self._inputs[task_id] = {'prompt': prompt, 'mode': task.get('mode'),
                                 'aspect_ratio': task.get('aspect_ratio'),
                                 'duration': task.get('omniDuration') or task.get('video_duration'),
                                 'images': []}
        for i, u in enumerate(urls):
            r = req_lib.get(u, timeout=60)
            r.raise_for_status()
            self._inputs[task_id]['images'].append((u, (getattr(r, 'headers', None) or {}).get('content-type', ''), r.content))
            b64 = base64.b64encode(r.content).decode()
            up = self._step(f'upload ảnh {i + 1}', lambda: self.tab.evaluate(
                omni_be.call_expr('upload', b64), timeout=120) or {}, ok=lambda x: x.get('blobId'))
            ings.append((omni_be.new_ingredient_uuid(), up['blobId'], omni_be.label_for(i)))
        if ings:
            self._log('info', f'Task #{task_id}: đã upload {len(ings)} ảnh thành phần')

        body = omni_be.build_generate_body(prompt, ings, doc_id=st.get('docId'),
                                           aspect_ratio=task.get('aspect_ratio'),
                                           resolution=self._resolution(task),
                                           # Thời lượng riêng cho Omni của project (heartbeat
                                           # gửi `omniDuration`), không có thì theo video_duration.
                                           duration=(task.get('omniDuration') or task.get('video_duration')
                                                     or omni_be.DEFAULT_DURATION))
        key = f'omni-{task_id}-{int(time.time() * 1000)}'
        # (2026-10-06) `api_fake_typing` (dùng chung với VEO): lệnh tạo vẫn đi bằng API
        # nhưng trước đó gõ prompt vào ô "Mô tả video" như người thật, bắn xong thì xoá
        # chữ để chuẩn bị cho task kế tiếp. Chỉ gõ + xoá, KHÔNG bấm Tạo.
        fake = None
        if int(get_local_settings().get('api_fake_typing', 0) or 0) and prompt:
            from .omni_dom import OmniDom
            fake = OmniDom(self.tab, self._log)
            fake.fake_type(prompt)
        try:
            r = self.tab.evaluate(omni_be.call_expr('kick', key, json.dumps(body, ensure_ascii=False),
                                                    st['apiKey'], st.get('serverToken') or '',
                                                    omni_be.GENERATE_TIMEOUT_SECS * 1000), timeout=30) or {}
            if not r.get('ok'):
                raise RuntimeError(f'không bắn được generate: {r}')
        finally:
            if fake:
                # Lệnh API đã rời đi — nghỉ vài giây như người vừa bấm xong rồi xoá chữ.
                time.sleep(2 + random.random() * 2)
                fake.fake_clear()
                self._log('info', '⌫ Đã xoá prompt trong ô Video AI sau khi gọi API')
        return key

    def _finish(self, task: dict, g: dict, elapsed: float):
        task_id = task['id']
        text = g.get('text') or ''
        urls = omni_be.parse_generate_response(text)
        if g.get('status') != 200 or not urls:
            raise RuntimeError(omni_be.summarize_error(g.get('status') or 0, text))
        r = self._step('tải video', lambda: self.tab.evaluate(
            omni_be.call_expr('fetchB64', urls[0]), timeout=180) or {}, ok=lambda x: x.get('b64'))
        data = base64.b64decode(r['b64'])
        self._step('gửi kết quả về server', lambda: self.w._upload_video_result(
            task_id, data, source='omni', resolution=self._resolution(task),
            machine_code=self.machine_code) or True)
        self.done_count += 1; self._refused_streak = 0
        try:
            pm.bump_task_stat(self.profile_id, 'done')
        except Exception:
            pass
        (getattr(self, '_inputs', {}) or {}).pop(task_id, None)
        self._log('ok', f'✔ Task #{task_id} xong ({elapsed:.0f}s, {len(data) // 1024}KB)')

    def _step(self, name: str, fn, ok=bool):
        """Chạy 1 bước nhỏ, lỗi mạng tạm thời thì thử lại tại chỗ (không làm lại
        cả task). Hết lượt thử → raise để `_retry_or_fail()` quyết định."""
        last = None
        for i in range(_STEP_RETRIES):
            try:
                res = fn()
                if ok(res):
                    return res
                last = (res or {}).get('error') if isinstance(res, dict) else res
            except Exception as e:
                last = e
            if not _is_transient(str(last)) or i == _STEP_RETRIES - 1:
                break
            self._log('warn', f'{name} lỗi ({str(last)[:100]}) — thử lại {i + 2}/{_STEP_RETRIES}')
            self._halt.wait(5)
        raise RuntimeError(f'{name} lỗi: {last}')

    def _dump_refused(self, task_id, msg: str):
        """(2026-09-29) Google Vids trả REQUEST_REFUSED (400, bộ lọc nội dung) cho
        1 số task cố định — lưu prompt + ảnh thành phần vào logs/omni_refused/ để
        xem đúng nội dung nào bị chặn. Giữ tối đa 50 task gần nhất."""
        inp = (getattr(self, '_inputs', {}) or {}).get(task_id)
        if not inp:
            return
        try:
            from .config import LOGS_DIR
            d = Path(LOGS_DIR) / 'omni_refused' / f'task_{task_id}'
            d.mkdir(parents=True, exist_ok=True)
            imgs = []
            for i, (u, ctype, data) in enumerate(inp['images']):
                ext = 'png' if data[1:4] == b'PNG' else ('webp' if data[8:12] == b'WEBP' else 'jpg')
                (d / f'image_{i + 1}.{ext}').write_bytes(data)
                imgs.append({'url': u, 'contentType': ctype, 'bytes': len(data), 'file': f'image_{i + 1}.{ext}'})
            (d / 'info.json').write_text(json.dumps(
                {'taskId': task_id, 'profile': self.profile_id, 'error': msg[:500],
                 'mode': inp['mode'], 'aspect_ratio': inp['aspect_ratio'], 'duration': inp['duration'],
                 'prompt_len': len(inp['prompt']), 'prompt': inp['prompt'], 'images': imgs,
                 'at': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False, indent=1), encoding='utf-8')
            olds = sorted(d.parent.iterdir(), key=lambda x: x.stat().st_mtime)
            for x in olds[:-50]:
                import shutil
                shutil.rmtree(x, ignore_errors=True)
            self._log('warn', f'Task #{task_id}: Google từ chối nội dung — đã lưu prompt + ảnh vào {d}')
        except Exception as e:
            self._log('warn', f'Không lưu được dữ liệu task bị từ chối: {e}')

    def _reject(self, task: dict, err):
        """Vids từ chối nội dung (thẻ video hỏng có lý do). Thử lại cùng nội dung vô ích →
        lưu log riêng của task (prompt + ảnh + lý do) và báo server ĐÁ VỀ DRAFT kèm lý do để
        người dùng sửa prompt/ảnh. KHÔNG tính lỗi tài khoản, không nghỉ làn, không đổi tài liệu."""
        tid = task['id']
        reason = getattr(err, 'reason', None) or str(err)
        if 'REQUEST_REFUSED' in reason:
            reason = ('Google/Vids từ chối request (REQUEST_REFUSED — bộ lọc nội dung / điều khoản). '
                      f'Phản hồi: {reason[:200]}')
        inp = (getattr(self, '_inputs', {}) or {}).get(tid) or {}
        ctx = (f'prompt {len(inp.get("prompt") or "")} ký tự, {len(inp.get("images") or [])} ảnh, '
               f'tỉ lệ {inp.get("aspect_ratio")}, {inp.get("duration")}s, '
               f'{self._resolution(task)}, tài liệu {self.doc_url or "?"}')
        self._dump_refused(tid, f'Vids từ chối nội dung: {reason}')
        (getattr(self, '_inputs', {}) or {}).pop(tid, None)
        self._log('error', f'✘ Task #{tid}: Vids từ chối — đá về draft. Lý do: {reason[:300]}')
        self._log('warn', f'   ↳ Ngữ cảnh: {ctx}')
        self._log('warn', f'   ↳ Prompt: "{(inp.get("prompt") or "")[:160]}"')
        self.error_count += 1
        try:
            pm.bump_task_stat(self.profile_id, 'error')
        except Exception:
            pass
        try:
            self.w._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                        body={'taskId': tid, 'machineCode': self.machine_code, 'toDraft': True,
                              'errorMessage': f'[Omni] Vids từ chối nội dung: {reason}'[:1000]})
        except Exception as e:
            self._log('warn', f'Báo draft về server lỗi: {e}')

    def _fail(self, task: dict, err):
        msg = str(err)
        refused = 'REQUEST_REFUSED' in msg
        if refused:
            # Cùng lỗi với thẻ "That request looks like it goes against our terms" của giao diện:
            # nội dung bị từ chối → thử lại vô ích → đá về draft kèm lý do + lưu log riêng của task.
            self._refused_streak = 0
            self._reject(task, msg)
            return
        if refused:
            self._dump_refused(task['id'], msg)
        self._refused_streak = self._refused_streak + 1 if refused else 0
        (getattr(self, '_inputs', {}) or {}).pop(task['id'], None)
        self._log('error', f'✘ Task #{task["id"]}: {msg[:300]}')
        self._report_error(task['id'], msg[:500])
        if not self.need_new_doc:
            self._log('warn', 'Sẽ tạo tài liệu Vids mới sau lô này — các task sau chạy trên tài liệu mới')
        self.need_new_doc = True
        if any(h in msg for h in _ACCOUNT_BLOCK_HINTS):
            self._backoff_until = time.time() + _ACCOUNT_BACKOFF_SECS
            self._log('warn', f'Lỗi tầng tài khoản — làn Omni nghỉ {_ACCOUNT_BACKOFF_SECS // 60} phút')
        if refused and self._refused_streak >= _REFUSED_ROTATE_AFTER:
            self._backoff_until = time.time() + _ACCOUNT_BACKOFF_SECS
            self._log('warn', f'REQUEST_REFUSED {self._refused_streak} task liên tiếp — làn Omni nghỉ '
                              f'{_ACCOUNT_BACKOFF_SECS // 60} phút, đổi fingerprint')
            self._refused_streak = 0
            try:
                # Đóng trình duyệt (worker dừng) để lần mở sau dùng fingerprint mới;
                # no-op nếu chưa bật cài đặt `cloak_rotate_fp_on_block`.
                self.w._rotate_fingerprint_on_block()
            except Exception as e:
                self._log('warn', f'Đổi fingerprint lỗi: {e}')

    @staticmethod
    def _source_urls(task: dict) -> list[str]:
        sm = task.get('source_media') or []
        if isinstance(sm, str):
            try:
                sm = json.loads(sm)
            except Exception:
                sm = []
        out = []
        for m in sm:
            u = (m.get('url') if isinstance(m, dict) else str(m or '')).strip()
            if not u:
                continue
            if u.startswith('/'):
                u = f'{FLOW_SERVER}{u}'
            if u not in out:
                out.append(u)
        return out
