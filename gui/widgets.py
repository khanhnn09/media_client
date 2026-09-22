"""Widget dùng chung nhiều nơi: nút/label có role, separator, drop-shadow effect,
badge trạng thái màu, stat card cho dashboard."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from .style import C


def btn(text='', role='', tip='', size: tuple | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty('role', role)
        b.style().unpolish(b); b.style().polish(b)
    if tip:
        b.setToolTip(tip)
    if size:
        b.setFixedSize(*size)
    return b


def sep() -> QFrame:
    f = QFrame()
    f.setProperty('role', 'separator')
    f.setFixedHeight(1)
    return f


def lbl(text='', role='') -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty('role', role)
    return lb


def add_shadow(widget: QWidget, blur=28, dx=0, dy=8, alpha=110, color='#000000') -> QGraphicsDropShadowEffect:
    """Elevation cho card/sidebar — QSS không hỗ trợ box-shadow nên dùng effect riêng."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(dx, dy)
    col = QColor(color); col.setAlpha(alpha)
    eff.setColor(col)
    widget.setGraphicsEffect(eff)
    return eff


class Badge(QLabel):
    """Colored status pill."""
    _STYLES = {
        'green':  (f'background:#1a4721; color:{C["green"]}; border:1px solid #2ea043;'),
        'blue':   (f'background:#1c3461; color:{C["accent"]}; border:1px solid #388bfd44;'),
        'red':    (f'background:#3d1a1a; color:{C["red"]}; border:1px solid #6e2020;'),
        'yellow': (f'background:#3d2f0a; color:{C["yellow"]}; border:1px solid #6e4c0f;'),
        'purple': (f'background:#271d40; color:{C["purple"]}; border:1px solid #6e40c9;'),
        'orange': (f'background:#2d1e0a; color:{C["orange"]}; border:1px solid #6b3311;'),
        'gray':   (f'background:{C["surface2"]}; color:{C["muted"]}; border:1px solid {C["border"]};'),
    }

    def __init__(self, text='', color='gray', parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.font(); f.setPointSize(9); f.setBold(True); self.setFont(f)
        self.set(text, color)

    def set(self, text: str, color: str = 'gray'):
        self.setText(text)
        base = self._STYLES.get(color, self._STYLES['gray'])
        self.setStyleSheet(f'{base} border-radius:4px; padding:2px 8px; font-size:10px; font-weight:700;')


class StatCard(QFrame):
    """Dashboard-style tile: icon + số liệu lớn + nhãn phụ, dùng thay cho chip phẳng."""

    def __init__(self, icon: str, label: str, color: str = 'accent'):
        super().__init__()
        self.setProperty('role', 'stat')
        self.setMinimumWidth(150)
        add_shadow(self, blur=22, dy=6, alpha=70)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setFixedSize(38, 38)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(f"""
            background: {C[color]}22;
            color: {C[color]};
            border-radius: 10px;
            font-size: 16px;
        """)
        lay.addWidget(icon_lbl)

        col = QVBoxLayout()
        col.setSpacing(1)
        self._num = QLabel('0')
        self._num.setStyleSheet(f'font-size:20px; font-weight:800; color:{C["text"]}; background:transparent;')
        self._lbl = QLabel(label)
        self._lbl.setStyleSheet(f'font-size:11px; color:{C["muted"]}; background:transparent;')
        col.addWidget(self._num)
        col.addWidget(self._lbl)
        lay.addLayout(col)
        lay.addStretch()

    def set_value(self, n, dim: bool = False):
        self._num.setText(str(n))
        self._num.setStyleSheet(
            f'font-size:20px; font-weight:800; color:{C["muted"] if dim else C["text"]}; background:transparent;'
        )

