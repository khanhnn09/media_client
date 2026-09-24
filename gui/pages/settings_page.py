"""Trang Cài đặt — hiển thị + CHỈNH SỬA settings cục bộ (2026-07-17, theo yêu
cầu user: "default cục bộ không nhận từ server nữa và có thể chỉnh sửa"). Các
setting này KHÔNG đồng bộ từ FLOW_SERVER — mỗi máy client_tool tự quản lý riêng,
lưu vào `client_tool/local_settings.json` (xem `server/local_settings.py`).

(2026-09-24) Theo yêu cầu user "các setting đang quá dài hãy làm gọn lại và
xuống hàng nếu quá dài, chia thành nhiều tab" + "phải dùng checkbox chứ ko dùng
số 0, 1": chia thành nhiều tab theo nhóm, mỗi dòng = nhãn NGẮN + ghi chú nhỏ tự
xuống dòng, setting bật/tắt dùng QCheckBox (vẫn lưu 0/1 trong JSON như cũ).
Nút Lưu/Đặt lại dùng CHUNG cho mọi tab cài đặt."""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from ..style import C
from ..widgets import btn, lbl, add_shadow
from ..api_client import api

# Mỗi tab: (tiêu đề, [(key, nhãn ngắn, kiểu, ghi chú)]).
# Kiểu: 'int'/'float' ép số, 'list' tách dấu phẩy, 'str' gửi nguyên text,
# 'bool' = checkbox (lưu 0/1). Server validate lại lần nữa ở `_clamp()`.
_TABS = [
    ('Chung', [
        ('max_concurrent_veo3_profiles',   'Số profile VEO chạy đồng thời', 'int',
         'Tối đa bao nhiêu profile VEO (api/dom) được mở cùng lúc.'),
        ('max_concurrent_gemini_profiles', 'Số profile Gemini chạy đồng thời', 'int',
         'Chỉ mở khi có việc Gemini đang chờ.'),
        ('quiet_hours_enabled',            'Bật khung giờ không nhận task', 'bool',
         'Tự tắt nhận task trong khung giờ bên dưới, hết giờ tự bật lại.'),
        ('quiet_hours_start',              'Bắt đầu (HH:MM)', 'str', ''),
        ('quiet_hours_end',                'Kết thúc (HH:MM)', 'str',
         'Hỗ trợ khung vắt qua nửa đêm, vd 22:00 → 06:00.'),
        ('bind_tasks_to_project_email',    'Chỉ chạy task theo project + email', 'bool',
         'Profile VEO chỉ nhận task của project gán đúng email của nó, vào đúng link Flow '
         'đã gán và dùng lại id ảnh đã lưu làm tham chiếu thay vì upload.'),
        ('google_login_check_enabled',     'Check đăng nhập Google trước khi vào trang', 'bool',
         'Tắt = vào thẳng trang nhận task. Bật = tự phát hiện và tự đăng nhập lại nếu bị đăng xuất.'),
    ]),
    ('Tạo ảnh/video', [
        ('generate_via_batchexecute',      'Tạo qua batchexecute (đường API mới)', 'bool',
         'Bỏ chọn = dùng đường aisandbox cũ.'),
        ('api_fallback_to_dom',            'Profile API: lỗi API thì chuyển sang DOM', 'bool',
         'Mặc định tắt — profile API CHỈ chạy API, task lỗi báo về server. Bật = task lỗi API '
         '(hoặc không gọi được API) chạy lại ngay bằng DOM. Video đã gửi API thành công '
         'không chuyển DOM (tránh tạo trùng).'),
        ('gemini_send_via_rpc',            'Gemini: gửi ẩn qua RPC', 'bool',
         'Không gõ prompt vào ô nhập liệu. Có file đính kèm luôn dùng DOM.'),
        ('task_delay_secs',                'Delay giữa các task (giây)', 'int',
         'Dùng cho luồng tuần tự (DOM).'),
        ('thread_stagger_min_secs',        'Giãn cách thread — tối thiểu (giây)', 'float',
         'Chờ ngẫu nhiên trước khi khởi động task API kế tiếp.'),
        ('thread_stagger_max_secs',        'Giãn cách thread — tối đa (giây)', 'float', ''),
        ('step_delay_min_secs',            'Delay giữa các bước — tối thiểu (giây)', 'float', ''),
        ('step_delay_max_secs',            'Delay giữa các bước — tối đa (giây)', 'float', ''),
        ('max_project_media_items',        'Số media tối đa / project', 'int',
         'Đạt ngưỡng thì tự tạo project Flow mới. 0 = tắt.'),
    ]),
    ('Lỗi & phục hồi', [
        ('error_count_before_refresh',       'Lỗi liên tiếp trước khi refresh', 'int', ''),
        ('refresh_count_before_new_project', 'Số lần refresh trước khi tạo project mới', 'int', ''),
        ('error_sleep_secs',                 'Thời gian ngủ khi escalation (giây)', 'int', ''),
        ('error_wait_secs',                  'Chờ sau lỗi trước khi thử lại (giây)', 'int', ''),
        ('batch_fail_count_before_cleanup',  'Batch lỗi liên tiếp → dọn cookie', 'int',
         'Xoá cookie labs.google + cache (giữ đăng nhập) rồi bấm "Create with Google Flow". '
         'Batch có ≥1 task thành công = batch thành công.'),
        ('batch_fail_count_before_sleep',    'Batch lỗi liên tiếp → ngủ', 'int',
         'Ngủ + xoá toàn bộ cookie/cache + ép check đăng nhập lần sau. Phải lớn hơn ngưỡng dọn cookie.'),
        ('error_patterns',                   'Mẫu lỗi nhận diện', 'list',
         'Phân cách bằng dấu phẩy.'),
    ]),
    ('Reconcile', [
        ('download_wait_secs',             'Chờ ban đầu trước khi check render (giây)', 'int', ''),
        ('reconcile_wait_secs',            'Trần chờ render xong (giây)', 'int',
         'Hết trần vẫn refresh dù trang còn báo đang render.'),
        ('reconcile_max_rounds',           'Số vòng reconcile tối đa', 'int', ''),
        ('reconcile_lookback_secs',        'Cửa sổ media gần đây (giây)', 'int', ''),
        ('reconcile_retry_lookback_secs',  'Cửa sổ quét khi lô có task chạy lại (giây)', 'int',
         'Check đầu batch để không tạo trùng task đã render xong.'),
    ]),
    ('Debug', [
        ('debug_log_curl',                 'Log đầy đủ curl khi gọi API', 'bool',
         'Log URL + header + body. ⚠ Có thể lộ bearer token trong log.'),
    ]),
]

