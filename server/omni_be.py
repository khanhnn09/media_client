"""Builder/parser cho API "Tạo video AI" (Omni) của Google Vids —
docs.google.com/videos (2026-09-27).

Vai trò song song `server/flow_be.py` (VEO3) và `server/gemini_be.py` (Gemini):
"nơi DUY NHẤT biết shape body", `worker.py` chỉ lo điều phối.

⚠️ Mọi shape dưới đây CAPTURE TỪ REQUEST THẬT — chạy đúng luồng DOM của Vids
("+" → "Tạo video AI" → "Thành phần" đính 3 ảnh → nhập prompt → Tạo) rồi chặn
network bằng CDP (xem `tests/_test_omni_workspace.py --capture`). KHÔNG đoán
từ bundle.

## 1. Upload ảnh thành phần — resumable 2 bước, chỉ cần cookie

    POST https://docs.google.com/upload/temporaryblob/videos?authuser=0
         X-Goog-Upload-Protocol: resumable
         X-Goog-Upload-Command: start
         X-Goog-Upload-Content-Length: <bytes>
         X-Goog-Upload-File-Name: temporary_blob
      → header `x-goog-upload-url`
    PUT  <x-goog-upload-url>
         X-Goog-Upload-Command: upload, finalize
         X-Goog-Upload-Offset: 0
         body = bytes ảnh
      → `["<blob id AVL_…>"]`

## 2. Sinh video — ĐỒNG BỘ (~30-60s), trả thẳng URL mp4

    POST https://appsgenaiserver-pa.clients6.google.com/v1/genai/generate?key=<API key trang>
         Content-Type: application/json+protobuf
         Authorization: SAPISIDHASH <ts>_<sha1> SAPISID1PHASH … SAPISID3PHASH …
         X-Server-Token: _docs_flag_initialData['docs-est']
         X-Goog-AuthUser: 0

Body xem `build_generate_body()`. Response chứa
`https://contribution-rt.usercontent.google.com/download?c=…&filename=video.mp4`
— URL này CẦN COOKIE (gọi trần trả 302 về trang đăng nhập) nên phải tải bằng
`fetch(url, {credentials:'include'})` TRONG trang docs.google.com.

⚠️ Mọi request đều bắn bằng `fetch()` TRONG TRANG docs.google.com (cookie +
Origin đúng), không bắn từ Python.
"""
from __future__ import annotations

import copy
import json
import random
import re
import uuid

VIDS_HOME_URL = 'https://docs.google.com/videos/u/0/'
GENAI_GENERATE_URL = 'https://appsgenaiserver-pa.clients6.google.com/v1/genai/generate'
UPLOAD_URL = 'https://docs.google.com/upload/temporaryblob/videos?authuser=0'

# Omni nhận tối đa 3 ảnh thành phần (giới hạn của chính UI Vids).
MAX_INGREDIENTS = 3
MIN_DURATION = 3
MAX_DURATION = 10
DEFAULT_DURATION = 8

# ô [0] — id "tính năng" của lời gọi genai. Capture thật:
#   374 = text → video (không thành phần)
#   376 = thành phần (ảnh) → video (r2v)
# Gửi nhầm (vd 376 mà không có thành phần) → REQUEST_REFUSED "invalid argument".
FEATURE_TEXT_TO_VIDEO = 374
FEATURE_INGREDIENTS_TO_VIDEO = 376
# ô [5] — capture thật `[1, null, [[null, "1", 1189]]]` (cũng xuất hiện trong
# quotaSummary) — giữ nguyên.
QUOTA_CONTEXT = [1, None, [[None, '1', 1189]]]

# Thường xong trong ~30-40s; có lúc server giữ request không trả (đã gặp thật
# vài lần, không tái hiện được theo điều kiện cụ thể) → huỷ sau chừng này.
GENERATE_TIMEOUT_SECS = 300

# Tỉ lệ khung hình + độ phân giải → settings[15][4]. ĐO THẬT 2026-10-06 (chạy API rồi
# ffprobe file tải về): 1 = 720p ngang (1280x720), 2 = 720p dọc, 5 = 1080p ngang
# (1920x1080), 6 = 1080p dọc (1080x1920). Tức là 1080p KHÔNG cần upscale riêng — chỉ
# việc gửi mã 5/6. (Nút "Tăng độ phân giải" trên giao diện chỉ để nâng video 720p.)
ASPECT_LANDSCAPE = 1
ASPECT_PORTRAIT = 2
RESOLUTION_1080_OFFSET = 4
RESOLUTIONS = ('720p', '1080p')


