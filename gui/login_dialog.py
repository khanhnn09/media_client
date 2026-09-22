"""Dialog đăng nhập — gate MỞ client_tool GUI (2026-07-18). Dùng CHUNG tài khoản
với backend chính (bảng `users`, `POST /api/auth/login`) qua `server/auth_client.py`
— KHÔNG phải hệ thống tài khoản riêng. Đăng nhập thành công tự "nhớ" cho lần mở
sau (session cookie lưu cục bộ, xem auth_client.py::try_resume_session)."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from .style import C
from server import auth_client


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Đăng nhập — Selenium Manager')
        self.setFixedSize(380, 300)
        self.setModal(True)
        self._user: dict | None = None
        self._build()

    def _build(self):
        self.setStyleSheet(f'background:{C["bg"]};')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 32, 32, 24)
        lay.setSpacing(12)

        mark = QLabel('P')
        mark.setFixedSize(44, 44)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C['accent2']}, stop:1 {C['accent']});
            color: #ffffff; border-radius: 12px; font-weight: 800; font-size: 18px;
        """)
        lay.addWidget(mark)

        title = QLabel('Đăng nhập')
        title.setStyleSheet(f'font-size:19px; font-weight:800; color:{C["text"]}; background:transparent;')
        lay.addWidget(title)
        sub = QLabel('Dùng tài khoản backend chính để mở Selenium Manager')
        sub.setWordWrap(True)
        sub.setStyleSheet(f'color:{C["muted"]}; font-size:12px; background:transparent;')
        lay.addWidget(sub)
        lay.addSpacing(8)

        self._user_input = QLineEdit()
        self._user_input.setPlaceholderText('Username')
        self._user_input.setFixedHeight(38)
        self._user_input.returnPressed.connect(lambda: self._pass_input.setFocus())
        lay.addWidget(self._user_input)

        self._pass_input = QLineEdit()
        self._pass_input.setPlaceholderText('Password')
        self._pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_input.setFixedHeight(38)
        self._pass_input.returnPressed.connect(self._on_login)
        lay.addWidget(self._pass_input)

        self._err_lbl = QLabel('')
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setStyleSheet(f'color:{C["red"]}; font-size:12px; background:transparent;')
        lay.addWidget(self._err_lbl)

        lay.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_login = QPushButton('Đăng nhập')
        self._btn_login.setFixedHeight(38)
        self._btn_login.setDefault(True)
        self._btn_login.setStyleSheet(f"""
            QPushButton {{
                background: {C['accent']}; color: #ffffff; border: none;
                border-radius: 8px; padding: 0 20px; font-size: 13px; font-weight: 700;
            }}
            QPushButton:disabled {{ background: {C['muted']}; }}
        """)
        self._btn_login.clicked.connect(self._on_login)
        btn_row.addWidget(self._btn_login)
        lay.addLayout(btn_row)

        self._user_input.setFocus()

    def _on_login(self):
        username = self._user_input.text().strip()
        password = self._pass_input.text()
        if not username or not password:
            self._err_lbl.setText('Nhập đủ username và password')
            return
        self._btn_login.setEnabled(False)
        self._btn_login.setText('Đang đăng nhập…')
        self._err_lbl.setText('')
        # Đăng nhập chạy ĐỒNG BỘ (chặn GUI 1 nhịp ngắn) — chấp nhận được vì đây là
        # hành động người dùng chủ động bấm, không phải vòng lặp poll nền (khác hẳn
        # ApiCall/QThreadPool dùng cho MainWindow._tick()).
        ok, err, user = auth_client.login(username, password)
        if not ok:
            self._btn_login.setEnabled(True)
            self._btn_login.setText('Đăng nhập')
            self._err_lbl.setText(err)
            self._pass_input.selectAll()
            self._pass_input.setFocus()
            return
        self._user = user
        self.accept()

    def user(self) -> dict | None:
        return self._user
