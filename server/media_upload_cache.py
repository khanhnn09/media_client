"""Cache CỤC BỘ (JSON file, cùng pattern `local_settings.py`) lưu `media.name`
(UUID) của ẢNH THAM CHIẾU đã upload thành công lên Flow, khoá theo
**(project Flow hiện tại, URL ảnh gốc)**.

═══ v2 (2026-09-05) — ĐỔI KHOÁ từ `task_id` sang URL ẢNH GỐC ═══

Bản v1 (2026-08-13) khoá theo `task_id` + `project_id`, chỉ giúp được khi CHÍNH
task đó retry. User báo bug thật: *"task image tham chiếu và check id project có
khớp với project hiện tại không để tận dụng không upload ảnh tham chiếu mới giờ
đã lỗi... cùng project nhưng upload lại ảnh tham chiếu nhiều lần"*.

Đúng — và root cause KHÔNG phải flow.google đổi phiên bản (đường upload mới
`maseQ`/batchexecute vẫn trả `media.name` bình thường, cache v1 vẫn đọc/ghi
đúng). Root cause là CHÍNH CÁCH ĐẶT KHOÁ:

1. **Khoá theo `task_id` ⇒ không task nào dùng lại được của task nào.** Trong
   NanoBananaPro, CÙNG 1 ảnh CHAR/BG/PROP được hàng chục scene tham chiếu — mỗi
   scene là 1 task khác nhau nên mỗi task upload lại 1 bản RIÊNG của cùng 1 tấm
   ảnh vào CÙNG 1 project. Bằng chứng trên máy user: file cache v1 có 196 entry
   ⇒ 196 `media.name` KHÁC NHAU, không 1 lần dùng lại nào.
2. **Task ẢNH (`imageToImage`) chưa từng có cache.** v1 chỉ được gọi trong
   `_prepare_video_uploads()` (task video); 3 vòng upload còn lại
   (`_call_image_api_be`, `_call_image_api_v2`, nhánh frameToVideo) upload
   thẳng, không hỏi cache lần nào.

Hệ quả: tốn băng thông/thời gian mỗi task, và project bị bơm đầy ảnh trùng ⇒
chạm trần `max_project_media_items` (300, §11.20e) sớm hơn nhiều so với lượng
media THẬT ⇒ tự luân chuyển sang project mới oan.

v2 khoá theo `(project_id, url ảnh gốc)` nên MỌI task trong cùng project dùng
chung 1 lần upload — đúng bản chất: media thuộc về PROJECT trên Flow, không
thuộc về task. Vẫn giữ nguyên bảo đảm an toàn của v1: khác `project_id` là cache
miss, upload lại bình thường (không bao giờ dùng `media.name` của project khác).

⚠️ TTL (`_TTL_SECS`, 7 ngày): v1 không có. Vì 1 entry giờ được DÙNG CHUNG bởi
nhiều task, 1 `media.name` hỏng/bị Flow dọn sẽ làm hỏng NHIỀU task thay vì 1 —
TTL để cache tự lành sau 1 tuần thay vì kẹt vĩnh viễn.

⚠️ KHÔNG còn hàm xoá-theo-task: v1 xoá entry khi task xong hẳn, v2 làm vậy là
SAI (xoá mất bản upload mà các task khác đang dùng chung). Entry sống tới khi
hết TTL, đổi project, hoặc bị đẩy ra do trần `_MAX_ENTRIES`.

File JSON KHÔNG commit git (đã có trong .gitignore) — state runtime cục bộ,
mất đi chỉ mất tác dụng cache chứ không mất dữ liệu thật (ảnh gốc vẫn nằm ở
`source_media[].url` trên server chính, upload lại vẫn ra kết quả đúng). File
v1 cũ (khoá phẳng theo task_id) tự bị bỏ qua khi đọc — coi như cache rỗng 1
lần, không cần migrate.
"""

import json
import threading
import time
from pathlib import Path

from .config import log

_CACHE_PATH  = Path(__file__).parent.parent / 'uploaded_media_cache.json'
_MAX_ENTRIES = 2000       # trần đơn giản, tránh file phình to vô hạn
_TTL_SECS    = 7 * 86400  # 7 ngày — xem docstring
_VERSION     = 2

_lock  = threading.RLock()
_cache: dict | None = None


def _load() -> dict:
    """Trả dict `{key: {name, ts}}`. File v1 (khoá phẳng theo task_id, entry có
    field 'names'/'projectId') KHÔNG đọc được theo khoá mới → bỏ qua, bắt đầu
    lại từ rỗng."""
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    try:
        if _CACHE_PATH.exists():
            raw = json.loads(_CACHE_PATH.read_text(encoding='utf-8')) or {}
            if isinstance(raw, dict) and raw.get('version') == _VERSION:
                media = raw.get('media')
                if isinstance(media, dict):
                    _cache = media
            else:
                log.info('[media_upload_cache] File cache bản cũ (khoá theo task) — '
                         'bỏ qua, dựng lại theo khoá (project, ảnh gốc)')
    except Exception as e:
        log.warning(f'[media_upload_cache] Đọc file lỗi, dùng cache rỗng: {e}')
    return _cache


def _save():
    try:
        _CACHE_PATH.write_text(
            json.dumps({'version': _VERSION, 'media': _cache}, ensure_ascii=False),
            encoding='utf-8',
        )
    except Exception as e:
        log.warning(f'[media_upload_cache] Ghi file lỗi (bỏ qua, chỉ mất cache): {e}')


def _key(project_id: str, source_url: str) -> str:
    return f'{project_id}|{source_url}'


def get_cached_media_name(project_id: str, source_url: str):
    """Trả `media.name` đã upload trước đó cho ĐÚNG cặp (project, ảnh gốc) này,
    hoặc `None` nếu chưa có/hết hạn/khác project."""
    if not project_id or not source_url:
        return None
    with _lock:
        entry = _load().get(_key(project_id, source_url))
        if not entry:
            return None
        if time.time() - (entry.get('ts') or 0) > _TTL_SECS:
            return None
        return entry.get('name') or None


def set_cached_media_name(project_id: str, source_url: str, name: str):
    """Lưu `media.name` vừa upload thành công cho cặp (project, ảnh gốc)."""
    if not project_id or not source_url or not name:
        return
    with _lock:
        cache = _load()
        cache[_key(project_id, source_url)] = {'name': name, 'ts': time.time()}
        # Hết chỗ → bỏ entry CŨ NHẤT (đường dưới trần là đường đi thường xuyên
        # nhất, không tốn gì thêm).
        if len(cache) > _MAX_ENTRIES:
            for k in sorted(cache, key=lambda k: cache[k].get('ts', 0))[:len(cache) - _MAX_ENTRIES]:
                cache.pop(k, None)
        _save()


def cache_stats() -> dict:
    """Số entry đang giữ — chỉ để log/chẩn đoán."""
    with _lock:
        return {'entries': len(_load())}