def aspect_code(aspect_ratio: str | None, resolution: str | None = None) -> int:
    """Omni chỉ có Khổ ngang / Khổ dọc. '9:16' (và mọi tỉ lệ dọc) → dọc."""
    s = (aspect_ratio or '').replace(' ', '')
    portrait = s in ('9:16', '3:4', '4:5', '2:3') or 'portrait' in s.lower() or 'doc' in s.lower()
    code = ASPECT_PORTRAIT if portrait else ASPECT_LANDSCAPE
    if normalize_resolution(resolution) == '1080p':
        code += RESOLUTION_1080_OFFSET
    return code


def normalize_resolution(resolution) -> str:
    """'1080p' (mọi cách viết: 1080, 1080P, FHD) → '1080p'; còn lại → '720p'."""
    v = str(resolution or '').strip().lower().rstrip('p')
    return '1080p' if v in ('1080', 'fhd', 'full hd', 'fullhd') else '720p'


def clamp_duration(seconds) -> int:
    """Nhận số hoặc chuỗi kiểu project lưu ('8s') → số giây trong 3..10."""
    try:
        v = int(round(float(str(seconds).strip().rstrip('sS'))))
    except (TypeError, ValueError):
        return DEFAULT_DURATION
    if v <= 0:
        return DEFAULT_DURATION
    return max(MIN_DURATION, min(MAX_DURATION, v))


def new_ingredient_uuid() -> str:
    return str(uuid.uuid4()).upper()


def new_client_id() -> str:
    return f'goog_{random.randint(100000000, 999999999)}'


def _ingredient_entry(ing_uuid: str, blob_id: str, label: str) -> list:
    """1 thành phần trong b[2][25] — shape capture thật."""
    blob = [None, None, None, None, None, None, None, None,
            [None, None, None, blob_id, 1], None, None, None, None, label]
    inner = [None] * 11 + [blob]
    return [[None, None, None, None, None, None, None, [ing_uuid, inner]]]


def _chip_segment(ing_uuid: str, label: str) -> list:
    return [None, None, None, None, None, None, None,
            [None, [None, None, label], [None, None, None, None, None, None, None, [ing_uuid]]]]


def _text_segment(text: str) -> list:
    return [None, None, text]


def build_generate_body(prompt: str,
                        ingredients: list | None = None,
                        doc_id: str | None = None,
                        aspect_ratio: str | None = None,
                        duration: int | None = None,
                        lang: str = 'en',
                        client_id: str | None = None,
                        resolution: str | None = None) -> list:
    """Dựng body `genai/generate` tạo video Omni.

    `ingredients`: list `(ing_uuid, blob_id, label)` — tối đa 3 (cắt bớt nếu dư).
    `doc_id`: id tài liệu Vids làm ngữ cảnh (capture thật luôn có). Bỏ trống thì
    không gửi ngữ cảnh tài liệu.
    """
    ings = list(ingredients or [])[:MAX_INGREDIENTS]

    ctx = [None] * 41
    ctx[0] = 9
    ctx[4] = client_id or new_client_id()
    ctx[6] = '0'
    if doc_id:
        ctx[8] = [None, None, None, [[[None, None, None, None, None, None, None,
                  [None, None, [[doc_id, 'application/vnd.google-apps.flix', None, None, 1]]]]]]]
    ctx[11] = [24, 0]
    ctx[13] = lang
    ctx[19] = 1
    if ings:
        ctx[25] = [_ingredient_entry(u, b, lab) for (u, b, lab) in ings]
    ctx[40] = 0

    segs: list = []
    for i, (u, _b, lab) in enumerate(ings):
        if i:
            segs.append(_text_segment(' '))
        segs.append(_chip_segment(u, lab))
    text = (prompt or '').strip()
    segs.append(_text_segment((' ' + text) if ings else text))
    prompt_block = [None, None, None, [segs]]

    # settings[15] = [0, 12, null, 0, <tỉ lệ>, null, null, null, <số giây>]
    # (ô 0/1/3 giữ nguyên từ capture — Omni/720p; chưa thấy UI đổi được).
    settings = [None] * 15 + [[0, 12, None, 0, aspect_code(aspect_ratio, resolution), None, None, None,
                               clamp_duration(duration)]]

    feature = FEATURE_INGREDIENTS_TO_VIDEO if ings else FEATURE_TEXT_TO_VIDEO
    return [feature, None, ctx, prompt_block, settings,
            copy.deepcopy(QUOTA_CONTEXT), 1]


