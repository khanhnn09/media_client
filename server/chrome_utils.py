"""Chrome/chromedriver helpers: detect binary, build options, attach/connect tới
Chrome đang mở, phát hiện Chrome Portable. Import selenium/undetected_chromedriver
đều LAZY bên trong từng hàm (giữ nguyên từ bản gốc) — không cần top-level import ở đây."""

import os
from pathlib import Path

from .config import _ROOT, PROFILES_DIR, CHROMEDRIVER_PATH, CHROME_BINARY, log
from .managers import em
from .proxy_config import build_proxy_setup


CHROME_DEBUG_PORT_BASE = int(os.getenv('CHROME_DEBUG_PORT_BASE', 9300))
CHROME_PORTABLE_DIR    = os.getenv('CHROME_PORTABLE_DIR', str(_ROOT / 'data' / 'portables'))

_SELENIUM_POOL_MAXSIZE = int(os.getenv('SELENIUM_POOL_MAXSIZE', 30))
_selenium_pool_patched = False


def _patch_selenium_pool_size(maxsize: int = _SELENIUM_POOL_MAXSIZE):
    """(2026-08-20) Tăng maxsize của urllib3 connection pool mà Selenium dùng để
    nói chuyện với chromedriver cục bộ (`http://localhost:{port}`) — mặc định
    urllib3 chỉ giữ tối đa 10 connection/pool (`HTTPConnectionPool(maxsize=10,
    block=False)`). Batch VEO ảnh/video chạy LÂU bắn RẤT NHIỀU lệnh DOM/CDP liên
    tiếp qua CÙNG 1 phiên driver (đặc biệt các vòng poll dồn dập như
    `_wait_js()`/round-robin Gemini `gemini_tab_switch_interval`) — khi có lúc
    >10 connection cùng "đang dùng" (chưa kịp trả về pool) tại 1 thời điểm,
    urllib3 tự DISCARD connection thừa khi trả về (`block=False` → không chờ,
    chỉ log warning rồi vứt) thay vì tái sử dụng — sinh cảnh báo lặp lại
    "Connection pool is full, discarding connection: localhost. Connection pool
    size: 10" suốt batch. Về chức năng VÔ HẠI (Selenium tự mở connection mới khi
    cần) nhưng tốn chi phí handshake TCP lặp lại không cần thiết.

    Monkeypatch `RemoteConnection._get_connection_manager()` (dùng CHUNG bởi CẢ
    `webdriver.Chrome()` LẪN `undetected_chromedriver.Chrome()` — cả 2 đều kế
    thừa `ChromiumRemoteConnection`→`RemoteConnection`, không cần patch riêng
    từng loại) — để nguyên hàm GỐC chạy trọn vẹn (giữ đúng mọi nhánh xử lý
    proxy/cert/timeout đã có) rồi CHỈ mutate `PoolManager.connection_pool_kw`
    của kết quả trả về TRƯỚC KHI bất kỳ pool con nào (theo host/port) được tạo
    — `connection_pool_kw` là dict THUẦN được `urllib3.PoolManager` dùng LƯỜI
    mỗi khi cần tạo 1 `HTTPConnectionPool` con mới, nên mutate ở đây áp dụng cho
    toàn bộ vòng đời phiên driver mà không cần biết chi tiết cách hàm gốc build
    `PoolManager`. Idempotent (patch 1 lần/process qua `_selenium_pool_patched`)
    — gọi lại nhiều lần từ `_make_driver()`/`_connect_to_chrome()` an toàn."""
    global _selenium_pool_patched
    if _selenium_pool_patched:
        return
    try:
        from selenium.webdriver.remote.remote_connection import RemoteConnection
        _orig_get_connection_manager = RemoteConnection._get_connection_manager

        def _patched_get_connection_manager(self):
            pool = _orig_get_connection_manager(self)
            try:
                pool.connection_pool_kw['maxsize'] = maxsize
                pool.connection_pool_kw.setdefault('block', False)
            except Exception:
                pass
            return pool

        RemoteConnection._get_connection_manager = _patched_get_connection_manager
        _selenium_pool_patched = True
        log.info(f'[chrome-pool-patch] Selenium urllib3 connection pool maxsize → {maxsize}')
    except Exception as e:
        log.warning(f'[chrome-pool-patch] Không patch được connection pool size: {e}')


def _chrome_debug_port(profile_id: int) -> int:
    """Port debug riêng cho mỗi profile (9300–9499). Không đụng nhau."""
    return CHROME_DEBUG_PORT_BASE + (profile_id % 200)


