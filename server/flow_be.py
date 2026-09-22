"""Builder body cho các RPC GENERATE qua `batchexecute` của flow.google.com.

Vai trò song song với `tests/utils/flow_api.py` (đường `aisandbox` cũ) — cùng
là "nơi duy nhất biết shape body", để `worker.py` chỉ lo điều phối.

⚠️ Vì sao có module này (2026-09-04): Google đã đổi app Flow sang
`flow.google.com` (Angular Material) và **bỏ hẳn** endpoint `aisandbox` khỏi
app — đường cũ giờ 403 hàng loạt trong sản xuất (log profile 25 lúc 00:33-00:34
ngày 2026-09-04: `curl_cffi` 403 với cả 4 impersonation, phải hái lại bearer
token mới qua được 1 lệnh, rồi 403 tiếp; fallback `fetch()` trong trang chết vì
CORS). Đường `batchexecute` là đường CHÍNH APP ĐANG DÙNG.

So với `aisandbox`, đường này BỎ được: bearer token, fingerprint
(`x-browser-validation`/`x-client-data`), và `curl_cffi` impersonate. Nhưng
VẪN CẦN reCAPTCHA (phỏng đoán ban đầu "chỉ cần bl/f.sid/at" là SAI) — token
nằm ở `clientContext[10][0]`.

Mọi shape dưới đây **capture từ request THẬT** (chạy đúng luồng DOM sản xuất
rồi chặn network), KHÔNG suy từ bundle đã minify — xem
`docs/FLOW_BATCHEXECUTE_API.md` và `tests/_flow_rpc_capture/*.json`.
"""
from __future__ import annotations

import base64
import json
import random
import re
import uuid as _uuid

# ── rpcid (trích từ bundle, xem tests/_extract_rpcid_map.py) ────────────────
RPC_UPLOAD_IMAGE = 'maseQ'            # /FlowService.UploadImage
RPC_TEXT_TO_IMAGE = 'ogiZ0b'          # /FlowService.BatchGenerateImages
RPC_TEXT_TO_VIDEO = 'YhhmEf'          # /VideoFxService.BatchAsyncGenerateVideoText
RPC_INGREDIENT_TO_VIDEO = 'MZZa6b'    # /VideoFxService.BatchAsyncGenerateVideoReferenceImages
RPC_FRAME_TO_VIDEO = 'eb1hJf'         # /VideoFxService.BatchAsyncGenerateVideoStartImage
RPC_GET_MEDIA = 'as29s'               # /FlowService.GetMedia

# ── enum tỉ lệ khung hình ──────────────────────────────────────────────────
# ⚠️ ẢNH và VIDEO dùng 2 enum KHÁC NHAU — xác minh bằng capture thật 2026-09-04
# (diff 2 request 16:9 vs 9:16, xem `_flow_rpc_capture/*_16x9*.json` /
# `*_9x16*.json`). Khớp với bundle: `_.RPa` chỉ khai VIDEO_ASPECT_RATIO_LANDSCAPE
# và _PORTRAIT (video KHÔNG có SQUARE), còn ảnh có thêm SQUARE/4:3/3:4 — nên
# ảnh lệch 1 bậc so với video.
IMAGE_ASPECT_ENUM = {'16:9': 3, '9:16': 2}   # ĐÃ VERIFY bằng capture thật
VIDEO_ASPECT_ENUM = {'16:9': 2, '9:16': 1}   # ĐÃ VERIFY bằng capture thật
# 1:1 / 4:3 / 3:4 CHƯA verify — cố tình KHÔNG đoán. `aspect_enum()` trả None
# cho các giá trị này để `worker.py` rơi về đường aisandbox thay vì gửi sai tỉ lệ.

_UUID_LOWER_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")
_CDN_URL_RE = re.compile(r"https://[^\"\s\\]*flow-content\.google/[^\"\s\\]+")

# Lỗi chống-lạm-dụng phía Google: KHÔNG phải lỗi của batchexecute, đường
# `aisandbox` cũng trả đúng lỗi này (403 "reCAPTCHA evaluation failed" kèm
# `PUBLIC_ERROR_UNUSUAL_ACTIVITY`). Đổi đường không giúp gì — chỉ chờ/đổi
# tài khoản/giảm nhịp mới hết.
ACCOUNT_BLOCK_REASONS = ('PUBLIC_ERROR_UNUSUAL_ACTIVITY',
                         'PUBLIC_ERROR_USER_QUOTA_REACHED',
                         'PUBLIC_ERROR_MODEL_ACCESS_DENIED')


class BatchExecuteError(RuntimeError):
    """RPC trả về entry `wrb.fr` KHÔNG có payload nhưng CÓ khối lỗi.

    Tách riêng để phân biệt với "payload null hợp lệ" (vd `as29s` gọi trên 1
    media upload thô) — bản đầu gộp 2 trường hợp làm một nên lỗi bị báo nhầm
    thành "payload rỗng", che mất mã lỗi thật (bug thật, log sản xuất
    2026-09-04 01:20)."""

    def __init__(self, reason: str, rpc_id: str = ''):
        self.reason = reason
        self.rpc_id = rpc_id
        super().__init__(f'RPC {rpc_id} lỗi: {reason}' if rpc_id else reason)

    @property
    def is_account_block(self) -> bool:
        return self.reason in ACCOUNT_BLOCK_REASONS


