"""Bảng màu (GitHub Dark, refined) + QSS stylesheet dùng chung toàn bộ GUI."""

C = {
    'bg':       '#0d1117',
    'surface':  '#161b22',
    'surface2': '#21262d',
    'surface3': '#2a313c',
    'border':   '#30363d',
    'text':     '#e6edf3',
    'muted':    '#8b949e',
    'accent':   '#388bfd',
    'accent2':  '#79c0ff',
    'green':    '#3fb950',
    'red':      '#f85149',
    'yellow':   '#d29922',
    'purple':   '#a371f7',
    'orange':   '#db6d28',
}

QSS = f"""
* {{
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    color: {C['text']};
}}
QMainWindow, QDialog, QWidget {{
    background: {C['bg']};
}}

/* ── Scrollbar ── (2026-08-13: tăng độ rộng/tương phản — user báo "thanh
   trượt scroll cũng không thấy", handle cũ dùng C['border'] gần như trùng
   màu nền C['surface']/C['bg'] của các khung log/table tối màu, khó nhận ra
   có thể kéo) */
QScrollBar:vertical {{
    background: {C['surface']};
    width: 12px;
    border-radius: 5px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C['surface3']};
    border: 1px solid {C['muted']};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {C['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {C['surface']};
    height: 12px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {C['surface3']};
    border: 1px solid {C['muted']};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {C['muted']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── Table ── */
QTableWidget {{
    background: {C['surface']};
    alternate-background-color: {C['bg']};
    border: 1px solid {C['border']};
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: {C['surface2']};
    selection-color: {C['text']};
    outline: none;
}}
QTableWidget::item {{
    padding: 0 12px;
    border: none;
    border-bottom: 1px solid {C['border']};
}}
QTableWidget::item:selected {{
    background: {C['surface2']};
    color: {C['text']};
}}
QHeaderView::section {{
    background: {C['bg']};
    color: {C['muted']};
    padding: 0 12px;
    height: 38px;
    border: none;
    border-bottom: 1px solid {C['border']};
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}
QHeaderView {{ background: transparent; border: none; }}

/* ── Input / Combo ── */
QLineEdit, QTextEdit {{
    background: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 8px 12px;
    color: {C['text']};
    selection-background-color: {C['accent']};
}}
QLineEdit:focus, QTextEdit:focus {{
    border-color: {C['accent']};
    outline: none;
}}
QLineEdit:disabled {{
    color: {C['muted']};
    background: {C['surface']};
}}
QComboBox {{
    background: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 7px 32px 7px 12px;
    color: {C['text']};
    min-width: 120px;
}}
QComboBox:focus {{ border-color: {C['accent']}; }}
QComboBox::drop-down {{
    border: none;
    width: 28px;
}}
QComboBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C['muted']};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    selection-background-color: {C['accent']};
    color: {C['text']};
    padding: 4px;
    outline: none;
}}

/* ── Buttons ── */
QPushButton {{
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 600;
    background: {C['surface2']};
    color: {C['muted']};
    min-width: 32px;
    min-height: 32px;
}}
QPushButton:hover {{ background: {C['surface3']}; color: {C['text']}; }}
QPushButton:pressed {{ background: {C['surface']}; }}
QPushButton:disabled {{ color: #444c56; background: {C['surface']}; }}

QPushButton[role="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {C['accent2']}, stop:1 {C['accent']});
    color: #ffffff;
}}
QPushButton[role="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #91cbff, stop:1 {C['accent2']});
}}
QPushButton[role="primary"]:pressed {{ background: {C['accent']}; }}

QPushButton[role="success"] {{
    background: #1a4721;
    color: {C['green']};
    border: 1px solid #2ea043;
}}
QPushButton[role="success"]:hover {{ background: #238636; color: #fff; }}

QPushButton[role="danger"] {{
    background: #3d1a1a;
    color: {C['red']};
    border: 1px solid #6e2020;
}}
QPushButton[role="danger"]:hover {{ background: #b91c1c; color: #fff; }}

QPushButton[role="warning"] {{
    background: #3d2f0a;
    color: {C['yellow']};
    border: 1px solid #6e4c0f;
}}
QPushButton[role="warning"]:hover {{ background: #9e6a03; color: #fff; }}

QPushButton[role="icon"] {{
    background: transparent;
    color: {C['muted']};
    padding: 4px;
    border-radius: 6px;
    min-width: 28px;
    min-height: 28px;
    font-size: 14px;
}}
QPushButton[role="icon"]:hover {{ background: {C['surface2']}; color: {C['text']}; }}

/* ── Labels ── */
QLabel {{ background: transparent; }}
QLabel[role="title"] {{
    font-size: 20px;
    font-weight: 800;
    color: {C['text']};
    letter-spacing: -0.2px;
}}
QLabel[role="subtitle"] {{
    font-size: 12px;
    color: {C['muted']};
}}
QLabel[role="section"] {{
    font-size: 11px;
    font-weight: 700;
    color: {C['muted']};
    text-transform: uppercase;
    letter-spacing: 0.9px;
}}

/* ── Frame / Separator ── */
QFrame[role="separator"] {{
    background: {C['border']};
    max-height: 1px;
    border: none;
}}
QFrame[role="card"] {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-top: 1px solid #0fffffff;
    border-radius: 12px;
}}
QFrame[role="stat"] {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-top: 1px solid #0fffffff;
    border-radius: 12px;
}}

/* ── Checkbox ── */
QCheckBox {{ spacing: 8px; color: {C['muted']}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border-radius: 4px;
    border: 1.5px solid {C['border']};
    background: {C['surface2']};
}}
QCheckBox::indicator:checked {{
    background: {C['accent']};
    border-color: {C['accent']};
}}

/* ── Dialog ── */
QDialog {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 14px;
}}

/* ── Menu (2026-08-10, dropdown "⋮" trong ProfilesPage) ── */
QMenu {{
    background: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 14px;
    border-radius: 6px;
    color: {C['text']};
    font-size: 12px;
}}
QMenu::item:selected {{ background: {C['surface3']}; }}
QMenu::item:disabled {{ color: {C['muted']}; }}
QMenu::separator {{
    height: 1px;
    background: {C['border']};
    margin: 6px 4px;
}}
"""
