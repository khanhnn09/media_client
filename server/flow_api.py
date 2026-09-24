"""Bảng tra model — tên hiển thị GUI (vd "Veo 3.1 - Lite") → key thật của Flow.

Lưới an toàn của `model_catalog.py` (nguồn chính là `veo_models.model_key` ở
backend + catalog thật qua RPC `HTrJv`, xem `flow_models.py`).

(2026-09-24) Đường aisandbox cũ đã bị GỠ HẲN — phần endpoint/body builder/
parser từng nằm ở đây đã xoá, chỉ giữ phần tra model. Tạo ảnh/video giờ chỉ
đi qua batchexecute (`flow_be.py`)."""

from __future__ import annotations

# ─── Models — xem CLAUDE.md client_tool để biết nguồn gốc từng giá trị ─────────
DEFAULT_IMAGE_MODEL = 'NARWHAL'
DEFAULT_VIDEO_MODEL = 'veo_3_1_t2v_lite_low_priority'

# Tên hiển thị (DB `tasks_media_flow.model` / `veo_models.name`) → videoModelKey.
# Heartbeat gửi đúng chuỗi GUI ("Veo 3.1 - Fast"), API aisandbox cần key `veo_3_1_t2v_*`.
_VIDEO_MODEL_LABEL_TO_KEY = {
    'veo 3.1 - lite [lower priority]': 'veo_3_1_t2v_lite_low_priority',
    'veo 3.1 - lite lp':               'veo_3_1_t2v_lite_low_priority',
    'veo 3.1 - lite':                  'veo_3_1_t2v_lite',
    'veo 3.1 - fast':                  'veo_3_1_t2v_fast',
    'veo 3.1 - quality':               'veo_3_1_t2v',
}
_VIDEO_MODEL_API_KEYS = frozenset(_VIDEO_MODEL_LABEL_TO_KEY.values())


def _norm_video_model_label(name: str) -> str:
    s = (name or '').strip().lower()
    s = s.replace('\u2013', '-').replace('\u2014', '-').replace('\u00a0', ' ')
    while '  ' in s:
        s = s.replace('  ', ' ')
    return s


def resolve_video_model_key(name: str | None, default: str = DEFAULT_VIDEO_MODEL) -> str:
    """Đổi tên model GUI/DB thành `videoModelKey` aisandbox.

    'Veo 3.1 - Fast' → 'veo_3_1_t2v_fast'. Key API đã đúng (`veo_3_1_t2v_*`) thì giữ nguyên.
    """
    raw = (name or '').strip()
    if not raw:
        return default
    if raw in _VIDEO_MODEL_API_KEYS or raw.startswith('veo_') or '_t2v_' in raw:
        return raw
    return _VIDEO_MODEL_LABEL_TO_KEY.get(_norm_video_model_label(raw)) or default


# (2026-08-13) `tasks_media_flow.model` cho ẢNH cũng chỉ lưu TÊN HIỂN THỊ GUI
# (`ProjectManager.jsx::FALLBACK_IMAGE`, vd "Nano Banana Pro"/"Nano Banana 2"/
# "Nano Banana 2 Lite") — KHÔNG PHẢI `imageModelName` thật của aisandbox. Bug
# thật đã gặp (task #2454): `_call_image_api_v2()` từng gửi THẲNG chuỗi hiển
# thị này làm `imageModelName` → aisandbox trả 400 `INVALID_ARGUMENT` NGAY
# (không phải lỗi mạng/proxy — mọi impersonate profile `curl_cffi` đều bị từ
# chối y hệt). Mapping THẬT (user xác nhận trực tiếp 2026-08-13, KHÔNG phải
# đoán) — cùng cấu trúc `_VIDEO_MODEL_LABEL_TO_KEY` ở trên:
_IMAGE_MODEL_LABEL_TO_KEY = {
    'nano banana pro':      'GEM_PIX_2',
    'nano banana 2':        'NARWHAL',
    'nano banana 2 lite':   'HARBOR_SEAL',
}
_IMAGE_MODEL_API_KEYS = frozenset(_IMAGE_MODEL_LABEL_TO_KEY.values())


def _norm_image_model_label(name: str) -> str:
    s = (name or '').strip().lower()
    while '  ' in s:
        s = s.replace('  ', ' ')
    return s


