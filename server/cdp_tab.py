"""Client CDP (Chrome DevTools Protocol) gắn vào ĐÚNG 1 tab qua websocket riêng
(2026-09-27, dùng cho làn Omni — xem `server/omni_lane.py`).

Vì sao không dùng Selenium: 1 phiên Selenium chỉ có ĐÚNG 1 "cửa sổ hiện tại"
cho mọi lệnh — muốn chạy tab Omni SONG SONG với tab VEO thì phải
`switch_to.window` qua lại, mà vòng VEO có những đoạn chặn rất lâu (reconcile,
chờ render…). Chrome cho phép NHIỀU client CDP cùng lúc, mỗi client gắn 1
target riêng — nên làn Omni mở websocket thẳng tới tab của nó, hoàn toàn độc
lập với phiên Selenium của VEO.

Target id của 1 tab == window handle Selenium của tab đó (chromedriver dùng
luôn target id làm handle).
"""
from __future__ import annotations

import itertools
import json
import threading
import time
import urllib.request


class CdpError(RuntimeError):
    pass


def list_targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=5) as r:
        return json.loads(r.read().decode('utf-8'))


class CdpTab:
    def __init__(self, port: int, target_id: str):
        self.port = port
        self.target_id = target_id
        self._ws = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # ── kết nối ────────────────────────────────────────────────────────────
    def connect(self) -> 'CdpTab':
        import websocket   # websocket-client
        url = None
        for t in list_targets(self.port):
            if t.get('id') == self.target_id:
                url = t.get('webSocketDebuggerUrl')
                break
        if not url:
            raise CdpError(f'Không thấy tab {self.target_id[:8]} trên port {self.port} (đã đóng?)')
        # suppress_origin: Chrome ≥111 từ chối websocket có header Origin lạ.
        self._ws = websocket.create_connection(url, suppress_origin=True, timeout=30)
        return self

    def close(self):
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._ws = None

    @property
    def alive(self) -> bool:
        return self._ws is not None

    # ── lệnh ───────────────────────────────────────────────────────────────
    def send(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        """Gửi 1 lệnh rồi chờ ĐÚNG response của nó (bỏ qua event xen giữa)."""
        with self._lock:
            if not self._ws:
                self.connect()
            mid = next(self._ids)
            self._ws.settimeout(timeout)
            try:
                self._ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
                deadline = time.time() + timeout
                while True:
                    if time.time() > deadline:
                        raise CdpError(f'{method}: quá {timeout}s không có response')
                    msg = json.loads(self._ws.recv())
                    if msg.get('id') != mid:
                        continue
                    if 'error' in msg:
                        raise CdpError(f'{method}: {msg["error"]}')
                    return msg.get('result') or {}
            except CdpError:
                raise
            except Exception as e:
                # Websocket hỏng (tab đóng/Chrome chết) — lần sau tự kết nối lại.
                self.close()
                raise CdpError(f'{method}: {e}') from e

    def evaluate(self, expression: str, timeout: float = 60, await_promise: bool = True):
        r = self.send('Runtime.evaluate', {
            'expression': expression, 'awaitPromise': await_promise,
            'returnByValue': True, 'userGesture': True,
        }, timeout=timeout)
        if r.get('exceptionDetails'):
            d = r['exceptionDetails']
            raise CdpError(f'JS lỗi: {(d.get("exception") or {}).get("description") or d.get("text")}')
        return (r.get('result') or {}).get('value')

    def url(self) -> str:
        try:
            return self.evaluate('location.href', timeout=10, await_promise=False) or ''
        except Exception:
            return ''

    def navigate(self, url: str, wait: float = 30) -> str:
        """Điều hướng rồi chờ trang tải xong (document.readyState=complete)."""
        self.send('Page.navigate', {'url': url}, timeout=wait)
        deadline = time.time() + wait
        time.sleep(1.5)
        while time.time() < deadline:
            try:
                if self.evaluate('document.readyState', timeout=10, await_promise=False) == 'complete':
                    break
            except CdpError:
                pass
            time.sleep(1)
        return self.url()
