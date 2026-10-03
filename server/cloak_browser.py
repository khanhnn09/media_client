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

import json
import os
import random
import threading
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


_FP_EPOCH_FILE = Path(__file__).parent.parent / 'cloak_fp_epoch.json'
_fp_lock = threading.Lock()

# (2026-10-03) Các MỤC fingerprint có thể đổi khi Google chặn — user chọn mục nào
# trong hộp thoại profile (cờ cục bộ `fp_aspects`, rỗng/None = đổi TẤT CẢ).
# Mỗi mục có "đời" riêng: đổi mục nào thì chỉ đời của mục đó tăng.
#   seed     : CloakBrowser --fingerprint (đổi cả bộ canvas/audio/WebGL/font nội bộ)
#   hardware : số nhân CPU + RAM báo cho trang
#   window   : kích thước cửa sổ trình duyệt
#   canvas / audio / webgl : nhiễu nhỏ (Chrome thường — Cloak đã gồm trong seed)
#   languages: thứ tự navigator.languages (Chrome thường)
FP_ASPECTS = {
    'seed':      'Seed CloakBrowser (canvas/audio/WebGL/font nội bộ)',
    'hardware':  'Phần cứng ảo (số nhân CPU, RAM)',
    'window':    'Kích thước cửa sổ',
    'canvas':    'Nhiễu canvas (Chrome thường)',
    'audio':     'Nhiễu âm thanh (Chrome thường)',
    'webgl':     'Nhiễu WebGL (Chrome thường)',
    'languages': 'Danh sách ngôn ngữ (Chrome thường)',
}
_CLOAK_ASPECTS = ('seed', 'hardware', 'window')
_CHROME_ASPECTS = ('hardware', 'window', 'canvas', 'audio', 'webgl', 'languages')


def parse_fp_aspects(value) -> list:
    """CSV/list → danh sách mục hợp lệ; rỗng/None = tất cả."""
    if isinstance(value, str):
        value = [x.strip() for x in value.split(',')]
    got = [x for x in (value or []) if x in FP_ASPECTS]
    return got or list(FP_ASPECTS)


def _load_fp_epochs() -> dict:
    """{pid: {aspect: đời}}. File cũ dạng {pid: số} = mọi mục cùng đời đó."""
    try:
        raw = json.loads(_FP_EPOCH_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}
    out = {}
    for pid, v in raw.items():
        out[pid] = ({a: int(v) for a in FP_ASPECTS} if isinstance(v, int)
                    else {a: int((v or {}).get(a, 0)) for a in FP_ASPECTS})
    return out


def _epoch(profile_id, aspect) -> int:
    return int(_load_fp_epochs().get(str(profile_id), {}).get(aspect, 0))


_WIN_SIZES = [(1280, 720), (1366, 768), (1440, 900), (1536, 864), (1600, 900),
              (1680, 1050), (1920, 1080)]
_LANGS = [['en-US', 'en'], ['en-US', 'en', 'vi'], ['vi-VN', 'vi', 'en-US', 'en'],
          ['en-GB', 'en'], ['vi', 'en-US', 'en']]


def aspect_value(profile_id, aspect, epoch=None):
    """Giá trị của 1 mục ở 1 đời (đời 0 = None: để nguyên của máy thật)."""
    n = _epoch(profile_id, aspect) if epoch is None else epoch
    if not n:
        return None
    rnd = random.Random(zlib.crc32(f'toolsub-fp-{profile_id}-{aspect}-{n}'.encode()))
    if aspect == 'seed':
        return 10000 + rnd.randint(0, 89999)
    if aspect == 'hardware':
        return {'cores': rnd.choice([4, 6, 8, 12, 16]), 'mem': rnd.choice([4, 8, 8, 16])}
    if aspect == 'window':
        w, h = rnd.choice(_WIN_SIZES)
        return {'w': w, 'h': h}
    if aspect == 'languages':
        return rnd.choice(_LANGS)
    return rnd.randint(1, 2 ** 31 - 1)       # canvas / audio / webgl: seed nhiễu


def describe_value(aspect, v) -> str:
    if v is None:
        return 'mặc định máy thật'
    if aspect == 'hardware':
        return f"{v['cores']} nhân / {v['mem']}GB"
    if aspect == 'window':
        return f"{v['w']}x{v['h']}"
    if aspect == 'languages':
        return ','.join(v)
    if aspect == 'seed':
        return f'seed {v}'
    return f'nhiễu #{v}'


def rotate_fingerprint(profile_id, aspects, cloak: bool) -> list:
    """Đổi các mục đã chọn sang đời kế tiếp. Trả danh sách dòng mô tả để ghi log
    ("Nhãn: cũ → mới" hoặc lý do bỏ qua)."""
    applicable = _CLOAK_ASPECTS if cloak else _CHROME_ASPECTS
    lines = []
    with _fp_lock:
        d = _load_fp_epochs()
        cur = d.setdefault(str(profile_id), {a: 0 for a in FP_ASPECTS})
        for a in parse_fp_aspects(aspects):
            if a not in applicable:
                why = ('đã nằm trong seed của CloakBrowser' if cloak
                       else 'chỉ dùng cho CloakBrowser')
                lines.append(f'{FP_ASPECTS[a]}: bỏ qua ({why})')
                continue
            old = aspect_value(profile_id, a, cur[a])
            cur[a] += 1
            new = aspect_value(profile_id, a, cur[a])
            lines.append(f'{FP_ASPECTS[a]}: {describe_value(a, old)} → {describe_value(a, new)}')
        try:
            _FP_EPOCH_FILE.write_text(json.dumps(d), encoding='utf-8')
        except Exception as e:
            log.warning(f'[cloak] không lưu được epoch fingerprint: {e}')
    return lines


