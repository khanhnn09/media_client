"""Cấu hình + hằng số dùng chung cho toàn bộ package server/ — PORT, đường dẫn,
FLOW_SERVER, settings mặc định, logging, Flask app instance, _profile_log()."""

import os, logging
from pathlib import Path
from datetime import datetime

from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
import requests as req_lib

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
# (2026-07-17) client_tool KHÔNG còn kết nối DB trực tiếp — mọi thao tác DB (profile/
# extension/log) giờ qua HTTP API trên backend (`server/managers.py::_api()`,
# `backend/routes/worker_profiles.py`). Không còn cần import db.py (dù trực tiếp
# lẫn vendor cục bộ) — xem CHANGELOG "client_tool: bỏ kết nối DB trực tiếp".
# _ROOT chỉ dùng để tính PROFILES_DIR/CHROMEDRIVER_PATH mặc định: nếu client_tool/
# vẫn đang nằm trong repo ToolSub (còn thấy ToolSub/db.py ở 1 cấp cha — file đó giờ
# chỉ dùng bởi whisperx_server.py/backend/, KHÔNG phải client_tool) → dùng
# ToolSub/data/profiles (share chung với các tool khác trong repo, hành vi cũ). Nếu
# client_tool/ đã được copy standalone sang máy khác (không có ToolSub/db.py) → tự
# dùng client_tool/data/profiles cục bộ.
_this_dir = Path(__file__).parent.parent
_ROOT = _this_dir.parent if (_this_dir.parent / 'db.py').exists() else _this_dir

PORT              = int(os.getenv('SELENIUM_PORT',       13445))
PROFILES_DIR      = os.getenv('SELENIUM_PROFILES_DIR',  str(_ROOT / 'data' / 'profiles'))
# chromedriver tên khác nhau theo OS (Windows: .exe). Nếu file không tồn tại,
# _make_service() sẽ tự fallback về Selenium Manager (tự tải driver phù hợp).
_CHROMEDRIVER_NAME = 'chromedriver.exe' if os.name == 'nt' else 'chromedriver'
CHROMEDRIVER_PATH = os.getenv('CHROMEDRIVER_PATH',       str(_ROOT / 'data' / 'profiles' / 'chromedriver' / _CHROMEDRIVER_NAME))
# Binary Chrome/Chromium (Linux/macOS thường cần chỉ định, Windows để trống → Selenium tự dò)
CHROME_BINARY     = os.getenv('CHROME_BINARY', '').strip()
# aarch64/arm64: Google KHÔNG phát hành Chrome lẫn chromedriver cho linux-arm →
# Selenium Manager và undetected_chromedriver đều không tự tải được driver.
# Phải dùng chromium + chromedriver do hệ điều hành/snap cung cấp (khớp version).
import platform as _platform
_IS_ARM = _platform.machine().lower() in ('aarch64', 'arm64', 'armv7l', 'armv8l')
FLOW_SERVER       = os.getenv('FLOW_API_URL',            'http://localhost:13443')
POLL_INTERVAL     = int(os.getenv('SELENIUM_POLL_SECS', 8))
DEBUG             = os.getenv('DEBUG', 'false').lower() == 'true'
# LOG_KEEP (2026-07-17) — đã bỏ, retention giờ do backend tự quản (`_LOG_KEEP` trong
# backend/routes/worker_profiles.py) vì client_tool không còn ghi DB trực tiếp.

