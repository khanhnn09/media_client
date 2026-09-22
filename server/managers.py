"""Quản lý profile/extension — em (extensions), pm (profiles).

(2026-07-17) KHÔNG còn kết nối DB trực tiếp — mọi thao tác giờ gọi qua HTTP API
`/api/worker_profiles`/`/api/worker_extensions` trên backend chính (xem
`backend/routes/worker_profiles.py`, migration `_ensure_worker_profile_tables()`
trong `backend/core/migrations.py`). Lý do: client_tool chạy trên nhiều máy khác
nhau (worker machine) — TRƯỚC ĐÂY mỗi máy đó cần network line-of-sight + credentials
MariaDB thật (qua `db.py`/`.env` DB_*), một rủi ro bảo mật/vận hành không cần thiết.
Giờ máy chạy client_tool chỉ cần gọi HTTP tới `FLOW_API_URL` — không cần biết gì về
DB phía sau.

Interface `pm.xxx()`/`em.xxx()` GIỮ NGUYÊN 100% (tên hàm, tham số, kiểu trả về) so
với bản dùng SQL trực tiếp trước đây — dispatcher.py/routes.py/worker.py/
chrome_utils.py (47+ call site) KHÔNG cần sửa gì.

`profile_dir` (đường dẫn Chrome user-data-dir cục bộ) và validate `manifest.json`
của extension vẫn HOÀN TOÀN CỤC BỘ — chỉ máy đang chạy mới biết filesystem của
chính nó, backend chỉ lưu/trả lại string, không đụng filesystem."""

import os, shutil, sqlite3
from pathlib import Path
from urllib.parse import quote

from .config import PROFILES_DIR, FLOW_SERVER, req_lib, log
from .client_identity import client_headers
from .auth_client import get_auth_cookies
from .run_hours import format_run_hours


def _api(method: str, path: str, *, body=None, timeout=10, quiet=False):
    """Gọi API `/api/worker_profiles`/`/api/worker_extensions` trên backend.
    Raise RuntimeError nếu lỗi, TRỪ KHI quiet=True (log warning + trả None) — dùng
    cho các call best-effort (vd set_status gọi rất thường xuyên, không nên làm
    worker crash chỉ vì 1 lần mạng chập chờn — khớp hành vi try/except cũ quanh
    get_conn()).

    Header `X-Client-Id`/`X-Client-Name` (2026-07-18) — đính kèm MỌI request để
    backend scope theo installation client_tool này (xem client_identity.py và
    CHANGELOG "client_tool: scope profile/extension theo client_id").

    Cookie session (2026-07-18) — route `/api/worker_profiles*` giờ yêu cầu đăng
    nhập + group 'Tool Media' (`backend/routes/worker_profiles.py::
    _require_tool_media`, xem CHANGELOG "client_tool: group Tool Media..."). Đính
    kèm luôn cho MỌI request (kể cả `/api/worker_extensions*` — vô hại, route đó
    không đọc cookie) để đơn giản hoá, không cần phân biệt theo path."""
    url = f'{FLOW_SERVER}{path}'
    try:
        kw: dict = {'timeout': timeout, 'headers': client_headers(), 'cookies': get_auth_cookies()}
        if body is not None:
            kw['json'] = body
        r = getattr(req_lib, method.lower())(url, **kw)
        try:
            data = r.json()
        except Exception:
            data = {}
        if not r.ok or not data.get('success', True):
            msg = data.get('error') or f'HTTP {r.status_code}'
            raise RuntimeError(f'{method} {path} lỗi: {msg}')
        return data
    except Exception as e:
        if quiet:
            log.warning(f'[managers] {method} {path} lỗi: {e}')
            return None
        raise


# Extension Manager
# ═════════════════════════════════════════════════════════════════════════════

class _ExtensionManager:

    def list(self) -> list:
        r = _api('GET', '/api/worker_extensions')
        return r.get('extensions', [])

    def add(self, name: str, path: str) -> int:
        path = path.strip().rstrip('\\').rstrip('/')
        manifest = os.path.join(path, 'manifest.json')
        if not os.path.isfile(manifest):
            raise ValueError(f'manifest.json không tìm thấy tại: {path}')
        r = _api('POST', '/api/worker_extensions', body={'name': name, 'path': path})
        return r.get('id', 0)

    def set_enabled(self, ext_id: int, enabled: bool):
        _api('PATCH', f'/api/worker_extensions/{ext_id}', body={'enabled': bool(enabled)})

    def remove(self, ext_id: int):
        _api('DELETE', f'/api/worker_extensions/{ext_id}')

    def active_paths(self) -> list:
        return [e['ext_path'] for e in self.list() if e.get('enabled')]


em = _ExtensionManager()


