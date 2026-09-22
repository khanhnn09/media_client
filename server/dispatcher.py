"""Orchestration: registry (worker nào đang chạy), master switch, auto-scale
veo3 theo backlog task thực tế, dispatcher vòng lặp nền chính."""

import threading, time
from datetime import datetime, time as dtime

from .config import FLOW_SERVER, log, req_lib
from .managers import pm
from .worker import SeleniumFlowWorker
from .local_settings import get_local_settings
from .run_hours import in_run_hours, summarize_run_hours
from .state import (
    _workers, _desired_veo3, _sleep_until_by_pid, _master_switch_snapshot,
    _pending_cache, _PENDING_CACHE_TTL, _pending_fail_streak, _PENDING_MAX_STALE_FAILS,
    _gemini_pending_cache, _GEMINI_PENDING_CACHE_TTL,
    _gemini_pending_fail_streak, _GEMINI_PENDING_MAX_STALE_FAILS,
    _gemini_idle_since, _GEMINI_CLOSE_GRACE_SECS,
)
from . import state as _state  # _master_task_intake_enabled bị REASSIGN — phải qua
                                # module-qualified access, xem cảnh báo trong state.py

# ═════════════════════════════════════════════════════════════════════════════

def _start_worker(profile: dict):
    pid = profile['id']
    if pid in _workers:
        return 'already_running'
    # 2026-07-17 — lưới an toàn: routes.py::start_worker() (bấm Start thủ công)
    # đã chặn profile_disabled ở tầng route, nhưng nhánh veo3 KHÔNG gọi
    # _start_worker() trực tiếp — chỉ add vào _desired_veo3 rồi để
    # _veo3_dispatcher_tick() tự promote (gọi _start_worker() sau đó). Check lại
    # ở ĐÂY (điểm chốt DUY NHẤT thực sự mở Chrome, dùng chung bởi cả gemini
    # manual-start lẫn veo3 dispatcher-tick-promote) đảm bảo Chrome không bao
    # giờ mở cho 1 profile đã tắt, bất kể qua đường nào.
    if not profile.get('enabled', 1):
        return 'profile_disabled'
    # worker_mode='gemini'/'chatgpt'/'gemini_video'/'gemini_image' không cần
    # project_url (Flow project) — chỉ dùng gemini.google.com/chatgpt.com
    # tương ứng (gemini_video/gemini_image cũng dùng gemini.google.com, chỉ
    # khác "Tạo video" hoặc chat ảnh thường thay vì chat text thường — xem
    # worker.py::_run_task_gemini_video/_run_task_gemini_image).
    if (profile.get('worker_mode') not in ('gemini', 'chatgpt', 'gemini_video', 'gemini_image')
            and not profile.get('project_url') and not _bind_enabled()):
        return 'project_url_required'
    w = SeleniumFlowWorker(profile)
    t = threading.Thread(target=w.run, daemon=True, name=f'sel-worker-{pid}')
    t.start()
    _workers[pid] = (w, t)
    return None


def _stop_worker(profile_id: int):
    if profile_id not in _workers:
        return
    w, t = _workers.pop(profile_id)
    w.stop()


def _reap_dead_workers():
    """Dọn entry trong _workers mà thread đã tự thoát (sleep escalation, browser
    đóng, exception fatal...) — không có gì khác từng gọi _stop_worker() cho các
    trường hợp này trước đây, khiến profile bị coi là 'đang chạy' mãi mãi dù
    thread đã chết, và không bao giờ start lại được qua API."""
    for pid in list(_workers.keys()):
        _, t = _workers[pid]
        if not t.is_alive():
            _workers.pop(pid, None)


# ═════════════════════════════════════════════════════════════════════════════
# Veo3 dispatcher — giới hạn số profile veo3 (worker_mode api/dom) chạy Chrome+
# worker thật sự đồng thời theo setting server max_concurrent_veo3_profiles.
# Không áp dụng cho worker_mode='gemini' — loại đó start/stop trực tiếp (không có
# khái niệm "waiting"/slot-promotion), xem _auto_scale_gemini_tick() bên dưới cho
# cơ chế auto-scale RIÊNG của gemini (2026-07-18, "giữ sẵn N máy online").
# ═════════════════════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════════════════════
# Master switch (2026-07-16) — 1 công tắc DUY NHẤT cho CẢ client_tool (mọi profile
# trên máy này, kể cả worker_mode='gemini' — KHÁC _desired_veo3 vốn chỉ veo3). Điều
# khiển từ main.py (Sidebar). TẮT = dừng hẳn TẤT CẢ worker đang chạy ngay lập
# tức (đóng Chrome), chặn auto-scale VÀ chặn cả /start thủ công. BẬT lại = để
# auto-scale (veo3 + gemini) tự đánh giá LẠI theo backlog THẬT ngay ở tick kế
# tiếp (2026-07-20 — xem fix bên dưới, trước đây "khôi phục mù"). Chỉ sống trong
# RAM (không persist DB) — restart server thì về lại mặc định TẮT (2026-07-17,
# xem state.py — trước đây mặc định BẬT, đổi theo yêu cầu user "mặc định khi mở
# tool sẽ không nhận task").
# ═════════════════════════════════════════════════════════════════════════════