# Fallback nếu chưa từng nhận được settings từ server (heartbeat đầu tiên lỗi/chưa chạy) —
# khớp _SETTINGS_DEFAULTS trong backend/core/settings_store.py, cộng thêm 4 field veo3
# mới (error_count_before_refresh/refresh_count_before_new_project/
# max_concurrent_veo3_profiles/error_sleep_secs) — nếu server cũ chưa có 4 field này,
# worker vẫn chạy được với default hợp lý ở đây thay vì crash vì thiếu key.
_DEFAULT_SERVER_SETTINGS = {
    'error_wait_secs':      60,
    'task_delay_secs':      10,
    'download_wait_secs':   20,
    'step_delay_min_secs':  1.0,
    'step_delay_max_secs':  3.0,
    'error_patterns':       [],
    'error_count_before_refresh':      3,
    'refresh_count_before_new_project': 5,
    'max_concurrent_veo3_profiles':    1,
    'error_sleep_secs':                300,
    'error_window_minutes':            10,
    'error_window_max_errors':         5,
    # (2026-07-18) Chờ giữa mỗi vòng reconcile sau khi submit batch (refresh project
    # → đọc projectInitialData → so khớp DB) và số vòng tối đa trước khi fallback
    # tile-polling — trước đây hardcode 60/10 trong _wait_and_reconcile_tasks(), theo
    # yêu cầu user đưa ra Cài đặt để tự chỉnh (tuỳ tải máy/tốc độ mạng thực tế).
    'reconcile_wait_secs':   60,
    'reconcile_max_rounds':  10,
    # (2026-07-19) Số profile worker_mode='gemini' enabled=1 chạy đồng thời tối đa
    # khi CÓ backlog Gemini đang chờ máy (mirror max_concurrent_veo3_profiles —
    # cùng default=1, cùng ý nghĩa "trần đồng thời", KHÁC bản đầu 2026-07-18 "giữ
    # sẵn N máy luôn online bất kể có việc hay không", đã thay bằng cơ chế phản
    # ứng theo backlog thật `gemini_pending_requests`/`GET /api/gemini/pending_count`
    # sau khi user yêu cầu "giống VEO — có task thì tự bật, hết task thì tự tắt").
    # Xem dispatcher.py::_auto_scale_gemini_tick().
    'max_concurrent_gemini_profiles': 1,
    # (2026-07-20) Khung giờ KHÔNG nhận task — theo yêu cầu user "thêm setting bật
    # khung giờ không nhận task". Khi bật, `dispatcher.py::_quiet_hours_tick()`
    # (chạy mỗi 10s cùng các tick khác) tự BẬT/TẮT Master switch (dùng lại nguyên
    # `_set_master_task_intake()`, cùng cơ chế snapshot/restore đã có cho nút bấm
    # tay) ngay tại thời điểm bắt đầu/kết thúc khung giờ — không ép buộc liên tục
    # suốt khung giờ, để user vẫn tự bật lại thủ công giữa chừng nếu cần xử lý gấp
    # mà không bị hệ thống tắt lại ngay. `quiet_hours_start`/`quiet_hours_end`
    # dạng "HH:MM", hỗ trợ khung giờ vắt qua nửa đêm (vd 22:00 → 06:00).
    'quiet_hours_enabled': 0,
    'quiet_hours_start':   '22:00',
    'quiet_hours_end':     '06:00',
    # (2026-07-21) Số item media tối đa 1 project Flow chứa trước khi tự chuyển
    # sang project MỚI cho task tiếp theo — theo yêu cầu user "thêm setting số
    # task hoàn thành tối đa 1 project, đạt ngưỡng thì bấm tạo project mới".
    # Đếm qua chính danh sách media đọc được lúc reconcile
    # (`_dom_fetch_project_media()`/`flow.projectInitialData`, xem
    # `_rotate_project_if_full()`) — KHÔNG gọi API riêng chỉ để đếm. 0 = tắt
    # tính năng (không bao giờ tự chuyển).
    'max_project_media_items': 300,
    # (2026-08-13) Log ĐẦY ĐỦ request dạng lệnh `curl` copy-paste được (URL +
    # mọi header + body JSON nguyên văn) + response body ĐẦY ĐỦ (không cắt
    # 300 ký tự như log lỗi mặc định) mỗi lần gọi trực tiếp API tạo ảnh/video/
    # uploadImage (`_post_aisandbox()`, worker.py) — theo yêu cầu user "thêm
    # bật ghi lại log curl đầy đủ khi gọi tạo video/image qua api". Mặc định
    # TẮT (0) — log dài, chỉ bật tạm khi cần debug 1 request cụ thể (vd payload
    # bị 400 INVALID_ARGUMENT nhưng log gọn không đủ chi tiết để soi).
    # ⚠️ Header `authorization` KHÔNG bị che khi bật — log này có thể lộ bearer
    # token thật của phiên đang chạy, không nên chia sẻ log ra ngoài lúc đang bật.
    'debug_log_curl': 0,
    # (2026-09-04) Dùng RPC `batchexecute` (flow.google.com) làm ĐƯỜNG CHÍNH
    # cho upload/generate ảnh+video, `aisandbox` chỉ còn là DỰ PHÒNG.
    # Bật vì app Flow đã bỏ hẳn endpoint aisandbox — đường cũ 403 hàng loạt
    # (log profile 25, 2026-09-04 00:33-00:34). Đặt 0 để quay lại dùng THẲNG
    # aisandbox nếu đường mới có vấn đề (không cần sửa code).
    'generate_via_batchexecute': 1,
    # (2026-09-06) Gemini chat — GỬI ẨN prompt qua RPC `StreamGenerate` thay vì
    # gõ vào ô nhập liệu. CHỈ đổi bước GỬI; đọc kết quả vẫn bằng DOM. Request có
    # FILE ĐÍNH KÈM luôn tự động dùng DOM (chưa capture shape endpoint upload).
    # Đặt 0 để quay lại gõ DOM hoàn toàn. Hỏng ở bất kỳ bước nào cũng tự rơi về
    # DOM trong CÙNG task, không làm mất task.
    'gemini_send_via_rpc': 1,
    # (2026-08-14) Khoảng random (giây) chờ TRƯỚC KHI khởi động thread kế tiếp
    # trong 1 lô ảnh/video chạy Direct API song song (`_run_image_tasks_concurrent()`/
    # `_run_video_tasks_staggered()`, worker.py) — theo yêu cầu user "cho phép
    # setting random giây trong khoảng: 10-15 giây mặc định để khởi động thread
    # tiếp theo". Task ĐÃ khởi động vẫn tiếp tục chạy song song trong lúc chờ —
    # đây chỉ giãn cách LÚC BẮT ĐẦU từng task (tránh bắn N request cùng lúc,
    # trông giống bot), KHÔNG chờ task trước xong mới bắt đầu task sau (khác
    # `task_delay_secs` — dùng cho đường tuần tự hoàn toàn: DOM mode, UI-driven
    # fallback). Ref ảnh (nếu có) upload NGAY TRƯỚC khi khởi động thread của
    # ĐÚNG task đó (không còn upload cả lô video 1 lượt trước khi generate).
    'thread_stagger_min_secs': 10.0,
    'thread_stagger_max_secs': 15.0,
    # (2026-08-17) Bật/tắt bước "check đăng nhập Google" (`_ensure_google_login()`,
    # worker.py) — theo yêu cầu user "tạm tắt tính năng check login chạy thẳng
    # vào trang nhận task, khi cần có thể bật lại". TẮT (0, mặc định) = bỏ qua
    # HOÀN TOÀN bước vào gmail.com/kiểm tra ô email/tự nhập email-mật khẩu —
    # `_ensure_google_login()` chỉ còn `driver.get(target_url)` thẳng (hành vi
    # ĐƠN GIẢN như trước khi có tính năng này, §11.31) — nhanh hơn, ít bước hơn
    # nhưng KHÔNG tự phục hồi được nếu session Google bị đăng xuất giữa chừng.
    # BẬT (1) = hành vi đầy đủ đã có (vào gmail.com trước, tự nhập email/mật
    # khẩu đã lưu nếu cần). Áp dụng CHUNG cho MỌI entry point đã wire hàm này
    # (VEO3 dom/api, gemini, gemini_video, gemini round-robin — xem CLAUDE.md
    # §11.31/§11.38b) vì tất cả đều gọi qua CHÍNH `_ensure_google_login()`,
    # không cần sửa từng call site riêng.
    'google_login_check_enabled': 0,
    # (2026-09-14) "Chỉ chạy task theo project + email" — theo yêu cầu user "để
    # tránh bị khóa upload image... check chọn chỉ chạy task theo project +
    # email thì task nhận chỉ có cùng email mới được nhận và truy cập link flow
    # đó để thực hiện task". BẬT (1): máy VEO (api/dom) chỉ nhận task của project
    # Nano Banana đã gán ĐÚNG email profile đang đăng nhập (`projects.flow_email`),
    # vào ĐÚNG link project Flow của project đó (`projects.flow_project_id`) rồi
    # chạy — không tự tạo/luân chuyển project Flow nào. Ảnh tạo ra trong project
    # Flow đó được dùng LẠI làm ảnh tham chiếu bằng id đã lưu, không upload lại.
    # TẮT (0, mặc định): như cũ, và KHÔNG nhận task của project đã gán email.
    'bind_tasks_to_project_email': 0,
    # (2026-09-24) API mode — lỗi thì chuyển sang DOM hay không. TẮT (0, mặc
    # định): profile `worker_mode='api'` CHỈ chạy API — task lỗi API (hoặc không
    # đủ điều kiện gọi API: thiếu reCAPTCHA/project id, 403...) báo lỗi về server
    # để giao lại, KHÔNG tự gõ prompt qua giao diện. BẬT (1): các task đó được
    # chạy lại NGAY trong cùng lô bằng luồng DOM (`_run_tasks_batch()` —
    # configure/đính ảnh/gõ prompt trên trang Flow). Task video ĐÃ submit API
    # thành công nhưng chưa thấy kết quả thì KHÔNG chuyển DOM (API có thể vẫn
    # đang render — chạy DOM sẽ tạo trùng video).
    'api_fallback_to_dom': 0,
    # (2026-08-20) Bậc thang escalation THEO BATCH — theo yêu cầu user: "Nếu batch
    # gửi lên 5 task 1 lúc mà thành công 1 vẫn tính batch thành công -> nhưng nếu
    # cả batch đều không thành công liên tiếp 2 batch liền -> thì mới xóa cache
    # cookie không ảnh hưởng login rồi click button create lại -> nhưng vẫn lỗi
    # tiếp tục batch kế tiếp đó -> thì chuyển sang chế độ ngủ -> trong lúc ngủ xóa
    # tất cả cookie - cache -> rồi check login -> đăng nhập vào project lại như
    # trước -> vòng lặp cứ thế".
    #
    # SONG SONG (KHÔNG thay thế) 2 bậc thang THEO TASK đã có:
    #   (1) `error_count_before_refresh`/`refresh_count_before_new_project` —
    #       đếm lỗi LIÊN TIẾP từng task, escalate refresh → project mới → ngủ.
    #   (2) `error_window_minutes`/`error_window_max_errors` — N lỗi trong M phút.
    # Bậc thang MỚI này đếm ở mức BATCH (1 lần heartbeat nhận về N task): batch
    # có ÍT NHẤT 1 task thành công = batch THÀNH CÔNG (reset bộ đếm về 0, dù
    # N-1 task còn lại có lỗi hết) — chỉ batch KHÔNG task nào thành công mới
    # tính là "batch lỗi". Bắt được đúng lớp sự cố mà 2 bậc thang kia bỏ sót:
    # cả lô cùng chết vì 1 nguyên nhân chung (session/cookie hỏng, project bị
    # Google chặn...) chứ không phải vài task lẻ lỗi rải rác.
    #
    # `batch_fail_count_before_cleanup` — số batch lỗi LIÊN TIẾP trước khi dọn
    # cookie labs.google + cache (GIỮ NGUYÊN cookie đăng nhập Google ở domain
    # khác, xem `worker.py::_cdp_clear_cache_and_cookies()`) rồi click lại nút
    # "Create with Google Flow". ⚠️ (2026-08-20, theo yêu cầu user "theo phương
    # án 1: nhưng không click create vì chỉ khi xóa cookie mới cần click") —
    # TRƯỚC ĐÂY 2 bước này chạy sau MỌI batch vô điều kiện; giờ CHỈ chạy khi
    # chạm ngưỡng này. Batch bình thường vẫn refresh + reconcile (lấy task đã
    # render xong về, đánh dấu done) như cũ — KHÔNG bị gate, chỉ bỏ 2 bước
    # xoá-cookie + click-create.
    'batch_fail_count_before_cleanup': 2,
    # `batch_fail_count_before_sleep` — số batch lỗi LIÊN TIẾP trước khi cho
    # profile "ngủ" (dùng chung `error_sleep_secs` làm thời lượng ngủ). Lúc ngủ,
    # `_clear_profile_if_sleeping()` xoá TẤT CẢ cookie + cache (khác mặc định
    # chỉ-xoá-cache) rồi ép bước "check đăng nhập Google" chạy ở lần khởi động
    # worker KẾ TIẾP — bất kể `google_login_check_enabled` đang tắt hay bật —
    # vì vừa xoá sạch cookie thì chắc chắn đã đăng xuất, không check thì mọi
    # task sau đó đều lỗi. PHẢI lớn hơn `batch_fail_count_before_cleanup` (nếu
    # đặt ≤ thì bước dọn dẹp không bao giờ có cơ hội chạy trước khi ngủ).
    'batch_fail_count_before_sleep': 3,
    # (2026-09-03) Cửa sổ "gần đây" (giây) khi `_dom_fetch_project_media()` lọc
    # candidate gọi RPC `as29s` — xem CLAUDE.md §11.45. `Zzl0ze` liệt kê MỌI
    # media của project (có thể hàng trăm), mà muốn biết prompt+URL của 1 item
    # thì PHẢI gọi `as29s` riêng cho nó — gọi cho toàn bộ project mỗi vòng
    # reconcile sẽ rất chậm. Lọc theo timestamp tạo (`meta[1]`, giây Unix) để
    # chỉ xét media gần đây: vừa đủ rộng để "nhặt" task mắc kẹt từ phiên làm
    # việc gần đây, vừa tránh quét lại lịch sử. Bonus: `as29s` dường như chỉ
    # phục vụ item RECENT (item ~10 ngày trước trả `null` dù request hợp lệ),
    # nên cửa sổ này cũng tự né luôn vùng dữ liệu đã hết hạn.
    'reconcile_lookback_secs': 7200,
    # (2026-09-19) Cửa sổ RỘNG hơn, chỉ dùng cho bước check ĐẦU BATCH khi lô có
    # task chạy lại (retry_count>0) — media của lần chạy trước có thể đã render
    # xong từ lâu hơn 2 giờ mà chưa tải về; bỏ sót là task bị tạo lại lần 2.
    'reconcile_retry_lookback_secs': 86400,
}

