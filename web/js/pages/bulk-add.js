// Thêm NHIỀU profile 1 lúc (port của gui/bulk_profile_dialog.py): mỗi dòng 1 profile,
// thông số cài đặt copy từ 1 profile mẫu.
import { h } from '../util.js';
import { openModal } from '../ui.js';

const IDENTITY = ['profile_name', 'display_name', 'account_email', 'account_password', 'account_totp_secret', 'project_url', 'proxy_server', 'notes'];
const ALLOWED = ['profile_name', 'display_name', 'account_email', 'account_password', 'account_totp_secret', 'project_url', 'task_mode', 'worker_mode', 'notes',
  'gemini_attach_timeout', 'gemini_response_timeout', 'max_concurrent', 'enabled', 'gemini_max_concurrent_tabs', 'gemini_tab_switch_interval',
  'proxy_server', 'run_hours', 'omni_enabled', 'veo_enabled', 'fp_aspects', 'bind_project_email', 'extension_ids', 'chatgpt_attach_timeout', 'chatgpt_response_timeout'];
const COLS = ['profile_name', 'display_name', 'account_email', 'account_password', 'type', 'proxy_server'];
const TYPES = {
  api: 'api', veo: 'api', veo3: 'api', flow: 'api', dom: 'dom', 'veo dom': 'dom',
  gemini: 'gemini', 'gemini chat': 'gemini', chatgpt: 'chatgpt', gpt: 'chatgpt',
  gemini_video: 'gemini_video', 'gemini video': 'gemini_video', gemini_image: 'gemini_image', 'gemini image': 'gemini_image',
  muse: 'muse', 'muse ai': 'muse',
};
const TYPE_EXTRA = {
  gemini: { project_url: '', task_mode: 'all' }, chatgpt: { project_url: '', task_mode: 'all' },
  gemini_video: { project_url: '', task_mode: 'video_only' }, gemini_image: { project_url: '', task_mode: 'image_only' },
  muse: { project_url: '', task_mode: 'all' },
};

// '' → null (theo profile mẫu); không nhận ra → false
function normalizeType(text) {
  const t = (text || '').trim().toLowerCase().replace(/-/g, ' ');
  if (!t) return null;
  return TYPES[t] || TYPES[t.replace(/ /g, '_')] || false;
}

function parseLines(text) {
  const out = [];
  for (const raw of (text || '').split('\n')) {
    if (!raw.trim()) continue;
    const parts = raw.includes('|') ? raw.split('|') : raw.split('\t');
    const row = {};
    COLS.forEach((k, i) => { const v = (parts[i] || '').trim(); if (v) row[k] = v; });
    if (row.profile_name) out.push(row);
  }
  return out;
}

function buildProfiles(rows, template) {
  let base = { worker_mode: 'api', task_mode: 'all', enabled: 1 };
  if (template) {
    base = {};
    for (const [k, v] of Object.entries(template)) if (ALLOWED.includes(k) && !IDENTITY.includes(k)) base[k] = v;
  }
  return rows.map((row) => {
    const { type, ...rest } = row;
    const d = { ...base, ...rest };
    const mode = normalizeType(type);
    if (mode) { d.worker_mode = mode; Object.assign(d, TYPE_EXTRA[mode] || {}); }
    return d;
  });
}

export function openBulkAdd({ profiles, onSubmit }) {
  const tpl = h('select', null,
    h('option', { value: '' }, '— Mặc định (API) —'),
    profiles.map((p, i) => h('option', { value: i }, `Sao chép thông số từ: ${p.display_name || p.profile_name}`)));
  const ta = h('textarea', { rows: 11, placeholder: 'acc01 | Acc 01 | acc01@gmail.com | matkhau | api | socks5://u:p@1.2.3.4:1080\nacc02 | | acc02@gmail.com | matkhau | gemini\nacc03' });
  const count = h('div', { class: 'hint', style: { marginTop: '8px' } });
  let m;

  const badTypes = (rows) => rows.filter((r) => r.type && normalizeType(r.type) === false).map((r) => `${r.profile_name} ("${r.type}")`);
  const update = () => {
    const rows = parseLines(ta.value);
    const names = rows.map((r) => r.profile_name.toLowerCase());
    const dup = names.length - new Set(names).size;
    let t = `${rows.length} profile`;
    if (dup) t += `  •  ⚠ ${dup} tên trùng trong danh sách`;
    const bad = badTypes(rows);
    if (bad.length) t += '  •  ⚠ Loại không nhận ra: ' + bad.slice(0, 5).join(', ');
    count.textContent = t;
  };
  ta.oninput = update;

  const submit = async () => {
    const rows = parseLines(ta.value);
    if (!rows.length) { count.textContent = 'Chưa có dòng nào hợp lệ (cần ít nhất tên profile)'; return; }
    if (badTypes(rows).length) { update(); return; }
    const seen = new Set(), uniq = [];
    for (const r of rows) { const k = r.profile_name.toLowerCase(); if (!seen.has(k)) { seen.add(k); uniq.push(r); } }
    const template = tpl.value === '' ? null : profiles[+tpl.value];
    m.close();
    await onSubmit(buildProfiles(uniq, template));
  };

  m = openModal({
    title: 'Thêm nhiều profile', icon: '➕', wide: true, sub: 'Mỗi dòng 1 profile — thông số cài đặt copy từ profile mẫu',
    body: h('div', { class: 'form-pane' },
      h('div', { class: 'row', style: { marginBottom: '10px' } }, h('span', { class: 'hint' }, 'Cấu hình mẫu:'), tpl),
      h('div', { class: 'hint', style: { marginBottom: '8px', lineHeight: 1.6 } },
        'Định dạng:  tên | hiển thị | email | mật khẩu | loại | proxy', h('br'),
        'Loại: api (VEO, mặc định), dom, gemini, chatgpt, gemini_video, gemini_image, muse — để trống thì theo profile mẫu. ' +
        'Chỉ cần tên là đủ; cột sau để trống hoặc bỏ. Phân cách bằng dấu | hoặc Tab.'),
      ta, count),
    footer: [h('button', { class: 'btn', onclick: () => m.close() }, 'Hủy'), h('button', { class: 'btn primary', onclick: submit }, 'Thêm tất cả')],
  });
  update();
}