# ── "Xóa cache" — reset profile Chrome về TRẮNG (2026-08-10) ────────────────
# Lịch sử: bản đầu chỉ xoá cache/cookie/history theo 3 checkbox của Chrome
# ("thêm xóa cache cho profile: cache, cookie, browing history - all time"),
# rồi phát hiện+fix thiếu `Web Data` (refresh token đăng nhập Google CẤP
# TRÌNH DUYỆT — "Account consistency", khác hẳn cookie từng site — Chrome tự
# dùng token đó SINH LẠI cookie mỗi lần khởi động nên chỉ xoá Cookies không
# đủ để thật sự đăng xuất). Theo yêu cầu tiếp theo của user "còn gì cần xóa
# thì xóa sạch coi như 1 profile trắng" — bỏ hẳn cách chọn lọc từng
# file/nhóm (dễ sót — đã sót đúng 1 lần rồi), đổi sang XOÁ SẠCH TOÀN BỘ NỘI
# DUNG `profile_dir` (không chỉ subfolder `Default/` — còn `Local State` ở
# gốc user-data-dir: os_crypt key, DNS cache, browser-level prefs...) — Chrome
# tự tạo lại mọi thứ từ đầu ở lần mở kế tiếp, y hệt lần đầu tiên tạo profile
# (đăng nhập, extension load qua flag/path NGOÀI profile_dir nên không ảnh
# hưởng — xem đầu file). KHÔNG xoá record DB (`selenium_profiles` row) —
# khác `delete(delete_data=True)` bên dưới (xoá LUÔN profile khỏi danh sách),
# đây chỉ làm sạch dữ liệu Chrome, giữ nguyên cấu hình
# name/worker_mode/task_mode/project_url/... của profile.
#
# ⚠️ ĐĂNG XUẤT KHỎI GOOGLE + MẤT MỌI DỮ LIỆU CHROME của profile (cache/
# cookie/history/mật khẩu đã lưu/bookmark/cài đặt...) — không hoàn tác được.
# GUI (`profiles_page.py`) PHẢI cảnh báo rõ trước khi gọi — hàm này KHÔNG tự
# hỏi lại, chỉ thực thi.
def _wipe_chrome_profile_dir(profile_dir: str) -> dict:
    """Xoá SẠCH toàn bộ nội dung `profile_dir` (--user-data-dir) — coi như
    Chrome CHƯA TỪNG chạy ở đây, "profile trắng" đúng nghĩa đen. Giữ nguyên
    CHÍNH thư mục `profile_dir` (chỉ xoá nội dung bên trong) để không phải
    tạo lại quyền/đường dẫn — Chrome tự tạo lại toàn bộ cấu trúc con khi
    khởi động với `--user-data-dir` trỏ vào thư mục rỗng này."""
    removed, errors = [], []
    for entry in os.listdir(profile_dir):
        full = os.path.join(profile_dir, entry)
        try:
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                os.remove(full)
            removed.append(entry)
        except Exception as e:
            errors.append(f'{entry}: {e}')
    return {'removed': removed, 'errors': errors}


# (2026-08-12) Tên thư mục cache THUẦN của Chrome — an toàn xoá, Chrome tự
# tạo lại từ đầu, KHÔNG chứa cookie/session/mật khẩu đã lưu. Dùng bởi
# `_clear_chrome_cache_only()` — KHÁC HẲN `_wipe_chrome_profile_dir()` ở trên
# (xoá SẠCH toàn bộ, kể cả `Cookies`/`Web Data`/`Login Data`).
_CACHE_ONLY_DIR_NAMES = {
    'Cache', 'Cache2', 'Code Cache', 'GPUCache', 'DawnCache',
    'DawnGraphiteCache', 'GrShaderCache', 'ShaderCache', 'Media Cache',
}


