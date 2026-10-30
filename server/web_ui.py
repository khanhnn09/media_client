"""Giao diện web của client_tool (2026-10-30).

Thay GUI PyQt cũ: toàn bộ giao diện là 1 trang web tĩnh (client_tool/web/) do chính
Flask embedded phục vụ tại `/ui/`, được nhúng vào cửa sổ chính bằng QWebEngineView
(xem gui/main_window.py). Trang gọi lại các API `/api/selenium/*` sẵn có — module này
chỉ thêm 3 thứ nhỏ mà JS không tự làm được:

  GET  /ui/, /ui/<file>           file tĩnh (ép mimetype vì Windows có thể map .js → text/plain,
                                  làm ES module không nạp được)
  GET  /api/selenium/ui_meta      phiên bản, user đang đăng nhập, định nghĩa các ô Cài đặt,
                                  danh sách mục fingerprint
  POST /api/selenium/ui_format    định dạng hộ vài giá trị cần logic của server (proxy đã che
                                  mật khẩu) — không port sang JS vì dạng `host:port:user:pass`
                                  không có '@', cắt tay dễ lộ mật khẩu lên màn hình.
"""

from pathlib import Path

from flask import jsonify, request, send_from_directory

from .config import app, APP_VERSION

WEB_DIR = Path(__file__).resolve().parent.parent / 'web'

_MIME = {
    '.html': 'text/html',
    '.js': 'text/javascript',
    '.mjs': 'text/javascript',
    '.css': 'text/css',
    '.svg': 'image/svg+xml',
    '.json': 'application/json',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
}


def _static(filename: str):
    resp = send_from_directory(WEB_DIR, filename, mimetype=_MIME.get(Path(filename).suffix.lower()))
    # Luôn lấy bản mới: file nằm trong thư mục code, sửa xong chỉ cần tải lại trang.
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/ui/')
@app.route('/ui')
def ui_index():
    return _static('index.html')


@app.route('/ui/qwebchannel.js')
def ui_qwebchannel():
    """Thư viện JS của QWebChannel — nằm trong tài nguyên biên dịch sẵn của Qt (không có file trên đĩa),
    đọc ra rồi trả lại để trang (nạp qua http) dùng được cầu nối tới Python."""
    from flask import Response
    from PyQt6.QtCore import QFile, QIODevice
    import PyQt6.QtWebChannel  # noqa: F401  (import để đăng ký tài nguyên qrc:/qtwebchannel)
    f = QFile(':/qtwebchannel/qwebchannel.js')
    if not f.open(QIODevice.OpenModeFlag.ReadOnly):
        return Response('', status=404)
    try:
        data = bytes(f.readAll())
    finally:
        f.close()
    return Response(data, mimetype='text/javascript', headers={'Cache-Control': 'no-store'})


@app.route('/ui/<path:filename>')
def ui_file(filename):
    return _static(filename)


@app.route('/api/selenium/ui_meta', methods=['GET'])
def ui_meta():
    from .settings_schema import SETTINGS_TABS, HIDDEN_FIELDS
    from .cloak_browser import FP_ASPECTS
    from . import auth_client
    user = auth_client.get_current_user() or {}
    return jsonify({
        'app_version': APP_VERSION,
        'user': user.get('displayName') or user.get('username') or '',
        'fp_aspects': FP_ASPECTS,
        'settings_tabs': [{'title': t, 'fields': [
            {'key': k, 'label': lb, 'type': ty, 'hint': h} for (k, lb, ty, h) in fields]}
            for (t, fields) in SETTINGS_TABS],
        'settings_hidden': [k for (k, *_rest) in HIDDEN_FIELDS],
    })


@app.route('/api/selenium/ui_format', methods=['POST'])
def ui_format():
    """Body: {proxies: [raw,...]} → {proxies: {raw: displayĐãChe}}."""
    from .proxy_config import parse_proxy
    d = request.json or {}
    out = {}
    for raw in d.get('proxies') or []:
        raw = (raw or '').strip()
        if not raw:
            continue
        try:
            info = parse_proxy(raw)
            out[raw] = info['display'] if info else f'{raw} (không hợp lệ)'
        except Exception:
            out[raw] = '(proxy)'
    return jsonify({'proxies': out})
