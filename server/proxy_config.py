"""Proxy RIÊNG cho từng Chrome profile (2026-09-05).

Theo yêu cầu user: "cho phép nhập proxy trên UI" → "proxy setting cho từng
profile" — mỗi profile có 1 ô "Proxy" trong `ProfileDialog`, lưu vào cột
`selenium_profiles.proxy_server` (backend sở hữu bảng này, xem CLAUDE.md §11.4).
Để trống = đi thẳng, không proxy (hành vi cũ, không đổi gì).

Điểm áp dụng DUY NHẤT: `chrome_utils._build_chrome_options(proxy=...)` — mọi
đường mở Chrome đều đi qua đó (worker uc lẫn thường, "Mở login browser" ở
routes.py, harness test ở tests/utils/profile_target.py), nên không cần sửa
từng call site.

⚠️ PHẠM VI — CHỈ traffic của TRÌNH DUYỆT đi qua proxy. Các lệnh HTTP do CHÍNH
Python bắn (heartbeat/tải ref ảnh từ backend qua `requests`) VẪN đi thẳng —
đúng ý muốn:
heartbeat/tải file nội bộ không nên vòng qua proxy. Đường generate CHÍNH hiện
tại (`batchexecute`) chạy bằng `fetch()` TRONG TRANG nên vẫn được proxy che,
xem CLAUDE.md §11.46.

⚠️ AUTH (user/pass): Chrome KHÔNG nhận credentials nhúng trong `--proxy-server`
(`http://user:pass@host:port` — phần credentials bị bỏ qua im lặng). Cách duy
nhất còn dùng được trên Chrome hiện đại (MV2 đã bị gỡ hẳn từ Chrome 139) là 1
extension MV3 bắt `chrome.webRequest.onAuthRequired` với quyền
`webRequestAuthProvider` — quyền Chrome thêm vào MV3 ĐÚNG cho use case này.
`ensure_proxy_auth_extension()` sinh extension đó vào thư mục RIÊNG theo profile
(`data/proxy_auth_ext/profile_{id}/` — mỗi profile 1 bộ credentials khác nhau,
KHÔNG dùng chung 1 thư mục nếu không profile mở sau sẽ ghi đè creds của profile
đang chạy) rồi nạp qua `--load-extension` — nạp KỂ CẢ khi `load_extensions=False`
(cờ đó chỉ để tắt extension Flow, không liên quan proxy).

⚠️ SOCKS5 CÓ user/pass (2026-09-23): Chrome KHÔNG xác thực được SOCKS ở bất kỳ
dạng nào (kể cả extension) → trỏ Chrome vào 1 relay SOCKS5 không mật khẩu trên
127.0.0.1, relay tự xác thực với proxy thật (`socks_relay.py`). Dạng
`IP:PORT:USER:PASS` không ghi scheme thì DÒ thật xem là SOCKS5 hay HTTP
(`detect_scheme()`), không còn đoán cứng 'http'. SOCKS4 vẫn không có auth.
"""

import json
import re
from pathlib import Path

from .config import _this_dir, log
from .socks_relay import detect_scheme, ensure_socks5_relay

# (host, port) -> scheme dò được, để không dò lại mỗi lần mở Chrome.
_detected_schemes = {}

# Thư mục gốc chứa extension proxy-auth TỰ SINH (mỗi profile 1 thư mục con).
# LUÔN dùng `_this_dir` (= client_tool/) chứ KHÔNG phải `_ROOT` — `_ROOT` trỏ LÊN
# repo cha ToolSub khi client_tool nằm trong checkout đầy đủ, sẽ ghi extension ra
# ngoài project này. Cùng nguyên tắc với LOGS_DIR/VIDEO_TMP_DIR, xem CLAUDE.md §11.23.
PROXY_EXT_ROOT = Path(_this_dir) / 'data' / 'proxy_auth_ext'

# Scheme Chrome chấp nhận cho --proxy-server.
_VALID_SCHEMES = ('http', 'https', 'socks4', 'socks5')

# Không cho proxy đụng vào localhost — client_tool nói chuyện với chính nó
# (server nhúng cổng 13445) và backend nội bộ; vòng qua proxy là vô nghĩa và
# dễ hỏng. Chrome mặc định đã bypass loopback, khai báo tường minh cho chắc.
DEFAULT_BYPASS_LIST = 'localhost;127.0.0.1;[::1]'