def resolve_image_model_key(name: str | None, default: str = DEFAULT_IMAGE_MODEL) -> tuple[str, bool]:
    """Trả `(imageModelName, matched)` — `matched=False` nghĩa là KHÔNG nhận
    diện được `name` (không phải key thật, không khớp label đã biết) — đã
    fallback về `default` (caller nên log rõ để không âm thầm sai model)."""
    raw = (name or '').strip()
    if not raw:
        return default, False
    if raw in _IMAGE_MODEL_API_KEYS:
        return raw, True
    key = _IMAGE_MODEL_LABEL_TO_KEY.get(_norm_image_model_label(raw))
    if key:
        return key, True
    return default, False
# Model key cho ingredient/frame — namespace `veo_3_1_r2v_*` KHÁC hẳn
# `veo_3_1_t2v_*` (text-to-video), dùng khi mở sub-tab "Thành phần". Capture
# thật 2026-08-12 (profile huavantien84_2 + project "khanh"), xem
# tests/FLOW_API_CAPTURE.md mục "6b"/"6c" — CHỈ capture được 1 giá trị
# (`veo_3_1_r2v_lite`), lúc đó suy ra "model do Flow UI tự khoá, KHÔNG theo
# dropdown user chọn". (2026-08-13) SUY LUẬN ĐÓ SAI — user xác nhận trực tiếp
# namespace `veo_3_1_r2v_*` CŨNG có biến thể theo tier giống hệt `veo_3_1_t2v_*`,
# chỉ khác đúng 3 ký tự `t2v`→`r2v` (vd "Veo 3.1 - Lite [Lower Priority]":
# `veo_3_1_t2v_lite_low_priority` ↔ `veo_3_1_r2v_lite_low_priority`) — bug
# thật đã gặp: project_6 (855 task `imageToVideo`, model="Veo 3.1 - Lite
# [Lower Priority]") trước đây LUÔN gửi `veo_3_1_r2v_lite` (SAI tier, bỏ qua
# hẳn task.model) vì `_call_video_api()` không hề resolve model cho nhánh
# ingredient, chỉ dùng default cứng.
DEFAULT_INGREDIENT_VIDEO_MODEL = 'veo_3_1_r2v_lite'
DEFAULT_FRAME_VIDEO_MODEL = 'abra_i2v_8s'


def resolve_ingredient_video_model_key(
    name: str | None, default: str = DEFAULT_INGREDIENT_VIDEO_MODEL,
) -> tuple[str, bool]:
    """Đổi tên model GUI/DB thành `videoModelKey` cho ingredientToVideo
    (namespace `veo_3_1_r2v_*`). (2026-08-13, theo yêu cầu user "model_key
    dùng chung ko phân biệt khi dùng ingredient xóa cột đó đi") — KHÔNG còn
    dict riêng cho ingredient (và KHÔNG còn cột DB riêng, xem `model_catalog.py`)
    — DÙNG CHUNG `_VIDEO_MODEL_LABEL_TO_KEY`/`resolve_video_model_key()` (t2v)
    rồi DERIVE sang r2v bằng cách thay `t2v`→`r2v` trong key, dựa trên pattern
    ĐÃ XÁC NHẬN (case duy nhất capture được khớp đúng phép thay thế này). Trả
    `(videoModelKey, matched)` — `matched=True` khi label/key nhận diện được
    (dù chỉ suy ra bằng swap, chưa từng capture riêng), `False` chỉ khi hoàn
    toàn không nhận diện được gì (fallback `default` — giá trị plain Lite ĐÃ
    capture verify thật, an toàn hơn suy ra từ 1 default khác)."""
    raw = (name or '').strip()
    if not raw:
        return default, False
    if raw.startswith('veo_3_1_r2v'):
        return raw, True
    if raw in _VIDEO_MODEL_API_KEYS or raw.startswith('veo_3_1_t2v') or '_t2v_' in raw:
        t2v_key = raw
    else:
        t2v_key = _VIDEO_MODEL_LABEL_TO_KEY.get(_norm_video_model_label(raw))
    if t2v_key and 't2v' in t2v_key:
        return t2v_key.replace('t2v', 'r2v', 1), True
    return default, False
