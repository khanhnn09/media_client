"""CloakBrowser — luồng mở trình duyệt RIÊNG, tách hẳn khỏi luồng Chrome thường
+ `undetected_chromedriver` (chrome_utils.py / worker._make_driver()).

Bật bằng setting `use_cloakbrowser`. Khi bật, worker và login browser gọi thẳng
`open_cloak_driver()` ở đây và KHÔNG đi qua bất kỳ đoạn nào của luồng cũ (uc,
selenium-stealth, script sửa navigator.webdriver, cờ tắt GPU, Chrome Portable,
extension proxy-auth, relay SOCKS5). Tắt setting → luồng cũ chạy y nguyên.

Cấu hình tương đương demo của thư viện:

    launch(proxy=<proxy của profile>, geoip=True, headless=False, humanize=True)

- Cờ dòng lệnh dựng bằng CHÍNH hàm của cloakbrowser (`build_args`,
  `_resolve_proxy_config`, `maybe_resolve_geoip`, `_append_webrtc_exit_ip`) nên
  khớp với `launch()` của họ — chỉ khác là Selenium (ChromeDriver) điều khiển
  thay cho Playwright, vì toàn bộ worker viết bằng Selenium.
- `geoip` → múi giờ + ngôn ngữ (`--lang`/`--fingerprint-locale`) + IP WebRTC
  theo IP ra của proxy (không proxy thì theo IP máy).
- `humanize` → `CloakHuman`: dùng bộ đường chuột Bezier + nhịp gõ phím của
  cloakbrowser.human, bắn qua CDP (vì `humanize=True` của họ chỉ vá API
  Playwright, không áp được cho Selenium).
- Seed `--fingerprint` cố định theo profile (thư viện random mỗi lần mở — với
  profile giữ đăng nhập lâu dài, fingerprint đổi mỗi lần chính là bất thường).
- Thư mục profile RIÊNG `<profile_dir>_cloak`: Chromium của Cloak cũ hơn Chrome
  đang cài, mở profile do Chrome mới tạo sẽ hỏng → lần đầu phải đăng nhập lại.
"""

from __future__ import annotations

import os
import random
import time
import zlib
from pathlib import Path
from urllib.parse import quote

from .config import log, CHROMEDRIVER_PATH
from .managers import em

CLOAK_DIR_SUFFIX = '_cloak'

# Cờ ChromeDriver tự thêm khi launch mà Chrome người dùng thật không bao giờ có —
# bỏ hết để dòng lệnh giống trình duyệt bình thường nhất có thể.
_CHROMEDRIVER_SWITCHES_TO_DROP = [
    'enable-automation', 'enable-logging', 'test-type', 'disable-popup-blocking',
    'disable-default-apps', 'disable-sync', 'disable-background-networking',
    'disable-client-side-phishing-detection', 'disable-hang-monitor',
    'disable-prompt-on-repost', 'password-store', 'use-mock-keychain',
    'allow-pre-commit-input', 'no-service-autorun', 'log-level',
]

_bin_cache = ''
_geo_cache: dict = {}


# ── Setting ──────────────────────────────────────────────────────────────────

def _settings() -> dict:
    try:
        from .local_settings import get_local_settings
        return get_local_settings()
    except Exception:
        return {}


def cloak_enabled() -> bool:
    """Đọc tươi mỗi lần gọi — bật/tắt áp dụng cho lần mở trình duyệt kế tiếp."""
    return bool(int(_settings().get('use_cloakbrowser') or 0))


def cloak_launch_config() -> dict:
    """Cấu hình đang áp dụng, cùng tên tham số với `cloakbrowser.launch()`."""
    s = _settings()
    return {
        'geoip':    bool(int(s.get('cloak_geoip', 1) or 0)),
        'headless': False,
        'humanize': bool(int(s.get('cloak_humanize', 1) or 0)),
    }


# ── Binary / thư mục / proxy ─────────────────────────────────────────────────

