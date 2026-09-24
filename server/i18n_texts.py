"""i18n text variants dùng cho DOM automation (worker.py::_dom_upload_images) —
tách ra file JSON riêng `client_tool/i18n_texts.json` (2026-07-17, theo yêu cầu
user "tách danh sách click theo language ra file json để dễ thay đổi") thay vì
hardcode trong Python — sửa/thêm biến thể ngôn ngữ (vd phát hiện thêm 1 cách
Google hiển thị text khác) chỉ cần sửa file JSON, KHÔNG cần đổi code.

KHÁC `local_settings.json` (state runtime riêng từng máy, gitignore) — file này
là DEFAULT DÙNG CHUNG, commit vào git như source thật (không phải cấu hình cá
nhân). `_DEFAULT_I18N_TEXTS` trong file này chỉ là lưới an toàn nếu
`i18n_texts.json` bị xoá/hỏng, không phải nguồn chính."""

import json
from pathlib import Path

from .config import log

_I18N_TEXTS_PATH = Path(__file__).parent.parent / 'i18n_texts.json'

_DEFAULT_I18N_TEXTS = {
    # (2026-09-24) Nhãn giao diện flow.google.com — Flow dịch aria-label/chữ
    # theo ngôn ngữ TÀI KHOẢN (xác nhận trên profile 25 tiếng Việt). Biến thể
    # tiếng Việt lấy từ DOM thật, trừ 'Đang tải lên'/'Thêm vào lời nhắc'
    # là dự đoán (chưa thấy trên trang).
    'flowSettingsTrigger': ['Settings trigger', 'Điều kiện kích hoạt cài đặt'],
    'flowStartGeneration': ['Start generation', 'Bắt đầu tạo'],
    'flowAddIngredients': ['Add ingredients to the prompt box', 'Thêm thành phần vào ô nhập câu lệnh'],
    'flowSelectModel': ['Select model family', 'Chọn nhóm mô hình'],
    'flowToggleMode': ['Mode', 'Chế độ'],
    'flowToggleVideoType': ['Video type', 'Loại video'],
    'flowToggleAspectRatio': ['Aspect ratio', 'Tỷ lệ khung hình'],
    'flowToggleOutputCount': ['Output count', 'Số lượng kết quả đầu ra'],
    'flowVideoTypeIngredients': ['Ingredients', 'Thành phần'],
    'flowVideoTypeFrames': ['Frames', 'Khung hình'],
    'flowUploadMedia': ['Upload media', 'Tải nội dung nghe nhìn lên'],
    'flowUploading': ['Uploading', 'Đang tải lên'],
    'flowToggleDuration': ['Video duration', 'Duration', 'Thời lượng video'],
    'flowFrameStart': ['Start', 'Bắt đầu'],
    'flowFrameEnd': ['End', 'Kết thúc'],
    'flowCreditWarning': ['credit', 'tín dụng'],
    'flowAddToPrompt': ['Add to prompt', 'Thêm vào câu lệnh', 'Thêm vào lời nhắc'],
    'imageTab':          ['Hình ảnh', 'Images'],
    'searchPlaceholder': ['Tìm kiếm thành phần', 'Search assets'],
    'addToPrompt':       ['Thêm vào câu lệnh', 'Add to Prompt'],
    'newProject':        ['Dự án mới', 'New project'],
    # (2026-08-10) Nút xen giữa đôi khi hiện khi navigate THẲNG tới URL 1
    # project cụ thể (/project/{uuid}) — bấm để thực sự vào được project.
    # "Create with Google Flow" xác nhận qua HTML thật user gửi; biến thể
    # tiếng Việt là DỰ ĐOÁN best-effort (chưa xác nhận qua tài khoản VN thật,
    # khác "Dự án mới" đã verify) — thêm sẵn theo đúng tinh thần phòng ngừa
    # đã áp dụng cho `newProject` (lịch sử: hardcode chỉ tiếng Anh từng gây
    # miss hẳn trên tài khoản Việt).
    'createWithFlow':    ['Tạo trong Google Flow', 'Create with Google Flow'],
    # (2026-08-10) Nút "Sign in" trên workspace.google.com (trang Google
    # redirect tới khi vào gmail.com mà CHƯA đăng nhập) — dẫn tới
    # accounts.google.com/AccountChooser/signinchooser. Xem
    # `_ensure_google_login()`, server/worker.py.
    'googleSignIn':      ['Đăng nhập', 'Sign in'],
    # (2026-09-09) Gemini chat "Tạo video" — bug thật bắt được qua test end-to-end
    # THẬT với 2 account khác nhau: account tiếng Việt (menu chỉ có "Tạo hình
    # ảnh"/"Tạo nhạc", KHÔNG có "Tạo video" — có thể do account đó thật sự không
    # có quyền, hoặc mục bị ẩn dưới "Các công cụ khác"/"More tools", CHƯA xác
    # nhận) và account tiếng Anh (huavantien84@gmail.com — menu CÓ "Create
    # video" đầy đủ, nhưng `_gemini_video_enter_mode()`/`_gemini_video_set_ratio()`
    # trước đây hardcode CHỈ tiếng Việt ('Tạo video'/'Bỏ chọn Video'/'Tỷ lệ khung
    # hình'/'Ngang'/'Dọc') nên KHÔNG BAO GIỜ khớp trên account này — raise
    # RuntimeError "Không tìm thấy mục Tạo video" dù mục đó THẬT SỰ có mặt.
    # Verify trực tiếp qua Selenium thật (không đoán): dump toàn bộ menu "+" +
    # click "Create video" + dump aria-label — xác nhận đúng 5 giá trị tiếng Anh
    # dưới đây (`Deselect Videos` SỐ NHIỀU, khác `Bỏ chọn Video` số ít tiếng
    # Việt — vẫn so khớp đúng vì literal-match không quan tâm ngữ pháp).
    'geminiCreateVideo':      ['Tạo video', 'Create video'],
    'geminiDeselectVideo':    ['Bỏ chọn Video', 'Deselect Videos'],
    # (2026-09-13) — mirror geminiCreateVideo/geminiDeselectVideo cho mục "Tạo
    # hình ảnh" trong menu '+' (xem `_gemini_image_enter_mode()`). ⚠️ CHƯA
    # VERIFY qua DOM Gemini thật — SUY LUẬN theo đúng convention đã xác nhận
    # cho video, KHÔNG PHẢI capture trực tiếp. Sai thì chỉ cần sửa file này/
    # `i18n_texts.json`, không cần đổi code.
    'geminiCreateImage':      ['Tạo hình ảnh', 'Create images', 'Create image'],
    'geminiDeselectImage':    ['Bỏ chọn Hình ảnh', 'Bỏ chọn hình ảnh', 'Deselect images', 'Deselect Images'],
    'geminiAspectRatioPrefix': ['Tỷ lệ khung hình', 'Aspect ratio'],
    'geminiRatioHorizontal':  ['Ngang', 'Landscape'],
    'geminiRatioVertical':    ['Dọc', 'Portrait'],
}

_cache: dict | None = None


def get_i18n_texts() -> dict:
    """Trả dict {key: [variant, ...]} — load từ i18n_texts.json 1 lần, cache
    trong RAM. Key nào file thiếu/sai kiểu (không phải list[str]) tự lấy từ
    _DEFAULT_I18N_TEXTS, không làm hỏng cả file vì 1 key lỗi."""
    global _cache
    if _cache is not None:
        return _cache

    merged = dict(_DEFAULT_I18N_TEXTS)
    try:
        with open(_I18N_TEXTS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, variants in data.items():
                if isinstance(variants, list) and all(isinstance(v, str) for v in variants):
                    merged[key] = variants
                else:
                    log.warning(f'[i18n_texts] Key "{key}" trong {_I18N_TEXTS_PATH.name} '
                                f'không phải list[str] — dùng default')
    except FileNotFoundError:
        log.warning(f'[i18n_texts] Không tìm thấy {_I18N_TEXTS_PATH} — dùng default cứng trong code')
    except Exception as e:
        log.warning(f'[i18n_texts] Lỗi đọc {_I18N_TEXTS_PATH}: {e} — dùng default cứng trong code')

    _cache = merged
    return merged
