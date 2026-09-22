"""Sidebar: logo + nav trái + card trạng thái server + master switch (bật/tắt
nhận task cho TOÀN BỘ profile trên máy này — xem
selenium_flow.py::_set_master_task_intake)."""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .style import C
from .widgets import btn, lbl, sep
from .api_client import api
from server.config import APP_VERSION


class NavItem(QWidget):
    """Nav row với indicator bar bên trái khi active (thay vì chỉ đổi nền)."""
    clicked = pyqtSignal()

    def __init__(self, icon: str, label: str):
        super().__init__()
        self.setFixedHeight(40)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._indicator = QFrame()
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet('background: transparent; border-radius: 1px;')
        lay.addWidget(self._indicator)

        self._btn = QPushButton(f'   {icon}   {label}')
        self._btn.setCheckable(True)
        self._btn.setFixedHeight(40)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C['muted']};
                border: none;
                border-radius: 6px;
                text-align: left;
                padding: 0 12px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background: {C['surface2']}; color: {C['text']}; }}
            QPushButton:checked {{
                background: {C['surface2']};
                color: {C['text']};
                font-weight: 700;
            }}
        """)
        self._btn.clicked.connect(lambda: self.clicked.emit())
        lay.addWidget(self._btn)

    def setChecked(self, v: bool):
        self._btn.setChecked(v)
        self._indicator.setStyleSheet(
            f'background:{C["accent"]}; border-radius:1px;' if v else
            'background: transparent; border-radius: 1px;'
        )


class Sidebar(QWidget):
    nav_changed = pyqtSignal(int)
    logout_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFixedWidth(224)
        self.setStyleSheet(f'background:{C["surface"]}; border-right:1px solid {C["border"]};')

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 18, 12, 16)
        lay.setSpacing(0)

        # Brand
        logo_frame = QWidget()
        logo_frame.setFixedHeight(48)
        logo_lay = QHBoxLayout(logo_frame)
        logo_lay.setContentsMargins(4, 0, 0, 0)
        logo_lay.setSpacing(10)

        mark = QLabel('P')
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C['accent2']}, stop:1 {C['accent']});
            color: #ffffff;
            border-radius: 10px;
            font-weight: 800;
            font-size: 15px;
        """)
        logo_lay.addWidget(mark)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_lbl = QLabel('Profile Manager')
        title_lbl.setStyleSheet(f'font-size:14px; font-weight:700; color:{C["text"]}; background:transparent;')
        caption_lbl = QLabel(f'Selenium Worker Console · v{APP_VERSION}')
        caption_lbl.setStyleSheet(f'font-size:10px; color:{C["muted"]}; background:transparent;')
        title_col.addWidget(title_lbl)
        title_col.addWidget(caption_lbl)
        logo_lay.addLayout(title_col)
        logo_lay.addStretch()

        lay.addWidget(logo_frame)
        lay.addSpacing(16)
        lay.addWidget(sep())
        lay.addSpacing(16)

        section_lbl = lbl('WORKSPACE', 'section')
        section_lbl.setContentsMargins(12, 0, 0, 8)
        lay.addWidget(section_lbl)

        # Nav items
        self._nav: list[NavItem] = []
        items = [('👤', 'Profiles'), ('🧩', 'Extensions'), ('📜', 'Logs'), ('⚙️', 'Cài đặt')]
        for i, (icon, label) in enumerate(items):
            n = NavItem(icon, label)
            n.clicked.connect(lambda idx=i: self._select(idx))
            lay.addWidget(n)
            lay.addSpacing(2)
            self._nav.append(n)

        lay.addStretch()
        lay.addWidget(sep())
        lay.addSpacing(12)

        # Server status — dạng pill card thay vì hàng chữ trần
        self._srv_card = QFrame()
        self._srv_card.setStyleSheet(f'background:{C["surface2"]}; border-radius:8px;')
        srv_row = QHBoxLayout(self._srv_card)
        srv_row.setContentsMargins(10, 8, 10, 8)
        srv_row.setSpacing(7)

        self._srv_dot  = QLabel('●')
        self._srv_dot.setStyleSheet(f'color:{C["yellow"]}; font-size:10px; background:transparent;')
        self._srv_text = QLabel('Connecting…')
        self._srv_text.setStyleSheet(f'color:{C["muted"]}; font-size:11px; font-weight:600; background:transparent;')
        srv_row.addWidget(self._srv_dot)
        srv_row.addWidget(self._srv_text)
        srv_row.addStretch()
        lay.addWidget(self._srv_card)
        lay.addSpacing(8)

        # Master switch — bật/tắt nhận task cho TOÀN BỘ profile trên máy này cùng lúc
        # (khác _stat_free/StatCard ở ProfilesPage vốn chỉ hiển thị, không điều khiển
        # được gì). TẮT → server đóng hết Chrome/worker + chặn auto-scale/Start thủ
        # công (xem selenium_flow.py _set_master_task_intake). Trạng thái đồng bộ mỗi
        # lần MainWindow._tick() poll /api/selenium/status (field master_switch_enabled).
        # Mặc định FALSE (2026-07-17) — khớp default mới ở state.py, chỉ là giá trị
        # hiển thị TẠM trước khi có phản hồi thật đầu tiên từ server.
        self._switch_enabled = False
        self._switch_card = QFrame()
        self._switch_card.setStyleSheet(f'background:{C["surface2"]}; border-radius:8px;')
        switch_row = QHBoxLayout(self._switch_card)
        switch_row.setContentsMargins(10, 8, 10, 8)
        switch_row.setSpacing(7)

        self._switch_dot  = QLabel('●')
        self._switch_text = QLabel('Đang nhận task')
        switch_row.addWidget(self._switch_dot)
        switch_row.addWidget(self._switch_text)
        switch_row.addStretch()
        self._switch_btn = btn('⏻', 'icon')
        self._switch_btn.clicked.connect(self._toggle_switch)
        switch_row.addWidget(self._switch_btn)
        lay.addWidget(self._switch_card)
        self._apply_switch_style()
        lay.addSpacing(8)

        # User info + logout (2026-07-18) — hiển thị ai đang đăng nhập (dùng chung
        # tài khoản backend chính, xem server/auth_client.py) + nút đăng xuất. Đăng
        # xuất đóng hẳn app (xem MainWindow._on_logout) — mở lại sẽ hiện màn hình
        # đăng nhập vì session đã lưu bị xoá.
        self._user_card = QFrame()
        self._user_card.setStyleSheet(f'background:{C["surface2"]}; border-radius:8px;')
        user_row = QHBoxLayout(self._user_card)
        user_row.setContentsMargins(10, 8, 10, 8)
        user_row.setSpacing(7)
        user_icon = QLabel('👤')
        user_icon.setStyleSheet('background:transparent; font-size:11px;')
        self._user_lbl = QLabel('—')
        self._user_lbl.setStyleSheet(f'color:{C["text"]}; font-size:11px; font-weight:600; background:transparent;')
        user_row.addWidget(user_icon)
        user_row.addWidget(self._user_lbl)
        user_row.addStretch()
        self._logout_btn = btn('⎋', 'icon', 'Đăng xuất')
        self._logout_btn.clicked.connect(lambda: self.logout_requested.emit())
        user_row.addWidget(self._logout_btn)
        lay.addWidget(self._user_card)

        self._select(0)

    def set_user(self, display_text: str):
        self._user_lbl.setText(display_text)
        self._user_card.setToolTip(f'Đăng nhập với tài khoản: {display_text}')

    def _select(self, idx: int):
        for i, n in enumerate(self._nav):
            n.setChecked(i == idx)
        self.nav_changed.emit(idx)

    def set_server_ok(self):
        self._srv_dot.setStyleSheet(f'color:{C["green"]}; font-size:10px; background:transparent;')
        self._srv_text.setText('Server online')
        self._srv_text.setStyleSheet(f'color:{C["green"]}; font-size:11px; font-weight:600; background:transparent;')
        self._srv_card.setToolTip('')

    def set_server_err(self, detail: str = ''):
        """`detail` (2026-07-17) — lý do THẬT nếu có (vd exception lúc `run_server()`
        khởi động thất bại: lỗi kết nối DB, port conflict...). Trước đây LUÔN hiện
        text chung chung "Server offline" dù `main_window.py` có bắt được exception
        message cụ thể (`self._server_start_error`) — message đó bị bỏ phí, không
        bao giờ tới tay user, khiến không cách nào tự chẩn đoán khi server thật sự
        KHÔNG khởi động được (khác hẳn debounce false-positive lúc server đang chạy
        bình thường chỉ bận). Giờ hiện qua tooltip (hover vào card) — không đổi
        text chính để tránh card bị giãn/vỡ layout với message dài."""
        self._srv_dot.setStyleSheet(f'color:{C["red"]}; font-size:10px; background:transparent;')
        self._srv_text.setText('Server offline')
        self._srv_text.setStyleSheet(f'color:{C["red"]}; font-size:11px; font-weight:600; background:transparent;')
        self._srv_card.setToolTip(detail or 'Không có chi tiết lỗi — có thể chỉ đang bận tạm thời.')

    def select(self, idx: int):
        self._select(idx)

    # ── Master switch ─────────────────────────────────────────────────────────

    def _apply_switch_style(self):
        if self._switch_enabled:
            color = C['green']
            self._switch_text.setText('Đang nhận task')
            self._switch_btn.setToolTip('Bấm để TẠM DỪNG — đóng hết Chrome/worker trên máy này ngay lập tức')
        else:
            color = C['red']
            self._switch_text.setText('Đã tạm dừng')
            self._switch_btn.setToolTip('Bấm để BẬT LẠI — khôi phục các profile đã dừng và tiếp tục nhận task')
        self._switch_dot.setStyleSheet(f'color:{color}; font-size:10px; background:transparent;')
        self._switch_text.setStyleSheet(f'color:{color}; font-size:11px; font-weight:600; background:transparent;')

    def set_switch_state(self, enabled: bool):
        """Gọi bởi MainWindow._tick() mỗi lần poll — đồng bộ theo trạng thái THẬT
        trên server (kể cả khi trạng thái đổi do nơi khác, vd gọi API trực tiếp)."""
        if enabled == self._switch_enabled:
            return
        self._switch_enabled = enabled
        self._apply_switch_style()

    def _toggle_switch(self):
        new_val = not self._switch_enabled
        self._switch_btn.setEnabled(False)
        api('POST', '/api/selenium/master_switch', {'enabled': new_val},
            on_done=self._on_switch_result, on_err=self._on_switch_error)

    def _on_switch_result(self, r):
        self._switch_btn.setEnabled(True)
        if isinstance(r, dict) and 'enabled' in r:
            self.set_switch_state(r['enabled'])
        else:
            self._show_switch_error()

    def _on_switch_error(self, _msg):
        self._switch_btn.setEnabled(True)
        self._show_switch_error()

    def _show_switch_error(self):
        """Trước đây lỗi (server không phản hồi) im lặng hoàn toàn — chỉ enable lại
        nút, không có dấu hiệu gì cho user biết bấm có tác dụng hay không (bug đã
        gặp thực tế khi 2 process selenium_flow.py trùng port 13445). Giờ hiện rõ
        'Không kết nối được server' màu đỏ 3s rồi tự trả lại đúng trạng thái hiện tại."""
        self._switch_dot.setStyleSheet(f'color:{C["red"]}; font-size:10px; background:transparent;')
        self._switch_text.setStyleSheet(f'color:{C["red"]}; font-size:11px; font-weight:600; background:transparent;')
        self._switch_text.setText('Không kết nối được server')
        QTimer.singleShot(3000, self._apply_switch_style)