def cloak_binary() -> str:
    """Chromium của Cloak (lần đầu tự tải ~550MB về ~/.cloakbrowser). Raise nếu
    chưa cài `cloakbrowser` — KHÔNG âm thầm quay về Chrome thường."""
    global _bin_cache
    if _bin_cache and Path(_bin_cache).exists():
        return _bin_cache
    try:
        from cloakbrowser.download import ensure_binary
    except ImportError as e:
        raise RuntimeError('Chưa cài thư viện cloakbrowser (pip install "cloakbrowser[geoip]") '
                           '— bỏ chọn "Chạy bằng CloakBrowser" hoặc cài rồi mở lại app') from e
    log.info('[cloak] Kiểm tra/tải binary CloakBrowser (lần đầu có thể mất vài phút)…')
    _bin_cache = str(ensure_binary())
    log.info(f'[cloak] Binary: {_bin_cache}')
    return _bin_cache


def cloak_profile_dir(profile: dict) -> str:
    from .chrome_utils import _usable_profile_dir
    pd = _usable_profile_dir(profile).rstrip('\\/') + CLOAK_DIR_SUFFIX
    os.makedirs(pd, exist_ok=True)
    return pd


def cloak_proxy_url(raw: str) -> str | None:
    """Chuỗi proxy ở ProfileDialog (mọi định dạng parse_proxy nhận) → URL chuẩn
    `scheme://user:pass@host:port`. Không ghi scheme mà có user/pass thì dò thật
    (SOCKS5 hay HTTP) — cùng cách luồng cũ làm."""
    from .proxy_config import parse_proxy
    info = parse_proxy(raw or '')
    if not info:
        return None
    scheme = info['scheme']
    if info['username'] and not info['explicit_scheme']:
        try:
            from .socks_relay import detect_scheme
            scheme = detect_scheme(info['host'], info['port'], info['username'], info['password'])
        except Exception as e:
            log.warning(f'[cloak] Không dò được loại proxy ({e}) — dùng {scheme}')
    hostport = f'{info["host"]}:{info["port"]}' if info['port'] else info['host']
    cred = ''
    if info['username']:
        cred = f'{quote(info["username"], safe="")}:{quote(info["password"] or "", safe="")}@'
    return f'{scheme}://{cred}{hostport}'


def _mask(url: str | None) -> str:
    if not url:
        return 'không proxy'
    if '@' in url:
        scheme, rest = url.split('://', 1)
        return f'{scheme}://***@{rest.split("@", 1)[1]}'
    return url


def _resolve_geo(proxy_url: str | None) -> tuple:
    """(timezone, locale, exit_ip) theo IP ra — cache theo proxy trong tiến trình.
    Lỗi → (None, None, None): vẫn mở trình duyệt, giữ múi giờ/ngôn ngữ máy."""
    key = proxy_url or '<direct>'
    if key in _geo_cache:
        return _geo_cache[key]
    try:
        from cloakbrowser.browser import maybe_resolve_geoip
        res = maybe_resolve_geoip(True, proxy_url, None, None, [])
    except Exception as e:
        log.warning(f'[cloak] geoip lỗi ({e}) — giữ múi giờ/ngôn ngữ máy')
        res = (None, None, None)
    _geo_cache[key] = res
    return res


def _fingerprint_seed(profile_id) -> int:
    return 10000 + zlib.crc32(f'toolsub-cloak-{profile_id}'.encode()) % 90000


# ── Dựng ChromeOptions ───────────────────────────────────────────────────────

