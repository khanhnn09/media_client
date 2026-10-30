import { h, clear } from '../util.js';
import { api } from '../api.js';
import { badge, pageHeader, confirmDialog, alertDialog } from '../ui.js';

const LOG_REFRESH_MS = 4000;

export function createLogsPage() {
  const sub = h('div', { class: 'subtitle' }, 'Chọn profile để xem logs hoạt động');
  const profSel = h('select', { style: { minWidth: '220px' } });
  const levelSel = h('select', null, ['Tất cả', 'ok', 'info', 'warn', 'error'].map((v) => h('option', null, v)));
  const limitSel = h('select', null, ['100', '200', '500'].map((v) => h('option', null, v)));
  limitSel.value = '200';
  const autoChk = h('input', { type: 'checkbox', checked: true });
  const tokBadge = h('span');
  const box = h('div', { class: 'logbox' }, 'Chưa chọn profile.');

  let profiles = [], namesSig = '', timer = null;

  const pid = () => (profSel.value ? parseInt(profSel.value, 10) : null);

  function feedProfiles(list) {
    profiles = list;
    const sig = list.map((p) => `${p.id}:${p.profile_name}`).join('|');
    if (sig === namesSig) return;
    namesSig = sig;
    const cur = profSel.value;
    clear(profSel).append(h('option', { value: '' }, '— Chọn profile —'),
      ...list.map((p) => h('option', { value: p.id }, `[${p.id}]  ${p.profile_name}`)));
    if (cur && list.some((p) => String(p.id) === cur)) profSel.value = cur;
  }

  async function reload() {
    const id = pid();
    if (!id) { tokBadge.replaceChildren(badge('—', 'gray')); return; }
    sub.textContent = `Profile [${id}]`;
    const level = levelSel.value;
    const qs = `?limit=${limitSel.value}` + (level !== 'Tất cả' ? `&level=${level}` : '');
    try {
      const [rows, tok] = await Promise.all([
        api('GET', `/api/selenium/profiles/${id}/logs${qs}`),
        api('GET', `/api/selenium/profiles/${id}/tokens`),
      ]);
      render(rows);
      renderTokens(tok);
    } catch { /* server tạm bận — lần tick sau thử lại */ }
  }

  function render(rows) {
    if (!Array.isArray(rows)) return;
    // Giữ vị trí cuộn: chỉ tự cuộn xuống đáy khi trước đó đã ở đáy (kiểu "tail -f"); đang cuộn lên đọc thì giữ nguyên.
    const atBottom = box.scrollTop >= box.scrollHeight - box.clientHeight - 20;
    const prev = box.scrollTop;
    clear(box);
    if (!rows.length) box.textContent = 'Chưa có log nào.';
    else {
      const frag = document.createDocumentFragment();
      for (const r of rows) {
        const ts = String(r.created_at || '').slice(0, 19).replace('T', ' ');
        const lvl = r.level || 'info';
        frag.append(h('span', { class: 'ts' }, `${ts}  `), h('span', { class: lvl }, `${lvl.padEnd(5)}  `), `${r.message || ''}\n`);
      }
      box.append(frag);
    }
    box.scrollTop = atBottom ? box.scrollHeight : Math.min(prev, box.scrollHeight);
  }

  function renderTokens(d) {
    if (!d || typeof d !== 'object') return;
    if (!d.running) return tokBadge.replaceChildren(badge('Worker offline', 'gray'));
    const has = !!d.hasRecaptcha;
    tokBadge.replaceChildren(badge(has ? (d.ageSeconds != null ? `✔  ${d.ageSeconds}s trước` : '✔') : '— chưa lấy', has ? 'green' : 'gray'));
  }

  profSel.onchange = reload; levelSel.onchange = reload; limitSel.onchange = reload;

  const el = h('div', null,
    pageHeader('Logs', sub),
    h('div', { class: 'toolbar' },
      h('span', { class: 'hint' }, 'Profile:'), profSel,
      h('span', { class: 'hint' }, 'Level:'), levelSel,
      h('span', { class: 'hint' }, 'Dòng:'), limitSel,
      h('label', { class: 'chk' }, autoChk, 'Auto refresh'),
      h('div', { style: { flex: 1 } }),
      h('button', { class: 'btn icon', title: 'Refresh', onclick: reload }, '↻'),
      h('button', { class: 'btn danger', onclick: async () => {
        if (!pid()) return alertDialog('Chọn profile trước', 'Info');
        if (await confirmDialog('Xoá toàn bộ logs?', { title: 'Xoá logs', danger: true, okText: 'Xoá' })) { await api('DELETE', `/api/selenium/profiles/${pid()}/logs`); reload(); }
      } }, '🗑 Xoá')),
    h('div', { class: 'row', style: { marginTop: '12px' } }, h('span', { class: 'hint' }, 'reCAPTCHA:'), tokBadge),
    box);
  tokBadge.append(badge('—', 'gray'));

  return {
    el, feedProfiles,
    onShow(arg) {
      if (arg != null) { profSel.value = String(arg); }
      reload();
      clearInterval(timer);
      timer = setInterval(() => { if (autoChk.checked && pid()) reload(); }, LOG_REFRESH_MS);
    },
    onHide() { clearInterval(timer); },
  };
}
