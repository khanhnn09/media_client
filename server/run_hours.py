"""Khung giờ chạy RIÊNG của từng profile VEO (2026-09-17).

User: *"các tài khoản thuộc veo cho phép chọn thời gian chạy như 1 ngày 24h sẽ
có check chọn 0 -> 24 chỉ nhận task trong thời gian được chọn, nếu ko chọn thì
chạy liên tục"*.

Lưu ở cột `selenium_profiles.run_hours` (backend sở hữu) dạng chuỗi CSV các
giờ 0..23, vd `"8,9,10,20"`. Chọn giờ `h` = được nhận task trong khoảng
`h:00 → h:59` theo GIỜ MÁY chạy client_tool (cùng quy ước với "Khung giờ không
nhận task" toàn máy, `dispatcher._is_in_quiet_hours`). Rỗng = chạy liên tục.

CHỈ áp dụng `worker_mode` api/dom (VEO3) — xem `applies_to()`. Đọc từ profile
dict nên backend cũ chưa có cột (field vắng mặt) tự rơi về "chạy liên tục".
"""

from datetime import datetime

RUN_HOURS_WORKER_MODES = ('api', 'dom')


def parse_run_hours(value) -> list[int]:
    """Chuỗi CSV / list → list giờ 0..23 đã sắp xếp, bỏ trùng + giá trị lạ."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else str(value).split(',')
    hours = set()
    for item in items:
        try:
            h = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= h <= 23:
            hours.add(h)
    return sorted(hours)


def format_run_hours(hours) -> str:
    """list giờ → chuỗi CSV lưu DB (`''` = chạy liên tục)."""
    return ','.join(str(h) for h in parse_run_hours(hours))


def applies_to(profile: dict | None) -> bool:
    return bool(profile) and profile.get('worker_mode') in RUN_HOURS_WORKER_MODES


def in_run_hours(profile: dict | None, now: datetime | None = None) -> bool:
    """True nếu profile được phép nhận task NGAY LÚC NÀY."""
    if not applies_to(profile):
        return True
    hours = parse_run_hours(profile.get('run_hours'))
    if not hours or len(hours) == 24:
        return True
    return (now or datetime.now()).hour in hours


def summarize_run_hours(value) -> str:
    """`"8,9,10,20,21"` → `"8-11h, 20-22h"` (khoảng liền nhau gộp lại, giờ kết
    thúc là mốc HẾT giờ). Rỗng/đủ 24 → `''` (chạy liên tục)."""
    hours = parse_run_hours(value)
    if not hours or len(hours) == 24:
        return ''
    ranges, start, prev = [], hours[0], hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        ranges.append((start, prev))
        start = prev = h
    ranges.append((start, prev))
    return ', '.join(f'{a}-{b + 1}h' for a, b in ranges)
