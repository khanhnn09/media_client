import { h, clear, summarizeHours, fmtClock, truthy } from '../util.js';
import { api } from '../api.js';
import { badge, statCard, pageHeader, showMenu, confirmDialog, alertDialog, openModal, toast } from '../ui.js';
import { openProfileForm } from './profile-form.js';
import { openBulkAdd } from './bulk-add.js';

// Nhãn/màu "Loại" profile (worker_mode) — khớp gui/pages/profiles_page.py cũ.
const ENGINE_BADGE = {
  gemini: ['✨ Gemini', 'purple'], chatgpt: ['🤖 ChatGPT', 'green'], gemini_video: ['🎬 Gemini Video', 'purple'],
  gemini_image: ['🖼️ Gemini Ảnh', 'purple'], muse: ['🎨 Muse AI', 'purple'],
};
const TASK_MODE_BADGE = {
  all: ['🖼️🎬 VEO3', 'blue'], image_only: ['🖼️ VEO3 · Ảnh', 'purple'], video_only: ['🎬 VEO3 · Video', 'orange'],
};
const NO_PROJECT_MODES = ['gemini', 'chatgpt', 'gemini_video', 'gemini_image', 'muse'];

export function createProfilesPage(ctx) {
  const state = { profiles: [], checked: new Set(), filter: '', lastSig: null, proxyDisp: {}, proxyAsked: new Set() };

  // ── Header ──
  const sub = h('div', { class: 'subtitle' }, 'Quản lý tài khoản Chrome cho Flow worker');
  const btnAuto = h('button', { class: 'btn toggled', title: 'Dừng/bật tự cập nhật trạng thái dashboard. ↻ vẫn làm mới tay.' }, '⏸ Dừng cập nhật');
  const spin = h('input', { type: 'number', min: 1, max: 300, value: 5, style: { width: '72px' }, title: 'Chu kỳ tự cập nhật (giây)' });
  const btnRefresh = h('button', { class: 'btn icon', title: 'Làm mới' }, '↻');
  const btnMany = h('button', { class: 'btn', title: 'Thêm nhiều profile 1 lúc (mỗi dòng 1 profile)' }, '+ Thêm nhiều');
  const btnAdd = h('button', { class: 'btn primary' }, '+ Thêm profile');
  let auto = true;
  btnAuto.onclick = () => {
    auto = !auto;
    ctx.setAuto(auto);
    btnAuto.classList.toggle('toggled', auto);
    btnAuto.textContent = auto ? '⏸ Dừng cập nhật' : '▶ Bật cập nhật';
  };
  spin.onchange = () => ctx.setIntervalMs((parseInt(spin.value, 10) || 5) * 1000);
  btnRefresh.onclick = () => ctx.refreshNow();
  btnAdd.onclick = () => add();
  btnMany.onclick = () => addMany();

  // ── Stats ──
  const S = {
    total: statCard('👤', 'Tổng profile', 'accent'),
    running: statCard('⚡', 'Đang chạy', 'green'),
    free: statCard('✅', 'Rảnh (sẵn sàng nhận task)', 'green'),
    busy: statCard('🎬', 'Đang xử lý task', 'accent'),
    sleeping: statCard('😴', 'Đang ngủ', 'orange'),
    login: statCard('🌐', 'Đang login', 'yellow'),
    errors: statCard('⚠', 'Lỗi (phiên chạy)', 'red'),
  };

  // ── Search / chọn ──
  const search = h('input', { type: 'text', placeholder: '🔍  Tìm theo tên, tên hiển thị hoặc email…', style: { maxWidth: '360px' } });
  const chkAll = h('input', { type: 'checkbox' });
  const selLbl = h('span', { class: 'hint' });
  const btnBulk = h('button', { class: 'btn', disabled: true }, '⚙ Hành động cho profile đã chọn ▾');
  search.oninput = () => { state.filter = search.value.trim().toLowerCase(); render(true); };
  chkAll.onchange = () => {
    const ids = visible().map((p) => p.id);
    ids.forEach((id) => (chkAll.checked ? state.checked.add(id) : state.checked.delete(id)));
    render(true);
  };
  btnBulk.onclick = () => showBulkMenu();

  const tbody = h('tbody');
  const emptyBox = h('div', { class: 'empty', style: { display: 'none' } });
  const table = h('div', { class: 'tbl-wrap' },
    h('table', null,
      h('thead', null, h('tr', null, ['', 'Tên / Email', 'Mode', 'Trạng thái', 'Task hiện tại', 'Port', 'Auto', 'Hành động']
        .map((t, i) => h('th', { style: i === 0 ? { width: '40px' } : null }, t)))),
      tbody), emptyBox);

  const el = h('div', null,
    pageHeader('Profiles', sub, [btnAuto, spin, btnRefresh, btnMany, btnAdd]),
    h('div', { class: 'stats' }, Object.values(S)),
    h('div', { class: 'row', style: { marginBottom: '14px', flexWrap: 'wrap' } },
      search, h('label', { class: 'chk', style: { marginLeft: '8px' } }, chkAll, 'Chọn tất cả'), selLbl, btnBulk),
    table);

  // ── Dữ liệu ──
  function visible() {
    if (!state.filter) return state.profiles;
    return state.profiles.filter((p) =>
      [p.profile_name, p.display_name, p.account_email].map((s) => s || '').join(' ').toLowerCase().includes(state.filter));
  }

  function onStatus(data) {
    if (!data || typeof data !== 'object') return;
    if (data.error && !(data.profiles || []).length) return;
    state.profiles = data.profiles || [];
    const P = state.profiles;
    const n = P.length;
    const run = P.filter((p) => p.worker_running).length;
    const free = P.filter((p) => p.worker_running && !p.liveTaskId).length;
    S.total.set(n); S.running.set(run); S.free.set(free);
    S.busy.set(P.filter((p) => p.liveTaskId).length);
    S.sleeping.set(P.filter((p) => p.status === 'sleeping').length);
    S.login.set(P.filter((p) => p.login_open).length);
    S.errors.set(P.reduce((a, p) => a + (parseInt(p.taskErrorCount, 10) || 0), 0));
    sub.textContent = `${n} profile • ${run} worker đang chạy • ${free} rảnh`;
    ensureProxyDisplay();
    render(false);
  }

  async function ensureProxyDisplay() {
    const need = [...new Set(state.profiles.map((p) => (p.proxy_server || '').trim()).filter((r) => r && !state.proxyAsked.has(r)))];
    if (!need.length) return;
    need.forEach((r) => state.proxyAsked.add(r));
    try {
      const r = await api('POST', '/api/selenium/ui_format', { proxies: need });
      Object.assign(state.proxyDisp, r.proxies || {});
      render(true);
    } catch { need.forEach((r) => state.proxyAsked.delete(r)); }
  }

  // ── Dựng bảng ──
  function render(force) {
    const ticking = state.profiles.some((p) => p.liveTaskId || p.status === 'sleeping');
    const sig = JSON.stringify(state.profiles) + `|${ticking ? Math.floor(Date.now() / 10000) : 0}|${state.filter}|${[...state.checked].join(',')}|${Object.keys(state.proxyDisp).length}`;
    if (!force && sig === state.lastSig) return;
    state.lastSig = sig;

    const rows = visible();
    clear(tbody);
    emptyBox.style.display = 'none';
    table.querySelector('table').style.display = '';
    if (!state.profiles.length) {
      emptyBox.textContent = 'Chưa có profile nào — bấm "+ Thêm profile" để bắt đầu.';
      emptyBox.style.display = ''; table.querySelector('table').style.display = 'none';
    } else if (!rows.length) {
      emptyBox.textContent = `Không tìm thấy profile khớp "${state.filter}".`;
      emptyBox.style.display = ''; table.querySelector('table').style.display = 'none';
    }
    rows.forEach((p) => tbody.append(row(p)));
    updateBulkBar();
  }

  function updateBulkBar() {
    const known = new Set(state.profiles.map((p) => p.id));
    state.checked = new Set([...state.checked].filter((i) => known.has(i)));
    const n = state.checked.size;
    selLbl.textContent = n ? `${n} profile đã chọn` : '';
    btnBulk.disabled = n === 0;
    const vis = visible();
    chkAll.checked = vis.length > 0 && vis.every((p) => state.checked.has(p.id));
  }

  function row(p) {
    const running = !!p.worker_running, loginOpen = !!p.login_open;
    const cb = h('input', { type: 'checkbox', checked: state.checked.has(p.id) });
    cb.onchange = () => { cb.checked ? state.checked.add(p.id) : state.checked.delete(p.id); state.lastSig = null; updateBulkBar(); };

    const color = running ? 'var(--green)' : loginOpen ? 'var(--accent)' : 'var(--muted)';
    const display = (p.display_name || '').trim();
    const proxyRaw = (p.proxy_server || '').trim();
    const nameCell = h('div', { class: 'pcell' },
      h('div', { class: 'avatar', style: { background: `linear-gradient(135deg, ${color}, ${color}aa)` } }, (p.profile_name || 'P')[0].toUpperCase()),
      h('div', { class: 'stack' },
        h('div', { class: 'nm' }, display || p.profile_name || ''),
        h('div', { class: 'sub' }, display ? p.profile_name : (p.account_email || '—')),
        proxyRaw && h('div', { class: 'px', title: 'Chrome của profile này đi qua proxy' }, `🌐 ${state.proxyDisp[proxyRaw] || '…'}`)));

    return h('tr', null,
      h('td', null, cb), h('td', null, nameCell), h('td', null, modeCell(p)), h('td', null, statusCell(p)),
      h('td', { style: { textAlign: 'center' } }, taskCell(p)),
      h('td', { style: { textAlign: 'center', color: running || loginOpen ? 'var(--green)' : 'var(--border)' } }, String(p.debug_port ?? '—')),
      h('td', null, autoCell(p)), h('td', null, actionCell(p, running, loginOpen)));
  }

  function modeCell(p) {
    const wm = p.worker_mode || 'api';
    const conc = p.max_concurrent || 1;
    let b, subTxt;
    if (ENGINE_BADGE[wm]) {
      b = badge(...ENGINE_BADGE[wm]);
      const parts = [];
      if (wm === 'gemini') { parts.push('Chat + Video'); if ((p.gemini_max_concurrent_tabs || 1) > 1) parts.push(`${p.gemini_max_concurrent_tabs} tab`); }
      else if (wm === 'chatgpt') parts.push('Chat + Ảnh');
      else if (wm === 'gemini_video') { parts.push('Tạo video qua chat'); if (conc > 1) parts.push(`x${conc}`); }
      else if (wm === 'muse') parts.push('muse.ai · ảnh + video');
      else { parts.push('Tạo ảnh qua chat'); if (conc > 1) parts.push(`x${conc}`); }
      subTxt = parts.join(' · ');
    } else {
      b = badge(...(TASK_MODE_BADGE[p.task_mode || 'all'] || TASK_MODE_BADGE.all));
      const omniOn = parseInt(p.omni_enabled || 0, 10);
      const veoOn = !['0', 'False'].includes(String(p.veo_enabled ?? 1)) || !omniOn;
      let parts;
      if (veoOn) { parts = [`${wm.toUpperCase()} mode`, `x${conc}`]; if (omniOn) parts.push('+ Omni'); }
      else { b = badge('🎬 Chỉ Omni', 'purple'); parts = ['Omni (Google Vids) · chỉ task video']; }
      if (parseInt(p.bind_project_email || 0, 10)) parts.push('🔗 Theo project+email');
      const span = summarizeHours(p.run_hours);
      if (span) parts.push(`⏰ ${span}`);
      subTxt = parts.join(' · ');
    }
    return h('div', { class: 'stack' }, h('div', null, b), h('div', { class: 'mini' }, subTxt));
  }

  function statusCell(p) {
    const status = p.status || 'idle';
    let st;
    if (p.worker_running) st = badge('⚡  Running', 'green');
    else if (p.login_open) st = badge('🌐  Login open', 'yellow');
    else if (p.worker_waiting || status === 'waiting') st = badge('⏳  Waiting', 'blue');
    else if (status === 'sleeping') {
      const remain = p.sleepUntil ? Math.floor(p.sleepUntil - Date.now() / 1000) : null;
      st = remain && remain > 0
        ? badge(`😴  Ngủ ${Math.floor(remain / 60)}:${String(remain % 60).padStart(2, '0')}`, 'orange', `Thức dậy lúc ${fmtClock(p.sleepUntil)}`)
        : badge('😴  Sleeping', 'orange');
    } else if (!truthy(p.enabled ?? 1)) {
      st = badge('🚫  Đã tắt', 'red', 'Profile đang TẮT — không tự mở (auto-scale) và không bấm Start thủ công được. Sửa profile để bật lại.');
    } else st = badge(status, 'gray');

    const errTotal = parseInt(p.taskErrorCount, 10) || 0, consec = parseInt(p.consecutiveErrors, 10) || 0;
    const today = `Hôm nay: ✅ ${parseInt(p.tasks_done_today, 10) || 0}  ❌ ${parseInt(p.tasks_error_today, 10) || 0}`;
    let err;
    if (errTotal > 0) {
      const last = (p.lastErrorMsg || '').slice(0, 300);
      const tip = p.lastErrorAt ? `${fmtClock(p.lastErrorAt)} — ${last}` : last;
      err = badge(`${errTotal}${consec ? ` (${consec} liên tiếp)` : ''}`, consec > 0 ? 'red' : 'orange', tip ? `${tip}\n${today}` : today);
    } else err = badge('0', 'gray', today);
    return h('div', { class: 'stack' }, h('div', null, st), h('div', null, err));
  }

  function taskCell(p) {
    const id = p.liveTaskId || p.current_task_id;
    const ups = Array.isArray(p.upscaleJobs) ? p.upscaleJobs : [];
    let main;
    if (!id) main = h('span', { style: { color: 'var(--muted)' } }, ups.length ? '' : '—');
    else {
      const parts = [`#${id}`];
      if (p.liveTaskMode) parts.push(p.liveTaskMode);
      if (p.liveTaskStartedAt) parts.push(`${Math.floor(Date.now() / 1000 - p.liveTaskStartedAt)}s`);
      main = h('span', { style: { color: 'var(--yellow)' }, title: p.liveTaskPrompt || '' }, parts.join(' · '));
    }
    if (!ups.length) return main;
    // Job upscale 1080p đang chạy/chờ ở máy này — hiện rõ task nào, bao lâu.
    const running = ups.filter((j) => j.state === 'running');
    const shown = ups.slice(0, 3).map((j) => `#${j.taskId}${j.state === 'queued' ? ' (chờ)' : ''} ${j.elapsed || 0}s`).join(', ');
    const more = ups.length > 3 ? ` +${ups.length - 3}` : '';
    const tip = ups.map((j) => `#${j.taskId} ${j.nameId || ''} — ${j.state === 'running' ? 'đang upscale' : 'chờ gửi'} ${j.elapsed || 0}s`).join('\n');
    return h('div', { class: 'stack' }, main,
      h('span', { style: { color: 'var(--accent, #58a6ff)', fontSize: '11px' }, title: tip },
        `⬆ Upscale ${running.length}/${ups.length}: ${shown}${more}`));
  }

  function autoCell(p) {
    const on = truthy(p.enabled ?? 1);
    return h('button', {
      class: 'btn sm', style: { color: on ? 'var(--green)' : 'var(--muted)' },
      title: on ? 'Đang BẬT — auto-scale được phép mở profile này.\nBấm để TẮT (không nhận task mới).'
        : 'Đang TẮT — auto-scale bỏ qua, Start thủ công cũng bị chặn.\nBấm để BẬT.',
      onclick: async () => { await api('PATCH', `/api/selenium/profiles/${p.id}`, { enabled: on ? 0 : 1 }); ctx.refreshNow(); },
    }, on ? '🟢 Bật' : '⚪ Tắt');
  }

  function actionCell(p, running, loginOpen) {
    const wm = p.worker_mode || 'api';
    const enabled = truthy(p.enabled ?? 1);
    const canStart = enabled && (NO_PROJECT_MODES.includes(wm) || !!p.project_url);
    const main = running
      ? h('button', { class: 'btn icon sm', title: 'Stop worker', onclick: () => act(p.id, 'stop') }, '⏹')
      : h('button', {
        class: 'btn icon sm', disabled: !canStart, onclick: () => act(p.id, 'start'),
        title: !enabled ? 'Profile đang TẮT — sửa profile để bật lại trước khi Start' : canStart ? 'Start worker' : 'Set Flow URL trước',
      }, '▶');
    const more = h('button', { class: 'btn icon sm', title: 'Hành động khác' }, '⋮');
    more.onclick = () => showMenu(more, profileMenu(p, running, loginOpen));
    return h('div', { class: 'row', style: { gap: '4px' } }, main, more);
  }

  function profileMenu(p, running, loginOpen) {
    const id = p.id;
    const asleep = p.status === 'sleeping' || !!p.sleepUntil;
    return [
      loginOpen
        ? { label: '✕  Đóng login browser', onClick: () => act(id, 'close') }
        : { label: '🌐  Mở login browser', disabled: running, title: running ? 'Stop worker trước' : '', onClick: () => act(id, 'open') },
      { label: '📜  Xem logs', onClick: () => ctx.go(2, id) },
      { label: '✏  Sửa profile', onClick: () => edit(p) },
      { label: '📋  Sao chép profile (chỉ thông số cài đặt)', onClick: () => clone(p) },
      { label: '⏰  Xóa thời gian ngủ (đánh thức ngay)', disabled: !asleep, title: asleep ? '' : 'Profile không ở trạng thái ngủ', onClick: () => act(id, 'clear_sleep') },
      '-',
      { label: '🧹  Làm mới profile (xóa sạch — về trắng)', disabled: running || loginOpen,
        title: running || loginOpen ? 'Đóng worker/login browser trước khi làm mới profile' : '', onClick: () => wipe(p) },
      '-',
      { label: '🗑  Xóa profile', onClick: () => del(p) },
    ];
  }

  // ── Hành động ──
  const pname = (p) => p.display_name || p.profile_name || `#${p.id}`;

  // Trả true nếu response là lỗi (đã báo người dùng).
  async function chk(r) {
    if (r && r.error) {
      if (r.error === 'outside_run_hours') {
        await alertDialog(`Profile chỉ nhận task trong khung giờ: ${r.run_hours || '?'} (giờ máy này).\nTới giờ, auto-scale sẽ tự mở profile khi có task.`, 'Ngoài khung giờ chạy');
      } else await alertDialog(r.error, 'Lỗi');
      return true;
    }
    return false;
  }

  async function act(id, what) {
    try { await chk(await api('POST', `/api/selenium/profiles/${id}/${what}`)); }
    catch (e) { toast(`Lỗi: ${e.message || e}`, 'err'); }
    ctx.refreshNow();
  }

  function add() {
    openProfileForm({ meta: ctx.store.meta, onSave: async (data) => {
      const r = await api('POST', '/api/selenium/profiles', data);
      if (await chk(r)) return false;
      ctx.refreshNow(); return true;
    } });
  }
  function edit(p) {
    openProfileForm({ meta: ctx.store.meta, profile: p, onSave: async (data) => {
      const r = await api('PATCH', `/api/selenium/profiles/${p.id}`, data);
      if (await chk(r)) return false;
      ctx.refreshNow(); return true;
    } });
  }
  function clone(p) {
    openProfileForm({ meta: ctx.store.meta, profile: p, clone: true, onSave: async (data) => {
      const r = await api('POST', '/api/selenium/profiles', data);
      if (await chk(r)) return false;
      ctx.refreshNow(); return true;
    } });
  }
  async function del(p) {
    if (!(await confirmDialog(`Xóa profile "${p.profile_name}"?\nDữ liệu Chrome sẽ giữ lại.`, { title: 'Xóa profile', danger: true, okText: 'Xóa' }))) return;
    await api('DELETE', `/api/selenium/profiles/${p.id}`);
    ctx.refreshNow();
  }
  async function wipe(p) {
    const ok = await confirmDialog(
      `Xóa SẠCH TOÀN BỘ dữ liệu Chrome của profile "${pname(p)}" — coi như 1 profile hoàn toàn mới?\n\n` +
      '⚠️ Mất: đăng nhập Google, mật khẩu đã lưu, bookmark, cache, cookie, lịch sử, mọi cài đặt Chrome khác của profile này. ' +
      'Cần "Mở login browser" đăng nhập lại từ đầu trước khi Start worker được. KHÔNG THỂ HOÀN TÁC.',
      { title: 'Làm mới profile (xóa sạch — về trắng)', danger: true, okText: 'Xóa sạch' });
    if (!ok) return;
    await chk(await api('POST', `/api/selenium/profiles/${p.id}/clear_data`));
    ctx.refreshNow();
  }

  async function addMany() {
    openBulkAdd({ profiles: state.profiles, onSubmit: async (items) => {
      const existing = new Set(state.profiles.map((x) => (x.profile_name || '').toLowerCase()));
      const todo = items.filter((d) => !existing.has(d.profile_name.toLowerCase()));
      const skipped = items.filter((d) => existing.has(d.profile_name.toLowerCase())).map((d) => d.profile_name);
      let ok = 0; const errs = [];
      for (const d of todo) {
        try { const r = await api('POST', '/api/selenium/profiles', d); r && r.error ? errs.push(`${d.profile_name}: ${r.error}`) : ok++; }
        catch (e) { errs.push(`${d.profile_name}: ${e.message || e}`); }
      }
      ctx.refreshNow();
      let msg = `Đã thêm ${ok}/${todo.length} profile.`;
      if (skipped.length) msg += `\nBỏ qua (đã tồn tại): ${skipped.join(', ')}`;
      if (errs.length) msg += `\nLỗi:\n${errs.slice(0, 8).join('\n')}`;
      await alertDialog(msg, 'Thêm nhiều profile');
    } });
  }

  // ── Hành động hàng loạt ──
  function showBulkMenu() {
    const n = state.checked.size;
    showMenu(btnBulk, [
      { label: `🌐  Mở login browser (${n})`, onClick: () => bulk('open') },
      { label: `✖  Đóng login browser (${n})`, onClick: () => bulk('close') },
      { label: `😴  Xóa thời gian ngủ (${n})`, onClick: () => bulk('clear_sleep') },
      { label: `🌐  Sửa proxy (${n})`, onClick: () => bulk('proxy') },
      { label: `🟢  Bật auto (${n})`, onClick: () => bulk('auto_on') },
      { label: `⚪  Tắt auto (${n})`, onClick: () => bulk('auto_off') },
      '-',
      { label: `🧹  Làm mới profile — xóa sạch (${n})`, onClick: () => bulk('wipe') },
      { label: `🗑  Xóa profile (${n})`, onClick: () => bulk('delete') },
    ]);
  }

  const BULK_TITLE = { open: 'Mở login browser', close: 'Đóng login browser', clear_sleep: 'Xóa thời gian ngủ', proxy: 'Sửa proxy',
    auto_on: 'Bật auto', auto_off: 'Tắt auto', wipe: 'Làm mới profile', delete: 'Xóa profile' };

  async function bulk(key) {
    const byId = new Map(state.profiles.map((p) => [p.id, p]));
    const ids = [...state.checked].filter((i) => byId.has(i)).sort((a, b) => a - b);
    if (!ids.length) return;
    const skipped = [];
    const pick = (pred, why) => ids.filter((i) => pred(byId.get(i)) || (skipped.push(`${pname(byId.get(i))} (${why})`), false));
    let calls;

    if (key === 'open') calls = pick((p) => !p.worker_running && !p.login_open, 'đang chạy/đã mở').map((i) => [i, 'POST', `/api/selenium/profiles/${i}/open`]);
    else if (key === 'close') calls = pick((p) => p.login_open, 'chưa mở login').map((i) => [i, 'POST', `/api/selenium/profiles/${i}/close`]);
    else if (key === 'clear_sleep') calls = pick((p) => p.status === 'sleeping', 'không ngủ').map((i) => [i, 'POST', `/api/selenium/profiles/${i}/clear_sleep`]);
    else if (key === 'auto_on' || key === 'auto_off') calls = ids.map((i) => [i, 'PATCH', `/api/selenium/profiles/${i}`, { enabled: key === 'auto_on' ? 1 : 0 }]);
    else if (key === 'proxy') {
      const proxies = await askBulkProxy(ids.map((i) => byId.get(i)));
      if (!proxies) return;
      calls = ids.map((i, n) => [i, 'PATCH', `/api/selenium/profiles/${i}`, { proxy_server: proxies[n] }]);
    } else if (key === 'wipe') {
      const todo = pick((p) => !p.worker_running && !p.login_open, 'đang chạy/đang mở login');
      if (todo.length && !(await confirmDialog(
        `Xóa SẠCH TOÀN BỘ dữ liệu Chrome của ${todo.length} profile đã chọn?\n\n⚠️ Mất đăng nhập Google, mật khẩu đã lưu, bookmark, cache, cookie, lịch sử, mọi cài đặt Chrome. ` +
        'Cần "Mở login browser" đăng nhập lại. KHÔNG THỂ HOÀN TÁC.', { title: 'Làm mới profile (xóa sạch — về trắng)', danger: true, okText: 'Xóa sạch' }))) return;
      calls = todo.map((i) => [i, 'POST', `/api/selenium/profiles/${i}/clear_data`]);
    } else if (key === 'delete') {
      const names = ids.slice(0, 8).map((i) => pname(byId.get(i))).join(', ') + (ids.length > 8 ? ' …' : '');
      if (!(await confirmDialog(`Xóa ${ids.length} profile đã chọn?\n${names}\nDữ liệu Chrome sẽ giữ lại.`, { title: 'Xóa profile', danger: true, okText: 'Xóa' }))) return;
      calls = ids.map((i) => [i, 'DELETE', `/api/selenium/profiles/${i}`]);
    } else return;

    if (!calls.length) { await alertDialog('Không có profile nào phù hợp.\n' + skipped.slice(0, 10).join('\n'), 'Hành động hàng loạt'); return; }

    let ok = 0; const errs = [];
    for (const [pid, method, path, body] of calls) {
      const name = pname(byId.get(pid) || { id: pid });
      try {
        const r = await api(method, path, body);
        r && r.error ? errs.push(`${name}: ${r.error}`) : ok++;
      } catch (e) { errs.push(`${name}: ${e.message || e}`); }
    }
    if (key === 'delete') calls.forEach(([pid]) => state.checked.delete(pid));
    ctx.refreshNow();
    let msg = `${BULK_TITLE[key]}: thành công ${ok}/${calls.length}.`;
    if (skipped.length) msg += '\nBỏ qua: ' + skipped.slice(0, 8).join(', ') + (skipped.length > 8 ? ' …' : '');
    if (errs.length) msg += '\nLỗi:\n' + errs.slice(0, 8).join('\n');
    await alertDialog(msg, 'Hành động hàng loạt');
  }

  // Hộp nhập proxy hàng loạt: 1 dòng = áp cho tất cả; đúng N dòng = gán lần lượt; trống = xoá proxy.
  function askBulkProxy(plist) {
    const n = plist.length;
    return new Promise((resolve) => {
      const cur = new Set(plist.map((p) => (p.proxy_server || '').trim()));
      const ta = h('textarea', { rows: 9, placeholder: 'host:port\nuser:pass@host:port\nsocks5://host:port' });
      if (cur.size === 1) ta.value = [...cur][0];
      const err = h('div', { style: { color: 'var(--red)', fontSize: '12px', minHeight: '16px' } });
      let m;
      const finish = (val) => { resolve(val); m.close(val); };   // resolve TRƯỚC, đóng sau (onClose chỉ resolve(null) khi huỷ)
      const submit = async () => {
        const lines = ta.value.split('\n').map((s) => s.trim()).filter(Boolean);
        if (!lines.length) {
          if (!(await confirmDialog(`Xoá proxy của ${n} profile đã chọn?`, { title: 'Xoá proxy', danger: true, okText: 'Xoá proxy' }))) return;
          return finish(Array(n).fill(''));
        }
        if (lines.length === 1) return finish(Array(n).fill(lines[0]));
        if (lines.length === n) return finish(lines);
        err.textContent = `Có ${lines.length} dòng nhưng đã chọn ${n} profile. Nhập 1 dòng hoặc đúng ${n} dòng.`;
      };
      m = openModal({
        title: `Sửa proxy — ${n} profile đã chọn`, icon: '🌐', onClose: (r) => { if (r === undefined) resolve(null); },
        body: h('div', { class: 'form-pane' },
          h('div', { class: 'hint', style: { lineHeight: 1.7, marginBottom: '8px' } },
            `• Nhập 1 dòng: áp cho cả ${n} profile.`, h('br'),
            `• Nhập đúng ${n} dòng: gán lần lượt theo thứ tự bên dưới.`, h('br'),
            '• Để trống: XOÁ proxy (đi thẳng).', h('br'),
            'Định dạng: host:port · scheme://host:port · user:pass@host:port · host:port:user:pass', h('br'),
            'Đổi proxy chỉ có hiệu lực ở lần mở Chrome kế tiếp (Stop rồi Start lại profile).'),
          h('div', { class: 'hint', style: { marginBottom: '8px' } }, 'Thứ tự: ' + plist.slice(0, 12).map(pname).join(', ') + (n > 12 ? ' …' : '')),
          ta, err),
        footer: [h('button', { class: 'btn', onclick: () => m.close() }, 'Hủy'), h('button', { class: 'btn primary', onclick: submit }, 'Áp dụng')],
      });
    });
  }

  return { el, onStatus, onShow: () => { state.lastSig = null; if (ctx.store.status) onStatus(ctx.store.status); } };
}
