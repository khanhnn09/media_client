"""Dọn sạch tàn dư từ lần chạy TRƯỚC ĐÓ ngay lúc khởi động (2026-07-20, theo yêu
cầu user: "đã tắt hết vẫn thấy server offline hãy làm cho tôi phương án start
thì phải reset hết tránh trường hợp này").

Bug gốc: `gui/main_window.py::_start_embedded_server_worker()` phát hiện port
`SELENIUM_PORT` (13445) đã bị process khác giữ (thường là 1 bản client_tool cũ
chưa thoát sạch — server thread không exit, hoặc GUI đóng nhưng process mẹ vẫn
sống) thì CHỈ báo lỗi yêu cầu user tự vào Task Manager kill tay — không có gì tự
động dọn cả. Người dùng đóng hết cửa sổ nhìn thấy được nhưng process nền vẫn
sống sót phía sau (vd driver.quit() bị treo lúc đóng, hoặc closeEvent() không
kịp chạy nếu Windows force-kill cửa sổ) → lần mở lại vẫn thấy y hệt lỗi cũ.

Module này chạy 1 lần duy nhất, NGAY LÚC khởi động app (trước khi tạo
MainWindow) — tìm process đang giữ port, XÁC NHẬN nó thật sự là 1 bản
client_tool cũ (gọi GET /api/selenium/health — chỉ app này có route đó, tránh
kill nhầm phần mềm khác tình cờ dùng cùng port), rồi kill CƯỠNG BỨC cả tiến
trình đó LẪN toàn bộ tiến trình con của nó (Chrome/chromedriver mồ côi) để có
1 khởi đầu hoàn toàn sạch mỗi lần start — đúng yêu cầu "reset hết"."""

import requests


def kill_stale_client_tool(port: int) -> str | None:
    """Trả None nếu port đã sạch (không có gì / đã dọn xong). Trả message lỗi
    nếu phát hiện process LẠ (không phải client_tool) đang giữ port — trường
    hợp này KHÔNG tự kill, để caller tự quyết định (báo lỗi rõ như hành vi cũ)."""
    try:
        r = requests.get(f'http://127.0.0.1:{port}/api/selenium/health', timeout=2)
        # Chỉ status 200 là CHƯA ĐỦ để coi là client_tool — bất kỳ web server nào
        # trả 200 cho MỌI path (khá phổ biến, vd health-check catch-all) cũng sẽ
        # khớp nhầm. Verify thêm nội dung JSON đúng shape của CHÍNH route này
        # (`server/routes.py::health()`): có key 'profiles_dir' riêng của
        # client_tool VÀ field 'port' echo đúng port đang hỏi.
        try:
            data = r.json()
        except Exception:
            data = None
        is_client_tool = (
            r.ok and isinstance(data, dict)
            and 'profiles_dir' in data and data.get('port') == port
        )
    except Exception:
        return None  # không có gì đang nghe port này — không cần dọn gì

    if not is_client_tool:
        return (f'Port {port} đang bị 1 phần mềm KHÁC (không phải client_tool) chiếm giữ — '
                f'không tự động dọn được, kiểm tra thủ công qua Task Manager.')

    try:
        import psutil
    except ImportError:
        return (f'Phát hiện bản client_tool cũ còn giữ port {port} nhưng thiếu thư viện '
                f'psutil để tự dọn — chạy lại start.bat (tự cài psutil) rồi thử lại, '
                f'hoặc tự đóng process đó qua Task Manager.')

    procs = []
    for conn in psutil.net_connections(kind='tcp'):
        if conn.status != psutil.CONN_LISTEN or not conn.laddr or conn.laddr.port != port or not conn.pid:
            continue
        try:
            proc = psutil.Process(conn.pid)
        except psutil.NoSuchProcess:
            continue
        procs.append(proc)
        procs.extend(proc.children(recursive=True))  # dọn luôn Chrome/chromedriver mồ côi

    seen, targets = set(), []
    for p in procs:
        if p.pid not in seen:
            seen.add(p.pid)
            targets.append(p)

    for p in targets:
        try:
            p.kill()
        except Exception:
            pass
    if targets:
        psutil.wait_procs(targets, timeout=5)
    return None