_SCHEME_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.\-]*)://(.*)$')


def parse_proxy(raw: str):
    """Parse chuỗi proxy user gõ ở UI thành dict, hoặc `None` nếu rỗng/không hợp lệ.

    Chấp nhận mọi định dạng phổ biến (người bán proxy mỗi nơi ghi 1 kiểu):
        host:port
        scheme://host:port
        scheme://user:pass@host:port
        user:pass@host:port
        host:port:user:pass          <- rất phổ biến, IP:PORT:USER:PASS

    Trả: {scheme, host, port, username, password, server, display}
        `server`  - giá trị truyền cho `--proxy-server` (ĐÃ bỏ credentials).
        `display` - bản che mật khẩu, an toàn để log/hiển thị lên UI.
    """
    raw = (raw or '').strip()
    if not raw:
        return None

    scheme = 'http'
    explicit_scheme = False
    m = _SCHEME_RE.match(raw)
    if m:
        scheme = m.group(1).lower()
        explicit_scheme = True
        rest = m.group(2)
    else:
        rest = raw
    rest = rest.strip().strip('/')

    if scheme not in _VALID_SCHEMES:
        log.warning(f'[proxy] Scheme "{scheme}" không được Chrome hỗ trợ '
                    f'(chỉ {", ".join(_VALID_SCHEMES)}) — BỎ QUA proxy.')
        return None

    username = password = ''
    if '@' in rest:
        creds, hostport = rest.rsplit('@', 1)
        if ':' in creds:
            username, password = creds.split(':', 1)
        else:
            username = creds
    else:
        parts = rest.split(':')
        if len(parts) == 4:
            # host:port:user:pass — dạng người bán proxy hay đưa
            hostport = f'{parts[0]}:{parts[1]}'
            username, password = parts[2], parts[3]
        else:
            hostport = rest

    if ':' in hostport:
        host, _, port_s = hostport.rpartition(':')
    else:
        host, port_s = hostport, ''

    host = host.strip()
    port_s = port_s.strip()
    if not host:
        log.warning(f'[proxy] Không đọc được host từ "{raw}" — BỎ QUA proxy.')
        return None
    if port_s:
        if not port_s.isdigit() or not (1 <= int(port_s) <= 65535):
            log.warning(f'[proxy] Port "{port_s}" không hợp lệ trong "{raw}" — BỎ QUA proxy.')
            return None

    hostport_disp = f'{host}:{port_s}' if port_s else host
    server = f'{scheme}://{hostport_disp}'
    display = f'{scheme}://{username}:******@{hostport_disp}' if username else server

    return {
        'scheme':   scheme,
        'host':     host,
        'port':     int(port_s) if port_s else 0,
        'username': username,
        'password': password,
        'server':   server,
        'display':  display,
        # Không ghi scheme → 'http' chỉ là đoán; build_proxy_setup() sẽ dò lại.
        'explicit_scheme': explicit_scheme,
    }


_MANIFEST = {
    'manifest_version': 3,
    'name':    'client_tool Proxy Auth',
    'version': '1.0.0',
    'description': 'Tu dien user/pass cho proxy - sinh tu dong boi client_tool.',
    # `webRequestAuthProvider` là quyền Chrome thêm riêng cho MV3 để extension
    # được trả credentials trong onAuthRequired (thay cho `webRequestBlocking`
    # của MV2 vốn chỉ còn dành cho extension cài bằng policy).
    'permissions': ['webRequest', 'webRequestAuthProvider'],
    'host_permissions': ['<all_urls>'],
    'background': {'service_worker': 'background.js'},
}

_BACKGROUND_JS = """// Sinh tu dong boi client_tool (server/proxy_config.py) - KHONG sua tay.
const CREDENTIALS = __CREDS__;

chrome.webRequest.onAuthRequired.addListener(
  (details, callback) => {
    // CHI tra credentials cho challenge cua PROXY. Trang web tu hoi mat khau
    // (details.isProxy === false) phai de nguyen cho nguoi dung/Chrome xu ly -
    // tra bua credentials proxy vao do la gui mat khau proxy cho site la.
    if (!details.isProxy) { callback({}); return; }
    callback({ authCredentials: CREDENTIALS });
  },
  { urls: ['<all_urls>'] },
  ['asyncBlocking']
);
"""


