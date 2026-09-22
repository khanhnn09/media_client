"""State runtime chia sẻ giữa dispatcher.py và worker.py — tách riêng thành module
KHÔNG phụ thuộc gì (không import worker.py/dispatcher.py) để tránh circular import:
worker.py cần đọc/ghi _login_drivers/_sleep_until_by_pid, còn dispatcher.py cần
SeleniumFlowWorker từ worker.py để tạo instance mới — nếu state sống chung với
dispatcher.py sẽ tạo vòng lặp import.

⚠️ `_master_task_intake_enabled`/`_quiet_hours_active` là 2 biến DUY NHẤT trong file
này bị REASSIGN (không chỉ mutate in-place) — xem dispatcher.py::
_set_master_task_intake()/_quiet_hours_tick(). Mọi module khác đọc/ghi 2 biến này
PHẢI qua `from . import state as _state` rồi `_state.xxx` (module-qualified access),
KHÔNG được `from .state import _master_task_intake_enabled` vì sẽ chỉ snapshot giá
trị lúc import, không thấy được thay đổi sau đó. Các biến còn lại (dict/set) an toàn
để `from .state import xxx` bình thường vì chỉ mutate in-place (.pop/.add/.clear/
[key]=...), không bao giờ bị gán lại toàn bộ."""

_workers:       dict = {}   # profile_id → (SeleniumFlowWorker, Thread)
_login_drivers: dict = {}   # profile_id → WebDriver (login browser đang mở, detach=True)

# _desired_veo3 được nạp bởi 2 nguồn: (1) user bấm Start (/start endpoint), (2)
# _auto_scale_veo3_tick() tự thêm/bớt theo backlog task thực tế — cả 2 nguồn dùng
# chung tập này, dispatcher tick không phân biệt profile được "desired" bằng cách nào.
_desired_veo3:       set  = set()   # profile_id đang muốn chạy (thủ công hoặc auto), kể cả khi 'waiting'
_sleep_until_by_pid: dict = {}      # profile_id → epoch time hết "ngủ" (xem SeleniumFlowWorker._handle_task_error)

# (2026-08-20) profile_id BẮT BUỘC chạy bước "check đăng nhập Google" đầy đủ ở lần
# khởi động worker KẾ TIẾP — BẤT KỂ setting `google_login_check_enabled` đang tắt.
# Set bởi `worker.py::_clear_profile_if_sleeping()` NGAY SAU khi vừa xoá TẤT CẢ
# cookie (bậc thang escalation THEO BATCH — `batch_fail_count_before_sleep` batch
# lỗi liên tiếp): vừa xoá sạch cookie thì chắc chắn đã đăng xuất Google, bỏ qua
# check thì worker mới sẽ vào thẳng labs.google ở trạng thái chưa đăng nhập và
# MỌI task sau đó đều lỗi ở bước tìm DOM.
#
# Phải sống ở module-level (không phải trên object worker) vì worker CŨ sắp thoát
# hẳn — worker MỚI do dispatcher tạo sau khi hết giờ ngủ là 1 instance khác hoàn
# toàn, không có cách nào đọc lại state của instance cũ. Cùng lý do/cùng pattern
# với `_sleep_until_by_pid` ngay trên. Cờ dùng-1-lần: `_ensure_google_login()` tự
# `discard()` ngay khi đọc được (không giữ mãi — lần chạy sau đó trở về đúng theo
# setting `google_login_check_enabled` như bình thường).
_force_login_check: set = set()

# Master switch (xem dispatcher.py::_set_master_task_intake) — 1 công tắc DUY NHẤT
# cho CẢ client_tool (mọi profile, kể cả worker_mode='gemini'), điều khiển từ
# main.py (Sidebar). Chỉ sống trong RAM — restart server luôn về lại mặc định.
#
# Mặc định FALSE (2026-07-17, theo yêu cầu user "mặc định khi mở tool sẽ không
# nhận task") — TRƯỚC ĐÂY mặc định True, nghĩa là mở app xong auto-scale/dispatcher
# TỰ ĐỘNG mở Chrome + nhận task ngay lập tức, không cho user cơ hội kiểm tra
# settings/trạng thái trước. Giờ mở app luôn ở trạng thái TẠM DỪNG — user phải tự
# bấm nút BẬT (Sidebar) mới bắt đầu nhận task. Không ảnh hưởng gì tới cơ chế
# TẮT/BẬT khi đang chạy (_set_master_task_intake) — chỉ đổi giá trị khởi tạo lúc
# server mới start.
_master_task_intake_enabled = False
_master_switch_snapshot: set = set()   # profile_id đang chạy ngay trước lúc tắt switch