_VIDEO_URL_RE = re.compile(r'https://contribution-rt\.usercontent\.google\.com/download\?[^"\\\s]+')
_ERR_HINT_RE = re.compile(r'"(?:[A-Z][A-Z0-9_]{6,})"')


def parse_generate_response(text: str) -> list[str]:
    """Trả list URL mp4 (khử trùng, giữ thứ tự) trong response `genai/generate`."""
    out: list[str] = []
    for m in _VIDEO_URL_RE.finditer(text or ''):
        u = m.group(0).replace('\\u0026', '&').replace('\\u003d', '=')
        if u not in out:
            out.append(u)
    return out


def summarize_error(status: int, text: str) -> str:
    """Tóm tắt response lỗi / không có video — đủ để log chẩn đoán."""
    t = (text or '').strip()
    codes = [c.strip('"') for c in _ERR_HINT_RE.findall(t)][:4]
    head = t[:300].replace('\n', ' ')
    return f'HTTP {status}' + (f' {codes}' if codes else '') + (f' — {head}' if head else '')


def label_for(index: int, lang_vi: bool = True) -> str:
    """Nhãn chip thành phần (UI Vids tự đặt "Hình ảnh1"...). Chỉ để hiển thị."""
    return f'Hình ảnh{index + 1}' if lang_vi else f'Image{index + 1}'