def _set_master_task_intake(enabled: bool):
    if enabled == _state._master_task_intake_enabled:
        return
    _state._master_task_intake_enabled = enabled

    if not enabled:
        _master_switch_snapshot.clear()
        _master_switch_snapshot.update(_workers.keys())
        _desired_veo3.clear()
        for pid in list(_workers.keys()):
            _stop_worker(pid)
        log.info(f'[master-switch] TẮT — đã dừng {len(_master_switch_snapshot)} profile')
        return

    # 2026-07-20 (theo bug user báo — "khi bật nhận task hiện tại chỉ có gemini,
    # nhưng nó mở cả profile VEO"): TRƯỚC ĐÂY nhánh này "khôi phục mù" — start lại
    # NGAY (không kiểm tra backlog) mọi profile đã từng chạy trước lúc tắt switch,
    # kể cả profile KHÔNG hề có task nào đang chờ. Với profile veo3 dùng CHUNG
    # account Google với 1 profile gemini khác, việc tự mở thêm 1 Chrome vô cớ (chỉ
    # vì "trước đây nó đang chạy") có thể gây xung đột session ngay trên chính tài
    # khoản Google đó, kéo theo cả profile ĐANG CÓ VIỆC (gemini) bị crash/rớt theo.
    # Auto-scale (`_auto_scale_veo3_tick`/`_auto_scale_gemini_tick`) đã tự quản lý
    # MỌI profile enabled=1 theo backlog THẬT — chạy ngay tick kế tiếp (≤10s, cùng
    # background loop) vì cờ `_master_task_intake_enabled` đã bật ở dòng trên rồi.
    # Nên KHÔNG cần (và KHÔNG NÊN) tự start/thêm _desired_veo3 ở đây nữa — chỉ cần
    # bỏ trạng thái TẮT, còn "nên mở lại profile nào" để đúng 2 hàm auto-scale đó
    # tự quyết theo backlog thực tế, tránh mở nhầm profile không có việc.
    n_snapshot = len(_master_switch_snapshot)
    _master_switch_snapshot.clear()
    log.info(f'[master-switch] BẬT — {n_snapshot} profile trước đó sẽ được auto-scale '
             f'tự đánh giá lại theo backlog thực tế ở tick kế tiếp (không khôi phục mù)')


def _fetch_global_settings() -> dict:
    """Trước đây gọi `GET {FLOW_SERVER}/api/media/settings` (cache 15s) — settings
    (max_concurrent_veo3_profiles, error_*, ...) giờ CỤC BỘ hoàn toàn (2026-07-17,
    xem local_settings.py) nên đọc thẳng, không cần gọi mạng/cache TTL nữa. Giữ
    nguyên TÊN hàm để không phải đổi call site (`_veo3_dispatcher_tick`)."""
    return get_local_settings()


# ═════════════════════════════════════════════════════════════════════════════
# Khung giờ không nhận task (2026-07-20, theo yêu cầu user "thêm setting bật
# khung giờ không nhận task") — tự BẬT/TẮT Master switch (dùng lại NGUYÊN
# _set_master_task_intake(), cùng cơ chế snapshot/restore đã có cho nút bấm tay)
# đúng vào thời điểm bắt đầu/kết thúc khung giờ đã cấu hình. KHÔNG ép buộc liên
# tục suốt khung giờ — nếu user tự bật lại thủ công giữa chừng (vd cần xử lý gấp
# lúc nửa đêm), hệ thống không tắt lại ngay, chỉ chờ tới boundary kế tiếp.
# ═════════════════════════════════════════════════════════════════════════════

def _parse_hhmm(value: str, default: dtime) -> dtime:
    try:
        h, m = str(value).strip().split(':')
        return dtime(hour=int(h), minute=int(m))
    except Exception:
        return default


def _is_in_quiet_hours(settings: dict) -> bool:
    """True nếu THỜI ĐIỂM HIỆN TẠI (giờ máy local) nằm trong khung giờ
    quiet_hours_start→quiet_hours_end đã cấu hình VÀ tính năng đang bật. Hỗ trợ
    khung giờ vắt qua nửa đêm (vd 22:00 → 06:00: start > end)."""
    if not int(settings.get('quiet_hours_enabled', 0) or 0):
        return False
    start = _parse_hhmm(settings.get('quiet_hours_start', '22:00'), dtime(22, 0))
    end   = _parse_hhmm(settings.get('quiet_hours_end',   '06:00'), dtime(6, 0))
    now   = datetime.now().time()
    if start == end:
        return False  # khung giờ rỗng (bắt đầu = kết thúc) — coi như tắt, tránh 24/24
    if start < end:
        return start <= now < end
    return now >= start or now < end  # vắt qua nửa đêm


def _quiet_hours_tick():
    """Gọi định kỳ (thread nền _veo3_dispatcher_loop) — chỉ hành động ở ĐÚNG thời
    điểm CHUYỂN trạng thái (vừa vào/vừa ra khung giờ), so với `_state.
    _quiet_hours_active` (lần tick trước). Việc gọi lại `_set_master_task_intake()`
    với giá trị KHÔNG đổi là no-op an toàn (hàm đó tự return sớm nếu trùng giá trị
    hiện tại) nên không lo ghi đè lựa chọn thủ công của user ngoài đúng 2 thời điểm
    boundary này."""
    settings = _fetch_global_settings()
    should_pause = _is_in_quiet_hours(settings)
    if should_pause == _state._quiet_hours_active:
        return
    _state._quiet_hours_active = should_pause
    if should_pause:
        log.info('[quiet-hours] Vào khung giờ không nhận task — tự tắt master switch')
        _set_master_task_intake(False)
    else:
        log.info('[quiet-hours] Hết khung giờ không nhận task — tự bật lại master switch')
        _set_master_task_intake(True)


# ═════════════════════════════════════════════════════════════════════════════
# Auto-scale veo3 (2026-07-16) — tự thêm/bớt profile khỏi _desired_veo3 theo backlog
# task THỰC TẾ trên server (GET /api/media/pending_by_mode), thay vì chỉ dựa vào
# user bấm Start. Chỉ áp dụng worker_mode api/dom — gemini KHÔNG có hàng đợi
# backlog kiểu này (dispatch kiểu "push" ngay khi có máy rảnh, xem gemini.py
# _pick_gemini_machine) nên không thể auto-scale theo cùng cơ chế; xem
# _auto_scale_gemini_tick() (2026-07-18) bên dưới cho cơ chế RIÊNG của gemini
# ("giữ sẵn N máy online" thay vì phản ứng theo backlog).
# (_IMAGE_TASK_MODES/_VIDEO_TASK_MODES/_pending_cache/... định nghĩa trong state.py,
# import ở đầu file — KHÔNG định nghĩa lại ở đây, tránh shadow bản trong state.py.)
# ═════════════════════════════════════════════════════════════════════════════


