"""Profile loại "Muse AI" (`worker_mode='muse'`, https://muse.ai) — 2026-10-11.

Muse AI là MỘT LOẠI PROFILE độc lập như VEO/Flow, Gemini… (KHÔNG phải làn tab phụ như
Omni): profile loại này mở Chrome, vào muse.ai bằng TAB CHÍNH (profile phải đã đăng nhập
Muse) và chạy `MuseLane` làm việc duy nhất — nhận task ẢNH + VIDEO của project bật
"Muse AI" rồi tạo bằng giao diện chat (xem `worker._run_muse_loop`).

VÌ SAO DOM mà không phải API: Muse là agent chạy trên VM riêng, mọi tin nhắn đi qua
WebSocket mã hóa (xem tests/utils/muse_dom.py) — không có endpoint REST để gọi.

Heartbeat bằng machine code `selenium-<id>-muse` (taskMode `all`) — backend giao đúng task
ảnh/video của project bật `enable_muse` (`heartbeat.py`), cạnh tranh cùng hàng đợi với các
máy khác qua `FOR UPDATE SKIP LOCKED`.

Chạy theo LÔ (batch): nhận tối đa `muse_batch_size` task → gửi lần lượt vào chat →
chờ đủ kết quả → tải về → upload → nghỉ cho đủ `muse_batch_gap_secs` kể từ lúc gửi
lô → lô kế. Tab điều khiển qua websocket CDP (`cdp_tab.py`).

Prompt gửi Muse = <tiền tố tạo ảnh/video> + prompt task + <hậu tố tỉ lệ khung hình>
(sửa ở tab Cài đặt "Muse AI"). Mặc định tiền tố:
  "I want to create a video of " / "I want to create an image of ".

Gán kết quả về đúng task (ĐÃ KIỂM CHỨNG trên Muse thật, 2026-10-11): Muse gộp nhiều yêu
cầu thành 1 tin trả lời ("Here are both videos.") nên KHÔNG dựa vào vị trí được, nhưng nó
tự đặt tên file theo MÃ ĐẦU PROMPT: prompt "CUE_346. Style…" → file
`media-generation-cue-346-golden-scroll-0-<uuid>.mp4`, "CUE_345…" → `…cue-345-boy-dusty-path…`.
Prompt gửi Muse luôn mang `TASK_<id>:` (cùng quy ước VEO) ngay sau tiền tố, nên gán theo:
  1. `task-<id>` (slug của `TASK_<id>`) có trong tên media;
  2. mã đầu prompt gốc (`CUE_346`/`SCENE_380` → `cue-346`/`scene-380`) có trong tên media;
  3. thứ tự xuất hiện — CHỈ khi tắt `muse_name_token` và prompt không có mã nào (không đảm bảo).
⚠️ Chưa kiểm chứng Muse có đặt tên file chứa `task-<id>` không (mới thấy nó lấy mã CUE trong prompt).
"""
from __future__ import annotations

import base64
import json
import re
import tempfile
import threading
import time
from pathlib import Path

from .cdp_tab import CdpTab, CdpError, debug_port_of
from .config import FLOW_SERVER, POLL_INTERVAL, req_lib
from .local_settings import get_local_settings
from .managers import pm
from .run_hours import in_run_hours

MUSE_URL = 'https://muse.ai/'
BUSY_LABELS = ['Dừng', 'Dừng nhiệm vụ', 'Stop', 'Stop task', 'Stop generating', 'Stop response']
VIDEO_MODES = ('textToVideo', 'imageToVideo', 'componentsToVideo', 'frameToVideo')

_ACCOUNT_BACKOFF_SECS = 600


# ── Dựng prompt (hàm thuần, test được) ───────────────────────────────────────
def is_video_task(task: dict) -> bool:
    return (task.get('mode') or '') in VIDEO_MODES


def build_prompt(task: dict, settings: dict | None = None, with_token: bool = True) -> str:
    """<tiền tố ảnh|video> + prompt + <hậu tố tỉ lệ> (+ mã gán kết quả)."""
    s = settings if settings is not None else get_local_settings()
    video = is_video_task(task)
    prefix = str(s.get('muse_prefix_video' if video else 'muse_prefix_image') or '')
    if prefix and not prefix.endswith((' ', '\n')):
        prefix += ' '
    body = (task.get('prompt_text') or task.get('title') or '').strip()
    ratio = (task.get('aspect_ratio') or '').strip()
    suffix = ''
    tpl = str(s.get('muse_aspect_suffix') or '')
    if ratio and tpl:
        suffix = tpl.replace('{ratio}', ratio)
    # `TASK_<id>:` như VEO — mã để gán kết quả về đúng task (Muse đặt tên file theo mã đầu prompt).
    if with_token and s.get('muse_name_token', 1) and not _TASK_LEAD.match(body):
        body = f'{task_token(task)}: {body}'
    return f'{prefix}{body}{suffix}'