def _clear_chrome_cache_only(profile_dir: str) -> dict:
    """Xoá CHỈ các thư mục cache thuần (HTTP/GPU/shader/code cache, quét
    NÔNG — tại `profile_dir` gốc VÀ 1 cấp con như `Default/`) — GIỮ NGUYÊN
    `Cookies`/`Web Data`/`Login Data`/`History`/mọi thứ khác, KHÔNG đăng xuất
    Google. Dùng cho luồng TỰ PHỤC HỒI khi profile rơi vào `'sleeping'` do lỗi
    liên tiếp (`_clear_profile_if_sleeping()`, worker.py) — theo yêu cầu user
    "lỗi nhiều vào trạng thái ngủ tự xóa cache không xóa cookie" (2026-08-12):
    tự phục hồi sau khi ngủ KHÔNG được ép re-login mỗi lần (tốn thời gian,
    rủi ro Google chặn/đòi xác minh nếu đăng nhập lặp lại quá thường xuyên từ
    cùng 1 script) — chỉ cần dọn cache có khả năng đã hỏng/gây lỗi render,
    giữ nguyên session đang có. Nút "Làm mới profile" THỦ CÔNG (GUI, có cảnh
    báo mất đăng nhập) vẫn dùng `_wipe_chrome_profile_dir()` như cũ, không
    đổi — đây CHỈ là 1 hàm mới song song, không thay thế."""
    removed, errors = [], []
    try:
        top_entries = os.listdir(profile_dir)
    except OSError as e:
        return {'removed': [], 'errors': [f'{profile_dir}: {e}']}
    bases = [profile_dir] + [
        os.path.join(profile_dir, d) for d in top_entries
        if os.path.isdir(os.path.join(profile_dir, d))
    ]
    for base in bases:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for name in entries:
            if name not in _CACHE_ONLY_DIR_NAMES:
                continue
            full = os.path.join(base, name)
            if not os.path.isdir(full):
                continue
            try:
                shutil.rmtree(full, ignore_errors=True)
                removed.append(os.path.relpath(full, profile_dir))
            except Exception as e:
                errors.append(f'{name}: {e}')
    return {'removed': removed, 'errors': errors}


# (2026-08-14) Theo yêu cầu user "khi lỗi quá nhiều ngoài xóa cache xóa thêm
# các cookie không liên quan đăng nhập" — mở rộng luồng tự phục hồi
# (`_clear_profile_if_sleeping()`, worker.py) thêm 1 bước: xoá cookie "rác".
#
# ⚠️ (2026-08-17, ĐỔI HƯỚNG) — bản đầu giữ NGUYÊN CẢ DOMAIN `google`/`chatgpt`/
# `openai` (mọi cookie có `host_key` chứa 1 trong 3 từ khoá này). User báo
# đúng hệ quả: "clear chỉ xóa cache và 0 cookie" — vì profile chỉ chạy Flow/
# Gemini, GẦN NHƯ TOÀN BỘ cookie của nó vốn dĩ đã thuộc domain `*.google.com`
# (kể cả cookie KHÔNG liên quan đăng nhập — `NID`/`CONSENT`/`1P_JAR`/`AEC`/
# `DV`/`OTZ`/`ANID`... là analytics/consent/personalization, không cần để
# giữ session) — giữ NGUYÊN CẢ DOMAIN đồng nghĩa GIỮ NGUYÊN GẦN NHƯ TẤT CẢ,
# khớp đúng `removedCookies=0` user thấy. User làm rõ tiêu chí ĐÚNG cần dùng:
# "cookie xóa tất cả để lại SSID" — xoá SẠCH, chỉ giữ lại ĐÚNG họ cookie
# "SID" (Google dùng cụm 5-10 cookie tên SID/HSID/SSID/APISID/SAPISID +
# biến thể `__Secure-1P*`/`__Secure-3P*` để mang phiên đăng nhập cross-Google-
# service thật — ĐÂY MỚI LÀ THỨ CẦN GIỮ, không phải cả domain) — lọc theo TÊN
# cookie (cột `name`), KHÔNG còn theo domain (`host_key`) cho Google nữa.
_GOOGLE_LOGIN_COOKIE_NAMES = (
    'SID', 'HSID', 'SSID', 'APISID', 'SAPISID', 'LSID',
    '__Secure-1PSID', '__Secure-3PSID',
    '__Secure-1PAPISID', '__Secure-3PAPISID',
    '__Secure-1PSIDCC', '__Secure-3PSIDCC',
    '__Secure-1PSIDTS', '__Secure-3PSIDTS',
)
# ChatGPT/OpenAI — GIỮ NGUYÊN theo domain (user CHƯA yêu cầu đổi phần này,
# và KHÔNG có xác nhận thật tên cookie session-token cụ thể của ChatGPT để
# đổi sang lọc theo tên như Google — đoán sai tên sẽ vô tình đăng xuất
# ChatGPT, rủi ro cao hơn lợi ích).
_LOGIN_COOKIE_HOST_KEYWORDS = ('chatgpt', 'openai')


