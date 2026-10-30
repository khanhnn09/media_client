"""Cửa sổ chính — nhúng giao diện WEB (client_tool/web/, phục vụ bởi chính Flask embedded tại
`/ui/`) bằng QWebEngineView. Thay GUI PyQt cũ gồm Sidebar + 4 trang (2026-10-30).

Việc còn lại ở phía Python:
  • khởi động server selenium_flow EMBEDDED trong 1 thread nền của CHÍNH process này
    (xem module docstring main.py) rồi mới nạp trang web — trang cần server để có dữ liệu;
  • cầu nối QWebChannel (`bridge`) cho 2 việc web không tự làm được: hộp chọn thư mục
    native và đăng xuất;
  • closeEvent: dừng sạch worker/Chrome trước khi thoát.

Vòng thăm dò trạng thái, hiển thị "Server offline", master switch… giờ nằm hết trong JS
(web/js/app.js) — Python không còn timer/poll nào.
"""

import html
import threading
import time

import requests
from PyQt6.QtCore import QObject, QThread, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

import selenium_flow as sf  # server thật — chạy embedded trong thread nền (xem dưới)

from server import auth_client

from .config import API_BASE
from .style import C

_LOADING_HTML = f"""<html><body style="margin:0;background:{C['bg']};color:{C['muted']};
font:14px 'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">
<div>⏳ Đang khởi động server…</div></body></html>"""


def _error_html(msg: str) -> str:
    return f"""<html><body style="margin:0;background:{C['bg']};color:{C['text']};
font:14px 'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">
<div style="max-width:640px;padding:24px"><div style="font-size:18px;font-weight:700;color:{C['red']}">
Server không khởi động được</div><p style="color:{C['muted']};line-height:1.6">{html.escape(msg)}</p></div></body></html>"""


class Bridge(QObject):
    """Đối tượng JS gọi được qua QWebChannel (`bridge.pickFolder(...)`, `bridge.logout()`)."""

    def __init__(self, win: 'MainWindow'):
        super().__init__(win)
        self._win = win

    @pyqtSlot(str, result=str)
    def pickFolder(self, title: str) -> str:
        return QFileDialog.getExistingDirectory(self._win, title or 'Chọn thư mục') or ''

    @pyqtSlot()
    def logout(self):
        # Đăng xuất = đóng hẳn app (tái dùng closeEvent — dừng sạch worker/Chrome). Mở lại sẽ hiện
        # màn hình đăng nhập vì auth_client.logout() xoá session đã lưu.
        auth_client.logout()
        self._win.close()


class MainWindow(QMainWindow):
    _server_ready = pyqtSignal()
    _server_failed = pyqtSignal(str)

    def __init__(self, user: dict | None = None):
        super().__init__()
        self.setWindowTitle('Selenium Manager')
        self.setMinimumSize(1100, 680)
        self.resize(1300, 800)

        self._user = user or {}
        self._server_thread: threading.Thread | None = None
        self._server_start_error: str | None = None

        self._view = QWebEngineView(self)
        self._view.setPage(QWebEnginePage(self._view))
        self._channel = QWebChannel(self)
        self._bridge = Bridge(self)
        self._channel.registerObject('bridge', self._bridge)
        self._view.page().setWebChannel(self._channel)
        self._view.setHtml(_LOADING_HTML)
        self.setCentralWidget(self._view)

        self._server_ready.connect(lambda: self._view.load(QUrl(f'{API_BASE}/ui/')))
        self._server_failed.connect(lambda m: self._view.setHtml(_error_html(m)))
        self._start_embedded_server()

    # ── Server management (embedded — chạy trong CÙNG process, xem module docstring) ──

    def _start_embedded_server(self):
        # Giữ tham chiếu Python tới QThread suốt đời cửa sổ: nếu chỉ để biến local, PyQt có thể
        # garbage-collect wrapper giữa chừng ("QThread: Destroyed while thread is still running") và
        # làm hỏng signal/slot xuyên thread (bug thật 2026-07-17).
        self._embedded_server_qthread = QThread(self)
        self._embedded_server_qthread.run = self._start_embedded_server_worker  # type: ignore
        self._embedded_server_qthread.start()

    def _start_embedded_server_worker(self):
        # Có process KHÁC đã chiếm port (1 bản client_tool chưa đóng hết, hoặc mở 2 lần) → từ chối mở
        # đè, báo rõ — mục tiêu là đúng 1 process duy nhất.
        try:
            if requests.get(f'{API_BASE}/api/selenium/health', timeout=2).ok:
                self._server_failed.emit(
                    f'Port {sf.PORT} đã có process khác đang dùng (có thể còn 1 bản client_tool chưa đóng hết, '
                    f'hoặc đang mở 2 lần). Đóng process đó (Task Manager) rồi mở lại app này.')
                return
        except Exception:
            pass  # không ai đang nghe port này — tiếp tục start embedded server

        def _run():
            try:
                sf.run_server()
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._server_start_error = str(e)

        self._server_thread = threading.Thread(target=_run, daemon=True, name='embedded-selenium-flow')
        self._server_thread.start()

        for _ in range(40):
            time.sleep(0.5)
            if self._server_start_error:
                break
            try:
                if requests.get(f'{API_BASE}/api/selenium/health', timeout=2).ok:
                    self._server_ready.emit()
                    return
            except Exception:
                pass
        self._server_failed.emit(
            self._server_start_error or
            f'Server không phản hồi sau 20s khởi động (không có exception cụ thể — có thể do máy chậm, hoặc '
            f'port {sf.PORT} bị phần mềm khác chiếm giữ). Thử đóng hẳn app và mở lại; nếu vẫn vậy, kiểm tra '
            f'"netstat -ano | findstr :{sf.PORT}" xem process nào đang giữ port.')

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # Server chạy embedded trong process này — đóng GUI LUÔN tắt hẳn server + mọi worker/Chrome.
        n_running = len(sf._workers)
        if n_running:
            reply = QMessageBox.question(self, 'Thoát',
                f'{n_running} profile đang chạy — đóng app sẽ dừng hết ngay lập tức. Tiếp tục?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore(); return

        try:
            sf.shutdown_all_workers()
            # Chờ tối đa 5s để worker thread tự thoát (đóng Chrome sạch) trước khi process thoát hẳn.
            deadline = time.time() + 5
            while time.time() < deadline and sf._workers:
                time.sleep(0.2)
        except Exception:
            pass
        event.accept()
