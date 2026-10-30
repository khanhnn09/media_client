// Tiện ích dùng chung. Dựng DOM bằng h() (không innerHTML) để tên profile/log chứa ký tự HTML không phá giao diện.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value') el.value = v;
      else if (k in el && k !== 'list' && k !== 'type') { try { el[k] = v; } catch { el.setAttribute(k, v); } }
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export const pad2 = (n) => String(n).padStart(2, '0');

export function fmtClock(epochSecs) {
  const d = new Date(epochSecs * 1000);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

// Giờ 0..23 → "8-11h, 20-22h" (port của server/run_hours.py::summarize_run_hours).
export function parseHours(value) {
  const items = Array.isArray(value) ? value : String(value ?? '').split(',');
  const set = new Set();
  for (const it of items) {
    const n = parseInt(String(it).trim(), 10);
    if (Number.isInteger(n) && n >= 0 && n <= 23) set.add(n);
  }
  return [...set].sort((a, b) => a - b);
}

export function summarizeHours(value) {
  const hours = parseHours(value);
  if (!hours.length || hours.length === 24) return '';
  const ranges = [];
  let start = hours[0], prev = hours[0];
  for (const x of hours.slice(1)) {
    if (x === prev + 1) { prev = x; continue; }
    ranges.push([start, prev]);
    start = prev = x;
  }
  ranges.push([start, prev]);
  return ranges.map(([a, b]) => `${a}-${b + 1}h`).join(', ');
}

export const truthy = (v) => !(v == null || v === 0 || v === '0' || v === false || v === 'false' || v === 'False' || v === '');
