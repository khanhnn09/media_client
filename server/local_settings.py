"""Local settings — cấu hình CỤC BỘ cho máy chạy client_tool này (2026-07-17,
theo yêu cầu user: "default cục bộ không nhận từ server nữa và có thể chỉnh
sửa"). TRƯỚC ĐÂY các setting này (error_wait_secs, max_concurrent_veo3_profiles,
error_window_minutes, ...) đồng bộ 1 CHIỀU từ backend chính (FLOW_SERVER)
`GET /api/media/settings` — worker.py tự cập nhật `self._server_settings` mỗi
lần heartbeat, dispatcher.py tự fetch lại mỗi 15s. Giờ MỖI MÁY client_tool tự
quản lý ĐỘC LẬP: khởi động từ `_DEFAULT_SERVER_SETTINGS` (config.py), lưu/đọc
từ file JSON cục bộ `client_tool/local_settings.json`, chỉnh trực tiếp qua GUI
(trang Cài đặt) hoặc `PATCH /api/selenium/local_settings` — KHÔNG còn gọi
`FLOW_SERVER` cho mục đích này nữa (worker.py vẫn heartbeat để nhận task như
cũ, chỉ bỏ phần đồng bộ `settings` trong response)."""

import json, re, threading
from pathlib import Path

from .config import _DEFAULT_SERVER_SETTINGS, log

# client_tool/local_settings.json — cùng cấp với main.py/start.bat, KHÔNG phải
# repo root (mỗi máy tự có bản riêng, không commit vào git — xem .gitignore).
_LOCAL_SETTINGS_PATH = Path(__file__).parent.parent / 'local_settings.json'

# Setting kiểu bật/tắt — lưu 0/1 trong JSON, GUI dùng checkbox (không gõ số).
BOOL_KEYS = (
    'quiet_hours_enabled', 'debug_log_curl', 'generate_via_batchexecute',
    'gemini_send_via_rpc', 'google_login_check_enabled',
    'bind_tasks_to_project_email', 'api_fallback_to_dom',
)

_TRUE_STR  = {'1', 'true', 'yes', 'on', 'bật', 'bat'}
_FALSE_STR = {'0', 'false', 'no', 'off', 'tắt', 'tat', ''}


def _to_bool01(v, default) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if v else 0
    if isinstance(v, str):
        t = v.strip().lower()
        if t in _TRUE_STR:
            return 1
        if t in _FALSE_STR:
            return 0
    return 1 if default else 0


_lock  = threading.RLock()  # RLock — update_local_settings() gọi lại get_local_settings()
_cache: dict | None = None


