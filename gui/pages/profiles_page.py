"""Trang Profiles — dashboard (StatCard) + bảng quản lý profile Chrome
(start/stop/mở login/sửa/xoá), hiển thị live task/lỗi/sleep countdown theo profile."""

import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..style import C
from ..widgets import btn, lbl, Badge, StatCard
from ..api_client import api
from ..profile_dialog import ProfileDialog
from server.run_hours import summarize_run_hours

# (2026-08-10) Nhãn/màu ĐỌC ĐƯỢC cho "Loại" profile (worker_mode) — theo yêu
# cầu user "badge mode dồn cục quá nhỏ không biết đang chạy gì". Trước đây cột
# Mode nhồi 2-3 Badge nhỏ liền kề (task_mode + worker_mode + x{N}) trong 130px,
# chữ bị cắt/khó đọc. Giờ mỗi profile chỉ còn ĐÚNG 1 badge "loại chính"
# (engine — to, màu rõ, khớp đúng combo "Loại *" trong ProfileDialog) + 1 dòng
# phụ nhỏ (không phải badge, chỉ text muted) cho chi tiết — mirror layout 2
# dòng đã dùng ở cột "Tên / Email" ngay bên cạnh, không phải pattern mới.
_ENGINE_BADGE = {
    'gemini':       ('✨ Gemini',       'purple'),
    'chatgpt':      ('🤖 ChatGPT',      'green'),
    'gemini_video': ('🎬 Gemini Video', 'purple'),
    # (2026-09-09) — engine THỨ 2 cho task ẢNH, mirror gemini_video
    'gemini_image': ('🖼️ Gemini Ảnh',  'purple'),
}
_TASK_MODE_ENGINE_BADGE = {
    'all':         ('🖼️🎬 VEO3',      'blue'),
    'image_only':  ('🖼️ VEO3 · Ảnh',  'purple'),
    'video_only':  ('🎬 VEO3 · Video', 'orange'),
}