def _is_snap_wrapper(path: str) -> bool:
    """
    True nếu `path` là wrapper của snap (vd /snap/bin/chromium → /usr/bin/snap).
    KHÔNG được set làm binary_location: chromedriver (chạy trong sandbox snap) không
    execvp được snap khác → 'Chrome instance exited'. Với snap phải để trống
    binary_location, snap chromedriver sẽ tự chạy chrome nội bộ của chính snap.
    """
    if not path:
        return False
    if path.startswith('/snap/'):
        return True
    try:
        return os.path.realpath(path) == '/usr/bin/snap'
    except Exception:
        return False


def _detect_chrome_binary() -> str:
    """
    Tìm binary Chrome/Chromium để set opts.binary_location.
    Ưu tiên env CHROME_BINARY; sau đó dò các đường dẫn phổ biến theo OS.
    BỎ QUA binary bản snap (wrapper) — xem _is_snap_wrapper(). Trả '' nếu không có
    binary "thật" (để snap chromedriver tự dò chrome nội bộ, hoặc Selenium tự dò).
    """
    if CHROME_BINARY and Path(CHROME_BINARY).exists():
        return CHROME_BINARY  # user tự chỉ định → tôn trọng (kể cả nếu là snap, tự chịu)
    if os.name == 'nt':
        return ''  # Windows: để Selenium tự dò trong registry / PATH
    # Linux / macOS: dò trong PATH rồi tới các vị trí cài đặt phổ biến (bỏ snap wrapper)
    import shutil
    names = [
        'google-chrome', 'google-chrome-stable', 'google-chrome-beta',
        'chromium', 'chromium-browser',
    ]
    for name in names:
        found = shutil.which(name)
        if found and not _is_snap_wrapper(found):
            return found
    fixed_paths = [
        '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium', '/usr/bin/chromium-browser',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ]
    for p in fixed_paths:
        if Path(p).exists() and not _is_snap_wrapper(p):
            return p
    return ''