def _clamp(settings: dict) -> dict:
    """Validate/ép kiểu — cùng ngưỡng backend từng áp dụng ở `PUT /api/media/settings`
    (`backend/routes/tasks.py`), giờ áp dụng cục bộ vì không còn qua backend nữa."""
    s = dict(settings)

    int_min1_keys = ('error_wait_secs', 'task_delay_secs', 'download_wait_secs',
                      'error_count_before_refresh', 'refresh_count_before_new_project',
                      'error_sleep_secs', 'error_window_minutes', 'error_window_max_errors',
                      'reconcile_wait_secs', 'reconcile_max_rounds',
                      'batch_fail_count_before_cleanup', 'batch_fail_count_before_sleep',
                      'reconcile_lookback_secs', 'reconcile_retry_lookback_secs')
    for k in int_min1_keys:
        if k in s:
            try:
                s[k] = max(1, int(s[k]))
            except (TypeError, ValueError):
                s[k] = _DEFAULT_SERVER_SETTINGS.get(k, 1)

    # (2026-08-20) Bậc thang escalation THEO BATCH — ngưỡng "ngủ" PHẢI lớn hơn
    # ngưỡng "dọn cookie + click Create", nếu không bước dọn dẹp KHÔNG BAO GIỜ
    # có cơ hội chạy (batch chạm ngưỡng ngủ trước, worker thoát luôn). Đặt sai
    # (≤) thì tự nâng ngưỡng ngủ lên đúng 1 bậc trên ngưỡng dọn dẹp thay vì im
    # lặng chấp nhận cấu hình vô hiệu hoá mất 1 bậc thang.
    if 'batch_fail_count_before_cleanup' in s and 'batch_fail_count_before_sleep' in s:
        if s['batch_fail_count_before_sleep'] <= s['batch_fail_count_before_cleanup']:
            s['batch_fail_count_before_sleep'] = s['batch_fail_count_before_cleanup'] + 1

    if 'max_concurrent_veo3_profiles' in s:
        try:
            s['max_concurrent_veo3_profiles'] = max(1, min(int(s['max_concurrent_veo3_profiles']), 50))
        except (TypeError, ValueError):
            s['max_concurrent_veo3_profiles'] = _DEFAULT_SERVER_SETTINGS['max_concurrent_veo3_profiles']

    if 'max_concurrent_gemini_profiles' in s:
        try:
            s['max_concurrent_gemini_profiles'] = max(1, min(int(s['max_concurrent_gemini_profiles']), 50))
        except (TypeError, ValueError):
            s['max_concurrent_gemini_profiles'] = _DEFAULT_SERVER_SETTINGS['max_concurrent_gemini_profiles']

    # 0 = tắt tính năng tự luân chuyển project (khác các int_min1_keys ở trên,
    # vốn không cho phép 0) — xem worker.py::_rotate_project_if_full().
    if 'max_project_media_items' in s:
        try:
            s['max_project_media_items'] = max(0, int(s['max_project_media_items']))
        except (TypeError, ValueError):
            s['max_project_media_items'] = _DEFAULT_SERVER_SETTINGS['max_project_media_items']

    for k in ('step_delay_min_secs', 'step_delay_max_secs',
              'thread_stagger_min_secs', 'thread_stagger_max_secs'):
        if k in s:
            try:
                s[k] = max(0.0, float(s[k]))
            except (TypeError, ValueError):
                s[k] = _DEFAULT_SERVER_SETTINGS.get(k, 1.0)

    if 'error_patterns' in s:
        v = s['error_patterns']
        if isinstance(v, str):
            s['error_patterns'] = [p.strip() for p in v.split(',') if p.strip()]
        elif not isinstance(v, list):
            s['error_patterns'] = []

    # Khung giờ không nhận task (2026-07-20) — quiet_hours_enabled xử lý chung ở
    # BOOL_KEYS bên dưới; quiet_hours_start/end là chuỗi "HH:MM" — validate bằng regex, sai định dạng
    # thì rơi về mặc định thay vì lưu giá trị vô nghĩa khiến _is_in_quiet_hours()
    # (dispatcher.py) tính sai giờ.
    _hhmm_re = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
    for k in ('quiet_hours_start', 'quiet_hours_end'):
        if k in s:
            v = str(s[k]).strip()
            s[k] = v if _hhmm_re.match(v) else _DEFAULT_SERVER_SETTINGS[k]

    # Bool — lưu dạng 0/1 (tương thích file cũ), GUI hiển thị bằng checkbox.
    # Nhận int/bool/chuỗi ("1","0","true","false","on","off"...).
    for k in BOOL_KEYS:
        if k in s:
            s[k] = _to_bool01(s[k], _DEFAULT_SERVER_SETTINGS.get(k, 0))

    return s


def get_local_settings() -> dict:
    """Trả settings hiện tại (dict mới, an toàn để caller tự sửa mà không ảnh
    hưởng cache). Lần đầu: load từ file JSON cục bộ (merge lên
    `_DEFAULT_SERVER_SETTINGS` — key nào file chưa có, vd bản cũ chưa từng lưu
    hoặc field mới thêm sau này, tự lấy default). Sau đó cache thẳng trong RAM,
    không đọc lại file mỗi lần gọi."""
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        merged = dict(_DEFAULT_SERVER_SETTINGS)
        if _LOCAL_SETTINGS_PATH.exists():
            try:
                with open(_LOCAL_SETTINGS_PATH, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    merged.update(saved)
            except Exception as e:
                log.warning(f'[local_settings] Không đọc được {_LOCAL_SETTINGS_PATH}: {e}')
        merged = _clamp(merged)
        _cache = merged
        return dict(merged)


def update_local_settings(updates: dict) -> dict:
    """Merge `updates` (chỉ nhận key đã biết trong _DEFAULT_SERVER_SETTINGS —
    bỏ qua key lạ) vào settings hiện tại, validate/clamp, ghi file, cập nhật
    cache RAM ngay. LƯU Ý: worker ĐANG CHẠY giữ bản `self._server_settings` đã
    copy lúc `__init__` (xem worker.py) — đổi ở đây chỉ áp dụng cho worker MỚI
    start hoặc lần đọc tiếp theo của dispatcher (`_fetch_global_settings`), Không
    hot-reload vào các worker đang sống giữa chừng (chấp nhận được — các
    setting này chủ yếu ảnh hưởng hành vi lúc bắt đầu task/escalation, không
    cần tức thời tuyệt đối)."""
    global _cache
    with _lock:
        current = get_local_settings()
        for k, v in updates.items():
            if k in _DEFAULT_SERVER_SETTINGS:
                current[k] = v
        current = _clamp(current)
        try:
            _LOCAL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOCAL_SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f'[local_settings] Không lưu được {_LOCAL_SETTINGS_PATH}: {e}')
        _cache = current
        return dict(current)


def reset_local_settings() -> dict:
    """Trả về mặc định gốc (_DEFAULT_SERVER_SETTINGS), ghi đè file/cache."""
    return update_local_settings(dict(_DEFAULT_SERVER_SETTINGS))
