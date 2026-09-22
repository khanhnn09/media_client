"""Flow API — endpoint constants + body builders + response parsers, dùng bởi
`worker.py` khi gọi TRỰC TIẾP (Python, không qua DOM/UI) tới
aisandbox-pa.googleapis.com để tạo ảnh/video.

(2026-08-12) Port từ `tests/utils/flow_api.py` — nơi các endpoint/body này được
capture + verify THẬT từ network traffic của Flow UI thật (xem
`tests/FLOW_API_CAPTURE.md`). File này KHÔNG import gì từ `tests/` (production
không phụ thuộc test code) — chỉ port lại phần THUẦN DỮ LIỆU (constants + hàm
build dict/parse dict), bỏ hẳn `FlowBrowserSession`/`FlowAPIClient` (2 class đó
quản lý vòng đời 1 phiên Selenium riêng cho mục đích test — `SeleniumFlowWorker`
trong `worker.py` đã tự quản lý driver/token/request theo cách RIÊNG của nó).

⚠️ Nếu Google đổi endpoint/body shape/model — SỬA CẢ 2 NƠI (file này VÀ
`tests/utils/flow_api.py`, 2 bản ĐỘC LẬP không dùng chung code) — cách xác nhận
lại: dùng DevTools Network tab bắt request thật từ Flow UI, hoặc chạy lại các
script `tests/_test_*.py`/`tests/_investigate_*.py` đã dùng để capture ban đầu."""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

AISANDBOX_BASE = 'https://aisandbox-pa.googleapis.com/v1'

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

IMAGE_ASPECT = {
    '16:9': 'IMAGE_ASPECT_RATIO_LANDSCAPE',
    '9:16': 'IMAGE_ASPECT_RATIO_PORTRAIT',
    '1:1':  'IMAGE_ASPECT_RATIO_SQUARE',
    '4:3':  'IMAGE_ASPECT_RATIO_LANDSCAPE',
}
VIDEO_ASPECT = {
    '16:9': 'VIDEO_ASPECT_RATIO_LANDSCAPE',
    '9:16': 'VIDEO_ASPECT_RATIO_PORTRAIT',
    '1:1':  'VIDEO_ASPECT_RATIO_SQUARE',
}

# Crop mặc định "không crop" (toàn ảnh) cho startImage/endImage của frameToVideo.
FULL_IMAGE_CROP = {'top': 0.0, 'left': 0.0, 'bottom': 1.0, 'right': 1.0}


@dataclass(frozen=True)
class FlowEndpoint:
    name: str
    path: str  # có thể chứa {project_id}

    def url(self, project_id: str = '') -> str:
        return AISANDBOX_BASE + self.path.format(project_id=project_id)


TEXT_TO_IMAGE = FlowEndpoint('textToImage', '/projects/{project_id}/flowMedia:batchGenerateImages')
TEXT_TO_VIDEO = FlowEndpoint('textToVideo', '/video:batchAsyncGenerateVideoText')
# imageToVideo/componentsToVideo (N ảnh "nguyên liệu", sub-tab "Thành phần").
INGREDIENT_TO_VIDEO = FlowEndpoint('ingredientToVideo', '/video:batchAsyncGenerateVideoReferenceImages')
# frameToVideo (ảnh "Bắt đầu"/"Kết thúc", sub-tab "Khung hình") — CHỈ
# `startImage` đã xác nhận hoạt động (HTTP 200), `endImage` CHƯA proven (xem
# `build_frame_to_video_body()`).
FRAME_TO_VIDEO = FlowEndpoint('frameToVideo', '/video:batchAsyncGenerateVideoStartImage')
# Upload ảnh tham chiếu — path TUYỆT ĐỐI (không nằm dưới /projects/{id}/).
# KHÔNG cần recaptcha (khác 4 endpoint generate ở trên).
UPLOAD_IMAGE = FlowEndpoint('uploadImage', '/flow/uploadImage')


def _session_id(existing: str = '') -> str:
    if existing and existing.startswith(';'):
        return existing
    return f';{int(time.time() * 1000)}'


def build_client_context(
    project_id: str,
    captcha: str,
    session_id: str = '',
    *,
    base: dict | None = None,
    user_paygate_tier: str = 'PAYGATE_TIER_TWO',
) -> dict:
    ctx = {
        **(base or {}),
        'projectId': project_id,
        'tool': 'PINHOLE',
        'sessionId': _session_id(session_id),
        'recaptchaContext': {
            'token': captcha,
            'applicationType': 'RECAPTCHA_APPLICATION_TYPE_WEB',
        },
    }
    if user_paygate_tier:
        ctx['userPaygateTier'] = user_paygate_tier
    return ctx


