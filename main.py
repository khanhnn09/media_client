"""
main.py — Desktop GUI (PyQt6) — Selenium Profile Manager. Entry point DUY NHẤT
của client_tool (2026-07-16: đổi tên từ selenium_gui.py để rõ đây là entry point
chuẩn của cả project, giống quy ước main.py phổ biến).

VAI TRÒ: Điều khiển qua API http://localhost:13445 — server (selenium_flow.py)
  chạy EMBEDDED, làm thread nền NGAY TRONG process này (xem
  gui/main_window.py MainWindow._start_embedded_server) — không có subprocess
  riêng biệt. Đóng GUI = tắt hẳn server + mọi worker/Chrome cùng lúc, không có
  gì sống sót lại phía sau.

CẤU TRÚC (tách từ 1 file selenium_gui.py ~1820 dòng thành package gui/ để dễ quản lý):
  gui/config.py            → API_BASE, REFRESH_MS, LOG_REFRESH_MS
  gui/style.py              → bảng màu C + QSS stylesheet
  gui/api_client.py         → api()/api_sync() — gọi HTTP nội bộ, chạy nền QThreadPool
  gui/widgets.py            → btn/sep/lbl/add_shadow/Badge/StatCard (dùng chung)
  gui/sidebar.py            → Sidebar (nav + trạng thái server + master switch)
  gui/profile_dialog.py     → ProfileDialog (thêm/sửa profile)
  gui/pages/profiles_page.py    → ProfilesPage
  gui/pages/extensions_page.py  → ExtensionsPage
  gui/pages/logs_page.py        → LogsPage
  gui/main_window.py        → MainWindow (ráp mọi thứ lại, khởi động server embedded)
  File này (main.py) chỉ còn là entry point mỏng — dựng QApplication + palette
  rồi show MainWindow.
"""

import sys

from dotenv import load_dotenv
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from gui.style import C, QSS
from gui.main_window import MainWindow

load_dotenv()  # đọc client_tool/.env (python-dotenv tự tìm .env gần nhất, xem .env.example)


def _disable_console_quickedit():
    """Tắt QuickEdit mode của cửa sổ cmd (2026-09-23).

    Bug user báo: "scroll trong cmd thì tự động tắt kết nối với server". Khi
    QuickEdit bật (mặc định Windows), click/bôi chọn trong cmd khiến console
    NGỪNG đọc output — mọi lệnh ghi stdout/stderr (log của Flask/worker) bị
    BLOCK tới khi bấm Esc/Enter. Server embedded chạy chung process nên đứng
    theo, GUI báo "Server offline". Tắt QuickEdit thì cuộn/click không còn
    đóng băng process (vẫn bôi chọn được qua menu chuột phải → Mark)."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        h_in = kernel32.GetStdHandle(-10)          # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(h_in, ctypes.byref(mode)):
            return                                  # không chạy trong console (pythonw/IDE)
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080
        kernel32.SetConsoleMode(h_in, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
    except Exception:
        pass


def main():
    _disable_console_quickedit()
    app = QApplication(sys.argv)
    app.setApplicationName('Selenium Manager')
    app.setStyle('Fusion')
    app.setStyleSheet(QSS)

    # ── Reset hết tàn dư từ lần chạy trước (2026-07-20) ─────────────────────
    # Chạy TRƯỚC KHI làm bất kỳ điều gì khác — theo yêu cầu user "start thì
    # phải reset hết tránh trường hợp này" (bug "Server offline" dai dẳng dù đã
    # đóng hết cửa sổ nhìn thấy được, vì 1 bản client_tool cũ vẫn còn giữ port
    # 13445 phía sau). Xem server/reset_guard.py để biết chi tiết + lý do.
    from server.config import PORT as _SELENIUM_PORT
    from server.reset_guard import kill_stale_client_tool
    _reset_err = kill_stale_client_tool(_SELENIUM_PORT)
    if _reset_err:
        QMessageBox.critical(None, 'Không tự dọn được port',
                              f'{_reset_err}\n\nApp sẽ vẫn cố mở, nhưng có thể báo "Server offline".')

    from PyQt6.QtGui import QPalette
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(C['bg']))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(C['text']))
    pal.setColor(QPalette.ColorRole.Base,            QColor(C['surface']))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(C['surface2']))
    pal.setColor(QPalette.ColorRole.Text,            QColor(C['text']))
    pal.setColor(QPalette.ColorRole.Button,          QColor(C['surface2']))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(C['text']))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(C['accent']))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor('#ffffff'))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(C['muted']))
    app.setPalette(pal)

    # ── Đăng nhập (2026-07-18) — gate mở app, dùng chung tài khoản backend chính.
    # Thử khôi phục session đã lưu từ lần trước TRƯỚC KHI hỏi lại username/password
    # (xem server/auth_client.py) — "ghi nhớ cho lần sau mở lại nếu đã đăng nhập".
    from server import auth_client
    user = auth_client.try_resume_session()
    if not user:
        from gui.login_dialog import LoginDialog
        dlg = LoginDialog()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        user = dlg.user()

    win = MainWindow(user)
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