def current_fingerprint_lines(profile_id, cloak: bool) -> list:
    """Giá trị fingerprint đang áp (chỉ các mục đã từng đổi) — để ghi log lúc mở."""
    out = []
    for a in (_CLOAK_ASPECTS if cloak else _CHROME_ASPECTS):
        v = aspect_value(profile_id, a)
        if v is not None:
            out.append(f'{FP_ASPECTS[a]}: {describe_value(a, v)}')
    return out


def fingerprint_patch_js(profile_id) -> str:
    """JS vá fingerprint cho Chrome thường theo đời từng mục. Rỗng nếu chưa đổi gì.
    (CPU/cửa sổ đi qua CDP chính thống — xem `apply_cdp_overrides`.)"""
    parts = []
    hw = aspect_value(profile_id, 'hardware')
    if hw:
        parts.append("try{Object.defineProperty(navigator,'deviceMemory',{get:()=>%d})}catch(e){}"
                     % hw['mem'])
    lg = aspect_value(profile_id, 'languages')
    if lg:
        parts.append("try{Object.defineProperty(navigator,'languages',{get:()=>%s});"
                     "Object.defineProperty(navigator,'language',{get:()=>%s})}catch(e){}"
                     % (json.dumps(lg), json.dumps(lg[0])))
    cv = aspect_value(profile_id, 'canvas')
    if cv:
        parts.append("""(()=>{let x=%d;const rn=()=>{x=(x*1664525+1013904223)>>>0;return x/4294967296};
const gid=CanvasRenderingContext2D.prototype.getImageData;
CanvasRenderingContext2D.prototype.getImageData=function(a,b,c,d){
 const r=gid.call(this,a,b,c,d);const n=r.data.length;
 for(let i=0;i<8;i++){const k=(Math.floor(rn()*n)>>2<<2);r.data[k]=(r.data[k]+(rn()<.5?1:-1))&255}
 return r};
const tdu=HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL=function(){
 try{const c=this.getContext('2d');if(c&&this.width&&this.height){
  const im=gid.call(c,0,0,1,1);im.data[0]=(im.data[0]+1)&255;c.putImageData(im,0,0)}}catch(e){}
 return tdu.apply(this,arguments)}})();""" % cv)
    au = aspect_value(profile_id, 'audio')
    if au:
        parts.append("""(()=>{let x=%d;const rn=()=>{x=(x*1664525+1013904223)>>>0;return x/4294967296};
const gcd=AudioBuffer.prototype.getChannelData;
AudioBuffer.prototype.getChannelData=function(){const d=gcd.apply(this,arguments);
 if(!this.__fp){this.__fp=1;for(let i=0;i<d.length;i+=Math.max(1,(d.length/16)|0)){d[i]+=(rn()-.5)*1e-7}}
 return d}})();""" % au)
    wg = aspect_value(profile_id, 'webgl')
    if wg:
        parts.append("""(()=>{let x=%d;const rn=()=>{x=(x*1664525+1013904223)>>>0;return x/4294967296};
for(const C of [WebGLRenderingContext,self.WebGL2RenderingContext]){if(!C)continue;
 const rp=C.prototype.readPixels;
 C.prototype.readPixels=function(){const r=rp.apply(this,arguments);
  const buf=arguments[6];if(buf&&buf.length){const k=Math.floor(rn()*buf.length);buf[k]=(buf[k]+1)&255}return r}}})();""" % wg)
    if not parts:
        return ''
    return '(()=>{' + '\n'.join(parts) + '})();'


def apply_cdp_overrides(driver, profile_id, log_fn=None) -> list:
    """Phần đổi được bằng CDP chính thống (không vá JS): số nhân CPU, kích thước
    cửa sổ. Dùng cho CẢ CloakBrowser và Chrome thường. Trả danh sách mục đã áp."""
    done = []
    hw = aspect_value(profile_id, 'hardware')
    if hw:
        try:
            driver.execute_cdp_cmd('Emulation.setHardwareConcurrencyOverride',
                                   {'hardwareConcurrency': hw['cores']})
            done.append(f"CPU {hw['cores']} nhân")
        except Exception as e:
            if log_fn:
                log_fn('warn', f'Không áp được số nhân CPU: {e}')
    win = aspect_value(profile_id, 'window')
    if win:
        try:
            wid = driver.execute_cdp_cmd('Browser.getWindowForTarget', {}).get('windowId')
            driver.execute_cdp_cmd('Browser.setWindowBounds', {
                'windowId': wid, 'bounds': {'windowState': 'normal', 'width': win['w'],
                                            'height': win['h'], 'left': 20, 'top': 20}})
            done.append(f"cửa sổ {win['w']}x{win['h']}")
        except Exception as e:
            if log_fn:
                log_fn('warn', f'Không đổi được kích thước cửa sổ: {e}')
    return done


def _fingerprint_seed(profile_id) -> int:
    v = aspect_value(profile_id, 'seed')
    if v is not None:
        return v
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