def build_cloak_options(profile: dict, debug_port: int, load_extensions: bool = True,
                        detach: bool = False):
    """Trả (Options, summary_str). Mọi cờ stealth/proxy/geoip đi qua hàm của
    cloakbrowser để khớp `launch()`."""
    from selenium.webdriver.chrome.options import Options
    from cloakbrowser.browser import (build_args, _resolve_proxy_config,
                                      _append_webrtc_exit_ip)
    from cloakbrowser.config import binary_supports_maximized_window

    cfg = cloak_launch_config()
    pid = profile.get('id')
    prof_dir = cloak_profile_dir(profile)
    proxy_url = cloak_proxy_url(profile.get('proxy_server') or '')

    extra: list[str] = [f'--fingerprint={_fingerprint_seed(pid)}']
    if proxy_url:
        proxy_kwargs, proxy_args = _resolve_proxy_config(proxy_url)
        if proxy_kwargs and not proxy_args:
            # HTTP không mật khẩu: launch() đưa vào dict proxy của Playwright —
            # với Selenium thì truyền thẳng --proxy-server.
            proxy_args = [f'--proxy-server={proxy_url}']
        extra += proxy_args
        extra.append('--proxy-bypass-list=localhost;127.0.0.1;[::1]')

    tz = locale = ip = None
    if cfg['geoip']:
        tz, locale, ip = _resolve_geo(proxy_url)
        extra = _append_webrtc_exit_ip(extra, ip)

    extra += [
        f'--user-data-dir={prof_dir}',
        '--profile-directory=Default',
        f'--remote-debugging-port={debug_port}',
        '--no-first-run', '--no-default-browser-check',
        '--disable-session-crashed-bubble', '--no-restore-last-session',
        # Tab nền vẫn chạy JS bình thường (worker xoay nhiều tab/cửa sổ)
        '--disable-renderer-backgrounding', '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
    ]
    ext_paths = list(em.active_paths()) if load_extensions else []

    args = build_args(True, extra, timezone=tz, locale=locale, headless=False,
                      extension_paths=ext_paths or None,
                      start_maximized=binary_supports_maximized_window())

    opts = Options()
    opts.binary_location = cloak_binary()
    for a in args:
        opts.add_argument(a)
    opts.add_experimental_option('excludeSwitches', _CHROMEDRIVER_SWITCHES_TO_DROP)
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_experimental_option('prefs', {
        'credentials_enable_service':                            False,
        'profile.password_manager_enabled':                      False,
        'profile.default_content_setting_values.notifications': 2,
    })
    if detach:
        opts.add_experimental_option('detach', True)
    opts.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})

    summary = (f'geoip={cfg["geoip"]} (tz={tz or "máy"}, locale={locale or "máy"}, '
               f'webrtc={ip or "-"}), headless=False, humanize={cfg["humanize"]}, '
               f'proxy={_mask(proxy_url)}, dir={prof_dir}')
    return opts, summary


def _cloak_service():
    """ChromeDriver khớp major version Chromium của Cloak. File cục bộ lệch bản
    → Service() trống để Selenium Manager tự tải đúng bản theo binary_location."""
    from selenium.webdriver.chrome.service import Service
    from .chrome_utils import _chromedriver_major_version, _detect_chrome_version
    drv = CHROMEDRIVER_PATH if CHROMEDRIVER_PATH and Path(CHROMEDRIVER_PATH).exists() else ''
    if not drv:
        return Service()
    want = _detect_chrome_version(cloak_binary())
    have = _chromedriver_major_version(drv)
    if want and have and want != have:
        return Service()
    return Service(executable_path=drv)


# ── Mở / attach ──────────────────────────────────────────────────────────────

def open_cloak_driver(profile: dict, debug_port: int, *, detach: bool = False,
                      load_extensions: bool = True, log_fn=None):
    """Mở (hoặc attach vào) CloakBrowser của profile. Dùng chung cho worker và
    "Mở login browser"."""
    from selenium import webdriver
    from .chrome_utils import (_usable_profile_dir, _chrome_alive_on_port,
                               _chrome_running_for_profile, _kill_chrome_for_profile,
                               _connect_to_chrome, _patch_selenium_pool_size)

    def _log(level, msg):
        if log_fn:
            log_fn(level, msg)
        else:
            getattr(log, 'warning' if level == 'warn' else 'info')(msg)

    _patch_selenium_pool_size()
    prof_dir = cloak_profile_dir(profile)

    # Chrome THƯỜNG của profile này còn mở (giữ cổng debug) → đóng trước, không
    # thì attach nhầm sang trình duyệt cũ.
    plain_dir = _usable_profile_dir(profile)
    if _chrome_running_for_profile(plain_dir):
        _log('warn', '[cloak] Chrome thường của profile này đang mở — đóng để mở bằng CloakBrowser')
        _kill_chrome_for_profile(plain_dir, log_fn=log_fn)
        time.sleep(2)

    if _chrome_alive_on_port(debug_port) and _chrome_running_for_profile(prof_dir):
        drv = _connect_to_chrome(debug_port, log_fn=log_fn)
        if drv:
            _log('ok', f'[cloak] Attach vào CloakBrowser đang mở (port {debug_port})')
            return drv
    if _chrome_running_for_profile(prof_dir):
        _kill_chrome_for_profile(prof_dir, log_fn=log_fn)
        time.sleep(2)

    opts, summary = build_cloak_options(profile, debug_port, load_extensions=load_extensions,
                                        detach=detach)
    _log('info', f'[cloak] Engine: CloakBrowser — {summary}')
    drv = webdriver.Chrome(service=_cloak_service(), options=opts)
    _log('info', f'[cloak] CloakBrowser opened (port={debug_port})')
    return drv


