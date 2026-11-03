"""Hẹn giờ tự bật lại profile Muse đã bị tắt vì chạm limit (lưu file để sống qua khởi động lại app)."""
import json
import threading
import time
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / 'muse_resume.json'
_lock = threading.Lock()


def _load() -> dict:
    try:
        return {str(k): float(v) for k, v in json.loads(_FILE.read_text('utf-8')).items()}
    except Exception:
        return {}


def _save(data: dict):
    try:
        _FILE.write_text(json.dumps(data), 'utf-8')
    except Exception:
        pass


def schedule(profile_id, hours: float):
    with _lock:
        d = _load()
        d[str(profile_id)] = time.time() + max(0.0, float(hours)) * 3600
        _save(d)


def cancel(profile_id):
    with _lock:
        d = _load()
        if d.pop(str(profile_id), None) is not None:
            _save(d)


def due() -> list:
    """Danh sách profile_id đã tới giờ bật lại (chưa xoá khỏi file)."""
    now = time.time()
    with _lock:
        return [int(k) for k, v in _load().items() if v <= now]


def pending() -> dict:
    with _lock:
        return _load()
