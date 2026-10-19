"""Dialog thêm NHIỀU profile 1 lúc: mỗi dòng 1 profile, thông số cài đặt copy từ 1 profile mẫu."""

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout,
)

from .widgets import btn, lbl

# Trường "danh tính" KHÔNG copy từ profile mẫu (giống Sao chép profile).
_IDENTITY = ('profile_name', 'display_name', 'account_email', 'account_password',
             'account_totp_secret', 'project_url', 'proxy_server', 'notes')

# Chỉ gửi các trường route tạo profile (`POST /api/selenium/profiles`) nhận.
_ALLOWED = ('profile_name', 'display_name', 'account_email', 'account_password', 'account_totp_secret',
            'project_url', 'task_mode', 'worker_mode', 'notes', 'gemini_attach_timeout',
            'gemini_response_timeout', 'max_concurrent', 'enabled', 'gemini_max_concurrent_tabs',
            'gemini_tab_switch_interval', 'proxy_server', 'run_hours', 'omni_enabled', 'veo_enabled',
            'fp_aspects', 'bind_project_email', 'extension_ids', 'chatgpt_attach_timeout',
            'chatgpt_response_timeout')

# Thứ tự cột mỗi dòng: tên | hiển thị | email | mật khẩu | loại | proxy
_COLS = ('profile_name', 'display_name', 'account_email', 'account_password', 'type', 'proxy_server')

# Cột "Loại" → worker_mode (+ task_mode/project_url đi kèm, giống ProfileDialog.get_data()).
_TYPES = {
    'api': 'api', 'veo': 'api', 'veo3': 'api', 'flow': 'api', 'dom': 'dom', 'veo dom': 'dom',
    'gemini': 'gemini', 'gemini chat': 'gemini', 'chatgpt': 'chatgpt', 'gpt': 'chatgpt',
    'gemini_video': 'gemini_video', 'gemini video': 'gemini_video',
    'gemini_image': 'gemini_image', 'gemini image': 'gemini_image',
    'muse': 'muse', 'muse ai': 'muse',
}
_TYPE_EXTRA = {
    'gemini':       {'project_url': '', 'task_mode': 'all'},
    'chatgpt':      {'project_url': '', 'task_mode': 'all'},
    'gemini_video': {'project_url': '', 'task_mode': 'video_only'},
    'gemini_image': {'project_url': '', 'task_mode': 'image_only'},
    'muse':         {'project_url': '', 'task_mode': 'all'},
}


def normalize_type(text: str):
    """'' → None (dùng của profile mẫu/mặc định); không nhận ra → False."""
    t = (text or '').strip().lower().replace('-', ' ')
    if not t:
        return None
    return _TYPES.get(t, _TYPES.get(t.replace(' ', '_'), False))


def parse_lines(text: str) -> list:
    """Mỗi dòng: `tên | hiển thị | email | mật khẩu | loại | proxy` (phân cách `|` hoặc Tab; cột sau
    có thể bỏ trống/bỏ hẳn). Dòng trống bị bỏ qua. Trả list dict các cột đã điền."""
    out = []
    for raw in (text or '').splitlines():
        if not raw.strip():
            continue
        parts = raw.split('|') if '|' in raw else raw.split('\t')
        row = {}
        for key, val in zip(_COLS, parts):
            val = val.strip()
            if val:
                row[key] = val
        if row.get('profile_name'):
            out.append(row)
    return out


def build_profiles(rows: list, template: dict | None) -> list:
    """Ghép từng dòng với thông số của profile mẫu → list dict sẵn sàng POST."""
    base = {'worker_mode': 'api', 'task_mode': 'all', 'enabled': 1}
    if template:
        base = {k: v for k, v in template.items() if k in _ALLOWED and k not in _IDENTITY}
    result = []
    for row in rows:
        row = dict(row)
        typ = row.pop('type', '')
        d = dict(base)
        d.update(row)
        mode = normalize_type(typ)
        if mode:
            d['worker_mode'] = mode
            d.update(_TYPE_EXTRA.get(mode, {}))
        result.append(d)
    return result