def extract_rpc_error(entry) -> str:
    """Mã lỗi trong entry `wrb.fr` (khi `entry[2]` là null), hoặc '' nếu đây
    chỉ là payload null bình thường.

    2 shape thật quan sát được cho `entry[5]` khi CÓ lỗi — cả 2 đều là 1 LIST
    KHÔNG null (khác payload-null-bình-thường, lúc đó `entry[5]` chính nó là
    `null` — vd `as29s` gọi trên 1 media upload thô, chưa qua generation):
      (a) có chi tiết — `[7,null,[["type.googleapis.com/google.rpc.ErrorInfo",
          ["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]]`
      (b) CHỈ có mã số trần, KHÔNG chi tiết — `[8]`, `[5]`, … (bắt được thật
          2026-09-11: `[8]` lúc 1 account khác đang bị Google chặn/hết quota
          verify qua upload thành công bình thường trên account KHÁC cùng
          lúc; `[5]`=NOT_FOUND tự tái hiện khi gửi `referenceImages` với uuid
          ảnh KHÔNG thuộc project — nên mã số trần KHÔNG LUÔN LÀ chặn tài
          khoản, chỉ là lỗi RPC thật cần thấy rõ thay vì bị nuốt thành
          "payload rỗng"). Trước đây CHỈ nhận diện được case (a) — case (b)
          bị coi nhầm là payload-null-bình-thường, che mất lỗi thật."""
    try:
        err_block = entry[5] if isinstance(entry, list) and len(entry) > 5 else None
    except Exception:
        return ''
    if err_block is None:
        return ''
    try:
        blob = json.dumps(err_block, ensure_ascii=False)
    except Exception:
        return ''
    m = re.search(r'"(PUBLIC_ERROR_[A-Z0-9_]+)"', blob)
    if m:
        return m.group(1)
    if 'rpc.ErrorInfo' in blob:
        m = re.search(r'"([A-Z][A-Z0-9_]{5,})"', blob)
        return m.group(1) if m else 'UNKNOWN_RPC_ERROR'
    # Không có text chi tiết — chỉ có mã số gRPC trần (vd `[8]`) → vẫn trả về
    # để caller THẤY được (qua BatchExecuteError), không còn im lặng coi là
    # payload rỗng. Chủ ý KHÔNG đưa vào `ACCOUNT_BLOCK_REASONS` — mã số trần
    # dùng chung cho MỌI loại lỗi RPC (đã tự tái hiện cả NOT_FOUND lẫn nghi
    # chặn-tài-khoản với 2 mã khác nhau), không đủ để khẳng định là chặn
    # tài khoản chỉ từ con số.
    if isinstance(err_block, list) and err_block and isinstance(err_block[0], int):
        return f'RPC_ERROR_CODE_{err_block[0]}'
    return 'UNKNOWN_RPC_ERROR'


def aspect_enum(aspect_ratio: str, *, video: bool) -> int | None:
    """Trả số enum, hoặc None nếu tỉ lệ CHƯA được verify (caller tự fallback)."""
    table = VIDEO_ASPECT_ENUM if video else IMAGE_ASPECT_ENUM
    return table.get((aspect_ratio or '').strip())


def _uid() -> str:
    """UUID client tự sinh — trang LUÔN gửi dạng CHỮ HOA."""
    return str(_uuid.uuid4()).upper()


def client_context(project_id: str, captcha: str) -> list:
    """`clientContext` — lặp lại ở nhiều vị trí trong cùng 1 body.
    Ý nghĩa `[1]=22` chưa rõ (giữ nguyên giá trị capture)."""
    return [None, 22, None, None, None, project_id, None, None, None, None,
            [captcha, 1]]


# ── maseQ — upload ảnh ─────────────────────────────────────────────────────
def build_upload_image_args(project_id: str, captcha: str, image_bytes: bytes,
                            file_name: str, mime_type: str) -> list:
    return [
        client_context(project_id, captcha),
        base64.b64encode(image_bytes).decode('ascii'),
        mime_type,
        1,
        None, None, None, None,
        file_name,
        None,
        _uid(),
        _uid(),
    ]


def parse_uploaded_media_name(payload) -> str:
    """`payload[0][0]` = DETAIL_UUID — CHÍNH LÀ id dùng cho `imageInputs`/
    `referenceImages`.

    ⚠️ KHÔNG dùng `payload[0][2]` — đó là *tile id*, một uuid KHÁC. Đã xác
    minh 2026-09-04: upload 1 ảnh rồi tra listing `Zzl0ze` theo TÊN FILE —
    `payload[0][0]` khớp `meta[4]` (DETAIL_UUID), `payload[0][2]` khớp
    `entry[0]` (tile id); và uuid mà capture imageToImage dùng làm
    `imageInputs` chính là `meta[4]`. Đây là đúng cái bẫy đã trả giá 1 lần ở
    `_dom_fetch_project_media()` (xem CLAUDE.md §11.45)."""
    try:
        name = payload[0][0]
        return name if isinstance(name, str) and _UUID_LOWER_RE.fullmatch(name) else ''
    except Exception:
        return ''


