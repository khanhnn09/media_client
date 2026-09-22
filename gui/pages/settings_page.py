"""Trang Cài đặt — hiển thị + CHỈNH SỬA settings cục bộ (2026-07-17, theo yêu
cầu user: "default cục bộ không nhận từ server nữa và có thể chỉnh sửa"). Các
setting này (max_concurrent_veo3_profiles, error_*, ...) KHÔNG còn đồng bộ từ
FLOW_SERVER nữa — mỗi máy client_tool tự quản lý riêng, lưu vào
`client_tool/local_settings.json` (xem `server/local_settings.py`), sửa trực
tiếp ở form dưới rồi bấm Lưu. Trang cũng hiển thị backlog (pending_by_mode) và
state dispatcher (desired_veo3/active_workers/master switch) — read-only, dùng
để tự chẩn đoán vì sao 1 profile không được auto-scale mở."""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..style import C
from ..widgets import btn, lbl, add_shadow
from ..api_client import api

# (key, label hiển thị, kiểu) — kiểu quyết định cách parse text nhập vào trước
# khi PATCH lên server: 'int'/'float' ép kiểu số, 'list' tách theo dấu phẩy,
# 'str' gửi nguyên text (vd "HH:MM") — server tự parse/validate lại lần nữa ở
# _clamp() (local_settings.py) — double-check vô hại, không phá gì.
_FIELD_DEFS = [
    ('max_concurrent_veo3_profiles',     'Số profile veo3 chạy đồng thời tối đa',        'int'),
    ('max_concurrent_gemini_profiles',   'Số profile Gemini chạy đồng thời tối đa (khi có việc)', 'int'),
    ('error_count_before_refresh',       'Số lỗi liên tiếp trước khi refresh trang',      'int'),
    ('refresh_count_before_new_project', 'Số lần refresh trước khi tạo project mới',      'int'),
    ('error_sleep_secs',                 'Thời gian "ngủ" khi escalation tới hạn (giây)', 'int'),
    ('batch_fail_count_before_cleanup',  'Số BATCH lỗi liên tiếp trước khi xoá cookie labs.google + cache (giữ login) rồi click "Create with Google Flow" — batch có ≥1 task thành công = batch thành công', 'int'),
    ('batch_fail_count_before_sleep',    'Số BATCH lỗi liên tiếp trước khi "ngủ" + xoá TẤT CẢ cookie/cache + ép check đăng nhập Google lần chạy sau (phải > ngưỡng dọn cookie ở trên)', 'int'),
    ('task_delay_secs',                  'Delay giữa các task trong batch (giây)',        'int'),
    ('error_wait_secs',                  'Chờ sau lỗi trước khi thử lại (giây)',          'int'),
    ('download_wait_secs',               'Chờ ban đầu trước khi check còn tile đang render (giây)', 'int'),
    ('reconcile_wait_secs',              'Trần tối đa chờ tile render xong trước khi refresh (giây)', 'int'),
    ('reconcile_max_rounds',             'Số vòng reconcile tối đa trước khi fallback',   'int'),
    ('reconcile_lookback_secs',          'Cửa sổ "gần đây" khi lọc media để reconcile (giây)', 'int'),
    ('reconcile_retry_lookback_secs',    'Cửa sổ quét media khi lô có task chạy lại — check đầu batch tránh tạo trùng (giây)', 'int'),
    ('step_delay_min_secs',              'Delay tối thiểu giữa các bước thao tác (giây)', 'float'),
    ('step_delay_max_secs',              'Delay tối đa giữa các bước thao tác (giây)',    'float'),
    ('thread_stagger_min_secs',          'Giãn cách tối thiểu trước khi khởi động thread task tiếp theo (giây)', 'float'),
    ('thread_stagger_max_secs',          'Giãn cách tối đa trước khi khởi động thread task tiếp theo (giây)',   'float'),
    ('error_patterns',                   'Mẫu lỗi nhận diện (phân cách bởi dấu phẩy)',    'list'),
    ('quiet_hours_enabled',              'Bật khung giờ không nhận task (0=tắt, 1=bật)',  'int'),
    ('quiet_hours_start',                'Giờ bắt đầu không nhận task (HH:MM)',           'str'),
    ('quiet_hours_end',                  'Giờ kết thúc không nhận task (HH:MM)',          'str'),
    ('max_project_media_items',          'Số item tối đa 1 project trước khi tự tạo project mới (0=tắt)', 'int'),
    ('generate_via_batchexecute',        'Tạo ảnh/video qua batchexecute (1 = đường chính, aisandbox dự phòng; 0 = dùng thẳng aisandbox)', 'int'),
    ('gemini_send_via_rpc',              'Gemini chat — gửi ẩn qua RPC (1 = không gõ DOM; 0 = gõ như cũ). Có file đính kèm luôn dùng DOM', 'int'),
    ('debug_log_curl',                   'Log đầy đủ curl (URL+header+body) khi gọi API ảnh/video (0=tắt, 1=bật — lộ bearer token trong log)', 'int'),
    ('google_login_check_enabled',       'Check đăng nhập Google trước khi vào Flow/Gemini/ChatGPT (0=tắt — chạy thẳng vào trang nhận task, 1=bật — tự phát hiện + tự đăng nhập lại nếu cần)', 'int'),
    ('bind_tasks_to_project_email',      'Chỉ chạy task theo project + email (1=bật — profile VEO chỉ nhận task của project gán đúng email của nó, vào đúng link Flow của project, dùng lại id ảnh đã lưu làm tham chiếu thay vì upload; 0=tắt — như cũ, bỏ qua project đã gán email)', 'int'),
]

