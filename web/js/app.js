// Khung ứng dụng: sidebar + định tuyến 4 trang + vòng thăm dò /api/selenium/status.
import { h, clear } from './util.js';
import { api, initBridge, hasBridge, bridgeCall } from './api.js';
import { confirmDialog, toast } from './ui.js';
import { createProfilesPage } from './pages/profiles.js';
import { createExtensionsPage } from './pages/extensions.js';
import { createLogsPage } from './pages/logs.js';
import { createSettingsPage } from './pages/settings.js';

const OFFLINE_AFTER_MS = 25000;   // chỉ báo "offline" khi KHÔNG có lần poll nào thành công trong khoảng này
const DEFAULT_INTERVAL = 5000;

export const store = {
  status: null,
  meta: null,
  lastOkAt: Date.now(),
  inFlight: false,
  inFlightSince: 0,
  auto: true,
  interval: DEFAULT_INTERVAL,
  timer: null,
  listeners: new Set(),
};

export function refreshNow() { return poll(true); }
export function setAuto(on) { store.auto = on; }
export function setIntervalMs(ms) { store.interval = Math.max(1000, ms); schedule(); }

function schedule() {
  clearInterval(store.timer);
  store.timer = setInterval(() => poll(false), store.interval);
}

async function poll(force) {
  if (!force && !store.auto) return;
  if (store.inFlight) {
    if (Date.now() - store.inFlightSince < 30000) return;   // call trước chưa xong — đừng chồng thêm
  }
  store.inFlight = true; store.inFlightSince = Date.now();
  try {
    const data = await api('GET', '/api/selenium/status');
    if (!data || typeof data !== 'object') throw new Error('Phản hồi không đúng định dạng JSON');
    store.status = data;
    store.lastOkAt = Date.now();
    setServer(true);
    setSwitch(!!data.master_switch_enabled);
    store.listeners.forEach((fn) => fn(data));
  } catch (e) {
    const elapsed = Date.now() - store.lastOkAt;
    if (elapsed >= OFFLINE_AFTER_MS) {
      setServer(false, `Không có phản hồi thành công trong ${Math.round(elapsed / 1000)}s. Lỗi gần nhất: ${e.message || e}`);
    }
  } finally {
    store.inFlight = false;
  }
}

// ── Sidebar ──
const NAV = [['👤', 'Profiles'], ['🧩', 'Extensions'], ['📜', 'Logs'], ['⚙️', 'Cài đặt']];
let srvDot, srvText, srvCard, swDot, swText, swBtn, switchEnabled = false;

function buildSidebar(meta) {
  const sb = document.getElementById('sidebar');
  clear(sb);
  sb.append(
    h('div', { class: 'logo' },
      h('div', { class: 'mark' }, 'P'),
      h('div', null, h('div', { class: 't1' }, 'Profile Manager'),
        h('div', { class: 't2' }, `Selenium Worker Console · v${meta.app_version}`))),
    h('div', { class: 'section-lbl' }, 'WORKSPACE'),
    ...NAV.map(([ic, lb], i) => h('button', { class: 'nav', 'data-i': i, onclick: () => go(i) }, h('span', null, ic), lb)),
    h('div', { class: 'sb-grow' }),
  );

  srvDot = h('span', { class: 'dot wait' }, '●');
  srvText = h('span', { class: 'txt' }, 'Connecting…');
  srvCard = h('div', { class: 'sb-card' }, srvDot, srvText);

  swDot = h('span', { class: 'dot ok' }, '●');
  swText = h('span', { class: 'txt' }, 'Đang nhận task');
  swBtn = h('button', { class: 'btn icon sm', onclick: toggleSwitch }, '⏻');
  const swCard = h('div', { class: 'sb-card' }, swDot, swText, swBtn);

  sb.append(srvCard, swCard);

  const userCard = h('div', { class: 'sb-card' }, h('span', null, '👤'), h('span', { class: 'txt' }, meta.user || '—'));
  if (hasBridge()) {
    userCard.append(h('button', {
      class: 'btn icon sm', title: 'Đăng xuất', onclick: async () => {
        if (await confirmDialog('Đăng xuất sẽ đóng hẳn ứng dụng (dừng mọi profile đang chạy). Tiếp tục?', { title: 'Đăng xuất' })) {
          bridgeCall('logout');
        }
      },
    }, '⎋'));
  }
  sb.append(userCard);
  applySwitchStyle();
}

function setServer(ok, detail = '') {
  if (!srvDot) return;
  srvDot.className = `dot ${ok ? 'ok' : 'err'}`;
  srvText.textContent = ok ? 'Server online' : 'Server offline';
  srvCard.title = ok ? '' : detail;
}

function applySwitchStyle() {
  if (!swDot) return;
  swDot.className = `dot ${switchEnabled ? 'ok' : 'err'}`;
  swText.style.color = switchEnabled ? 'var(--green)' : 'var(--red)';
  swText.textContent = switchEnabled ? 'Đang nhận task' : 'Đã tạm dừng';
  swBtn.title = switchEnabled
    ? 'Bấm để TẠM DỪNG — đóng hết Chrome/worker trên máy này ngay lập tức'
    : 'Bấm để BẬT LẠI — khôi phục các profile đã dừng và tiếp tục nhận task';
}
function setSwitch(on) { if (on !== switchEnabled) { switchEnabled = on; applySwitchStyle(); } }

async function toggleSwitch() {
  swBtn.disabled = true;
  try {
    const r = await api('POST', '/api/selenium/master_switch', { enabled: !switchEnabled });
    if (r && 'enabled' in r) setSwitch(r.enabled);
    else throw new Error('bad response');
  } catch {
    swDot.className = 'dot err'; swText.style.color = 'var(--red)'; swText.textContent = 'Không kết nối được server';
    setTimeout(applySwitchStyle, 3000);
  } finally {
    swBtn.disabled = false;
  }
}

// ── Định tuyến ──
let pages = [], current = -1;

export function go(i, arg) {
  if (current >= 0) pages[current].onHide && pages[current].onHide();
  current = i;
  const main = document.getElementById('main');
  clear(main).append(pages[i].el);
  document.querySelectorAll('.nav').forEach((n) => n.classList.toggle('active', +n.dataset.i === i));
  pages[i].onShow && pages[i].onShow(arg);
  if (store.status) pages[i].onStatus && pages[i].onStatus(store.status);
}

async function main() {
  await initBridge();
  let meta;
  try { meta = await api('GET', '/api/selenium/ui_meta'); }
  catch (e) { document.getElementById('main').textContent = `Không tải được cấu hình giao diện: ${e}`; return; }
  store.meta = meta;
  buildSidebar(meta);

  const ctx = { store, go, refreshNow, setAuto, setIntervalMs };
  pages = [createProfilesPage(ctx), createExtensionsPage(ctx), createLogsPage(ctx), createSettingsPage(ctx)];
  store.listeners.add((data) => { if (current >= 0) pages[current].onStatus && pages[current].onStatus(data); });
  store.listeners.add((data) => pages[2].feedProfiles && pages[2].feedProfiles(data.profiles || []));

  go(0);
  schedule();
  poll(true);
}

main();
