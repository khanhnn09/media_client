"""Trang Extensions — quản lý Chrome extension load vào profile (thêm/verify/
bật-tắt/xoá)."""

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..style import C
from ..widgets import btn, lbl, Badge, add_shadow
from ..api_client import api, api_sync


class ExtensionsPage(QWidget):
    _HDR = ['id', 'Tên extension', 'Đường dẫn', 'Trạng thái', '']
    _W   = [0,    180,             0,            100,           80]

    def __init__(self):
        super().__init__()
        self._exts: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(0)

        # Header
        hdr = QHBoxLayout()
        title_col = QVBoxLayout(); title_col.setSpacing(4)
        title_col.addWidget(lbl('Extensions', 'title'))
        title_col.addWidget(lbl('Extension local tự động load vào Chrome khi worker start', 'subtitle'))
        hdr.addLayout(title_col); hdr.addStretch()
        b_refresh = btn('↻', 'icon', 'Làm mới'); b_refresh.setFixedSize(36,36)
        b_refresh.clicked.connect(self.refresh)
        hdr.addWidget(b_refresh)
        lay.addLayout(hdr)
        lay.addSpacing(24)

        # Add card
        card = QFrame(); card.setProperty('role','card')
        add_shadow(card, blur=24, dy=6, alpha=70)
        card_lay = QVBoxLayout(card); card_lay.setContentsMargins(20,16,20,16); card_lay.setSpacing(12)

        card_title = lbl('THÊM EXTENSION', 'section'); card_title.setContentsMargins(0,0,0,4)
        card_lay.addWidget(card_title)

        row1 = QHBoxLayout(); row1.setSpacing(8)
        self._path_edit = QLineEdit(); self._path_edit.setPlaceholderText('Đường dẫn thư mục extension (chứa manifest.json)')
        self._path_edit.setFixedHeight(36)
        b_browse = btn('📂', 'icon', 'Duyệt thư mục'); b_browse.setFixedSize(36,36)
        b_browse.clicked.connect(self._browse)
        row1.addWidget(self._path_edit); row1.addWidget(b_browse)
        card_lay.addLayout(row1)

        row2 = QHBoxLayout(); row2.setSpacing(8)
        self._name_edit = QLineEdit(); self._name_edit.setPlaceholderText('Tên gợi nhớ (để trống = tự điền từ manifest)')
        self._name_edit.setFixedHeight(36)
        b_verify = btn('🔍 Kiểm tra', tip='Đọc manifest.json'); b_verify.setFixedHeight(36)
        b_verify.clicked.connect(self._verify)
        b_add = btn('+ Thêm', 'primary'); b_add.setFixedHeight(36); b_add.setFixedWidth(90)
        b_add.clicked.connect(self._add)
        row2.addWidget(self._name_edit); row2.addWidget(b_verify); row2.addWidget(b_add)
        card_lay.addLayout(row2)

        self._verify_lbl = QLabel('')
        self._verify_lbl.setStyleSheet(f'font-size:11px; color:{C["green"]}; background:transparent;')
        card_lay.addWidget(self._verify_lbl)
        lay.addWidget(card)
        lay.addSpacing(16)

        # Table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(self._HDR)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setColumnHidden(0, True)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(1, 180); self._table.setColumnWidth(3, 100); self._table.setColumnWidth(4, 80)
        lay.addWidget(self._table)

    def refresh(self):
        api('GET', '/api/selenium/extensions', on_done=self._on_data)

    def _on_data(self, data):
        if not isinstance(data, list): return
        self._exts = data
        self._table.setRowCount(0)
        for e in data:
            row = self._table.rowCount(); self._table.insertRow(row); self._table.setRowHeight(row, 44)

            self._table.setItem(row, 0, QTableWidgetItem(str(e['id'])))

            name_item = QTableWidgetItem(e.get('ext_name',''))
            name_item.setForeground(QColor(C['text']))
            self._table.setItem(row, 1, name_item)

            path_item = QTableWidgetItem(e.get('ext_path',''))
            path_item.setForeground(QColor(C['muted']))
            self._table.setItem(row, 2, path_item)

            en = e.get('enabled', 1)
            st_w = QWidget(); st_w.setStyleSheet('background:transparent;')
            st_lay = QHBoxLayout(st_w); st_lay.setContentsMargins(12,0,0,0)
            st_lay.addWidget(Badge('✔ Bật' if en else '✘ Tắt', 'green' if en else 'gray'))
            st_lay.addStretch()
            self._table.setCellWidget(row, 3, st_w)

            ac = QWidget(); ac.setStyleSheet('background:transparent;')
            ac_lay = QHBoxLayout(ac); ac_lay.setContentsMargins(8,0,8,0); ac_lay.setSpacing(4)
            b_tog = btn('✔' if not en else '✘', 'icon', 'Bật' if not en else 'Tắt')
            b_tog.clicked.connect(lambda _, eid=e['id'], enabled=not en: self._toggle(eid, enabled))
            b_del = btn('🗑', 'icon', 'Xóa')
            b_del.clicked.connect(lambda _, eid=e['id']: self._remove(eid))
            ac_lay.addWidget(b_tog); ac_lay.addWidget(b_del)
            self._table.setCellWidget(row, 4, ac)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, 'Chọn thư mục extension')
        if path: self._path_edit.setText(path)

    def _verify(self):
        path = self._path_edit.text().strip()
        if not path:
            self._verify_lbl.setStyleSheet(f'font-size:11px; color:{C["red"]}; background:transparent;')
            self._verify_lbl.setText('Nhập đường dẫn trước'); return
        r = api_sync('POST', '/api/selenium/extensions/verify', {'ext_path': path})
        if r.get('ok'):
            self._verify_lbl.setStyleSheet(f'font-size:11px; color:{C["green"]}; background:transparent;')
            self._verify_lbl.setText(f'✔  {r.get("name","")} v{r.get("version","")}')
            if not self._name_edit.text() and r.get('name'):
                self._name_edit.setText(r['name'])
        else:
            self._verify_lbl.setStyleSheet(f'font-size:11px; color:{C["red"]}; background:transparent;')
            self._verify_lbl.setText(f'✘  {r.get("error","")}')

    def _add(self):
        path = self._path_edit.text().strip()
        if not path: QMessageBox.warning(self,'Lỗi','Nhập đường dẫn extension'); return
        r = api_sync('POST', '/api/selenium/extensions',
                     {'ext_path': path, 'ext_name': self._name_edit.text().strip()})
        if r.get('error'): QMessageBox.critical(self,'Lỗi',r['error']); return
        self._path_edit.clear(); self._name_edit.clear(); self._verify_lbl.clear()
        self.refresh()

    def _toggle(self, eid, enabled):
        api('PATCH', f'/api/selenium/extensions/{eid}', {'enabled': enabled},
            on_done=lambda _: self.refresh())

    def _remove(self, eid):
        if QMessageBox.question(self,'Xóa',f'Xóa extension #{eid}?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes: return
        api('DELETE', f'/api/selenium/extensions/{eid}', on_done=lambda _: self.refresh())


