import { h, clear } from '../util.js';
import { api, hasBridge, bridgeCall } from '../api.js';
import { badge, pageHeader, confirmDialog, alertDialog } from '../ui.js';

export function createExtensionsPage() {
  const pathIn = h('input', { type: 'text', placeholder: 'Đường dẫn thư mục extension (chứa manifest.json)' });
  const nameIn = h('input', { type: 'text', placeholder: 'Tên gợi nhớ (để trống = tự điền từ manifest)' });
  const verifyLbl = h('div', { style: { fontSize: '11px', minHeight: '16px' } });
  const tbody = h('tbody');
  const btnBrowse = h('button', { class: 'btn icon', title: 'Duyệt thư mục' }, '📂');
  const btnRefresh = h('button', { class: 'btn icon', title: 'Làm mới' }, '↻');

  const say = (txt, ok) => { verifyLbl.style.color = ok ? 'var(--green)' : 'var(--red)'; verifyLbl.textContent = txt; };

  btnBrowse.onclick = async () => {
    const dir = await bridgeCall('pickFolder', 'Chọn thư mục extension');
    if (dir) pathIn.value = dir;
    else if (!hasBridge()) say('Không mở được hộp chọn thư mục — hãy dán đường dẫn vào ô bên cạnh', false);
  };
  btnRefresh.onclick = () => refresh();

  async function verify() {
    const path = pathIn.value.trim();
    if (!path) return say('Nhập đường dẫn trước', false);
    const r = await api('POST', '/api/selenium/extensions/verify', { ext_path: path });
    if (r.ok) { say(`✔  ${r.name || ''} v${r.version || ''}`, true); if (!nameIn.value && r.name) nameIn.value = r.name; }
    else say(`✘  ${r.error || ''}`, false);
  }
  async function add() {
    const path = pathIn.value.trim();
    if (!path) return alertDialog('Nhập đường dẫn extension', 'Lỗi');
    const r = await api('POST', '/api/selenium/extensions', { ext_path: path, ext_name: nameIn.value.trim() });
    if (r.error) return alertDialog(r.error, 'Lỗi');
    pathIn.value = ''; nameIn.value = ''; verifyLbl.textContent = '';
    refresh();
  }

  async function refresh() {
    let data;
    try { data = await api('GET', '/api/selenium/extensions'); } catch { return; }
    if (!Array.isArray(data)) return;
    clear(tbody);
    if (!data.length) tbody.append(h('tr', null, h('td', { colspan: 4, class: 'empty' }, 'Chưa có extension nào.')));
    for (const e of data) {
      const en = truthy(e.enabled);
      tbody.append(h('tr', null,
        h('td', null, e.ext_name || ''),
        h('td', { style: { color: 'var(--muted)', wordBreak: 'break-all' } }, e.ext_path || ''),
        h('td', null, badge(en ? '✔ Bật' : '✘ Tắt', en ? 'green' : 'gray')),
        h('td', null, h('div', { class: 'row', style: { gap: '4px' } },
          h('button', { class: 'btn icon sm', title: en ? 'Tắt' : 'Bật', onclick: async () => { await api('PATCH', `/api/selenium/extensions/${e.id}`, { enabled: !en }); refresh(); } }, en ? '✘' : '✔'),
          h('button', { class: 'btn icon sm', title: 'Xóa', onclick: async () => {
            if (await confirmDialog(`Xóa extension #${e.id}?`, { title: 'Xóa', danger: true, okText: 'Xóa' })) { await api('DELETE', `/api/selenium/extensions/${e.id}`); refresh(); }
          } }, '🗑')))));
    }
  }
  const truthy = (v) => !(v == null || v === 0 || v === '0' || v === false);

  const el = h('div', null,
    pageHeader('Extensions', h('div', { class: 'subtitle' }, 'Extension local tự động load vào Chrome khi worker start'), [btnRefresh]),
    h('div', { class: 'card', style: { marginBottom: '16px' } },
      h('div', { class: 'section-lbl', style: { padding: '0 0 10px' } }, 'THÊM EXTENSION'),
      h('div', { class: 'row', style: { marginBottom: '8px' } }, pathIn, btnBrowse),
      h('div', { class: 'row' }, nameIn,
        h('button', { class: 'btn', title: 'Đọc manifest.json', onclick: verify }, '🔍 Kiểm tra'),
        h('button', { class: 'btn primary', style: { width: '90px', justifyContent: 'center' }, onclick: add }, '+ Thêm')),
      h('div', { style: { marginTop: '8px' } }, verifyLbl)),
    h('div', { class: 'tbl-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Tên extension', 'Đường dẫn', 'Trạng thái', ''].map((t) => h('th', null, t)))), tbody)));

  return { el, onShow: refresh };
}