def ensure_proxy_auth_extension(username: str, password: str, profile_id=None) -> str:
    """Ghi (idempotent) extension MV3 điền sẵn user/pass proxy, trả về đường dẫn
    thư mục để nạp qua `--load-extension`. Trả '' nếu ghi lỗi (proxy vẫn dùng
    được, chỉ là Chrome sẽ hiện hộp thoại hỏi mật khẩu).

    Thư mục RIÊNG theo `profile_id` — mỗi profile 1 proxy/credentials khác nhau,
    dùng chung 1 thư mục sẽ khiến profile mở sau ghi đè creds của profile đang
    chạy (Chrome đọc file lúc load extension, nhưng service worker có thể bị
    khởi động lại giữa chừng và đọc lại file đã bị ghi đè)."""
    try:
        ext_dir = PROXY_EXT_ROOT / (f'profile_{profile_id}' if profile_id is not None else 'shared')
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / 'manifest.json').write_text(
            json.dumps(_MANIFEST, ensure_ascii=False, indent=2), encoding='utf-8')
        creds = json.dumps({'username': username, 'password': password}, ensure_ascii=False)
        (ext_dir / 'background.js').write_text(
            _BACKGROUND_JS.replace('__CREDS__', creds), encoding='utf-8')
        return str(ext_dir)
    except Exception as e:
        log.warning(f'[proxy] Không tạo được extension proxy-auth: {e}')
        return ''


def build_proxy_setup(proxy: str, profile_id=None) -> dict:
    """Trả {'args': [...], 'ext_paths': [...], 'display': str} để
    `_build_chrome_options()` gắn thẳng vào ChromeOptions.

    Rỗng (args/ext_paths rỗng) khi: `proxy` rỗng, hoặc chuỗi không parse được
    (đã log lý do) — Chrome chạy như không có proxy, KHÔNG chặn worker khởi
    động (proxy hỏng cấu hình không đáng để cả profile không mở được)."""
    empty = {'args': [], 'ext_paths': [], 'display': ''}
    try:
        info = parse_proxy(proxy)
        if not info:
            return empty

        # Không ghi scheme mà có user/pass (dạng IP:PORT:USER:PASS) → dò thật xem
        # là SOCKS5 hay HTTP. Đoán bừa 'http' cho proxy SOCKS5 = Chrome mất mạng
        # hoàn toàn (bug thật 2026-09-23). Kết quả dò được cache theo host:port.
        if info['username'] and not info['explicit_scheme']:
            key = (info['host'], info['port'])
            if key not in _detected_schemes:
                _detected_schemes[key] = detect_scheme(
                    info['host'], info['port'], info['username'], info['password'])
            if _detected_schemes[key] != info['scheme']:
                info['scheme'] = _detected_schemes[key]
                log.info(f'[proxy] Dò được proxy {info["host"]}:{info["port"]} là '
                         f'{info["scheme"].upper()}')

        hostport = f'{info["host"]}:{info["port"]}' if info['port'] else info['host']
        server = f'{info["scheme"]}://{hostport}'
        ext_paths = []
        display = f'{info["scheme"]}://{info["username"]}:******@{hostport}'             if info['username'] else server

        if info['username']:
            if info['scheme'] == 'socks5':
                # Chrome không xác thực được SOCKS → trỏ vào relay cục bộ không
                # mật khẩu, relay tự xác thực với proxy thật (xem socks_relay.py).
                local_port = ensure_socks5_relay(
                    info['host'], info['port'], info['username'], info['password'])
                server = f'socks5://127.0.0.1:{local_port}'
                display += f' (qua relay 127.0.0.1:{local_port})'
            elif info['scheme'] == 'socks4':
                log.warning('[proxy] SOCKS4 KHÔNG hỗ trợ user/pass — proxy này phải '
                            'whitelist IP. Bỏ qua phần credentials.')
            else:
                p = ensure_proxy_auth_extension(info['username'], info['password'], profile_id)
                if p:
                    ext_paths.append(p)

        args = [
            f'--proxy-server={server}',
            f'--proxy-bypass-list={DEFAULT_BYPASS_LIST}',
        ]

        return {'args': args, 'ext_paths': ext_paths, 'display': display}
    except Exception as e:
        log.warning(f'[proxy] Lỗi khi dựng cấu hình proxy: {e}')
        return empty
