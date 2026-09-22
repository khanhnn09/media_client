"""Builder/parser cho RPC `assistant.lamda.BardFrontendService/StreamGenerate`
của gemini.google.com — nền tảng "API mode" của worker Gemini (gửi ẩn, không
điều khiển DOM).

Vai trò song song với `server/flow_be.py` (Flow/VEO3) — "nơi DUY NHẤT biết shape
body", để `worker.py` chỉ lo điều phối.

⚠️ Mọi shape dưới đây **capture từ request THẬT** (`tests/_gemini_rpc_capture.py`
chạy đúng luồng DOM sản xuất rồi chặn network), KHÔNG suy từ bundle đã minify —
cùng chuẩn đã áp cho `flow_be.py`.

## Endpoint

    POST https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate
         ?bl=<boq version>&f.sid=<sid>&hl=en&_reqid=<random>&rt=c
    content-type: application/x-www-form-urlencoded;charset=utf-8
    body: f.req=<url-encoded>&at=<xsrf token>&

`bl`/`f.sid`/`at` đọc THẲNG từ `window.WIZ_global_data` (`cfb2h`/`FdrFJe`/`SNlM0e`)
— KHÔNG cần chờ trang tự bắn 1 request rồi trích từ URL như `flow_be` phải làm.

## f.req

    [null, "<json string của mảng `inner` 99 phần tử>"]

Các ô ĐÃ BIẾT Ý NGHĨA (phần còn lại giữ nguyên từ template capture — cờ tính
năng của app, không đụng vào):

| ô | ý nghĩa |
|----|---------|
| `inner[0]` | `[prompt, 0, null, null, null, null, 0]` |
| `inner[1]` | `["en"]` — ngôn ngữ |
| `inner[2]` | `[c_id, r_id, rc_id, null×6, ""]` — id hội thoại; chat MỚI thì 3 ô đầu là `""` |
| `inner[3]` | token BotGuard (tiền tố `!`, ~1.6KB) — sinh TRONG TRANG, xem `worker.py` |
| `inner[4]` | id 32-hex, sinh mới mỗi request |

## Response

Chuỗi chunk length-prefixed (giống batchexecute):

    )]}'\\n\\n<độ dài>\\n[["wrb.fr",null,"<json string>"]]\\n<độ dài>\\n[[...]]

Mỗi `wrb.fr` mang 1 bản cập nhật TĂNG DẦN của cùng câu trả lời — bản cuối là
bản đầy đủ. `parse_stream_generate()` gom hết rồi lấy bản dài nhất.
"""
from __future__ import annotations

import json
import re
import uuid

# ── Template `inner` (99 ô) — capture từ request THẬT ─────────────────────
# `tests/_gemini_rpc_capture.py` chạy đúng luồng DOM sản xuất rồi chặn network.
# 6 ô do runtime quyết định đã trung hoà về `null` (prompt/lang/conv/blob/
# request-id/uuid client) — `build_f_req()` điền lại. Các ô còn lại là CỜ TÍNH
# NĂNG của app: giữ NGUYÊN, đoán bừa là cách nhanh nhất để Google từ chối.
#
# Google đổi build → chạy lại script capture rồi thay mảng này (in ra bằng
# `python tests/_gemini_rpc_capture.py` + đọc `03_send_requests.json`).
STREAM_GENERATE_TEMPLATE = [None, None, None, None, None, None, [0], 1, None, None, 1, 0, 
    None, None, None, None, None, [[0]], 0, None, None, None, None, None, None, None, None, 
    1, None, None, [4], None, None, None, None, None, None, None, None, None, None, [1], 
    None, None, None, None, None, None, None, None, None, None, None, 0, None, None, None, 
    None, None, None, None, [], None, None, None, None, None, 0, 1, None, None, None, None, 
    None, None, None, None, None, None, 1, 1, None, None, None, None, None, None, None, 
    None, None, None, 0, None, None, None, None, 0, None, 1]

# ⚠️ `inner[3]` = token BotGuard. ĐÃ VERIFY TRÊN TÀI KHOẢN THẬT (2026-09-06):
# gửi `null` vẫn được chấp nhận — HTTP 200, hội thoại được tạo, Gemini trả lời
# đầy đủ. `window.botguard` CÓ tồn tại trong trang nếu sau này Google siết lại
# và bắt buộc token, nhưng HIỆN TẠI không cần sinh gì cả.
DEFAULT_BOTGUARD_TOKEN = None


# ── Endpoint ──────────────────────────────────────────────────────────────
STREAM_GENERATE_PATH = '/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate'
GEMINI_ORIGIN = 'https://gemini.google.com'


def new_request_id() -> str:
    """`inner[4]` — 32 ký tự hex, mỗi request 1 giá trị mới (capture cho thấy
    giá trị khác nhau giữa các lần gửi, không tái dùng)."""
    return uuid.uuid4().hex