def _purge_non_login_cookies(profile_dir: str) -> dict:
    """⚠️ (2026-08-18) TẠM KHÔNG CÒN CALLER NÀO — `ProfileManager.clear_cache_only()`
    (dưới đây, dùng bởi `worker.py::_clear_profile_if_sleeping()`) đã TẠM TẮT
    lời gọi hàm này theo yêu cầu user "số 2 khi sleep thì tạm không dùng đến"
    — GIỮ NGUYÊN code (không xoá) để dễ bật lại sau nếu cần. Cơ chế xoá
    cookie "mỗi batch" (`worker.py::_cdp_clear_cache_and_cookies()`) đã ĐỔI
    HƯỚNG LẦN 2 (2026-08-18) sang lọc THUẦN THEO DOMAIN (`_batch_clean_
    should_delete_cookie()`, sống trong `worker.py` — chỉ xoá cookie thuộc
    domain `labs.google`, không còn danh sách tên "giữ lại" nào), KHÔNG gọi
    hàm này/2 hằng số `_GOOGLE_LOGIN_COOKIE_NAMES`/`_LOGIN_COOKIE_HOST_KEYWORDS`
    ngay trên.

    Xoá cookie "rác" khỏi SQLite Cookies DB — GIỮ LẠI đúng những gì cần để
    KHÔNG đăng xuất: (1) Google — CHỈ giữ đúng họ cookie "SID"
    (`_GOOGLE_LOGIN_COOKIE_NAMES`, lọc theo TÊN cookie, KHÔNG theo domain —
    xem giải thích ở hằng số trên), MỌI cookie khác dù thuộc domain
    `*.google.com` hay không đều bị xoá; (2) ChatGPT/OpenAI — GIỮ NGUYÊN cả
    domain (`_LOGIN_COOKIE_HOST_KEYWORDS`, chưa đổi). Dùng SONG SONG với
    `_clear_chrome_cache_only()` cho luồng TỰ PHỤC HỒI khi profile 'sleeping'
    do lỗi liên tiếp — chỉ xoá cache đôi khi CHƯA đủ nếu nguyên nhân lỗi là
    cookie rác/hỏng (tracking cookie, cookie stale của 1 lần thử nghiệm cũ,
    site quảng cáo/analytics gây lỗi JS...).

    Quét cả 2 vị trí Chrome có thể lưu Cookies DB tuỳ version (`Default/
    Network/Cookies` — Chrome mới, `Default/Cookies` — fallback bản cũ).
    Best-effort — DB không tồn tại (profile chưa từng mở) hoặc lỗi đọc/ghi
    chỉ ghi vào `errors`, KHÔNG raise (gọi trong `finally` của worker loop,
    không được làm crash) — NHƯNG caller (`worker.py::_clear_profile_if_sleeping()`)
    PHẢI tự log `errors` ra, không được im lặng nuốt (bài học từ chính bug
    "0 cookie" — trước đây `errors` có populate nhưng KHÔNG BAO GIỜ được log,
    khiến không phân biệt được '0 cookie vì đúng là không có gì để xoá' với
    '0 cookie vì DB bị khoá/lỗi đọc')."""
    removed_count = 0
    errors = []
    candidates = [
        os.path.join(profile_dir, 'Default', 'Network', 'Cookies'),
        os.path.join(profile_dir, 'Default', 'Cookies'),
    ]
    for db_path in candidates:
        if not os.path.isfile(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                name_placeholders = ', '.join('?' for _ in _GOOGLE_LOGIN_COOKIE_NAMES)
                host_conds = ' AND '.join('host_key NOT LIKE ?' for _ in _LOGIN_COOKIE_HOST_KEYWORDS)
                # Giữ lại (KHÔNG xoá) nếu: tên cookie thuộc họ SID của Google,
                # HOẶC domain thuộc ChatGPT/OpenAI — xoá mọi cookie còn lại.
                where = f'name NOT IN ({name_placeholders}) AND {host_conds}'
                params = list(_GOOGLE_LOGIN_COOKIE_NAMES) + [f'%{kw}%' for kw in _LOGIN_COOKIE_HOST_KEYWORDS]
                cur.execute(f'DELETE FROM cookies WHERE {where}', params)
                removed_count += max(cur.rowcount, 0)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            errors.append(f'{os.path.relpath(db_path, profile_dir)}: {e}')
    return {'removedCookies': removed_count, 'errors': errors}


def _purge_all_cookies(profile_dir: str) -> dict:
    """(2026-08-20) Xoá SẠCH TOÀN BỘ cookie khỏi Cookies DB — KHÔNG chừa lại gì
    (kể cả họ SID đăng nhập Google), khác hẳn `_purge_non_login_cookies()` ngay
    trên (giữ lại cookie đăng nhập) và khác `_wipe_chrome_profile_dir()` (xoá
    NGUYÊN thư mục profile, mất luôn cả `Login Data`/`Preferences`/extension...).

    Dùng CHỈ cho bậc thang escalation THEO BATCH mới (`worker.py::
    _clear_profile_if_sleeping()` khi `_sleep_wipe_all_cookies=True`) — theo yêu
    cầu user "trong lúc ngủ xóa tất cả cookie - cache -> rồi check login -> đăng
    nhập vào project lại như trước": ĐÚNG nghĩa đen "tất cả cookie", chấp nhận
    mất đăng nhập vì bước ngay sau đó (`_ensure_google_login()`, được ÉP chạy)
    tự đăng nhập lại bằng email/mật khẩu đã lưu trong profile.

    Giữ nguyên `Login Data` (mật khẩu Chrome đã lưu) và mọi thứ khác trong
    `profile_dir` — chỉ dọn đúng bảng `cookies`, nhẹ hơn nhiều so với wipe cả
    thư mục và không phá cấu hình Chrome của profile.

    Quét cả 2 vị trí Cookies DB tuỳ version Chrome (mirror `_purge_non_login_
    cookies()`). Best-effort — DB không tồn tại/bị khoá chỉ ghi vào `errors`,
    KHÔNG raise (caller đang ở `finally` của worker loop). Caller PHẢI tự log
    `errors` (cùng bài học "0 cookie" đã ghi ở `_purge_non_login_cookies()`)."""
    removed_count = 0
    errors = []
    candidates = [
        os.path.join(profile_dir, 'Default', 'Network', 'Cookies'),
        os.path.join(profile_dir, 'Default', 'Cookies'),
    ]
    for db_path in candidates:
        if not os.path.isfile(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute('DELETE FROM cookies')
                removed_count += max(cur.rowcount, 0)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            errors.append(f'{os.path.relpath(db_path, profile_dir)}: {e}')
    return {'removedCookies': removed_count, 'errors': errors}


# (2026-08-23) "Cookies and other site data" — bản ĐẦY ĐỦ, khớp đúng thứ nút
# "Clear browsing data → Cookies and other site data" của Chrome xoá.
#
# ⚠️ BUG THẬT user báo: *"clear cookie lúc ngủ ko sạch sẽ hay sao, tôi phải
# clear bằng tay tất cả cookie trên trình duyệt thì không bị labs.google bắt
# lỗi"*. `_purge_all_cookies()` (bản cũ) CHỈ `DELETE FROM cookies` trong SQLite
# — nhưng cookie chỉ là 1 trong ~10 kho trạng thái mỗi origin. labs.google (và
# lớp chống lạm dụng của Google nói chung) còn đọc/ghi:
#   - `Local Storage`/`Session Storage`/`IndexedDB` — session, cờ trạng thái,
#     dấu vết phiên trước.
#   - `Service Worker` — script nền ĐÃ ĐĂNG KÝ, tự sống lại sau khi xoá cookie
#     và có kho `CacheStorage` riêng.
#   - `Trust Tokens` (Private State Tokens) — token CHỐNG GIAN LẬN do chính
#     Google phát hành, đúng thứ dùng để nhận diện "trình duyệt này đáng ngờ".
#   - `Network Persistent State`/`Reporting and NEL`/`TransportSecurity` — dấu
#     vết mạng gắn với origin.
# Xoá mỗi bảng `cookies` để lại toàn bộ số đó ⇒ nhìn thì "đã xoá cookie" nhưng
# site vẫn nhận ra profile cũ — đúng hiện tượng user gặp.
#
# GIỮ LẠI có chủ đích (KHÔNG được đụng):
#   - `Login Data`/`Login Data For Account` — mật khẩu Chrome đã lưu, cần cho
#     `_ensure_google_login()` tự đăng nhập lại ở lần chạy sau.
#   - `Local State` (ở GỐC profile_dir) — chứa KHOÁ os_crypt để giải mã
#     `Login Data`; xoá là mất luôn khả năng đọc mật khẩu đã lưu.
#   - `Preferences`/`Secure Preferences`/`Web Data`/`History`/`Bookmarks`/
#     `Extensions` — cấu hình profile, không phải site data.
_SITE_DATA_DIR_NAMES = {
    'Local Storage', 'Session Storage', 'IndexedDB', 'Service Worker',
    'databases', 'Storage', 'blob_storage', 'File System', 'WebStorage',
    'Shared Dictionary', 'Platform Notifications', 'Session Storage-journal',
}
_SITE_DATA_FILE_NAMES = {
    'Cookies', 'Cookies-journal', 'Cookies-wal', 'Cookies-shm',
    'Trust Tokens', 'Trust Tokens-journal',
    'Network Persistent State', 'Reporting and NEL', 'TransportSecurity',
    'SCT Auditing Pending Reports', 'Origin Bound Certs', 'Origin Bound Certs-journal',
    'QuotaManager', 'QuotaManager-journal',
}


def _purge_all_site_data(profile_dir: str) -> dict:
    """Xoá SẠCH cookie + MỌI kho site-data khác (xem ghi chú hằng số ở trên).

    XOÁ HẲN FILE `Cookies` thay vì `DELETE FROM cookies`: dứt điểm hơn (không
    để lại residue trong WAL/journal, không cần VACUUM), và Chrome tự tạo lại
    DB rỗng ở lần khởi động sau. Bậc thang này vốn đã CHẤP NHẬN mất đăng nhập
    nên không có gì để giữ lại trong đó.

    Quét GỐC `profile_dir`, mọi thư mục con 1 cấp (`Default/`, `Profile 1/`...)
    và `*/Network/` — cùng cách quét NÔNG của `_clear_chrome_cache_only()`.

    Best-effort: mục nào xoá lỗi (file bị khoá) chỉ ghi `errors`, KHÔNG raise —
    caller đang ở `finally` của worker loop. Caller PHẢI log `errors`."""
    removed, errors = [], []
    try:
        top = os.listdir(profile_dir)
    except OSError as e:
        return {'removed': [], 'errors': [f'{profile_dir}: {e}']}

    bases = [profile_dir]
    for d in top:
        sub = os.path.join(profile_dir, d)
        if not os.path.isdir(sub):
            continue
        bases.append(sub)
        net = os.path.join(sub, 'Network')     # Chrome mới tách cookie/net state ra đây
        if os.path.isdir(net):
            bases.append(net)

    for base in bases:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for name in entries:
            full = os.path.join(base, name)
            is_dir = os.path.isdir(full)
            if is_dir and name not in _SITE_DATA_DIR_NAMES:
                continue
            if not is_dir and name not in _SITE_DATA_FILE_NAMES:
                continue
            try:
                if is_dir:
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    os.remove(full)
                removed.append(os.path.relpath(full, profile_dir))
            except Exception as e:
                errors.append(f'{os.path.relpath(full, profile_dir)}: {e}')
    return {'removed': removed, 'errors': errors}



# ═════════════════════════════════════════════════════════════════════════════
# Profile Manager
# ═════════════════════════════════════════════════════════════════════════════

class _ProfileManager:

    def list(self) -> list:
        r = _api('GET', '/api/worker_profiles')
        return r.get('profiles', [])

    def get(self, profile_id: int):
        r = _api('GET', f'/api/worker_profiles/{profile_id}', quiet=True)
        return (r or {}).get('profile')

    def create(self, name: str, email='', password='', display_name='', project_url='', task_mode='all',
               worker_mode='api', notes='', gemini_attach_timeout=180,
               gemini_response_timeout=300, max_concurrent=1, enabled=1,
               gemini_max_concurrent_tabs=1, gemini_tab_switch_interval=0.5,
               chatgpt_attach_timeout=60, chatgpt_response_timeout=300,
               proxy_server='', run_hours='') -> int:
        profile_dir = str(Path(PROFILES_DIR) / name)
        os.makedirs(profile_dir, exist_ok=True)  # cục bộ — user-data-dir chỉ có ý nghĩa trên máy này
        # (2026-08-07) 'gemini_video' — BUG THẬT đã sót ở đây từ lúc thêm loại
        # profile này: whitelist ở managers.py là RIÊNG (khác whitelist ở
        # backend/routes/worker_profiles.py, KHÔNG dùng chung) — thiếu
        # 'gemini_video' khiến MỌI profile tạo mới qua GUI (chọn Loại = "🎬
        # Gemini — Tạo Video") bị ÂM THẦM reset về 'api' ngay tại đây, TRƯỚC
        # khi gửi lên backend — phát hiện lúc thêm field mật khẩu (2026-08-08),
        # xem CLAUDE.md §11.30. (2026-09-09) Thêm 'gemini_image' NGAY TỪ ĐẦU
        # lúc viết engine mới này — tránh lặp lại đúng bug đã ghi ở trên.
        if worker_mode not in ('api', 'dom', 'gemini', 'chatgpt', 'gemini_video', 'gemini_image'):
            worker_mode = 'api'
        if task_mode not in ('all', 'image_only', 'video_only'):
            task_mode = 'all'
        max_concurrent = max(1, min(int(max_concurrent or 1), 10))
        gemini_max_concurrent_tabs = max(1, min(int(gemini_max_concurrent_tabs or 1), 10))
        gemini_tab_switch_interval = max(0.1, min(float(gemini_tab_switch_interval or 0.5), 10.0))
        r = _api('POST', '/api/worker_profiles', body={
            'profile_name': name, 'display_name': display_name, 'profile_dir': profile_dir,
            'account_email': email, 'account_password': password,
            'proxy_server': (proxy_server or '').strip(),
            'run_hours': format_run_hours(run_hours),
            'project_url': project_url, 'task_mode': task_mode,
            'worker_mode': worker_mode, 'notes': notes,
            'gemini_attach_timeout': gemini_attach_timeout,
            'gemini_response_timeout': gemini_response_timeout,
            'max_concurrent': max_concurrent, 'enabled': 1 if enabled else 0,
            'gemini_max_concurrent_tabs': gemini_max_concurrent_tabs,
            'gemini_tab_switch_interval': gemini_tab_switch_interval,
            'chatgpt_attach_timeout': chatgpt_attach_timeout,
            'chatgpt_response_timeout': chatgpt_response_timeout,
        })
        return r.get('id', 0)

    def update(self, profile_id: int, **fields):
        allowed = {'profile_name', 'display_name', 'account_email', 'account_password', 'project_url',
                   'task_mode', 'worker_mode', 'notes', 'enabled', 'proxy_server', 'run_hours',
                   'gemini_attach_timeout', 'gemini_response_timeout', 'max_concurrent',
                   'gemini_max_concurrent_tabs', 'gemini_tab_switch_interval',
                   'chatgpt_attach_timeout', 'chatgpt_response_timeout'}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if 'run_hours' in sets:
            sets['run_hours'] = format_run_hours(sets['run_hours'])
        if not sets:
            return
        _api('PATCH', f'/api/worker_profiles/{profile_id}', body=sets)

    def get_shared_project_url(self, account_email: str) -> str:
        """(2026-09-11) "url ghi nhớ project flow lưu theo email, dùng chung
        nhiều PC" — tra `GET /api/worker_profiles/project_url_by_email` (bảng
        GLOBAL `flow_account_projects`, KHÔNG scope theo client_id/owner như
        mọi API khác ở class này — dùng chung CHO MỌI máy có CÙNG
        `account_email`, đúng chủ ý). Trả `''` nếu thiếu email/chưa có gì
        remembered/lỗi mạng (best-effort — `quiet=True`, KHÔNG raise: thiếu
        chỗ này chỉ nghĩa là worker sẽ tự tạo project mới như hành vi cũ, chứ
        không phải lỗi cần chặn task)."""
        email = (account_email or '').strip()
        if not email:
            return ''
        r = _api('GET', f'/api/worker_profiles/project_url_by_email?email={quote(email)}', quiet=True)
        return ((r or {}).get('projectUrl') or '').strip()

    def delete(self, profile_id: int, delete_data=False):
        if delete_data:
            profile = self.get(profile_id)
            profile_dir = (profile or {}).get('profile_dir')
            if profile_dir and os.path.isdir(profile_dir):
                shutil.rmtree(profile_dir, ignore_errors=True)  # cục bộ
        _api('DELETE', f'/api/worker_profiles/{profile_id}')

    def clear_browser_data(self, profile_id: int) -> dict:
        """(2026-08-10, mở rộng theo yêu cầu user "còn gì cần xóa thì xóa
        sạch coi như 1 profile trắng") — xoá SẠCH toàn bộ nội dung
        `profile_dir`, coi như Chrome chưa từng chạy ở đây. Hoàn toàn CỤC BỘ
        (không gọi backend) — CHỈ khác `delete(delete_data=True)` ở chỗ giữ
        nguyên record DB (profile vẫn còn trong danh sách, chỉ mất dữ liệu
        Chrome — tên/worker_mode/task_mode/project_url... không đổi). Caller
        (routes.py) chịu trách nhiệm đảm bảo profile đang ĐÓNG (không worker/
        login browser nào giữ file) trước khi gọi — xoá trong lúc Chrome
        đang mở dễ lỗi file-lock/corrupt."""
        profile = self.get(profile_id)
        profile_dir = (profile or {}).get('profile_dir')
        if not profile_dir or not os.path.isdir(profile_dir):
            raise ValueError('profile_dir không tồn tại trên máy này')
        return _wipe_chrome_profile_dir(profile_dir)

    def clear_cache_only(self, profile_id: int) -> dict:
        """(2026-08-12) Xoá cache; (2026-08-14, theo yêu cầu user "khi lỗi quá
        nhiều ngoài xóa cache xóa thêm các cookie không liên quan đăng nhập")
        từng MỞ RỘNG thêm xoá cookie mọi domain KHÔNG liên quan đăng nhập.

        ⚠️ (2026-08-18, TẠM TẮT theo yêu cầu user "chỉ áp dụng MỖI BATCH giữ
        lại các cookie trên, còn số 2 khi sleep thì tạm không dùng đến") —
        bước xoá cookie (`_purge_non_login_cookies()`) ĐANG BỊ VÔ HIỆU HOÁ Ở
        ĐÂY — hàm này giờ CHỈ còn xoá cache (`_clear_chrome_cache_only()`),
        KHÔNG đụng cookie nào cả. `_purge_non_login_cookies()` VẪN CÒN NGUYÊN
        (không xoá code) — chỉ tạm không gọi, dễ bật lại sau nếu user đổi ý.
        Cơ chế xoá cookie "mỗi batch" giờ dùng RIÊNG 1 danh sách khác, sống
        trong `worker.py` (`_batch_clean_should_keep_cookie()`), KHÔNG còn
        liên quan gì tới `_purge_non_login_cookies()`/hàm này nữa — 2 cơ chế
        đã tách rời hoàn toàn theo yêu cầu trên.

        Dùng bởi `worker.py::_clear_profile_if_sleeping()` (tự phục hồi khi
        sleeping do lỗi nhiều). CÙNG ràng buộc "profile phải đóng" như
        `clear_browser_data()` ở trên (caller đảm bảo)."""
        profile = self.get(profile_id)
        profile_dir = (profile or {}).get('profile_dir')
        if not profile_dir or not os.path.isdir(profile_dir):
            raise ValueError('profile_dir không tồn tại trên máy này')
        cache_result = _clear_chrome_cache_only(profile_dir)
        return {
            'removed':        cache_result['removed'],
            'removedCookies': 0,
            'errors':         cache_result['errors'],
        }

    def clear_cache_and_all_cookies(self, profile_id: int) -> dict:
        """(2026-08-20) Xoá cache + TẤT CẢ cookie (kể cả cookie đăng nhập
        Google) — dùng CHỈ cho bậc thang escalation THEO BATCH khi profile ngủ
        vì `batch_fail_count_before_sleep` batch lỗi liên tiếp (xem
        `worker.py::_clear_profile_if_sleeping()`). KHÁC `clear_cache_only()`
        ngay trên (giữ nguyên cookie/đăng nhập — dùng cho bậc thang THEO TASK
        cũ) và KHÁC `clear_browser_data()` (xoá NGUYÊN `profile_dir`, mất luôn
        `Login Data`/mật khẩu Chrome đã lưu/extension — chỉ dùng cho nút "Làm
        mới profile" thủ công trong GUI).

        Cố ý CHẤP NHẬN mất đăng nhập: bước ngay sau đó (`_ensure_google_login()`
        ở lần khởi động worker kế tiếp, được ÉP chạy qua `state._force_login_check`
        bất kể `google_login_check_enabled`) tự đăng nhập lại bằng email/mật
        khẩu đã lưu của profile — đúng yêu cầu user "trong lúc ngủ xóa tất cả
        cookie - cache -> rồi check login -> đăng nhập vào project lại như
        trước". CÙNG ràng buộc "profile phải đóng" như 2 hàm trên (caller đảm
        bảo — `_clear_profile_if_sleeping()` gọi ngay sau `driver.quit()`)."""
        profile = self.get(profile_id)
        profile_dir = (profile or {}).get('profile_dir')
        if not profile_dir or not os.path.isdir(profile_dir):
            raise ValueError('profile_dir không tồn tại trên máy này')
        cache_result = _clear_chrome_cache_only(profile_dir)
        # (2026-08-23) ĐỔI từ `_purge_all_cookies()` (chỉ `DELETE FROM cookies`)
        # sang `_purge_all_site_data()` — xoá CẢ localStorage/IndexedDB/Service
        # Worker/Trust Tokens/network state. Bản cũ để lại toàn bộ số đó nên
        # labs.google vẫn nhận ra profile cũ dù "đã xoá cookie" (bug thật user
        # báo: phải tự tay Clear browsing data trên trình duyệt mới hết bị bắt
        # lỗi) — xem ghi chú đầy đủ ở `_purge_all_site_data()`.
        site_result = _purge_all_site_data(profile_dir)
        return {
            'removed':        cache_result['removed'] + site_result['removed'],
            'removedCookies': len(site_result['removed']),
            'errors':         cache_result['errors'] + site_result['errors'],
        }

    def set_status(self, profile_id: int, status: str,
                   task_id=None, pid=None, clear_task=False):
        # Best-effort — KHÔNG được làm chết worker thread nếu backend/mạng lỗi,
        # khớp hành vi try/except cũ quanh get_conn().
        _api('PATCH', f'/api/worker_profiles/{profile_id}/status', body={
            'status': status, 'taskId': task_id, 'pid': pid, 'clearTask': clear_task,
        }, timeout=8, quiet=True)

    def bump_task_stat(self, profile_id: int, result: str):
        """result: 'done' | 'error' — tăng bộ đếm HOÀN THÀNH/LỖI TRONG NGÀY của
        profile (2026-07-18, lưu bền vững phía backend — khác với `_task_done_count`/
        `_task_error_count` trong RAM của worker, mất khi restart). Best-effort,
        cùng tính chất với set_status()."""
        _api('PATCH', f'/api/worker_profiles/{profile_id}/task_stats',
             body={'result': result}, timeout=8, quiet=True)


pm = _ProfileManager()
