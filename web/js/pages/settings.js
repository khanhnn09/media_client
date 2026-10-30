// Trang Cài đặt: các tab ô nhập sinh từ /api/selenium/ui_meta (server/settings_schema.py) + tab Danh tính máy.
import { h, clear, truthy } from '../util.js';
import { api } from '../api.js';
import { pageHeader, confirmDialog, toast } from '../ui.js';

export function createSettingsPage(ctx) {
  const sub = h('div', { class: 'subtitle' }, 'Settings cục bộ của máy này — không đồng bộ từ server');
  const inputs = {};                 // key → {el, type}
  const tabBar = h('div', { class: 'tabs', style: { margin: '0 0 12px', padding: 0 } });
  const host = h('div');
  const saveBar = h('div', { class: 'row', style: { marginTop: '12px' } });
  const status = h('span', { style: { fontSize: '11px', fontWeight: 600 } });
  let tabs = [], active = 0, built = false;

  function say(txt, ok) { status.style.color = ok ? 'var(--green)' : 'var(--red)'; status.textContent = txt; if (ok) setTimeout(() => { status.textContent = ''; }, 3000); }

  function fieldsPane(fields) {
    const card = h('div', { class: 'card' });
    for (const f of fields) {
      let ctl;
      if (f.type === 'bool') {
        ctl = h('input', { type: 'checkbox' });
        card.append(h('div', { class: 'set-row bool' },
          h('div', null, h('label', { class: 'chk' }, ctl, f.label), f.hint && h('div', { class: 'hint' }, f.hint))));
      } else {
        ctl = h('input', { type: 'text', style: { width: f.type === 'list' ? '260px' : '200px' } });
        card.append(h('div', { class: 'set-row' },
          h('div', null, h('div', { class: 'lb' }, f.label), f.hint && h('div', { class: 'hint' }, f.hint)), ctl));
      }
      inputs[f.key] = { el: ctl, type: f.type, label: f.label };
    }
    return card;
  }

  // ── Tab danh tính ──
  const idIn = h('input', { type: 'text', readOnly: true });
  const nameIn = h('input', { type: 'text', placeholder: 'vd: PC Văn phòng 1' });
  const idStatus = h('span', { style: { fontSize: '11px', fontWeight: 600 } });
  function identityPane() {
    return h('div', { class: 'card' },
      h('div', { style: { fontWeight: 700, marginBottom: '4px' } }, '🪪  Danh tính client_tool này'),
      h('div', { class: 'hint', style: { marginBottom: '12px' } }, 'Dùng để backend phân biệt profile/extension của máy này với các máy client_tool khác'),
      h('div', { class: 'form-row' }, h('div', { class: 'lb' }, 'Client ID (cố định)'), idIn),
      h('div', { class: 'form-row' }, h('div', { class: 'lb' }, 'Tên gợi nhớ (tuỳ chỉnh)'), nameIn),
      h('div', { class: 'row', style: { marginTop: '10px' } }, idStatus, h('div', { style: { flex: 1 } }),
        h('button', { class: 'btn primary', onclick: async () => {
          try {
            const r = await api('PATCH', '/api/selenium/client_identity', { clientName: nameIn.value.trim() });
            if (r && typeof r === 'object') nameIn.value = r.client_name || '';
            idStatus.style.color = 'var(--green)'; idStatus.textContent = '✔ Đã lưu'; setTimeout(() => { idStatus.textContent = ''; }, 3000);
          } catch { idStatus.style.color = 'var(--red)'; idStatus.textContent = 'Lỗi khi lưu — server offline?'; }
        } }, '💾  Lưu tên')));
  }

  function build() {
    const meta = ctx.store.meta;
    tabs = meta.settings_tabs.map((t) => ({ title: t.title, el: fieldsPane(t.fields), settings: true }));
    tabs.push({ title: 'Danh tính máy', el: identityPane(), settings: false });
    clear(tabBar).append(...tabs.map((t, i) => h('button', { onclick: () => show(i) }, t.title)));
    built = true;
    show(0);
  }

  function show(i) {
    active = i;
    clear(host).append(tabs[i].el);
    [...tabBar.children].forEach((b, j) => b.classList.toggle('active', j === i));
    saveBar.style.display = tabs[i].settings ? '' : 'none';
  }

  function apply(s) {
    for (const [key, { el, type }] of Object.entries(inputs)) {
      const v = s[key];
      if (type === 'bool') el.checked = truthy(v) && !['false', 'False'].includes(String(v));
      else if (type === 'list') el.value = Array.isArray(v) ? v.join(', ') : String(v ?? '');
      else el.value = v == null ? '' : String(v);
    }
  }

  function collect() {
    const payload = {};
    for (const [key, { el, type, label }] of Object.entries(inputs)) {
      if (type === 'bool') { payload[key] = el.checked ? 1 : 0; continue; }
      const raw = el.value.trim();
      if (type === 'int' || type === 'float') {
        const n = type === 'int' ? parseInt(raw, 10) : parseFloat(raw);
        if (raw === '' || Number.isNaN(n) || (type === 'int' && !/^-?\d+$/.test(raw))) return { err: `Giá trị không hợp lệ: "${label}"` };
        payload[key] = n;
      } else payload[key] = raw;
    }
    return { payload };
  }

  async function save() {
    const { payload, err } = collect();
    if (err) return say(err, false);
    try {
      const d = await api('PATCH', '/api/selenium/local_settings', payload);
      if (!d || typeof d !== 'object' || d.error) throw new Error('bad');
      apply(d); say('✔ Đã lưu', true);
    } catch { say('Lỗi khi lưu — server offline?', false); }
  }

  async function reset() {
    if (!(await confirmDialog('Đặt lại toàn bộ cài đặt cục bộ về mặc định gốc?', { title: 'Đặt lại mặc định', danger: true, okText: 'Đặt lại' }))) return;
    try {
      const d = await api('POST', '/api/selenium/local_settings/reset');
      if (!d || typeof d !== 'object') throw new Error('bad');
      apply(d); say('✔ Đã lưu', true);
    } catch { say('Lỗi khi reset — server offline?', false); }
  }

  async function refresh() {
    if (!built) build();
    try {
      const d = await api('GET', '/api/selenium/local_settings');   // nhẹ hơn settings_debug (không gọi backend)
      apply(d && typeof d === 'object' ? d : {});
      sub.textContent = 'Settings cục bộ của máy này — không đồng bộ từ server';
    } catch { sub.textContent = 'Không lấy được dữ liệu — server có đang chạy không?'; }
    try {
      const id = await api('GET', '/api/selenium/client_identity');
      if (id && typeof id === 'object') { idIn.value = id.client_id || ''; nameIn.value = id.client_name || ''; }
    } catch { /* bỏ qua */ }
  }

  saveBar.append(status, h('div', { style: { flex: 1 } }),
    h('button', { class: 'btn danger', onclick: reset }, 'Đặt lại mặc định'),
    h('button', { class: 'btn primary', onclick: save }, '💾  Lưu'));

  const el = h('div', null,
    pageHeader('Cài đặt', sub, [h('button', { class: 'btn primary', onclick: refresh }, '↻  Làm mới')]),
    tabBar, host, saveBar);

  return { el, onShow: refresh };
}