# Setting ĐANG ẨN (2026-08-20) — bậc thang "time-window" TẠM TẮT ở
# `worker.py::_record_error()`. Bật lại: chuyển dòng vào 1 tab ở `_TABS`.
_HIDDEN_FIELD_DEFS = [
    ('error_window_minutes',    'Cửa sổ thời gian tính lỗi (phút)',         'int', ''),
    ('error_window_max_errors', 'Số lỗi tối đa trong cửa sổ trước khi ngủ', 'int', ''),
]

_FIELD_DEFS = [f for _title, fields in _TABS for f in fields]

_TAB_QSS = f"""
    QTabWidget::pane {{ border:none; border-top:1px solid {C['border']}; top:-1px; background:transparent; }}
    QTabBar::tab {{ background:transparent; color:{C['muted']}; padding:8px 14px;
                    border:none; border-bottom:2px solid transparent;
                    font-size:12px; font-weight:600; }}
    QTabBar::tab:selected {{ color:{C['text']}; border-bottom:2px solid {C['accent']}; }}
    QTabBar::tab:hover:!selected {{ color:{C['text']}; }}
    QTabWidget, QTabBar, QStackedWidget {{ background:transparent; }}
"""


def _kv_row(grid: QGridLayout, row: int, key: str, value):
    k = QLabel(str(key))
    k.setStyleSheet(f'color:{C["muted"]}; font-size:12px; background:transparent;')
    k.setWordWrap(True)
    v = QLabel(str(value))
    v.setStyleSheet(f'color:{C["text"]}; font-size:12px; font-weight:600; background:transparent;')
    v.setWordWrap(True)
    grid.addWidget(k, row, 0)
    grid.addWidget(v, row, 1)


