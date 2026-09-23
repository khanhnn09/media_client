"""Relay SOCKS5 cục bộ — cho Chrome dùng được proxy SOCKS5 CÓ user/pass (2026-09-23).

Chrome KHÔNG hỗ trợ xác thực SOCKS ở bất kỳ dạng nào (kể cả extension
`onAuthRequired` — chỉ bắn cho HTTP proxy). Trong khi phần lớn proxy bán dạng
`IP:PORT:USER:PASS` lại là SOCKS5 có mật khẩu → trước đây Chrome mở ra là mất
mạng hoàn toàn.

Cách giải: mở 1 cổng SOCKS5 KHÔNG mật khẩu trên 127.0.0.1, Chrome trỏ vào đó.
Mỗi kết nối Chrome mở tới:
  1. đọc lời chào SOCKS5 của Chrome,
  2. tự kết nối lên proxy thật + bắt tay có user/pass (RFC 1929),
  3. trả lời Chrome "không cần auth" (hoặc từ chối nếu upstream lỗi),
  4. từ đây CHUYỂN NGUYÊN byte 2 chiều — lệnh CONNECT của Chrome đi thẳng tới
     upstream, nên DNS vẫn phân giải ở phía proxy như bình thường.

Relay chỉ bind 127.0.0.1 (máy khác không dùng ké được) và sống suốt vòng đời
tiến trình client_tool; mỗi bộ (host, port, user, pass) dùng chung 1 cổng.
"""

import socket
import threading

from .config import log

_relays = {}            # (host, port, user, pass) -> local_port
_relays_lock = threading.Lock()
_CONNECT_TIMEOUT = 20


def _recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('đóng kết nối giữa chừng')
        buf += chunk
    return buf


def _upstream_handshake(host, port, user, pw):
    """Kết nối + xác thực với proxy SOCKS5 thật. Trả socket đã sẵn sàng nhận CONNECT."""
    up = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
    try:
        up.sendall(b'\x05\x02\x00\x02' if user else b'\x05\x01\x00')
        ver, method = _recv_exact(up, 2)
        if ver != 5:
            raise ConnectionError('upstream không phải SOCKS5')
        if method == 2:
            u, p = user.encode(), pw.encode()
            up.sendall(b'\x01' + bytes([len(u)]) + u + bytes([len(p)]) + p)
            if _recv_exact(up, 2)[1] != 0:
                raise PermissionError('sai user/pass proxy')
        elif method != 0:
            raise PermissionError('proxy từ chối mọi phương thức xác thực')
        return up
    except Exception:
        up.close()
        raise


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (dst, src):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle(client, host, port, user, pw, state):
    up = None
    try:
        client.settimeout(_CONNECT_TIMEOUT)
        ver, n = _recv_exact(client, 2)
        if ver != 5:
            return
        _recv_exact(client, n)
        try:
            up = _upstream_handshake(host, port, user, pw)
        except Exception as e:
            if not state.get('warned'):
                state['warned'] = True
                log.warning(f'[proxy-relay] Không kết nối được proxy {host}:{port}: {e}')
            client.sendall(b'\x05\xff')
            return
        state['warned'] = False
        client.sendall(b'\x05\x00')
        client.settimeout(None)
        up.settimeout(None)
        t = threading.Thread(target=_pipe, args=(up, client), daemon=True)
        t.start()
        _pipe(client, up)
        t.join()
    except Exception:
        pass
    finally:
        for s in (client, up):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def ensure_socks5_relay(host: str, port: int, user: str, pw: str) -> int:
    """Trả cổng cục bộ của relay cho proxy này (tạo mới nếu chưa có)."""
    key = (host, int(port), user, pw)
    with _relays_lock:
        if key in _relays:
            return _relays[key]
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(('127.0.0.1', 0))
        srv.listen(128)
        local_port = srv.getsockname()[1]
        state = {}

        def _accept_loop():
            while True:
                try:
                    c, _ = srv.accept()
                except OSError:
                    return
                threading.Thread(target=_handle, args=(c, host, int(port), user, pw, state),
                                 daemon=True).start()

        threading.Thread(target=_accept_loop, daemon=True, name=f'socks-relay-{local_port}').start()
        _relays[key] = local_port
        log.info(f'[proxy-relay] 127.0.0.1:{local_port} → socks5://{user}:******@{host}:{port}')
        return local_port


def detect_scheme(host: str, port: int, user: str, pw: str) -> str:
    """Dò proxy là SOCKS5 hay HTTP khi user không ghi scheme. Trả 'socks5'/'http'.

    Chỉ gửi lời chào giao thức, không mở kết nối ra ngoài. Không dò được
    (proxy chết/timeout) thì trả 'http' — giữ đúng mặc định cũ."""
    try:
        with socket.create_connection((host, int(port)), timeout=8) as s:
            s.settimeout(8)
            s.sendall(b'\x05\x02\x00\x02' if user else b'\x05\x01\x00')
            r = s.recv(2)
            if len(r) == 2 and r[0] == 5:
                return 'socks5'
    except Exception:
        pass
    return 'http'
