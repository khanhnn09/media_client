"""Dialog thêm/sửa 1 profile Chrome (tên, project_url, worker_mode, task_mode,
gemini timeout, max_concurrent...)."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from server.run_hours import parse_run_hours, summarize_run_hours

from .style import C
from .widgets import btn, lbl, sep


class ProfileDialog(QDialog):
    def __init__(self, parent=None, profile: dict | None = None):
        super().__init__(parent)
        self._p = profile
        self.setWindowTitle('Thêm profile' if not profile else f'Sửa — {profile["profile_name"]}')
        self.setFixedWidth(480)
        self.setModal(True)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(0)

        # Title
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        icon_badge = QLabel('➕' if not self._p else '✏')
        icon_badge.setFixedSize(34, 34)
        icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_badge.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C['accent2']}, stop:1 {C['accent']});
            border-radius: 10px;
            font-size: 14px;
        """)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        t = lbl('Thêm profile' if not self._p else 'Sửa profile', 'title')
        sub = lbl('Mỗi profile là một tài khoản Google riêng biệt' if not self._p else
                  f'ID: {self._p["id"]}  •  Thư mục: {self._p.get("profile_dir","?")}', 'subtitle')
        title_col.addWidget(t)
        title_col.addWidget(sub)
        title_row.addWidget(icon_badge)
        title_row.addLayout(title_col)
        title_row.addStretch()
        lay.addLayout(title_row)
        lay.addSpacing(20)
        lay.addWidget(sep())
        lay.addSpacing(20)

        def field(placeholder='') -> QLineEdit:
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setFixedHeight(38)
            return e

        def label(text: str) -> QLabel:
            lb = QLabel(text)
            lb.setStyleSheet(f'color:{C["muted"]}; font-size:12px; font-weight:600;')
            lb.setFixedWidth(90)
            return lb

        self._name     = field('vd: account_01')
        self._display  = field('vd: Máy 1 - Văn phòng A (hiển thị trong admin)')
        self._email    = field('vd: user@gmail.com')
        # ── Mật khẩu Google (2026-08-08) — dùng để TỰ ĐĂNG NHẬP LẠI khi Chrome
        # bị redirect sang accounts.google.com (vd sau khi bấm "Làm mới profile
        # — xóa sạch", §11.29 — xoá luôn session Google đã đăng nhập) — xem
        # `server/worker.py::_ensure_google_login()` + CLAUDE.md §11.30.
        # EchoMode.Password che ký tự trên màn hình; LƯU Ý: vẫn lưu PLAINTEXT ở
        # backend (không có hạ tầng mã hoá field-level trong dự án — xem
        # migration `_ensure_worker_profile_password()`, ToolSub gốc) — chỉ nên
        # dùng cho tài khoản Google TẠO RIÊNG cho automation, không phải tài
        # khoản cá nhân.
        self._password = field('Mật khẩu Google (để trống nếu không cần tự đăng nhập lại)')
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        # Khoá bí mật Google Authenticator (2026-09-24, CLAUDE.md §11.58) — lấy ở
        # myaccount.google.com → Bảo mật → Authenticator → "Không quét được?".
        # Worker dùng pyotp sinh mã 6 số khi Google hỏi xác minh 2 bước. Cùng
        # rủi ro lưu PLAINTEXT với mật khẩu ở trên.
        self._totp = field('Khoá Authenticator (base32, để trống nếu không bật 2FA)')
        self._totp.setEchoMode(QLineEdit.EchoMode.Password)
        self._url      = field('vd: https://labs.google/fx/...flow')
        # ── Proxy RIÊNG của profile (2026-09-05, theo yêu cầu user "proxy
        # setting cho từng profile"). Áp dụng cho MỌI loại profile (VEO3/Gemini/
        # ChatGPT/Gemini Video — Chrome nào cũng đi qua được) nên KHÔNG nằm
        # trong `_type_fields`, luôn hiển thị. Để trống = đi thẳng như trước.
        # Nhận nhiều dạng chuỗi (xem server/proxy_config.py::parse_proxy):
        # host:port · scheme://host:port · scheme://user:pass@host:port ·
        # user:pass@host:port · host:port:user:pass.
        # ⚠️ Proxy CHỈ che traffic của TRÌNH DUYỆT — heartbeat/tải file từ
        # backend do Python bắn thẳng, không qua proxy (chủ ý).
        self._proxy    = field('vd: 1.2.3.4:8080  ·  user:pass@1.2.3.4:8080  ·  socks5://1.2.3.4:1080')
        self._proxy.setToolTip(
            'Proxy riêng cho Chrome của profile này. Để trống = không dùng proxy.\n'
            'Định dạng nhận được:\n'
            '  1.2.3.4:8080\n'
            '  http://1.2.3.4:8080\n'
            '  user:pass@1.2.3.4:8080\n'
            '  http://user:pass@1.2.3.4:8080\n'
            '  1.2.3.4:8080:user:pass\n'
            '  socks5://1.2.3.4:1080  (SOCKS KHÔNG hỗ trợ user/pass — phải whitelist IP)\n\n'
            'Proxy chỉ áp dụng cho trình duyệt; heartbeat/tải file nội bộ vẫn đi thẳng.\n'
            'Đổi proxy chỉ có hiệu lực ở lần MỞ Chrome kế tiếp (Stop rồi Start lại profile).')
        self._notes    = field('Ghi chú tùy ý')

        if self._p:
            self._name.setText(self._p['profile_name'])
            self._name.setEnabled(False)
            self._display.setText(self._p.get('display_name',''))
            self._email.setText(self._p.get('account_email',''))
            self._password.setText(self._p.get('account_password',''))
            self._totp.setText(self._p.get('account_totp_secret','') or '')
            self._url.setText(self._p.get('project_url',''))
            self._proxy.setText(self._p.get('proxy_server','') or '')
            self._notes.setText(self._p.get('notes',''))

        # ── Trạng thái bật/tắt (2026-07-17) — cột `enabled` đã có sẵn trong DB
        # (dùng bởi auto-scale, xem client_tool/CLAUDE.md §11.9) nhưng trước giờ
        # KHÔNG có toggle UI, chỉ sửa được qua PATCH API thủ công. TẮT → profile
        # bị chặn cả auto-scale LẪN bấm Start thủ công (xem routes.py/dispatcher.py
        # _start_worker) — dùng để tạm ngưng 1 profile cụ thể mà không cần xoá.
        # ⚠️ Text NGẮN — QCheckBox không tự xuống dòng/thu gọn, nhãn dài bắt
        # QFormLayout nới cột field cho vừa nó (đo được 612px so với vùng cuộn
        # chỉ 410px) khiến MỌI hàng khác bị kéo giãn theo, trong đó lưới 24 ô
        # khung giờ giãn ra tới mức mấy giờ cuối văng khỏi vùng nhìn thấy.
        # Chi tiết dài chuyển hết vào tooltip.
        self._enabled_chk = QCheckBox('Bật — cho phép chạy')
        self._enabled_chk.setToolTip('Cho phép auto-scale tự mở VÀ bấm Start thủ công.\n'
                                     'Tắt = tạm ngưng profile này mà không cần xoá.')
        self._enabled_chk.setChecked(bool(self._p.get('enabled', 1)) if self._p else True)

        # ── Loại profile: VEO3 (Flow Automation), Gemini (chat + video), ChatGPT
        # (chat + upload ảnh, 2026-08-01), Gemini — Tạo Video (2026-08-07,
        # engine THỨ 2 cho task VIDEO, dùng tính năng "Tạo video" trong chat
        # Gemini thay vì Flow DOM — xem server/worker.py::_run_task_gemini_video),
        # hay Gemini — Tạo Ảnh (2026-09-09, engine THỨ 2 cho task ẢNH, dùng
        # Gemini chat thường (KHÔNG cần chọn tỷ lệ khung hình — plain chat
        # không có ratio selector) thay vì Flow DOM/API — xem
        # server/worker.py::_run_task_gemini_image)
        # ──────────────────────────────────────────────────────────────────────
        # Mỗi loại có bộ option riêng, ẩn/hiện tùy chọn (xem _on_type_change).
        cur_worker_mode = self._p.get('worker_mode', 'api') if self._p else 'api'
        self._type = QComboBox()
        self._type.setFixedHeight(38)
        # Mặc định QComboBox đòi bề rộng đủ chứa MỤC DÀI NHẤT (đo được 474px) —
        # cùng lý do với checkbox ở trên, nó nới cột field và kéo giãn lưới khung
        # giờ. Giới hạn bề rộng yêu cầu; danh sách bung ra vẫn hiện đủ chữ.
        self._type.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._type.setMinimumContentsLength(18)
        self._type.addItem('VEO3 — Flow Automation (ảnh/video)',     'veo3')
        self._type.addItem('Gemini — Chat + upload video',           'gemini')
        self._type.addItem('ChatGPT — Chat + upload ảnh',            'chatgpt')
        self._type.addItem('🎬 Gemini — Tạo Video (mới, thay VEO3)', 'gemini_video')
        self._type.addItem('🖼️ Gemini — Tạo Ảnh (mới, thay VEO3)',  'gemini_image')
        _type_idx = {'gemini': 1, 'chatgpt': 2, 'gemini_video': 3, 'gemini_image': 4}.get(cur_worker_mode, 0)
        self._type.setCurrentIndex(_type_idx)
        self._type.currentIndexChanged.connect(self._on_type_change)

        # — Option riêng của VEO3 —
        self._task_mode = QComboBox()
        self._task_mode.setFixedHeight(38)
        self._task_mode.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._task_mode.setMinimumContentsLength(18)
        self._task_mode.addItem('all',         'all')
        self._task_mode.addItem('image_only — Chỉ Ảnh', 'image_only')
        self._task_mode.addItem('video_only — Chỉ Video', 'video_only')
        if self._p:
            idx = self._task_mode.findData(self._p.get('task_mode', 'all'))
            self._task_mode.setCurrentIndex(idx if idx >= 0 else 0)

        self._worker_mode = QComboBox()
        self._worker_mode.setFixedHeight(38)
        self._worker_mode.addItems(['api', 'dom'])
        self._worker_mode.setToolTip(
            'api = capture OAuth token → gọi aisandbox API trực tiếp (nhanh, ổn định)\n'
            'dom = Selenium click giao diện labs.google (như Chrome Extension, không cần token)'
        )
        if self._p and cur_worker_mode in ('api', 'dom'):
            self._worker_mode.setCurrentText(cur_worker_mode)

        self._max_concurrent = QSpinBox()
        self._max_concurrent.setFixedHeight(38)
        self._max_concurrent.setRange(1, 10)
        self._max_concurrent.setValue(int((self._p or {}).get('max_concurrent') or 1))
        self._max_concurrent.setToolTip(
            'Số task veo3 nhận đồng thời mỗi lần heartbeat (1-10) — gửi prompt liên tiếp\n'
            'trong cùng 1 tab rồi poll chung kết quả, không mở nhiều tab song song.'
        )

        # — Khung giờ chạy (2026-09-17, chỉ VEO3) — 24 ô 0..23, ô `h` = nhận task
        # trong khoảng h:00-h:59 theo giờ máy này. Không tick ô nào = chạy liên tục.
        # Áp dụng ở `worker._heartbeat` (không nhận task) + dispatcher (không mở/
        # tự đóng profile) — xem server/run_hours.py.
        self._run_hours_w = QWidget()
        rh_lay = QVBoxLayout(self._run_hours_w)
        rh_lay.setContentsMargins(0, 0, 0, 0)
        rh_lay.setSpacing(4)
        rh_grid = QGridLayout()
        rh_grid.setHorizontalSpacing(4)
        rh_grid.setVerticalSpacing(2)
        saved_hours = set(parse_run_hours((self._p or {}).get('run_hours')))
        self._hour_checks: list[QCheckBox] = []
        for h in range(24):
            cb = QCheckBox(f'{h}')
            # Ép bề rộng: để tự do thì mỗi cột lưới đòi ~48px (tổng 308px) —
            # rộng hơn chỗ còn lại trong dialog 480px nên cột cuối (giờ 5/11/
            # 17/23) bị cắt. 40px đủ cho ô tick + 2 chữ số.
            cb.setFixedWidth(40)
            cb.setToolTip(f'Nhận task trong khoảng {h}:00 – {h}:59')
            cb.setChecked(h in saved_hours)
            cb.toggled.connect(self._update_run_hours_hint)
            rh_grid.addWidget(cb, h // 6, h % 6)
            self._hour_checks.append(cb)
        # ⚠️ Bọc lưới trong HBox + addStretch: QFormLayout cho cột field rộng
        # bằng hàng RỘNG NHẤT của cả form, nếu thả lưới trực tiếp thì 6 cột tự
        # giãn ra lấp hết bề rộng đó và mấy ô giờ cuối trôi khỏi vùng nhìn thấy.
        # Có stretch thì lưới luôn giữ đúng bề rộng tự nhiên (~308px), miễn
        # nhiễm với việc sau này có hàng nào khác lại nới cột ra.
        rh_grid_row = QHBoxLayout()
        rh_grid_row.setContentsMargins(0, 0, 0, 0)
        rh_grid_row.addLayout(rh_grid)
        # ⚠️ PHẢI truyền hệ số 1: `addStretch()` mặc định là 0, lúc đó khoảng dư
        # bị CHIA cho cả lưới (ô checkbox có sizePolicy co giãn được) nên mỗi ô
        # phình từ 36px lên 48px và lưới lại tràn. Hệ số 1 cho spacer giành trọn
        # phần dư, lưới giữ đúng bề rộng tự nhiên.
        rh_grid_row.addStretch(1)
        rh_lay.addLayout(rh_grid_row)
        rh_btns = QHBoxLayout()
        rh_btns.setSpacing(6)
        for text, fn in (('Chọn hết', lambda: self._set_all_hours(True)),
                         ('Bỏ hết', lambda: self._set_all_hours(False)),
                         ('8-17h', lambda: self._set_hours(range(8, 17)))):
            b = QPushButton(text)
            b.setFixedHeight(24)
            # Padding mặc định của QSS làm 3 nút cộng lại ~308px, tự nó nới cột
            # field rộng hơn vùng cuộn. Thu gọn riêng nhóm nút phụ trợ này.
            b.setStyleSheet('padding:2px 8px;')
            if text == '8-17h':
                b.setToolTip('Chọn nhanh giờ hành chính 8:00 – 17:00')
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            rh_btns.addWidget(b)
        rh_btns.addStretch()
        rh_lay.addLayout(rh_btns)
        self._run_hours_hint = QLabel()
        self._run_hours_hint.setStyleSheet(f'color:{C["muted"]}; font-size:11px;')
        # ⚠️ PHẢI cho xuống dòng: tick xen kẽ (0,2,4,…) cho chuỗi tới ~84 ký tự
        # ("0-1h, 2-3h, 4-5h, …"). QLabel 1 dòng có sizeHint rộng hơn cả dialog
        # (cố định 480px) nên nó kéo giãn cột field, đẩy lưới 24 ô khung giờ
        # tràn ra ngoài vùng nhìn thấy — bug thật user báo 2026-09-22.
        # `setMinimumWidth(1)` để minimumSizeHint của label không tự đặt sàn
        # rộng theo nội dung, nếu không wrap vẫn bị bỏ qua khi layout chật.
        self._run_hours_hint.setWordWrap(True)
        self._run_hours_hint.setMinimumWidth(1)
        rh_lay.addWidget(self._run_hours_hint)
        self._update_run_hours_hint()

        # — Option riêng của Gemini —
        self._gemini_attach_to = field('180')
        self._gemini_attach_to.setToolTip(
            'Thời gian (giây) chờ xác nhận file đã đính kèm vào Gemini trước khi báo lỗi.\n'
            'Video càng lớn càng cần lâu hơn (mặc định 180s).'
        )
        self._gemini_resp_to = field('300')
        self._gemini_resp_to.setToolTip(
            'Thời gian (giây) chờ Gemini phản hồi xong (mặc định 300s).\n'
            'Với loại "🎬 Gemini — Tạo Video": worker tự áp dụng sàn tối thiểu\n'
            '600s (video sinh chậm hơn chat text nhiều) dù giá trị ở đây thấp hơn.'
        )
        if self._p:
            self._gemini_attach_to.setText(str(self._p.get('gemini_attach_timeout') or 180))
            self._gemini_resp_to.setText(str(self._p.get('gemini_response_timeout') or 300))
        else:
            self._gemini_attach_to.setText('180')
            self._gemini_resp_to.setText('300')

        # — Round-robin nhiều tab Gemini (2026-07-24) — worker tự mở nhiều tab
        # gemini.google.com, mỗi tab 1 conversation/task riêng, lần lượt
        # switch_to.window() giữa các tab (chờ `gemini_tab_switch_interval` giây
        # mỗi lần chuyển) để cả N task cùng tiến triển song song trong 1 phiên
        # trình duyệt — chỉ cần khi hết ĐỦ N task mới nhận lô mới. Xem
        # server/worker.py::_run_gemini_loop_concurrent().
        self._gemini_max_tabs = QSpinBox()
        self._gemini_max_tabs.setFixedHeight(38)
        self._gemini_max_tabs.setRange(1, 10)
        self._gemini_max_tabs.setValue(int((self._p or {}).get('gemini_max_concurrent_tabs') or 1))
        self._gemini_max_tabs.setToolTip(
            'Số tab Gemini chạy đồng thời (round-robin) — nhận 1 lô tối đa N task/lần,\n'
            'chờ hết cả lô mới nhận lô tiếp theo. 1 = hành vi cũ (tuần tự, 1 tab).'
        )
        self._gemini_tab_interval = field('0.5')
        self._gemini_tab_interval.setToolTip(
            'Số giây chờ giữa mỗi lần chuyển tab lúc round-robin (mặc định 0.5s).'
        )
        if self._p:
            self._gemini_tab_interval.setText(str((self._p.get('gemini_tab_switch_interval') or 0.5)))
        else:
            self._gemini_tab_interval.setText('0.5')

        # — Option riêng của ChatGPT (2026-08-01) — mirror Gemini's attach/response
        # timeout (xem client_tool/server/worker.py::_run_task_chatgpt). Chưa có
        # round-robin nhiều tab như Gemini (worker chạy sequential 1-tab).
        self._chatgpt_attach_to = field('60')
        self._chatgpt_attach_to.setToolTip(
            'Thời gian (giây) chờ xác nhận ảnh đã đính kèm vào ChatGPT trước khi báo lỗi\n'
            '(mặc định 60s).'
        )
        self._chatgpt_resp_to = field('300')
        self._chatgpt_resp_to.setToolTip('Thời gian (giây) chờ ChatGPT phản hồi xong (mặc định 300s).')
        if self._p:
            self._chatgpt_attach_to.setText(str(self._p.get('chatgpt_attach_timeout') or 60))
            self._chatgpt_resp_to.setText(str(self._p.get('chatgpt_response_timeout') or 300))
        else:
            self._chatgpt_attach_to.setText('60')
            self._chatgpt_resp_to.setText('300')

        # (2026-09-24, theo yêu cầu user "modal edit profile chia thành nhóm tab
        # để tránh quá dài") — 4 tab, mỗi tab 1 QFormLayout RIÊNG trong vùng cuộn
        # riêng. Tab "Giờ chạy" chỉ hiện với profile VEO3. Chiều cao dialog tính
        # theo tab CAO NHẤT (xem `_fit_height()`) để đổi tab không làm cửa sổ nhảy.
        def make_form() -> QFormLayout:
            f = QFormLayout()
            f.setSpacing(14)
            f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            f.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
            return f

        f_acc, f_task, f_hours, f_misc = make_form(), make_form(), make_form(), make_form()
        f_acc.addRow(label('Tên *'),    self._name)
        f_acc.addRow(label('Hiển thị'), self._display)
        f_acc.addRow(label('Email'),    self._email)
        f_acc.addRow(label('Mật khẩu'), self._password)
        f_acc.addRow(label('Khoá 2FA'), self._totp)
        f_acc.addRow(label('Trạng thái'), self._enabled_chk)
        f_acc.addRow(label('Loại *'),   self._type)

        f_task.addRow(label('Flow URL'),         self._url)
        f_task.addRow(label('Task mode'),        self._task_mode)
        f_task.addRow(label('Worker mode'),      self._worker_mode)
        f_task.addRow(label('Task nhận đồng thời'), self._max_concurrent)
        f_task.addRow(label('Attach timeout'),   self._gemini_attach_to)
        f_task.addRow(label('Response timeout'), self._gemini_resp_to)
        f_task.addRow(label('Số tab đồng thời'), self._gemini_max_tabs)
        f_task.addRow(label('Giãn cách chuyển tab'), self._gemini_tab_interval)
        f_task.addRow(label('Attach timeout'),   self._chatgpt_attach_to)
        f_task.addRow(label('Response timeout'), self._chatgpt_resp_to)

        f_hours.addRow(label('Giờ chạy'), self._run_hours_w)

        f_misc.addRow(label('Proxy'),   self._proxy)
        f_misc.addRow(label('Ghi chú'), self._notes)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border:none; border-top:1px solid {C['border']}; top:-1px; background:transparent; }}
            QTabBar::tab {{ background:transparent; color:{C['muted']}; padding:8px 12px;
                            border:none; border-bottom:2px solid transparent;
                            font-size:12px; font-weight:600; }}
            QTabBar::tab:selected {{ color:{C['text']}; border-bottom:2px solid {C['accent']}; }}
            QTabBar::tab:hover:!selected {{ color:{C['text']}; }}
            QTabWidget, QTabBar, QStackedWidget {{ background:transparent; }}
        """)
        self._pages = []   # [(form, host)]
        for form_, title in ((f_acc, 'Tài khoản'), (f_task, 'Chạy task'),
                             (f_hours, 'Giờ chạy'), (f_misc, 'Proxy & ghi chú')):
            host = QWidget()
            host.setStyleSheet('background:transparent;')
            col = QVBoxLayout(host)
            col.setContentsMargins(0, 16, 4, 0)
            col.setSpacing(0)
            col.addLayout(form_)
            col.addStretch(1)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setStyleSheet('QScrollArea{border:none; background:transparent;}')
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sc.setWidget(host)
            sc.viewport().setAutoFillBackground(False)
            sc.viewport().setStyleSheet('background:transparent;')
            self._tabs.addTab(sc, title)
            self._pages.append((form_, host))
        self._tabs.tabBar().setUsesScrollButtons(False)
        self._tabs.tabBar().setExpanding(False)
        self._tabs.tabBar().setDrawBase(False)
        self._hours_tab_index = 2
        lay.addWidget(self._tabs, 1)

        # widget → form chứa nó (để `_on_type_change` lấy đúng label qua labelForField)
        self._field_form = {}
        for form_, _host in self._pages:
            for i in range(form_.rowCount()):
                it = form_.itemAt(i, QFormLayout.ItemRole.FieldRole)
                if it and it.widget():
                    self._field_form[id(it.widget())] = form_
        # Field → nhóm ('veo3' | 'gemini' | 'chatgpt'...) để _on_type_change biết
        # ẩn/hiện đúng widget. _gemini_attach_to/_chatgpt_attach_to CÙNG label
        # "Attach timeout" nhưng là 2 widget RIÊNG — ẩn/hiện theo widget nên không nhầm.
        self._type_fields = {
            'veo3':         [self._url, self._task_mode, self._worker_mode, self._max_concurrent,
                             self._run_hours_w],
            'gemini':       [self._gemini_attach_to, self._gemini_resp_to,
                              self._gemini_max_tabs, self._gemini_tab_interval],
            'chatgpt':      [self._chatgpt_attach_to, self._chatgpt_resp_to],
            # gemini_video (2026-08-07)/gemini_image (2026-09-09) — tái dùng 2
            # field timeout của 'gemini' + `max_concurrent` của 'veo3'; không có
            # Flow URL/Task mode/Worker mode (cố định trong get_data()).
            'gemini_video': [self._gemini_attach_to, self._gemini_resp_to, self._max_concurrent],
            'gemini_image': [self._gemini_attach_to, self._gemini_resp_to, self._max_concurrent],
        }
        self._on_type_change()

        lay.addSpacing(24)
        lay.addWidget(sep())
        lay.addSpacing(20)

        # Buttons
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        b_cancel = btn('Hủy', tip='Esc')
        b_cancel.setFixedHeight(38)
        b_cancel.clicked.connect(self.reject)
        b_save = btn('Lưu', 'primary', 'Ctrl+Enter')
        b_save.setFixedHeight(38)
        b_save.setFixedWidth(100)
        b_save.clicked.connect(self._accept)
        row.addWidget(b_cancel)
        row.addWidget(b_save)
        lay.addLayout(row)

        self._built = True
        self._fit_height()

    # ── Khung giờ chạy ────────────────────────────────────────────────────────
    def _checked_hours(self) -> list[int]:
        return [h for h, cb in enumerate(self._hour_checks) if cb.isChecked()]

    def _set_hours(self, hours):
        wanted = set(hours)
        for h, cb in enumerate(self._hour_checks):
            cb.blockSignals(True)
            cb.setChecked(h in wanted)
            cb.blockSignals(False)
        self._update_run_hours_hint()

    def _set_all_hours(self, on: bool):
        self._set_hours(range(24) if on else [])

    def _update_run_hours_hint(self, *_):
        span = summarize_run_hours(self._checked_hours())
        self._run_hours_hint.setText(
            f'Chỉ nhận task: {span} (giờ máy này)' if span
            else 'Không chọn giờ nào (hoặc chọn đủ 24h) = chạy liên tục')
        # Label đã bật wordWrap nên tick thêm/bớt giờ có thể làm nó nhảy 1↔2
        # dòng — dialog phải tính lại chiều cao, không thì cuộn oan hoặc thừa
        # khoảng trống. `_fit_height()` tự no-op trước khi dialog dựng xong.
        self._fit_height()

    def _on_type_change(self, *_):
        """Ẩn/hiện option riêng của từng loại (VEO3 / Gemini / ChatGPT) — cả field
        lẫn label (lấy qua form.labelForField, không phụ thuộc setRowVisible mới
        có từ Qt 6.4).

        Widget dùng CHUNG giữa 2 nhóm (vd `_max_concurrent` vừa veo3 vừa
        gemini_video) phải tính tập visible TRƯỚC rồi mới ẩn/hiện 1 lần —
        loop cũ `visible = (group == current)` khiến nhóm sau ghi đè: chọn
        VEO3 thì gemini_video (duyệt sau) ẩn mất ô Task nhận đồng thời."""
        current = self._type.currentData()
        visible_ids = set()
        for group, fields in self._type_fields.items():
            if group == current:
                visible_ids.update(id(w) for w in fields)
        seen = set()
        for fields in self._type_fields.values():
            for w in fields:
                wid = id(w)
                if wid in seen:
                    continue
                seen.add(wid)
                vis = wid in visible_ids
                w.setVisible(vis)
                form_ = self._field_form.get(wid)
                lb = form_.labelForField(w) if form_ else None
                if lb:
                    lb.setVisible(vis)
        # Tab "Giờ chạy" chỉ có ý nghĩa với VEO3.
        self._tabs.setTabVisible(self._hours_tab_index, current == 'veo3')
        self._fit_height()

    def _fit_height(self):
        """Chiều cao vùng tab = tab CAO NHẤT đang hiện (đổi tab không làm cửa sổ
        nhảy), chỉ bật thanh cuộn khi vượt chiều cao màn hình.

        Phải gọi lại sau mỗi `_on_type_change()` — đổi Loại ẩn/hiện hàng nên chiều
        cao cần thiết đổi (VEO3 có lưới 24 ô khung giờ, Gemini/ChatGPT chỉ vài ô)."""
        if not getattr(self, '_built', False):
            return
        need = 0
        for i, (form_, host) in enumerate(self._pages):
            if not self._tabs.isTabVisible(i):
                continue
            form_.activate()                      # ép tính lại layout NGAY sau setVisible
            need = max(need, host.sizeHint().height())
        # + thanh tab + viền pane + khoảng dư để không bật thanh cuộn oan.
        need += self._tabs.tabBar().sizeHint().height() + 24

        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen else 900
        # Chừa chỗ cho tiêu đề + separator + hàng nút + khung cửa sổ của OS.
        cap = max(240, avail - 260)
        self._tabs.setFixedHeight(min(need, cap))
        self.adjustSize()

    def _accept(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, 'Thiếu thông tin', 'Tên profile là bắt buộc.')
            return
        current = self._type.currentData()
        if current == 'gemini':
            for e, lbl_txt in ((self._gemini_attach_to, 'Attach timeout'),
                               (self._gemini_resp_to, 'Response timeout')):
                if e.text().strip() and not e.text().strip().isdigit():
                    QMessageBox.warning(self, 'Giá trị không hợp lệ', f'{lbl_txt} phải là số (giây).')
                    return
            interval_text = self._gemini_tab_interval.text().strip()
            if interval_text:
                try:
                    float(interval_text)
                except ValueError:
                    QMessageBox.warning(self, 'Giá trị không hợp lệ', 'Giãn cách chuyển tab phải là số (giây).')
                    return
        elif current == 'chatgpt':
            for e, lbl_txt in ((self._chatgpt_attach_to, 'Attach timeout'),
                               (self._chatgpt_resp_to, 'Response timeout')):
                if e.text().strip() and not e.text().strip().isdigit():
                    QMessageBox.warning(self, 'Giá trị không hợp lệ', f'{lbl_txt} phải là số (giây).')
                    return
        elif current in ('gemini_video', 'gemini_image'):
            for e, lbl_txt in ((self._gemini_attach_to, 'Attach timeout'),
                               (self._gemini_resp_to, 'Response timeout')):
                if e.text().strip() and not e.text().strip().isdigit():
                    QMessageBox.warning(self, 'Giá trị không hợp lệ', f'{lbl_txt} phải là số (giây).')
                    return
        self.accept()

    def get_data(self) -> dict:
        current = self._type.currentData()
        data = {
            'profile_name':  self._name.text().strip(),
            'display_name':  self._display.text().strip(),
            'account_email': self._email.text().strip(),
            # KHÔNG .strip() — mật khẩu thật có thể (hiếm nhưng có thể) chứa
            # khoảng trắng ở đầu/cuối, trim nhầm sẽ làm sai mật khẩu thật.
            'account_password': self._password.text(),
            'account_totp_secret': self._totp.text().replace(' ', '').strip(),
            'notes':         self._notes.text().strip(),
            'enabled':       self._enabled_chk.isChecked(),
            # Proxy áp dụng cho MỌI loại profile — đặt ở dict CHUNG (không nằm
            # trong nhánh if/elif theo `current`) để không phải lặp lại 4 lần.
            'proxy_server':  self._proxy.text().strip(),
        }
        if current == 'gemini':
            data['worker_mode'] = 'gemini'
            data['project_url'] = ''       # gemini không dùng Flow project
            data['task_mode']   = 'all'    # giữ giá trị mặc định hợp lệ, không dùng tới
            data['gemini_attach_timeout']   = int(self._gemini_attach_to.text().strip() or 180)
            data['gemini_response_timeout'] = int(self._gemini_resp_to.text().strip() or 300)
            data['gemini_max_concurrent_tabs'] = self._gemini_max_tabs.value()
            data['gemini_tab_switch_interval'] = float(self._gemini_tab_interval.text().strip() or 0.5)
        elif current == 'chatgpt':
            data['worker_mode'] = 'chatgpt'
            data['project_url'] = ''       # chatgpt không dùng Flow project
            data['task_mode']   = 'all'    # giữ giá trị mặc định hợp lệ, không dùng tới
            data['chatgpt_attach_timeout']   = int(self._chatgpt_attach_to.text().strip() or 60)
            data['chatgpt_response_timeout'] = int(self._chatgpt_resp_to.text().strip() or 300)
        elif current == 'gemini_video':
            data['worker_mode'] = 'gemini_video'
            data['project_url'] = ''            # dùng gemini.google.com, không phải Flow project
            data['task_mode']   = 'video_only'  # engine này CHỈ xử lý task video
            data['gemini_attach_timeout']   = int(self._gemini_attach_to.text().strip() or 180)
            data['gemini_response_timeout'] = int(self._gemini_resp_to.text().strip() or 300)
            data['max_concurrent'] = self._max_concurrent.value()
        elif current == 'gemini_image':
            data['worker_mode'] = 'gemini_image'
            data['project_url'] = ''            # dùng gemini.google.com, không phải Flow project
            data['task_mode']   = 'image_only'  # engine này CHỈ xử lý task ảnh
            data['gemini_attach_timeout']   = int(self._gemini_attach_to.text().strip() or 180)
            data['gemini_response_timeout'] = int(self._gemini_resp_to.text().strip() or 300)
            data['max_concurrent'] = self._max_concurrent.value()
        else:
            data['worker_mode']    = self._worker_mode.currentText()
            data['project_url']    = self._url.text().strip()
            data['task_mode']      = self._task_mode.currentData()
            data['max_concurrent'] = self._max_concurrent.value()
            data['run_hours']      = ','.join(str(h) for h in self._checked_hours())
        if current != 'veo3':
            data['run_hours'] = ''   # khung giờ chạy chỉ áp dụng VEO3
        return data