# (2026-08-20, theo yêu cầu user "theo luồng mới ẩn setting này trước, tạm thời
# ko dùng nữa gây xáo trộn luồng") — setting ĐANG ẨN khỏi trang Cài đặt vì cơ
# chế đọc chúng đã TẠM TẮT trong code. Giữ danh sách ở đây (thay vì xoá hẳn
# dòng) để biết CHÍNH XÁC cần trả lại gì khi bật lại: chuyển dòng tương ứng
# ngược lên `_FIELD_DEFS` ở trên.
#
#   error_window_minutes / error_window_max_errors — bậc thang "time-window"
#   (N lỗi trong M phút → ngủ ngay). TẠM TẮT ở `worker.py::_record_error()`
#   (khối `if False:`) vì nó đếm lỗi TÍCH LUỸ THEO THỜI GIAN, không biết ranh
#   giới batch, nên hay cướp quyền bậc thang THEO BATCH (§11.39) trước khi bậc
#   đó kịp chạy hết chuỗi "2 batch lỗi → dọn cookie → batch 3 → ngủ".
#
# 2 key VẪN nằm trong `_DEFAULT_SERVER_SETTINGS`/`_clamp()` — CHỈ ẩn khỏi UI,
# giá trị đã lưu trong `local_settings.json` không bị mất/ghi đè.
_HIDDEN_FIELD_DEFS = [
    ('error_window_minutes',             'Cửa sổ thời gian tính lỗi (phút)',              'int'),
    ('error_window_max_errors',          'Số lỗi tối đa trong cửa sổ trước khi ngủ',      'int'),
]


