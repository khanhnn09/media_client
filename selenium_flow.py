"""
selenium_flow.py — Profile manager + Chrome worker client.

════════════════════════════════════════════════════════════════
KIẾN TRÚC TỔNG THỂ
════════════════════════════════════════════════════════════════

  [audio-cue-editor.html]
       │  tạo task (ảnh/video)
       ▼
  [server_gemini_flow.py]  ← SERVER CHÍNH (port 13443)
       │  lưu task vào DB tasks_media_flow
       │  chờ worker heartbeat để phân phối
       ▲
       │  heartbeat (nhận task) + upload kết quả
       │
  [selenium_flow.py]       ← file này (port 13445)
       │  quản lý profile Chrome (CRUD)
       │  khi worker active:
       │    - mở Chrome với --user-data-dir (session Google đã login)
       │    - capture OAuth token từ Chrome network logs
       │    - gọi aisandbox-pa.googleapis.com trực tiếp
       ▲
       │  lệnh quản lý (start/stop/logs...)
       │
  [main.py]        ← Desktop client PyQt6 (không làm việc thật)

════════════════════════════════════════════════════════════════
VAI TRÒ CỦA FILE NÀY
════════════════════════════════════════════════════════════════

  ✔ Flask API server port 13445 — nhận lệnh từ main.py
  ✔ Quản lý DB: selenium_profiles, selenium_extensions, profile_logs
  ✔ Mở Chrome với profile riêng (user-data-dir = session persistent)
  ✔ Capture OAuth token + reCAPTCHA từ Chrome performance logs
  ✔ Heartbeat → server_gemini_flow.py (port 13443) để nhận task
  ✔ Gọi aisandbox-pa.googleapis.com trực tiếp (API mode, không DOM)
  ✔ Upload kết quả → server_gemini_flow.py

  ✗ KHÔNG phải server task chính — server_gemini_flow.py mới là chủ
  ✗ KHÔNG nhận task trực tiếp từ user — chỉ poll từ server_gemini_flow

════════════════════════════════════════════════════════════════
LUỒNG KHI WORKER CHẠY
════════════════════════════════════════════════════════════════

  main.py     POST /profiles/1/start
      ↓
  _start_worker(profile)  →  SeleniumFlowWorker.run() [thread]
      ↓
  _make_driver()          →  attach login Chrome hoặc mở Chrome mới
      ↓
  _ensure_flow_page()     →  navigate labs.google/flow
  _wait_for_tokens()      →  đọc perf logs → capture Authorization header
      ↓
  loop mỗi 8s:
    _heartbeat()          →  POST server_gemini_flow:13443/api/media/heartbeat
                          ←  trả task (hoặc null)
    _run_task(task)
      ├─ image: POST aisandbox-pa.googleapis.com/v1:batchGenerateImages
      │         POST server_gemini_flow:13443/api/media/task/result
      └─ video: POST aisandbox-pa.googleapis.com/v1:batchAsyncGenerateVideo
                poll until done
                POST server_gemini_flow:13443/api/media/task/download

Port: 13445
"""


# ═════════════════════════════════════════════════════════════════════════════
# Entry point (2026-07-16) — nội dung thật đã tách vào package server/ (xem
# CLAUDE.md §11.13 để biết ranh giới từng module). File này giờ chỉ:
#   (1) chạy standalone `python selenium_flow.py` cho debug/test độc lập;
#   (2) re-export các tên mà gui/main_window.py và tests/*.py cần qua `sf.xxx`
#       (import selenium_flow as sf) — GIỮ NGUYÊN contract cũ để không phải
#       sửa lại các chỗ gọi `sf.PORT`, `sf.pm`, `sf.SeleniumFlowWorker`, ...
# ═════════════════════════════════════════════════════════════════════════════

from server.config import PORT, FLOW_SERVER, POLL_INTERVAL, req_lib
from server.managers import pm, em
from server.worker import SeleniumFlowWorker
from server.state import _workers, _login_drivers
from server.app_server import run_server, shutdown_all_workers


if __name__ == '__main__':
    run_server()