_TASK_LEAD = re.compile(r'^\s*TASK_\d+')


def task_token(task: dict) -> str:
    return f'TASK_{task.get("id")}'


def task_slug(task: dict) -> str:
    """Slug tên file ứng với `TASK_<id>` → `task-<id>`."""
    return f'task-{task.get("id")}'


_LEAD_ID = re.compile(r'^\s*([A-Za-z]+_\d+)')


def lead_slug(task: dict) -> str:
    """Mã đầu prompt dạng slug tên file Muse: "CUE_346. Style…" → "cue-346"; không có → ''."""
    m = _LEAD_ID.match((task.get('prompt_text') or task.get('title') or ''))
    return m.group(1).lower().replace('_', '-') if m else ''


_WORD = re.compile(r'[a-z0-9]{4,}')


def _no_zero(text: str) -> str:
    """Bỏ số 0 đứng đầu sau dấu gạch: "cue-074" → "cue-74" (Muse có thể đặt tên file không có số 0)."""
    return re.sub(r'-0+(?=\d)', '-', text)


def assign_media(tasks: list, media: list, order_fallback: bool = False) -> dict:
    """Gán media mới (list {kind,label,src}, theo thứ tự xuất hiện) cho task: theo
    `task-<id>` rồi mã đầu prompt gốc trong nhãn; `order_fallback` mới gán theo thứ tự
    (cùng loại ảnh/video) cho task KHÔNG có mã nào. Trả {task_id: media}."""
    out: dict = {}
    left = list(media)
    leads = [lead_slug(t) for t in tasks if lead_slug(t)]
    for t in tasks:
        ls = lead_slug(t)
        if ls and leads.count(ls) > 1:
            ls = ''                  # mã đầu prompt trùng giữa 2 task (khác project) → không dùng
        keys = [k for k in (task_slug(t), ls) if k]
        hit = None
        for k in keys:                       # mã đầu prompt trước, rồi mã m<id>
            k = _no_zero(k)
            hit = next((m for m in left
                        if re.search(r'(?<![a-z0-9])' + re.escape(k) + r'(?![a-z0-9])',
                                     _no_zero((m.get('label') or '').lower().replace('_', '-')))), None)
            if hit:
                break
        if hit:
            out[t['id']] = hit
            left.remove(hit)
    # Nhãn không có mã (Muse cắt tên file) → dùng tin nhắn liền trước ô media. Chỉ gán khi ctx
    # chỉ khớp ĐÚNG 1 task cùng loại (tin nhắn nhắc nhiều mã thì bỏ qua, tránh gán nhầm).
    for m in list(left):
        ctx = _no_zero((m.get('ctx') or '').lower().replace('_', '-'))
        if not ctx:
            continue
        cands = []
        for t in tasks:
            if t['id'] in out or is_video_task(t) != (m.get('kind') == 'video'):
                continue
            ks = [task_slug(t)]          # CHỈ TASK_<id>: mã CUE_xx có thể trùng giữa các project
            if any(re.search(r'(?<![a-z0-9])' + re.escape(k) + r'(?![a-z0-9])', ctx) for k in ks):
                cands.append(t)
        if len(cands) == 1:
            out[cands[0]['id']] = m
            left.remove(m)
    if order_fallback:
        for t in tasks:
            if t['id'] in out or lead_slug(t):
                continue                 # task có mã đầu chỉ nhận media ĐÚNG mã, không đoán theo thứ tự
            want = 'video' if is_video_task(t) else 'image'
            for m in left:
                if m.get('kind') == want:
                    out[t['id']] = m
                    left.remove(m)
                    break
    return out


# ── Muse TỪ CHỐI tạo (trả lời bằng chữ thay vì media) ────────────────────────
# Ví dụ: "I wasn't able to generate TASK_5539: CUE_294 (…). If you'd like, I can try …"
#        "I can't generate TASK_5515: CUE_282 as written." / "…TASK_5551: CUE_300 or TASK_5553: …"
_REFUSAL_RE = re.compile(
    r"(?:I\s+(?:wasn['’]t|was\s+not|am\s+not|['’]m\s+not)\s+able\s+to|I\s+(?:can['’]t|cannot|couldn['’]t|could\s+not)|"
    r"I['’]m\s+unable\s+to|I\s+am\s+unable\s+to|unable\s+to)\s+(?:generate|create|make|produce)"
    # Tiếng Việt: "không thể tạo/dựng/sinh/thực hiện", "chưa thể tạo", "không tạo được", "từ chối tạo"
    r"|(?:không|chưa)\s+(?:thể|có\s+thể|được\s+phép)\s+(?:tạo|dựng|sinh|thực\s+hiện)"
    r"|không\s+(?:tạo|dựng|sinh|thực\s+hiện)\s+được"
    r"|từ\s+chối\s+(?:tạo|dựng|sinh)", re.I)