# ── Humanize qua CDP ─────────────────────────────────────────────────────────

class CloakHuman:
    """Đường chuột Bezier + nhịp gõ phím của cloakbrowser.human, bắn qua CDP
    `Input.dispatch*` của Selenium. Nhớ vị trí con trỏ giữa các lần bấm để
    chuột di chuyển liên tục như người thật (không nhảy cóc)."""

    def __init__(self, driver, preset: str = 'default'):
        from cloakbrowser.human.config import resolve_config
        self.driver = driver
        self.cfg = resolve_config(preset)
        self.x = random.randint(300, 700)
        self.y = random.randint(200, 500)
        self._buttons = 0

    def _cdp(self, method, params):
        return self.driver.execute_cdp_cmd(method, params)

    # RawMouse protocol
    def move(self, x, y):
        self.x, self.y = x, y
        self._cdp('Input.dispatchMouseEvent', {
            'type': 'mouseMoved', 'x': x, 'y': y,
            'button': 'left' if self._buttons else 'none', 'buttons': self._buttons})

    def down(self):
        self._buttons = 1
        self._cdp('Input.dispatchMouseEvent', {
            'type': 'mousePressed', 'x': self.x, 'y': self.y, 'button': 'left',
            'buttons': 1, 'clickCount': 1})

    def up(self):
        self._buttons = 0
        self._cdp('Input.dispatchMouseEvent', {
            'type': 'mouseReleased', 'x': self.x, 'y': self.y, 'button': 'left',
            'buttons': 0, 'clickCount': 1})

    def wheel(self, delta_x, delta_y):
        self._cdp('Input.dispatchMouseEvent', {
            'type': 'mouseWheel', 'x': self.x, 'y': self.y,
            'deltaX': delta_x, 'deltaY': delta_y})

    def click_box(self, left, top, width, height, is_input=False):
        from cloakbrowser.human.mouse import human_move, human_click, click_target
        pt = click_target({'x': left, 'y': top, 'width': width, 'height': height},
                          is_input, self.cfg)
        human_move(self, self.x, self.y, pt.x, pt.y, self.cfg)
        human_click(self, is_input, self.cfg)

    def type_text(self, text: str):
        from cloakbrowser.human.keyboard import human_type
        human_type(None, _CdpKeyboard(self), text, self.cfg, cdp_session=_CdpSession(self))


_KEY_CODES = {'Backspace': ('Backspace', 8), 'Shift': ('ShiftLeft', 16),
              'Enter': ('Enter', 13), 'Tab': ('Tab', 9)}


class _CdpSession:
    def __init__(self, h: CloakHuman):
        self.h = h

    def send(self, method, params):
        return self.h._cdp(method, params)


class _CdpKeyboard:
    """RawKeyboard protocol của cloakbrowser.human qua CDP."""

    def __init__(self, h: CloakHuman):
        self.h = h

    def _event(self, etype, key):
        if key in _KEY_CODES:
            code, vk = _KEY_CODES[key]
            p = {'type': etype, 'key': key, 'code': code, 'windowsVirtualKeyCode': vk}
            if key == 'Shift':
                p['modifiers'] = 8 if etype == 'keyDown' else 0
        else:
            vk = ord(key.upper()) if len(key) == 1 else 0
            p = {'type': etype, 'key': key, 'windowsVirtualKeyCode': vk}
            if etype == 'keyDown' and len(key) == 1:
                p['text'] = key
                p['unmodifiedText'] = key
        self.h._cdp('Input.dispatchKeyEvent', p)

    def down(self, key):
        self._event('keyDown', key)

    def up(self, key):
        self._event('keyUp', key)

    def type(self, text):
        for ch in text:
            self.down(ch)
            self.up(ch)

    def insert_text(self, text):
        self.h._cdp('Input.insertText', {'text': text})