def _win_file_version(path: str) -> str | None:
    """Đọc FileVersion embedded trong resource PE của 1 file .exe (Windows) qua
    Win32 API (`version.dll`, ctypes — KHÔNG cần thêm dependency pywin32). KHÔNG
    chạy file — tránh hẳn vấn đề đã verify thật: `chrome.exe --version` qua
    subprocess TREO/timeout 10s trên Windows dù file tồn tại và hợp lệ (khác hành
    vi trên Linux/macOS, nơi `--version` chạy nhanh và không có vấn đề này)."""
    import ctypes
    from ctypes import wintypes
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr  = ctypes.c_void_p()
        plen = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(buf, '\\', ctypes.byref(ptr), ctypes.byref(plen)):
            return None

        class _VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ('dwSignature',        wintypes.DWORD), ('dwStrucVersion',     wintypes.DWORD),
                ('dwFileVersionMS',    wintypes.DWORD), ('dwFileVersionLS',    wintypes.DWORD),
                ('dwProductVersionMS', wintypes.DWORD), ('dwProductVersionLS', wintypes.DWORD),
                ('dwFileFlagsMask',    wintypes.DWORD), ('dwFileFlags',        wintypes.DWORD),
                ('dwFileOS',           wintypes.DWORD), ('dwFileType',         wintypes.DWORD),
                ('dwFileSubtype',      wintypes.DWORD), ('dwFileDateMS',       wintypes.DWORD),
                ('dwFileDateLS',       wintypes.DWORD),
            ]
        info = ctypes.cast(ptr, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f'{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}'
    except Exception as e:
        log.warning(f'[chrome_utils] Không đọc được FileVersion của "{path}": {e}')
        return None


def _win_resolve_chrome_exe() -> str:
    """Tìm đường dẫn chrome.exe THẬT sẽ được Chrome/Selenium launch trên Windows
    khi KHÔNG chỉ định portable_exe/CHROME_BINARY (`_build_chrome_options()`'s
    nhánh mặc định để trống `binary_location`, để Windows/Selenium tự dò).

    2026-09-12, fix bug thật: `_detect_chrome_version()` bản cũ trên Windows
    KHÔNG có `binary_path` (Chrome hệ thống) nên chỉ đọc registry BLBeacon —
    giá trị này là Chrome TỰ GHI LẠI mỗi lần launch, không đồng bộ với
    auto-updater: Chrome auto-update thay THẲNG file nhị phân trên đĩa trong
    lúc process cũ (nếu còn chạy) vẫn dùng bản code cũ trong RAM, BLBeacon chỉ
    được ghi lại ở lần launch KẾ TIẾP — nếu lần launch kế tiếp đó CHÍNH LÀ lần
    worker này gọi `_detect_chrome_version()` (đọc registry TRƯỚC khi Chrome
    mới kịp khởi động và tự cập nhật), giá trị đọc được vẫn là version CŨ dù
    file nhị phân trên đĩa đã là bản MỚI — ép uc dùng `version_main` sai, launch
    Chrome (bản MỚI thật) bằng chromedriver khớp bản CŨ → "session not created:
    ChromeDriver only supports Chrome version X, Current browser version is Y"
    (X < Y, khớp đúng log thật gặp: driver 151, Chrome đã tự cập nhật 153).

    Đọc THẲNG version embedded trong CHÍNH file .exe sẽ launch (như đã làm cho
    Chrome Portable từ trước) loại bỏ hoàn toàn độ trễ này — file trên đĩa LUÔN
    phản ánh đúng bản sẽ chạy, không phụ thuộc lần Chrome chạy gần nhất.

    Ưu tiên các đường dẫn cài đặt CỐ ĐỊNH theo ĐÚNG thứ tự chromedriver tự tìm
    binary khi `binary_location` không set (`LOCALAPPDATA` trước — bản cài
    per-user không cần quyền admin, phổ biến nhất hiện nay — rồi tới
    `PROGRAMFILES`/`PROGRAMFILES(X86)`, bản cài toàn máy). Registry "App Paths"
    (`...\\App Paths\\chrome.exe`) CHỈ dùng làm fallback CUỐI CÙNG khi không có
    đường dẫn cố định nào tồn tại — **⚠️ đã verify trên máy thật đây KHÔNG PHẢI
    nguồn tin cậy**: key này có thể bị 1 bản cài Chrome KHÁC (vd Chrome Portable
    của tool khác) ghi đè, trỏ sang 1 exe HOÀN TOÀN KHÁC với Chrome thật sự được
    chromedriver/uc launch (verify trực tiếp: máy test có App Paths trỏ về 1
    Chrome Portable version 136 trong khi Chrome hệ thống THẬT SỰ được launch —
    và khớp đúng crash log — là bản 153 tại `Program Files`). Trả '' nếu không
    tìm thấy (an toàn — caller tự fallback về registry BLBeacon)."""
    for env_var, suffix in (
        ('LOCALAPPDATA',      r'Google\Chrome\Application\chrome.exe'),
        ('PROGRAMFILES',      r'Google\Chrome\Application\chrome.exe'),
        ('PROGRAMFILES(X86)', r'Google\Chrome\Application\chrome.exe'),
    ):
        base = os.environ.get(env_var)
        if base:
            candidate = Path(base) / suffix
            if candidate.exists():
                return str(candidate)

    try:
        import winreg
        for hive, subkey in (
            (winreg.HKEY_CURRENT_USER,
             r'Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'),
            (winreg.HKEY_LOCAL_MACHINE,
             r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'),
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, '')  # default value
                    if path and Path(path).exists():
                        return path
            except FileNotFoundError:
                continue
    except Exception as e:
        log.warning(f'[chrome_utils] Không đọc được registry App Paths cho chrome.exe: {e}')

    return ''


def _detect_chrome_version(binary_path: str = '') -> int | None:
    """Dò MAJOR version của Chrome THẬT SỰ sẽ được launch (binary_path nếu có —
    vd Chrome Portable — hoặc Chrome hệ thống mặc định), để truyền `version_main`
    cho `uc.Chrome()` (2026-07-20, fix lỗi thật đã gặp: 'This version of
    ChromeDriver only supports Chrome version 151, Current browser version is
    150.0.7871.125').

    KHÔNG liên quan `_detect_chromedriver()`/Selenium Manager — khi
    `driver_executable_path=None`, `undetected_chromedriver` tự lo driver bằng cơ
    chế RIÊNG của nó (tải + patch chromedriver theo version nó tự đoán), tách biệt
    hoàn toàn khỏi Selenium Manager (>=4.6, chỉ áp dụng cho `Service()` thường).
    uc đôi khi đoán SAI (vd lấy chromedriver bản mới nhất thay vì đúng bản khớp
    Chrome cài trên máy) → tải nhầm driver không khớp browser thật, launch fail.
    Truyền `version_main` ép uc dùng ĐÚNG version đã cài, bỏ qua bước tự đoán.

    Trả None nếu không dò được — an toàn, uc quay lại tự đoán như cũ (không tệ
    hơn hành vi trước khi có hàm này)."""
    import re

    if binary_path and Path(binary_path).exists():
        if os.name == 'nt':
            # Đọc version embedded trong .exe — KHÔNG chạy file (xem _win_file_version).
            ver = _win_file_version(binary_path)
            if ver:
                m = re.match(r'(\d+)\.', ver)
                if m:
                    return int(m.group(1))
        else:
            # Linux/macOS: `--version` chạy nhanh, không có vấn đề treo như Windows.
            import subprocess
            try:
                out = subprocess.run([binary_path, '--version'], capture_output=True,
                                      text=True, timeout=10)
                m = re.search(r'(\d+)\.\d+\.\d+\.\d+', out.stdout or out.stderr or '')
                if m:
                    return int(m.group(1))
            except Exception as e:
                log.warning(f'[chrome_utils] Không lấy được version qua "{binary_path} --version": {e}')

    if os.name == 'nt':
        # Không có binary_path cụ thể (Chrome hệ thống mặc định, KHÔNG phải
        # Portable) — (2026-09-12) ưu tiên đọc THẲNG FileVersion của chính
        # chrome.exe sẽ launch (xem _win_resolve_chrome_exe()) — đáng tin cậy
        # hơn registry BLBeacon vì không có độ trễ so với bản nhị phân THẬT
        # trên đĩa sau khi Chrome auto-update.
        exe = _win_resolve_chrome_exe()
        if exe:
            ver = _win_file_version(exe)
            if ver:
                m = re.match(r'(\d+)\.', ver)
                if m:
                    return int(m.group(1))

        # Fallback: registry BLBeacon (Chrome tự ghi version thật vào đây mỗi
        # lần chạy) — chỉ dùng khi không resolve được đường dẫn chrome.exe ở
        # trên. Dò cả HKCU (cài riêng user) lẫn HKLM (cài toàn máy, 32/64-bit).
        try:
            import winreg
            for hive, subkey in (
                (winreg.HKEY_CURRENT_USER,  r'Software\Google\Chrome\BLBeacon'),
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Google\Chrome\BLBeacon'),
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon'),
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        version, _ = winreg.QueryValueEx(key, 'version')
                        m = re.match(r'(\d+)\.', version)
                        if m:
                            return int(m.group(1))
                except FileNotFoundError:
                    continue
        except Exception as e:
            log.warning(f'[chrome_utils] Không đọc được Chrome version từ registry: {e}')

    return None


def _chromedriver_major_version(path: str) -> int | None:
    """Dò MAJOR version của CHÍNH file chromedriver tại `path` (KHÁC
    `_detect_chrome_version()`, vốn dò version của Chrome BROWSER) — dùng để phát
    hiện `_detect_chromedriver()` đang trả về 1 bản chromedriver CŨ/STALE còn sót
    lại từ lần cài trước (khớp Chrome bản cũ, không khớp Chrome hiện tại sau khi
    tự auto-update).

    2026-09-12 (b), fix bug thật: `_make_driver()` gọi `uc.Chrome(driver_executable_
    path=cdp_path, version_main=chrome_ver)` — nhưng khi `driver_executable_path`
    KHÔNG rỗng, `undetected_chromedriver.Patcher.auto()` (xem `patcher.py`, nhánh
    `if self._custom_exe_path: ...`) CHỈ kiểm tra file đó đã được "patch" (chống
    bot-detection) hay chưa rồi DÙNG NGUYÊN FILE — HOÀN TOÀN BỎ QUA `version_main`,
    không hề tải lại driver khớp version mới. Nếu `_detect_chromedriver()` (Windows,
    mặc định đọc `CHROMEDRIVER_PATH` = `data/profiles/chromedriver/chromedriver.exe`)
    trả về 1 file CÒN TỒN TẠI từ lần cài Chrome trước (vd version 151), việc fix
    `_detect_chrome_version()` đọc đúng Chrome hiện tại (153) trở nên VÔ NGHĨA —
    uc vẫn khởi động bằng đúng driver 151 cũ, tái diễn y hệt lỗi 'This version of
    ChromeDriver only supports Chrome version 151, Current browser version is
    153...'. Trả None nếu không đọc được (an toàn — caller coi như "không rõ",
    KHÔNG tự ý bỏ qua path đã cấu hình)."""
    import re
    if os.name == 'nt':
        ver = _win_file_version(path)
        if ver:
            m = re.match(r'(\d+)\.', ver)
            if m:
                return int(m.group(1))
        return None
    import subprocess
    try:
        out = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=10)
        m = re.search(r'(\d+)\.\d+\.\d+\.\d+', out.stdout or out.stderr or '')
        if m:
            return int(m.group(1))
    except Exception as e:
        log.warning(f'[chrome_utils] Không lấy được version của chromedriver "{path}": {e}')
    return None


def _detect_chrome_portables() -> list[dict]:
    """
    Scan CHROME_PORTABLE_DIR tìm Chrome Portable instances.
    Cấu trúc: portables/1/App/Chrome-bin/chrome.exe  (Windows)
              portables/1/chrome-linux/chrome         (Linux)
    Trả list[{index, dir, exe, user_data}] sắp xếp theo index.
    """
    result = []
    base = Path(CHROME_PORTABLE_DIR)
    if not base.exists():
        return result
    for item in sorted(base.iterdir()):
        if not item.is_dir():
            continue
        # Tìm binary Chrome ở các vị trí phổ biến (Windows .exe + Linux/macOS)
        candidates = [
            item / 'App' / 'Chrome-bin' / 'chrome.exe',
            item / 'App' / 'Chrome'     / 'chrome.exe',
            item / 'GoogleChromePortable.exe',
            item / 'chrome-linux' / 'chrome',
            item / 'App' / 'Chrome-bin' / 'chrome',
            item / 'chrome',
        ]
        exe = next((str(c) for c in candidates if c.exists()), None)
        if exe:
            user_data = str(item / 'UserData')
            os.makedirs(user_data, exist_ok=True)
            result.append({
                'index':     item.name,
                'dir':       str(item),
                'exe':       exe,
                'user_data': user_data,
            })
    return result


def _usable_profile_dir(profile: dict) -> str:
    """
    Trả về profile_dir dùng được trên OS hiện tại.
    Nếu path lưu trong DB thuộc OS khác (vd profile tạo trên Windows lưu 'D:\\...'
    rồi chạy lại trên Linux) → remap về PROFILES_DIR/<profile_name>, tạo thư mục nếu chưa có.
    Tránh lỗi chromium exit ngay do --user-data-dir không hợp lệ.
    """
    pd   = (profile.get('profile_dir') or '').strip()
    name = profile.get('profile_name') or f"profile_{profile.get('id', 'x')}"
    is_win_path = ('\\' in pd) or (len(pd) >= 2 and pd[1] == ':' and pd[0].isalpha())
    if os.name != 'nt' and is_win_path:
        pd = ''  # path Windows trên POSIX → không dùng được
    elif os.name == 'nt' and pd.startswith('/'):
        pd = ''  # path POSIX trên Windows → không dùng được
    if not pd:
        pd = str(Path(PROFILES_DIR) / name)
    try:
        os.makedirs(pd, exist_ok=True)
    except Exception:
        pd = str(Path(PROFILES_DIR) / name)
        os.makedirs(pd, exist_ok=True)
    return pd


def _build_chrome_options(profile_dir: str,
                           debug_port:  int  = 0,
                           portable_exe: str = '',
                           load_extensions: bool = True,
                           detach: bool = False,
                           use_uc: bool = False,
                           proxy: str = '',
                           profile_id=None):
    """Tạo ChromeOptions chuẩn.
    use_uc=True: dùng uc.ChromeOptions (bỏ experimental options mà uc tự quản lý).

    `proxy` (2026-09-05) — chuỗi proxy RIÊNG của profile (`selenium_profiles.
    proxy_server`, nhập ở ProfileDialog). Rỗng = đi thẳng như trước. Đây là điểm
    áp dụng DUY NHẤT của proxy trong toàn bộ client_tool: mọi đường mở Chrome
    (worker, login browser, harness test) đều gọi hàm này. `profile_id` chỉ dùng
    để tách thư mục extension proxy-auth theo profile — xem proxy_config.py.
    """
    if use_uc:
        try:
            import undetected_chromedriver as uc
            opts = uc.ChromeOptions()
        except ImportError:
            from selenium.webdriver.chrome.options import Options
            opts = Options()
    else:
        from selenium.webdriver.chrome.options import Options
        opts = Options()

    if portable_exe:
        opts.binary_location = portable_exe
    else:
        # Linux/macOS: chỉ định binary Chromium/Chrome nếu dò được
        # (Windows để trống → Selenium tự dò trong registry).
        _bin = _detect_chrome_binary()
        if _bin:
            opts.binary_location = _bin

    opts.add_argument(f'--user-data-dir={profile_dir}')
    opts.add_argument('--profile-directory=Default')

    if debug_port:
        opts.add_argument(f'--remote-debugging-port={debug_port}')

    # Session / startup
    opts.add_argument('--no-sandbox')
    opts.add_argument('--no-restore-last-session')
    opts.add_argument('--no-first-run')
    opts.add_argument('--no-default-browser-check')
    opts.add_argument('--disable-session-crashed-bubble')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-infobars')
    opts.add_argument('--disable-breakpad')

    # GPU / Rendering — tránh lỗi GLES context khi chạy headless/background
    opts.add_argument('--disable-gpu')
    opts.add_argument('--disable-gpu-compositing')
    opts.add_argument('--disable-software-rasterizer')
    opts.add_argument('--disable-features=VizDisplayCompositor')
    opts.add_argument('--disable-gpu-sandbox')
    opts.add_argument('--disable-accelerated-2d-canvas')
    opts.add_argument('--disable-accelerated-video-decode')

    # Background throttling — tránh tab bị throttle khi không focus
    opts.add_argument('--disable-renderer-backgrounding')
    opts.add_argument('--disable-background-timer-throttling')
    opts.add_argument('--disable-backgrounding-occluded-windows')
    opts.add_argument('--disable-features=CalculateNativeWinOcclusion')

    # Automation hiding
    opts.add_argument('--disable-blink-features=AutomationControlled')
    if not use_uc:
        # uc tự patch chromedriver binary và set các flag này — thêm thủ công sẽ conflict
        opts.add_experimental_option('excludeSwitches', ['enable-automation'])
        opts.add_experimental_option('useAutomationExtension', False)

    # Prefs — tắt popup password manager + notifications
    opts.add_experimental_option('prefs', {
        'credentials_enable_service':                              False,
        'profile.password_manager_enabled':                        False,
        'profile.default_content_setting_values.notifications':   2,
    })

    if detach:
        # Chrome sống khi driver.quit() — dùng khi login browser cần giữ session
        opts.add_experimental_option('detach', True)

    # Performance logging để capture Authorization headers + browser console logs
    opts.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})

    opts.add_argument('--window-size=1280,800')
    opts.add_argument('--log-level=3')

    ext_paths = list(em.active_paths()) if load_extensions else []

    # Proxy riêng của profile (2026-09-05). Extension proxy-auth (nếu proxy có
    # user/pass) phải nạp KỂ CẢ khi load_extensions=False — cờ đó chỉ để tắt
    # extension Flow cho các worker_mode gemini/chatgpt (tránh chúng tự
    # heartbeat/thao tác DOM song song), hoàn toàn không liên quan tới proxy.
    proxy_setup = build_proxy_setup(proxy, profile_id)
    for a in proxy_setup['args']:
        opts.add_argument(a)
    ext_paths.extend(proxy_setup['ext_paths'])
    if proxy_setup['display']:
        log.info(f'[proxy] profile={profile_id} → {proxy_setup["display"]}')

    if ext_paths:
        opts.add_argument('--load-extension=' + ','.join(ext_paths))

    return opts