def _fetch_pending_by_mode() -> dict:
    """LƯU Ý (bug đã vá 2026-07-16): nếu FLOW_SERVER sập/không kết nối được, KHÔNG
    được tin cache cũ vô thời hạn — trước đây fallback trả thẳng
    `_pending_cache['data']` bất kể đã cũ bao lâu, khiến _auto_scale_veo3_tick() cứ
    đinh ninh "còn task pending" mãi mãi (dù server đã tắt từ lâu) và liên tục
    mở/giữ mở profile dù không còn ai gửi task nữa. Giờ: sau
    _PENDING_MAX_STALE_FAILS lần fetch lỗi LIÊN TIẾP, coi backlog = 0 (an toàn hơn
    là tin dữ liệu đã lỗi thời) — auto-scale sẽ tự đóng bớt profile thay vì giữ mở."""
    now = time.time()
    if _pending_cache['data'] is not None and now - _pending_cache['fetched_at'] < _PENDING_CACHE_TTL:
        return _pending_cache['data']
    try:
        r = req_lib.get(f'{FLOW_SERVER}/api/media/pending_by_mode', timeout=8)
        data = r.json()
        if data.get('success'):
            _pending_cache['data'] = data
            _pending_cache['fetched_at'] = now
            _pending_fail_streak['count'] = 0
            return data
    except Exception as e:
        log.warning(f'[auto-scale] Không lấy được pending_by_mode: {e}')

    _pending_fail_streak['count'] += 1
    if _pending_fail_streak['count'] >= _PENDING_MAX_STALE_FAILS:
        return {'byMode': {}, 'imageTotal': 0, 'videoTotal': 0, 'total': 0}
    return _pending_cache['data'] or {'byMode': {}, 'imageTotal': 0, 'videoTotal': 0, 'total': 0}


def _bind_enabled() -> bool:
    """(2026-09-14) Cài đặt "Chỉ chạy task theo project + email" đang bật? — xem
    `config.py::bind_tasks_to_project_email`. Bật thì profile VEO (api/dom)
    KHÔNG cần `project_url` riêng (link Flow lấy từ project Nano Banana của task)
    và backlog tính theo ĐÚNG email của từng profile (`byFlowEmail`)."""
    try:
        return bool(int(get_local_settings().get('bind_tasks_to_project_email', 0) or 0))
    except (TypeError, ValueError):
        return False


def _email_backlog(profile: dict, pending: dict) -> dict:
    """Backlog của project gán ĐÚNG email profile này — `{imageTotal, videoTotal,
    total}` (0 hết nếu backend cũ chưa trả `byFlowEmail` hoặc profile không có
    email)."""
    email = (profile.get('account_email') or '').strip().lower()
    by_email = pending.get('byFlowEmail') or {}
    return by_email.get(email) or {'imageTotal': 0, 'videoTotal': 0, 'total': 0}


def _profile_veo3_eligible(profile: dict, pending: dict) -> bool:
    """profile có task khớp task_mode (all/image_only/video_only) đang pending
    không — dùng để quyết định có nên tự mở/giữ mở profile này hay không.

    (2026-09-12, fix bug thật user báo "bật tài khoản gemini tạo image/video
    hiện profile cái tắt liền") — TRƯỚC ĐÂY dùng CHUNG `imageTotal`/`videoTotal`
    (đếm theo engine_clause MẶC ĐỊNH của `heartbeat.py`, tức VEO3
    `machine_type='selenium_profile'`) cho CẢ `worker_mode='gemini_image'`/
    `'gemini_video'` — 2 engine đó có `engine_clause` RIÊNG ở `heartbeat.py`
    (chỉ project BẬT `enable_gemini_image`/`enable_gemini_video` mới được giao),
    hoàn toàn KHÁC `imageTotal`/`videoTotal` (chỉ tính VEO3-eligible). Mismatch
    này khiến `_auto_scale_veo3_tick()` quyết định SAI cả 2 chiều: tưởng có việc
    (đếm nhầm task của project VEO3-only) rồi mở worker lên chỉ để heartbeat mãi
    nhận 0 task, hoặc tưởng hết việc (vì `imageTotalGeminiImage`/
    `videoTotalGeminiVideo` — con số ĐÚNG — không tồn tại ở backend cũ nên rơi về
    generic) rồi đóng ngay tick kế tiếp — đúng cảm giác "bật cái tắt liền".

    Backend MỚI (`ToolSub/backend/routes/admin.py::pending_by_mode()`, cùng đợt
    fix) trả thêm `imageTotalGeminiImage`/`videoTotalGeminiVideo` — mirror ĐÚNG
    `engine_clause` riêng của 2 machine_type này. Backend CŨ (chưa restart) chưa
    có 2 field này → fallback về `imageTotal`/`videoTotal` generic (hành vi CŨ,
    vẫn có thể mismatch nhưng KHÔNG tệ hơn trước — chỉ mất đi phần chính xác mới
    thêm, không regress)."""
    worker_mode = profile.get('worker_mode')
    if worker_mode == 'gemini_image':
        total = pending.get('imageTotalGeminiImage')
        if total is None:
            total = pending.get('imageTotal') or 0
        return total > 0
    if worker_mode == 'gemini_video':
        total = pending.get('videoTotalGeminiVideo')
        if total is None:
            total = pending.get('videoTotal') or 0
        return total > 0

    task_mode = profile.get('task_mode') or 'all'
    # (2026-09-14) Chế độ gán project + email: profile VEO chỉ được giao task của
    # project gán đúng email của nó (heartbeat.py `binding_clause`) — phải xét
    # đúng backlog đó, không phải backlog chung (vốn đã loại project đã gán).
    if worker_mode in ('api', 'dom') and _bind_enabled():
        pending = _email_backlog(profile, pending)
    if task_mode == 'image_only':
        return (pending.get('imageTotal') or 0) > 0
    if task_mode == 'video_only':
        return (pending.get('videoTotal') or 0) > 0
    return (pending.get('total') or 0) > 0


