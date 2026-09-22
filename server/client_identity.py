"""Danh tính CỤC BỘ của installation client_tool này (2026-07-18, theo yêu cầu user:
"client_tool phải có UUID riêng và có thể setting riêng để dễ phân biệt các profile
được tạo sẽ được gán cho client_tool để mỗi client_tool chỉ thấy profile của mình").

Bối cảnh: sau khi bỏ kết nối DB trực tiếp (xem CHANGELOG "client_tool: bỏ kết nối DB
trực tiếp"), MỌI client_tool (chạy trên nhiều máy khác nhau) đều gọi CHUNG 1 backend
→ bảng `selenium_profiles`/`selenium_extensions` giờ là 1 pool DÙNG CHUNG. Nhưng
`profile_dir`/`ext_path` là đường dẫn filesystem CỤC BỘ (Chrome user-data-dir/thư
mục extension) — client_tool ở máy B KHÔNG THỂ dùng được path do máy A tạo ra. Nếu
không scope, máy B sẽ thấy profile của máy A trong danh sách và cố gắng mở Chrome
với 1 path không tồn tại trên máy mình → lỗi ngay.

Giải pháp: mỗi installation client_tool tự sinh 1 UUID (`client_id`) ở LẦN CHẠY ĐẦU
TIÊN, lưu cố định vào file cục bộ `client_tool/client_identity.json` (KHÔNG commit
git — xem .gitignore), gửi kèm MỌI request lên backend qua header `X-Client-Id`
(`server/managers.py::_api()`). Backend (`backend/routes/worker_profiles.py`) dùng
header này để SCOPE mọi thao tác — mỗi client_tool chỉ thấy/sửa/xoá được profile
+ extension do CHÍNH NÓ tạo. `client_name` là tên gợi nhớ TUỲ CHỌN (vd "PC Văn
phòng 1") — chỉ để hiển thị cho DỄ PHÂN BIỆT (vd trong log/debug phía backend),
KHÔNG dùng để scope (client_id mới là khoá thật, ổn định — client_name có thể trùng
nhau hoặc đổi tuỳ ý mà không ảnh hưởng logic)."""

import json, logging, uuid, threading
from pathlib import Path

# Logger RIÊNG (KHÔNG import từ .config) — config.py cần import module này ngược
# lại (_profile_log() gắn header X-Client-Id) nên import .config ở đây sẽ tạo
# circular import.
log = logging.getLogger('selenium-flow')

_IDENTITY_PATH = Path(__file__).parent.parent / 'client_identity.json'
_lock  = threading.RLock()
_cache: dict | None = None


def _save(data: dict):
    try:
        _IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_IDENTITY_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f'[client_identity] Không lưu được {_IDENTITY_PATH}: {e}')


def _load_or_create() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        data = None
        if _IDENTITY_PATH.exists():
            try:
                with open(_IDENTITY_PATH, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and loaded.get('client_id'):
                    data = loaded
            except Exception as e:
                log.warning(f'[client_identity] Không đọc được {_IDENTITY_PATH}: {e}')
        if data is None:
            # Lần chạy đầu tiên (hoặc file lỗi/mất) — sinh UUID mới, CỐ ĐỊNH từ đây
            # trở đi. KHÔNG được sinh lại mỗi lần start — nếu đổi client_id, backend
            # sẽ coi đây là 1 client_tool HOÀN TOÀN MỚI, mất quyền thấy profile cũ
            # đã tạo trước đó (dù profile vẫn còn nguyên trong DB, chỉ là không còn
            # match client_id để lọc ra).
            data = {'client_id': str(uuid.uuid4()), 'client_name': ''}
            _save(data)
            log.info(f'[client_identity] Tạo client_id mới cho installation này: {data["client_id"]}')
        _cache = data
        return dict(data)


def get_client_id() -> str:
    return _load_or_create()['client_id']


def get_client_name() -> str:
    return _load_or_create().get('client_name') or ''


def get_identity() -> dict:
    """{'client_id': ..., 'client_name': ...} — dùng bởi route GUI-facing
    GET /api/selenium/client_identity."""
    return _load_or_create()


def client_headers() -> dict:
    """{'X-Client-Id': ..., 'X-Client-Name': ...} — đính kèm mọi request lên backend
    (`/api/worker_profiles`/`/api/worker_extensions`). Dùng lại ở managers.py,
    config.py (`_profile_log`), routes.py (proxy logs/health) — 3 chỗ gọi backend
    trực tiếp bằng req_lib thay vì qua managers.py::_api()."""
    return {'X-Client-Id': get_client_id(), 'X-Client-Name': get_client_name()}


def set_client_name(name: str) -> dict:
    global _cache
    with _lock:
        data = _load_or_create()
        data['client_name'] = (name or '').strip()[:100]
        _save(data)
        _cache = data
        return dict(data)