class BulkProfileDialog(QDialog):
    def __init__(self, parent=None, profiles: list | None = None):
        super().__init__(parent)
        self._profiles = profiles or []
        self.setWindowTitle('Thêm nhiều profile')
        self.setModal(True)
        self.setMinimumWidth(680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(10)
        lay.addWidget(lbl('Thêm nhiều profile', 'title'))
        lay.addWidget(lbl('Mỗi dòng 1 profile — thông số cài đặt copy từ profile mẫu', 'subtitle'))

        row = QHBoxLayout()
        row.addWidget(QLabel('Cấu hình mẫu:'))
        self._tpl = QComboBox()
        self._tpl.addItem('— Mặc định (API) —', None)
        for p in self._profiles:
            self._tpl.addItem(f'Sao chép thông số từ: {p.get("display_name") or p["profile_name"]}', p)
        row.addWidget(self._tpl, 1)
        lay.addLayout(row)

        hint = QLabel('Định dạng:  tên | hiển thị | email | mật khẩu | loại | proxy\n'
                      'Loại: api (VEO, mặc định), dom, gemini, chatgpt, gemini_video, gemini_image, muse '
                      '— để trống thì theo profile mẫu. Chỉ cần tên là đủ; cột sau để trống hoặc bỏ. '
                      'Phân cách bằng dấu | hoặc Tab.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color:#94a3b8; font-size:11px; background:transparent;')
        lay.addWidget(hint)

        self._text = QPlainTextEdit()
        self._text.setPlaceholderText(
            'acc01 | Acc 01 | acc01@gmail.com | matkhau | api | socks5://u:p@1.2.3.4:1080\n'
            'acc02 | | acc02@gmail.com | matkhau | gemini\n'
            'acc03')
        self._text.setMinimumHeight(220)
        self._text.textChanged.connect(self._update_count)
        lay.addWidget(self._text)

        self._count = QLabel('')
        self._count.setWordWrap(True)
        self._count.setStyleSheet('color:#94a3b8; font-size:11px; background:transparent;')
        lay.addWidget(self._count)

        bar = QHBoxLayout()
        bar.addStretch()
        b_cancel = btn('Hủy'); b_cancel.setFixedHeight(34)
        b_ok = btn('Thêm tất cả', 'primary'); b_ok.setFixedHeight(34)
        b_cancel.clicked.connect(self.reject)
        b_ok.clicked.connect(self._accept)
        bar.addWidget(b_cancel); bar.addWidget(b_ok)
        lay.addLayout(bar)
        self._update_count()

    def _bad_types(self, rows: list) -> list:
        return [f'{r["profile_name"]} ("{r["type"]}")' for r in rows
                if r.get('type') and normalize_type(r['type']) is False]

    def _update_count(self):
        rows = parse_lines(self._text.toPlainText())
        names = [r['profile_name'].lower() for r in rows]
        dup = len(names) - len(set(names))
        txt = f'{len(rows)} profile'
        if dup:
            txt += f'  •  ⚠ {dup} tên trùng trong danh sách'
        bad = self._bad_types(rows)
        if bad:
            txt += '  •  ⚠ Loại không nhận ra: ' + ', '.join(bad[:5])
        self._count.setText(txt)

    def _accept(self):
        rows = parse_lines(self._text.toPlainText())
        if not rows:
            self._count.setText('Chưa có dòng nào hợp lệ (cần ít nhất tên profile)')
            return
        if self._bad_types(rows):
            self._update_count()
            return
        self.accept()

    def get_profiles(self) -> list:
        rows = parse_lines(self._text.toPlainText())
        seen, uniq = set(), []
        for r in rows:                       # bỏ tên trùng trong chính danh sách
            k = r['profile_name'].lower()
            if k not in seen:
                seen.add(k); uniq.append(r)
        return build_profiles(uniq, self._tpl.currentData())