# ── JS chạy TRONG trang docs.google.com ─────────────────────────────────────
# 1 thư viện `window.__omniLib` — mỗi hàm trả Promise<object JSON>. Gọi được
# bằng CẢ Selenium (`selenium_call()`) lẫn CDP `Runtime.evaluate` với
# `awaitPromise` (`call_expr()`) — worker điều khiển tab Omni qua websocket CDP
# riêng nên chạy song song với tab VEO mà không cần `switch_to.window`.
LIB_JS = r"""
if (!window.__omniLib) window.__omniLib = (function(){
  function ck(n){ var m=document.cookie.match(new RegExp('(?:^|; )'+n.replace(/[-.]/g,'\\$&')+'=([^;]*)')); return m?decodeURIComponent(m[1]):''; }
  async function hash(v){ var ts=Math.floor(Date.now()/1000);
    var buf=await crypto.subtle.digest('SHA-1', new TextEncoder().encode(ts+' '+v+' '+location.origin));
    return ts+'_'+Array.from(new Uint8Array(buf)).map(function(b){return b.toString(16).padStart(2,'0');}).join(''); }
  async function auth(){
    var s=ck('SAPISID')||ck('__Secure-3PAPISID'); if(!s) return null;
    var p1=ck('__Secure-1PAPISID')||s, p3=ck('__Secure-3PAPISID')||s;
    return 'SAPISIDHASH '+await hash(s)+' SAPISID1PHASH '+await hash(p1)+' SAPISID3PHASH '+await hash(p3);
  }
  var gens = {};
  return {
    state: async function(){
      var f = window._docs_flag_initialData || {};
      var m = location.pathname.match(/\/videos\/(?:u\/\d+\/)?d\/([^/]+)/);
      var key = null;
      try { var html = document.documentElement.innerHTML;
            var k = html.match(/appsgenaiserver[^"']*?key=(AIza[0-9A-Za-z_-]{30,})/) || html.match(/"(AIzaSyA-[0-9A-Za-z_-]{30,})"/);
            if (k) key = k[1]; } catch(e) {}
      return {serverToken: f['docs-est'] || null, docId: m ? m[1] : null, apiKey: key,
              hasSapisid: !!(ck('SAPISID')||ck('__Secure-3PAPISID')), url: location.href};
    },
    upload: async function(b64){
      var bin = atob(b64); var u8 = new Uint8Array(bin.length);
      for (var i=0;i<bin.length;i++) u8[i]=bin.charCodeAt(i);
      var r1 = await fetch('%UPLOAD_URL%', {method:'POST', credentials:'include', headers:{
          'X-Goog-Upload-Protocol':'resumable','X-Goog-Upload-Command':'start',
          'X-Goog-Upload-Content-Length': String(u8.length),'X-Goog-Upload-File-Name':'temporary_blob',
          'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}});
      var up = r1.headers.get('x-goog-upload-url');
      if (!up) return {error:'upload start HTTP '+r1.status+' (không có x-goog-upload-url)'};
      var r2 = await fetch(up, {method:'PUT', credentials:'include', headers:{
          'X-Goog-Upload-Command':'upload, finalize','X-Goog-Upload-Offset':'0',
          'Content-Type':'application/x-www-form-urlencoded;charset=utf-8'}, body:u8});
      var t = await r2.text(); var m = t.match(/"(AVL_[^"]+)"/);
      return m ? {blobId:m[1]} : {error:'upload finalize HTTP '+r2.status+': '+t.slice(0,200)};
    },
    // Bắn generate rồi return NGAY (không chờ ~30-60s) — kết quả đọc bằng poll().
    kick: async function(key, body, apiKey, serverToken, timeoutMs){
      gens[key] = {state:'running', t0: Date.now()};
      (async function(){
        var a = await auth();
        if (!a) return {status:0, text:'NO_SAPISID_COOKIE (chưa đăng nhập Google?)'};
        var h = {'Content-Type':'application/json+protobuf','Authorization':a,'X-Goog-AuthUser':'0'};
        if (serverToken) h['X-Server-Token'] = serverToken;
        // Có lúc server giữ request không trả (đã gặp thật) — tự huỷ sau timeoutMs.
        var ac = new AbortController(); setTimeout(function(){ ac.abort(); }, timeoutMs || 300000);
        var r = await fetch('%GEN_URL%?key='+encodeURIComponent(apiKey), {method:'POST', credentials:'include', headers:h, body:body, signal:ac.signal});
        return {status:r.status, text: await r.text()};
      })().then(function(res){ res.state='done'; gens[key]=res; },
                function(e){ gens[key]={state:'done', status:0, text:'fetch error: '+e}; });
      return {ok:true};
    },
    poll: async function(key){
      var g = gens[key]; if (!g) return {state:'missing'};
      if (g.state === 'done') delete gens[key];
      return g;
    },
    fetchB64: async function(url){
      var r = await fetch(url, {credentials:'include'});
      if (!r.ok) return {error:'HTTP '+r.status};
      var b = await r.blob();
      var s = await new Promise(function(ok, bad){ var fr=new FileReader();
        fr.onload=function(){ ok(String(fr.result)); }; fr.onerror=function(){ bad('FileReader lỗi'); }; fr.readAsDataURL(b); });
      return {b64: s.slice(s.indexOf(',')+1), size: b.size, type: b.type};
    }
  };
})();
""".replace('%UPLOAD_URL%', UPLOAD_URL).replace('%GEN_URL%', GENAI_GENERATE_URL)


def call_expr(fn: str, *args) -> str:
    """Biểu thức JS (Promise) gọi `window.__omniLib.<fn>(...)` — cho CDP
    `Runtime.evaluate(awaitPromise=True)`. Tự cài thư viện nếu trang vừa tải lại."""
    a = ', '.join(json.dumps(x, ensure_ascii=False) for x in args)
    return (LIB_JS + f'\nwindow.__omniLib.{fn}({a})'
            ".catch(function(e){ return {error: String(e)}; });")


def selenium_call(driver, fn: str, *args, timeout: float = 120):
    """Gọi thư viện qua Selenium `execute_async_script` (dùng trong test)."""
    driver.set_script_timeout(timeout)
    js = (LIB_JS + '\nvar __cb = arguments[arguments.length-1];\n'
          f'window.__omniLib.{fn}.apply(null, Array.prototype.slice.call(arguments, 0, -1))'
          '.then(__cb, function(e){ __cb({error: String(e)}); });')
    return driver.execute_async_script(js, *args) or {}