# worker_mode ăn theo backlog `tasks_media_flow` giống hệt VEO3 (đăng ký task
# qua CÙNG `_heartbeat()`/`/api/media/heartbeat`) — 'gemini_video' (2026-08-07,
# xem worker.py::_run_task_gemini_video) là engine THỨ 2 cho task VIDEO (Gemini
# chat's "Tạo video" thay vì Flow DOM), luôn `task_mode='video_only'` (ép ở
# ProfileDialog.get_data(), không có UI chọn khác) nên tự nhiên rơi đúng vào
# nhánh `video_only` của `_profile_veo3_eligible()`/ngân sách `video_budget`
# trong `_veo3_dispatcher_tick()` — cạnh tranh CÙNG pool video với profile VEO3
# `task_mode='video_only'`, không cần sửa gì thêm ở logic cấp ngân sách.
# 'gemini_image' (2026-09-09, xem worker.py::_run_task_gemini_image) là engine
# THỨ 2 cho task ẢNH (Gemini chat thường thay vì Flow DOM/API), luôn
# `task_mode='image_only'` (ép ở ProfileDialog.get_data()) — rơi đúng vào
# nhánh `image_only`/`image_budget`, cạnh tranh CÙNG pool ảnh với VEO3
# `task_mode='image_only'`.
_VEO3_LIKE_WORKER_MODES = ('api', 'dom', 'gemini_video', 'gemini_image')

# 2 worker_mode không có "project" Flow (gemini.google.com thay vì labs.google/
# flow.google.com) — dùng ở nhiều chỗ cần loại trừ yêu cầu `project_url`.
_NO_PROJECT_WORKER_MODES = ('gemini_video', 'gemini_image')


# (2026-09-17) Profile VEO đang NGOÀI khung giờ chạy riêng (`run_hours`) — chỉ để
# log 1 lần lúc vào/ra khung giờ, không spam mỗi tick 10s.
_run_hours_blocked_pids: set = set()


def _run_hours_gate(profile: dict) -> bool:
    """True nếu profile được chạy lúc này. Ngoài khung giờ → bỏ khỏi `_desired_veo3`
    và đóng ngay nếu đang chạy mà không có task dở (task dở chạy xong mới đóng —
    worker cũng tự không nhận task mới, xem `worker._heartbeat`)."""
    pid = profile['id']
    if in_run_hours(profile):
        if pid in _run_hours_blocked_pids:
            _run_hours_blocked_pids.discard(pid)
            log.info(f'[run-hours] Profile {pid} vào khung giờ chạy '
                     f'({summarize_run_hours(profile.get("run_hours"))}) — nhận task lại')
        return True
    if pid not in _run_hours_blocked_pids:
        _run_hours_blocked_pids.add(pid)
        log.info(f'[run-hours] Profile {pid} ngoài khung giờ chạy '
                 f'({summarize_run_hours(profile.get("run_hours"))}) — không nhận task')
    _desired_veo3.discard(pid)
    if pid in _workers:
        w, _t = _workers[pid]
        if w._current_task is None:
            log.info(f'[run-hours] Profile {pid} ngoài khung giờ chạy — tự đóng')
            _stop_worker(pid)
    return False


def _auto_scale_veo3_tick():
    """Gọi định kỳ (thread nền _veo3_dispatcher_loop). MỌI profile veo3-like
    enabled=1 (worker_mode api/dom/gemini_video/gemini_image) được tool tự
    quản lý:
      - Có task pending khớp task_mode → thêm vào _desired_veo3 (dispatcher tick kế
        tiếp tự mở, vẫn tôn trọng max_concurrent_veo3_profiles — xem _veo3_dispatcher_tick).
      - Hết task khớp VÀ không có task đang xử lý dở (_current_task is None) → đóng
        NGAY (không chờ grace period — task hiện tại vẫn được chạy xong trước khi
        đóng, chỉ không nhận task mới)."""
    if not _state._master_task_intake_enabled:
        return
    pending = _fetch_pending_by_mode()
    try:
        profiles = pm.list()
    except Exception as e:
        log.warning(f'[auto-scale] pm.list() lỗi: {e}')
        return

    for profile in profiles:
        pid = profile['id']
        if profile.get('worker_mode') not in _VEO3_LIKE_WORKER_MODES:
            continue
        if not profile.get('enabled', 1):
            # 2026-07-17 — trước đây chỉ `continue` (bỏ qua), KHÔNG chủ động gỡ
            # khỏi `_desired_veo3` — nếu profile đã ở trong `_desired_veo3` từ
            # trước (vd đang 'waiting' để được promote) rồi mới bị tắt qua
            # ProfileDialog, pid vẫn nằm lì trong `_desired_veo3` mãi mãi:
            # `_veo3_dispatcher_tick()` cứ mỗi 10s lại thử `_start_worker()`,
            # luôn nhận `profile_disabled` (xem check mới ở `_start_worker`),
            # log warning lặp vô hạn mà không bao giờ tự dừng. Giờ chủ động
            # discard + dừng hẳn nếu đang chạy — cùng hành vi với nhánh "hết
            # task khớp" bên dưới.
            _desired_veo3.discard(pid)
            if pid in _workers:
                w, _t = _workers[pid]
                if w._current_task is None:
                    log.info(f'[auto-scale] Profile {pid} vừa bị tắt (enabled=0) — tự đóng')
                    _stop_worker(pid)
            continue
        if (profile.get('worker_mode') not in _NO_PROJECT_WORKER_MODES
                and not profile.get('project_url') and not _bind_enabled()):
            continue
        if not _run_hours_gate(profile):
            continue

        if _profile_veo3_eligible(profile, pending):
            _desired_veo3.add(pid)
            continue

        # Diagnostic log (2026-07-17): user báo "2 profile phù hợp, server cho
        # chạy đồng thời 2, nhưng tool chỉ chạy 1" — profile thứ 2 nằm im ở
        # 'idle', chưa từng được thêm vào _desired_veo3. Có 2 khả năng chính,
        # KHÔNG loại trừ nhau: (1) task_mode của profile không khớp LOẠI task
        # đang pending (vd profile 'video_only' nhưng chỉ có task ảnh) — hành vi
        # ĐÚNG, không phải bug; (2) pending_by_mode chỉ đếm status='pending'
        # (KHÔNG tính 'assigned'/'processing') — nếu profile A xử lý đủ nhanh
        # (heartbeat mỗi vài giây) so với chu kỳ check của tick này (10s), có thể
        # A đã "gom" hết task trước khi tick kịp thấy pending>0 cho profile khác
        # — pending trông như 0 dù thực ra có việc đang chạy. Log dòng này (chỉ
        # khi CÓ backlog toàn cục nhưng profile không khớp) để phân biệt 2 case
        # trên qua log thật, tránh đoán mò thêm.
        if (pending.get('total') or 0) > 0:
            log.info(f'[auto-scale] Profile {pid} (task_mode="{profile.get("task_mode")}") '
                     f'KHÔNG khớp pending hiện tại — image={pending.get("imageTotal")} '
                     f'video={pending.get("videoTotal")} total={pending.get("total")}')

        _desired_veo3.discard(pid)
        if pid not in _workers:
            continue
        w, _t = _workers[pid]
        if w._current_task is not None:
            continue
        log.info(f'[auto-scale] Profile {pid} hết task khớp task_mode "{profile.get("task_mode")}" — tự đóng')
        _stop_worker(pid)