_IMAGE_TASK_MODES = ('textToImage', 'imageToImage')
_VIDEO_TASK_MODES = ('textToVideo', 'imageToVideo', 'frameToVideo', 'componentsToVideo')
_pending_cache = {'data': None, 'fetched_at': 0.0}
_PENDING_CACHE_TTL = 8  # giây — khớp POLL_INTERVAL mặc định, đủ mới cho auto-scale
_pending_fail_streak = {'count': 0}
_PENDING_MAX_STALE_FAILS = 3  # ~3 tick liên tiếp lỗi (~30s) mới hết tin cache cũ

# Backlog Gemini (2026-07-19) — mirror _pending_cache/_pending_fail_streak ở trên
# nhưng cho GET /api/gemini/pending_count (bảng gemini_pending_requests), dùng
# bởi _auto_scale_gemini_tick() (dispatcher.py) để biết CÓ backlog gemini đang
# chờ máy hay không, phản ứng giống hệt cách _auto_scale_veo3_tick() phản ứng
# theo pending_by_mode — thay cho cơ chế "giữ sẵn N máy luôn online" cũ (đã bỏ
# theo yêu cầu user: "tôi muốn như VEO khi có task thì mới tự bật, kết thúc thì
# tự tắt chứ không phải bật thủ công").
_gemini_pending_cache = {'data': None, 'fetched_at': 0.0}
_GEMINI_PENDING_CACHE_TTL = 8
_gemini_pending_fail_streak = {'count': 0}
_GEMINI_PENDING_MAX_STALE_FAILS = 3

# Khung giờ không nhận task (2026-07-20, xem dispatcher.py::_quiet_hours_tick()) —
# theo dõi trạng thái ĐÃ áp dụng gần nhất, để tick chỉ hành động lúc CHUYỂN trạng
# thái (bắt đầu/kết thúc khung giờ), không ép buộc Master switch liên tục suốt
# khung giờ — user vẫn tự bật lại thủ công giữa chừng được nếu cần xử lý gấp.
_quiet_hours_active = False

# Grace period trước khi đóng profile gemini "trông có vẻ rảnh" (2026-07-21, xem
# dispatcher.py::_auto_scale_gemini_tick()) — bug thật: backend giao prompt TRỰC
# TIẾP cho 1 machine rảnh (set `machines_media.pending_prompt`, KHÔNG qua bảng
# `gemini_pending_requests`) xong KHÔNG đổi `status` ngay — máy chỉ tự đổi
# `status='processing'` ở LẦN HEARTBEAT KẾ TIẾP của chính nó (`_run_task_gemini()`,
# cách nhau tối đa POLL_INTERVAL=8s). Nếu dispatcher tick (chu kỳ 10s) rơi đúng
# vào khe hở này — `gemini_pending_requests` rỗng (vì dispatch trực tiếp, không
# qua hàng đợi) NÊN `has_backlog=False`, còn máy vẫn đọc `status='idle'` (chưa
# kịp đổi) — code cũ COI LÀ RẢNH THẬT và đóng luôn, giết chết task vừa giao (mất
# vĩnh viễn vì nó chưa từng nằm trong `gemini_pending_requests` để retry). Dict
# này lưu profile_id → thời điểm ĐẦU TIÊN thấy "có vẻ rảnh" liên tục — chỉ đóng
# thật khi đã liên tục "rảnh" qua _GEMINI_CLOSE_GRACE_SECS (đủ hơn 1 chu kỳ
# heartbeat của chính worker để nó kịp tự cập nhật status nếu THẬT SỰ có việc).
_gemini_idle_since: dict = {}
_GEMINI_CLOSE_GRACE_SECS = 20