def build_image_input_ref(media_name: str) -> dict:
    """1 phần tử `imageInputs[]` (textToImage/imageToImage) — `media_name` là
    `media.name` (uuid) trả về từ `POST /flow/uploadImage`."""
    return {'imageInputType': 'IMAGE_INPUT_TYPE_REFERENCE', 'name': media_name}


def build_video_reference_image(media_name: str) -> dict:
    """1 phần tử `referenceImages[]` cho ingredientToVideo — field tên `mediaId`
    (KHÁC `imageInputs` của ảnh, vốn dùng field `name`)."""
    return {'mediaId': media_name, 'imageUsageType': 'IMAGE_USAGE_TYPE_ASSET'}


def build_text_to_image_body(
    project_id: str,
    prompt: str,
    captcha: str,
    *,
    aspect_ratio: str = '16:9',
    model: str = DEFAULT_IMAGE_MODEL,
    seed: int | None = None,
    image_inputs: list[dict] | None = None,
    session_id: str = '',
) -> dict:
    """Body khớp `flowMedia:batchGenerateImages` — dùng cho CẢ textToImage
    (image_inputs=None/[]) LẪN imageToImage (image_inputs có phần tử)."""
    ctx = build_client_context(project_id, captcha, session_id)
    aspect = IMAGE_ASPECT.get(aspect_ratio, IMAGE_ASPECT['16:9'])
    seed_val = seed if seed is not None else random.randint(1, 999999)
    return {
        'clientContext': ctx,
        'mediaGenerationContext': {'batchId': str(uuid.uuid4())},
        'useNewMedia': True,
        'requests': [{
            'clientContext': dict(ctx),
            'imageModelName': model,
            'imageAspectRatio': aspect,
            'structuredPrompt': {'parts': [{'text': prompt}]},
            'seed': seed_val,
            'imageInputs': image_inputs or [],
        }],
    }


def build_upload_image_body(
    project_id: str,
    image_bytes_b64: str,
    *,
    file_name: str = 'ref.png',
    mime_type: str = 'image/png',
) -> dict:
    """Body khớp `POST /flow/uploadImage` — KHÔNG cần recaptcha. `image_bytes_b64`
    là base64 THUẦN (không có prefix `data:...;base64,`)."""
    return {
        'clientContext': {'projectId': project_id, 'tool': 'PINHOLE'},
        'imageBytes': image_bytes_b64,
        'isUserUploaded': True,
        'isHidden': False,
        'mimeType': mime_type,
        'fileName': file_name,
    }


def build_text_to_video_body(
    project_id: str,
    prompt: str,
    captcha: str,
    *,
    aspect_ratio: str = '16:9',
    video_model_key: str = DEFAULT_VIDEO_MODEL,
    seed: int | None = None,
    session_id: str = '',
    audio_failure_preference: str = 'BLOCK_SILENCED_VIDEOS',
) -> dict:
    """Body khớp `video:batchAsyncGenerateVideoText` (textToVideo).
    `video_model_key` nhận cả tên GUI ("Veo 3.1 - Fast") lẫn key API — tự map."""
    video_model_key = resolve_video_model_key(video_model_key)
    ctx = build_client_context(project_id, captcha, session_id)
    aspect = VIDEO_ASPECT.get(aspect_ratio, VIDEO_ASPECT['16:9'])
    seed_val = seed if seed is not None else random.randint(1, 999999)
    return {
        'mediaGenerationContext': {
            'batchId': str(uuid.uuid4()),
            'audioFailurePreference': audio_failure_preference,
        },
        'clientContext': ctx,
        'requests': [{
            'aspectRatio': aspect,
            'textInput': {'structuredPrompt': {'parts': [{'text': prompt}]}},
            'videoModelKey': video_model_key,
            'seed': seed_val,
            'metadata': {},
        }],
        'useV2ModelConfig': True,
    }