def _veo3_dispatcher_tick():
    """Gọi định kỳ (thread nền _veo3_dispatcher_loop) hoặc ngay sau khi user bấm
    Start — đảm bảo tối đa max_concurrent_veo3_profiles profile veo3 có
    Chrome+worker thật sự sống cùng lúc; các profile 'desired' còn lại giữ trạng
    thái 'waiting' và tự được thăng cấp khi có slot trống."""
    _reap_dead_workers()
    if not _state._master_task_intake_enabled:
        return
    now = time.time()

    # Đánh thức profile đã hết thời gian ngủ — đưa về 'waiting' để xét cấp slot lại.
    for pid in list(_desired_veo3):
        wake_at = _sleep_until_by_pid.get(pid)
        if wake_at is not None and now >= wake_at:
            _sleep_until_by_pid.pop(pid, None)
            profile = pm.get(pid)
            if profile and profile.get('status') == 'sleeping':
                pm.set_status(pid, 'waiting')
                log.info(f'[dispatcher] Profile {pid} đã hết thời gian ngủ — chuyển waiting')

    settings = _fetch_global_settings()
    max_conc = max(1, int(settings.get('max_concurrent_veo3_profiles', 1) or 1))
    running_veo3 = [pid for pid in _workers if pid in _desired_veo3]
    slots_free = max_conc - len(running_veo3)

    waiting_pids = [pid for pid in _desired_veo3
                    if pid not in _workers and pid not in _sleep_until_by_pid]
    if slots_free <= 0 or not waiting_pids:
        return

    # 2026-07-21 (theo bug user báo "profile không liên quan tự start dù không
    # có task được giao", sau đó làm rõ thêm "VEO cũng phải phân biệt mode —
    # chỉ có task image thì profile chỉ nhận video không được mở"): KHÔNG mở
    # nhiều profile hơn số lượng task ĐANG THẬT SỰ còn chờ, TÁCH RIÊNG theo
    # từng loại (image/video) — không dùng 1 con số `total` gộp chung như bản
    # đầu (2026-07-21 v1). Lý do bản v1 (`slots_free = min(max_conc,
    # pending_total) - running`) vẫn CHƯA đủ chính xác: `_profile_veo3_eligible`
    # chỉ trả lời "profile này có ÍT NHẤT 1 task khớp mode không" (nhị phân),
    # KHÔNG biết có bao nhiêu profile CÙNG task_mode đang cạnh tranh cùng 1 số
    # lượng task nhỏ — vd 1 task ảnh + 3 task video (total=4) nhưng có 2 profile
    # `image_only` cùng "khớp" (imageTotal=1>0 cho CẢ 2) → v1 vẫn mở cả 2 dù
    # chỉ có 1 task ảnh, y hệt lỗi gemini đã fix nhưng ở dạng khác (đa profile
    # CÙNG mode tranh nhau 1 số lượng việc nhỏ, thay vì 2 mode khác nhau tranh
    # nhau 1 pool chung). Fix: cấp "ngân sách" riêng cho `image_only`/`video_only`
    # (đúng bằng `imageTotal`/`videoTotal`), chỉ profile `task_mode='all'` mới
    # được lấy từ phần CÒN LẠI SAU KHI đã cấp đủ cho 2 nhóm trên (vì `all` linh
    # hoạt nhận được cả 2 loại, còn `image_only`/`video_only` thì không) — trừ
    # thêm phần các profile ĐANG CHẠY (running_veo3) đã tiêu thụ theo ĐÚNG
    # task_mode của chính chúng (best-effort, không biết chính xác profile
    # 'all' đang chạy đang xử lý loại nào — coi nó tiêu thụ từ ngân sách chung).
    # (2026-09-12, fix bug thật user báo "bật tài khoản gemini tạo image/video
    # hiện profile cái tắt liền") — TRƯỚC ĐÂY `image_budget`/`video_budget` (từ
    # `imageTotal`/`videoTotal`, giờ mirror ĐÚNG engine_clause VEO3 mặc định của
    # `heartbeat.py` — xem `pending_by_mode()` phía backend) bị DÙNG CHUNG cho
    # CẢ profile VEO3 (`api`/`dom`) LẪN `gemini_image`/`gemini_video` — 2 loại
    # sau chỉ vì force `task_mode='image_only'`/`'video_only'` (ProfileDialog)
    # nên bị gộp NHẦM vào cùng `img_wait`/`vid_wait`, dùng NHẦM ngân sách của
    # backlog VEO3 (project `enable_veo=1`) để quyết định có PROMOTE (mở Chrome)
    # hay không — trong khi backlog THẬT SỰ dispatch được cho chúng (project
    # BẬT `enable_gemini_image`/`enable_gemini_video`) là 1 tập HOÀN TOÀN KHÁC.
    # Hệ quả: profile `gemini_image` được mở (`image_budget>0` nhờ backlog VEO3
    # không liên quan) rồi NGAY TICK KẾ TIẾP (`_auto_scale_veo3_tick()`, đã sửa
    # dùng đúng `imageTotalGeminiImage`) thấy KHÔNG có backlog gemini thật →
    # đóng ngay vì chưa từng nhận được task nào — đúng "bật cái tắt liền". Giờ
    # tách RIÊNG 2 ngân sách `gemini_image_budget`/`gemini_video_budget` (từ
    # `imageTotalGeminiImage`/`videoTotalGeminiVideo`, fallback về
    # `imageTotal`/`videoTotal` nếu backend cũ chưa có field mới — KHÔNG regress
    # so với trước). `all_wait` KHÔNG cần đổi — gemini_image/gemini_video KHÔNG
    # BAO GIỜ có `task_mode='all'` (force cứng ở ProfileDialog.get_data()).
    pending              = _fetch_pending_by_mode()
    image_budget         = pending.get('imageTotal') or 0
    video_budget         = pending.get('videoTotal') or 0
    gemini_image_budget  = pending.get('imageTotalGeminiImage')
    if gemini_image_budget is None:
        gemini_image_budget = image_budget
    gemini_video_budget  = pending.get('videoTotalGeminiVideo')
    if gemini_video_budget is None:
        gemini_video_budget = video_budget
    # (2026-09-14) Chế độ gán project + email — profile VEO (api/dom) chỉ ăn
    # backlog của ĐÚNG email mình (`byFlowEmail`), nên ngân sách cũng phải tính
    # RIÊNG theo email, không trừ vào `image_budget`/`video_budget` chung.
    bind = _bind_enabled()
    email_budget: dict = {}

    def _eb(prof: dict) -> dict:
        email = (prof.get('account_email') or '').strip().lower()
        if email not in email_budget:
            src = _email_backlog(prof, pending)
            email_budget[email] = {'image': src.get('imageTotal') or 0,
                                   'video': src.get('videoTotal') or 0}
        return email_budget[email]

    def _eb_take(prof: dict) -> bool:
        b = _eb(prof)
        tm_ = prof.get('task_mode') or 'all'
        kinds = ['image'] if tm_ == 'image_only' else ['video'] if tm_ == 'video_only' else ['image', 'video']
        for k in kinds:
            if b[k] > 0:
                b[k] -= 1
                return True
        return False

    all_running_ct = 0
    for pid in running_veo3:
        running_profile = pm.get(pid)
        if not running_profile:
            continue
        wm = running_profile.get('worker_mode')
        tm = running_profile.get('task_mode') or 'all'
        if bind and wm in ('api', 'dom'):
            _eb_take(running_profile)
            continue
        if wm == 'gemini_image':
            gemini_image_budget -= 1
        elif wm == 'gemini_video':
            gemini_video_budget -= 1
        elif tm == 'image_only':
            image_budget -= 1
        elif tm == 'video_only':
            video_budget -= 1
        else:
            all_running_ct += 1
    image_budget        = max(0, image_budget)
    video_budget         = max(0, video_budget)
    gemini_image_budget  = max(0, gemini_image_budget)
    gemini_video_budget  = max(0, gemini_video_budget)

    img_wait, vid_wait, all_wait, gemini_img_wait, gemini_vid_wait = [], [], [], [], []
    bound_wait = []
    for pid in waiting_pids:
        profile = pm.get(pid)
        if not profile:
            _desired_veo3.discard(pid)
            continue
        # (2026-09-17) Start thủ công lúc ngoài khung giờ chạy — không promote.
        if not in_run_hours(profile):
            _desired_veo3.discard(pid)
            continue
        wm = profile.get('worker_mode')
        tm = profile.get('task_mode') or 'all'
        if bind and wm in ('api', 'dom'):
            bound_wait.append(profile)
        elif wm == 'gemini_image':
            gemini_img_wait.append(profile)
        elif wm == 'gemini_video':
            gemini_vid_wait.append(profile)
        else:
            (img_wait if tm == 'image_only' else vid_wait if tm == 'video_only' else all_wait).append(profile)

    to_promote = []
    for profile in bound_wait:
        if len(to_promote) >= slots_free:
            break
        if _eb_take(profile):
            to_promote.append(profile)
    for profile in img_wait:
        if len(to_promote) >= slots_free or image_budget <= 0:
            break
        to_promote.append(profile)
        image_budget -= 1
    for profile in vid_wait:
        if len(to_promote) >= slots_free or video_budget <= 0:
            break
        to_promote.append(profile)
        video_budget -= 1
    for profile in gemini_img_wait:
        if len(to_promote) >= slots_free or gemini_image_budget <= 0:
            break
        to_promote.append(profile)
        gemini_image_budget -= 1
    for profile in gemini_vid_wait:
        if len(to_promote) >= slots_free or gemini_video_budget <= 0:
            break
        to_promote.append(profile)
        gemini_video_budget -= 1
    # combined_budget tính SAU KHI 2 nhóm trên đã lấy phần của mình — phần
    # CÒN LẠI mới là thứ 'all' được cạnh tranh, trừ thêm phần profile 'all'
    # ĐANG CHẠY coi như đã tiêu thụ (best-effort, không biết chính xác đang xử
    # lý loại nào). CHỈ tính từ image_budget/video_budget (pool VEO3) — 'all'
    # KHÔNG BAO GIỜ là gemini_image/gemini_video nên không đụng 2 ngân sách kia.
    combined_budget = max(0, image_budget + video_budget - all_running_ct)
    for profile in all_wait:
        if len(to_promote) >= slots_free or combined_budget <= 0:
            break
        to_promote.append(profile)
        combined_budget -= 1

    for profile in to_promote:
        pid = profile['id']
        err = _start_worker(profile)
        if err:
            log.warning(f'[dispatcher] Không thể start profile {pid}: {err}')
        else:
            log.info(f'[dispatcher] Thăng cấp profile {pid} từ waiting → running')


