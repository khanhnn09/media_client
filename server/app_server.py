"""Lifecycle của server: shutdown_all_workers() (dừng sạch mọi worker + đóng
login browser) và run_server() (điểm khởi động DUY NHẤT — dùng cả khi chạy
standalone `python selenium_flow.py` lẫn khi main.py import làm module chạy
embedded trong thread nền của process GUI)."""

import sys, threading

from .config import app, log, PORT, DEBUG
from .state import _workers, _login_drivers
from .dispatcher import _stop_worker, _veo3_dispatcher_loop
# routes phải được import (dù không dùng trực tiếp tên nào) để các @app.route(...)
# đăng ký lên `app` — Flask không có route nào nếu module này chưa từng chạy.
from . import routes as _routes  # noqa: F401

def shutdown_all_workers():
    """Dừng TẤT CẢ worker + đóng TẤT CẢ login browser đang mở — gọi khi process
    chứa server này sắp thoát hẳn (2026-07-16: main.py giờ chạy server này
    làm thread nền trong CÙNG process thay vì subprocess riêng — GUI đóng là cả
    process thoát luôn, nên cần chủ động đóng Chrome ở đây thay vì để lại orphan)."""
    for pid in list(_workers.keys()):
        _stop_worker(pid)
    for pid in list(_login_drivers.keys()):
        driver = _login_drivers.pop(pid, None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_server():
    """Khởi động Flask app + dispatcher thread. Dùng CHUNG bởi 2 đường:
    (1) chạy standalone `python selenium_flow.py` (khối __main__ bên dưới);
    (2) import làm module rồi gọi từ main.py trong 1 thread nền của process
    GUI (xem MainWindow._start_embedded_server) — gộp GUI+server thành 1 process
    duy nhất, đóng GUI là tắt hẳn (không còn subprocess riêng biệt sống sót lại)."""
    # sys.setswitchinterval() (2026-07-17) — mặc định CPython 5ms giữa 2 lần
    # kiểm tra nhường GIL cho thread khác. Khi worker thread bận (execute_script/
    # execute_cdp_cmd liên tục trong luồng reconcile — driver.refresh() + poll
    # interceptor + resolve URL, xem CLAUDE.md §5.7/§11.16), thread Flask xử lý
    # /api/selenium/status vẫn ĐÚNG LÝ THUYẾT được nhường GIL đều đặn (I/O socket
    # tự release GIL lúc chờ), nhưng hạ interval xuống 1ms giúp CPython chuyển
    # ngữ cảnh dày hơn — thread status có cơ hội chen vào SỚM HƠN giữa các lệnh
    # Selenium liên tiếp thay vì phải đợi hết 1 chuỗi mới tới lượt. Cái giá: tăng
    # nhẹ overhead context-switch (không đáng kể so với chi phí mỗi lệnh Selenium
    # vốn đã tốn hàng trăm ms). Bổ sung (không thay thế) debounce time-based ở
    # main_window.py — 2 lớp phòng thủ độc lập cho cùng 1 vấn đề GIL contention.
    sys.setswitchinterval(0.001)
    # _ensure_profile_columns() đã bỏ (2026-07-17) — migration bảng selenium_profiles/
    # selenium_extensions/profile_logs giờ chạy phía backend (run_all_migrations() →
    # _ensure_worker_profile_tables(), backend/core/migrations.py) vì client_tool
    # không còn kết nối DB trực tiếp để tự ALTER TABLE nữa.
    threading.Thread(target=_veo3_dispatcher_loop, daemon=True, name='veo3-dispatcher').start()
    log.info(f'SeleniumFlow server → http://localhost:{PORT}')
    # threaded=True BẮT BUỘC (2026-07-16): mặc định Werkzeug dev server xử lý
    # TỪNG REQUEST MỘT — khi main.py poll /api/selenium/status mỗi 5s trong
    # lúc worker đang tự động hoá DOM (tight loop + execute_script liên tục, giữ GIL
    # lâu), request health-check/status có thể bị xếp hàng phía sau và timeout dù
    # server vẫn sống — GUI hiện "không kết nối được" trong khi log cho thấy worker
    # đang chạy bình thường (bug đã gặp thực tế). threaded=True cho Werkzeug tự
    # spawn 1 thread/request thay vì xử lý tuần tự.
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG, use_reloader=False, threaded=True)