def build_ingredient_to_video_body(
    project_id: str,
    prompt: str,
    captcha: str,
    reference_images: list[dict],
    *,
    aspect_ratio: str = '16:9',
    video_model_key: str = DEFAULT_INGREDIENT_VIDEO_MODEL,
    seed: int | None = None,
    session_id: str = '',
    audio_failure_preference: str = 'BLOCK_SILENCED_VIDEOS',
) -> dict:
    """Body khớp `video:batchAsyncGenerateVideoReferenceImages`
    (imageToVideo/componentsToVideo — "Thành phần"). `reference_images`: list
    build qua `build_video_reference_image()`."""
    ctx = build_client_context(project_id, captcha, session_id)
    aspect = VIDEO_ASPECT.get(aspect_ratio, VIDEO_ASPECT['16:9'])
    seed_val = seed if seed is not None else random.randint(1, 999999)
    return {
        'mediaGenerationContext': {
            'batchId': str(uuid.uuid4()),
            'audioFailurePreference': audio_failure_preference,
        },
        'clientContext': ctx,
        'requests': [{
            'aspectRatio': aspect,
            'textInput': {'structuredPrompt': {'parts': [{'text': prompt}]}},
            'videoModelKey': video_model_key,
            'seed': seed_val,
            'metadata': {},
            'referenceImages': reference_images,
        }],
        'useV2ModelConfig': True,
    }


def build_frame_to_video_body(
    project_id: str,
    prompt: str,
    captcha: str,
    start_image_media_name: str,
    *,
    end_image_media_name: str | None = None,
    start_crop: dict | None = None,
    end_crop: dict | None = None,
    aspect_ratio: str = '16:9',
    video_model_key: str = DEFAULT_FRAME_VIDEO_MODEL,
    seed: int | None = None,
    session_id: str = '',
    audio_failure_preference: str = 'BLOCK_SILENCED_VIDEOS',
) -> dict:
    """Body khớp `video:batchAsyncGenerateVideoStartImage` (frameToVideo).

    ⚠️ `end_image_media_name` CHƯA XÁC NHẬN hoạt động (server từng trả 400
    "Unknown name endImage" khi field này được gửi) — xem docstring gốc ở
    `tests/utils/flow_api.py::build_frame_to_video_body()`. Mặc định `None`
    (an toàn, khớp case ĐÃ verify HTTP 200)."""
    ctx = build_client_context(project_id, captcha, session_id)
    aspect = VIDEO_ASPECT.get(aspect_ratio, VIDEO_ASPECT['16:9'])
    seed_val = seed if seed is not None else random.randint(1, 999999)
    req: dict[str, Any] = {
        'aspectRatio': aspect,
        'textInput': {'structuredPrompt': {'parts': [{'text': prompt}]}},
        'videoModelKey': video_model_key,
        'seed': seed_val,
        'metadata': {},
        'startImage': {
            'mediaId': start_image_media_name,
            'cropCoordinates': start_crop or FULL_IMAGE_CROP,
        },
    }
    if end_image_media_name:
        req['endImage'] = {
            'mediaId': end_image_media_name,
            'cropCoordinates': end_crop or FULL_IMAGE_CROP,
        }
    return {
        'mediaGenerationContext': {
            'batchId': str(uuid.uuid4()),
            'audioFailurePreference': audio_failure_preference,
        },
        'clientContext': ctx,
        'requests': [req],
        'useV2ModelConfig': True,
    }


def parse_image_results(data: dict) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for m in data.get('media', []):
        img = m.get('image', {}).get('generatedImage', {})
        fife = img.get('fifeUrl', '')
        if fife:
            results.append({'url': fife, 'name': m.get('name', ''), 'mime': 'image/jpeg'})
    return results


def parse_uploaded_image_name(data: dict) -> str:
    """Trích `media.name` (uuid) từ response `uploadImage`."""
    name = (data.get('media') or {}).get('name') or ''
    if not name:
        raise RuntimeError(f'uploadImage response thiếu media.name: {str(data)[:400]}')
    return name


def parse_video_workflow(data: dict) -> dict[str, Any]:
    """Trích thông tin workflow/media từ response 3 endpoint video async
    (batchAsyncGenerateVideoText/ReferenceImages/StartImage) — shape THẬT:
    `{remainingCredits, workflows:[{name, metadata:{displayName, primaryMediaId,
    batchId}}], media:[{name, workflowId, mediaMetadata:{...}}]}`. Response này
    CHỈ xác nhận đã TẠO workflow (submit thành công) — KHÔNG có URL video ngay
    (sinh video là tác vụ ASYNC). Worker API mode submit xong là sang prompt
    tiếp — không poll tile; kết quả lấy sau qua projectInitialData."""
    workflows = data.get('workflows') or []
    media = data.get('media') or []
    return {
        'remainingCredits': data.get('remainingCredits'),
        'workflowId': (workflows[0].get('name') if workflows else '') or '',
        'displayName': (workflows[0].get('metadata', {}).get('displayName') if workflows else '') or '',
        'mediaId': (media[0].get('name') if media else '') or '',
    }