# ═════════════════════════════════════════════════════════════════════════════
# Auto-scale gemini (2026-07-19) — PHẢN ỨNG theo backlog thật, mirror chính xác
# _auto_scale_veo3_tick() ở trên. Bản đầu (2026-07-18, "giữ sẵn N máy luôn
# online") đã BỊ THAY THẾ theo yêu cầu tiếp theo của user: "tôi muốn như VEO khi
# có task thì mới tự bật, kết thúc thì tự tắt chứ không phải bật thủ công" — lúc
# đó gemini CHƯA có bảng backlog nên chỉ làm được "giữ sẵn N máy", không phản
# ứng được theo nhu cầu thật. Giờ ĐÃ có bảng `gemini_pending_requests`
# (backend/core/migrations.py) — khi hết máy rảnh, `_dispatch_or_queue_gemini_prompt()`
# (backend/routes/gemini.py) INSERT vào bảng này thay vì fail 503; máy nào rảnh
# ở heartbeat kế tiếp tự lấy request cũ nhất (heartbeat.py). Tick này poll
# GET /api/gemini/pending_count để biết CÓ backlog hay không, y hệt cách
# _auto_scale_veo3_tick() poll pending_by_mode.
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_gemini_pending_count() -> dict:
    """Mirror `_fetch_pending_by_mode()` — cache TTL + stale-fail-streak fallback
    về {'total':0,...} sau nhiều lần fetch lỗi liên tiếp (an toàn hơn tin cache
    cũ vô thời hạn, tránh giữ mở profile mãi mãi nếu backend đã tắt)."""
    now = time.time()
    if _gemini_pending_cache['data'] is not None and now - _gemini_pending_cache['fetched_at'] < _GEMINI_PENDING_CACHE_TTL:
        return _gemini_pending_cache['data']
    try:
        r = req_lib.get(f'{FLOW_SERVER}/api/gemini/pending_count', timeout=8)
        data = r.json()
        if data.get('success'):
            _gemini_pending_cache['data'] = data
            _gemini_pending_cache['fetched_at'] = now
            _gemini_pending_fail_streak['count'] = 0
            return data
    except Exception as e:
        log.warning(f'[auto-scale-gemini] Không lấy được pending_count: {e}')

    _gemini_pending_fail_streak['count'] += 1
    if _gemini_pending_fail_streak['count'] >= _GEMINI_PENDING_MAX_STALE_FAILS:
        return {'total': 0, 'videoTotal': 0, 'textTotal': 0, 'geminiTotal': 0, 'chatgptTotal': 0}
    return _gemini_pending_cache['data'] or {'total': 0, 'videoTotal': 0, 'textTotal': 0, 'geminiTotal': 0, 'chatgptTotal': 0}


