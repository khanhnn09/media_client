// Thành phần giao diện dùng chung: toast, hộp thoại xác nhận, modal, menu thả xuống, badge, thẻ thống kê.
import { h, clear } from './util.js';

// ── Toast ──
export function toast(msg, kind = '') {
  const el = h('div', { class: `toast ${kind}` }, msg);
  document.getElementById('toast-root').append(el);
  setTimeout(() => el.remove(), kind === 'err' ? 7000 : 3500);
}

// ── Modal ──
// openModal({title, icon, wide, body, footer}) → { close(), el }
export function openModal({ title, icon = '', wide = false, body, footer = [], onClose, sub } = {}) {
  const root = document.getElementById('modal-root');
  const overlay = h('div', { class: 'overlay' });
  const bodyEl = h('div', { class: 'modal-bd' }, body);
  const modal = h('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog' },
    h('div', { class: 'modal-hd' },
      icon && h('div', { class: 'ic' }, icon),
      h('div', null, h('div', { class: 'title', style: { fontSize: '16px' } }, title), sub && h('div', { class: 'subtitle' }, sub))),
    bodyEl,
    h('div', { class: 'modal-ft' }, footer));
  overlay.append(modal);
  root.append(overlay);

  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  function close(result) {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    onClose && onClose(result);
  }
  return { close, el: modal, bodyEl };
}

// ── Hộp thoại xác nhận / thông báo ──
export function confirmDialog(message, { title = 'Xác nhận', okText = 'Đồng ý', danger = false } = {}) {
  return new Promise((resolve) => {
    let m;
    const done = (v) => { m.close(); resolve(v); };
    m = openModal({
      title, onClose: () => resolve(false),
      body: h('div', { class: 'form-pane', style: { whiteSpace: 'pre-wrap' } }, message),
      footer: [
        h('button', { class: 'btn', onclick: () => done(false) }, 'Hủy'),
        h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => done(true) }, okText),
      ],
    });
  });
}

export function alertDialog(message, title = 'Thông báo') {
  return new Promise((resolve) => {
    let m;
    m = openModal({
      title, onClose: () => resolve(),
      body: h('div', { class: 'form-pane', style: { whiteSpace: 'pre-wrap' } }, message),
      footer: [h('button', { class: 'btn primary', onclick: () => m.close() }, 'Đóng')],
    });
  });
}

// ── Menu thả xuống ──
// items: [{label, onClick, disabled, title}] hoặc '-' (đường kẻ). anchor: phần tử để căn vị trí.
let _closeMenu = null;
export function showMenu(anchor, items) {
  if (_closeMenu) _closeMenu();
  const menu = h('div', { class: 'menu' });
  const closeAll = () => { menu.remove(); document.removeEventListener('mousedown', outside, true); _closeMenu = null; };
  const outside = (e) => { if (!menu.contains(e.target)) closeAll(); };
  for (const it of items) {
    if (it === '-') { menu.append(h('hr')); continue; }
    menu.append(h('button', {
      disabled: !!it.disabled, title: it.title || '',
      onclick: () => { closeAll(); it.onClick && it.onClick(); },
    }, it.label));
  }
  document.body.append(menu);
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = Math.min(r.left, window.innerWidth - mw - 8);
  let top = r.bottom + 4;
  if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 4);
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${top}px`;
  setTimeout(() => document.addEventListener('mousedown', outside, true), 0);
  _closeMenu = closeAll;
}

// ── Badge / thẻ thống kê ──
export const badge = (text, color = 'gray', tip = '') => h('span', { class: `badge ${color}`, title: tip }, text);

const STAT_COLOR = { accent: '#388bfd', green: '#3fb950', orange: '#db6d28', yellow: '#d29922', red: '#f85149' };
export function statCard(icon, label, color) {
  const n = h('div', { class: 'n dim' }, '0');
  const el = h('div', { class: 'stat' },
    h('div', { class: 'ic', style: { background: `${STAT_COLOR[color]}22`, color: STAT_COLOR[color] } }, icon),
    h('div', null, n, h('div', { class: 'l' }, label)));
  el.set = (v) => { n.textContent = v; n.classList.toggle('dim', !v); };
  return el;
}

// Tiêu đề trang
export function pageHeader(title, subtitleEl, actions = []) {
  return h('div', { class: 'page-hdr' },
    h('div', { class: 'grow' }, h('h1', { class: 'title' }, title), subtitleEl),
    h('div', { class: 'row' }, actions));
}

export { clear };