def _hint(text: str) -> QLabel:
    h = QLabel(text)
    h.setWordWrap(True)
    h.setStyleSheet(f'color:{C["muted"]}; font-size:11px; background:transparent;')
    return h


def _scroll(widget: QWidget) -> QScrollArea:
    sc = QScrollArea(); sc.setWidgetResizable(True)
    sc.setStyleSheet('QScrollArea{border:none; background:transparent;}')
    sc.setWidget(widget)
    return sc


def _card(title: str = '') -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(); card.setProperty('role', 'card')
    add_shadow(card, blur=20, dy=4, alpha=60)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setStyleSheet(f'font-size:13px; font-weight:700; color:{C["text"]}; background:transparent;')
        lay.addWidget(t)
    return card, lay


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        self._grids: dict[str, QGridLayout] = {}
        self._inputs: dict[str, QWidget] = {}   # QLineEdit hoặc QCheckBox
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
        outer.addSpacing(12)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_TAB_QSS)
        for title, fields in _TABS:
            self._tabs.addTab(self._make_fields_tab(fields), title)
        self._tabs.addTab(self._make_status_tab(), 'Trạng thái')
        self._tabs.addTab(self._make_identity_tab(), 'Danh tính máy')
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs, 1)

        # Thanh Lưu/Đặt lại dùng chung cho các tab cài đặt (ẩn ở tab
        # Trạng thái / Danh tính — 2 tab đó không có gì để lưu ở đây).
        self._save_bar = QWidget()
        bar = QHBoxLayout(self._save_bar)
        bar.setContentsMargins(0, 10, 0, 0)
        self._save_status = QLabel('')
        self._save_status.setStyleSheet(f'color:{C["green"]}; font-size:11px; background:transparent;')
        bar.addWidget(self._save_status)
        bar.addStretch()
        b_reset = btn('Đặt lại mặc định', 'danger'); b_reset.setFixedHeight(34)
        b_reset.clicked.connect(self._on_reset)
        b_save = btn('💾  Lưu', 'primary'); b_save.setFixedHeight(34)
        b_save.clicked.connect(self._on_save)
        bar.addWidget(b_reset)
        bar.addWidget(b_save)
        outer.addWidget(self._save_bar)

    def _on_tab_changed(self, idx: int):
        self._save_bar.setVisible(idx < len(_TABS))

    # ── Tab cài đặt ──────────────────────────────────────────────────────────

    def _make_fields_tab(self, fields: list) -> QScrollArea:
        inner = QWidget(); inner.setStyleSheet('background:transparent;')
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 12, 4, 0)
        card, lay = _card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)

        for row, (key, label, typ, hint) in enumerate(fields):
            if typ == 'bool':
                box = QVBoxLayout(); box.setSpacing(2)
                cb = QCheckBox(label)
                cb.setStyleSheet(f'color:{C["text"]}; font-size:12px;')
                box.addWidget(cb)
                if hint:
                    h = _hint(hint); h.setContentsMargins(26, 0, 0, 0)
                    box.addWidget(h)
                grid.addLayout(box, row, 0, 1, 2)
                self._inputs[key] = cb
                continue
            box = QVBoxLayout(); box.setSpacing(2)
            k = QLabel(label)
            k.setWordWrap(True)
            k.setStyleSheet(f'color:{C["text"]}; font-size:12px; background:transparent;')
            box.addWidget(k)
            if hint:
                box.addWidget(_hint(hint))
            edit = QLineEdit()
            edit.setFixedWidth(260 if typ == 'list' else 140)
            grid.addLayout(box, row, 0)
            grid.addWidget(edit, row, 1)
            self._inputs[key] = edit

        lay.addLayout(grid)
        col.addWidget(card)
        col.addStretch()
        return _scroll(inner)

    def _apply_settings(self, settings: dict):
        for key, _label, typ, _hint_ in _FIELD_DEFS:
            w = self._inputs[key]
            v = settings.get(key)
            if typ == 'bool':
                w.setChecked(bool(v) and str(v) not in ('0', 'false', 'False'))
            elif typ == 'list':
                w.setText(', '.join(v) if isinstance(v, list) else str(v or ''))
            else:
                w.setText('' if v is None else str(v))

    def _collect_payload(self):
        """Trả (payload, lỗi) — lỗi là chuỗi mô tả nếu có ô nhập sai kiểu."""
        payload = {}
        for key, label, typ, _hint_ in _FIELD_DEFS:
            w = self._inputs[key]
            if typ == 'bool':
                payload[key] = 1 if w.isChecked() else 0
                continue
            raw = w.text().strip()
            if typ in ('int', 'float'):
                try:
                    payload[key] = int(raw) if typ == 'int' else float(raw)
                except ValueError:
                    return None, f'Giá trị không hợp lệ: "{label}"'
            else:  # 'list' (server tự tách dấu phẩy) / 'str' — gửi nguyên text
                payload[key] = raw
        return payload, ''

    def _set_status(self, text: str, ok: bool):
        color = C['green'] if ok else C['red']
        self._save_status.setStyleSheet(f'color:{color}; font-size:11px; font-weight:600; background:transparent;')
        self._save_status.setText(text)
        if ok:
            QTimer.singleShot(3000, lambda: self._save_status.setText(''))

    def _on_save(self):
        payload, err = self._collect_payload()
        if err:
            self._set_status(err, ok=False); return
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

    # ── Tab trạng thái (read-only) ───────────────────────────────────────────

    def _make_status_tab(self) -> QScrollArea:
        inner = QWidget(); inner.setStyleSheet('background:transparent;')
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 12, 4, 0)
        col.setSpacing(16)
        for key, title in (('dispatcher', '⚙️  Dispatcher / Auto-scale'),
                           ('pending',    '📊  Backlog hiện tại (pending_by_mode)')):
            card, lay = _card(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(20)
            grid.setVerticalSpacing(6)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)
            self._grids[key] = grid
            col.addWidget(card)
        col.addStretch()
        return _scroll(inner)

    def _fill(self, key: str, rows: list):
        grid = self._grids[key]
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, (k, v) in enumerate(rows):
            _kv_row(grid, i, k, v)

    # ── Tab danh tính (2026-07-18) ───────────────────────────────────────────
    # UUID cố định của installation này — backend dùng để scope profile/
    # extension. Client ID chỉ đọc; Tên gợi nhớ tuỳ chỉnh để phân biệt máy.

    def _make_identity_tab(self) -> QScrollArea:
        inner = QWidget(); inner.setStyleSheet('background:transparent;')
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 12, 4, 0)
        card, lay = _card('🪪  Danh tính client_tool này')
        lay.addWidget(_hint('Dùng để backend phân biệt profile/extension của máy này với các máy client_tool khác'))

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

        btn_row = QHBoxLayout()
        self._identity_status = QLabel('')
        self._identity_status.setStyleSheet(f'color:{C["green"]}; font-size:11px; background:transparent;')
        btn_row.addWidget(self._identity_status)
        btn_row.addStretch()
        b_save = btn('💾  Lưu tên', 'primary'); b_save.setFixedHeight(34)
        b_save.clicked.connect(self._on_save_identity)
        btn_row.addWidget(b_save)
        lay.addLayout(btn_row)

        col.addWidget(card)
        col.addStretch()
        api('GET', '/api/selenium/client_identity', on_done=self._on_identity_loaded)
        return _scroll(inner)

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
