"""Model catalog — fetch `veo_models` (name/label/modelKey) từ backend
`GET /api/models`, cache TTL, dùng làm SOURCE OF TRUTH cho việc map TÊN HIỂN
THỊ GUI (lưu trong `tasks_media_flow.model`) → key thật gửi
aisandbox-pa.googleapis.com — thay cho hardcode cứng trong `flow_api.py`
(2026-08-13, theo yêu cầu user "thêm model_key vào database để truy vấn từ
veo_models cho chuẩn").

Backend (`backend/routes/admin.py::list_models()`) đọc cột
`veo_models.model_key` (migration `backend/core/migrations.py::
_ensure_veo_models_key_columns()`) — cột NULL (model chưa từng được xác nhận
key thật) khiến hàm resolve ở đây TỰ ĐỘNG rơi xuống dict hardcode CŨ trong
`flow_api.py` (giữ nguyên làm lưới an toàn — vẫn đúng cho các giá trị ĐÃ
verify từ trước, và app vẫn hoạt động được nếu backend tạm không tới được /
cột DB trống).

CHỈ 1 field `modelKey` DÙNG CHUNG (2026-08-13, theo yêu cầu user "model_key
dùng chung ko phân biệt khi dùng ingredient xóa cột đó đi") — video mode
imageToVideo/componentsToVideo (namespace `veo_3_1_r2v_*`) KHÔNG có field DB
riêng, được DERIVE từ `modelKey` (dạng t2v) bằng cách thay `t2v`→`r2v`, xem
`flow_api.py::resolve_ingredient_video_model_key()`.

KHÔNG import `worker.py` (tránh circular — `worker.py` import module này)."""

import threading
import time

from .config import FLOW_SERVER, req_lib, log
from . import flow_api

_TTL_SECS = 300  # cache 5 phút — model catalog gần như không đổi trong lúc chạy

_lock     = threading.RLock()
_cache: dict | None = None
_cache_ts: float = 0.0


def _fetch() -> dict:
    """GET /api/models, index theo `name` (khớp đúng field GUI lưu trong
    tasks_media_flow.model) cho tra cứu O(1). Lỗi mạng/backend down → giữ
    nguyên cache CŨ (nếu có) thay vì xoá sạch — an toàn hơn 1 lần fetch lỗi
    thoáng qua làm mất hẳn khả năng resolve đúng."""
    global _cache, _cache_ts
    try:
        r = req_lib.get(f'{FLOW_SERVER}/api/models', timeout=10)
        r.raise_for_status()
        data = r.json()
        by_name = {'image': {}, 'video': {}}
        for kind in ('image', 'video'):
            for m in (data.get(kind) or []):
                name = (m.get('name') or '').strip()
                if name:
                    by_name[kind][name] = m
        with _lock:
            _cache, _cache_ts = by_name, time.time()
        return by_name
    except Exception as e:
        log.warning(f'[model_catalog] Không fetch được {FLOW_SERVER}/api/models: {e} '
                    f'— dùng cache cũ / fallback hardcode cục bộ')
        with _lock:
            return _cache or {'image': {}, 'video': {}}


def _catalog() -> dict:
    with _lock:
        stale = _cache is None or (time.time() - _cache_ts) > _TTL_SECS
    return _fetch() if stale else _cache


def resolve_image_model(name: str | None) -> tuple[str, str]:
    """Trả `(imageModelName, source)` — `source` ∈ {'db','local-fallback'}
    (dùng để log rõ nguồn gốc giá trị, xem worker.py)."""
    raw = (name or '').strip()
    entry = _catalog().get('image', {}).get(raw)
    if entry and entry.get('modelKey'):
        return entry['modelKey'], 'db'
    key, matched = flow_api.resolve_image_model_key(raw)
    return key, ('local-fallback' if matched else 'local-default')


def resolve_video_model(name: str | None, ingredient: bool = False) -> tuple[str, str]:
    """Trả `(videoModelKey, source)`. `ingredient=True` → namespace `veo_3_1_r2v_*`
    (mode imageToVideo/componentsToVideo, DERIVE từ `modelKey` t2v bằng cách
    thay `t2v`→`r2v` — KHÔNG có field DB riêng); `False` → namespace
    `veo_3_1_t2v_*` (mode textToVideo, dùng thẳng `modelKey`)."""
    raw = (name or '').strip()
    entry = _catalog().get('video', {}).get(raw)
    db_key = entry.get('modelKey') if entry else None
    if db_key:
        if not ingredient:
            return db_key, 'db'
        if 't2v' in db_key:
            return db_key.replace('t2v', 'r2v', 1), 'db-derived'
        # đã ở dạng r2v (hoặc key lạ không theo pattern t2v/r2v) — dùng thẳng
        return db_key, 'db'
    if ingredient:
        key, matched = flow_api.resolve_ingredient_video_model_key(raw)
        return key, ('local-fallback' if matched else 'local-default')
    return flow_api.resolve_video_model_key(raw), 'local-fallback'
