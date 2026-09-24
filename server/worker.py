"""SeleniumFlowWorker — engine tự động hoá chính: API mode (gọi RPC
`batchexecute` của flow.google.com từ trong trang), DOM mode (điều khiển DOM
đầy đủ), gemini mode (chat + upload video). 1 instance = 1 profile Chrome.

API mode CHỈ chạy `batchexecute` — xem `server/flow_be.py` và
`docs/FLOW_BATCHEXECUTE_API.md`. (2026-09-24) Đường `aisandbox` cũ (bearer
token, fingerprint, curl_cffi impersonate, setting `generate_via_batchexecute`)
đã GỠ HẲN khỏi code."""

import base64, json, os, random, re, shutil, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlparse


from .proxy_config import build_proxy_setup
from .config import FLOW_SERVER, POLL_INTERVAL, _IS_ARM, req_lib, _profile_log, VIDEO_TMP_DIR
from .local_settings import get_local_settings
from .i18n_texts import get_i18n_texts
from .media_upload_cache import get_cached_media_name, set_cached_media_name
from . import flow_media_index
from .managers import pm, em
from . import flow_be
from . import gemini_be
from . import model_catalog
from . import flow_models
from .run_hours import in_run_hours, summarize_run_hours
from .chrome_utils import (
    _chrome_alive_on_port, _chrome_running_for_profile, _kill_chrome_for_profile,
    _build_chrome_options, _chrome_debug_port, _connect_to_chrome,
    _detect_chrome_portables, _detect_chrome_version, _detect_chromedriver,
    _chromedriver_major_version, _make_service, _patch_selenium_pool_size,
    _usable_profile_dir,
)
from .state import _login_drivers, _sleep_until_by_pid, _force_login_check

# (2026-08-18, fix bug thật user báo — log warn "KHÔNG có trong veo_models.
# model_key_ingredient" cho case ĐÃ ĐÚNG) `model_catalog.resolve_image_model()`/
# `resolve_video_model()` trả `source` ∈ {'db','db-derived','local-fallback',
# 'local-default'} — CẢ 'db' LẪN 'db-derived' đều "dùng hoàn toàn trong DB"
# (db-derived chỉ transform `t2v`→`r2v` string TỪ giá trị THẬT đọc từ cột
# `veo_models.model_key`, KHÔNG đụng gì tới dict hardcode `flow_api.py` —
# xem `model_catalog.py::resolve_video_model()`), chỉ 'local-fallback'/
# 'local-default' mới THẬT SỰ dùng dict cục bộ (backend không tới được hoặc
# model chưa từng confirm trong DB) — CHỈ 2 giá trị này mới đáng cảnh báo.
_MODEL_SRC_FROM_DB = frozenset({'db', 'db-derived'})

FLOW_PROJECT_URL         = 'https://labs.google/fx/vi/tools/flow'
LABS_RECAPTCHA_SITE_KEY  = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV'
# (2026-08-14) Khớp `_CDN_FLOW_RE` phía backend (`backend/services/media_download.py`)
# — trích UUID media THẬT từ CDN signed URL (`https://flow-content.google/
# image/<uuid>?Expires=...` hoặc `.../video/<uuid>?...`). Dùng để lấy `name`
# ĐÚNG (khác nhau mỗi lần generate) thay vì tự chế 1 chuỗi cố định theo
# task_id — xem `_extract_cdn_media_name()`.
_CDN_MEDIA_NAME_RE = re.compile(r'flow-content\.google/(?:video|image)/([0-9a-f-]{36})', re.I)


def _extract_cdn_media_name(url: str) -> str:
    """Trích UUID media thật từ CDN URL đã resolve — trả '' nếu không khớp
    (caller tự fallback về tên tự chế nếu cần, KHÔNG raise)."""
    m = _CDN_MEDIA_NAME_RE.search(url or '')
    return m.group(1) if m else ''


# (2026-08-18, ĐỔI HƯỚNG LẦN 2) Tiêu chí XOÁ cookie cho `SeleniumFlowWorker.
# _cdp_clear_cache_and_cookies()` — theo yêu cầu user "đổi sang xóa tất cả
# cookie trong labs.google ko đụng cái khác": chỉ xét `domain`, KHÔNG xét tên —
# cookie thuộc domain đích (hoặc subdomain của nó) bị XOÁ TẤT CẢ, không có
# ngoại lệ nào theo tên; cookie domain khác (`.google.com`,
# `accounts.google.com`, `chatgpt.com`...) HOÀN TOÀN KHÔNG BỊ ĐỤNG TỚI — không
# cần danh sách "giữ lại" nào vì phạm vi xoá đã tự giới hạn đúng đắn qua
# domain. Cookie đăng nhập Google (SID-family) sống trên `.google.com`, không
# phải domain đích của bất kỳ worker_mode nào ở đây, nên tự động an toàn.
#
# (2026-09-13, GENERIC HOÁ theo yêu cầu user "với các tài khoản gemini... clear
# cookie như veo") — trước đây CỐ ĐỊNH `labs.google` (chỉ VEO3 dom/api dùng
# được, Gemini/ChatGPT gọi vào chỉ tốn round-trip CDP vô ích — xem lịch sử
# 2026-08-30 "BỎ bước xoá cache+cookie cho task GEMINI... nó vốn đã VÔ NGHĨA ở
# luồng này"). Giờ domain đích tra theo `worker_mode` — Gemini (gemini/
# gemini_video/gemini_image) dọn `gemini.google.com` thay vì `labs.google`,
# cùng cơ chế/hàm DUY NHẤT, không viết thêm bản riêng.
_BATCH_CLEAN_DOMAIN_BY_WORKER_MODE = {
    'dom':          'labs.google',
    'api':          'labs.google',
    'gemini':       'gemini.google.com',
    'gemini_video': 'gemini.google.com',
    'gemini_image': 'gemini.google.com',
}


def _batch_clean_target_domain(worker_mode: str) -> str | None:
    """Domain cookie/site-data sẽ bị dọn mỗi batch/task cho `worker_mode` —
    `None` nếu worker_mode đó không có domain nào để dọn qua cơ chế này (vd
    'chatgpt' — dùng hệ đăng nhập RIÊNG của OpenAI, không liên quan domain
    Google nào ở đây; gọi `_cdp_clear_cache_and_cookies()` cho nó vẫn CHỈ xoá
    HTTP cache, không đụng cookie gì)."""
    return _BATCH_CLEAN_DOMAIN_BY_WORKER_MODE.get(worker_mode)


def _cookie_matches_domain(cookie_domain: str, target_domain: str) -> bool:
    """`True` nếu `cookie_domain` thuộc `target_domain` (hoặc subdomain của
    nó) — dùng bởi `_cdp_clear_cache_and_cookies()`. Domain cookie Chrome có
    thể mang tiền tố `.` (domain-cookie, vd `.labs.google`) hoặc không
    (host-only cookie, `labs.google`) — so cả 2 dạng, và cả subdomain
    (`www.labs.google`...)."""
    d = (cookie_domain or '').lstrip('.')
    return d == target_domain or d.endswith('.' + target_domain)


# (2026-08-12) MIME theo phần mở rộng — dùng khi tải lại `source_media` cho
# luồng direct-API (upload qua /flow/uploadImage trước khi generate), mirror
# ĐÚNG dict cục bộ trong `_dom_upload_images()` (giữ 2 bản riêng — 1 dict dùng
# chung 2 chỗ không đáng để tách thêm 1 tham số/module).
_MEDIA_MIME_BY_EXT = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
}

# (2026-08-19) Đổi tên phần mở rộng "lạ" nhưng THỰC CHẤT là JPEG thường
# (`.jfif`/`.pjpeg`/`.pjp` — đều là biến thể/alias của JPEG File Interchange
# Format, KHÔNG cần re-encode, chỉ đổi tên file) → `.jpg` TRƯỚC KHI ghi ra đĩa
# để `send_keys()`/CDP `DOM.setFileInputFiles` gắn file vào `<input type=file>`
# của Gemini/ChatGPT. Root cause bug thật user báo "upload file .jfif nó vẫn
# nhận nhưng ko show được ảnh preview lên chat rồi gemini bắt lỗi không đủ
# ảnh": Chrome tự suy `File.type` (MIME) của object File từ ĐUÔI TÊN FILE trên
# đĩa (không đọc nội dung byte) — bảng tra MIME-theo-đuôi nội bộ của Chrome
# (net::GetMimeTypeFromExtension, dùng registry OS làm nguồn phụ) KHÔNG có
# entry đáng tin cậy cho `.jfif` trên nhiều máy Windows (khác `.jpg`/`.jpeg`
# luôn có sẵn) — `File.type` ra rỗng/`application/octet-stream`, Gemini nhận
# file (input vẫn nhận, không lỗi ngay) nhưng KHÔNG coi đó là ảnh hợp lệ (JS
# phía Gemini lọc theo `file.type.startsWith('image/')` trước khi hiện
# preview) → không có thumbnail, và lúc submit server Gemini đếm số ảnh THẬT
# SỰ đính kèm = 0, trả lỗi kiểu "thiếu ảnh". Đổi đuôi trước khi attach đảm bảo
# Chrome LUÔN suy đúng `image/jpeg` (đuôi `.jpg` được nhận diện phổ biến trên
# MỌI hệ điều hành), không cần đổi nội dung file — dữ liệu JFIF vốn dĩ CHÍNH
# LÀ JPEG hợp lệ (đọc/hiển thị được bởi mọi trình xem ảnh chuẩn).
_ATTACH_EXT_ALIASES = {'.jfif': '.jpg', '.pjpeg': '.jpg', '.pjp': '.jpg'}


def _normalize_attach_filename(filename: str) -> str:
    """Đổi đuôi file 'lạ nhưng thực chất JPEG' (`.jfif`/...) → `.jpg` — xem
    `_ATTACH_EXT_ALIASES`. Dùng TRƯỚC khi ghi file tải về ra đĩa ở mọi nơi
    chuẩn bị đính kèm ảnh cho Gemini/ChatGPT (KHÔNG ảnh hưởng luồng DOM Flow
    VEO3 — mode đó dùng base64 inject qua JS, không qua file input native)."""
    stem, ext = os.path.splitext(filename)
    new_ext = _ATTACH_EXT_ALIASES.get(ext.lower())
    return f'{stem}{new_ext}' if new_ext else filename

# Tìm menuitem model đúng trong dropdown Flow (2026-07-21, VIẾT LẠI 2026-09-03
# theo DOM MỚI — xem CLAUDE.md "DOM video veo3 trang flow đã thay đổi") — tách
# thành hằng số module-level (dùng bởi _dom_configure() VÀ tests/_test_model_select.py,
# đảm bảo test luôn chạy đúng logic PRODUCTION, không phải bản copy có thể lệch).
#
# (2026-09-03) Google Flow đổi domain `labs.google/.../tools/flow` → REDIRECT
# sang app HOÀN TOÀN MỚI `flow.google.com` (Angular Material thay React cũ,
# ProseMirror thay Slate.js) — mọi selector icon-ligature (`<i>`) + text-includes
# cũ đều chết. Xác nhận qua Chrome đang mở THẬT (attach debuggerAddress, profile
# huavantien84_2, xem tests/_investigate_dom_change*.py — throwaway, đã xoá).
# Menu model giờ là `<mat-menu>` chứa `<flow-menu-item><button mat-menu-item>
# <span class="item-text"><span class="label">Veo 3.1 - Lite</span></span>
# </button></flow-menu-item>` — label nằm RIÊNG hẳn 1 span, KHÔNG còn dính icon
# ligature ("volume_up") chung element như bản React cũ → không cần cloneNode+
# xoá `<i>` nữa, chỉ đọc thẳng `.label`. Vẫn giữ nguyên tinh thần "ưu tiên khớp
# CHÍNH XÁC trước substring" (đề phòng 1 label là prefix của label khác, vd
# "Veo 3.1 - Lite" ⊂ "Veo 3.1 - Lite [Lower Priority]"). Trả về `<button
# mat-menu-item>` (phần tử CLICK ĐƯỢC) khớp nhất, hoặc null.
MODEL_MATCH_JS = """
    var model = arguments[0];
    var labels = [...document.querySelectorAll('flow-menu-item .label, [role="menuitem"] .label')]
        .filter(function(el){ return el.getClientRects().length>0; });
    var candidates = labels.filter(function(el){ return el.textContent.trim().includes(model); });
    if (candidates.length === 0) return null;
    var exact = candidates.find(function(el){ return el.textContent.trim() === model; });
    var target = exact || candidates[0];
    return target.closest('button[mat-menu-item], button[role="menuitem"], button') || target;
"""

# Chọn 1 option trong 1 nhóm `<flow-toggles aria-label="...">` (Mode/Video type/
# Aspect ratio/Output count — mọi mat-button-toggle-group trong settings popup
# MỚI, xem `_dom_configure()`). Nhận arguments[0]=aria-label nhóm,
# arguments[1]=list text biến thể chấp nhận được — trả `<button role="radio">`
# khớp (ưu tiên khớp CHÍNH XÁC .toggle-text) hoặc null nếu không tìm thấy nhóm/
# option nào. Dùng chung 1 hàm cho cả 4 nhóm settings — chỉ khác aria-label +
# danh sách text truyền vào.
TOGGLE_MATCH_JS = """
    // (2026-09-24) arguments[0] = 1 nhãn HOẶC danh sách nhãn nhóm (mọi ngôn ngữ —
    // Flow dịch aria-label theo ngôn ngữ tài khoản, vd "Mode"/"Chế độ").
    // Biến thể dạng 'icon:xxx' khớp theo mat-icon (không phụ thuộc ngôn ngữ).
    // Không thấy nhóm theo nhãn → quét MỌI nhóm (biến thể đủ đặc trưng).
    var labels = Array.isArray(arguments[0]) ? arguments[0] : [arguments[0]];
    var variants = arguments[1];
    var groups = [];
    labels.forEach(function(l){
        var g = document.querySelector('flow-toggles[aria-label="' + l + '"] mat-button-toggle-group');
        if (g) groups.push(g);
    });
    if (!groups.length) groups = [...document.querySelectorAll('flow-toggles mat-button-toggle-group')];
    var btns = [];
    groups.forEach(function(g){ btns = btns.concat([...g.querySelectorAll('button[role="radio"]')]); });
    function iconOf(b){ var i = b.querySelector('mat-icon'); return i ? i.textContent.trim() : ''; }
    function textOf(b){
        var c = b.cloneNode(true);
        c.querySelectorAll('mat-icon').forEach(function(i){ i.remove(); });
        return c.textContent.trim().replace(/\\s+/g,' ');
    }
    function hit(b, exact){
        return variants.some(function(v){
            if (v.indexOf('icon:') === 0) return iconOf(b) === v.slice(5);
            var t = textOf(b);
            return exact ? t === v : t.includes(v);
        });
    }
    return btns.find(function(b){ return hit(b, true); })
        || btns.find(function(b){ return hit(b, false); }) || null;
"""


def _flow_btn_expr(key: str, fallback_js: str = 'null') -> str:
    """(2026-09-24) Biểu thức JS trả về 1 nút của Flow theo `aria-label` — thử MỌI
    biến thể ngôn ngữ trong `i18n_texts.json[key]` (Flow dịch aria-label theo
    ngôn ngữ tài khoản: "Start generation" ↔ "Bắt đầu tạo"...), không thấy thì
    dùng `fallback_js` (tìm theo component/icon, không phụ thuộc ngôn ngữ)."""
    labels = json.dumps(get_i18n_texts().get(key) or [])
    return ("(function(){var L=" + labels + ";"
            "var b=[...document.querySelectorAll('button')].find(function(e){"
            "return L.indexOf(e.getAttribute('aria-label')||'')>=0;});"
            "return b || (" + fallback_js + ") || null;})()")


def _flow_btn_js(key: str, fallback_js: str = 'null') -> str:
    return 'return ' + _flow_btn_expr(key, fallback_js) + ';'


def _prompt_box_btn_by_icon(test_js: str) -> str:
    """JS dự phòng: nút trong ô nhập prompt (`flow-prompt-box`) có mat-icon thoả `test_js`
    (biến `t` = text của icon)."""
    return ("(function(){var p=document.querySelector('flow-prompt-box')||document;"
            "return [...p.querySelectorAll('button')].find(function(b){"
            "var i=b.querySelector('mat-icon'); var t=i?i.textContent.trim():'';"
            "return " + test_js + ";});})()")


_FB_SETTINGS = _prompt_box_btn_by_icon("/^crop_/.test(t)")
_FB_SUBMIT   = "document.querySelector('flow-generate-icon-button button')"
_FB_ADD_REF  = _prompt_box_btn_by_icon("t==='add'")
_FB_MODEL    = "document.querySelector('flow-prompt-box-settings button[aria-haspopup=\"menu\"]')"

# JS reCAPTCHA — port từ tests/utils/flow_session.py (đã proven qua _test_textToImage.py).
# execute_async_script: callback `done` LUÔN là arguments[arguments.length-1],
# KHÔNG phải arguments[0] (bug thật 2026-08-12: worker cũ gán done=arguments[0]
# → siteKey bị hiểu thành 'VIDEO_GENERATION' → "Invalid site key ... VIDEO_GENERATION").
# (2026-09-23) BẪY reCAPTCHA của Flow — bundle có hàm `x2a` (bật bằng cờ
# `enable_recaptcha_execute_closure_wrap`): sau `grecaptcha.enterprise.ready()`,
# trang CẤT bản `execute` gốc vào closure riêng rồi THAY hàm công khai bằng
#   c.execute = (e,f) => d(e, Object.assign({}, f, {action:"extension_hijack_detected"}))
# ⇒ mọi lời gọi từ bên ngoài (tool) ra token action `extension_hijack_detected`
# thay vì `VIDEO_GENERATION` → Google trả `PUBLIC_ERROR_UNUSUAL_ACTIVITY`, trong
# khi bấm tay (trang dùng bản gốc) vẫn chạy. Script này chạy TRƯỚC mọi script
# của trang (`Page.addScriptToEvaluateOnNewDocument`), bọc setter của
# `window.grecaptcha` → `.enterprise` → `.execute` để giữ lại bản GỐC vào
# `window.__rcOrigExecute` trước khi trang kịp thay bằng bẫy.
RECAPTCHA_GUARD_JS = r"""
(function () {
  if (window.__rcKeepHook) return;
  window.__rcKeepHook = true;
  function guardEnterprise(ent) {
    if (!ent || ent.__rcGuarded) return;
    var cur = ent.execute;
    if (typeof cur === 'function' && !window.__rcOrigExecute) window.__rcOrigExecute = cur.bind(ent);
    try {
      Object.defineProperty(ent, 'execute', {
        configurable: true, enumerable: true,
        get: function () { return cur; },
        set: function (v) {
          if (typeof cur === 'function' && !window.__rcOrigExecute) window.__rcOrigExecute = cur.bind(ent);
          if (typeof v === 'function' && !window.__rcOrigExecute) window.__rcOrigExecute = v.bind(ent);
          cur = v;
        }
      });
      ent.__rcGuarded = true;
    } catch (e) {}
  }
  function guardRoot(g) {
    if (!g || g.__rcRootGuarded) return;
    var ent = g.enterprise;
    try {
      Object.defineProperty(g, 'enterprise', {
        configurable: true, enumerable: true,
        get: function () { return ent; },
        set: function (v) { ent = v; guardEnterprise(v); }
      });
      g.__rcRootGuarded = true;
    } catch (e) {}
    guardEnterprise(ent);
  }
  var root = window.grecaptcha;
  try {
    Object.defineProperty(window, 'grecaptcha', {
      configurable: true, enumerable: true,
      get: function () { return root; },
      set: function (v) { root = v; guardRoot(v); }
    });
  } catch (e) {}
  guardRoot(root);
})();
"""

RECAPTCHA_FETCH_JS = """
    var siteKey = arguments[0];
    var maxWait = arguments[1] || 30000;
    var action  = arguments[2] || 'IMAGE_GENERATION';
    var done    = arguments[arguments.length - 1];
    var start   = Date.now();
    function hasClient() {
        var cfg = window.___grecaptcha_cfg;
        return !!(cfg && cfg.clients && Object.keys(cfg.clients).length > 0);
    }
    function pickExecute() {
        // Ưu tiên bản GỐC đã giữ bởi RECAPTCHA_GUARD_JS. Không có mà hàm công
        // khai đã bị trang thay bằng bẫy → báo lỗi riêng để Python reload trang
        // (KHÔNG gọi bẫy: token sẽ mang action extension_hijack_detected).
        if (typeof window.__rcOrigExecute === 'function') return window.__rcOrigExecute;
        var pub = window.grecaptcha && window.grecaptcha.enterprise &&
                  window.grecaptcha.enterprise.execute;
        if (!pub) return null;
        if (String(pub).indexOf('extension_hijack_detected') >= 0) return 'TRAPPED';
        return pub.bind(window.grecaptcha.enterprise);
    }
    function tryExecute(retries) {
        var exec = pickExecute();
        if (exec === 'TRAPPED') {
            done({token: null, error: 'RC_TRAPPED'}); return;
        }
        if (!exec) {
            done({token: null, error: 'grecaptcha.enterprise not available'}); return;
        }
        try {
            exec(siteKey, {action: action})
                .then(function(t) { done({token: t, error: null}); })
                .catch(function(e) {
                    var msg = String(e);
                    if (retries > 0 && msg.indexOf('No reCAPTCHA clients') >= 0) {
                        setTimeout(function() { tryExecute(retries - 1); }, 2000);
                    } else { done({token: null, error: msg}); }
                });
        } catch (e) {
            var msg = String(e);
            if (retries > 0 && msg.indexOf('No reCAPTCHA clients') >= 0) {
                setTimeout(function() { tryExecute(retries - 1); }, 2000);
            } else { done({token: null, error: msg}); }
        }
    }
    function poll() {
        if (hasClient() && window.grecaptcha && window.grecaptcha.enterprise &&
            window.grecaptcha.enterprise.execute) {
            tryExecute(3);
        } else if (Date.now() - start < maxWait) {
            setTimeout(poll, 500);
        } else {
            done({token: null, error: 'recaptcha client not ready after ' + maxWait + 'ms'});
        }
    }
    poll();
"""





class ModelSelectionFailed(RuntimeError):
    """Raise khi `_dom_select_model_verified()` không thể XÁC NHẬN model đã
    thật sự được áp dụng sau nhiều lần thử — PHẢI làm hỏng cả task (không được
    `_dom_configure()`'s try/except chung nuốt mất) vì generate với SAI model
    tốn quota thật + cho kết quả sai. Xem `_dom_configure()`'s except-clause
    (re-raise riêng loại này, mọi lỗi khác vẫn best-effort như cũ) và CLAUDE.md
    §11.28 (2026-08-07, "chọn sai model")."""

# ── i18n (2026-07-16, tách ra JSON 2026-07-17) ──────────────────────────────────
# UI Flow hiển thị text theo locale tài khoản/trình duyệt (vi hoặc en) — port từ
# static I18N trong extensions/content/flowMediaGenerator.js. Bản DOM mode ở file
# này trước đây chỉ hardcode text tiếng Việt (vd 'Thêm vào câu lệnh') — nếu account
# đang hiển thị UI tiếng Anh ('Add to Prompt'), MỌI check text-match sẽ luôn thất
# bại — khớp đúng triệu chứng "picker mở thật (nhìn thấy) nhưng code không thấy"
# (không phải lỗi timing, mà do so sánh chuỗi sai ngôn ngữ). Icon Material Symbol
# (vd 'add_2', 'image') không đổi theo locale nên KHÔNG cần biến thể.
#
# Danh sách biến thể giờ đọc từ `client_tool/i18n_texts.json` (xem i18n_texts.py)
# thay vì hardcode ở đây — theo yêu cầu user, sửa/thêm biến thể ngôn ngữ (vd sau
# khi xác nhận qua dump chẩn đoán ở _dom_upload_images) chỉ cần sửa file JSON,
# KHÔNG cần đổi code Python.


class SeleniumFlowWorker:
    """
    Mỗi worker = một Chrome instance chạy nền:
      1. Mở Chrome với user-data-dir (giữ session Google đã đăng nhập)
      2. Navigate đến flow.google.com/project/{uuid}
      3. Hái `bl`/`f.sid`/`at` từ 1 lệnh batchexecute mà chính trang tự bắn
      4. Heartbeat → nhận task từ Flow server
      5. Gọi RPC batchexecute từ trong trang (API mode, không cần DOM clicks)
      6. Upload kết quả lên Flow server
    """

    def __init__(self, profile: dict):
        self.profile     = profile
        self.profile_id  = profile['id']
        self.machine_code = f'selenium-{profile["id"]}'
        self.worker_mode = profile.get('worker_mode') or 'api'
        if self.worker_mode not in ('api', 'dom', 'gemini', 'chatgpt', 'gemini_video', 'gemini_image'):
            self.worker_mode = 'api'
        self.driver      = None
        self._stop       = threading.Event()
        # Settings CỤC BỘ (2026-07-17) — không còn đồng bộ từ FLOW_SERVER qua
        # heartbeat nữa (xem _heartbeat và local_settings.py). Snapshot lúc worker
        # khởi động; sửa qua GUI trang Cài đặt áp dụng cho worker MỚI, không
        # hot-reload vào worker đang chạy giữa chừng.
        self._server_settings = get_local_settings()
        # Đếm lỗi liên tiếp / số lần đã refresh cho project hiện tại — dùng cho escalation
        # error → refresh trang → tạo project mới (xem _handle_task_error).
        self._consecutive_errors = 0
        self._refresh_count      = 0
        self._project_resets_since_success = 0
        # Số item media project hiện tại (2026-07-21) — cập nhật mỗi lần reconcile
        # đọc projectInitialData thành công (_reconcile_project_media), dùng bởi
        # _rotate_project_if_full() để biết khi nào cần chuyển project mới.
        self._last_project_media_count = 0
        self._sleep_until        = 0.0
        # Live-tracking cho dashboard (main.py, xem live_info()) — chỉ phản ánh
        # PHIÊN CHẠY hiện tại của worker, KHÔNG persist DB — reset về 0 mỗi lần worker
        # restart (giống _consecutive_errors/_refresh_count ở trên, cùng triết lý).
        self._current_task      = None  # {'id','mode','prompt','started_at'} khi đang xử lý
        self._task_done_count   = 0
        self._task_error_count  = 0
        self._last_error_msg    = ''
        self._last_error_at     = 0.0
        # Cửa sổ thời gian cho time-window sleep (xem _record_error) — epoch time của
        # từng lỗi gần đây, tự prune các mốc cũ hơn error_window_minutes mỗi lần thêm.
        self._error_timestamps: list = []
        # (2026-08-20) Bậc thang escalation THEO BATCH — SONG SONG (không thay thế)
        # 2 bậc thang THEO TASK ở trên, xem `_evaluate_batch_outcome()` +
        # `config.py::batch_fail_count_before_cleanup/_sleep`.
        #   `_batch_success_count`        — số task THÀNH CÔNG trong batch ĐANG chạy;
        #                                   reset về 0 ở đầu mỗi `_process_tasks()`,
        #                                   tăng trong `_handle_task_success()` (điểm
        #                                   funnel DUY NHẤT của mọi đường thành công).
        #                                   >0 lúc kết batch = batch THÀNH CÔNG.
        #   `_consecutive_failed_batches` — số batch LIÊN TIẾP không task nào thành công.
        #   `_sleep_wipe_all_cookies`     — bậc thang batch vừa cho ngủ → `_clear_profile_
        #                                   if_sleeping()` phải xoá TẤT CẢ cookie (không
        #                                   chỉ cache như mặc định) + ép check login.
        self._batch_success_count        = 0
        self._consecutive_failed_batches = 0
        self._sleep_wipe_all_cookies     = False
        self._sleep_skip_clean           = False
        # reCAPTCHA gần nhất (tái dùng khi lần mint sau lỗi)
        self._tokens = {
            'recaptchaToken':     None,
            'capturedAt':         0.0,
        }
        self._client_hints_cache = None
        self._last_api_sync_at = 0.0
        # (2026-09-14) Chế độ "chỉ chạy task theo project + email" — link project
        # Flow của lô ĐANG chạy (lấy từ project Nano Banana của task), và
        # `project_url` RIÊNG của profile để trả lại khi thôi gán. Xem
        # `_apply_flow_binding()`.
        self._bound_flow_url   = ''
        self._own_project_url  = None
        # Danh sách id media của từng project Flow (RPC `Zzl0ze`) — kiểm tra 1 id
        # ảnh đã lưu có THẬT SỰ nằm trong project đang làm việc không trước khi
        # dùng lại làm ảnh tham chiếu. Xem `_flow_project_media_names()`.
        self._flow_listing_cache: dict = {}
        self._flow_listing_lock = threading.Lock()
        self._nav_ensure_state()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str):
        _profile_log(self.profile_id, level, msg)

    def _sleep(self, secs: float):
        self._stop.wait(secs)

    # ── Điều hướng CÓ GHI LOG (2026-09-13) ───────────────────────────────────
    # Theo yêu cầu user "refresh linh tinh nhiều, ghi rõ để debug loại bỏ các
    # bước không cần thiết": MỌI `driver.refresh()` / `driver.get()` /
    # `location.href = …` của worker PHẢI đi qua `_nav_refresh()` /
    # `_nav_get()` / `_nav_js_href()`. Mỗi lần ghi ĐÚNG 1 dòng `[nav #N]`:
    # lý do, chuỗi hàm đã gọi tới, trang đang đứng + trang sau khi xong (thấy
    # ngay khi bị đá về trang chung), mất bao lâu, cách lần trước bao lâu (dưới
    # `_NAV_BURST_SECS` thì gắn cờ ⚠ DỒN DẬP, level warn), task/batch đang chạy.
    # Cuối mỗi batch có 1 dòng tổng kết gom theo lý do (`_nav_batch_end()`).
    # ⚠️ Thêm điểm điều hướng mới sau này: dùng 3 hàm này, ĐỪNG gọi thẳng driver.
    _NAV_BURST_SECS = 10

    def _nav_ensure_state(self):
        """State của log điều hướng. Gọi ở `__init__` VÀ đầu mỗi hàm `_nav*` —
        test dựng worker bằng `object.__new__` (bỏ qua `__init__`) vẫn chạy."""
        if not hasattr(self, '_nav_seq'):
            self._nav_seq = 0
            self._nav_last_at = 0.0
            self._nav_batch_no = 0
            self._nav_batch_active = False
            self._nav_batch_events = []
            self._nav_batch_started = 0.0

    @staticmethod
    def _short_url(url: str, limit: int = 90) -> str:
        u = re.sub(r'^https?://', '', url or '')
        return u if len(u) <= limit else u[:limit - 1] + '…'

    @staticmethod
    def _nav_caller_chain(depth: int = 4) -> str:
        """`hàm:dòng ← hàm:dòng ← …` — chỉ lấy frame thuộc CHÍNH module này,
        bỏ qua các hàm `_nav*` và lambda bọc action."""
        skip = {'_nav', '_nav_refresh', '_nav_get', '_nav_js_href',
                '_nav_caller_chain', '<lambda>'}
        names = []
        f = sys._getframe(1)
        while f is not None and len(names) < depth:
            code = f.f_code
            if f.f_globals.get('__name__') == __name__ and code.co_name not in skip:
                names.append(f'{code.co_name}:{f.f_lineno}')
            f = f.f_back
        return ' ← '.join(names) or '?'

    def _nav(self, kind: str, reason: str, action, url: str = ''):
        """Chạy `action` (1 lần điều hướng) rồi ghi 1 dòng log. Lỗi → vẫn log
        rồi RAISE lại nguyên exception (call site vẫn tự xử lý như trước)."""
        self._nav_ensure_state()
        self._nav_seq += 1
        seq = self._nav_seq
        start = time.time()
        gap = (start - self._nav_last_at) if self._nav_last_at else None
        self._nav_last_at = start
        try:
            before = self.driver.current_url or ''
        except Exception:
            before = ''
        chain = self._nav_caller_chain()

        err = None
        try:
            action()
        except Exception as e:
            err = e
        took = time.time() - start
        try:
            after = self.driver.current_url or ''
        except Exception:
            after = ''
        self._nav_batch_events.append((kind, reason, took))

        head = f'[nav #{seq}] {kind.upper()}'
        if url:
            head += f' → {self._short_url(url)}'
        parts = [f'{head} ({took:.1f}s)', f'lý do: {reason}',
                 f'từ trang: {self._short_url(before) or "(trống)"}']
        # "sau:" chỉ hiện khi KHÁC điều mong đợi — refresh mà URL đổi, hoặc get
        # mà không tới đúng đích (bị redirect về trang chung/đăng nhập…).
        expected = (url or before).rstrip('/')
        if after and after.rstrip('/') != expected:
            parts.append(f'sau: {self._short_url(after)}')
        burst = gap is not None and gap < self._NAV_BURST_SECS
        if gap is None:
            parts.append('lần đầu phiên này')
        else:
            parts.append(f'cách lần trước {gap:.1f}s' + (' ⚠ DỒN DẬP' if burst else ''))
        task_id = (getattr(self, '_current_task', None) or {}).get('id')
        if task_id:
            parts.append(f'task #{task_id}')
        if self._nav_batch_active:
            parts.append(f'batch #{self._nav_batch_no}')
        parts.append(f'gọi từ: {chain}')
        if err is not None:
            parts.append(f'LỖI: {err}')
        self._log('warn' if (burst or err is not None) else 'info', ' | '.join(parts))
        if err is not None:
            raise err

    def _nav_refresh(self, reason: str):
        self._nav('refresh', reason, lambda: self.driver.refresh())

    def _nav_get(self, url: str, reason: str):
        self._nav('get', reason, lambda: self.driver.get(url), url=url)

    def _nav_js_href(self, url: str, reason: str):
        self._nav('js-href', reason,
                  lambda: self._js('window.location.href = arguments[0];', url), url=url)

    def _nav_batch_begin(self, tasks: list):
        self._nav_ensure_state()
        self._nav_batch_no += 1
        self._nav_batch_active = True
        self._nav_batch_events = []
        self._nav_batch_started = time.time()
        ids = ', '.join(f'#{t.get("id")}' for t in tasks[:10]) + (' …' if len(tasks) > 10 else '')
        self._log('info', f'[nav] ── Bắt đầu batch #{self._nav_batch_no}: '
                           f'{len(tasks)} task ({ids}) ──')

    def _nav_batch_end(self):
        """Tổng kết điều hướng của batch vừa xong, GOM theo (loại, lý do) —
        số trong lý do đổi thành N để các lần "lần 1/3", "lần 2/3" gộp chung."""
        self._nav_ensure_state()
        if not self._nav_batch_active:
            return
        self._nav_batch_active = False
        events = self._nav_batch_events
        took = time.time() - self._nav_batch_started
        n = self._nav_batch_no
        if not events:
            self._log('info', f'[nav] ── Kết thúc batch #{n} ({took:.0f}s): không điều hướng lần nào ──')
            return
        groups: dict = {}
        kinds: dict = {}
        nav_secs = 0.0
        for kind, reason, secs in events:
            key = (kind, re.sub(r'\d+', 'N', reason))
            g = groups.setdefault(key, [0, 0.0])
            g[0] += 1
            g[1] += secs
            kinds[kind] = kinds.get(kind, 0) + 1
            nav_secs += secs
        kind_txt = ', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))
        lines = [f'    {kind.upper()} ×{c} ({s:.0f}s) — {reason}'
                 for (kind, reason), (c, s) in sorted(groups.items(), key=lambda kv: -kv[1][0])]
        self._log('info', f'[nav] ── Kết thúc batch #{n} ({took:.0f}s): {len(events)} lần điều '
                           f'hướng ({kind_txt}), riêng điều hướng tốn {nav_secs:.0f}s ──\n'
                           + '\n'.join(lines))

    def _req(self, method: str, url: str, *,
             body=None, timeout: int = 10, quiet: bool = False) -> dict:
        """
        HTTP request với logging vào profile_logs.
        quiet=True → chỉ log khi lỗi (dùng cho heartbeat idle để tránh spam).
        Trả về dict JSON response hoặc raise exception.
        """
        short = url.replace(FLOW_SERVER, '[FLOW]')
        t0 = time.time()
        try:
            kw: dict = {'timeout': timeout}
            if body is not None:
                kw['json'] = body
            r = getattr(req_lib, method.lower())(url, **kw)
            ms = round((time.time() - t0) * 1000)
            if r.ok:
                if not quiet:
                    self._log('info', f'{method.upper()} {short} → {r.status_code} ({ms}ms)')
            else:
                snippet = r.text[:300].replace('\n', ' ')
                self._log('warn', f'{method.upper()} {short} → {r.status_code} ({ms}ms): {snippet}')
            return r.json()
        except req_lib.exceptions.Timeout:
            ms = round((time.time() - t0) * 1000)
            self._log('error', f'{method.upper()} {short} TIMEOUT ({ms}ms)')
            raise
        except req_lib.exceptions.ConnectionError as e:
            self._log('error', f'{method.upper()} {short} CONNECTION ERROR: {e}')
            raise
        except Exception as e:
            self._log('error', f'{method.upper()} {short} ERROR: {e}')
            raise

    # ── Chrome driver ─────────────────────────────────────────────────────────

    def _make_driver(self):
        """
        Mở Chrome cho worker.

        Ưu tiên:
        1. Nếu login browser đang mở cho profile này → attach qua debuggerAddress
           (cùng Chrome đã đăng nhập, không mở instance mới — không có conflict)
        2. Không có login Chrome → mở Chrome mới với user-data-dir (session đã lưu)
        3. Nếu có Chrome Portable → dùng portable exe thay vì hệ thống
        """
        from selenium import webdriver

        _patch_selenium_pool_size()

        pid   = self.profile_id
        port  = _chrome_debug_port(pid)

        # CloakBrowser — luồng RIÊNG (server/cloak_browser.py), không đi qua bất
        # kỳ đoạn nào bên dưới. Tắt setting thì luồng cũ chạy y nguyên.
        self._cloak_human = None
        from .cloak_browser import cloak_enabled, open_cloak_driver, cloak_launch_config, CloakHuman
        if cloak_enabled():
            load_ext = self.worker_mode not in ('gemini', 'chatgpt', 'gemini_video', 'gemini_image')
            driver = open_cloak_driver(self.profile, port, load_extensions=load_ext, log_fn=self._log)
            try:
                driver.execute_cdp_cmd('Network.enable', {})
                driver.set_script_timeout(60)
            except Exception:
                pass
            self._install_recaptcha_guard(driver)
            if cloak_launch_config()['humanize']:
                self._cloak_human = CloakHuman(driver)
            return driver

        # Strategy 1: attach vào Chrome ĐANG SỐNG của profile này.
        # ⚠️ (2026-09-04) Điều kiện cũ CHỈ là `pid in _login_drivers` — dict RAM
        # đó bị xoá ngay khi login-thread kết thúc (dù Chrome vẫn sống vì
        # `detach=True`) và mất sạch khi restart client_tool. Mất dấu ⇒ nhánh
        # dưới LAUNCH Chrome mới cùng `--user-data-dir` ⇒ Chrome chuyển yêu cầu
        # sang instance đang chạy và MỞ THÊM CỬA SỔ/TAB. Giờ hỏi thẳng DevTools
        # port nên không còn phụ thuộc trí nhớ của tiến trình.
        if pid in _login_drivers or _chrome_alive_on_port(port):
            self._log('info', f'Chrome của profile đang mở — attach qua debuggerAddress port={port}')
            # Chrome này có thể được mở bởi 1 tiến trình client_tool TRƯỚC (đã
            # restart) — relay SOCKS5 sống trong tiến trình nên phải dựng lại,
            # cổng cố định nên khớp đúng `--proxy-server` Chrome đã nhận.
            if self.profile.get('proxy_server'):
                build_proxy_setup(self.profile.get('proxy_server') or '', pid)
            driver = _connect_to_chrome(port, log_fn=self._log)
            if driver:
                self._log('ok', f'Attached vào Chrome sẵn có (port {port}) — không mở instance mới')
                try:
                    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                        'source': "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                    })
                    driver.execute_cdp_cmd('Network.enable', {})
                except Exception:
                    pass
                self._install_recaptcha_guard(driver)
                return driver
            self._log('warn', 'Attach thất bại — fallback mở Chrome mới')

        # Strategy 2/3: Chrome Portable nếu có, ngược lại dùng system Chrome
        portable_exe = ''
        portables    = _detect_chrome_portables()
        if portables:
            # Map profile theo index (profile 1 → portable 1, ...)
            idx = (pid - 1) % len(portables)
            portable_exe = portables[idx]['exe']
            self._log('info', f'Dùng Chrome Portable: {portable_exe}')

        # Remap profile_dir về path hợp lệ trên OS hiện tại (vd profile tạo trên Windows)
        prof_dir = _usable_profile_dir(self.profile)
        if prof_dir != (self.profile.get('profile_dir') or ''):
            self._log('info', f'profile_dir remap → {prof_dir}')

        # ⚠️ (2026-09-04, fix bug "tự mở 1 đống tab chrome") Tới đây nghĩa là
        # KHÔNG attach được (Strategy 1 ở trên đã thử). Nếu vẫn còn Chrome mồ
        # côi giữ ĐÚNG `--user-data-dir` này thì launch tiếp là sai: Chrome
        # KHÔNG tạo instance thứ 2 cho cùng 1 user-data-dir — nó chuyển yêu cầu
        # sang instance đang chạy, instance đó MỞ THÊM 1 CỬA SỔ/TAB rồi tiến
        # trình vừa gọi thoát. Lặp qua nhiều lần restart worker ⇒ một đống tab.
        # (Chrome mồ côi hay gặp vì login browser mở với `detach=True` — sống
        # tiếp sau khi driver kết thúc — hoặc worker/client_tool bị kill đột ngột.)
        if _chrome_running_for_profile(prof_dir):
            self._log('warn', 'Còn Chrome mồ côi đang giữ profile này nhưng KHÔNG attach '
                              'được — đóng nó trước khi mở lại (nếu không Chrome sẽ đẻ '
                              'thêm tab vào cửa sổ cũ thay vì mở phiên mới).')
            _kill_chrome_for_profile(prof_dir, log_fn=self._log)
            self._sleep(2)

        # gemini/chatgpt/gemini_video/gemini_image mode dùng send_keys() thuần
        # Selenium, không phụ thuộc extension nào — tắt load_extensions để tránh
        # Flow Automation extension (nếu có trong profile) cùng lúc heartbeat/thao
        # tác DOM, gây race condition với tab chat của worker này.
        load_ext = self.worker_mode not in ('gemini', 'chatgpt', 'gemini_video', 'gemini_image')
        ext_paths = em.active_paths() if load_ext else []
        if ext_paths:
            self._log('info', f'Loading {len(ext_paths)} extension(s)')

        # Thử undetected_chromedriver trước (bypass bot-detection → reCAPTCHA token hợp lệ hơn).
        # BỎ QUA trên aarch64: uc không có bản chromedriver ARM để tải, và không patch được
        # snap chromedriver (là wrapper /usr/bin/snap, chỉ đọc) → luôn fail, tốn thời gian.
        driver = None
        if _IS_ARM:
            self._log('info', 'aarch64 — bỏ qua undetected_chromedriver, dùng chromedriver hệ thống/snap')
        else:
            try:
                import undetected_chromedriver as uc
                uc_opts = _build_chrome_options(
                    profile_dir     = prof_dir,
                    debug_port      = port,
                    portable_exe    = portable_exe,
                    load_extensions = load_ext,
                    detach          = False,
                    use_uc          = True,
                    proxy           = self.profile.get('proxy_server') or '',
                    profile_id      = self.profile_id,
                )
                cdp_path = _detect_chromedriver() or None
                # version_main (2026-07-20) — ép uc dùng ĐÚNG major version Chrome
                # đã cài thay vì để nó tự đoán (khi driver_executable_path=None, uc
                # tự tải+patch chromedriver bằng cơ chế RIÊNG, tách biệt Selenium
                # Manager — đã gặp lỗi thật uc đoán sai, tải nhầm chromedriver 151
                # trong khi Chrome cài trên máy là 150, khiến "session not created:
                # This version of ChromeDriver only supports Chrome version 151").
                chrome_ver = _detect_chrome_version(portable_exe)
                # (2026-09-12 (b), fix bug thật — version_main ở trên bị VÔ HIỆU
                # HOÀN TOÀN nếu cdp_path trỏ tới 1 file chromedriver CÓ SẴN: khi
                # driver_executable_path không rỗng, uc.Patcher.auto() chỉ verify
                # file đã "patch" hay chưa rồi DÙNG NGUYÊN, bỏ qua version_main —
                # xem docstring _chromedriver_major_version()) — nếu file cục bộ
                # đang trỏ tới KHÔNG khớp version Chrome vừa dò được (stale, còn
                # sót từ lần cài Chrome trước), bỏ qua nó để uc tự tải đúng bản.
                if cdp_path and chrome_ver:
                    cdp_ver = _chromedriver_major_version(cdp_path)
                    if cdp_ver and cdp_ver != chrome_ver:
                        self._log('warn', f'chromedriver cục bộ "{cdp_path}" là bản '
                                          f'{cdp_ver}, KHÔNG khớp Chrome {chrome_ver} đang '
                                          f'cài — bỏ qua, để uc tự tải đúng bản (version_main).')
                        cdp_path = None
                uc_kwargs = {'options': uc_opts, 'driver_executable_path': cdp_path}
                if chrome_ver:
                    uc_kwargs['version_main'] = chrome_ver
                    self._log('info', f'Chrome version dò được: {chrome_ver} — ép uc dùng đúng bản này')
                driver = uc.Chrome(**uc_kwargs)
                self._log('info', f'Chrome opened via undetected_chromedriver (port={port})')
            except ImportError:
                pass  # thư viện chưa cài → dùng regular
            except Exception as e:
                self._log('warn', f'uc.Chrome failed ({e}) — fallback regular webdriver')
                driver = None

        if driver is None:
            opts = _build_chrome_options(
                profile_dir     = prof_dir,
                debug_port      = port,
                portable_exe    = portable_exe,
                load_extensions = load_ext,
                detach          = False,
                proxy           = self.profile.get('proxy_server') or '',
                profile_id      = self.profile_id,
            )
            driver = webdriver.Chrome(service=_make_service(chrome_binary=portable_exe), options=opts)
            self._log('info', f'Chrome opened via regular webdriver (port={port})')

        # Áp dụng selenium-stealth (JS-level anti-detection, hoạt động trên cả 2 loại driver)
        try:
            from selenium_stealth import stealth as _apply_stealth
            _apply_stealth(driver,
                languages    = ['vi-VN', 'en-US', 'en'],
                vendor       = 'Google Inc.',
                platform     = 'Win32',
                webgl_vendor = 'Intel Inc.',
                renderer     = 'Intel Iris OpenGL Engine',
                fix_hairline = True,
            )
            self._log('info', 'selenium-stealth applied')
        except ImportError:
            pass

        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        })
        try:
            driver.execute_cdp_cmd('Network.enable', {})
        except Exception:
            pass
        try:
            driver.set_script_timeout(60)
        except Exception:
            pass
        self._install_recaptcha_guard(driver)
        return driver

    def _install_recaptcha_guard(self, driver) -> None:
        """Cài `RECAPTCHA_GUARD_JS` cho MỌI lần load trang sau này. Trang đang mở
        sẵn thì đã muộn (bẫy đã giăng) — `_get_fresh_recaptcha()` tự reload 1
        lần khi gặp `RC_TRAPPED`."""
        try:
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',
                                   {'source': RECAPTCHA_GUARD_JS})
            self._rc_guard_installed = True
        except Exception as e:
            self._log('warn', f'Không cài được reCAPTCHA guard: {e}')

    # ── Token capture từ Chrome performance logs ──────────────────────────────

    def _drain_browser_logs(self):
        """Đọc Chrome browser console logs và ghi vào profile log file."""
        if not self.driver:
            return
        try:
            entries = self.driver.get_log('browser')
            for entry in entries:
                level   = entry.get('level', 'INFO').lower()
                source  = entry.get('source', '')
                message = entry.get('message', '')
                # Chỉ log WARNING trở lên để tránh spam; info cho console.log từ labs.google
                lvl_map = {'severe': 'error', 'warning': 'warn', 'info': 'info', 'debug': 'info'}
                mapped  = lvl_map.get(level, 'info')
                if mapped in ('error', 'warn') or 'labs.google' in source:
                    self._log(mapped, f'[browser] [{source}] {message[:300]}')
        except Exception:
            pass

    def _get_fresh_recaptcha(self, action: str = 'IMAGE_GENERATION',
                             _retry_trapped: bool = False) -> str:
        """Lấy reCAPTCHA v3 enterprise token — CÙNG JS với
        tests/utils/flow_session.py::RECAPTCHA_FETCH_JS (đã proven qua
        _test_textToImage.py / _test_textToVideo.py).

        `action`: `'IMAGE_GENERATION'` (ảnh) hoặc `'VIDEO_GENERATION'` (video).
        execute_async_script: siteKey/maxWait/action là args thường, callback
        `done` là arguments CUỐI — KHÔNG đảo (bug 2026-08-12: done=arguments[0]
        khiến grecaptcha.execute('VIDEO_GENERATION') → Invalid site key)."""
        if not self.driver:
            return self._tokens.get('recaptchaToken') or ''
        try:
            info = self._js_async(
                RECAPTCHA_FETCH_JS, LABS_RECAPTCHA_SITE_KEY, 30000, action,
                timeout=45,
            )
        except Exception as e:
            self._log('warn', f'reCAPTCHA refresh: {e}')
            return self._tokens.get('recaptchaToken') or ''
        token = (info or {}).get('token') or ''
        err = (info or {}).get('error') or ''
        if err == 'RC_TRAPPED' and not _retry_trapped:
            # Trang load TRƯỚC khi guard được cài (vd attach vào Chrome đang mở)
            # → hàm công khai đã là bẫy. Cài guard rồi reload để giữ bản gốc.
            self._log('warn', 'reCAPTCHA: hàm execute của trang đã bị thay bằng bẫy '
                              '"extension_hijack_detected" — cài guard + tải lại trang')
            if not getattr(self, '_rc_guard_installed', False):
                self._install_recaptcha_guard(self.driver)
            try:
                self.driver.refresh()
            except Exception as e:
                self._log('warn', f'reload trang lỗi: {e}')
            # reload đổi f.sid — bỏ cache session batchexecute để harvest lại
            self._be_session_cache = None
            time.sleep(4)
            return self._get_fresh_recaptcha(action, _retry_trapped=True)
        if err or not token:
            self._log('warn', f'reCAPTCHA refresh ({action}): {err or "empty token"}')
            return self._tokens.get('recaptchaToken') or ''
        if isinstance(token, str) and len(token) > 20:
            self._tokens['recaptchaToken'] = token
            self._tokens['capturedAt'] = time.time()
            self._log('info', f'Fresh reCAPTCHA ({action}): {token[:20]}…')
            return token
        return self._tokens.get('recaptchaToken') or ''

    def token_age_secs(self) -> float:
        if not self._tokens['capturedAt']:
            return 9999.0
        return time.time() - self._tokens['capturedAt']

    # ── Flow page management ─────────────────────────────────────────────────

    @staticmethod
    def _project_id_from_url(url: str) -> str:
        """Trích `{uuid}` từ `.../project/{uuid}` (bỏ query/fragment). '' nếu
        không phải URL project — dùng để SO SÁNH 2 URL project với nhau mà
        không bị nhiễu bởi query string Google thỉnh thoảng gắn thêm."""
        m = re.search(r'/project/([^/?#]+)', url or '')
        return m.group(1) if m else ''

    def _persist_project_url(self, new_url: str, reason: str = '') -> bool:
        """(2026-08-23) Lưu `project_url` MỚI — điểm DUY NHẤT được phép ghi
        field này, dùng bởi CẢ 4 nơi tạo project mới + bước tự đồng bộ.

        ⚠️ 2 điều bắt buộc, đều là bug thật của bản trước:
        1. **Gán RAM TRƯỚC, ghi DB SAU.** Bản cũ gọi `pm.update()` trước rồi
           mới `self.profile['project_url'] = new_url` — mà `pm.update()`
           KHÔNG best-effort (`_api()` raise `RuntimeError` khi backend chập
           chờn/HTTP lỗi), nên exception làm dòng gán RAM ngay sau đó KHÔNG
           BAO GIỜ chạy: worker đang đứng trên project MỚI nhưng cả DB lẫn RAM
           đều còn trỏ project CŨ.
        2. **Bọc `pm.update()` trong try/except.** Cùng lý do — 1 lần mạng
           chập chờn không được phép làm chết cả batch (exception lan ra khỏi
           `_ensure_flow_page()`/`_rotate_project_if_full()`). Ghi DB thất bại
           thì RAM vẫn đúng ⇒ phiên hiện tại chạy tiếp bình thường, và bước tự
           đồng bộ (`_sync_project_url_from_browser()`) sẽ thử ghi lại ở lần
           `_ensure_flow_page()` kế tiếp.

        Trả `True` nếu đã ghi được xuống DB (bền vững qua restart)."""
        if not new_url or '/project/' not in new_url:
            return False
        if self._bound_url():
            # Chế độ gán: project Flow do project Nano Banana quyết định — không
            # được đổi sang project khác, càng không ghi đè project riêng của profile.
            self._log('warn', f'[flow-bind] Bỏ qua việc đổi project_url sang {new_url} '
                               f'({reason or "?"}) — đang chạy theo project Flow đã gán')
            return False
        self.profile['project_url'] = new_url          # RAM trước — xem (1)
        try:
            pm.update(self.profile_id, project_url=new_url)
            return True
        except Exception as e:
            self._log('warn', f'Lưu project_url mới xuống server thất bại ({e}) — '
                               f'phiên này vẫn dùng {new_url}, sẽ thử lưu lại sau.')
            return False

    def _sync_project_url_from_browser(self, current_url: str):
        """(2026-08-23) Tự đồng bộ: trình duyệt đang đứng ở project KHÁC với
        `project_url` đã lưu ⇒ lưu lại theo cái đang đứng.

        Vá 2 kẽ hở khiến project vừa tạo bị "mồ côi" (Google đã tạo thật nhưng
        không nơi nào ghi lại id, lần chạy sau quay về project cũ):
        - `_wait_for_project_url()` hết hạn (20s) trong khi Google vẫn điều
          hướng sang project mới ngay sau đó.
        - `pm.update()` lỗi mạng đúng lúc lưu (xem `_persist_project_url()`).

        An toàn vì trong worker KHÔNG có đường nào đưa trình duyệt tới 1 project
        khác ngoài 4 nơi tạo mới + `_return_to_saved_project()` — đang đứng ở
        project nào tức là đang LÀM VIỆC trên project đó.

        Chế độ gán project + email: KHÔNG đồng bộ — đứng sai project thì phải quay
        về project đã gán, không phải nhận project đang đứng làm project mới."""
        if self._bound_url():
            return
        cur_id = self._project_id_from_url(current_url)
        if not cur_id:
            return
        if cur_id == self._project_id_from_url(self.profile.get('project_url') or ''):
            return
        self._log('warn', f'Trình duyệt đang ở project khác với project_url đã lưu — '
                           f'tự đồng bộ sang {current_url}')
        self._persist_project_url(current_url, reason='sync-from-browser')

    # ── (2026-09-14) Chạy theo project + email ───────────────────────────────
    # Cài đặt `bind_tasks_to_project_email` (config.py). Backend chỉ giao cho
    # profile task của project Nano Banana gán ĐÚNG email profile, kèm
    # `flowProjectId` — worker vào đúng link Flow đó rồi mới chạy, cả lô 1 project.
    # Ở chế độ này worker KHÔNG BAO GIỜ tạo/luân chuyển/đồng bộ project Flow:
    # project do người dùng gán, sai/không vào được thì báo lỗi, không tự "chữa".

    _FLOW_PROJECT_BASE = 'https://flow.google.com/project'
    _FLOW_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)

    def _bind_mode_enabled(self) -> bool:
        """Cài đặt "chỉ chạy task theo project + email" đang bật VÀ đây là profile
        VEO (api/dom) — Gemini/ChatGPT không dùng project Flow nên không áp dụng.
        Đọc thẳng settings cục bộ (cache RAM) để bật/tắt có tác dụng ngay."""
        if getattr(self, 'worker_mode', '') not in ('api', 'dom'):
            return False
        try:
            return bool(int(get_local_settings().get('bind_tasks_to_project_email', 0) or 0))
        except (TypeError, ValueError):
            return False

    def _api_dom_fallback_enabled(self) -> bool:
        """(2026-09-24) Setting "API lỗi thì chuyển sang DOM" — chỉ có nghĩa với
        profile `worker_mode='api'`. Mặc định TẮT: API mode CHỈ chạy API. Đọc
        tươi settings cục bộ để bật/tắt có tác dụng ngay ở lô kế tiếp."""
        if getattr(self, 'worker_mode', '') != 'api':
            return False
        try:
            return bool(int(get_local_settings().get('api_fallback_to_dom', 0) or 0))
        except (TypeError, ValueError):
            return False

    def _api_task_failed(self, task: dict, err, dom_queue) -> bool:
        """Xử lý 1 task lỗi ở đường API. `dom_queue` là list (setting chuyển DOM
        đang BẬT) → đưa task vào hàng chờ chạy lại bằng DOM, CHƯA báo lỗi về
        server (báo lỗi = server nhả task về hàng chờ, máy khác có thể nhận
        trùng trong lúc ta đang chạy DOM). `dom_queue=None` → báo lỗi như cũ.
        Trả True nếu escalation cho profile ngủ."""
        task_id = task['id']
        msg = str(err)
        if dom_queue is not None:
            self._log('warn', f'[api→dom] Task #{task_id} lỗi API ({msg[:200]}) '
                              f'→ sẽ chạy lại bằng DOM')
            if all(t['id'] != task_id for t in dom_queue):
                dom_queue.append(task)
            return False
        self._log('error', f'Task #{task_id} lỗi API: {msg}')
        self._report_task_error(task_id, msg)
        return self._handle_task_error(msg)

    def _run_dom_fallback(self, tasks: list) -> bool:
        """Chạy lại các task lỗi API bằng luồng DOM (`_run_tasks_batch()` —
        configure → đính ảnh tham chiếu → gõ prompt → reconcile). Trả True nếu
        escalation cho profile ngủ."""
        if not tasks:
            return False
        ids = ', '.join(f"#{t['id']}" for t in tasks)
        self._log('warn', f'[api→dom] Chạy lại {len(tasks)} task bằng DOM: {ids}')
        try:
            return bool(self._run_tasks_batch(tasks))
        except Exception as e:
            msg = f'Chạy DOM (fallback từ API) lỗi: {e}'
            self._log('error', f'[api→dom] {msg}')
            for t in tasks:
                self._report_task_error(t['id'], msg)
            return bool(self._handle_task_error(msg))

    def _bound_url(self) -> str:
        """Link project Flow đang gán cho lô hiện tại ('' = không gán)."""
        return getattr(self, '_bound_flow_url', '') or ''

    def _apply_flow_binding(self, tasks: list):
        """Đặt/bỏ project Flow đã gán theo lô `tasks` — gọi đầu `_process_tasks()`.

        Chỉ đổi `project_url` TRONG RAM (không ghi DB) để mọi đường điều hướng/
        reconcile/cache upload sẵn có tự dùng đúng project này. Lô không gán (hoặc
        tắt chế độ) thì trả lại `project_url` riêng của profile."""
        fid = ''
        if tasks and self._bind_mode_enabled():
            fid = str(tasks[0].get('flowProjectId') or '').strip().lower()
            if fid and not self._FLOW_UUID_RE.match(fid):
                self._log('warn', f'[flow-bind] Flow ID của project không hợp lệ: "{fid}" — bỏ qua gán')
                fid = ''
        cur = self._bound_url()
        if fid:
            url = f'{self._FLOW_PROJECT_BASE}/{fid}'
            if getattr(self, '_own_project_url', None) is None:
                self._own_project_url = self.profile.get('project_url') or ''
            if url != cur:
                email = str(tasks[0].get('flowEmail') or '').strip()
                self._log('info', f'[flow-bind] Lô này thuộc project Flow đã gán {url} '
                                  f'(email {email or "?"}) — chỉ làm việc trên project này')
                acc = (self.profile.get('account_email') or '').strip().lower()
                if email and acc and email.lower() != acc:
                    self._log('warn', f'[flow-bind] Email của project ({email}) KHÁC email '
                                      f'profile ({acc}) — ảnh tham chiếu có thể bị chặn upload')
            self._bound_flow_url = url
            self.profile['project_url'] = url
        elif cur:
            self._log('info', '[flow-bind] Thôi chạy theo project đã gán — quay về project riêng của profile')
            self.profile['project_url'] = getattr(self, '_own_project_url', None) or ''
            self._bound_flow_url = ''

    def _ensure_bound_flow_page(self):
        """Đưa trình duyệt vào ĐÚNG project Flow đã gán. KHÔNG tạo project mới —
        không vào được thì chỉ báo lỗi (caller thấy trang chưa sẵn sàng và báo
        lỗi task như mọi trường hợp kẹt trang khác)."""
        bound = self._bound_url()
        bid = self._project_id_from_url(bound)
        try:
            current = self.driver.current_url or ''
        except Exception:
            current = ''
        if self._project_id_from_url(current) == bid and self._project_page_ready():
            return
        if not self._ensure_google_login(bound):
            self._log('error', '[flow-bind] Đăng nhập Google thất bại/chưa cấu hình — không vào '
                               'được project Flow đã gán. Xem log [google-login] ở trên.')
            return
        self._click_create_with_flow_if_present()
        if self._project_page_ready():
            self._log('ok', f'[flow-bind] ✔ Đã vào project Flow đã gán: {bound}')
            return
        if self._return_to_saved_project(attempts=3) and self._project_page_ready():
            self._log('ok', f'[flow-bind] ✔ Đã vào project Flow đã gán: {bound}')
            return
        self._log('error', f'[flow-bind] Không vào được project Flow đã gán ({bound}) — KHÔNG '
                           'tạo project mới. Kiểm tra Flow ID của project Nano Banana có đúng '
                           'project của tài khoản này không.')

    def _flow_project_media_names(self, project_id: str, max_age: float = 120):
        """Tập id media (`meta[4]` = DETAIL_UUID) đang có trong project Flow —
        RPC `Zzl0ze` qua session của CHÍNH tài khoản đang mở (= email đã gán),
        cache `max_age` giây/project. Dùng để xác nhận 1 id ảnh tham chiếu (đã
        lưu hoặc đã upload trước đó) THẬT SỰ còn nằm trong project đang làm
        việc trước khi dùng lại.

        Trả `set` (có thể rỗng) khi đọc được; `None` khi KHÔNG kiểm tra được
        (đường batchexecute đang tắt, không có session, RPC lỗi) — caller phân
        biệt "đã tra mà không thấy" với "không tra được"."""
        if not project_id:
            return None
        lock = getattr(self, '_flow_listing_lock', None) or threading.Lock()
        with lock:
            cache = getattr(self, '_flow_listing_cache', None)
            if cache is None:
                cache = self._flow_listing_cache = {}
            hit = cache.get(project_id)
            if hit and time.time() - hit[0] < max_age:
                return hit[1]
            try:
                session = self._be_session()
                if not session:
                    return None
                self._log('info', f'  🔎 Đọc danh sách uuid media project Flow {project_id[:8]}… (Zzl0ze)')
                raw = self._batchexecute_call(
                    'Zzl0ze', [f'projects/{project_id}', None, None, None, [1]], session)
                data = self._batchexecute_parse(raw, 'Zzl0ze') if raw else None
                if not (isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list)):
                    if data is None:
                        return None
                    data = [None, []]
                names = self._flow_listing_names(data[1])
            except Exception as e:
                self._log('warn', f'Đọc danh sách media project Flow lỗi — không kiểm tra được id ảnh tham chiếu: {e}')
                return None
        self._flow_listing_store(project_id, names)
        return names

    @staticmethod
    def _flow_listing_names(entries) -> set:
        """Tập `meta[4]` (DETAIL_UUID, chữ thường) từ danh sách entry `Zzl0ze`."""
        names: set = set()
        for entry in entries or []:
            meta = entry[3] if isinstance(entry, list) and len(entry) > 3 else None
            if isinstance(meta, list) and len(meta) > 4 and meta[4]:
                names.add(str(meta[4]).lower())
        return names

    def _flow_listing_store(self, project_id: str, names: set):
        """Lưu danh sách vừa đọc ĐỦ vào cache RAM + file chỉ mục
        `flow_media_index.json` (ghi đè — id đã bị xoá khỏi project tự rơi ra)."""
        if not project_id or names is None:
            return
        cache = getattr(self, '_flow_listing_cache', None)
        if cache is None:
            cache = self._flow_listing_cache = {}
        cache[project_id] = (time.time(), set(names))
        try:
            flow_media_index.replace_project_names(project_id, names)
        except Exception as e:
            self._log('warn', f'Ghi chỉ mục uuid project Flow lỗi (bỏ qua): {e}')

    def _flow_listing_add(self, project_id: str, name: str):
        """Ghi nhận 1 id vừa upload vào cache RAM + file chỉ mục — lần cần kế
        tiếp tìm thấy ngay, không phải đọc lại danh sách."""
        if not project_id or not name:
            return
        cache = getattr(self, '_flow_listing_cache', None)
        hit = cache.get(project_id) if cache else None
        if hit:
            hit[1].add(str(name).lower())
        try:
            flow_media_index.add_project_names(project_id, [name])
        except Exception:
            pass

    def _flow_id_in_project(self, name: str, project_id: str, listing: dict):
        """`True`/`False` = id có/không có trong project Flow; `None` = không
        kiểm tra được. `listing` là hộp dùng chung cho 1 lượt ảnh tham chiếu.

        (2026-09-15) Tra FILE chỉ mục `flow_media_index.json` trước — có thì
        dùng luôn, KHÔNG gọi RPC. Chỉ khi không có (hoặc chưa từng đọc project
        này) mới đọc lại danh sách thật từ Flow, tối đa 1 lần/lượt, rồi ghi đè
        chỉ mục."""
        key = name.lower()
        if 'index' not in listing:
            try:
                listing['index'] = flow_media_index.get_project_names(project_id)
            except Exception:
                listing['index'] = None
        idx = listing['index']
        if idx is not None and key in idx:
            return True
        if not listing.get('fetched'):
            listing['fetched'] = True
            fresh = self._flow_project_media_names(project_id, max_age=5)
            listing['names'] = fresh
            if fresh is not None:
                listing['index'] = fresh
        names = listing.get('names')
        if names is None:
            return None
        return key in names

    def _saved_flow_media_name(self, item: dict, project_id: str, listing: dict) -> str:
        """Id Flow đã lưu của 1 ảnh tham chiếu, NẾU dùng lại được trong project
        `project_id` (khỏi upload).

        (2026-09-15) Id nào cũng phải TÌM THẤY trong danh sách media của đúng
        project Flow đó (qua tài khoản đang mở) mới dùng lại — không thấy thì
        trả '' để upload ảnh tham chiếu như bình thường. Riêng khi KHÔNG đọc
        được danh sách (RPC lỗi, đường batchexecute tắt): id có dấu đúng
        project vẫn dùng (tránh upload hàng loạt dính giới hạn nhịp), id chưa
        có dấu thì upload."""
        name = str((item or {}).get('flowMediaName') or '').strip()
        if not project_id or not self._FLOW_UUID_RE.match(name):
            return ''
        stamp = str((item or {}).get('flowProjectId') or '').strip().lower()
        if stamp and stamp != project_id.lower():
            return ''                         # id của project khác — không bao giờ có ở đây
        found = self._flow_id_in_project(name, project_id, listing)
        if found is None:
            return name if stamp else ''
        if not found:
            listing.setdefault('missing', []).append(name)
        return name if found else ''

    def _return_to_saved_project(self, attempts: int = 3) -> bool:
        """(2026-08-23, FIX BUG THẬT user báo: *"xóa cookie đăng nhập lại tới
        bước click create xong phải vào lại link dự án cũ... hiện tại hình như
        login lại là đã tạo project mới"*.)

        Kiên trì quay lại ĐÚNG `profile.project_url` đã lưu. Trả `True` nếu
        cuối cùng đứng được trên 1 trang `/project/`.

        **Vì sao cần hàm riêng thay vì 1 lần `driver.get()`:** ngay sau khi xoá
        SẠCH cookie + đăng nhập lại, `driver.get(project_url)` lần đầu rất hay
        BỊ BẬT RA trang chung (session mới chưa "ấm", Google chưa gắn project
        vào tài khoản vừa đăng nhập), hoặc lại hiện màn hình xen giữa "Create
        with Google Flow" (URL VẪN giữ `/project/{uuid}` suốt lúc đó — xem
        `_click_create_with_flow_if_present()`). Code cũ chỉ thử ĐÚNG 1 LẦN rồi
        rơi thẳng xuống nhánh bấm "New project" ⇒ **tạo project mới ngay sau mỗi
        lần đăng nhập lại**, trong khi project cũ vẫn còn nguyên và chưa hề đạt
        ngưỡng `max_project_media_items` (300). Đây CHÍNH LÀ triệu chứng user
        báo.

        Mỗi vòng: `driver.get(saved)` → xử lý màn hình xen giữa → đọc lại URL.
        Bấm nút xen giữa xong mà bị đẩy ra khỏi `/project/` thì vòng kế tiếp tự
        `driver.get(saved)` lại (đó là lý do dùng vòng lặp chứ không phải chuỗi
        if lồng nhau).

        Chỉ khi HẾT `attempts` mà vẫn không vào được mới trả `False` — lúc đó
        caller được phép coi project cũ là hỏng/đã bị xoá và tạo project mới.
        """
        saved = (self.profile.get('project_url') or '').strip()
        if '/project/' not in saved:
            return False   # chưa từng có project nào — caller tự tạo mới
        for i in range(1, attempts + 1):
            try:
                self._nav_get(saved, f'vào lại project đã lưu (lần {i}/{attempts})')
                self._sleep(4)
            except Exception as e:
                self._log('warn', f'[project] Vào lại project cũ lỗi (lần {i}/{attempts}): {e}')
                continue
            # (2026-09-24) Bị đá về flow.google.com/about (phiên hết hiệu lực,
            # thường do đổi proxy) → chạy lại quy trình check đăng nhập rồi vào
            # lại đúng project, thay vì coi project là hỏng.
            if self._on_flow_about_page():
                if not self._relogin_after_flow_about(saved):
                    return False
            # Màn hình xen giữa có thể hiện LẠI ở mỗi lần vào (session mới) —
            # phải check mỗi vòng, không chỉ vòng đầu.
            self._click_create_with_flow_if_present()
            try:
                current = self.driver.current_url or ''
            except Exception:
                current = ''
            if '/project/' in current:
                if i > 1:
                    self._log('ok', f'✔ Đã quay lại project cũ sau {i} lần thử: {saved}')
                return True
            self._log('warn', f'[project] Sau khi vào {saved} lại bị đẩy về trang chung '
                               f'({current or "?"}) — thử lại ({i}/{attempts})')
        self._log('warn', f'[project] Không vào lại được project cũ sau {attempts} lần thử '
                           f'({saved}) — coi như project đã bị xoá/hết hạn.')
        return False

    def _lookup_shared_project_url_by_email(self) -> str:
        """(2026-09-11) "url ghi nhớ project flow lưu theo email... tránh tạo
        quá nhiều project không cần thiết" — CHỈ gọi khi profile CHƯA TỪNG có
        `project_url` riêng (bootstrap lần đầu, xem 2 call site ở
        `_ensure_flow_page()`/`_ensure_flow_project()`). Tra theo
        `account_email` của CHÍNH profile này qua `pm.get_shared_project_url()`
        (bảng GLOBAL `flow_account_projects`, KHÔNG scope theo máy — dùng chung
        cho MỌI profile/PC đăng nhập CÙNG Gmail). Trả `''` nếu không có email
        hoặc chưa có gì remembered — caller tự rơi xuống nhánh "tạo project
        mới" như cũ, KHÔNG raise."""
        email = (self.profile.get('account_email') or '').strip()
        if not email:
            return ''
        try:
            url = pm.get_shared_project_url(email)
        except Exception as e:
            self._log('warn', f'Tra project_url theo email thất bại (best-effort): {e}')
            return ''
        if url:
            self._log('info', f'[project] Tìm thấy project đã dùng trước cho '
                               f'"{email}" trên 1 PC/profile khác — dùng lại: {url}')
        return url

    def _try_reuse_or_bootstrap_project(self) -> bool:
        """(2026-09-11, theo yêu cầu user "chỉ check trường hợp quá setting...
        300 media thì tạo project mới còn lại không được tạo mới") — GỌI KHI
        `_return_to_saved_project()` đã thất bại (hoặc profile CHƯA TỪNG có
        `project_url`). Trả `True` nếu ĐÃ đứng được trên 1 trang `/project/`
        (dùng lại project của CÙNG email, hoặc tạo project ĐẦU TIÊN thật sự —
        2 trường hợp DUY NHẤT ngoài ngưỡng `max_project_media_items` còn được
        tạo project mới); `False` nếu profile ĐÃ TỪNG có `project_url` riêng
        nhưng giờ không vào lại được (caller KHÔNG được tạo mới trong trường
        hợp này — rất có thể do Google đang chặn/throttle tài khoản tạm thời,
        không phải project đã mất, xem `_return_to_saved_project()`)."""
        if self._bind_mode_enabled():
            # Chế độ gán: project Flow lấy theo project Nano Banana của từng lô
            # task — không tự dùng lại/tạo project Flow nào lúc khởi động.
            self._log('info', '[flow-bind] Chế độ "chạy theo project + email" — không tự tạo '
                              'project Flow, chờ lô task để vào đúng project đã gán')
            return False
        had_own_url = '/project/' in (self.profile.get('project_url') or '')
        if had_own_url:
            self._log('error', 'Không vào lại được project đã lưu sau nhiều lần thử — '
                               'KHÔNG tạo project mới (chỉ ngưỡng max_project_media_items '
                               'mới được tạo mới). Có thể do Google đang chặn/throttle tài '
                               'khoản tạm thời — sẽ tự thử lại ở lần sau.')
            return False

        # Chưa từng có project nào (profile MỚI) — thử dùng lại project đã có
        # của CÙNG tài khoản (PC/profile khác) trước khi đành phải tạo mới.
        shared_url = self._lookup_shared_project_url_by_email()
        if shared_url:
            try:
                self._nav_get(shared_url, 'profile chưa có project — dùng lại project chung theo email')
                self._sleep(4)
                self._click_create_with_flow_if_present()
                current = self.driver.current_url or ''
            except Exception as e:
                self._log('warn', f'[project] Vào project dùng chung lỗi: {e} — tạo project mới.')
                current = ''
            if '/project/' in current:
                self._persist_project_url(current, reason='reuse-by-email')
                self._log('ok', f'✔ Dùng lại project chung theo email: {current}')
                return True
            self._log('warn', '[project] Không vào được project dùng chung theo email — tạo project mới.')

        self._log('warn', 'Chưa từng có project nào (profile mới, không có project chung '
                          'theo email) — tạo project mới.')
        if self._click_new_project_button():
            new_url = self._wait_for_project_url(
                timeout=20, exclude_url=self.profile.get('project_url') or '')
            if new_url:
                self._persist_project_url(new_url, reason='new-project')
                self._log('ok', f'✔ Đã tạo project mới: {new_url}')
                return True
            self._log('warn', 'Bấm "New project" nhưng không bắt được URL project mới sau 20s')
        else:
            self._log('warn', 'Không tìm thấy nút "New project" trên trang chung')
        return False

    def _ensure_flow_page(self):
        """Navigate về Flow project nếu chưa ở đó. Nếu vẫn rơi vào trang chung (không
        có /project/) — kể cả sau khi navigate, hoặc đã ở labs.google từ trước nhưng
        đúng lúc là trang chung — tự bấm "New project" (xem _ensure_flow_project,
        cùng logic, dùng chung 2 helper _click_new_project_button/_wait_for_project_url)
        vì Google Flow không còn tự tạo/redirect sang project mới nữa."""
        if self._bound_url():
            return self._ensure_bound_flow_page()
        url = (self.profile.get('project_url') or '').strip() or FLOW_PROJECT_URL
        try:
            current = self.driver.current_url or ''
        except Exception:
            current = ''
        if 'labs.google' not in current:
            self._log('info', f'Navigate → {url}')
            # (2026-08-08, FIX THẬT — user báo "cứ vào account có input ko
            # chịu mà cứ đá sang flow mặc dù chưa login") — TRƯỚC ĐÂY không
            # check kết quả trả về của `_ensure_google_login()`: nếu login
            # thất bại (thiếu mật khẩu đã lưu, hoặc cần xác minh thêm),
            # `current_url` vẫn còn ở accounts.google.com (không chứa
            # '/project/') — code phía dưới (nhánh "bấm xong lỡ điều hướng ra
            # khỏi URL project ban đầu") hiểu NHẦM đây là trường hợp bấm
            # "Create with Google Flow" bị lệch, TỰ Ý `driver.get(url)` LẦN
            # NỮA bất kể có đăng nhập được hay không — khiến worker cứ "đá
            # sang flow" dù rõ ràng vẫn đang đứng ở trang đăng nhập với ô
            # nhập email/mật khẩu chưa điền gì. Giờ return SỚM nếu đăng nhập
            # thất bại — mọi bước sau đều vô nghĩa khi chưa đăng nhập được.
            if not self._ensure_google_login(url):
                self._log('error', 'Không thể tiếp tục — đăng nhập Google thất bại/chưa '
                                    'cấu hình. Xem log [google-login] ở trên để biết chi tiết.')
                return
            # (2026-08-10) Nếu url là project cụ thể, đôi khi Google chặn 1
            # màn hình xen giữa bắt bấm "Create with Google Flow" trước khi
            # thực sự vào được — xem docstring `_click_create_with_flow_if_present()`.
            if '/project/' in url:
                self._click_create_with_flow_if_present()
            try:
                current = self.driver.current_url or ''
            except Exception:
                current = ''
            # Bấm xong lỡ điều hướng ra khỏi URL project ban đầu (hiếm) —
            # quay lại đúng url cũ theo đúng yêu cầu "click rồi quay lại url
            # project như cũ".
            if '/project/' in url and '/project/' not in current:
                self._nav_get(url, 'sau bước "Create with Google Flow" không còn ở /project/ — '
                                   'quay lại project')
                self._sleep(4)
                try:
                    current = self.driver.current_url or ''
                except Exception:
                    current = ''

        if '/project/' in current:
            # (2026-08-23) Đã ở 1 trang project rồi — nếu KHÁC cái đã lưu thì
            # đồng bộ lại, xem `_sync_project_url_from_browser()`.
            self._sync_project_url_from_browser(current)
            return

        # (2026-09-11, theo yêu cầu user "chỉ check trường hợp quá setting...
        # 300 media thì tạo project mới còn lại không được tạo mới") — project
        # MỚI CHỈ còn được tạo ở 2 trường hợp (xem
        # `_try_reuse_or_bootstrap_project()`): dùng lại project chung theo
        # email, hoặc bootstrap THẬT SỰ lần đầu (chưa từng có project nào, kể
        # cả của PC/profile khác cùng email) — KHÔNG còn tạo mới chỉ vì 1 lần
        # vào lại project cũ bị lỗi/timeout (rất có thể do Google throttle tạm
        # thời tài khoản, không phải project đã mất — tạo thêm project không
        # sửa được gì, chỉ tạo thêm rác). Ngưỡng `max_project_media_items`
        # (300) vẫn là nơi DUY NHẤT luân chuyển project có chủ đích
        # (`_rotate_project_if_full()`).
        if self._return_to_saved_project():
            return
        self._try_reuse_or_bootstrap_project()

    # ── Build API request ────────────────────────────────────────────────────

    # ── UI-driven image generation (Selenium + fetch interceptor) ─────────────

    def _click_new_project_button(self, timeout: int = 15) -> bool:
        """Trên trang chung labs.google/fx/.../tools/flow (chưa vào project nào cả),
        Google Flow KHÔNG còn tự tạo/redirect sang project mới nữa — phải bấm nút
        "New project"/"Dự án mới" thủ công. Tìm nút qua textContent (KHÔNG dùng CSS
        class vì class hash tự sinh bởi styled-components, đổi mỗi lần Google
        deploy lại UI).

        FIX i18n (2026-07-17): trước đây chỉ so khớp CHUỖI TIẾNG ANH CỨNG
        'New project' — nếu tài khoản Google hiển thị UI tiếng Việt, nút thật là
        "Dự án mới", check này KHÔNG BAO GIỜ khớp. Khớp đúng log thật đã quan sát
        (`[warn] Không tìm thấy nút "New project" trên trang chung` lặp lại nhiều
        lần cho 1 profile cụ thể trong lúc 2 profile chạy đồng thời) — 1 trong 2
        profile rất có thể dùng tài khoản tiếng Việt. Giờ so với danh sách biến
        thể `newProject` trong i18n_texts.json (xem get_i18n_texts())."""
        variants_js = json.dumps(get_i18n_texts()['newProject'])
        deadline = time.time() + timeout
        while time.time() < deadline:
            el = self._js(f"""
                var variants = {variants_js};
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {{
                    var t = (btns[i].textContent || '');
                    if (variants.some(function(v){{ return t.indexOf(v) !== -1; }})) {{
                        return btns[i];
                    }}
                }}
                return null;
            """)
            if el:
                self._fire_click(el)
                return True
            self._sleep(1)
        return False

    def _click_create_with_flow_if_present(self, timeout: int = 6) -> bool:
        """(2026-08-10, theo yêu cầu user) Khi navigate THẲNG tới URL của 1
        project cụ thể (dạng `/project/{uuid}`), Google Flow ĐÔI KHI hiện 1
        màn hình xen giữa bắt bấm nút "Create with Google Flow" trước khi
        thực sự vào được project — URL trên thanh địa chỉ VẪN giữ nguyên
        `/project/{uuid}` suốt lúc này, nên check `'/project/' not in current`
        ở `_ensure_flow_page()`/`_ensure_flow_project()` KHÔNG phát hiện được
        case này (tưởng đã vào project rồi nhưng thực ra còn kẹt ở màn hình
        xen giữa, chưa có Slate CE để gõ prompt).

        Match theo `textContent` (KHÔNG dùng class — `sc-fe61cac2-1 iwEYmY`/
        `sc-fe61cac2-0 dagixW` trong HTML user gửi là styled-components hash
        tự sinh, đổi mỗi lần Google deploy lại UI, xem lịch sử các bug DOM-
        detection khác trong project này), cùng pattern `_click_new_project_button()`.
        Không phải LÚC NÀO cũng xuất hiện — poll ngắn rồi bỏ qua nếu không
        thấy, KHÔNG log warning (bình thường, không phải lỗi). Caller chịu
        trách nhiệm xác nhận lại `/project/` còn đúng URL mong muốn sau khi
        gọi hàm này (xem 2 call site)."""
        variants_js = json.dumps(get_i18n_texts()['createWithFlow'])
        deadline = time.time() + timeout
        while time.time() < deadline:
            el = self._js(f"""
                var variants = {variants_js};
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {{
                    var t = (btns[i].textContent || '');
                    if (variants.some(function(v){{ return t.indexOf(v) !== -1; }})) {{
                        return btns[i];
                    }}
                }}
                return null;
            """)
            if el:
                self._fire_click(el)
                self._log('info', '✔ Bấm "Create with Google Flow" để vào project')
                self._sleep(3)
                return True
            self._sleep(1)
        return False

    def _wait_for_project_url(self, timeout: int = 30, exclude_url: str = '') -> str:
        """Poll current_url tới khi vào được /project/{uuid}, trả '' nếu timeout.

        (2026-08-23) `exclude_url` — BỎ QUA nếu URL bắt được trỏ về ĐÚNG project
        đang loại trừ (thường là project CŨ). Không có tham số này, bấm "New
        project" mà Google không thật sự tạo gì (hoặc điều hướng ngược về project
        cũ) sẽ khiến hàm trả về URL CŨ ngay lần poll đầu, caller tưởng đã tạo
        project mới thành công — báo "✔ Đã tạo project mới" SAI, và riêng
        `_rotate_project_if_full()` còn reset luôn bộ đếm media về 0 trong khi
        vẫn đang ở project đã đầy."""
        exclude_id = self._project_id_from_url(exclude_url)
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._sleep(2)
            try:
                cur = self.driver.current_url or ''
            except Exception:
                continue
            if '/project/' in cur and 'labs.google' in cur:
                if exclude_id and self._project_id_from_url(cur) == exclude_id:
                    continue   # vẫn là project cũ — chưa phải project MỚI
                return cur
        return ''

    # ── Tự đăng nhập lại Google (2026-08-08) ────────────────────────────────
    #
    # Theo yêu cầu user: "sau khi xóa cache lần chạy sau phải login
    # accounts.google.com dùng email/pass để đăng nhập mail xong mới về lại
    # task hiện tại để chạy tiếp" — tính năng "Làm mới profile — xóa sạch"
    # (§11.29, `pm.clear_browser_data()`) xoá TOÀN BỘ nội dung `profile_dir`
    # kể cả session Google đã đăng nhập. Lần chạy kế tiếp, MỌI navigate tới
    # labs.google/gemini.google.com/chatgpt.com đều bị Google tự redirect
    # sang `accounts.google.com` để đăng nhập lại — trước đây worker sẽ kẹt
    # vô thời hạn ở đó (không tìm thấy UI mong đợi → timeout/lỗi liên tục),
    # cần user tự mở "Login browser" đăng nhập tay. Giờ tự phát hiện + tự
    # đăng nhập bằng `account_email`/`account_password` đã lưu trên profile
    # (xem `gui/profile_dialog.py`'s field "Mật khẩu" + CLAUDE.md §11.30),
    # rồi tự quay lại ĐÚNG trang đang định vào để tiếp tục task như bình
    # thường — không cần user can thiệp thủ công.
    # (2026-08-08, FIX THẬT — user báo "đang check login account Google chưa
    # chính xác", yêu cầu tự mở profile huavantien84 lấy đúng input + tham
    # khảo extensions/panel/panel.js) — bản đầu ở trên đoán SAI 2 chỗ, xác
    # nhận qua dump DOM THẬT (script `tests/_probe_google_login_dom.py`, Chrome
    # mới hoàn toàn — KHÔNG đụng profile_dir thật của huavantien84 — chỉ gõ
    # EMAIL thật huavantien84@gmail.com để trang login load đúng ngữ cảnh tài
    # khoản đó, KHÔNG BAO GIỜ gõ/đoán mật khẩu):
    #   1. Ô nhập email THẬT có `id="identifierId"` `name="identifier"`
    #      nhưng **`type="text"`** — KHÔNG PHẢI `type="email"` như bản đầu
    #      đoán (selector `input[type="email"]` KHÔNG BAO GIỜ khớp được element
    #      thật, đây là nguyên nhân gốc user báo "chưa chính xác").
    #   2. Nút "Next" (jsname="LgbsSe") KHÔNG hề có id `identifierNext`/
    #      `passwordNext` nào cả (`id=""` rỗng) — hoàn toàn không tồn tại
    #      trong DOM thật hiện tại, dù đây là cách rất phổ biến được các bài
    #      hướng dẫn Selenium cũ chỉ (có thể đúng ở phiên bản UI cũ của
    #      Google, giờ đã đổi). CHỈ so khớp được qua TEXT.
    #   3. Ô nhập mật khẩu `input[type="password"]` (name="Passwd") ĐÚNG như
    #      bản đầu — nhưng bước 1 (email) CŨNG có sẵn 1 input ẩn
    #      `name="hiddenPassword" type="password"` (decoy/autofill-prevention)
    #      — PHẢI lọc theo visibility (`getClientRects().length>0`), không thì
    #      có thể khớp nhầm field ẩn này nếu gọi sai thời điểm.
    # `extensions/panel/panel.js`'s tính năng "Relogin Gmail" (PROVEN, chạy
    # thật trong production) xác nhận ĐÚNG hướng khắc phục: KHÔNG dùng ID cho
    # nút Next, chỉ so khớp theo TEXT với danh sách từ khoá đa ngôn ngữ
    # (`'tiếp theo'`,`'next'`,`'đăng nhập'`,`'sign in'`,`'continue'` — so
    # chính xác HOẶC bắt đầu bằng, không phân biệt hoa/thường) — áp dụng lại
    # y hệt logic đó ở đây (khác biệt duy nhất: extension chạy qua
    # `chrome.scripting.executeScript`/click JS thường, ở đây vẫn dùng
    # `_cdp_click_el()` — CDP click thật, đáng tin cậy hơn — xem lý do click
    # JS-only không tin cậy được ghi ở nhiều nơi khác trong file này).
    _GOOGLE_NEXT_KEYWORDS = ('tiếp theo', 'next', 'đăng nhập', 'sign in', 'continue')

    def _click_google_next(self, step: str = '') -> bool:
        """Click nút "Tiếp theo"/"Next"/"Sign in" trên trang đăng nhập Google —
        CHỈ so khớp theo TEXT (không có id ổn định nào tồn tại trong DOM thật,
        xem giải thích ở comment class-level ngay trên). `step` giữ lại làm
        tham số cho khả năng đọc code tại call site (không ảnh hưởng logic)."""
        btn = self._wait_js("""
            var keywords = arguments[0];
            var all = [...document.querySelectorAll('button')]
                .filter(function(el){ return el.getClientRects().length>0; });
            return all.find(function(el){
                var t = (el.textContent || '').trim().toLowerCase();
                return keywords.some(function(k){ return t === k || t.indexOf(k) === 0; });
            }) || null;
        """, (list(self._GOOGLE_NEXT_KEYWORDS),), timeout=6.0)
        if not btn:
            return False
        self._cdp_click_el(btn)
        return True

    # ── 2FA Google Authenticator (2026-09-24, CLAUDE.md §11.58) ─────────────
    # Theo yêu cầu user "mỗi lần đổi proxy tài khoản google phải xác nhận...
    # đọc tài khoản 2FA google authentication để tự điền". Profile lưu khoá bí
    # mật base32 (`account_totp_secret`), `pyotp` sinh mã 6 số y hệt app
    # Authenticator. `_google_signin_flow()` đọc trang ĐANG HIỆN rồi làm đúng
    # bước đó (email / mật khẩu / mã 2FA / "Thử cách khác" → chọn Authenticator)
    # thay vì chạy cứng 1 chuỗi — trang xác minh sau khi đổi proxy có thể bắt
    # đầu thẳng ở bước mật khẩu hoặc bước 2FA, không qua bước email.
    _GOOGLE_TRY_ANOTHER_KEYWORDS = ('try another way', 'thử cách khác')
    _GOOGLE_SIGNIN_STATE_JS = """
        var email = (arguments[0] || '').toLowerCase();
        var vis = function(el){ return !!el && el.getClientRects().length > 0; };
        var q = function(s){ return [...document.querySelectorAll(s)].find(vis) || null; };
        var path = location.pathname || '';
        var totp = q('#totpPin, input[name="totpPin"]');
        if (!totp && path.indexOf('/challenge/totp') !== -1)
            totp = q('input[type="tel"], input[type="text"], input[type="number"]');
        if (totp) return {step: 'totp', el: totp};
        var pw = [...document.querySelectorAll('input[type="password"], input[name="Passwd"]')]
            .find(function(el){ return vis(el) && el.name !== 'hiddenPassword'; });
        if (pw) return {step: 'password', el: pw};
        var em = q('#identifierId, input[name="identifier"], input[type="email"]');
        if (em) return {step: 'email', el: em, value: em.value || ''};
        if (email) {
            var acct = [...document.querySelectorAll('[data-identifier]')].find(function(el){
                return vis(el) && (el.getAttribute('data-identifier') || '').toLowerCase() === email;
            });
            if (acct) return {step: 'account', el: acct};
        }
        var opt = q('[data-challengetype="6"]');
        if (!opt) opt = [...document.querySelectorAll('[role="link"], li, button, div[data-challengeid]')]
            .find(function(el){
                return vis(el) && /authenticator/i.test(el.textContent || '') && (el.textContent || '').length < 200;
            }) || null;
        if (opt) return {step: 'choose_totp', el: opt};
        var kw = arguments[1] || [];
        var another = [...document.querySelectorAll('button, [role="button"], [role="link"]')]
            .find(function(el){
                var t = (el.textContent || '').trim().toLowerCase();
                return vis(el) && kw.some(function(k){ return t.indexOf(k) === 0; });
            });
        if (another) return {step: 'try_another', el: another};
        return {step: 'unknown'};
    """

    def _totp_code(self) -> str:
        """Mã 6 số hiện tại từ khoá bí mật đã lưu, '' nếu chưa cấu hình/khoá hỏng."""
        secret = (self.profile.get('account_totp_secret') or '').replace(' ', '').strip().upper()
        if not secret:
            return ''
        try:
            import pyotp
            return pyotp.TOTP(secret).now()
        except Exception as e:
            self._log('error', f'[google-login] Khoá 2FA không hợp lệ ({e}) — kiểm tra lại '
                                f'ô "Khoá 2FA" trong profile (chuỗi base32, không phải mã 6 số).')
            return ''

    def _on_google_challenge(self) -> bool:
        try:
            return 'accounts.google.com' in (self.driver.current_url or '')
        except Exception:
            return False

    def _on_flow_about_page(self) -> bool:
        """(2026-09-24) Trang giới thiệu `https://flow.google.com/about` — Flow
        đá về đây khi vào link project mà phiên đăng nhập không còn hợp lệ
        (hay gặp ngay sau khi đổi proxy/IP). Khác màn hình xen giữa "Create with
        Google Flow" (§11.28, URL vẫn giữ `/project/`)."""
        try:
            u = urlparse(self.driver.current_url or '')
        except Exception:
            return False
        host = (u.hostname or '').lower()
        return host == 'flow.google.com' and (u.path or '').rstrip('/').startswith('/about')

    def _relogin_after_flow_about(self, target_url: str) -> bool:
        """Bị đá về `flow.google.com/about` → chạy lại quy trình check đăng nhập
        (ÉP chạy, bỏ qua setting `google_login_check_enabled`) rồi vào lại
        `target_url`. Trả True nếu cuối cùng không còn kẹt ở /about.

        Gọi lồng: bên trong `_ensure_google_login()` lại đi qua
        `_nav_target_and_resolve_challenge()` → thấy /about lần nữa → KHÔNG đệ
        quy (cờ `_about_relogin_active`), để hàm ngoài xử lý tiếp."""
        if getattr(self, '_about_relogin_active', False):
            return True
        self._about_relogin_active = True
        try:
            self._log('warn', f'[google-login] Vào {target_url} bị đá về flow.google.com/about '
                              f'(phiên đăng nhập hết hiệu lực, thường do đổi proxy) — '
                              f'chạy lại quy trình check đăng nhập…')
            _force_login_check.add(self.profile_id)
            ok = self._ensure_google_login(target_url)
            if not ok:
                self._log('error', '[google-login] Check đăng nhập lại thất bại — cần đăng nhập '
                                   'thủ công qua "Mở login browser".')
                return False
            if not self._on_flow_about_page():
                self._log('ok', '[google-login] ✔ Đã đăng nhập lại, vào được trang Flow')
                return True
            # Đã đăng nhập Google mà Flow vẫn ở /about — thử nút "Create with
            # Google Flow" trên trang đó rồi vào lại đúng link 1 lần.
            self._click_create_with_flow_if_present()
            try:
                self._nav_get(target_url, 'vào lại trang sau khi đăng nhập lại (/about)')
                self._sleep(4)
            except Exception as e:
                self._log('warn', f'[google-login] Vào lại {target_url} lỗi: {e}')
            self._click_create_with_flow_if_present()
            if self._on_flow_about_page():
                self._log('error', '[google-login] Đã đăng nhập lại nhưng Flow vẫn đá về '
                                   'flow.google.com/about — cần kiểm tra tài khoản/proxy thủ công.')
                return False
            return True
        finally:
            self._about_relogin_active = False

    def _google_signin_flow(self, target_url: str, timeout: float = 120) -> bool:
        """Hoàn tất đăng nhập/xác minh Google từ BẤT KỲ bước nào đang hiện trên
        accounts.google.com, rồi vào `target_url`. Mỗi loại hành động có giới hạn
        số lần để không lặp vô hạn (vd sai mật khẩu Google trả lại đúng trang cũ).
        Trả False khi gặp bước không tự động được (bấm "Có" trên điện thoại,
        SMS, captcha...) hoặc thiếu email/mật khẩu/khoá 2FA cần cho bước đó."""
        email    = (self.profile.get('account_email') or '').strip()
        password = self.profile.get('account_password') or ''
        has_totp = bool((self.profile.get('account_totp_secret') or '').strip())
        counts: dict = {}
        last_code = ''
        unknown_since = None
        deadline = time.time() + timeout

        def _bump(step, limit):
            counts[step] = counts.get(step, 0) + 1
            return counts[step] <= limit

        def _type_into(el, text):
            self._cdp_click_el(el)
            self._sleep(0.3)
            self._js('arguments[0].value = "";', el)
            self._cdp('Input.insertText', {'text': text})

        while time.time() < deadline:
            if not self._on_google_challenge():
                try:
                    now_url = self.driver.current_url or ''
                except Exception:
                    now_url = ''
                self._log('ok', f'[google-login] ✔ Đã qua trang đăng nhập/xác minh → {now_url[:120]}')
                self._nav_get(target_url, 'vừa tự đăng nhập/xác minh Google xong — vào trang cần thiết')
                self._sleep(3)
                return True
            try:
                st = self.driver.execute_script(self._GOOGLE_SIGNIN_STATE_JS, email,
                                                list(self._GOOGLE_TRY_ANOTHER_KEYWORDS)) or {}
            except Exception:
                st = {}
            step = st.get('step') or 'unknown'
            el = st.get('el')
            if step != 'unknown':
                unknown_since = None
            try:
                if step == 'email':
                    if not email:
                        self._log('error', '[google-login] Google hỏi email nhưng profile chưa lưu Email.')
                        return False
                    if not _bump('email', 3):
                        break
                    if (st.get('value') or '').strip().lower() != email.lower():
                        _type_into(el, email)
                    self._log('info', f'[google-login] Nhập email ({email})')
                    self._click_google_next('identifier')
                    self._sleep(3)
                elif step == 'account':
                    if not _bump('account', 2):
                        break
                    self._log('info', f'[google-login] Chọn tài khoản {email} trong danh sách')
                    self._cdp_click_el(el)
                    self._sleep(3)
                elif step == 'password':
                    if not password:
                        self._log('error', '[google-login] Google hỏi mật khẩu nhưng profile chưa lưu Mật khẩu.')
                        return False
                    if not _bump('password', 2):
                        self._log('error', '[google-login] Vẫn bị hỏi mật khẩu sau 2 lần nhập — có thể sai mật khẩu.')
                        return False
                    _type_into(el, password)
                    self._log('info', '[google-login] Đã nhập mật khẩu')
                    self._click_google_next('password')
                    self._sleep(3)
                elif step == 'totp':
                    if not has_totp:
                        self._log('error', '[google-login] Google hỏi mã xác minh 2 bước nhưng profile chưa '
                                            'có "Khoá 2FA" — điền khoá Authenticator vào profile hoặc xác '
                                            'minh thủ công qua "Mở login browser".')
                        return False
                    if not _bump('totp', 3):
                        self._log('error', '[google-login] Mã 2FA bị từ chối 3 lần — kiểm tra lại khoá 2FA '
                                            'và giờ hệ thống của máy (lệch giờ làm mã sai).')
                        return False
                    code = self._totp_code()
                    if not code:
                        return False
                    # Mã trước bị từ chối thì chờ sang chu kỳ 30s kế tiếp, không gửi lại đúng mã đó.
                    wait_until = time.time() + 31
                    while code == last_code and time.time() < wait_until:
                        self._sleep(1)
                        code = self._totp_code()
                    last_code = code
                    _type_into(el, code)
                    self._log('info', '[google-login] Đã điền mã 2FA từ Authenticator')
                    self._click_google_next('totp')
                    self._sleep(4)
                elif step == 'choose_totp':
                    if not has_totp:
                        self._log('error', '[google-login] Trang chọn cách xác minh nhưng profile chưa có '
                                            '"Khoá 2FA" — cần xác minh thủ công.')
                        return False
                    if not _bump('choose_totp', 2):
                        break
                    self._log('info', '[google-login] Chọn xác minh bằng Google Authenticator')
                    self._cdp_click_el(el)
                    self._sleep(3)
                elif step == 'try_another':
                    if not has_totp or not _bump('try_another', 2):
                        self._log('error', '[google-login] Google yêu cầu xác minh bằng cách khác (bấm "Có" '
                                            'trên điện thoại/SMS...) và không chuyển được sang Authenticator '
                                            '— cần xác minh thủ công qua "Mở login browser".')
                        return False
                    self._log('info', '[google-login] Bấm "Thử cách khác" để chuyển sang Authenticator')
                    self._cdp_click_el(el)
                    self._sleep(3)
                else:
                    # Trang đang chuyển tiếp/loading — chờ; đứng yên quá 20s thì bỏ cuộc.
                    unknown_since = unknown_since or time.time()
                    if time.time() - unknown_since > 20:
                        break
                    self._sleep(1)
            except Exception as e:
                self._log('warn', f'[google-login] Lỗi ở bước "{step}": {e}')
                self._sleep(2)

        try:
            now_url = self.driver.current_url or ''
        except Exception:
            now_url = ''
        self._log('error', f'[google-login] Không tự hoàn tất được đăng nhập/xác minh Google '
                            f'(dừng ở {now_url[:120]}) — cần xử lý thủ công qua "Mở login browser".')
        return False

    def _nav_target_and_resolve_challenge(self, target_url: str, reason: str) -> bool:
        """Vào thẳng `target_url`; nếu bị đá sang accounts.google.com (đổi proxy/IP
        khiến Google bắt xác minh lại) thì tự hoàn tất bằng `_google_signin_flow()`
        — áp dụng kể cả khi setting check đăng nhập đang TẮT (§11.58)."""
        try:
            self._nav_get(target_url, reason)
            self._sleep(3)
        except Exception as e:
            self._log('warn', f'[google-login] Navigate {target_url} lỗi: {e}')
            return True
        if self._on_flow_about_page():
            return self._relogin_after_flow_about(target_url)
        if not self._on_google_challenge():
            return True
        self._log('warn', '[google-login] Bị chuyển sang trang đăng nhập/xác minh Google — tự xử lý…')
        ok = self._google_signin_flow(target_url)
        if ok and self._on_flow_about_page():
            return self._relogin_after_flow_about(target_url)
        return ok

    def _ensure_google_login(self, target_url: str) -> bool:
        """(2026-08-10, ĐỔI HƯỚNG theo yêu cầu user "viết lại luồng check bằng
        cách vào https://gmail.com/ ko vào accounts.google.com nữa") — thay vì
        vào THẲNG `accounts.google.com` (cách cũ), giờ vào `https://gmail.com/`
        trước rồi ĐỌC KẾT QUẢ REDIRECT để biết trạng thái đăng nhập:
          - Redirect sang `mail.google.com` → ĐÃ đăng nhập.
          - Redirect sang `workspace.google.com` (trang marketing Gmail, Google
            hiện cho khách chưa đăng nhập) → CHƯA đăng nhập — bấm nút "Sign in"
            trên trang đó (dẫn tới `accounts.google.com/AccountChooser/
            signinchooser?continue=...`) để vào ĐÚNG trang đăng nhập, rồi tiếp
            tục luồng nhập email/mật khẩu y hệt trước (KHÔNG đổi — chỉ đổi
            CÁCH VÀO accounts.google.com, DOM đăng nhập thật sự vẫn giống cũ).
        Nút "Sign in" là thẻ `<a target="_blank" href="https://accounts.google
        .com/AccountChooser/signinchooser?...">` (xác nhận qua HTML thật user
        gửi) — click thật sẽ MỞ TAB MỚI (target="_blank"), phức tạp hoá việc
        theo dõi tab hiện tại không cần thiết; thay vào đó ĐỌC THẲNG `href` của
        thẻ `<a>` rồi `driver.get(href)` trên CÙNG tab — cùng đích đến, không
        cần quản lý nhiều tab.

        CALLER KHÔNG cần tự `driver.get(target_url)` trước khi gọi hàm này —
        hàm này tự lo TOÀN BỘ điều hướng, đây là ĐIỂM ĐẾN NAVIGATE DUY NHẤT
        cho mọi entry point đã wire (xem danh sách ở CLAUDE.md §11.31).

        Trả `True` nếu đã vào được `target_url` (dù có phải đăng nhập hay
        không). Trả `False` nếu cần đăng nhập nhưng KHÔNG tự động được (thiếu
        email/mật khẩu đã lưu, không tìm thấy nút "Sign in", hoặc Google yêu
        cầu bước xác minh không tự động hoá được — 2FA/captcha/"xác nhận đây
        là bạn") — trong trường hợp này KHÔNG điều hướng tới `target_url`
        (đứng lại ở trang đăng nhập để user thấy rõ cần làm gì nếu mở lại
        profile), CALLER tự quyết định raise lỗi hay tiếp tục best-effort.

        (2026-08-17) Setting `google_login_check_enabled` (mặc định TẮT/0,
        xem `config.py`/trang Cài đặt) — theo yêu cầu user "tạm tắt tính năng
        check login chạy thẳng vào trang nhận task, khi cần có thể bật lại".
        TẮT → bỏ qua HOÀN TOÀN bước vào gmail.com/kiểm tra/tự nhập email-mật
        khẩu bên dưới, chỉ còn `driver.get(target_url)` thẳng (hành vi ĐƠN
        GIẢN như trước khi có `_ensure_google_login()`, §11.31) — nhanh hơn
        NHƯNG không tự phục hồi nếu session Google bị đăng xuất giữa chừng
        (rơi lại về hành vi CŨ: task sẽ lỗi ở bước tìm DOM vì đang đứng ở
        trang đăng nhập, cần user tự "Mở login browser" xử lý tay). BẬT lại
        (set `1`) để khôi phục toàn bộ luồng tự phát hiện/tự đăng nhập."""
        # (2026-08-20) Cờ ÉP check 1 lần — set bởi `_clear_profile_if_sleeping()`
        # ngay sau khi bậc thang escalation THEO BATCH xoá TẤT CẢ cookie (profile
        # chắc chắn đã đăng xuất). Đọc-và-xoá (`discard`) ngay: chỉ ép ĐÚNG lần
        # khởi động này, lần sau trở về đúng theo setting như bình thường.
        forced = self.profile_id in _force_login_check

        # (2026-09-13, theo yêu cầu user "với các tài khoản gemini ko cần
        # check login... như veo") — tài khoản Gemini (gemini/gemini_video/
        # gemini_image), TRỪ khi vừa bị `forced` (edge case hiếm — profile này
        # từng bị xoá SẠCH cookie kể cả cấp tài khoản qua bậc thang batch, xem
        # ngay dưới), LUÔN bỏ qua bước detour gmail.com/tự nhập email-mật khẩu,
        # KHÔNG PHỤ THUỘC setting `google_login_check_enabled` — chỉ navigate
        # thẳng, giống hệt nhánh "check TẮT" ngay dưới. Đây nhất quán với việc
        # "clear cookie như veo" (xem `_cdp_clear_cache_and_cookies()` — CHỈ
        # xoá cookie riêng của `gemini.google.com`, KHÔNG đụng session Google
        # cấp tài khoản trên `.google.com`/`accounts.google.com` nơi SID/HSID
        # thật sự sống) — sau khi xoá, Google tự khôi phục đăng nhập cho
        # gemini.google.com qua SSO ngay khi navigate lại, KHÔNG cần dò/nhập
        # tay như VEO/labs.google.
        if not forced and self.worker_mode in ('gemini', 'gemini_video', 'gemini_image'):
            return self._nav_target_and_resolve_challenge(
                target_url, 'Gemini: vào thẳng trang (không check đăng nhập)')
        if forced:
            _force_login_check.discard(self.profile_id)
            self._log('warn', '[google-login] Vừa xoá TẤT CẢ cookie ở lần ngủ trước — ÉP '
                               'check đăng nhập lần này (bỏ qua setting '
                               'google_login_check_enabled đang tắt).')

        if not forced and not self._server_settings.get('google_login_check_enabled'):
            return self._nav_target_and_resolve_challenge(
                target_url, 'check đăng nhập đang TẮT — vào thẳng trang')

        self._log('info', '[google-login] Kiểm tra trạng thái đăng nhập Google '
                           '(vào gmail.com trước)…')
        try:
            self._nav_get('https://gmail.com/', 'check đăng nhập: vào gmail.com dò trạng thái')
        except Exception as e:
            self._log('warn', f'[google-login] Không vào được gmail.com ({e}) — '
                               f'bỏ qua kiểm tra, thử thẳng {target_url}')
            try:
                self._nav_get(target_url, 'gmail.com lỗi — bỏ qua check, vào thẳng trang')
                self._sleep(3)
            except Exception:
                pass
            return True

        # (2026-08-10) FIX THẬT — user báo "chạy task gemini thì chỉ mở profile
        # mà ko vào gemini". Root cause: bản đầu dùng `self._sleep(3)` CỐ ĐỊNH
        # rồi đọc `current_url` NGAY — gmail.com redirect + bootstrap app Gmail
        # đầy đủ có thể mất LÂU HƠN 3s thật (mạng chậm, máy tải nặng, cold start
        # profile mới) — đọc quá sớm bắt được URL TRUNG GIAN (chưa phải
        # `mail.google.com`/`workspace.google.com`), rơi vào nhánh "không như
        # dự kiến" HOẶC (tệ hơn) URL trung gian tình cờ trông giống 1 trong 2 —
        # cả 2 case đều có thể dẫn tới kết quả sai. Đổi sang POLL tới khi URL
        # khớp 1 trong 2 pattern đã biết (tối đa 15s, đủ dư cho cold start),
        # thay vì đoán 1 con số cố định — cùng pattern `_wait_js`/`_wait_for_project_url`
        # đã dùng khắp file này cho MỌI chỗ chờ điều hướng/DOM khác.
        current = ''
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                current = self.driver.current_url or ''
            except Exception:
                current = ''
            if 'mail.google.com' in current or 'workspace.google.com' in current:
                break
            self._sleep(0.5)
        else:
            self._log('warn', f'[google-login] Sau 15s vẫn chưa thấy URL quen thuộc '
                               f'(hiện tại: {current}) — tiếp tục kiểm tra best-effort với giá trị này')

        if 'mail.google.com' in current:
            self._log('ok', '[google-login] Đã đăng nhập sẵn (gmail.com → mail.google.com) — '
                             f'vào trang cần thiết: {target_url}')
            return self._nav_target_and_resolve_challenge(target_url, 'đã đăng nhập sẵn — vào trang cần thiết')

        if 'workspace.google.com' not in current:
            # (2026-08-10) URL redirect không khớp CẢ 2 case đã biết (hiếm —
            # Google đổi hành vi, hoặc mạng chậm chưa kịp redirect xong) —
            # không đủ tín hiệu để khẳng định chưa đăng nhập, best-effort đi
            # thẳng target_url thay vì chặn cứng (giữ đúng tinh thần "không
            # false-positive chặn worker" của bản trước).
            self._log('warn', f'[google-login] URL sau gmail.com không như dự kiến ({current}) — '
                               f'thử thẳng {target_url}')
            return self._nav_target_and_resolve_challenge(
                target_url, 'URL sau gmail.com không như dự kiến — vào thẳng trang')

        self._log('warn', '[google-login] CHƯA đăng nhập (gmail.com → workspace.google.com) — '
                           'tìm nút "Sign in"…')
        # (2026-08-10, ĐỔI theo yêu cầu user "không đi thẳng vào accounts.google.com
        # mà phải click button đăng nhập từ workspace.google.com tránh bị là
        # trình duyệt ko hợp lệ") — bản trước đọc THẲNG `href` rồi `driver.get()`,
        # bỏ qua thao tác click thật. Google's Identity Platform có thể coi đây là
        # điều hướng "không tự nhiên" (thiếu ngữ cảnh click/referrer của 1 cú
        # click thật từ trang trước) và trả lỗi "This browser or app may not be
        # secure" — CDP trusted click (`_cdp_click_el()`, mouseMoved+mousePressed+
        # mouseReleased thật, ĐÃ chứng minh hoạt động ổn định trên labs.google/
        # gemini ở nhiều nơi khác trong file này) mô phỏng đúng hành vi user thật
        # bấm chuột, giữ nguyên ngữ cảnh điều hướng mà Google mong đợi.
        variants_js = json.dumps(get_i18n_texts()['googleSignIn'])
        signin_link = self._wait_js(f"""
            var variants = {variants_js};
            var links = [...document.querySelectorAll('a[href]')]
                .filter(function(el){{ return el.getClientRects().length>0; }});
            return links.find(function(el){{
                var t = (el.textContent || '').trim().toLowerCase();
                return variants.some(function(v){{ return t === v.toLowerCase(); }});
            }}) || null;
        """, timeout=8.0)
        if not signin_link:
            self._log('error', '[google-login] Không tìm thấy nút "Sign in" trên workspace.google.com '
                                '— không thể tự động đăng nhập. Cần đăng nhập thủ công qua '
                                '"Mở login browser".')
            return False

        # Nút mang `target="_blank"` (xác nhận qua HTML thật user gửi) — click
        # thật SẼ MỞ TAB MỚI, không phải navigate tab hiện tại. Theo dõi
        # window_handles để bắt tab mới, chuyển driver sang đó, rồi đóng LUÔN
        # tab workspace.google.com cũ (tránh để lại tab thừa gây nhiễu các đoạn
        # code khác trong worker vốn giả định chỉ có 1 tab đang hoạt động).
        old_handles = set(self.driver.window_handles)
        old_active = self.driver.current_window_handle
        self._cdp_click_el(signin_link)
        self._log('info', '[google-login] Đã bấm "Sign in" — chờ tab đăng nhập mới mở…')

        deadline = time.time() + 8
        new_handle = None
        while time.time() < deadline:
            self._sleep(0.3)
            extra = set(self.driver.window_handles) - old_handles
            if extra:
                new_handle = extra.pop()
                break
        if not new_handle:
            self._log('error', '[google-login] Bấm "Sign in" nhưng không thấy tab mới mở sau 8s '
                                '— không thể tiếp tục tự động.')
            return False

        self.driver.switch_to.window(new_handle)
        self._sleep(3)
        try:
            self.driver.switch_to.window(old_active)
            self.driver.close()
        except Exception as e:
            self._log('warn', f'[google-login] Không đóng được tab workspace.google.com cũ ({e}) — bỏ qua')
        self.driver.switch_to.window(new_handle)

        # (2026-09-24) Các bước email/mật khẩu/2FA giờ do `_google_signin_flow()`
        # xử lý theo trang ĐANG HIỆN (selector email/mật khẩu giữ nguyên như bản
        # đã verify DOM thật 2026-08-08, xem `_GOOGLE_SIGNIN_STATE_JS`).
        return self._google_signin_flow(target_url)

    def _ensure_flow_project(self):
        """Navigate đến project page (có CE input).
        project_url trong profile phải là dạng /project/{uuid}."""
        if self._bound_url():
            return self._ensure_bound_flow_page()
        url = (self.profile.get('project_url') or '').strip()
        try:
            current = self.driver.current_url or ''
            if '/project/' in current and 'labs.google' in current:
                # (2026-08-23) Đang ở project khác cái đã lưu ⇒ đồng bộ lại,
                # xem `_sync_project_url_from_browser()`.
                self._sync_project_url_from_browser(current)
                return   # already on a project page
        except Exception:
            pass
        # Navigate to project URL or fall back to general flow page
        target = url if (url and '/project/' in url) else FLOW_PROJECT_URL
        # (2026-08-08, FIX THẬT — cùng bug với _ensure_flow_page(), xem giải
        # thích đầy đủ ở đó) — PHẢI check kết quả trả về, return SỚM nếu đăng
        # nhập thất bại, không thì nhánh "bấm xong lỡ điều hướng ra khỏi URL
        # project" ngay bên dưới sẽ TỰ Ý navigate lại `target` bất kể có đăng
        # nhập được hay không, worker cứ "đá sang flow" dù chưa đăng nhập.
        if not self._ensure_google_login(target):
            self._log('error', 'Không thể tiếp tục — đăng nhập Google thất bại/chưa cấu '
                                'hình. Xem log [google-login] ở trên để biết chi tiết.')
            return

        # (2026-08-10) Navigate THẲNG tới URL project cụ thể đôi khi bị chặn
        # bởi màn hình xen giữa bắt bấm "Create with Google Flow" — URL vẫn
        # giữ /project/{uuid} suốt lúc đó nên check bên dưới không tự phát
        # hiện được, xem docstring `_click_create_with_flow_if_present()`.
        if '/project/' in target:
            self._click_create_with_flow_if_present()
            try:
                current = self.driver.current_url or ''
            except Exception:
                current = ''
            # Bấm xong lỡ điều hướng ra khỏi URL project ban đầu (hiếm) —
            # quay lại đúng url cũ theo đúng yêu cầu "click rồi quay lại url
            # project như cũ".
            if '/project/' not in current:
                self._nav_get(target, 'sau bước "Create with Google Flow" không còn ở /project/ — '
                                      'quay lại project')
                self._sleep(4)

        # Nếu target không có /project/ (rơi vào FLOW_PROJECT_URL) hoặc project_url cũ
        # đã hết hạn/bị xoá (Google tự redirect về trang chung) — trang chung KHÔNG tự
        # tạo/redirect sang project mới nữa, phải bấm "New project" thủ công rồi mới
        # có Slate CE để gõ prompt.
        try:
            current = self.driver.current_url or ''
        except Exception:
            current = ''
        if '/project/' not in current:
            # (2026-09-11) Xem giải thích đầy đủ ở `_ensure_flow_page()` —
            # KHÔNG tạo project mới chỉ vì 1 lần vào project cũ bị bật ra
            # trang chung, CHỈ còn dùng lại project chung theo email hoặc
            # bootstrap thật (chưa từng có project nào).
            if self._return_to_saved_project():
                return
            self._try_reuse_or_bootstrap_project()

    # ── Direct API calls ──────────────────────────────────────────────────────

    def _extract_project_id(self) -> str:
        """Trích UUID project từ profile project_url (/project/{uuid})."""
        import re
        url = (self.profile.get('project_url') or '').strip()
        m = re.search(r'/project/([0-9a-f-]{36})', url)
        return m.group(1) if m else ''

    def _parse_source_media(self, task: dict) -> list:
        """Parse task['source_media'] (JSON string hoặc list) → list dict chuẩn
        [{url, filename}, ...]. Field này do backend build (§ ref_images.py) —
        `url` luôn là path server-hosted (thường TƯƠNG ĐỐI, xem
        `_download_source_media()`)."""
        src = task.get('source_media')
        if not src:
            return []
        try:
            parsed = json.loads(src) if isinstance(src, str) else src
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []

    def _download_source_media(self, source_media: list) -> list:
        """Tải từng ảnh tham chiếu về bytes — mirror ĐÚNG cách
        `_dom_upload_images()` xử lý path tương đối (prefix `FLOW_SERVER`).
        Trả list[(bytes, filename, mime_type)], bỏ qua item lỗi (log warn)."""
        out = []
        for i, item in enumerate(source_media):
            url  = (item or {}).get('url') or ''
            name = (item or {}).get('filename') or (item or {}).get('name') or f'image_{i+1}.jpg'
            if url.startswith('/'):
                url = f'{FLOW_SERVER}{url}'
            if not url.startswith('http'):
                self._log('warn', f'API upload: bỏ qua url không hợp lệ "{url}"')
                continue
            try:
                resp = req_lib.get(url, timeout=30)
                if not resp.ok:
                    self._log('warn', f'API upload: HTTP {resp.status_code} tải "{url}"')
                    continue
                ext  = os.path.splitext(name)[1].lower()
                mime = _MEDIA_MIME_BY_EXT.get(ext)
                if not mime:
                    ct   = resp.headers.get('content-type', '')
                    mime = next((v for k, v in _MEDIA_MIME_BY_EXT.items() if v in ct), 'image/jpeg')
                out.append((resp.content, name, mime))
            except Exception as e:
                self._log('warn', f'API upload: tải "{url}" lỗi: {e}')
        return out

    @staticmethod
    def _abs_media_url(item: dict) -> str:
        """URL tuyệt đối của 1 item `source_media` — CÙNG quy tắc prefix
        `FLOW_SERVER` với `_download_source_media()` (backend trả path TƯƠNG
        ĐỐI). Dùng làm khoá cache upload nên phải tính y hệt ở mọi nơi."""
        url = (item or {}).get('url') or ''
        return f'{FLOW_SERVER}{url}' if url.startswith('/') else url

    def _upload_source_media_cached(self, source_media: list) -> list:
        """Tải + upload ảnh tham chiếu lên Flow, TÁI SỬ DỤNG bản đã upload nếu
        CÙNG project. Trả list `media.name` theo đúng thứ tự `source_media`
        (item tải/upload lỗi bị bỏ qua — mirror `_download_source_media()`).

        (2026-09-05, FIX BUG THẬT user báo: *"cùng project nhưng upload lại ảnh
        tham chiếu nhiều lần"*.) Đây là ĐIỂM DUY NHẤT được phép upload ảnh tham
        chiếu — cả 4 vòng upload cũ (2 nhánh ảnh + 2 nhánh video) đều gọi vào
        đây, nên cache áp dụng đồng nhất, không còn nhánh nào bỏ sót.

        Cache khoá theo **(project Flow, URL ảnh gốc)** chứ KHÔNG theo task
        (xem `media_upload_cache.py` — bản cũ khoá theo task nên hàng chục scene
        cùng tham chiếu 1 ảnh CHAR vẫn upload lại từng bản riêng vào cùng 1
        project). Không có `project_id` (profile chưa có `project_url` hợp lệ)
        → bỏ qua cache, upload thẳng như trước, KHÔNG chặn task.

        (2026-09-14) Ưu tiên SỐ 1: ảnh tham chiếu là ảnh ĐÃ TẠO trong chính project
        Flow này (backend gửi kèm `flowMediaName` + `flowProjectId`, xem
        `ref_images._flow_media_ref()`) → dùng thẳng id đó, KHÔNG tải, KHÔNG
        upload — tránh bước upload `maseQ` đang bị Google giới hạn nhịp.

        (2026-09-15) Mọi id dùng lại (id đã lưu lẫn id trong cache upload) đều
        phải còn TÌM THẤY trong project Flow hiện tại (`_flow_id_in_project()`)
        — không thấy thì upload ảnh tham chiếu lại, tránh gửi generate 1 id đã
        mất khỏi project."""
        project_id = self._extract_project_id()
        names, reused, reused_flow, uploaded = [], 0, 0, 0
        listing: dict = {}
        for item in (source_media or []):
            saved = self._saved_flow_media_name(item, project_id, listing)
            if saved:
                names.append(saved)
                reused_flow += 1
                continue
            url = self._abs_media_url(item)
            cached = get_cached_media_name(project_id, url) if project_id else None
            if cached and self._flow_id_in_project(cached, project_id, listing) is False:
                listing.setdefault('missing', []).append(cached)
                cached = None
            if cached:
                names.append(cached)
                reused += 1
                continue
            downloaded = self._download_source_media([item])
            if not downloaded:
                continue                      # đã log warn trong _download_source_media
            image_bytes, filename, mime = downloaded[0]
            name = self._upload_media_to_flow(image_bytes, filename, mime)
            if not name:
                continue
            names.append(name)
            uploaded += 1
            if project_id:
                set_cached_media_name(project_id, url, name)
                self._flow_listing_add(project_id, name)
        missing = listing.get('missing') or []
        if missing:
            self._log('info', f'  🔎 {len(missing)} id ảnh tham chiếu không tìm thấy trong project Flow '
                               f'{project_id[:8]}… ({", ".join(m[:8] for m in missing)}) → upload tham chiếu')
        if reused_flow:
            self._log('info', f'  ♻ Dùng id Flow đã lưu cho {reused_flow}/{len(source_media or [])} '
                               f'ảnh tham chiếu (ảnh đã tạo trong project {project_id[:8]}…) — không upload')
        if uploaded and self._bound_url():
            self._log('warn', f'  ⬆ Phải upload {uploaded} ảnh tham chiếu (chưa có id Flow đã lưu '
                               f'trong project này — ảnh upload tay hoặc tạo ở project Flow khác)')
        if reused:
            self._log('info', f'  ♻ Dùng lại {reused}/{len(names)} ảnh tham chiếu đã upload '
                               f'trước đó (cùng project {project_id[:8]}…) — không upload mới')
        return names

    def _upload_media_to_flow(self, image_bytes: bytes, filename: str, mime_type: str) -> str:
        """Upload 1 ảnh tham chiếu qua batchexecute (`maseQ`) — trả media.name (uuid)."""
        name = self._upload_media_to_flow_be(image_bytes, filename, mime_type)
        if not name:
            raise RuntimeError(self._be_fail_msg('Upload ảnh tham chiếu'))
        return name

    def _prepare_video_uploads(self, task: dict) -> list:
        """Tải + upload hết ảnh đính kèm của 1 task video. Trả list `media.name`
        theo đúng thứ tự `source_media` (frameToVideo chỉ lấy ảnh đầu).
        textToVideo → `[]`. Không cần recaptcha (uploadImage).

        (2026-08-13, theo yêu cầu user "lưu lại luôn id ảnh tham chiếu đã
        upload thành công kèm id project hiện đang chạy vào task, đề phòng lỗi
        video đã có ảnh tham chiếu upload thì đính kèm lại ảnh tham chiếu nếu
        cùng id project ko cần upload mới") — TRƯỚC KHI tải/upload, check cache
        cục bộ (`media_upload_cache.py`, khoá theo `task_id`+`project_id` HIỆN
        TẠI) — nếu task này ĐÃ TỪNG upload thành công cho ĐÚNG project đang
        chạy (còn nguyên trên aisandbox, không cần biết Chrome session/driver
        nào đã upload nó), dùng lại NGAY, bỏ qua hẳn bước tải+upload. Cache
        SỐNG SÓT qua retry/worker restart (file JSON cục bộ) — hữu ích nhất
        khi bước GENERATE (sau upload) lỗi/task bị reap timeout phải submit
        lại, tránh tải+upload lại ảnh đã có sẵn trên server."""
        mode = task.get('mode', 'textToVideo')
        source_media = self._parse_source_media(task)

        if mode in ('imageToVideo', 'componentsToVideo'):
            if not source_media:
                raise RuntimeError(f'mode={mode} cần ít nhất 1 ảnh tham chiếu (source_media rỗng)')
            names = self._upload_source_media_cached(source_media)
            if not names:
                raise RuntimeError('Không upload được ảnh tham chiếu nào cho ingredientToVideo')
            return names
        if mode == 'frameToVideo':
            if not source_media:
                raise RuntimeError('mode=frameToVideo cần ít nhất 1 ảnh (Bắt đầu)')
            # CHỈ ảnh ĐẦU (endImage chưa proven, xem CLAUDE.md §11.33) — cắt
            # danh sách TRƯỚC khi upload để không tải/upload thừa ảnh cuối.
            names = self._upload_source_media_cached(source_media[:1])
            if not names:
                raise RuntimeError('Không tải được ảnh khung hình nào')
            return names
        return []

    def _call_image_api_v2(self, task: dict, captcha: str) -> list:
        """Gọi trực tiếp từ Python: POST /v1/projects/{id}/flowMedia:batchGenerateImages
        (endpoint capture thật, xem `flow_api.py`/`tests/FLOW_API_CAPTURE.md`).
        Hỗ trợ imageToImage — upload từng `task['source_media']` qua
        /flow/uploadImage rồi đính kèm qua `imageInputs` (2026-08-12, thay bản cũ
        `_build_body()` vốn dùng field `.data` sai schema — ref ảnh trước đây bị
        ÂM THẦM BỎ QUA vì `source_media[].data` không tồn tại, chỉ có `.url`).
        Trả list [{type:'image', url:fifeUrl}] hoặc raise RuntimeError.
        """
        be_results = self._call_image_api_be(task, captcha)
        if not be_results:
            raise RuntimeError(self._be_fail_msg('Tạo ảnh'))
        return be_results

    def _call_video_api(self, task: dict, captcha: str,
                        uploaded_media_names: list | None = None) -> dict:
        """Submit 1 task video qua API — CHỈ xác nhận đã TẠO workflow, KHÔNG chờ
        tile/URL (2026-08-12, theo yêu cầu user: API không hiện tile trên UI nên
        gửi prompt liên tiếp, lấy kết quả sau qua projectInitialData).

        Prompt gửi kèm prefix `TASK_{id}:` để `_reconcile_project_media()` khớp
        được sau này (cùng quy ước DOM mode).

        `uploaded_media_names`: list `media.name` đã upload sẵn (batch pre-upload).
        None → tự download+upload trong hàm này (đường 1 task lẻ)."""
        mode = task.get('mode', 'textToVideo')
        project_id = self._extract_project_id()
        if not project_id:
            raise RuntimeError('Không tìm được project ID từ project_url (cần dạng /project/{uuid})')

        names = (list(uploaded_media_names)
                 if uploaded_media_names is not None
                 else self._prepare_video_uploads(task))

        be_info = self._call_video_api_be(task, captcha, names)
        if not be_info:
            raise RuntimeError(self._be_fail_msg(f'Tạo video (mode={mode})'))
        return be_info

    # ══════════════════════════════════════════════════════════════════════════
    # DOM mode helpers — port từ FlowMediaGenerator.js (dùng CDP qua Selenium)
    # ══════════════════════════════════════════════════════════════════════════

    def _is_driver_alive(self) -> bool:
        """Kiểm tra Chrome còn chạy và driver còn kết nối không."""
        if not self.driver:
            return False
        try:
            _ = self.driver.current_url   # raise nếu browser đã đóng
            return True
        except Exception:
            return False

    def _js(self, script: str, *args):
        return self.driver.execute_script(script, *args)

    def _js_async(self, script: str, *args, timeout: int = 25):
        old = 30
        try:
            raw = self.driver.timeouts.script
            old = raw.total_seconds() if hasattr(raw, 'total_seconds') else float(raw)
        except Exception:
            pass
        self.driver.set_script_timeout(timeout)
        try:
            return self.driver.execute_async_script(script, *args)
        finally:
            try:
                self.driver.set_script_timeout(old)
            except Exception:
                pass

    def _cdp(self, cmd: str, params: dict):
        return self.driver.execute_cdp_cmd(cmd, params)

    def _wait_js(self, script: str, args: tuple = (), timeout: float = 4.0, interval: float = 0.15):
        """Poll self._js(script, *args) tới khi trả về giá trị truthy hoặc hết
        timeout — port của FlowMediaGenerator.waitFor() trong
        extensions/content/flowMediaGenerator.js (dùng cho configButton 4000ms,
        sub-tab 2000ms, model dropdown menu item 3000ms). `_dom_configure()` bản
        cũ dùng single-shot self._js(...) (không retry) hoặc fixed self._sleep()
        rồi check 1 lần — cả 2 kiểu đều báo nhầm "not found" nếu popup/dropdown
        chưa kịp render xong tại đúng thời điểm check (đúng bug JS gốc từng gặp
        và đã tự sửa bằng waitFor — xem comment gốc trong flowMediaGenerator.js).
        Trả về kết quả cuối cùng (falsy nếu timeout)."""
        deadline = time.time() + timeout
        result = None
        while time.time() < deadline:
            result = self._js(script, *args)
            if result:
                return result
            self._sleep(interval)
        return result

    def _fire_click(self, el):
        """Synthetic pointer+mouse events + .click() — mirrors JS fireClick() in flowMediaGenerator."""
        r = self.driver.execute_script(
            "var r=arguments[0].getBoundingClientRect();"
            "return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)};", el)
        x, y = int(r['x']), int(r['y'])
        self.driver.execute_script("""
            var el=arguments[0], x=arguments[1], y=arguments[2];
            var o={bubbles:true,cancelable:true,view:window,clientX:x,clientY:y};
            ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(t){
                el.dispatchEvent(new (t.startsWith('pointer')?PointerEvent:MouseEvent)(t,o));
            });
            el.click();
        """, el, x, y)

    def _cdp_click_el(self, el):
        """CDP mouseMoved+mousePressed+mouseReleased — mirrors JS trustedClickAt()/case
        'CC' trong extension_gemini/background.js & extensions/background.js (đã chứng
        minh hoạt động ổn định trên labs.google/gemini). Bản cũ ở đây CHỈ gửi
        mousePressed+mouseReleased, THIẾU mouseMoved mở đầu (hover trước khi bấm — một
        số component React tra pointer-position/hover-state trước khi chấp nhận click)
        VÀ thiếu field 'buttons' (bitmask nút đang giữ — 1 lúc pressed, 0 lúc released,
        Chromium có thể xử lý sự kiện không đầy đủ nếu thiếu field này) — nghi là
        nguyên nhân picker mở không ổn định ('picker không mở sau 10s').

        (2026-09-24) Chờ phần tử ĐỨNG YÊN trước khi bấm: menu/popover của Flow
        (Angular Material) mở bằng hiệu ứng trượt/phóng to — đo trên profile 25,
        mục menu model ở ~0.1s đầu nằm sai chỗ ([20,136] rồi mới về [619,388]).
        Bấm ngay lúc tìm thấy → trúng chỗ khác, model không đổi (chọn model
        chập chờn). Đọc toạ độ 2 lần liên tiếp cách 80ms tới khi trùng (tối đa ~1.5s)."""
        rect_js = ("var r=arguments[0].getBoundingClientRect();"
                   "return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];")
        prev = self.driver.execute_script(rect_js, el)
        for _ in range(18):
            time.sleep(0.08)
            cur = self.driver.execute_script(rect_js, el)
            if cur == prev:
                break
            prev = cur
        left, top, width, height = prev
        if getattr(self, '_cloak_human', None):
            # CloakBrowser humanize: chuột đi đường cong Bezier tới điểm ngẫu nhiên trong phần tử
            self._cloak_human.click_box(left, top, width, height)
            return
        x, y = int(left + width / 2), int(top + height / 2)
        self._cdp('Input.dispatchMouseEvent',
                  {'type': 'mouseMoved', 'x': x, 'y': y, 'button': 'none', 'modifiers': 0})
        self._cdp('Input.dispatchMouseEvent',
                  {'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left',
                   'buttons': 1, 'clickCount': 1, 'modifiers': 0})
        self._cdp('Input.dispatchMouseEvent',
                  {'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left',
                   'buttons': 0, 'clickCount': 1, 'modifiers': 0})

    def _dom_tile_ids(self) -> set:
        ids = self._js("""
            return [...new Set(
                [...document.querySelectorAll('[data-tile-id]')]
                    .map(function(e){return e.dataset.tileId;})
                    .filter(Boolean)
            )];
        """)
        return set(ids or [])

    def _dom_tile_ids_ordered(self) -> list:
        """Giống _dom_tile_ids() nhưng GIỮ NGUYÊN thứ tự xuất hiện trong DOM (list,
        không phải set) — cần cho batch mode (_run_tasks_batch) để gán tile mới về
        đúng task theo thứ tự submit, vì tile không mang task-id marker nào."""
        ids = self._js("""
            return [...new Set(
                [...document.querySelectorAll('[data-tile-id]')]
                    .map(function(e){return e.dataset.tileId;})
                    .filter(Boolean)
            )];
        """)
        return list(ids or [])

    def _dom_wait_new_tiles(self, before: set, count: int, timeout: int = 90) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline:
            new = self._dom_tile_ids() - before
            if len(new) >= count:
                return list(new)
            if new and time.time() > deadline - 30:
                return list(new)
            self._sleep(2)
        return []

    def _dom_tile_status(self, tile_id: str) -> dict:
        return self._js("""
            var id = arguments[0];
            var tile = document.querySelector('[data-tile-id="' + id + '"]');
            if (!tile) return {status:'missing'};
            var vid = tile.querySelector('video');
            if (vid && vid.src) return {status:'done', type:'video', src:vid.src};
            var img = tile.querySelector('img[src]:not([src=""])');
            if (img && img.src && !img.src.startsWith('data:'))
                return {status:'done', type:'image', src:img.src};
            var hasErr = [...tile.querySelectorAll('i')]
                .some(function(i){return i.textContent.trim()==='warning';});
            if (hasErr) return {status:'error', error:'Tile error (warning icon)'};
            var texts = [];
            function walk(n){
                if(n.nodeType===3) texts.push(n.textContent.trim());
                n.childNodes.forEach(walk);
            }
            walk(tile);
            var pctStr = texts.find(function(t){return /^\\d+%$/.test(t);});
            if (pctStr) return {status:'generating', pct:parseInt(pctStr)};
            return {status:'waiting', pct:0};
        """, tile_id)

    def _dom_poll_done(self, tile_ids: list, timeout: int = 600) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline:
            statuses = [self._dom_tile_status(tid) for tid in tile_ids]
            errs = [s for s in statuses if s.get('status') == 'error']
            if errs:
                raise RuntimeError(errs[0].get('error', 'Tile error'))
            done = [s for s in statuses if s.get('status') == 'done']
            if len(done) == len(tile_ids):
                return done
            pcts = [100 if s.get('status') == 'done' else (s.get('pct') or 0) for s in statuses]
            avg  = sum(pcts) // max(len(pcts), 1)
            self._log('info', f'DOM polling {len(tile_ids)} tile(s) avg={avg}%')
            self._sleep(5)
        raise RuntimeError('DOM tile polling timeout')

    # ── Match media theo prompt qua RPC `batchexecute` (2026-09-03) ──────────
    # Thay thế 2 thế hệ trước:
    #   1. tile DOM polling (fragile — offsetParent/icon 'warning'/i18n…)
    #   2. `flow.projectInitialData` (2026-07-16) — endpoint này KHÔNG CÒN TỒN
    #      TẠI trên `flow.google.com` (app Angular mới): gọi thẳng URL cũ trả
    #      NGUYÊN shell HTML SPA, không phải data. Vá thêm `XMLHttpRequest`
    #      (đoán "Angular HttpClient dùng XHR") KHÔNG giúp gì — bản thân
    #      endpoint không được app gọi ở BẤT KỲ transport nào.
    # Nguyên tắc khớp media KHÔNG đổi: dựa vào prefix `TASK_{id}:` nhúng sẵn
    # trong prompt (xem root CLAUDE.md mục "Reconciliation media").
    #
    # App mới dùng RPC đa dụng `batchexecute` (`/_/AiSandboxAngularFrontend/
    # data/batchexecute` — CÙNG protocol Google dùng chung cho Gmail/Drive/Docs,
    # không có URL riêng theo mục đích). rpcid được nhúng thẳng trong bundle JS
    # kèm TÊN METHOD BACKEND (`new _.xx("<rpcid>", …, "/FlowService.<Method>")`)
    # — trích được 72 cái, xem `tests/_extract_rpcid_map.py`. Các cái đã dùng:
    #   - `Zzl0ze` = `/FlowService.GetProjectContents`
    #     args=["projects/{projectId}",null,null,null,[1]] → LIỆT KÊ mọi media.
    #     Mỗi entry: `[tileId, null, null, [title, [ts_sec,ts_nsec], null, null,
    #     DETAIL_UUID, GEN_MARKER, [ts2]], projectId]`. ⚠️ uuid cho `as29s` là
    #     `meta[4]` (DETAIL_UUID — khớp uuid trong CDN URL), KHÔNG PHẢI
    #     `entry[0]` (tile id). `GEN_MARKER` (meta[5]) khác None = đã qua
    #     generation (verify 100% trên 275 entry thật).
    #   - `as29s` = `/FlowService.GetMedia` args=["{DETAIL_UUID}"] → prompt đầy
    #     đủ + URL CDN có chữ ký. Trả `null` cho item không qua generation.
    #
    # Response là format length-prefixed-chunk riêng của Google (`)]}'` XSSI
    # prefix + cặp dòng "độ dài"/"JSON array", payload THẬT là 1 CHUỖI chứa
    # JSON — decode 2 LỚP), xem `_batchexecute_parse()`.
    def _batchexecute_harvest_session(self) -> dict | None:
        """Lấy `bl`/`f.sid`/`at` (token bắt buộc để tự CONSTRUCT 1 lệnh
        batchexecute) — LẮNG NGHE 1 lệnh batchexecute mà CHÍNH TRANG tự bắn
        (Angular tự gọi RẤT NHIỀU RPC batchexecute mỗi lần vào/reload project,
        không cần biết CHÍNH XÁC rpcId nào của lệnh bắt được — chỉ cần lấy
        token dùng lại được cho lệnh TỰ CONSTRUCT sau đó, đã verify token dùng
        lại nhiều lần trong CÙNG 1 lần load trang vẫn hợp lệ). Cài interceptor
        qua CDP `Page.addScriptToEvaluateOnNewDocument` (chạy TRƯỚC JS của
        chính trang — không lỡ mất lệnh bắn ra ngay lúc mount, `execute_script()`
        thường chạy SAU khi trang đã load nên sẽ lỡ) — vá CẢ `fetch` LẪN
        `XMLHttpRequest` (không biết trước Angular dùng transport nào cho
        batchexecute cụ thể). Trả `{'bl','sid','at'}` hoặc `None` nếu không
        bắt được lệnh nào sau 15s."""
        if not getattr(self, '_be_interceptor_installed', False):
            try:
                self._cdp('Page.enable', {})
                self._cdp('Page.addScriptToEvaluateOnNewDocument', {'source': """
                    (function(){
                        window.__beCalls = [];
                        function push(rec){
                            try {
                                window.__beCalls.push(rec);
                                if (window.__beCalls.length > 20) window.__beCalls.shift();
                            } catch(e) {}
                        }
                        var _origOpen = XMLHttpRequest.prototype.open;
                        var _origSend = XMLHttpRequest.prototype.send;
                        XMLHttpRequest.prototype.open = function(method, url){
                            this.__beUrl = url;
                            return _origOpen.apply(this, arguments);
                        };
                        XMLHttpRequest.prototype.send = function(body){
                            var self = this;
                            if (self.__beUrl && String(self.__beUrl).indexOf('batchexecute') !== -1) {
                                self.__beReqBody = body;
                                self.addEventListener('load', function(){
                                    try {
                                        push({url: self.__beUrl, reqBody: self.__beReqBody,
                                              respBody: self.responseText, status: self.status});
                                    } catch(e) {}
                                });
                            }
                            return _origSend.apply(this, arguments);
                        };
                        var _prevFetch = window.fetch;
                        window.fetch = function(input, init){
                            var url = typeof input === 'string' ? input : (input && input.url) || String(input);
                            if (String(url).indexOf('batchexecute') === -1) return _prevFetch.apply(this, arguments);
                            return _prevFetch.apply(this, arguments).then(function(resp){
                                try {
                                    resp.clone().text().then(function(t){
                                        push({url: url, reqBody: init && init.body, respBody: t, status: resp.status});
                                    }).catch(function(){});
                                } catch(e) {}
                                return resp;
                            });
                        };
                    })();
                """})
                self._be_interceptor_installed = True
            except Exception as e:
                self._log('warn', f'batchexecute: không cài được interceptor: {e}')
                return None

        try:
            self._nav_refresh('batchexecute: lấy token bl/f.sid/at — refresh để bắt lệnh trang tự bắn')
        except Exception as e:
            self._log('warn', f'batchexecute: refresh trang lỗi: {e}')
            return None

        sample = None
        deadline = time.time() + 15
        while time.time() < deadline:
            sample = self._js(
                'return (window.__beCalls && window.__beCalls.length) ? window.__beCalls[0] : null;')
            if sample:
                break
            self._sleep(1)
        if not sample:
            self._log('warn', 'batchexecute: reload xong nhưng không bắt được lệnh nào sau 15s')
            return None

        url = sample.get('url') or ''
        req_body = sample.get('reqBody') or ''
        m_bl  = re.search(r'[?&]bl=([^&]+)', url)
        m_sid = re.search(r'[?&]f\.sid=([^&]+)', url)
        m_at  = re.search(r'(?:^|&)at=([^&]+)', req_body)
        if not (m_bl and m_sid and m_at):
            self._log('warn', 'batchexecute: không trích được bl/f.sid/at từ lệnh đã bắt')
            return None
        return {'bl': unquote(m_bl.group(1)), 'sid': unquote(m_sid.group(1)),
                'at': unquote(m_at.group(1))}

    def _batchexecute_call(self, rpc_id: str, args: list, session: dict,
                            timeout: int = 20) -> str | None:
        """Tự CONSTRUCT + fire 1 lệnh batchexecute (không cần chờ trang tự gọi
        ĐÚNG RPC mình cần — chỉ cần token đã harvest 1 lần/phiên) — verify
        trực tiếp trên `flow.google.com` thật: `bl`/`f.sid`/`at` harvest từ 1
        lệnh bất kỳ dùng lại được cho lệnh KHÁC rpc_id. `source-path` lấy từ
        chính `location.pathname` của trang đang mở (khớp cách capture thật:
        luôn là `/project/{uuid}`). `timeout` (giây) — RPC đọc dữ liệu xong
        trong vài giây (mặc định 20 đủ), nhưng RPC SINH NỘI DUNG (vd
        `ogiZ0b`=BatchGenerateImages) chờ tới lúc ảnh render xong nên cần
        nới lên 90-180. Trả raw response text hoặc `None` nếu lỗi
        mạng/timeout/status khác 200."""
        inner_args = json.dumps(args)
        f_req = json.dumps([[[rpc_id, inner_args, None, 'generic']]])
        result = self._js_async("""
            var fReq = arguments[0], at = arguments[1], bl = arguments[2], sid = arguments[3];
            var rpcId = arguments[4];
            var done = arguments[arguments.length - 1];
            var body = 'f.req=' + encodeURIComponent(fReq) + '&at=' + encodeURIComponent(at) + '&';
            var qs = new URLSearchParams({
                rpcids: rpcId, 'source-path': location.pathname, bl: bl, 'f.sid': sid,
                hl: 'en', _reqid: String(Math.floor(Math.random()*900000)+100000), rt: 'c'
            });
            fetch('https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?' + qs.toString(), {
                method: 'POST', credentials: 'include',
                headers: {'content-type': 'application/x-www-form-urlencoded;charset=UTF-8'},
                body: body
            }).then(function(r){
                return r.text().then(function(t){ done({ok:true, status:r.status, body:t}); });
            }).catch(function(e){ done({ok:false, error:String(e)}); });
        """, f_req, session['at'], session['bl'], session['sid'], rpc_id, timeout=timeout)
        if not result or not result.get('ok'):
            self._log('warn', f'batchexecute: gọi RPC "{rpc_id}" lỗi: '
                               f'{result.get("error") if result else "no response"}')
            return None
        if result.get('status') != 200:
            self._log('warn', f'batchexecute: RPC "{rpc_id}" trả status {result.get("status")}')
            return None
        return result.get('body')

    @staticmethod
    def _batchexecute_parse(raw_text: str, rpc_id: str):
        """Parse response batchexecute (định dạng length-prefixed-chunk Google
        dùng chung nhiều sản phẩm) — bỏ tiền tố XSSI `)]}'`, duyệt từng cặp
        dòng (độ-dài, JSON-array), tìm entry `["wrb.fr", rpc_id, payloadStr,
        ...]` — `payloadStr` LÀ 1 CHUỖI chứa JSON (decode 2 LỚP, đã verify qua
        capture thật). Trả structure đã parse, `None` nếu payload rỗng (item
        KHÔNG qua generation — xác nhận qua `as29s` thật với 1 media upload
        thô), raise nếu không tìm thấy chunk nào khớp `rpc_id` (lỗi cấu trúc
        thật, khác "không có dữ liệu")."""
        text = raw_text or ''
        if text.startswith(")]}'"):
            text = text[4:].lstrip('\n')
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            len_line = lines[i].strip()
            if not len_line.isdigit():
                i += 1
                continue
            i += 1
            if i >= len(lines):
                break
            json_line = lines[i]
            i += 1
            try:
                chunk = json.loads(json_line)
            except Exception:
                continue
            for entry in chunk:
                if (isinstance(entry, list) and len(entry) >= 3
                        and entry[0] == 'wrb.fr' and entry[1] == rpc_id):
                    payload_str = entry[2]
                    if payload_str is None:
                        # Payload null có 2 nghĩa HOÀN TOÀN khác nhau:
                        #  (a) không có dữ liệu (vd `as29s` trên media upload
                        #      thô) — hợp lệ, trả None như cũ;
                        #  (b) RPC BỊ TỪ CHỐI, mã lỗi nằm ở `entry[5]`.
                        # Bản đầu gộp làm một nên (b) bị báo nhầm "payload
                        # rỗng", che mất `PUBLIC_ERROR_UNUSUAL_ACTIVITY` —
                        # bug thật, log sản xuất 2026-09-04 01:20.
                        reason = flow_be.extract_rpc_error(entry)
                        if reason:
                            raise flow_be.BatchExecuteError(reason, rpc_id)
                        return None
                    return json.loads(payload_str)
        raise RuntimeError(f'không tìm thấy chunk khớp rpcId="{rpc_id}"')

    @staticmethod
    def _extract_task_id_from_prompt(prompt: str):
        """Tìm prefix `TASK_<id>:` Ở BẤT KỲ ĐÂU trong chuỗi, KHÔNG neo đầu
        (2026-07-17, fix bằng log thật: prompt task video bị Google bọc trong
        template XML `<root><context>…<instruction><prompt>TASK_2777:…` nên
        `re.match('^TASK_')` luôn fail). Dùng chung cho CẢ 2 hệ — input là
        "prompt text 1 item" (hệ cũ) hay "response `as29s` đã flatten thành
        JSON string" (hệ batchexecute) đều đúng."""
        m = re.search(r'TASK_(\d+):', prompt or '')
        return int(m.group(1)) if m else None

    # ══════════════════════════════════════════════════════════════════════
    # GENERATE qua batchexecute (2026-09-04) — ĐƯỜNG CHÍNH, aisandbox dự phòng
    #
    # Vì sao đổi: app Flow đã chuyển sang `flow.google.com` và BỎ HẲN endpoint
    # `aisandbox` — đường cũ 403 hàng loạt trong sản xuất (log profile 25 lúc
    # 00:33-00:34 ngày 2026-09-04). Đường mới là đường CHÍNH APP ĐANG DÙNG.
    #
    # Thiết kế (theo lựa chọn của user): batchexecute làm CHÍNH, aisandbox làm
    # DỰ PHÒNG — mọi hàm `_xxx_be()` dưới đây trả '' / None / raise khi không
    # đi được, để hàm gọi (giữ NGUYÊN tên cũ) tự rơi về đường aisandbox. Nhờ
    # vậy không call site nào ngoài 3 hàm đó phải sửa.
    # ══════════════════════════════════════════════════════════════════════
    _BE_SESSION_TTL_SECS = 600      # token bl/f.sid/at dùng lại được trong 1 lần load trang
    _FLOW_MODELS_TTL_SECS = 1800    # catalog model đổi rất hiếm — 30 phút là đủ
    _FLOW_MODELS_FAIL_TTL_SECS = 300  # lấy hụt thì đừng thử lại mỗi task

    def _flow_models_catalog(self, force: bool = False):
        """Catalog model THẬT của tài khoản này (RPC `HTrJv`), CÓ CACHE.

        Catalog là PER-ACCOUNT nên cache theo worker (1 worker = 1 profile =
        1 tài khoản). RPC chỉ ĐỌC, không cần reCAPTCHA, không tốn quota —
        nhưng vẫn cache vì nằm trên hot path generate.

        Trả `None` khi không lấy được → caller tự lui về `model_catalog`
        (bảng key cũ) thay vì chặn task."""
        now = time.time()
        c = getattr(self, '_flow_models_cache', None)
        if c and not force:
            ttl = (self._FLOW_MODELS_TTL_SECS if c.get('catalog')
                   else self._FLOW_MODELS_FAIL_TTL_SECS)
            if now - c.get('_at', 0) < ttl:
                return c.get('catalog')
        cat = None
        try:
            session = self._be_session()
            if session:
                raw = self._batchexecute_call('HTrJv', [], session, timeout=30)
                if raw:
                    cat = flow_models.parse_catalog(
                        self._batchexecute_parse(raw, 'HTrJv'))
        except Exception as e:
            self._log('warn', f'Không đọc được catalog model (HTrJv): {e}')
        if cat:
            fams = [f['label'] for f in cat['video'] if f['enabled']]
            self._log('info', f'Catalog model: {len(fams)} họ video dùng được '
                              f'(hạng gói={cat.get("tier") or "?"}) — {", ".join(fams)}')
        self._flow_models_cache = {'catalog': cat, '_at': now}
        return cat

    _PAYGATE_TIER_JS = r"""
        var dl = window.dataLayer || [];
        for (var i = dl.length - 1; i >= 0; i--) {
            var t = dl[i] && dl[i].MEDIA_GENERATION_PAYGATE_TIER;
            if (t) return String(t);
        }
        return '';
    """
    _PAYGATE_TTL_SECS = 600

    def _flow_paygate_tier(self) -> str:
        """Hạng gói của tài khoản (vd `PAYGATE_TIER_TWO`). CÓ CACHE 10 phút.
        Trả '' nếu không đọc được — caller giữ cách dò cũ.

        Nguồn chính: RPC `nzlxg` (`/VideoFxService.GetCredits`) — chỉ đọc, không
        cần reCAPTCHA, chính trang gọi mỗi lần tải. Dự phòng: `window.dataLayer`
        (`MEDIA_GENERATION_PAYGATE_TIER`) — CHỈ có sau khi người dùng thao tác
        trên trang, lần tải mới thì trống (bản đầu chỉ đọc nguồn này nên luôn
        ra '' trên worker — log 2026-09-23 11:31).

        (2026-09-23) Lý do tồn tại: không biết hạng thì task video gửi key
        thường trước rồi mới tới `_ultra`. Qua proxy, bản sai bị Google trả
        `PUBLIC_ERROR_UNUSUAL_ACTIVITY` và task hỏng hẳn, trong khi bấm tay
        (trang gửi đúng key ngay) vẫn chạy."""
        now = time.time()
        c = getattr(self, '_paygate_cache', None)
        if c and c.get('tier') and now - c['_at'] < self._PAYGATE_TTL_SECS:
            return c['tier']
        tier, src = '', ''
        try:
            session = self._be_session()
            if session:
                raw = self._batchexecute_call('nzlxg', [], session, timeout=20)
                if raw:
                    tier = flow_models.paygate_from_credits(
                        self._batchexecute_parse(raw, 'nzlxg'))
                    src = 'GetCredits'
        except Exception as e:
            self._log('warn', f'GetCredits (nzlxg) lỗi: {e}')
        if not tier:
            try:
                tier = (self.driver.execute_script(self._PAYGATE_TIER_JS) or '').strip()                     if self.driver else ''
                src = 'dataLayer'
            except Exception as e:
                self._log('warn', f'Không đọc được hạng gói Flow: {e}')
        if tier and (not c or c.get('tier') != tier):
            self._log('info', f'Hạng gói Flow: {tier} (nguồn: {src})')
        elif not tier:
            self._log('warn', 'Không đọc được hạng gói Flow — dò model key theo cách cũ')
        self._paygate_cache = {'tier': tier, '_at': now}
        return tier

    def _resolve_video_model_keys(self, task: dict, ingredient: bool, aspect: str):
        """DANH SÁCH `videoModelKey` nên thử, khớp (nhãn model + tỉ lệ + thời
        lượng), tốt nhất đứng đầu. Trả `(keys, note)`.

        ⚠️ Ưu tiên catalog THẬT đọc lúc chạy. Bảng key cũ (`model_catalog`,
        suy `t2v`→`r2v`) chỉ còn là DỰ PHÒNG vì nó SAI với họ Fast/Quality —
        xem `flow_models` để biết chi tiết bug `RPC_ERROR_CODE_5`.

        Trả NHIỀU ứng viên vì biến thể `_ultra` (hạng gói) KHÔNG suy được từ
        catalog — phải thử mới biết, xem docstring `flow_models`."""
        label = (task.get('model') or '').strip()
        kind = flow_models.KIND_R2V if ingredient else flow_models.KIND_T2V
        duration = flow_models.parse_duration(task.get('video_duration'))
        cat = self._flow_models_catalog()
        if cat and label:
            tier = self._flow_paygate_tier()
            known_ultra = flow_models.ultra_from_paygate(tier)
            keys, note = flow_models.resolve_candidates(
                cat, label, kind, aspect, duration,
                prefer_ultra=bool(getattr(self, '_flow_ultra_pref', False)),
                known_ultra=known_ultra)
            if keys:
                if known_ultra is not None:
                    note += f', gói {tier}'
                return keys, note
            # Catalog nói RÕ là không làm được (họ không có mode đó) — vẫn thử
            # bảng cũ, nhưng báo to để không âm thầm gửi key ảo.
            self._log('warn', f'Catalog model không chọn được key cho '
                              f'"{label}" ({kind}, {aspect}, {duration}s): {note}')
        key, src = model_catalog.resolve_video_model(label, ingredient=ingredient)
        return ([key] if key else []), src

    # Mã lỗi Google trả khi key CÓ THẬT nhưng tài khoản không được dùng (sai
    # biến thể hạng gói), hoặc key không tồn tại — cả hai đều đáng thử ứng
    # viên kế tiếp, và đều bị chặn TRƯỚC khi sinh nội dung nên KHÔNG tốn quota.
    _MODEL_RETRY_REASONS = ('MODEL_ACCESS_DENIED', 'RPC_ERROR_CODE_5', 'NOT_FOUND')

    def _is_model_retry_error(self, exc) -> bool:
        reason = getattr(exc, 'reason', '') or str(exc)
        return any(r in reason for r in self._MODEL_RETRY_REASONS)

    def _be_note_fail(self, what: str, err) -> None:
        """Ghi lý do hỏng (KHÔNG còn chuyển đường) — `_be_last_error` được đưa
        vào message lỗi của task để nhìn log task là biết ngay nguyên nhân."""
        self._be_last_error = str(err)
        # Google chặn ở tầng TÀI KHOẢN (chống lạm dụng / hết quota / không có
        # quyền model) — KHÔNG phải lỗi của batchexecute: đường aisandbox trả
        # đúng lỗi này (403 "reCAPTCHA evaluation failed"), nên đổi đường vô ích.
        if isinstance(err, flow_be.BatchExecuteError) and err.is_account_block:
            self._log('warn', f'⛔ Google TỪ CHỐI ở tầng tài khoản: {err.reason} '
                              f'({what}) — KHÔNG phải lỗi batchexecute, đường aisandbox '
                              f'cũng trả đúng lỗi này. Cần giảm nhịp / chờ / đổi tài khoản.')
            return
        self._log('warn', f'batchexecute {what} thất bại: {err}')

    def _be_fail_msg(self, what: str) -> str:
        last = getattr(self, '_be_last_error', '') or 'xem log phía trên'
        return f'{what} qua batchexecute thất bại: {last}'

    def _be_note_ok(self) -> None:
        self._be_last_error = ''

    def _be_session(self, force: bool = False) -> dict | None:
        """Session batchexecute CÓ CACHE.

        `_batchexecute_harvest_session()` gọi `driver.refresh()` (~15s) — chạy
        mỗi task là không chấp nhận được trên hot path generate. Token đã verify
        dùng lại được nhiều lần trong cùng 1 lần load trang."""
        now = time.time()
        cached = getattr(self, '_be_session_cache', None)
        if cached and not force and (now - cached.get('_at', 0)) < self._BE_SESSION_TTL_SECS:
            return cached
        # Interceptor được cài qua `Page.addScriptToEvaluateOnNewDocument` nên
        # sống qua mọi navigate — nếu đã cài, thử đọc lệnh trang TỰ bắn trước
        # (rẻ), chỉ khi không có gì mới chịu refresh.
        sess = None
        if getattr(self, '_be_interceptor_installed', False):
            sess = self._be_session_from_page()
        if not sess:
            sess = self._batchexecute_harvest_session()
        if not sess:
            # Thử LẠI 1 lần: harvest chờ tối đa 15s sau `driver.refresh()` —
            # ngay sau khi worker vừa khởi động (hoặc mạng chậm) trang có thể
            # chưa kịp bắn lệnh batchexecute nào trong ngần ấy. Đã gặp thật lúc
            # verify 2026-09-04: lần chạy đầu im lặng rơi về aisandbox, lần
            # chạy ngay sau đó (cùng máy, cùng trang) thành công.
            self._log('info', 'batchexecute: harvest session lần 1 không được — thử lại')
            sess = self._batchexecute_harvest_session()
        if sess:
            sess['_at'] = now
            self._be_session_cache = sess
        return sess

    def _be_session_from_page(self) -> dict | None:
        """Đọc bl/f.sid/at từ lệnh batchexecute trang TỰ bắn — KHÔNG refresh."""
        try:
            sample = self._js(
                'return (window.__beCalls && window.__beCalls.length) '
                '? window.__beCalls[window.__beCalls.length - 1] : null;')
        except Exception:
            return None
        if not sample:
            return None
        url = sample.get('url') or ''
        req_body = sample.get('reqBody') or ''
        m_bl = re.search(r'[?&]bl=([^&]+)', url)
        m_sid = re.search(r'[?&]f\.sid=([^&]+)', url)
        m_at = re.search(r'(?:^|&)at=([^&]+)', req_body)
        if not (m_bl and m_sid and m_at):
            return None
        return {'bl': unquote(m_bl.group(1)), 'sid': unquote(m_sid.group(1)),
                'at': unquote(m_at.group(1))}

    def _upload_media_to_flow_be(self, image_bytes: bytes, filename: str,
                                 mime_type: str) -> str:
        """Upload 1 ảnh qua RPC `maseQ`. Trả DETAIL_UUID, hoặc '' nếu không đi
        được."""
        try:
            project_id = self._extract_project_id()
            if not project_id:
                self._log('warn', 'batchexecute upload: bỏ qua — profile chưa có '
                                  'project_url dạng /project/{uuid}')
                return ''
            session = self._be_session()
            if not session:
                self._be_note_fail('uploadImage', 'không lấy được session')
                return ''
            captcha = self._get_fresh_recaptcha('IMAGE_GENERATION')
            if not captcha:
                self._be_note_fail('uploadImage', 'không mint được reCAPTCHA')
                return ''
            args = flow_be.build_upload_image_args(
                project_id, captcha, image_bytes, filename, mime_type)
            raw = self._batchexecute_call(flow_be.RPC_UPLOAD_IMAGE, args, session, timeout=120)
            payload = self._batchexecute_parse(raw, flow_be.RPC_UPLOAD_IMAGE) if raw else None
            name = flow_be.parse_uploaded_media_name(payload) if payload else ''
            if not name:
                self._be_note_fail('uploadImage', f'payload không có uuid: {str(payload)[:160]}')
                return ''
            self._be_note_ok()
            self._log('info', f'  ✔ maseQ upload → {name} ({filename}, {len(image_bytes)}B)')
            return name
        except Exception as e:
            self._be_note_fail('uploadImage', e)
            return ''

    def _be_resolve_media_url(self, media_name: str, session: dict) -> str:
        """CDN URL của 1 media qua `as29s` — dùng khi response generate chưa
        kèm URL (cùng cách `_dom_fetch_project_media()` đã làm)."""
        try:
            raw = self._batchexecute_call(flow_be.RPC_GET_MEDIA, [media_name], session, timeout=60)
            detail = self._batchexecute_parse(raw, flow_be.RPC_GET_MEDIA) if raw else None
            if not detail:
                return ''
            urls = re.findall(r'https://[^"\s\\]*flow-content\.google/[^"\s\\]+',
                              json.dumps(detail, ensure_ascii=False))
            return urls[0] if urls else ''
        except Exception:
            return ''

    def _call_image_api_be(self, task: dict, captcha: str) -> list:
        """Sinh ảnh qua RPC `ogiZ0b` (kể cả imageToImage — ref ảnh đính qua
        `imageInputs`). Trả list [{type,url,name}], hoặc [] nếu không đi được.

        `captcha` truyền vào KHÔNG dùng lại được: caller mint nó cho đường
        riêng, nhưng token reCAPTCHA gắn với 1 lần dùng — mint riêng ở đây."""
        project_id = self._extract_project_id()
        if not project_id:
            self._log('warn', 'batchexecute ảnh: bỏ qua — profile chưa có '
                              'project_url dạng /project/{uuid}')
            return []
        aspect = task.get('aspect_ratio') or '16:9'
        if flow_be.aspect_enum(aspect, video=False) is None:
            self._be_note_fail('ảnh', f'tỉ lệ "{aspect}" chưa verify cho batchexecute '
                                       f'(mới verify 16:9 và 9:16)')
            return []
        try:
            session = self._be_session()
            if not session:
                self._be_note_fail('ogiZ0b', 'không lấy được session')
                return []
            source_media = self._parse_source_media(task)
            # GIỮ ĐÚNG THỨ TỰ `source_media` — `ref_names` (set) chỉ dùng cho
            # `exclude` ở parse_image_results, KHÔNG được dùng để dựng
            # `image_inputs` (set không có thứ tự ⇒ ref đảo lộn giữa các lần).
            names = self._upload_source_media_cached(source_media)
            if source_media and not names:
                self._be_note_fail('ogiZ0b', 'không upload được ảnh tham chiếu nào')
                return []
            ref_names = set(names)
            image_inputs = [flow_be.build_image_input_ref(n) for n in names]

            image_model, model_src = model_catalog.resolve_image_model(task.get('model'))
            self._log('info', f'Task #{task["id"]} model="{task.get("model") or ""}" '
                              f'→ imageModelName={image_model} (nguồn: {model_src})')
            fresh = self._get_fresh_recaptcha('IMAGE_GENERATION') or captcha
            args = flow_be.build_text_to_image_args(
                project_id, task.get('prompt_text') or task.get('title') or '', fresh,
                aspect_ratio=aspect, model=image_model,
                image_inputs=image_inputs or None)
            self._log('info', f'POST {flow_be.RPC_TEXT_TO_IMAGE} (batchexecute) '
                              f'project={project_id[:8]}… ({len(image_inputs)} ref ảnh)')
            raw = self._batchexecute_call(flow_be.RPC_TEXT_TO_IMAGE, args, session, timeout=180)
            payload = self._batchexecute_parse(raw, flow_be.RPC_TEXT_TO_IMAGE) if raw else None
            if payload is None:
                self._be_note_fail('ogiZ0b', f'payload rỗng — raw: {str(raw)[:200]}')
                return []
            # Loại projectId VÀ uuid ảnh tham chiếu (đầu vào) — nếu không,
            # imageToImage sẽ coi chính ảnh ref là "kết quả" (bug thật, verify
            # 2026-09-04: 1 lần sinh trả về 2 kết quả).
            results = flow_be.parse_image_results(payload, exclude={project_id} | ref_names)
            for r in results:
                if not r['url']:
                    r['url'] = self._be_resolve_media_url(r['name'], session)
            results = [r for r in results if r['url']]
            if not results:
                self._be_note_fail('ogiZ0b', f'không resolve được URL nào — {str(payload)[:200]}')
                return []
            self._be_note_ok()
            self._log('ok', f'{len(results)} image URL(s) từ batchexecute')
            return results
        except Exception as e:
            self._be_note_fail('ogiZ0b', e)
            return []

    def _call_video_api_be(self, task: dict, captcha: str,
                           media_names: list) -> dict | None:
        """Submit 1 task video qua RPC `YhhmEf`/`MZZa6b`. Trả dict workflow,
        hoặc None nếu không đi được (caller tự fallback aisandbox).

        `frameToVideo` CHƯA hỗ trợ (rpcid `eb1hJf` mới biết tên từ bundle,
        chưa capture shape) — trả None để đi aisandbox."""
        mode = task.get('mode', 'textToVideo')
        if mode == 'frameToVideo':
            # rpcid `eb1hJf` mới biết tên từ bundle, CHƯA capture shape body
            self._be_note_fail('video', 'mode frameToVideo chưa hỗ trợ trên batchexecute '
                                        '(rpcid eb1hJf chưa capture được shape body)')
            return None
        project_id = self._extract_project_id()
        if not project_id:
            self._log('warn', 'batchexecute video: bỏ qua — profile chưa có '
                              'project_url dạng /project/{uuid}')
            return None
        aspect = task.get('aspect_ratio') or '16:9'
        if flow_be.aspect_enum(aspect, video=True) is None:
            self._be_note_fail('video', f'tỉ lệ "{aspect}" chưa verify cho batchexecute '
                                        f'(mới verify 16:9 và 9:16)')
            return None
        try:
            session = self._be_session()
            if not session:
                self._be_note_fail('video', 'không lấy được session')
                return None
            prompt = f'TASK_{task["id"]}:{task.get("prompt_text") or task.get("title") or ""}'
            ingredient = mode in ('imageToVideo', 'componentsToVideo')
            if ingredient and not media_names:
                self._be_note_fail('video', f'mode={mode} chưa có ảnh tham chiếu nào')
                return None
            rpc = (flow_be.RPC_INGREDIENT_TO_VIDEO if ingredient
                   else flow_be.RPC_TEXT_TO_VIDEO)
            model_keys, model_src = self._resolve_video_model_keys(
                task, ingredient=ingredient, aspect=aspect)
            if not model_keys:
                self._be_note_fail('video', f'không chọn được model key: {model_src}')
                return None
            # Nguồn `catalog …` = đọc từ chính Google (chuẩn nhất, có khớp tỉ lệ
            # + thời lượng) nên KHÔNG cảnh báo. Chỉ còn cảnh báo khi phải lui về
            # bảng key cũ — nơi phép suy `t2v`→`r2v` SAI với họ Fast/Quality.
            if (not model_src.startswith('catalog')
                    and model_src not in _MODEL_SRC_FROM_DB
                    and (task.get('model') or '').strip()):
                self._log('warn', f'Task #{task["id"]} model="{task.get("model")}" — KHÔNG có '
                                  f'trong veo_models.model_key (backend), dùng {model_src}: '
                                  f'videoModelKey={model_keys[0]}')

            # Thử lần lượt ứng viên: key bị từ chối vì SAI BIẾN THỂ hạng gói
            # (`_ultra` hay không) bị Google chặn TRƯỚC khi sinh nội dung nên
            # lần hụt KHÔNG tốn quota. Thành công thì NHỚ lại để task sau đi
            # thẳng — xem docstring `flow_models` về việc hạng gói không suy
            # được từ catalog.
            last_err = None
            for attempt, model_key in enumerate(model_keys, 1):
                fresh = self._get_fresh_recaptcha('VIDEO_GENERATION') or captcha
                if ingredient:
                    args = flow_be.build_ingredient_to_video_args(
                        project_id, prompt, fresh, media_names,
                        aspect_ratio=aspect, video_model_key=model_key)
                else:
                    args = flow_be.build_text_to_video_args(
                        project_id, prompt, fresh,
                        aspect_ratio=aspect, video_model_key=model_key)
                self._log('info', f'  Task #{task["id"]} model="{task.get("model") or ""}" '
                                  f'→ videoModelKey={model_key} (nguồn: {model_src}'
                                  + (f', thử {attempt}/{len(model_keys)}'
                                     if len(model_keys) > 1 else '') + ')')
                self._log('info', f'POST {rpc} (batchexecute) mode={mode}')
                try:
                    raw = self._batchexecute_call(rpc, args, session, timeout=180)
                    payload = self._batchexecute_parse(raw, rpc) if raw else None
                except Exception as e:
                    last_err = e
                    if self._is_model_retry_error(e) and attempt < len(model_keys):
                        self._log('warn', f'  model "{model_key}" không dùng được '
                                          f'({getattr(e, "reason", e)}) — thử ứng viên kế')
                        continue
                    raise
                if payload is None:
                    self._be_note_fail('video', f'payload rỗng — raw: {str(raw)[:200]}')
                    return None
                info = flow_be.parse_video_workflow(payload)
                if not info['workflowId'] and not info['mediaId']:
                    self._be_note_fail('video', f'không có workflow/media — {info["raw"]}')
                    return None
                # Nhớ biến thể dùng được cho các task sau của CÙNG tài khoản
                self._flow_ultra_pref = '_ultra' in model_key
                self._be_note_ok()
                self._log('ok', f'✔ Đã submit {rpc} (batchexecute) — '
                                f'workflowId=…{(info["workflowId"] or "")[-16:]} '
                                f'mediaId={info["mediaId"] or "?"}')
                return info
            if last_err:
                raise last_err
            return None
        except Exception as e:
            self._be_note_fail('video', e)
            return None

    def _dom_fetch_project_media(self, lookback_secs: float | None = None):
        """Lấy media của project qua RPC `batchexecute` (2026-09-03) — THAY
        HẲN `flow.projectInitialData` (endpoint đó KHÔNG CÒN TỒN TẠI trên
        `flow.google.com`; gọi thẳng URL cũ trả nguyên shell HTML SPA).

        `Zzl0ze` (=`/FlowService.GetProjectContents`) liệt kê mọi media →
        lọc candidate (đã qua generation + mới đủ gần đây) → `as29s`
        (=`/FlowService.GetMedia`) cho từng cái để lấy prompt + URL CDN đã ký.

        ⚠️ uuid truyền cho `as29s` là `meta[4]` (DETAIL_UUID — khớp uuid nhúng
        trong CDN URL), KHÔNG PHẢI `entry[0]` (tile id) — bug đã trả giá 1 lần:
        gọi bằng `entry[0]` thì 130/130 candidate trả `null`.

        Trả về:
          - `None` nếu THẤT BẠI HẲN (không ở trang project, không trích được
            project id, không harvest được session, lỗi/shape lạ ở Zzl0ze).
          - `list[dict]` `{'name','type','taskId','prompt','url'}` — ĐÃ CÓ SẴN
            `url` (khác hệ cũ phải resolve riêng ở giai đoạn 2, vì `as29s` trả
            prompt VÀ url trong CÙNG 1 lần gọi)."""
        try:
            current = self.driver.current_url or ''
        except Exception:
            current = ''
        if '/project/' not in current:
            self._log('warn', 'batchexecute: không ở trang project — bỏ qua')
            return None
        project_id = self._project_id_from_url(current)
        if not project_id:
            self._log('warn', 'batchexecute: không trích được project id từ URL')
            return None

        session = self._batchexecute_harvest_session()
        if not session:
            return None

        zzl_raw = self._batchexecute_call(
            'Zzl0ze', [f'projects/{project_id}', None, None, None, [1]], session)
        if not zzl_raw:
            return None
        try:
            zzl_data = self._batchexecute_parse(zzl_raw, 'Zzl0ze')
        except Exception as e:
            self._log('warn', f'batchexecute: parse Zzl0ze lỗi: {e}')
            return None
        if not (isinstance(zzl_data, list) and len(zzl_data) >= 2 and isinstance(zzl_data[1], list)):
            self._log('warn', 'batchexecute: Zzl0ze shape lạ — '
                               f'{json.dumps(zzl_data, ensure_ascii=False)[:300]}')
            return None

        entries = zzl_data[1]
        # TỔNG số item của project — dùng bởi _rotate_project_if_full() để biết
        # project đã đầy chưa, KHÔNG cần gọi API riêng chỉ để đếm.
        self._last_project_media_count = len(entries)
        # Đã đọc TOÀN BỘ danh sách rồi — lưu luôn vào chỉ mục uuid của project
        # (file flow_media_index.json) cho bước dùng lại ảnh tham chiếu, khỏi
        # phải gọi Zzl0ze lần nữa. Xem `_flow_id_in_project()`.
        self._flow_listing_store(project_id, self._flow_listing_names(entries))

        lookback = float(lookback_secs if lookback_secs is not None
                         else self._server_settings.get('reconcile_lookback_secs', 7200))
        cutoff = time.time() - lookback
        candidates = []
        for entry in entries:
            try:
                # entry[3] = meta: [title, [ts_sec,ts_ns], null, null,
                #                   DETAIL_UUID, GEN_MARKER, [ts2]]
                # GEN_MARKER (meta[5]) khác None = ĐÃ QUA GENERATION — verify
                # 100% trên 275 entry thật (upload thô luôn None).
                meta = entry[3] if len(entry) > 3 else None
                if not isinstance(meta, list) or len(meta) <= 5:
                    continue
                if meta[5] is None:
                    continue
                detail_uuid = meta[4]
                if not detail_uuid:
                    continue
                ts_pair = meta[1] if len(meta) > 1 else None
                created_at = ts_pair[0] if isinstance(ts_pair, list) and ts_pair else 0
                if created_at and created_at < cutoff:
                    continue
                candidates.append(detail_uuid)
            except Exception:
                continue

        if not candidates:
            self._log('info', f'batchexecute: {len(entries)} media trong project, 0 item '
                               f'đã qua generation trong {lookback:.0f}s gần đây')
            return []

        items = []
        for uuid in candidates:
            raw = self._batchexecute_call('as29s', [uuid], session)
            if not raw:
                continue
            try:
                data = self._batchexecute_parse(raw, 'as29s')
            except Exception as e:
                self._log('warn', f'batchexecute: parse as29s({uuid[:8]}…) lỗi: {e}')
                continue
            if data is None:   # không phải kết quả generation — bỏ qua an toàn
                continue
            flat = json.dumps(data, ensure_ascii=False)
            task_id = self._extract_task_id_from_prompt(flat)
            if task_id is None:
                continue
            video_m = re.search(r'https://flow-content\.google/video/[^"\\\s]+', flat)
            image_m = re.search(r'https://flow-content\.google/image/[^"\\\s]+', flat)
            if video_m:
                url, media_type = video_m.group(0), 'video'
            elif image_m:
                url, media_type = image_m.group(0), 'image'
            else:
                continue
            url = url.replace('\\u003d', '=').replace('\\u0026', '&').replace('\\/', '/')
            items.append({'name': uuid, 'type': media_type, 'taskId': task_id,
                          'prompt': f'TASK_{task_id}:', 'url': url,
                          # (2026-09-14) project Flow chứa media — backend lưu kèm
                          # để lần sau dùng lại id làm ảnh tham chiếu, không upload.
                          'flowProjectId': project_id})

        return items

    # ── Reconcile-based flow (2026-07-16, batchexecute từ 2026-09-03) ─────────
    # Thay hẳn _collect_task_media_via_project_api() (poll + tự tải qua
    # /task/download). Quy trình user mô tả: submit batch → chờ → refresh
    # project → lấy TOÀN BỘ media, so khớp với DATABASE (không tự đoán ở
    # client) → item nào DB chưa có thì TẢI → còn task chưa xong thì lặp lại.
    # Backend đã có sẵn `POST /api/media/reconcile/check`
    # (`backend/routes/reconcile.py`) — nhận `items:[{name,url,type,taskId,
    # prompt}]`, tự so `result_files` trong DB, tự tải + set done, trả
    # `{matched:[taskId,...]}`.
    def _reconcile_project_media(self, task_ids: set, lookback_secs: float | None = None) -> set:
        """Lấy media (qua `_dom_fetch_project_media()`, đã kèm sẵn url) khớp
        `TASK_{id}:` nào đó, gửi TẤT CẢ lên `/reconcile/check` 1 LẦN DUY NHẤT.
        Trả set task_id server xác nhận `matched` lần này.

        (2026-09-03) KHÔNG CÒN chia 2 giai đoạn "check trước → resolve URL cho
        unknown sau" như hệ `projectInitialData` cũ: `as29s` trả prompt VÀ url
        trong cùng 1 lần gọi nên không tách được "rẻ" khỏi "đắt" nữa.

        **KHÔNG lọc theo `task_ids`** (giữ nguyên quyết định 2026-07-17) — gửi
        MỌI item nhận dạng được TASK_id để tự "nhặt" task khác đang mắc kẹt
        trong cùng project (xem CLAUDE.md §11.16)."""
        items = self._dom_fetch_project_media(lookback_secs=lookback_secs)
        if not items:
            if items is not None:
                # Mốc lần quét project thành công gần nhất — `_precheck_batch_done()`
                # dùng để khỏi quét lại đầu batch nếu cuối batch trước vừa quét.
                self._last_reconcile_at = time.time()
                self._log('info', 'reconcile: không có item nào khớp TASK_id vòng này')
            else:
                self._log('warn', 'reconcile: _dom_fetch_project_media() trả về None — '
                                   'lấy media thất bại vòng này (xem warn phía trên)')
            return set()

        try:
            r = self._req('POST', f'{FLOW_SERVER}/api/media/reconcile/check', timeout=60,
                          body={'items': items})
        except Exception as e:
            self._log('warn', f'reconcile/check lỗi: {e}')
            return set()

        self._last_reconcile_at = time.time()
        matched = set(r.get('matched') or [])
        known_cnt = r.get('known') or 0
        self._log('info', f'reconcile: gửi {len(items)} item(s) — {known_cnt} đã có sẵn, '
                           f'{len(matched)} vừa khớp/tải xong')
        bonus = matched - task_ids
        if bonus:
            self._log('ok', f'reconcile: nhặt được thêm {len(bonus)} task khác đang mắc kẹt '
                             f'trong project — {sorted(bonus)}')
        return matched

    def _wait_until_render_done(self):
        """Chờ TỚI KHI không còn tile nào đang render, thay vì `sleep(wait_secs)`
        CỐ ĐỊNH trước khi refresh (2026-07-18, theo mô tả DOM thật user cung cấp).

        Tile ĐANG render có 1 div con hiển thị phần trăm (vd `<div class="sc-...">
        23%</div>`) cạnh icon loại media — dùng regex khớp NỘI DUNG text (`^\\d{1,3}%$`),
        KHÔNG dùng class name đã hash (`sc-40f16b33-7`) vì class kiểu styled-components
        không ổn định giữa các lần build của Google, đã nhiều lần gãy vì lý do này
        (xem lịch sử comment trong file — bài học lặp lại). Tile ĐÃ XONG (thành công —
        percent biến mất, thay bằng media thật) hoặc ĐÃ LỖI (hiện "Không thành công",
        cũng không có percent) đều tự nhiên được coi là "không còn render" — không cần
        phân biệt riêng, vì cả 2 case đều không còn hiện %.

        Quy trình: chờ `download_wait_secs` (mặc định 20s — field CÓ SẴN trong Cài đặt
        từ trước nhưng CHƯA TỪNG được dùng tới, giờ tái sử dụng đúng mục đích) cho
        tile kịp bắt đầu render trước khi check lần đầu, rồi poll mỗi 5s xem còn %
        nào không — hết % là refresh NGAY (nhanh hơn chờ đủ `reconcile_wait_secs` cố
        định như trước nếu batch xong sớm). `reconcile_wait_secs` (Cài đặt) giờ là
        TRẦN TỐI ĐA — nếu DOM cứ báo "đang render" quá lâu (kẹt thật, hoặc Google đổi
        markup khiến detect luôn sai) vẫn refresh sau khi chạm trần, tránh chờ vô hạn."""
        initial_wait = int(self._server_settings.get('download_wait_secs', 20))
        max_wait     = int(self._server_settings.get('reconcile_wait_secs', 60))
        poll_secs    = 5
        self._sleep(initial_wait)
        elapsed = initial_wait
        while elapsed < max_wait:
            try:
                rendering = self._js(r"""
                    var tiles = document.querySelectorAll('[data-tile-id]');
                    for (var i = 0; i < tiles.length; i++) {
                        var divs = tiles[i].querySelectorAll('div');
                        for (var j = 0; j < divs.length; j++) {
                            var t = (divs[j].textContent || '').trim();
                            if (/^\d{1,3}%$/.test(t)) return true;
                        }
                    }
                    return false;
                """)
            except Exception:
                rendering = False
            if not rendering:
                return
            self._sleep(poll_secs)
            elapsed += poll_secs
        self._log('warn', f'_wait_until_render_done: vẫn còn tile báo đang render sau '
                           f'{max_wait}s — refresh luôn (safety-net, tránh chờ vô hạn '
                           'nếu DOM đổi markup hoặc tile kẹt thật)')

    def _wait_and_reconcile_tasks(self, tasks_by_id: dict, max_rounds: int = 10,
                                  wait_tiles: bool = True) -> set:
        """Lặp: chờ → `_reconcile_project_media(pending)` → task matched thì
        `_handle_task_success`. `wait_tiles=True` (DOM): chờ tile hết % rồi
        refresh. `wait_tiles=False` (API): KHÔNG nhìn tile — API không hiện
        tile trên UI; mỗi vòng ngủ `reconcile_wait_secs` rồi đọc
        projectInitialData. Trả set task_id còn lại."""
        pending = set(tasks_by_id.keys())
        pause = int(self._server_settings.get('reconcile_wait_secs', 60))
        for round_i in range(1, max_rounds + 1):
            if not pending:
                break
            if wait_tiles:
                self._wait_until_render_done()
            else:
                self._log('info', f'reconcile API: vòng {round_i}/{max_rounds} — '
                                   f'chờ {pause}s rồi đọc projectInitialData '
                                   f'(không chờ tile)')
                self._sleep(pause)
            matched = self._reconcile_project_media(pending)
            for tid in matched:
                t = tasks_by_id.get(tid, {})
                self._task_start(tid, t.get('mode', 'textToImage'),
                                  t.get('prompt_text') or t.get('title') or '')
                self._log('ok', f'✔ Task #{tid} — reconciled qua projectInitialData '
                                 f'(vòng {round_i}/{max_rounds})')
                self._handle_task_success()
            pending -= matched
            if pending:
                self._log('info', f'reconcile: vòng {round_i}/{max_rounds} — còn '
                                   f'{len(pending)}/{len(tasks_by_id)} task chưa xong')
        if not pending:
            self._rotate_project_if_full()
        return pending

    def _rotate_project_if_full(self):
        """Nếu project hiện tại đã đạt/vượt ngưỡng `max_project_media_items`
        (setting, mặc định 300 — theo yêu cầu user "thêm setting số task hoàn
        thành tối đa 1 project, đạt ngưỡng thì tạo project mới"), tự điều hướng
        về trang chung (`FLOW_PROJECT_URL`) rồi bấm "New project"/"Dự án mới"
        (tái dùng `_click_new_project_button`/`_wait_for_project_url` — cùng 2
        helper `_ensure_flow_page()` đã dùng khi chưa có project nào) để task
        TIẾP THEO không dồn thêm vào project đã quá lớn. Đếm số item qua
        `_last_project_media_count` (cập nhật mỗi lần `_reconcile_project_media()`
        đọc `projectInitialData` thành công) — KHÔNG gọi thêm 1 API/reload riêng
        chỉ để đếm. `max_project_media_items=0` = tắt tính năng (mặc định BẬT,
        300). Best-effort: lỗi ở bất kỳ bước nào chỉ log warning rồi GIỮ NGUYÊN
        project cũ — luân chuyển thất bại không nên chặn task tiếp theo."""
        threshold = int(self._server_settings.get('max_project_media_items', 0) or 0)
        if threshold <= 0:
            return
        count = self._last_project_media_count
        if count < threshold:
            return
        if self._bound_url():
            self._log('warn', f'Project Flow đã gán có {count} item (≥ ngưỡng {threshold}) — chế độ '
                              f'"chạy theo project + email" KHÔNG tự tạo project mới; muốn chuyển thì '
                              f'đổi Flow ID của project Nano Banana')
            return
        self._log('info', f'Project hiện tại đã có {count} item (≥ ngưỡng {threshold}) — '
                           f'tự chuyển sang project mới cho task tiếp theo')
        try:
            self._nav_get(FLOW_PROJECT_URL, f'luân chuyển project: đã {count} item ≥ ngưỡng {threshold}')
            self._sleep(4)
        except Exception as e:
            self._log('warn', f'Luân chuyển project — navigate về trang chung lỗi: {e}')
            return
        if not self._click_new_project_button():
            self._log('warn', 'Luân chuyển project — không tìm thấy nút "New project", '
                               'giữ nguyên project cũ')
            return
        new_url = self._wait_for_project_url(
            timeout=20, exclude_url=self.profile.get('project_url') or '')
        if not new_url:
            self._log('warn', 'Luân chuyển project — bấm "New project" nhưng không bắt được '
                               'URL project MỚI sau 20s, giữ nguyên project cũ')
            return
        self._persist_project_url(new_url, reason='rotate-full')
        self._last_project_media_count = 0
        self._log('ok', f'✔ Đã chuyển sang project mới ({new_url}) — project trước đã đạt '
                         f'ngưỡng {threshold} item')

    def _resolve_tile_media(self, task_id, tile_ids: list, timeout: int = 600) -> list:
        """Poll danh sách tile ids ĐÃ BIẾT tới khi done, resolve CDN URL. Dùng
        chung bởi _collect_task_media_via_tiles (single-task, tile ids từ
        _dom_wait_new_tiles) và _run_tasks_batch (multi-task, tile ids xác định
        qua cursor theo thứ tự submit).

        `name` (2026-08-14, fix bug thật — xem docstring `_extract_cdn_media_name()`)
        LUÔN trích UUID THẬT từ CDN URL đã resolve (khác nhau mỗi lần generate)
        — TRƯỚC ĐÂY dùng chuỗi tự chế `dom_{task_id}_{i+1}` GIỐNG HỆT nhau mỗi
        lần render CÙNG 1 task (cùng task_id + cùng vị trí trong list), khiến
        `_apply_media_to_task()` (backend) tưởng media MỚI đã "tồn tại" rồi và
        ÂM THẦM BỎ QUA không append — task báo `status='done'` nhưng
        `result_files` không hề đổi, không thấy media mới. Chỉ fallback về tên
        tự chế khi KHÔNG trích được UUID (regex không khớp — phòng Google đổi
        định dạng CDN URL)."""
        if not tile_ids:
            return []
        results  = self._dom_poll_done(tile_ids, timeout=timeout)
        urls_raw = [r['src'] for r in results if r.get('src')]
        if not urls_raw:
            return []
        cdn_urls = [self._dom_resolve_url(u) for u in urls_raw]
        return [{'name': _extract_cdn_media_name(u) or f'dom_{task_id}_{i+1}', 'url': u}
                for i, u in enumerate(cdn_urls) if u]

    def _collect_task_media_via_tiles(self, before_ids: set, task_id, count: int) -> list:
        """Luồng CŨ (tile DOM polling) — giữ lại làm fallback khi
        _wait_and_reconcile_tasks() không xác nhận xong sau max_rounds."""
        new_ids = self._dom_wait_new_tiles(before_ids, count, timeout=90)
        if not new_ids:
            raise RuntimeError('No new tiles appeared after submit (90s timeout)')

        self._log('info', f'DOM: {len(new_ids)} tile(s) appeared — polling…')

        media = self._resolve_tile_media(task_id, new_ids)
        if not media:
            raise RuntimeError('No media URLs collected from tiles')
        return media

    def _dom_resolve_url(self, url: str) -> str:
        """Resolve labs.google media URL → CDN signed URL dùng session cookie."""
        try:
            result = self._js_async("""
                var url = arguments[0], done = arguments[1];
                fetch(url, {credentials:'same-origin', redirect:'follow', cache:'no-store'})
                    .then(function(r){
                        var u = r.url || url;
                        if (!u || u.includes('accounts.google') || u.includes('/signin')) {
                            done(url);
                        } else {
                            done(u);
                        }
                    })
                    .catch(function(){ done(url); });
            """, url, timeout=25)
            return result or url
        except Exception as e:
            self._log('warn', f'DOM resolve URL: {e}')
            return url

    def _dom_fill_and_submit(self, prompt: str):
        """Tìm ô nhập prompt, type qua CDP insertText, submit.

        (2026-09-03, VIẾT LẠI HOÀN TOÀN — DOM Flow đổi từ React/Slate.js sang
        Angular/ProseMirror, xem CLAUDE.md "DOM video veo3 trang flow đã thay
        đổi"). 2 thay đổi CĂN BẢN, xác nhận trực tiếp qua Chrome đang mở thật:
        1. `div[role="textbox"]` KHÔNG CÒN TỒN TẠI — ô nhập prompt giờ là
           `.ProseMirror[contenteditable="true"]` (KHÔNG có `role="textbox"`).
        2. Google KHÔNG còn tự submit khi nhấn Enter theo cùng cơ chế cũ (Slate
           `onKeyDown` đọc internal state → gọi generate() trực tiếp) — ProseMirror
           có thể coi Enter là xuống dòng bình thường trong 1 số ngữ cảnh. Chuyển
           hẳn sang bấm THẲNG nút submit thật `button[aria-label="Start generation"]`
           (đã verify: bật `disabled=false` ngay khi ProseMirror có text, qua CDP
           `Input.insertText` — không cần Enter nữa)."""
        # Chờ ô nhập xuất hiện
        ce = None
        for _ in range(30):
            ce = self._js('return document.querySelector(\'.ProseMirror[contenteditable="true"]\');')
            if ce:
                break
            self._sleep(1)
        if not ce:
            raise RuntimeError('DOM: prompt editor not found (.ProseMirror[contenteditable="true"])')

        # Click vào ô nhập qua CDP
        r = self.driver.execute_script(
            "var r=arguments[0].getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};",
            ce)
        x, y = int(r['x']), int(r['y'])
        human = getattr(self, '_cloak_human', None)
        if human:
            self._cdp_click_el(ce)
        else:
            for etype in ('mousePressed', 'mouseReleased'):
                self._cdp('Input.dispatchMouseEvent', {
                    'type': etype, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1,
                })
        self._sleep(0.3)

        # Type prompt: char-by-char cho 20 ký tự đầu → mô phỏng typing
        head = prompt[:20]
        rest = prompt[20:]
        if human:
            human.type_text(head)   # nhịp gõ + gõ sai rồi xoá của cloakbrowser.human
        else:
            for ch in head:
                self._cdp('Input.insertText', {'text': ch})
                self._sleep(0.07 + random.random() * 0.10)
        if rest:
            self._sleep(0.1)
            self._cdp('Input.insertText', {'text': rest})

        self._sleep(0.5)

        # Verify text thật sự vào ô nhập (không chỉ tin insertText "chạy không
        # lỗi" — cùng lớp bug đã gặp ở Gemini's ProseMirror-tương-tự, xem
        # `_gemini_type_prompt()`) trước khi tìm nút Gửi.
        typed_len = self._js('var ce=document.querySelector(\'.ProseMirror[contenteditable="true"]\'); '
                              'return ce ? (ce.innerText||"").length : 0;') or 0
        if typed_len < max(1, len(prompt) * 0.5):
            raise RuntimeError(f'DOM: gõ prompt thất bại — ô nhập chỉ có {typed_len}/{len(prompt)} ký tự')

        # Submit = bấm nút "Start generation" thật (KHÔNG còn dùng Enter — xem
        # docstring). Chờ nút hết disabled tối đa 10s trước khi bấm.
        submit_btn = None
        for _ in range(40):
            submit_btn = self._js(
                'var b=' + _flow_btn_expr('flowStartGeneration', _FB_SUBMIT) + ';'
                'return (b && !b.disabled) ? b : null;')
            if submit_btn:
                break
            self._sleep(0.25)
        if not submit_btn:
            credit_warn = self._js("""
                var keys = arguments[0].map(function(k){ return k.toLowerCase(); });
                var box = document.querySelector('flow-prompt-box') || document;
                var b = [...box.querySelectorAll('button')].find(function(e){
                    var a = (e.getAttribute('aria-label') || '').toLowerCase();
                    return keys.some(function(k){ return a.includes(k); });
                });
                return b ? b.getAttribute('aria-label') : null;
            """, get_i18n_texts().get('flowCreditWarning') or ['credit'])
            if credit_warn:
                raise RuntimeError(f'DOM: tài khoản KHÔNG ĐỦ CREDIT — nút "Bắt đầu tạo" bị khoá '
                                   f'(Flow hiện "{credit_warn}")')
            raise RuntimeError('DOM: nút "Start generation" không sẵn sàng (vẫn disabled) sau 10s')
        self._cdp_click_el(submit_btn)
        self._log('info', f'DOM prompt submitted ({len(prompt)} chars)')

    def _dom_is_config_open(self) -> bool:
        """(2026-09-03) Popup settings MỚI (`<flow-prompt-box-settings>`,
        Angular CDK overlay `popover="manual"`) không có `aria-expanded`/
        `data-state` đáng tin cậy trên nút trigger (xác nhận trực tiếp qua
        Chrome đang mở thật — luôn đọc `null`). Dùng tín hiệu NỘI DUNG thay
        thế: component này chỉ tồn tại trong DOM khi popup đang mở, biến mất
        hoàn toàn khi đóng (đã verify: re-click trigger → element biến mất)."""
        return bool(self._js('return !!document.querySelector("flow-prompt-box-settings");'))

    def _dom_configure(self, task: dict):
        """Mở settings popup, chọn Mode/Video type/Aspect ratio/Output count/
        Model. Best-effort.

        (2026-09-03, VIẾT LẠI HOÀN TOÀN — Google Flow đổi domain
        `labs.google/.../tools/flow` → REDIRECT sang app MỚI `flow.google.com`
        (Angular Material thay React cũ) — xem CLAUDE.md "DOM video veo3 trang
        flow đã thay đổi". Xác nhận qua Chrome đang mở THẬT (attach
        debuggerAddress, profile huavantien84_2). Cấu trúc popup mới
        (`<flow-prompt-box-settings>`): mỗi nhóm là `<flow-toggles aria-label=
        "...">` chứa `<mat-button-toggle-group><button role="radio"
        aria-checked="true|false">` — nhóm "Mode" ("Image"/"Video"), "Video
        type" ("Frames"/"Ingredients", chỉ có ý nghĩa khi Mode=Video — mapping
        GIỮ NGUYÊN ý nghĩa cũ 'Thành phần'→Ingredients/'Khung hình'→Frames,
        xem lịch sử ở CLAUDE.md §5.2), "Aspect ratio" ("16:9"/"9:16"...),
        "Output count" ("x1".."x4"). Model là 1 nút RIÊNG, aria-label DUY NHẤT
        trên trang (`button[aria-label="Select model family"]`), mở
        `<mat-menu>`. KHÔNG còn khái niệm "tab" (`button[role="tab"]`)/icon-
        ligature (`<i>`) như UI cũ — toàn bộ đổi sang `TOGGLE_MATCH_JS`."""
        try:
            mode         = task.get('mode', 'textToImage')
            aspect_ratio = (task.get('aspect_ratio') or '').strip()
            output_count = int(task.get('output_count') or 1)
            model        = (task.get('model') or '').strip()

            config_btn = self._wait_js(_flow_btn_js('flowSettingsTrigger', _FB_SETTINGS),
                                       timeout=4.0)
            if not config_btn:
                self._log('warn', 'DOM: config button not found — skip settings')
                return

            if not self._dom_is_config_open():
                self._cdp_click_el(config_btn)
                for _ in range(20):
                    if self._dom_is_config_open():
                        break
                    self._sleep(0.15)
                self._sleep(0.3)

            def select_toggle(group_label, variants, timeout=2.0) -> bool:
                """Chọn 1 option trong `<flow-toggles aria-label="group_label">`
                — bỏ qua nếu đã đúng (`aria-checked="true"`), click nếu cần.
                Trả `True` nếu tìm được nhóm+option (không nhất thiết đã đổi
                gì), `False` nếu không tìm thấy."""
                t_list = variants if isinstance(variants, list) else [variants]
                btn = self._wait_js(TOGGLE_MATCH_JS, (group_label, t_list), timeout=timeout)
                if not btn:
                    return False
                checked = self._js("return arguments[0].getAttribute('aria-checked');", btn)
                if checked != 'true':
                    self._cdp_click_el(btn)
                    self._sleep(0.4)
                return True

            # ── Mode (Image / Video) ────────────────────────────────────
            # (2026-09-24) Mode chọn theo ICON (image/videocam) — chữ trên nút
            # đổi theo ngôn ngữ tài khoản ("Image"/"Hình ảnh").
            is_video_mode = mode in (
                'textToVideo', 'imageToVideo', 'frameToVideo', 'componentsToVideo')
            i18n = get_i18n_texts()
            select_toggle(i18n.get('flowToggleMode'),
                          ['icon:videocam', 'icon:play_circle'] if is_video_mode else ['icon:image'])

            # ── Video type (Frames / Ingredients) ───────────────────────
            video_type_key = {
                'imageToVideo':      'flowVideoTypeIngredients',
                'componentsToVideo': 'flowVideoTypeIngredients',
                'frameToVideo':      'flowVideoTypeFrames',
            }.get(mode)
            if video_type_key:
                if not select_toggle(i18n.get('flowToggleVideoType'), i18n.get(video_type_key)):
                    self._log('warn', f'DOM: không tìm thấy lựa chọn loại video cho mode={mode}')

            # ── Aspect ratio ─────────────────────────────────────────────
            if aspect_ratio:
                if not select_toggle(i18n.get('flowToggleAspectRatio'), [aspect_ratio]):
                    self._log('warn', f'DOM: ratio button "{aspect_ratio}" not found')

            # ── Thời lượng video (2026-09-24 — nhóm "Thời lượng video" mới) ──
            if is_video_mode:
                try:
                    dur = int(float(task.get('video_duration') or 0))
                except (TypeError, ValueError):
                    dur = 0
                if dur:
                    if not select_toggle(i18n.get('flowToggleDuration'),
                                         [f'{dur} giây', f'{dur}s', f'{dur} sec', f'{dur} seconds']):
                        self._log('warn', f'DOM: không có lựa chọn thời lượng {dur}s — giữ mặc định')

            # ── Output count ─────────────────────────────────────────────
            count_variants = {1: ['x1'], 2: ['x2'], 3: ['x3'], 4: ['x4']}.get(output_count, ['x1'])
            select_toggle(i18n.get('flowToggleOutputCount'), count_variants)

            # ── Model dropdown ────────────────────────────────────────────
            # (2026-08-07) RETRY + XÁC NHẬN THẬT SỰ ĐÃ ÁP DỤNG — xem
            # `_dom_select_model_verified()` + CLAUDE.md §11.28. Hết cả 3 lần
            # vẫn không xác nhận được thì RAISE (không nuốt lỗi) để task này
            # error/retry thay vì âm thầm generate sai model, tốn quota thật.
            # (2026-09-03) KHÔNG còn cần truyền `popup` để scope — nút trigger
            # model giờ có aria-label DUY NHẤT trên trang.
            if model:
                self._dom_select_model_verified(model)

            # ── Đóng settings popup ─────────────────────────────────────
            if self._dom_is_config_open():
                self._cdp_click_el(config_btn)
                self._sleep(0.4)

        except ModelSelectionFailed:
            # KHÔNG nuốt — model sai là lỗi NGHIÊM TRỌNG (tốn quota thật để
            # generate sai), phải để task error/retry thay vì âm thầm tiếp tục
            # với model cũ còn sót lại từ task trước. Cố đóng popup lại trước
            # khi raise để không để lại popup mở dở cho bước kế tiếp.
            try:
                if self._dom_is_config_open():
                    btn = self._js(_flow_btn_js('flowSettingsTrigger', _FB_SETTINGS))
                    if btn:
                        self._cdp_click_el(btn)
            except Exception:
                pass
            raise
        except Exception as e:
            self._log('warn', f'DOM configure: {e}')

    def _dom_select_model_verified(self, model: str, attempts: int = 3):
        """Mở dropdown model, click item khớp `model` (qua MODEL_MATCH_JS), rồi
        XÁC NHẬN THẬT SỰ đã áp dụng bằng cách đọc lại label của trigger button
        — KHÔNG chỉ tin "đã tìm thấy + đã click là xong". Retry tối đa
        `attempts` lần trước khi raise `ModelSelectionFailed`. Xem
        CLAUDE.md §11.28.

        (2026-09-03, VIẾT LẠI — DOM Flow đổi từ React sang Angular Material,
        xem CLAUDE.md "DOM video veo3 trang flow đã thay đổi") — nút trigger
        model giờ có `aria-label="Select model family"` DUY NHẤT trên trang
        (xác nhận qua Chrome đang mở thật) — KHÔNG còn cần kỹ thuật scope-
        theo-popup (`window.__modelPopupRoot`) từng cần thiết vì bản React cũ
        tìm theo icon `arrow_drop_down` (không unique, nhiều nút khác cùng
        icon). Label trigger đọc qua `.model-select-trigger-content` (span
        RIÊNG, chỉ cần xoá `<mat-icon>` con — không còn dính prefix icon-
        ligature như bản cũ, nên bỏ luôn bước `.replace(/^[^A-Za-z0-9]+/, '')`.

        ⚠️ (2026-09-03) FIX THẬT bắt được lúc verify TRỰC TIẾP trên Chrome đang
        mở thật — chọn 1 model KHÁC model hiện tại đôi khi làm ĐÓNG HẲN TOÀN BỘ
        settings popup `<flow-prompt-box-settings>` (không chỉ đóng submenu
        model) ngay sau khi click item — xác nhận bằng poll từng bước: panel
        vẫn còn 2 overlay pane (settings+submenu) ngay lúc vừa click, rồi CẢ
        HAI biến mất trong vòng ~0.3s. Hành vi này KHÔNG NHẤT QUÁN 100% (verify
        lặp lại nhiều lần cho thấy có lúc panel VẪN MỞ và label tự cập nhật
        đúng trong ~0.3s, có lúc panel đóng hẳn) — cùng lớp flaky timing/
        network-roundtrip (Google có thể validate tương thích model×ratio×
        resolution phía server) đã ghi nhận nhiều lần cho dropdown model từ
        trước (§11.28, bản React cũ). Bản cũ ở đây đọc lại label BẰNG CÁCH
        `document.querySelector('button[aria-label="Select model family"]')`
        MÀ KHÔNG BAO GIỜ kiểm tra panel còn mở hay không — nếu panel đã đóng,
        query luôn trả `null`, verify luôn thất bại DÙ SELECTION ĐÃ ÁP DỤNG
        ĐÚNG, và các lần retry sau đó CŨNG THẤT BẠI THEO KIỂU KHÁC ("nút Select
        model family không tìm thấy") vì không hề thử MỞ LẠI panel. Fix:
        `_ensure_settings_open()` — kiểm tra + tự mở lại panel TRƯỚC khi tìm
        `drop_btn` (đầu mỗi attempt) VÀ TRƯỚC khi đọc verify (sau khi click
        item, nếu panel đã đóng thì mở lại rồi mới đọc)."""
        settings_trigger_js = _flow_btn_js('flowSettingsTrigger', _FB_SETTINGS)
        find_drop_btn_js = _flow_btn_js('flowSelectModel', _FB_MODEL)
        read_label_js = """
            var btn = """ + _flow_btn_expr('flowSelectModel', _FB_MODEL) + """;
            if (!btn) return null;
            var span = btn.querySelector('.model-select-trigger-content');
            if (!span) return null;
            var clone = span.cloneNode(true);
            clone.querySelectorAll('mat-icon').forEach(function(i){ i.remove(); });
            return clone.textContent.trim();
        """

        def _ensure_settings_open(timeout=3.0) -> bool:
            """Mở lại settings popup nếu đang đóng (đã tự đóng do chọn model
            khác, hoặc bị đóng bởi Escape dọn dẹp ở attempt trước). No-op nếu
            đã mở sẵn. Trả `True` nếu chắc chắn đang mở sau khi gọi."""
            if self._dom_is_config_open():
                return True
            btn = self._wait_js(settings_trigger_js, timeout=timeout)
            if not btn:
                return False
            self._cdp_click_el(btn)
            for _ in range(20):
                if self._dom_is_config_open():
                    return True
                self._sleep(0.15)
            return self._dom_is_config_open()

        last_seen = None
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                # Dọn state dở từ lần thử trước (dropdown model có thể còn mở
                # nếu lần trước click bị lệch/không phản hồi) trước khi thử lại.
                self._cdp('Input.dispatchKeyEvent',
                          {'type': 'keyDown', 'key': 'Escape', 'code': 'Escape', 'keyCode': 27})
                self._sleep(0.2)
                self._cdp('Input.dispatchKeyEvent',
                          {'type': 'keyUp', 'key': 'Escape', 'code': 'Escape', 'keyCode': 27})
                self._sleep(0.3)

            if not _ensure_settings_open():
                self._log('warn', f'DOM: không mở lại được settings popup (lần {attempt}/{attempts})')
                self._sleep(0.5)
                continue

            drop_btn = self._wait_js(find_drop_btn_js, timeout=3.0)
            if not drop_btn:
                self._log('warn', f'DOM: nút "Select model family" không tìm thấy '
                                   f'(lần {attempt}/{attempts})')
                self._sleep(0.5)
                continue

            self._cdp_click_el(drop_btn)
            # RETRY poll thay vì fixed sleep+check 1 lần — dropdown vừa mở có
            # thể còn đang render animation. Fix chọn NHẦM model khi 1 label
            # là PREFIX của label khác — xem MODEL_MATCH_JS. Test độc lập:
            # tests/_test_model_select.py.
            menu_item = self._wait_js(MODEL_MATCH_JS, (model,), timeout=4.0)
            if not menu_item:
                self._log('warn', f'DOM: model "{model}" không thấy trong dropdown '
                                   f'(lần {attempt}/{attempts})')
                self._sleep(0.5)
                continue

            self._cdp_click_el(menu_item)
            # Cho panel 1 nhịp để hoặc (a) tự cập nhật label tại chỗ, hoặc
            # (b) tự đóng hẳn — cả 2 đều là hành vi THẬT đã quan sát được, xem
            # docstring. Đọc lại trạng thái panel TRƯỚC khi verify, mở lại nếu
            # cần — KHÔNG được giả định panel còn mở như bản cũ.
            self._sleep(0.6)
            _ensure_settings_open(timeout=2.0)

            # Verify = poll trigger label tới khi khớp ĐÚNG model muốn (không phải
            # chỉ "có label nào đó") — kết hợp luôn việc "chờ UI cập nhật xong" và
            # "xác nhận đúng nội dung" trong 1 bước.
            applied = self._wait_js("""
                var wanted = arguments[0];
                var label = (function(){ %s })();
                if (!label) return false;
                // (2026-09-24) Nhãn trigger có tiền tố emoji ("🍌 Nano Banana 2") —
                // bỏ ký tự không phải chữ/số ở đầu cả 2 vế trước khi so.
                var norm = function(x){ return x.trim().replace(/^[^A-Za-z0-9]+/, '').trim(); };
                return norm(label) === norm(wanted);
            """ % read_label_js, (model,), timeout=3.0, interval=0.15)

            if applied:
                self._log('info', f'DOM: model selected → {model} (đã xác nhận, '
                                   f'lần {attempt}/{attempts})')
                return

            last_seen = self._js(read_label_js)
            self._log('warn', f'DOM: đã click model "{model}" nhưng xác nhận KHÔNG khớp '
                               f'(hiện đang hiển thị "{last_seen}") — lần {attempt}/{attempts}')
            self._sleep(0.5)

        # Nếu attempt CUỐI CÙNG dừng lại ở nhánh "model không thấy trong
        # dropdown" (KHÔNG phải "button không tìm thấy"), dropdown model VẪN
        # ĐANG MỞ khi raise — để nguyên state này sẽ làm caller kế tiếp (config
        # popup của `_dom_configure()`, hoặc lần gọi RIÊNG khác) bị rối: click
        # lại nút trigger sẽ TOGGLE ĐÓNG (vì nó đang mở) thay vì mở mới, khiến
        # lần thử tiếp theo luôn thất bại theo kiểu KHÁC hẳn (không tìm thấy cả
        # nút) dù không liên quan gì tới nguyên nhân gốc. Dọn sạch trước khi raise.
        try:
            self._cdp('Input.dispatchKeyEvent',
                      {'type': 'keyDown', 'key': 'Escape', 'code': 'Escape', 'keyCode': 27})
            self._sleep(0.15)
            self._cdp('Input.dispatchKeyEvent',
                      {'type': 'keyUp', 'key': 'Escape', 'code': 'Escape', 'keyCode': 27})
        except Exception:
            pass
        raise ModelSelectionFailed(
            f'Không thể xác nhận model "{model}" đã được áp dụng sau {attempts} lần thử '
            f'(lần cuối hiển thị "{last_seen}")'
        )

    def _dom_upload_images(self, source_media: list, mode: str):
        """
        Upload ảnh đính kèm qua menu "Add ingredients" MỚI của Flow.

        (2026-09-03, VIẾT LẠI HOÀN TOÀN — Google Flow đổi domain sang app
        Angular Material mới `flow.google.com`, xem CLAUDE.md "DOM video veo3
        trang flow đã thay đổi"). Xác nhận qua Chrome đang mở THẬT (attach
        debuggerAddress, profile huavantien84_2) — kể cả nhánh upload file
        MỚI hoàn toàn (không chỉ nhánh chọn ảnh đã có sẵn), vì đây là trường
        hợp PHỔ BIẾN NHẤT trong thực tế (mỗi ảnh scene chỉ dùng đúng 1 lần).

        Luồng picker MỚI, khác hẳn UI React/icon-ligature cũ:
          B1. Click `button[aria-label="Add ingredients to the prompt box"]`
              (thay nút icon `add_2` cũ).
          B2. Click `mat-list-item[role="tab"]` chứa text "Images" trong
              side-nav (thay tab `button[role="tab"]` icon `image` cũ).
          B3. Gõ tên file (bỏ extension) vào
              `input[placeholder="Search assets"]`.
          B4. Kiểm tra gallery: `button.asset-item[role="option"]`.
              — CÓ item khớp (Case 1, ảnh đã từng upload) → click item ĐÓ:
                xác nhận trực tiếp 1 click DUY NHẤT tự động đính kèm vào
                composer + đóng hẳn popover luôn — KHÔNG còn bước "Add to
                prompt" riêng cho case này (khác UI cũ).
              — KHÔNG có item (Case 2, ảnh chưa từng upload) → click nút
                "Upload media" để lộ `input[type=file]` ẩn, inject file qua
                DataTransfer (GIỮ NGUYÊN kỹ thuật cũ — API HTML chuẩn, không
                phụ thuộc framework của trang), chờ item hết trạng thái
                "Uploading" rồi click nó → bấm nút "Add to prompt" (persistent
                bottom-bar, có mặt ngay khi mở picker — khác per-item cũ) để
                attach + đóng popover.
          Verify: đếm chip `<flow-image-ingredient-chip>` trong composer
          (thay `[data-card-open]` cũ).

        Giữ NGUYÊN cấu trúc resilience đã có: tải file trước (HTTP fetch),
        retry TOÀN BỘ B1-B4/B5 tối đa `_MAX_ATTACH_ATTEMPTS` lần/ảnh nếu
        verify thất bại, lưới an toàn chống-đính-trùng.
        """
        if not source_media:
            return

        import base64 as _b64

        _MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                 '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}

        def _inject_file_to_input(file_input_el, name: str, data_url: str):
            """
            Inject file qua DataTransfer + atob() — KHÔNG dùng fetch() để tránh CSP
            block. Ghi data URL vào window.__selUpload trước, rồi atob() trong
            execute_script. (KHÔNG đổi so với bản cũ — API HTML chuẩn, không phụ
            thuộc framework React/Angular của trang, vẫn hoạt động đúng trên input
            ẩn của UI mới, xem B4/Case 2.)
            """
            try:
                self._js("window.__selUpload = arguments[0];", data_url)
                result = self._js("""
                    var name=arguments[0], inp=arguments[1];
                    var dataUrl = window.__selUpload;
                    window.__selUpload = null;
                    try {
                        var parts = dataUrl.split(',');
                        var mime  = parts[0].split(':')[1].split(';')[0];
                        var bstr  = atob(parts[1]);
                        var u8arr = new Uint8Array(bstr.length);
                        for (var j=0; j<bstr.length; j++) u8arr[j]=bstr.charCodeAt(j);
                        var blob = new Blob([u8arr], {type:mime});
                        var file = new File([blob], name, {type:mime});
                        var dt   = new DataTransfer();
                        dt.items.add(file);
                        var ns = Object.getOwnPropertyDescriptor(
                            HTMLInputElement.prototype,'files')?.set;
                        if (ns) ns.call(inp, dt.files);
                        else {
                            try {
                                Object.defineProperty(inp,'files',
                                    {configurable:true,value:dt.files});
                            } catch(_) { inp.files=dt.files; }
                        }
                        inp.dispatchEvent(new Event('input', {bubbles:true,cancelable:true}));
                        inp.dispatchEvent(new Event('change',{bubbles:true,cancelable:true}));
                        return true;
                    } catch(e) { return 'err:'+e.message; }
                """, name, file_input_el)
                if result is True:
                    return True
                self._log('warn', f'DOM inject: {result}')
                return False
            except Exception as e:
                self._log('warn', f'DOM inject exception: {e}')
                return False

        # Đếm ảnh THẬT SỰ đã đính kèm vào prompt — mỗi ảnh đính kèm là 1
        # `<flow-image-ingredient-chip>` chứa `<img class="chip-image">` trong
        # thanh `<flow-ingredient-bar>` cạnh ô nhập liệu (2026-09-03, DOM MỚI —
        # thay `button[data-card-open] img[src*=getMediaUrlRedirect]` cũ, xác
        # nhận qua Chrome đang mở thật sau khi click "Add to prompt"). KHÔNG
        # cần scope thủ công lên tới container composer như bản cũ (từng phải
        # tránh đếm nhầm `[data-card-open]` khác trên trang) — component
        # `<flow-image-ingredient-chip>` CHỈ xuất hiện trong thanh ingredient
        # của composer, không dùng lẫn ở nơi khác.
        def _count_attached_refs():
            # Cả "Thành phần" lẫn ô "Bắt đầu/Kết thúc" của "Khung hình" đều hiện
            # ảnh đã đính bằng chip này (verify trên profile 25, 2026-09-24).
            # ĐỪNG đếm mọi <img> trong ô prompt — tài khoản hết credit có thêm
            # `img.prompt-warning-sphere-image` sẽ làm lệch số ảnh mong đợi.
            return self._js(
                "return document.querySelectorAll('flow-image-ingredient-chip img.chip-image').length;"
            ) or 0

        # Số lần thử lại TOÀN BỘ search/upload cho 1 ảnh nếu sau khi đính kèm
        # prompt vẫn KHÔNG hiện đủ ảnh — theo yêu cầu user (lịch sử, xem
        # CLAUDE.md §5.3): "hãy viết thêm validate nếu đủ image đính kèm
        # prompt thì mới gửi kèm prompt, còn không lặp lại quá trình
        # search/upload ảnh tham chiếu".
        _MAX_ATTACH_ATTEMPTS = 3

        # Tải ảnh về temp dir
        tmp_dir = tempfile.mkdtemp(prefix='selenium_flow_img_')
        try:
            tmp_files = []
            for item in source_media:
                url  = item.get('url') or ''
                name = item.get('filename') or item.get('name') or f'image_{len(tmp_files)+1}.jpg'
                if url.startswith('/'):
                    url = f'{FLOW_SERVER}{url}'
                if not url.startswith('http'):
                    self._log('warn', f'DOM upload: skip invalid url "{url}"')
                    continue
                try:
                    resp = req_lib.get(url, timeout=30)
                    if not resp.ok:
                        self._log('warn', f'DOM upload: HTTP {resp.status_code} for {url}')
                        continue
                    # Đảm bảo tên có extension hợp lệ
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in _MIME:
                        ct = resp.headers.get('content-type', '')
                        for k, v in _MIME.items():
                            if v in ct:
                                name = os.path.splitext(name)[0] + k
                                ext  = k
                                break
                    tmp_path = os.path.join(tmp_dir, name)
                    with open(tmp_path, 'wb') as f:
                        f.write(resp.content)
                    mime     = _MIME.get(ext, 'image/jpeg')
                    b64      = _b64.b64encode(resp.content).decode()
                    data_url = f'data:{mime};base64,{b64}'
                    tmp_files.append({'name': name, 'path': tmp_path,
                                      'data_url': data_url, 'mime': mime})
                    self._log('info', f'DOM upload: fetched {name} ({len(resp.content)//1024}KB)')
                except Exception as e:
                    self._log('warn', f'DOM upload: fetch error {name}: {e}')

            if not tmp_files:
                self._log('warn', 'DOM upload: no files fetched — skip upload step')
                return

            def _open_add_menu(idx: int = 0) -> bool:
                """B1: mở popover "Add ingredients" — nếu 1 attempt trước để
                nó mở dở (chưa đóng đúng cách), click lại nút trigger để đóng
                (toggle) trước khi mở lại từ đầu, tránh state lẫn lộn."""
                if self._js("return !!document.querySelector('flow-add-menu-popover-content');"):
                    stale_btn = self._js(_flow_btn_js('flowAddIngredients', _FB_ADD_REF))
                    if stale_btn:
                        self._cdp_click_el(stale_btn)
                        self._sleep(0.5)
                if mode == 'frameToVideo':
                    # (2026-09-24) Chế độ "Khung hình" KHÔNG có nút đính thành
                    # phần — có 2 ô "Bắt đầu"/"Kết thúc" (`button.empty-chip`),
                    # bấm vào mở CÙNG popup chọn ảnh ("Chọn một hình ảnh khung").
                    i18n = get_i18n_texts()
                    names = i18n.get('flowFrameStart' if idx == 0 else 'flowFrameEnd') or []
                    add_btn = self._wait_js("""
                        var names = arguments[0], idx = arguments[1];
                        var box = document.querySelector('flow-prompt-box');
                        if (!box) return null;
                        var btns = [...box.querySelectorAll('button')];
                        return btns.find(function(b){
                            return names.indexOf((b.textContent||'').trim()) >= 0;
                        }) || box.querySelectorAll('button.empty-chip')[0] || null;
                    """, (names, idx), timeout=4.0)
                else:
                    add_btn = self._wait_js(_flow_btn_js('flowAddIngredients', _FB_ADD_REF),
                                            timeout=4.0)
                if not add_btn:
                    return False
                self._cdp_click_el(add_btn)
                popover = self._wait_js(
                    'return document.querySelector(\'flow-add-menu-popover-content\') || null;',
                    timeout=4.0)
                return bool(popover)

            def _press_add_to_prompt(idx: int, expected_count: int):
                """B5: bấm "Add to prompt"/"Thêm vào câu lệnh" nếu prompt CHƯA đủ
                ảnh (click item đôi khi tự đính + đóng popover, lúc đó bỏ qua).
                Không thấy nút cũng không raise — bước validate quyết định."""
                if _count_attached_refs() >= expected_count:
                    return
                add_btn = self._wait_js("""
                    var names = arguments[0].map(function(n){ return n.toLowerCase(); });
                    var c = document.querySelector('.cdk-overlay-container');
                    if (!c) return null;
                    return [...c.querySelectorAll('button')].find(function(b){
                        return names.indexOf((b.textContent||'').trim().toLowerCase()) >= 0;
                    }) || null;
                """, (get_i18n_texts().get('flowAddToPrompt') or ['Add to prompt'],), timeout=4.0)
                if add_btn:
                    # Chờ 1s cho Angular gắn xong handler trước khi bấm.
                    self._sleep(1)
                    self._cdp_click_el(add_btn)
                    self._sleep(1)
                else:
                    self._log('info', f'DOM upload: không thấy nút "Add to prompt" '
                                       f'(img {idx+1}) — click item có thể đã tự đính kèm, '
                                       'để bước validate quyết định')

            def _attach_one_reference(img: dict, idx: int, expected_count: int) -> bool:
                """B1-B5 cho ĐÚNG 1 ảnh qua popover "Add ingredients" mới. Trả
                True nếu sau đó prompt THẬT SỰ hiện đủ `expected_count` ảnh
                đính kèm (đếm qua `_count_attached_refs()`), False nếu không.
                Raise RuntimeError cho lỗi CẤU TRÚC thật (không tìm thấy
                nút/input) — caller (vòng lặp ngoài) tự bắt và thử lại."""
                # Lưới an toàn CHỐNG UPLOAD TRÙNG: nếu lần gọi TRƯỚC (attempt
                # trước) thật ra ĐÃ đính kèm thành công nhưng validate poll của
                # NÓ hết hạn trước khi thấy (false-negative), ảnh đã nằm sẵn
                # trong prompt rồi — không cần upload thêm bản nữa.
                if _count_attached_refs() >= expected_count:
                    return True

                if not _open_add_menu(idx):
                    raise RuntimeError(
                        f'[{mode}] "Add ingredients" popover không mở được (img {idx+1})')

                # ── B2: Click tab "Images" trong side-nav ────────────────
                img_tab = self._wait_js("""
                    var names = arguments[0];
                    var tabs = [...document.querySelectorAll('mat-list-item[role="tab"]')];
                    return tabs.find(function(el){
                        var i = el.querySelector('mat-icon');
                        return i && i.textContent.trim() === 'image';
                    }) || tabs.find(function(el){
                        return names.some(function(n){ return el.textContent.includes(n); });
                    }) || null;
                """, (get_i18n_texts().get('imageTab') or ['Images'],), timeout=4.0)
                if not img_tab:
                    raise RuntimeError(f'[{mode}] "Images" tab not found trong Add menu (img {idx+1})')
                self._cdp_click_el(img_tab)
                self._sleep(0.6)

                # ── B3: Search tên file ──────────────────────────────────
                search_name = re.sub(r'\.[^.]+$', '', img['name'])  # bỏ extension
                search_input = self._wait_js("""
                    var ph = arguments[0];
                    var c = document.querySelector('.cdk-overlay-container');
                    if (!c) return null;
                    return [...c.querySelectorAll('input')].find(function(i){
                        return ph.indexOf(i.getAttribute('placeholder') || '') >= 0;
                    }) || c.querySelector('flow-add-menu-popover-content input[type="text"], '
                                        + 'flow-add-menu-popover-content input:not([type])') || null;
                """, (get_i18n_texts().get('searchPlaceholder') or ['Search assets'],), timeout=3.0)
                if search_input and search_name:
                    # CDP click để focus
                    rc = self.driver.execute_script(
                        "var r=arguments[0].getBoundingClientRect();"
                        "return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)};",
                        search_input)
                    self._cdp('Input.dispatchMouseEvent', {
                        'type': 'mousePressed', 'x': int(rc['x']), 'y': int(rc['y']),
                        'button': 'left', 'clickCount': 1,
                    })
                    self._cdp('Input.dispatchMouseEvent', {
                        'type': 'mouseReleased', 'x': int(rc['x']), 'y': int(rc['y']),
                        'button': 'left', 'clickCount': 1,
                    })
                    self._sleep(0.2)
                    # Ctrl+A → select all → insertText thay thế nội dung cũ
                    for ktype in ('keyDown', 'keyUp'):
                        self._cdp('Input.dispatchKeyEvent', {
                            'type': ktype, 'key': 'a', 'code': 'KeyA',
                            'keyCode': 65, 'modifiers': 2,
                        })
                        self._sleep(0.05)
                    self._cdp('Input.insertText', {'text': search_name})
                    self._sleep(1.5)  # đợi gallery filter render

                # ── B4: Kiểm tra gallery ─────────────────────────────────
                gallery_item = self._js("""
                    var c = document.querySelector('.cdk-overlay-container');
                    return c ? c.querySelector('button.asset-item[role="option"]') : null;
                """)

                if gallery_item:
                    # Case 1 — ảnh ĐÃ từng upload: 1 click TỰ ĐỘNG đính kèm +
                    # đóng popover luôn (xác nhận trực tiếp qua Chrome thật —
                    # KHÔNG có bước "Add to prompt" riêng cho case này, khác
                    # UI cũ).
                    self._log('info', f'DOM upload: Case 1 — "{img["name"]}" đã có sẵn, click để đính kèm')
                    self._cdp_click_el(gallery_item)
                    self._sleep(1.5)
                    # (2026-09-24, verify trên profile 25) Ở chế độ Video, click chỉ
                    # CHỌN ảnh — popover vẫn mở, phải bấm "Thêm vào câu lệnh" mới
                    # đính. Không bấm thì lần thử lại (đóng popover cũ) đính luôn
                    # ảnh đã chọn → TRÙNG ảnh. Đủ chip rồi thì hàm tự bỏ qua.
                    _press_add_to_prompt(idx, expected_count)
                else:
                    # Case 2 — ảnh CHƯA từng upload (trường hợp PHỔ BIẾN NHẤT
                    # trong thực tế) → click "Upload media" để lộ
                    # input[type=file] ẩn, inject qua DataTransfer, chờ item
                    # hết "Uploading" rồi click nó + bấm "Add to prompt".
                    self._log('info', f'DOM upload: Case 2 — upload mới "{img["name"]}" qua DataTransfer')

                    upload_btn = self._wait_js("""
                        var names = arguments[0];
                        var c = document.querySelector('.cdk-overlay-container');
                        if (!c) return null;
                        var btns = [...c.querySelectorAll('button')];
                        return btns.find(function(b){
                            var i = b.querySelector('mat-icon');
                            return i && i.textContent.trim() === 'upload';
                        }) || btns.find(function(b){
                            var tip = b.getAttribute('mattooltip') || '', t = b.textContent || '';
                            return names.some(function(n){ return tip.includes(n) || t.includes(n); });
                        }) || null;
                    """, (get_i18n_texts().get('flowUploadMedia') or ['Upload media'],), timeout=3.0)
                    if not upload_btn:
                        raise RuntimeError(f'[{mode}] "Upload media" button not found (img {idx+1})')
                    # (2026-09-24) Flow giờ tạo `input[type=file]` TẠM (không gắn
                    # vào DOM) rồi gọi `.click()` mở hộp thoại hệ điều hành — tìm
                    # bằng `querySelector` không thấy. Móc `click()`/`showPicker()`
                    # của input file để GIỮ LẠI đúng ô đó (và không mở hộp thoại),
                    # kèm chặn hộp thoại qua CDP phòng khi móc không kịp.
                    self._js("""
                        if (!window.__fcHooked) {
                            window.__fcHooked = true;
                            var P = HTMLInputElement.prototype;
                            ['click', 'showPicker'].forEach(function(fn){
                                var orig = P[fn];
                                if (!orig) return;
                                P[fn] = function(){
                                    if (this.type === 'file' && window.__fcCapture) {
                                        window.__fcLast = this;
                                        window.__fcCapture = false;
                                        return;
                                    }
                                    return orig.apply(this, arguments);
                                };
                            });
                        }
                        window.__fcLast = null;
                        window.__fcCapture = true;
                    """)
                    try:
                        self._cdp('Page.setInterceptFileChooserDialog', {'enabled': True})
                    except Exception:
                        pass
                    try:
                        self._cdp_click_el(upload_btn)
                        self._sleep(0.5)
                        file_input = self._wait_js(
                            'return window.__fcLast || document.querySelector(\'input[type="file"]\') || null;',
                            timeout=5.0)
                    finally:
                        self._js('window.__fcCapture = false;')
                        try:
                            self._cdp('Page.setInterceptFileChooserDialog', {'enabled': False})
                        except Exception:
                            pass
                    if not file_input:
                        raise RuntimeError(f'[{mode}] file input not found (img {idx+1})')

                    ok = _inject_file_to_input(file_input, img['name'], img['data_url'])
                    if not ok:
                        self._log('warn', 'DOM upload: DataTransfer inject failed')

                    # Chờ item hết trạng thái "Uploading" (tối đa 40s — cùng độ
                    # kiên nhẫn với bản cũ, upload thật có thể mất hàng chục
                    # giây khi nhiều profile chạy đồng thời tranh chấp CPU).
                    uploaded_item = None
                    for _ in range(160):
                        uploaded_item = self._js("""
                            var name = arguments[0], busy = arguments[1];
                            var c = document.querySelector('.cdk-overlay-container');
                            if (!c) return null;
                            var items = [...c.querySelectorAll('button.asset-item[role="option"]')];
                            return items.find(function(b){
                                var t = b.textContent || '';
                                return !busy.some(function(w){ return t.includes(w); }) && t.includes(name);
                            }) || null;
                        """, search_name, get_i18n_texts().get('flowUploading') or ['Uploading'])
                        if uploaded_item:
                            break
                        self._sleep(0.25)
                    if not uploaded_item:
                        raise RuntimeError(
                            f'[{mode}] Upload timeout: "{img["name"]}" không xử lý xong sau 40s')
                    self._log('ok', f'DOM upload: "{img["name"]}" uploaded ✔')

                    self._cdp_click_el(uploaded_item)
                    self._sleep(1.0)

                    # ── B5: "Add to prompt" — CHỈ khi click item chưa tự đính ──
                    #
                    # ⚠️ FIX 2026-09-03 (bug thật, bắt được bằng
                    # `tests/_diag_ref_upload.py`): TRƯỚC ĐÂY bước này BẮT BUỘC
                    # tìm thấy nút "Add to prompt", không thấy thì raise. Nhưng
                    # quan sát trực tiếp cho thấy click vào item VỪA UPLOAD đã
                    # TỰ ĐÍNH KÈM luôn (giống hệt Case 1) và popover tự đóng —
                    # nút đó KHÔNG còn tồn tại để mà tìm. Hệ quả cũ: raise →
                    # vòng retry ngoài lặp lại TOÀN BỘ search/upload 3 lần →
                    # cuối cùng đính kèm TRÙNG 3 ẢNH rồi vẫn báo lỗi (đã đo:
                    # `chip: 3` trong khi chỉ yêu cầu 1). Ảnh hưởng THẬT tới
                    # production: mọi task imageToImage/imageToVideo/
                    # componentsToVideo (luôn có ref ảnh) đều dính.
                    #
                    # Giờ: kiểm tra chip TRƯỚC — đã đủ thì bỏ qua hẳn B5; chỉ
                    # khi CHƯA đủ mới tìm nút, và không thấy nút cũng KHÔNG
                    # raise nữa (để bước validate phía dưới phán quyết, nó vốn
                    # đã poll 20s và trả True/False cho vòng retry).
                    _press_add_to_prompt(idx, expected_count)

                # ── Validate: prompt có THẬT SỰ hiện đủ ảnh đính kèm chưa ──
                # (lịch sử, xem CLAUDE.md §5.3) — poll cho DOM kịp cập nhật,
                # KHÔNG chỉ tin click đã "chạy không lỗi". 20s (100×0.2s) —
                # đủ kiên nhẫn dưới điều kiện nhiều profile tranh chấp CPU.
                attached_count = 0
                for _ in range(100):
                    attached_count = _count_attached_refs()
                    if attached_count >= expected_count:
                        break
                    self._sleep(0.2)
                return attached_count >= expected_count

            for i, img in enumerate(tmp_files):
                self._log('info', f'DOM upload [{mode}] {i+1}/{len(tmp_files)}: {img["name"]}')
                # Baseline tính LẠI mỗi ảnh (không snapshot 1 lần đầu) — tự sửa nếu vì
                # lý do gì đó số ảnh đã đính kèm lệch so với dự đoán "i ảnh trước đó".
                expected_count = _count_attached_refs() + 1
                attached_ok = False
                for attempt in range(1, _MAX_ATTACH_ATTEMPTS + 1):
                    if attempt > 1:
                        self._log('warn', f'DOM upload: ảnh "{img["name"]}" chưa xác nhận đính '
                                           f'kèm vào prompt — thử lại toàn bộ search/upload '
                                           f'(lần {attempt}/{_MAX_ATTACH_ATTEMPTS})')
                    try:
                        attached_ok = _attach_one_reference(img, i, expected_count)
                    except RuntimeError as e:
                        if attempt >= _MAX_ATTACH_ATTEMPTS:
                            raise
                        self._log('warn', f'DOM upload: lỗi lúc thử lần {attempt}: {e}')
                        continue
                    if attached_ok:
                        break
                if not attached_ok:
                    raise RuntimeError(
                        f'[{mode}] Ảnh "{img["name"]}" không đính kèm được vào prompt sau '
                        f'{_MAX_ATTACH_ATTEMPTS} lần thử (prompt không hiện đủ '
                        f'{expected_count} ảnh đính kèm)')
                self._log('ok', f'DOM upload: image {i+1} attached ✔ (đã xác nhận '
                                 f'{expected_count} ảnh trong prompt)')

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════════════
    # Gemini Chat mode — worker_mode='gemini' (2026-07-04)
    # ══════════════════════════════════════════════════════════════════════════
    # Thay thế cho luồng extension_gemini (Chrome extension DOM automation qua content
    # script). Đã test thật nhiều lần với project 4 (video_breakdown, clip_001.mp4 ~23MB):
    # `element.click()` (script click, isTrusted=false) từ content script lên menu "+" /
    # mục "Tải tệp lên" của Gemini đôi khi "chạy không lỗi" nhưng KHÔNG thực sự tạo/kích
    # hoạt input[type=file] — verify trực tiếp bằng Chrome DevTools: script click lặp lại
    # nhiều lần trên cùng phần tử không tạo input mới, nhưng 1 click chuột THẬT (qua CDP
    # Input.dispatchMouseEvent, giống hệt cách labs.google.com automation trong file này
    # dùng ở _cdp_click_el) tạo input ngay trong vài giây. Với Selenium, việc set file lên
    # input[type=file] dùng WebElement.send_keys(local_path) — đây là cơ chế NATIVE của
    # WebDriver protocol (ChromeDriver xử lý qua DOM.setFileInputFiles nội bộ), hoàn toàn
    # không phụ thuộc "click có trusted hay không" → đáng tin cậy hơn nhiều so với cách cũ.

    GEMINI_URL  = 'https://gemini.google.com/app'
    CHATGPT_URL = 'https://chatgpt.com/'

    def _extract_response_images_base64(self, scope_js: str, min_width: int = 80) -> tuple:
        """Dùng CHUNG cho Gemini VÀ ChatGPT (2026-08-01) — chạy trong page context:
        `scope_js` là 1 biểu thức JS trả về element cha (vd container của response
        cuối cùng); tìm mọi `<img>` bên trong có `naturalWidth > min_width` (loại
        avatar/icon nhỏ) rồi lấy base64. Trả `([], False)` nếu không có ảnh hoặc lỗi
        (best-effort, không nên chặn việc lấy text response chỉ vì ảnh tải lỗi).

        Dedupe theo `src` TRƯỚC khi xử lý (2026-08-01, bug thật bắt được lúc test
        ChatGPT thật) — 1 ảnh generate ra có thể xuất hiện dưới NHIỀU thẻ `<img>`
        cùng trỏ 1 `src` (vd 1 để hiển thị + 1-2 bản ẩn cho lightbox/responsive
        srcset khác kích thước cùng URL) — verify trực tiếp: response 1 ảnh thật
        nhưng không dedupe trả về 3 bản base64 giống hệt nhau (mỗi bản ~3.6MB,
        lãng phí băng thông callback gấp 3 lần vô ích).

        Chiến lược lấy base64 (2026-08-01, bug thật bắt được lúc test Gemini
        thật) — ƯU TIÊN vẽ ảnh lên `<canvas>` rồi `canvas.toDataURL()` thay vì
        `fetch(img.src)`: ảnh Gemini generate ra dùng `src` dạng `blob:...`
        (Blob URL tạo bởi chính trang, KHÔNG phải `https://...`) — verify trực
        tiếp: `fetch()` trên URL này báo `TypeError: Failed to fetch` dù ảnh
        hiển thị BÌNH THƯỜNG trên trang (browser đã decode xong, chỉ fetch()
        KHÔNG đọc lại được nội dung blob trong ngữ cảnh CDP execute_script này).
        Canvas đọc trực tiếp PIXEL đã decode sẵn của `<img>` — không phụ thuộc
        `src` là scheme gì (`blob:`/`https:`/`data:`), không có bước fetch mạng
        nào cả nên tránh hẳn lớp lỗi này. Chỉ fallback về `fetch()` (dùng cho
        ChatGPT — ảnh ở đó LÀ `https://chatgpt.com/...`, cùng-origin, đã proven
        hoạt động) nếu canvas thất bại (vd lỗi "tainted canvas" hiếm gặp với
        ảnh cross-origin không bật CORS).

        Fix bug thật (2026-08-03, storyboard #34 frame 2/11 — ChatGPT lịch sử
        CÓ ảnh thật, DB lại lưu 1 ảnh TRẮNG TOÀN BỘ, 2 frame khác nhau ra CÙNG 1
        file y hệt byte-for-byte): `<img>` container hiện placeholder rỗng (cùng
        kích thước cuối cùng, để giữ layout) TRONG LÚC ChatGPT còn generate ảnh —
        `naturalWidth` của placeholder này đã > `min_width` (đủ điều kiện filter)
        NHƯNG canvas vẽ ra chỉ toàn 1 màu (trắng) vì chưa có nội dung thật. Vì
        placeholder trắng luôn cho CÙNG 1 kết quả pixel bất kể lúc nào chụp, kết
        quả base64 giữa các lần bị race khác nhau lại giống hệt nhau — đúng triệu
        chứng quan sát được. Fix: `_isBlankCanvas()` lấy mẫu 9 điểm pixel (4 góc +
        4 điểm giữa cạnh + tâm) qua `ctx.getImageData()` — nếu TẤT CẢ gần như cùng
        1 màu (lệch kênh màu ≤4) thì coi là placeholder CHƯA XONG, loại khỏi kết
        quả (KHÔNG lưu ảnh trắng) và báo `pending=True` cho caller biết "còn ảnh
        đang render dở, ĐỪNG chốt response vội" — `_gemini_response_if_ready()`/
        `_chatgpt_response_if_ready()` trả `None` (tiếp tục poll) thay vì trả về
        kết quả thiếu ảnh/có ảnh trắng. Không áp dụng cho nhánh fallback `fetch()`
        (không vẽ canvas nên không đọc được pixel — nhánh đó vốn chỉ dùng cho ảnh
        cross-origin đã tải xong hẳn, rủi ro placeholder thấp hơn nhiều)."""
        script = f"""
            var done = arguments[arguments.length - 1];
            (async function() {{
                try {{
                    var scope = (function() {{ {scope_js} }})();
                    if (!scope) {{ done({{images: [], pending: false}}); return; }}
                    var seen = {{}};
                    var imgs = Array.from(scope.querySelectorAll('img')).filter(function(i) {{
                        if (i.naturalWidth <= {min_width}) return false;
                        if (seen[i.src]) return false;
                        seen[i.src] = true;
                        return true;
                    }});
                    function isBlankCanvas(ctx, w, h) {{
                        try {{
                            var pts = [[0,0],[w-1,0],[0,h-1],[w-1,h-1],
                                       [Math.floor(w/2),0],[Math.floor(w/2),h-1],
                                       [0,Math.floor(h/2)],[w-1,Math.floor(h/2)],
                                       [Math.floor(w/2),Math.floor(h/2)]];
                            var base = null;
                            for (var p = 0; p < pts.length; p++) {{
                                var d = ctx.getImageData(pts[p][0], pts[p][1], 1, 1).data;
                                if (base === null) {{ base = d; continue; }}
                                if (Math.abs(d[0]-base[0]) > 4 || Math.abs(d[1]-base[1]) > 4 ||
                                    Math.abs(d[2]-base[2]) > 4 || Math.abs(d[3]-base[3]) > 4) return false;
                            }}
                            return true;
                        }} catch (e) {{ return false; }}  // lỗi đọc pixel (tainted canvas) -> đừng chặn nhầm
                    }}
                    var out = [];
                    var pending = false;
                    for (var i = 0; i < imgs.length; i++) {{
                        var dataUri = null;
                        // Cách 1 (ưu tiên) — canvas, đọc pixel đã decode, không cần fetch mạng
                        try {{
                            var canvas = document.createElement('canvas');
                            canvas.width = imgs[i].naturalWidth;
                            canvas.height = imgs[i].naturalHeight;
                            var ctx = canvas.getContext('2d');
                            ctx.drawImage(imgs[i], 0, 0);
                            if (isBlankCanvas(ctx, canvas.width, canvas.height)) {{
                                pending = true;
                            }} else {{
                                dataUri = canvas.toDataURL('image/png');
                            }}
                        }} catch (e) {{}}
                        // Cách 2 (fallback) — fetch() cho URL http(s) cùng-origin thật
                        if (!dataUri && !pending) {{
                            try {{
                                var resp = await fetch(imgs[i].src, {{credentials: 'include'}});
                                var blob = await resp.blob();
                                dataUri = await new Promise(function(resolve, reject) {{
                                    var reader = new FileReader();
                                    reader.onload = function() {{ resolve(reader.result); }};
                                    reader.onerror = reject;
                                    reader.readAsDataURL(blob);
                                }});
                            }} catch (e) {{}}
                        }}
                        if (dataUri) out.push(dataUri);
                    }}
                    done({{images: out, pending: pending}});
                }} catch (e) {{ done({{images: [], pending: false}}); }}
            }})();
        """
        try:
            result = self._js_async(script, timeout=30) or {}
            return (result.get('images') or [], bool(result.get('pending')))
        except Exception as e:
            self._log('warn', f'Không lấy được ảnh phản hồi: {e}')
            return ([], False)

    def _gemini_find(self, selector: str):
        return self._js("return document.querySelector(arguments[0]);", selector)

    def _gemini_select_model(self, model_label: str):
        """Mở dropdown chọn model Gemini (nút cạnh ô nhập liệu) và chọn đúng model
        theo TÊN HIỂN THỊ ĐẦY ĐỦ trong menu (vd '3.1 Pro', '3.5 Flash', '3.1
        Flash-Lite') — khớp theo text `.label` bên trong mỗi `gem-menu-item` thay vì
        `data-mode-id` (hash nội bộ Google, có thể đổi giữa các bản build). Rỗng/None
        = giữ nguyên model đang chọn sẵn, bỏ qua hoàn toàn không mở menu (2026-07-20,
        theo project.gemini_model — xem CHANGELOG). Best-effort: bất kỳ bước nào
        không tìm thấy nút/menu/option khớp chỉ log warning rồi để task tiếp tục
        chạy với model hiện tại — đổi model thất bại không nên chặn cả task.

        LUÔN mở menu trước rồi mới so sánh (KHÔNG dựa vào text của nút trigger để
        quyết định có cần mở hay không) — nút trigger chỉ hiện tên RÚT GỌN (vd
        "Flash") trong khi menu item ghi ĐẦY ĐỦ (vd "3.5 Flash"/"3.1 Flash-Lite"
        đều chứa "Flash" → so trực tiếp với text rút gọn dễ khớp nhầm/không khớp).
        Model ĐANG chọn xác định qua class `selected` trên `gem-menu-item` (KHÔNG
        phải `data-active` — theo đúng DOM thật user cung cấp, `data-active="true"`
        chỉ đánh dấu item đang FOCUS/highlight trong menu, item số 1 trong danh sách
        luôn mang thuộc tính này bất kể model nào đang thật sự được chọn; item THẬT
        SỰ đang chọn mang class `selected` + chứa icon check `aria-label="Đã chọn"`)."""
        if not model_label or not model_label.strip():
            return
        wanted = model_label.strip()

        trigger = self._gemini_find('button[data-test-id="bard-mode-menu-button"]')
        if not trigger:
            self._log('warn', '[gemini] Không tìm thấy nút chọn model — bỏ qua, giữ model hiện tại')
            return

        self._cdp_click_el(trigger)
        self._sleep(0.4)

        menu_ready = self._wait_js(
            'return !!document.querySelector(\'gem-menu[data-test-id="gem-mode-menu"]\');',
            timeout=5,
        )
        if not menu_ready:
            self._log('warn', '[gemini] Menu chọn model không mở ra — bỏ qua, giữ model hiện tại')
            return

        current = self._js("""
            var items = document.querySelectorAll('gem-menu[data-test-id="gem-mode-menu"] gem-menu-item[data-mode-id]');
            for (var i = 0; i < items.length; i++) {
                if (items[i].classList.contains('selected')) {
                    var label = items[i].querySelector('.label');
                    return label ? label.innerText.trim() : '';
                }
            }
            return '';
        """) or ''

        if current.strip().lower() == wanted.lower():
            self._log('info', f'[gemini] Model đã đúng "{wanted}" sẵn — đóng menu, không đổi')
            self._cdp_click_el(trigger)  # đóng lại menu vừa mở, không click item nào
            return

        target = self._wait_js("""
            var wanted = arguments[0].trim().toLowerCase();
            var items = document.querySelectorAll('gem-menu[data-test-id="gem-mode-menu"] gem-menu-item[data-mode-id]');
            for (var i = 0; i < items.length; i++) {
                var label = items[i].querySelector('.label');
                if (label && label.innerText.trim().toLowerCase() === wanted) return items[i];
            }
            return null;
        """, args=(wanted,), timeout=3)

        if not target:
            self._log('warn', f'[gemini] Không tìm thấy model "{wanted}" trong menu (model hiện tại: "{current}") — đóng menu, giữ nguyên')
            self._cdp_click_el(trigger)
            return

        self._cdp_click_el(target)
        self._sleep(0.5)
        self._log('ok', f'[gemini] Đã đổi model "{current}" → "{wanted}"')

    def _gemini_open_composer_menu(self):
        """Mở menu '+' cạnh ô nhập liệu Gemini (nếu chưa mở sẵn)."""
        if self._gemini_find('[data-test-id="local-images-files-uploader-button"]'):
            return  # menu đã mở sẵn (vd từ 1 attempt trước) — không click lại (menu toggle)
        menu_btn = self._js("""
            var field = document.querySelector('.text-input-field') || document;
            return field.querySelector('button[aria-haspopup="menu"]');
        """)
        if not menu_btn:
            raise RuntimeError('Không tìm thấy nút mở menu đính kèm (+) trên Gemini')
        self._cdp_click_el(menu_btn)
        self._sleep(0.5)

    def _gemini_attach_file_kickoff(self, local_path: str) -> int:
        """Phần "châm ngòi" của đính kèm file — mở menu, click "Tải tệp lên",
        send_keys(local_path) vào input[type=file]. KHÔNG chờ xác nhận (đó là việc
        của `_gemini_attach_file_poll`) — tách riêng để round-robin nhiều tab
        (`_run_gemini_loop_concurrent`) gọi 1 lần rồi tự poll không-block giữa các
        tab khác, thay vì đứng chờ nguyên khối như `_gemini_attach_file` (vẫn giữ
        nguyên cho luồng 1-tab tuần tự, xem hàm đó bên dưới).
        Trả về `before_count` (số tile đính kèm hiện có TRƯỚC khi gọi), cần truyền
        lại cho `_gemini_attach_file_poll`."""
        from selenium.webdriver.common.by import By

        # Click thật (CDP) vào mục "Tải tệp lên" khiến Angular tự gọi .click() lên
        # input[type=file] ẩn phía sau → trình duyệt mở cửa sổ OS "Open File" thật (đã
        # thấy trực tiếp 2026-07-05: cửa sổ Explorer "Open" nổi lên, không tự đóng, gây
        # rối/chiếm focus dù send_keys() sau đó vẫn set file thành công qua CDP). Bật
        # Page.setInterceptFileChooserDialog TRƯỚC khi click để Chrome chặn không hiện
        # dialog OS đó ra ngoài màn hình — send_keys() bên dưới vẫn hoạt động y hệt vì nó
        # set file qua CDP DOM.setFileInputFiles nội bộ, không phụ thuộc dialog có hiện hay không.
        try:
            self._cdp('Page.setInterceptFileChooserDialog', {'enabled': True})
        except Exception as e:
            self._log('warn', f'Không bật được setInterceptFileChooserDialog: {e}')

        before_count = self._js(
            "return document.querySelectorAll('gem-media-attachment.gem-attachment-tile').length;"
        ) or 0

        # Thử tối đa 3 lần mở menu (menu button đôi khi cần thêm thời gian hydrate ngay
        # sau khi navigate trang mới — giữ nguyên bài học từ lần sửa content.js trước).
        upload_item = None
        for attempt in range(3):
            self._gemini_open_composer_menu()
            deadline = time.time() + 6
            while time.time() < deadline:
                upload_item = self._gemini_find('[data-test-id="local-images-files-uploader-button"]')
                if upload_item:
                    break
                self._sleep(0.3)
            if upload_item:
                break
            self._sleep(0.5)
        if not upload_item:
            raise RuntimeError('Không tìm thấy mục "Tải tệp lên" trong menu đính kèm Gemini')
        self._cdp_click_el(upload_item)

        # Chờ input[type=file] xuất hiện, lấy qua Selenium find_element (cần WebElement
        # thật — không phải JS handle — để gọi được send_keys()).
        file_input = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                file_input = self.driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
                break
            except Exception:
                self._sleep(0.3)
        if not file_input:
            raise RuntimeError('Không tìm thấy input đính kèm file trên Gemini')

        file_input.send_keys(local_path)
        self._log('info', f'send_keys() đã set file: {os.path.basename(local_path)}')
        return before_count

    def _gemini_attach_file_poll(self, before_count: int) -> bool:
        """1 lần kiểm tra KHÔNG-BLOCKING xem file đã đính kèm xong chưa (tile xuất
        hiện trong composer). Gọi lặp lại bởi caller (round-robin hoặc
        `_gemini_attach_file` bên dưới)."""
        count = self._js(
            "return document.querySelectorAll('gem-media-attachment.gem-attachment-tile').length;"
        ) or 0
        return count > before_count

    def _gemini_attach_file(self, local_path: str, timeout: int = 45):
        """Đính kèm 1 file local vào composer Gemini bằng send_keys (native WebDriver) —
        không dùng DataTransfer/click-based approach của extension nữa. Dùng cho luồng
        1-tab tuần tự (`_run_task_gemini`) — wrapper mỏng quanh kickoff+poll ở trên,
        tự lặp lại poll tới khi xong hoặc hết `timeout`."""
        before_count = self._gemini_attach_file_kickoff(local_path)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._gemini_attach_file_poll(before_count):
                self._log('ok', 'File đính kèm xác nhận thành công (tile xuất hiện trong composer)')
                return
            self._sleep(1)
        raise RuntimeError(f'Timeout {timeout}s: không xác nhận được file đã đính kèm vào Gemini')

    def _gemini_type_prompt(self, prompt: str):
        """Gõ prompt vào ô nhập liệu Gemini (contenteditable) — KHÔNG bấm Gửi (đó là
        việc của `_gemini_submit_if_ready`, tách riêng cho round-robin nhiều tab).

        (2026-08-17, fix bug thật user báo "profile gemini chat + upload khi
        nhận task bị lỗi chỉ mở profile không vào gemini để chạy task") —
        selector CŨ tìm ô nhập liệu qua `wrapper.querySelector('[contenteditable=
        "true"]') || wrapper.querySelector('p')` — SAI vì `.new-input-ui`
        (Quill editor của Gemini) TỰ NÓ mang `contenteditable="true"` ngay trên
        chính nó (`<div class="ql-editor ... new-input-ui" contenteditable=
        "true">`), KHÔNG PHẢI 1 ancestor bọc quanh 1 phần tử con khác mới có
        thuộc tính này — `querySelector` chỉ tìm CON CHÁU, không match chính
        phần tử gọi nó, nên nhánh đầu LUÔN trả `null` rồi rơi xuống `<p>` (thẻ
        con hiển thị placeholder, KHÔNG tự focus được — `focus()` không đổi
        `document.activeElement`). Click+gõ vào `<p>` không thực sự vào ô nhập
        liệu thật → ô thật vẫn RỖNG → nút Gửi không bao giờ hết disabled → task
        kẹt/timeout ở bước Gửi phía sau với thông báo khó hiểu, đúng cảm giác
        "mở profile xong không thấy vào gemini chạy gì cả". Nguyên nhân giống
        HỆT bug đã tìm ra + fix ở `extensions/content.js::_findInput()` (Chrome
        Extension, cùng DOM Gemini nhưng codebase HOÀN TOÀN riêng — xem ToolSub
        gốc CLAUDE.md mục "ROOT CAUSE THẬT SỰ ĐÃ XÁC NHẬN (v4)") — fix đó CHƯA
        BAO GIỜ được áp dụng sang bản Selenium độc lập ở đây. Fix: kiểm tra
        CHÍNH `wrapper` có `contenteditable="true"` TRƯỚC (ưu tiên trả về chính
        nó), chỉ tìm con cháu nếu wrapper không tự editable (phòng DOM đổi khác
        về sau). Thêm bước verify SAU KHI gõ — nếu độ dài thật trong ô vẫn là 0
        (điển hình của việc gõ trúng phần tử sai) thì raise NGAY, LỘ RÕ lỗi ở
        đúng bước này thay vì để task âm thầm timeout mơ hồ ở bước chờ nút Gửi."""
        textbox = None
        deadline = time.time() + 10
        while time.time() < deadline:
            textbox = self._js("""
                var wrapper = document.querySelector('.new-input-ui');
                if (!wrapper) return null;
                if (wrapper.getAttribute('contenteditable') === 'true') return wrapper;
                return wrapper.querySelector('[contenteditable="true"]') || wrapper.querySelector('p');
            """)
            if textbox:
                break
            self._sleep(0.3)
        if not textbox:
            raise RuntimeError('Không tìm thấy ô nhập liệu Gemini')

        self._cdp_click_el(textbox)
        self._sleep(0.3)

        head, rest = prompt[:20], prompt[20:]
        for ch in head:
            self._cdp('Input.insertText', {'text': ch})
            self._sleep(0.03 + random.random() * 0.05)
        if rest:
            self._cdp('Input.insertText', {'text': rest})
        self._sleep(0.5)

        typed_len = self._js("""
            var wrapper = document.querySelector('.new-input-ui');
            return wrapper ? (wrapper.innerText || wrapper.textContent || '').length : 0;
        """) or 0
        if typed_len == 0 and prompt.strip():
            raise RuntimeError('Gõ prompt thất bại: ô nhập liệu Gemini vẫn rỗng sau khi gõ '
                                '— có thể đã click nhầm phần tử (DOM Gemini có thể đã đổi)')

    def _gemini_submit_if_ready(self) -> bool:
        """1 lần kiểm tra KHÔNG-BLOCKING xem nút Gửi đã sẵn sàng chưa — container có
        class 'visible' VÀ nút con không `aria-disabled='true'` (đúng cấu trúc DOM
        thật của Gemini — xem CHANGELOG 2026-07-05). Khi có đính kèm (video/ảnh), nút
        chỉ thật sự sẵn sàng SAU KHI Gemini nhận đủ file — click sớm hơn có thể click
        trúng nút đang ẩn/disabled và không có tác dụng. Click NGAY nếu sẵn sàng, trả
        True; ngược lại trả False (caller tự lặp lại poll)."""
        send_btn = self._js("""
            var c = document.querySelector('[data-test-id="send-button-container"]');
            if (!c || !c.classList.contains('visible')) return null;
            var host = c.querySelector('gem-icon-button');
            if (host && host.getAttribute('aria-disabled') === 'true') return null;
            return c.querySelector('button[aria-label="Send message"]') || c.querySelector('button');
        """)
        if not send_btn:
            return False
        self._cdp_click_el(send_btn)
        return True

    def _gemini_fill_and_submit(self, prompt: str, send_ready_timeout: int = 180):
        """Gõ prompt rồi bấm nút Gửi — dùng cho luồng 1-tab tuần tự
        (`_run_task_gemini`). Wrapper mỏng quanh `_gemini_type_prompt`+
        `_gemini_submit_if_ready`, tự lặp lại poll tới khi xong hoặc hết
        `send_ready_timeout`."""
        self._gemini_type_prompt(prompt)
        deadline = time.time() + send_ready_timeout
        while time.time() < deadline:
            if self._gemini_submit_if_ready():
                self._log('info', f'Gemini prompt submitted ({len(prompt)} chars)')
                return
            self._sleep(0.5)
        raise RuntimeError(
            f'Timeout {send_ready_timeout}s: nút Gửi không sẵn sàng '
            f'(có thể Gemini chưa nhận xong file đính kèm)'
        )


    # ── GỬI ẨN qua RPC StreamGenerate (API mode cho Gemini chat, 2026-09-06) ──
    #
    # Theo yêu cầu user: *"api chỉ cần gửi nội dung còn lắng nghe đọc kết quả
    # giống DOM"* — nhóm hàm này CHỈ THAY BƯỚC GỬI (không gõ, không click);
    # việc đọc kết quả vẫn để `_gemini_response_if_ready()`/DOM lo như cũ.
    #
    # Vì sao đáng làm: gõ prompt dài (~29K ký tự, thường gặp ở scene_batch/
    # full_script) vào ProseMirror/Quill từng là nguồn bug dai dẳng nhất của
    # luồng Gemini (xem root CLAUDE.md §11.5 — `insertText` không áp hết,
    # nút Gửi kẹt `disabled`, phải retry 3 lần, có lúc gõ trúng thẻ `<p>` không
    # focus được). Bắn thẳng RPC thì prompt chỉ là 1 chuỗi trong body — dài bao
    # nhiêu cũng không đổi hành vi.
    #
    # ĐÃ VERIFY TRÊN TÀI KHOẢN THẬT (2026-09-06, xem CHANGELOG):
    #   • KHÔNG cần token BotGuard (`inner[3]=null` vẫn HTTP 200 + trả lời đúng).
    #   • KHÔNG cần harvest bằng cách chờ trang tự bắn request như `flow_be`:
    #     `at`/`bl`/`f.sid` đọc THẲNG từ `window.WIZ_global_data` (không refresh).
    #   • Sau khi bắn, mở `/app/<slug>` (BỎ tiền tố `c_`) thì Angular render đủ
    #     `structured-content-container`/`.markdown` để DOM reader đọc bình thường.
    #
    # Kiến trúc kickoff+poll (KHÔNG phải 1 lệnh blocking): `fetch` StreamGenerate
    # chỉ resolve khi Gemini sinh XONG (có thể vài chục giây) — dùng
    # `execute_async_script` sẽ ĐÓNG BĂNG cả vòng round-robin nhiều tab
    # (§11.24). Nên kickoff ghi kết quả vào `window.__gmRpc[key]` rồi poll —
    # cùng pattern `_gemini_attach_file_kickoff`/`_gemini_attach_file_poll`.

    _GEMINI_RPC_STATE_VAR = '__gmRpc'

    def _gemini_rpc_session(self) -> dict | None:
        """Đọc `at`/`bl`/`f.sid` từ `window.WIZ_global_data` của TAB HIỆN TẠI.

        Token gắn với 1 lần load trang — mỗi tab round-robin có bộ riêng, nên
        LUÔN đọc lại tại tab đang active thay vì cache ở cấp worker."""
        try:
            s = self._js("""
                var g = window.WIZ_global_data || {};
                return {at: g.SNlM0e || null, bl: g.cfb2h || null, sid: g.FdrFJe || null};
            """) or {}
        except Exception as exc:
            self._log('warn', f'[gemini-rpc] Không đọc được WIZ_global_data: {exc}')
            return None
        if not (s.get('at') and s.get('bl') and s.get('sid')):
            self._log('warn', '[gemini-rpc] Thiếu at/bl/f.sid — trang chưa dựng xong '
                              'hoặc chưa đăng nhập')
            return None
        return s

    def _gemini_rpc_kickoff(self, prompt: str, key: str = 'main',
                            conv: tuple | None = None) -> bool:
        """Bắn StreamGenerate và trả về NGAY (không chờ Gemini sinh xong).

        Kết quả ghi vào `window.__gmRpc[key]`; đọc bằng `_gemini_rpc_poll(key)`.
        `key` phân biệt các tab round-robin (mỗi tab 1 `window` riêng nên thực
        tế không đụng nhau, nhưng giữ key cho rõ ràng khi debug)."""
        sess = self._gemini_rpc_session()
        if not sess:
            return False
        f_req = gemini_be.build_f_req(gemini_be.STREAM_GENERATE_TEMPLATE, prompt,
                                      blob=gemini_be.DEFAULT_BOTGUARD_TOKEN, conv=conv)
        try:
            self._js("""
                var key = arguments[0], fReq = arguments[1], at = arguments[2];
                var bl = arguments[3], sid = arguments[4], path = arguments[5];
                var VAR = arguments[6];
                window[VAR] = window[VAR] || {};
                window[VAR][key] = {done: false, status: null, body: null, error: null};
                var qs = new URLSearchParams({
                    bl: bl, 'f.sid': sid, hl: 'en',
                    _reqid: String(Math.floor(Math.random() * 900000) + 100000), rt: 'c'
                });
                fetch(path + '?' + qs.toString(), {
                    method: 'POST', credentials: 'include',
                    headers: {'content-type': 'application/x-www-form-urlencoded;charset=utf-8',
                              'x-same-domain': '1'},
                    body: 'f.req=' + encodeURIComponent(fReq) + '&at=' + encodeURIComponent(at) + '&'
                }).then(function(r){
                    return r.text().then(function(t){
                        window[VAR][key] = {done: true, status: r.status, body: t, error: null};
                    });
                }).catch(function(e){
                    window[VAR][key] = {done: true, status: -1, body: null, error: String(e)};
                });
                return true;
            """, key, f_req, sess['at'], sess['bl'], sess['sid'],
                 gemini_be.STREAM_GENERATE_PATH, self._GEMINI_RPC_STATE_VAR)
        except Exception as exc:
            self._log('warn', f'[gemini-rpc] Không bắn được request: {exc}')
            return False
        self._log('info', f'[gemini-rpc] ⇢ Đã gửi ẩn prompt ({len(prompt)} ký tự), '
                          f'không gõ DOM')
        return True

    def _gemini_rpc_poll(self, key: str = 'main') -> dict | None:
        """1 lần kiểm tra KHÔNG-BLOCKING. Trả `None` nếu còn đang chạy;
        `{'conv': (c,r,rc), 'text': ...}` khi xong; raise nếu Gemini từ chối."""
        try:
            st = self._js("""
                var VAR = arguments[0], key = arguments[1];
                var s = (window[VAR] || {})[key];
                if (!s || !s.done) return null;
                return {status: s.status, body: s.body, error: s.error};
            """, self._GEMINI_RPC_STATE_VAR, key)
        except Exception as exc:
            raise RuntimeError(f'[gemini-rpc] Mất kết nối lúc đọc kết quả: {exc}')
        if not st:
            return None
        if st.get('error') or st.get('status') != 200:
            raise RuntimeError(f"[gemini-rpc] Gửi thất bại (HTTP {st.get('status')}): "
                               f"{st.get('error') or (st.get('body') or '')[:200]}")
        parsed = gemini_be.parse_stream_generate(st.get('body') or '')
        if not parsed['conv'][0]:
            raise RuntimeError('[gemini-rpc] Response không có conversation id — '
                               'shape f.req có thể đã đổi, chạy lại '
                               'tests/_gemini_rpc_capture.py để lấy template mới')
        return parsed

    def _gemini_rpc_open_conversation(self, conv_id: str, settle_secs: float = 5.0):
        """Mở hội thoại vừa tạo để Angular render — bước BẮT BUỘC trước khi đọc
        DOM, vì `fetch()` không đi qua state của app (app không tự hiển thị
        câu trả lời mà mình bắn ngoài luồng của nó).

        Dùng `location.href` (điều hướng TRONG trang) thay vì `driver.get()` —
        verify thật cho thấy cả 2 đều render đúng, nhưng `location.href` giữ
        nguyên window handle, an toàn hơn cho vòng round-robin nhiều tab."""
        url = gemini_be.conversation_url(conv_id, base=self.GEMINI_URL)
        self._nav_js_href(url, 'Gemini RPC: mở hội thoại vừa gửi ẩn để đọc câu trả lời')
        self._sleep(settle_secs)

    def _gemini_send_via_rpc(self, prompt: str, timeout: int = 300,
                             key: str = 'main') -> bool:
        """Wrapper BLOCKING cho luồng 1-tab tuần tự: gửi ẩn → chờ RPC xong →
        mở hội thoại. Trả `False` nếu KHÔNG dùng được đường này (caller tự rơi
        về `_gemini_fill_and_submit()` — không bao giờ làm hỏng task)."""
        if not self._gemini_rpc_kickoff(prompt, key=key):
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                res = self._gemini_rpc_poll(key=key)
            except RuntimeError as exc:
                self._log('warn', f'{exc} — rơi về gõ DOM')
                return False
            if res:
                self._log('ok', f"[gemini-rpc] ✔ Gemini đã trả lời "
                                f"(conv={res['conv'][0]}), mở hội thoại để đọc bằng DOM")
                self._gemini_rpc_open_conversation(res['conv'][0])
                return True
            self._sleep(1)
        self._log('warn', f'[gemini-rpc] Quá {timeout}s chưa thấy phản hồi — rơi về gõ DOM')
        return False

    def _gemini_rpc_enabled(self, has_attachments: bool) -> bool:
        """Bật/tắt đường gửi ẩn.

        ⚠️ CÓ FILE ĐÍNH KÈM thì LUÔN dùng DOM: bước upload của Gemini đi qua
        endpoint resumable riêng (`push.clients6.google.com/upload/…`) mà
        `tests/_gemini_rpc_capture.py` CHƯA capture shape — đoán bừa là cách
        nhanh nhất để hỏng. `_gemini_attach_file()` (DOM, `send_keys` native)
        đã proven cho phần này, giữ nguyên."""
        if has_attachments:
            return False
        return bool(int(self._server_settings.get('gemini_send_via_rpc', 1) or 0))

    # (2026-09-01) BUG THẬT user báo: "response chat trả về đầy đủ mà lại bắt
    # kết quả trước đó rồi báo JSON không hợp lệ" — bản CŨ chốt text/ảnh ngay
    # ở ĐÚNG 1 lần poll khi `is_generating` vừa về `false`, không xác nhận gì
    # thêm. Có ít nhất 2 nguồn RACE khớp đúng triệu chứng "bắt kết quả TRƯỚC
    # ĐÓ" (không phải "trả lời thiếu", mà là 1 mẩu nội dung CŨ HƠN bản cuối):
    #   (a) Model "thinking" (Gemini 3.x Flash/Pro đang dùng thực tế trong
    #       CLAUDE.md, đều có chế độ suy luận) hiện 1 khối "Đang suy nghĩ"
    #       RIÊNG trước khi khối trả lời thật xuất hiện — nếu `is_generating`
    #       (chỉ theo dõi nút Stop/loading-indicator TỔNG QUÁT) về `false`
    #       đúng vào khe hở giữa 2 khối, `containers[last]` khi đó trỏ vào
    #       khối "đang suy nghĩ" — nội dung CŨ HƠN, gần như chắc chắn không
    #       phải JSON hợp lệ.
    #   (b) Angular re-render `.markdown` không cùng nhịp với việc gỡ nút
    #       Stop — đọc `.innerText` ngay khung hình đó có thể là bản DOM CHƯA
    #       COMMIT xong đoạn cuối.
    # Fix: `stability_state` (dict do CALLER sở hữu, sống xuyên suốt các lần
    # poll của ĐÚNG 1 tác vụ) — chỉ trả kết quả khi CÙNG 1 nội dung (text, số
    # ảnh) được thấy ỔN ĐỊNH liên tục ≥ `_GEMINI_RESPONSE_STABLE_SECS`, tính
    # theo THỜI GIAN THỰC (không phải số lần poll — 2 caller có nhịp poll
    # khác nhau: ~1s cho luồng 1-tab, phụ thuộc số slot cho round-robin).
    # Nội dung ĐỔI giữa 2 lần đọc (dù `is_generating` đã false) → coi là CHƯA
    # ổn định, reset đồng hồ, tiếp tục chờ — bắt đúng CẢ 2 dạng race ở trên:
    # đổi nội dung (khối thinking bị thay bằng khối trả lời thật) VÀ đổi độ
    # dài (DOM đang commit dở).
    _GEMINI_RESPONSE_STABLE_SECS = 1.2

    def _gemini_response_if_ready(self, stability_state: dict | None = None) -> dict | None:
        """1 lần kiểm tra KHÔNG-BLOCKING xem Gemini đã phản hồi xong chưa. KHÔNG dùng
        '.send-button-container.visible' — container này luôn có class 'visible' kể
        cả lúc rảnh (có nút mic thường trực, class 'persistent-mic'), nên is_generating
        luôn = true nếu dựa vào đó. Tín hiệu đúng: nút bên trong đổi thành "Stop
        response" — biến mất nghĩa là đã phản hồi xong. Trả `{'text','images'}` nếu
        đã xong (2026-08-01: thêm `images` — Gemini có thể trả ảnh tạo ra, vd Nano
        Banana image-gen ngay trong chat — xem `_extract_response_images_base64()`),
        None nếu còn đang generate hoặc chưa có gì đáng kể (không text lẫn ảnh).

        `stability_state` — TUỲ CHỌN nhưng NÊN LUÔN truyền (xem giải thích ở
        `_GEMINI_RESPONSE_STABLE_SECS` phía trên) — 1 dict trống do caller tự
        giữ xuyên suốt các lần gọi CHO ĐÚNG 1 tác vụ (KHÔNG dùng chung giữa 2
        tác vụ khác nhau/2 slot khác nhau — mỗi tác vụ 1 dict riêng). Không
        truyền (`None`) = giữ hành vi CŨ (chốt ngay lần đầu is_generating=false
        có nội dung), chỉ để tương thích ngược cho code gọi cũ nếu còn sót."""
        is_generating = self._js("""
            if (document.querySelector('button[aria-label="Stop response"]')) return true;
            if (document.querySelector('button[aria-label*="Stop"]')) return true;
            if (document.querySelector('gem-icon-button.stop')) return true;
            if (document.querySelector('model-response loading-indicator')) return true;
            return false;
        """)
        if is_generating:
            if stability_state is not None:
                stability_state.clear()  # còn đang sinh → bỏ mọi ứng viên cũ, không tính dở dang
            return None
        text = self._js("""
            var containers = document.querySelectorAll('structured-content-container');
            if (!containers.length) return null;
            var last = containers[containers.length - 1];
            var md = last.querySelector('.markdown');
            return md ? md.innerText.trim() : null;
        """) or ''
        images, images_pending = self._extract_response_images_base64("""
            var containers = document.querySelectorAll('structured-content-container');
            return containers.length ? containers[containers.length - 1] : null;
        """)
        # (2026-08-03) Ảnh còn đang render (placeholder trắng, xem docstring
        # _extract_response_images_base64()) — ĐỪNG chốt response vội, tiếp tục
        # poll ở vòng sau thay vì trả về text-only/ảnh trắng.
        if images_pending:
            if stability_state is not None:
                stability_state.clear()
            return None
        if not ((text and len(text) > 5) or images):
            if stability_state is not None:
                stability_state.clear()
            return None

        result = {'text': text, 'images': images}
        if stability_state is None:
            return result  # tương thích ngược — không xác nhận ổn định

        key = (text, len(images))
        now = time.time()
        if stability_state.get('key') != key:
            stability_state['key']   = key
            stability_state['since'] = now
            return None  # nội dung MỚI thấy lần đầu (hoặc vừa đổi) — chưa tin, chờ vòng sau
        if now - stability_state.get('since', now) < self._GEMINI_RESPONSE_STABLE_SECS:
            return None  # đã thấy nhưng chưa đủ lâu — có thể vẫn đang commit dở
        return result  # cùng nội dung, đủ ổn định — tin được

    def _gemini_wait_response(self, timeout: int = 300) -> dict:
        """Chờ Gemini generate xong, trả về `{'text','images'}` phản hồi cuối cùng —
        dùng cho luồng 1-tab tuần tự (`_run_task_gemini`). Wrapper mỏng quanh
        `_gemini_response_if_ready`, tự lặp lại poll tới khi xong hoặc hết `timeout`."""
        self._sleep(1.5)
        stability: dict = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._gemini_response_if_ready(stability)
            if result:
                return result
            self._sleep(1)
        raise RuntimeError(f'Timeout: Gemini không phản hồi sau {timeout}s')

    # ── Gemini "Tạo video" — engine THỨ 2 cho task video, thay thế VEO3/Flow DOM
    # (2026-08-07) ────────────────────────────────────────────────────────────
    #
    # Khác HẲN `_run_task_gemini`/`_run_gemini_loop` ở trên (dùng cho pipeline
    # kịch bản/ảnh NanaBananaPro, tiêu thụ hàng đợi `gemini_pending_requests`
    # qua `_heartbeat_gemini`) — nhóm hàm này xử lý TRỰC TIẾP task VIDEO trong
    # `tasks_media_flow` (CÙNG hàng đợi mà VEO3 `worker_mode='dom'/'api'` tiêu
    # thụ qua `_heartbeat()`/`/api/media/heartbeat`), chỉ khác "công cụ tạo" là
    # Gemini chat (mục "Tạo video" trong menu '+') thay vì Flow DOM automation.
    # Do đó `_run_task_gemini_video()` mirror ĐÚNG shape của `_run_task_dom()`
    # (POST /task/processing → làm việc → POST kết quả/lỗi), KHÔNG dùng
    # `callback_url`/`gemini_pending_requests` như `_run_task_gemini`.
    #
    # Khám phá DOM (profile kqxs0007, worker_mode='gemini', xem CHANGELOG
    # 2026-08-07 để có ảnh chụp/log đầy đủ):
    #   - Mở menu '+' (`_gemini_open_composer_menu`, ĐÃ CÓ sẵn) → click chip
    #     text CHÍNH XÁC 'Tạo video' (role=menuitemcheckbox, KHÔNG có testId
    #     ổn định — so text, giống cách tìm 'Tạo hình ảnh' nếu cần sau này).
    #   - Sau khi vào chế độ Video: nút "Tải tệp lên" (đính kèm ảnh tham chiếu)
    #     VẪN DÙNG ĐƯỢC NGUYÊN `_gemini_attach_file()`/`_gemini_open_composer_
    #     menu()` sẵn có — verify trực tiếp bằng Selenium thật, không cần viết
    #     lại (dù `data-test-id="local-images-files-uploader-button"` KHÔNG map
    #     trực tiếp ra 1 nút trực tiếp trên toolbar trong chế độ video, luồng
    #     "mở menu + tìm mục" vẫn hoạt động y hệt).
    #   - Nút chọn tỷ lệ khung hình: `button[aria-label^="Tỷ lệ khung hình"]`
    #     (label đầy đủ vd "Tỷ lệ khung hình, Ngang (16:9)") — click mở
    #     `.cdk-overlay-container` chứa đúng 2 option `[role="menuitemradio"]`
    #     ("Ngang (16:9)"/"Dọc (9:16)"), `aria-checked` phản ánh ĐÚNG lựa chọn
    #     hiện tại — verify bằng cách đổi ratio rồi đọc lại aria-label của
    #     trigger button, khớp 100%.
    #   - Kết quả: 1 thẻ `<video crossorigin="use-credentials" src="https://
    #     contribution.usercontent.google.com/download?...">` xuất hiện trong
    #     response — `src` này CẦN session cookie (không public), backend
    #     KHÔNG fetch được (không có cookie Google) nên KHÔNG dùng
    #     `/api/media/task/download` (URL-based) như VEO3 — phải tải NGAY
    #     TRONG BROWSER (`fetch(url,{credentials:'include'})` → blob →
    #     base64, cùng pattern đã proven ở `_extract_response_images_base64`),
    #     giải mã base64 ở Python rồi upload multipart qua
    #     `/api/media/task/<id>/upload_result` (endpoint CÓ SẴN, dùng cho
    #     "Thêm ảnh/video thủ công" — `_apply_media_to_task()` tự set
    #     `status='done'`, không cần backend đổi gì). Verify end-to-end thật:
    #     video 2.5MB tải về đúng, header PE `ftyp isom` hợp lệ, phát được.
    #   - Thời gian generate: prompt ngắn KHÔNG ref ảnh ~65-72s; CÓ ref ảnh có
    #     thể lâu hơn NHIỀU (1 lần test vượt quá 150s chưa xong) — timeout mặc
    #     định phải RỘNG HƠN hẳn timeout chat text thông thường.

    def _gemini_video_enter_mode(self):
        """Mở menu '+' rồi click chip 'Tạo video'/'Create video' — bật chế độ
        tạo video cho composer hiện tại. Idempotent: nếu đã ở chế độ Video (nút
        'Bỏ chọn Video'/'Deselect Videos' đã tồn tại) thì bỏ qua, không click
        lại (click lại sẽ TẮT chế độ video — toggle).

        i18n (2026-09-09) — xem `geminiCreateVideo`/`geminiDeselectVideo` trong
        `i18n_texts.json`/`get_i18n_texts()`."""
        deselect_variants_js = json.dumps(get_i18n_texts()['geminiDeselectVideo'])
        create_variants_js = json.dumps(get_i18n_texts()['geminiCreateVideo'])
        already = self._js(f"""
            var variants = {deselect_variants_js};
            return variants.some(function(v){{ return !!document.querySelector('[aria-label="' + v + '"]'); }});
        """)
        if already:
            return
        chip = None
        deadline = time.time() + 10
        while time.time() < deadline and not chip:
            self._gemini_open_composer_menu()
            self._sleep(0.5)
            chip = self._js(f"""
                var variants = {create_variants_js};
                var all = [...document.querySelectorAll('[role="menuitem"],[role="menuitemcheckbox"],button,[role="button"]')]
                    .filter(function(el){{ return el.getClientRects().length>0; }});
                return all.find(function(el){{
                    var t = el.textContent.trim();
                    return variants.indexOf(t) !== -1;
                }}) || null;
            """)
            if not chip:
                self._sleep(0.5)
        if not chip:
            raise RuntimeError('Không tìm thấy mục "Tạo video"/"Create video" trong menu Gemini')
        self._cdp_click_el(chip)
        self._sleep(1.2)
        activated = self._js(f"""
            var variants = {deselect_variants_js};
            return variants.some(function(v){{ return !!document.querySelector('[aria-label="' + v + '"]'); }});
        """)
        if not activated:
            raise RuntimeError('Đã bấm "Tạo video" nhưng chế độ Video không kích hoạt')

    def _gemini_image_enter_mode(self):
        """Mở menu '+' rồi click chip 'Tạo hình ảnh'/'Create images' — bật chế
        độ tạo ảnh cho composer hiện tại. Mirror ĐÚNG `_gemini_video_enter_
        mode()` ngay trên (xem docstring hàm đó để biết đầy đủ cơ chế poll/
        idempotent-check).

        (2026-09-13, fix bug thật user báo: "task tạo image... ko chọn option
        mà gửi thẳng chat làm tạo ảnh sai") — `_run_task_gemini_image()` từ
        trước tới giờ gõ+gửi prompt THẲNG vào composer mặc định (như 1 câu hỏi
        chat thường), KHÔNG hề bật mục 'Tạo hình ảnh' trong menu '+' trước —
        Gemini ở chế độ chat mặc định có thể chỉ mô tả bằng lời/hỏi lại/dùng
        model ảnh khác thay vì chắc chắn gọi đúng công cụ 'Create images'
        chuyên dụng, khớp đúng TODO để lại lúc viết `_gemini_video_enter_
        mode()` ("giống cách tìm 'Tạo hình ảnh' nếu cần sau này").

        Idempotent: nếu đã ở chế độ Ảnh (nút 'Bỏ chọn Hình ảnh'/'Deselect
        images' đã tồn tại) thì bỏ qua, không click lại (click lại sẽ TẮT chế
        độ — toggle).

        i18n — `geminiCreateImage`/`geminiDeselectImage` trong `i18n_texts.
        json`/`get_i18n_texts()`. ⚠️ CHƯA VERIFY trên DOM Gemini thật (môi
        trường phát triển không chạy được Chrome) — nhãn là SUY LUẬN theo
        đúng convention 'Tạo video'/'Create video' đã xác nhận thật, KHÔNG
        PHẢI capture trực tiếp. Nếu chip không khớp tên thật, sửa lại
        `i18n_texts.json` (không cần đổi code)."""
        deselect_variants_js = json.dumps(get_i18n_texts()['geminiDeselectImage'])
        create_variants_js = json.dumps(get_i18n_texts()['geminiCreateImage'])
        already = self._js(f"""
            var variants = {deselect_variants_js};
            return variants.some(function(v){{ return !!document.querySelector('[aria-label="' + v + '"]'); }});
        """)
        if already:
            return
        chip = None
        deadline = time.time() + 10
        while time.time() < deadline and not chip:
            self._gemini_open_composer_menu()
            self._sleep(0.5)
            chip = self._js(f"""
                var variants = {create_variants_js};
                var all = [...document.querySelectorAll('[role="menuitem"],[role="menuitemcheckbox"],button,[role="button"]')]
                    .filter(function(el){{ return el.getClientRects().length>0; }});
                return all.find(function(el){{
                    var t = el.textContent.trim();
                    return variants.indexOf(t) !== -1;
                }}) || null;
            """)
            if not chip:
                self._sleep(0.5)
        if not chip:
            raise RuntimeError('Không tìm thấy mục "Tạo hình ảnh"/"Create images" trong menu Gemini')
        self._cdp_click_el(chip)
        self._sleep(1.2)
        activated = self._js(f"""
            var variants = {deselect_variants_js};
            return variants.some(function(v){{ return !!document.querySelector('[aria-label="' + v + '"]'); }});
        """)
        if not activated:
            raise RuntimeError('Đã bấm "Tạo hình ảnh" nhưng chế độ Ảnh không kích hoạt')

    def _gemini_video_set_ratio(self, aspect_ratio: str):
        """Map `aspect_ratio` (vd '16:9'/'9:16', tự do format của
        `tasks_media_flow.aspect_ratio`) sang 1 trong ĐÚNG 2 option Gemini video
        hỗ trợ ('Ngang (16:9)'/'Dọc (9:16)', hoặc 'Landscape (16:9)'/'Portrait
        (9:16)' trên account tiếng Anh) rồi chọn qua dropdown
        `role="menuitemradio"`. Rỗng/không khớp '9:16' → mặc định Ngang/Landscape
        (16:9, cũng là default của Gemini) — best-effort, lỗi chỉ log warning,
        không chặn task (đổi ratio thất bại không nên hỏng cả video).

        i18n (2026-09-09) — BUG THẬT bắt được qua test end-to-end thật trên
        account tiếng Anh (huavantien84@gmail.com): bản cũ hardcode CHỈ tiếng
        Việt ('Tỷ lệ khung hình'/'Ngang'/'Dọc') nên luôn rơi vào nhánh "không
        tìm thấy nút" trên account đó — verify trực tiếp DOM thật xác nhận
        label đầy đủ là 'Aspect ratio, Landscape (16:9)', option dropdown
        'Landscape (16:9)'/'Portrait (9:16)'. Xem `geminiAspectRatioPrefix`/
        `geminiRatioHorizontal`/`geminiRatioVertical` trong `i18n_texts.json`."""
        want_vertical = '9:16' in (aspect_ratio or '')
        want_variants = get_i18n_texts()['geminiRatioVertical' if want_vertical else 'geminiRatioHorizontal']
        want_variants_js = json.dumps(want_variants)
        prefix_variants_js = json.dumps(get_i18n_texts()['geminiAspectRatioPrefix'])
        try:
            ratio_btn = self._js(f"""
                var prefixes = {prefix_variants_js};
                return [...document.querySelectorAll('button,[role="button"]')]
                    .find(function(el){{
                        var a = el.getAttribute('aria-label') || '';
                        return prefixes.some(function(p){{ return a.indexOf(p) === 0; }});
                    }}) || null;
            """)
            if not ratio_btn:
                self._log('warn', '[gemini-video] Không tìm thấy nút tỷ lệ khung hình — giữ mặc định')
                return
            current_label = self._js("return arguments[0].getAttribute('aria-label') || '';", ratio_btn)
            if any(v in current_label for v in want_variants):
                return  # đã đúng sẵn, không cần mở dropdown
            self._cdp_click_el(ratio_btn)
            self._sleep(0.6)
            target = self._wait_js(f"""
                var variants = {want_variants_js};
                var c = document.querySelector('.cdk-overlay-container');
                var items = c ? [...c.querySelectorAll('[role="menuitemradio"]')] : [];
                return items.find(function(el){{
                    var t = el.textContent.trim();
                    return variants.some(function(v){{ return t.indexOf(v) === 0; }});
                }}) || null;
            """, (), timeout=3)
            if not target:
                self._log('warn', f'[gemini-video] Không tìm thấy option tỷ lệ "{want_variants}" — giữ mặc định')
                return
            self._cdp_click_el(target)
            self._sleep(0.5)
            self._log('info', f'[gemini-video] Tỷ lệ khung hình → {want_variants[0]}')
        except Exception as e:
            self._log('warn', f'[gemini-video] Đổi tỷ lệ khung hình lỗi: {e} — giữ mặc định')

    # (2026-08-08) Bắt được qua log production THẬT (profile "gemini-video",
    # id=31, task #4710/#4711, CẢ 2 đều có ref ảnh tham chiếu) — Gemini gửi 1
    # ĐOẠN ACK NGẮN ngay khi bắt đầu xử lý ("I'm generating your video. This
    # could take a few minutes... / Đang tạo video cho bạn… Quá trình này có
    # thể mất vài phút.") — TURN CHAT này (kèm nút "Stop response"/loading-
    # indicator) hoàn tất RẤT NHANH (ack ngắn), NHƯNG video thật generate
    # NGẦM Ở BACKGROUND, tách rời khỏi trạng thái "đang generate" của khung
    # chat — is_generating về `false` trong khi <video> CHƯA hề tồn tại. Bản
    # cũ coi is_generating=false + có text + không có video = "Gemini từ chối"
    # → raise lỗi NGAY dù Gemini vẫn đang xử lý thật (verify: cả 2 task lỗi ở
    # ~79s/~80s sau submit — đúng khớp mốc video BÌNH THƯỜNG mới bắt đầu render
    # xong theo các lần test trước, KHÔNG phải bị từ chối). Nhận diện text ACK
    # này (theo cả tiếng Anh lẫn tiếng Việt Gemini tự trả) → coi là "vẫn đang
    # xử lý", tiếp tục poll thay vì kết luận lỗi ngay — CHỈ text KHÔNG khớp
    # pattern này (vd rate-limit "I'm getting a lot of requests...", đã verify
    # thật ở lần trước) mới bị coi là lỗi thật.
    _GEMINI_VIDEO_STILL_WORKING_MARKERS = (
        'generating your video', 'đang tạo video', 'check back',
        'a few minutes', 'few minutes', 'vài phút',
    )

    def _gemini_video_result_if_ready(self) -> dict | None:
        """1 lần kiểm tra KHÔNG-BLOCKING kết quả video. Trả:
          - {'video_url': str}  khi có <video> kết quả
          - {'error': str}      khi Gemini đã trả lời xong với text KHÔNG PHẢI
                                 ack "đang xử lý" và KHÔNG có video (từ chối/lỗi
                                 nội dung thật — trích text phản hồi làm thông
                                 báo lỗi)
          - None                khi còn đang generate HOẶC đang ở giai đoạn ack
                                 "đang xử lý ngầm" (xem _GEMINI_VIDEO_STILL_
                                 WORKING_MARKERS) — caller tiếp tục poll
        `crossorigin="use-credentials"` trên thẻ <video> (xem discovery) xác
        nhận `src` cần session cookie — KHÔNG dùng được trực tiếp từ server,
        chỉ trả URL ở đây, việc tải+base64 do `_gemini_video_extract_base64()`
        đảm nhiệm."""
        is_generating = self._js("""
            if (document.querySelector('button[aria-label="Stop response"]')) return true;
            if (document.querySelector('button[aria-label*="Stop"]')) return true;
            if (document.querySelector('gem-icon-button.stop')) return true;
            if (document.querySelector('model-response loading-indicator')) return true;
            return false;
        """)
        video_url = self._js("""
            var vids = document.querySelectorAll('video');
            if (!vids.length) return null;
            var last = vids[vids.length - 1];
            return last.currentSrc || last.src || null;
        """)
        if video_url:
            return {'video_url': video_url}
        if is_generating:
            return None
        text = self._js("""
            var containers = document.querySelectorAll('structured-content-container');
            if (!containers.length) return null;
            var last = containers[containers.length - 1];
            var md = last.querySelector('.markdown');
            return md ? md.innerText.trim() : null;
        """) or ''
        if not text or len(text) <= 3:
            return None
        lower = text.lower()
        if any(marker in lower for marker in self._GEMINI_VIDEO_STILL_WORKING_MARKERS):
            return None
        return {'error': text[:500]}

    def _gemini_video_wait_result(self, timeout: int = 600) -> dict:
        """Chờ tới khi có video hoặc lỗi rõ ràng, raise nếu timeout. Mặc định
        600s (10 phút) — RỘNG HƠN hẳn `gemini_response_timeout` dùng cho chat
        text (300s) vì generate video chậm hơn nhiều (verify thật: prompt
        không ref ảnh ~65-72s, có ref ảnh có thể vượt 150s)."""
        self._sleep(2)
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._gemini_video_result_if_ready()
            if result:
                if result.get('error'):
                    raise RuntimeError(f'Gemini từ chối/không tạo được video: {result["error"]}')
                return result
            self._sleep(3)
        raise RuntimeError(f'Timeout: Gemini không trả về video sau {timeout}s')

    def _gemini_video_extract_base64(self, video_url: str, timeout: int = 60) -> bytes:
        """Tải video kết quả NGAY TRONG BROWSER (fetch credentials:'include' →
        blob → FileReader base64, cùng pattern đã proven cho ảnh — xem
        `_extract_response_images_base64`) rồi giải mã ở Python. `video_url`
        yêu cầu session cookie (`crossorigin="use-credentials"`) nên server
        KHÔNG tự tải được — phải tải trong context trang. Verify thật (2026-08-07):
        video 2.5MB tải đúng, header PE hợp lệ."""
        result = self._js_async("""
            var url = arguments[0];
            var callback = arguments[arguments.length - 1];
            fetch(url, {credentials: 'include'}).then(function(r){
                if (!r.ok) { callback({ok:false, error:'HTTP '+r.status}); return; }
                return r.blob();
            }).then(function(blob){
                if (!blob) return;
                var reader = new FileReader();
                reader.onload = function(){ callback({ok:true, size: blob.size, dataUrl: reader.result}); };
                reader.onerror = function(){ callback({ok:false, error:'FileReader error'}); };
                reader.readAsDataURL(blob);
            }).catch(function(e){ callback({ok:false, error: String(e)}); });
        """, video_url, timeout=timeout) or {}
        if not result.get('ok') or not result.get('dataUrl'):
            raise RuntimeError(f'Tải video kết quả thất bại: {result.get("error", "unknown")}')
        _head, data = result['dataUrl'].split(',', 1)
        return base64.b64decode(data)

    def _upload_video_result(self, task_id, video_bytes: bytes, filename: str = 'video.mp4') -> dict:
        """Upload trực tiếp bytes video làm kết quả task — dùng
        `/api/media/task/<id>/upload_result` (multipart, CÓ SẴN — dùng bởi
        tính năng "Thêm video thủ công" trên web, `_apply_media_to_task()` tự
        set `status='done'`). KHÔNG dùng `/task/download` (URL-based) vì
        `video_url` của Gemini cần session cookie, server backend không tải
        được trực tiếp — xem docstring `_gemini_video_extract_base64`."""
        url = f'{FLOW_SERVER}/api/media/task/{task_id}/upload_result'
        r = req_lib.post(url, files={'files': (filename, video_bytes, 'video/mp4')}, timeout=120)
        r.raise_for_status()
        data = r.json()
        if not data.get('success', True):
            raise RuntimeError(data.get('error') or 'upload_result thất bại')
        return data

    def _run_task_gemini_video(self, task: dict) -> bool:
        """Xử lý 1 task VIDEO (`tasks_media_flow`, CÙNG hàng đợi VEO3) bằng
        Gemini chat's "Tạo video" thay vì Flow DOM automation. Mirror shape của
        `_run_task_dom()` — trả True nếu escalation đã cho profile 'ngủ'."""
        task_id      = task['id']
        mode         = task.get('mode', 'textToVideo')
        prompt       = (task.get('prompt_text') or task.get('title') or '').strip()
        aspect_ratio = (task.get('aspect_ratio') or '').strip()
        source_media = task.get('source_media') or []
        if isinstance(source_media, str):
            try:    source_media = json.loads(source_media)
            except: source_media = []

        attach_timeout   = int(self.profile.get('gemini_attach_timeout') or 180)
        # (2026-08-07) response_timeout dành riêng cho video — RỘNG HƠN hẳn giá
        # trị mặc định 300s của `gemini_response_timeout` (dùng chung field
        # profile, nhưng sàn tối thiểu clamp lên 600s cho video — xem docstring
        # _gemini_video_wait_result). Profile tự set cao hơn (vd 900s) vẫn tôn
        # trọng nguyên giá trị đó.
        response_timeout = max(600, int(self.profile.get('gemini_response_timeout') or 300))

        self._log('info', f'▶ [GEMINI-VIDEO] Task #{task_id} mode={mode} ratio={aspect_ratio or "?"} '
                           f'ref={len(source_media)} "{prompt[:60]}"')
        pm.set_status(self.profile_id, 'processing', task_id=task_id)
        self._task_start(task_id, mode, prompt)

        stop_now = False
        tmp_paths: list = []
        try:
            self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                      body={'taskId': task_id, 'machineCode': self.machine_code})

            # Luôn bắt đầu chat mới (mirror _run_task_gemini) — tránh lẫn kết
            # quả/state giữa các task, và composer luôn về lại chế độ mặc định.
            # (2026-08-08) Raise SỚM + rõ ràng nếu đăng nhập thất bại — không
            # thì bước ngay dưới sẽ tự thất bại theo kiểu khó hiểu ("không tìm
            # thấy mục Tạo video") vì vẫn đang đứng ở accounts.google.com.
            if not self._ensure_google_login(self.GEMINI_URL):
                raise RuntimeError('Đăng nhập Google thất bại/chưa cấu hình — '
                                    'xem log [google-login] để biết chi tiết')
            self._gemini_video_enter_mode()
            self._gemini_video_set_ratio(aspect_ratio)

            if source_media:
                profile_tmp_dir = VIDEO_TMP_DIR / f'profile_{self.profile_id}'
                profile_tmp_dir.mkdir(exist_ok=True)
                for idx, item in enumerate(source_media):
                    url = item.get('url') if isinstance(item, dict) else item
                    if not url:
                        continue
                    # (2026-08-07, bug thật user gặp — Task #4635 lỗi "Invalid URL
                    # ... No scheme supplied") — `url` ở đây là đường dẫn TƯƠNG ĐỐI
                    # server trả về (vd `/api/media/files/storyboard/...png`, xem
                    # `_storyboard_task_url()`/`result_files` phía backend), KHÔNG
                    # phải URL tuyệt đối — `requests.get()` cần scheme mới gọi được.
                    # Mirror ĐÚNG pattern đã dùng ở nhánh DOM upload (~line 2000
                    # phía trên, cùng file) thay vì trust thẳng giá trị `url` thô.
                    if url.startswith('/'):
                        url = f'{FLOW_SERVER}{url}'
                    if not url.startswith('http'):
                        self._log('warn', f'[gemini-video] skip invalid ref url "{url}"')
                        continue
                    self._log('info', f'[gemini-video] Đang tải ảnh tham chiếu {idx+1}/{len(source_media)}…')
                    resp = req_lib.get(url, timeout=60, stream=True)
                    resp.raise_for_status()
                    orig_name = _normalize_attach_filename(os.path.basename(url.split('?')[0]) or f'ref_{idx}.jpg')
                    filename  = orig_name if idx == 0 else f'{idx}_{orig_name}'
                    tmp_path  = str(profile_tmp_dir / filename)
                    with open(tmp_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                    tmp_paths.append(tmp_path)
                    self._gemini_attach_file(tmp_path, timeout=attach_timeout)

            task_prompt = prompt or 'Create a short video.'
            self._gemini_fill_and_submit(task_prompt, send_ready_timeout=attach_timeout)
            result = self._gemini_video_wait_result(timeout=response_timeout)
            video_url = result['video_url']
            self._log('ok', '[gemini-video] ✔ Video xuất hiện — đang tải + upload kết quả…')

            video_bytes = self._gemini_video_extract_base64(video_url, timeout=90)
            self._upload_video_result(task_id, video_bytes, filename=f'task{task_id}_gemini.mp4')
            self._log('ok', f'✔ [GEMINI-VIDEO] Task #{task_id} — {len(video_bytes)} bytes uploaded')
            self._handle_task_success()

        except Exception as e:
            self._log('error', f'[GEMINI-VIDEO] Task #{task_id} lỗi: {e}')
            try:
                self._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                          body={'taskId': task_id, 'machineCode': self.machine_code,
                                'errorMessage': str(e)})
            except Exception:
                pass
            stop_now = self._handle_task_error(str(e))
        finally:
            for p in tmp_paths:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass
            if not stop_now:
                pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())
        return stop_now

    # ── Gemini chat "tạo ảnh thường" — engine THỨ 2 cho task ẢNH (2026-09-09)
    # ────────────────────────────────────────────────────────────────────────
    # Mirror 1:1 `_run_task_gemini_video()`/`_upload_video_result()` ngay trên,
    # chỉ khác: KHÔNG cần `_gemini_video_enter_mode()`/`_gemini_video_set_ratio()`
    # (ảnh generate thường trong chat KHÔNG có khái niệm chọn tỷ lệ khung hình —
    # đã verify trực tiếp qua `tests/_test_geminiImageToImage.py`, chạy end-to-end
    # THẬT trên profile production, Gemini trả về ảnh dùng canvas-based extraction
    # sẵn có `_extract_response_images_base64()`/`_gemini_wait_response()`), và
    # kết quả là ẢNH (có thể NHIỀU ảnh/lượt) thay vì đúng 1 video — upload multipart
    # field 'files' HỖ TRỢ NHIỀU FILE 1 LẦN GỌI (backend đọc qua
    # `request.files.getlist('files')`, xem `backend/routes/tasks.py::
    # upload_task_result()`), khác `_upload_video_result()` chỉ có 1 file.
    def _upload_image_result(self, task_id, files: list) -> dict:
        """Upload trực tiếp N ảnh (list[(filename, bytes, mime)]) làm kết quả
        task — dùng `/api/media/task/<id>/upload_result` (multipart, CÓ SẴN).
        `_apply_media_to_task()` phía backend tự APPEND (không ghi đè) + set
        `status='done'`, giống hệt cơ chế "Render lại" dùng khắp NanoBananaPro."""
        url = f'{FLOW_SERVER}/api/media/task/{task_id}/upload_result'
        payload = [('files', f) for f in files]
        r = req_lib.post(url, files=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        if not data.get('success', True):
            raise RuntimeError(data.get('error') or 'upload_result thất bại')
        return data

    def _run_task_gemini_image(self, task: dict) -> bool:
        """Xử lý 1 task ẢNH (`tasks_media_flow`, CÙNG hàng đợi VEO3 mà
        `worker_mode='dom'/'api'` tiêu thụ qua `_heartbeat()`/`heartbeat.py`)
        bằng Gemini chat's khả năng generate ảnh thường, thay Flow DOM/API.
        Mirror shape của `_run_task_gemini_video()` — trả True nếu escalation
        đã cho profile 'ngủ'."""
        task_id      = task['id']
        mode         = task.get('mode', 'textToImage')
        prompt       = (task.get('prompt_text') or task.get('title') or '').strip()
        source_media = task.get('source_media') or []
        if isinstance(source_media, str):
            try:    source_media = json.loads(source_media)
            except: source_media = []

        attach_timeout   = int(self.profile.get('gemini_attach_timeout') or 180)
        response_timeout = int(self.profile.get('gemini_response_timeout') or 300)

        self._log('info', f'▶ [GEMINI-IMAGE] Task #{task_id} mode={mode} '
                           f'ref={len(source_media)} "{prompt[:60]}"')
        pm.set_status(self.profile_id, 'processing', task_id=task_id)
        self._task_start(task_id, mode, prompt)

        stop_now = False
        tmp_paths: list = []
        try:
            self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                      body={'taskId': task_id, 'machineCode': self.machine_code})

            # Luôn bắt đầu chat mới (mirror _run_task_gemini_video) — tránh lẫn
            # kết quả/state giữa các task.
            if not self._ensure_google_login(self.GEMINI_URL):
                raise RuntimeError('Đăng nhập Google thất bại/chưa cấu hình — '
                                    'xem log [google-login] để biết chi tiết')
            # (2026-09-13) Bật mục "Tạo hình ảnh" TRƯỚC khi gõ prompt — xem
            # docstring `_gemini_image_enter_mode()` (fix bug thật user báo:
            # trước đây gõ thẳng vào composer mặc định, không chọn option nào,
            # sinh ảnh sai/không đúng ý).
            self._gemini_image_enter_mode()

            if source_media:
                profile_tmp_dir = VIDEO_TMP_DIR / f'profile_{self.profile_id}'
                profile_tmp_dir.mkdir(exist_ok=True)
                for idx, item in enumerate(source_media):
                    url = item.get('url') if isinstance(item, dict) else item
                    if not url:
                        continue
                    # Cùng bug/fix đã ghi ở _run_task_gemini_video() — `url` có
                    # thể là đường dẫn TƯƠNG ĐỐI server trả về.
                    if url.startswith('/'):
                        url = f'{FLOW_SERVER}{url}'
                    if not url.startswith('http'):
                        self._log('warn', f'[gemini-image] skip invalid ref url "{url}"')
                        continue
                    self._log('info', f'[gemini-image] Đang tải ảnh tham chiếu {idx+1}/{len(source_media)}…')
                    resp = req_lib.get(url, timeout=60, stream=True)
                    resp.raise_for_status()
                    orig_name = _normalize_attach_filename(os.path.basename(url.split('?')[0]) or f'ref_{idx}.jpg')
                    filename  = orig_name if idx == 0 else f'{idx}_{orig_name}'
                    tmp_path  = str(profile_tmp_dir / filename)
                    with open(tmp_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                    tmp_paths.append(tmp_path)
                    self._gemini_attach_file(tmp_path, timeout=attach_timeout)

            task_prompt = prompt or 'Create an image.'
            self._gemini_fill_and_submit(task_prompt, send_ready_timeout=attach_timeout)
            result = self._gemini_wait_response(timeout=response_timeout)
            images = result.get('images') or []
            if not images:
                raise RuntimeError('Gemini không trả về ảnh nào (chỉ có text — có thể là '
                                    'từ chối/câu hỏi làm rõ thay vì generate ảnh)')

            files_payload = []
            for i, data_uri in enumerate(images, 1):
                if ',' not in data_uri:
                    continue
                _head, b64data = data_uri.split(',', 1)
                raw = base64.b64decode(b64data)
                is_jpeg = raw.startswith(b'\xff\xd8\xff')
                ext, mime = ('jpg', 'image/jpeg') if is_jpeg else ('png', 'image/png')
                files_payload.append((f'task{task_id}_gemini_{i}.{ext}', raw, mime))
            if not files_payload:
                raise RuntimeError('Gemini trả về ảnh nhưng không đọc được data URI hợp lệ nào')

            self._upload_image_result(task_id, files_payload)
            self._log('ok', f'✔ [GEMINI-IMAGE] Task #{task_id} — {len(files_payload)} ảnh uploaded')
            self._handle_task_success()

        except Exception as e:
            self._log('error', f'[GEMINI-IMAGE] Task #{task_id} lỗi: {e}')
            try:
                self._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                          body={'taskId': task_id, 'machineCode': self.machine_code,
                                'errorMessage': str(e)})
            except Exception:
                pass
            stop_now = self._handle_task_error(str(e))
        finally:
            for p in tmp_paths:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass
            if not stop_now:
                pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())
        return stop_now

    def _heartbeat_gemini(self, running: int = 0, max_concurrent: int = 1,
                          keepalive_only: bool = False) -> list:
        """Heartbeat riêng cho machine_type=gemini_chat_selenium — server trả về
        pendingPrompts[] (mảng, có thể 0..max_concurrent item — xem
        backend/routes/heartbeat.py) theo nhánh riêng, KHÔNG phải tasks[] của VEO3.
        Dùng type RIÊNG (khác `gemini_chat` của extension_gemini) để 2 loại worker
        Gemini hiển thị thành 2 nhóm tách biệt trên MachinesPage — dù được server xử
        lý y hệt nhau (xem _pick_gemini_machine trong backend/routes/gemini.py, chọn
        cả 2 type khi giao prompt).

        `max_concurrent` (2026-07-24) — số tab Gemini worker này chạy round-robin
        đồng thời (xem `_run_gemini_loop_concurrent`); mặc định 1 = hành vi cũ (luôn
        tối đa 1 prompt/lần, `_run_gemini_loop` tuần tự đọc `pendingPrompts[0]`).

        `running` PHẢI phản ánh ĐÚNG số slot/tab đang thật sự bận lúc gọi (không chỉ
        0/1 nhị phân như trước) — server tính `free_slots = max_concurrent - running`
        để quyết định giao thêm bao nhiêu; báo sai sẽ giao thiếu/thừa.

        `keepalive_only=True` — chỉ để giữ last_seen tươi (server SKIP HẲN bước giao
        việc, luôn trả `pendingPrompts: []` dù có backlog thật) khi 1 batch đang xử lý
        dở — round-robin loop gọi cờ này giữa chừng để KHÔNG nhận thêm việc mới cho
        tới khi TOÀN BỘ batch hiện tại xong (đúng ngữ nghĩa "nhận đủ N task, xong hết
        mới nhận lô tiếp theo" — xem client_tool/CLAUDE.md). Cùng cơ chế đã dùng cho
        VEO3 (`_process_tasks`'s keepalive thread), tái dùng nguyên, không cần đổi gì
        phía backend cho case này."""
        try:
            profile      = pm.get(self.profile_id)
            display_name = (profile.get('display_name') or '').strip() \
                or f'[SEL-Gemini] {profile.get("profile_name", "")}'
            r = self._req('POST', f'{FLOW_SERVER}/api/media/heartbeat', quiet=True,
                          body={'machineCode':  self.machine_code,
                                'runningCount': running,
                                'waitingCount': 0,
                                'machineType':  'gemini_chat_selenium',
                                'displayName':  display_name,
                                'maxConcurrent': max_concurrent,
                                'keepaliveOnly': keepalive_only}) or {}
            # (2026-09-06) `or {}` — `_req()` trả `r.json()`, mà body `null` từ
            # server/proxy cho ra `None` ⇒ `.get()` ném "'NoneType' object has no
            # attribute 'get'" (đã thấy thật trong profile_logs 2026-09-06 00:57).
            pending_list = r.get('pendingPrompts')
            if pending_list is None:
                single = r.get('pendingPrompt')
                pending_list = [single] if single else []
            for pending in pending_list:
                self._log('info',
                          f'← Heartbeat: nhận prompt {len(pending.get("prompt", ""))} ký tự'
                          f'{" + video" if pending.get("videoUrl") else ""}')
            return pending_list
        except Exception as e:
            self._log('warn', f'Heartbeat (gemini) error: {e}')
            return []

    def _run_task_gemini(self, pending: dict):
        """Xử lý 1 pendingPrompt: tải MỌI ảnh/video đính kèm (nếu có, qua `imageUrls`
        — mảng đầy đủ, 2026-08-04, fix bug thật "upload 2 ảnh tham chiếu nhưng qua
        chatgpt/gemini chỉ thấy đính kèm có 1 ảnh"; fallback `videoUrl` số ít cho
        backend CŨ chưa gửi `imageUrls`) qua requests (Python, không dính mixed-content
        vì không chạy trong page context) → attach TUẦN TỰ từng file bằng send_keys →
        gõ prompt → gửi → chờ phản hồi → POST callback_url."""
        prompt       = pending.get('prompt') or ''
        video_url    = pending.get('videoUrl')
        image_urls   = pending.get('imageUrls') or ([video_url] if video_url else [])
        # (2026-08-04) FIX bug thật user báo (backend ToolSub gốc): "upload ảnh
        # 1 xong -> upload ảnh 2 nhưng bị báo lỗi ảnh này đã upload lên rồi" —
        # nghi 2 URL khác nhau nhưng trỏ CÙNG 1 file vật lý (vd 2 asset dùng
        # chung ảnh qua "Thư viện", không copy byte) lọt xuống đây, worker tải
        # thành 2 file cục bộ nội dung GIỐNG HỆT, ChatGPT tự phát hiện trùng
        # theo nội dung. Backend đã dedup theo URL tại nguồn (`_dispatch_storyboard_
        # sheet_image()`), thêm lớp phòng vệ THỨ 2 độc lập ở client — cùng triết
        # lý dedup pendingPrompt phía client đã áp dụng ở §11.24.
        image_urls = list(dict.fromkeys(image_urls))
        meta         = pending.get('meta') or {}
        callback_url = pending.get('callbackUrl')

        # Timeout riêng theo profile (mục "Loại" = Gemini trong GUI) — mặc định 180s/300s
        # nếu profile chưa set (profile cũ trước migration, hoặc tạo qua API không truyền).
        attach_timeout   = int(self.profile.get('gemini_attach_timeout')   or 180)
        response_timeout = int(self.profile.get('gemini_response_timeout') or 300)

        pm.set_status(self.profile_id, 'processing')
        tmp_paths = []

        # Keepalive: task có thể chạy vài phút (attach_timeout/response_timeout),
        # trong khi vòng lặp chính (_run_gemini_loop) bị block ở đây và không heartbeat
        # được — server sẽ đánh dấu machine 'offline' sau 30s không thấy heartbeat
        # (xem backend/routes/heartbeat.py, stale-check chạy mỗi lần MÁY KHÁC gọi
        # heartbeat). Thread nền này gửi heartbeat running=1 mỗi POLL_INTERVAL giây
        # trong suốt lúc xử lý để giữ machine luôn "còn sống" trên server.
        stop_keepalive = threading.Event()

        def _keepalive():
            while not stop_keepalive.wait(POLL_INTERVAL):
                self._heartbeat_gemini(running=1, keepalive_only=True)

        keepalive_thread = threading.Thread(target=_keepalive, daemon=True)
        keepalive_thread.start()

        try:
            # Luôn bắt đầu chat mới. (2026-08-08) Raise sớm nếu đăng nhập
            # thất bại — tránh lỗi khó hiểu ở bước ngay dưới.
            if not self._ensure_google_login(self.GEMINI_URL):
                raise RuntimeError('Đăng nhập Google thất bại/chưa cấu hình — '
                                    'xem log [google-login] để biết chi tiết')

            # Đổi model Gemini (2026-07-20) nếu project cấu hình gemini_model — rỗng
            # thì _gemini_select_model() tự no-op ngay, giữ nguyên model mặc định.
            self._gemini_select_model(meta.get('geminiModel'))

            if image_urls:
                # (2026-08-04) Tải + đính kèm TUẦN TỰ TỪNG ảnh trong `image_urls` —
                # trước đây chỉ xử lý ĐÚNG 1 file (`video_url`), giờ lặp qua mảng đầy
                # đủ. Giữ nguyên tên file gốc (2026-07-20) — dùng thư mục con RIÊNG
                # theo profile (VIDEO_TMP_DIR/profile_{id}/) để không đụng file tạm
                # của profile khác; thêm tiền tố index (`0_`, `1_`...) phòng 2 ảnh
                # trong CÙNG 1 request trùng basename (hiếm nhưng có thể xảy ra).
                profile_tmp_dir = VIDEO_TMP_DIR / f'profile_{self.profile_id}'
                profile_tmp_dir.mkdir(exist_ok=True)
                for idx, url in enumerate(image_urls):
                    self._log('info', f'Đang tải file đính kèm {idx+1}/{len(image_urls)} từ server ({url})…')
                    resp = req_lib.get(url, timeout=120, stream=True)
                    resp.raise_for_status()
                    orig_name = _normalize_attach_filename(os.path.basename(url.split('?')[0]) or f'attach_{idx}.jpg')
                    filename  = orig_name if idx == 0 else f'{idx}_{orig_name}'
                    tmp_path  = str(profile_tmp_dir / filename)
                    with open(tmp_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                    tmp_paths.append(tmp_path)
                    size_kb = os.path.getsize(tmp_path) // 1024
                    self._log('ok', f'✔ Đã tải file đính kèm {idx+1}/{len(image_urls)} ({size_kb} KB) → {tmp_path}')

                    self._gemini_attach_file(tmp_path, timeout=attach_timeout)

            # send_ready_timeout dùng CHUNG attach_timeout (đã configurable per-profile,
            # xem ProfileDialog "Attach timeout") — về bản chất đây là 2 giai đoạn của
            # CÙNG 1 việc chờ ("Gemini xử lý xong file đính kèm"): (1) tile đính kèm hiện
            # ra composer (_gemini_attach_file), (2) nút Gửi hết disabled
            # (_gemini_fill_and_submit) — video lớn có thể còn cần xử lý THÊM sau khi tile
            # đã hiện, nên không dùng hardcode 60s riêng nữa (user báo thật: có video mất
            # tới 3 phút vẫn chưa gửi được, trong khi 60s cũ luôn timeout trước).
            # (2026-09-06) Ưu tiên GỬI ẨN qua RPC — không gõ, không click. Chỉ
            # thay bước GỬI; đọc kết quả vẫn bằng DOM ngay dưới (yêu cầu user:
            # *"api chỉ cần gửi nội dung còn lắng nghe đọc kết quả giống DOM"*).
            # Có file đính kèm → tự động dùng DOM (xem `_gemini_rpc_enabled`).
            sent_via_rpc = False
            if self._gemini_rpc_enabled(bool(image_urls)):
                sent_via_rpc = self._gemini_send_via_rpc(prompt, timeout=response_timeout)
            if not sent_via_rpc:
                self._gemini_fill_and_submit(prompt, send_ready_timeout=attach_timeout)
            result = self._gemini_wait_response(timeout=response_timeout)
            text   = result.get('text') or ''
            images = result.get('images') or []
            self._log('ok', f'✔ Nhận response {len(text)} ký tự'
                             + (f' + {len(images)} ảnh' if images else ''))

            if callback_url:
                try:
                    payload = {'prompt': prompt, 'response': text, 'meta': meta,
                               'ts': int(time.time() * 1000)}
                    if images:
                        payload['images'] = images
                    req_lib.post(callback_url, json=payload, timeout=30)
                    self._log('info', f'callback → {callback_url} ✔')
                except Exception as e:
                    self._log('warn', f'callback lỗi: {e}')

        except Exception as e:
            self._log('error', f'[gemini] Task lỗi: {e}')
            # BUG FIX (2026-07-05, cùng lớp lỗi với extension_gemini/background.js):
            # trước đây lỗi chỉ log cục bộ, không báo về server → clip/task kẹt vĩnh viễn
            # ở 'processing'. Luôn POST callback kèm field `error` nếu có callback_url.
            if callback_url:
                try:
                    req_lib.post(callback_url, json={
                        'prompt': prompt, 'error': str(e), 'meta': meta,
                        'ts': int(time.time() * 1000),
                    }, timeout=30)
                except Exception:
                    pass
        finally:
            # Dừng keepalive TRƯỚC — để không race với heartbeat running=0 kế tiếp
            # (vòng lặp _run_gemini_loop sẽ tự gọi ngay khi hàm này return, đưa machine
            # về đúng trạng thái rảnh/chờ nhận task trên server).
            stop_keepalive.set()
            keepalive_thread.join(timeout=POLL_INTERVAL + 2)
            for p in tmp_paths:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass
            pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())

    def _run_gemini_loop(self):
        """Main loop riêng cho worker_mode='gemini' (không dùng chung run() của Flow).
        Đọc `gemini_max_concurrent_tabs` của profile (2026-07-24) — 1 (mặc định,
        HÀNH VI CŨ không đổi) chạy tuần tự 1 tab; >1 giao hẳn cho
        `_run_gemini_loop_concurrent()` (round-robin nhiều tab)."""
        max_tabs = max(1, int(self.profile.get('gemini_max_concurrent_tabs') or 1))
        if max_tabs > 1:
            return self._run_gemini_loop_concurrent(max_tabs)

        self._log('info', 'Worker starting (Gemini Chat mode)')
        pm.set_status(self.profile_id, 'idle', pid=threading.get_ident())
        try:
            self.driver = self._make_driver()
            self._log('info', 'Chrome opened — navigate to Gemini')
            if self._ensure_google_login(self.GEMINI_URL):
                self._log('ok', 'Gemini Chat mode — sẵn sàng nhận prompt')
            else:
                self._log('error', 'Đăng nhập Google thất bại/chưa cấu hình — worker vẫn '
                                    'chạy nhưng MỌI task sẽ lỗi tới khi khắc phục (xem log '
                                    '[google-login] ở trên).')

            while not self._stop.is_set():
                if not self._is_driver_alive():
                    self._log('warn', 'Browser đã đóng — thoát worker loop')
                    break
                try:
                    pending_list = self._heartbeat_gemini()
                    if pending_list:
                        self._run_task_gemini(pending_list[0])
                        # (2026-08-30) TỪNG bỏ bước này cho task GEMINI theo yêu
                        # cầu user *"các task liên quan gemini không cần xóa
                        # cache khi xong"* — lúc đó ĐÚNG là vô nghĩa, vì
                        # `_cdp_clear_cache_and_cookies()` chỉ xoá cookie domain
                        # `labs.google`, không đụng gì `gemini.google.com`.
                        # (2026-09-13) KHÔI PHỤC lại theo yêu cầu MỚI "clear
                        # cookie như veo" — hàm đó giờ tra domain ĐÚNG theo
                        # `worker_mode` (xem `_batch_clean_target_domain()`),
                        # Gemini dọn `gemini.google.com` — không còn vô nghĩa
                        # nữa. Session Google cấp TÀI KHOẢN (`.google.com`) vẫn
                        # sống nguyên nên KHÔNG cần `_ensure_google_login()` để
                        # phục hồi (xem hàm đó, đã bỏ hẳn bước check cho Gemini).
                        self._cdp_clear_cache_and_cookies()
                    else:
                        self._stop.wait(POLL_INTERVAL)
                except Exception as e:
                    self._log('error', f'Worker loop (gemini): {e}')
                    if not self._is_driver_alive():
                        self._log('warn', 'Driver mất kết nối sau exception — thoát loop')
                        break
                    self._stop.wait(POLL_INTERVAL)

        except Exception as e:
            self._log('error', f'Worker fatal (gemini): {e}')
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            if not self._clear_profile_if_sleeping(pm.get(self.profile_id)):
                pm.set_status(self.profile_id, 'offline', clear_task=True, pid=None)
            self._log('info', 'Worker stopped')

    # ══════════════════════════════════════════════════════════════════════════
    # Gemini round-robin nhiều tab (2026-07-24) — worker_mode='gemini' với
    # `gemini_max_concurrent_tabs` > 1
    # ══════════════════════════════════════════════════════════════════════════
    # Selenium/ChromeDriver chỉ có 1 "current window" cho mọi lệnh (execute_script,
    # execute_cdp_cmd, find_element...) tại 1 thời điểm — driver.switch_to.window(handle)
    # đổi window đó cho TẤT CẢ lệnh tiếp theo, kể cả CDP passthrough (_cdp/_cdp_click_el
    # dùng execute_cdp_cmd, tự động theo đúng window đang active). Vì vậy để N
    # conversation Gemini cùng tiến triển "song song" trong 1 phiên trình duyệt DUY
    # NHẤT, worker phải LUÂN PHIÊN switch focus qua từng tab, làm 1 bước nhỏ (kiểm tra
    # không-block), rồi chuyển tab kế — không có gì thật sự chạy "cùng lúc" theo nghĩa
    # đa luồng, nhưng vì các bước chờ dài nhất (đợi Gemini generate, đợi xác nhận đính
    # kèm) đều là CHỜ PHÍA SERVER/DOM (không cần driver làm gì), việc bỏ tab A đứng chờ
    # 1 nhịp trong lúc phục vụ tab B/C không có phí tổn thật — N task hoàn tất trong
    # khoảng THỜI GIAN CHỜ DÀI NHẤT + N × (thời gian mỗi bước nhỏ), gần như N lần
    # nhanh hơn chạy tuần tự.

    @staticmethod
    def _pending_item_key(item: dict):
        """Khoá nhận diện 1 pendingPrompt item — dùng để phát hiện trùng lặp trong
        CÙNG 1 lô heartbeat (2026-07-25, xem `_run_gemini_loop_concurrent`). Trả
        `None` nếu không đủ thông tin (thiếu `field`) để nhận diện — item đó luôn
        coi là duy nhất, không bị lọc, tránh lọc nhầm dữ liệu thiếu meta.

        2026-07-25 (fix bug thật user báo "request thì nhiều, setting 2 tab, mà 1
        tab ko làm gì, chạy có 1 tab"): khoá BAN ĐẦU chỉ có `(field, target)` —
        với field họ `*_scene_batch`, backend giờ tạo SẴN nhiều request KHÁC NHAU
        cho CÙNG 1 script/clip (mỗi request 1 khoảng batchFrom-batchTo riêng, xem
        `_dispatch_all_scene_batches` ở backend `ToolSub` gốc, đổi cùng ngày) —
        khoá thiếu `batchFrom`/`batchTo` khiến 2 batch THẬT SỰ KHÁC NHAU bị coi là
        "trùng", batch thứ 2 trở đi bị lọc bỏ oan, chỉ còn 1 tab có việc dù server
        đã giao đủ cho cả 2. Thêm `batchFrom`/`batchTo` vào khoá — field không
        phải scene_batch (draft/full_script/rewrite_storyboard) luôn có 2 giá trị
        này là `None` như nhau nên hành vi dedupe cũ (1 việc/field/target) không
        đổi, chỉ scene_batch mới thật sự phân biệt được theo khoảng batch."""
        meta = item.get('meta') or {}
        field = meta.get('field')
        if not field:
            return None
        target = meta.get('scriptId') or meta.get('clipId') or meta.get('projectId')
        return (field, target, meta.get('batchFrom'), meta.get('batchTo'))

    def _gemini_open_tab(self) -> str:
        """Mở 1 tab MỚI trong CÙNG phiên trình duyệt hiện có (Selenium 4
        `switch_to.new_window`), trả về window handle của tab vừa mở (đã tự động
        switch focus sang tab đó)."""
        self.driver.switch_to.new_window('tab')
        return self.driver.current_window_handle

    def _gemini_close_tab(self, handle: str):
        """Đóng 1 tab, rồi chuyển focus về tab còn lại bất kỳ — tránh driver kẹt focus
        vào 1 handle đã đóng (lệnh kế tiếp trên nó sẽ lỗi 'no such window').

        ⚠️ (2026-08-30, BUG THẬT — nguyên nhân "xong batch là Chrome tự đóng, worker
        restart liên tục, kết quả không lên server") Đóng tab CUỐI CÙNG = đóng luôn
        CỬA SỔ CUỐI của Chrome → trình duyệt THOÁT HẲN → mọi lệnh sau đó lỗi
        `invalid session id`, `_is_driver_alive()` false → `_run_gemini_loop_concurrent`
        thoát vòng lặp ("Browser đã đóng — thoát worker loop") → worker stopped →
        auto-scale mở lại → lặp vô tận, MỖI batch 1 vòng. Lộ ra từ 2026-07-25 khi
        tab khởi động được TÁI SỬ DỤNG làm slot đầu tiên (trước đó tab khởi động
        luôn còn lại nên browser không bao giờ hết tab). Fix: nếu đây là tab cuối
        cùng, MỞ 1 tab giữ chỗ TRƯỚC rồi mới đóng — phiên trình duyệt sống tiếp,
        vòng lặp tiếp tục xin lô mới bình thường.

        Tab giữ chỗ này sẽ được TÁI SỬ DỤNG làm slot của lô kế tiếp (xem
        `_run_gemini_loop_concurrent` — `startup_tab_handle` được gán lại), nên
        không tích tụ tab rác qua các lô."""
        try:
            handles = self.driver.window_handles
        except Exception:
            handles = []
        if len(handles) <= 1 and handle in handles:
            try:
                self.driver.switch_to.new_window('tab')
            except Exception as e:
                self._log('warn', f'Không mở được tab giữ chỗ trước khi đóng tab cuối: {e}')
        try:
            self.driver.switch_to.window(handle)
            self.driver.close()
        except Exception:
            pass
        remaining = []
        try:
            remaining = self.driver.window_handles
        except Exception:
            pass
        if remaining:
            try:
                self.driver.switch_to.window(remaining[0])
            except Exception:
                pass

    def _gemini_slot_finish(self, slot: dict, slot_idx: int, result: dict = None, error: str = None):
        """Gửi callback (thành công hoặc lỗi) cho 1 slot, dọn file tạm nếu có, đánh
        dấu slot 'done' — round-robin loop sẽ đóng tab + giải phóng slot ngay sau khi
        thấy trạng thái này. `result` = `{'text','images'}` (2026-08-01, xem
        `_gemini_response_if_ready`)."""
        pending      = slot['pending']
        callback_url = pending.get('callbackUrl')
        if callback_url:
            payload = {'prompt': pending.get('prompt') or '', 'meta': pending.get('meta') or {},
                       'ts': int(time.time() * 1000)}
            if error:
                payload['error'] = error
            else:
                payload['response'] = (result or {}).get('text') or ''
                if (result or {}).get('images'):
                    payload['images'] = result['images']
            try:
                resp = req_lib.post(callback_url, json=payload, timeout=30)
                # (2026-08-30) TRƯỚC ĐÂY log ✔ cho MỌI phản hồi 200 — nhưng backend
                # trả `{"skipped": true, "reason": "stale_assignment"}` (vẫn HTTP 200)
                # khi request đã bị reclaim/gán lại: kết quả BỊ VỨT, clip không có dữ
                # liệu, mà log lại báo thành công → cực khó lần ra. Giờ đọc body và
                # log WARN rõ ràng cho case đó.
                try:
                    data = resp.json() if resp is not None else {}
                except Exception:
                    data = {}
                if isinstance(data, dict) and data.get('skipped'):
                    self._log('warn', f'[slot {slot_idx}] ⚠ Server BỎ QUA kết quả này '
                                       f'(reason={data.get("reason") or "?"}) — kết quả KHÔNG '
                                       f'được lưu. Thường do request đã bị thu hồi (máy mất '
                                       f'heartbeat quá 30s) rồi giao lại; xem log reaper phía backend.')
                else:
                    self._log('info', f'[slot {slot_idx}] callback → {callback_url} ✔')
            except Exception as e:
                self._log('warn', f'[slot {slot_idx}] callback lỗi: {e}')
        for p in (slot.get('tmp_paths') or []):
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        slot['state'] = 'done'

    def _gemini_slot_download_and_kickoff(self, slot: dict, slot_idx: int, idx: int) -> None:
        """Tải ảnh thứ `idx` trong `slot['image_urls']` rồi kickoff attach — dùng
        chung bởi state 'new' (ảnh đầu) VÀ state 'attaching' (2026-08-04, ảnh thứ
        2 trở đi — fix bug thật "upload 2 ảnh tham chiếu nhưng qua chatgpt/gemini
        chỉ thấy đính kèm có 1 ảnh"). Thư mục con RIÊNG theo slot (khác
        `_run_task_gemini`'s profile_{id}/ — ở đó chỉ 1 task/lần nên không cần) —
        nhiều slot có thể tải file TRÙNG basename cùng lúc, phải tách thư mục để
        không đè lên nhau; thêm tiền tố index phòng 2 ảnh CÙNG 1 slot trùng tên."""
        url = slot['image_urls'][idx]
        self._log('info', f'[slot {slot_idx}] Đang tải file đính kèm {idx+1}/{len(slot["image_urls"])} '
                           f'từ server ({url})…')
        resp = req_lib.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        orig_name = _normalize_attach_filename(os.path.basename(url.split('?')[0]) or f'attach_{idx}.jpg')
        filename  = orig_name if idx == 0 else f'{idx}_{orig_name}'
        slot_tmp_dir = VIDEO_TMP_DIR / f'profile_{self.profile_id}' / f'slot_{slot_idx}'
        slot_tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = str(slot_tmp_dir / filename)
        with open(tmp_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        slot['tmp_paths'].append(tmp_path)
        size_kb = os.path.getsize(tmp_path) // 1024
        self._log('ok', f'[slot {slot_idx}] ✔ Đã tải file đính kèm {idx+1}/{len(slot["image_urls"])} ({size_kb} KB)')

        slot['attach_before_count'] = self._gemini_attach_file_kickoff(tmp_path)
        slot['attach_deadline'] = time.time() + slot['attach_timeout']

    def _gemini_slot_step(self, slot: dict, slot_idx: int):
        """Tiến 1 bước NHỎ, KHÔNG-BLOCKING cho 1 slot (tab đã switch focus tới ở đây,
        không phải ở caller — mọi exception kể cả switch_to.window() thất bại đều được
        bắt tại đây, coi như lỗi task, KHÔNG làm chết round-robin loop của các slot
        khác). State machine mirror ĐÚNG trình tự `_run_task_gemini()` (luồng 1-tab
        tuần tự): new → [attaching, LẶP LẠI tuần tự cho TỪNG ảnh trong `imageUrls`
        nếu có — 2026-08-04] → submitting → waiting_response → done. Mỗi state chỉ
        làm ĐÚNG 1 việc nhỏ rồi return — vòng lặp gọi lại hàm này mỗi lần quay lại
        tab đó."""
        try:
            self.driver.switch_to.window(slot['handle'])
            state   = slot['state']
            pending = slot['pending']

            if state == 'new':
                self._nav_get(self.GEMINI_URL, f'[slot {slot_idx}] mở chat Gemini mới cho task')
                self._sleep(2)
                self._gemini_select_model((pending.get('meta') or {}).get('geminiModel'))

                video_url = pending.get('videoUrl')
                image_urls = pending.get('imageUrls') or ([video_url] if video_url else [])
                image_urls = list(dict.fromkeys(image_urls))  # dedup theo URL — xem _run_task_gemini()
                slot['image_urls'] = image_urls
                slot['image_idx']  = 0
                slot['tmp_paths']  = []
                if image_urls:
                    self._gemini_slot_download_and_kickoff(slot, slot_idx, 0)
                    slot['state'] = 'attaching'
                elif (self._gemini_rpc_enabled(False)
                        and self._gemini_rpc_kickoff(pending.get('prompt') or '',
                                                     key=f'slot{slot_idx}')):
                    # (2026-09-06) Gửi ẩn qua RPC — KHÔNG chờ ở đây: `fetch` chỉ
                    # resolve khi Gemini sinh xong (vài chục giây), chờ blocking
                    # sẽ đóng băng cả vòng round-robin. Poll ở state kế tiếp.
                    slot['rpc_key']      = f'slot{slot_idx}'
                    slot['rpc_deadline'] = time.time() + slot['response_timeout']
                    slot['state']        = 'rpc_sending'
                else:
                    self._gemini_type_prompt(pending.get('prompt') or '')
                    slot['submit_deadline'] = time.time() + slot['attach_timeout']
                    slot['state'] = 'submitting'

            elif state == 'attaching':
                if self._gemini_attach_file_poll(slot['attach_before_count']):
                    slot['image_idx'] += 1
                    if slot['image_idx'] < len(slot['image_urls']):
                        # Còn ảnh tiếp theo trong hàng đợi — tải + kickoff attach ảnh
                        # kế, VẪN Ở state 'attaching' (poll lần tới sẽ chờ ảnh MỚI này).
                        self._gemini_slot_download_and_kickoff(slot, slot_idx, slot['image_idx'])
                    else:
                        self._gemini_type_prompt(pending.get('prompt') or '')
                        slot['submit_deadline'] = time.time() + slot['attach_timeout']
                        slot['state'] = 'submitting'
                elif time.time() > slot['attach_deadline']:
                    raise RuntimeError(
                        f'Timeout {slot["attach_timeout"]}s: không xác nhận được file đã đính kèm vào Gemini'
                    )

            elif state == 'rpc_sending':
                # Gửi ẩn đang chạy — poll KHÔNG-BLOCKING, nhường lượt cho tab khác.
                try:
                    res = self._gemini_rpc_poll(slot['rpc_key'])
                except RuntimeError as exc:
                    # Không bao giờ làm hỏng task vì đường mới: rơi về gõ DOM.
                    self._log('warn', f'[slot {slot_idx}] {exc} — rơi về gõ DOM')
                    self._gemini_type_prompt(pending.get('prompt') or '')
                    slot['submit_deadline'] = time.time() + slot['attach_timeout']
                    slot['state'] = 'submitting'
                    return
                if res:
                    self._log('ok', f"[slot {slot_idx}] [gemini-rpc] ✔ đã trả lời "
                                    f"(conv={res['conv'][0]}) — mở hội thoại để đọc DOM")
                    self._gemini_rpc_open_conversation(res['conv'][0])
                    slot['response_deadline'] = time.time() + slot['response_timeout']
                    slot['state'] = 'waiting_response'
                elif time.time() > slot['rpc_deadline']:
                    raise RuntimeError(
                        f'Timeout {slot["response_timeout"]}s: gửi ẩn qua RPC không có phản hồi'
                    )

            elif state == 'submitting':
                if self._gemini_submit_if_ready():
                    self._log('info', f'[slot {slot_idx}] Gemini prompt submitted '
                                       f'({len(pending.get("prompt") or "")} chars)')
                    slot['response_deadline'] = time.time() + slot['response_timeout']
                    slot['state'] = 'waiting_response'
                elif time.time() > slot['submit_deadline']:
                    raise RuntimeError(
                        f'Timeout {slot["attach_timeout"]}s: nút Gửi không sẵn sàng '
                        f'(có thể Gemini chưa nhận xong file đính kèm)'
                    )

            elif state == 'waiting_response':
                result = self._gemini_response_if_ready(slot.get('response_stability'))
                if result:
                    text = result.get('text') or ''
                    n_img = len(result.get('images') or [])
                    self._log('ok', f'[slot {slot_idx}] ✔ Nhận response {len(text)} ký tự'
                                     + (f' + {n_img} ảnh' if n_img else ''))
                    self._gemini_slot_finish(slot, slot_idx, result=result)
                elif time.time() > slot['response_deadline']:
                    raise RuntimeError(f'Timeout: Gemini không phản hồi sau {slot["response_timeout"]}s')

        except Exception as e:
            self._log('error', f'[slot {slot_idx}] Task lỗi: {e}')
            self._gemini_slot_finish(slot, slot_idx, error=str(e))

    def _run_gemini_loop_concurrent(self, max_tabs: int):
        """Round-robin nhiều tab Gemini — nhận 1 LÔ tối đa `max_tabs` prompt cùng
        lúc (mỗi prompt 1 tab riêng), lần lượt switch_to.window() giữa các tab (chờ
        `gemini_tab_switch_interval` giây mỗi lần chuyển: tab 1 → 2 → 3 → về 1, lặp
        lại) để TẤT CẢ cùng tiến triển trong 1 phiên trình duyệt — CHỈ khi TOÀN BỘ lô
        hiện tại xong (mọi slot 'done') mới heartbeat xin lô tiếp theo, đúng yêu cầu
        "setting 3 tab thì nhận 1 lần 3 task, hoàn tất đủ 3 task thì nhận tiếp".
        Giữa chừng (còn slot chưa xong) chỉ heartbeat với `keepalive_only=True` (giữ
        last_seen tươi, KHÔNG xin việc mới — xem docstring `_heartbeat_gemini`) — nhờ
        đó không cần thread keepalive riêng như luồng 1-tab tuần tự (`_run_task_gemini`),
        vì vòng lặp round-robin bản thân nó không bao giờ bị block quá `POLL_INTERVAL`."""
        interval         = float(self.profile.get('gemini_tab_switch_interval') or 0.5)
        attach_timeout   = int(self.profile.get('gemini_attach_timeout')   or 180)
        response_timeout = int(self.profile.get('gemini_response_timeout') or 300)

        self._log('info', f'Worker starting (Gemini Chat mode — {max_tabs} tab đồng thời, '
                           f'giãn cách {interval}s)')
        pm.set_status(self.profile_id, 'idle', pid=threading.get_ident())
        slots: list = [None] * max_tabs
        # ⚠️ (2026-08-30, BUG THẬT — "phân tích video có trả về mà dữ liệu không cập
        # nhật lên frontend") Keepalive CŨ chỉ gửi ở CUỐI mỗi vòng lặp round-robin;
        # 1 bước slot có thể block LÂU HƠN 30s (tải video 100MB+ rồi `send_keys`
        # đính kèm, `attach_timeout` tới 180s) → server không thấy heartbeat >30s →
        # đánh dấu máy `offline` → `_reap_stuck_gemini_requests` reclaim NGAY request
        # đang xử lý dở (đổi `assign_token`) → callback về sau bị coi là "stale
        # assignment" và BỊ VỨT (`gemini_callback` trả `{skipped:true}`, HTTP 200 nên
        # client vẫn log ✔) → clip mãi không có dữ liệu dù Gemini đã trả lời xong.
        # Thread nền dưới đây gửi heartbeat ĐỀU ĐẶN mỗi POLL_INTERVAL bất kể vòng lặp
        # chính đang block ở đâu — cùng cơ chế `_run_task_gemini` (1-tab) đã dùng từ
        # 2026-07-06. `keepalive_only=True` nên KHÔNG bao giờ tự nhận thêm việc.
        stop_keepalive = threading.Event()

        def _keepalive_loop():
            while not stop_keepalive.wait(POLL_INTERVAL):
                busy = sum(1 for s in slots if s is not None)
                if busy:
                    self._heartbeat_gemini(running=busy, max_concurrent=max_tabs,
                                           keepalive_only=True)

        keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True)
        keepalive_thread.start()
        try:
            self.driver = self._make_driver()
            # (2026-08-17, fix bug thật user báo "chỉ mở profile trắng ko vào
            # gemini" — test THẬT trên máy khác, log dừng NGAY sau dòng "sẵn
            # sàng nhận prompt (round-robin)", không có gì tiếp theo) — TRƯỚC
            # ĐÂY hàm này mở Chrome rồi log "sẵn sàng" NGAY, KHÔNG BAO GIỜ
            # navigate đi đâu cả — tab khởi động vẫn là trang trắng mặc định
            # (`chrome://new-tab-page/`) cho tới khi lô task ĐẦU TIÊN tới,
            # lúc đó `_gemini_slot_step()`'s state='new' mới `driver.get(
            # GEMINI_URL)` (KHÔNG check đăng nhập, xem docstring hàm đó) —
            # nếu chưa có task nào, browser cứ đứng trắng vô thời hạn, đúng
            # y hệt hiện tượng user mô tả. Khác hẳn luồng 1-tab tuần tự
            # (`_run_gemini_loop`) đã navigate + `_ensure_google_login()` NGAY
            # sau khi mở Chrome — round-robin bị SÓT bước này hoàn toàn. Fix:
            # gọi `_ensure_google_login(GEMINI_URL)` NGAY ở đây (1 LẦN DUY
            # NHẤT lúc khởi động, không phải mỗi lần chuyển tab nên không ảnh
            # hưởng nhịp round-robin) — vừa cho phản hồi/log sớm về trạng thái
            # đăng nhập (khớp trải nghiệm luồng tuần tự), vừa đảm bảo tab đầu
            # tiên đã thật sự ở gemini.google.com/app trước khi vào vòng lặp
            # chờ task. Các tab MỞ SAU (`_gemini_open_tab()`, slot thứ 2 trở
            # đi) dùng CHUNG session/cookie Chrome với tab này nên không cần
            # đăng nhập lại — `driver.get(GEMINI_URL)` blind ở `_gemini_slot_step`
            # vẫn an toàn cho các tab đó.
            if self._ensure_google_login(self.GEMINI_URL):
                self._log('ok', 'Chrome opened — sẵn sàng nhận prompt (round-robin)')
            else:
                self._log('error', 'Đăng nhập Google thất bại/chưa cấu hình — worker vẫn '
                                    'chạy nhưng MỌI task sẽ lỗi tới khi khắc phục (xem log '
                                    '[google-login] ở trên).')
            # (2026-07-25, theo yêu cầu user "khi khởi động đã có sẵn 1 tab thì
            # nếu chạy 2 tab mở thêm 1 tab là đủ, chứ để không tab đầu làm gì")
            # — TÁI SỬ DỤNG tab Chrome hiện tại (đã navigate xong ở bước trên)
            # làm slot ĐẦU TIÊN thay vì luôn mở tab MỚI cho MỌI slot (bug cũ:
            # mở đủ max_tabs tab mới, bỏ hoang tab khởi động — vừa lãng phí
            # vừa trông như "1 tab không làm gì"). Đọc `current_window_handle`
            # SAU `_ensure_google_login()` (không phải trước) — hàm đó có thể
            # đóng tab gốc + mở tab MỚI khi cần đăng nhập lại (xem docstring),
            # đọc quá sớm sẽ bắt nhầm handle của tab đã bị đóng. Chỉ dùng 1
            # LẦN — sau lô đầu tiên, MỌI tab (kể cả tab tái sử dụng này) đều bị
            # đóng khi xong batch (`_gemini_close_tab`), nên các lô sau không
            # còn tab "mồ côi" nào.
            startup_tab_handle = self.driver.current_window_handle

            while not self._stop.is_set():
                if not self._is_driver_alive():
                    self._log('warn', 'Browser đã đóng — thoát worker loop')
                    break

                active_idx = [i for i, s in enumerate(slots) if s is not None]
                if not active_idx:
                    pending_list = self._heartbeat_gemini(running=0, max_concurrent=max_tabs)
                    if not pending_list:
                        self._stop.wait(POLL_INTERVAL)
                        continue
                    # 2026-07-25 (bug thật user báo "setting 2 tab nhưng chỉ có 1 task
                    # thì mở 1 tab thôi" — server đôi khi trả 2 pendingPrompt cho CÙNG
                    # 1 script/clip do dispatch trùng, xem lớp bug "pending_prompt mồ
                    # côi/dispatch trùng" ở ToolSub gốc) — lọc trùng NGAY TRƯỚC KHI mở
                    # tab: 2 item cùng (field, scriptId/clipId/projectId) chỉ giữ lại
                    # item ĐẦU, đảm bảo KHÔNG BAO GIỜ mở nhiều tab hơn số việc THẬT SỰ
                    # khác nhau, bất kể server có lỡ giao trùng hay không.
                    seen_keys, deduped = set(), []
                    for item in pending_list:
                        key = self._pending_item_key(item)
                        if key is not None:
                            if key in seen_keys:
                                self._log('warn', f'[gemini] Bỏ qua pendingPrompt trùng trong '
                                                   f'cùng lô (field={key[0]}, target={key[1]}) — '
                                                   f'tránh mở tab thừa cho cùng 1 việc')
                                continue
                            seen_keys.add(key)
                        deduped.append(item)
                    pending_list = deduped
                    if not pending_list:
                        self._stop.wait(POLL_INTERVAL)
                        continue
                    self._log('info', f'← Nhận lô {len(pending_list)}/{max_tabs} task — mở tab round-robin')
                    for i, pending in enumerate(pending_list[:max_tabs]):
                        if startup_tab_handle is not None:
                            handle = startup_tab_handle
                            startup_tab_handle = None
                        else:
                            handle = self._gemini_open_tab()
                        slots[i] = {
                            'handle': handle, 'state': 'new', 'pending': pending,
                            'attach_before_count': None, 'attach_deadline': None,
                            'submit_deadline': None, 'response_deadline': None,
                            'image_urls': [], 'image_idx': 0, 'tmp_paths': [],
                            'attach_timeout': attach_timeout,
                            'response_timeout': response_timeout,
                            # (2026-09-01) Dict RIÊNG mỗi slot cho cơ chế xác
                            # nhận ổn định của `_gemini_response_if_ready()` —
                            # KHÔNG dùng chung 1 dict cho nhiều slot (mỗi tab
                            # là 1 conversation độc lập, ứng viên nội dung của
                            # tab này không liên quan gì tab khác).
                            'response_stability': {},
                        }
                    active_idx = [i for i, s in enumerate(slots) if s is not None]
                    # 2026-07-24 (bug thật user báo "1 tab chạy xong thì đóng tất cả
                    # profile nhưng không update kịch bản lên server"): trước đây hàm
                    # này CHỈ set 'idle' MỘT LẦN lúc mới start, không bao giờ báo
                    # 'processing' khi slot có việc — `_auto_scale_gemini_tick()`
                    # (dispatcher.py::_gemini_profile_busy) đọc đúng cột `status` này
                    # để biết KHÔNG được đóng profile đang xử lý dở; vì cột luôn kẹt ở
                    # 'idle', auto-scale tưởng profile rảnh ngay khi backlog vừa cạn
                    # (chỉ cần 1 slot còn việc thật, các slot khác rỗng) và `_stop_worker()`
                    # NGAY GIỮA CHỪNG — Chrome bị đóng trước khi `_gemini_slot_finish()`
                    # kịp POST callback, kịch bản/clip mất kết quả dù đã generate xong.
                    pm.set_status(self.profile_id, 'processing', pid=threading.get_ident())

                for i in active_idx:
                    if self._stop.is_set():
                        break
                    slot = slots[i]
                    if slot is None or slot['state'] == 'done':
                        continue
                    self._gemini_slot_step(slot, i)
                    self._stop.wait(interval)

                # Dọn slot vừa xong (đóng tab, giải phóng) — vòng kế tiếp mới thấy
                # active_idx rỗng và xin lô mới, nếu ĐÂY là slot cuối cùng còn lại.
                for i in active_idx:
                    slot = slots[i]
                    if slot is not None and slot['state'] == 'done':
                        self._gemini_close_tab(slot['handle'])
                        slots[i] = None

                if not any(s is not None for s in slots):
                    # Toàn bộ lô vừa xong (mọi slot đã đóng) — báo lại 'idle' NGAY, để
                    # auto-scale biết profile thật sự rảnh (không phải kẹt giả từ trước).
                    pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())
                    # (2026-08-30) Tab GIỮ CHỖ còn lại sau khi đóng tab slot cuối (xem
                    # `_gemini_close_tab` — mở tab giữ chỗ để Chrome không thoát) được
                    # TÁI SỬ DỤNG làm slot đầu của lô kế tiếp, y hệt tab khởi động ban
                    # đầu. Không làm bước này thì mỗi lô để lại 1 tab mồ côi, tích tụ dần.
                    try:
                        handles = self.driver.window_handles
                        if handles:
                            startup_tab_handle = handles[0]
                            self.driver.switch_to.window(startup_tab_handle)
                            # (2026-09-13, KHÔI PHỤC + GENERIC HOÁ theo yêu cầu
                            # user "clear cookie như veo") — trước đây bỏ hẳn ở
                            # ĐÂY vì `_cdp_clear_cache_and_cookies()` chỉ dọn
                            # domain `labs.google` (vô nghĩa cho Gemini) VÀ hay
                            # ném log `invalid session id` khi Chrome vừa thoát
                            # (xem CHANGELOG 2026-08-30). Giờ hàm đó tra domain
                            # ĐÚNG theo `worker_mode` (`gemini.google.com` cho
                            # nhóm Gemini) nên KHÔNG còn vô nghĩa; gọi TRƯỚC khi
                            # navigate tab nghỉ về Gemini để lần navigate ngay
                            # dưới tự re-auth qua SSO (session Google cấp tài
                            # khoản trên `.google.com` không bị đụng) — tab nghỉ
                            # hiện ĐÚNG trạng thái đã đăng nhập lại, không phải
                            # trang chưa đăng nhập. `invalid session id` (nếu
                            # còn tái diễn) vẫn chỉ là warning best-effort, không
                            # chặn batch tiếp theo.
                            self._cdp_clear_cache_and_cookies()
                            # (2026-08-31, user báo "profile đang mở TRANG TRẮNG
                            # không vào gemini") — tab giữ chỗ do
                            # `_gemini_close_tab()` mở là `chrome://new-tab-page`
                            # nên lúc RẢNH giữa 2 lô cửa sổ trông như "chết", dù
                            # worker vẫn heartbeat và lô sau vẫn chạy đúng
                            # (state 'new' của `_gemini_slot_step` tự
                            # `driver.get(GEMINI_URL)`). Đưa tab nghỉ về thẳng
                            # gemini.google.com: (1) nhìn là biết profile còn
                            # sống/còn đăng nhập, (2) giữ session Google được
                            # "chạm" định kỳ thay vì nằm im ở trang trắng,
                            # (3) lô kế tiếp đỡ 1 lần điều hướng nguội.
                            try:
                                if not (self.driver.current_url or '').startswith(self.GEMINI_URL):
                                    self._nav_get(self.GEMINI_URL, 'đưa tab nghỉ về Gemini giữa 2 lô')
                            except Exception as nav_exc:
                                self._log('warn', f'Không đưa tab nghỉ về Gemini được: {nav_exc}')
                    except Exception:
                        startup_tab_handle = None

                # (2026-08-30) Keepalive giữa chừng giờ do THREAD NỀN lo (xem
                # `_keepalive_loop` ở đầu hàm) — vòng lặp chính không còn tự gửi,
                # tránh phụ thuộc vào việc 1 vòng lặp có kịp chạy trong 30s hay không.

        except Exception as e:
            self._log('error', f'Worker fatal (gemini concurrent): {e}')
        finally:
            stop_keepalive.set()
            for slot in slots:
                if slot is not None:
                    try: self._gemini_close_tab(slot['handle'])
                    except Exception: pass
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            if not self._clear_profile_if_sleeping(pm.get(self.profile_id)):
                pm.set_status(self.profile_id, 'offline', clear_task=True, pid=None)
            self._log('info', 'Worker stopped')

    # ══════════════════════════════════════════════════════════════════════════
    # ChatGPT (2026-08-01) — worker_mode='chatgpt': chat + upload ảnh, mirror
    # KIẾN TRÚC của Gemini ở trên (attach → gõ prompt → gửi → chờ phản hồi →
    # callback), khác selector DOM vì là trang khác (chatgpt.com). Sequential
    # 1-tab CHỈ (chưa có round-robin nhiều tab như
    # `_run_gemini_loop_concurrent()` — có thể thêm sau theo cùng khuôn mẫu nếu
    # cần, xem client_tool/CLAUDE.md).
    #
    # Selector xác nhận trực tiếp qua DevTools thật (2026-08-01, KHÔNG suy đoán),
    # QUA 2 TÀI KHOẢN KHÁC NHAU (Claude-in-Chrome extension VÀ Selenium thật) —
    # phát hiện 1 khác biệt quan trọng giữa 2 tài khoản, xem ngay dưới:
    #   - Composer: `#prompt-textarea` (div contenteditable, role=textbox).
    #   - Input ảnh: `#upload-files` (KHÔNG PHẢI `#upload-photos` như quan sát ban
    #     đầu qua tài khoản 1 — xem `_CHATGPT_UPLOAD_IDS` bên dưới, ID nào thực sự
    #     nhận file KHÔNG ổn định giữa tài khoản/phiên, đã verify trực tiếp bằng
    #     Selenium thật + fix). CHATGPT RENDER SẴN các input này trong DOM NGAY TỪ
    #     ĐẦU (khác Gemini — không cần mở menu "+" trước mới có input để
    #     send_keys()).
    #   - Nút Gửi: `#composer-submit-button`, `data-testid` đổi giữa
    #     "send-button" (rảnh, click được) ↔ "stop-button" (đang generate) — dùng
    #     CHÍNH testid này làm tín hiệu is-generating (không có bẫy "luôn visible"
    #     như Gemini, không cần workaround).
    #   - Response: `[data-testid^="conversation-turn-"]` (mỗi lượt chat 1 turn),
    #     lượt cuối có `[data-message-author-role="assistant"]`; text trong
    #     `.markdown`; ảnh AI tạo ra là `<img alt="Generated image: ...">` bên
    #     trong cùng turn — dùng CHUNG `_extract_response_images_base64()`.
    #
    # ✅ Test thật QUA Claude-in-Chrome (tài khoản ChatGPT ĐÃ ĐĂNG NHẬP thật) —
    # prompt "Tạo cho tôi 1 bức ảnh duy nhất gồm 12 frame quy trình nấu bánh quy"
    # → tạo ảnh thành công (12-frame, ~10s) + hỏi tiếp "Tóm tắt bằng 1 câu ảnh vừa
    # tạo" → trả lời text đúng — CẢ 2 luồng (ảnh lẫn text) đều hoạt động, xác nhận
    # ĐÚNG cấu trúc DOM ở trên (thao tác chuột/bàn phím THẬT, không phải Selenium).
    #
    # ✅ Test thật QUA Selenium (`SeleniumFlowWorker` thật, KHÔNG mock gì) — SAU KHI
    # user tự đăng nhập chatgpt.com vào ĐÚNG profile "khanh" (client_tool profile
    # Chrome riêng, KHÁC trình duyệt thường dùng hàng ngày — user ban đầu đăng nhập
    # nhầm chỗ, tưởng đã login nhưng thật ra vẫn ở chế độ khách trên profile này;
    # `document.body.innerText` dump ra "Log in to get answers..." + `turns=0` sau
    # khi gửi prompt là bằng chứng — đã sửa bằng cách MỞ ĐÚNG cửa sổ Chrome của
    # profile automation này, để user đăng nhập trực tiếp vào đó):
    #   - 1 ẢNH đính kèm + prompt tạo ảnh "12 frame quy trình..." → THÀNH CÔNG HOÀN
    #     TOÀN qua đúng `_chatgpt_attach_file()`/`_chatgpt_fill_and_submit()`/
    #     `_chatgpt_wait_response()` (không mock) — nhận đúng 1 ảnh base64 (~3.6MB,
    #     sau khi fix dedupe — xem `_extract_response_images_base64()`), text rỗng
    #     (đúng — ChatGPT chỉ trả ảnh cho prompt gen thuần, không kèm caption).
    #   - Bug thật bắt được + fix ngay trong lúc test: `#upload-photos` (đúng với
    #     tài khoản 1 ở trên) hoàn toàn KHÔNG nhận file trên tài khoản 2
    #     (`.files.length` đứng yên ở 0 dù send_keys() không lỗi) — chỉ
    #     `#upload-files` mới nhận đúng → sửa thử LẦN LƯỢT cả 2 candidate
    #     (`_CHATGPT_UPLOAD_IDS`), không hardcode 1 id.
    #   - Bug thật thứ 2: gửi NGAY sau khi `.files.length`/thumbnail xác nhận (0s
    #     chờ) → composer bị xoá (trông như gửi thành công) nhưng KHÔNG có
    #     conversation turn nào xuất hiện — tin nhắn bị ChatGPT ÂM THẦM DROP toàn
    #     bộ (nút Gửi KHÔNG hề disable trong lúc ChatGPT còn upload file lên server
    #     ở background, khác hẳn Gemini). Fix: thêm 6s chờ SAU KHI xác nhận local
    #     trước khi coi attach xong (`_chatgpt_attach_file`).
    #
    # ⚠️ "GIỚI HẠN 2 ẢNH" Ở TRÊN — KẾT LUẬN CŨ (product-level limit) NHIỀU KHẢ
    # NĂNG LÀ CHẨN ĐOÁN SAI, đã tìm ra root cause khác + fix (2026-08-04):
    # User báo storyboard #46 (2 ảnh tham chiếu CHAR+BG, xác nhận qua SHA256 —
    # nội dung HOÀN TOÀN khác nhau, KHÔNG trùng byte) bị ChatGPT báo lỗi rõ ràng
    # **"Bạn đã upload ảnh này"** ngay khi đính kèm ảnh thứ 2 — nhưng USER TỰ TAY
    # chọn CÙNG 2 file đó qua dialog thật (chọn cả 2 cùng lúc) lại hoạt động
    # bình thường. Vậy KHÔNG PHẢI giới hạn phía OpenAI (nếu là giới hạn sản
    # phẩm, thao tác thủ công cũng phải bị chặn y hệt) — mà là do
    # `_run_task_chatgpt()` cũ gọi `_chatgpt_attach_file()` RIÊNG cho từng ảnh
    # (N lần `send_keys()` liên tiếp trên CÙNG 1 input TĨNH `#upload-files`,
    # cách nhau vài giây) — khác hẳn thao tác thủ công (1 lần chọn multi-file
    # qua dialog = 1 sự kiện `change` duy nhất). Nghi ChatGPT có cơ chế chống-
    # trùng dựa theo tín hiệu KHÁC nội dung byte (session/token upload nội bộ)
    # và bị false-positive khi thấy 2 lần chọn-file liên tiếp trên cùng input.
    # FIX: `_chatgpt_attach_files()` (list, thay `_chatgpt_attach_file()` đơn lẻ
    # trong vòng lặp) — gộp TẤT CẢ path vào ĐÚNG 1 lệnh `send_keys('\n'.join(...))`
    # duy nhất, mô phỏng đúng hành vi multi-select thủ công. `_run_task_chatgpt()`
    # đã cập nhật gọi hàm mới NÀY (download xong hết rồi mới attach 1 lần, không
    # còn attach xen kẽ từng ảnh trong vòng lặp download nữa).
    # ⚠️ CHƯA verify lại trên browser thật sau fix này (task test trước đó đã
    # timeout 300s trước khi fix kịp deploy) — cần RESTART client_tool (đóng mở
    # lại `main.py`, code không hot-reload) rồi bấm "Chạy lại" trên storyboard
    # bị lỗi để xác nhận dứt điểm.
    # ══════════════════════════════════════════════════════════════════════════

    def _chatgpt_find(self, selector: str):
        return self._js("return document.querySelector(arguments[0]);", selector)

    # (2026-08-01) ID input NÀO thực sự nhận file KHÔNG ổn định giữa các tài
    # khoản/phiên — verify trực tiếp qua Selenium thật (2 lần test riêng biệt):
    # tài khoản test 1 (qua Claude-in-Chrome extension) có `#upload-photos`
    # (accept="image/*") RIÊNG biệt với `#upload-files` (accept rỗng); tài khoản
    # test 2 (qua Selenium thật, profile "khanh") `#upload-photos` HOÀN TOÀN
    # không nhận file dù send_keys() không raise lỗi gì (`.files.length` đứng
    # yên ở 0 suốt >4s dù đã thử cả 2 đường: gọi thẳng VÀ click qua menu
    # "+"/"Add photos & files" trước) — chỉ `#upload-files` (accept lúc đó lại
    # CHÍNH LÀ danh sách ảnh, khác hẳn lúc rỗng ở tài khoản 1) mới thực sự set
    # được `.files.length=1` + xuất hiện thumbnail preview trong composer.
    # Kết luận: KHÔNG hardcode 1 id — thử lần lượt theo `_CHATGPT_UPLOAD_IDS`
    # (`upload-files` trước, đã proven ở lần test gần nhất), mỗi candidate được
    # cấp 1 phần `timeout`; nếu candidate đầu không xác nhận được trong phần của
    # nó thì thử candidate kế tiếp — KHÔNG lặp lại toàn bộ timeout cho mỗi lần.
    _CHATGPT_UPLOAD_IDS = ('upload-files', 'upload-photos')

    def _chatgpt_attach_file_kickoff(self, local_path: str, target_id: str) -> int:
        """Set file trực tiếp vào `#{target_id}` (KHÔNG cần mở menu '+' trước —
        đã verify không cần). Trả về số file ĐÃ chọn trước đó trên CHÍNH input
        này (input có `multiple` — mỗi lần `send_keys()` CỘNG DỒN vào `.files`,
        không thay thế), để `_chatgpt_attach_file_poll()` biết ngưỡng so sánh."""
        from selenium.webdriver.common.by import By
        before_count = self._js(f"""
            var el = document.getElementById('{target_id}');
            return (el && el.files) ? el.files.length : 0;
        """) or 0
        file_input = self.driver.find_element(By.CSS_SELECTOR, f'#{target_id}')
        file_input.send_keys(local_path)
        self._log('info', f'send_keys() → #{target_id}: {os.path.basename(local_path)}')
        return before_count

    def _chatgpt_attach_file_poll(self, target_id: str, before_count: int) -> bool:
        """1 lần kiểm tra KHÔNG-BLOCKING — `.files.length` của `#{target_id}` đã
        tăng hay chưa. Đây là tín hiệu Ở TẦNG FORM (chắc chắn, không phụ thuộc UI
        có render thumbnail hay không) — độ sẵn sàng "ChatGPT xử lý xong file"
        THẬT SỰ được xác nhận gián tiếp bởi `_chatgpt_submit_if_ready()` sau đó
        (nút Gửi chỉ hết disabled khi ChatGPT nhận đủ file — cùng triết lý 2
        giai đoạn của Gemini, xem `_gemini_fill_and_submit`)."""
        count = self._js(f"""
            var el = document.getElementById('{target_id}');
            return (el && el.files) ? el.files.length : 0;
        """) or 0
        return count > before_count

    def _chatgpt_attach_file(self, local_path: str, timeout: int = 45):
        """Đính kèm 1 file ảnh local vào composer ChatGPT — thử lần lượt từng
        candidate id trong `_CHATGPT_UPLOAD_IDS` (xem ghi chú class-level ở
        trên), mỗi candidate được cấp `timeout / len(_CHATGPT_UPLOAD_IDS)` giây."""
        per_candidate = max(5, timeout // len(self._CHATGPT_UPLOAD_IDS))
        last_error = None
        for target_id in self._CHATGPT_UPLOAD_IDS:
            try:
                before_count = self._chatgpt_attach_file_kickoff(local_path, target_id)
            except Exception as e:
                last_error = e
                continue
            deadline = time.time() + per_candidate
            while time.time() < deadline:
                if self._chatgpt_attach_file_poll(target_id, before_count):
                    # (2026-08-01) BUG THẬT bắt được qua Selenium thật: `.files.length`
                    # tăng + thumbnail xuất hiện CHỈ xác nhận file đã chọn Ở TẦNG FORM
                    # (đọc local, tức thì) — KHÔNG đồng nghĩa ChatGPT đã upload xong lên
                    # server. Nút Gửi (`[data-testid="send-button"]`) KHÔNG hề bị disable
                    # trong lúc upload (khác Gemini — nút Gửi Gemini disable cho tới khi
                    # nhận đủ file, nên `_gemini_fill_and_submit`'s poll tự nhiên an toàn).
                    # Verify trực tiếp: gửi ngay sau khi `.files.length` tăng (0 giây chờ)
                    # → composer bị xoá (trông như gửi thành công) nhưng KHÔNG có
                    # conversation turn nào xuất hiện, tin nhắn bị ChatGPT ÂM THẦM DROP
                    # toàn bộ (cả text lẫn ảnh) — lặp lại y hệt 2 lần độc lập. Thêm 5s chờ
                    # sau khi thumbnail xác nhận → gửi thành công ngay, turn xuất hiện đúng
                    # trong ~2s. Không có tín hiệu DOM nào khác (spinner/progress bar) để
                    # bắt chính xác "upload xong" — đành dùng sleep cố định, đặt RỘNG RÃI
                    # hơn mốc verify (5s) để an toàn với ảnh lớn hơn/mạng chậm hơn.
                    self._sleep(6)
                    self._log('ok', f'File đính kèm xác nhận thành công qua #{target_id} (đã chờ upload ổn định)')
                    return
                self._sleep(1)
            self._log('warn', f'#{target_id} không xác nhận được sau {per_candidate}s — thử candidate kế tiếp')
        raise RuntimeError(
            f'Timeout {timeout}s: không xác nhận được file đã đính kèm vào ChatGPT'
            + (f' (lỗi cuối: {last_error})' if last_error else '')
        )

    def _chatgpt_attach_files(self, local_paths: list, timeout: int = 45):
        """Đính kèm 1 HOẶC NHIỀU file cùng lúc qua ĐÚNG 1 lệnh `send_keys()` duy
        nhất (nhiều path nối bằng `\\n` — cú pháp Selenium chuẩn cho
        `input[multiple]`, dịch sang ĐÚNG 1 lệnh CDP `DOM.setFileInputFiles`
        mang toàn bộ danh sách path, KHÔNG phải N lệnh riêng lẻ).

        (2026-08-04) FIX bug thật: `_run_task_chatgpt()` trước đây gọi
        `_chatgpt_attach_file()` (single) RIÊNG cho từng ảnh — N lần
        `send_keys()` liên tiếp trên CÙNG 1 input TĨNH `#upload-files` (khác
        Gemini — input đó không mở lại menu mỗi lần, luôn là CHÍNH element cũ).
        User báo lỗi thật + verify: 2 ảnh gắn cho storyboard #46 (`15_CHAR_001_03.jpg`
        634KB, `6_BG_008_01.jpg` 1121KB — SHA256 xác nhận nội dung HOÀN TOÀN
        khác nhau) bị ChatGPT báo "Bạn đã upload ảnh này" ngay ảnh thứ 2 — dù
        USER TỰ TAY chọn CÙNG 2 file đó qua dialog thật (chọn cả 2 cùng lúc)
        lại hoạt động bình thường. Kết luận: vấn đề KHÔNG phải nội dung ảnh
        trùng, mà ở việc gửi 2 lần `send_keys()` RIÊNG BIỆT (2 sự kiện `change`
        cách nhau vài giây) — nhiều khả năng ChatGPT có cơ chế chống-trùng dựa
        trên tín hiệu KHÁC nội dung byte (session upload/token nội bộ) và bị
        false-positive khi thấy 2 lần chọn file liên tiếp trên cùng 1 input.
        Gộp thành 1 `send_keys()` duy nhất mô phỏng ĐÚNG hành vi người dùng
        multi-select 2 file 1 lúc — chỉ 1 sự kiện `change` với `.files.length`
        nhảy thẳng lên N, không có 'lần thứ 2' nào để hệ thống hiểu nhầm là
        trùng."""
        n = len(local_paths)
        if n == 0:
            return
        per_candidate = max(5, timeout // len(self._CHATGPT_UPLOAD_IDS))
        last_error = None
        for target_id in self._CHATGPT_UPLOAD_IDS:
            try:
                from selenium.webdriver.common.by import By
                before_count = self._js(f"""
                    var el = document.getElementById('{target_id}');
                    return (el && el.files) ? el.files.length : 0;
                """) or 0
                file_input = self.driver.find_element(By.CSS_SELECTOR, f'#{target_id}')
                file_input.send_keys('\n'.join(local_paths))
                names = ', '.join(os.path.basename(p) for p in local_paths)
                self._log('info', f'send_keys() → #{target_id}: {n} file cùng lúc ({names})')
            except Exception as e:
                last_error = e
                continue
            deadline = time.time() + per_candidate
            while time.time() < deadline:
                count = self._js(f"""
                    var el = document.getElementById('{target_id}');
                    return (el && el.files) ? el.files.length : 0;
                """) or 0
                if count >= before_count + n:
                    # Chờ ổn định lâu hơn nếu nhiều file (ChatGPT upload nền lâu hơn) —
                    # mirror _chatgpt_attach_file(), xem docstring đó để biết lý do cần chờ.
                    self._sleep(6 if n <= 1 else min(6 + (n - 1) * 3, 20))
                    self._log('ok', f'{n} file đính kèm xác nhận thành công qua #{target_id} '
                                     f'(đã chờ upload ổn định)')
                    return
                self._sleep(1)
            self._log('warn', f'#{target_id} không xác nhận đủ {n} file sau {per_candidate}s '
                               f'— thử candidate kế tiếp')
        raise RuntimeError(
            f'Timeout {timeout}s: không xác nhận được {n} file đã đính kèm vào ChatGPT'
            + (f' (lỗi cuối: {last_error})' if last_error else '')
        )


    # ── Phát hiện CHƯA ĐĂNG NHẬP ChatGPT (2026-09-06) ────────────────────────
    #
    # ⚠️ Bug thật user báo *"chatgpt tạo ảnh đang lỗi"*. Log sản xuất (profile 25,
    # 2026-09-06 00:56 và 00:59) chỉ nói:
    #     [chatgpt] Task lỗi: Không tìm thấy ô nhập liệu ChatGPT (#prompt-textarea)
    # → dẫn người đọc đi tìm bug DOM, trong khi nguyên nhân THẬT là **profile đã
    # bị đăng xuất khỏi chatgpt.com**. Xác nhận bằng 2 bằng chứng độc lập:
    #   • Cookie: chỉ còn cookie ẩn danh/CDN (`oai-did`/`__cf_bm`/`_cfuvid`/
    #     `oai-sc`), KHÔNG còn `__Secure-next-auth.session-token`.
    #   • DOM: trang trả về composer của bản CHƯA đăng nhập
    #     (`#mobile-composer-prompt`, class `wm-*`) kèm nút ghi thẳng
    #     "Create image. Log in to use." và câu "Log in to get answers…".
    # ĐÂY LÀ LẦN THỨ 2 mất thời gian vì đúng cái bẫy này — lần đầu 2026-08-01
    # (xem ghi chú "user ban đầu đăng nhập nhầm chỗ" ở block trên). Nên giờ kiểm
    # tra TƯỜNG MINH và báo lỗi ĐÚNG BẢN CHẤT, thay vì để nó lộ ra dưới dạng
    # "không tìm thấy selector".
    #
    # KHÔNG tự đăng nhập giúp (khác `_ensure_google_login()` của Google): ChatGPT
    # dùng hệ đăng nhập riêng của OpenAI, thường kèm captcha/2FA — tự động hoá
    # không đáng tin. Chỉ phát hiện + báo rõ để user tự đăng nhập 1 lần vào ĐÚNG
    # profile Chrome này ("Mở login browser" trong GUI).

    _CHATGPT_LOGGED_OUT_JS = r"""
        // Ưu tiên tín hiệu CẤU TRÚC (bền hơn text): nút/hộp thoại đăng nhập mà
        // bản chưa-đăng-nhập luôn render.
        var structural = document.querySelector(
            '[commandfor*="auth-dialog"], [data-testid="login-button"], '
            + '[data-testid="signup-button"], button[aria-label*="Log in to use"]');
        if (structural) return {loggedOut: true, why: 'nút/hộp thoại đăng nhập có mặt'};
        // Composer của bản chưa đăng nhập (quan sát thật 2026-09-06).
        if (document.querySelector('#mobile-composer-prompt')
                && !document.querySelector('#prompt-textarea')) {
            return {loggedOut: true, why: 'composer bản chưa đăng nhập (#mobile-composer-prompt)'};
        }
        var t = (document.body && document.body.innerText || '').slice(0, 600);
        if (/log in to get answers|log in to use|đăng nhập để/i.test(t)) {
            return {loggedOut: true, why: 'trang mời đăng nhập'};
        }
        return {loggedOut: false, why: ''};
    """

    def _chatgpt_logged_out_reason(self) -> str:
        """Trả lý do (chuỗi) nếu ĐANG Ở TRẠNG THÁI CHƯA ĐĂNG NHẬP, `''` nếu ổn.

        Best-effort: JS lỗi/driver rụng → trả `''` (coi như ổn) để KHÔNG chặn
        nhầm task chỉ vì bước kiểm tra phụ này hỏng."""
        try:
            r = self._js(self._CHATGPT_LOGGED_OUT_JS) or {}
        except Exception:
            return ''
        return r.get('why', '') if r.get('loggedOut') else ''

    def _chatgpt_assert_logged_in(self):
        reason = self._chatgpt_logged_out_reason()
        if reason:
            raise RuntimeError(
                f'ChatGPT CHƯA ĐĂNG NHẬP trên profile này ({reason}). '
                f'Mở "login browser" của profile rồi tự đăng nhập chatgpt.com 1 lần '
                f'(session sẽ được lưu lại trong profile_dir).'
            )

    def _chatgpt_type_prompt(self, prompt: str):
        """Gõ prompt vào ô nhập liệu ChatGPT (`#prompt-textarea`, contenteditable) —
        KHÔNG bấm Gửi (đó là việc của `_chatgpt_submit_if_ready`)."""
        textbox = None
        deadline = time.time() + 10
        while time.time() < deadline:
            textbox = self._chatgpt_find('#prompt-textarea')
            if textbox:
                break
            self._sleep(0.3)
        if not textbox:
            # (2026-09-06) Nguyên nhân PHỔ BIẾN NHẤT của "không tìm thấy ô nhập
            # liệu" là CHƯA ĐĂNG NHẬP (bản chưa đăng nhập dùng composer khác) —
            # kiểm tra trước để báo đúng bản chất thay vì đổ cho selector.
            self._chatgpt_assert_logged_in()
            raise RuntimeError('Không tìm thấy ô nhập liệu ChatGPT (#prompt-textarea) '
                               '— đã đăng nhập nhưng DOM không khớp: OpenAI có thể vừa '
                               'đổi giao diện, chạy tests/_diag_chatgpt_dom.py để lấy selector mới')

        self._cdp_click_el(textbox)
        self._sleep(0.3)

        head, rest = prompt[:20], prompt[20:]
        for ch in head:
            self._cdp('Input.insertText', {'text': ch})
            self._sleep(0.03 + random.random() * 0.05)
        if rest:
            self._cdp('Input.insertText', {'text': rest})
        self._sleep(0.5)

    def _chatgpt_submit_if_ready(self) -> bool:
        """1 lần kiểm tra KHÔNG-BLOCKING xem nút Gửi (`[data-testid="send-button"]`)
        đã sẵn sàng chưa (tồn tại + không disabled — hết disabled đúng lúc ChatGPT
        xử lý xong mọi file đính kèm). Click NGAY nếu sẵn sàng, trả True; ngược lại
        trả False (caller tự lặp lại poll)."""
        send_btn = self._js("""
            var b = document.querySelector('[data-testid="send-button"]');
            if (!b || b.disabled) return null;
            return b;
        """)
        if not send_btn:
            return False
        self._cdp_click_el(send_btn)
        return True

    def _chatgpt_fill_and_submit(self, prompt: str, send_ready_timeout: int = 180):
        """Gõ prompt rồi bấm nút Gửi — wrapper mỏng quanh `_chatgpt_type_prompt` +
        `_chatgpt_submit_if_ready`, tự lặp lại poll tới khi xong hoặc hết
        `send_ready_timeout`."""
        self._chatgpt_type_prompt(prompt)
        deadline = time.time() + send_ready_timeout
        while time.time() < deadline:
            if self._chatgpt_submit_if_ready():
                self._log('info', f'ChatGPT prompt submitted ({len(prompt)} chars)')
                return
            self._sleep(0.5)
        raise RuntimeError(
            f'Timeout {send_ready_timeout}s: nút Gửi ChatGPT không sẵn sàng '
            f'(có thể ChatGPT chưa nhận xong file đính kèm)'
        )

    def _chatgpt_response_if_ready(self) -> dict | None:
        """1 lần kiểm tra KHÔNG-BLOCKING xem ChatGPT đã phản hồi xong chưa. Tín hiệu
        is-generating: `[data-testid="stop-button"]` tồn tại (nút Gửi tự đổi
        testid — KHÔNG có bẫy "luôn visible" như Gemini). Trả `{'text','images'}`
        nếu lượt CUỐI là của assistant và đã xong (có text HOẶC ảnh); None nếu còn
        đang generate hoặc lượt cuối chưa phải của assistant (vừa submit, DOM chưa
        kịp thêm turn mới)."""
        is_generating = self._js(
            'return !!document.querySelector(\'[data-testid="stop-button"]\');'
        )
        if is_generating:
            return None
        text = self._js("""
            var turns = document.querySelectorAll('[data-testid^="conversation-turn-"]');
            if (!turns.length) return null;
            var last = turns[turns.length - 1];
            var roleEl = last.querySelector('[data-message-author-role]');
            if (roleEl && roleEl.getAttribute('data-message-author-role') !== 'assistant') return null;
            var md = last.querySelector('.markdown');
            return md ? md.innerText.trim() : '';
        """)
        if text is None:
            return None
        images, images_pending = self._extract_response_images_base64("""
            var turns = document.querySelectorAll('[data-testid^="conversation-turn-"]');
            return turns.length ? turns[turns.length - 1] : null;
        """)
        # (2026-08-03) Ảnh còn đang render (placeholder trắng, xem docstring
        # _extract_response_images_base64()) — ĐỪNG chốt response vội, tiếp tục
        # poll ở vòng sau thay vì trả về text-only/ảnh trắng.
        if images_pending:
            return None
        if (text and len(text) > 2) or images:
            return {'text': text or '', 'images': images}
        return None

    def _chatgpt_wait_response(self, timeout: int = 300) -> dict:
        """Chờ ChatGPT generate xong, trả về `{'text','images'}` phản hồi cuối
        cùng. Wrapper mỏng quanh `_chatgpt_response_if_ready`, tự lặp lại poll tới
        khi xong hoặc hết `timeout`."""
        self._sleep(1.5)
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._chatgpt_response_if_ready()
            if result:
                return result
            self._sleep(1)
        raise RuntimeError(f'Timeout: ChatGPT không phản hồi sau {timeout}s')

    def _heartbeat_chatgpt(self, running: int = 0, max_concurrent: int = 1,
                           keepalive_only: bool = False) -> list:
        """Heartbeat riêng cho machine_type=chatgpt_chat_selenium — mirror
        `_heartbeat_gemini()` 1:1 (xem docstring đó để biết ý nghĩa từng tham số:
        `max_concurrent`/`keepalive_only`).

        ⚠️ Backend (`backend/routes/heartbeat.py`, repo `ToolSub` gốc — KHÁC repo
        với `client_tool`) CHƯA được cập nhật để nhận diện machine_type này ở bản
        2026-08-01 — phần WORKER (đủ để chạy/test độc lập, vd qua script mirror
        `tests/_test_gemini_video.py`) đã sẵn sàng, nhưng để ChatGPT THẬT SỰ nhận
        được prompt qua heartbeat sản xuất (giống cách `gemini_chat_selenium` nhận
        qua `gemini_pending_requests`), cần thêm việc phía backend — xem
        CLAUDE.md/CHANGELOG mục ChatGPT để biết chi tiết còn thiếu."""
        try:
            profile      = pm.get(self.profile_id)
            display_name = (profile.get('display_name') or '').strip() \
                or f'[SEL-ChatGPT] {profile.get("profile_name", "")}'
            r = self._req('POST', f'{FLOW_SERVER}/api/media/heartbeat', quiet=True,
                          body={'machineCode':  self.machine_code,
                                'runningCount': running,
                                'waitingCount': 0,
                                'machineType':  'chatgpt_chat_selenium',
                                'displayName':  display_name,
                                'maxConcurrent': max_concurrent,
                                'keepaliveOnly': keepalive_only}) or {}
            # (2026-09-06) `or {}` — `_req()` trả `r.json()`, mà body `null` từ
            # server/proxy cho ra `None` ⇒ `.get()` ném "'NoneType' object has no
            # attribute 'get'" (đã thấy thật trong profile_logs 2026-09-06 00:57).
            pending_list = r.get('pendingPrompts')
            if pending_list is None:
                single = r.get('pendingPrompt')
                pending_list = [single] if single else []
            for pending in pending_list:
                has_img = bool(pending.get('imageUrl') or pending.get('videoUrl'))
                self._log('info',
                          f'← Heartbeat: nhận prompt {len(pending.get("prompt", ""))} ký tự'
                          f'{" + ảnh" if has_img else ""}')
            return pending_list
        except Exception as e:
            self._log('warn', f'Heartbeat (chatgpt) error: {e}')
            return []

    def _run_task_chatgpt(self, pending: dict):
        """Xử lý 1 pendingPrompt: tải MỌI ảnh đính kèm (nếu có, qua `imageUrls` —
        mảng đầy đủ, 2026-08-04, fix bug thật "upload 2 ảnh tham chiếu nhưng qua
        chatgpt/gemini chỉ thấy đính kèm có 1 ảnh"; fallback `imageUrl`/`videoUrl`
        số ít cho backend CŨ chưa gửi `imageUrls`) → attach TUẦN TỰ từng file bằng
        send_keys → gõ prompt → gửi → chờ phản hồi (text + ảnh tạo ra nếu có) →
        POST callback_url. Mirror `_run_task_gemini()` 1:1 về cấu trúc.

        ⚠️ Giới hạn ĐÃ KIỂM CHỨNG (§11.25 client_tool/CLAUDE.md, test thật 3 lần độc
        lập với tài khoản Free): ChatGPT ÂM THẦM DROP tin nhắn nếu đính kèm ≥2 ảnh
        cùng lúc — 1 ảnh luôn ổn định, chưa xác nhận hành vi với tài khoản trả phí.
        Vòng lặp dưới đây vẫn cố đính kèm đủ N ảnh (đúng ý muốn), nhưng nếu tài
        khoản đang dùng là Free, prompt N≥2 ảnh nhiều khả năng vẫn thất bại ở tầng
        SẢN PHẨM OpenAI — không phải lỗi code, không có cách khắc phục từ phía tool."""
        prompt       = pending.get('prompt') or ''
        image_url    = pending.get('imageUrl') or pending.get('videoUrl')
        image_urls   = pending.get('imageUrls') or ([image_url] if image_url else [])
        # (2026-08-04) Dedup theo URL — xem comment tương đương ở _run_task_gemini().
        image_urls   = list(dict.fromkeys(image_urls))
        meta         = pending.get('meta') or {}
        callback_url = pending.get('callbackUrl')

        attach_timeout   = int(self.profile.get('chatgpt_attach_timeout')   or 60)
        response_timeout = int(self.profile.get('chatgpt_response_timeout') or 300)

        pm.set_status(self.profile_id, 'processing')
        tmp_paths = []

        # Keepalive — cùng lý do/cơ chế với _run_task_gemini() (task có thể chạy
        # vài phút, vòng lặp chính bị block ở đây không heartbeat được).
        stop_keepalive = threading.Event()

        def _keepalive():
            while not stop_keepalive.wait(POLL_INTERVAL):
                self._heartbeat_chatgpt(running=1, keepalive_only=True)

        keepalive_thread = threading.Thread(target=_keepalive, daemon=True)
        keepalive_thread.start()

        try:
            # Luôn bắt đầu chat mới. (2026-08-08) KHÔNG gọi _ensure_google_login()
            # ở đây — hàm đó giờ LUÔN chủ động detour qua accounts.google.com
            # trước khi vào target (theo yêu cầu user cho luồng VEO3/Gemini),
            # nhưng ChatGPT dùng hệ thống đăng nhập RIÊNG của OpenAI
            # (chatgpt.com/auth), không liên quan gì tới trạng thái đăng nhập
            # Google — detour qua đó chỉ tốn thời gian vô ích mà không nói lên
            # được gì về việc ChatGPT đã đăng nhập hay chưa.
            self._nav_get(self.CHATGPT_URL, 'ChatGPT: bắt đầu chat mới cho task')
            self._sleep(3)
            # (2026-09-06) Chặn SỚM khi chưa đăng nhập — trước đây lỗi chỉ lộ ra ở
            # bước gõ prompt dưới dạng "không tìm thấy ô nhập liệu", sau khi đã tốn
            # công tải + đính kèm hết ảnh.
            self._chatgpt_assert_logged_in()

            if image_urls:
                profile_tmp_dir = VIDEO_TMP_DIR / f'profile_{self.profile_id}_chatgpt'
                profile_tmp_dir.mkdir(exist_ok=True)
                for idx, url in enumerate(image_urls):
                    self._log('info', f'Đang tải ảnh đính kèm {idx+1}/{len(image_urls)} từ server ({url})…')
                    resp = req_lib.get(url, timeout=60, stream=True)
                    resp.raise_for_status()
                    orig_name = _normalize_attach_filename(os.path.basename(url.split('?')[0]) or f'image_{idx}.jpg')
                    filename  = orig_name if idx == 0 else f'{idx}_{orig_name}'
                    tmp_path  = str(profile_tmp_dir / filename)
                    with open(tmp_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                    tmp_paths.append(tmp_path)
                    size_kb = os.path.getsize(tmp_path) // 1024
                    self._log('ok', f'✔ Đã tải ảnh đính kèm {idx+1}/{len(image_urls)} ({size_kb} KB) → {tmp_path}')
                # (2026-08-04) Đính kèm TẤT CẢ ảnh trong 1 lần send_keys() DUY NHẤT
                # qua _chatgpt_attach_files() — KHÔNG loop _chatgpt_attach_file() per
                # ảnh nữa (đó là root cause bug "Bạn đã upload ảnh này", xem docstring
                # _chatgpt_attach_files()).
                self._chatgpt_attach_files(tmp_paths, timeout=attach_timeout)

            self._chatgpt_fill_and_submit(prompt, send_ready_timeout=attach_timeout)
            result = self._chatgpt_wait_response(timeout=response_timeout)
            text   = result.get('text') or ''
            images = result.get('images') or []
            self._log('ok', f'✔ Nhận response {len(text)} ký tự'
                             + (f' + {len(images)} ảnh' if images else ''))

            if callback_url:
                try:
                    payload = {'prompt': prompt, 'response': text, 'meta': meta,
                               'ts': int(time.time() * 1000)}
                    if images:
                        payload['images'] = images
                    req_lib.post(callback_url, json=payload, timeout=30)
                    self._log('info', f'callback → {callback_url} ✔')
                except Exception as e:
                    self._log('warn', f'callback lỗi: {e}')

        except Exception as e:
            self._log('error', f'[chatgpt] Task lỗi: {e}')
            if callback_url:
                try:
                    req_lib.post(callback_url, json={
                        'prompt': prompt, 'error': str(e), 'meta': meta,
                        'ts': int(time.time() * 1000),
                    }, timeout=30)
                except Exception:
                    pass
        finally:
            stop_keepalive.set()
            keepalive_thread.join(timeout=POLL_INTERVAL + 2)
            for p in tmp_paths:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass
            pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())

    def _run_chatgpt_loop(self):
        """Main loop riêng cho worker_mode='chatgpt' (không dùng chung run() của
        Flow/Gemini). Sequential 1-tab — mirror `_run_gemini_loop()`."""
        self._log('info', 'Worker starting (ChatGPT mode)')
        pm.set_status(self.profile_id, 'idle', pid=threading.get_ident())
        try:
            self.driver = self._make_driver()
            self._log('info', 'Chrome opened — navigate to ChatGPT')
            # (2026-08-08) KHÔNG gọi _ensure_google_login() — xem giải thích ở
            # _run_task_chatgpt(): ChatGPT dùng hệ đăng nhập riêng của OpenAI,
            # không liên quan trạng thái đăng nhập Google.
            self._nav_get(self.CHATGPT_URL, 'ChatGPT: mở trang lúc khởi động worker')
            self._sleep(3)
            # (2026-09-06) Trước đây LUÔN báo "sẵn sàng" kể cả khi profile đã bị
            # đăng xuất — mọi task sau đó lỗi với thông báo sai bản chất. Giờ nói
            # rõ ngay từ lúc khởi động (vẫn chạy tiếp: session có thể được khôi
            # phục giữa chừng nếu user đăng nhập vào cửa sổ này).
            _logged_out = self._chatgpt_logged_out_reason()
            if _logged_out:
                self._log('error', f'ChatGPT CHƯA ĐĂNG NHẬP ({_logged_out}) — mọi task '
                                    f'sẽ lỗi cho tới khi đăng nhập chatgpt.com trên '
                                    f'ĐÚNG profile Chrome này')
            else:
                self._log('ok', 'ChatGPT mode — sẵn sàng nhận prompt')

            while not self._stop.is_set():
                if not self._is_driver_alive():
                    self._log('warn', 'Browser đã đóng — thoát worker loop')
                    break
                try:
                    pending_list = self._heartbeat_chatgpt()
                    if pending_list:
                        self._run_task_chatgpt(pending_list[0])
                        # (2026-08-17) "chạy xong batch là xóa cache + cookie
                        # luôn" — mirror `_run_gemini_loop()`, xem giải thích ở đó.
                        self._cdp_clear_cache_and_cookies()
                    else:
                        self._stop.wait(POLL_INTERVAL)
                except Exception as e:
                    self._log('error', f'Worker loop (chatgpt): {e}')
                    if not self._is_driver_alive():
                        self._log('warn', 'Driver mất kết nối sau exception — thoát loop')
                        break
                    self._stop.wait(POLL_INTERVAL)

        except Exception as e:
            self._log('error', f'Worker fatal (chatgpt): {e}')
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            if not self._clear_profile_if_sleeping(pm.get(self.profile_id)):
                pm.set_status(self.profile_id, 'offline', clear_task=True, pid=None)
            self._log('info', 'Worker stopped')

    # ── Error-recovery escalation ──────────────────────────────────────────────
    #
    # error (liên tiếp) → refresh trang (F5) → nếu vẫn lỗi, lặp lại refresh tới
    # ngưỡng → bỏ project hiện tại, quay về FLOW_PROJECT_URL, bắt project mới do
    # Google tự tạo/redirect, lưu đè lên project_url của profile → nếu NGAY CẢ
    # project mới cũng lỗi tới ngưỡng refresh (nghĩa là vấn đề không nằm ở 1
    # project cụ thể — có thể account bị khoá/hết quota) → cho profile "ngủ"
    # error_sleep_secs, dừng hẳn worker loop, để dispatcher (phase sau) tự đánh
    # thức lại sau khi hết thời gian ngủ.
    def _task_start(self, task_id, mode: str, prompt: str):
        """Gọi ngay khi 1 task bắt đầu xử lý thật (sau pm.set_status 'processing') —
        cho dashboard live (xem live_info())."""
        self._current_task = {
            'id': task_id, 'mode': mode, 'prompt': (prompt or '')[:200],
            'started_at': time.time(),
        }

    def live_info(self) -> dict:
        """Snapshot trạng thái phiên chạy hiện tại, dùng bởi GET /api/selenium/status
        để dashboard (main.py) hiển thị live: task đang chạy, số lỗi, sleep."""
        t = self._current_task
        return {
            'liveTaskId':        t['id']        if t else None,
            'liveTaskMode':      t['mode']       if t else None,
            'liveTaskPrompt':    t['prompt']     if t else None,
            'liveTaskStartedAt': t['started_at'] if t else None,
            'taskDoneCount':     self._task_done_count,
            'taskErrorCount':    self._task_error_count,
            'consecutiveErrors': self._consecutive_errors,
            # (2026-08-20) Bậc thang escalation THEO BATCH — xem _finish_batch_cleanup()
            'consecutiveFailedBatches': self._consecutive_failed_batches,
            'lastErrorMsg':      self._last_error_msg,
            'lastErrorAt':       self._last_error_at or None,
            'sleepUntil':        self._sleep_until if self._sleep_until > time.time() else None,
        }

    def _handle_task_success(self):
        self._task_done_count += 1
        self._current_task = None
        self._consecutive_errors = 0
        self._project_resets_since_success = 0
        # (2026-08-20) Bậc thang THEO BATCH — đây là điểm funnel DUY NHẤT của MỌI
        # đường "task thành công" (DOM batch/API batch/tuần tự/reconcile — 9 call
        # site đều đi qua đây), nên đếm ở đúng chỗ này là đủ phủ hết mọi chế độ,
        # không cần rải counter ra từng nhánh. Batch có ≥1 task tăng biến này =
        # batch THÀNH CÔNG (xem `_evaluate_batch_outcome()`).
        self._batch_success_count += 1
        # Bộ đếm BỀN VỮNG trong ngày (2026-07-18) — khác _task_done_count ở trên
        # (chỉ sống trong RAM, mất khi worker restart), lưu ở backend qua
        # selenium_profiles.tasks_done_today, xem CHANGELOG "profile lưu thêm số
        # task hoàn thành/lỗi trong ngày".
        pm.bump_task_stat(self.profile_id, 'done')

    def _record_error(self, message: str = '') -> bool:
        """Bookkeeping cho dashboard (counter + last error). Trả True nếu VỪA cho
        profile ngủ (caller phải dừng hẳn ngay, không ghi đè status/tiếp tục xử lý
        task khác) — hiện LUÔN trả False, xem ghi chú TẠM TẮT ngay dưới.

        ⚠️ (2026-08-20, TẠM TẮT theo yêu cầu user "theo luồng mới ẩn setting này
        trước, tạm thời ko dùng nữa gây xáo trộn luồng") — bậc thang "time-window"
        (`error_window_minutes`/`error_window_max_errors`: N lỗi trong M phút →
        ngủ NGAY) TỪNG chạy ở đây, ĐÃ VÔ HIỆU HOÁ. Lý do: nó cho profile ngủ dựa
        trên bộ đếm lỗi TÍCH LUỸ THEO THỜI GIAN, hoàn toàn không biết gì về ranh
        giới batch — nên thường xuyên CƯỚP QUYỀN của bậc thang THEO BATCH mới
        (§11.39, `_finish_batch_cleanup()`) trước khi bậc đó kịp chạy hết chuỗi
        "2 batch lỗi → dọn cookie + click Create → batch thứ 3 → ngủ". Bằng chứng
        user gửi: `5 lỗi trong 50 phút (time-window) — cho profile "ngủ" 60s` —
        5 lỗi rải rác suốt 50 PHÚT (có thể xen kẽ nhiều batch THÀNH CÔNG) vẫn ép
        ngủ, đúng nghĩa "gây xáo trộn luồng".

        Code time-window GIỮ NGUYÊN Ở DƯỚI (trong `if False:`) — cùng convention
        "tạm tắt, không xoá" đã dùng cho `_purge_non_login_cookies()`
        (`managers.py`). BẬT LẠI: đổi `if False:` → `if True:` (và bỏ 2 setting
        khỏi phần ẩn ở `gui/pages/settings_page.py::_HIDDEN_FIELD_DEFS`). 2 setting
        `error_window_minutes`/`error_window_max_errors` VẪN nằm trong
        `_DEFAULT_SERVER_SETTINGS`/`_clamp()` (không xoá — `local_settings.json`
        của máy đang chạy đã lưu sẵn giá trị, xoá key sẽ làm chúng biến mất khi
        `update_local_settings()` ghi đè file).

        2 bậc thang CÒN LẠI KHÔNG bị ảnh hưởng: (1) escalation LIÊN TIẾP
        (`_handle_task_error()`: error_count_before_refresh → refresh →
        refresh_count_before_new_project → project mới → ngủ) vẫn chạy nguyên;
        (2) bậc thang THEO BATCH (§11.39) vẫn chạy nguyên."""
        self._task_error_count += 1
        self._current_task = None
        self._last_error_msg = message
        self._last_error_at  = time.time()
        # Bộ đếm BỀN VỮNG trong ngày (2026-07-18) — xem _handle_task_success().
        pm.bump_task_stat(self.profile_id, 'error')

        if False:   # ⚠️ TẠM TẮT — xem docstring. Đổi thành `if True:` để bật lại.
            now = time.time()
            window_secs = int(self._server_settings.get('error_window_minutes', 10) or 10) * 60
            self._error_timestamps.append(now)
            self._error_timestamps = [t for t in self._error_timestamps if now - t <= window_secs]
            max_errors = int(self._server_settings.get('error_window_max_errors', 5) or 5)
            if len(self._error_timestamps) >= max_errors:
                sleep_secs = int(self._server_settings.get('error_sleep_secs', 300))
                window_min = self._server_settings.get('error_window_minutes', 10)
                self._log('error', f'{len(self._error_timestamps)} lỗi trong {window_min} phút '
                                    f'(time-window) — cho profile "ngủ" {sleep_secs}s')
                pm.set_status(self.profile_id, 'sleeping', clear_task=True, pid=None)
                self._sleep_until = time.time() + sleep_secs
                _sleep_until_by_pid[self.profile_id] = self._sleep_until
                self._error_timestamps = []
                return True
        return False

    def _handle_task_error(self, message: str = '') -> bool:
        """Gọi trong except block của _run_task_dom/_run_task_api/_run_tasks_batch. Trả
        True nếu worker nên DỪNG HẲN (đã cho profile ngủ), False nếu vẫn tiếp tục vòng
        lặp bình thường."""
        if self._record_error(message):
            return True
        self._consecutive_errors += 1
        threshold_refresh = int(self._server_settings.get('error_count_before_refresh', 3))
        if self._consecutive_errors < threshold_refresh:
            return False

        self._consecutive_errors = 0
        self._refresh_count += 1
        try:
            self._nav_refresh(f'{threshold_refresh} lỗi task liên tiếp → refresh trang '
                              f'(lần {self._refresh_count}, lỗi cuối: {(message or "")[:120]})')
            self._sleep(5)
        except Exception as e:
            self._log('error', f'Refresh trang thất bại: {e}')

        threshold_reset = int(self._server_settings.get('refresh_count_before_new_project', 5))
        if self._refresh_count < threshold_reset:
            return False

        self._refresh_count = 0
        self._project_resets_since_success += 1
        if self._project_resets_since_success >= 2:
            sleep_secs = int(self._server_settings.get('error_sleep_secs', 300))
            self._log('error', f'Vẫn lỗi sau nhiều lần thử phục hồi — cho profile '
                                f'"ngủ" {sleep_secs}s')
            pm.set_status(self.profile_id, 'sleeping', clear_task=True, pid=None)
            self._sleep_until = time.time() + sleep_secs
            # Ghi vào dict module-level (không chỉ self) — thread worker sắp thoát,
            # dispatcher (_veo3_dispatcher_tick) cần đọc được thời điểm hết ngủ này
            # SAU KHI object worker/thread này đã bị reap, không còn cách nào lấy
            # lại self._sleep_until nữa.
            _sleep_until_by_pid[self.profile_id] = self._sleep_until
            return True

        # gemini_video/gemini_image KHÔNG có khái niệm "project" Flow — mỗi task
        # đã tự navigate về GEMINI_URL (chat mới) ngay từ đầu
        # `_run_task_gemini_video()`/`_run_task_gemini_image()`, nên bước phục
        # hồi project ở đây vô nghĩa với 2 worker_mode này (gọi nhầm sẽ điều
        # hướng sang labs.google, không phải lỗi nghiêm trọng vì task kế tiếp tự
        # ghi đè lại, nhưng tốn thời gian/gây log khó hiểu) — bỏ qua, coi như đã
        # "reset" (task tiếp theo tự về chat mới).
        if self.worker_mode in ('gemini_video', 'gemini_image'):
            self._log('info', f'{self.worker_mode} — bỏ qua bước phục hồi project (mỗi task tự vào chat mới)')
            return False

        # (2026-09-11, theo yêu cầu user "chỉ check trường hợp quá setting...
        # 300 media thì tạo project mới còn lại không được tạo mới") — TRƯỚC
        # ĐÂY ở đây gọi `_reset_flow_project()` (bấm "New project") khi refresh
        # nhiều lần vẫn không hết lỗi. Nhưng lỗi liên tục kiểu này RẤT THƯỜNG
        # do Google chặn/throttle ở TẦNG TÀI KHOẢN (PUBLIC_ERROR_UNUSUAL_
        # ACTIVITY/RPC_ERROR_CODE_8...) — tạo project mới KHÔNG sửa được gì
        # (tài khoản vẫn bị chặn), chỉ tạo thêm project không cần thiết — đúng
        # triệu chứng user báo "đang có lỗi tạo project quá nhiều". Project
        # MỚI CHỈ còn được tạo ở `_rotate_project_if_full()` (vượt
        # `max_project_media_items`, mặc định 300) — ở đây CHỈ còn thử quay
        # lại đúng project đã lưu (KHÔNG tính vào ngưỡng ngủ ở trên nữa —
        # ngưỡng đó đã tính RỒI, đây chỉ là 1 lần cố phục hồi thêm trước khi
        # vòng lặp task tiếp theo tự thử lại qua `_ensure_flow_page()`).
        returned = self._return_to_saved_project()
        self._log('warn' if not returned else 'ok',
                  f'Vượt {threshold_reset} lần refresh cho project hiện tại — ' +
                  ('đã quay lại được project đã lưu.' if returned else
                   'KHÔNG tạo project mới (chỉ ngưỡng max_project_media_items '
                   'mới được tạo mới) — vẫn chưa quay lại được, sẽ tự thử lại '
                   'ở task kế tiếp.'))
        return False

    def _cdp_clear_cache_and_cookies(self):
        """(2026-08-17, theo yêu cầu user "chạy xong batch là xóa cache + cookie
        luôn") — xoá cache HTTP + cookie "rác" NGAY TRONG LÚC Chrome VẪN ĐANG MỞ,
        qua CDP — KHÁC HẲN `pm.clear_cache_only()`/`_clear_profile_if_sleeping()`
        ngay dưới đây (đụng FILE trên đĩa của `profile_dir`, chỉ an toàn SAU
        `driver.quit()`, dùng khi worker SẮP THOÁT hẳn). Ở đây worker vẫn tiếp
        tục sống qua nhiều batch/heartbeat tiếp theo — không đóng/mở lại Chrome
        được, nên chỉ có cách dọn qua CDP trong khi trình duyệt vẫn sống.

        ⚠️ (2026-08-18, ĐỔI HƯỚNG LẦN 2 theo yêu cầu user "đổi sang xóa tất cả
        cookie trong labs.google ko đụng cái khác") — tiêu chí giờ THUẦN THEO
        DOMAIN, không còn danh sách tên "giữ lại" nào cả: cookie thuộc domain
        ĐÍCH (kể cả subdomain) bị XOÁ TẤT CẢ, MỌI domain khác (kể cả
        `.google.com`/`accounts.google.com` nơi cookie đăng nhập Google thật
        sự sống, và `chatgpt.com`) HOÀN TOÀN không bị đụng tới. Thay thế bản
        trước (so khớp theo TÊN cookie SID-family/ChatGPT, giữ lại ở MỌI
        domain) — bản đó vẫn giữ nguyên trong `managers.py`
        (`_GOOGLE_LOGIN_COOKIE_NAMES`/`_LOGIN_COOKIE_HOST_KEYWORDS`, cơ chế
        #2 `_clear_profile_if_sleeping()`/`clear_cache_only()`, ĐANG TẠM TẮT
        bước xoá cookie) — 2 cơ chế vẫn TÁCH RIÊNG hoàn toàn, không liên quan
        gì tới đổi hướng lần này.

        ⚠️ (2026-09-13, GENERIC HOÁ theo yêu cầu user "với các tài khoản
        gemini... clear cookie như veo") — domain ĐÍCH giờ tra theo
        `self.worker_mode` qua `_batch_clean_target_domain()` (`dom`/`api`
        → `labs.google` như trước, `gemini`/`gemini_video`/`gemini_image` →
        `gemini.google.com`, `chatgpt` → `None` = bỏ qua hẳn phần cookie/site
        data, chỉ còn xoá HTTP cache) — 1 hàm DUY NHẤT dùng chung cho mọi
        worker_mode, không viết bản riêng cho Gemini.

        `Storage.getCookies` (KHÔNG deprecated, KHÁC `Network.getCookies` — hàm
        đó chỉ trả cookie của trang ĐANG MỞ trong tab hiện tại, sẽ bỏ sót cookie
        của domain khác từng ghé qua nhưng không có frame nào đang load) — trả
        TOÀN BỘ cookie của browser context, không phụ thuộc tab đang active.
        Fallback `Network.getCookies` nếu `Storage.getCookies` không hỗ trợ
        (phiên bản Chrome/CDP cũ) — còn hơn không lấy được gì.

        Best-effort — CDP lỗi (driver vừa chết, tab đã đóng...) chỉ log, KHÔNG
        raise (gọi ở nhiều điểm giữa vòng lặp worker, không được làm crash cả
        batch chỉ vì bước dọn dẹp phụ này thất bại)."""
        try:
            self._cdp('Network.clearBrowserCache', {})
        except Exception as e:
            self._log('warn', f'[batch-clean] Xoá cache lỗi: {e}')

        target_domain = _batch_clean_target_domain(self.worker_mode)
        if not target_domain:
            self._log('info', '[batch-clean] ✔ Đã xoá cache HTTP (worker_mode '
                               f'"{self.worker_mode}" không có domain cookie riêng để dọn).')
            return

        cookies = None
        try:
            cookies = (self._cdp('Storage.getCookies', {}) or {}).get('cookies')
        except Exception:
            try:
                cookies = (self._cdp('Network.getCookies', {}) or {}).get('cookies')
            except Exception as e:
                self._log('warn', f'[batch-clean] Đọc cookie lỗi: {e}')

        self._cdp_clear_site_data_for_domain(target_domain)

        if not cookies:
            return
        removed = 0
        for c in cookies:
            domain = c.get('domain', '')
            if not _cookie_matches_domain(domain, target_domain):
                continue
            try:
                self._cdp('Network.deleteCookies', {
                    'name': c.get('name', ''), 'domain': domain, 'path': c.get('path', '/'),
                })
                removed += 1
            except Exception:
                pass
        self._log('info', f'[batch-clean] ✔ Đã xoá cache + site data + {removed}/{len(cookies)} '
                           f'cookie thuộc domain {target_domain} (không đụng domain khác).')

    def _cdp_clear_site_data_for_domain(self, domain: str):
        """(2026-08-23, GENERIC HOÁ 2026-09-13 — trước đây `_cdp_clear_labs_
        site_data()` CỐ ĐỊNH `labs.google`, giờ nhận `domain` bất kỳ để dùng
        chung cho cả VEO3 (`labs.google`) lẫn Gemini (`gemini.google.com`))
        — xoá localStorage/IndexedDB/Service Worker/CacheStorage... CHỈ của
        origin `domain` (và biến thể `www.`) — bổ sung cho bước xoá cookie ở
        `_cdp_clear_cache_and_cookies()`.

        Lý do: cookie chỉ là 1 trong ~10 kho trạng thái mỗi origin. Chỉ xoá
        đúng cookie mà để nguyên localStorage/IndexedDB/Service Worker của
        CÙNG origin đó ⇒ trang vẫn khôi phục lại trạng thái phiên cũ ngay lần
        load kế tiếp, gần như vô hiệu hoá cả bước dọn (cùng lớp bug với bước
        dọn lúc NGỦ — xem `managers.py::_purge_all_site_data()`).

        `Storage.clearDataForOrigin` giới hạn ĐÚNG theo origin nên KHÔNG đụng
        Google login (`accounts.google.com`/`.google.com` là origin khác) —
        giữ nguyên tinh thần "mỗi batch không được làm mất đăng nhập".

        CỐ Ý KHÔNG kèm `cookies` trong `storageTypes`: cookie đã được xử lý
        CHỌN LỌC ở hàm gọi (theo domain, còn giữ lại được cái cần) — để CDP
        xoá thêm 1 lần nữa chỉ làm 2 cơ chế chồng chéo, khó lần khi cần đổi.

        Best-effort — CDP không hỗ trợ/không có origin nào thì bỏ qua êm."""
        # ⚠️ Dùng `'all'` chứ KHÔNG liệt kê tay từng loại: `storageTypes` là
        # enum CHẶT của CDP (`Storage.StorageType`) — chỉ cần SAI 1 tên (rất dễ:
        # `file_systems` có gạch dưới, không phải `filesystems`) là Chrome từ
        # chối NGUYÊN LỆNH, và vì đây là bước best-effort bọc try/except nên
        # lỗi đó chỉ thành 1 dòng warning: toàn bộ việc dọn ÂM THẦM không xảy ra.
        # `'all'` giới hạn theo ĐÚNG origin nên tương đương "Clear site data"
        # của Chrome cho riêng domain này — không đụng origin khác.
        for origin in (f'https://{domain}', f'https://www.{domain}'):
            try:
                self._cdp('Storage.clearDataForOrigin', {'origin': origin, 'storageTypes': 'all'})
                continue
            except Exception as e:
                first_err = e
            # Bản Chrome/CDP không nhận `'all'` → thử lại bằng danh sách tường
            # minh (tên viết ĐÚNG theo spec, `file_systems` có gạch dưới).
            try:
                self._cdp('Storage.clearDataForOrigin', {
                    'origin': origin,
                    'storageTypes': 'local_storage,indexeddb,websql,file_systems,'
                                    'service_workers,cache_storage,shader_cache',
                })
            except Exception as e:
                self._log('warn', f'[batch-clean] Xoá site data {origin} lỗi: {first_err} / {e}')

    def _recover_flow_project_page_after_cache_clear(self, settle_wait_secs: float | None = None,
                                                     click_create: bool = True):
        """(2026-08-18, ĐỔI HƯỚNG LẦN 2 theo yêu cầu user mô tả LẠI TOÀN BỘ quy
        trình cuối mỗi batch: "nhập xong batch xóa cookie labs.google -> refresh
        trang project -> lấy setting thời gian khoảng cách giữa 2 batch làm thời
        gian chờ -> xong click nút create with google flow -> rồi lại refresh
        trang project lấy task đã hoàn tất -> nhận batch tiếp theo nếu có") — gọi
        NGAY SAU `_cdp_clear_cache_and_cookies()` khi worker đang đứng trên 1
        trang project labs.google THẬT (`worker_mode` dom/api).

        **Thay đổi so với bản đầu (chỉ "chờ thụ động" cho trang tự phát hiện mất
        cookie):** giờ chủ động `driver.refresh()` NGAY (không chờ trang tự nhận
        ra qua 1 lần gọi API nền thất bại — làm sạch/dứt khoát hơn, khớp đúng
        chữ "refresh trang project" user nêu tường minh trong quy trình), RỒI
        MỚI chờ — thời gian chờ đổi từ hằng số `settle_wait_secs=3.0` cũ sang
        ĐỌC TRỰC TIẾP setting `task_delay_secs` (Cài đặt trang "Delay giữa các
        task trong batch (giây)" — KHÔNG có setting nào tên riêng "khoảng cách
        giữa 2 batch", đây là setting delay/spacing DUY NHẤT sẵn có trong hệ
        thống nên TÁI DÙNG làm "thời gian chờ" cho bước này, đúng tinh thần
        "lấy setting thời gian khoảng cách... làm thời gian chờ"). Tham số
        `settle_wait_secs` giữ lại CHỈ để override thủ công lúc test (`None` =
        mặc định đọc `task_delay_secs`).

        Sau khi chờ xong mới CHECK+CLICK "Create with Google Flow" bằng
        `_click_create_with_flow_if_present()` (§11.28) — timeout poll riêng
        6s của chính hàm đó KHÔNG đổi, độc lập với thời gian chờ ở trên.

        ⚠️ (2026-08-20, theo yêu cầu user "theo phương án 1: nhưng không click
        create vì chỉ khi xóa cookie mới cần click") — `click_create=False` BỎ
        QUA hẳn bước click đó: màn hình xen giữa "Create with Google Flow" chỉ
        xuất hiện khi session/cookie vừa bị reset, nên batch bình thường (KHÔNG
        xoá cookie — xem `_finish_batch_cleanup()`) không cần tốn 6s poll tìm
        1 nút chắc chắn không có. `True` (mặc định) giữ nguyên hành vi cũ, dùng
        cho đúng nhánh VỪA xoá cookie.

        **Bước MỚI cuối hàm (2026-08-18, cùng lần đổi hướng này) — "rồi lại
        refresh trang project lấy task đã hoàn tất":** sau khi đã quay lại đúng
        trang project (dù có bấm nút hay không), gọi `_reconcile_project_media(
        set())` — hàm này TỰ `driver.refresh()` LẦN 2 (qua `_dom_fetch_project_
        media()`) rồi đọc `projectInitialData`, so khớp DB, tải về + đánh dấu
        `done` bất kỳ media nào đã render xong nhưng server chưa biết — ĐÚNG
        nghĩa đen "lấy task đã hoàn tất". Đây CHÍNH LÀ bước đã làm ở ĐẦU
        `_process_tasks()` cho batch KẾ TIẾP (fix cùng ngày, sáng hơn) — GIỜ
        CHUYỂN HẲN sang làm ở ĐÂY (cuối batch VỪA XONG) thay vì đầu batch SAU,
        theo ĐÚNG thứ tự user mô tả, và XOÁ đoạn code cũ ở đầu `_process_tasks()`
        để tránh refresh+đọc `projectInitialData` 2 LẦN LIÊN TIẾP cho cùng 1
        mục đích (mỗi lần tốn tới ~15s chờ interceptor — làm 1 lần ở đây là đủ,
        "cuối batch N" và "đầu batch N+1" là CÙNG 1 thời điểm về mặt luồng chạy
        khi không có gì xen giữa ngoài chờ heartbeat).

        Best-effort TOÀN BỘ hàm — lỗi đọc `current_url`/refresh/reconcile chỉ
        log, không raise (gọi trong `finally` của `_process_tasks()`, không được
        làm crash cả batch chỉ vì bước dọn dẹp cuối này thất bại)."""
        try:
            before = self.driver.current_url or ''
        except Exception as e:
            self._log('warn', f'[batch-clean] Không đọc được current_url: {e}')
            return
        if '/project/' not in before:
            return  # không đứng trên trang project cụ thể — không có gì để check
        # Chốt lại `project_url` theo project ĐANG làm việc trước khi refresh —
        # `_return_to_saved_project()` bên dưới đọc chính field này để quay về.
        self._sync_project_url_from_browser(before)

        try:
            self._nav_refresh('cuối batch: refresh trang project (sau bước dọn cookie nếu có) '
                              'trước khi reconcile')
        except Exception as e:
            self._log('warn', f'[batch-clean] Refresh trang project sau khi xoá cookie lỗi: {e}')
        wait_secs = settle_wait_secs if settle_wait_secs is not None else float(self._server_settings.get('task_delay_secs', 10))
        if wait_secs > 0:
            self._sleep(wait_secs)

        def _url():
            try:
                return self.driver.current_url or ''
            except Exception:
                return ''

        # ⚠️ (2026-08-23, FIX BUG THẬT user báo: *"xóa cookie lại ra trang
        # .../tools/flow -> click tạo dự án -> ... -> tạo project mới không cần
        # thiết"*) — bản trước NHỐT toàn bộ phần quay-lại-project vào TRONG
        # `if click_create and _click_create_with_flow_if_present():`. Nhưng
        # màn hình xen giữa "Create with Google Flow" CHỈ hiện khi ĐIỀU HƯỚNG
        # VÀO 1 URL project — nó KHÔNG có trên trang chung. Mà refresh ngay sau
        # khi xoá cookie thì gần như luôn bị bật về đúng trang chung ⇒ không
        # tìm thấy nút ⇒ cả khối bị bỏ qua ⇒ **không bao giờ quay lại project**.
        # Worker kết thúc bước dọn khi đang đứng ở trang chung, batch kế tiếp
        # thấy vậy, thử vào lại project vài lần không được rồi TẠO PROJECT MỚI
        # — đúng hiện tượng user gặp.
        #
        # Thứ tự ĐÚNG (khớp user mô tả): refresh → **vào lại link project** →
        # LÚC ĐÓ màn hình xen giữa mới hiện → bấm → ở trong project.
        # `_return_to_saved_project()` làm CHÍNH XÁC chuỗi đó (mỗi vòng:
        # `driver.get(saved)` → `_click_create_with_flow_if_present()` → kiểm
        # tra), thử lại tối đa 3 lần và **KHÔNG BAO GIỜ tạo project mới**.
        if '/project/' in _url():
            # Vẫn ở URL project sau refresh — nhưng URL KHÔNG đổi khi bị kẹt ở
            # màn hình xen giữa, nên vẫn phải thử bấm.
            if click_create:
                self._click_create_with_flow_if_present()

        if '/project/' not in _url():
            try:
                if not self._return_to_saved_project():
                    self._log('warn', '[batch-clean] Không quay lại được project sau khi xoá '
                                       'cookie — bỏ qua reconcile lần này (batch sau sẽ thử lại).')
                    return
            except Exception as e:
                self._log('warn', f'[batch-clean] Quay lại project cũ lỗi: {e}')
                return

        # ⚠️ (2026-09-04) 2 nhánh trên chỉ soi URL — mà màn hình xen giữa
        # "Create with Google Flow" GIỮ NGUYÊN `/project/{uuid}`, nên "URL
        # đúng" KHÔNG có nghĩa là đã vào được project. Chốt lại bằng kiểm tra
        # UI thật; chưa được thì đi đường phục hồi đầy đủ.
        if not self._project_page_ready():
            self._log('warn', '[batch-clean] URL đúng project nhưng UI chưa sẵn sàng '
                              '(còn kẹt màn hình xen giữa?) — thử đưa về trang làm việc…')
            if not self._ensure_project_page_ready(attempts=2):
                self._log('warn', '[batch-clean] Vẫn chưa vào được trang project — bỏ qua '
                                  'reconcile lần này (batch sau tự chặn không chạy task).')
                return

        # Chỉ reconcile khi CHẮC CHẮN đang ở trang project — chạy trên trang
        # chung là vô ích mà vẫn tốn 1 lần refresh + ~15s chờ interceptor.
        try:
            self._reconcile_project_media(set())
        except Exception as exc:
            self._log('warn', f'[batch-clean] Reconcile lấy task đã hoàn tất sau batch lỗi (bỏ qua): {exc}')

    def _clear_profile_if_sleeping(self, profile_row: dict | None) -> bool:
        """(2026-08-10, theo yêu cầu user "luồng mới nếu rơi vào trạng thái ngủ
        do lỗi nhiều, thì sẽ tự 'làm mới profile' đó") — gọi trong `finally`
        của MỌI vòng lặp worker (VEO3 `run()`, `_run_gemini_loop()`,
        `_run_gemini_loop_concurrent()`, `_run_chatgpt_loop()`) NGAY SAU
        `self.driver.quit()` — tại thời điểm này Chrome đã đóng hẳn
        (`driver.quit()` blocking tới khi process thoát hẳn), an toàn để đụng
        filesystem `profile_dir` (mirror guard "profile phải đóng" đã áp dụng
        cho route `/clear_data`, xem §11.29 — ở đây gọi THẲNG `pm.clear_cache_only()`
        cục bộ, không qua HTTP route đó, vì đang tự biết chắc chắn Chrome vừa
        đóng bởi CHÍNH lời gọi `driver.quit()` ngay phía trên).

        `profile_row` = kết quả `pm.get(self.profile_id)` gọi TRƯỚC ĐÓ trong
        cùng `finally` (dùng lại, không query 2 lần). Sleeping = do
        `_record_error()`/`_handle_task_error()` vừa set NGAY TRƯỚC KHI thread
        thoát (quá nhiều lỗi liên tiếp/trong khoảng thời gian ngắn) — coi đây
        là dấu hiệu khả năng cao CACHE Chrome đã hỏng theo cách gì đó (render
        lỗi, JS state kẹt...), tự dọn cache trước khi dispatcher đánh thức lại
        — KHÔNG cần user tự vào GUI bấm tay.

        ⚠️ (2026-08-12, ĐỔI HƯỚNG theo yêu cầu user "lỗi nhiều vào trạng thái
        ngủ tự xóa cache không xóa cookie") — TRƯỚC ĐÂY gọi `pm.clear_browser_data()`
        (xoá SẠCH TOÀN BỘ `profile_dir`, kể cả `Cookies`/`Web Data`/`Login Data`
        → MẤT ĐĂNG NHẬP GOOGLE mỗi lần sleeping, buộc `_ensure_google_login()`
        tự đăng nhập lại bằng mật khẩu đã lưu ở LẦN CHẠY KẾ TIẾP — tốn thời
        gian + rủi ro Google chặn/đòi xác minh nếu đăng nhập lại quá thường
        xuyên từ cùng 1 script). Giờ gọi `pm.clear_cache_only()` — CHỈ xoá các
        thư mục cache thuần (Cache/Code Cache/GPUCache/...), GIỮ NGUYÊN
        cookie/session — profile vẫn đăng nhập sẵn ở lần chạy kế tiếp, không
        cần qua `_ensure_google_login()`'s nhánh nhập mật khẩu. Nút "Làm mới
        profile" THỦ CÔNG trong GUI (có dialog cảnh báo mất đăng nhập) VẪN
        dùng `pm.clear_browser_data()` như cũ, không đổi — đây CHỈ đổi đường
        TỰ ĐỘNG này.

        ⚠️ (2026-08-18, theo yêu cầu user "số 2 khi sleep thì tạm không dùng
        đến") — `pm.clear_cache_only()` giờ CHỈ còn xoá cache, bước xoá cookie
        (từng thêm 2026-08-14) đã TẠM TẮT ở `managers.py` — cơ chế xoá cookie
        "mỗi batch" (`_cdp_clear_cache_and_cookies()`, ĐỘC LẬP hoàn toàn, xem
        docstring hàm đó) mới là nơi ĐANG THẬT SỰ xoá cookie hiện tại.

        Best-effort — lỗi không làm crash worker (đang ở `finally`, sắp thoát
        dù thế nào), chỉ log. Trả `True` nếu profile đang sleeping (để caller
        biết KHÔNG ghi đè status 'offline' — giữ nguyên hành vi cũ, để dispatcher
        tự quản lý theo `_sleep_until_by_pid`)."""
        is_sleeping = bool(profile_row and profile_row.get('status') == 'sleeping')
        if is_sleeping:
            # (2026-08-20) `_sleep_wipe_all_cookies` — CHỈ bậc thang escalation
            # THEO BATCH (`_finish_batch_cleanup()`, chạm `batch_fail_count_
            # before_sleep`) mới bật cờ này: xoá TẤT CẢ cookie + cache rồi ÉP
            # bước check đăng nhập Google chạy ở lần khởi động worker kế tiếp.
            # 2 bậc thang THEO TASK cũ (`_handle_task_error`/`_record_error`)
            # KHÔNG bật cờ → giữ nguyên hành vi cũ (chỉ xoá cache, không đụng
            # cookie, không ép login) — 2 đường hoàn toàn tách biệt.
            wipe_all = self._sleep_wipe_all_cookies
            if getattr(self, '_sleep_skip_clean', False):
                self._sleep_skip_clean = False
                self._log('info', '[auto-refresh] Profile ngủ do bậc thang batch — KHÔNG xoá '
                                   'cookie/cache (tuỳ chọn "xoá cookie/cache khi ngủ" đang tắt).')
                return True
            # CloakBrowser dùng thư mục profile riêng (<profile_dir>_cloak) → dọn đúng thư mục đó
            clean_dir = None
            try:
                from .cloak_browser import cloak_enabled, cloak_profile_dir
                if cloak_enabled():
                    clean_dir = cloak_profile_dir(self.profile)
            except Exception:
                pass
            if wipe_all:
                self._log('warn', '[auto-refresh] Profile ngủ do BẬC THANG BATCH — xoá TẤT CẢ '
                                   'cookie + cache (chấp nhận mất đăng nhập, sẽ tự đăng nhập '
                                   'lại ở lần chạy sau)...')
            else:
                self._log('warn', '[auto-refresh] Profile đang ngủ do lỗi nhiều — tự xoá cache '
                                   '(KHÔNG đụng cookie — tạm tắt, xem _cdp_clear_cache_and_cookies() '
                                   'cho cơ chế xoá cookie hiện tại) trước khi thử lại...')
            try:
                if wipe_all:
                    result = pm.clear_cache_and_all_cookies(self.profile_id, profile_dir=clean_dir)
                    # Vừa xoá sạch cookie ⇒ CHẮC CHẮN đã đăng xuất Google. Ép
                    # worker KẾ TIẾP của profile này chạy `_ensure_google_login()`
                    # đầy đủ bất kể `google_login_check_enabled` đang tắt — nếu
                    # không, nó sẽ vào thẳng labs.google ở trạng thái chưa đăng
                    # nhập và MỌI task sau đó đều lỗi ở bước tìm DOM.
                    _force_login_check.add(self.profile_id)
                    # (2026-08-23) `removed` giờ gộp cả cache LẪN site-data
                    # (cookie/localStorage/IndexedDB/Service Worker/Trust
                    # Tokens...) — xem `managers.py::_purge_all_site_data()`.
                    self._log('ok', f'[auto-refresh] ✔ Đã xoá cache + TOÀN BỘ cookie & site data '
                                     f'({len(result.get("removed", []))} mục: localStorage/IndexedDB/'
                                     'Service Worker/Trust Tokens...). '
                                     'Lần chạy sau sẽ tự check/đăng nhập lại Google.')
                    self._sleep_wipe_all_cookies = False
                else:
                    result = pm.clear_cache_only(self.profile_id, profile_dir=clean_dir)
                    self._log('ok', f'[auto-refresh] ✔ Đã xoá cache ({len(result.get("removed", []))} mục).')
                # (2026-08-17) TRƯỚC ĐÂY `errors` (populate bởi
                # `_clear_chrome_cache_only()`/`_purge_non_login_cookies()` khi
                # 1 file/DB bị khoá hoặc lỗi đọc/ghi) KHÔNG BAO GIỜ được log —
                # khiến "0 cookie xoá" không phân biệt được là ĐÚNG (không có
                # gì để xoá) hay lỗi ẩn (DB Cookies bị khoá ngay sau khi Chrome
                # vừa đóng). Log rõ nếu có, không còn nuốt im lặng.
                errs = result.get('errors') or []
                if errs:
                    self._log('warn', f'[auto-refresh] ⚠ {len(errs)} lỗi lúc dọn cache/cookie: '
                                       f'{"; ".join(errs)}')
            except Exception as e:
                self._log('error', f'[auto-refresh] Xoá cache/cookie thất bại: {e}')
        return is_sleeping

    # ── Run tasks nhận từ 1 lần heartbeat (có thể nhiều nếu max_concurrent > 1) ──
    #
    # DOM mode + >1 task: batch submit (_run_tasks_batch).
    # API mode: _run_tasks_api_batch — gửi prompt liên tiếp, không chờ tile
    # (API không hiện trên UI); ảnh lấy URL ngay từ HTTP, video reconcile sau.
    # Bỏ qua bước check đầu batch nếu cuối batch trước vừa quét project xong
    # trong khoảng này (lô không có task chạy lại) — tránh refresh + quét 2 lần liền.
    _PRECHECK_SKIP_SECS = 60

    def _precheck_batch_done(self, tasks: list) -> list:
        """(2026-09-19) Check ĐẦU BATCH: quét project Flow hiện tại, task nào
        trong lô ĐÃ render xong từ trước (media mang `TASK_{id}:` còn nằm
        trong project, server chưa tải) thì cho server tải về + đánh dấu xong
        rồi BỎ khỏi lô — chỉ trả về các task THẬT SỰ còn phải gửi prompt.

        Theo yêu cầu user "mỗi lần vào chạy task có check tất cả task cũ chạy
        trước đó trong project đó được download về chưa tránh trường hợp chạy
        nhiều lần 1 task". Trước đây chỉ có (a) quét CUỐI batch — bỏ sót batch
        đầu tiên sau khi mở worker/ngủ dậy và các nhánh không quét được — và
        (b) check riêng từng task retry, CHỈ ở mode DOM (mode API gửi lại
        thẳng).

        - Lô có task chạy lại (`retry_count>0`) → quét với cửa sổ RỘNG
          `reconcile_retry_lookback_secs` (media lần trước có thể cũ hơn 2 giờ).
        - Lô không có task chạy lại và vừa quét < `_PRECHECK_SKIP_SECS` → bỏ qua.
        - Chỉ xét project Flow đang mở; media nằm ở project KHÁC coi như chưa
          tạo (quyết định user).
        - Best-effort: không vào được trang project / quét lỗi → giữ nguyên lô,
          để bước submit tự xử lý như trước."""
        if self.worker_mode not in ('dom', 'api') or not tasks:
            return tasks
        has_retry = any(int(t.get('retry_count') or 0) > 0 for t in tasks)
        last = getattr(self, '_last_reconcile_at', 0) or 0
        if not has_retry and time.time() - last < self._PRECHECK_SKIP_SECS:
            self._log('info', f'[pre-check] Vừa quét project {time.time() - last:.0f}s trước — '
                               'bỏ qua bước check đầu batch')
            return tasks
        try:
            if not self._ensure_project_page_ready():
                self._log('warn', '[pre-check] Chưa vào được trang project — bỏ qua check '
                                   'task đã hoàn tất từ trước')
                return tasks
            lookback = None
            if has_retry:
                lookback = max(
                    float(self._server_settings.get('reconcile_retry_lookback_secs', 86400)),
                    float(self._server_settings.get('reconcile_lookback_secs', 7200)))
            ids = {t['id'] for t in tasks}
            self._log('info', f'[pre-check] Quét project trước khi gửi {len(tasks)} task'
                               + (f' (có task chạy lại — cửa sổ {lookback:.0f}s)' if has_retry else ''))
            matched = self._reconcile_project_media(ids, lookback_secs=lookback)
        except Exception as e:
            self._log('warn', f'[pre-check] Lỗi khi quét project: {e} — giữ nguyên lô')
            return tasks
        done = [t for t in tasks if t['id'] in matched]
        for t in done:
            self._log('ok', f'✔ Task #{t["id"]} — media đã có sẵn trong project từ lần '
                             'chạy trước, server đã tải về — KHÔNG gửi lại prompt')
            self._handle_task_success()
        return [t for t in tasks if t['id'] not in matched]

    def _process_tasks(self, tasks: list) -> bool:
        """Trả True nếu escalation THEO TASK đã cho profile 'ngủ' giữa chừng —
        caller (run()) phải dừng vòng lặp ngay, không heartbeat tiếp.

        ⚠️ Bậc thang escalation THEO BATCH (2026-08-20, `_finish_batch_cleanup()`
        chạy trong `finally`) KHÔNG báo qua giá trị trả về này — nó set
        `self._sleep_until`, và `run()` tự check mốc đó sau mỗi vòng lặp (dùng
        `return` trong `finally` sẽ nuốt exception đang lan ra, xem comment ở
        cuối hàm)."""
        # (2026-08-20) Reset bộ đếm task-thành-công CỦA RIÊNG BATCH NÀY — PHẢI ở
        # đây (đầu mỗi batch), không phải chỉ ở `__init__`: nếu để tích luỹ qua
        # nhiều batch thì chỉ cần 1 task thành công ở batch đầu là MỌI batch lỗi
        # về sau đều bị hiểu nhầm là "batch thành công", bậc thang batch không
        # bao giờ kích hoạt được.
        # (2026-09-14) Chạy theo project + email: 1 lô chỉ được thuộc 1 project
        # Flow (backend đã gom đúng như vậy — đây là lưới an toàn cho backend cũ):
        # lô lẫn nhiều project thì chạy lần lượt từng nhóm, mỗi nhóm 1 batch.
        if self._bind_mode_enabled() and len(tasks) > 1:
            groups: dict = {}
            for t in tasks:
                groups.setdefault(str(t.get('flowProjectId') or ''), []).append(t)
            if len(groups) > 1:
                glist = list(groups.values())
                self._log('warn', f'[flow-bind] Lô {len(tasks)} task thuộc {len(glist)} project '
                                  f'Flow khác nhau — chạy lần lượt từng nhóm')
                for gi, g in enumerate(glist):
                    stopped = self._stop.is_set() or getattr(self, '_sleep_until', 0) > time.time()
                    if stopped:
                        for rest in glist[gi:]:
                            for t in rest:
                                self._report_task_error(t['id'], 'Worker dừng trước khi tới lượt '
                                                                 'nhóm project Flow của task này')
                        return False
                    if self._process_tasks(g):
                        for rest in glist[gi + 1:]:
                            for t in rest:
                                self._report_task_error(t['id'], 'Profile chuyển sang ngủ trước khi '
                                                                 'tới lượt nhóm project Flow của task này')
                        return True
                return False
        self._apply_flow_binding(tasks)
        self._batch_success_count = 0
        # (2026-09-13) Mốc đầu/cuối batch cho log điều hướng — xem `_nav_batch_end()`.
        self._nav_batch_begin(tasks)
        # Keepalive (2026-07-17): xác nhận qua DB thật — task #2457 (imageToVideo)
        # bị server set error_message='Reclaimed by reaper (machine offline / task
        # timeout)' dù client_tool log cho thấy worker vẫn đang xử lý bình thường
        # (upload xong, prompt đã submit, đang chờ reconcile vòng 1-6/10 — hoàn
        # toàn khỏe mạnh). Nguyên nhân: `run()` chỉ gọi `self._heartbeat()` MỘT LẦN
        # trước khi vào `_process_tasks()`, rồi bị block hoàn toàn cho tới khi task
        # xong (DOM upload + `_wait_and_reconcile_tasks` một mình đã có thể block
        # tới 10 phút) — không heartbeat lại trong suốt thời gian đó. Server
        # (`backend/routes/heartbeat.py`) mark machine 'offline' nếu `last_seen`
        # quá 30s, kiểm tra này chạy MỖI KHI CÓ MÁY KHÁC BẤT KỲ gọi heartbeat —
        # nên machine này bị mark offline giữa chừng dù vẫn đang chạy thật, và
        # reaper phía server (`_reap_stuck_tasks`, xem root CLAUDE.md §4.2) thu hồi
        # ngay task 'processing' của 1 machine 'offline' bất kể task có thật sự bị
        # treo hay không. Đây CHÍNH LÀ lớp bug đã từng gặp và fix cho worker Gemini
        # Selenium (xem CHANGELOG 2026-07-06 "Bug: machine bị đánh dấu offline giữa
        # chừng khi task chạy lâu", `_run_task_gemini`/`_heartbeat_gemini`) — áp
        # dụng lại đúng pattern keepalive-thread cho worker DOM/API.
        #
        # Bản đầu (2026-07-17) gọi lại NGUYÊN self._heartbeat() với lý do "server
        # tính slot trống từ in-flight THẬT trong DB, không dựa runningCount client
        # tự báo nên an toàn" — ĐÚNG VỀ LOGIC nhưng bỏ sót 1 RACE THẬT, bắt được qua
        # log production (2026-07-18, batch 2 task #3618/#3620): reconcile của CẢ 2
        # task trong batch hoàn tất gần như ĐỒNG THỜI → in-flight của machine này
        # tạm về 0 ĐÚNG NGAY LÚC keepalive gọi heartbeat (trước khi `_process_tasks()`
        # kịp return để vòng lặp chính `run()` tự heartbeat lại) → server thấy "còn
        # slot trống", giao luôn 2 task MỚI (#3622/#3624) cho machine này ngay trong
        # request keepalive — nhưng object task đó không được `_process_tasks()` xử
        # lý (đang bận, sắp return), bị log "Keepalive heartbeat nhận nhầm task...
        # bỏ qua" vứt thẳng → 2 task kẹt ở status assigned/processing cho tới khi
        # reaper server thu hồi lại, làm CHẬM TIẾN ĐỘ batch (task coi như "mất tích"
        # 1 khoảng thời gian thay vì được xử lý ngay). Fix: `_heartbeat(keepalive_
        # only=True)` — gửi `keepaliveOnly: true`, backend (`backend/routes/
        # heartbeat.py`) BỎ QUA HẲN bước giao task cho request này (không chỉ dựa
        # vào in-flight tính đúng lúc gọi — loại bỏ cả khe hở race về thời điểm).
        stop_keepalive = threading.Event()

        def _keepalive():
            while not stop_keepalive.wait(POLL_INTERVAL):
                try:
                    self._heartbeat(keepalive_only=True)
                except Exception:
                    pass

        keepalive_thread = threading.Thread(target=_keepalive, daemon=True)
        keepalive_thread.start()

        # (2026-08-18, ĐÃ CHUYỂN — xem docstring `_recover_flow_project_page_
        # after_cache_clear()`) — bước "reconcile toàn bộ project TRƯỚC khi
        # nhận task mới" TỪNG nằm ở đây (đầu batch) đã dời sang CUỐI batch
        # TRƯỚC ĐÓ (trong `finally` bên dưới, ngay sau bước click "Create with
        # Google Flow") theo ĐÚNG thứ tự user mô tả lại toàn bộ quy trình —
        # tránh refresh+đọc `projectInitialData` 2 LẦN LIÊN TIẾP (đầu batch
        # này + cuối batch trước) cho CÙNG 1 mục đích.

        # (2026-08-17) `escalated` — biết TRƯỚC khi `finally` chạy liệu batch
        # này có kết thúc bằng việc profile sắp 'sleeping'/dừng hẳn hay không —
        # nếu có, KHÔNG cần xoá cache/cookie qua CDP ở đây (worker sắp thoát,
        # `driver.quit()` rồi `_clear_profile_if_sleeping()`/tương đương sẽ tự
        # dọn kỹ hơn qua file ngay sau đó, dọn 2 lần liên tiếp là phí công).
        escalated = False
        try:
            # (2026-09-03, FIX THẬT — user: "luồng là nhập batch mới giống API
            # rồi mới check các thứ chứ sao vừa tạo xong 1 task lại đi check
            # rồi") — TRƯỚC ĐÂY chỉ dùng `_run_tasks_batch()` (submit CẢ BATCH
            # trước, reconcile SAU CÙNG — đúng luồng user muốn) khi
            # `len(tasks) > 1`; batch chỉ có ĐÚNG 1 task (rất phổ biến khi
            # backlog mỏng hoặc `max_concurrent` thấp) rơi xuống vòng lặp
            # generic bên dưới → `_run_task_dom()`, hàm này submit RỒI GỌI
            # `_wait_and_reconcile_tasks()` NGAY LẬP TỨC cho ĐÚNG 1 task đó —
            # khác hẳn `worker_mode='api'` (nhánh ngay dưới) vốn LUÔN dùng
            # `_run_tasks_api_batch()` bất kể `len(tasks)` bao nhiêu. Bỏ điều
            # kiện `and len(tasks) > 1` — DOM mode giờ ĐỐI XỨNG với API mode:
            # luôn submit hết batch (kể cả batch chỉ có 1 task) rồi mới
            # reconcile 1 lần cho cả batch, không còn check ngay sau từng task.
            # (2026-09-19) Check đầu batch — bỏ các task đã render xong từ trước.
            if self.worker_mode in ('dom', 'api'):
                tasks = self._precheck_batch_done(tasks)
                if not tasks:
                    self._log('ok', '[pre-check] Mọi task trong lô đã có media sẵn — '
                                    'không cần gửi prompt nào')
                    pm.set_status(self.profile_id, 'idle', clear_task=True,
                                  pid=threading.get_ident())
                    return False
            if self.worker_mode == 'dom':
                escalated = self._run_tasks_batch(tasks)
                return escalated
            if self.worker_mode == 'api':
                escalated = self._run_tasks_api_batch(tasks)
                return escalated

            delay = self._server_settings.get('task_delay_secs', 10)
            for i, task in enumerate(tasks):
                if self._stop.is_set():
                    break
                if self._run_task(task):
                    escalated = True
                    return True
                if i < len(tasks) - 1:
                    self._stop.wait(delay)
            return False
        finally:
            stop_keepalive.set()
            keepalive_thread.join(timeout=POLL_INTERVAL + 2)
            # (2026-08-20) CỐ Ý KHÔNG `return` ở đây — `return` trong `finally`
            # NUỐT LUÔN exception đang lan ra (batch lỗi nặng sẽ biến mất im
            # lặng, `run()` không log/không check driver còn sống). Thay vào đó
            # `_finish_batch_cleanup()` tự set `self._sleep_until` khi cần ngủ,
            # và `run()` tự check mốc đó sau mỗi vòng lặp (cả nhánh bình thường
            # LẪN nhánh except) để quyết định thoát loop.
            if not escalated:
                self._finish_batch_cleanup()
            # Đặt SAU `_finish_batch_cleanup()` — bước dọn cuối batch cũng
            # refresh/điều hướng, phải được tính vào tổng kết của batch này.
            try:
                self._nav_batch_end()
            except Exception:
                pass

    def _finish_batch_cleanup(self) -> bool:
        """(2026-08-20) Chạy CUỐI MỖI BATCH (trong `finally` của `_process_tasks()`,
        chỉ khi bậc thang THEO TASK chưa cho ngủ) — đánh giá batch vừa xong THÀNH
        CÔNG hay LỖI rồi áp bậc thang escalation THEO BATCH tương ứng. Trả `True`
        nếu vừa cho profile ngủ (caller phải dừng worker loop ngay).

        Theo yêu cầu user: "Nếu batch gửi lên 5 task 1 lúc mà thành công 1 vẫn
        tính batch thành công -> nhưng nếu cả batch đều không thành công liên tiếp
        2 batch liền -> thì mới xóa cache cookie không ảnh hưởng login rồi click
        button create lại -> nhưng vẫn lỗi tiếp tục batch kế tiếp đó -> thì chuyển
        sang chế độ ngủ -> trong lúc ngủ xóa tất cả cookie - cache -> rồi check
        login -> đăng nhập vào project lại như trước -> vòng lặp cứ thế".

        3 nhánh:
          • **Batch THÀNH CÔNG** (`_batch_success_count > 0` — ĐỦ 1 task thành công
            là tính, dù N-1 task còn lại lỗi hết): reset bộ đếm về 0, KHÔNG dọn
            cookie, KHÔNG click Create — chỉ refresh + reconcile như bình thường.
          • ~~Chạm `batch_fail_count_before_cleanup` → dọn cookie + click Create~~
            (2026-09-24) ĐÃ BỎ theo yêu cầu user — batch lỗi chưa tới ngưỡng ngủ
            chỉ refresh + reconcile như batch thường.
          • **Chạm `batch_fail_count_before_sleep`** (mặc định 3): cho profile ngủ
            `error_sleep_secs`. Tuỳ chọn `batch_sleep_wipe_cookies` (mặc định bật):
            bật cờ `_sleep_wipe_all_cookies` để `_clear_profile_if_sleeping()` (chạy
            sau `driver.quit()`) xoá TẤT CẢ cookie + cache rồi ép check đăng nhập
            ở lần chạy kế tiếp; tắt → chỉ ngủ, không xoá gì (`_sleep_skip_clean`).

        ⚠️ (2026-08-20, ĐỔI HÀNH VI CŨ theo yêu cầu user "theo phương án 1: nhưng
        không click create vì chỉ khi xóa cookie mới cần click") — TRƯỚC ĐÂY
        (2026-08-17/18) `_cdp_clear_cache_and_cookies()` + click "Create with
        Google Flow" chạy sau MỌI batch VÔ ĐIỀU KIỆN. Giờ CHỈ chạy ở nhánh chạm
        ngưỡng dọn dẹp. Bước refresh + reconcile (lấy task đã render xong về,
        đánh dấu `done`) VẪN chạy MỌI batch như cũ — KHÔNG bị gate, vì đó là
        đường DUY NHẤT media hoàn tất được ghi nhận về server sau batch."""
        # `gemini_video`/`gemini_image` đứng trên gemini.google.com — không có
        # project Flow/nút "Create with Google Flow", KHÔNG gọi
        # `_recover_flow_project_page_after_cache_clear()` (đó là bước riêng
        # của luồng Flow: refresh trang project + reconcile — 2 worker_mode
        # này hoàn tất từng task ngay tại chỗ, không cần bước đó). Cookie/site
        # data VẪN được dọn đúng domain riêng (`gemini.google.com`) ở rung
        # `threshold_clean` bên dưới — `_cdp_clear_cache_and_cookies()` tự tra
        # domain theo `worker_mode`, không gated bởi `is_flow`. Vẫn đếm batch
        # lỗi + ngủ như thường (bậc thang này không phụ thuộc Flow).
        # (2026-09-24) Nấc `threshold_clean` nhắc ở trên ĐÃ BỎ.
        is_flow = self.worker_mode in ('dom', 'api')

        if self._batch_success_count > 0:
            if self._consecutive_failed_batches:
                self._log('info', f'[batch-escalation] Batch thành công '
                                   f'({self._batch_success_count} task) — reset bộ đếm '
                                   f'batch lỗi liên tiếp (đang {self._consecutive_failed_batches}).')
            self._consecutive_failed_batches = 0
            if is_flow:
                self._recover_flow_project_page_after_cache_clear(click_create=False)
            return False

        self._consecutive_failed_batches += 1
        n = self._consecutive_failed_batches
        threshold_sleep = int(self._server_settings.get('batch_fail_count_before_sleep', 3) or 3)

        if n >= threshold_sleep:
            sleep_secs = int(self._server_settings.get('error_sleep_secs', 300))
            # (2026-09-24) Tuỳ chọn `batch_sleep_wipe_cookies`: bật = ngủ + xoá
            # TẤT CẢ cookie/cache + ép check đăng nhập (hành vi cũ); tắt = chỉ
            # ngủ, KHÔNG đụng cookie/cache gì cả.
            wipe = bool(int(self._server_settings.get('batch_sleep_wipe_cookies', 1) or 0))
            self._log('error', f'[batch-escalation] {n} batch lỗi liên tiếp (không task nào '
                                f'thành công) — cho profile "ngủ" {sleep_secs}s'
                                + (' + xoá TẤT CẢ cookie/cache, ép check đăng nhập Google lần chạy sau.'
                                   if wipe else ' (KHÔNG xoá cookie/cache — tuỳ chọn đang tắt).'))
            self._consecutive_failed_batches = 0
            self._sleep_wipe_all_cookies = wipe
            self._sleep_skip_clean = not wipe
            pm.set_status(self.profile_id, 'sleeping', clear_task=True, pid=None)
            self._sleep_until = time.time() + sleep_secs
            # Ghi vào dict module-level — thread worker sắp thoát, dispatcher cần
            # đọc được mốc hết ngủ SAU KHI object worker này bị reap (cùng lý do
            # đã ghi ở `_handle_task_error()`).
            _sleep_until_by_pid[self.profile_id] = self._sleep_until
            return True

        # (2026-09-24) Bỏ nấc "N batch lỗi → dọn cookie labs.google + click
        # Create" theo yêu cầu user — batch lỗi chưa tới ngưỡng ngủ chỉ refresh
        # + reconcile như batch bình thường.
        self._log('warn', f'[batch-escalation] Batch lỗi (không task nào thành công) — '
                           f'{n}/{threshold_sleep} trước khi ngủ.')
        if is_flow:
            self._recover_flow_project_page_after_cache_clear(click_create=False)
        return False

    def _report_task_error(self, task_id, message: str):
        try:
            self._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                      body={'taskId': task_id, 'machineCode': self.machine_code,
                            'errorMessage': message})
        except Exception:
            pass

    # ── Run one task (dispatcher) ─────────────────────────────────────────────

    def _submit_video_api_task(self, task: dict,
                              uploaded_media_names: list | None = None) -> tuple:
        """Submit 1 video qua API, KHÔNG chờ kết quả.
        `uploaded_media_names` — ảnh đã upload sẵn (batch); None thì tự upload.
        Trả `(ok, stop_now)` — ok=True đã submit; stop_now=True profile phải ngủ."""
        task_id = task['id']
        mode    = task.get('mode', 'textToVideo')
        prompt  = (task.get('prompt_text') or task.get('title') or '')[:80]
        self._log('info', f'▶ Task #{task_id} mode={mode} model={task.get("model","?")} "{prompt}"')
        pm.set_status(self.profile_id, 'processing', task_id=task_id)
        self._task_start(task_id, mode, task.get('prompt_text') or task.get('title') or '')
        try:
            self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                      body={'taskId': task_id, 'machineCode': self.machine_code})
            captcha = self._get_fresh_recaptcha('VIDEO_GENERATION')
            if not captcha:
                raise RuntimeError('Không lấy được reCAPTCHA VIDEO_GENERATION')
            self._call_video_api(task, captcha, uploaded_media_names=uploaded_media_names)
            return True, False
        except Exception as e:
            self._log('error', f'Task #{task_id} lỗi: {e}')
            self._report_task_error(task_id, str(e))
            return False, self._handle_task_error(str(e))

    # ── Đa luồng THẬT SỰ cho Direct API (2026-08-13) ────────────────────────
    # Theo yêu cầu user: "các task image/video veo3 nên chạy đa luồng kiểu có
    # 5 prompt cứ gửi 5 prompt rồi nhận về kq" — làm rõ thêm: "giống đa luồng 5
    # prompt thì gữi 5 api 5 luồng khác nhau rồi nhận phản hồi" — nghĩa là N
    # THREAD Python THẬT, mỗi thread tự bắn 1 request rồi BLOCK CHỜ ĐÚNG
    # response của chính nó, thay vì tuần tự "gửi 1 → chờ xong → gửi 2".
    #
    # (2026-09-24) Ghi chú lịch sử: bản đầu dựa trên đường aisandbox
    # (curl_cffi, tách khỏi Selenium) — đường đó đã GỠ; giờ generate đi qua
    # batchexecute (`flow_be.py`). Chỉ CÓ 1 bước THẬT SỰ cần driver:
    # mint reCAPTCHA token (`_get_fresh_recaptcha()`, dùng
    # `execute_async_script`) — 1 Selenium session KHÔNG an toàn nếu nhiều
    # thread cùng gọi lệnh 1 lúc (1 session = 1 command queue), nên bước này
    # LUÔN làm TUẦN TỰ TRƯỚC (nhanh, ~1-2s/task) trong vòng lặp "prep" ở đầu
    # mỗi hàm dưới đây — chỉ phần TỐN THỜI GIAN NHẤT (Google xử lý ảnh/video)
    # mới thực sự chạy song song. Mọi mutation state không thread-safe
    # (`_handle_task_success`/`_handle_task_error`/`_task_start`/counters) chỉ
    # được gọi từ THREAD CHÍNH (vòng lặp `as_completed`), KHÔNG BAO GIỜ từ bên
    # trong `_worker()` chạy trong ThreadPoolExecutor.
    #
    # Số thread = số task trong batch — batch size đã bị chặn bởi
    # `selenium_profiles.max_concurrent` (server chỉ giao tối đa từng đó task
    # 1 lần heartbeat, xem §11.9 CLAUDE.md) nên không cần thêm giới hạn nào
    # khác — đúng đã là "N task/lần" mà user cấu hình.

    def _run_image_tasks_concurrent(self, tasks: list, dom_queue=None) -> bool:
        """N task ẢNH — MỖI task tự mint reCAPTCHA + khởi động thread Direct
        API riêng (ThreadPoolExecutor), thread ĐÃ khởi động chạy song song
        bình thường trong khi vòng lặp GIÃN CÁCH random
        (`thread_stagger_min_secs`/`thread_stagger_max_secs`, mặc định 10-15s
        — theo yêu cầu user "cho phép setting random giây... để khởi động
        thread tiếp theo") rồi mới chuyển sang task kế — KHÔNG còn bắn tất cả
        gần như cùng lúc (trông giống bot). Ref ảnh (nếu có, imageToImage) tự
        upload BÊN TRONG `_call_image_api_v2()` của chính task đó, không cần
        xử lý riêng ở đây. Task nào thiếu điều kiện Direct API (chưa có
        token/captcha/project_id) rơi về `_run_task_api()` (UI-driven, dùng
        driver) — KHÔNG thread-safe nên xử lý TUẦN TỰ riêng, tách khỏi lô
        song song. Trả True nếu escalation cho profile ngủ."""
        if not tasks:
            return False

        stagger_min = float(self._server_settings.get('thread_stagger_min_secs', 10))
        stagger_max = float(self._server_settings.get('thread_stagger_max_secs', 15))
        if stagger_max < stagger_min:
            stagger_max = stagger_min

        # (2026-09-24) Task không đi được API (thiếu điều kiện / 403) — xử lý
        # sau vòng song song: chuyển DOM nếu `dom_queue` (setting bật), ngược lại
        # báo lỗi. KHÔNG còn tự rơi về UI-driven (`_run_task_api`) như trước —
        # API mode mặc định CHỈ chạy API.
        ui_tasks: list = []
        stop_now = False
        futures = {}

        def _worker(task, captcha):
            return self._call_image_api_v2(task, captcha)

        with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
            for i, task in enumerate(tasks):
                if self._stop.is_set():
                    break
                # Kiểm tra điều kiện RẺ trước, mint reCAPTCHA sau — mint là 1 lời
                # gọi THẬT tới Google; task chắc chắn không đi API thì đừng gọi
                # (log 09:07 cho thấy mỗi task hỏng vẫn mint 1 token vô ích, vừa
                # phí vừa dễ bị tính là nhịp bất thường).
                if not self._extract_project_id():
                    ui_tasks.append(task)
                    continue
                captcha = self._get_fresh_recaptcha('IMAGE_GENERATION')
                if not captcha:
                    ui_tasks.append(task)
                    continue

                task_id = task['id']
                mode = task.get('mode', 'textToImage')
                pm.set_status(self.profile_id, 'processing', task_id=task_id)
                self._task_start(task_id, mode, task.get('prompt_text') or task.get('title') or '')
                try:
                    self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                              body={'taskId': task_id, 'machineCode': self.machine_code})
                except Exception:
                    pass

                if self._parse_source_media(task):
                    self._log('info', f'⬆ Upload reference image TASK #{task_id}…')
                self._log('info', f'▶ Task #{task_id} — chạy media (thread song song)…')
                fut = pool.submit(_worker, task, captcha)
                futures[fut] = task

                if i < len(tasks) - 1:
                    delay = random.uniform(stagger_min, stagger_max)
                    self._log('info', f'⏳ Chờ {delay:.1f}s trước khi khởi động thread tiếp theo…')
                    if self._stop.wait(delay):
                        break

            for fut in as_completed(futures):
                task = futures[fut]
                task_id = task['id']
                try:
                    results_urls = fut.result()
                except Exception as e:
                    err_str = str(e)
                    if self._api_task_failed(task, f'(song song) {err_str}', dom_queue):
                        stop_now = True
                    continue
                self._log('ok', f'✔ Task #{task_id} — {len(results_urls)} image(s) (song song)')
                # (2026-08-14, fix bug thật "task hoàn tất nhưng không thấy
                # media mới" khi Render lại) — ưu tiên `u['name']` (uuid THẬT
                # từ `_call_image_api_v2()`/`flow_api.parse_image_results()`,
                # khác nhau mỗi lần generate); fallback tên tự chế CHỈ khi
                # thiếu (không nên xảy ra ở đường Direct API, phòng hờ).
                media = [{'name': u.get('name') or f'img_{task_id}_{i+1}', 'url': u['url']}
                         for i, u in enumerate(results_urls)]
                try:
                    self._req('POST', f'{FLOW_SERVER}/api/media/task/download', timeout=120,
                              body={'taskId': task_id, 'machineCode': self.machine_code,
                                    'cueId': task.get('cue_id'), 'mode': task.get('mode'),
                                    'media': media, 'flowProjectId': self._extract_project_id()})
                    self._handle_task_success()
                except Exception as e:
                    self._log('error', f'Task #{task_id} lỗi lúc báo kết quả: {e}')
                    self._report_task_error(task_id, str(e))
                    if self._handle_task_error(str(e)):
                        stop_now = True

        for task in ui_tasks:
            if stop_now:
                self._report_task_error(task['id'], 'Dừng trước khi generate (profile chuyển sang ngủ)')
                continue
            reason = ('không có project id' if not self._extract_project_id()
                      else 'không lấy được reCAPTCHA')
            if self._api_task_failed(task, f'Không gọi được API tạo ảnh ({reason})', dom_queue):
                stop_now = True

        return stop_now

    def _run_video_tasks_staggered(self, tasks: list, dom_queue=None) -> tuple:
        """N task VIDEO — upload ref (nếu có, NGAY TRƯỚC lúc khởi động CHÍNH
        task đó — KHÔNG còn upload cả lô 1 lượt trước khi generate như bản
        cũ 2026-08-13) + mint reCAPTCHA rồi khởi động thread submit, GIÃN
        CÁCH random (`thread_stagger_min_secs`/`thread_stagger_max_secs`,
        mặc định 10-15s — theo yêu cầu user "cho phép setting random giây...
        để khởi động thread tiếp theo") trước khi chuyển sang task tiếp theo
        — task đã khởi động vẫn chạy song song trong lúc chờ. Khác ảnh — API
        video KHÔNG hiện tile/URL trên UI, "phản hồi" mỗi thread nhận được
        chỉ xác nhận đã tạo workflow — kết quả THẬT (video xong) vẫn phải lấy
        SAU qua reconcile `projectInitialData` (KHÔNG đổi phần đó, xem
        caller). Trả `(video_pending: dict, stop_now: bool, attempted_ids)`
        — `attempted_ids` để caller biết task nào ĐÃ được thử (thành công
        hay lỗi đều tính), phân biệt với task chưa từng chạm tới vì batch
        dừng sớm (escalation) — caller báo lỗi "Dừng trước khi generate"
        riêng cho nhóm đó."""
        if not tasks:
            return {}, False, set()

        stagger_min = float(self._server_settings.get('thread_stagger_min_secs', 10))
        stagger_max = float(self._server_settings.get('thread_stagger_max_secs', 15))
        if stagger_max < stagger_min:
            stagger_max = stagger_min

        attempted_ids: set = set()
        video_pending: dict = {}
        stop_now = False
        futures = {}

        def _worker(task, captcha, names):
            self._call_video_api(task, captcha, uploaded_media_names=names)

        with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
            for i, task in enumerate(tasks):
                if self._stop.is_set():
                    break
                task_id = task['id']
                mode = task.get('mode', 'textToVideo')
                attempted_ids.add(task_id)
                pm.set_status(self.profile_id, 'processing', task_id=task_id)
                self._task_start(task_id, mode, task.get('prompt_text') or task.get('title') or '')
                try:
                    self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                              body={'taskId': task_id, 'machineCode': self.machine_code})
                    source_media = self._parse_source_media(task)
                    if source_media:
                        self._log('info', f'⬆ Upload reference image TASK #{task_id}…')
                    names = self._prepare_video_uploads(task) if source_media else []
                    # Kiểm tra điều kiện TRƯỚC khi mint reCAPTCHA (mint là 1 lời
                    # gọi THẬT tới Google) — trước đây mint xong mới kiểm tra nên
                    # mỗi task hỏng vẫn đốt 1 token vô ích (log 09:07).
                    captcha = self._get_fresh_recaptcha('VIDEO_GENERATION')
                    if not captcha:
                        raise RuntimeError('Không lấy được reCAPTCHA VIDEO_GENERATION')
                except Exception as e:
                    if self._api_task_failed(task, f'(chuẩn bị) {e}', dom_queue):
                        stop_now = True
                        break
                    prepared = False
                else:
                    prepared = True

                if prepared:
                    self._log('info', f'▶ Task #{task_id} — chạy media (thread song song)…')
                    fut = pool.submit(_worker, task, captcha, names)
                    futures[fut] = task

                # Giãn cách áp dụng cho CẢ task hỏng ở bước chuẩn bị.
                # (2026-09-04, user hỏi "sao không thấy giãn cách trong log"):
                # trước đây `continue` ở nhánh lỗi nhảy thẳng qua đoạn này, nên
                # khi cả lô cùng hỏng thì batch chạy hết trong vài giây — spam
                # `/task/error`, đốt `retry_count` và kích escalation gần như
                # tức thì. Nhịp phải giữ bất kể task thành công hay hỏng.
                if i < len(tasks) - 1:
                    delay = random.uniform(stagger_min, stagger_max)
                    self._log('info', f'⏳ Chờ {delay:.1f}s trước khi sang task tiếp theo…')
                    if self._stop.wait(delay):
                        break

            for fut in as_completed(futures):
                task = futures[fut]
                task_id = task['id']
                try:
                    fut.result()
                except Exception as e:
                    if self._api_task_failed(task, f'(song song) {e}', dom_queue):
                        stop_now = True
                    continue
                self._log('ok', f'✔ Task #{task_id} — đã submit (song song)')
                video_pending[task_id] = task

        return video_pending, stop_now, attempted_ids

    def _project_page_ready(self) -> bool:
        """Trang project ĐÃ SẴN SÀNG chạy task chưa — KHÔNG chỉ đúng URL.

        ⚠️ Kiểm tra URL là KHÔNG ĐỦ: màn hình xen giữa "Create with Google
        Flow" GIỮ NGUYÊN `/project/{uuid}` trên thanh địa chỉ (xem
        `_click_create_with_flow_if_present()`), nên `'/project/' in url` vẫn
        True dù chưa vào được project. Phải soi UI thật của trang làm việc."""
        try:
            current = self.driver.current_url or ''
            if '/project/' not in current:
                return False
        except Exception:
            return False
        # Chế độ gán: phải đúng project Flow đã gán, không phải project bất kỳ.
        bound = self._bound_url()
        if bound and self._project_id_from_url(current) != self._project_id_from_url(bound):
            return False
        try:
            return bool(self._js(
                "return !!(document.querySelector('.ProseMirror[contenteditable=\"true\"]')"
                " || " + _flow_btn_expr('flowSettingsTrigger', _FB_SETTINGS) + ");"))
        except Exception:
            return False

    def _ensure_project_page_ready(self, attempts: int = 3) -> bool:
        """Đưa trình duyệt về ĐÚNG trang project làm việc được, trước khi chạy task.

        (2026-09-04, theo yêu cầu user "bị đá ra ngoài trang có nút create,
        đáng lẽ phải click nút này để về trang project chạy task tiếp, nhưng
        lại chạy task ngay tại đây gây lỗi — hãy ràng buộc kỹ luồng hơn.")

        Trước đây nhánh API mode KHÔNG hề kiểm tra trang trước khi generate
        (chỉ nhánh DOM có `_ensure_flow_page()`), mà `_extract_project_id()`
        đọc project id từ DB chứ không phải từ URL — nên sau 1 lần xoá cookie,
        worker vẫn "chạy" task bình thường trong khi trình duyệt còn kẹt ở màn
        hình "Create with Google Flow"/đăng nhập ⇒ task lỗi hàng loạt."""
        for i in range(max(1, attempts)):
            if self._project_page_ready():
                return True
            self._log('warn', f'Chưa ở trang project làm việc được (lần {i+1}/{attempts}) '
                              f'— thử đưa về đúng trang…')
            # Màn hình xen giữa giữ NGUYÊN URL nên phải BẤM nút, không thể
            # dựa vào điều hướng.
            if self._click_create_with_flow_if_present(timeout=4):
                self._sleep(2)
                if self._project_page_ready():
                    return True
            # Vẫn chưa được → đường phục hồi đầy đủ (đăng nhập nếu cần, quay
            # lại đúng project_url đã lưu, xử lý lại màn hình xen giữa).
            self._ensure_flow_page()
            self._sleep(2)
        ok = self._project_page_ready()
        if not ok:
            try:
                self._log('error', f'Vẫn không vào được trang project sau {attempts} lần '
                                   f'— URL hiện tại: {self.driver.current_url}')
            except Exception:
                pass
        return ok

    def _run_tasks_api_batch(self, tasks: list) -> bool:
        """API mode: ẢNH và VIDEO đều submit SONG SONG với thread launch GIÃN
        CÁCH random (`thread_stagger_min_secs`/`thread_stagger_max_secs`, xem
        `_run_image_tasks_concurrent()`/`_run_video_tasks_staggered()`). Video
        upload ref (nếu có) NGAY TRƯỚC khi khởi động CHÍNH task đó (2026-08-14
        — KHÔNG còn upload cả lô 1 lượt trước như bản cũ), rồi reconcile
        `projectInitialData` NHƯ CŨ (bước CHỜ KẾT QUẢ, khác bước SUBMIT)."""
        # ⚠️ RÀNG BUỘC BẮT BUỘC trước khi generate (2026-09-04): phải THẬT SỰ
        # đang ở trang project làm việc được. Không có bước này, sau 1 lần xoá
        # cookie worker vẫn generate trong lúc còn kẹt ở màn hình "Create with
        # Google Flow" ⇒ cả lô lỗi.
        if not self._ensure_project_page_ready():
            msg = ('Chưa vào được trang project (còn kẹt ở màn hình "Create with '
                   'Google Flow" hoặc trang đăng nhập) — KHÔNG chạy lô này')
            self._log('error', msg)
            for t in tasks:
                self._report_task_error(t['id'], msg)
            return self._handle_task_error(msg)

        image_tasks = [t for t in tasks if 'video' not in (t.get('mode') or '').lower()]
        video_tasks = [t for t in tasks if 'video' in (t.get('mode') or '').lower()]
        video_pending: dict = {}
        stop_now = False
        # (2026-09-24) Setting "API lỗi thì chuyển sang DOM": BẬT → task lỗi API
        # gom vào đây, chạy lại bằng DOM ở cuối lô; TẮT (mặc định) → None, task
        # lỗi API báo lỗi về server như cũ, KHÔNG đụng DOM.
        dom_queue = [] if self._api_dom_fallback_enabled() else None

        if image_tasks:
            stop_now = self._run_image_tasks_concurrent(image_tasks, dom_queue=dom_queue)

        attempted_ids: set = set()
        if video_tasks and not stop_now and not self._stop.is_set():
            video_pending, stop_now, attempted_ids = self._run_video_tasks_staggered(
                video_tasks, dom_queue=dom_queue)
            for task in video_tasks:
                if task['id'] not in attempted_ids:
                    msg = 'Dừng trước khi generate (lô bị lỗi hoặc worker stop)'
                    self._log('error', f"Task #{task['id']} lỗi: {msg}")
                    self._report_task_error(task['id'], msg)

        if video_pending and not stop_now and not self._stop.is_set():
            max_rounds = int(self._server_settings.get('reconcile_max_rounds', 10))
            leftover = self._wait_and_reconcile_tasks(
                video_pending, max_rounds=max_rounds, wait_tiles=False,
            )
            for tid in leftover:
                msg = ('API đã submit nhưng không thấy media trong projectInitialData '
                       f'sau {max_rounds} vòng (API không hiện tile trên UI)')
                self._log('error', f'Task #{tid} lỗi: {msg}')
                self._report_task_error(tid, msg)
                if self._handle_task_error(msg):
                    stop_now = True
                    break

        # Video đã submit API thành công nhưng chưa thấy kết quả KHÔNG vào đây
        # (API có thể vẫn đang render — chạy DOM sẽ tạo trùng video).
        if dom_queue:
            if stop_now or self._stop.is_set():
                for t in dom_queue:
                    self._report_task_error(t['id'], 'Lỗi API, dừng trước khi kịp chạy DOM')
            else:
                stop_now = self._run_dom_fallback(dom_queue)

        if not stop_now:
            pm.set_status(self.profile_id, 'idle', clear_task=True,
                          pid=threading.get_ident())
        return stop_now

    def _run_task(self, task: dict) -> bool:
        if self.worker_mode == 'dom':
            return self._run_task_dom(task)
        elif self.worker_mode == 'gemini_video':
            return self._run_task_gemini_video(task)
        elif self.worker_mode == 'gemini_image':
            return self._run_task_gemini_image(task)
        else:
            return self._run_task_api(task)

    # ── Run 1 BATCH task — DOM mode (2026-09-03: dùng cho MỌI kích cỡ batch,
    # kể cả batch chỉ có 1 task — xem `_process_tasks()`) ───────────────────────
    #
    # Submit LIÊN TIẾP từng task (không chờ ảnh/video xong mới sang task kế), rồi
    # poll CHUNG toàn bộ tile mới xuất hiện — mirror cách Chrome Extension gom
    # concurrentPrompts=N vào 1 batch. Tile không mang task-id marker nào nên việc
    # gán tile→task dựa trên THỨ TỰ XUẤT HIỆN trong DOM (tile mới nhất thường chèn
    # lên ĐẦU feed — đảo ngược lại để "cũ nhất trước" khớp thứ tự submit) — đây là
    # một suy đoán hợp lý dựa trên cách Flow UI render, KHÔNG phải cơ chế chính thức
    # từ Google. Validate qua tests/_test_dom_batch.py (batch 2 task) trước khi tin
    # tưởng ở production với batch lớn hơn.
    def _run_tasks_batch(self, tasks: list) -> bool:
        delay = self._server_settings.get('task_delay_secs', 10)
        before_all = set(self._dom_tile_ids_ordered())
        submitted: list = []  # [{'task': dict, 'count': int}]

        for i, task in enumerate(tasks):
            task_id      = task['id']
            mode         = task.get('mode', 'textToImage')
            prompt       = (task.get('prompt_text') or task.get('title') or '').strip()
            count        = int(task.get('output_count') or 1)
            source_media = task.get('source_media') or []
            if isinstance(source_media, str):
                try:    source_media = json.loads(source_media)
                except: source_media = []

            self._log('info', f'▶ [BATCH {i+1}/{len(tasks)}] Task #{task_id} mode={mode} '
                               f'model={task.get("model","?")} images={len(source_media)} "{prompt[:60]}"')
            pm.set_status(self.profile_id, 'processing', task_id=task_id)
            self._task_start(task_id, mode, prompt)
            try:
                self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                          body={'taskId': task_id, 'machineCode': self.machine_code})
                self._ensure_flow_page()

                # (2026-09-19) Check task retry đã có media sẵn giờ làm 1 LẦN cho
                # cả lô ở `_precheck_batch_done()` (đầu `_process_tasks()`), không
                # còn refresh + quét riêng cho từng task retry ở đây.

                self._dom_configure(task)
                if source_media:
                    self._log('info', f'DOM: uploading {len(source_media)} image(s)…')
                    self._dom_upload_images(source_media, mode)
                task_prompt = f'TASK_{task_id}:{prompt}'
                self._dom_fill_and_submit(task_prompt)
                submitted.append({'task': task, 'count': count})
            except Exception as e:
                self._log('error', f'[BATCH] Submit task #{task_id} lỗi: {e}')
                self._report_task_error(task_id, str(e))
                if self._record_error(str(e)):
                    return True  # time-window vừa cho ngủ — dừng hẳn batch ngay
                pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())
            if i < len(tasks) - 1:
                self._stop.wait(delay)

        if not submitted:
            return False

        # Quy trình reconcile (2026-07-16, xem _wait_and_reconcile_tasks): submit
        # xong CẢ BATCH → chờ 60s → refresh project → projectInitialData → so khớp
        # TOÀN BỘ media với DB qua server (/reconcile/check) → task nào server báo
        # done thì ghi nhận ngay, lặp lại cho tới khi hết pending hoặc hết
        # max_rounds. Thay hẳn cursor-theo-thứ-tự-submit cũ (new_ids_ordered[cursor:
        # cursor+count]) — cursor chỉ là ĐOÁN "tile mới nhất khớp task nộp sau
        # cùng", có thể gán NHẦM task nếu Google không chèn tile đúng thứ tự submit;
        # match theo taskId trích từ prompt (qua server, đã proven — cùng cơ chế
        # extensions/content/flowMediaGenerator.js::reconcileProjectTiles() dùng)
        # không phụ thuộc giả định đó.
        tasks_by_id = {s['task']['id']: s['task'] for s in submitted}
        pending = self._wait_and_reconcile_tasks(
            tasks_by_id,
            max_rounds=int(self._server_settings.get('reconcile_max_rounds', 10)),
        )

        if not pending:
            return False

        # Fallback CHO RIÊNG các task server chưa xác nhận xong: tile theo thứ tự
        # submit (luồng cũ, đã proven, chỉ áp dụng phạm vi hẹp hơn nhiều so với
        # trước — trước đây MỌI task trong batch đều dùng cursor này).
        self._log('warn', f'[BATCH] {len(pending)}/{len(submitted)} task chưa reconcile '
                           'được — fallback tile theo thứ tự submit')
        total_expected = sum(s['count'] for s in submitted)
        new_ids = self._dom_wait_new_tiles(before_all, total_expected,
                                           timeout=90 + 30 * len(submitted))
        if not new_ids:
            went_to_sleep = False
            for task_id in pending:
                self._log('error', f'[BATCH] Task #{task_id} — không tile nào xuất hiện')
                self._report_task_error(task_id, 'No new tiles appeared after batch submit')
                if self._record_error('No new tiles appeared after batch submit'):
                    went_to_sleep = True
            if not went_to_sleep:
                pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())
            return went_to_sleep

        # Tile mới nhất chèn lên ĐẦU feed → đảo ngược để "cũ nhất trước" khớp thứ tự submit.
        all_ids_now     = self._dom_tile_ids_ordered()
        new_ids_set     = set(new_ids)
        new_ids_ordered = [tid for tid in reversed(all_ids_now) if tid in new_ids_set]

        stop_now = False
        cursor   = 0
        for s in submitted:
            task, count = s['task'], s['count']
            task_id = task['id']
            my_ids  = new_ids_ordered[cursor: cursor + count]
            cursor += count
            if task_id not in pending:
                continue   # đã reconciled xong ở trên rồi
            self._task_start(task_id, task.get('mode', 'textToImage'),
                              task.get('prompt_text') or task.get('title') or '')
            try:
                if len(my_ids) < count:
                    self._log('warn', f'[BATCH] Task #{task_id} thiếu tile '
                                       f'(cần {count}, còn {len(my_ids)})')
                media = self._resolve_tile_media(task_id, my_ids)
                if not media:
                    raise RuntimeError('Không thu thập được media nào (tile fallback)')
                self._log('ok', f'✔ [BATCH] Task #{task_id} — {len(media)} item(s) '
                                 'done (tile fallback)')
                self._req('POST', f'{FLOW_SERVER}/api/media/task/download', timeout=120,
                          body={'taskId': task_id, 'machineCode': self.machine_code,
                                'cueId': task.get('cue_id'), 'mode': task.get('mode', 'textToImage'),
                                'media': media, 'flowProjectId': self._extract_project_id()})
                self._handle_task_success()
            except Exception as e:
                self._log('error', f'[BATCH] Task #{task_id} lỗi: {e}')
                self._report_task_error(task_id, str(e))
                stop_now = self._handle_task_error(str(e))
            finally:
                if not stop_now:
                    pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())
            if stop_now:
                return True
        return False

    # ── Run task — DOM mode ────────────────────────────────────────────────────

    def _run_task_dom(self, task: dict) -> bool:
        """Trả True nếu escalation đã cho profile 'ngủ' (worker nên dừng hẳn)."""
        task_id      = task['id']
        mode         = task.get('mode', 'textToImage')
        prompt       = (task.get('prompt_text') or task.get('title') or '').strip()
        count        = int(task.get('output_count') or 1)
        source_media = task.get('source_media') or []
        if isinstance(source_media, str):
            try:    source_media = json.loads(source_media)
            except: source_media = []

        self._log('info',
                  f'▶ [DOM] Task #{task_id} mode={mode} model={task.get("model","?")} '
                  f'images={len(source_media)} "{prompt[:60]}"')
        pm.set_status(self.profile_id, 'processing', task_id=task_id)
        self._task_start(task_id, mode, prompt)

        stop_now = False
        try:
            self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                      body={'taskId': task_id, 'machineCode': self.machine_code})

            # Đảm bảo đang ở Flow project page
            self._ensure_flow_page()
            self._sleep(3)

            # Retry của task từng lỗi (server reap/timeout, hoặc bug reconcile cũ —
            # xem CHANGELOG "fix reconcile 0 item khớp") có thể ĐÃ CÓ media render
            # xong thật trong project từ lần trước — Flow đã tạo xong, chỉ là
            # server chưa kịp/không tải được. Kiểm tra TRƯỚC khi submit lại để
            # KHÔNG sinh thêm 1 lần generate MỚI trùng lặp (tốn quota Flow +
            # thời gian) cho task đã có sẵn kết quả. Chỉ check khi retry_count>0
            # (task lần đầu chắc chắn chưa từng submit prompt này, không cần tốn
            # 1 lần refresh+15s chờ interceptor cho mọi task).
            if int(task.get('retry_count') or 0) > 0:
                self._log('info', f'[DOM] Task #{task_id} là retry (lần '
                                   f'{task.get("retry_count")}) — kiểm tra media đã có '
                                   'sẵn trước khi submit lại…')
                already = self._reconcile_project_media({task_id})
                if already:
                    self._log('ok', f'✔ [DOM] Task #{task_id} — media đã có sẵn từ lần '
                                     'trước (phát hiện trước khi submit lại) — bỏ qua '
                                     'tạo mới')
                    self._handle_task_success()
                    return stop_now
                self._log('info', f'[DOM] Task #{task_id} — chưa có media sẵn, tiến hành '
                                   'submit như bình thường')

            # Configure settings: mode tab, model, ratio, count
            self._dom_configure(task)

            # Upload ảnh đính kèm (imageToImage / imageToVideo / componentsToVideo)
            if source_media:
                self._log('info', f'DOM: uploading {len(source_media)} image(s)…')
                self._dom_upload_images(source_media, mode)

            # Snapshot tile IDs hiện tại (dùng cho fallback DOM tile polling nếu cần)
            before_ids = self._dom_tile_ids()

            # Nhập prompt với prefix TASK_x: — để reconcile nhận ra task qua
            # flow.projectInitialData bên dưới
            task_prompt = f'TASK_{task_id}:{prompt}'
            self._dom_fill_and_submit(task_prompt)

            # Quy trình reconcile (2026-07-16, xem _wait_and_reconcile_tasks): chờ tới
            # khi DOM báo hết render (_wait_until_render_done) → refresh project →
            # projectInitialData → so khớp qua server (server tự tải + set done nếu
            # khớp) → lặp lại tới khi xong hoặc hết max_rounds. Nếu server không xác
            # nhận xong sau max_rounds, fallback về tile-polling cũ (đã proven, chỉ
            # áp dụng riêng task này).
            reconcile_max_rounds = int(self._server_settings.get('reconcile_max_rounds', 10))
            pending = self._wait_and_reconcile_tasks(
                {task_id: task}, max_rounds=reconcile_max_rounds,
            )
            if pending:
                self._log('warn', f'[DOM] Task #{task_id}: reconcile chưa xong sau '
                                   f'{reconcile_max_rounds} vòng — fallback tile polling')
                media = self._collect_task_media_via_tiles(before_ids, task_id, count)
                if not media:
                    raise RuntimeError('Không thu thập được media nào '
                                        '(cả reconcile lẫn tile fallback)')
                self._log('ok', f'✔ [DOM] Task #{task_id} — {len(media)} item(s) '
                                 'done (tile fallback)')
                self._req('POST', f'{FLOW_SERVER}/api/media/task/download', timeout=120,
                          body={'taskId': task_id, 'machineCode': self.machine_code,
                                'cueId': task.get('cue_id'), 'mode': mode, 'media': media,
                                'flowProjectId': self._extract_project_id()})
                self._handle_task_success()
            # else: _wait_and_reconcile_tasks() đã tự gọi _handle_task_success() rồi

        except Exception as e:
            self._log('error', f'[DOM] Task #{task_id} lỗi: {e}')
            try:
                self._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                          body={'taskId': task_id, 'machineCode': self.machine_code,
                                'errorMessage': str(e)})
            except Exception:
                pass
            stop_now = self._handle_task_error(str(e))
        finally:
            # Không ghi đè 'sleeping' do _handle_task_error() vừa set — profile phải
            # hiện đúng trạng thái ngủ, không phải 'idle' như xong việc bình thường.
            if not stop_now:
                pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())
        return stop_now

    # ── Run task — API mode ────────────────────────────────────────────────────

    def _run_task_api(self, task: dict) -> bool:
        """Trả True nếu escalation đã cho profile 'ngủ' (worker nên dừng hẳn)."""
        task_id  = task['id']
        mode     = task.get('mode', 'textToImage')
        prompt   = (task.get('prompt_text') or task.get('title') or '')[:80]
        is_video = 'video' in mode.lower()

        self._log('info', f'▶ Task #{task_id} mode={mode} model={task.get("model","?")} "{prompt}"')
        pm.set_status(self.profile_id, 'processing', task_id=task_id)
        self._task_start(task_id, mode, task.get('prompt_text') or task.get('title') or '')

        stop_now = False
        try:
            # Báo server bắt đầu xử lý
            self._req('POST', f'{FLOW_SERVER}/api/media/task/processing',
                      body={'taskId': task_id, 'machineCode': self.machine_code})

            if not is_video:
                # ── Image generation ──────────────────────────────────────
                # Ưu tiên: direct API (Python) với recaptcha token từ browser (uc + stealth)
                # → nhanh hơn, không phụ thuộc DOM; cần: auth headers + recaptchaToken + project_id
                # (2026-08-12) body tự dựng qua flow_api.py, không còn cần lastRequestBody làm template
                # Fallback: UI-driven (type prompt + Enter, intercept fetch response)
                captcha = self._get_fresh_recaptcha('IMAGE_GENERATION')
                can_api = bool(captcha) and bool(self._extract_project_id())

                if can_api:
                    try:
                        results_urls = self._call_image_api_v2(task, captcha)
                        self._log('ok', f'✔ Task #{task_id} — {len(results_urls)} image(s) (direct API)')
                    except RuntimeError as api_err:
                        # Mặc định CHỈ API — lỗi thì báo lỗi; bật setting mới chạy DOM.
                        if self._api_dom_fallback_enabled():
                            self._log('warn', f'[api→dom] Direct API lỗi → chạy DOM: {api_err}')
                            return self._run_task_dom(task)
                        raise
                else:
                    reason = 'no recaptcha' if not captcha else 'no project_id'
                    if self._api_dom_fallback_enabled():
                        self._log('warn', f'[api→dom] Không gọi được API ({reason}) → chạy DOM')
                        return self._run_task_dom(task)
                    raise RuntimeError(f'Không gọi được API tạo ảnh ({reason}) — '
                                       f'chưa bật "API lỗi thì chuyển sang DOM"')

                # (2026-08-14, fix bug thật "task hoàn tất nhưng không thấy
                # media mới" khi Render lại) — ưu tiên `u['name']` (uuid THẬT,
                # xem `_call_image_api_v2()`); fallback
                # tên tự chế CHỈ khi thiếu.
                media = [{'name': u.get('name') or f'img_{task_id}_{i+1}', 'url': u['url']}
                         for i, u in enumerate(results_urls)]
                self._req('POST', f'{FLOW_SERVER}/api/media/task/download', timeout=120,
                          body={'taskId': task_id, 'machineCode': self.machine_code,
                                'cueId': task.get('cue_id'), 'mode': mode, 'media': media,
                                'flowProjectId': self._extract_project_id()})
                self._handle_task_success()

            else:
                # Video đi qua `_run_tasks_api_batch` (submit liên tiếp, không chờ
                # tile). Nhánh này chỉ còn nếu `_run_task_api` bị gọi lẻ.
                captcha = self._get_fresh_recaptcha('VIDEO_GENERATION')
                if not captcha:
                    raise RuntimeError('Không lấy được reCAPTCHA VIDEO_GENERATION')
                self._call_video_api(task, captcha)
                leftover = self._wait_and_reconcile_tasks(
                    {task_id: task},
                    max_rounds=int(self._server_settings.get('reconcile_max_rounds', 10)),
                    wait_tiles=False,
                )
                if leftover:
                    raise RuntimeError(
                        'API đã submit nhưng không thấy media trong projectInitialData '
                        '(API không hiện tile trên UI)'
                    )
                self._log('ok', f'✔ Task #{task_id} — video reconciled')

        except Exception as e:
            self._log('error', f'Task #{task_id} lỗi: {e}')
            try:
                self._req('POST', f'{FLOW_SERVER}/api/media/task/error',
                          body={'taskId': task_id, 'machineCode': self.machine_code,
                                'errorMessage': str(e)})
            except Exception:
                pass
            stop_now = self._handle_task_error(str(e))
        finally:
            if not stop_now:
                pm.set_status(self.profile_id, 'idle', clear_task=True,
                              pid=threading.get_ident())
        return stop_now

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _heartbeat(self, keepalive_only: bool = False):
        """Gọi heartbeat → trả list task (có thể rỗng). Cũng gửi maxConcurrent của
        profile. KHÔNG còn đồng bộ self._server_settings từ response nữa
        (2026-07-17) — settings giờ CỤC BỘ hoàn toàn, xem local_settings.py và
        __init__. Response vẫn có thể chứa field `settings` (backend cũ gửi cho
        loại machine khác), nhưng ở đây cố tình bỏ qua.

        `keepalive_only=True` (2026-07-18) — dùng bởi keepalive thread trong
        `_process_tasks()` (giữ `last_seen` tươi trong lúc xử lý task dài, xem
        comment ở đó). Gửi `keepaliveOnly: true` để backend BỎ QUA HẲN bước giao
        task (không chỉ dựa vào in-flight tính đúng — tính đúng vẫn có 1 khe hở
        RACE THẬT đã bắt được qua log: batch 2 task vừa reconcile xong GẦN NHƯ
        ĐỒNG THỜI → in-flight tạm về 0 đúng lúc keepalive gọi heartbeat → server
        tưởng máy rảnh, giao ngay 2 task mới trong lúc `_process_tasks()` còn chưa
        kịp return để vòng lặp chính tự heartbeat lại — 2 task đó bị
        "Keepalive heartbeat nhận nhầm task... bỏ qua" vứt bỏ, kẹt ở status
        assigned/processing cho tới khi reaper server thu hồi, làm chậm tiến độ
        batch). Cờ này loại bỏ race tận gốc — backend không chạy code path giao
        task cho lần gọi này nữa, bất kể in-flight lúc đó là bao nhiêu."""
        try:
            profile        = pm.get(self.profile_id)
            display_name   = (profile.get('display_name') or '').strip()
            if not display_name:
                display_name = f'[SEL] {profile.get("profile_name", "")}'
            max_concurrent = max(1, min(int(profile.get('max_concurrent') or 1), 10))
            # (2026-09-09) machineType theo worker_mode — 'gemini_video'/'gemini_image'
            # dùng type riêng để backend's heartbeat.py gate task theo
            # projects.enable_gemini_video/enable_gemini_image (§ "tích hợp vào
            # project banana" — xem root CLAUDE.md); mọi mode khác (dom/api) giữ
            # nguyên 'selenium_profile' như trước.
            machine_type = {
                'gemini_video': 'gemini_video_selenium',
                'gemini_image': 'gemini_image_selenium',
            }.get(self.worker_mode, 'selenium_profile')
            body = {'machineCode':    self.machine_code,
                    'runningCount':   0,
                    'waitingCount':   0,
                    'taskMode':       profile.get('task_mode', 'all'),
                    'displayName':    display_name,
                    'machineType':    machine_type,
                    'maxConcurrent':  max_concurrent}
            # (2026-09-17) Khung giờ chạy riêng của profile VEO (`run_hours`) — ngoài
            # khung thì chỉ giữ last_seen (keepaliveOnly), KHÔNG nhận task mới. Task
            # đang chạy dở vẫn hoàn tất bình thường (không đi qua nhánh này).
            outside_hours = not keepalive_only and not in_run_hours(profile)
            if outside_hours != getattr(self, '_run_hours_blocked', False):
                self._run_hours_blocked = outside_hours
                span = summarize_run_hours(profile.get('run_hours'))
                if outside_hours:
                    self._log('info', f'⏸ Ngoài khung giờ chạy ({span}) — tạm không nhận task')
                else:
                    self._log('info', f'▶ Vào khung giờ chạy ({span}) — nhận task lại')
            if keepalive_only or outside_hours:
                body['keepaliveOnly'] = True
            # (2026-09-14) Chạy theo project + email — backend chỉ giao task của
            # project gán đúng email này (xem heartbeat.py `binding_clause`).
            if self._bind_mode_enabled():
                body['bindProjectEmail'] = True
                body['accountEmail'] = (profile.get('account_email') or '').strip()
            r = self._req('POST', f'{FLOW_SERVER}/api/media/heartbeat', quiet=True, body=body) or {}
            if outside_hours:
                return []
            tasks = r.get('tasks') or ([r['task']] if r.get('task') else [])
            if tasks:
                ids = ', '.join(f'#{t.get("id")}' for t in tasks)
                if keepalive_only:
                    # KHÔNG nên xảy ra nữa (backend đã chặn ở nguồn khi keepaliveOnly=True)
                    # — vẫn log rõ nếu backend cũ chưa hỗ trợ cờ này để dễ chẩn đoán.
                    self._log('warn', f'Keepalive heartbeat (keepaliveOnly) vẫn nhận '
                                       f'{len(tasks)} task ({ids}) — backend có thể chưa hỗ trợ '
                                       'keepaliveOnly, task này coi như bỏ qua')
                else:
                    self._log('info', f'← Heartbeat: nhận {len(tasks)} task ({ids})')
            return tasks
        except Exception as e:
            self._log('warn', f'Heartbeat error: {e}')
            return []

    # ── Worker main loop ──────────────────────────────────────────────────────

    def run(self):
        if self.worker_mode == 'gemini':
            return self._run_gemini_loop()
        if self.worker_mode == 'chatgpt':
            return self._run_chatgpt_loop()

        is_gemini_video = self.worker_mode == 'gemini_video'
        is_gemini_image = self.worker_mode == 'gemini_image'
        mode_label = ('Gemini Video' if is_gemini_video else
                       'Gemini Image' if is_gemini_image else
                       ('DOM' if self.worker_mode == 'dom' else 'API'))
        self._log('info', f'Worker starting ({mode_label} mode)')
        pm.set_status(self.profile_id, 'idle', pid=threading.get_ident())
        try:
            self.driver = self._make_driver()

            if is_gemini_video or is_gemini_image:
                # gemini_video/gemini_image KHÔNG dùng Flow (labs.google) —
                # navigate thẳng gemini.google.com, không cần
                # _ensure_flow_page()/token capture.
                self._log('info', 'Chrome opened — navigate to Gemini')
                if self._ensure_google_login(self.GEMINI_URL):
                    self._log('ok', f'{mode_label} mode — sẵn sàng nhận task (không cần token)')
                else:
                    self._log('error', 'Đăng nhập Google thất bại/chưa cấu hình — worker vẫn '
                                        'chạy nhưng MỌI task sẽ lỗi tới khi khắc phục (xem log '
                                        '[google-login] ở trên).')
            else:
                self._log('info', 'Chrome opened — navigate to Flow page')
                self._ensure_flow_page()

                if self.worker_mode == 'api':
                    self._log('ok', 'API mode (batchexecute) — sẵn sàng nhận task')
                else:
                    self._log('ok', 'DOM mode — sẵn sàng nhận task (không cần token)')

            while not self._stop.is_set():
                # Kiểm tra browser còn sống TRƯỚC KHI heartbeat
                # Nếu user đóng Chrome thủ công → thoát ngay, không nhận task
                if not self._is_driver_alive():
                    self._log('warn', 'Browser đã đóng — thoát worker loop')
                    break

                self._drain_browser_logs()

                try:
                    tasks = self._heartbeat()
                    if tasks:
                        if self._process_tasks(tasks):
                            self._log('warn', 'Profile chuyển sang trạng thái ngủ — thoát worker loop')
                            break
                    else:
                        self._stop.wait(POLL_INTERVAL)
                except Exception as e:
                    self._log('error', f'Worker loop: {e}')
                    # Nếu exception do driver chết → check ngay, không đợi interval
                    if not self._is_driver_alive():
                        self._log('warn', 'Driver mất kết nối sau exception — thoát loop')
                        break
                    self._stop.wait(POLL_INTERVAL)

                # (2026-08-20) Bậc thang escalation THEO BATCH (`_finish_batch_
                # cleanup()`) chạy trong `finally` của `_process_tasks()` nên
                # KHÔNG truyền được quyết định "ngủ" qua giá trị trả về (dùng
                # `return` trong `finally` sẽ nuốt exception — xem comment ở đó).
                # Check thẳng mốc `_sleep_until` mà nó vừa set. Đặt Ở ĐÂY (sau
                # CẢ try LẪN except) để bắt được cả trường hợp batch kết thúc
                # bằng exception — lúc đó `finally` vẫn đã chạy bậc thang batch.
                if self._sleep_until > time.time():
                    self._log('warn', 'Profile chuyển sang trạng thái ngủ (bậc thang batch) '
                                       '— thoát worker loop')
                    break

        except Exception as e:
            self._log('error', f'Worker fatal: {e}')
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            # Không ghi đè 'sleeping' — _handle_task_error() đã set đúng trạng thái
            # kèm self._sleep_until để dispatcher (phase sau) biết khi nào đánh thức lại.
            # (2026-08-10) `_clear_profile_if_sleeping()` — nếu đúng đang sleeping,
            # tự "Làm mới profile" (xoá sạch cache/cookie/session) trước khi dispatcher
            # đánh thức lại, xem docstring hàm đó.
            if not self._clear_profile_if_sleeping(pm.get(self.profile_id)):
                pm.set_status(self.profile_id, 'offline', clear_task=True, pid=None)
            self._log('info', 'Worker stopped')

    def stop(self):
        self._stop.set()


