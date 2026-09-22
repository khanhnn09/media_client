"""Chỉ mục CỤC BỘ (file JSON, cùng pattern `media_upload_cache.py`) lưu danh sách
id media (`meta[4]` = DETAIL_UUID) đang có trong từng project Flow.

(2026-09-15) User: *"nhớ lưu file temp để các lịch sử duyệt danh sách uuid
assets của project flow đó để tìm khi cần tham chiếu gán vào tránh phải đọc
request danh sách uuid đang có, chỉ khi không có mới cần đọc lại"*.

Dùng bởi `SeleniumFlowWorker._flow_id_in_project()`: 1 id ảnh tham chiếu có
trong chỉ mục ⇒ dùng lại ngay, KHÔNG gọi RPC `Zzl0ze`. Không có trong chỉ mục
⇒ mới đọc lại danh sách thật từ Flow (rồi ghi đè chỉ mục của project đó).

Nguồn ghi:
- `replace_project_names()` — mỗi lần đọc được TOÀN BỘ danh sách (`Zzl0ze`,
  cả ở bước reconcile cuối batch lẫn lúc kiểm tra ảnh tham chiếu) ⇒ ghi đè, nên
  id đã bị xoá khỏi project tự rơi ra ở lần đọc kế tiếp.
- `add_project_names()` — id vừa upload thành công (chưa chờ lần đọc danh sách
  kế tiếp).

⚠️ Chỉ mục có thể CŨ: 1 id đã bị xoá trên Flow sau lần đọc cuối vẫn còn trong
file cho tới lần đọc danh sách kế tiếp. Chấp nhận — bước reconcile cuối mỗi
batch đọc lại danh sách thật và ghi đè, nên độ trễ tối đa khoảng 1 batch.

File KHÔNG commit git (.gitignore). Mất file chỉ mất tác dụng tra nhanh — lần
cần kế tiếp tự đọc lại từ Flow.
"""

import json
import os
import threading
import time
from pathlib import Path

from .config import log

_INDEX_PATH   = Path(__file__).parent.parent / 'flow_media_index.json'
_MAX_PROJECTS = 100        # giữ tối đa N project gần nhất, tránh file phình to
_VERSION      = 1

_lock = threading.RLock()
_data: dict | None = None   # {project_id: {'names': set, 'ts': float}}


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    _data = {}
    try:
        if _INDEX_PATH.exists():
            raw = json.loads(_INDEX_PATH.read_text(encoding='utf-8')) or {}
            projects = raw.get('projects') if isinstance(raw, dict) and raw.get('version') == _VERSION else None
            for pid, entry in (projects or {}).items():
                names = entry.get('names') if isinstance(entry, dict) else None
                if isinstance(names, list):
                    _data[pid] = {'names': {str(n).lower() for n in names if n},
                                  'ts': float(entry.get('ts') or 0)}
    except Exception as e:
        log.warning(f'[flow_media_index] Đọc file lỗi, dùng chỉ mục rỗng: {e}')
    return _data


def _save():
    data = _load()
    if len(data) > _MAX_PROJECTS:
        for pid in sorted(data, key=lambda p: data[p]['ts'])[:len(data) - _MAX_PROJECTS]:
            data.pop(pid, None)
    payload = {'version': _VERSION,
               'projects': {pid: {'names': sorted(e['names']), 'ts': e['ts']} for pid, e in data.items()}}
    tmp = _INDEX_PATH.with_suffix('.json.tmp')
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, _INDEX_PATH)
    except Exception as e:
        log.warning(f'[flow_media_index] Ghi file lỗi (bỏ qua, chỉ mất chỉ mục): {e}')


def _norm_pid(project_id: str) -> str:
    return str(project_id or '').strip().lower()


def get_project_names(project_id: str):
    """Tập id đã biết của project, hoặc `None` nếu chưa từng đọc danh sách
    project này."""
    pid = _norm_pid(project_id)
    if not pid:
        return None
    with _lock:
        entry = _load().get(pid)
        return set(entry['names']) if entry else None


def project_updated_at(project_id: str) -> float:
    """Thời điểm ghi gần nhất (epoch), 0 nếu chưa có."""
    with _lock:
        entry = _load().get(_norm_pid(project_id))
        return entry['ts'] if entry else 0.0


def replace_project_names(project_id: str, names):
    """Ghi đè danh sách id của project bằng danh sách vừa đọc TOÀN BỘ từ Flow."""
    pid = _norm_pid(project_id)
    if not pid or names is None:
        return
    with _lock:
        _load()[pid] = {'names': {str(n).lower() for n in names if n}, 'ts': time.time()}
        _save()


def add_project_names(project_id: str, names):
    """Thêm id (vd vừa upload) vào danh sách đã biết của project."""
    pid = _norm_pid(project_id)
    new = {str(n).lower() for n in (names or []) if n}
    if not pid or not new:
        return
    with _lock:
        data = _load()
        entry = data.get(pid)
        if entry is None:
            # Chưa từng đọc danh sách project này — KHÔNG tạo entry chỉ từ vài
            # id upload: tập thiếu sẽ bị hiểu nhầm là "đã tra đủ".
            return
        if new <= entry['names']:
            return
        entry['names'] |= new
        entry['ts'] = time.time()
        _save()