class ProfilesPage(QWidget):
    open_logs = pyqtSignal(int)

    # (2026-08-10, theo yêu cầu user "cột trạng thái / token/ lỗi cũng làm
    # dang badge bỏ cột hôm nay") — gộp 3 cột Trạng thái/Tokens/Lỗi (trước
    # đứng riêng, mỗi cột 1 Badge) thành ĐÚNG 1 cột "Trạng thái" (2 dòng: badge
    # trạng thái chính + hàng badge Token/Lỗi nhỏ bên dưới, mirror layout đã
    # dùng cho cột Mode) — vẫn giữ NGUYÊN dạng Badge cho cả 3, chỉ gom lại 1
    # chỗ thay vì chiếm 3 cột riêng. Bỏ hẳn cột "Hôm nay" (số task hoàn thành/
    # lỗi trong ngày) — dữ liệu vẫn có, chuyển thành tooltip trên badge Lỗi
    # thay vì chiếm hẳn 1 cột.
    _HDR = ['', 'Tên / Email', 'Mode', 'Trạng thái', 'Task hiện tại', 'Port', 'Auto', 'Hành động']
    _W   = [10, 0,             165,    190,           200,             70,    82,     90]

    def __init__(self):
        super().__init__()
        self._profiles: list[dict] = []
        self._filter_text = ''
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(0)

        # ── Page header ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(0)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self._title_lbl = lbl('Profiles', 'title')
        self._sub_lbl   = lbl('Quản lý tài khoản Chrome cho Flow worker', 'subtitle')
        title_col.addWidget(self._title_lbl)
        title_col.addWidget(self._sub_lbl)
        hdr.addLayout(title_col)
        hdr.addStretch()

        self._btn_refresh = btn('↻', 'icon', 'Làm mới (F5)')
        self._btn_refresh.setFixedSize(36, 36)
        self._btn_add = btn('+ Thêm profile', 'primary')
        self._btn_add.setFixedHeight(36)
        hdr.addWidget(self._btn_refresh)
        hdr.addSpacing(8)
        hdr.addWidget(self._btn_add)
        lay.addLayout(hdr)
        lay.addSpacing(20)

        # ── Stat cards ────────────────────────────────────────────────────────
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._stat_total   = StatCard('👤', 'Tổng profile',  'accent')
        self._stat_running = StatCard('⚡', 'Đang chạy',     'green')
        self._stat_free    = StatCard('✅', 'Rảnh (sẵn sàng nhận task)', 'green')
        self._stat_busy     = StatCard('🎬', 'Đang xử lý task', 'accent')
        self._stat_sleeping = StatCard('😴', 'Đang ngủ',     'orange')
        self._stat_login   = StatCard('🌐', 'Đang login',    'yellow')
        self._stat_errors  = StatCard('⚠', 'Lỗi (phiên chạy)', 'red')
        for c in [self._stat_total, self._stat_running, self._stat_free,
                  self._stat_busy, self._stat_sleeping, self._stat_login, self._stat_errors]:
            stats.addWidget(c)
        stats.addStretch()
        lay.addLayout(stats)
        lay.addSpacing(16)

        # ── Search ────────────────────────────────────────────────────────────
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText('🔍  Tìm theo tên, tên hiển thị hoặc email…')
        self._search.setFixedHeight(36)
        self._search.setMaximumWidth(360)
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)
        search_row.addStretch()
        lay.addLayout(search_row)
        lay.addSpacing(14)

        # ── Table ──────────────────────────────────────────────────────────────
        self._table = QTableWidget(0, len(self._HDR))
        self._table.setHorizontalHeaderLabels(self._HDR)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setSortingEnabled(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate(self._W):
            if w: self._table.setColumnWidth(i, w)
        self._table.setColumnHidden(0, True)
        lay.addWidget(self._table)

        # Empty state (overlay text bên dưới header table, chỉ hiện khi rỗng)
        self._empty_lbl = QLabel('')
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(f'color:{C["muted"]}; font-size:13px; padding:36px; background:transparent;')
        self._empty_lbl.setVisible(False)
        lay.addWidget(self._empty_lbl)

        # Connections
        self._btn_add.clicked.connect(self._add)
        self._btn_refresh.clicked.connect(self.refresh)

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self):
        api('GET', '/api/selenium/status', on_done=self._on_data)

    def _on_data(self, data):
        if not isinstance(data, dict):
            return
        if 'error' in data and not data.get('profiles'):
            return  # server-side error, keep existing list
        self._profiles = data.get('profiles', [])
        n = len(self._profiles)
        n_run   = sum(1 for p in self._profiles if p.get('worker_running'))
        n_login = sum(1 for p in self._profiles if p.get('login_open'))
        # "Rảnh" = worker đang chạy nhưng KHÔNG có task nào đang xử lý — đây là số
        # profile sẵn sàng nhận task NGAY ở lần heartbeat kế tiếp (sức chứa hiện có).
        n_free  = sum(1 for p in self._profiles
                      if p.get('worker_running') and not p.get('liveTaskId'))
        n_busy  = sum(1 for p in self._profiles if p.get('liveTaskId'))
        n_sleep = sum(1 for p in self._profiles if p.get('status') == 'sleeping')
        # Tổng lỗi CHỈ trong phiên chạy hiện tại của từng worker (reset về 0 khi worker
        # restart — xem SeleniumFlowWorker._task_error_count), KHÔNG phải lỗi lịch sử.
        n_errors = sum(int(p.get('taskErrorCount') or 0) for p in self._profiles)

        self._stat_total.set_value(n, dim=(n == 0))
        self._stat_running.set_value(n_run, dim=(n_run == 0))
        self._stat_free.set_value(n_free, dim=(n_free == 0))
        self._stat_busy.set_value(n_busy, dim=(n_busy == 0))
        self._stat_sleeping.set_value(n_sleep, dim=(n_sleep == 0))
        self._stat_login.set_value(n_login, dim=(n_login == 0))
        self._stat_errors.set_value(n_errors, dim=(n_errors == 0))
        self._sub_lbl.setText(f'{n} profile • {n_run} worker đang chạy • {n_free} rảnh')

        self._populate()

    def _on_search(self, text: str):
        self._filter_text = text.strip().lower()
        self._populate()

    def _filtered(self) -> list[dict]:
        if not self._filter_text:
            return self._profiles
        ft = self._filter_text
        out = []
        for p in self._profiles:
            hay = ' '.join([
                p.get('profile_name', '') or '',
                p.get('display_name', '') or '',
                p.get('account_email', '') or '',
            ]).lower()
            if ft in hay:
                out.append(p)
        return out

    def _populate(self):
        sel_id = self._sel_id()
        self._table.setRowCount(0)
        rows = self._filtered()

        if not self._profiles:
            self._empty_lbl.setText('Chưa có profile nào — bấm "+ Thêm profile" để bắt đầu.')
            self._empty_lbl.setVisible(True)
            self._table.setVisible(False)
            return
        if not rows:
            self._empty_lbl.setText(f'Không tìm thấy profile khớp "{self._filter_text}".')
            self._empty_lbl.setVisible(True)
            self._table.setVisible(False)
            return
        self._empty_lbl.setVisible(False)
        self._table.setVisible(True)

        for p in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setRowHeight(row, 56)

            running    = p.get('worker_running', False)
            login_open = p.get('login_open', False)
            has_tokens = p.get('has_tokens', False)
            age        = p.get('token_age')
            port       = p.get('debug_port', '—')

            # Col 0: hidden id
            self._table.setItem(row, 0, QTableWidgetItem(str(p['id'])))

            # Col 1: Avatar + Name + Email
            cell = QWidget()
            cell.setStyleSheet('background: transparent;')
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(12, 6, 8, 6)
            cell_lay.setSpacing(12)

            av_text = (p.get('profile_name') or 'P')[0].upper()
            av = QLabel(av_text)
            av.setFixedSize(36, 36)
            av.setAlignment(Qt.AlignmentFlag.AlignCenter)
            av_color = C['green'] if running else C['accent'] if login_open else C['muted']
            av.setStyleSheet(f"""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {av_color}, stop:1 {av_color}aa);
                color: #ffffff;
                border-radius: 10px;
                font-weight: 800;
                font-size: 14px;
            """)

            info = QVBoxLayout()
            info.setSpacing(2)
            display = (p.get('display_name') or '').strip()
            name_lbl = QLabel(display if display else p.get('profile_name', ''))
            name_lbl.setStyleSheet(f'color:{C["text"]}; font-weight:600; font-size:13px; background:transparent;')
            sub_text = p.get('profile_name','') if display else (p.get('account_email') or '—')
            email_lbl = QLabel(sub_text)
            email_lbl.setStyleSheet(f'color:{C["muted"]}; font-size:11px; background:transparent;')
            info.addWidget(name_lbl)
            info.addWidget(email_lbl)

            # (2026-09-05) Dòng thứ 3 — proxy RIÊNG của profile (nếu có). Dùng
            # `parse_proxy()` của chính server để hiển thị bản ĐÃ CHE mật khẩu,
            # KHÔNG tự cắt chuỗi ở đây: dạng `host:port:user:pass` không có ký
            # tự '@' nào, cắt tay theo '@' sẽ hiện nguyên mật khẩu proxy lên UI.
            proxy_raw = (p.get('proxy_server') or '').strip()
            if proxy_raw:
                try:
                    from server.proxy_config import parse_proxy
                    info_p = parse_proxy(proxy_raw)
                    proxy_txt = info_p['display'] if info_p else f'{proxy_raw} (không hợp lệ)'
                except Exception:
                    proxy_txt = '(proxy)'
                proxy_lbl = QLabel(f'🌐 {proxy_txt}')
                proxy_lbl.setStyleSheet(f'color:{C["accent"]}; font-size:10px; background:transparent;')
                proxy_lbl.setToolTip(f'Chrome của profile này đi qua proxy: {proxy_txt}')
                info.addWidget(proxy_lbl)

            cell_lay.addWidget(av)
            cell_lay.addLayout(info)
            self._table.setCellWidget(row, 1, cell)

            # Col 2: Mode — (2026-08-10) đổi từ 2-3 Badge nhồi ngang (chữ bị
            # cắt trong 130px, "không biết đang chạy gì") sang layout DỌC 2
            # dòng: dòng 1 = ĐÚNG 1 badge "loại chính" (to, màu rõ theo đúng
            # combo "Loại *" trong ProfileDialog); dòng 2 = text phụ nhỏ
            # (KHÔNG phải badge, chỉ QLabel muted) cho chi tiết task_mode/
            # worker_mode/số tab đồng thời — cùng tinh thần layout 2 dòng đã
            # dùng ở cột "Tên / Email" ngay bên cạnh.
            mode_w = QWidget(); mode_w.setStyleSheet('background:transparent;')
            mode_col = QVBoxLayout(mode_w); mode_col.setContentsMargins(12,6,4,6); mode_col.setSpacing(3)
            worker_mode = p.get('worker_mode', 'api')
            max_conc = p.get('max_concurrent') or 1

            if worker_mode in _ENGINE_BADGE:
                text, color = _ENGINE_BADGE[worker_mode]
                engine_badge = Badge(text, color)
                sub_parts = []
                if worker_mode == 'gemini':
                    sub_parts.append('Chat + Video')
                    tabs = p.get('gemini_max_concurrent_tabs') or 1
                    if tabs > 1: sub_parts.append(f'{tabs} tab')
                elif worker_mode == 'chatgpt':
                    sub_parts.append('Chat + Ảnh')
                elif worker_mode == 'gemini_video':
                    sub_parts.append('Tạo video qua chat')
                    if max_conc > 1: sub_parts.append(f'x{max_conc}')
                else:  # gemini_image
                    sub_parts.append('Tạo ảnh qua chat')
                    if max_conc > 1: sub_parts.append(f'x{max_conc}')
                sub_text = ' · '.join(sub_parts)
            else:  # veo3 (api/dom)
                task_mode = p.get('task_mode', 'all')
                text, color = _TASK_MODE_ENGINE_BADGE.get(task_mode, _TASK_MODE_ENGINE_BADGE['all'])
                engine_badge = Badge(text, color)
                sub_parts = [f'{worker_mode.upper()} mode', f'x{max_conc}']
                # (2026-09-17) Khung giờ chạy riêng — rỗng = chạy liên tục, không hiện.
                hours_span = summarize_run_hours(p.get('run_hours'))
                if hours_span:
                    sub_parts.append(f'⏰ {hours_span}')
                sub_text = ' · '.join(sub_parts)

            engine_row = QHBoxLayout(); engine_row.setSpacing(0)
            engine_row.addWidget(engine_badge); engine_row.addStretch()
            mode_col.addLayout(engine_row)

            sub_lbl = QLabel(sub_text)
            sub_lbl.setStyleSheet(f'color:{C["muted"]}; font-size:10px; background:transparent;')
            mode_col.addWidget(sub_lbl)
            self._table.setCellWidget(row, 2, mode_w)

            # Col 3: Trạng thái — (2026-08-10, theo yêu cầu user "cột trạng
            # thái / token/ lỗi cũng làm dang badge bỏ cột hôm nay") GỘP 3 cột
            # cũ (Trạng thái/Tokens/Lỗi, mỗi cột trước đứng riêng 1 Badge) vào
            # ĐÚNG 1 cột, layout DỌC 2 dòng mirror cột Mode: dòng 1 = badge
            # trạng thái CHÍNH (to, logic/màu giữ NGUYÊN như cũ); dòng 2 = hàng
            # ngang 2 badge nhỏ Token + Lỗi (VẪN LÀ Badge, không rút gọn thành
            # text, chỉ gom lại 1 chỗ). "Hôm nay" (done/error trong ngày) bỏ
            # hẳn khỏi bảng — giữ lại dưới dạng tooltip trên badge Lỗi.
            st_w = QWidget(); st_w.setStyleSheet('background:transparent;')
            st_col = QVBoxLayout(st_w); st_col.setContentsMargins(12,6,4,6); st_col.setSpacing(3)

            status = p.get('status', 'idle')
            if running:
                st = Badge('⚡  Running', 'green')
            elif login_open:
                st = Badge('🌐  Login open', 'yellow')
            elif p.get('worker_waiting') or status == 'waiting':
                st = Badge('⏳  Waiting', 'blue')
            elif status == 'sleeping':
                wake_at = p.get('sleepUntil')
                remain  = int(wake_at - time.time()) if wake_at else None
                if remain is not None and remain > 0:
                    st = Badge(f'😴  Ngủ {remain//60}:{remain%60:02d}', 'orange')
                    st.setToolTip(f'Thức dậy lúc {time.strftime("%H:%M:%S", time.localtime(wake_at))}')
                else:
                    st = Badge('😴  Sleeping', 'orange')
            elif not p.get('enabled', 1):
                # 2026-07-17 — chỉ tới đây khi KHÔNG running/login/waiting/sleeping
                # (nếu đang chạy dở task lúc bị tắt, dispatcher để chạy xong trước
                # khi đóng — xem dispatcher.py _auto_scale_veo3_tick — nên badge
                # "Running" vẫn ưu tiên hiện đúng cho tới lúc đó).
                st = Badge('🚫  Đã tắt', 'red')
                st.setToolTip('Profile đang TẮT — không tự mở (auto-scale) và không '
                               'bấm Start thủ công được. Sửa profile để bật lại.')
            else:
                st = Badge(status, 'gray')
            st_row = QHBoxLayout(); st_row.setSpacing(0)
            st_row.addWidget(st); st_row.addStretch()
            st_col.addLayout(st_row)

            # Token — logic/màu giữ NGUYÊN như cột "Tokens" cũ.
            if running:
                if has_tokens:
                    tk = Badge(f'✔  {age}s' if age is not None else '✔  ok', 'green')
                else:
                    tk = Badge('✘  —', 'red')
            else:
                tk = Badge('—', 'gray')

            # Lỗi — logic/màu/tooltip giữ NGUYÊN như cột "Lỗi" cũ, cộng thêm
            # tooltip "Hôm nay" (done/error TRONG NGÀY, bền vững phía backend —
            # KHÁC err_total chỉ tính trong phiên chạy hiện tại) thay cho cột
            # riêng đã bỏ.
            err_total = int(p.get('taskErrorCount') or 0)
            err_consec = int(p.get('consecutiveErrors') or 0)
            done_today  = int(p.get('tasks_done_today') or 0)
            error_today = int(p.get('tasks_error_today') or 0)
            today_tip = f'Hôm nay: ✅ {done_today}  ❌ {error_today}'
            if err_total > 0:
                color = 'red' if err_consec > 0 else 'orange'
                text  = f'{err_total}' + (f' ({err_consec} liên tiếp)' if err_consec else '')
                err_badge = Badge(text, color)
                last_msg = (p.get('lastErrorMsg') or '')[:300]
                last_at  = p.get('lastErrorAt')
                tip = last_msg
                if last_at:
                    tip = f'{time.strftime("%H:%M:%S", time.localtime(last_at))} — {last_msg}'
                err_badge.setToolTip(f'{tip}\n{today_tip}' if tip else today_tip)
            else:
                err_badge = Badge('0', 'gray')
                err_badge.setToolTip(today_tip)

            sub_row = QHBoxLayout(); sub_row.setSpacing(4)
            sub_row.addWidget(tk); sub_row.addWidget(err_badge); sub_row.addStretch()
            st_col.addLayout(sub_row)
            self._table.setCellWidget(row, 3, st_w)

            # Col 4: Task hiện tại — id + mode + thời gian đã chạy (liveTaskId/Mode/StartedAt
            # từ live_info() phía server); tooltip hiện đầy đủ prompt. Fallback về
            # current_task_id (cột DB) nếu worker chưa kịp trả live_info (vd vừa mới nhận task).
            live_task_id = p.get('liveTaskId') or p.get('current_task_id')
            if live_task_id:
                mode_txt = p.get('liveTaskMode') or ''
                started  = p.get('liveTaskStartedAt')
                elapsed  = int(time.time() - started) if started else None
                parts = [f'#{live_task_id}']
                if mode_txt: parts.append(mode_txt)
                if elapsed is not None: parts.append(f'{elapsed}s')
                task_txt = ' · '.join(parts)
            else:
                task_txt = '—'
            task_item = QTableWidgetItem(task_txt)
            task_item.setForeground(QColor(C['yellow'] if live_task_id else C['muted']))
            task_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            prompt_tip = p.get('liveTaskPrompt') or ''
            if prompt_tip:
                task_item.setToolTip(prompt_tip)
            self._table.setItem(row, 4, task_item)

            # Col 5: Port
            port_item = QTableWidgetItem(str(port))
            port_color = C['green'] if (running or login_open) else C['border']
            port_item.setForeground(QColor(port_color))
            port_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, port_item)

            # Col 6: Auto — bật/tắt `enabled` NGAY TRÊN DANH SÁCH (2026-09-04,
            # theo yêu cầu user "bật tắt auto profile chỉnh ngay trên danh sách
            # profile không phải vào edit mới chỉnh được"). Cùng field với
            # checkbox "Bật — cho phép chạy" trong ProfileDialog — `enabled=0`
            # thì auto-scale KHÔNG mở profile này và Start thủ công cũng bị
            # chặn (xem CLAUDE.md §11.9).
            auto_on = bool(p.get('enabled', 1))
            auto_w = QWidget(); auto_w.setStyleSheet('background:transparent;')
            auto_lay = QHBoxLayout(auto_w)
            auto_lay.setContentsMargins(0, 0, 0, 0)
            auto_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b_auto = btn('🟢 Bật' if auto_on else '⚪ Tắt', 'icon',
                         'Đang BẬT — auto-scale được phép mở profile này.\n'
                         'Bấm để TẮT (không nhận task mới).' if auto_on else
                         'Đang TẮT — auto-scale bỏ qua, Start thủ công cũng bị chặn.\n'
                         'Bấm để BẬT.')
            b_auto.setFixedWidth(66)
            b_auto.setStyleSheet(
                f'color:{C["green"]};' if auto_on else f'color:{C["muted"]};')
            b_auto.clicked.connect(
                lambda _, pid=p['id'], cur=auto_on: self._toggle_auto(pid, cur))
            auto_lay.addWidget(b_auto)
            self._table.setCellWidget(row, 6, auto_w)

            # Col 7: Actions — (2026-08-10, theo yêu cầu user "đưa các action
            # thành dropdown") trước đây nhồi tới 5 icon-button liền kề (start/
            # stop, mở/đóng login, xem logs, sửa, xóa) trong 140px — giờ chỉ
            # còn ĐÚNG 1 nút chính luôn thấy (Start/Stop — hành động dùng
            # thường xuyên nhất, cần bấm nhanh không qua menu) + 1 nút "⋮" mở
            # menu thả xuống (`_build_actions_menu`) gom mọi action PHỤ còn lại.
            ac = QWidget(); ac.setStyleSheet('background:transparent;')
            ac_lay = QHBoxLayout(ac); ac_lay.setContentsMargins(8,0,8,0); ac_lay.setSpacing(4)

            if running:
                b_stop = btn('⏹', 'icon', 'Stop worker')
                b_stop.clicked.connect(lambda _, pid=p['id']: self._stop(pid))
                ac_lay.addWidget(b_stop)
            else:
                # worker_mode='gemini'/'chatgpt'/'gemini_video'/'gemini_image'
                # không cần project_url (Flow project) — chỉ VEO3 (api/dom) mới cần.
                is_enabled = bool(p.get('enabled', 1))
                can_start = is_enabled and (worker_mode in ('gemini', 'chatgpt', 'gemini_video', 'gemini_image')
                                             or bool(p.get('project_url')))
                if not is_enabled:
                    start_tip = 'Profile đang TẮT — sửa profile để bật lại trước khi Start'
                elif can_start:
                    start_tip = 'Start worker'
                else:
                    start_tip = 'Set Flow URL trước'
                b_start = btn('▶', 'icon', start_tip)
                b_start.setEnabled(can_start)
                b_start.clicked.connect(lambda _, pid=p['id']: self._start(pid))
                ac_lay.addWidget(b_start)

            b_more = btn('⋮', 'icon', 'Hành động khác')
            b_more.setMenu(self._build_actions_menu(p, running, login_open))
            ac_lay.addWidget(b_more)

            self._table.setCellWidget(row, 7, ac)

            if p['id'] == sel_id:
                self._table.selectRow(row)

    # ── Menu "⋮" — gom mọi action PHỤ (2026-08-10, thay 4-5 icon-button rời
    # rạc trước đây) ─────────────────────────────────────────────────────────
    def _build_actions_menu(self, p: dict, running: bool, login_open: bool) -> QMenu:
        pid = p['id']
        menu = QMenu(self)

        if login_open:
            act = QAction('✕  Đóng login browser', menu)
            act.triggered.connect(lambda _, i=pid: self._close_login(i))
        else:
            act = QAction('🌐  Mở login browser', menu)
            act.setEnabled(not running)
            if running:
                act.setToolTip('Stop worker trước')
            act.triggered.connect(lambda _, i=pid: self._open_login(i))
        menu.addAction(act)

        act_logs = QAction('📜  Xem logs', menu)
        act_logs.triggered.connect(lambda _, i=pid: self.open_logs.emit(i))
        menu.addAction(act_logs)

        act_edit = QAction('✏  Sửa profile', menu)
        act_edit.triggered.connect(lambda _, pp=p: self._edit(pp))
        menu.addAction(act_edit)

        menu.addSeparator()

        # "Làm mới profile (trắng)" (2026-08-10 — bắt đầu từ yêu cầu "thêm xóa
        # cache cho profile: cache, cookie, browing history - all time", mở
        # rộng theo yêu cầu tiếp theo "còn gì cần xóa thì xóa sạch coi như 1
        # profile trắng" — giờ xoá SẠCH toàn bộ profile_dir, không chỉ
        # cache/cookie/history). CHỈ bấm được khi profile hoàn toàn ĐÓNG
        # (không worker, không login browser) — xoá file lúc Chrome đang giữ
        # handle dễ corrupt, mirror guard đã có ở backend
        # (`routes.py::clear_profile_data`).
        act_clear = QAction('🧹  Làm mới profile (xóa sạch — về trắng)', menu)
        can_clear = not running and not login_open
        act_clear.setEnabled(can_clear)
        if not can_clear:
            act_clear.setToolTip('Đóng worker/login browser trước khi làm mới profile')
        act_clear.triggered.connect(lambda _, i=pid, pp=p: self._clear_cache(i, pp))
        menu.addAction(act_clear)

        menu.addSeparator()

        act_del = QAction('🗑  Xóa profile', menu)
        act_del.triggered.connect(lambda _, i=pid: self._delete(i))
        menu.addAction(act_del)

        return menu

    # ── Actions ───────────────────────────────────────────────────────────────

    def _sel_id(self) -> int | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows: return None
        try: return int(self._table.item(rows[0].row(), 0).text())
        except: return None

    def _add(self):
        dlg = ProfileDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        api('POST', '/api/selenium/profiles', dlg.get_data(),
            on_done=lambda r: self._chk(r) or self.refresh())

    def _edit(self, p: dict):
        dlg = ProfileDialog(self, p)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        api('PATCH', f'/api/selenium/profiles/{p["id"]}', dlg.get_data(),
            on_done=lambda _: self.refresh())

    def _delete(self, pid: int):
        p = next((x for x in self._profiles if x['id'] == pid), None)
        name = p['profile_name'] if p else f'#{pid}'
        if QMessageBox.question(self, 'Xóa profile',
            f'Xóa profile "{name}"?\nDữ liệu Chrome sẽ giữ lại.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes: return
        api('DELETE', f'/api/selenium/profiles/{pid}', on_done=lambda _: self.refresh())

    def _clear_cache(self, pid: int, p: dict):
        """(2026-08-10, mở rộng theo yêu cầu user "còn gì cần xóa thì xóa
        sạch coi như 1 profile trắng") — xoá SẠCH toàn bộ dữ liệu Chrome của
        profile (không chỉ cache/cookie/history nữa) — xem
        `server/managers.py::pm.clear_browser_data()`. Cảnh báo RÕ trước khi
        xoá: mất TOÀN BỘ dữ liệu Chrome (đăng nhập/mật khẩu đã lưu/bookmark/
        cài đặt...), profile coi như hoàn toàn mới, cần "Mở login browser"
        đăng nhập lại trước khi Start được — hành động khó hoàn tác (không
        có "undo")."""
        name = (p.get('display_name') or p.get('profile_name') or f'#{pid}')
        if QMessageBox.question(self, 'Làm mới profile (xóa sạch — về trắng)',
            f'Xóa SẠCH TOÀN BỘ dữ liệu Chrome của profile "{name}" — coi như 1 profile hoàn toàn mới?\n\n'
            '⚠️ Mất: đăng nhập Google, mật khẩu đã lưu, bookmark, cache, cookie, lịch sử, '
            'mọi cài đặt Chrome khác của profile này. Cần "Mở login browser" đăng nhập lại '
            'từ đầu trước khi Start worker được. KHÔNG THỂ HOÀN TÁC.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes: return
        api('POST', f'/api/selenium/profiles/{pid}/clear_data',
            on_done=lambda r: self._chk(r) or self.refresh())

    def _toggle_auto(self, pid: int, current: bool):
        """Bật/tắt `enabled` ngay trên danh sách (không cần mở dialog Sửa).

        Tắt KHÔNG dừng worker đang chạy dở — auto-scale tự đóng khi profile
        rảnh (`_auto_scale_veo3_tick`/`_auto_scale_chat_worker_group`), giữ
        đúng hành vi sẵn có của field này khi sửa qua dialog."""
        api('PATCH', f'/api/selenium/profiles/{pid}',
            {'enabled': 0 if current else 1},
            on_done=lambda _: self.refresh())

    def _start(self, pid: int):
        api('POST', f'/api/selenium/profiles/{pid}/start',
            on_done=lambda r: self._chk(r) or self.refresh())

    def _stop(self, pid: int):
        api('POST', f'/api/selenium/profiles/{pid}/stop', on_done=lambda _: self.refresh())

    def _open_login(self, pid: int):
        api('POST', f'/api/selenium/profiles/{pid}/open',
            on_done=lambda r: self._chk(r) or self.refresh())

    def _close_login(self, pid: int):
        api('POST', f'/api/selenium/profiles/{pid}/close', on_done=lambda _: self.refresh())

    def _chk(self, r: dict) -> bool:
        if r and r.get('error'):
            if r['error'] == 'outside_run_hours':
                QMessageBox.information(
                    self, 'Ngoài khung giờ chạy',
                    f'Profile chỉ nhận task trong khung giờ: {r.get("run_hours") or "?"} '
                    '(giờ máy này).\nTới giờ, auto-scale sẽ tự mở profile khi có task.')
                return True
            QMessageBox.critical(self, 'Lỗi', r['error']); return True
        return False


