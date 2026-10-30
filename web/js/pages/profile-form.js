// Form thêm / sửa / sao chép 1 profile (port của gui/profile_dialog.py).
import { h, clear, parseHours, summarizeHours, truthy } from '../util.js';
import { api } from '../api.js';
import { openModal, alertDialog } from '../ui.js';

const CLONE_CLEAR = ['profile_name', 'display_name', 'account_email', 'account_password', 'account_totp_secret', 'project_url', 'proxy_server', 'notes'];

const TYPES = [
  ['veo3', 'VEO3 — Flow Automation (ảnh/video)'],
  ['gemini', 'Gemini — Chat + upload video'],
  ['chatgpt', 'ChatGPT — Chat + upload ảnh'],
  ['gemini_video', '🎬 Gemini — Tạo Video (mới, thay VEO3)'],
  ['gemini_image', '🖼️ Gemini — Tạo Ảnh (mới, thay VEO3)'],
  ['muse', '🎨 Muse AI — Tạo Ảnh + Video (muse.ai)'],
];

export async function openProfileForm({ meta, profile = null, clone = false, onSave }) {
  const isClone = !!(clone && profile);
  let p = profile ? { ...profile } : null;
  if (isClone) {
    delete p.id;
    CLONE_CLEAR.forEach((k) => { p[k] = ''; });
  }
  const P = p || {};
  const curMode = P.worker_mode || 'api';
  const curType = ['gemini', 'chatgpt', 'gemini_video', 'gemini_image', 'muse'].includes(curMode) ? curMode : 'veo3';

  let exts = [];
  try { const r = await api('GET', '/api/selenium/extensions'); exts = Array.isArray(r) ? r : []; } catch { /* không có extension */ }

  // ── Các ô nhập ──
  const text = (v, ph = '', type = 'text') => h('input', { type, placeholder: ph, value: v ?? '' });
  const f = {
    name: text(P.profile_name, 'vd: account_01'),
    display: text(P.display_name, 'vd: Máy 1 - Văn phòng A (hiển thị trong admin)'),
    email: text(P.account_email, 'vd: user@gmail.com'),
    password: text(P.account_password, 'Mật khẩu Google (để trống nếu không cần tự đăng nhập lại)', 'password'),
    totp: text(P.account_totp_secret, 'Khoá Authenticator (base32, để trống nếu không bật 2FA)', 'password'),
    url: text(P.project_url, 'vd: https://labs.google/fx/...flow'),
    proxy: text(P.proxy_server, 'vd: 1.2.3.4:8080  ·  user:pass@1.2.3.4:8080  ·  socks5://1.2.3.4:1080'),
    notes: text(P.notes, 'Ghi chú tùy ý'),
    gAttach: text(P.gemini_attach_timeout || 180, '180'),
    gResp: text(P.gemini_response_timeout || 300, '300'),
    gInterval: text(P.gemini_tab_switch_interval || 0.5, '0.5'),
    cAttach: text(P.chatgpt_attach_timeout || 60, '60'),
    cResp: text(P.chatgpt_response_timeout || 300, '300'),
  };
  f.name.disabled = !!p && !isClone;
  const num = (v, min, max) => h('input', { type: 'number', min, max, value: v });
  f.maxConc = num(parseInt(P.max_concurrent, 10) || 1, 1, 10);
  f.gTabs = num(parseInt(P.gemini_max_concurrent_tabs, 10) || 1, 1, 10);

  const sel = (opts, val) => { const s = h('select', null, opts.map(([v, l]) => h('option', { value: v }, l))); s.value = val; return s; };
  f.type = sel(TYPES, curType);
  f.taskMode = sel([['all', 'all'], ['image_only', 'image_only — Chỉ Ảnh'], ['video_only', 'video_only — Chỉ Video']], P.task_mode || 'all');
  f.workerMode = sel([['api', 'api'], ['dom', 'dom']], ['api', 'dom'].includes(curMode) ? curMode : 'api');
  f.workerMode.title = 'api = gọi API trực tiếp (nhanh, ổn định)\ndom = Selenium click giao diện Flow (không cần token)';

  const chk = (label, on, tip = '') => { const i = h('input', { type: 'checkbox', checked: !!on }); return [i, h('label', { class: 'chk', title: tip }, i, label)]; };
  [f.enabled, f.enabledW] = chk('Bật — cho phép chạy', p ? truthy(P.enabled ?? 1) : true, 'Cho phép auto-scale tự mở VÀ bấm Start thủ công.\nTắt = tạm ngưng profile này mà không cần xoá.');
  [f.veo, f.veoW] = chk('VEO', P.veo_enabled == null ? true : truthy(P.veo_enabled), 'Tab Flow nhận task ảnh/video theo Task mode.\nBỏ tick = không chạy VEO, chỉ chạy Omni (nếu tick Omni).');
  [f.omni, f.omniW] = chk('Omni', truthy(P.omni_enabled), 'Mở thêm 1 tab Google Vids chạy song song — Omni CHỈ nhận task video.');
  [f.bind, f.bindW] = chk('Chỉ chạy task theo project + email', truthy(P.bind_project_email),
    'Profile chỉ nhận task của project Nano Banana gán ĐÚNG email của nó, vào đúng link Flow đã gán và dùng lại id ảnh đã lưu làm tham chiếu.');
  const enginesW = h('div', { class: 'row', style: { gap: '16px' } }, f.veoW, f.omniW);

  // Khung giờ chạy
  const savedHours = new Set(parseHours(P.run_hours));
  const hourBoxes = Array.from({ length: 24 }, (_, hr) => {
    const i = h('input', { type: 'checkbox', checked: savedHours.has(hr), onchange: updateHint });
    return [i, h('label', { title: `Nhận task trong khoảng ${hr}:00 – ${hr}:59` }, i, String(hr))];
  });
  const hoursHint = h('div', { class: 'hint' });
  const setHours = (arr) => { const s = new Set(arr); hourBoxes.forEach(([i], hr) => { i.checked = s.has(hr); }); updateHint(); };
  const checkedHours = () => hourBoxes.map(([i], hr) => (i.checked ? hr : -1)).filter((x) => x >= 0);
  function updateHint() {
    const span = summarizeHours(checkedHours());
    hoursHint.textContent = span ? `Chỉ nhận task: ${span} (giờ máy này)` : 'Không chọn giờ nào (hoặc chọn đủ 24h) = chạy liên tục';
  }
  const runHoursW = h('div', null,
    h('div', { class: 'hours' }, hourBoxes.map(([, l]) => l)),
    h('div', { class: 'row', style: { margin: '8px 0 4px' } },
      h('button', { class: 'btn sm', onclick: () => setHours([...Array(24).keys()]) }, 'Chọn hết'),
      h('button', { class: 'btn sm', onclick: () => setHours([]) }, 'Bỏ hết'),
      h('button', { class: 'btn sm', title: 'Chọn nhanh giờ hành chính 8:00 – 17:00', onclick: () => setHours([8, 9, 10, 11, 12, 13, 14, 15, 16]) }, '8-17h')),
    hoursHint);
  updateHint();

  // Fingerprint
  const fpAspects = meta.fp_aspects || {};
  const savedFp = (P.fp_aspects || '').split(',').map((s) => s.trim()).filter(Boolean);
  const fpOn = savedFp.length ? new Set(savedFp) : new Set(Object.keys(fpAspects));
  const fpBoxes = Object.entries(fpAspects).map(([k, label]) => { const i = h('input', { type: 'checkbox', checked: fpOn.has(k) }); return [k, i, h('label', { class: 'chk' }, i, label)]; });
  const fpW = h('div', { class: 'checks' }, fpBoxes.map(([, , l]) => l));

  // Extension
  const savedExt = P.extension_ids;
  const savedExtSet = savedExt == null ? null : new Set(String(savedExt).split(',').map((x) => x.trim()).filter(Boolean));
  let extTouched = false;
  const extAuto = savedExtSet === null;
  const extBoxes = exts.map((e) => {
    const on = truthy(e.enabled ?? 1);
    const i = h('input', { type: 'checkbox', disabled: !on, checked: savedExtSet ? on && savedExtSet.has(String(e.id)) : false, onchange: () => { extTouched = true; } });
    return [String(e.id), i, h('label', { class: 'chk', title: e.ext_path || '' }, i, (e.ext_name || `Extension ${e.id}`) + (on ? '' : ' (đang tắt ở trang Extensions)'))];
  });
  const extW = h('div', { class: 'checks' }, extBoxes.length ? extBoxes.map(([, , l]) => l) : h('span', { class: 'hint' }, 'Chưa có extension nào — thêm ở trang Extensions.'));
  function applyDefaultExts() {
    if (!extAuto || extTouched) return;
    const veo = f.type.value === 'veo3';
    extBoxes.forEach(([, i]) => { i.checked = veo && !i.disabled; });
  }

  // ── Bố cục tab ──
  const row = (label, input, hint) => {
    const r = h('div', { class: 'form-row' }, h('div', { class: 'lb' }, label), h('div', null, input, hint && h('div', { class: 'hint' }, hint)));
    return r;
  };
  // Mỗi hàng gắn nhóm: nhóm nào chứa Loại hiện tại thì hiện.
  const groups = {
    veo3: ['url', 'taskMode', 'workerMode', 'maxConc', 'engines', 'bind'],
    gemini: ['gAttach', 'gResp', 'gTabs', 'gInterval'],
    chatgpt: ['cAttach', 'cResp'],
    gemini_video: ['gAttach', 'gResp', 'maxConc'],
    gemini_image: ['gAttach', 'gResp', 'maxConc'],
    muse: [],
  };
  const taskRows = {
    url: row('Flow URL', f.url), taskMode: row('Task mode', f.taskMode), workerMode: row('Worker mode', f.workerMode),
    maxConc: row('Task nhận đồng thời', f.maxConc, 'Số task nhận mỗi lần heartbeat (1-10), gửi liên tiếp trong cùng 1 tab.'),
    engines: row('Engine chạy', enginesW), bind: row('Theo project+email', f.bindW),
    gAttach: row('Attach timeout', f.gAttach, 'Giây chờ file đính kèm vào Gemini (mặc định 180). Video lớn cần lâu hơn.'),
    gResp: row('Response timeout', f.gResp, 'Giây chờ Gemini phản hồi xong (mặc định 300). "Tạo Video" tự áp sàn 600s.'),
    gTabs: row('Số tab đồng thời', f.gTabs, 'Nhận 1 lô tối đa N task/lần, chờ hết cả lô mới nhận lô tiếp. 1 = tuần tự, 1 tab.'),
    gInterval: row('Giãn cách chuyển tab', f.gInterval, 'Số giây chờ giữa mỗi lần chuyển tab lúc round-robin (mặc định 0.5).'),
    cAttach: row('Attach timeout', f.cAttach, 'Giây chờ ảnh đính kèm vào ChatGPT (mặc định 60).'),
    cResp: row('Response timeout', f.cResp, 'Giây chờ ChatGPT phản hồi xong (mặc định 300).'),
  };
  const noOpt = h('div', { class: 'hint', style: { padding: '14px 0' } }, 'Loại này không có tuỳ chọn riêng — cỡ lô/khoảng cách/tiền tố ở Cài đặt → Muse AI.');

  const hoursRow = row('Giờ chạy', runHoursW);
  const fpRow = row('Đổi fingerprint', fpW);
  const panes = [
    ['Tài khoản', h('div', { class: 'form-pane' },
      row('Tên *', f.name), row('Hiển thị', f.display), row('Email', f.email), row('Mật khẩu', f.password),
      row('Khoá 2FA', f.totp), row('Trạng thái', f.enabledW), row('Loại *', f.type))],
    ['Chạy task', h('div', { class: 'form-pane' }, Object.values(taskRows), noOpt)],
    ['Giờ chạy', h('div', { class: 'form-pane' }, hoursRow, fpRow)],
    ['Proxy & ghi chú', h('div', { class: 'form-pane' },
      row('Proxy', f.proxy, 'Proxy riêng cho Chrome của profile này (để trống = không dùng). Định dạng: 1.2.3.4:8080 · user:pass@1.2.3.4:8080 · socks5://1.2.3.4:1080. ' +
        'Chỉ áp dụng cho trình duyệt; đổi proxy có hiệu lực ở lần MỞ Chrome kế tiếp (Stop rồi Start lại).'),
      row('Extension', extW), row('Ghi chú', f.notes))],
  ];

  let active = 0, modal;
  const tabBar = h('div', { class: 'tabs' });
  const paneHost = h('div');
  function showTab(i) {
    active = i;
    clear(paneHost).append(panes[i][1]);
    [...tabBar.children].forEach((b, j) => b.classList.toggle('active', j === i));
  }
  panes.forEach(([t], i) => tabBar.append(h('button', { onclick: () => showTab(i) }, t)));

  function onTypeChange() {
    const cur = f.type.value;
    const show = new Set(groups[cur] || []);
    for (const [k, r] of Object.entries(taskRows)) r.style.display = show.has(k) ? '' : 'none';
    noOpt.style.display = show.size ? 'none' : '';
    const isVeo = cur === 'veo3';
    tabBar.children[2].style.display = isVeo ? '' : 'none';
    if (!isVeo && active === 2) showTab(1);
    applyDefaultExts();
  }
  f.type.onchange = onTypeChange;

  // ── Lưu ──
  const errBox = h('div', { style: { color: 'var(--red)', fontSize: '12px', marginRight: 'auto' } });
  const digits = (el, label) => { if (el.value.trim() && !/^\d+$/.test(el.value.trim())) { errBox.textContent = `${label} phải là số (giây).`; return false; } return true; };

  function validate() {
    errBox.textContent = '';
    if (!f.name.value.trim()) { showTab(0); errBox.textContent = 'Tên profile là bắt buộc.'; return false; }
    const t = f.type.value;
    if (t === 'gemini') {
      if (!digits(f.gAttach, 'Attach timeout') || !digits(f.gResp, 'Response timeout')) { showTab(1); return false; }
      if (f.gInterval.value.trim() && isNaN(parseFloat(f.gInterval.value))) { showTab(1); errBox.textContent = 'Giãn cách chuyển tab phải là số (giây).'; return false; }
    } else if (t === 'veo3' && !f.veo.checked && !f.omni.checked) {
      showTab(1); errBox.textContent = 'Phải tick ít nhất 1 trong 2: VEO hoặc Omni.'; return false;
    } else if (t === 'chatgpt') {
      if (!digits(f.cAttach, 'Attach timeout') || !digits(f.cResp, 'Response timeout')) { showTab(1); return false; }
    } else if (t === 'gemini_video' || t === 'gemini_image') {
      if (!digits(f.gAttach, 'Attach timeout') || !digits(f.gResp, 'Response timeout')) { showTab(1); return false; }
    }
    return true;
  }

  function collect() {
    const t = f.type.value;
    const i = (el, d) => parseInt(el.value.trim(), 10) || d;
    const data = {
      profile_name: f.name.value.trim(), display_name: f.display.value.trim(), account_email: f.email.value.trim(),
      account_password: f.password.value,                              // KHÔNG trim — mật khẩu thật có thể chứa khoảng trắng
      account_totp_secret: f.totp.value.replace(/ /g, '').trim(), notes: f.notes.value.trim(),
      extension_ids: extBoxes.filter(([, c]) => c.checked).map(([id]) => id).join(','),
      enabled: f.enabled.checked, proxy_server: f.proxy.value.trim(),
    };
    if (t === 'gemini') {
      Object.assign(data, { worker_mode: 'gemini', project_url: '', task_mode: 'all',
        gemini_attach_timeout: i(f.gAttach, 180), gemini_response_timeout: i(f.gResp, 300),
        gemini_max_concurrent_tabs: i(f.gTabs, 1), gemini_tab_switch_interval: parseFloat(f.gInterval.value.trim()) || 0.5 });
    } else if (t === 'chatgpt') {
      Object.assign(data, { worker_mode: 'chatgpt', project_url: '', task_mode: 'all',
        chatgpt_attach_timeout: i(f.cAttach, 60), chatgpt_response_timeout: i(f.cResp, 300) });
    } else if (t === 'gemini_video' || t === 'gemini_image') {
      Object.assign(data, { worker_mode: t, project_url: '', task_mode: t === 'gemini_video' ? 'video_only' : 'image_only',
        gemini_attach_timeout: i(f.gAttach, 180), gemini_response_timeout: i(f.gResp, 300), max_concurrent: i(f.maxConc, 1) });
    } else if (t === 'muse') {
      Object.assign(data, { worker_mode: 'muse', project_url: '', task_mode: 'all', max_concurrent: 1 });
    } else {
      const sel = fpBoxes.filter(([, c]) => c.checked).map(([k]) => k);
      Object.assign(data, { worker_mode: f.workerMode.value, project_url: f.url.value.trim(), task_mode: f.taskMode.value,
        max_concurrent: i(f.maxConc, 1), run_hours: checkedHours().join(','),
        omni_enabled: f.omni.checked ? 1 : 0, veo_enabled: f.veo.checked ? 1 : 0, bind_project_email: f.bind.checked ? 1 : 0,
        fp_aspects: sel.length === fpBoxes.length ? '' : sel.join(',') });
    }
    if (t !== 'veo3') Object.assign(data, { run_hours: '', omni_enabled: 0, veo_enabled: 1, bind_project_email: 0 });
    return data;
  }

  const btnSave = h('button', { class: 'btn primary', style: { minWidth: '100px' } }, 'Lưu');
  btnSave.onclick = async () => {
    if (!validate()) return;
    btnSave.disabled = true;
    try { if (await onSave(collect())) modal.close(); }
    catch (e) { errBox.textContent = `Lỗi: ${e.message || e}`; }
    finally { btnSave.disabled = false; }
  };

  const title = !p ? 'Thêm profile' : isClone ? 'Sao chép profile' : 'Sửa profile';
  modal = openModal({
    title, wide: false, icon: !p ? '➕' : isClone ? '📋' : '✏',
    sub: !p || isClone ? 'Mỗi profile là một tài khoản Google riêng biệt' : `ID: ${P.id}  •  Thư mục: ${P.profile_dir || '?'}`,
    body: [tabBar, paneHost],
    footer: [errBox, h('button', { class: 'btn', onclick: () => modal.close() }, 'Hủy'), btnSave],
  });
  modal.el.style.width = '640px';
  modal.el.style.height = 'min(720px, calc(100vh - 40px))';
  onTypeChange();
  applyDefaultExts();
  showTab(0);
  f.name.focus();
}
