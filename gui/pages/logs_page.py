"""Trang Logs — xem log real-time theo profile (auto-tick poll, filter theo
level, xoá logs)."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QTextEdit, QVBoxLayout, QWidget,
)

from ..style import C
from ..widgets import btn, lbl, Badge, add_shadow
from ..api_client import api


class LogsPage(QWidget):
    _COLORS = {'ok':'#3fb950','info':'#58a6ff','warn':'#d29922','error':'#f85149'}

    def __init__(self):
        super().__init__()
        self._profiles: list[dict] = []
        self._auto = True
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(0)

        # Header
        hdr = QHBoxLayout()
        title_col = QVBoxLayout(); title_col.setSpacing(4)
        title_col.addWidget(lbl('Logs', 'title'))
        self._sub = lbl('Chọn profile để xem logs hoạt động', 'subtitle')
        title_col.addWidget(self._sub)
        hdr.addLayout(title_col); hdr.addStretch()
        lay.addLayout(hdr)
        lay.addSpacing(20)

        # Toolbar
        tb_card = QFrame(); tb_card.setProperty('role', 'card')
        add_shadow(tb_card, blur=20, dy=4, alpha=60)
        tb = QHBoxLayout(tb_card); tb.setContentsMargins(14, 10, 14, 10); tb.setSpacing(8)

        lbl_p = QLabel('Profile:')
        lbl_p.setStyleSheet(f'color:{C["muted"]}; font-size:12px; font-weight:600; background:transparent;')
        self._profile_cb = QComboBox(); self._profile_cb.setFixedHeight(36); self._profile_cb.setMinimumWidth(200)
        self._profile_cb.currentIndexChanged.connect(self._on_profile_change)

        lbl_lv = QLabel('Level:')
        lbl_lv.setStyleSheet(f'color:{C["muted"]}; font-size:12px; font-weight:600; background:transparent;')
        self._level_cb = QComboBox(); self._level_cb.setFixedHeight(36); self._level_cb.setFixedWidth(100)
        self._level_cb.addItems(['Tất cả','ok','info','warn','error'])
        self._level_cb.currentIndexChanged.connect(self.reload)

        lbl_li = QLabel('Dòng:')
        lbl_li.setStyleSheet(f'color:{C["muted"]}; font-size:12px; font-weight:600; background:transparent;')
        self._limit_cb = QComboBox(); self._limit_cb.setFixedHeight(36); self._limit_cb.setFixedWidth(80)
        self._limit_cb.addItems(['100','200','500'])
        self._limit_cb.setCurrentIndex(1)
        self._limit_cb.currentIndexChanged.connect(self.reload)

        auto_chk = QCheckBox('Auto refresh')
        auto_chk.setChecked(True); auto_chk.setFixedHeight(36)
        auto_chk.toggled.connect(lambda v: setattr(self,'_auto',v))

        b_refresh = btn('↻', 'icon', 'Refresh'); b_refresh.setFixedSize(36,36)
        b_refresh.clicked.connect(self.reload)
        b_clear = btn('🗑 Xoá', 'danger'); b_clear.setFixedHeight(36)
        b_clear.clicked.connect(self._clear)

        for w in [lbl_p, self._profile_cb, lbl_lv, self._level_cb,
                  lbl_li, self._limit_cb, auto_chk]:
            tb.addWidget(w)
        tb.addStretch()
        tb.addWidget(b_refresh); tb.addWidget(b_clear)
        lay.addWidget(tb_card)
        lay.addSpacing(12)

        # Token bar
        tok_bar = QHBoxLayout(); tok_bar.setSpacing(8)
        tok_lbl = QLabel('Tokens:')
        tok_lbl.setStyleSheet(f'color:{C["muted"]}; font-size:11px; font-weight:600; background:transparent;')
        self._tok_auth = Badge('—  Auth',       'gray')
        self._tok_rc   = Badge('—  reCAPTCHA',  'gray')
        tok_bar.addWidget(tok_lbl)
        tok_bar.addWidget(self._tok_auth)
        tok_bar.addWidget(self._tok_rc)
        tok_bar.addStretch()
        lay.addLayout(tok_bar)
        lay.addSpacing(10)

        # Log viewer
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont('Consolas', 10))
        # (2026-08-13) Luôn hiện scrollbar dọc (kể cả khi không hover) — user
        # báo "thanh bar để trượt scroll cũng ko thấy", mặc định ScrollBarAsNeeded
        # của Qt vẫn hiện khi tràn nội dung nhưng dễ bị bỏ sót trên nền tối —
        # AlwaysOn giúp luôn thấy rõ có thể kéo.
        self._log.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._log.setStyleSheet(f"""
            background: {C['surface']};
            border: 1px solid {C['border']};
            border-radius: 10px;
            padding: 12px;
            color: {C['muted']};
            selection-background-color: {C['accent']};
        """)
        lay.addWidget(self._log)

    # ── Public ────────────────────────────────────────────────────────────────

    def update_profiles(self, profiles: list[dict]):
        self._profiles = profiles
        cur = self._profile_cb.currentData()
        self._profile_cb.blockSignals(True)
        self._profile_cb.clear()
        self._profile_cb.addItem('— Chọn profile —', None)
        for p in profiles:
            self._profile_cb.addItem(f'[{p["id"]}]  {p["profile_name"]}', p['id'])
        self._profile_cb.blockSignals(False)
        if cur is not None:
            idx = self._profile_cb.findData(cur)
            if idx >= 0: self._profile_cb.setCurrentIndex(idx)

    def select(self, pid: int):
        idx = self._profile_cb.findData(pid)
        if idx >= 0: self._profile_cb.setCurrentIndex(idx)
        self.reload()

    def auto_tick(self):
        if self._auto and self._profile_cb.currentData() is not None:
            self.reload()

    def _on_profile_change(self, _):
        pid = self._profile_cb.currentData()
        if pid is not None:
            self._sub.setText(f'Profile [{pid}]')
            self.reload()
        else:
            self._tok_auth.set('—  Auth',       'gray')
            self._tok_rc  .set('—  reCAPTCHA',  'gray')

    def _pid(self) -> int | None:
        return self._profile_cb.currentData()

    def reload(self):
        pid   = self._pid()
        if not pid: return
        level = self._level_cb.currentText()
        limit = self._limit_cb.currentText()
        qs    = f'?limit={limit}' + (f'&level={level}' if level not in ('','Tất cả') else '')
        api('GET', f'/api/selenium/profiles/{pid}/logs{qs}', on_done=self._render)
        api('GET', f'/api/selenium/profiles/{pid}/tokens',  on_done=self._render_tokens)

    def _render(self, rows):
        if not isinstance(rows, list): return
        # (2026-08-13, fix bug user báo "đang scroll đọc log cứ giật lên đầu
        # trang") — `_render()` được gọi lại MỖI LẦN auto-refresh (mặc định
        # 4s/lần, xem `auto_tick()`/`LOG_REFRESH_MS`), luôn `clear()` rồi build
        # lại TOÀN BỘ nội dung. Bản cũ CHỈ khôi phục vị trí scroll khi đang ở
        # ĐÁY (`at_bot`) — nếu user đang cuộn LÊN đọc log cũ (chỗ ĐANG ĐỌC
        # THẬT SỰ), vị trí đó KHÔNG được lưu lại, Qt tự đưa scrollbar về 0 sau
        # `clear()`+insert mới → nhảy lên đầu trang mỗi 4s, đúng triệu chứng.
        # Fix: LUÔN lưu giá trị scroll TUYỆT ĐỐI hiện tại; auto-scroll xuống
        # đáy CHỈ khi trước đó ĐÃ ở đáy (theo dõi log mới — hành vi "tail -f"),
        # mọi vị trí khác đều khôi phục lại ĐÚNG giá trị cũ (clamp về max mới
        # phòng nội dung ngắn lại, vd đổi filter/limit).
        vsb      = self._log.verticalScrollBar()
        at_bot   = vsb.value() >= vsb.maximum() - 20
        prev_val = vsb.value()
        doc      = self._log.document()
        self._log.clear()
        cur      = QTextCursor(doc)

        ts_fmt = QTextCharFormat(); ts_fmt.setForeground(QColor(C['border']))

        def fmt(c): f = QTextCharFormat(); f.setForeground(QColor(c)); return f

        if not rows:
            cur.setCharFormat(fmt(C['muted'])); cur.insertText('Chưa có log nào.')
        else:
            for r in rows:
                ts  = str(r.get('created_at',''))[:19].replace('T',' ')
                lvl = r.get('level','info')
                msg = r.get('message','')
                color = self._COLORS.get(lvl, C['muted'])
                cur.setCharFormat(ts_fmt); cur.insertText(f'{ts}  ')
                cur.setCharFormat(fmt(color)); cur.insertText(f'{lvl:<5}  ')
                cur.setCharFormat(fmt(C['text'])); cur.insertText(f'{msg}\n')
        vsb.setValue(vsb.maximum() if at_bot else min(prev_val, vsb.maximum()))

    def _render_tokens(self, data):
        if not isinstance(data, dict): return
        if not data.get('running'):
            self._tok_auth.set('Worker offline', 'gray')
            self._tok_rc  .set('—', 'gray'); return
        has_a = data.get('hasAuth',False)
        has_r = data.get('hasRecaptcha',False)
        age   = data.get('ageSeconds')
        self._tok_auth.set(f'✔  Auth ({age}s)' if has_a and age is not None else
                           '✔  Auth' if has_a else '✘  No Auth',
                           'green' if has_a else 'red')
        self._tok_rc  .set('✔  reCAPTCHA' if has_r else '✘  No reCAPTCHA',
                           'green' if has_r else 'red')

    def _clear(self):
        pid = self._pid()
        if not pid: QMessageBox.information(self,'Info','Chọn profile trước'); return
        if QMessageBox.question(self,'Xoá logs','Xoá toàn bộ logs?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes: return
        api('DELETE', f'/api/selenium/profiles/{pid}/logs', on_done=lambda _: self.reload())