def _gemini_profile_busy(profile: dict) -> bool:
    """Gemini worker KHÔNG set `_current_task` (khác veo3 — xem worker.py
    `_run_task_gemini()`, chỉ gọi `pm.set_status(..., 'processing')`/`'idle'`) nên
    KHÔNG thể dùng `w._current_task is None` để biết máy có đang xử lý dở hay
    không (luôn None suốt lúc chạy, sẽ đóng nhầm worker giữa chừng nếu dùng sai
    tín hiệu này). Dùng `status` bền vững phía backend thay thế."""
    return profile.get('status') == 'processing'


def _auto_scale_chat_worker_group(worker_mode: str, total_pending: int, max_conc: int, label: str):
    """Thân dùng chung cho CẢ 2 nhóm profile chat (`worker_mode='gemini'`/
    `'chatgpt'`) — tách ra làm hàm riêng (2026-08-02) vì trước đây
    `_auto_scale_gemini_tick()` CHỈ lọc `worker_mode=='gemini'`
    (`gemini_profiles = [p for p in profiles if p.get('worker_mode')=='gemini']`)
    — profile `worker_mode='chatgpt'` KHÔNG BAO GIỜ được auto-scale xét tới, nên
    dù `gemini_pending_requests` có backlog `provider='chatgpt'` thật, không
    profile ChatGPT nào tự mở để nhận — đúng bug user báo ("chọn provider
    chatgpt mà client_tool mở loại gemini và không chạy gì"). Logic mở/đóng bên
    trong GIỮ NGUYÊN 100% so với bản gốc `_auto_scale_gemini_tick()`, chỉ tổng
    quát hoá theo `worker_mode`/tổng backlog truyền vào."""
    try:
        profiles = pm.list()
    except Exception as e:
        log.warning(f'[auto-scale-{label}] pm.list() lỗi: {e}')
        return

    group_profiles = [p for p in profiles if p.get('worker_mode') == worker_mode]

    # Dừng ngay profile vừa bị tắt (enabled=0) đang chạy — không có task dở dang.
    for profile in group_profiles:
        pid = profile['id']
        if profile.get('enabled', 1) or pid not in _workers:
            continue
        if not _gemini_profile_busy(profile):
            log.info(f'[auto-scale-{label}] Profile {pid} vừa bị tắt (enabled=0) — tự đóng')
            _stop_worker(pid)

    enabled_profiles = [p for p in group_profiles if p.get('enabled', 1)]
    running_pids     = [p['id'] for p in enabled_profiles if p['id'] in _workers]
    has_backlog      = total_pending > 0

    if has_backlog:
        # 2026-07-21 (theo bug user báo "1 task gemini mà cả 2 profile gemini
        # đều bật") — `need` cap bằng min(max_conc, total_pending) — không mở
        # nhiều hơn số request thật sự đang chờ máy của ĐÚNG provider này.
        need = min(max_conc, total_pending) - len(running_pids)
        if need > 0:
            candidates = [p for p in enabled_profiles if p['id'] not in _workers]
            for profile in candidates[:need]:
                err = _start_worker(profile)
                if err:
                    log.warning(f'[auto-scale-{label}] Không thể start profile {profile["id"]}: {err}')
                else:
                    log.info(f'[auto-scale-{label}] Có backlog {label} (total={total_pending}) '
                             f'— tự mở profile {profile["id"]}')
        # `has_backlog=True` là tín hiệu "hệ thống đang bận" cho NHÓM NÀY — reset
        # đồng hồ "rảnh liên tục" của các profile trong nhóm, để lần tới backlog
        # về 0, grace period (xem nhánh dưới) luôn tính từ đúng thời điểm này.
        for profile in group_profiles:
            _gemini_idle_since.pop(profile['id'], None)
        return

    # 2026-07-21 (bug thật user báo "task gemini chạy 1 tí là dừng dù đang chạy
    # tự động"): backend giao prompt TRỰC TIẾP cho máy rảnh (set
    # `machines_media.pending_prompt`) KHÔNG qua `gemini_pending_requests`, nên
    # `has_backlog` (đọc từ bảng đó) có thể = False ngay cả khi máy VỪA được
    # giao việc thật — máy chỉ tự đổi `status='processing'` ở heartbeat KẾ TIẾP
    # của chính nó (tối đa POLL_INTERVAL=8s sau). Nếu tick này (chu kỳ 10s) rơi
    # đúng khe hở đó, `_gemini_profile_busy()` vẫn đọc `status='idle'` (cũ) →
    # trước đây đóng NGAY, giết chết task vừa giao (mất vĩnh viễn vì chưa từng
    # nằm trong hàng đợi để retry). Giờ chỉ đóng sau khi đã "trông có vẻ rảnh"
    # LIÊN TỤC qua _GEMINI_CLOSE_GRACE_SECS (20s, hơn 1 chu kỳ heartbeat của
    # worker) — đủ thời gian cho worker tự cập nhật status nếu THẬT SỰ có việc.
    now = time.time()
    for profile in enabled_profiles:
        pid = profile['id']
        if pid not in _workers or _gemini_profile_busy(profile):
            _gemini_idle_since.pop(pid, None)
            continue
        first_idle = _gemini_idle_since.get(pid)
        if first_idle is None:
            _gemini_idle_since[pid] = now
            continue
        if now - first_idle < _GEMINI_CLOSE_GRACE_SECS:
            continue
        log.info(f'[auto-scale-{label}] Hết backlog {label} — tự đóng profile {pid} '
                 f'(đã rảnh liên tục ≥{_GEMINI_CLOSE_GRACE_SECS}s)')
        _stop_worker(pid)
        _gemini_idle_since.pop(pid, None)