def _detect_chromedriver() -> str:
    """
    Tìm chromedriver khớp Chrome/Chromium.
    Ưu tiên CHROMEDRIVER_PATH (nếu tồn tại) → PATH → snap/hệ thống.
    Trả '' nếu không thấy (để Selenium Manager tự tải — CHỈ hoạt động trên x86_64;
    trên aarch64 Google không có bản driver nên bắt buộc phải dò được ở đây).
    """
    if CHROMEDRIVER_PATH and Path(CHROMEDRIVER_PATH).exists():
        return CHROMEDRIVER_PATH
    if os.name == 'nt':
        return ''  # Windows: default .exe đã set ở trên; tới đây thì để Selenium tự dò
    import shutil
    found = shutil.which('chromedriver')
    if found:
        return found
    fixed_paths = [
        '/snap/bin/chromium.chromedriver',            # Ubuntu snap chromium (kể cả aarch64)
        '/usr/bin/chromedriver',
        '/usr/lib/chromium-browser/chromedriver',
        '/usr/lib/chromium/chromedriver',
    ]
    for p in fixed_paths:
        if Path(p).exists():
            return p
    return ''


def _running_chrome_major(debug_port: int, timeout: float = 1.5) -> int | None:
    """MAJOR version của Chrome ĐANG CHẠY ở cổng DevTools này (đọc `/json/version`,
    field `Browser` dạng "Chrome/153.0.8010.37"). Chính xác nhất cho trường hợp
    ATTACH — đúng bản đang chạy thật, kể cả khi đó là Chrome Portable."""
    import json as _json
    import re
    import urllib.request
    try:
        with urllib.request.urlopen(
                f'http://127.0.0.1:{debug_port}/json/version', timeout=timeout) as r:
            browser = _json.loads(r.read()).get('Browser') or ''
        m = re.search(r'/(\d+)\.', browser)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _make_service(chrome_binary: str = '', debug_port: int = 0):
    """Service cho `webdriver.Chrome()` thường (login browser, fallback của worker,
    attach qua debuggerAddress).

    ⚠️ (2026-09-12 (d), fix lỗi thật "mở login profile bị lỗi": 'This version of
    ChromeDriver only supports Chrome version 151, Current browser version is
    153'). Trước đây trả THẲNG file `CHROMEDRIVER_PATH` nếu tồn tại, KHÔNG kiểm
    tra version — Chrome tự auto-update lên bản mới thì file cục bộ thành STALE
    và MỌI đường dùng hàm này đều chết. Bản vá (c) hôm trước chỉ sửa nhánh
    `uc.Chrome` trong `_make_driver()`, bỏ sót các đường đi qua đây.

    Giờ: so MAJOR version của file chromedriver cục bộ với Chrome sẽ chạy
    (`debug_port` → Chrome đang chạy thật; không có thì `chrome_binary`/Chrome
    hệ thống). Lệch → bỏ qua file cũ, trả `Service()` trống để Selenium Manager
    (>= 4.6) tự tải ĐÚNG bản khớp (theo `options.binary_location` nếu có, nên
    Portable cũng đúng). KHÔNG áp dụng trên aarch64 — Google không có bản
    chromedriver ARM, Selenium Manager vô dụng, file hệ thống/snap là lựa chọn
    duy nhất. Không dò được version → giữ nguyên file cục bộ như cũ."""
    import platform
    from selenium.webdriver.chrome.service import Service
    drv = _detect_chromedriver()
    if not drv:
        return Service()
    if platform.machine().lower() in ('aarch64', 'arm64'):
        return Service(executable_path=drv)

    want = _running_chrome_major(debug_port) if debug_port else None
    if want is None:
        want = _detect_chrome_version(chrome_binary)
    have = _chromedriver_major_version(drv) if want else None
    if want and have and have != want:
        log.warning(f'[chrome_utils] chromedriver cục bộ "{drv}" là bản {have}, KHÔNG '
                    f'khớp Chrome {want} — bỏ qua, để Selenium Manager tự tải đúng bản.')
        return Service()
    return Service(executable_path=drv)


