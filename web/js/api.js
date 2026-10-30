// Gọi control-API của chính server này (cùng origin) + cầu nối tới cửa sổ PyQt (QWebChannel).

export async function api(method, path, body, { timeout = 15000 } = {}) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  try {
    const opt = { method, signal: ctl.signal, headers: {} };
    if (body !== undefined && body !== null) {
      opt.body = JSON.stringify(body);
      opt.headers['Content-Type'] = 'application/json';
    }
    const r = await fetch(path, opt);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

// ── Cầu nối tới Python (chỉ có khi chạy trong cửa sổ PyQt) ───────────────────
let _bridge = null;
let _bridgeReady = null;

export function initBridge() {
  if (_bridgeReady) return _bridgeReady;
  _bridgeReady = new Promise((resolve) => {
    if (!window.qt || !window.qt.webChannelTransport) return resolve(null);
    const s = document.createElement('script');
    s.src = '/ui/qwebchannel.js';
    s.onload = () => {
      try {
        new window.QWebChannel(window.qt.webChannelTransport, (ch) => { _bridge = ch.objects.bridge || null; resolve(_bridge); });
      } catch { resolve(null); }
    };
    s.onerror = () => resolve(null);
    document.head.append(s);
  });
  return _bridgeReady;
}

export const hasBridge = () => !!_bridge;

// Gọi 1 hàm của bridge (slot PyQt) → Promise<kết quả>. Không có bridge → trả null.
export function bridgeCall(name, ...args) {
  return new Promise((resolve) => {
    if (!_bridge || typeof _bridge[name] !== 'function') return resolve(null);
    _bridge[name](...args, (res) => resolve(res));
  });
}