os.makedirs(PROFILES_DIR, exist_ok=True)

# Thư mục log file per-profile — client_tool/logs/ (dùng _this_dir đã lên đúng cấp
# client_tool/ ở trên, KHÔNG dùng Path(__file__).parent trực tiếp vì file này nằm
# trong server/ — sẽ ra client_tool/server/logs/ sai chỗ).
LOGS_DIR = _this_dir / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

# Thư mục tạm chứa video clip Gemini worker tải về trước khi đính kèm (2026-07-20,
# theo yêu cầu user — trước đây dùng thẳng `tempfile.mkstemp()` (thư mục temp hệ
# điều hành, vd %TEMP%/…/gemini_clip_xxx.mp4), giờ đổi sang thư mục CỐ ĐỊNH trong
# chính project client_tool để dễ theo dõi/dọn dẹp. Luôn dùng `_this_dir` (KHÔNG
# phải `_ROOT`, vốn có thể trỏ lên repo cha) — cùng nguyên tắc với LOGS_DIR ở trên,
# đảm bảo video_tmp/ LUÔN nằm trong client_tool/ dù chạy trong repo đầy đủ hay
# standalone. File tải về vẫn tự xoá ngay sau khi gửi xong (xem worker.py
# `_run_task_gemini()`'s `finally`) — thư mục này chỉ giữ file TẠM THỜI trong lúc
# xử lý, không tích luỹ.
VIDEO_TMP_DIR = _this_dir / 'video_tmp'
VIDEO_TMP_DIR.mkdir(exist_ok=True)

