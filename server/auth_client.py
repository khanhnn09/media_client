"""Đăng nhập vào backend chính để MỞ client_tool GUI (2026-07-18, theo yêu cầu
user: "tạo phần user pass cho client_tool, và tạo login/pass cho client_tool ghi
nhớ cho lần sau mở lại nếu đã đăng nhập").

**KHÔNG phải hệ thống tài khoản riêng của client_tool** — dùng CHUNG bảng `users`
thật của backend chính (`POST /api/auth/login`, xem `backend/routes/auth.py` +
`backend/core/auth.py` — hệ thống auth đã có sẵn cho React frontend/admin). Mọi
tài khoản active trong `users` (không phân biệt role/group) đều đăng nhập được —
mục đích ở đây chỉ là CHẶN người không có tài khoản mở được app, không áp thêm ma
trận quyền theo trang như React (client_tool có luồng chức năng riêng, không có
khái niệm "page" như projects/machines).

**"Ghi nhớ" (remember):** Flask session của backend là cookie ký bằng
`SECRET_KEY` (`session.permanent=True` khi login → mặc định 31 ngày, xem
`backend/routes/auth.py`). client_tool không phải browser nên không có cookie jar
tự động — sau khi đăng nhập thành công, giá trị cookie `session` được lưu THỦ CÔNG
vào `client_tool/auth_session.json` (gitignore, cùng chỗ với `client_identity.json`/
`local_settings.json`). Lần MỞ APP SAU: `try_resume_session()` đọc lại cookie đã
lưu, gọi `GET /api/auth/me` để xác nhận còn hợp lệ — hợp lệ thì tự vào thẳng app
(không hỏi lại username/password); hết hạn/không hợp lệ thì xoá file, quay lại
màn hình đăng nhập.

**Giới hạn group 'Tool Media' (2026-07-18, theo yêu cầu user: "client_tool thì
chỉ các user thuộc group này mới được đăng nhập"):** dù username/password đúng,
tài khoản không thuộc group `REQUIRED_GROUP` bị TỪ CHỐI ngay tại đây (không cho
vào app) — check LẶP LẠI ở CẢ `login()` (lần đăng nhập mới) LẪN
`try_resume_session()` (lần mở app sau — phòng trường hợp bị gỡ khỏi group SAU
khi đã có session lưu sẵn, không chỉ check 1 lần lúc login). Backend
(`backend/routes/worker_profiles.py::_require_tool_media`) check LẶP LẠI y hệt —
đây chỉ là gate phía client, không phải chỗ duy nhất chặn."""

import json, threading
from pathlib import Path

import requests

from .config import FLOW_SERVER, log

REQUIRED_GROUP = 'Tool Media'

_SESSION_PATH = Path(__file__).parent.parent / 'auth_session.json'
_lock = threading.RLock()
_cookie_value: str | None = None
_current_user: dict | None = None


def _reject_group_message(user: dict) -> str:
    group = user.get('groupName') or '(không có group)'
    return (f'Tài khoản "{user.get("username")}" thuộc group "{group}" — '
            f'chỉ tài khoản group "{REQUIRED_GROUP}" mới được dùng công cụ này.')


def _load_saved_cookie() -> str | None:
    if not _SESSION_PATH.exists():
        return None
    try:
        with open(_SESSION_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('session_cookie') or None
    except Exception as e:
        log.warning(f'[auth_client] Không đọc được {_SESSION_PATH}: {e}')
        return None


def _save_cookie(cookie_value: str):
    try:
        _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SESSION_PATH, 'w', encoding='utf-8') as f:
            json.dump({'session_cookie': cookie_value}, f)
    except Exception as e:
        log.warning(f'[auth_client] Không lưu được {_SESSION_PATH}: {e}')