# Nhãn vai trò thay đổi theo ngôn ngữ giao diện Muse (EN: "User message:", VI: "Tin nhắn của người dùng:").
_MSG_SPLIT = re.compile(r'(User message|Assistant message|Tin nhắn của người dùng|Tin nhắn của Assistant)\s*:')


def _role(label: str) -> str:
    l = (label or '').lower()
    return 'user' if l.startswith('user') or 'người dùng' in l else 'assistant'


def _blocks(page_text: str) -> list:
    parts = _MSG_SPLIT.split(page_text or '')
    return [(_role(parts[i]), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def find_replies(page_text: str, ids) -> dict:
    """{task_id: nội dung chữ Muse trả lời} cho task chưa có media: các tin TRỢ LÝ nằm sau tin người dùng
    gần nhất chứa `TASK_<id>`, tới trước tin người dùng kế tiếp; bỏ tin nhắc mã TASK của task KHÁC trong lô."""
    blocks = _blocks(page_text)
    idset = {str(i) for i in ids}
    out = {}
    for tid in ids:
        tok = re.compile(r'TASK_%s(?!\d)' % re.escape(str(tid)))
        last_user = -1
        for i, (role, txt) in enumerate(blocks):
            if role == 'user' and tok.search(txt):
                last_user = i
        if last_user < 0:
            continue
        texts = []
        for role, txt in blocks[last_user + 1:]:
            if role == 'user':
                break
            if not txt:
                continue
            others = {m for m in re.findall(r'TASK_(\d+)', txt)} - {str(tid)}
            if others & idset:
                continue                    # tin này nói về task khác trong lô
            texts.append(' '.join(txt.split()))
        if texts:
            out[tid] = ' '.join(texts)[:600]
    return out


def find_refusals(page_text: str, ids) -> dict:
    """{task_id: nội dung tin Muse từ chối}. Chỉ tính tin TRỢ LÝ nằm SAU tin người dùng gần nhất
    chứa `TASK_<id>` (task gửi lại thì lời từ chối cũ không tính) và câu từ chối phải nhắc `TASK_<id>`."""
    blocks = _blocks(page_text)                    # [(role, text)]
    out = {}
    for tid in ids:
        tok = re.compile(r'TASK_%s(?!\d)' % re.escape(str(tid)))
        last_user = -1
        for i, (role, txt) in enumerate(blocks):
            if role == 'user' and tok.search(txt):
                last_user = i
        if last_user < 0:
            continue
        for role, txt in blocks[last_user + 1:]:
            if role != 'assistant':
                continue
            for m in _REFUSAL_RE.finditer(txt):
                seg_end = len(txt)
                for sep in ('. ', chr(10)):
                    k = txt.find(sep, m.end() + 20)
                    if k != -1:
                        seg_end = min(seg_end, k)
                if tok.search(txt[m.start():seg_end]):
                    out[tid] = txt[:600]
                    break
            if tid in out:
                break
    return out


# ── JS chạy trong trang Muse ─────────────────────────────────────────────────
_STATE_JS = """(function(){
  var ta = document.querySelector('textarea');
  var busy = Array.from(document.querySelectorAll('button')).some(function(b){
    return %s.indexOf(b.getAttribute('aria-label')||'') >= 0; });
  var txt = (document.body && document.body.innerText) || '';
  return {url: location.href, ready: !!ta, busy: busy,
          login: !ta && /Đăng nhập|Đăng ký|Log in|Sign in|Sign up|waitlist|danh sách chờ/i.test(txt),
          error: /Đã xảy ra lỗi|Something went wrong|An error occurred/i.test(txt) && !ta,
          vis: document.visibilityState};
})()""" % json.dumps(BUSY_LABELS)

_MEDIA_JS = """(function(){
  var out = [];
  // Nhãn đổi theo ngôn ngữ giao diện Muse: "Mở video …"/"Open animated video …",
  // "Mở hình ảnh đã tạo …"/"Open generated image …" — nhận cả hai, phân loại theo thẻ con.
  var LAB = /^(Mở |Open |View |Xem )|image|hình ảnh|video/i;
  // Muse cắt tên file ở ~30 ký tự đầu prompt nên mã TASK_/CUE_ có thể KHÔNG nằm trong nhãn.
  // Tin nhắn Muse ngay trước ô media ("Video CUE_074 … đã hoàn thành") thì luôn nhắc mã → lấy làm ngữ cảnh.
  var CODE = /TASK_\\d+/;
  function ctxOf(el){
    var log = el.closest('[role=log]');
    var msg = el;
    while (log && msg.parentElement && msg.parentElement !== log) msg = msg.parentElement;
    if (!log || msg.parentElement !== log) return '';
    var p = msg.previousElementSibling, n = 0;
    while (p && n < 3) {
      var t = (p.innerText || '');
      if (!/^\\s*(Bạn|You|Tin nhắn của người dùng|User message)\\s*:/i.test(t) && CODE.test(t)) return t.slice(0, 400);
      p = p.previousElementSibling; n++;
    }
    return '';
  }
  document.querySelectorAll('[aria-label]').forEach(function(b){
    var l = b.getAttribute('aria-label') || '';
    if (!LAB.test(l)) return;
    var v = b.querySelector('video');
    var i = v ? null : b.querySelector('img[src^="blob:"]');
    if (!v && !i) return;
    out.push({kind: v ? 'video' : 'image', label:l, src: v ? (v.currentSrc||v.src) : i.src,
              ctx: ctxOf(b)});
  });
  return out;
})()"""

_FOCUS_JS = "(function(){var t=document.querySelector('textarea'); if(!t) return false; t.focus(); return true;})()"
_CLEAR_JS = ("(function(){var t=document.querySelector('textarea'); if(!t) return false;"
             "var d=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value');"
             "d.set.call(t,''); t.dispatchEvent(new Event('input',{bubbles:true})); t.focus(); return true;})()")
_REMOVE_SEL = ('button[aria-label="Remove attachment"], button[aria-label="Remove file"], '
               'button[aria-label="Gỡ file đính kèm"], button[aria-label="Gỡ tệp đính kèm"], '
               'button[aria-label="Xóa tệp đính kèm"], button[aria-label="Xóa tệp"]')
_ATTACHED_JS = "document.querySelectorAll('%s').length" % _REMOVE_SEL.replace("'", "\'")
_CLEAR_ATTACH_JS = ("(function(){var n=0;document.querySelectorAll('%s').forEach(function(b){b.click();n++;});return n;})()"
                    % _REMOVE_SEL.replace("'", "\'"))
_FETCH_JS = """new Promise(function(res){
  fetch(%s).then(function(r){return r.blob();}).then(function(b){
    var fr = new FileReader();
    fr.onload = function(){ res({type:b.type, size:b.size, b64:String(fr.result).split(',')[1]}); };
    fr.onerror = function(){ res({error:'FileReader'}); };
    fr.readAsDataURL(b);
  }).catch(function(e){ res({error:String(e)}); });
})"""


class MuseLane:
    def __init__(self, worker):
        self._late: dict = {}        # task hết giờ chờ nhưng CHƯA báo lỗi — còn chờ nền (tránh giao trùng sang tài khoản khác)
        self._recover_due = True      # quét media đã tạo mà chưa nhận: lúc khởi động + mỗi lần nạp lại chat
        self.w = worker
        self.profile_id = worker.profile_id
        self.machine_code = f'{worker.machine_code}-muse'
        self.handle = None
        self.tab: CdpTab | None = None
        self._thread = None
        self._halt = threading.Event()
        self._backoff_until = 0.0
        self.need_login = False
        self.done_count = 0
        self.error_count = 0
        self.inflight = 0
        self._seen: set = set()      # src blob đã xử lý — không gán lại cho lô sau

    # ── tiện ích ────────────────────────────────────────────────────────────
    def _log(self, level, msg):
        self.w._log(level, f'[muse] {msg}')

    def _stopped(self) -> bool:
        return self.w._stop.is_set() or self._halt.is_set()

    @property
    def busy(self) -> bool:
        return self.inflight > 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _settings(self) -> dict:
        return get_local_settings()

    def _batch_size(self) -> int:
        try:
            return max(1, min(int(self._settings().get('muse_batch_size') or 1), 5))
        except Exception:
            return 1

    # ── khởi động (LUỒNG CHÍNH, có Selenium) ─────────────────────────────────
    def start(self) -> bool:
        """Vào muse.ai ở TAB CHÍNH của profile, gắn CDP rồi chạy làn ở thread riêng."""
        d = self.w.driver
        try:
            d.get(MUSE_URL)
            self.handle = d.current_window_handle
        except Exception as e:
            self._log('error', f'Không mở được muse.ai: {e}')
            return False
        from .chrome_utils import _chrome_debug_port
        port = debug_port_of(d, _chrome_debug_port(self.profile_id))
        self.tab = CdpTab(port, self.handle)
        try:
            self.tab.connect()
            self.tab.send('Emulation.setFocusEmulationEnabled', {'enabled': True}, timeout=10)
        except Exception as e:
            self._log('error', f'Không gắn được CDP vào tab Muse (port {port}): {e}')
            return False
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f'muse-{self.profile_id}')
        self._thread.start()
        # Heartbeat giữ ấm TÁCH RIÊNG vòng chính: gửi task/chờ kết quả/tải file có thể chặn >30s,
        # khi đó máy bị coi offline → reaper thu hồi NGAY task đang tạo và giao cho tài khoản khác
        # (chạy trùng, có task ra 3 video). Luồng này heartbeat đều mỗi POLL_INTERVAL bất kể.
        self._ka_thread = threading.Thread(target=self._keepalive_loop, daemon=True,
                                           name=f'muse-ka-{self.profile_id}')
        self._ka_thread.start()
        self._log('ok', f'Tab Muse sẵn sàng — nhận task ẢNH + VIDEO ({self.machine_code})')
        return True

    def _keepalive_loop(self):
        while not self._halt.wait(POLL_INTERVAL):
            try:
                self._heartbeat(running=(getattr(self, 'inflight', 0) or 0) + len(self._late),
                                keepalive_only=True)
            except Exception:
                pass

    def stop(self):
        self._halt.set()
        try:
            if self.tab:
                self.tab.close()
        except Exception:
            pass

    # ── heartbeat riêng ──────────────────────────────────────────────────────
    def _heartbeat(self, running: int = 0, keepalive_only: bool = False) -> list:
        try:
            profile = pm.get(self.profile_id) or self.w.profile
            name = (profile.get('display_name') or '').strip() or profile.get('profile_name', '')
            body = {'machineCode': self.machine_code, 'runningCount': running, 'waitingCount': 0,
                    'taskMode': 'all', 'displayName': f'[MUSE] {name}',
                    'machineType': 'selenium_profile', 'maxConcurrent': self._batch_size()}
            outside = not keepalive_only and not in_run_hours(profile)
            if keepalive_only or outside:
                body['keepaliveOnly'] = True
            r = self.w._req('POST', f'{FLOW_SERVER}/api/media/heartbeat', quiet=True, body=body) or {}
            if keepalive_only or outside:
                return []
            tasks = r.get('tasks') or ([r['task']] if r.get('task') else [])
            if tasks:
                self._log('info', f'← Heartbeat: nhận {len(tasks)} task '
                                  f'({", ".join("#%s" % t["id"] for t in tasks)})')
            return tasks
        except Exception as e:
            self._log('warn', f'Heartbeat lỗi: {e}')
            return []

    # ── nhật ký task đã gửi (sống qua restart) để nhận lại kết quả sót ─────────
    def _sent_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / 'data' / f'muse_sent_{self.profile_id}.json'

    def _sent_load(self) -> dict:
        try:
            d = json.loads(self._sent_path().read_text(encoding='utf-8'))
            cut = time.time() - 3 * 86400
            return {k: v for k, v in d.items() if v.get('ts', 0) > cut}
        except Exception:
            return {}

    def _sent_save(self, d: dict):
        try:
            pth = self._sent_path()
            pth.parent.mkdir(parents=True, exist_ok=True)
            keep = dict(sorted(d.items(), key=lambda kv: kv[1].get('ts', 0))[-300:])
            pth.write_text(json.dumps(keep, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    def _sent_add(self, task: dict):
        d = self._sent_load()
        d[str(task['id'])] = {'id': task['id'], 'mode': task.get('mode'), 'ts': time.time(),
                              'prompt_text': (task.get('prompt_text') or task.get('title') or '')[:160]}
        self._sent_save(d)

    def _sent_drop(self, tid):
        d = self._sent_load()
        if d.pop(str(tid), None) is not None:
            self._sent_save(d)

    def _recover_existing(self):
        """Vào lại chat: media nào Muse ĐÃ tạo (nhãn khớp mã task đã gửi) mà chưa upload về
        thì tải + cập nhật task (kể cả task đã bị báo lỗi timeout)."""
        stubs = list(self._sent_load().values())
        if not stubs:
            return
        media = [m for m in self._media() if m['src'] not in self._seen]
        got = assign_media(stubs, media, order_fallback=False) if media else {}
        rest = [t for t in stubs if t['id'] not in got]
        for tid, reply in (self._refusals([t['id'] for t in rest]) if rest else {}).items():
            self._reject(next(t for t in rest if t['id'] == tid), reply)
        if not got:
            return
        self._log('info', f'♻ Phát hiện {len(got)} kết quả đã tạo mà chưa cập nhật — nhận lại…')
        for tid, m in got.items():
            task = next(t for t in stubs if t['id'] == tid)
            try:
                self._finish(task, m)
                self._seen.add(m['src'])
                self._sent_drop(tid)
            except Exception as e:
                self._log('error', f'✘ Nhận lại task #{tid} lỗi: {e}')

    def _refusals(self, ids) -> dict:
        try:
            text = self.tab.evaluate("document.body.innerText", timeout=30, await_promise=False) or ''
        except Exception:
            return {}
        return find_refusals(text, list(ids))

    def _replies(self, ids) -> dict:
        try:
            text = self.tab.evaluate("document.body.innerText", timeout=30, await_promise=False) or ''
        except Exception:
            return {}
        return find_replies(text, list(ids))

    def _expire(self, tid, task: dict, default_msg: str):
        """Hết hạn mà không có media: có chữ Muse trả lời (không phải ảnh/video) → lưu chữ đó làm lỗi
        (đá về draft); không có gì → báo lỗi hết giờ như cũ."""
        reply = self._replies([tid]).get(tid)
        if reply:
            self._reject(task, reply, kind='text')
        else:
            self._log('error', f'✘ Task #{tid}: {default_msg}')
            self._report_error(tid, default_msg)

    def _reject(self, task: dict, reply: str, kind: str = 'refuse'):
        """Muse từ chối tạo (trả lời chữ): lưu lý do vào lịch sử lỗi của task + ĐÁ VỀ DRAFT."""
        tid = task['id']
        reply = ' '.join((reply or '').split())
        self._log('error', f'✘ Task #{tid}: Muse từ chối tạo — đá về draft. Muse nói: {reply[:300]}')
        self.error_count += 1
        try:
            pm.bump_task_stat(self.profile_id, 'error')
        except Exception:
            pass
        try:
            from .config import LOGS_DIR
            d = Path(LOGS_DIR) / 'muse_refused'
            d.mkdir(parents=True, exist_ok=True)
            (d / f'task_{tid}.json').write_text(json.dumps(
                {'taskId': tid, 'profile': self.profile_id, 'reply': reply, 'mode': task.get('mode'),
                 'prompt': task.get('prompt_text') or task.get('title') or '',
                 'at': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False, indent=1), encoding='utf-8')
        except Exception:
            pass
        try:
            msg = (f'[Muse] Hết giờ chờ, Muse chỉ trả lời bằng chữ (không có ảnh/video): {reply}'
                   if kind == 'text' else f'[Muse] Muse từ chối tạo: {reply}')
            self.w._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                        body={'taskId': tid, 'machineCode': self.machine_code, 'toDraft': True,
                              'errorMessage': msg[:1000]})
        except Exception as e:
            self._log('warn', f'Báo draft về server lỗi: {e}')
        self._sent_drop(tid)

    def _poll_late(self):
        """Task hết giờ chờ trong lô nhưng còn trong `_late`: nhận kết quả nếu Muse tạo xong muộn,
        ghi nhận từ chối, hoặc báo lỗi khi quá hạn ân hạn."""
        if not self._late:
            return
        tasks = [t for t, _ in self._late.values()]
        media = [m for m in self._media() if m['src'] not in self._seen]
        got = assign_media(tasks, media, order_fallback=False) if media else {}
        for tid, m in got.items():
            task = self._late.pop(tid)[0]
            try:
                self._finish(task, m)
                self._seen.add(m['src'])
                self._sent_drop(tid)
            except Exception as e:
                self._log('error', f'✘ Task #{tid}: {e}')
                self._report_error(tid, str(e))
        rest = {tid: v for tid, v in self._late.items()}
        for tid, reply in self._refusals(list(rest)).items():
            if tid in self._late:
                self._reject(self._late.pop(tid)[0], reply)
        now = time.time()
        for tid, (t, dl) in list(self._late.items()):
            if now >= dl:
                self._late.pop(tid, None)
                self._expire(tid, t, 'Hết thời gian chờ kết quả từ Muse (kể cả chờ nền)')

    def _report_error(self, task_id, message: str):
        self.error_count += 1
        try:
            pm.bump_task_stat(self.profile_id, 'error')
        except Exception:
            pass
        try:
            self.w._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                        # toDraft: MỌI lỗi của làn Muse đá task về DRAFT (kèm lý do) thay vì tự quay lại
                        # hàng chờ — retry tự động làm Muse tạo lại (tốn quota, chạy trùng); người dùng
                        # xem lý do ở lịch sử lỗi rồi tự bấm chạy lại khi muốn.
                        body={'taskId': task_id, 'machineCode': self.machine_code, 'toDraft': True,
                              'errorMessage': f'[Muse] {message}'[:1000]})
        except Exception:
            pass

    # ── vòng lặp ────────────────────────────────────────────────────────────
    def _loop(self):
        stop = self._halt
        while not self._stopped():
            try:
                if self.need_login or time.time() < self._backoff_until:
                    self._heartbeat(keepalive_only=True)
                    if self.need_login and self._ensure_page():
                        self.need_login = False
                    stop.wait(POLL_INTERVAL)
                    continue
                if not self._ensure_page():
                    self._heartbeat(keepalive_only=True)
                    stop.wait(POLL_INTERVAL)
                    continue
                if self._recover_due:
                    self._recover_due = False
                    try:
                        self._recover_existing()
                    except Exception as e:
                        self._log('warn', f'Quét kết quả sót lỗi: {e}')
                if self._late:
                    self._poll_late()
                tasks = self._heartbeat(running=len(self._late))
                if not tasks:
                    stop.wait(POLL_INTERVAL)
                    continue
                t0 = time.time()
                self._run_batch(tasks)
                gap = float(self._settings().get('muse_batch_gap_secs') or 0)
                left = gap - (time.time() - t0)
                if left > 0:
                    self._log('info', f'⏳ Nghỉ {left:.0f}s cho đủ khoảng cách giữa 2 lô ({gap:.0f}s)')
                    while left > 0 and not self._stopped():
                        self._heartbeat(keepalive_only=True)
                        step = min(left, POLL_INTERVAL)
                        stop.wait(step)
                        left -= step
            except Exception as e:
                self._log('error', f'Vòng lặp lỗi: {e}')
                stop.wait(POLL_INTERVAL)
        self._log('info', f'Làn Muse dừng (xong {self.done_count}, lỗi {self.error_count})')

    def _state(self) -> dict:
        return self.tab.evaluate(_STATE_JS, timeout=20, await_promise=False) or {}

    def _ensure_page(self) -> bool:
        """Tab còn sống + đang ở chat Muse đã đăng nhập (có textarea)."""
        try:
            st = self._state()
            url = st.get('url') or ''
            if 'muse.ai' not in url or '/library' in url or '/feed' in url or st.get('error'):
                self.tab.navigate(MUSE_URL, wait=40)
                self._recover_due = True
                time.sleep(2)
                st = self._state()
            if not st.get('ready'):
                for _ in range(10):         # SPA nạp muộn
                    time.sleep(1.5)
                    st = self._state()
                    if st.get('ready'):
                        break
            if not st.get('ready'):
                self._log('warn', 'Chưa thấy ô chat Muse — chưa đăng nhập muse.ai trong profile này '
                                  'hoặc trang lỗi; chờ đăng nhập/nạp lại')
                self.need_login = bool(st.get('login')) or True
                return False
            try:
                self.tab.send('Page.bringToFront', timeout=10)
            except Exception:
                pass
            return True
        except CdpError as e:
            self._log('warn', f'Tab Muse không phản hồi: {e}')
            return False

    # ── một lô ──────────────────────────────────────────────────────────────
    def _media(self) -> list:
        return self.tab.evaluate(_MEDIA_JS, timeout=20, await_promise=False) or []

    def _run_batch(self, tasks: list):
        self.inflight = len(tasks)
        try:
            self._run_batch_inner(tasks)
        finally:
            self.inflight = 0

    def _run_batch_inner(self, tasks: list):
        for t in tasks:
            try:
                self.w._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                            body={'taskId': t['id'], 'machineCode': self.machine_code})
            except Exception:
                pass
        baseline = {m['src'] for m in self._media()} | self._seen
        sent = []
        task_gap = max(0, float(self._settings().get('muse_task_gap_secs') or 0))
        for i, t in enumerate(tasks):
            if i and task_gap and not self._stopped():
                self._log('info', f'⏳ Chờ {task_gap:.0f}s trước task kế trong lô…')
                self.w._stop.wait(task_gap)
            if self._stopped():
                self._report_error(t['id'], 'Worker dừng trước khi gửi task')
                continue
            try:
                self._send(t)
                self._sent_add(t)
                sent.append(t)
            except Exception as e:
                self._log('error', f'✘ Task #{t["id"]}: gửi vào chat lỗi: {e}')
                self._report_error(t['id'], f'Gửi vào chat Muse lỗi: {e}')
        if not sent:
            return
        s = self._settings()
        timeout = max(int(s.get('muse_image_timeout') or 300) if not any(is_video_task(t) for t in sent)
                      else int(s.get('muse_video_timeout') or 600), 60)
        pending = {t['id']: t for t in sent}
        t_start = time.time()
        last_ka = time.time()
        results: dict = {}
        while pending and not self._stopped() and time.time() - t_start < timeout:
            time.sleep(3)
            new = [m for m in self._media() if m['src'] not in baseline]
            got = assign_media(list(pending.values()), new,
                               order_fallback=not s.get('muse_name_token', 1))
            for tid, m in got.items():
                if tid in pending and (time.time() - t_start > 4):
                    results[tid] = m
                    baseline.add(m['src'])
                    pending.pop(tid)
            if pending:
                for tid, reply in self._refusals(list(pending)).items():
                    if tid in pending and tid not in results:
                        self._reject(pending.pop(tid), reply)
            if time.time() - last_ka >= POLL_INTERVAL:
                self._heartbeat(running=len(pending), keepalive_only=True)
                last_ka = time.time()
        for tid, m in results.items():
            task = next(t for t in sent if t['id'] == tid)
            try:
                self._finish(task, m)
                self._seen.add(m['src'])
                self._sent_drop(tid)
            except Exception as e:
                self._log('error', f'✘ Task #{tid}: {e}')
                self._report_error(tid, str(e))
        grace = max(0, int(s.get('muse_late_grace_secs') or 0))
        for tid, t in pending.items():
            if grace and not self._stopped():
                # KHÔNG báo lỗi ngay: báo lỗi → server giao lại cho tài khoản khác trong khi Muse vẫn
                # đang tạo ⇒ chạy trùng. Giữ task ở trạng thái đang xử lý, chờ nền thêm `grace` giây.
                self._late[tid] = (t, time.time() + grace)
                self._log('warn', f'⏳ Task #{tid}: chưa có kết quả sau {timeout}s — chờ nền thêm {grace}s '
                                  f'(chưa báo lỗi để không bị giao trùng)')
                continue
            self._expire(tid, t, f'Hết {timeout}s chưa có kết quả từ Muse')

    # ── gửi 1 task vào chat ───────────────────────────────────────────────────
    def _send(self, task: dict):
        refs = self._download_refs(task)
        try:
            if refs:
                self._attach(refs)
            text = build_prompt(task, self._settings())
            self._log('info', f'▶ Task #{task["id"]} mode={task.get("mode")} ref={len(refs)} "{text[:90]}"')
            if not self.tab.evaluate(_CLEAR_JS, timeout=15, await_promise=False):
                raise RuntimeError('Không thấy ô chat (textarea)')
            self.tab.send('Input.insertText', {'text': text}, timeout=20)
            time.sleep(0.4)
            for typ in ('keyDown', 'keyUp'):
                self.tab.send('Input.dispatchKeyEvent', {
                    'type': typ, 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13,
                    'nativeVirtualKeyCode': 13, **({'text': '\r'} if typ == 'keyDown' else {})}, timeout=10)
            time.sleep(1.5)        # cho React nhả ô nhập trước khi gửi tin kế tiếp
        finally:
            for p in refs:
                try:
                    Path(p).unlink()
                except Exception:
                    pass

    def _download_refs(self, task: dict) -> list:
        sm = task.get('source_media') or []
        if isinstance(sm, str):
            try:
                sm = json.loads(sm)
            except Exception:
                sm = []
        out = []
        for i, m in enumerate(sm[:4]):
            u = (m.get('url') if isinstance(m, dict) else str(m or '')).strip()
            if not u:
                continue
            if u.startswith('/'):
                u = f'{FLOW_SERVER}{u}'
            r = req_lib.get(u, timeout=60)
            r.raise_for_status()
            ctype = ((getattr(r, 'headers', None) or {}).get('content-type') or '').lower()
            ext = 'jpg' if 'jpeg' in ctype else 'webp' if 'webp' in ctype else 'png'
            f = Path(tempfile.gettempdir()) / f'muse_ref_{task["id"]}_{i}.{ext}'
            f.write_bytes(r.content)
            out.append(str(f))
        return out

    def _attach(self, paths: list):
        """Đính file qua CDP `DOM.setFileInputFiles` lên `input[type=file]` (input có sẵn trong DOM)."""
        # Xoá ảnh còn sót từ task trước (Muse giữ file đính cho tới khi gửi/gỡ).
        if self.tab.evaluate(_CLEAR_ATTACH_JS, timeout=10, await_promise=False):
            time.sleep(0.8)
        self.tab.send('DOM.enable', timeout=10)
        doc = self.tab.send('DOM.getDocument', {'depth': 0}, timeout=15)
        node = self.tab.send('DOM.querySelector', {
            'nodeId': doc['root']['nodeId'], 'selector': 'input[type=file]'}, timeout=15)
        if not node.get('nodeId'):
            raise RuntimeError('Không thấy input[type=file] để đính ảnh tham chiếu')
        self.tab.send('DOM.setFileInputFiles', {'nodeId': node['nodeId'], 'files': paths}, timeout=30)
        for _ in range(30):
            if self.tab.evaluate(_ATTACHED_JS, timeout=10, await_promise=False):
                return
            time.sleep(0.5)
        raise RuntimeError('Đính ảnh tham chiếu không thành công (không thấy nút gỡ đính kèm)')

    # ── nhận kết quả ──────────────────────────────────────────────────────────
    def _finish(self, task: dict, media: dict):
        tid = task['id']
        r = self.tab.evaluate(_FETCH_JS % json.dumps(media['src']), timeout=240) or {}
        if r.get('error') or not r.get('b64'):
            raise RuntimeError(f'Tải kết quả lỗi: {r.get("error") or "rỗng"}')
        data = base64.b64decode(r['b64'])
        if media['kind'] == 'video':
            self.w._upload_video_result(tid, data, source='muse', machine_code=self.machine_code)
        else:
            ext = 'webp' if 'webp' in (r.get('type') or '') else 'jpg' if 'jpeg' in (r.get('type') or '') else 'png'
            url = f'{FLOW_SERVER}/api/media/task/{tid}/upload_result'
            resp = req_lib.post(url, files=[('files', (f'task{tid}_muse.{ext}', data, r.get('type') or 'image/png'))],
                                data={'source': 'muse', 'machineCode': self.machine_code}, timeout=120)
            resp.raise_for_status()
        self.done_count += 1
        try:
            pm.bump_task_stat(self.profile_id, 'done')
        except Exception:
            pass
        self._log('ok', f'✔ Task #{tid} xong ({media["kind"]}, {len(data) // 1024}KB)')
