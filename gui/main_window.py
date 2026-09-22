"""Cửa sổ chính — ráp Sidebar + 4 trang (Profiles/Extensions/Logs/Cài đặt), khởi
động server selenium_flow.py EMBEDDED trong 1 thread nền của CHÍNH process này
(xem module docstring main.py), và vòng lặp refresh."""

import threading
import time

import requests
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QStatusBar, QWidget,
)

import selenium_flow as sf  # server thật — chạy embedded trong thread nền (xem dưới)

from server import auth_client

from .config import API_BASE, REFRESH_MS, LOG_REFRESH_MS
from .style import C
from .api_client import api
from .sidebar import Sidebar
from .pages.profiles_page import ProfilesPage
from .pages.extensions_page import ExtensionsPage
from .pages.logs_page import LogsPage
from .pages.settings_page import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self, user: dict | None = None):
        super().__init__()
        self.setWindowTitle('Selenium Manager')
        self.setMinimumSize(1100, 680)
        self.resize(1300, 800)

        self._user = user or {}
        self._server_thread: threading.Thread | None = None
        self._server_start_error: str | None = None
        # time-based debounce (2026-07-17, xem _tick/_note_server_err) — khởi tạo
        # NGAY LÚC construct để có sẵn 1 khoảng "ân hạn" trước lần poll đầu tiên.
        self._last_status_ok_at = time.time()
        self._status_in_flight = False
        self._status_in_flight_since = 0.0
        self._build_ui()
        self._start_embedded_server()

        self._main_timer = QTimer(self)
        self._main_timer.timeout.connect(self._tick)
        self._main_timer.start(REFRESH_MS)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._page_logs.auto_tick)
        self._log_timer.start(LOG_REFRESH_MS)

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        root_lay = QHBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar()
        self._sidebar.nav_changed.connect(self._on_nav)
        self._sidebar.logout_requested.connect(self._on_logout)
        display = self._user.get('displayName') or self._user.get('username') or ''
        if display:
            self._sidebar.set_user(display)
        root_lay.addWidget(self._sidebar)

        # Pages
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f'background:{C["bg"]};')
        root_lay.addWidget(self._stack)

        self._page_profiles   = ProfilesPage()
        self._page_extensions = ExtensionsPage()
        self._page_logs       = LogsPage()
        self._page_settings   = SettingsPage()

        self._stack.addWidget(self._page_profiles)
        self._stack.addWidget(self._page_extensions)
        self._stack.addWidget(self._page_logs)
        self._stack.addWidget(self._page_settings)

        self._page_profiles.open_logs.connect(self._goto_logs)

        # Status bar
        sb = QStatusBar()
        sb.setStyleSheet(f'background:{C["surface"]}; color:{C["muted"]}; border-top:1px solid {C["border"]}; font-size:11px;')
        sb.showMessage(f'API: {API_BASE}  •  Auto-refresh: {REFRESH_MS//1000}s')
        self.setStatusBar(sb)

    def _on_nav(self, idx: int):
        self._stack.setCurrentIndex(idx)
        if idx == 0: self._page_profiles.refresh()
        elif idx == 1: self._page_extensions.refresh()
        elif idx == 2: self._page_logs.reload()
        elif idx == 3: self._page_settings.refresh()

    def _goto_logs(self, pid: int):
        self._sidebar.select(2)
        self._page_logs.select(pid)

    # ── Server management (embedded — chạy trong CÙNG process, xem module docstring) ──

    def _start_embedded_server(self):
        # 2026-07-17 — FIX BUG THẬT: trước đây `t` là biến LOCAL, không lưu vào
        # `self` — Qt log rõ "QThread: Destroyed while thread '' is still running"
        # (user báo trực tiếp qua console output) ngay sau khi server start. Dù
        # `QThread(self)` có set parent Qt-level, PyQt vẫn có thể garbage-collect
        # WRAPPER Python của `t` khi hàm này return (t.start() không block, trả
        # về ngay) nếu không có tham chiếu Python nào khác giữ nó sống — QThread
        # này chạy `sf.run_server()` (block mãi mãi qua app.run(), lẽ ra phải sống
        # suốt vòng đời app) bị destroy giữa chừng có thể làm bất ổn cơ chế
        # signal/slot xuyên thread của Qt — ĐÚNG cơ chế `api()` (QThreadPool +
        # signal `done`/`error`) dùng để đưa kết quả poll status về GUI. Khớp
        # triệu chứng user báo: Flask THẬT SỰ đang chạy (console xác nhận rõ
        # "Running on http://127.0.0.1:13445") nhưng GUI vẫn báo "Server Offline"
        # — không phải server chết, mà là kết quả poll không bao giờ tới được
        # callback. Fix: lưu vào `self._embedded_server_qthread` — sống suốt đời
        # `self` (MainWindow), đúng như ý đồ ban đầu.
        self._embedded_server_qthread = QThread(self)
        self._embedded_server_qthread.run = self._start_embedded_server_worker  # type: ignore
        self._embedded_server_qthread.start()

    def _start_embedded_server_worker(self):
        # Phát hiện process KHÁC đã chiếm port này trước (vd 1 bản client_tool khác
        # chưa đóng hết, hoặc mở nhầm 2 lần) — từ chối tự mở đè lên, tránh lặp lại
        # đúng bug port-conflict/state rối loạn đã gặp trước đây. Không cố "dùng nhờ"
        # process lạ đó — mục tiêu là 1 process duy nhất, thấy xung đột thì báo rõ.
        try:
            if requests.get(f'{API_BASE}/api/selenium/health', timeout=2).ok:
                self._server_start_error = (
                    f'Port {sf.PORT} đã có process khác đang dùng (có thể còn 1 bản '
                    f'client_tool chưa đóng hết, hoặc đang mở 2 lần). Đóng process đó '
                    f'(Task Manager) rồi mở lại app này.'
                )
                self._sidebar.set_server_err(self._server_start_error)
                return
        except Exception:
            pass  # không ai đang nghe port này — tiếp tục start embedded server

        def _run():
            try:
                sf.run_server()
            except Exception as e:
                # In traceback ĐẦY ĐỦ ra console (cửa sổ cmd chạy start.bat) để chẩn
                # đoán sâu hơn nếu cần — tooltip GUI (self._server_start_error) chỉ
                # giữ str(e) ngắn gọn, tránh tooltip dài vỡ layout.
                import traceback
                traceback.print_exc()
                self._server_start_error = str(e)

        self._server_thread = threading.Thread(target=_run, daemon=True, name='embedded-selenium-flow')
        self._server_thread.start()

        for _ in range(20):
            time.sleep(0.5)
            if self._server_start_error:
                break
            try:
                if requests.get(f'{API_BASE}/api/selenium/health', timeout=2).ok:
                    self._sidebar.set_server_ok(); self._tick(); return
            except Exception:
                pass
        # 2026-07-17: hiện self._server_start_error thật (nếu run_server() ném
        # exception, vd DB không kết nối được, chứ không chỉ "hết 10s vẫn chưa
        # thấy") — trước đây tooltip trống hoàn toàn, không cách nào tự chẩn đoán
        # khi server THẬT SỰ không khởi động được (khác debounce false-positive
        # lúc server đang chạy bình thường chỉ bận, xem _note_server_err bên dưới).
        self._sidebar.set_server_err(
            self._server_start_error or
            f'Server không phản hồi sau 10s khởi động (không có exception cụ thể — '
            f'có thể do máy chậm, hoặc port {sf.PORT} bị phần mềm khác (không phải '
            f'Python) chiếm giữ). Thử đóng hẳn app và mở lại; nếu vẫn vậy, kiểm tra '
            f'"netstat -ano | findstr :{sf.PORT}" xem process nào đang giữ port.'
        )

    # ── Refresh ───────────────────────────────────────────────────────────────

    # Debounce "Server offline" — server embedded chạy CÙNG process với Qt GUI +
    # Selenium worker, có thể có lúc phản hồi chậm do tranh chấp GIL (worker đang
    # tự động hoá DOM nặng, đặc biệt luồng reconcile — driver.refresh() + chờ
    # interceptor 15s + nhiều lệnh resolve URL liên tiếp mỗi vòng, xem CLAUDE.md
    # §5.7/§11.16) chứ KHÔNG PHẢI thật sự sập.
    #
    # 2026-07-17 — ĐỔI HẲN thiết kế (bản trước: đếm SỐ LẦN LỖI LIÊN TIẾP, tăng
    # 3→6, vẫn KHÔNG đủ — user báo lại vẫn còn false-positive). Root cause của
    # bản đếm-liên-tiếp: `_tick()` bắn 1 ApiCall MỚI mỗi 5s BẤT KỂ call trước đã
    # xong chưa — trong lúc server bận, nhiều call chồng lên nhau cùng timeout
    # (12s ở api_client.py) gần như ĐỒNG THỜI, dồn "streak" tăng vọt trong 1 nhịp
    # ngắn thay vì đều đặn, dễ vượt ngưỡng dù tổng thời gian mất kết nối thực tế
    # chưa lâu. Thay bằng đo THỜI GIAN THỰC kể từ lần thành công gần nhất
    # (`_last_status_ok_at`) — không quan tâm bao nhiêu request đã fail ở giữa,
    # chỉ hỏi "đã bao lâu KHÔNG CÓ lần nào thành công" — đúng bản chất câu hỏi
    # "server có thật sự offline không" hơn hẳn. Đồng thời thêm `_status_in_flight`
    # để KHÔNG bắn call mới khi call cũ chưa xong — ngăn chồng chất nhiều
    # ApiCall trong QThreadPool (mỗi call chiếm 1 thread pool slot, tồn đọng có
    # thể khiến ngay cả server ĐÃ hồi phục vẫn phải đợi hàng chờ giải phóng).
    _OFFLINE_THRESHOLD_SECS = 25  # không hiện "offline" trừ khi KHÔNG có lần
                                   # poll thành công nào trong khoảng này

    def _tick(self):
        if self._status_in_flight:
            # Van an toàn: ApiCall.run() luôn emit done/error nên flag này về lý
            # thuyết không bao giờ kẹt mãi, nhưng phòng hờ QThreadPool bị nghẽn
            # bởi call khác (LogsPage auto-tick, v.v.) — quá 30s coi như kẹt, tự
            # reset để không khoá polling vĩnh viễn.
            if time.time() - self._status_in_flight_since > 30:
                self._status_in_flight = False
            else:
                return
        self._status_in_flight = True
        self._status_in_flight_since = time.time()

        def on_status(data):
            self._status_in_flight = False
            if not isinstance(data, dict):
                self._note_server_err('Phản hồi không đúng định dạng JSON'); return
            self._last_status_ok_at = time.time()
            self._sidebar.set_server_ok()
            self._sidebar.set_switch_state(data.get('master_switch_enabled', False))
            self._page_profiles._on_data(data)
            self._page_logs.update_profiles(data.get('profiles', []))

        def on_err(msg):
            self._status_in_flight = False
            self._note_server_err(msg)

        api('GET', '/api/selenium/status', on_done=on_status, on_err=on_err)

    def _note_server_err(self, last_err: str = ''):
        # `last_err` (2026-07-17) — message thật từ `requests` (vd "Connection
        # refused" nghĩa là KHÔNG CÓ GÌ đang nghe port — server đã chết/chưa từng
        # start, khác hẳn "Read timed out" nghĩa là server VẪN sống nhưng phản hồi
        # chậm do bận — 2 loại lỗi này cần hướng xử lý hoàn toàn khác nhau, hiện
        # rõ qua tooltip thay vì chỉ 1 dòng "Server offline" chung chung.
        elapsed = time.time() - self._last_status_ok_at
        if elapsed >= self._OFFLINE_THRESHOLD_SECS:
            detail = (f'Không có phản hồi thành công trong {int(elapsed)}s. '
                       f'Lỗi gần nhất: {last_err or "(không rõ)"}')
            self._sidebar.set_server_err(detail)

    # ── Logout ────────────────────────────────────────────────────────────────

    def _on_logout(self):
        # Đăng xuất = đóng hẳn app (tái dùng nguyên luồng closeEvent — dừng sạch
        # worker/Chrome đang chạy). Mở lại app sau đó sẽ không còn session đã lưu
        # (auth_client.logout() xoá auth_session.json) nên hiện lại màn hình đăng
        # nhập — không hỗ trợ "đổi user mà không khởi động lại process" để tránh
        # phức tạp hoá vòng đời server embedded (port 13445, thread nền...).
        reply = QMessageBox.question(self, 'Đăng xuất',
            'Đăng xuất sẽ đóng hẳn ứng dụng (dừng mọi profile đang chạy). Tiếp tục?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Yes:
            return
        auth_client.logout()
        self.close()

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # Server chạy embedded trong process này (không còn subprocess riêng nữa) —
        # đóng GUI LUÔN tắt hẳn server + mọi worker/Chrome cùng lúc, không có lựa
        # chọn "để server chạy nền" nữa (đúng ý muốn: gộp chung, tắt là tắt hết).
        n_running = len(sf._workers)
        if n_running:
            reply = QMessageBox.question(self, 'Thoát',
                f'{n_running} profile đang chạy — đóng app sẽ dừng hết ngay lập tức. Tiếp tục?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore(); return

        try:
            sf.shutdown_all_workers()
            # Chờ tối đa 5s để worker thread tự thoát (đóng Chrome sạch) trước khi
            # process chính thoát hẳn — daemon thread không đảm bảo chạy xong nếu
            # process chết đột ngột giữa chừng (khác _stop_worker gọi qua API, lúc đó
            # process vẫn sống nên có bao nhiêu thời gian cũng được).
            deadline = time.time() + 5
            while time.time() < deadline and sf._workers:
                time.sleep(0.2)
        except Exception:
            pass
        event.accept()