def _clear_saved_cookie():
    try:
        _SESSION_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def try_resume_session() -> dict | None:
    """Gọi lúc app khởi động, TRƯỚC KHI hiện màn hình đăng nhập — nếu có cookie đã
    lưu từ lần trước VÀ backend vẫn xác nhận hợp lệ, tự khôi phục đăng nhập, trả
    về user dict. Trả None nếu chưa từng đăng nhập, cookie hết hạn, hoặc backend
    không kết nối được (an toàn — không tự "coi như đã đăng nhập" khi không chắc)."""
    global _cookie_value, _current_user
    with _lock:
        cookie = _load_saved_cookie()
        if not cookie:
            return None
        try:
            r = requests.get(f'{FLOW_SERVER}/api/auth/me',
                             cookies={'session': cookie}, timeout=8)
        except Exception as e:
            log.warning(f'[auth_client] resume session — không kết nối được backend: {e}')
            return None
        if r.ok:
            data = r.json()
            user = data.get('user') if data.get('success') else None
            if user:
                if user.get('groupName') != REQUIRED_GROUP:
                    # Session còn hợp lệ nhưng đã bị gỡ khỏi group 'Tool Media' —
                    # KHÔNG cho vào app, xoá session cục bộ để lần sau hỏi lại.
                    # Gán _cookie_value TRƯỚC khi gọi logout() — logout() dùng biến
                    # global này để báo backend huỷ ĐÚNG session vừa đọc từ file
                    # (không phải session cũ/None còn sót trong RAM).
                    log.warning(f'[auth_client] resume session bị từ chối: {_reject_group_message(user)}')
                    _cookie_value = cookie
                    logout()
                    return None
                _cookie_value = cookie
                _current_user = user
                return _current_user
        _clear_saved_cookie()
        return None


def login(username: str, password: str):
    """Trả (ok: bool, error_message: str, user: dict|None)."""
    global _cookie_value, _current_user
    with _lock:
        try:
            r = requests.post(f'{FLOW_SERVER}/api/auth/login',
                              json={'username': username, 'password': password}, timeout=10)
        except Exception as e:
            return False, f'Không kết nối được backend ({FLOW_SERVER}): {e}', None
        try:
            data = r.json()
        except Exception:
            data = {}
        if not r.ok or not data.get('success'):
            return False, data.get('error') or 'Sai username hoặc password', None
        cookie = r.cookies.get('session')
        if not cookie:
            return False, 'Backend không trả session cookie — kiểm tra SECRET_KEY/cấu hình session', None
        user = data.get('user') or {}
        if user.get('groupName') != REQUIRED_GROUP:
            # Username/password ĐÚNG nhưng sai group — huỷ session vừa tạo ngay
            # (không để sống sót trên backend dù không lưu cục bộ) rồi từ chối.
            try:
                requests.post(f'{FLOW_SERVER}/api/auth/logout', cookies={'session': cookie}, timeout=5)
            except Exception:
                pass
            return False, _reject_group_message(user), None
        _cookie_value = cookie
        _current_user = user
        _save_cookie(cookie)
        return True, '', _current_user


def logout():
    """Xoá session cả cục bộ lẫn phía backend (best-effort)."""
    global _cookie_value, _current_user
    with _lock:
        if _cookie_value:
            try:
                requests.post(f'{FLOW_SERVER}/api/auth/logout',
                              cookies={'session': _cookie_value}, timeout=5)
            except Exception:
                pass
        _cookie_value = None
        _current_user = None
        _clear_saved_cookie()


def get_current_user() -> dict | None:
    return _current_user


def get_auth_cookies() -> dict:
    """{'session': cookie} nếu đã đăng nhập, {} nếu chưa — đính kèm vào MỌI request
    tới `/api/worker_profiles*` (giờ yêu cầu login + group 'Tool Media', xem
    `backend/routes/worker_profiles.py::_require_tool_media`). Dùng bởi
    `managers.py::_api()`."""
    return {'session': _cookie_value} if _cookie_value else {}
