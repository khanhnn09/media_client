"""HTTP client nội bộ — gọi API của selenium_flow.py (embedded trong cùng process,
xem main_window.py) trên background thread (QThreadPool) để không chặn UI."""

import requests
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

from .config import API_BASE


class _Signals(QObject):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)


class ApiCall(QRunnable):
    def __init__(self, method, path, body=None, on_done=None, on_err=None):
        super().__init__()
        self.method, self.path, self.body = method, path, body
        self.sig = _Signals()
        if on_done: self.sig.done.connect(on_done)
        if on_err:  self.sig.error.connect(on_err)

    @pyqtSlot()
    def run(self):
        try:
            kw = {'timeout': 12}
            if self.body is not None:
                kw['json'] = self.body
                kw['headers'] = {'Content-Type': 'application/json'}
            r = getattr(requests, self.method.lower())(f'{API_BASE}{self.path}', **kw)
            self.sig.done.emit(r.json())
        except Exception as e:
            self.sig.error.emit(str(e))


def api(method, path, body=None, on_done=None, on_err=None):
    QThreadPool.globalInstance().start(ApiCall(method, path, body, on_done, on_err))


def api_sync(method, path, body=None) -> dict:
    try:
        kw = {'timeout': 12}
        if body is not None:
            kw['json'] = body; kw['headers'] = {'Content-Type': 'application/json'}
        return getattr(requests, method.lower())(f'{API_BASE}{path}', **kw).json()
    except Exception as e:
        return {'error': str(e)}