# ── ogiZ0b — sinh ảnh (text→image và image→image) ──────────────────────────
def build_image_input_ref(media_name: str) -> list:
    return [media_name, None, None, None, 1]


def build_text_to_image_args(project_id: str, prompt: str, captcha: str, *,
                             aspect_ratio: str = '16:9',
                             model: str = 'GEM_PIX_2',
                             image_inputs: list | None = None,
                             seed: int | None = None) -> list:
    aspect = aspect_enum(aspect_ratio, video=False)
    if aspect is None:
        raise ValueError(f'aspect_ratio "{aspect_ratio}" chưa verify cho batchexecute')
    ctx = client_context(project_id, captcha)
    req = [None] * 14
    req[2] = list(image_inputs) if image_inputs else None
    req[3] = seed if seed is not None else random.randint(1, 999999999)
    req[4] = aspect
    req[5] = model
    req[7] = ctx
    req[8] = [[[prompt]]]
    req[12] = _uid()
    req[13] = _uid()
    return [None, [req], 1, list(ctx), [_uid()]]


def parse_image_results(payload, exclude: set | None = None) -> list:
    """Trả `[{'type':'image','name':uuid,'url':cdn_url|''}]`.

    ⚠️ `exclude` BẮT BUỘC phải chứa uuid của ẢNH THAM CHIẾU (đầu vào) và
    `projectId`. Bug thật đã gặp lúc verify 2026-09-04: imageToImage trả về
    2 kết quả cho 1 lần sinh — uuid ảnh ref (input) cũng nằm trong response và
    bị tính nhầm thành kết quả, khiến sản xuất tải chính ảnh đầu vào về rồi
    lưu như ảnh đã sinh.

    Ưu tiên uuid có CDN URL ngay trong response (chắc chắn là media thật);
    chỉ khi KHÔNG có URL nào mới quét uuid trần để caller tự resolve qua
    `as29s` (cùng cách `_dom_fetch_project_media()` đã làm)."""
    skip = set(exclude or ())
    flat = json.dumps(payload, ensure_ascii=False)

    by_uuid: dict[str, str] = {}
    for u in _CDN_URL_RE.findall(flat):
        m = _UUID_LOWER_RE.search(u)
        if m:
            by_uuid.setdefault(m.group(1), u)
    if by_uuid:
        return [{'type': 'image', 'name': n, 'url': u}
                for n, u in by_uuid.items() if n not in skip]

    out, seen = [], set()
    for name in _UUID_LOWER_RE.findall(flat):
        if name in seen or name in skip:
            continue
        seen.add(name)
        out.append({'type': 'image', 'name': name, 'url': ''})
    return out


# ── YhhmEf / MZZa6b — sinh video (BatchAsync: submit xong là xong) ─────────
def _video_prompt_block(prompt: str) -> list:
    return [None, None, [[[prompt]]]]


def _video_ids_block(size: int) -> list:
    ids = [None] * size
    ids[4] = _uid()
    ids[5] = _uid()
    return ids


def build_text_to_video_args(project_id: str, prompt: str, captcha: str, *,
                             aspect_ratio: str = '16:9',
                             video_model_key: str = 'abra_t2v_8s') -> list:
    aspect = aspect_enum(aspect_ratio, video=True)
    if aspect is None:
        raise ValueError(f'aspect_ratio "{aspect_ratio}" chưa verify cho batchexecute')
    req = [None] * 5
    req[0] = _video_prompt_block(prompt)
    req[1] = video_model_key
    req[2] = aspect
    req[4] = _video_ids_block(6)
    return [[req], client_context(project_id, captcha), [_uid(), 2]]


def build_ingredient_to_video_args(project_id: str, prompt: str, captcha: str,
                                   media_names: list, *,
                                   aspect_ratio: str = '16:9',
                                   video_model_key: str = 'abra_r2v_8s') -> list:
    aspect = aspect_enum(aspect_ratio, video=True)
    if aspect is None:
        raise ValueError(f'aspect_ratio "{aspect_ratio}" chưa verify cho batchexecute')
    req = [None] * 6
    req[0] = _video_prompt_block(prompt)
    req[1] = [[None, n] for n in media_names]
    req[2] = video_model_key
    req[3] = aspect
    req[5] = _video_ids_block(6)
    return [[req], client_context(project_id, captcha), [_uid(), 2]]


def parse_video_workflow(payload) -> dict:
    """RPC video là **BatchAsync…** — response chỉ xác nhận đã tạo workflow,
    KHÔNG có URL. Kết quả lấy sau qua reconcile (`Zzl0ze`+`as29s`)."""
    flat = json.dumps(payload, ensure_ascii=False)
    uuids = _UUID_LOWER_RE.findall(flat)
    return {'workflowId': uuids[0] if uuids else '',
            'mediaId': uuids[1] if len(uuids) > 1 else '',
            'raw': flat[:300]}