# App version (2026-07-21) — đọc từ file VERSION ở gốc client_tool/, hiển thị
# trong GUI (Sidebar). File này được `.git/hooks/pre-push` tự tăng patch version
# mỗi lần push lên machinemedia.git — xem CHANGELOG. Thiếu file/lỗi đọc không
# nên chặn app khởi động, chỉ fallback về '0.0.0'.
_VERSION_FILE = _this_dir / 'VERSION'
try:
    APP_VERSION = _VERSION_FILE.read_text(encoding='utf-8').strip() or '0.0.0'
except Exception:
    APP_VERSION = '0.0.0'

logging.basicConfig(
    level   = logging.DEBUG if DEBUG else logging.INFO,
    format  = '%(asctime)s [%(name)s] %(levelname)s %(message)s',
    datefmt = '%H:%M:%S',
)
log = logging.getLogger('selenium-flow')

app = Flask(__name__)
CORS(app)

# ── Per-profile logger ────────────────────────────────────────────────────────

def _profile_log(profile_id: int, level: str, message: str):
    """Ghi log vào file logs/profile_{id}.log VÀ backend (POST /api/worker_profiles/
    <id>/logs — server tự giữ tối đa N entries/profile, xem `_LOG_KEEP` trong
    `backend/routes/worker_profiles.py`). (2026-07-17) TRƯỚC ĐÂY INSERT trực tiếp
    vào bảng `profile_logs` qua `db.get_conn()` — client_tool không còn kết nối DB
    trực tiếp, xem CHANGELOG "client_tool: bỏ kết nối DB trực tiếp"."""
    log.info(f'[profile-{profile_id}] [{level}] {message}')

    # ── File log ─────────────────────────────────────────────────────────────
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_path = LOGS_DIR / f'profile_{profile_id}.log'
        with open(log_path, 'a', encoding='utf-8') as lf:
            lf.write(f'{ts} [{level.upper():5}] {message}\n')
    except Exception:
        pass

    # ── Backend log (best-effort, KHÔNG được làm chậm/crash caller nếu mạng lỗi) ──
    # Header X-Client-Id/X-Client-Name (2026-07-18) — bắt buộc để backend scope
    # đúng, xem client_identity.py. Import TRỄ (trong hàm) để tránh circular import
    # với client_identity.py (module đó không import gì từ config.py).
    try:
        from .client_identity import client_headers
        # (2026-08-26) Session cookie BAT BUOC — route log ben backend gio co
        # @_require_tool_media (truoc day khong gate auth, ai cung POST log gia
        # vao bat ki profile nao). Import TRE giong client_headers: auth_client
        # import nguoc lai config.py nen import o dau file se circular.
        from .auth_client import get_auth_cookies
        req_lib.post(
            f'{FLOW_SERVER}/api/worker_profiles/{profile_id}/logs',
            json={'level': level, 'message': message[:4000]},
            headers=client_headers(),
            cookies=get_auth_cookies(),
            timeout=5,
        )
    except Exception as e:
        log.warning(f'profile_log backend error: {e}')