def _chrome_alive_on_port(debug_port: int, timeout: float = 1.5) -> bool:
    """Có Chrome nào ĐANG chạy và mở DevTools ở cổng này không?

    ⚠️ (2026-09-04, fix bug "tự mở 1 đống tab") Trước đây nơi DUY NHẤT biết
    "Chrome của profile này đã mở chưa" là dict RAM `_login_drivers`. Nhưng
    login browser mở với `detach=True` (Chrome SỐNG TIẾP sau khi driver kết
    thúc — xem log "Chrome có thể vẫn còn mở"), rồi `finally` lại
    `_login_drivers.pop(pid)`; dict cũng mất sạch khi restart client_tool.
    Mất dấu vết ⇒ lần sau `_make_driver()`/route `/open` LAUNCH Chrome MỚI với
    CÙNG `--user-data-dir`. Chrome không khởi động instance thứ 2 cho cùng 1
    user-data-dir — nó chuyển yêu cầu sang instance đang chạy, instance đó MỞ
    THÊM 1 CỬA SỔ/TAB rồi tiến trình vừa gọi thoát. Lặp lại ⇒ một đống tab.

    Hỏi THỰC TẾ (DevTools endpoint) thay vì hỏi RAM thì không còn mất dấu."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
                f'http://127.0.0.1:{debug_port}/json/version', timeout=timeout) as r:
            return bool(_json.loads(r.read()).get('Browser'))
    except Exception:
        return False


def _chrome_procs_for_profile(profile_dir: str) -> list:
    """Danh sách tiến trình Chrome (process CHÍNH, không phải renderer/gpu) đang
    giữ đúng `--user-data-dir` này."""
    profile_dir = os.path.normcase(os.path.abspath(str(profile_dir or '').strip()))
    if not profile_dir:
        return []
    try:
        import psutil
    except ImportError:
        return []
    found = []
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            if (proc.info.get('name') or '').lower() not in ('chrome.exe', 'chrome', 'chromium'):
                continue
            cmdline = proc.info.get('cmdline') or ()
            # bỏ qua tiến trình con (renderer/gpu/utility) — chúng cũng mang
            # `--user-data-dir` nhưng đóng chúng không giải phóng được profile
            if any(a.startswith('--type=') for a in cmdline):
                continue
            for arg in cmdline:
                if not arg.startswith('--user-data-dir='):
                    continue
                got = os.path.normcase(os.path.abspath(arg.split('=', 1)[1].strip('"')))
                if got == profile_dir:
                    found.append(proc)
                    break
        except Exception:
            continue
    return found


def _kill_chrome_for_profile(profile_dir: str, log_fn=None) -> int:
    """Đóng CƯỠNG BỨC Chrome mồ côi đang giữ `--user-data-dir` này (kèm tiến
    trình con). Trả số process đã đóng.

    Chỉ dùng khi Chrome đó KHÔNG attach được — để lại thì lần launch kế tiếp
    sẽ bị Chrome chuyển sang instance cũ và đẻ thêm cửa sổ/tab."""
    procs = _chrome_procs_for_profile(profile_dir)
    if not procs:
        return 0
    try:
        import psutil
    except ImportError:
        return 0
    targets = []
    for p in procs:
        try:
            targets.extend(p.children(recursive=True))
        except Exception:
            pass
        targets.append(p)
    for p in targets:
        try:
            p.kill()
        except Exception:
            pass
    try:
        psutil.wait_procs(targets, timeout=5)
    except Exception:
        pass
    if log_fn:
        log_fn('warn', f'Đã đóng {len(procs)} Chrome mồ côi đang giữ profile này '
                       f'(không attach được) — tránh Chrome đẻ thêm tab vào cửa sổ cũ')
    return len(procs)


def _chrome_running_for_profile(profile_dir: str) -> bool:
    """Có tiến trình Chrome nào ĐANG giữ đúng `--user-data-dir` này không?

    Cần bên cạnh `_chrome_alive_on_port()` vì worker mở Chrome qua
    `undetected_chromedriver` — uc KHÔNG giữ `--remote-debugging-port` mở như
    `webdriver.Chrome` thường (đã ĐO THỰC TẾ: process có flag trong command
    line nhưng cổng không lắng nghe), nên probe theo cổng bỏ sót đúng trường
    hợp Chrome của worker còn sống.

    Đây là điều kiện THẬT quyết định Chrome có "đẻ thêm tab" hay không: 2 lần
    launch cùng 1 `--user-data-dir` thì lần sau KHÔNG tạo instance mới mà
    chuyển yêu cầu sang instance đang chạy, instance đó mở thêm cửa sổ/tab."""
    return bool(_chrome_procs_for_profile(profile_dir))


def _connect_to_chrome(debug_port: int, log_fn=None):
    """
    Attach Selenium vào Chrome đang chạy qua remote debugging port.
    Tương tự connect_to_existing_chrome() trong AutoImage/selenium_manager.py.
    Trả WebDriver hoặc None nếu không kết nối được.
    log_fn(level, msg) — nếu None dùng global log.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import WebDriverException, SessionNotCreatedException

    _patch_selenium_pool_size()

    def _emit(level: str, msg: str):
        if log_fn:
            log_fn(level, msg)
        else:
            (log.warning if level in ('warn', 'error') else log.info)(f'[chrome-attach] {msg}')

    try:
        opts = Options()
        opts.add_experimental_option('debuggerAddress', f'127.0.0.1:{debug_port}')
        # goog:loggingPrefs là capability của phiên WebDriver — vẫn cần khi ATTACH
        # (Chrome đã mở). Thiếu → get_log('performance') fail, không bắt được
        # x-browser-validation / sessionId. Mirror tests/utils/flow_session.py.
        opts.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})
        driver = webdriver.Chrome(service=_make_service(debug_port=debug_port), options=opts)

        # Kiểm tra liveness với timeout riêng (Chrome có thể đang load)
        import threading as _th
        result: dict = {'url': None, 'done': False}

        def _get():
            try:
                result['url']  = driver.current_url
                result['done'] = True
            except Exception:
                result['done'] = True

        t = _th.Thread(target=_get, daemon=True)
        t.start()
        t.join(timeout=4)

        if result['done']:
            _emit('info', f'chrome-attach port={debug_port} url={result["url"] or "unknown"}')
            return driver
        else:
            _emit('warn', f'chrome-attach timeout getting URL, port={debug_port} — dùng anyway')
            return driver

    except (WebDriverException, SessionNotCreatedException) as e:
        _emit('warn', f'chrome-attach failed port={debug_port}: {e}')
        return None
    except Exception as e:
        _emit('warn', f'chrome-attach error port={debug_port}: {e}')
        return None