def build_f_req(template_inner: list, prompt: str, *, blob: str | None = None,
                conv: tuple | None = None, lang: str = 'en') -> str:
    """Dựng chuỗi `f.req` từ `template_inner` (mảng 99 phần tử capture được).

    CHỈ ghi đè các ô đã hiểu; mọi ô khác giữ NGUYÊN từ template — đó là cờ tính
    năng của app, đoán bừa là cách nhanh nhất để Google từ chối.

    `conv=(c_id, r_id, rc_id)` để gửi TIẾP trong cùng hội thoại; `None` = chat mới.
    """
    inner = list(template_inner)
    inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = [lang]
    c, r, rc = (conv or ('', '', ''))
    inner[2] = [c or '', r or '', rc or '', None, None, None, None, None, None, '']
    inner[3] = blob
    inner[4] = new_request_id()
    return json.dumps([None, json.dumps(inner, ensure_ascii=False)], ensure_ascii=False)


# ── Parser ────────────────────────────────────────────────────────────────
_CHUNK_RE = re.compile(r'^\d+$')


def _iter_wrb(raw: str):
    """Bóc từng payload `wrb.fr` khỏi chuỗi chunk length-prefixed.

    KHÔNG dựa vào con số độ dài (đếm theo BYTE UTF-8, lệch với index ký tự của
    Python) — quét từng dòng, dòng nào parse được thành JSON `[["wrb.fr",...]]`
    thì lấy. Dòng số độ dài tự bị bỏ qua vì không phải JSON hợp lệ dạng list.
    """
    for line in (raw or '').split('\n'):
        line = line.strip()
        if not line or line.startswith(")]}'") or _CHUNK_RE.match(line):
            continue
        try:
            outer = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(outer, list):
            continue
        for entry in outer:
            if (isinstance(entry, list) and len(entry) >= 3
                    and entry[0] == 'wrb.fr' and isinstance(entry[2], str)):
                try:
                    yield json.loads(entry[2])
                except (ValueError, TypeError):
                    continue


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        yield node
        for v in node:
            yield from _walk(v)


_RC_RE = re.compile(r'^rc_[0-9a-f]+$')
_IMG_RE = re.compile(r'https://[^\s"\\]+\.(?:png|jpe?g|webp)(?:\?[^\s"\\]*)?', re.I)


def parse_stream_generate(raw: str) -> dict:
    """Trả `{'text', 'conv': (c,r,rc), 'images': [url…], 'candidates': n, 'raw_len'}`.

    `text` = bản dài nhất trong mọi bản cập nhật tăng dần (bản cuối cùng đầy đủ
    nhất, nhưng lấy theo ĐỘ DÀI an toàn hơn là tin thứ tự chunk)."""
    best_text = ''
    conv = (None, None, None)
    images: list[str] = []
    candidates = 0

    for payload in _iter_wrb(raw):
        # id hội thoại: payload[1] = ["c_…", "r_…"]
        if (isinstance(payload, list) and len(payload) > 1
                and isinstance(payload[1], list) and len(payload[1]) >= 2
                and isinstance(payload[1][0], str) and payload[1][0].startswith('c_')):
            conv = (payload[1][0], payload[1][1], conv[2])

        # Câu trả lời: node dạng ["rc_…", ["<text>"], …]
        for node in _walk(payload):
            if (isinstance(node, list) and len(node) >= 2
                    and isinstance(node[0], str) and _RC_RE.match(node[0])
                    and isinstance(node[1], list) and node[1]
                    and isinstance(node[1][0], str)):
                candidates += 1
                conv = (conv[0], conv[1], node[0])
                if len(node[1][0]) > len(best_text):
                    best_text = node[1][0]

    for m in _IMG_RE.finditer(raw or ''):
        u = m.group(0)
        if u not in images:
            images.append(u)

    return {'text': best_text, 'conv': conv, 'images': images,
            'candidates': candidates, 'raw_len': len(raw or '')}


def conversation_slug(conv_id: str) -> str:
    """`c_160db5890af84cdd` → `160db5890af84cdd`.

    ⚠️ URL hội thoại BỎ tiền tố `c_`. Verify thật (2026-09-06): mở
    `/app/c_<id>` cho ra trang TRỐNG (Angular không nhận ra route, chỉ hiện
    sidebar) — DOM reader nhìn thấy 0 `structured-content-container` và timeout;
    mở `/app/<id>` mới render đúng hội thoại. Capture request thật cũng cho
    `source-path=%2Fapp%2F160db5890af84cdd` trong khi id là `c_160db5890af84cdd`.
    """
    conv_id = (conv_id or '').strip()
    return conv_id[2:] if conv_id.startswith('c_') else conv_id


def conversation_url(conv_id: str, base: str = 'https://gemini.google.com/app') -> str:
    slug = conversation_slug(conv_id)
    return f'{base}/{slug}' if slug else base