def _kv_row(grid: QGridLayout, row: int, key: str, value):
    k = QLabel(str(key))
    k.setStyleSheet(f'color:{C["muted"]}; font-size:12px; background:transparent;')
    v = QLabel(str(value))
    v.setStyleSheet(f'color:{C["text"]}; font-size:12px; font-weight:600; background:transparent;')
    v.setWordWrap(True)
    grid.addWidget(k, row, 0)
    grid.addWidget(v, row, 1)


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        self._grids: dict[str, QGridLayout] = {}
        self._inputs: dict[str, QLineEdit] = {}
        self._build()
        self.refresh()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(0)

        hdr = QHBoxLayout()
        title_col = QVBoxLayout(); title_col.setSpacing(4)
        title_col.addWidget(lbl('Cài đặt', 'title'))
        self._sub = lbl('Settings cục bộ của máy này — không đồng bộ từ server', 'subtitle')
        title_col.addWidget(self._sub)
        hdr.addLayout(title_col); hdr.addStretch()
        b_refresh = btn('↻  Làm mới', 'primary'); b_refresh.setFixedHeight(36)
        b_refresh.clicked.connect(self.refresh)
        hdr.addWidget(b_refresh)
        outer.addLayout(hdr)
        outer.addSpacing(16)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea{border:none; background:transparent;}')
        inner = QWidget(); inner.setStyleSheet('background:transparent;')
        col = QVBoxLayout(inner)
        col.setSpacing(16)
        col.setContentsMargins(0, 0, 4, 0)

        col.addWidget(self._make_identity_card())
        col.addWidget(self._make_settings_card())
        col.addWidget(self._make_card('dispatcher', '⚙️  Dispatcher / Auto-scale'))
        col.addWidget(self._make_card('pending',    '📊  Backlog hiện tại (pending_by_mode)'))
        col.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    # ── Card: danh tính client_tool (2026-07-18) ────────────────────────────────
    #
    # UUID cố định của installation này (`client_tool/client_identity.json`) —
    # dùng để backend scope profile/extension (mỗi client_tool chỉ thấy profile
    # do CHÍNH NÓ tạo, xem client_identity.py + backend/routes/worker_profiles.py).
    # Client ID chỉ đọc (đổi = coi như "máy mới", mất quyền thấy profile cũ);
    # Client Name là tên gợi nhớ tuỳ chỉnh, chỉ để hiển thị/phân biệt.

    def _make_identity_card(self) -> QFrame:
        card = QFrame(); card.setProperty('role', 'card')
        add_shadow(card, blur=20, dy=4, alpha=60)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)

        t = QLabel('🪪  Danh tính client_tool này')
        t.setStyleSheet(f'font-size:13px; font-weight:700; color:{C["text"]}; background:transparent;')
        lay.addWidget(t)
        sub = QLabel('Dùng để backend phân biệt profile/extension của máy này với các máy client_tool khác')
        sub.setStyleSheet(f'color:{C["muted"]}; font-size:11px; background:transparent;')
        sub.setWordWrap(True)
        lay.addWidget(sub)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        k1 = QLabel('Client ID (cố định)')
        k1.setStyleSheet(f'color:{C["muted"]}; font-size:12px; background:transparent;')
        self._client_id_field = QLineEdit(); self._client_id_field.setReadOnly(True)
        grid.addWidget(k1, 0, 0)
        grid.addWidget(self._client_id_field, 0, 1)

        k2 = QLabel('Tên gợi nhớ (tuỳ chỉnh)')
        k2.setStyleSheet(f'color:{C["muted"]}; font-size:12px; background:transparent;')
        self._client_name_field = QLineEdit()
        self._client_name_field.setPlaceholderText('vd: PC Văn phòng 1')
        grid.addWidget(k2, 1, 0)
        grid.addWidget(self._client_name_field, 1, 1)

        lay.addLayout(grid)
        lay.addSpacing(6)

        btn_row = QHBoxLayout()
        self._identity_status = QLabel('')
        self._identity_status.setStyleSheet(f'color:{C["green"]}; font-size:11px; background:transparent;')
        btn_row.addWidget(self._identity_status)
        btn_row.addStretch()
        b_save = btn('💾  Lưu tên', 'primary'); b_save.setFixedHeight(34)
        b_save.clicked.connect(self._on_save_identity)
        btn_row.addWidget(b_save)
        lay.addLayout(btn_row)

        api('GET', '/api/selenium/client_identity', on_done=self._on_identity_loaded)
        return card

    def _on_identity_loaded(self, data):
        if not isinstance(data, dict):
            return
        self._client_id_field.setText(data.get('client_id') or '')
        self._client_name_field.setText(data.get('client_name') or '')

    def _on_save_identity(self):
        name = self._client_name_field.text().strip()
        api('PATCH', '/api/selenium/client_identity', {'clientName': name},
            on_done=self._on_save_identity_done,
            on_err=lambda _m: self._set_identity_status('Lỗi khi lưu — server offline?', ok=False))

    def _on_save_identity_done(self, data):
        if isinstance(data, dict):
            self._client_name_field.setText(data.get('client_name') or '')
        self._set_identity_status('✔ Đã lưu', ok=True)

    def _set_identity_status(self, text: str, ok: bool):
        color = C['green'] if ok else C['red']
        self._identity_status.setStyleSheet(f'color:{color}; font-size:11px; font-weight:600; background:transparent;')
        self._identity_status.setText(text)
        if ok:
            QTimer.singleShot(3000, lambda: self._identity_status.setText(''))

    # ── Card: settings cục bộ (EDITABLE) ────────────────────────────────────────

    def _make_settings_card(self) -> QFrame:
        card = QFrame(); card.setProperty('role', 'card')
        add_shadow(card, blur=20, dy=4, alpha=60)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)

        t = QLabel('🔧  Cài đặt cục bộ (máy này) — không đồng bộ từ server, chỉnh trực tiếp ở đây')
        t.setStyleSheet(f'font-size:13px; font-weight:700; color:{C["text"]}; background:transparent;')
        lay.addWidget(t)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        for i, (key, label, _typ) in enumerate(_FIELD_DEFS):
            k = QLabel(label)
            k.setStyleSheet(f'color:{C["muted"]}; font-size:12px; background:transparent;')
            edit = QLineEdit()
            edit.setFixedWidth(180)
            grid.addWidget(k, i, 0)
            grid.addWidget(edit, i, 1)
            self._inputs[key] = edit
        lay.addLayout(grid)
        lay.addSpacing(6)

        btn_row = QHBoxLayout()
        self._save_status = QLabel('')
        self._save_status.setStyleSheet(f'color:{C["green"]}; font-size:11px; background:transparent;')
        btn_row.addWidget(self._save_status)
        btn_row.addStretch()
        b_reset = btn('Đặt lại mặc định', 'danger'); b_reset.setFixedHeight(34)
        b_reset.clicked.connect(self._on_reset)
        b_save = btn('💾  Lưu', 'primary'); b_save.setFixedHeight(34)
        b_save.clicked.connect(self._on_save)
        btn_row.addWidget(b_reset)
        btn_row.addWidget(b_save)
        lay.addLayout(btn_row)

        return card

    def _apply_settings(self, settings: dict):
        for key, _label, typ in _FIELD_DEFS:
            v = settings.get(key)
            if typ == 'list':
                text = ', '.join(v) if isinstance(v, list) else str(v or '')
            else:
                text = '' if v is None else str(v)
            self._inputs[key].setText(text)

    def _set_status(self, text: str, ok: bool):
        color = C['green'] if ok else C['red']
        self._save_status.setStyleSheet(f'color:{color}; font-size:11px; font-weight:600; background:transparent;')
        self._save_status.setText(text)
        if ok:
            QTimer.singleShot(3000, lambda: self._save_status.setText(''))

    def _on_save(self):
        payload = {}
        for key, label, typ in _FIELD_DEFS:
            raw = self._inputs[key].text().strip()
            if typ == 'int':
                try:
                    payload[key] = int(raw)
                except ValueError:
                    self._set_status(f'Giá trị không hợp lệ: "{label}"', ok=False); return
            elif typ == 'float':
                try:
                    payload[key] = float(raw)
                except ValueError:
                    self._set_status(f'Giá trị không hợp lệ: "{label}"', ok=False); return
            else:  # 'list' (server tự tách theo dấu phẩy) hoặc 'str' (HH:MM...) — gửi nguyên text
                payload[key] = raw
        api('PATCH', '/api/selenium/local_settings', payload,
            on_done=self._on_save_done, on_err=lambda _m: self._set_status('Lỗi khi lưu — server offline?', ok=False))

    def _on_save_done(self, data):
        if not isinstance(data, dict):
            self._set_status('Lỗi khi lưu — phản hồi không hợp lệ', ok=False); return
        self._apply_settings(data)
        self._set_status('✔ Đã lưu', ok=True)

    def _on_reset(self):
        if QMessageBox.question(
            self, 'Đặt lại mặc định', 'Đặt lại toàn bộ cài đặt cục bộ về mặc định gốc?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        api('POST', '/api/selenium/local_settings/reset',
            on_done=self._on_save_done, on_err=lambda _m: self._set_status('Lỗi khi reset — server offline?', ok=False))

    # ── Card: read-only (dispatcher / backlog) ──────────────────────────────────

    def _make_card(self, key: str, title: str) -> QFrame:
        card = QFrame(); card.setProperty('role', 'card')
        add_shadow(card, blur=20, dy=4, alpha=60)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setStyleSheet(f'font-size:13px; font-weight:700; color:{C["text"]}; background:transparent;')
        lay.addWidget(t)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self._grids[key] = grid
        return card

    def _fill(self, key: str, rows: list):
        grid = self._grids[key]
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, (k, v) in enumerate(rows):
            _kv_row(grid, i, k, v)

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self):
        api('GET', '/api/selenium/settings_debug', on_done=self._render, on_err=self._render_err)

    def _render_err(self, _msg):
        self._sub.setText('Không lấy được dữ liệu — server có đang chạy không?')

    def _render(self, data):
        if not isinstance(data, dict):
            self._render_err(''); return
        self._sub.setText('Settings cục bộ của máy này — không đồng bộ từ server')

        self._apply_settings(data.get('local_settings') or {})

        desired = data.get('desired_veo3') or []
        active  = data.get('active_workers') or []
        self._fill('dispatcher', [
            ('Master switch', 'BẬT — đang nhận task' if data.get('master_switch_enabled')
                               else 'TẮT — đã tạm dừng'),
            ('Khung giờ không nhận task', 'ĐANG trong khung giờ' if data.get('quiet_hours_active')
                               else 'Ngoài khung giờ (hoặc đang tắt)'),
            ('Profile đang chạy thật (active_workers)', ', '.join(map(str, active)) or '(không có)'),
            ('Profile "muốn chạy" (desired_veo3)',      ', '.join(map(str, desired)) or '(không có)'),
        ])

        p    = data.get('pending') or {}
        page = data.get('pending_age_secs')
        fail = data.get('pending_fail_streak')
        self._fill('pending', [
            ('imageTotal', p.get('imageTotal', 0)),
            ('videoTotal', p.get('videoTotal', 0)),
            ('total',      p.get('total', 0)),
            ('byMode',     p.get('byMode') or {}),
            ('Tuổi cache', f'{page}s trước' if page is not None else 'chưa fetch được lần nào'),
            ('Số lần fetch lỗi liên tiếp', fail if fail is not None else 0),
        ])