def _auto_scale_gemini_tick():
    """Gọi định kỳ (thread nền _veo3_dispatcher_loop). MỌI profile
    `worker_mode` ∈ ('gemini','chatgpt') `enabled=1` được tool tự quản lý —
    mirror chính xác `_auto_scale_veo3_tick()`, nhưng (2026-08-02) tách backlog
    theo `provider` (`geminiTotal`/`chatgptTotal`, xem `GET /api/gemini/pending_count`)
    để mở ĐÚNG loại profile — trước đây chỉ xét `worker_mode=='gemini'`, backlog
    provider='chatgpt' không bao giờ mở được profile ChatGPT nào.
      - Có backlog đúng provider → mở thêm profile của NHÓM ĐÓ cho tới khi đạt
        `max_concurrent_gemini_profiles` (mặc định 1, giống max_concurrent_veo3_profiles,
        áp dụng CHUNG cho cả 2 nhóm — chưa có setting riêng theo provider).
      - Hết backlog VÀ máy không đang xử lý dở (`status != 'processing'`) → đóng
        NGAY (task hiện tại vẫn chạy xong trước khi đóng, chỉ không heartbeat
        nhận thêm việc mới)."""
    if not _state._master_task_intake_enabled:
        return
    pending = _fetch_gemini_pending_count()
    settings = _fetch_global_settings()
    max_conc = max(1, int(settings.get('max_concurrent_gemini_profiles', 1) or 1))

    gemini_pending  = pending.get('geminiTotal')
    chatgpt_pending = pending.get('chatgptTotal')
    # Tương thích ngược: backend cũ (chưa có geminiTotal/chatgptTotal) — coi hết
    # 'total' là gemini (hành vi y hệt trước khi thêm split theo provider),
    # chatgpt luôn 0 (không tự mở nhầm khi backend chưa hỗ trợ).
    if gemini_pending is None:
        gemini_pending = pending.get('total') or 0
    if chatgpt_pending is None:
        chatgpt_pending = 0

    _auto_scale_chat_worker_group('gemini', gemini_pending, max_conc, 'gemini')
    _auto_scale_chat_worker_group('chatgpt', chatgpt_pending, max_conc, 'chatgpt')


def _veo3_dispatcher_loop():
    while True:
        time.sleep(10)
        try:
            _quiet_hours_tick()
        except Exception as e:
            log.warning(f'[quiet-hours] tick error: {e}')
        try:
            _auto_scale_veo3_tick()
        except Exception as e:
            log.warning(f'[auto-scale] tick error: {e}')
        try:
            _auto_scale_gemini_tick()
        except Exception as e:
            log.warning(f'[auto-scale-gemini] tick error: {e}')
        try:
            _veo3_dispatcher_tick()
        except Exception as e:
            log.warning(f'[dispatcher] tick error: {e}')


