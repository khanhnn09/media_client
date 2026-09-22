# client_tool — Tài liệu kỹ thuật & Luồng chạy từng bước

> **Dành cho AI:** Đọc file này TRƯỚC KHI can thiệp bất kỳ file nào trong thư mục `client_tool/`.  
> File này mô tả toàn bộ kiến trúc, hai worker mode, và luồng chạy chi tiết cho từng case.

> **⚠️ 2026-07-21 — Đây là GIT REPO RIÊNG**, remote `git@45.117.76.38:khanh.nn/machinemedia.git`, KHÔNG còn nằm trong repo `ToolSub` chính (đã gitignore + xoá tracking ở đó). Commit/push khi sửa code ở đây phải làm TỪ BÊN TRONG thư mục `client_tool/` này, không phải từ repo gốc. `start.bat` tự `git pull --ff-only` mỗi lần mở app — mỗi máy worker tự nhận bản mới nhất, không cần cập nhật tay.

> **App version (2026-07-21):** file `VERSION` (gốc `client_tool/`, dạng `MAJOR.MINOR.PATCH`) đọc bởi `server/config.py::APP_VERSION`, hiển thị trong `gui/sidebar.py` (dòng dưới logo: "Selenium Worker Console · v{version}"). **Tự tăng PATCH mỗi lần `git push` thật sự gửi commit mới** qua `.git/hooks/pre-push` (LOCAL, KHÔNG version-controlled — chỉ tồn tại trên máy đang push, không cần propagate qua các máy worker vì chúng chỉ `git pull`, không push). Cơ chế: hook đọc `VERSION`, tăng patch, `git add`+`git commit` (commit MỚI, KHÔNG amend — giữ đúng nguyên tắc "luôn tạo commit mới" của project), rồi **chủ động huỷ lần push hiện tại** (`exit 1`) để bắt chạy `git push` lại — lần sau commit bump này đi kèm bình thường. Bỏ qua (không bump) nếu: không có gì mới để push (mọi ref đã khớp remote), hoặc tip hiện tại đã là 1 commit bump từ lần push trước đó (tránh bump chồng khi retry). **Cài lại hook nếu clone máy mới:** copy nguyên `.git/hooks/pre-push` (nội dung xem trong CHANGELOG hoặc hỏi lại) — vì không nằm trong tracked files.

---

## 1. Vai trò trong hệ thống

`client_tool/` là **worker client di động** — có thể đem đi bất kỳ máy nào, chỉ cần trỏ
`FLOW_API_URL` (`.env`) về đúng server chính đang chạy — kết nối vào backend chính
(`server_gemini_flow.py`, port 13443 mặc định) để nhận và xử lý task tạo ảnh/video trên
Google Flow (labs.google). Thay thế hoàn toàn `profile_client/` cũ (giữ lại như bản
legacy, không phát triển tiếp).

```
audio-cue-editor.html                   → tạo task
    ↓
server_gemini_flow.py  (port 13443)     → SERVER CHÍNH, lưu DB, phân phối task
    ↑
selenium_flow.py       (port 13445)     → WORKER: nhận task, chạy Chrome, upload kết quả
    ↑
main.py                         → Desktop client PyQt6 (chỉ gọi API, không làm việc)
```

**`selenium_flow.py` KHÔNG tự nhận task từ user.** Nó poll heartbeat → server_gemini_flow trả task → worker xử lý → upload kết quả.

**⚠️ (2026-07-16) `selenium_flow.py` giờ chạy EMBEDDED trong `main.py`, không còn là process riêng.** Sơ đồ trên vẫn đúng về LUỒNG DỮ LIỆU (server_gemini_flow ↔ worker ↔ GUI qua HTTP `localhost:13445`), nhưng KHÔNG còn đúng về process boundary: `main.py` giờ là entry point DUY NHẤT — nó `import selenium_flow as sf` làm module rồi tự chạy `sf.run_server()` trong 1 thread nền của CHÍNH process GUI, thay vì spawn `selenium_flow.py` làm subprocess như trước. Xem chi tiết §11.11.

**Setup trên máy mới / đóng gói standalone (2026-07-17):** `client_tool/` giờ TỰ CHỨA HOÀN TOÀN (self-contained) — copy nguyên thư mục này sang máy khác (không cần phần còn lại của repo ToolSub) rồi bấm `start.bat` là đủ:
- `start.bat` tự tìm Python (`py`/`python` trên PATH, báo lỗi + link tải nếu chưa có Python 3.10+) → tự tạo venv LOCAL tại `client_tool\venv\` nếu chưa có → tự `pip install -r requirements.txt --no-cache-dir` (idempotent, chạy lại không sao) → tự copy `.env.example` → `.env` nếu chưa có → chạy `main.py`. **Máy đích vẫn phải tự cài sẵn Python 3.10+ và Google Chrome** — 2 việc không tự động hoá an toàn qua `.bat` được.
- **`--no-cache-dir` (2026-07-18, bug thật user gặp khi tự chạy lần đầu):** pip dùng 1 wheel cache DÙNG CHUNG cố định tại `%LOCALAPPDATA%\pip\cache` (luôn ở ổ C theo quy ước Windows — KHÔNG liên quan gì tới venv đặt ở ổ nào, hành vi bình thường của pip, không phải bug) — user gặp `OSError: [Errno 13] Permission denied` trên 1 file wheel cụ thể trong cache đó (verify: `icacls` cùng user cũng bị "Access is denied" trên đúng file — dấu hiệu bị tiến trình khác giữ handle, phổ biến nhất là antivirus quét real-time). `--no-cache-dir` bỏ qua hẳn cache dùng chung, tải thẳng từ PyPI mỗi lần — chậm hơn chút nhưng không bao giờ bị chặn bởi cache hỏng/bị khoá trên máy lạ (đúng tinh thần "1 click luôn chạy được").
- **`setuptools` bắt buộc trong requirements.txt (2026-07-18, phát hiện ngay sau khi fix `--no-cache-dir` ở trên):** cài xong hết package nhưng `import undetected_chromedriver` crash `ModuleNotFoundError: No module named 'distutils'`. Nguyên nhân: máy user có Python **3.13** trên PATH (`start.bat` tự chọn `py -3`/`python` tìm được đầu tiên, không ép version) — `distutils` bị XOÁ HẲN khỏi thư viện chuẩn từ Python 3.12 (PEP 632), và `venv` cũng KHÔNG còn tự cài kèm `setuptools` từ 3.12 (trước đây setuptools cung cấp shim bù lại cho code cũ như `undetected-chromedriver` vẫn `from distutils.version import LooseVersion`). Verify: venv MỚI (Python 3.13) không có `setuptools`; venv GỐC của repo (Python 3.10, có sẵn từ trước) CÓ `setuptools 70.2.0` — đúng lý do venv cũ chưa từng gặp bug này (Python 3.10 vẫn có `distutils` trong thư viện chuẩn). Thêm `setuptools>=69.0.0` vào `requirements.txt` — cài xong tự động shim `distutils` qua `.pth` file, không cần sửa code nào. Verify lại: import thành công trên ĐÚNG venv Python 3.13 vừa lỗi; `python -m compileall .` (Python 3.13) toàn bộ client_tool không lỗi — lần đầu tiên venv RIÊNG của client_tool (không phải venv gốc repo) được verify end-to-end hoàn chỉnh.
- `client_tool/.env` (tự copy từ `.env.example` lần chạy đầu, KHÔNG commit git) — CHỈ CẦN sửa `FLOW_API_URL` (URL backend chính, mặc định `http://localhost:13443`) trước khi dùng thật. **KHÔNG còn `DB_*`** — xem mục ngay dưới.
- `client_tool/requirements.txt` — đã audit lại đầy đủ (trước đây thiếu `PyQt6`/`Flask`/`flask-cors`/`python-dotenv`/`undetected-chromedriver`/`selenium-stealth`, chỉ có 6 package test-only). KHÔNG có `PyMySQL` (xem mục ngay dưới).

**Bỏ hẳn kết nối DB trực tiếp (2026-07-17, làm ngay sau lần đóng gói ở trên — xem CHANGELOG "client_tool: bỏ kết nối DB trực tiếp"):** User yêu cầu tường minh — máy chạy client_tool KHÔNG được kết nối MariaDB trực tiếp nữa (rủi ro bảo mật/vận hành khi chạy trên nhiều máy worker, mỗi máy cần biết credentials DB thật). Chuyển 100% CRUD của `selenium_profiles`/`selenium_extensions`/`profile_logs` (xem §11.4) sang gọi HTTP tới backend (`/api/worker_profiles`/`/api/worker_extensions`, `backend/routes/worker_profiles.py`, migration `_ensure_worker_profile_tables()` trong `backend/core/migrations.py`). `client_tool/server/managers.py` (`pm`/`em`) đổi TOÀN BỘ implementation từ SQL trực tiếp sang HTTP `_api()`, nhưng GIỮ NGUYÊN 100% interface (`pm.list()/get()/create()/update()/delete()/set_status()`, `em.list()/add()/set_enabled()/remove()/active_paths()`) nên 47+ call site khác (dispatcher.py/routes.py/worker.py/chrome_utils.py) KHÔNG cần sửa gì. `config.py::_profile_log()` đổi từ INSERT trực tiếp sang `POST /api/worker_profiles/<id>/logs`. `routes.py`'s `get_profile_logs`/`clear_profile_logs`/`health()` (db_ok) đổi thành proxy HTTP tới backend. File vendor `client_tool/db.py` (thêm lúc đóng gói standalone) bị XOÁ LUÔN vì không còn ai import — không còn khái niệm "vendor db.py" nữa, client_tool không có bất kỳ code nào biết về MySQL/PyMySQL. `client_tool/schema.sql` giữ lại CHỈ ĐỂ THAM KHẢO (đổi header comment) — 3 bảng giờ do backend tự tạo lúc khởi động, không cần chạy `mysql < schema.sql` thủ công nữa.

**Bug tìm thấy khi viết test end-to-end cho endpoint mới (2026-07-17, KHÔNG phải do refactor — bug đã có sẵn trong `_profile_log()` gốc):** logic "giữ tối đa N dòng log/profile" cũ dùng `DELETE ... WHERE id <= MIN(id của top-N dòng mới nhất)` — khi tổng số dòng CHƯA vượt N (vd profile mới, log đầu tiên), "top-N mới nhất" = toàn bộ bảng, nên `MIN(id)` = dòng CŨ NHẤT hiện có = CHÍNH dòng vừa insert nếu đó là dòng đầu tiên → xoá NGAY dòng vừa ghi. Verify trực tiếp: insert 1 dòng cho 1 profile test mới tinh → chạy lại đúng SQL cũ → `rowcount=1`, bảng về 0 dòng ngay lập tức. Bug này tồn tại từ trước, chỉ chưa ai thấy vì profile đang dùng thật luôn có sẵn >500 dòng che mất trường hợp biên (dưới ngưỡng). Fix trong `add_worker_profile_log()` (`backend/routes/worker_profiles.py`): đổi sang `LIMIT 1 OFFSET (_LOG_KEEP-1)` để lấy đúng id của dòng thứ N tính từ mới nhất — nếu tổng số dòng chưa đủ N, subquery trả rỗng → `COALESCE(...,0)` → `id < 0` không bao giờ đúng → không xoá gì. Verify lại bằng test thật: insert 3 dòng (dưới ngưỡng) → cả 3 còn nguyên; insert 6 dòng với `_LOG_KEEP=3` → đúng 3 dòng MỚI NHẤT còn lại.

**Mỗi client_tool có UUID riêng — scope profile/extension theo client_id (2026-07-18):** Sau khi bỏ kết nối DB trực tiếp (mục ngay trên), MỌI client_tool (chạy trên nhiều máy khác nhau) đều gọi CHUNG 1 backend → `selenium_profiles`/`selenium_extensions` trở thành 1 pool DÙNG CHUNG — nhưng `profile_dir`/`ext_path` là đường dẫn filesystem CỤC BỘ (Chrome user-data-dir), máy B không thể dùng path do máy A tạo. User yêu cầu: mỗi client_tool phải có UUID riêng, mỗi client_tool chỉ thấy profile của mình. `client_tool/server/client_identity.py` (MỚI) — sinh UUID cố định lúc chạy lần đầu, lưu `client_tool/client_identity.json` (gitignore), export `client_headers()` (`{X-Client-Id, X-Client-Name}`) đính kèm MỌI request lên backend (`managers.py::_api()`, `config.py::_profile_log()`, `routes.py`'s proxy log routes). `client_name` là tên gợi nhớ TUỲ CHỌN — sửa được ở card "🪪 Danh tính client_tool này" (đầu trang Cài đặt, `gui/pages/settings_page.py`), qua `GET/PATCH /api/selenium/client_identity` (local route mới trong `routes.py`).

Backend (`backend/routes/worker_profiles.py`) đọc header để scope: row `client_id IS NULL` (dữ liệu cũ trước khi có scope) = "chưa nhận chủ" — hiển thị cho MỌI client (LIST/GET), TỰ ĐỘNG được "nhận chủ" (claim — ghi client_id) ngay lần đầu bị UPDATE/DELETE/status bởi 1 client cụ thể, KHÔNG cần thao tác thủ công. Row đã có chủ khác → LIST/GET không thấy, UPDATE/DELETE/status trả 404. Migration `_ensure_worker_profile_client_scope()` (`backend/core/migrations.py`) thêm cột `client_id`/`client_name` + đổi UNIQUE constraint từ global (`profile_name`, `ext_path`) sang scoped `(client_id, profile_name)`/`(client_id, ext_path)` (tự phát hiện+xoá index cũ qua `information_schema.STATISTICS`). Verify bằng 7 kịch bản thật qua Flask test client (cross-client isolation, claim-on-touch, no-header fallback) + full round-trip qua code THẬT của client_tool — tất cả pass, xem CHANGELOG.

**Màn hình đăng nhập — dùng chung tài khoản backend chính, tự "nhớ" cho lần mở sau (2026-07-18):** `client_tool/server/auth_client.py` (MỚI) — `login(username,password)` gọi thẳng `POST /api/auth/login` trên backend chính (bảng `users` thật đã có sẵn cho React admin — xem §12 root CLAUDE.md, KHÔNG tạo hệ thống tài khoản riêng cho client_tool). Session cookie Flask (ký `SECRET_KEY`, `session.permanent=True` → mặc định 31 ngày) được LƯU THỦ CÔNG vào `client_tool/auth_session.json` (gitignore, coi như secret) vì client_tool không phải browser nên không có cookie jar tự động. `main.py::main()` — TRƯỚC KHI dựng `MainWindow`, gọi `auth_client.try_resume_session()` (đọc cookie đã lưu + xác nhận qua `GET /api/auth/me`); hợp lệ thì vào thẳng app luôn, không hợp lệ/chưa từng đăng nhập thì hiện `gui/login_dialog.py::LoginDialog` (form đồng bộ, chấp nhận được vì là hành động chủ động của user). Đóng dialog không đăng nhập (Cancel/X) → thoát hẳn app. Đăng nhập thành công → `MainWindow(user=...)` nhận user dict, hiển thị `displayName`/`username` lên `Sidebar` (card "👤" + nút đăng xuất "⎋" mới, signal `logout_requested`). Đăng xuất = `auth_client.logout()` (xoá session cả cục bộ lẫn phía backend) + đóng hẳn app (tái dùng `closeEvent` có sẵn để dừng sạch worker/Chrome) — KHÔNG hỗ trợ đổi user mà không khởi động lại process (tránh phức tạp hoá vòng đời server embedded port 13445). Verify: test end-to-end thật qua `auth_client` (user test tạm trong DB, backend throwaway) — 6 kịch bản đều pass, quan trọng nhất là **giả lập khởi động lại app** (reset state module rồi gọi lại `try_resume_session()`) tự khôi phục đúng user KHÔNG cần nhập lại — đúng yêu cầu cốt lõi "ghi nhớ cho lần sau mở lại nếu đã đăng nhập". `LoginDialog`/`Sidebar.set_user()` dựng/hiển thị đúng. CHƯA verify launch `main()` thật (khởi động server embedded thật, không an toàn tự chạy trong môi trường dev) — cần user tự mở app xác nhận UI + hành vi nhớ đăng nhập.

**Group 'Tool Media' bắt buộc + profile scope theo NGƯỜI, không chỉ theo máy (2026-07-18, theo yêu cầu user: "user group tạo thêm: Tool Media. client_tool thì chỉ các user thuộc group này mới được đăng nhập. các profile list/insert/update thì phải đi theo user này không được thấy profile của user khác"):**
- **Login gate group** — `auth_client.REQUIRED_GROUP = 'Tool Media'`. `login()` kiểm tra `user['groupName']` sau khi username/password đúng — sai group thì HUỶ session vừa tạo ngay (gọi logout backend, không để sống sót) rồi từ chối kèm message rõ ràng. `try_resume_session()` check LẶP LẠI (không chỉ lúc login) — bắt được trường hợp user bị gỡ khỏi group SAU khi đã có session lưu cục bộ (cookie vẫn còn hạn theo thời gian nhưng vẫn bị từ chối, tự xoá session). **KHÔNG cho admin bypass** — đúng nghĩa đen yêu cầu "chỉ user thuộc group này"; admin muốn dùng client_tool tự thêm mình vào group qua trang Users (có toàn quyền làm việc đó).
- **Backend enforcement (phòng tuyến thứ 2)** — `backend/routes/worker_profiles.py::_require_tool_media` (decorator) check LẶP LẠI y hệt trên MỌI route profile (list/get/create/update/delete/status/task_stats) — phòng trường hợp ai đó gọi thẳng API bằng session cookie hợp lệ của tài khoản KHÔNG thuộc group, bỏ qua hẳn màn hình đăng nhập client_tool.
- **Scope 2 TRỤC ĐỘC LẬP** — `_scope_clause(cid, uid)` thay cho `_visible_where(cid)`/`_own_or_claim(cid)` cũ (2 helper đó VẪN CÒN, giờ chỉ dùng cho extension — KHÔNG đổi phạm vi theo yêu cầu, chỉ "profile list/insert/update"). Profile PHẢI thoả CẢ client_id (máy — `profile_dir` chỉ dùng được đúng máy tạo ra, lý do có sẵn từ trước) VÀ owner_user_id (người — mới). Row "chưa nhận chủ" theo TỪNG trục riêng (client_id NULL hoặc owner_user_id NULL) vẫn hiển thị/sửa được, tự nhận chủ ở UPDATE/status/task_stats đầu tiên — y hệt cơ chế client_id đã dùng, thêm 1 trục cho owner_user_id.
- **Migration**: `_ensure_tool_media_group()` seed group (idempotent). `_ensure_worker_profile_owner()` thêm cột `owner_user_id` + FK `users.id ON DELETE SET NULL`.
- **Verify end-to-end thật** (2 user Tool Media + 1 user group khác, backend throwaway, chạy qua code THẬT `auth_client`/`pm`): user sai group bị từ chối đăng nhập; user Tool Media A tạo profile → đúng owner; user Tool Media B KHÔNG thấy profile của A, PATCH bị 404; gọi hoàn toàn không đăng nhập → 401; giả lập restart app → resume OK (còn đúng group); **giả lập bị gỡ khỏi group giữa chừng** (đổi `group_id` thẳng trong DB, cookie cũ vẫn còn hạn) → resume lần sau ĐÚNG bị từ chối. Cả 7 kịch bản pass.

---

## 2. Hai worker mode

Mỗi Chrome profile có thể chạy ở một trong hai mode, set trong cột `worker_mode` của bảng `selenium_profiles`:

| Mode | Giá trị | Cách hoạt động |
|------|---------|----------------|
| **API mode** | `api` | Image: Selenium type prompt + Enter vào Slate CE, JS fetch interceptor bắt response. Video: gọi Python API trực tiếp với token capture từ Chrome perf logs |
| **DOM mode** | `dom` | Selenium điều khiển DOM đầy đủ: config tab/model/ratio, upload ảnh qua picker, type prompt, poll tile cho đến khi done; sau đó resolve CDN URL qua fetch với session cookie |

**Quan trọng — Lựa chọn mode:**
- **API mode + image**: ✅ Đã test, hoạt động ổn (~30s/ảnh). Ưu tiên direct API qua `_call_image_api_v2()` (fallback `_generate_image_via_ui()` nếu 403/thiếu token).
- **API mode + video** (2026-08-12): `_call_video_api()` POST đúng 1 trong 3 endpoint thật theo `mode`. Submit xong **gửi prompt tiếp ngay** — API không hiện tile trên UI nên KHÔNG chờ tile. Kết quả lấy sau qua `projectInitialData` (`_wait_and_reconcile_tasks(wait_tiles=False)`), khớp prefix `TASK_{id}:` trong prompt.
- **DOM mode**: Hoạt động cho tất cả case nhưng phức tạp hơn, phụ thuộc DOM structure của labs.google.

---

## 3. Khởi động worker

```
main.py
  → POST /api/selenium/profiles/{id}/start
      ↓
_start_worker(profile)
  → tạo SeleniumFlowWorker(profile)
  → Thread target=w.run()
```

### 3.1 SeleniumFlowWorker.run() — vòng lặp chính

```
run()
  │
  ├─ _make_driver()              # Mở Chrome (attach hoặc mới, có/không portable)
  │
  ├─ _ensure_flow_page()         # Navigate → project URL hoặc labs.google/flow
  ├─ _trigger_page_requests()    # Scroll nhẹ → kích network request → _drain_perf_logs()
  │
  ├─ [API mode only] _wait_for_tokens(60s)
  │    └─ _drain_perf_logs() mỗi 3s → bắt Authorization header từ aisandbox calls
  │
  └─ loop (mỗi POLL_INTERVAL=8s):
       ├─ _is_driver_alive()       # Chrome còn sống không?
       ├─ _drain_browser_logs()    # Console errors → profile log
       ├─ [API] _drain_perf_logs() # Refresh token
       ├─ [API] nếu token > 10 phút: _get_fresh_recaptcha()
       ├─ _heartbeat()             # POST /api/media/heartbeat → nhận task
       └─ nếu có task: _process_tasks(tasks)
            └─ keepalive thread (2026-07-17, xem bên dưới) gọi lại _heartbeat()
               mỗi POLL_INTERVAL trong SUỐT lúc _run_task_dom/_run_tasks_batch
               chạy (có thể block tới 10+ phút với task video)
```

**Keepalive trong lúc xử lý task (2026-07-17) — chống server reaper thu hồi nhầm task đang chạy thật:** Trước đây `run()` chỉ gọi `_heartbeat()` MỘT LẦN trước khi vào `_process_tasks()`, sau đó bị block hoàn toàn cho tới khi task xong hẳn (DOM upload + `_wait_and_reconcile_tasks` riêng đã có thể block tới 10 phút với `imageToVideo`). `backend/routes/heartbeat.py` mark machine `offline` nếu `last_seen` quá 30s — check này chạy MỖI KHI CÓ MÁY KHÁC BẤT KỲ gọi heartbeat, không chỉ chính máy đó — nên machine đang xử lý task lâu bị mark offline giữa chừng dù vẫn chạy thật, và reaper server (`_reap_stuck_tasks`, xem root CLAUDE.md §4.2) thu hồi ngay task `processing` của machine `offline` bất kể task có thật sự treo hay không. Xác nhận bằng bằng chứng thật: query DB `tasks_media_flow` cho 1 task `imageToVideo` thất bại cho thấy `status='error'`, `error_message='Reclaimed by reaper (machine offline / task timeout)'`, trong khi log CHÍNH profile đó tại đúng thời điểm cho thấy nó vẫn xử lý bình thường (không lỗi/crash). Đây CÙNG LỚP BUG đã fix cho worker Gemini Selenium (xem §11.5 "machine bị đánh dấu offline giữa chừng khi task chạy lâu"). Fix: `_process_tasks()` giờ bọc TOÀN BỘ thân hàm (cả nhánh batch `_run_tasks_batch` lẫn vòng lặp tuần tự `_run_task`) bằng 1 keepalive thread gọi `self._heartbeat()` mỗi `POLL_INTERVAL` (8s), dừng trong `finally` khi hàm return.

**Fix race "keepalive cướp task rồi vứt bỏ" (2026-07-18):** Bản đầu ở trên gọi lại NGUYÊN `self._heartbeat()` với lý do "server tính slot trống từ in-flight THẬT trong DB (`tasks_media_flow WHERE assigned_machine=... AND status IN ('assigned','processing')`), không dựa `runningCount` client tự báo nên an toàn" — ĐÚNG LOGIC nhưng bỏ sót 1 RACE VỀ THỜI ĐIỂM, bắt được qua log production thật (batch 2 task #3618/#3620, profile-4): khi CẢ 2 task trong batch reconcile xong gần như ĐỒNG THỜI (chung 1 lần `POST reconcile/check` trả `matched=[...]` cho cả 2), in-flight của machine tạm về 0 ĐÚNG NGAY LÚC keepalive thread gọi heartbeat kế tiếp — SỚM HƠN thời điểm `_process_tasks()` kịp return để vòng lặp chính `run()` tự heartbeat lại — server thấy "còn slot trống thật" nên giao NGAY 2 task mới trong response keepalive, nhưng `_process_tasks()` đang bận (task khác), 2 task đó bị log `"Keepalive heartbeat nhận nhầm task... bỏ qua"` rồi VỨT THẲNG — kẹt `assigned` cho tới khi reaper server thu hồi lại (chờ hết timeout theo mode, không phải ngay lập tức) → CHẬM TIẾN ĐỘ batch rõ rệt. Fix: `_heartbeat(keepalive_only=True)` gửi thêm `keepaliveOnly: true` — backend (`heartbeat()`, `backend/routes/heartbeat.py`) đọc cờ này, sau khi upsert machine (last_seen/status/max_concurrent) thì trả về NGAY, bỏ qua HẲN code path giao task (không chỉ dựa vào in-flight tính đúng lúc gọi — loại bỏ luôn khe hở timing). Verify: test thật (Flask test client + DB thật) xác nhận `keepaliveOnly=true` luôn nhận `tasks:[]` dù có slot trống thật (task vẫn `status='pending'`, không bị "nuốt"), heartbeat THƯỜNG ngay sau đó vẫn nhận đúng task như cũ.

**`reconcile_wait_secs`/`reconcile_max_rounds` chuyển vào Cài đặt (2026-07-18):** trước đây hardcode `wait_secs=60, max_rounds=10` ngay trong lời gọi `_wait_and_reconcile_tasks()` (cả `_run_task_dom` lẫn `_run_tasks_batch`) — giờ đọc từ `self._server_settings` (local_settings.py, mặc định giữ nguyên 60/10), sửa được trực tiếp ở GUI trang Cài đặt (`gui/pages/settings_page.py`) theo tải máy/tốc độ mạng thực tế thay vì cố định trong code.

**Thay `sleep(wait_secs)` cố định bằng chờ DOM báo hết render (2026-07-18, ngay sau mục trên):** User cung cấp HTML thật của 1 tile Flow đang render — có 1 div con hiển thị phần trăm (vd `<div class="sc-40f16b33-7 cTByVE">23%</div>`) cạnh icon loại media (`image`/`video`); tile lỗi hiện "Không thành công" (không có %); tile xong thay % bằng media thật (cũng không có %). Yêu cầu: sau submit batch → chờ 20s → check còn tile nào đang render (%) không → còn thì tiếp tục chờ tới khi xong → refresh → chạy batch tiếp/đóng profile nếu hết — **bỏ hẳn khoảng chờ cố định trước khi refresh**. `_wait_until_render_done()` (mới, `worker.py`) thay thế `self._sleep(wait_secs)` trong `_wait_and_reconcile_tasks()`: chờ `download_wait_secs` (field CÓ SẴN trong `_DEFAULT_SERVER_SETTINGS`/Cài đặt từ trước nhưng CHƯA TỪNG được code nào dùng tới — giờ tái sử dụng đúng mục đích, mặc định 20s) cho tile kịp bắt đầu render, rồi poll DOM mỗi 5s (hardcode, không expose thêm setting) xem còn `%` không — hết là refresh NGAY, không cần chờ đủ khoảng cố định nếu batch xong sớm. Detect qua REGEX KHỚP TEXT (`/^\d{1,3}%$/`) áp lên `textContent` của mọi `div` trong từng `[data-tile-id]`, **KHÔNG dùng class name styled-components đã hash** (`sc-40f16b33-7` — không ổn định giữa các lần build/session của Google, đã lặp lại nhiều lần trong project này là nguyên nhân DOM-detection gãy, xem lịch sử "picker không mở"/"selectAll bôi xanh cả trang" ở các mục trước). `reconcile_wait_secs` (mục trên) đổi vai trò thành TRẦN TỐI ĐA — DOM báo "đang render" quá lâu (kẹt thật hoặc markup đổi khác khiến regex không còn khớp) vẫn refresh sau khi chạm trần, tránh treo vô hạn. Verify: JS snippet hợp lệ qua Node `new Function()`, regex test trực tiếp với chuỗi mẫu thật (`"23%"`✓, `"100%"`✓, `"TASK_3632:SCENE_593..."`✗, `"Không thành công"`✗, `"image"`✗, rỗng✗) — đúng thiết kế. **CHƯA verify trên browser thật** (môi trường này không chạy được Chrome/client_tool) — cần user chạy 1 batch thật để xác nhận.

**Profile lưu thêm số task hoàn thành/lỗi TRONG NGÀY (2026-07-18):** `_task_done_count`/`_task_error_count` (worker.py) trước giờ CHỈ sống trong RAM của process client_tool — mất khi worker restart (Chrome đóng/mở lại, máy reboot) — không phản ánh đúng "hôm nay đã làm được bao nhiêu". Thêm 3 cột trên `selenium_profiles`: `tasks_done_today`/`tasks_error_today` (INT, mặc định 0) + `stats_date` (DATE — ngày 2 cột trên đang tính cho) qua migration `_ensure_worker_profile_daily_stats()`. Endpoint mới `PATCH /api/worker_profiles/<id>/task_stats` (`backend/routes/worker_profiles.py`, body `{result:'done'|'error'}`) — reset về 0 NGAY TRONG CÂU UPDATE nếu `stats_date != CURDATE()` rồi mới cộng (không cần cron nửa đêm, chỉ "phát hiện ngày mới" khi có task thật báo kết quả), cùng pattern claim-or-verify + scope `client_id` như `/status`. `managers.py::pm.bump_task_stat(profile_id, result)` (best-effort, giống `set_status`) gọi từ `_handle_task_success()`/`_record_error()` — CÙNG CHỖ vốn đã tăng 2 counter RAM ở trên, giờ thêm bản bền vững song song (không thay thế — 2 counter RAM vẫn còn, ý nghĩa khác: "phiên chạy hiện tại" vs "hôm nay tính cả các phiên"). GUI (`gui/pages/profiles_page.py`) thêm cột "Hôm nay" (giữa "Lỗi" và "Port") hiện 2 badge `✅ N`/`❌ M` — đọc trực tiếp field mới trong response `GET /api/selenium/status` (không cần đổi gì phía đọc, `pm.list()` đã forward nguyên row từ backend). Verify: test thật (Flask test client + DB) xác nhận cộng dồn đúng trong ngày, tự reset khi giả lập rollover sang ngày mới, chặn cross-client, từ chối `result` không hợp lệ; round-trip qua code THẬT của client_tool (`pm.bump_task_stat`); `ProfilesPage` dựng + `_populate()` với row giả chạy không lỗi, đúng số cột (10). **CHƯA verify hiển thị thật trên GUI** — cần user tự mở trang Profiles sau khi chạy vài task thật.

### 3.2 _make_driver() — ưu tiên attach

```
_make_driver()
  │
  ├─ Nếu profile đang có login browser (_login_drivers[pid]):
  │    └─ _connect_to_chrome(debug_port)  [debuggerAddress]
  │         → attach vào Chrome đã đăng nhập (không mở instance mới)
  │
  └─ Không có → mở Chrome mới:
       ├─ Dùng Chrome Portable nếu có (portables/{idx}/App/Chrome-bin/chrome.exe)
       ├─ _build_chrome_options(profile_dir, debug_port, load_extensions=True)
       │    - --user-data-dir = profile['profile_dir'] (session Google persistent)
       │    - --remote-debugging-port = 9300 + (profile_id % 200)
       │    - --disable-gpu, --disable-renderer-backgrounding, ...
       │    - goog:loggingPrefs = {performance: ALL, browser: ALL}
       │    - load extensions từ selenium_extensions table
       └─ webdriver.Chrome() + Page.addScriptToEvaluateOnNewDocument (hide webdriver)
```

---

## 4. Luồng chi tiết — API mode

### 4.1 Token capture

```
run() API mode (sau _ensure_flow_page + warm-up scroll):
  _sync_project_apis()  — khớp tests/utils/flow_session.py, CHỈ BỎ proxy + token disk cache
    1. GET /fx/api/auth/session (credentials:include) → authorization
    2. chờ v1:checkAppAvailability (perf ExtraInfo) → x-browser-validation + x-client-data
    3. chờ flow:batchLogFrontendEvents → sessionId `;{ms}` (events[].metadata.sessionId)
    Reload 1 lần nếu quá 60% timeout vẫn thiếu fingerprint/sessionId.
    window.__tbCredentials (nếu Flow extension inject) là nguồn phụ.

_drain_perf_logs()
  │  Đọc driver.get_log('performance') — cần goog:loggingPrefs lúc attach
  │  VÀ Network.enable CDP (cả Chrome mới lẫn attach login browser)
  │  Tìm method='Network.requestWillBeSent' với url chứa 'aisandbox-pa.googleapis.com'
  │  Gộp thêm Network.requestWillBeSentExtraInfo (cùng requestId) — Chrome
  │  nhét x-browser-validation / x-client-data vào ExtraInfo, không có
  │  trong requestWillBeSent. Header đọc KHÔNG phân biệt hoa/thường.
  │
  └─ Trích từ headers đã gộp:
       - authorization        → Bearer ya29.xxx  (OAuth2)
       - x-browser-validation / x-client-data   (fingerprint — BẮT BUỘC cho curl_cffi)
       - x-browser-channel / x-browser-year / x-browser-copyright
       - x-goog-api-key
       - sec-ch-ua / sec-fetch-* / user-agent (từ checkApp; thiếu thì đọc navigator.userAgentData)
  └─ Trích từ request.postData (JSON):
       - batchLog events[].metadata.sessionId  (ưu tiên) rồi clientContext.sessionId
       - clientContext.recaptchaContext.token → recaptchaToken
       - toàn bộ body → lastRequestBody (chỉ còn lưu để chẩn đoán/log — 2026-08-12
         `_call_image_api_v2()`/`_call_video_api()` tự dựng body qua `flow_api.py`,
         không còn dùng làm template như `_build_body()` cũ, đã xoá)

Nguồn authorization CHÍNH (2026-08-12, khớp `_test_textToImage.py`):
  `_fetch_labs_session()` — `fetch('/fx/api/auth/session', {credentials:'include'})`
  từ tab labs.google, không phụ thuộc Chrome vừa có request aisandbox hay không.

Trước MỌI POST aisandbox: `_ensure_api_ready()` (auth + fingerprint; sessionId
thiếu thì `flow_api._session_id()` fallback `;{ms}`). KHÔNG SOCKS/proxy, KHÔNG
extension-proxy POST, KHÔNG ghi `.browser_tokens.json`.
```

### 4.2 textToImage — API mode (✅ hoạt động)

```
_generate_image_via_ui(task)
  │
  ├─ 1. _ensure_flow_project()
  │       Nếu current URL không chứa '/project/': navigate → profile.project_url
  │       Sleep 7s → _drain_perf_logs()
  │       Nếu URL vẫn không có '/project/' (rơi vào trang chung): bấm "New project"
  │         (_click_new_project_button) → chờ URL project mới (_wait_for_project_url)
  │         → lưu đè project_url (xem 11.5)
  │
  ├─ 2. _install_gen_interceptor()
  │       execute_script: override window.fetch
  │         nếu URL chứa 'batchGenerateImages' hoặc 'batchAsyncGenerate':
  │           push vào window.__genCaptures = [{url, method, ts, response_status, response_body}]
  │         gọi original fetch → clone().text() → lưu response_body
  │       Ghi window.__genCaptureStart = current length (mark điểm bắt đầu)
  │
  ├─ 3. Tìm Slate CE và type prompt
  │       WebDriverWait(15s).until(presence_of_element('[contenteditable="true"]'))
  │       ce.click() → sleep(0.2)
  │       ce.send_keys(prompt) → sleep(0.3)
  │       ce.send_keys(Keys.RETURN)
  │         → Slate onKeyDown bắt Enter → đọc Slate internal state → gọi generate()
  │         → Browser tự gắn recaptchaToken + Authorization + xây request body
  │         → POST https://aisandbox-pa.googleapis.com/v1/projects/{projectId}/flowMedia:batchGenerateImages
  │              body: {imageModelName:"NARWHAL", batchId, structuredPrompt, seed, imageInputs:[]}
  │
  ├─ 4. Poll tối đa 90s (sleep 2s mỗi vòng)
  │       execute_script: lấy genCaptures[start_idx:].filter(c => c.response_status !== null)
  │       Nếu response_status != 200 → raise RuntimeError('API lỗi ...')
  │       Nếu response_status == 200 và có response_body:
  │         parse JSON → lấy media[].image.generatedImage.fifeUrl
  │         fifeUrl = 'https://flow-content.google/image/{UUID}?Expires=...&Signature=...'
  │
  └─ 5. Trả về [{'type':'image', 'url': fifeUrl}, ...]
```

Sau khi có URLs:

```
_run_task_api() (image branch)
  │
  ├─ POST /api/media/task/processing {taskId, machineCode}
  ├─ _generate_image_via_ui(task) → results_urls
  └─ POST /api/media/task/download {taskId, machineCode, cueId, mode, media:[{name, url}]}
       → server_gemini_flow.py download CDN URL, lưu media_output/, update DB status='done'
```

### 4.3 textToVideo/imageToVideo/componentsToVideo/frameToVideo — API mode (2026-08-12, viết lại — xem §11.33/CHANGELOG "Production: tích hợp API THUẦN")

```
_run_tasks_api_batch(tasks)
  │
  ├─ Ảnh (textToImage/imageToImage): _run_task_api() — HTTP trả fifeUrl ngay
  │
  ├─ Video — 2 pha (2026-08-12, theo yêu cầu user "upload hết rồi gán tuần tự"):
  │     1. _prepare_video_uploads(task) cho MỌI task video trong lô
  │        (tải source_media → POST /flow/uploadImage, nhớ media.name theo task_id)
  │     2. _call_video_api(task, captcha, uploaded_media_names=...) TUẦN TỰ
  │        — mỗi POST generate chỉ dùng đúng list đã upload của task đó
  │
_call_video_api(task, captcha, uploaded_media_names=None)
  │
  ├─ _build_headers() → {Authorization: Bearer..., x-browser-validation, ...}
  ├─ branch theo task['mode']:
  │     textToVideo              → flow_api.TEXT_TO_VIDEO
  │                                 (POST /v1/video:batchAsyncGenerateVideoText,
  │                                  videoModelKey = resolve_video_model_key(task['model']):
  │                                    'Veo 3.1 - Lite [Lower Priority]' → veo_3_1_t2v_lite_low_priority
  │                                    'Veo 3.1 - Lite'                  → veo_3_1_t2v_lite
  │                                    'Veo 3.1 - Fast'                  → veo_3_1_t2v_fast
  │                                    'Veo 3.1 - Quality'               → veo_3_1_t2v
  │                                  — DB/heartbeat gửi TÊN GUI, API cần key `veo_3_1_t2v_*`)
  │     imageToVideo/
  │     componentsToVideo        → flow_api.INGREDIENT_TO_VIDEO
  │                                 (POST /v1/video:batchAsyncGenerateVideoReferenceImages,
  │                                  model CỐ ĐỊNH 'veo_3_1_r2v_lite' — Flow UI tự chọn,
  │                                  KHÔNG đọc task['model']. Ref = uploaded_media_names
  │                                  hoặc tự _prepare_video_uploads() nếu gọi lẻ)
  │     frameToVideo              → flow_api.FRAME_TO_VIDEO
  │                                 (POST /v1/video:batchAsyncGenerateVideoStartImage,
  │                                  model CỐ ĐỊNH 'abra_i2v_8s'. CHỈ dùng ẢNH ĐẦU
  │                                  [names[0]] — endImage CHƯA proven)
  │
  ├─ _post_aisandbox(url, body)     (curl_cffi impersonate Chrome, KHÔNG proxy;
  │                                  page fetch() chỉ fallback — CORS chặn
  │                                  TypeError: Failed to fetch nếu set header
  │                                  x-browser-validation/sec-*/ua từ JS)
  │     → response CHỈ xác nhận đã TẠO workflow (async) — KHÔNG có URL video ngay
  │
  └─ KHÔNG chờ tile (API không hiện trên UI). Sau cả lô:
     `_wait_and_reconcile_tasks(wait_tiles=False)` đọc projectInitialData,
     khớp `TASK_{id}:` trong prompt, server tự tải + set done.
```

---

## 5. Luồng chi tiết — DOM mode

DOM mode áp dụng cho tất cả case: textToImage, imageToImage, imageToVideo, componentsToVideo, textToVideo.

### 5.1 Tổng quan _run_task_dom()

```
_run_task_dom(task)
  │
  ├─ POST /api/media/task/processing
  ├─ _ensure_flow_page()         # về đúng project page
  ├─ sleep(3s)
  ├─ _dom_configure(task)        # chọn tab/model/ratio/count
  ├─ [nếu có source_media] _dom_upload_images(source_media, mode)
  ├─ before_ids = _dom_tile_ids()          # snapshot — chỉ dùng cho fallback
  ├─ _dom_fill_and_submit('TASK_{id}:{prompt}')
  ├─ pending = _wait_and_reconcile_tasks({task_id: task}, max_rounds=10, wait_secs=60)  # xem 5.7 — CHÍNH
  └─ [nếu pending còn task_id] media = _collect_task_media_via_tiles(before_ids, task_id, count)  # fallback cũ
       → POST /api/media/task/download {media:[{name, url}]}
     # else: _wait_and_reconcile_tasks() đã tự _handle_task_success() rồi
```

`_run_tasks_batch()` (nhiều task nộp liên tiếp) dùng CÙNG `_wait_and_reconcile_tasks()` cho CẢ BATCH cùng lúc (1 lần chờ 60s + reconcile phục vụ mọi task còn pending trong batch, không phải per-task riêng lẻ) — chỉ task nào KHÔNG được server xác nhận sau 10 vòng mới rơi xuống fallback cursor `new_ids_ordered[cursor:cursor+count]` (đoán tile khớp task theo thứ tự submit, phạm vi giờ hẹp hơn nhiều so với trước) — xem 5.7.

### 5.2 _dom_configure(task)

```
_dom_configure(task)
  │
  ├─ Tìm config button (button[aria-controls] → popup element)
  ├─ Mở popup nếu chưa mở (_fire_click → pointer + mouse events)
  │
  ├─ Chọn main tab (nếu cần, khớp theo DANH SÁCH biến thể icon — xem 2026-07-30):
  │     textToImage / imageToImage → tab icon ['image']
  │     textToVideo / imageToVideo / frameToVideo / componentsToVideo → tab icon
  │       ['videocam', 'play_circle']  (Google đổi 'play_circle'→'videocam' 2026-07-30,
  │       giữ lại 'play_circle' làm fallback phòng đổi lại/A-B test)
  │
  ├─ Chọn sub-tab (nếu cần, 2026-07-21 — xem CHANGELOG "Fix mapping sub-tab NGƯỢC"):
  │     imageToVideo → tab text 'Thành phần'  (đã sửa, trước đây SAI là 'Khung hình')
  │     frameToVideo → tab text 'Khung hình'  (suy luận theo tên, CHƯA có task thật để verify)
  │     componentsToVideo → tab text 'Thành phần'  (2026-07-25, DÙNG CHUNG với imageToVideo —
  │       quyết định user: "chạy luồng chỉ ảnh đầu, add nhiều ảnh giống image to image".
  │       Mode này từng bị xoá khỏi cả 2 dict ở đây ngày 2026-07-21 do hiểu lầm ENUM
  │       tasks_media_flow.mode chưa hỗ trợ giá trị này — nay ToolSub gốc đã migrate
  │       ENUM (pipeline NanaBananaPro "Ingredient -> Video"), trả lại mapping.
  │       _dom_upload_images() KHÔNG cần sửa gì — đã generic, tự hỗ trợ N ảnh.)
  │
  ├─ Chọn aspect ratio (text match trong popup)
  ├─ Chọn output count: 1x / x2 / x3 / x4
  ├─ Chọn model: click arrow_drop_down button → tìm menuitem → click
  └─ Đóng popup
```

**Chọn model (2026-07-21, `MODEL_MATCH_JS` — hằng số module-level đầu `server/worker.py`, dùng chung bởi `_dom_configure()` VÀ `tests/_test_model_select.py`) — ưu tiên khớp CHÍNH XÁC, không chỉ substring:** tìm menuitem khớp `model` bằng cách LỌC hết candidate có `labelOf(el).includes(model)` rồi ưu tiên candidate nào khớp CHÍNH XÁC (sau khi bỏ tiền tố emoji/ký tự trang trí, vd `🍌`) — chỉ fallback về candidate substring đầu tiên nếu không có khớp chính xác. Bắt buộc vì danh sách model hiện có các cặp label mà 1 cái là PREFIX của cái kia (vd `"Nano Banana 2"` ⊂ `"Nano Banana 2 Lite"`; `"Veo 3.1 - Lite"` ⊂ `"Veo 3.1 - Lite [Lower Priority]"`) — nếu chỉ dùng `.find()` + `.includes()` đơn thuần, đổi thứ tự DOM (Google có thể đổi bất cứ lúc nào) có thể khiến chọn NHẦM model một cách âm thầm, không exception nào báo. `labelOf(el)` (KHÔNG phải `el.textContent` thô) — `cloneNode` rồi xoá hết thẻ `<i>` con trước khi lấy text: dropdown VIDEO có icon ligature `<i>volume_up</i>` NẰM CHUNG element với label, chữ "volume_up" là CHỮ THẬT (bắt đầu bằng chữ cái) nên regex strip-emoji không loại được, nếu chỉ dùng `el.textContent` thô thì nhánh khớp-chính-xác KHÔNG BAO GIỜ match cho model video nào — bug thứ 2 tự phát hiện lúc viết test thật bằng Chrome (Node.js test ban đầu dùng string thuần, không lộ ra vấn đề này). Xem CHANGELOG.

**Fix "video không tạo được" — icon tab Video đổi `play_circle`→`videocam` + text nút x1 là `'x1'` không phải `'1x'` (2026-07-30):** User cung cấp HTML thật của popup config lấy trực tiếp từ Flow — xác nhận Google đổi icon Material Symbol của tab "Video" (trước `play_circle`, dropdown mới `videocam`). `main_tab_icon` (chuỗi đơn, so `===`) đổi thành `main_tab_icons` (dict giá trị LIST, so `.includes()`) — mode video giờ khớp CẢ `videocam` lẫn `play_circle` (fallback nếu Google đổi lại), mode ảnh giữ `['image']`. Đồng thời `count_map` (tìm `'1x'`/`'x2'`/`'x3'`/`'x4'`) đổi thành `count_variants` — HTML thật cho thấy nút x1 hiển thị đúng `'x1'` (không phải `'1x'` như code cũ), mỗi count giờ khớp cả 2 thứ tự `['x{n}', '{n}x']`. Verify bằng script Node.js parse trực tiếp đoạn HTML thật (regex-based, không cần jsdom) — xác nhận logic cũ không tìm thấy tab Video/nút x1 trên HTML đó (tái hiện đúng bug), logic mới tìm thấy đúng. **CHƯA verify trên browser Flow thật.**

### 5.3 _dom_upload_images() — Upload ảnh đính kèm

Áp dụng cho: `imageToImage`, `imageToVideo`, `componentsToVideo`.

```
_dom_upload_images(source_media, mode)
  │
  ├─ Fetch ảnh về temp dir (req_lib.get → lưu file → base64 encode)
  │
  └─ Với mỗi ảnh:
       │
       ├─ B1: Mở picker — click button có icon 'add_2', DÙNG CHUNG cho MỌI mode
       │    (kể cả imageToVideo — xem fix 2026-07-17 bên dưới, KHÔNG còn nhánh riêng
       │    click div[aria-haspopup="dialog"] text='Bắt đầu'/'Kết thúc')
       │    Chờ picker mở: 'Thêm vào câu lệnh' button + 'Tìm kiếm thành phần' input
       │
       ├─ B2: Click tab 'Hình ảnh' (button[role="tab"] với icon 'image')
       │
       ├─ B3: Search tên file (CDP insertText vào input[placeholder='Tìm kiếm thành phần'])
       │       Sleep 1.5s → chờ gallery filter render
       │
       ├─ B4: Kiểm tra gallery
       │    Có [data-index] items → Case 1 (ảnh đã có trong gallery) ✔
       │    Không có items → Case 2 (upload file mới):
       │         Tìm input[type="file"] trong picker
       │         _inject_file_to_input():
       │           - Lưu dataURL vào window.__selUpload
       │           - execute_script: atob() → Uint8Array → Blob → File → DataTransfer
       │           - Set HTMLInputElement.prototype.files setter hoặc defineProperty
       │           - Dispatch 'input' + 'change' events
       │         Poll media src mới xuất hiện (img.src chứa 'trpc/media')
       │         Re-trigger search input để gallery refresh
       │
       ├─ B5: Click 'Thêm vào câu lệnh' → đợi picker đóng
       │
       └─ Validate (2026-07-21): poll ~3s đếm ảnh THẬT SỰ đính kèm trong prompt
            (button[data-card-open] img[src*='getMediaUrlRedirect']) — đủ số
            lượng mong đợi thì coi ảnh này xong; KHÔNG đủ thì lặp lại TOÀN BỘ
            B1-B5 cho ảnh này (tối đa 3 lần) thay vì tin click đã thành công
```

**Timeout tăng gấp đôi cho 3 bước "chờ Chrome render" (2026-07-17):** log thật (2 profile DOM `image_only` chạy đồng thời, cùng settings) cho thấy 1 profile chạy một mình luôn thành công, nhưng profile chạy CÙNG LÚC với profile khác lỗi đều đặn `picker không mở sau 10s` ngay quanh mốc 10-11s — 2 Chrome+Selenium tranh chấp CPU khiến Chrome cần nhiều thời gian hơn để thật sự render picker dialog/input/thumbnail. Đây là giới hạn TÀI NGUYÊN thật (không phải lỗi logic/selector) nên fix bằng cách tăng timeout, không sửa cơ chế: B1 "chờ picker mở đầy đủ" 10s→20s, B4 Case 2 "tìm file input" 5s→7.5s, B4 Case 2 "chờ ảnh render sau upload" 30s→40s. Các bước chờ DOM-state thuần (B2 `aria-selected`, đợi picker đóng sau B5) giữ nguyên — không phụ thuộc render nặng nên ít khả năng là bottleneck. Xem CHANGELOG "client_tool: tăng timeout _dom_upload_images()...". **Nếu máy chạy client_tool thiếu CPU/RAM thật sự cho nhiều Chrome đồng thời, tăng timeout chỉ trì hoãn triệu chứng** — cần theo dõi thêm, có thể phải cân nhắc giảm `max_concurrent`/số profile veo3 chạy đồng thời thay vì tiếp tục tăng timeout.

**Nghi vấn KHÁC — i18n chưa đủ khớp do 2 profile lệch ngôn ngữ tài khoản (2026-07-17, THÊM DUMP CHẨN ĐOÁN, chưa xác nhận):** User đưa ra giả thuyết cạnh tranh với "tải hệ thống" ở trên: 2 profile 1 tài khoản Google tiếng Anh, 1 tiếng Việt — nghi biến thể `addToPrompt`/`searchPlaceholder` (2 biến thể vi/en, xem 11.18) chưa khớp đúng text Google hiển thị cho 1 trong 2 ngôn ngữ. Đối chiếu `extensions/content/flowMediaGenerator.js::I18N` xác nhận 2 biến thể ĐANG DÙNG khớp CHÍNH XÁC với file tham chiếu — không phải lỗi port sai, nhưng KHÔNG chứng minh được bản tham chiếu đã từng verify với tài khoản tiếng Anh thật hay chưa. Không thể xác nhận giả thuyết này đúng/sai nếu không có bằng chứng DOM thật lúc lỗi — thay vì đoán thêm biến thể (rủi ro đoán sai lần nữa), vòng lặp "chờ picker mở" (B1) giờ log riêng `hasAdd`/`hasSearch` (biết chính xác điều kiện nào thiếu) + dump TOÀN BỘ text button và placeholder input thật trên trang ngay lúc timeout (`DOM upload: picker timeout — hasAdd=... hasSearch=... | button text trên trang: [...] | input placeholder: [...]`). Không đổi logic pass/fail hiện có. Lần lỗi tiếp theo sẽ có bằng chứng cụ thể để sửa đúng danh sách i18n (nếu đúng đây là nguyên nhân) thay vì tiếp tục đoán mò.

**Danh sách i18n tách ra file JSON (2026-07-17, xem 11.18):** ngay sau đó, user yêu cầu tách hẳn danh sách biến thể ra `client_tool/i18n_texts.json` để sửa/thêm biến thể (vd sau khi có bằng chứng từ dump chẩn đoán ở trên) mà KHÔNG cần đổi code. `_dom_upload_images()` giờ gọi `get_i18n_texts()` (`server/i18n_texts.py`) thay vì đọc hằng số module-level.

**Fix B1 `imageToVideo` không mở được picker (2026-07-17):** trước đây B1 rẽ nhánh RIÊNG cho `imageToVideo` — click 1 trong 2 nút `div[aria-haspopup="dialog"]` text CHÍNH XÁC "Bắt đầu"/"Kết thúc" (hardcode tiếng Việt, không qua i18n) để mở dialog chọn ảnh đầu/cuối. Dialog này mở ĐÚNG nhưng có DOM KHÁC HẲN picker `add_2` — B2-B5 ngay sau đều viết cho DOM của picker `add_2` nên không khớp, upload thất bại lặng lẽ dù dialog mở thành công. User báo lỗi thật: profile `task_mode=video_only` xử lý task #2457 (`mode=imageToVideo`) dừng ngay sau "DOM: uploading 1 image(s)…". Đối chiếu `extensions/content/flowMediaGenerator.js::uploadImagesViaPromptPicker()` (nguồn tham chiếu đã proven, tự document đúng bug này) xác nhận: MỌI mode dùng CHUNG 1 code path `add_2`, `mode` chỉ dùng để log. `uploadImagesDirect()` (2 nút Bắt đầu/Kết thúc) trong extension là dead code, không còn caller. Xoá hẳn nhánh riêng, `imageToVideo` giờ đi thẳng qua logic `add_2` như mọi mode khác. **CHƯA verify bằng browser thật** — cần user chạy lại task `imageToVideo` để xác nhận.

**Fix B1 đợi nhầm điều kiện "Thêm vào câu lệnh" — nút này chưa tồn tại lúc picker vừa mở (2026-07-18):** Bản 2026-07-17 (mục "picker không mở sau 10s" ở dưới) đợi CẢ `hasAdd` (nút "Thêm vào câu lệnh"/"Add to Prompt") LẪN `hasSearch` (search input) trước khi coi picker đã mở — dựa trên giả định nút confirm tồn tại NGAY khi picker mở. Log thật (task #3754, profile Tool Media mới, account tiếng Việt) phản bác giả định này bằng bằng chứng cụ thể: dump chẩn đoán cho thấy `hasSearch=True` (picker THẬT SỰ mở — đủ UI: tìm kiếm, filter, tab Hình ảnh/Video/Giọng nói/Hình đại diện...) nhưng `hasAdd=False` dù quét hết 40 button text duy nhất trên trang. Kết luận: nút "Thêm vào câu lệnh" chỉ render SAU KHI đã chọn/upload ≥1 media (kiểu nút "Thêm đã chọn" chỉ xuất hiện khi có gì để thêm) — đợi nó ở B1 (trước cả khi tìm/chọn ảnh) là sai điều kiện. Fix: B1 chỉ còn đợi `hasSearch`; B5 (bước click, cách B1 ~170 dòng) đã CÓ SẴN vòng lặp riêng chờ đúng nút này (4s) ngay trước khi click — đúng thời điểm nút có khả năng tồn tại thật, không cần B1 đợi lặp lại. B4 (kiểm tra gallery `[data-index]`, vốn cũng scope qua cùng text "Thêm vào câu lệnh") thêm fallback: nếu không tìm được "picker" container qua text đó, dùng `closest()` từ search input lên tối đa 8 cấp cha tìm ancestor chứa `[data-index]`. **CHƯA verify trên browser thật sau fix.**

**Validate ảnh THẬT SỰ đính kèm vào prompt + tự retry search/upload nếu không (2026-07-21):** user cung cấp HTML thật của prompt SAU KHI 1 ảnh đính kèm thành công — `<button data-card-open="false"><div><img src="/fx/api/trpc/media.getMediaUrlRedirect?name=UUID"></div><div><i>cancel</i></div></button>`, nằm NGAY CẠNH ô nhập liệu (khác hẳn tile trong gallery picker) — và báo lỗi thật: "upload image thành công nhưng nhấn Add to Prompt đôi khi lại không add ảnh vào prompt". Trước đây B5 coi bấm xong = thành công (chỉ đợi picker đóng rồi log "attached ✔"), không hề kiểm tra ảnh có THẬT SỰ xuất hiện trong prompt — cùng lớp bug "click chạy không lỗi (tìm thấy nút, không exception) nhưng không có tác dụng thật" đã gặp ở nơi khác trong file (vd nút Gửi Gemini). Fix: B1-B5 tách thành hàm lồng `_attach_one_reference(img, idx, expected_count)`, cuối hàm poll `_count_attached_refs()` (đếm `button[data-card-open] img[src*="getMediaUrlRedirect"]` — `data-card-open` là attribute semantic ổn định, không phải class hash) và trả `True`/`False`. Vòng lặp ngoài thử tối đa 3 lần — chưa đủ ảnh (hoặc lỗi cấu trúc như không tìm thấy nút) thì lặp lại TOÀN BỘ search/upload cho ảnh đó, hết 3 lần vẫn không đủ mới raise lỗi rõ ràng. Verify: load ĐÚNG HTML mẫu user cung cấp vào Chrome thật (`data:text/html`, headless) — selector đếm đúng 1/0/2 ảnh tương ứng 3 kịch bản test.

**FIX NGAY TRONG NGÀY — chính validate trên gây bug MỚI: poll ~3s quá ngắn → false-negative → upload trùng nhiều ảnh:** vài giờ sau khi thêm validate ở trên, user báo lỗi thật bằng log: task chỉ yêu cầu 1 ảnh, user xác nhận bằng mắt ảnh ĐÃ đính kèm thành công, nhưng validate liên tục báo "chưa xác nhận" và tự retry — kết quả cuối cùng **3 ảnh** bị đính kèm (3 UUID Google gán khác nhau, xác nhận 3 LẦN UPLOAD THẬT chứ không phải lỗi đếm). Đối chiếu timestamp log: bước upload NGAY TRƯỚC ĐÓ đã mất ~12-15s dưới cùng điều kiện "2 Chrome+Selenium tranh chấp CPU" đã biết (lý do các timeout khác trong hàm này từng tăng 10s→20s/5s→7.5s/30s→40s) — nhưng validate poll ~3s LUÔN hết hạn trước khi Angular kịp render chip. Fix: (1) tăng poll `_attach_one_reference()`'s validate từ ~3s → **20s** (100×0.2s, khớp cấp độ timeout khác trong hàm); (2) thêm lưới an toàn ĐẦU hàm — nếu `_count_attached_refs() >= expected_count` NGAY TỪ ĐẦU (attempt trước thật ra đã thành công, chỉ validate của nó hết hạn trước khi thấy) thì trả `True` ngay, KHÔNG upload thêm bản mới — chặn đứng khả năng nhân bản ảnh dù rơi vào edge case validate vẫn timeout hiếm gặp. **CHƯA verify lại trên browser thật sau fix lần 2 này** — cần user chạy lại task để xác nhận chỉ còn đúng 1 ảnh đính kèm.

**Bỏ bước "đợi picker đóng" — bấm xong chờ cố định 1s rồi validate thẳng (2026-07-23):** theo yêu cầu tiếp theo của user "check image đính kèm chưa chuẩn, hãy fix khi bấm Add to Prompt chờ 1 giây rồi check đủ image đính kèm chưa". Xoá hẳn `_find_picker()` + vòng lặp "đợi picker đóng" (tín hiệu GIÁN TIẾP — kiểm tra text "Thêm vào câu lệnh" biến mất khỏi DOM, không trực tiếp phản ánh ảnh đã đính kèm) + `sleep(0.5)` sau đó. Thay bằng `self._sleep(1)` cố định ngay sau click, rồi vào thẳng validate (`_count_attached_refs()`, vẫn giữ nguyên poll 20s đã fix ở mục trên — 1s chỉ là khoảng nghỉ cho Angular xử lý click, KHÔNG thay thế patience của validate). An toàn dù picker còn mở hay đã đóng vì `button[data-card-open]` (chip đính kèm) là phần tử khác hẳn gallery grid trong picker (`[data-index]`, xem B4) — không đếm nhầm. `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật.**

**Fix `_count_attached_refs()` đếm NHẦM `data-card-open` ở nơi khác trên trang (2026-07-23):** user cảnh báo đúng "toàn bộ HTML của Flow còn có các `data-card-open` khác" — bản trước query THẲNG `document.querySelectorAll('button[data-card-open] img...')` trên TOÀN TRANG, có thể đếm nhầm bất kỳ phần tử nào khác labs.google dùng chung attribute này (vd card trong khu vực kết quả/gallery bên dưới composer), không riêng chip ảnh đính kèm prompt. Fix: SCOPE lại — bắt đầu từ ô nhập liệu prompt (`div[role="textbox"][data-slate-editor="true"]`, attribute semantic ổn định của Slate.js), đi ngược lên tổ tiên (`parentElement`, tối đa 8 cấp) tới khi gặp container CŨNG chứa nút "add_2" (nút mở picker đính ảnh — luôn nằm CÙNG khung composer với ô nhập liệu và dải ảnh đính kèm, xem B1), dừng ở đó rồi mới đếm `[data-card-open]` BÊN TRONG container này — không hardcode "đi lên đúng N cấp" (dễ vỡ nếu Google chèn/bớt 1 lớp div), dừng theo TÍN HIỆU ngữ nghĩa an toàn hơn, cùng pattern `closest()`-tìm-theo-tín-hiệu đã dùng ở B4. Verify: dựng lại ĐÚNG cấu trúc composer thật kèm 1 phần tử `data-card-open` giả lập NẰM NGOÀI composer, chạy qua Chrome thật, trích xuất ĐÚNG đoạn JS từ file nguồn (không gõ lại tay) — selector cũ đếm dư (dính decoy), selector mới đếm đúng; test thêm case "composer chưa có ảnh nào" (0 chip thật + decoy bên ngoài) → đúng 0; case "không tìm thấy composer" → trả 0 an toàn.

### 5.4 _dom_fill_and_submit(prompt)

```
_dom_fill_and_submit(prompt)
  │
  ├─ Tìm div[role="textbox"] (tối đa 30s)
  ├─ CDP click vào textarea (mousePressed + mouseReleased)
  ├─ Type 20 ký tự đầu từng char (CDP insertText, delay 70-170ms/char — mô phỏng typing)
  ├─ CDP insertText phần còn lại
  └─ CDP dispatchKeyEvent Enter (keyDown + keyUp)
```

### 5.5 _dom_poll_done(tile_ids)

```
_dom_poll_done(tile_ids, timeout=600)
  │  Sleep 5s mỗi vòng
  │
  └─ Với mỗi tile_id: _dom_tile_status(tile_id)
       ├─ video src → {status:'done', type:'video', src:vid.src}
       ├─ img src (non-data:) → {status:'done', type:'image', src:img.src}
       ├─ warning icon → {status:'error', error:'Tile error'}
       ├─ text /\d+%/ → {status:'generating', pct:N}
       └─ else → {status:'waiting', pct:0}
```

### 5.6 _dom_resolve_url(url)

```
_dom_resolve_url(url)
  │
  └─ execute_async_script:
       fetch(url, {credentials:'same-origin', redirect:'follow', cache:'no-store'})
         → browser gửi labs.google session cookie
         → 302 → https://flow-content.google/image/UUID?Expires=...&Signature=...
         → res.url = CDN signed URL (public, không cần auth)
       Trả res.url về Python
```

### 5.7 _wait_and_reconcile_tasks(tasks_by_id) — RECONCILE theo batch qua server (2026-07-16)

Thay thế hoàn toàn `_collect_task_media_via_project_api()` (bản tự poll + tự tải qua `/task/download`, đã LỖI THỜI — xoá khỏi code). Lý do đổi: log thật cho thấy `_dom_fetch_project_media()` (interceptor) hoạt động ĐÚNG (đọc được 72 media/project liên tục), nhưng việc SO KHỚP/TẢI ở client vẫn không ra kết quả — trong khi backend đã có sẵn 1 cơ chế reconcile PROVEN (dùng bởi `extensions/`) làm đúng việc này, chỉ là `client_tool` chưa gọi tới. User mô tả lại đúng quy trình mong muốn: **submit batch → chạy xong batch → chờ 60s → refresh project → `flow.projectInitialData` → lấy TOÀN BỘ media so với DATABASE (server quyết định, không phải client tự đoán) → tải cái nào DB chưa có → còn batch thì lặp lại → hết thì đóng profile** (phần "đóng profile" đã có sẵn qua auto-scale, xem §11.9 — không cần thêm gì ở đây).

```
_wait_and_reconcile_tasks(tasks_by_id: {task_id: task_dict}, max_rounds, wait_secs)
  │  # max_rounds/wait_secs (2026-07-18): KHÔNG còn hardcode 10/60 — đọc từ
  │  # self._server_settings['reconcile_max_rounds']/['reconcile_wait_secs']
  │  # (local_settings.py, sửa được ở GUI trang Cài đặt)
  ├─ pending = set(tasks_by_id.keys())
  │
  └─ Lặp tối đa max_rounds vòng (dừng sớm nếu pending rỗng):
       │
       ├─ sleep(wait_secs)                       # mặc định 60s, tự chỉnh được
       ├─ matched = _reconcile_project_media(pending)   # 2 GIAI ĐOẠN — xem dưới
       └─ pending -= matched

  → Trả về pending CÒN LẠI (server chưa xác nhận xong sau max_rounds) — caller tự
    fallback tile-polling CHỈ CHO các task này (không phải toàn batch)
```

**`_reconcile_project_media(task_ids)` — 2 GIAI ĐOẠN, chỉ "mở link" cho media THẬT SỰ còn thiếu (2026-07-16, theo yêu cầu user):**
```
_reconcile_project_media(task_ids)
  │
  ├─ media_list = _dom_fetch_project_media()   # reload + interceptor, xem dưới
  ├─ Lọc item có prefix TASK_{id}: nhận dạng được (2026-07-17: KHÔNG còn giới hạn
  │    id ∈ task_ids — gửi MỌI item nhận dạng được để tự "nhặt" task khác đang
  │    mắc kẹt trong cùng project, xem giải thích dưới)
  ├─ items = [{name, taskId, prompt, type}, ...]     # KHÔNG kèm url ở bước này
  │
  ├─ Giai đoạn 1 — CHECK (rẻ, không cần mở link):
  │     POST /api/media/reconcile/check {items}   # không có url
  │       → server so `name` với result_files trong DB
  │       → name ĐÃ CÓ → tính vào known (không cần url để biết)
  │       → name CHƯA CÓ (thiếu url nên không tải được) → trả nguyên item
  │           trong unknown:[item...]
  │     matched = r1.matched (thường rỗng — chưa có url thì chưa tải được gì)
  │     [nếu unknown rỗng] → return matched   # mọi media task cần đều ĐÃ CÓ sẵn
  │
  └─ Giai đoạn 2 — RESOLVE + TẢI (chỉ cho item trong unknown):
       Với mỗi item ∈ unknown:
         real_src = _dom_find_tile_src_by_name(name)   # ƯU TIÊN — "mở link ẩn"
                    thực chất là fetch() với session cookie trong context trang,
                    KHÔNG phải mở tab thấy được — xem _dom_resolve_url (5.6)
         nếu real_src:  url = _dom_resolve_url(real_src)
         nếu không:     url = _dom_resolve_url(f'{FLOW_TRPC_BASE}/media.getMediaUrlRedirect?name={name}')  # fallback
       resolved = [{**item, url}, ...]
       POST /api/media/reconcile/check {items: resolved}   # LẦN 2, giờ có url
         → server tự tải + set done, trả {matched:[taskId,...]}
       matched |= r2.matched
       return matched
```

**Log chẩn đoán khi 0 item khớp (2026-07-17) → ROOT CAUSE tìm ra ngay từ log đầu tiên:** Nhánh `if not items:` trong `_reconcile_project_media()` trước đây `return set()` hoàn toàn im lặng; thêm log dump RAW JSON của item video (xem CHANGELOG) → log thật NGAY LẦN CHẠY ĐẦU cho thấy: `raw item` của task video #2777 (mode imageToVideo) CÓ ĐẦY ĐỦ `mediaMetadata.mediaTitle`/`promptInputs[].textInput` chứa đúng `TASK_2777:SCENE_165...` — nhưng KHÔNG ở vị trí 0 của chuỗi. Google bọc prompt thật của request `videoGenerationRequestData` (image-to-video/reference-to-video) trong 1 template XML nội bộ: `<root><context></context><instruction><prompt>TASK_2777:SCENE_165...` — `TASK_<id>:` nằm sau đoạn wrapper, không ở đầu. `_extract_task_id_from_prompt()` cũ dùng `re.match(r'^TASK_(\d+):', ...)` (neo đầu chuỗi) nên LUÔN fail với mọi task video, trong khi text-to-image lưu prompt thô trực tiếp (không bọc XML) nên vẫn luôn khớp — giải thích chính xác vì sao chỉ có task VIDEO không bao giờ reconcile được, task ảnh thì bình thường. **Fix:** đổi sang `re.search(r'TASK_(\d+):', prompt)` (tìm ở bất kỳ đâu, không neo đầu chuỗi) — vẫn khớp đúng y hệt cho prompt thô không bọc XML, và giờ khớp thêm cả prompt bị bọc. Bug nằm HOÀN TOÀN ở bước trích task_id — `_dom_resolve_url()` (bước resolve CDN URL, dùng `fetch(url,{credentials:'same-origin',redirect:'follow'})` rồi đọc `r.url`) vốn đã đúng sẵn, chỉ chưa bao giờ có cơ hội chạy tới vì matching luôn thất bại trước đó ở bước lọc `items`.

**`_reconcile_project_media()` không còn lọc theo `task_ids` — tự động nhặt task khác đang mắc kẹt (2026-07-17):** Trước đây chỉ gửi lên `/reconcile/check` item nào có `tid in task_ids` (task đang chờ NGAY LÚC gọi). User báo: prompt mới reconcile bình thường, nhưng 1 video CŨ đã render xong thật (task của nó `status='error'` hết retry, KHÔNG BAO GIỜ còn nằm trong `task_ids` của lần gọi nào sau) không được tải về dù vẫn đọc thấy trong `media_list` mỗi lần fetch. Xác nhận qua code server: `reconcile_check()` (`backend/routes/reconcile.py`) match theo `task_id` KHÔNG lọc status, và `_apply_media_to_task()` set `status='done'` VÔ ĐIỀU KIỆN — server sẵn sàng tự phục hồi task `error`, chỉ là client tự giới hạn không gửi lên. Fix: bỏ điều kiện `tid not in task_ids` — mọi item nhận dạng được `TASK_<id>:` đều gửi lên, không riêng task đang chờ; log `reconcile: nhặt được thêm N task khác đang mắc kẹt...` khi có "bonus recovery". Khớp đúng ý tưởng gốc user mô tả (mục "Reconciliation media" root CLAUDE.md): lấy TOÀN BỘ media so với DATABASE, không giới hạn theo task hiện tại — nhờ vậy các task `error` cũ (kể cả retry đã hết, không bao giờ được server giao lại) tự phục hồi mà KHÔNG cần reset DB thủ công, miễn media của nó vẫn còn trong project.

**Kiểm tra media đã có sẵn TRƯỚC KHI submit lại task retry (2026-07-17):** User báo đúng hiện tượng: task lỗi (do bug reaper §3.1 hoặc bug regex reconcile ngay ở mục trên) rồi retry → "cứ ở trạng thái đang tạo rồi lại tạo lại task cũ mặc dù nó đã có video" — `_run_task_dom()`/`_run_tasks_batch()` trước đây LUÔN `_dom_fill_and_submit()` prompt mới vô điều kiện mỗi lần được gọi, kể cả khi đây là lần retry của 1 task mà attempt trước ĐÃ render xong thật (chỉ là server chưa tải được) — sinh thêm 1 lần generate hoàn toàn MỚI, trùng lặp, tốn quota Flow. Fix: nếu `task.retry_count > 0` (là retry, không phải lần đầu), gọi `_reconcile_project_media({task_id})` MỘT LẦN ngay sau `_ensure_flow_page()`, TRƯỚC `_dom_configure()`/upload/submit — nếu tìm thấy media sẵn có (giờ match đúng nhờ fix `re.search` ở trên) thì tải luôn + `_handle_task_success()`, bỏ qua hoàn toàn việc submit/generate mới. Chỉ check khi `retry_count>0` để không tốn 1 lần refresh+15s chờ interceptor cho MỌI task (lần đầu chắc chắn chưa từng submit prompt này).

**⚠️ Mở rộng — reconcile TOÀN BỘ project ĐÃ LƯU 1 lần TRƯỚC MỖI BATCH task mới, không còn giới hạn theo `retry_count` (2026-08-18) — ⚠️ ĐÃ DỜI VỊ TRÍ NGAY TRONG CÙNG NGÀY, xem ghi chú NGAY DƯỚI:** Theo yêu cầu user "khi phát hiện task thì chưa nhận phải vào link project đã lưu xem có task nào hoàn tất chưa cập nhật rồi mới xem task chưa xong thì nhận — mỗi lần nhận task mới đều phải check project hiện tại". Quyết định "chỉ check khi `retry_count>0`" ở mục ngay trên ĐÚNG cho CHÍNH task đang xét, nhưng bỏ sót media của **TASK KHÁC** đang mắc kẹt trong CÙNG project (vd 1 task video render xong thật nhưng bị reaper reclaim giao lại cho máy/project khác — media mồ côi vĩnh viễn nếu không có task retry nào TÌNH CỜ chạy lại đúng project cũ). `_process_tasks()` (`server/worker.py`) LÚC ĐÓ, ngay sau khi khởi động keepalive thread và TRƯỚC KHI dispatch batch task VỪA nhận từ heartbeat tới bất kỳ hàm xử lý nào (`_run_tasks_batch`/`_run_tasks_api_batch`/vòng lặp `_run_task`): với `worker_mode in ('dom','api')` — `_ensure_flow_page()` (đảm bảo đứng đúng "link project đã lưu" = `profile['project_url']`) rồi `_reconcile_project_media(set())` (truyền `set()` rỗng vẫn quét TOÀN BỘ project như bình thường, do hàm này đã KHÔNG lọc theo `task_ids` từ 2026-07-17 — xem mục ngay trên). 2 chỗ check theo `retry_count` ở mục trên GIỮ NGUYÊN — mục đích khác (match ĐÚNG 1 task_id cụ thể để quyết định có submit lại hay không), 2 cơ chế bổ sung nhau chứ không thay thế.

**⚠️ ĐỔI HƯỚNG NGAY TRONG CÙNG NGÀY — bước reconcile ở trên ĐÃ DỜI từ "đầu batch mới" sang "cuối batch vừa xong" (bên trong `_recover_flow_project_page_after_cache_clear()`), xem chi tiết đầy đủ ở §11.30-mở-rộng (mục "ĐỔI HƯỚNG quy trình cuối mỗi batch" ngay dưới đây) — user mô tả lại TOÀN BỘ quy trình mong muốn theo đúng thứ tự: xoá cookie → refresh → chờ → click "Create with Google Flow" → refresh LẦN 2 lấy task hoàn tất → nhận batch tiếp theo. Đoạn code gọi `_reconcile_project_media()` ở ĐẦU `_process_tasks()` mô tả ở đoạn TRÊN ĐÂY đã bị XOÁ — không còn tồn tại trong code, chỉ giữ lại đoạn mô tả này làm lịch sử/bối cảnh cho quyết định thiết kế.**

**`/api/media/reconcile/check` — `url` giờ TÙY CHỌN (2026-07-16, `backend/routes/reconcile.py`):** ĐÃ CÓ SẴN, PROVEN — dùng bởi `extensions/content/flowMediaGenerator.js::reconcileProjectTiles()` từ trước (kể cả cách trích `taskId` từ prompt `/^TASK_(\d+):/`, xem `_scanProjectCards()` dòng ~275-278, port lại y hệt ở `_extract_task_id_from_prompt()`). Trước đây `url` BẮT BUỘC cho mọi item (thiếu thì bị lọc bỏ hoàn toàn, không xét tới) — client_tool bản đầu (xem "Cập nhật #4" ở §11.16) phải resolve URL cho MỌI item khớp taskId trước khi gửi, kể cả những media SERVER ĐÃ CÓ SẴN (lãng phí — mỗi resolve là 1 lần "mở link"/fetch tốn thời gian). Sửa: `url` giờ optional — item thiếu `url` vẫn được xét "đã có chưa" bình thường (không cần url để biết), chỉ khi CHƯA CÓ mới trả về trong `unknown` để caller tự resolve rồi gửi lại. Hành vi cũ (item CÓ `url`) giữ nguyên 100% — `extensions/` luôn gửi kèm `url` nên không bị ảnh hưởng.

**`_dom_fetch_project_media()` — KHÔNG tự gọi API, chỉ LẮNG NGHE (2026-07-16):** User quan sát trực tiếp Network tab: mỗi khi vào/reload project page, CHÍNH trang labs.google TỰ GỌI `flow.projectInitialData` — không cần (và không nên) tự `fetch()` endpoint này (bản đầu SUY ĐOÁN query string `?projectId=X`, rủi ro sai format + lặp lại đúng lớp bug "tự construct URL trpc → Failed to fetch" đã biết). **Đã verify hoạt động đúng qua log thật** (72 media/project đọc được liên tục). Cách làm:
```
_install_project_data_interceptor()   # gọi 1 lần, idempotent
  └─ CDP Page.addScriptToEvaluateOnNewDocument:
       cài window.fetch override CHẠY TRƯỚC JS của chính trang, ở MỌI lần
       navigate/reload tiếp theo trong cùng phiên driver (execute_script() thường
       chạy SAU khi trang đã load nên sẽ lỡ request tự động bắn ra ngay lúc mount)
       → bắt response bất kỳ request nào URL chứa 'projectInitialData'
       → lưu vào window.__pidCaptures (tối đa 5 bản ghi gần nhất)

_dom_fetch_project_media()   # trả None (thất bại) hoặc list (thành công, có thể [])
  ├─ [nếu current_url không có '/project/'] → trả None (chưa vào project)
  ├─ _install_project_data_interceptor()
  ├─ driver.refresh()          # kích hoạt 1 lần "truy cập" mới → Google tự gọi lại
  ├─ poll window.__pidCaptures tới khi có bản ghi mới (tối đa 15s) → không có → trả None
  └─ parse capture mới nhất: result.data.json.projectContents.media[]
       mỗi item: {name: UUID, mediaMetadata.requestData.promptInputs[].textInput, ...}
     Shape lạ/parse lỗi → log rõ để chẩn đoán, trả None
```

**`_dom_find_tile_src_by_name(name)` — QUAN TRỌNG, đọc trước khi đụng vào phần resolve URL (2026-07-16):** `extensions/` và `backend` KHÔNG BAO GIỜ tự construct URL `/fx/api/trpc/media.getMediaUrlRedirect?name=X` — luôn lấy nguyên `img.src`/`video.src` (DOM property, absolute) Google đã tự nhúng sẵn vào tile (xem `extensions/content/content.js::RESOLVE_MEDIA_URLS` + `extensions/content/flowMediaGenerator.js::_scanProjectCards/reconcileProjectTiles`, và CHANGELOG "Fix: resolve CDN URL dùng srcUrl từ DOM thay vì reconstruct canonical URL" — tự construct từng gây "Failed to fetch"). Response mẫu `flow.projectInitialData` KHÔNG có field URL nào (chỉ `name` + `mediaGenerationId` opaque), nên hàm này quét `[data-tile-id]` hiện có trong DOM, tìm tile mà `img.src`/`video.src` chứa `name=<UUID>` (cùng regex `extractMediaName()` ở `extensions/background.js` dùng ngược lại) để lấy URL THẬT thay vì tự construct — vì `flow.projectInitialData` chỉ liệt kê media ĐÃ HOÀN TẤT nên tile của task vừa submit gần như chắc chắn đã render. CHỈ khi tile không tìm thấy trong DOM (project vừa reload, tile bị đẩy khỏi feed...) mới rơi xuống tự construct `?name=X` — lúc đó chấp nhận lại đúng rủi ro đã biết, nhưng giờ chỉ còn là fallback của fallback.

**Fallback (2 lớp):** (1) nếu `_dom_find_tile_src_by_name()` không tìm thấy tile → tự construct URL (rủi ro đã biết, hiếm khi cần tới). (2) nếu `_wait_and_reconcile_tasks()` trả về `pending` khác rỗng sau `max_rounds` (~10 phút), caller (`_run_task_dom`/`_run_tasks_batch`) tự chuyển sang `_collect_task_media_via_tiles()`/`_resolve_tile_media()` (tile DOM polling cũ, 5.1/5.5/5.6, gọi thẳng `/task/download` như trước) — CHỈ áp dụng cho các task còn trong `pending`, không phải toàn batch. Luồng cũ KHÔNG bị xoá, chỉ xuống hàng ưu tiên cuối.

---

## 6. Luồng từng case

### Case 1: textToImage

**Khuyến nghị mode: `api` (đã test ✅)**

```
[API mode]
Task fields: prompt_text, mode='textToImage', aspect_ratio, output_count, model

1. Worker idle → heartbeat nhận task
2. _run_task_api():
   a. POST /task/processing
   b. _generate_image_via_ui():
      - _ensure_flow_project() → về /project/{uuid}
      - _install_gen_interceptor() → override window.fetch
      - send_keys(prompt) + send_keys(RETURN) vào [contenteditable="true"]
      - Poll 90s → bắt response 200 → lấy media[].image.generatedImage.fifeUrl
   c. POST /task/download {media:[{name:'img_{id}_1', url: fifeUrl}]}
3. server_gemini_flow: download CDN URL → lưu media_output/ → DB status='done'
4. audio-cue-editor: poll /task/result → hiển thị ảnh
```

**Lưu ý:** Flow UI tự gắn recaptchaToken và Authorization header — không cần capture thủ công cho image.

---

### Case 2: imageToImage

**Khuyến nghị mode: `dom`**

```
Task fields: prompt_text, mode='imageToImage', source_media=[{url, filename}], aspect_ratio, output_count

1. _run_task_dom():
   a. _ensure_flow_page()
   b. _dom_configure(task) → chọn tab 'image', aspect_ratio, output_count
   c. _dom_upload_images(source_media, 'imageToImage'):
      - Fetch ảnh từ URL → base64
      - Click 'add_2' button → mở picker
      - Click tab 'Hình ảnh'
      - Search tên file
      - Nếu chưa có gallery → DataTransfer inject → chờ src mới xuất hiện
      - Click 'Thêm vào câu lệnh'
   d. before_ids = snapshot tile IDs hiện tại
   e. _dom_fill_and_submit('TASK_{id}:{prompt}')
   f. _dom_wait_new_tiles(before_ids, count, 90s)
   g. _dom_poll_done(new_ids, 600s) → src URLs
   h. _dom_resolve_url(src) → CDN signed URL
   i. POST /task/download {media}
```

---

### Case 3: ingredientToImage (componentsToVideo dạng ảnh — chưa đặt tên chính thức)

**Tương tự imageToImage nhưng mode selector khác.**

```
Task fields: mode='imageToImage' hoặc custom mode, source_media, prompt_text

Luồng y hệt Case 2 (imageToImage DOM mode).
Khác biệt: nếu server_gemini_flow thêm mode riêng, cần thêm sub-tab selector trong _dom_configure().
```

---

### Case 4: imageToVideo (frameToVideo)

**Khuyến nghị mode: `dom`**

```
Task fields: prompt_text, mode='imageToVideo', source_media=[{url:'start.jpg'}, {url:'end.jpg'}], video_duration

1. _run_task_dom():
   a. _dom_configure(task):
      - Chọn tab Video (icon 'videocam', fallback 'play_circle' — xem 2026-07-30)
      - Chọn sub-tab 'Khung hình'
   b. _dom_upload_images(source_media, 'imageToVideo'):
      - Ảnh 0 và 1: click 'add_2' button → mở picker (dùng chung với mọi mode
        khác kể từ fix 2026-07-17, xem §5.3 — KHÔNG còn click
        div[aria-haspopup="dialog"] text='Bắt đầu'/'Kết thúc')
   c. _dom_fill_and_submit(prompt)
   d. Poll tiles → resolve URL → POST /task/download
```

**source_media:** list 2 items — `[{url: startFrame_url, filename:'start.jpg'}, {url: endFrame_url, filename:'end.jpg'}]`

---

### Case 5: textToVideo

**API mode** (cần token): Hiện HTTP 403 nếu recaptchaToken không hợp lệ.  
**DOM mode** (fallback): Tương tự textToImage nhưng cần chọn tab Video, không upload ảnh.

```
[DOM mode — khuyến nghị vì API 403]
Task fields: mode='textToVideo', prompt_text, video_duration

1. _run_task_dom():
   a. _dom_configure(task): chọn tab Video (icon 'videocam', fallback 'play_circle'), không chọn sub-tab
   b. Không upload ảnh (source_media rỗng)
   c. _dom_fill_and_submit(prompt) → poll tiles → download
```

```
[API mode — nếu token hợp lệ]
_run_task_api() → _call_video_api():
  POST /v1:batchAsyncGenerateVideo → nhận operation name
  Poll GET /v1/{op_name} mỗi 8s → đợi done=true
  Lấy videos[].uri → POST /task/download
```

---

## 7. Kết thúc một task — POST /task/download

Sau khi có CDN URL (từ API mode hoặc DOM resolve):

```
POST http://localhost:13443/api/media/task/download
  Body: {
    taskId: <int>,
    machineCode: 'selenium-{profile_id}',
    cueId: <int>,
    mode: 'textToImage' | ...,
    media: [
      {name: 'img_42_1', url: 'https://flow-content.google/image/UUID?Expires=...'},
      ...
    ]
  }
```

`server_gemini_flow.py` xử lý:
```
_process_media(taskId, media)
  → requests.get(url, stream=True)   # CDN URL public, không cần cookie
  → detect Content-Type → lưu media_output/task{id}_cue{id}_{n}.{ext}
  → UPDATE tasks_media_flow SET result_files=JSON, status='done', completed_at=NOW()
  → UPDATE machines_media SET status='idle', current_task_id=NULL
```

---

## 8. Cấu trúc thư mục

```
client_tool/
├── CLAUDE.md                  # File này — đọc đầu tiên khi làm việc với folder này
├── main.py                     # Entry point MỎNG (đổi tên từ selenium_gui.py, xem 11.12) — dựng
│                               #   QApplication + palette rồi show gui.main_window.MainWindow.
│                               #   Server chạy embedded trong process này (11.11).
├── gui/                        # Package GUI (tách từ 1 file ~1820 dòng, xem 11.12)
│   ├── config.py               #   API_BASE, REFRESH_MS, LOG_REFRESH_MS
│   ├── style.py                 #   Bảng màu C + QSS stylesheet
│   ├── api_client.py            #   api()/api_sync() — gọi HTTP nội bộ qua QThreadPool
│   ├── widgets.py                #   btn/sep/lbl/add_shadow/Badge/StatCard (dùng chung)
│   ├── sidebar.py                #   Sidebar (nav + trạng thái server + master switch)
│   ├── profile_dialog.py         #   ProfileDialog (thêm/sửa profile)
│   ├── main_window.py            #   MainWindow (ráp mọi thứ, khởi động server embedded)
│   └── pages/
│       ├── profiles_page.py      #   ProfilesPage (dashboard + bảng profile)
│       ├── extensions_page.py    #   ExtensionsPage
│       ├── logs_page.py          #   LogsPage
│       └── settings_page.py      #   SettingsPage (2026-07-17) — settings CỤC BỘ có thể sửa
│                                  #     (GET/PATCH /api/selenium/local_settings) + backlog/
│                                  #     dispatcher state read-only (GET .../settings_debug)
├── selenium_flow.py            # Entry point MỎNG (tách từ ~3729 dòng, xem 11.13) — chỉ
│                               #   re-export `sf.xxx` (PORT/pm/SeleniumFlowWorker/run_server/...)
│                               #   từ package server/ + `if __name__=='__main__': run_server()`
├── server/                     # Package server thật (tách từ 1 file selenium_flow.py, xem 11.13)
│   ├── config.py                #   PORT, PROFILES_DIR, FLOW_SERVER, settings mặc định, Flask `app`
│   ├── managers.py               #   _ExtensionManager (em), _ProfileManager (pm), migrations
│   ├── chrome_utils.py            #   Detect Chrome/portable, build options, attach driver
│   ├── state.py                    #   State runtime thuần (dict/set) — tách để tránh circular import
│   ├── local_settings.py            #   Settings CỤC BỘ (2026-07-17) — get/update/reset, đọc/ghi
│                                     #     ../local_settings.json, KHÔNG còn đồng bộ từ FLOW_SERVER
│   ├── i18n_texts.py                #   get_i18n_texts() (2026-07-17) — đọc/cache ../i18n_texts.json
│   ├── worker.py                    #   SeleniumFlowWorker (~2300 dòng, giữ nguyên 1 class/file)
│   ├── dispatcher.py                #   Registry + master switch + auto-scale + dispatcher loop
│   ├── routes.py                     #   Toàn bộ @app.route (25 endpoint /api/selenium/*, gồm
│                                       #     settings_debug + local_settings [GET/PATCH] +
│                                       #     local_settings/reset thêm 2026-07-17)
│   └── app_server.py                 #   shutdown_all_workers(), run_server()
├── local_settings.json         # Settings cục bộ máy này (tự tạo lúc đầu tiên sửa qua GUI/API,
│                               #   KHÔNG commit git — xem .gitignore) — xem server/local_settings.py
├── i18n_texts.json             # Biến thể text theo ngôn ngữ cho DOM automation (2026-07-17,
│                               #   COMMIT git — default dùng chung) — xem server/i18n_texts.py
├── selenium_profiles.html     # Web UI (serve bởi selenium_flow GET /)
├── start.bat                   # python main.py (khởi động CHÍNH THỐNG, xem 11.10)
├── logs/
│   └── profile_{id}.log      # Log file per-profile (rotate giữ LOG_KEEP entries)
├── data/
│   ├── profiles/
│   │   ├── list_profile/     # Chrome user-data-dir (session Google)
│   │   │   └── {profile_name}/
│   │   └── chromedriver/
│   │       └── chromedriver.exe
│   └── portables/            # Chrome Portable (optional)
│       └── {n}/App/Chrome-bin/chrome.exe
└── tests/
    ├── _test_textToImage.py  # ✅ PASSED — text-to-image end-to-end (API thuần)
    ├── _test_imageToImage.py # ✅ PASSED — image-to-image end-to-end (API thuần, xem §11.32)
    ├── _test_textToVideo.py  # API explored — cần token hợp lệ
    ├── _diag31.py            # Fetch interceptor — JS .click() button = no-op
    ├── _diag32.py            # KEY DISCOVERY: send_keys(RETURN) triggers API
    ├── _diag33.py            # Capture request body structure
    ├── _diag34.py            # Full request body (với expired tokens)
    ├── _diag34_capture.json  # Saved request bodies
    └── _diag35.py            # Full response confirmed: fifeUrl format
```

---

## 9. DB của client_tool (riêng, không dùng chung với server_gemini_flow)

```sql
selenium_profiles:
  id, profile_name, display_name, profile_dir, account_email,
  account_password, -- PLAINTEXT — dùng cho _ensure_google_login() tự đăng nhập
                     -- lại sau khi "Làm mới profile" xoá session (§11.31)
  project_url,    -- PHẢI là /project/{uuid} cho image generation
  task_mode,      -- 'all' | 'image_only' | 'video_only'
  worker_mode,    -- 'api' | 'dom' | 'gemini' | 'chatgpt' | 'gemini_video' (§11.5/§11.25/§11.27)
  proxy_server,   -- Proxy RIÊNG của profile (§11.47). Rỗng = đi thẳng.
                  -- host:port | scheme://host:port | scheme://user:pass@host:port |
                  -- user:pass@host:port | host:port:user:pass
  status,         -- 'idle' | 'processing' | 'offline'
  current_task_id, worker_pid, last_used, enabled, notes

selenium_extensions:
  id, ext_name, ext_path, enabled

profile_logs:
  id, profile_id, level, message, created_at
  -- Max LOG_KEEP=500 entries per profile (DB) + file logs/profile_{id}.log
```

---

## 10. Config (.env)

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `SELENIUM_PORT` | `13445` | Port Flask |
| `SELENIUM_PROFILES_DIR` | `../data/profiles` | Root dir cho profile data |
| `CHROMEDRIVER_PATH` | `../data/profiles/chromedriver/chromedriver{.exe}` | Path chromedriver (tên theo OS). Nếu không tồn tại → tự dò trong PATH / `/snap/bin/chromium.chromedriver` / `/usr/bin/chromedriver`; không thấy nữa mới để Selenium Manager tự tải (chỉ x86_64). |
| `CHROME_BINARY` | *(tự dò)* | Đường dẫn binary Chrome/Chromium. Linux/macOS để trống sẽ tự dò `google-chrome`/`chromium` trong PATH + vị trí phổ biến. |

**⚠️ aarch64 (ARM64):** Google KHÔNG có bản Chrome lẫn chromedriver cho linux-arm → Selenium Manager & `undetected_chromedriver` đều vô dụng. Bắt buộc dùng **chromium + chromedriver do snap/hệ điều hành cung cấp** (version phải khớp nhau); code tự dò `/snap/bin/chromium` + `/snap/bin/chromium.chromedriver` và bỏ qua `uc`. Với chromium **snap**: cần interface `home`/`removable-media` được kết nối (`snap connections chromium`) để truy cập user-data-dir; thư mục `data/profiles` phải thuộc sở hữu user chạy worker (`chown -R $USER data`), không phải root.
| `FLOW_API_URL` | `http://localhost:13443` | URL đến server_gemini_flow — đọc bởi `selenium_flow.py` |
| `SELENIUM_FLOW_API_URL` | `http://localhost:13445` | URL control-API của chính `selenium_flow.py` — đọc bởi `main.py` (client desktop) |
| `SELENIUM_POLL_SECS` | `8` | Interval heartbeat |
| `SELENIUM_LOG_KEEP` | `500` | Max log entries in DB per profile |
| `CHROME_DEBUG_PORT_BASE` | `9300` | Port debug base (profile 1 → 9301, ...) |
| `CHROME_PORTABLE_DIR` | `../data/portables` | Thư mục Chrome Portable |

---

## 11. Lưu ý kỹ thuật quan trọng

### 11.1 Tại sao send_keys(RETURN) mà không click button?

Flow project page dùng **Slate.js** cho `[contenteditable="true"]`. Khi Selenium gõ `send_keys(prompt)`, Slate internal state được cập nhật. Khi gõ `Keys.RETURN`, Slate's `onKeyDown` handler đọc internal state → gọi `generate()`.

Button `arrow_forwardTạo` dùng React `onClick` đọc **React component state** — state này lag sau Slate internal state, nên `.click()` hoặc JS `button.click()` là no-op. ❌

Cách đúng: `ce.send_keys(Keys.RETURN)` ✅

### 11.2 recaptchaToken cho image generation

Server Python KHÔNG thể tự tạo token này bằng thuật toán/HTTP thuần — gọi trực tiếp `batchGenerateImages` không kèm token hợp lệ trả HTTP 403 `"reCAPTCHA evaluation failed"`.
→ 2 cách lấy token hợp lệ, cả 2 đều CHẠY TRONG CHÍNH BROWSER (không phải Python tự sinh):
  1. **UI-driven** (Selenium type+Enter) — browser TỰ GẮN token khi user/Selenium trigger hành động thật trên trang.
  2. **Direct API** (`_get_fresh_recaptcha(action)`, 2026-08-12 — xem §11.32/§11.33) — `execute_async_script()` gọi thẳng `window.grecaptcha.enterprise.execute(siteKey, {action})` NGAY TRONG CONTEXT trang đang mở (không phải type/click gì cả) để tự mint 1 token mới, action khớp đúng thao tác (`'IMAGE_GENERATION'`/`'VIDEO_GENERATION'`). **Cùng JS với `tests/utils/flow_session.py::RECAPTCHA_FETCH_JS`** — callback `done` PHẢI là `arguments[arguments.length-1]` (Selenium nhét vào cuối; bản worker cũ gán `done=arguments[0]` khiến siteKey bị hiểu thành `'VIDEO_GENERATION'` → `Invalid site key`). POST aisandbox qua `_post_aisandbox()` — **`curl_cffi` impersonate Chrome, KHÔNG proxy** (page `fetch()` CORS fail `TypeError: Failed to fetch` nếu set `x-browser-validation`/`sec-*` từ JS — bug thật 2026-08-12 18:52 uploadImage; `_test_ingredientToVideo.py` cùng ngày cũng chỉ xong nhờ curl_cffi). Header POST phải có fingerprint ExtraInfo (`x-browser-validation`/`x-client-data`) + `sec-ch-ua` từ Chrome đang chạy — `_sync_project_apis()` chờ checkApp+batchLog giống `_test`, không port SOCKS. KHÔNG dùng `requests` Python thuần.

### 11.3 Endpoint mới image (2026-06-25)

```
DEPRECATED (404): POST https://aisandbox-pa.googleapis.com/v1:batchGenerateImages
MỚI (hoạt động):  POST https://aisandbox-pa.googleapis.com/v1/projects/{projectId}/flowMedia:batchGenerateImages
```

Endpoint mới yêu cầu `recaptchaToken` → chỉ gọi được từ browser context.  
Response: `media[].image.generatedImage.fifeUrl` = CDN URL trực tiếp.

### 11.4 CDN URL format

```
https://flow-content.google/image/{UUID}?Expires={timestamp}&KeyName=labs-flow-prod-cdn-key&Signature={sig}
```

- Public accessible (không cần session cookie)
- Hết hạn theo `Expires=` (thường vài giờ)
- Server phải download ngay sau khi nhận URL

### 11.5 project_url PHẢI là /project/{uuid} — tự bấm "New project" nếu chưa có (2026-07-16)

`_ensure_flow_project()` check `'/project/' in current_url`. Nếu `project_url` không chứa `/project/` → navigate về `FLOW_PROJECT_URL` (trang chung `labs.google/fx/.../tools/flow`, không có CE).

**Trước đây** giả định Google Flow tự tạo/redirect sang project mới khi vào trang chung — **không còn đúng nữa**. Trang chung giờ đứng yên, phải bấm nút "New project" thủ công mới có project (và Slate CE để gõ prompt). 2 helper mới:

- `_click_new_project_button(timeout=15)` — tìm nút qua `textContent` chứa `"New project"` (KHÔNG dùng CSS class, class hash tự sinh bởi styled-components và đổi mỗi lần Google deploy lại UI), bấm bằng `_fire_click()`.
- `_wait_for_project_url(timeout=30)` — poll `current_url` tới khi có `/project/{uuid}`, trả `''` nếu timeout.

`_ensure_flow_project()`: sau navigate, nếu URL vẫn thiếu `/project/` → gọi `_click_new_project_button()` rồi `_wait_for_project_url()`, lưu URL mới đè lên `project_url` qua `pm.update()`. `_reset_flow_project()` (đường hồi phục khi project lỗi liên tục — xem 11.8 kế tiếp) dùng lại đúng 2 helper này thay vì tự poll URL riêng.

**⚠️ 2 helper riêng cho 2 đường code KHÔNG dùng chung:** `_ensure_flow_project()` chỉ dùng bởi API mode (`_generate_image_via_ui`). Đường **DOM mode** (`_run_tasks_batch` batch-submit, `_run_task_dom` single-task, và `run()` lúc khởi động worker cho cả 2 mode) dùng 1 helper navigate KHÁC — `_ensure_flow_page()` — là implementation song song, không gọi `_ensure_flow_project()`. Bản fix đầu tiên (2026-07-16) chỉ sửa `_ensure_flow_project()` và bỏ sót `_ensure_flow_page()`, phát hiện qua `client_tool/tests/_test_dom_batch.py` (gọi thẳng `_run_tasks_batch()`, lộ ra thiếu bước bấm "New project"). Đã fix `_ensure_flow_page()` theo đúng logic tương tự (tái dùng `_click_new_project_button`/`_wait_for_project_url`) — nếu sau này còn thêm đường navigate mới, nhớ áp dụng cả 2 chỗ hoặc gộp lại thành 1 helper chung.

### 11.6 fetch interceptor và extension

Extensions load ở `document_start` có thể patch `window.fetch` trước. `_install_gen_interceptor()` chạy SAU page load → override lại fetch wrapper của extension → bắt được response.  
**Không gọi interceptor trước page load.**

### 11.7 Worker machine_code

```python
self.machine_code = f'selenium-{profile["id"]}'
```

Machine code này đăng ký trong `machines_media` table của `server_gemini_flow`. Task được assign `WHERE machine_code = 'selenium-{id}'`.

### 11.8 Dashboard live: task đang chạy / lỗi / sleep countdown (2026-07-16)

`main.py` (`ProfilesPage`) trước đây chỉ biết `current_task_id`/`status` (cột DB, qua `pm.list()`) — không thấy được prompt đang chạy là gì, đã lỗi bao nhiêu lần, hay "ngủ" (escalation, xem 11.5 comment block ở `_handle_task_error`) tới lúc nào. Giờ mỗi `SeleniumFlowWorker` giữ thêm state **CHỈ SỐNG TRONG PHIÊN CHẠY** (không persist DB, reset về 0 khi worker restart — cùng triết lý với `_consecutive_errors`/`_refresh_count` có sẵn):

- `_current_task` (dict `id`/`mode`/`prompt`/`started_at`) — set bởi `_task_start(task_id, mode, prompt)`, gọi ngay sau `pm.set_status(..., 'processing', ...)` ở CẢ 3 nơi 1 task thật sự bắt đầu chạy: `_run_task_dom`, `_run_task_api`, và **2 điểm** trong `_run_tasks_batch` (lúc submit từng task + lúc polling/download từng task — batch mode xử lý tuần tự nên "current task" vẫn có nghĩa dù nhiều task được submit liên tiếp).
- `_task_done_count` / `_task_error_count` — tăng trong `_handle_task_success()`/`_handle_task_error(message)`.
- `_last_error_msg` / `_last_error_at` — set trong `_record_error(message)` mà `_handle_task_error()` gọi ở bước đầu — cũng dùng riêng cho 2 nhánh lỗi phụ trong `_run_tasks_batch` (submit thất bại, không tile nào xuất hiện). **Cập nhật 2026-07-16:** `_record_error()` giờ KHÔNG còn "thuần bookkeeping" nữa — đã gánh thêm việc kiểm tra ngưỡng time-window (xem 11.9), trả `bool`. Chỉ escalation LIÊN TIẾP (refresh → project mới, trong `_handle_task_error`) là vẫn tách riêng khỏi `_record_error`.

`SeleniumFlowWorker.live_info()` snapshot toàn bộ state trên thành dict (`liveTaskId/Mode/Prompt/StartedAt`, `taskDoneCount`, `taskErrorCount`, `consecutiveErrors`, `lastErrorMsg/At`, `sleepUntil`). `GET /api/selenium/status` (`get_status()`) merge dict này vào profile khi `pid in _workers`; khi `status=='sleeping'` (worker thread đã thoát hẳn — escalation dừng loop, xem comment "Ghi vào dict module-level" ở `_handle_task_error`), lấy `sleepUntil` từ `_sleep_until_by_pid` module-level thay vì hỏi object worker (đã bị reap).

`ProfilesPage` (`main.py`) đọc các field trên mỗi lần poll (`REFRESH_MS=5s`): 4 StatCard mới — "Rảnh" (worker chạy nhưng không có `liveTaskId`, tức sẵn sàng nhận task ngay lần heartbeat kế tiếp — đây chính là "số task tổng có thể nhận" dựa vào số profile rảnh), "Đang xử lý task", "Đang ngủ", "Lỗi (phiên chạy)" (tổng `taskErrorCount` — nhắc lại: KHÔNG phải lỗi lịch sử, chỉ tính từ lúc worker start gần nhất). Cột bảng "Task hiện tại" hiện `#id · mode · Ns` + tooltip prompt đầy đủ; cột "Lỗi" mới (badge `total (N liên tiếp)` + tooltip lỗi gần nhất); badge "Trạng thái" khi sleeping hiện countdown `mm:ss` + tooltip giờ thức dậy.

### 11.9 Ngưỡng "N lỗi trong M phút" + tự mở/đóng profile veo3 theo backlog (2026-07-16)

**Time-window sleep — SONG SONG với escalation liên tiếp (11.5/11.8), không thay thế.** Chuỗi cũ (`_handle_task_error`): lỗi liên tiếp ≥ `error_count_before_refresh` → refresh trang → sau `refresh_count_before_new_project` lần refresh → tạo project mới → sau 2 lần vẫn lỗi mới `sleep`. Vấn đề: `_consecutive_errors` bị reset về 0 sau MỖI lần refresh — lỗi "chập chờn" (lỗi → refresh tạm ổn → lỗi lại, lặp mãi) không bao giờ đạt ngưỡng liên tiếp, worker cứ refresh vô hạn không bao giờ ngủ. Fix: `_record_error(message)` (được `_handle_task_error()` gọi ở bước đầu, VÀ gọi trực tiếp ở 2 nhánh lỗi phụ trong `_run_tasks_batch`) giờ tự giữ `self._error_timestamps` (list epoch time), mỗi lần lỗi append + tự prune mốc cũ hơn `error_window_minutes` phút; nếu số lỗi còn lại trong cửa sổ ≥ `error_window_max_errors` → cho profile `sleeping` NGAY (dùng chung `error_sleep_secs`, set `_sleep_until`/`_sleep_until_by_pid` y hệt nhánh sleep cũ), reset cửa sổ, trả `True`. `_handle_task_error()` return `True` ngay nếu `_record_error()` báo đã ngủ — bỏ qua nốt phần escalation liên tiếp. 2 field setting mới (server gửi qua `_server_settings`, xem `_DEFAULT_SERVER_SETTINGS`): `error_window_minutes` (mặc định 10), `error_window_max_errors` (mặc định 5) — sửa ở `MachinesPage.jsx` (nhóm setting veo3 sẵn có) hoặc `PUT /api/media/settings`.

**Tự mở/đóng profile veo3 theo backlog task thực tế.** Trước đây `_desired_veo3` (tập profile "muốn chạy") CHỈ được nạp khi user bấm nút Start — dispatcher (`_veo3_dispatcher_tick`) chỉ lo giới hạn concurrency, không tự quyết định NÊN mở hay đóng. Giờ có thêm `_auto_scale_veo3_tick()` (gọi mỗi 10s trong `_veo3_dispatcher_loop`, ngay TRƯỚC `_veo3_dispatcher_tick()`):

1. `_fetch_pending_by_mode()` (cache 8s) gọi `GET /api/media/pending_by_mode` — server trả `{byMode:{mode:count}, imageTotal, videoTotal, total}` từ `tasks_media_flow WHERE status='pending' GROUP BY mode`. **⚠️ Bug đã vá cùng ngày:** fallback khi fetch lỗi TRƯỚC ĐÂY trả thẳng cache cũ vô thời hạn — nếu backend (13443) tắt hẳn sau khi cache từng có `total>0`, auto-scale tin "còn task" MÃI MÃI và cứ mở/giữ mở profile dù không còn ai gửi task (đã xác nhận trực tiếp: 2 process `selenium_flow.py` mồ côi vẫn tick nền dù server/frontend đã tắt). Fix: `_pending_fail_streak` đếm số lần fetch lỗi LIÊN TIẾP — sau `_PENDING_MAX_STALE_FAILS=3` lần (~30s) thì coi backlog=0 thay vì tin cache cũ, để auto-scale tự đóng bớt (bước 4 bên dưới) thay vì giữ mở dựa trên tín hiệu lỗi thời.
2. Với MỌI profile `enabled=1`, `worker_mode` ∈ (`api`,`dom`), có `project_url`: `_profile_veo3_eligible(profile, pending)` so `imageTotal`/`videoTotal`/`total` với `profile.task_mode` (`image_only`/`video_only`/`all`).
3. Có việc khớp → thêm `pid` vào `_desired_veo3` (dispatcher tick kế tiếp tự mở, vẫn tôn trọng `max_concurrent_veo3_profiles` như cũ — không đổi cơ chế cấp slot).
4. Hết việc khớp → bỏ `pid` khỏi `_desired_veo3`; nếu đang chạy (`pid in _workers`) và KHÔNG có task dở dang (`w._current_task is None`) → `_stop_worker(pid)` đóng **NGAY LẬP TỨC** (task đang chạy vẫn được hoàn tất trước, chỉ không nhận task mới — không có grace period, đây là lựa chọn rõ ràng của user, đổi lại chấp nhận rủi ro mở/đóng liên tục nếu task đến rải rác/ngắt quãng).

**Phạm vi: đoạn logic CỤ THỂ ở trên (`_profile_veo3_eligible`/`_desired_veo3`/`_veo3_dispatcher_tick`'s slot-promotion) CHỈ áp dụng veo3 (`worker_mode` api/dom).** Gemini dùng CƠ CHẾ RIÊNG (không tái dùng `_desired_veo3`, vì gemini start/stop TRỰC TIẾP qua `_start_worker()`, không có khái niệm "waiting"/slot) nhưng **CŨNG phản ứng theo backlog thật** — xem §11.20 (2026-07-19): bảng `gemini_pending_requests` (mirror `tasks_media_flow`) + `GET /api/gemini/pending_count` (mirror `pending_by_mode`) + `_auto_scale_gemini_tick()` (mirror tinh thần `_auto_scale_veo3_tick()` ở trên, chỉ khác cơ chế cấp slot). Lịch sử: bản đầu (2026-07-18) từng "giữ sẵn N máy luôn online" (không phản ứng theo gì cả) vì lúc đó CHƯA có bảng backlog cho gemini — đã thay thế hoàn toàn sau khi user làm rõ muốn hành vi phản ứng-theo-nhu-cầu giống hệt veo3.

**Đòn bẩy tắt auto-scale cho 1 profile cụ thể:** set `enabled=0` (cột `selenium_profiles.enabled`, `schema.sql` dòng 17 — tồn tại từ đầu nhưng trước giờ không có code nào đọc, giờ là cờ opt-out chính thức).

**Cập nhật (2026-07-17) — có toggle UI + chặn thật sự CẢ Start thủ công, không chỉ auto-scale:** Trước đây field `enabled` CHỈ auto-scale tôn trọng (bỏ qua profile tắt khi xét thêm vào `_desired_veo3`) — bấm nút Start thủ công vẫn mở được Chrome bình thường cho 1 profile "tắt", không nhất quán, và không có toggle UI (chỉ sửa được qua `PATCH` API thủ công). Giờ: `ProfileDialog` có checkbox "Bật — cho phép chạy" (field "Trạng thái"); `routes.py::start_worker()` chặn ngay `403 profile_disabled` nếu tắt; `dispatcher.py::_start_worker()` (điểm chốt DUY NHẤT thực sự mở Chrome) có CÙNG check làm lưới an toàn cho nhánh veo3 (không gọi `_start_worker()` trực tiếp từ route, chỉ add vào `_desired_veo3` rồi để dispatcher tick tự promote); `_auto_scale_veo3_tick()` giờ CHỦ ĐỘNG `discard` khỏi `_desired_veo3` + dừng hẳn (nếu không có task dở dang) khi phát hiện profile vừa bị tắt, thay vì chỉ `continue` bỏ qua (trước đây pid có thể nằm lì trong `_desired_veo3` mãi mãi, dispatcher tick lặp lại thử-và-lỗi `profile_disabled` vô hạn). GUI (`ProfilesPage`) hiện badge "🚫 Đã tắt" (đỏ) ở cột Trạng thái + disable nút Start khi `enabled=0`. Xem CHANGELOG "client_tool: thêm toggle Trạng thái (enabled)...".

**⚠️ Bug nghi vấn — "2 profile phù hợp, server cho phép 2, tool chỉ chạy 1" (2026-07-17, CHƯA XÁC ĐỊNH ĐƯỢC ROOT CAUSE):** User báo profile thứ 2 đứng yên ở `idle` (không phải `waiting`) — nghĩa là chưa từng vào `_desired_veo3`, bug (nếu có) nằm ở bước 2 (`_profile_veo3_eligible`), KHÔNG PHẢI bước cấp slot. 2 khả năng: (a) `task_mode` của profile 2 không khớp loại task đang pending thật (KHÔNG phải bug, chỉ là cấu hình); (b) `pending_by_mode` (bước 1) chỉ đếm `status='pending'`, KHÔNG tính `assigned`/`processing` — nếu profile 1 xử lý đủ nhanh so với chu kỳ check 10s, backlog thật (đã bị "gom" sang assigned/processing) không còn hiện ra ở tín hiệu này, khiến profile khác không bao giờ thấy `pending>0`. Đã thêm log chẩn đoán (`[auto-scale] Profile {pid} ... KHÔNG khớp pending hiện tại — image=... video=... total=...`, chỉ log khi có backlog toàn cục nhưng profile cụ thể không khớp) — CHƯA sửa logic (tránh đoán mò), cần log thật lần sau để xác nhận (a) hay (b), xem CHANGELOG "client_tool auto-scale: thêm log chẩn đoán...". Nếu là (b), hướng fix khả dĩ: mở rộng SQL ở `pending_by_mode()` (`backend/routes/admin.py`) đếm thêm `assigned`/`processing`, hoặc giảm chu kỳ check `_veo3_dispatcher_loop` (hiện 10s).

**Bổ sung (cùng ngày) — trang GUI "Cài đặt" để tự kiểm tra settings/backlog:** User yêu cầu tool "show thêm các setting default hoặc server gửi xuống để check" — thay vì chỉ dựa vào log, giờ có thể tự xem trực tiếp: `GET /api/selenium/settings_debug` (mới, `routes.py`) trả `local_defaults`/`server_settings`+tuổi cache/`pending`+tuổi cache+fail streak/`desired_veo3`/`active_workers`/`master_switch_enabled` — dùng chính `_fetch_global_settings()`/`_fetch_pending_by_mode()` nên LUÔN khớp giá trị dispatcher đang thật sự dùng (không phải số liệu tách biệt có thể lệch pha). GUI: tab mới "⚙️ Cài đặt" (`gui/pages/settings_page.py`), 4 card tương ứng 4 nhóm dữ liệu trên, nút "Làm mới". Xem CHANGELOG "client_tool GUI: thêm trang Cài đặt...". Đây chính là công cụ để user tự xác nhận (a) hay (b) ở mục ngay trên — mở tab này lúc profile 2 đang `idle`, xem card "Backlog hiện tại" có `>0` cho đúng loại (`imageTotal`/`videoTotal`) khớp `task_mode` của profile 2 hay không.

**Bổ sung tiếp (cùng ngày) — settings đổi hẳn sang CỤC BỘ, không còn field `server_settings`/`_settings_cache` nữa:** User yêu cầu "default cục bộ không nhận từ server nữa và có thể chỉnh sửa" — `_fetch_global_settings()` (đọc `max_concurrent_veo3_profiles` ở trên) KHÔNG còn gọi `GET {FLOW_SERVER}/api/media/settings` nữa, đọc thẳng `local_settings.get_local_settings()` (file `client_tool/local_settings.json`, sửa qua GUI tab Cài đặt — xem 11.17). **Ảnh hưởng trực tiếp tới bug nghi vấn ở mục trên:** `max_concurrent_veo3_profiles` giờ KHÔNG còn set được từ `MachinesPage.jsx` (web) — mỗi máy client_tool tự set riêng ở tab Cài đặt cục bộ của chính nó. Nếu user trước đó set "2" trên web nghĩ rằng nó áp dụng cho client_tool, đó chính là 1 nguyên nhân khả dĩ THỨ 3 (ngoài (a)/(b) ở trên) cho việc "server cho phép 2 nhưng tool chỉ chạy 1" — giờ đã loại bỏ hẳn nguồn nhầm lẫn này vì không còn 2 nơi cấu hình cùng 1 giá trị nữa.

### 11.10 Master switch — 1 công tắc bật/tắt nhận task cho CẢ máy (2026-07-16)

Khác với `enabled` (11.9, opt-out per-PROFILE) và `machines_media.status='paused'` (per-MACHINE trên server, chỉ áp dụng sau khi machine đã heartbeat ít nhất 1 lần) — master switch là 1 công tắc DUY NHẤT ở **cấp client_tool** (toàn bộ máy chạy `selenium_flow.py`), điều khiển từ `main.py`, áp dụng cho **MỌI profile kể cả `worker_mode='gemini'`** (không giới hạn veo3 như auto-scale ở 11.9).

- State: `_master_task_intake_enabled` (bool, module-level, **mặc định `False` — đổi 2026-07-17, trước đây `True`**) + `_master_switch_snapshot` (set profile_id) — CHỈ sống trong RAM của `selenium_flow.py`, KHÔNG persist DB/file — restart server luôn về lại `False` (mặc định KHÔNG nhận task, theo yêu cầu user "mặc định khi mở tool sẽ không nhận task" — cho user cơ hội kiểm tra settings/trạng thái trước khi tự bấm BẬT).
- **TẮT** (`_set_master_task_intake(False)`): snapshot `_workers.keys()` hiện tại (cả veo3 lẫn gemini) vào `_master_switch_snapshot`, `_desired_veo3.clear()`, rồi `_stop_worker()` MỌI profile đang chạy — đóng Chrome ngay lập tức (không chờ task dở dang xong — khác hẳn "đóng ngay" ở auto-scale 11.9 vốn còn chờ task hiện tại chạy hết). Đồng thời `_auto_scale_veo3_tick()`/`_veo3_dispatcher_tick()` return sớm (không tự mở lại gì), và `POST /start` trả `423 master_switch_off` (chặn cả bấm Start thủ công) — trong lúc tắt, KHÔNG cách nào (auto lẫn thủ công) mở lại được profile ngoại trừ bật công tắc.
- **BẬT** (`_set_master_task_intake(True)`): **(2026-07-20, ĐÃ SỬA — xem bug bên dưới)** chỉ clear `_master_switch_snapshot` + log, KHÔNG tự start/thêm gì cả. Auto-scale (11.9, `_auto_scale_veo3_tick()`/`_auto_scale_gemini_tick()`) — đã chạy sẵn mỗi 10s trong background loop, chỉ bị chặn bởi đúng cờ `_master_task_intake_enabled` (vừa được set `True` ở đầu hàm) — tự đánh giá LẠI theo backlog THẬT ở tick kế tiếp (≤10s) để quyết định profile nào nên mở, y hệt như khi chưa từng tắt switch.
- **Bug đã sửa 2026-07-20 (bản TRƯỚC "khôi phục mù"):** user báo tạo 2 profile dùng CHUNG 1 email Google — 1 VEO (`api`/`dom`), 1 Gemini. Hiện tại chỉ có backlog Gemini. Bấm "bật nhận task": tool mở LUÔN cả profile VEO dù không có task khớp (nghi mở thêm Chrome vô cớ trên CÙNG account gây xung đột session) → profile Gemini (đang có việc thật) bị đóng theo → task Gemini không được xử lý → VEO vẫn mở dù không việc, vô lý. Root cause: bản cũ của nhánh BẬT lặp qua `_master_switch_snapshot` rồi `_start_worker()`(gemini)/`_desired_veo3.add()`(veo3) NGAY LẬP TỨC cho mọi profile đã từng chạy trước lúc tắt — **không hề gọi lại `_profile_veo3_eligible()`/check backlog thật**, trong khi auto-scale (đã chạy nền sẵn, đủ điều kiện hoạt động ngay khi cờ intake bật) đã tự quản lý MỌI profile theo backlog thật rồi — đoạn "khôi phục mù" hoàn toàn dư thừa, chỉ có tác dụng phụ là mở nhầm profile không có việc. Fix: bỏ hẳn đoạn khôi phục, giao 100% quyết định "profile nào nên chạy" cho 2 hàm auto-scale — verify bằng test cô lập (mock `_workers`/`pm`/`_start_worker`) xác nhận nhánh BẬT giờ 0 lần gọi `pm.get()`/`_start_worker()`, và test end-to-end mô phỏng đúng kịch bản bug (VEO3 không backlog + Gemini có backlog, cả 2 từng chạy trước khi tắt) — sau khi bật switch + chạy tick kế tiếp: chỉ Gemini mở, VEO3 vẫn đóng.
- API: `GET`/`POST /api/selenium/master_switch` (`{enabled: bool}`). `GET /api/selenium/status` trả thêm field `master_switch_enabled` để GUI đồng bộ theo mỗi lần poll (không cần gọi endpoint riêng).
- GUI: `Sidebar` (`main.py`) có 1 card ngay dưới card "Server status" — chấm màu xanh/đỏ + text trạng thái + nút toggle glyph `⏻`. `MainWindow._tick()` gọi `sidebar.set_switch_state(...)` mỗi 5s để phản ánh đúng trạng thái thật trên server, kể cả khi đổi từ nơi khác (gọi API trực tiếp, không qua nút này).

**⚠️ (ĐÃ SỬA 2026-07-16, xem 11.11) Trước đây `selenium_flow.py` là process ĐỘC LẬP với `main.py`** — đóng cửa sổ GUI không chắc tắt được server (nguồn gốc nhiều bug port-conflict/orphan process). Giờ server chạy embedded trong CHÍNH process GUI — đóng GUI luôn tắt hẳn cả 2.

**⚠️ KHÔNG chạy 2 instance `client_tool` song song trên cùng máy (2 Python interpreter khác nhau, vd venv + Python hệ thống).** Dù giờ mỗi instance chỉ còn 1 process (nhờ 11.11), chạy 2 INSTANCE cùng lúc vẫn khiến 2 process (mỗi cái tự embed server riêng) cùng cố bind port 13445 — `_start_embedded_server_worker` (11.11) sẽ phát hiện và từ chối mở đè lên NẾU instance kia mở trước và đã bind xong, nhưng nếu 2 instance khởi động gần như đồng thời (race condition — đã tái hiện trực tiếp lúc test 11.11) thì cả 2 có thể cùng vượt qua health-check (chưa ai kịp bind) rồi cùng cố `app.run()`, 1 cái thắng 1 cái treo — vẫn để lại state mạng rối loạn y hệt trước (`CLOSE_WAIT` tồn đọng, request timeout ngẫu nhiên). Luôn khởi động qua `client_tool/start.bat` (dùng `../venv/Scripts/python.exe`) — KHÔNG chạy thêm qua nút Run của IDE nếu IDE đang trỏ tới 1 Python interpreter khác, và luôn đóng hẳn instance cũ trước khi mở instance mới.

### 11.11 Gộp `main.py` + `selenium_flow.py` thành 1 process (2026-07-16)

Theo yêu cầu rõ ràng của user ("gộp chung, GUI tắt thì nó phải tắt, không cần độc lập") — đổi hẳn kiến trúc: KHÔNG còn `selenium_flow.py` là subprocess riêng do `main.py` spawn ra (`subprocess.Popen`) nữa.

- `selenium_flow.py`: khối `if __name__=='__main__':` refactor thành hàm `run_server()` (migrate DB columns → start `_veo3_dispatcher_loop` thread → `app.run()`), giữ `if __name__=='__main__': run_server()` nên `python selenium_flow.py` standalone VẪN chạy được y hệt trước (không breaking, dùng được cho debug/test độc lập nếu cần — xem `tests/_test_dom_batch.py` kiểu import module). Hàm mới `shutdown_all_workers()` — dừng mọi worker (`_stop_worker` từng pid) + đóng mọi login browser (`_login_drivers`), gọi lúc process sắp thoát hẳn để đóng Chrome sạch thay vì để orphan.
- `main.py`: `import selenium_flow as sf` thẳng làm module (2 file cùng thư mục, không cần chỉnh `sys.path`). `MainWindow._start_embedded_server_worker()` (thay `_start_server`/subprocess cũ): health-check port trước — nếu ĐÃ có ai lắng nghe, từ chối tự start (báo lỗi rõ, không cố "dùng nhờ" process lạ); nếu trống, chạy `sf.run_server()` trong 1 `threading.Thread(daemon=True)` của CHÍNH process GUI, rồi poll `/api/selenium/health` như cũ để biết khi nào sẵn sàng. Toàn bộ `api()`/`ApiCall`/mọi page (`ProfilesPage`, `ExtensionsPage`, ...) KHÔNG đổi gì — vẫn gọi HTTP `localhost:13445` y hệt trước, chỉ khác là server giờ là 1 thread trong cùng process thay vì 1 process khác.
- `MainWindow.closeEvent()`: bỏ hẳn dialog 3 lựa chọn cũ (Yes/No/Cancel "tắt server khi đóng?") — không còn khái niệm "để server chạy nền" nữa. Nếu có profile đang chạy (`len(sf._workers) > 0`) thì hỏi xác nhận đơn giản (Yes/Cancel, không có lựa chọn thứ 3), Yes → `sf.shutdown_all_workers()` rồi CHỜ TỐI ĐA 5s (block main thread, chấp nhận được vì là hành động thoát app) để worker thread tự thoát/đóng Chrome trước khi cho phép Qt thoát hẳn — cần thiết vì daemon thread không đảm bảo chạy xong nếu process chết đột ngột giữa chừng (khác lúc gọi qua API bình thường, lúc đó process vẫn sống nên chờ bao lâu cũng được).
- **Hệ quả:** không còn cách nào "để server chạy nền, đóng GUI" — đúng ý muốn ban đầu. Cũng loại bỏ hẳn 1 lớp bug: quên xác nhận tắt server ở dialog cũ (hoặc GUI khởi động lúc server đã chạy sẵn từ trước, `self._server_proc` không được set) khiến server mồ côi sống mãi.
- **Giới hạn đã biết:** vẫn có thể gặp race condition port-conflict nếu mở 2 INSTANCE (2 lần chạy `main.py`) gần như cùng lúc — xem cảnh báo ngay phía trên. Chưa verify end-to-end đầy đủ trên máy thật do lúc test gặp đúng tình huống 2 instance chạy song song (1 do IDE tự mở) — mới xác nhận `py_compile` + `import main` (không launch QApplication) pass.

**⚠️ Bug đã gặp + fix (cùng ngày):** GUI hiện "không kết nối được server" trong khi log rõ ràng cho thấy worker (Chrome, DOM automation, xử lý task) đang chạy bình thường. Root cause: `app.run()` mặc định Werkzeug XỬ LÝ TỪNG REQUEST MỘT (single-threaded) — worker DOM mode tự động hoá nặng (tight loop + `execute_script` liên tục, giữ GIL lâu) khiến request health-check/status từ GUI (poll mỗi 5s) bị xếp hàng chờ, timeout dù server vẫn sống. Fix: `run_server()` thêm `threaded=True` (Werkzeug tự spawn 1 thread/request) + `MainWindow` thêm debounce `_server_err_streak` (chỉ hiện "offline" sau `_SERVER_ERR_STREAK_THRESHOLD=3` lần lỗi LIÊN TIẾP, không phải 1 lần). **Đây là tradeoff thật của việc gộp process** — GUI+server+worker giờ chia sẻ 1 GIL, worker bận có thể tạm làm chậm phản hồi HTTP nội bộ. 2 fix trên giảm đáng kể nhưng KHÔNG loại bỏ hoàn toàn — nếu vẫn flaky dưới tải nặng (nhiều profile DOM mode chạy đồng thời), cân nhắc `waitress` (production WSGI server) thay Werkzeug dev server.

**Tái diễn + tăng ngưỡng debounce (2026-07-17, KHÔNG ĐỦ):** Sau khi đổi `_run_task_dom`/`_run_tasks_batch` sang luồng reconcile (§5.7/§11.16, `driver.refresh()` + chờ interceptor 15s + nhiều lệnh resolve URL tuần tự mỗi vòng) — burst hoạt động Selenium/GIL dài hơn HẲN so với luồng tile-polling cũ mà ngưỡng `_SERVER_ERR_STREAK_THRESHOLD=3` ban đầu được hiệu chỉnh cho — user báo lại đúng triệu chứng cũ ("Server Offline" dù profile vẫn chạy). Tăng ngưỡng 3 → 6 (~gấp đôi dư địa chịu đựng).

**Fix triệt để, thay hẳn thiết kế debounce (2026-07-17, cùng ngày):** User báo tăng ngưỡng VẪN chưa đủ, kèm chi tiết quan trọng: lần đầu mở GUI kết nối được, các lần sau hay lỗi hơn. Rà lại và tìm ra lỗ hổng THẬT của thiết kế đếm-liên-tiếp: `_tick()` (main_window.py) bắn 1 `ApiCall` MỚI mỗi 5s (`REFRESH_MS`) BẤT KỂ call trước đã xong chưa — lúc server bận, NHIỀU call chồng lên nhau cùng timeout ~12s gần như ĐỒNG THỜI khi hồi phục/thất bại, khiến "streak" tăng vọt trong 1 nhịp ngắn thay vì đều đặn theo thời gian thực — giải thích vì sao tăng ngưỡng chỉ trì hoãn chứ không sửa được gốc. Xem CHANGELOG "client_tool GUI: fix triệt để false-positive Server Offline...". Đổi hẳn 2 cơ chế trong `main_window.py`: (1) `_status_in_flight` — bỏ qua nhịp `_tick()` nếu call trước chưa xong (van an toàn tự reset sau 30s nếu kẹt) — không còn chồng chất nhiều `ApiCall` trong `QThreadPool` nữa; (2) debounce đổi từ đếm `_server_err_streak` sang đo THỜI GIAN THỰC (`_last_status_ok_at`) — chỉ hiện "offline" nếu ĐÃ ≥25s (`_OFFLINE_THRESHOLD_SECS`) không có lần poll nào thành công, bất kể bao nhiêu lần fail xen giữa. Đồng thời thêm `sys.setswitchinterval(0.001)` ở đầu `run_server()` (`server/app_server.py`) — hạ khoảng CPython cân nhắc nhường GIL (mặc định 5ms), giúp thread Flask chen được vào SỚM HƠN giữa chuỗi lệnh Selenium liên tiếp — giảm chính tần suất/độ dài của đợt "bận" gây ra vấn đề, không chỉ che triệu chứng bằng debounce. Nếu VẪN còn tái diễn sau bộ fix này, bước tiếp theo là `waitress` (production WSGI server) thay Werkzeug dev server, hoặc xa hơn là tách server ra khỏi process GUI trở lại (đảo ngược quyết định gộp process ở 11.11 — chỉ làm nếu user đồng ý, vì đây là quyết định kiến trúc đã chốt trước đó).

**Bug KHÁC phát hiện qua báo cáo tiếp theo — tooltip lỗi bị bỏ phí, không bao giờ tới tay user (2026-07-17, cùng ngày):** User báo triệu chứng khác hẳn: End Task TOÀN BỘ `python.exe` trong Task Manager rồi mở lại app, VẪN thấy "Server Offline" — không thể là GIL contention (không sống sót qua kill-toàn-bộ + mở lại 1 process HOÀN TOÀN MỚI), gợi ý server THẬT SỰ không khởi động được ở lần chạy mới (vd lỗi kết nối DB trong `_ensure_profile_columns()` lúc đầu `run_server()`, hoặc port bị phần mềm KHÔNG PHẢI Python giữ). Rà lại `_start_embedded_server_worker()` phát hiện: `self._server_start_error` ĐÃ capture đúng exception message thật, nhưng KHÔNG BAO GIỜ hiển thị — mọi nhánh gọi `set_server_err()` không truyền tham số, sidebar luôn chỉ hiện text chung chung "Server offline". Không ai (kể cả tôi lúc debug) có cách nào thấy lý do thật. Xem CHANGELOG "client_tool GUI: hiện chi tiết lỗi thật khi Server Offline...". Fix: `Sidebar.set_server_err(detail='')` (mới, `sidebar.py`) hiện `detail` qua **tooltip** trên card trạng thái (hover để xem) — không đổi text chính, tránh vỡ layout với message dài. 3 nhánh gọi trong `main_window.py` giờ đều truyền message cụ thể: startup thất bại → `self._server_start_error` thật (kèm `traceback.print_exc()` ra console cmd cho chi tiết đầy đủ); hết 10s khởi động không exception cụ thể → gợi ý kiểm tra `netstat -ano | findstr :{PORT}`; lỗi lúc ĐANG CHẠY (`_note_server_err`) → message thật từ `requests` ("Connection refused" = server chết hẳn, khác "Read timed out" = server sống nhưng chậm) + số giây đã mất kết nối. **Đây là công cụ CHẨN ĐOÁN, chưa phải fix cho nguyên nhân gốc** (nguyên nhân gốc của báo cáo "kill hết vẫn lỗi" vẫn CHƯA XÁC ĐỊNH được — cần user hover xem tooltip rồi báo lại nội dung).

**🎯 FIX ROOT CAUSE THẬT — QThread bị garbage-collect giữa chừng (2026-07-17, cùng ngày, RẤT CÓ THỂ là nguyên nhân gốc của TOÀN BỘ chuỗi báo cáo "Server Offline" trong ngày):** User gửi console output THẬT ngay sau khi restart backend — Flask CONFIRMED đang chạy, lắng nghe đúng port (`Running on http://127.0.0.1:13445`), NHƯNG GUI vẫn báo offline — loại trừ hẳn hướng "server chưa khởi động được"/DB/port-conflict ở mục ngay trên. Ngay sau "Press CTRL+C to quit" trong console có dòng cảnh báo Qt kinh điển: **`QThread: Destroyed while thread '' is still running`**. `_start_embedded_server()` trước đây:
```python
def _start_embedded_server(self):
    t = QThread(self); t.run = self._start_embedded_server_worker
    t.start()
```
`t` là biến LOCAL — `t.start()` không block, hàm return ngay, `t` mất tham chiếu Python. Dù `QThread(self)` set parent ở tầng Qt, PyQt vẫn có thể garbage-collect wrapper Python của `t` bất cứ lúc nào sau đó — ĐÂY LÀ LỖI KINH ĐIỂN ĐÃ BIẾT của PyQt/PySide: QThread cần giữ tham chiếu Python persistent, chỉ dựa vào Qt parent KHÔNG đủ đảm bảo sống sót. QThread này chạy `sf.run_server()` (block mãi mãi qua `app.run()`, ĐÚNG RA phải sống suốt đời app) — bị destroy giữa chừng có thể làm bất ổn cơ chế signal/slot XUYÊN THREAD của Qt, đúng cơ chế `api()` (`QThreadPool` + signal `done`/`error`) dùng để đưa kết quả poll `/api/selenium/status` về GUI. Khớp hoàn hảo triệu chứng: HTTP request tới Flask vẫn thành công ở tầng network (server thật sự sống), nhưng kết quả không bao giờ tới được callback `on_status`/`on_err` trên main thread vì cơ chế delivery đã hỏng. Fix: lưu vào `self._embedded_server_qthread` — sống suốt vòng đời `MainWindow`, đúng ý đồ thiết kế ban đầu. Xem CHANGELOG "client_tool GUI: FIX ROOT CAUSE THẬT của Server Offline...".

Bug này tồn tại từ lúc gộp process GUI+server (11.11, 2026-07-16) — nghĩa là đã âm thầm gây bất ổn từ TRƯỚC KHI bắt đầu cả chuỗi điều tra debounce/GIL trong ngày. Các fix trước đó (threaded=True, debounce time-based, `sys.setswitchinterval`, tooltip chi tiết lỗi) vẫn HỢP LỆ và ĐÁNG GIỮ (GIL contention khi nhiều Chrome+worker chạy đồng thời là vấn đề THẬT, độc lập với bug này), nhưng có thể KHÔNG PHẢI nguyên nhân chính của phần lớn báo cáo trong ngày — cần user xác nhận: (1) dòng `QThread: Destroyed...` không còn xuất hiện trong console, (2) GUI hiện đúng "Server online" khi Flask thật sự đang chạy.

**Fix mới nhất — tự "reset hết" lúc start thay vì bắt user tự vào Task Manager (2026-07-20):** User báo lại đúng lớp lỗi này lần nữa — "đã tắt hết vẫn thấy server offline hãy làm cho tôi phương án start thì phải reset hết tránh trường hợp này". Xác nhận trực tiếp trên máy user: đóng cửa sổ GUI KHÔNG đảm bảo tiến trình nền phía sau thoát sạch 100% (vd `driver.quit()` treo lúc đóng Chrome, hoặc Windows force-kill cửa sổ khiến `closeEvent()` không kịp chạy) — process cũ sống sót, giữ nguyên port 13445, cảnh báo ở dòng ngay trên (11.10, "KHÔNG chạy 2 instance song song") mô tả ĐÚNG hành vi cũ: `_start_embedded_server_worker()` phát hiện conflict thì CHỈ báo lỗi bắt tự kill tay, không tự dọn gì. Fix: file mới `server/reset_guard.py::kill_stale_client_tool(port)` — gọi NGAY ĐẦU `main()` (trước cả bước đăng nhập), verify process đang giữ port THẬT SỰ là client_tool (không chỉ dựa status 200 — parse JSON, check key `profiles_dir` riêng của route `/api/selenium/health` + field `port` khớp, tránh kill nhầm phần mềm khác tình cờ trả 200 cho mọi path) rồi kill CƯỠNG BỨC cả tiến trình đó lẫn TOÀN BỘ tiến trình con (Chrome/chromedriver mồ côi nó spawn ra, qua `psutil` — mới thêm vào `requirements.txt`) — dọn sạch hoàn toàn trước khi tự start, đúng nghĩa "reset hết" mỗi lần mở app. Nếu phát hiện port bị process LẠ (không phải client_tool) giữ thì KHÔNG tự kill, hiện `QMessageBox.critical` báo rõ thay vì im lặng treo. Xem CHANGELOG "client_tool: tự 'reset hết' lúc start...".

### 11.12 Tách `selenium_gui.py` thành package `gui/`, sau đó đổi tên thành `main.py` (2026-07-16)

Theo yêu cầu user ("chia thành các file nhỏ như 1 project chuyên để dễ quản lý và nâng cấp") — file `selenium_gui.py` gốc (~1820 dòng, mọi thứ dồn 1 chỗ: config, màu/QSS, API client, widget dùng chung, Sidebar, ProfileDialog, 3 page, MainWindow, entry point) tách thành package `client_tool/gui/` — xem cây thư mục §8, chỉ còn lại entry point mỏng ~60 dòng: dựng `QApplication` + `QPalette` (đọc màu từ `gui/style.py`) rồi `MainWindow().show()`. Ngay sau đó (cùng ngày), theo yêu cầu tiếp theo của user ("gộp thành file main.py để chạy và xoá các file không cần thiết nữa") — file entry point mỏng này ĐỔI TÊN từ `selenium_gui.py` → `main.py` (nội dung giữ nguyên, chỉ đổi tên file — `start.bat` cập nhật gọi `python main.py`), toàn bộ comment/docstring tham chiếu tên cũ trong `selenium_flow.py`, `gui/main_window.py`, `tests/*.py`, `docs/*.md` cũng cập nhật theo. Từ giờ **entry point CHÍNH THỐNG duy nhất là `client_tool/main.py`** — `selenium_gui.py` không còn tồn tại.

**Ranh giới module (theo trách nhiệm, không theo kích thước):**
- `config.py`/`style.py`: hằng số thuần (dict màu + f-string QSS), KHÔNG import PyQt6 — import được ở bất kỳ đâu không lo circular.
- `api_client.py`: tầng HTTP DUY NHẤT gọi ra `selenium_flow.py` — mọi page/widget khác gọi qua `api()`/`api_sync()`, KHÔNG tự gọi `requests` trực tiếp (giữ 1 chỗ để sau này đổi cơ chế HTTP, vd thêm retry/timeout chung, chỉ sửa 1 file).
- `widgets.py`: widget KHÔNG mang state nghiệp vụ (`btn`/`sep`/`lbl` là factory function thuần; `Badge`/`StatCard` chỉ nhận data hiển thị qua constructor/`set_value()`, không tự gọi API) — page nào cũng dùng được, không phụ thuộc ngược lại `pages/`.
- `sidebar.py`/`profile_dialog.py`: đủ lớn + đủ độc lập để tách riêng (Sidebar có cả nav lẫn master switch logic, đáng 1 file riêng thay vì nhét chung `main_window.py`).
- `pages/*.py`: mỗi tab UI 1 file, import `ProfileDialog` từ `..profile_dialog` (chỉ `profiles_page.py` cần, do là nơi bấm "+ Thêm profile"/"Sửa profile").
- `main_window.py`: nơi DUY NHẤT `import selenium_flow as sf` (khởi động embedded server + đọc `sf._workers`/gọi `sf.shutdown_all_workers()`) — các `pages/*.py` KHÔNG import `sf` trực tiếp, luôn qua `api()` để tới `selenium_flow.py`, giữ đúng ranh giới "GUI chỉ gọi API, không tự biết chi tiết server" (đúng tinh thần vai trò `main.py` mô tả ở §1).

**Import contract:** `import selenium_flow as sf` (bare, không package-qualified) hoạt động được từ `gui/main_window.py` (nested 1 cấp trong package `gui/`) vì Python tự thêm thư mục của SCRIPT ĐANG CHẠY (`client_tool/`, chứa `main.py`) vào `sys.path[0]` — `sys.path` là global cho cả process, không bị giới hạn theo package hierarchy, nên module nested nào cũng `import selenium_flow` được miễn app khởi động từ đúng `client_tool/main.py`. KHÔNG dùng relative import (`from . import`) để referemce `selenium_flow` vì nó nằm NGOÀI package `gui/` (sibling của `main.py`, không phải submodule của `gui/`).

**Verify đã làm:** `py_compile` toàn bộ file mới + `import main` (không launch QApplication) pass, không NameError/ImportError nào (đã bắt và sửa 1 lỗi thật lúc tách: `QThread` bị đặt nhầm vào nhóm import `PyQt6.QtWidgets` thay vì `PyQt6.QtCore`). **CHƯA launch thật để xem UI** — do lúc test máy user đang có sẵn nhiều process `client_tool` khác chạy (xem cảnh báo 2 instance ở §11.11), tránh gây thêm xung đột port. Cần user tự chạy `start.bat` để xác nhận UI hiển thị đúng, không khác gì trước khi tách.

### 11.13 Tách `selenium_flow.py` (~3729 dòng) thành package `server/` (2026-07-16)

Cùng yêu cầu với 11.12 ("cũng chia nhỏ ra... và xóa luôn cho gọn") áp dụng cho `selenium_flow.py` — file lớn hơn NHIỀU và RỦI RO CAO HƠN hẳn `gui/` vì đây là core automation engine (Selenium/Chrome), không phải UI khai báo thuần — một lỗi tham chiếu sai chỉ lộ ra lúc CHẠY THẬT (mở Chrome, DOM automation), không phải lúc `py_compile`/`import`.

**Ranh giới module:**
- `config.py`: hằng số + logging + Flask `app` instance + `_profile_log()`. **Lưu ý path:** file này nằm ở `client_tool/server/config.py` (2 cấp dưới `client_tool/`, khác bản gốc `client_tool/selenium_flow.py` chỉ 1 cấp) — `_this_dir`/`_ROOT`/`LOGS_DIR` phải tính `Path(__file__).parent.parent` (lên thêm 1 cấp) để vẫn trỏ đúng về `client_tool/`, nếu không `LOGS_DIR` sẽ ra nhầm `client_tool/server/logs/` và `_ROOT` detect sai (không tìm thấy `db.py`).
- `managers.py`: `_ExtensionManager` (em), `_ProfileManager` (pm), `_ensure_profile_columns()`.
- `chrome_utils.py`: detect Chrome/portable, build options, attach driver. Import `selenium`/`undetected_chromedriver` VẪN LAZY bên trong từng hàm (giữ nguyên bản gốc — không đổi thành top-level).
- `state.py`: **CHỈ chứa state runtime thuần** (dict/set: `_workers`, `_login_drivers`, `_desired_veo3`, `_sleep_until_by_pid`, `_settings_cache`, `_pending_cache`, `_master_task_intake_enabled`, `_master_switch_snapshot`, ...), KHÔNG có function nào. Tách riêng khỏi `dispatcher.py` để TRÁNH CIRCULAR IMPORT: `worker.py` cần đọc/ghi `_login_drivers`/`_sleep_until_by_pid`, còn `dispatcher.py` cần `SeleniumFlowWorker` từ `worker.py` để tạo instance mới (`_start_worker`) — nếu state sống chung với `dispatcher.py`, `worker.py` import `dispatcher` và `dispatcher` import `worker` → vòng lặp import, Flask sẽ lỗi lúc khởi động.
- `worker.py`: `SeleniumFlowWorker` (~2300 dòng) — GIỮ NGUYÊN 1 class/1 file, KHÔNG tách nhỏ hơn. Đây là 1 class cố kết chặt (hàng chục method đều thao tác trên cùng `self` state — token, driver, task hiện tại...); tách method ra khỏi class đòi hỏi redesign (mixin/composition), rủi ro cao hơn nhiều so với lợi ích tổ chức file, KHÔNG làm.
- `dispatcher.py`: registry (`_start_worker`/`_stop_worker`/`_reap_dead_workers`) + master switch + auto-scale veo3 + vòng lặp dispatcher nền. Import cả `state` (biến) lẫn `worker.SeleniumFlowWorker` (để tạo instance).
- `routes.py`: toàn bộ `@app.route(...)` (22 endpoint).
- `app_server.py`: `shutdown_all_workers()` + `run_server()` — điểm vào DUY NHẤT, `import . routes as _routes  # noqa: F401` ở đây là BẮT BUỘC (dù không dùng tên `_routes` trực tiếp) để decorator `@app.route` trong `routes.py` thực thi và đăng ký route lên `app` — Flask không tự quét file tìm route, phải import module đó ít nhất 1 lần.

**⚠️ Bẫy quan trọng nhất: `_master_task_intake_enabled` bị REASSIGN, không chỉ mutate.** Toàn bộ state khác (dict/set) chỉ bị mutate in-place (`.pop()`, `.add()`, `[key]=val`, `.clear()`) nên `from .state import x` ở bất kỳ module nào cũng an toàn — object không đổi, chỉ nội dung đổi. NHƯNG `_master_task_intake_enabled` (bool) bị gán lại hoàn toàn qua `global` trong `_set_master_task_intake()` — nếu `dispatcher.py`/`routes.py` làm `from .state import _master_task_intake_enabled` (snapshot giá trị lúc import), sau lần đầu gọi `_set_master_task_intake()` các module đó sẽ đọc MÃI giá trị CŨ — bug im lặng, không lỗi cú pháp, không NameError, chỉ sai logic (master switch tắt/bật không có tác dụng thật). Fix: `dispatcher.py` và `routes.py` đều `from . import state as _state` (import CHÍNH module, không phải tên biến) rồi luôn truy cập qua `_state._master_task_intake_enabled` — module-qualified access luôn đọc giá trị MỚI NHẤT vì tra cứu thẳng vào namespace dict của module `state`, không qua binding cục bộ. Đã viết script test runtime riêng xác nhận: gọi `_set_master_task_intake(False)` rồi đọc lại `_state._master_task_intake_enabled` từ ngoài → đúng `False`, xác nhận cross-module mutation hoạt động.

**Bug thật bắt được lúc tách (đã sửa):** `chrome_utils.py` và `managers.py` dùng `Path(...)` (từ `pathlib`) nhưng quên thêm `from pathlib import Path` vào header — `py_compile`/`import` KHÔNG bắt được lỗi này (vì `Path` chỉ được gọi lúc HÀM CHẠY, không phải lúc module load) — chỉ lộ ra khi gọi thật qua `app.test_client().get('/api/selenium/health')` → `NameError: name 'Path' is not defined` bên trong `_detect_chrome_portables()`. Đây là lý do phải test bằng Flask test client thay vì chỉ tin `py_compile`/`import` khi tách file có gọi hàm thật.

**Verify đã làm (kỹ hơn 11.12 do rủi ro cao hơn):**
1. `py_compile` toàn bộ file mới.
2. Full import chain: `import selenium_flow as sf` (từ `client_tool/`) VÀ `import main` — cả 2 đều phải hoạt động vì `gui/main_window.py` cần `sf.xxx`.
3. `app.url_map.iter_rules()` xác nhận đủ 23 route (22 tự định nghĩa + 1 `/static` mặc định Flask) — chứng minh mọi `@app.route` từ `routes.py` đã đăng ký đúng lên `app` (không bị bỏ sót do quên import `routes`).
4. Gọi THẬT qua `app.test_client()`: `/api/selenium/master_switch`, `/api/selenium/health` (exercises `chrome_utils.py` + path resolution), `/api/selenium/portables`, `/api/selenium/profiles` (round-trip DB THẬT qua `managers.py`, trả đúng số profile hiện có) — tất cả 200 OK sau khi sửa bug `Path`.
5. Script test runtime riêng cho master switch cross-module mutation (mục trên) — pass.

**CHƯA test được và KHÔNG THỂ test được từ môi trường này:** hành vi automation THẬT của `SeleniumFlowWorker` — mở Chrome, capture token, gõ prompt, DOM polling, v.v. Toàn bộ logic bên trong class chỉ được RELOCATE (di chuyển nguyên vẹn), không sửa 1 dòng logic nào, nên rủi ro chủ yếu nằm ở tầng import/wiring (đã verify kỹ ở trên) chứ không phải ở chính logic automation — nhưng vẫn cần user tự chạy `main.py` + Start 1 profile thật để xác nhận không có regression trước khi yên tâm hoàn toàn.

**⚠️ Bug thật đã xảy ra sau khi báo "verify xong" — bài học quan trọng (2026-07-16):** User chạy thật `main.py` → crash ngay `NameError: name 'C' is not defined` trong `gui/main_window.py::_build_ui()` (thiếu `from .style import C`, sót lại từ lúc tách 11.12). Đây là ví dụ THỰC TẾ cho đúng giới hạn đã cảnh báo ở trên: `py_compile`/`import module` KHÔNG bắt được tên thiếu import nếu tên đó chỉ dùng BÊN TRONG 1 method — import module chỉ nạp định nghĩa class, không gọi `__init__`/`_build_ui()`. Chạy `python -m pyflakes` (cài `pip install pyflakes`, phân tích AST tĩnh, không cần chạy code) trên toàn bộ file mới/sửa sau đó phát hiện THÊM hàng loạt lỗi tương tự đang ẩn trong `server/routes.py` (các route ít dùng như `/open`/`/logs`/`/logfile`/`/extensions/verify` thiếu `os`/`json`/`time`/`threading`/`serialize_row`/`LOGS_DIR`/`_profile_log`/`_build_chrome_options`/`_usable_profile_dir`/`_make_service`) — toàn bộ đã sửa, xem `CHANGELOG.md` 2026-07-16 "FIX: hàng loạt lỗi import thiếu...". **Từ giờ, quy trình verify sau khi tách/di chuyển code PHẢI có bước `python -m pyflakes <files>`** — không chỉ `py_compile` + `import` như 2 đợt tách trước, vì 2 bước đó chỉ phủ được code chạy NGAY LÚC MODULE LOAD, bỏ sót mọi nhánh bên trong hàm/method chưa từng được gọi tới lúc test.

### 11.14 `_cdp_click_el()` thiếu `mouseMoved` + field `buttons` — nghi nguyên nhân "picker không mở sau 10s" (2026-07-16)

User báo log thực tế `[error] [BATCH] Submit task #2630 lỗi: [textToImage] picker không mở sau 10s (img 1)` xảy ra THƯỜNG XUYÊN (xem `_dom_upload_images()` §5.3 — bấm nút icon `add_2` để mở picker ảnh, rồi poll tối đa 10s chờ `'Thêm vào câu lệnh'` button + `'Tìm kiếm thành phần'` input xuất hiện).

So sánh `SeleniumFlowWorker._cdp_click_el()` (dùng bởi 14 call site: `_dom_configure` chọn tab/model/ratio, `_dom_upload_images` mở picker/click "add_2"/tab "Hình ảnh"/"Thêm vào câu lệnh", nhánh gemini menu/upload/textbox/send) với `trustedClickAt()` (`extension_gemini/background.js`) và case `'CC'` (`extensions/background.js`, tự nhận "đã chứng minh hoạt động ổn định" trong comment) — phát hiện khác biệt cụ thể: bản `client_tool` CHỈ gửi `Input.dispatchMouseEvent` `mousePressed`→`mouseReleased`; CẢ 2 EXTENSION đều gửi thêm `mouseMoved` (button:'none') NGAY TRƯỚC đó, và set field `buttons` (1 lúc pressed, 0 lúc released) mà bản `client_tool` bỏ trống. Nghi đây là nguyên nhân: 1 số component React (như nút "+" mở picker) có thể cần tín hiệu hover/pointer-position trước khi chấp nhận click, hoặc Chromium xử lý sự kiện không đầy đủ khi thiếu `buttons`.

Fix: `_cdp_click_el()` gửi đủ 3 event, field GIỐNG HỆT bản đã proven — `mouseMoved(button:'none',modifiers:0)` → `mousePressed(button:'left',buttons:1,clickCount:1,modifiers:0)` → `mouseReleased(button:'left',buttons:0,clickCount:1,modifiers:0)`. Ảnh hưởng tích cực tới TẤT CẢ 14 nơi gọi hàm này, không riêng picker. **Chưa xác nhận 100% root cause** (không tự test lại trên labs.google thật được từ môi trường này) — rủi ro sửa thấp (chỉ thêm event, không đổi hành vi cũ), cần user test lại để xác nhận tần suất lỗi giảm.

**Cập nhật (cùng ngày) — user xác nhận fix trên KHÔNG đủ:** log tiếp theo vẫn báo `picker không mở sau 10s`, nhưng user QUAN SÁT TRỰC TIẾP (nhìn Chrome thật lúc automation chạy) xác nhận **picker THỰC SỰ ĐÃ MỞ trên màn hình** — vậy lỗi không nằm ở bước click nữa, mà ở bước PHÁT HIỆN picker đã mở (detection, không phải action). Rà lại toàn bộ điều kiện visibility trong file → phát hiện root cause khác, cụ thể hơn nhiều: **`X.offsetParent!==null` là cách kiểm tra "visible" SAI cho phần tử `position:fixed`** — theo spec, `offsetParent` LUÔN trả về `null` cho phần tử có `position:fixed` (hoặc `display:none`), BẤT KỂ phần tử đó có đang hiển thị thật trên màn hình hay không. Modal/dialog/picker hiện đại (đặc biệt kiểu "portal" render ra ngoài DOM tree gốc, rất phổ biến ở Material Design/Angular components mà Google hay dùng) HẦU HẾT dùng `position:fixed` — khớp chính xác với triệu chứng "picker mở thật nhưng code không thấy".

Pattern `X.offsetParent!==null` xuất hiện Y HỆT 6 LẦN trong `worker.py`, không chỉ ở bước mở picker: dropdown chọn model (`_dom_configure`), `_find_picker()` helper (dùng để chờ đóng picker), đếm số ảnh trong gallery, tìm `input[type="file"]` bên trong picker, và nút "Thêm vào câu lệnh" lúc submit — nghĩa là TOÀN BỘ luồng upload ảnh qua picker (không riêng bước mở) đều có nguy cơ tương tự nếu container/button dùng `position:fixed`. Fix: thay TẤT CẢ 6 chỗ bằng `X.getClientRects().length>0` — cách kiểm tra "đang render ít nhất 1 CSS box" chuẩn xác hơn, KHÔNG bị ảnh hưởng bởi `position:fixed` (đây là pattern được khuyến nghị bởi các thư viện testing hiện đại như testing-library chính vì lý do này), giữ nguyên ý nghĩa "phần tử có đang hiển thị không" nhưng không có điểm mù của `offsetParent`.

**Đây là fix THỨ 2 cho cùng 1 triệu chứng trong ngày** — fix đầu (`mouseMoved`/`buttons` ở `_cdp_click_el`) VẪN GIỮ NGUYÊN (là cải thiện hợp lệ, độc lập, không sai) nhưng KHÔNG đủ để giải quyết vấn đề vì bug thật nằm ở bước detection chứ không phải bước click. Cả 2 fix cộng lại nhiều khả năng giải quyết dứt điểm, nhưng **vẫn cần user test lại thực tế** — không thể tự verify trên labs.google thật từ môi trường này.

### 11.15 Port thiếu i18n (vi/en) + thiếu retry/polling trong DOM automation — so với `extensions/content/flowMediaGenerator.js` (2026-07-16)

Vẫn cùng chuỗi điều tra "picker không mở sau 10s" (11.14) — log tiếp theo còn cho thấy `_dom_configure()` cũng bị: `DOM: config button not found — skip settings`, `DOM: ratio button "16:9" not found`, `DOM: model "Nano Banana 2" not found in dropdown`. User yêu cầu trực tiếp: so sánh với `extensions/` (Chrome extension gốc, đã chứng minh chạy ổn định) xem gọi UI thế nào. `client_tool/server/worker.py` là bản PORT sang Python/Selenium của `extensions/content/flowMediaGenerator.js` (JS, chạy trong content script) — so sánh kỹ phát hiện 2 lớp gap thật, không phải suy đoán:

**(1) Thiếu i18n.** `flowMediaGenerator.js` có `static I18N` khai báo CẢ 2 biến thể ngôn ngữ cho mọi text đổi theo locale tài khoản/trình duyệt:
```js
static I18N = {
  imageTab:          ['Hình ảnh', 'Images'],
  searchPlaceholder: ['Tìm kiếm thành phần', 'Search assets'],
  addToPrompt:       ['Thêm vào câu lệnh', 'Add to Prompt'],
};
```
`worker.py`'s `_dom_upload_images()` CHỈ hardcode biến thể tiếng Việt ở TẤT CẢ 7 chỗ (mở picker, chờ picker mở, click tab Hình ảnh, tìm search input, đếm gallery, tìm file input, click nút submit). Nếu UI Flow đang hiển thị tiếng Anh (tài khoản/browser locale=en, hoặc Google đổi mặc định), MỌI so khớp text này thất bại — khớp ĐÚNG lời user mô tả trực tiếp: "đã mở picker nhưng không thấy" (không phải lỗi visibility/timing như 11.14, mà do so sai ngôn ngữ hoàn toàn — 2 loại bug ĐỘC LẬP, cả 2 đều có thể cùng góp phần).

Fix: thêm hằng số module-level `I18N_IMAGE_TAB`/`I18N_SEARCH_PH`/`I18N_ADD_TO_PROMPT` (đầu `worker.py`, ngay dưới `LABS_RECAPTCHA_SITE_KEY`). Trong `_dom_upload_images()`, build sẵn `_add_variants_js`/`_image_tab_variants_js` (`json.dumps(variants)` — mặc định `ensure_ascii=True` nên literal JS array chỉ gồm ASCII/`\uXXXX`, an toàn nhúng thẳng vào JS template string, đúng cách code gốc đã tự làm thủ công cho 1 chữ "Hình ảnh" trước đây) và `_search_selector` (nối các `input[placeholder="..."]` bằng dấu phẩy — CSS hỗ trợ multi-selector). Mọi `.includes('text cố định')` đổi thành `variants.some(function(v){return t.includes(v);})`; mọi `input[placeholder="text cố định"]` đổi thành `_search_selector`.

**(2) Thiếu retry/polling.** `configureSettings()` bên JS dùng `waitFor()` (poll có timeout) cho 3 chỗ: config button (4000ms), sub-tab (2000ms), model dropdown menu item (3000ms) — kèm comment gốc: *"máy/mạng chậm hơn có thể cần lâu hơn để render dropdown, sleep cứng gây báo nhầm 'not found' dù menu chỉ chưa kịp render xong"* — nghĩa là đây là bug ĐÃ TỪNG XẢY RA VÀ ĐÃ ĐƯỢC SỬA bên JS, nhưng fix đó CHƯA được port sang `_dom_configure()`: config button dùng single-shot `self._js(...)` không retry; model dropdown dùng `self._sleep(0.8)` (fixed) rồi check DOM đúng 1 lần — Y HỆT bug gốc JS từng gặp.

Fix: thêm helper `_cdp()`-adjacent mới `_wait_js(script, args=(), timeout=4.0, interval=0.15)` (port của `waitFor()`) — poll `self._js(script, *args)` tới khi truthy hoặc hết timeout. Áp dụng: config button (`timeout=4.0`), `find_btn_in_popup()` — helper dùng chung bởi sub-tab/ratio/count — đổi tham số mặc định thành `timeout=2.0` (RỘNG HƠN bản JS gốc, vốn để ratio/count single-shot — nhưng poll thêm không có rủi ro, chỉ tốn thời gian đúng lúc thật sự không tìm thấy), và model dropdown menu item (`timeout=3.0`, thay hẳn `sleep(0.8)` + check 1 lần).

**Verify đã làm:** `py_compile`/`pyflakes`/import chain đều pass. Trích riêng cả 7 JS snippet đã sửa i18n, dựng với giá trị biến thật, chạy qua Node.js (`new Function(body)`) xác nhận CẢ 7 là JavaScript hợp lệ cú pháp — bắt được đúng loại lỗi "quên escape `{`/`}` khi chuyển từ `\"\"\"...\"\"\"` sang f-string" nếu có (không có lần này). **KHÔNG THỂ verify được** liệu UI Flow thật của user có đang hiển thị tiếng Anh hay không, hay việc thêm retry có thực sự giải quyết dứt điểm — cần user test lại trên labs.google thật.

**Cập nhật (cùng ngày) — retry KHÔNG đủ, config button dùng SAI selector từ đầu:** user báo `config button not found` vẫn xảy ra sau fix retry ở trên — tức không phải vấn đề timing. Kiểm tra `extensions/background.js::remoteConfig.selectors.configButton` — giá trị THẬT là `'button:has(i:contains("crop"))'` (tìm theo icon Material Symbol `'crop'`, không đổi theo locale). `_dom_configure()` ở `client_tool` từ trước tới giờ dùng heuristic HOÀN TOÀN KHÁC, tự đoán: "bất kỳ `<button>` nào có `aria-controls` trỏ tới 1 element đang tồn tại" — generic, dễ khớp nhầm button khác hoặc không khớp gì. Đây mới là root cause thật (không phải thiếu retry). Fix: đổi sang tìm theo icon `'crop'` — CÙNG chiến lược icon-ligature đã dùng đúng ở chỗ khác trong file (`add_2`/`arrow_drop_down`/`image`/`play_circle`). `get_popup()` (đọc `aria-controls` từ `config_btn` ĐÃ xác định đúng) giữ nguyên, không đổi. **Bài học:** khi 1 heuristic tự đoán (không có nguồn tham chiếu) liên tục thất bại, luôn kiểm tra `extensions/background.js::remoteConfig.selectors` TRƯỚC — đây là nguồn sự thật duy nhất cho mọi selector đã proven, tránh đoán mò lặp lại.

### 11.16 Match media theo PROMPT qua `flow.projectInitialData` thay vì tile DOM polling (2026-07-16)

User cung cấp URL `https://labs.google/fx/api/trpc/flow.projectInitialData` + response mẫu thật (project page tự gọi API này mỗi lần vào) và yêu cầu chuyển hướng lấy kết quả sang match theo PROMPT — tận dụng prefix `TASK_{id}:` đã nhúng sẵn vào mọi prompt gửi lên Flow (cùng cơ chế reconcile dùng ở nơi khác, xem root `CLAUDE.md` mục "Reconciliation media") thay vì đọc `img.src`/`video.src` từ tile DOM (5.5/5.6 — cách cũ, đã chứng minh fragile qua chuỗi fix 11.14/11.15: phụ thuộc cấu trúc/class/icon tile có thể đổi bất cứ lúc nào).

**Response mẫu xác nhận:** envelope `result.data.json.projectContents.media[]`; mỗi item có `name` (UUID, giống hệt UUID dùng trong `media.getMediaUrlRedirect?name=X` đã proven) + prompt text tại `mediaMetadata.requestData.promptInputs[].textInput` (hoặc `image.generatedImage.prompt`/`video.generatedVideo.prompt`); AI-generated vs user-uploaded phân biệt qua key `image.generatedImage` vs `image.userUploadedImage`.

**Implement** (`client_tool/server/worker.py`, xem §5.7): `FLOW_TRPC_BASE` (const), `_media_item_prompt(item)` (trích prompt, thử nhiều vị trí — schema không có tài liệu chính thức từ Google), `_collect_task_media_via_project_api(task_id, count)` (poll tới khi đủ `count` match theo prefix `TASK_{id}:`, resolve từng `name` qua `media.getMediaUrlRedirect?name=X` — dùng lại `_dom_resolve_url()` đã proven sẵn), `_resolve_tile_media()` (tách từ code tile-polling cũ để dùng chung), `_collect_task_media_via_tiles()` (tile polling CŨ nguyên vẹn, giờ chỉ là fallback). `_run_task_dom()` VÀ `_run_tasks_batch()` (mode batch, nhiều task nộp liên tiếp) đều đổi sang thử project-API trước, fallback tile polling nếu không khớp/timeout.

**Ở `_run_tasks_batch()`, cách này còn quan trọng hơn single-task**: cursor cũ `new_ids_ordered[cursor:cursor+count]` chỉ là ĐOÁN "tile mới nhất khớp thứ tự submit" — không có gì đảm bảo Google chèn tile đúng thứ tự đó khi nhiều task submit gần nhau; match theo prompt sửa luôn 1 lớp rủi ro gán-nhầm-task tồn tại từ trước ở batch mode, độc lập với mục đích ban đầu (thay tile polling).

**Cập nhật cùng ngày #1 — ưu tiên `img.src`/`video.src` thật thay vì tự construct `getMediaUrlRedirect?name=X`:** xem CHANGELOG "FIX: `_collect_task_media_via_project_api` tự construct URL redirect...". Thêm `_dom_find_tile_src_by_name(name)` (§5.7) tìm lại tile thật trong DOM qua `name` UUID trước, chỉ tự construct `?name=X` khi tile không tìm thấy.

**Cập nhật cùng ngày #2 — bỏ hẳn self-fetch `flow.projectInitialData?projectId=X`, chuyển sang lắng nghe (kiến trúc, quan trọng hơn #1):** User quan sát trực tiếp Network tab, chỉ ra bản đầu tự `fetch(...?projectId=X)` là THỪA và RỦI RO — chính trang labs.google đã TỰ GỌI endpoint này mỗi khi vào/reload project page, chỉ cần LẮNG NGHE response đó. Đây cũng loại bỏ hẳn rủi ro "SUY LUẬN sai tên query param" đã ghi nhận trong lần vá đầu. Xem CHANGELOG "FIX (kiến trúc): bỏ hẳn self-fetch...". Implement: `_install_project_data_interceptor()` (CDP `Page.addScriptToEvaluateOnNewDocument`, cài 1 lần/phiên driver, tự áp dụng lại mọi navigate/reload sau đó) + `_dom_fetch_project_media()` đổi hẳn cách hoạt động — không còn nhận `project_id`, không còn tự `fetch()`, giờ chỉ `driver.refresh()` rồi đọc capture mới nhất từ `window.__pidCaptures`. `_collect_task_media_via_project_api()` bỏ luôn bước `_extract_project_id()` (không cần để tự build URL nữa), khoảng cách poll tăng từ 5s → 8s (mỗi lần giờ tốn thêm thời gian reload).

**Verify đã làm:** `py_compile`/`pyflakes` pass. JS snippet mới (interceptor + đọc capture, + `_dom_find_tile_src_by_name`) validate cú pháp qua Node `new Function()`. **CHƯA/KHÔNG THỂ verify được** trên browser thật: `Page.addScriptToEvaluateOnNewDocument` hoạt động đúng qua Selenium `execute_cdp_cmd()` hay không, tác dụng phụ của `driver.refresh()` lặp lại nhiều lần trong lúc chờ generate, và tile của task vừa submit có luôn tìm thấy trong DOM qua `name` hay không — cần user test lại trên labs.google thật. Toàn bộ 2 lớp fallback cũ (tự construct URL, rồi tile-polling hoàn toàn) vẫn giữ nguyên nên không mất khả năng hoạt động nếu hướng mới có vấn đề, chỉ chậm hơn.

**Cập nhật cùng ngày #3 — im lặng tới 600s nếu interceptor không bắt được gì, thêm log mỗi lần thử + bỏ cuộc sớm:** User test bản #2 xong báo lại: "chưa thấy lấy được image/video đã tạo và logs cũng không thấy đọc response". Nguyên nhân: vòng poll trong `_collect_task_media_via_project_api()` chỉ log khi CÓ MATCH hoặc khi `_dom_fetch_project_media()` gặp lỗi CÓ LOG cụ thể — nếu interceptor hoàn toàn không bắt được gì mọi lần (không phải lỗi, chỉ là "rỗng"), vòng lặp retry lặng lẽ suốt tới hết `timeout=600` (10 phút, không đổi theo mode ảnh/video) trước khi mới chịu fallback tile-polling — tệ hơn nhiều so với hành vi cũ (chỉ 90s), và hoàn toàn im lặng nên trông như treo. Xem CHANGELOG "FIX: `_collect_task_media_via_project_api` chạy im lặng tới 600s...". Fix: `_dom_fetch_project_media()` đổi return contract — `None` = thất bại hẳn (khác `[]` = đọc thành công nhưng rỗng/chưa khớp, tránh nhầm 2 trường hợp làm 1 như trước); `_collect_task_media_via_project_api()` log MỌI lần thử (thấy được bao nhiêu media/khớp bao nhiêu, hoặc lý do thất bại) và đếm số lần thất bại HẲN liên tiếp — sau 2 lần liên tiếp, bỏ cuộc ngay (không đợi hết `timeout`) để fallback tile-polling nhanh. Cũng tiện sửa 1 lỗi tiềm ẩn: nếu không đổi code gọi theo contract mới, `media_list=None` sẽ làm `for item in media_list` raise `TypeError` thay vì fallback êm.

**Kỳ vọng lần test tới:** nếu interceptor hoạt động đúng, log sẽ hiện `projectInitialData: lần thử N — X media trong project, Y/count khớp...` ngay trong ~15-20s đầu. Nếu KHÔNG hoạt động (vd Google dùng XHR thay vì `fetch()`, hoặc CSP chặn ghi đè `window.fetch`), log sẽ hiện rõ `thất bại liên tiếp — bỏ cuộc sớm` trong ~25-30s thay vì im lặng — đây sẽ là bằng chứng trực tiếp để quyết định bước tiếp theo (vd chuyển sang bắt qua Chrome performance log/CDP `Network.getResponseBody` thay vì `window.fetch` override).

**Cập nhật cùng ngày #4 — kết quả test #3: interceptor HOẠT ĐỘNG ĐÚNG, vấn đề nằm ở cách so khớp/tải phía client → thay hẳn bằng RECONCILE qua server (kiến trúc, thay thế #1-#3):** Log thật từ user (`projectInitialData: lần thử 29 — 72 media trong project, 0/1 khớp prompt "TASK_3252:"`, lặp lại ổn định ~10-16s/lần suốt 30+ lần) xác nhận interceptor + reload HOẠT ĐỘNG ĐÚNG — không phải vấn đề #3 lo ngại. Nhưng task cụ thể không bao giờ khớp — cho thấy vấn đề thật nằm ở CÁCH SO KHỚP/TẢI, không phải cách LẤY DỮ LIỆU. User mô tả lại đúng quy trình mong muốn (xem §5.7 để biết chi tiết implement): submit batch → chờ 60s → refresh → `projectInitialData` → so khớp TOÀN BỘ media với DATABASE (không phải client tự đoán) → tải cái DB chưa có → lặp lại/đóng. Phát hiện: `POST /api/media/reconcile/check` (`backend/routes/reconcile.py`) ĐÃ LÀM ĐÚNG VIỆC NÀY từ trước, dùng bởi `extensions/` — client_tool chỉ cần GỌI nó thay vì tự chế lại logic so khớp+tải riêng. Xoá hẳn `_collect_task_media_via_project_api()` (#1-#3, nay lỗi thời), thay bằng `_reconcile_project_media()` + `_wait_and_reconcile_tasks()` — xem CHANGELOG "client_tool DOM mode: thay hẳn poll-theo-task bằng RECONCILE-theo-batch" và §5.7 để biết chi tiết đầy đủ.

**Verify đã làm:** `py_compile`/`pyflakes` pass. **CHƯA/KHÔNG THỂ verify được** trên browser thật — cần user chạy lại; kỳ vọng log `reconcile: gửi N item(s) — server khớp/tải xong M task` xuất hiện và task được đánh dấu `done` thật trong DB.

**Cập nhật cùng ngày #5 — chỉ "mở link" resolve URL cho media THẬT SỰ chưa có, tách reconcile thành 2 giai đoạn (theo yêu cầu user):** User làm rõ thêm quy trình reconcile: bóc tách TASK id từ prompt → CHECK `name` đã có trong DB chưa TRƯỚC → chỉ `name` CHƯA CÓ mới yêu cầu client "mở link ẩn trên web" lấy public URL rồi tải. Bản #4 resolve URL cho MỌI item khớp `taskId` mỗi vòng poll, kể cả media SERVER ĐÃ CÓ SẴN — lãng phí, và không đúng thứ tự user muốn (check trước, resolve sau). Xem CHANGELOG "reconcile/check: `url` thành TÙY CHỌN, trả `unknown`..." — sửa `backend/routes/reconcile.py::reconcile_check()` cho `url` thành optional (item thiếu `url` vẫn xét được "đã có chưa", chỉ khi thiếu mới trả về trong `unknown:[item...]`), và `_reconcile_project_media()` (§5.7) tách thành 2 lệnh gọi: giai đoạn 1 check (không cần url), giai đoạn 2 chỉ resolve+tải cho `unknown`. Hành vi cũ của `extensions/` (luôn gửi kèm `url`) không đổi — 100% backward-compatible.

**Verify đã làm:** `py_compile`/`pyflakes` pass cả `backend/routes/reconcile.py` lẫn `client_tool/server/worker.py`. **CHƯA/KHÔNG THỂ verify được** trên browser thật — cần user chạy lại; kỳ vọng log `reconcile: check N item(s) — X đã có sẵn, Y chưa có` rồi (nếu Y>0) `reconcile: tải Y media mới — Z task khớp/xong`.

### 11.17 Settings đổi từ đồng bộ-từ-server sang CỤC BỘ hoàn toàn + chỉnh sửa được qua GUI (2026-07-17)

User yêu cầu ngắn gọn: "default cục bộ không nhận từ server nữa và có thể chỉnh sửa" — nối tiếp trang "Cài đặt" vừa thêm ở 11.9 (lúc đó chỉ hiển thị read-only 2 nhóm: `local_defaults` là hằng số Python cứng, `server_settings` là cache fetch từ `FLOW_SERVER`). User muốn xoá hẳn khái niệm "đồng bộ từ server" và biến "default cục bộ" thành nguồn sự thật DUY NHẤT, sửa được trực tiếp.

**Trước đây:** settings (`max_concurrent_veo3_profiles`, `error_count_before_refresh`, `refresh_count_before_new_project`, `error_sleep_secs`, `error_window_minutes`, `error_window_max_errors`, `task_delay_secs`, `error_wait_secs`, `download_wait_secs`, `step_delay_min_secs`, `step_delay_max_secs`, `error_patterns`) đồng bộ 1 CHIỀU từ backend chính: `worker.py::_heartbeat()` tự `self._server_settings.update(r.get('settings'))` sau MỖI lần heartbeat (POLL_INTERVAL ~8s); `dispatcher.py::_fetch_global_settings()` tự `GET {FLOW_SERVER}/api/media/settings` (cache 15s, `state.py::_settings_cache`). Cả 2 đều gọi tới CÙNG 1 nguồn cấu hình chỉnh được từ `frontend/src/pages/MachinesPage.jsx` (web) — thiết kế ban đầu để 1 admin điều khiển nhiều máy client_tool cùng lúc.

**Giờ:** module mới `client_tool/server/local_settings.py` — `get_local_settings()` (đọc file `client_tool/local_settings.json`, merge lên `_DEFAULT_SERVER_SETTINGS` cho key thiếu — vd bản cũ chưa từng lưu hoặc field mới thêm sau này, cache RAM), `update_local_settings(updates)` (merge + validate/clamp — CÙNG ngưỡng backend từng áp dụng ở `PUT /api/media/settings`, giờ chuyển hẳn vào đây — + ghi file + cập nhật cache), `reset_local_settings()` (về `_DEFAULT_SERVER_SETTINGS` gốc). `worker.py::__init__` đọc `self._server_settings = get_local_settings()` (snapshot lúc worker khởi động, không hot-reload giữa chừng — sửa xong áp dụng cho worker MỚI start); `_heartbeat()` bỏ hẳn đoạn đồng bộ từ response (heartbeat vẫn gọi để NHẬN TASK như cũ, chỉ không còn nhận settings). `dispatcher.py::_fetch_global_settings()` đọc thẳng `get_local_settings()` — giữ nguyên TÊN hàm nên `_veo3_dispatcher_tick()` không cần đổi gì. `state.py` xoá `_settings_cache`/`_SETTINGS_CACHE_TTL` (chết, không còn ai dùng).

**API mới** (`routes.py`): `GET`/`PATCH /api/selenium/local_settings` (PATCH chỉ áp dụng field nằm trong `_DEFAULT_SERVER_SETTINGS`, field lạ bị bỏ qua — an toàn nếu GUI gửi thừa field), `POST /api/selenium/local_settings/reset`. `GET /api/selenium/settings_debug` (11.9) đổi field `server_settings`/`server_settings_age_secs` → `local_settings` (không còn "tuổi cache" vì đọc RAM/file cục bộ tức thời, không phải fetch mạng có độ trễ).

**GUI** (`gui/pages/settings_page.py`): 2 card cũ "Settings đã fetch từ server" + "Default cục bộ" (đều read-only) gộp thành 1 card DUY NHẤT "🔧 Cài đặt cục bộ" — mỗi field là `QLineEdit` pre-fill giá trị hiện tại (`_FIELD_DEFS` map key→label tiếng Việt dễ hiểu + kiểu `int`/`float`/`list`), nút "💾 Lưu" (validate ép kiểu client-side trước, báo lỗi rõ field nào sai thay vì gửi bừa — `PATCH /api/selenium/local_settings`) và "Đặt lại mặc định" (có confirm dialog, `POST .../reset`). `error_patterns` (list) nhập/hiển thị dạng chuỗi phân cách dấu phẩy, server tự tách lại (`local_settings._clamp()`). 2 card Dispatcher/Backlog (11.9) giữ nguyên read-only.

**⚠️ Liên quan trực tiếp tới bug nghi vấn "2 profile phù hợp, tool chỉ chạy 1" (mục ngay trên):** `max_concurrent_veo3_profiles` giờ KHÔNG còn set được từ web `MachinesPage.jsx` nữa — mỗi máy client_tool set riêng ở tab Cài đặt cục bộ CHÍNH NÓ. Nếu user trước đó set "2" trên web tưởng áp dụng cho client_tool, đó là nguyên nhân khả dĩ THỨ 3 — giờ không còn 2 nơi cấu hình trùng ý nghĩa nữa nên loại bỏ hẳn nguồn nhầm lẫn này, nhưng CHƯA xác nhận đây có phải root cause thật của bug đó hay không.

**Verify đã làm:** `py_compile`/`pyflakes` pass 7 file (`local_settings.py`, `worker.py`, `dispatcher.py`, `state.py`, `routes.py`, `settings_page.py`, `main_window.py`/`sidebar.py` không đổi thêm). Test THẬT qua `app.test_client()`: `GET`/`PATCH /api/selenium/local_settings` (field lạ bị lọc đúng, field hợp lệ áp dụng đúng), `GET /api/selenium/settings_debug` (field `local_settings` phản ánh đúng PATCH vừa gửi, không còn `server_settings`), `POST .../reset` (về đúng default gốc) — cả 4 request 200 OK. Test GUI headless (`QApplication` + `SettingsPage()` thật, `QT_QPA_PLATFORM=offscreen`): `_render()` điền đúng mọi ô input kể cả `error_patterns` list→text; nhập "abc" vào field số → `_on_save()` chặn đúng, báo lỗi rõ ràng, KHÔNG gọi API. **CHƯA test trên GUI thật** (chỉ headless, không dựng `MainWindow()` đầy đủ vì rủi ro tự khởi động server thật — xem lý do tương tự ở 11.9) — cần user tự mở tab Cài đặt, sửa 1 giá trị, Lưu, restart client_tool xác nhận giá trị giữ nguyên từ file.

### 11.18 Danh sách text i18n tách ra file JSON riêng (2026-07-17)

Nối tiếp mạch điều tra "picker không mở" — sau khi thêm dump chẩn đoán (§5.3) cho nghi vấn i18n, user yêu cầu tiếp: "tách danh sách click theo language ra file json để dễ thay đổi". Trước đây `I18N_IMAGE_TAB`/`I18N_SEARCH_PH`/`I18N_ADD_TO_PROMPT` là hằng số Python hardcode ở đầu `worker.py` — sửa/thêm 1 biến thể ngôn ngữ (vd sau khi dump chẩn đoán tiết lộ text thật Google dùng) đòi hỏi sửa code Python + deploy lại.

**Implement:**
- `client_tool/i18n_texts.json` (mới, **commit vào git** — khác `local_settings.json` vốn gitignore vì là state runtime riêng từng máy; file này là DEFAULT DÙNG CHUNG cho mọi máy client_tool) — 3 key `imageTab`/`searchPlaceholder`/`addToPrompt`, mỗi key = list biến thể (hiện tại vẫn giữ nguyên 2 biến thể vi/en cũ, chỉ đổi NƠI LƯU, chưa đổi NỘI DUNG).
- `client_tool/server/i18n_texts.py` (mới) — `get_i18n_texts()`: đọc + cache file JSON, validate từng key phải là `list[str]` (key lỗi/thiếu tự rơi về `_DEFAULT_I18N_TEXTS` — lưới an toàn nếu file bị xoá/sửa hỏng, không làm crash worker).
- `worker.py::_dom_upload_images()` gọi `get_i18n_texts()` thay vì đọc hằng số module-level trực tiếp.

**Cách sửa 1 biến thể ngôn ngữ từ giờ:** mở `client_tool/i18n_texts.json`, thêm/sửa string trong list tương ứng, lưu file, **khởi động lại client_tool** (`get_i18n_texts()` cache trong RAM, không hot-reload giữa chừng — muốn áp dụng ngay cho worker đang chạy thì restart app, giống hệt cơ chế `local_settings.json` ở 11.17). Không cần sửa `worker.py`.

**Verify đã làm:** `py_compile`/`pyflakes` pass 2 file. Test THẬT: `get_i18n_texts()` đọc đúng nội dung file; sửa file JSON (thêm 1 biến thể mới) rồi force-reload cache trong cùng process → xác nhận biến thể mới có mặt ngay; revert lại nội dung gốc sau test.

**Bổ sung — tìm ra CHỖ i18n thật sự thiếu (2026-07-17, cùng ngày):** Ngay sau khi tách file JSON, user yêu cầu thêm "Dự án mới" vào i18n — đây chính là bản dịch tiếng Việt của nút "New project", và hoá ra `_click_new_project_button()` (dùng bởi `_ensure_flow_project`/`_ensure_flow_page`/`_reset_flow_project` mỗi khi vào trang chung labs.google chưa có project — xem §3) trước giờ so khớp CHUỖI TIẾNG ANH CỨNG `'New project'`, KHÔNG hề có i18n dù `_dom_upload_images()` đã có từ §11.15. Khớp đúng log thật đã quan sát nhiều lần trước đó: `[warn] Không tìm thấy nút "New project" trên trang chung` lặp lại cho `profile-4` cụ thể (không phải profile-2) trong lúc chạy 2 profile đồng thời — đây RẤT CÓ THỂ chính là bằng chứng "1 tài khoản Việt, 1 tài khoản Anh" mà user nghi ngờ trước đó, chỉ là nằm ở CHỖ KHÁC (nút "New project" lúc vào trang chung) chứ không phải picker upload ảnh (nơi đã có i18n từ trước). Thêm key `newProject: ["Dự án mới", "New project"]` vào `i18n_texts.json` + `_DEFAULT_I18N_TEXTS`; `_click_new_project_button()` đổi sang so khớp danh sách biến thể (cùng pattern `.some(v => t.indexOf(v)!==-1)` đã dùng ở `_dom_upload_images()`) thay vì chuỗi cứng. Xem CHANGELOG "client_tool: FIX i18n THẬT — nút New project...".

### 11.19 Gemini — setting "Nhận loại prompt" (2026-07-18, ĐÃ BỎ NGAY TRONG NGÀY)

Theo yêu cầu user "nâng cấp gemini lúc tạo profile sẽ có seting có nhận yêu cầu prompt có file đính kèm không" — thêm cột `selenium_profiles.gemini_task_mode` + combo "Nhận loại prompt" trong `ProfileDialog` (`all`/`text_only`/`video_only`) để lọc máy theo `requires_video` ở `_pick_gemini_machine()`. User yêu cầu bỏ ngay: "nhận loại prompt giờ cho phép nhận tất cả không cần phân loại: đối với file đính kèm sẽ dựa vào url backend gửi file client_tool sẽ download và upload link local kèm prompt sau khi gửi kèm prompt sẽ xóa file này." — cơ chế mô tả (tải từ URL, attach local, xoá sau khi gửi) **đã tồn tại sẵn** trong `_run_task_gemini()` từ trước (`tempfile.mkstemp()` → `_gemini_attach_file()` → `os.remove(tmp_path)` trong `finally`, xem §3/CHANGELOG 2026-07-05), không phải code mới — vì cơ chế này generic và giống nhau cho MỌI profile, phân loại theo profile không mang lại lợi ích, chỉ thêm phức tạp thừa. Đã revert TOÀN BỘ: combo GUI, `_heartbeat_gemini()`'s `taskMode`, `heartbeat.py`'s `text_only` trong danh sách hợp lệ, filter trong `_pick_gemini_machine()`/`_pick_free_gemini_machines()`, plumbing accept-field ở `managers.py`/`routes.py`/`worker_profiles.py`. **CHỈ giữ lại** migration đã áp dụng (`_ensure_profile_gemini_task_mode()` trong `backend/core/migrations.py`) — cột `gemini_task_mode` vẫn tồn tại trên DB nhưng là schema mồ côi (không code nào đọc/ghi, luôn `'all'`), theo quy ước additive-only migration của project (không có tiền lệ DROP COLUMN). Mọi máy `gemini_chat_selenium` giờ nhận được CẢ prompt text thuần LẪN prompt có đính kèm như nhau, không phân biệt — đúng hành vi TRƯỚC KHI thêm tính năng này.

### 11.20 Gemini — auto-scale PHẢN ỨNG THEO BACKLOG THẬT (2026-07-19, thay thế bản "giữ sẵn N máy luôn online" 2026-07-18)

**Vì sao bản đầu không đáp ứng đúng nhu cầu:** Sau khi user tạo 1 profile Gemini mới và báo "đã tạo profile gemini nhưng frontend ko thấy machine hoạt động và lúc tạo kịch bản cũng không thấy kết nối" — chẩn đoán ban đầu nghi Master switch TẮT (mặc định TẮT mỗi lần mở app, xem §11.10). User xác nhận đã bật switch nhưng **vẫn lỗi** — vì bật Master switch CHỈ khôi phục profile ĐÃ CHẠY TRƯỚC ĐÓ (`_master_switch_snapshot`), KHÔNG tự start profile MỚI TẠO/chưa từng chạy; và bản "giữ sẵn N máy luôn online" (§11.19 cũ) có `max_concurrent_gemini_profiles` mặc định **0 = tắt hẳn** nên không có gì tự mở Chrome cả — user phải biết bấm Start thủ công HOẶC vào Cài đặt chỉnh setting trước, đúng cái user KHÔNG muốn. Hỏi lại thì user làm rõ: **"tôi muốn như VEO khi có task thì mới tự bật, kết thúc thì tự tắt chứ không phải bật thủ công"** — đây là phương án (b) "xây hàng đợi backlog thật cho Gemini" đã từng đề cập ở lần hỏi TRƯỚC (lúc đó user chọn (a) "giữ sẵn N máy", giờ đổi ý sang (b) sau khi thấy (a) không đúng ý).

**Kiến trúc mới — mirror CHÍNH XÁC VEO3's `tasks_media_flow`/`pending_by_mode`:**

1. **Bảng mới `gemini_pending_requests`** (`backend/core/migrations.py::_ensure_gemini_pending_requests_table()`) — `prompt`, `video_url`, `requires_video`, `meta_json`, `callback_url`, `created_at`.
2. **`backend/routes/gemini.py::_dispatch_or_queue_gemini_prompt(cur, prompt, meta, callback_url, requires_video, video_url)`** — hàm dùng chung THAY THẾ pattern lặp lại ở 9 endpoint enqueue trước đây (`mrow = _pick_gemini_machine(...); if not mrow: fail(...,503); ...`). Tìm máy rảnh dispatch NGAY như cũ; hết máy rảnh → INSERT vào `gemini_pending_requests`, trả `{'queued': True, 'requestId': int}` thay vì lỗi. Áp dụng cho `gemini_enqueue`, `enqueue_full_script`, `enqueue_storyboard`, `enqueue_rewrite_storyboard`, `_dispatch_video_breakdown` (dùng chung bởi `enqueue_next_video_clip`/`enqueue_video_breakdown`), `enqueue_clip_full_script`, `enqueue_clip_rewrite_storyboard`. 2 endpoint multi-machine (`enqueue_scene_batch`/`enqueue_clip_scene_batch`, dùng `_pick_free_gemini_machines()` — giao NHIỀU batch cho NHIỀU máy cùng lúc) có xử lý riêng: khi `machine_codes` rỗng, vòng lặp dispatch CHÍNH nó chạy với **1 "vòng ảo"** (`iterations = machine_codes if machine_codes else [None]`) để queue ĐÚNG 1 batch (không phải toàn bộ phần thiếu — tránh backlog phình to không kiểm soát); batch còn lại tự chờ lần poll tiếp theo của frontend (đã sẵn cơ chế gọi lại `enqueue_scene_batch` mỗi 30s, xem §"Fix gap scene" trong root CLAUDE.md).
3. **`GET /api/gemini/pending_count`** (endpoint mới) — trả `{total, videoTotal, textTotal}` từ `COUNT(*) ... WHERE requires_video=X` — client_tool dispatcher poll endpoint này.
4. **`backend/routes/heartbeat.py`** — nhánh gemini: khi máy heartbeat báo idle (`running=0`) và KHÔNG có `pending_prompt` trực tiếp (cột `machines_media.pending_prompt`, cơ chế cũ vẫn giữ nguyên), thử POP request cũ nhất từ `gemini_pending_requests` — `gemini_chat` (extension) CHỈ lấy được `requires_video=0` (đúng giới hạn đã có từ trước — extension không đủ tin cậy đính kèm file), `gemini_chat_selenium` lấy được CẢ 2 loại. Dùng `SELECT ... FOR UPDATE SKIP LOCKED` chống 2 máy heartbeat đồng thời giành cùng 1 request — connection từ `db.py::get_conn()` có `autocommit=False` sẵn, nên transaction đã mở ngầm từ đầu request, không cần `conn.begin()` riêng cho đoạn này.
5. **`client_tool/server/dispatcher.py::_auto_scale_gemini_tick()`** viết lại hoàn toàn (bỏ hẳn logic "giữ sẵn N máy"), mirror `_auto_scale_veo3_tick()`:
   - `_fetch_gemini_pending_count()` (cache 8s + stale-fail-streak 3 lần, dùng `_gemini_pending_cache`/`_gemini_pending_fail_streak` mới trong `state.py`) poll `GET /api/gemini/pending_count`.
   - Có backlog (`total > 0`) → mở thêm profile `worker_mode='gemini'` `enabled=1` tới trần `max_concurrent_gemini_profiles` (setting cục bộ, **default đổi từ 0 → 1**, mirror `max_concurrent_veo3_profiles`).
   - Hết backlog → đóng các profile đang **RẢNH**.
   - Profile `enabled=0` đang chạy + rảnh → đóng ngay (giữ nguyên từ bản cũ).

**⚠️ Bug tiềm ẩn PHÁT HIỆN VÀ FIX NGAY LÚC VIẾT (chưa từng xảy ra thật, bắt được qua đọc code trước khi ship):** veo3's `_auto_scale_veo3_tick()` dùng `w._current_task is None` để biết profile có đang xử lý dở hay không (an toàn, vì `_task_start()` được gọi ở MỌI nhánh task veo3 — `_run_task_dom`/`_run_tasks_batch`). Gemini's `_run_task_gemini()` (worker.py) **KHÔNG BAO GIỜ gọi `_task_start()`/set `_current_task`** — chỉ gọi `pm.set_status(self.profile_id, 'processing')` lúc bắt đầu và `'idle'` lúc xong. Nếu tái dùng `w._current_task is None` cho gemini, biểu thức này LUÔN đúng (None suốt vòng đời) → `_auto_scale_gemini_tick()` sẽ ĐÓNG NHẦM 1 worker đang giữa chừng attach file/chờ Gemini phản hồi (có thể mất VÀI PHÚT, xem `gemini_attach_timeout`/`gemini_response_timeout`) ngay khi backlog tạm về 0 (dù chính worker đó vừa lấy request cuối cùng ra khỏi hàng đợi). Fix: hàm mới `_gemini_profile_busy(profile) -> bool` dùng `profile.get('status') == 'processing'` (đọc từ `pm.list()`, đã fresh mỗi tick) thay vì `w._current_task`.

**Frontend KHÔNG cần đổi gì:** mọi endpoint enqueue_* giờ LUÔN trả `ok(...)` (200, `success:true`) kể cả khi phải queue — code React hiện tại (`GenPromptButton`/`ProjectVideoClipsTab.jsx`) chỉ check `data.success` rồi tiếp tục polling bình thường, không phân biệt "dispatch ngay" hay "đang chờ máy" — khớp tự nhiên với luồng poll đã có sẵn.

**Verify (2026-07-19):** `py_compile` toàn bộ file sửa. Test thật qua backend throwaway (port 18448): `gemini_enqueue` khi không có máy nào online → 200 `queued:true` (không còn 503), insert đúng bảng; `GET pending_count` phản ánh đúng; heartbeat `gemini_chat_selenium` idle → POP đúng request, backlog về 0; request `requires_video=1` — heartbeat `gemini_chat` (extension) KHÔNG pop được, `gemini_chat_selenium` pop được. Unit test `_auto_scale_gemini_tick()` (mock `pm.list()`/`_start_worker`/`_stop_worker`, gọi THẬT `GET pending_count` qua backend throwaway): có backlog → mở đúng 1 profile (tôn trọng cap=1), tick lặp lại không mở thêm; backlog về 0 (drain qua heartbeat thật) → đóng đúng profile `status='idle'`, **KHÔNG đóng** profile `status='processing'` (xác nhận fix `_gemini_profile_busy()` — bug ở trên không lọt); `enabled=0`+rảnh → đóng ngay, `enabled=0`+đang xử lý → để yên. Dữ liệu test đã xoá sạch. **CHƯA verify UI/Chrome thật** — môi trường này không chạy được PyQt6/Chrome; cần user tự mở client_tool, bật Master switch, tạo 1 kịch bản khi CHƯA có máy Gemini nào online, xác nhận: (a) request "queued" không báo lỗi ngay; (b) trong ~10-20s (2 tick dispatcher, chu kỳ 10s) 1 profile Gemini enabled tự mở Chrome; (c) sau khi xử lý xong và hết backlog, profile tự đóng lại.

### 11.20b Auto-scale không mở nhiều profile hơn số task ĐANG THẬT SỰ chờ (2026-07-21)

User báo "khi có 1 task mới của gemini thì tất cả profile VEO, gemini đều bật và khi có task VEO thì gemini cũng bật cùng — không muốn profile không liên quan start mà không có task được giao". Điều tra bằng cách chạy THẬT `_auto_scale_veo3_tick()`/`_auto_scale_gemini_tick()` (mock `_start_worker` để không mở Chrome) với dữ liệu profile+backlog lấy TRỰC TIẾP từ DB lúc đó (1 profile veo3 `task_mode=video_only`, 2 profile gemini, 142 task `textToImage` pending, `gemini_pending_requests` rỗng): code hiện tại CÁCH LY ĐÚNG giữa 2 loại — không tái hiện được chiều "VEO bật do gemini" hay ngược lại (`pending_by_mode` chỉ đọc `tasks_media_flow`, `gemini/pending_count` chỉ đọc `gemini_pending_requests`, filter `worker_mode` không chồng lấn).

**Bug THẬT tìm thấy (khác hướng):** `local_settings.json` có `max_concurrent_gemini_profiles=2`, và user có ĐÚNG 2 profile gemini — nên dù chỉ 1 prompt gemini pending, `_auto_scale_gemini_tick()` (cũ) vẫn mở CẢ 2 (`need = max_conc - running`, không quan tâm còn bao nhiêu request thật). Đây khớp đúng phần "tất cả profile gemini đều bật" user mô tả, và là lời giải thích hợp lý nhất cho cảm giác "start mà không có task được giao" — 1 trong 2 máy mở ra chắc chắn không có việc. Cùng lớp bug ở `_veo3_dispatcher_tick()`'s slot-promotion (`slots_free = max_conc - running`, không cap theo `pending_by_mode` thật).

**Fix (gemini):** `need` cap thêm bởi `min(max_conc, pending_total)` — không bao giờ mở nhiều máy hơn số prompt thật sự đang chờ, nhưng KHÔNG làm giảm số máy mở khi backlog đã đủ lớn (≥max_conc — không hồi quy).

**Fix (veo3, viết lại NGAY SAU đó — xem §11.20c):** bản đầu dùng `slots_free = min(max_conc, pending_total) - running` với `pending_total` gộp chung image+video — user làm rõ thêm "VEO cũng phải phân biệt mode", và đúng là bản gộp chung CHƯA đủ chính xác khi nhiều profile CÙNG task_mode cạnh tranh 1 số lượng task nhỏ của riêng mode đó (vd 1 task ảnh + 3 task video, 2 profile `image_only` đều "khớp" nhị phân dù chỉ có 1 task ảnh thật) — xem §11.20c để biết cách cấp ngân sách RIÊNG theo từng mode đã thay thế bản này.

Verify trực tiếp qua dispatcher.py thật (mock `_start_worker`/`pm.list`): 1 gemini task pending + 2 profile → mở đúng 1 (trước: mở cả 2); 2 gemini task pending → mở đúng cả 2. `py_compile`/`pyflakes` pass.

### 11.20c VEO3 dispatcher: cấp ngân sách slot RIÊNG theo từng task_mode (2026-07-21, thay thế cách cap bằng `total` gộp chung ở §11.20b)

`_veo3_dispatcher_tick()`'s bản cap-bằng-`total` (§11.20b) không phân biệt được trường hợp NHIỀU profile CÙNG 1 `task_mode` cạnh tranh cùng 1 lượng task nhỏ của riêng mode đó — `_profile_veo3_eligible()` chỉ trả lời nhị phân "có ít nhất 1 task khớp không", không biết có bao nhiêu profile khác cũng đang "khớp" y hệt.

**Cách cấp ngân sách mới:** `image_budget = imageTotal`, `video_budget = videoTotal` (trừ phần các profile ĐANG CHẠY cùng `task_mode` cụ thể đã tiêu thụ — best-effort, không biết chính xác profile `all` đang chạy xử lý loại nào nên đếm riêng `all_running_ct` để trừ sau). Thăng cấp theo thứ tự: (1) profile `image_only` đang `waiting`, chỉ trong giới hạn `image_budget`; (2) profile `video_only`, chỉ trong giới hạn `video_budget`; (3) profile `task_mode='all'` (linh hoạt nhận cả 2 loại) chỉ được lấy từ phần CÒN LẠI của cả 2 ngân sách trên SAU KHI (1)/(2) đã lấy phần của mình, trừ thêm `all_running_ct`. Tất cả đều còn bị chặn bởi `slots_free` tổng (`max_concurrent_veo3_profiles - running`) như cũ.

**Verify:** kịch bản mới — 1 task ảnh + 3 task video, 2 profile `image_only` + 1 profile `video_only`, `max_concurrent=4` → CHỈ 1 `image_only` mở (đúng 1 task ảnh có thật), `video_only` mở đủ (3 task video dư dả) — trước fix (bản `total` gộp chung ở §11.20b) sẽ mở CẢ 2 `image_only`. Chạy lại 3 kịch bản đã verify ở §11.20b (không hồi quy): 3 profile `all` + `max_conc=2` + 1 task → mở đúng 1; setup thật của user (1 profile `video_only` + 142 task ảnh + 0 video) → không mở gì; backlog đủ lớn (100 task, `max_conc=2`) → vẫn mở đủ 2. `py_compile`/`pyflakes` pass. **CHƯA verify UI/Chrome thật.**

**Chưa xác nhận được:** liệu 2 chiều cross-triggering (VEO↔gemini) user mô tả có thật sự tồn tại hay không — không tái hiện được trong test dù đã dùng đúng dữ liệu thật. Khả năng cao nhất: 2 loại backlog tồn tại ĐỘC LẬP cùng lúc (vd tool tắt lâu, cả 2 hàng đợi đều tích luỹ), rồi 2 auto-scale tick (chạy chung 1 vòng lặp `_veo3_dispatcher_loop`, mỗi 10s) cùng mở máy trong 1 khoảng ngắn — trông giống nhân quả dù độc lập. Cần user quan sát lại sau khi cập nhật code, đối chiếu đúng lúc CHỈ có backlog 1 loại để xem loại còn lại có tự mở hay không — nếu vẫn tái diễn, sẽ cần thêm log chẩn đoán tương quan theo timestamp giữa 2 tick.

### 11.20c Gemini — `send_ready_timeout` (nút Gửi) dùng chung `gemini_attach_timeout` thay vì hardcode 60s (2026-07-21)

User báo lỗi thật `Timeout 60s: nút Gửi không sẵn sàng.` khi gửi prompt kèm video, kèm quan sát "có khi 1 video upload đến 3 phút mới có thể gửi prompt". `_gemini_fill_and_submit()` (`server/worker.py`) chờ nút Gửi hết `aria-disabled` bằng timeout HARDCODE 60s — tách biệt hoàn toàn với `_gemini_attach_file()` (bước NGAY TRƯỚC đó, chờ tile đính kèm hiện ra composer) vốn đã dùng `gemini_attach_timeout` configurable per-profile (mặc định 180s, sửa qua `ProfileDialog` field "Attach timeout"). 2 bước này về bản chất là 2 GIAI ĐOẠN của CÙNG 1 việc chờ Gemini xử lý xong file (tile có thể hiện sớm nhưng server Gemini vẫn cần thêm thời gian trước khi cho phép gửi, đặc biệt video lớn) — bước 1 đã đủ timeout theo báo cáo user, bước 2 vẫn hardcode ngắn hơn nên timeout dù bước 1 đã qua trót lọt.

**Fix:** `_run_task_gemini()` giờ truyền `send_ready_timeout=attach_timeout` (biến cục bộ đã đọc từ `self.profile.get('gemini_attach_timeout')`) thay vì để mặc định; default của chính `_gemini_fill_and_submit()` cũng tăng `60`→`180` làm lưới an toàn cho caller khác (vd `tests/_test_gemini_video.py`) không truyền tham số. Không cần field GUI/migration mới — chỉnh "Attach timeout" ở `ProfileDialog` giờ áp dụng cho CẢ 2 giai đoạn chờ. Không ảnh hưởng prompt không có video (nút Gửi sẵn sàng gần như ngay lập tức, vòng poll thoát sớm — timeout dài hơn chỉ kéo dài nhánh LỖI hiếm gặp). `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật** — cần user chạy lại 1 task video lớn để xác nhận không còn timeout sớm ở mốc ~60s.

### 11.20d Gemini auto-scale: grace period trước khi đóng profile — fix race đóng nhầm máy VỪA được giao task (2026-07-21)

User báo bug thật: "task gemini sau khi update cứ chạy 1 tí là dừng mặc dù chạy tự động kịch bản như 20/80 chạy dc tí 30/80 là ko truyền task nữa" (pipeline "Chạy Auto tất cả" — xem root CLAUDE.md — dừng giữa chừng, không phải chỉ chậm).

**Root cause:** `_dispatch_or_queue_gemini_prompt()` (backend) giao prompt TRỰC TIẾP cho máy rảnh — set `machines_media.pending_prompt`, KHÔNG qua bảng `gemini_pending_requests` (bảng đó CHỈ dùng khi hết máy rảnh). Máy chỉ tự đổi `selenium_profiles.status`→`'processing'` ở **heartbeat KẾ TIẾP của chính worker đó** (`_run_task_gemini()`, cách nhau tối đa `POLL_INTERVAL=8s`, xem `_run_gemini_loop()`). `_auto_scale_gemini_tick()` (chu kỳ 10s) có thể rơi đúng khe hở này: `gemini_pending_requests` rỗng (dispatch trực tiếp) → `has_backlog=False`, máy vẫn đọc `status='idle'` (chưa kịp đổi) → code CŨ đóng NGAY, giết Chrome đang cầm đúng task vừa giao — mất vĩnh viễn (chưa từng nằm trong hàng đợi để retry). Pipeline "Chạy Auto tất cả" (nhiều clip × 4 bước, dispatch trực tiếp liên tục cho máy rảnh) trúng khe hở này thường xuyên — mỗi lần trúng, task mất, bước kế tiếp của chain (chờ callback task đã mất) không bao giờ chạy → cả pipeline đứng yên.

**Fix:** `state.py` thêm `_gemini_idle_since` (dict pid→thời điểm đầu tiên thấy "có vẻ rảnh") + `_GEMINI_CLOSE_GRACE_SECS=20`. `_auto_scale_gemini_tick()`'s nhánh đóng chỉ đóng THẬT sau khi đã "rảnh liên tục" ≥20s (đủ hơn 1 chu kỳ heartbeat để worker tự cập nhật status nếu thật sự có việc) — cùng pattern debounce đã dùng cho "Server offline" false-positive (§11.11). Nhánh CÓ backlog clear sạch `_gemini_idle_since` trước khi return, đảm bảo đồng hồ luôn tính từ đúng lần backlog gần nhất về 0.

**Verify:** mô phỏng đúng race qua dispatcher.py thật (mock `_stop_worker`) — tick 1 (backlog=0, máy vừa "nhận việc" status còn `idle`) → không đóng; tick 2 (+10s, máy khác đã `processing`, máy này vẫn `idle` mới 10s) → không đóng; tick 3 (+15s nữa, tổng 25s idle thật) → đóng đúng. Backlog xuất hiện lại giữa chừng → `_gemini_idle_since` clear sạch, không cộng dồn nhầm. `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật với pipeline auto thật.**

### 11.20e Tự luân chuyển project VEO3 khi đầy — setting `max_project_media_items` (2026-07-21)

Theo yêu cầu user "thêm setting số task hoàn thành tối đa 1 project, đạt ngưỡng thì chuyển sang `labs.google/fx/vi/tools/flow` rồi bấm tạo project mới — cụ thể check reconcile mặc định đã có hơn 300 item thì tạo project mới". Setting mới `max_project_media_items` (`local_settings.json`, mặc định **300**, `0`=tắt, sửa qua tab Cài đặt) — đếm qua CHÍNH danh sách media đã đọc lúc reconcile (`_dom_fetch_project_media()`/`flow.projectInitialData`, xem §5.7/11.16), KHÔNG gọi thêm API/reload riêng chỉ để đếm.

`_reconcile_project_media()` ghi `self._last_project_media_count = len(media_list)` mỗi lần đọc `projectInitialData` thành công. Hàm mới `_rotate_project_if_full()` — nếu count ≥ ngưỡng: `driver.get(FLOW_PROJECT_URL)` → `_click_new_project_button()` → `_wait_for_project_url()` → `pm.update(profile_id, project_url=new_url)` (tái dùng nguyên 2 helper đã có từ `_ensure_flow_page()`, không viết logic điều hướng mới) → reset counter về 0. Best-effort: lỗi ở bất kỳ bước nào (không tìm thấy nút, không bắt được URL mới) chỉ log warning + giữ nguyên project cũ, không chặn task tiếp theo.

**Điểm gọi — CHỈ khi batch đã reconciled xong hoàn toàn:** `_wait_and_reconcile_tasks()` gọi `_rotate_project_if_full()` ở CUỐI hàm, CHỈ khi `pending` rỗng (không còn task nào chờ xác nhận). Nếu còn task pending (rơi xuống fallback tile-polling `_collect_task_media_via_tiles`), KHÔNG rotate — vì fallback đó polling tile dựa trên DOM/driver hiện tại, chuyển project ngay lúc này sẽ khiến nó tìm nhầm project và không bao giờ thấy tile của mình.

**Verify:** test trực tiếp `_rotate_project_if_full()` (mock driver/`_click_new_project_button`/`_wait_for_project_url`/`pm.update`, không mở Chrome thật) — dưới ngưỡng không chuyển; đạt ngưỡng chuyển đúng + counter reset; ngưỡng=0 (tắt) không bao giờ chuyển; đạt ngưỡng nhưng không tìm thấy nút "New project" → giữ nguyên project cũ, không crash. `_clamp()` validate đúng (âm→0, không phải số→default 300). **CHƯA verify trên browser thật.**

### 11.21 Khung giờ không nhận task — tự bật/tắt Master switch theo lịch (2026-07-20)

Theo yêu cầu user "client_tool thêm setting bật khung giờ không nhận task" — 3 setting cục bộ mới (`local_settings.json`, xem §11.17): `quiet_hours_enabled` (0/1, mặc định 0=tắt), `quiet_hours_start`/`quiet_hours_end` (chuỗi `"HH:MM"`, mặc định `"22:00"`/`"06:00"`) — sửa qua tab Cài đặt như mọi setting cục bộ khác.

**Cơ chế:** `dispatcher.py::_is_in_quiet_hours(settings)` so giờ hiện tại (`datetime.now().time()`) với khung `start`→`end`, hỗ trợ khung giờ VẮT QUA NỬA ĐÊM (`start > end`, vd `22:00→06:00`: active nếu `now >= start OR now < end`) lẫn khung trong-ngày (`start < end`, vd `13:00→17:00`: active nếu `start <= now < end`). Khung rỗng (`start == end`) luôn `False` (tránh vô tình active 24/24 do nhập sai). `_quiet_hours_tick()` (gọi mỗi 10s trong `_veo3_dispatcher_loop()`, TRƯỚC `_auto_scale_veo3_tick()`/`_auto_scale_gemini_tick()`) so kết quả với `_state._quiet_hours_active` (giá trị áp dụng lần gần nhất, biến RAM-only mới trong `state.py` — cùng nhóm "bị REASSIGN, phải qua `_state.` module-qualified" như `_master_task_intake_enabled`) — **CHỈ hành động lúc CHUYỂN trạng thái** (vừa vào/vừa ra khung giờ), gọi lại NGUYÊN `_set_master_task_intake()` đã có (§11.10) thay vì viết cơ chế dừng/khôi phục riêng.

**Vì sao KHÔNG ép buộc liên tục suốt khung giờ:** nếu tick nào cũng gọi `_set_master_task_intake(False)` bất kể trạng thái hiện tại, user tự bật thủ công giữa chừng (vd cần xử lý gấp lúc 2h sáng) sẽ bị hệ thống TẮT LẠI NGAY trong vòng 10s — vô hiệu hoá hoàn toàn khả năng override thủ công. Chỉ hành động ở boundary giải quyết việc này: sau khi user tự bật giữa khung giờ, tick tiếp theo thấy `should_pause == _quiet_hours_active` (đã update thành `True` từ lần transition trước) nên KHÔNG gọi lại — giữ nguyên lựa chọn thủ công của user cho tới boundary kế tiếp (lúc `_is_in_quiet_hours()` đổi từ `True`→`False`, tick MỚI action lại — lần này là "bật", vô hại vì switch đã bật sẵn, `_set_master_task_intake()` tự no-op nếu trùng giá trị). Hành vi tương tự "Do Not Disturb" có lịch trên điện thoại — tự động theo giờ nhưng vẫn tôn trọng thao tác tay ở giữa.

**Kế thừa 100% hành vi TẮT/BẬT đã có** (không viết code dừng/khôi phục mới): TẮT dừng hẳn TẤT CẢ worker (kể cả `worker_mode='gemini'`) NGAY LẬP TỨC (snapshot vào `_master_switch_snapshot`); BẬT (2026-07-20, đã sửa bug "khôi phục mù" — xem §11.10) để auto-scale tự mở lại ĐÚNG profile nào đang có backlog thật ở tick kế tiếp, thay vì khôi phục mù theo trạng thái trước lúc tắt — nghĩa là sau khi ra khỏi khung giờ quiet hours, chỉ profile NÀO THẬT SỰ CÓ VIỆC mới tự mở lại, không mở nhầm profile rảnh.

**GUI:** `GET /api/selenium/settings_debug` trả thêm `quiet_hours_active` (tính trực tiếp từ `_is_in_quiet_hours()`, luôn khớp trạng thái tick đang dùng — không phải số liệu tách biệt có thể lệch pha); tab Cài đặt (`settings_page.py`) — 3 field mới trong form (field `quiet_hours_start`/`quiet_hours_end` dùng type `'str'` MỚI thêm, trước đây form chỉ hỗ trợ `'int'`/`'float'`/`'list'` — gửi nguyên text, validate ở `local_settings._clamp()`), card "Dispatcher / Auto-scale" hiện thêm dòng "Khung giờ không nhận task: ĐANG trong khung giờ / Ngoài khung giờ".

**Verify:** Unit test `_is_in_quiet_hours()` (mock `datetime.now()`) — đúng cho: tắt hẳn (luôn `False`), khung vắt qua nửa đêm (đúng cả lúc 23:30/03:00/ngoài khung/2 boundary — bắt đầu inclusive, kết thúc exclusive), khung trong-ngày, khung rỗng (`start==end`→`False`), input sai định dạng (rơi về default `22:00`/`06:00`). Unit test `_quiet_hours_tick()` (mock `_set_master_task_intake`) — xác nhận chỉ gọi ĐÚNG 1 lần lúc chuyển trạng thái, tick lặp lại khi trạng thái không đổi không gọi lại (verify chính xác hành vi "không ép buộc liên tục" ở trên). Toàn bộ test cục bộ (settings JSON + RAM), không đụng DB/máy thật. **CHƯA verify UI thật trên client_tool GUI** — cần user tự mở app, bật `quiet_hours_enabled=1` với khung giờ ngắn (vd 2 phút tới) để xác nhận Master switch tự tắt/bật đúng giờ.

### 11.22 `uc.Chrome()` tải nhầm chromedriver không khớp Chrome đã cài — ép `version_main` (2026-07-20)

**Log lỗi thật:** `[profile-14] [warn] uc.Chrome failed (Message: session not created: cannot connect to chrome... This version of ChromeDriver only supports Chrome version 151, Current browser version is 150.0.7871.125`.

**Root cause — dễ nhầm với Selenium Manager, nhưng là 2 cơ chế HOÀN TOÀN KHÁC NHAU:** `_make_driver()` (`worker.py`) gọi `uc.Chrome(options=uc_opts, driver_executable_path=cdp_path)` với `cdp_path = _detect_chromedriver() or None` — trên Windows, khi `CHROMEDRIVER_PATH` chưa set trong `.env` (mặc định), `_detect_chromedriver()` (`chrome_utils.py`) trả `''` → `cdp_path=None`. Khi `driver_executable_path=None`, **`undetected_chromedriver` tự lo việc tải/patch chromedriver bằng cơ chế RIÊNG của chính nó** — hoàn toàn KHÔNG phải Selenium Manager (Selenium Manager là tính năng của Selenium >=4.6, chỉ áp dụng cho `Service()` "thường" ở `_make_service()`, dùng cho nhánh KHÔNG-uc). uc thỉnh thoảng đoán SAI version Chrome cài trên máy (tải nhầm chromedriver mới hơn/khác major version) → Chrome khởi động nhưng chromedriver không khớp → `session not created` ngay từ bước đầu.

**Fix:** hàm mới `_detect_chrome_version(binary_path='')` (`chrome_utils.py`) dò major version Chrome THẬT SỰ sẽ launch, truyền vào `uc.Chrome(version_main=...)` — tham số chính thức của `undetected_chromedriver` dùng đúng để ép version, bỏ qua bước tự đoán của nó.

**⚠️ Bug thứ 2 phát hiện lúc verify — `chrome.exe --version` qua subprocess TREO trên Windows:** Bản đầu của `_detect_chrome_version()` dùng `subprocess.run([binary_path, '--version'], timeout=10)` cho MỌI trường hợp có `binary_path` (đúng cách làm trên Linux/macOS) — test TRỰC TIẾP trên máy Windows thật (không đoán): lệnh này **treo đủ 10 giây rồi mới timeout**, dù `chrome.exe` tồn tại và hợp lệ. Đây là hành vi biết trước của Chrome trên Windows (`--version` không flush/exit nhanh gọn như Linux). Nguy hiểm hơn: nếu không có fallback, mỗi lần mở worker sẽ chậm thêm 10s; và với Chrome Portable (version có thể KHÁC Chrome hệ thống), fallback về registry BLBeacon sẽ lấy NHẦM version hệ thống thay vì version thật của bản Portable — tái tạo lại chính bug ban đầu.

**Fix thật (không dùng subprocess trên Windows):** hàm mới `_win_file_version(path)` đọc `FileVersion` embedded trong resource PE của file `.exe` qua Win32 API (`GetFileVersionInfoSizeW`/`GetFileVersionInfoW`/`VerQueryValueW`, `ctypes` — KHÔNG thêm dependency `pywin32`, KHÔNG chạy file nên không có rủi ro treo). `_detect_chrome_version()` giờ phân nhánh: **Windows + có `binary_path`** (vd Chrome Portable) → `_win_file_version()` (đọc metadata, không chạy file); **Windows + KHÔNG có `binary_path`** (Chrome hệ thống mặc định) → đọc registry `HKCU/HKLM...\Google\Chrome\BLBeacon\version` (Chrome tự ghi version thật vào đây mỗi lần chạy, không cần biết đường dẫn exe); **non-Windows + có `binary_path`** → vẫn giữ `subprocess --version` (không có vấn đề treo trên Linux/macOS, chưa có bằng chứng ngược lại).

**Verify (2026-07-20, test TRỰC TIẾP trên máy Windows thật, không đoán):** `_detect_chrome_version('')` (registry) → `150` tức thời (0.00s, KHÔNG còn treo 10s); `_detect_chrome_version(r'C:\Program Files\Google\Chrome\Application\chrome.exe')` (file-version, không chạy file) → `150` tức thời; đường dẫn không tồn tại → fallback registry đúng `150`. Cả 3 nhánh đều nhanh và đúng version Chrome thật đang cài trên máy test.

**⚠️ FIX THẬT tiếp theo (2026-09-12) — chính registry BLBeacon (nhánh Windows-không-Portable ở trên) VẪN có thể STALE, tái diễn ĐÚNG bug này với Chrome hệ thống:** user báo crash mới `[profile-32] Worker fatal: ... ChromeDriver only supports Chrome version 151. Current browser version is 153.0.8010.37`. Root cause: BLBeacon là registry Chrome TỰ GHI LẠI mỗi lần **launch**, nhưng Chrome auto-update thay THẲNG file nhị phân trên đĩa NGAY (không cần đóng process cũ) — BLBeacon chỉ cập nhật ở LẦN LAUNCH KẾ TIẾP. Nếu lần launch kế tiếp đó chính là lần worker gọi `_detect_chrome_version()` để lấy `version_main` (đọc registry TRƯỚC KHI Chrome mới kịp khởi động và tự ghi lại), giá trị đọc được vẫn là bản CŨ dù file nhị phân trên đĩa đã là bản MỚI — verify trực tiếp trên máy test: registry đọc `152` trong khi Chrome hệ thống thật (`Program Files`) đã là `153.0.8010.37`, xác nhận đúng lớp bug staleness (không phải giả thuyết). Fix: hàm mới `_win_resolve_chrome_exe()` — tìm đúng đường dẫn `chrome.exe` sẽ được chromedriver/uc launch theo THỨ TỰ chromedriver's internal binary finder tự dùng (`LOCALAPPDATA` → `PROGRAMFILES` → `PROGRAMFILES(X86)`), rồi `_detect_chrome_version()`'s nhánh Windows-không-Portable đọc THẲNG `_win_file_version()` (đã có sẵn, dùng cho Portable từ trước — không chạy file, không rủi ro treo) của file này — file trên đĩa LUÔN đúng bản sẽ chạy, loại bỏ hoàn toàn độ trễ. Registry BLBeacon hạ xuống làm fallback CUỐI CÙNG (không xoá). **⚠️ Bug thứ 2 tự bắt được lúc verify:** bản nháp đầu ưu tiên registry `...\App Paths\chrome.exe` (cơ chế Windows chuẩn resolve theo tên exe) TRƯỚC các đường dẫn cố định — verify trực tiếp lộ ra key này bị 1 Chrome Portable KHÁC (của tool khác trên máy) ghi đè, trỏ sang exe HOÀN TOÀN KHÁC (version 136) — SAI HẲN Chrome hệ thống thật (153) — đã hạ App Paths xuống fallback cuối cùng (sau 3 đường dẫn cố định), KHÔNG còn là nguồn ưu tiên. Verify: `_win_resolve_chrome_exe()`/`_detect_chrome_version('')` chạy trên máy test THẬT → resolve đúng `Program Files\...\chrome.exe`, đọc đúng `153.0.8010.37` → trả `153`, KHỚP CHÍNH XÁC crash log. `py_compile`/`pyflakes` sạch. **CHƯA verify launch Chrome thật qua `uc.Chrome(version_main=153)`** — môi trường phát triển này không chạy được Selenium/PyQt6 đầy đủ, cần user tự Start lại profile-32 để xác nhận hết crash. Xem CHANGELOG.md 2026-09-12 (b) để biết chi tiết đầy đủ.

**⚠️ FIX THẬT SỰ tiếp theo (2026-09-12 (c), CÙNG NGÀY) — bản vá (b) KHÔNG ĐỦ, user chạy lại VẪN lỗi y hệt:** bản (b) chỉ sửa ĐÚNG bước DÒ version Chrome (đã verify trả `153` chính xác), nhưng bug thật nằm ở TẦNG KHÁC — `_make_driver()` (`worker.py`) gọi `uc.Chrome(driver_executable_path=cdp_path, version_main=chrome_ver)` với `cdp_path = _detect_chromedriver() or None`. `_detect_chromedriver()` trên Windows trả về CỐ ĐỊNH `CHROMEDRIVER_PATH` (mặc định `data/profiles/chromedriver/chromedriver.exe`) NẾU file đó tồn tại — KHÔNG kiểm tra version của chính file này. Đọc trực tiếp source `undetected_chromedriver/patcher.py::Patcher.auto()` xác nhận: khi `executable_path` KHÔNG rỗng (`self._custom_exe_path=True`), hàm CHỈ kiểm tra file đã "patch" (chống bot-detection) hay chưa rồi **DÙNG NGUYÊN FILE** — nhánh tải/verify version (nơi `version_main` thật sự phát huy tác dụng) KHÔNG BAO GIỜ chạy tới. Nếu máy có sẵn 1 `chromedriver.exe` CŨ (151, từ lần cài Chrome trước) tại đúng path mặc định đó, `version_main=153` bị BỎ QUA HOÀN TOÀN — uc vẫn khởi động bằng driver 151 cũ, tái diễn y hệt crash dù bước dò version (bản vá b) đã đúng.

**Fix:** `_chromedriver_major_version(path)` (mới, `chrome_utils.py`) — đọc MAJOR version của CHÍNH file chromedriver (dùng lại `_win_file_version()`, đọc FileVersion embedded PE resource, không chạy file — chromedriver.exe cũng có FileVersion resource chuẩn như mọi .exe Windows khác, cùng cơ chế đã proven cho chrome.exe). `_make_driver()` giờ, sau khi có cả `cdp_path` lẫn `chrome_ver`, verify version của `cdp_path` — KHÔNG khớp `chrome_ver` (stale) thì bỏ qua nó (`cdp_path = None`, kèm log warning rõ ràng) để `uc.Chrome()` rơi về nhánh `driver_executable_path=None` — lúc đó `Patcher.auto()` MỚI thật sự chạy nhánh tải+patch driver ĐÚNG version qua `version_main`, đúng như bản vá (b) dự định nhưng chưa từng phát huy tác dụng vì bị nhánh `_custom_exe_path` chặn trước.

**Verify:** `py_compile`/`pyflakes` sạch. `_chromedriver_major_version()`: path không tồn tại → `None` tức thời (0.0001s, không treo); sanity-test cơ chế đọc PE FileVersion (tái dùng cho cả chrome.exe lẫn chromedriver.exe) qua `chrome.exe` thật trên máy dev → đọc đúng `153`, khớp `_detect_chrome_version()` đã verify ở bản (b). **CHƯA verify launch Chrome thật với 1 chromedriver.exe THẬT SỰ stale trên máy Windows** (môi trường dev không có sẵn file này để test bằng version thật) — cần user restart profile-32 lần nữa; nếu đúng root cause này, log sẽ xuất hiện dòng `chromedriver cục bộ "..." là bản 151, KHÔNG khớp Chrome 153...` rồi Chrome khởi động thành công (có thể chậm hơn chút lần đầu vì uc phải tải driver mới). Xem CHANGELOG.md 2026-09-12 (c).

**⚠️ FIX tiếp (2026-09-12 (d)) — bản (c) chỉ vá nhánh `uc.Chrome`, bỏ sót MỌI đường `webdriver.Chrome(service=_make_service())`:** user báo "Mở login browser" (`routes.py`) vẫn lỗi y hệt 151 vs 153. `_make_service()` trả THẲNG `CHROMEDRIVER_PATH` không kiểm tra version — dùng chung bởi login browser, fallback regular webdriver của worker, và attach `_connect_to_chrome()`. Fix tập trung tại `_make_service(chrome_binary='', debug_port=0)`: so major version file cục bộ với Chrome sẽ chạy (`debug_port` → `_running_chrome_major()` đọc `/json/version` của Chrome ĐANG chạy; không có thì `_detect_chrome_version(chrome_binary)`); lệch → trả `Service()` trống để Selenium Manager tự tải đúng bản (theo `options.binary_location` nên Portable cũng đúng). aarch64 giữ file cục bộ. ⚠️ **Quy tắc:** mọi chỗ tạo `webdriver.Chrome` thường PHẢI qua `_make_service(...)` kèm `chrome_binary`/`debug_port`, đừng tự `Service(executable_path=...)`. Verify trên máy thật: file cục bộ 151, Chrome 153 → `_make_service()` + `DriverFinder` (không mở Chrome) resolve ra `~/.cache/selenium/chromedriver/win64/153.0.8010.36/chromedriver.exe` (major 153). **CHƯA bấm thử trên GUI.**

### 11.23 Video clip Gemini tải về vào `client_tool/video_tmp/` thay vì thư mục temp hệ điều hành (2026-07-20, tên file đổi ngay sau đó cùng ngày — xem tiếp bên dưới)

Theo yêu cầu user — `_run_task_gemini()` (`worker.py`) trước đây tải video clip (trước khi đính kèm vào Gemini) vào thư mục temp mặc định của hệ điều hành qua `tempfile.mkstemp(suffix=suffix, prefix='gemini_clip_')` (không truyền `dir=`, vd ra `%TEMP%\gemini_clip_xxx.mp4` trên Windows) — khó tìm/theo dõi lúc debug. Giờ cố định vào `client_tool/video_tmp/` (`config.py`'s `VIDEO_TMP_DIR = _this_dir / 'video_tmp'`, tự tạo thư mục lúc import — CÙNG nguyên tắc với `LOGS_DIR` đã có: luôn dùng `_this_dir`, KHÔNG phải `_ROOT` — `_ROOT` có thể trỏ LÊN repo cha nếu client_tool đang nằm trong checkout đầy đủ, còn `_this_dir` LUÔN là chính `client_tool/`, đảm bảo `video_tmp/` không bao giờ lạc ra ngoài project này dù chạy kiểu nào). Đã thêm `client_tool/video_tmp/` vào `.gitignore`.

**Verify:** `py_compile` PASS. Test trực tiếp: `VIDEO_TMP_DIR` resolve đúng `client_tool/video_tmp/`, tự tạo khi import.

**Đổi tiếp — giữ NGUYÊN TÊN file gốc thay vì tên ngẫu nhiên của `mkstemp()` (cùng ngày 2026-07-20):** theo yêu cầu tiếp theo của user ("video download về phải giữ nguyên tên"), `_run_task_gemini()` bỏ hẳn `tempfile.mkstemp(suffix=..., prefix='gemini_clip_', dir=...)` — thay bằng lấy đúng `os.path.basename(video_url.split('?')[0])` (vd `clip_003.mp4`, khớp tên thật trên server) rồi ghi trực tiếp qua `open(tmp_path, 'wb')`. Vì quy ước đặt tên clip (`clip_001.mp4`, `clip_002.mp4`...) LẶP LẠI giữa các project khác nhau (chỉ phân biệt bởi `project_{id}` trong URL, không phải tên file) — nếu ghi thẳng vào `VIDEO_TMP_DIR`, 2 profile khác nhau tải CÙNG TÊN file từ 2 project khác nhau CÙNG LÚC sẽ ghi đè nhau. Fix: mỗi profile ghi vào thư mục con RIÊNG `VIDEO_TMP_DIR/profile_{profile_id}/` (tự tạo qua `.mkdir(exist_ok=True)`) — an toàn vì 1 profile tại 1 thời điểm chỉ chạy đúng 1 task Gemini (`_run_gemini_loop()` tuần tự, không tự đụng tên với chính mình), chỉ cần cách ly giữa CÁC profile khác nhau. File vẫn tự xoá NGAY trong `finally` sau khi gửi xong (logic cleanup không đổi) nên thư mục chỉ chứa file tạm trong lúc xử lý, không tích luỹ rác.

**Verify:** `py_compile` PASS. Test trực tiếp mô phỏng 2 request tải `clip_003.mp4` từ 2 project khác nhau, 2 profile khác nhau (14 và 19) đồng thời — cả 2 giữ đúng tên gốc, ghi vào `profile_14/clip_003.mp4` và `profile_19/clip_003.mp4`, không đè nhau. Dọn sạch file test sau khi verify.

### 11.24 Round-robin nhiều tab Gemini — 1 profile chạy đồng thời N conversation (2026-07-24)

**Yêu cầu user:** "nâng cấp profile gemini trong client_tool có thể chạy đồng thời bao nhiêu tab và thời gian chuyển tab giữa như 0.5 giây, ví dụ setting 3 tab thì nhận 1 lần 3 task hoàn tất đủ 3 task thì nhận tiếp, khi chạy task sẽ bấm luân chuyển các tab qua lại cho gemini chạy lệnh như click tab 1 → 2 → 3 rồi về 1 lặp lại đủ task thì nhận lô task mới".

**Vì sao cần round-robin (không chạy thật sự song song được):** Selenium/ChromeDriver chỉ có ĐÚNG 1 "current window" cho MỌI lệnh (`execute_script`, `find_element`, kể cả CDP passthrough `execute_cdp_cmd` mà `_cdp()`/`_cdp_click_el()` dùng) tại 1 thời điểm trong 1 phiên trình duyệt — `driver.switch_to.window(handle)` đổi window đó cho MỌI lệnh tiếp theo. Để N conversation Gemini cùng tiến triển trong 1 Chrome DUY NHẤT, worker phải LUÂN PHIÊN focus qua từng tab, làm 1 bước nhỏ rồi chuyển tab kế. Không có gì thật sự đa luồng, nhưng vì phần chờ DÀI NHẤT (Gemini generate response, xác nhận file đính kèm) là chờ DOM/mạng cập nhật (không cần driver bận rộn), việc bỏ tab A đứng chờ 1 nhịp trong lúc phục vụ tab B/C gần như không tốn gì — N task hoàn tất trong khoảng ≈ thời-gian-chờ-dài-nhất + N×(chi phí mỗi bước nhỏ), gần N lần nhanh hơn chạy tuần tự.

**2 setting mới trên profile** (chỉ hiện khi Loại = Gemini trong `gui/profile_dialog.py`): **Số tab đồng thời** (`gemini_max_concurrent_tabs`, 1-10, mặc định 1 = hành vi cũ không đổi) và **Giãn cách chuyển tab** (`gemini_tab_switch_interval`, giây, mặc định 0.5). 2 cột mới trên `selenium_profiles` (backend `ToolSub` gốc quản lý — xem §11.4 "bỏ kết nối DB trực tiếp").

**Refactor 3 hàm blocking-poll thành cặp kickoff+check** (`server/worker.py`) — ĐIỀU KIỆN TIÊN QUYẾT để round-robin khả thi: `_gemini_attach_file`/`_gemini_fill_and_submit`/`_gemini_wait_response` (bản gốc) mỗi hàm tự có vòng `while time.time()<deadline: ...; sleep(...)` RIÊNG BÊN TRONG — gọi thẳng cho tab A sẽ CHIẾM DỤNG driver tới khi xong hẳn (có thể tới 180-300s), tab B/C không được đụng tới trong suốt thời gian đó, phá vỡ hoàn toàn ý nghĩa round-robin. Tách mỗi hàm thành 2 nửa:
- `_gemini_attach_file_kickoff(local_path) -> before_count` (mở menu, click "Tải tệp lên", `send_keys()`) + `_gemini_attach_file_poll(before_count) -> bool` (1 lần kiểm tra KHÔNG-BLOCKING tile đã xuất hiện chưa).
- `_gemini_type_prompt(prompt)` (gõ prompt, KHÔNG chờ nút Gửi) + `_gemini_submit_if_ready() -> bool` (1 lần kiểm tra nút Gửi sẵn sàng chưa, click NGAY nếu có).
- `_gemini_response_if_ready() -> str|None` (1 lần kiểm tra Gemini đã trả lời xong chưa, trả text nếu có).

3 hàm GỐC (`_gemini_attach_file`/`_gemini_fill_and_submit`/`_gemini_wait_response`) GIỮ NGUYÊN chữ ký + hành vi bên ngoài 100% — giờ chỉ là wrapper mỏng gọi lặp lại đúng cặp trên trong vòng `while` local. **Luồng 1-tab tuần tự (`_run_task_gemini`, `gemini_max_concurrent_tabs=1`) KHÔNG đổi gì cả** — zero regression risk, đây là lý do chọn cách refactor "tách đôi rồi wrap lại" thay vì viết riêng 2 bộ logic DOM automation trùng lặp.

**`_heartbeat_gemini()` đổi chữ ký** — thêm `max_concurrent: int` (gửi `maxConcurrent` lên server, mặc định 1) và `keepalive_only: bool` (gửi `keepaliveOnly`, tái dùng cờ ĐÃ CÓ SẴN từ VEO3 — xem "Keepalive trong lúc xử lý task"/"Fix race keepalive cướp task rồi vứt bỏ" ở trên); **trả về `list`** (đọc field `pendingPrompts` mới từ backend, fallback bọc `pendingPrompt` số ít vào list nếu server cũ chưa update) thay vì 1 dict/None như trước. `_run_gemini_loop()` (tuần tự) chỉ lấy `list[0] if list else None` — hành vi y hệt cũ.

**`_run_gemini_loop_concurrent(max_tabs)`** (hàm mới, `_run_gemini_loop()` tự giao việc cho nhánh này nếu profile set `gemini_max_concurrent_tabs > 1`):
```
while chạy:
  nếu KHÔNG có slot nào đang active:
    heartbeat(running=0, max_concurrent=max_tabs) → nhận list tối đa max_tabs prompt
    (list rỗng → chờ POLL_INTERVAL rồi thử lại; list có 1..max_tabs item → mở đúng
     từng đó tab MỚI qua _gemini_open_tab(), mỗi tab 1 slot state='new')
  với MỖI slot đang active (theo thứ tự index, vòng round-robin):
    switch_to.window(slot.handle)
    _gemini_slot_step(slot)   # tiến 1 bước NHỎ của state machine, xem dưới
    sleep(gemini_tab_switch_interval)
  đóng tab + giải phóng MỌI slot vừa chuyển sang state='done'
  nếu đã hơn POLL_INTERVAL kể từ lần heartbeat gần nhất:
    heartbeat(running=<số slot đang active>, max_concurrent=max_tabs, keepalive_only=True)
```
`_gemini_slot_step(slot, idx)` — state machine per-slot, mirror ĐÚNG trình tự của `_run_task_gemini` (luồng tuần tự) nhưng mỗi lần gọi chỉ tiến 1 bước: `new` (navigate GEMINI_URL, chọn model, tải video nếu có rồi `_gemini_attach_file_kickoff` → `attaching`; hoặc không có video thì `_gemini_type_prompt` thẳng → `submitting`) → `attaching` (poll `_gemini_attach_file_poll`, xong thì `_gemini_type_prompt` → `submitting`) → `submitting` (poll `_gemini_submit_if_ready`, xong → `waiting_response`) → `waiting_response` (poll `_gemini_response_if_ready`, có text → gửi callback, state='done'). Mỗi state tự theo dõi deadline riêng (từ `gemini_attach_timeout`/`gemini_response_timeout` của profile) — quá hạn thì raise, bị bắt bởi try/except BAO TOÀN BỘ hàm (kể cả `switch_to.window()` thất bại) → gửi callback lỗi, state='done', KHÔNG làm chết round-robin loop của các slot khác.

**Vì sao KHÔNG cần thread keepalive riêng** (khác luồng tuần tự `_run_task_gemini`, vốn phải spawn 1 thread nền gọi heartbeat mỗi `POLL_INTERVAL` vì bị BLOCK hoàn toàn trong lúc xử lý): vòng lặp round-robin chính nó KHÔNG BAO GIỜ block quá `gemini_tab_switch_interval` (mặc định 0.5s) mỗi bước — tự nó đã đủ nhanh để chèn 1 lệnh heartbeat "giữ ấm" (throttled còn 1 lần/`POLL_INTERVAL`) ngay trong thân vòng lặp chính, không cần thread riêng.

**Đảm bảo đúng ngữ nghĩa "nhận đủ N task, xong hết mới nhận lô tiếp theo":** heartbeat GIỮA CHỪNG 1 lô LUÔN gửi `keepaliveOnly=True` — cờ này (backend đã có sẵn từ trước, dùng cho VEO3) khiến server SKIP HẲN bước giao việc, luôn trả `pendingPrompts: []` bất kể backlog có gì. CHỈ heartbeat lúc TẤT CẢ slot đã `active_idx` rỗng (nghĩa là lô trước đã xử lý xong/lỗi hết, tab đã đóng) mới KHÔNG gửi cờ này — đây là lần duy nhất server thật sự xét giao thêm việc. Không cần thêm state/cờ nào mới, tận dụng nguyên cơ chế `keepaliveOnly` sẵn có.

**Backend liên quan** (repo `ToolSub` gốc, KHÔNG phải file trong `client_tool`): `heartbeat.py`'s nhánh gemini đổi để trả `pendingPrompts` (mảng) thay vì chỉ `pendingPrompt` (1 item) — xem CHANGELOG.md của `ToolSub` (2026-07-24, "Backend: heartbeat Gemini giao nhiều pending prompt/lần").

**CHƯA verify trên browser thật** (môi trường phát triển không chạy được Chrome/Gemini thật) — đã verify: import `server.worker` qua venv thật (đủ dependency selenium/undetected-chromedriver) xác nhận mọi hàm mới tồn tại đúng trên class, không lỗi cú pháp/import; `python -m compileall` sạch cho toàn bộ `server/`, `gui/`, `main.py`, `selenium_flow.py`. **Cần user tự test thật**: tạo 1 profile Gemini, set "Số tab đồng thời" = 2-3, có sẵn vài prompt đang chờ trong hàng đợi (`gemini_pending_requests`), Start worker — xác nhận (1) mở đúng N tab mới, (2) round-robin chuyển tab đúng nhịp `gemini_tab_switch_interval`, (3) mỗi tab trả về ĐÚNG kết quả của conversation trong tab đó (không lẫn lộn — rủi ro cần để ý nhất, dù về lý thuyết mỗi tab Gemini là 1 `document`/conversation độc lập giống hệt user tự mở nhiều tab tay), (4) chỉ heartbeat xin lô mới sau khi TOÀN BỘ lô cũ xong (xem log `← Nhận lô N/max task`).

**Bug thật đã fix ngay trong ngày — profile bị đóng giữa chừng, mất kết quả chưa kịp báo server:** User test thật báo lại đúng như dự đoán ở mục "CHƯA verify" trên: "setting 2 tab 1 lúc: thì có mở 2 task, nhưng server chỉ có 1 task dc giao hiện tại nên 1 tab ko hoạt động, 1 tab chạy sau khi hoàn tất thì đóng tất cả profile nhưng không update kịch bản lên server". Root cause: `_run_gemini_loop_concurrent()` chỉ gọi `pm.set_status(..., 'idle', ...)` ĐÚNG 1 LẦN lúc mới start — suốt cả vòng đời xử lý batch (có thể mất vài phút) KHÔNG BAO GIỜ báo lại `'processing'`. `dispatcher.py::_gemini_profile_busy()` (dùng bởi `_auto_scale_gemini_tick()`, §11.20) đọc ĐÚNG cột `status` này để biết KHÔNG được đóng profile đang bận — vì cột kẹt ở `'idle'`, auto-scale tưởng profile rảnh ngay khi backlog server vừa cạn (chỉ cần 1 trong N slot còn việc) và `_stop_worker()` NGAY GIỮA CHỪNG, giết Chrome trước khi `_gemini_slot_finish()` kịp POST callback — kịch bản mất kết quả dù đã generate xong. Cùng lớp bug đã fix cho luồng tuần tự ở §11.20d ("grace period trước khi đóng profile"), nhưng ở TẦNG NÔNG HƠN: không phải do race đóng quá sớm, mà do CHƯA BAO GIỜ báo đúng trạng thái để guard đó có cơ hội phát huy tác dụng. Fix: thêm `pm.set_status(self.profile_id, 'processing', pid=threading.get_ident())` ngay sau khi mở batch tab mới, và `pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())` ngay khi TOÀN BỘ slot đã đóng (batch xong hẳn). Về phần "1 tab ko hoạt động" (mở 2 tab nhưng server chỉ có 1 task thật) — `_run_gemini_loop_concurrent()` chỉ mở đúng `len(pending_list)` tab, không mở thừa; nhiều khả năng liên quan lớp bug "pending_prompt mồ côi/dispatch trùng" đã fix cùng ngày ở backend `ToolSub` gốc (1 script bị dispatch trùng qua cả `pending_prompt` trực tiếp lẫn backlog, heartbeat trả 2 prompt cho cùng 1 việc logic) — chưa có bằng chứng DOM/log trực tiếp xác nhận 100%, cần theo dõi thêm. `py_compile`/`pyflakes` pass. **CHƯA verify lại trên browser thật sau fix này.**

**Follow-up (2026-07-25) — lọc pendingPrompt trùng phía CLIENT, phòng vệ thứ 2 độc lập với backend:** User xác nhận lại bất biến cần đảm bảo: "tuy setting 2 tab chạy đồng thời nhưng chỉ có 1 task thì mở 1 tab thôi". Thay vì chỉ trông chờ backend không bao giờ giao trùng (đã fix 1 nguyên nhân cụ thể ngày 2026-07-24 nhưng không đảm bảo tuyệt đối mọi nguyên nhân), thêm `SeleniumFlowWorker._pending_item_key(item)` (staticmethod, khoá `(field, scriptId/clipId/projectId)` trích từ `item['meta']`, trả `None` nếu thiếu `field`) — `_run_gemini_loop_concurrent()` lọc `pending_list` theo khoá này NGAY SAU heartbeat, TRƯỚC KHI mở bất kỳ tab nào: 2 item trùng khoá chỉ giữ item đầu (log warning cho item bị bỏ). Đảm bảo bất biến "số tab mở ≤ số việc thật sự khác nhau" đúng ở TẦNG CLIENT, không phụ thuộc backend có sót trường hợp dispatch trùng nào khác hay không. `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật.**

**Bug NGAY TRONG KHOÁ dedup vừa thêm ở trên — thiếu batchFrom/batchTo (2026-07-25, cùng ngày):** User test thật báo lại: "request thì nhiều, setting 2 tab, mà 1 tab ko làm gì, chạy có 1 tab". Nguyên nhân: khoá `(field, target)` ở trên KHÔNG có `batchFrom`/`batchTo` — đúng lúc này backend `ToolSub` gốc ĐANG được sửa CÙNG NGÀY (xem CHANGELOG.md `ToolSub`, "Redesign gemini_pending_requests") đổi `_dispatch_all_scene_batches()` sang tạo SẴN NHIỀU request khác nhau cho CÙNG 1 script/clip (mỗi request 1 khoảng batch riêng, vd script 10 scene → 2 request batch 1-5 và 6-10) — 2 request THẬT SỰ KHÁC NHAU này bị khoá dedup coi là trùng (cùng field, cùng target), request thứ 2 bị lọc bỏ oan trước khi mở tab, dù server đã giao đủ việc cho cả 2 tab. Fix: thêm `meta.get('batchFrom')`/`meta.get('batchTo')` vào khoá → `(field, target, batchFrom, batchTo)`. Field không phải scene_batch (draft/full_script/rewrite_storyboard) luôn có 2 giá trị này `None` như nhau ở mọi item nên hành vi dedupe cũ không đổi — chỉ scene_batch mới thật sự phân biệt đúng theo khoảng batch. `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật sau fix này.**

**Bug ĐỘC LẬP THỨ 2, cùng triệu chứng bề ngoài — tab khởi động bị bỏ hoang (2026-07-25, cùng ngày):** User làm rõ thêm: "ý là khi khởi động đã có sẵn 1 tab thì nếu chạy 2 tab mở thêm 1 tab là đủ, chứ để không tab đầu làm gì". Khác hẳn 2 bug dedup ở trên (đều về việc SERVER GIAO gì) — bug này ở phía CLIENT MỞ TAB: `_run_gemini_loop_concurrent()` sau `_make_driver()` (Chrome luôn tự có sẵn 1 tab mặc định, blank) KHÔNG BAO GIỜ dùng tab đó — mỗi lô mới LUÔN gọi `_gemini_open_tab()` (mở tab HOÀN TOÀN MỚI) cho MỌI slot kể cả slot đầu. Với `gemini_max_concurrent_tabs=2`: Chrome có 3 tab (1 tab khởi động bỏ hoang + 2 tab thật sự làm việc) — đúng hiện tượng "1 tab không làm gì". Fix: lưu `startup_tab_handle = self.driver.current_window_handle` ngay sau `_make_driver()`; lúc gán slot cho LÔ ĐẦU TIÊN, nếu còn `startup_tab_handle` thì TÁI SỬ DỤNG làm handle của 1 slot (rồi set `None`, chỉ dùng 1 lần) thay vì mở tab mới — an toàn vì tab khởi động blank giống hệt tab `_gemini_open_tab()` mở ra, cả 2 đều được `_gemini_slot_step()` tự `driver.get(GEMINI_URL)` khi bắt đầu xử lý, không phân biệt nguồn gốc. Các lô SAU không cần xử lý gì thêm — mọi tab (kể cả tab tái sử dụng) đều bị đóng khi xong batch (`_gemini_close_tab`), không còn tab "mồ côi" nào để tái sử dụng tiếp. `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật.**

### 11.25 ChatGPT — `worker_mode='chatgpt'` (2026-08-01) + nâng cấp Gemini/ChatGPT bắt ảnh trả về

Mirror kiến trúc `worker_mode='gemini'` (§11.5) 1:1 — navigate `https://chatgpt.com/` → attach ảnh (nếu có `imageUrl`/`videoUrl` từ pending prompt, tải về rồi `send_keys()`) → gõ prompt → gửi → chờ phản hồi → POST callback. Sequential 1-tab (chưa có round-robin nhiều tab như §11.24). Xem đầy đủ selector DOM + toàn bộ bug thật đã fix trong comment block đầu section ChatGPT của `server/worker.py` và CHANGELOG.md 2026-08-01.

**Đã verify ĐẦY ĐỦ qua Selenium thật (không mock) cho trường hợp 1 ảnh** — sau khi phát hiện lần đầu user đăng nhập NHẦM trình duyệt thường dùng thay vì đúng profile Chrome automation của client_tool (2 session hoàn toàn tách biệt, `user-data-dir` riêng — đã mở đúng cửa sổ profile automation để user đăng nhập lại): `_chatgpt_attach_file()` → `_chatgpt_fill_and_submit()` → `_chatgpt_wait_response()` chạy trọn vẹn, nhận đúng 1 ảnh base64 dedupe đúng. 2 bug thật bắt được + đã fix: (1) ID input nhận file không ổn định giữa tài khoản (`#upload-files` vs `#upload-photos` — sửa bằng `_CHATGPT_UPLOAD_IDS` thử lần lượt); (2) gửi ngay sau khi xác nhận đính kèm ở tầng form (0s chờ) khiến ChatGPT âm thầm drop cả tin nhắn vì file chưa upload xong lên server ở background (nút Gửi KHÔNG disable trong lúc chờ, khác Gemini) — sửa bằng cách thêm 6s chờ sau xác nhận local.

**⚠️ Giới hạn thật phát hiện, KHÔNG PHẢI bug trong code** — đính kèm **đúng 2 ảnh** cùng lúc (dù đã chờ đủ, cả 2 xác nhận đúng trong composer, tên file khác nhau hoàn toàn) → LUÔN bị ChatGPT âm thầm drop, tái hiện 3 lần độc lập kể cả với file hoàn toàn mới. 1 ảnh luôn ổn định; ≥2 ảnh không hoạt động với tài khoản Free đã test — nghi là giới hạn phía sản phẩm OpenAI (từ chối âm thầm thay vì báo lỗi), cần user tự xác nhận với tài khoản trả phí nếu cần nhiều ảnh/lượt thật sự.

**Nâng cấp song song cho Gemini:** `_gemini_response_if_ready()`/`_gemini_wait_response()` giờ trả `{'text','images'}` thay vì string thuần (Gemini cũng có thể generate ảnh inline trong chat) — mọi call site (`_run_task_gemini`, `_gemini_slot_finish`, `_gemini_slot_step` của round-robin) đã cập nhật theo. Ảnh lấy qua `_extract_response_images_base64()` (helper DÙNG CHUNG Gemini+ChatGPT), KHÔNG dùng Python `requests.get()` trực tiếp (không có session/cookie, không biết URL ảnh có cần auth hay không).

**FIX ngay trong ngày — Gemini KHÔNG bắt được ảnh do dùng `blob:` URL, không phải `https://` (2026-08-01):** User test lại báo đúng: "gemini hiện chỉ lấy text chưa có cơ chế lấy kết quả image" — bản đầu `_extract_response_images_base64()` chỉ verify được với ChatGPT (ảnh `https://`), CHƯA test với ảnh Gemini thật. Verify trực tiếp trên conversation có sẵn ảnh: `<img>` của Gemini có `src` dạng **`blob:...`** — `fetch(imgs[i].src, {credentials:'include'})` trên URL này báo `TypeError: Failed to fetch` (dù ảnh hiển thị/decode xong bình thường trên trang), lỗi bị `catch(e){}` nuốt im lặng nên ảnh lặng lẽ biến mất khỏi kết quả, không có log nào tố cáo. Fix: đổi chiến lược ưu tiên — vẽ `<img>` lên `<canvas>` (`ctx.drawImage()` rồi `canvas.toDataURL()`) thay vì `fetch()`; canvas đọc PIXEL đã decode sẵn, không cần fetch mạng nên hoạt động với MỌI scheme (`blob:`/`https:`/`data:`) — chỉ fallback về `fetch()` (giữ nguyên cho ChatGPT, đã proven) nếu canvas lỗi. **Đã verify end-to-end THẬT qua Selenium (không mock) 2 bước:** (1) mở lại conversation cũ có sẵn ảnh → extract đúng 1 ảnh (trước fix: 0); (2) chạy **full pipeline production** (`_gemini_fill_and_submit`+`_gemini_wait_response`) với prompt MỚI trên 1 profile khác — Gemini generate ảnh thật, nhận đúng `{images:[1 ảnh]}`, decode xác nhận header PNG hợp lệ. Xem CHANGELOG.md 2026-08-01 "FIX: `_extract_response_images_base64()` không bắt được ảnh Gemini trả về".

**FIX bug thật thứ 2 — chụp phải placeholder TRẮNG thay vì ảnh thật (2026-08-03):** phát hiện qua storyboard #34 (backend `ToolSub`) — frame 2 và 11 lịch sử ChatGPT CÓ ảnh thật, tool báo hoàn tất, nhưng ảnh lưu vào hệ thống là 1 tấm TRẮNG TOÀN BỘ, `md5sum` GIỐNG HỆT giữa 2 frame khác nhau. Root cause: `_chatgpt_response_if_ready()`/`_gemini_response_if_ready()` coi response xong ngay khi tín hiệu is-generating biến mất rồi gọi `_extract_response_images_base64()` NGAY — nhưng ChatGPT/Gemini có thể hiện `<img>` PLACEHOLDER (giữ đúng kích thước cuối, tránh nhảy layout) TRONG LÚC ảnh thật còn render — `naturalWidth` đã đủ lớn qua filter `min_width` nên bị vẽ lên canvas + `toDataURL()` như ảnh hợp lệ, dù nội dung chỉ là màu trắng đồng nhất. Fix: `_extract_response_images_base64()` đổi return type sang `(images, pending)` — thêm `isBlankCanvas()` (JS nội bộ, lấy mẫu 9 điểm pixel qua `ctx.getImageData()`, lệch màu ≤4 giữa tất cả điểm → coi là placeholder) loại ảnh trắng khỏi kết quả + báo `pending=True`; 2 hàm response-ready trả `None` (tiếp tục poll) nếu `pending=True`, không chốt response vội dù đã có text. CHỈ áp dụng nhánh canvas (không áp dụng fallback `fetch()` — không đọc được pixel, nhưng rủi ro thấp hơn nhiều vì chỉ dùng cho ảnh cross-origin đã tải xong hẳn). Verify: trích đúng JS `isBlankCanvas` chạy qua Node với mock `ctx.getImageData()` — 4 kịch bản (trắng toàn bộ/ảnh thật đa dạng/canvas lỗi đọc pixel/gần-đồng-màu do nhiễu anti-aliasing) đều đúng kỳ vọng. **CHƯA verify trên browser thật.** Xem CHANGELOG.md client_tool 2026-08-03.

**Còn thiếu (ngoài phạm vi lần này):** route "producer" để dispatch 1 request chat-tự-do (có thể kèm ảnh) vào `gemini_pending_requests` cho machine `chatgpt_chat_selenium`/`gemini_chat_selenium` consume — các route `enqueue_*` hiện có đều gắn chặt pipeline NanaBananaPro (script/scene cụ thể), không phù hợp use case chat generic này. `_heartbeat_chatgpt()`/backend `heartbeat.py` đã sẵn sàng consume nếu có ai insert đúng request vào bảng, nhưng chưa test full round-trip thật.

**⚠️ FIX auto-scale hoàn toàn bỏ sót `worker_mode='chatgpt'` (2026-08-01, ngay sau khi ship §11.25):** User báo bug thật: "storyboard chay provider: chatgpt mà sao client_tool mở loại gemini và ko chạy gì". Root cause: `_auto_scale_gemini_tick()` (`server/dispatcher.py`, xem §11.20) lọc CỨNG `worker_mode=='gemini'` — profile `worker_mode='chatgpt'` KHÔNG BAO GIỜ được auto-scale xét mở, dù `gemini_pending_requests` có backlog `provider='chatgpt'` lớn tới đâu; và `GET /api/gemini/pending_count` (backend) cũng chưa từng split theo `provider`, chỉ theo `requires_video`. Verify trực tiếp trên DB thật lộ ra bằng chứng sống: storyboard #34 có 1 request `provider='chatgpt'`, `status='pending'` nằm chờ từ trước — máy ChatGPT không bao giờ tự mở để nhận nó. Fix: `gemini_pending_count()` thêm `geminiTotal`/`chatgptTotal`; `_auto_scale_gemini_tick()` tách thân thành `_auto_scale_chat_worker_group(worker_mode, total_pending, max_conc, label)` rồi gọi 2 lần (1 cho nhóm `gemini` với `geminiTotal`, 1 cho nhóm `chatgpt` với `chatgptTotal`) — mỗi nhóm tự mở/đóng theo ĐÚNG backlog provider của mình, vẫn dùng chung setting `max_concurrent_gemini_profiles` làm trần (chưa có setting riêng theo provider). Xem CHANGELOG.md 2026-08-01 "FIX: auto-scale client_tool bỏ sót hẳn worker_mode='chatgpt'...". **CHƯA verify UI/Chrome thật** — cần user restart client_tool rồi xác nhận profile ChatGPT tự mở khi có backlog chatgpt-provider.

### 11.26 Multi-attach — đính kèm ĐỦ mọi ảnh tham chiếu (không còn giới hạn 1 ảnh/request) (2026-08-04)

User báo bug thật (backend `ToolSub`): "upload 2 ảnh tham chiếu nhưng qua chatgpt/gemini chỉ thấy đính kèm có 1 ảnh" — đúng giới hạn kiến trúc đã ghi nhận từ trước ở §11.25 ("⚠️ giới hạn: `gemini_pending_requests` chỉ có 1 slot đính kèm"). Backend thêm cột `gemini_pending_requests.extra_media_urls JSON` + gộp với `video_url` thành field mới `imageUrls` (mảng đầy đủ) trong `pendingPrompt`/`pendingPrompts` — xem CHANGELOG.md `ToolSub` gốc 2026-08-04 "Multi-attach" để biết chi tiết phía backend.

**Phía client_tool** — cả 3 nơi từng chỉ xử lý ĐÚNG 1 file đính kèm giờ đọc `pending.get('imageUrls')` (fallback `videoUrl`/`imageUrl` số ít nếu backend cũ chưa gửi `imageUrls` — 100% backward-compat, không breaking cho request 1-ảnh):

- **`_run_task_gemini()`** (1-tab tuần tự) — vòng `for idx, url in enumerate(image_urls)`: tải mỗi ảnh vào `VIDEO_TMP_DIR/profile_{id}/` (tiền tố `{idx}_` phòng trùng basename), gọi `_gemini_attach_file()` TUẦN TỰ cho từng file trước khi gõ prompt. `tmp_path` (biến đơn) đổi thành `tmp_paths` (list) — dọn sạch TOÀN BỘ trong `finally`.
- **`_run_task_chatgpt()`** — y hệt, dùng `_chatgpt_attach_file()`.
- **`_gemini_slot_step()`** (round-robin nhiều tab, §11.24) — phức tạp hơn vì đây là state machine kickoff+poll KHÔNG-BLOCKING (không thể dùng vòng `for` trần như 2 hàm trên): state `'new'` giờ lưu `slot['image_urls']`/`slot['image_idx']=0`/`slot['tmp_paths']=[]` rồi tải+kickoff ẢNH ĐẦU (helper mới `_gemini_slot_download_and_kickoff(slot, slot_idx, idx)`, dùng chung cho cả ảnh đầu lẫn ảnh sau). State `'attaching'` — khi poll xác nhận ảnh hiện tại đã đính kèm xong, TĂNG `image_idx`: còn ảnh tiếp theo trong hàng đợi thì tải+kickoff ảnh đó rồi **Ở LẠI** state `'attaching'` (không chuyển state — vòng lặp round-robin sẽ tự quay lại tab này ở lượt sau để poll ảnh mới); hết ảnh mới gõ prompt + chuyển `'submitting'`. `_gemini_slot_finish()` dọn `slot.get('tmp_paths')` (list) thay vì `tmp_path` đơn.

**⚠️ Giới hạn THẬT đã kiểm chứng riêng cho ChatGPT (không phải do fix này, đã ghi nhận từ §11.25):** tài khoản Free ÂM THẦM DROP tin nhắn nếu đính kèm ≥2 ảnh cùng lúc (test 3 lần độc lập). Fix này đảm bảo client_tool CỐ GẮNG đính kèm đủ N ảnh đúng như backend yêu cầu, nhưng với ChatGPT Free, N≥2 nhiều khả năng vẫn thất bại ở tầng sản phẩm OpenAI — không có cách khắc phục từ phía tool, cần tài khoản trả phí để xác nhận. Gemini không có giới hạn này.

**Verify:** `py_compile`/`pyflakes` sạch (`server/worker.py`). Backend đã verify multi-attach dispatch + heartbeat exposure qua DB thật (xem CHANGELOG `ToolSub`). **CHƯA verify trên browser thật** (môi trường phát triển không chạy được Chrome/Gemini/ChatGPT thật) — cần user tự chạy: (1) `_run_task_gemini()`/`_run_task_chatgpt()` 1-tab với 1 request có ≥2 ảnh tham chiếu, xác nhận cả 2/3 ảnh cùng xuất hiện trong composer trước khi gửi; (2) round-robin nhiều tab (`gemini_max_concurrent_tabs>1`) với ≥1 slot có nhiều ảnh, xác nhận state machine không kẹt ở `'attaching'` (log `[slot N] ✔ Đã tải file đính kèm i/M` phải tăng dần rồi mới thấy `Gemini prompt submitted`).

**FIX ngay sau khi user thử — đính kèm ảnh TRÙNG LẶP khiến ChatGPT báo "ảnh này đã upload lên rồi" (2026-08-04, cùng ngày):** User báo: "upload ảnh 1 xong -> upload ảnh 2 nhưng bị báo lỗi ảnh này đã upload lên rồi -> bước upload tuần tự ảnh đang có vấn đề". Root cause NẰM Ở BACKEND `ToolSub` gốc (không phải state machine round-robin/tuần tự ở đây) — `candidate_urls` của `_dispatch_storyboard_sheet_image()` gộp từ `source_media` (upload local) + `ref_name_ids` (asset), 2 nguồn KHÔNG đảm bảo không trùng (hệ thống có sẵn "gắn từ thư viện" cho 2 asset khác nhau trỏ CÙNG 1 file vật lý, không copy byte) — 2 URL khác nhau nhưng NỘI DUNG giống hệt lọt xuống đây, worker tải thành 2 file cục bộ tên khác nhau nội dung giống hệt, ChatGPT tự phát hiện trùng THEO NỘI DUNG (không phải tên file) và từ chối lần 2 — đúng khớp thông báo user thấy. Backend đã fix dedup tại nguồn (`_dispatch_storyboard_sheet_image()`, xem CHANGELOG `ToolSub` 2026-08-04). **Thêm lớp phòng vệ THỨ 2 độc lập ở đây** (`server/worker.py`) — `_run_task_gemini()`/`_run_task_chatgpt()`/`_gemini_slot_step()` đều dedup `image_urls` bằng `list(dict.fromkeys(image_urls))` (giữ nguyên thứ tự) ngay sau khi đọc từ `pending`, trước khi tải bất kỳ file nào — cùng triết lý "backend có thể sót, client tự vệ thêm" đã dùng cho dedup `pendingPrompt` ở §11.24. `py_compile`/`pyflakes` sạch. **Chưa verify trên browser thật.**

**FIX THẬT SỰ (2026-08-04, cùng ngày, SAU KHI fix trên KHÔNG ĐỦ) — "ảnh này đã upload lên rồi" VẪN xảy ra dù 2 ảnh KHÁC NỘI DUNG HOÀN TOÀN:** User bấm "Chạy lại" storyboard #46 (project 86, 2 ảnh tham chiếu CHAR+BG qua `source_media`, không qua `ref_name_ids` nên KHÔNG dính bug URL-alias ở fix ngay trên) — ChatGPT vẫn báo "Bạn đã upload ảnh này" ngay ảnh thứ 2. Verify trực tiếp: `curl` cả 2 URL (`/api/media/task/4590/images/15_CHAR_001_03.jpg` 634KB và `.../6_BG_008_01.jpg` 1121KB) → SHA256 khác nhau hoàn toàn, xác nhận đây KHÔNG PHẢI bug URL-alias đã fix ở trên (2 file thật sự khác nhau ngay từ nguồn). User cũng xác nhận **tự tay chọn CÙNG 2 file đó qua dialog thật (chọn 1 lúc) thì upload bình thường** — bằng chứng quyết định: loại trừ hẳn giả thuyết "giới hạn phía sản phẩm ChatGPT Free với ≥2 ảnh" đã ghi ở §11.25 (nếu là giới hạn sản phẩm, thao tác tay cũng phải bị chặn y hệt). Root cause thật: `_run_task_chatgpt()` (bản trước fix này) gọi `_chatgpt_attach_file()` (đơn) RIÊNG BIỆT cho TỪNG ảnh trong vòng lặp — N lần `send_keys()` tách rời trên CÙNG 1 input TĨNH `#upload-files` (ChatGPT render sẵn input này 1 lần duy nhất trong DOM, KHÔNG mở lại menu mỗi lần như Gemini) — khác hẳn thao tác tay (1 lần chọn multi-file = 1 sự kiện `change` duy nhất mang cả 2 File cùng lúc). Nghi ChatGPT có cơ chế chống-trùng dựa theo tín hiệu KHÁC nội dung byte (session/token upload nội bộ, hoặc coi 2 lần `change` liên tiếp trên cùng input là "chọn lại") và bị false-positive. Fix: hàm mới `_chatgpt_attach_files(local_paths: list, timeout)` — gộp TOÀN BỘ path vào ĐÚNG 1 lệnh `send_keys('\n'.join(local_paths))` (cú pháp Selenium chuẩn cho `input[multiple]`, dịch sang 1 lệnh CDP `DOM.setFileInputFiles` duy nhất mang cả mảng path — mô phỏng đúng multi-select thủ công), poll `.files.length >= before_count + N` (thay vì `> before_count` như bản đơn-lẻ). `_run_task_chatgpt()` đổi: tải xong HẾT mọi ảnh trước (vòng lặp download giữ nguyên), rồi mới gọi `_chatgpt_attach_files(tmp_paths, ...)` MỘT LẦN DUY NHẤT (không còn attach xen kẽ trong vòng lặp download nữa). `tests/_test_chatgpt.py` cập nhật theo (gọi `_chatgpt_attach_files()` 1 lần thay vì loop `_chatgpt_attach_file()`). **Hệ quả:** nhận định "GIỚI HẠN THẬT... tài khoản Free ÂM THẦM DROP nếu đính kèm ≥2 ảnh" ghi ở §11.25/§11.26 (dựa trên đúng bản test-loop-riêng-từng-ảnh này) NHIỀU KHẢ NĂNG LÀ CHẨN ĐOÁN SAI — root cause thật là cách gọi `send_keys()`, không phải giới hạn phía OpenAI; cần retest lại với tài khoản Free sau fix này để xác nhận dứt điểm (không loại trừ khả năng VẪN còn 1 giới hạn sản phẩm thật ở N≥3, chỉ mới chắc chắn N=2 không phải giới hạn cứng). `py_compile`/`pyflakes` sạch. **⚠️ CHƯA verify trên browser thật** — task thử nghiệm trước đó đã timeout 300s trước khi fix kịp deploy; code KHÔNG hot-reload, cần RESTART `main.py` rồi bấm "Chạy lại" trên storyboard để áp dụng.

---

### 11.27 `worker_mode='gemini_video'` — engine THỨ 2 cho task VIDEO, thay thế VEO3 (2026-08-07)

Theo yêu cầu user "nâng cấp model tạo video thành 2 option: Veo là luồng hiện tại, Gemini là luồng sẽ mới" — Gemini chat (gemini.google.com) hoá ra ĐÃ CÓ tính năng **"Tạo video"** trực tiếp trong menu "+" (khám phá qua profile Gemini có sẵn, `kqxs0007`), cho phép đính kèm ảnh tham chiếu + chọn tỷ lệ khung hình rồi generate — về chức năng tương đương VEO3/Flow nhưng chạy hoàn toàn trong Gemini, không cần labs.google.

**Khác biệt CĂN BẢN với `worker_mode='gemini'` đã có** (dùng cho pipeline kịch bản/ảnh NanaBananaPro qua `_run_task_gemini()`/`_run_gemini_loop()`, tiêu thụ hàng đợi RIÊNG `gemini_pending_requests`): `gemini_video` xử lý TRỰC TIẾP task **VIDEO** trong `tasks_media_flow` — **CÙNG hàng đợi mà VEO3 (`worker_mode='dom'/'api'`) tiêu thụ** qua `_heartbeat()`/`/api/media/heartbeat` (mirror hoàn toàn `_run_task_dom()`'s shape: `/task/processing` → làm việc → upload kết quả/báo lỗi). Nhờ vậy **KHÔNG cần sửa gì ở backend `ToolSub` gốc** — task dispatch, `pending_by_mode`, reaper đều generic theo `machineCode`, không phân biệt worker_mode.

**Luồng DOM Gemini "Tạo video" (khám phá qua Selenium thật):**
```
Menu "+" (_gemini_open_composer_menu(), CÓ SẴN) → click chip text 'Tạo video'
  (role=menuitemcheckbox, không có testId ổn định — so text)
  → composer chuyển sang "chế độ Video" (nút "Video" đổi aria-label
    thành "Bỏ chọn Video" khi active — dùng để idempotent-check)
       │
       ├─ Đính kèm ref ảnh: nút "Tải tệp lên" — DÙNG NGUYÊN _gemini_attach_file()
       │    sẵn có, KHÔNG cần viết lại (verify: hoạt động y hệt trong chế độ video)
       │
       ├─ Tỷ lệ khung hình: button[aria-label^="Tỷ lệ khung hình"] → click mở
       │    .cdk-overlay-container chứa ĐÚNG 2 option role="menuitemradio"
       │    ("Ngang (16:9)"/"Dọc (9:16)") — CHỈ 2 lựa chọn, khác hẳn dropdown
       │    ratio đa dạng của Flow. aria-checked + label trigger phản ánh đúng
       │    lựa chọn hiện tại (verify đổi ratio rồi đọc lại, khớp 100%).
       │
       └─ Gõ+gửi prompt: _gemini_type_prompt()/_gemini_submit_if_ready() (CÓ SẴN)
              │
              ▼ Chờ (thời gian đo thật: không ref ảnh ~53-72s; có ref ảnh CÓ THỂ
                lâu hơn nhiều — 1 lần test vượt 150s vẫn chưa xong, CHƯA rõ do
                ref ảnh hay do rate-limit tài khoản lúc đó)
       <video crossorigin="use-credentials"
              src="https://contribution.usercontent.google.com/download?...">
       (KHÔNG có video → Gemini trả text lỗi/từ chối, vd rate-limit "I'm getting
        a lot of requests right now" — verify bắt đúng qua test thật)
```

**`src` của `<video>` CẦN session cookie** (`crossorigin="use-credentials"`) — server backend KHÔNG tải trực tiếp được (khác VEO3's CDN URL public luôn download được server-side qua `requests.get()` thường). Phải tải NGAY TRONG BROWSER (`fetch(url,{credentials:'include'})` → blob → `FileReader.readAsDataURL()` → base64, CÙNG pattern đã proven cho ảnh Gemini — xem `_extract_response_images_base64()`), giải mã base64 ở Python rồi **upload multipart** qua `POST /api/media/task/<id>/upload_result` (endpoint CÓ SẴN — dùng cho tính năng "Thêm video thủ công" trên web, `_apply_media_to_task()` tự set `status='done'`) — KHÔNG dùng `/task/download` (URL-based, backend không tải được URL cần cookie này).

**Hàm mới trong `server/worker.py`** (đặt ngay sau `_gemini_wait_response()`, trước `_heartbeat_gemini()`):
- `_gemini_video_enter_mode()` — idempotent, mở menu + click chip 'Tạo video', verify bằng `[aria-label="Bỏ chọn Video"]`.
- `_gemini_video_set_ratio(aspect_ratio)` — map `'9:16' in aspect_ratio` → chọn "Dọc", còn lại → "Ngang" (mặc định của Gemini, cũng là fallback khi không khớp gì). Best-effort, lỗi chỉ log warning.
- `_gemini_video_result_if_ready()`/`_gemini_video_wait_result(timeout=600)` — poll không-blocking/blocking, trả `{'video_url'}` hoặc raise nếu Gemini trả lỗi rõ ràng (KHÔNG CÓ video nhưng có text phản hồi) hoặc timeout. Timeout mặc định **600s** — rộng hơn hẳn `gemini_response_timeout` mặc định 300s dùng cho chat text (video chậm hơn nhiều).
- `_gemini_video_extract_base64(video_url)` — fetch+base64+decode như mô tả trên.
- `_upload_video_result(task_id, video_bytes)` — POST multipart `/upload_result`.
- `_run_task_gemini_video(task)` — orchestrator đầy đủ, mirror `_run_task_dom()`: `/task/processing` → `driver.get(GEMINI_URL)` (LUÔN chat mới, như `_run_task_gemini()`) → enter mode → set ratio → tải+đính kèm `task.source_media` (nếu có, dùng chung logic tải tuần tự như `_run_task_gemini()`) → gõ+gửi `task.prompt_text` → chờ+trích video → upload → `_handle_task_success()`/`_handle_task_error()` khi lỗi.

**Wiring:** `__init__`'s validation list (`'api','dom','gemini','chatgpt','gemini_video'`), `load_ext` exclusion (thêm vào nhóm không cần Flow extension), `_run_task()` dispatcher (`elif worker_mode=='gemini_video': return self._run_task_gemini_video(task)`), `run()` (nhánh setup RIÊNG: navigate thẳng `GEMINI_URL`, bỏ qua `_ensure_flow_page()`/token capture — phần còn lại của vòng lặp chính, kể cả `_heartbeat()`/`_process_tasks()`, DÙNG CHUNG với DOM/API vì `_process_tasks()`'s nhánh batch chỉ áp dụng `worker_mode=='dom'`, `gemini_video` tự rơi vào nhánh tuần tự sẵn có). `_handle_task_error()`'s escalation "tạo project mới" (`_reset_flow_project()`, Flow-specific — navigate `FLOW_PROJECT_URL`) SKIP cho `gemini_video` vì mỗi task đã tự navigate `GEMINI_URL` mới từ đầu, coi như đã "reset".

**Auto-scale (`server/dispatcher.py`):** hằng số mới `_VEO3_LIKE_WORKER_MODES=('api','dom','gemini_video')` thay filter cứng `('api','dom')` trong `_auto_scale_veo3_tick()`. `gemini_video` LUÔN `task_mode='video_only'` (ép ở `ProfileDialog.get_data()`, không có UI chọn khác — engine này CHỈ xử lý video) nên tự nhiên rơi đúng vào ngân sách `video_budget` đã có sẵn trong `_veo3_dispatcher_tick()` (xem §11.20c) — **cạnh tranh CÙNG pool video với profile VEO3 `task_mode='video_only'`**, không cần sửa gì thêm ở logic cấp ngân sách theo mode. `_start_worker()` + route `POST /api/selenium/profiles/<id>/start` (`server/routes.py`) không còn bắt buộc `project_url` cho `gemini_video` (dùng `gemini.google.com`, không phải Flow project).

**GUI (`gui/profile_dialog.py`):** combo "Loại" thêm option thứ 4 `🎬 Gemini — Tạo Video (mới, thay VEO3)` — tái dùng NGUYÊN 2 field "Attach timeout"/"Response timeout" của nhóm 'gemini' (cùng ý nghĩa: chờ đính kèm ref ảnh / chờ phản hồi xong — chỉ khác thang thời gian, worker tự clamp SÀN 600s cho response bất kể giá trị field thấp hơn) + field "Task nhận đồng thời" (`max_concurrent`) của nhóm 'veo3' (số task video/lần heartbeat — xử lý TUẦN TỰ trong 1 tab, KHÔNG có round-robin nhiều tab như `worker_mode='gemini'`/`_run_gemini_loop_concurrent`). **⚠️ Bug ẩn field (2026-08-12):** widget `_max_concurrent` dùng CHUNG 2 nhóm; `_on_type_change()` cũ khiến chọn VEO3 thì nhóm `gemini_video` (duyệt sau) ẩn mất ô này — đã sửa tính tập visible trước rồi mới ẩn/hiện 1 lần. `gui/pages/profiles_page.py` — badge riêng "🎬 Gemini Video", `can_start` không đòi `project_url`.

**Test:** `tests/_test_gemini_video_flow.py` (mới) — test độc lập các bước nguyên tố (không cần task DB thật, trừ `--task-id` để chạy full round-trip qua `_run_task_gemini_video()` với 1 task test riêng). Verify end-to-end THẬT (Selenium thật, profile `kqxs0007`, tốn quota thật): ratio Dọc/Ngang đều áp dụng đúng (verify qua đọc lại `aria-label`), 2 lần generate thành công (784KB/2.5MB, header MP4 hợp lệ), đính kèm ref ảnh xác nhận tile xuất hiện, 1 lần bắt đúng lỗi rate-limit (raise message rõ ràng thay vì treo).

**⚠️ Chưa verify / cần theo dõi thêm:**
- Full round-trip qua hàng đợi heartbeat thật với 1 task video thật (`tasks_media_flow`) — verify hiện tại gọi trực tiếp các hàm `_gemini_video_*`, chưa qua `_heartbeat()`/`_process_tasks()`/dispatcher đầy đủ.
- Thời gian generate KHI CÓ ref ảnh chưa xác nhận nằm gọn trong sàn 600s cho mọi trường hợp (prompt dài + nhiều ref).
- Chưa test qua GUI thật (tạo profile mới chọn "🎬 Gemini — Tạo Video", Start, để auto-scale tự nhận task video thật từ backlog `pending_by_mode`).
- `_run_task_gemini_video()` hiện KHÔNG đọc `task.output_count` (Gemini video mode có vẻ chỉ tạo 1 video/lần submit, khác VEO3 DOM configure có chọn x1-x4) — chưa xác nhận rõ giới hạn này.

**FIX THẬT (2026-08-07, user báo lỗi ngay sau khi dùng thử) — "Invalid URL ... No scheme supplied" khi tải ref ảnh:** `_run_task_gemini_video()` đọc URL ref ảnh thẳng từ `task['source_media'][i]['url']` rồi `req_lib.get(url, ...)` NGAY KHÔNG QUA XỬ LÝ — nhưng `source_media` (build từ `_resolve_ref_images()` phía backend, gửi qua heartbeat) LUÔN là đường dẫn TƯƠNG ĐỐI (vd `/api/media/files/storyboard/...png`, quy ước dùng nhất quán khắp hệ thống, xem `ToolSub/CLAUDE.md`), không phải URL tuyệt đối → `requests.get()` raise `MissingSchema`. Fix: thêm `if url.startswith('/'): url = f'{FLOW_SERVER}{url}'` (+ skip/log nếu vẫn không có scheme sau đó) TRƯỚC khi gọi `req_lib.get()` — mirror ĐÚNG pattern đã có sẵn ở `_run_task_dom()` (nhánh upload ảnh DOM, cùng file) mà nhánh Gemini-Video mới thêm bị bỏ sót. Đã audit các nơi khác gọi `req_lib.get(url,...)` với URL từ server (`_run_task_gemini`/`_gemini_slot_download_and_kickoff`/`_run_task_chatgpt`'s `image_urls`) — cả 3 đọc từ field `imageUrls` do backend TỰ build kèm `SELF_BASE_URL` (đã có scheme sẵn) nên KHÔNG dính bug này, chỉ riêng `source_media` (field dùng chung cho DOM mode LẪN Gemini-Video mode) mới mang path tương đối.

**FIX THẬT (2026-08-08, user báo "gemini-video có phản hồi video được tạo nhưng báo về server lỗi") — báo lỗi SAI dù Gemini vẫn đang tạo video thật, do race giữa ACK-text và video thật:** Điều tra qua log production (profile `gemini-video` id=31) — task #4710/#4711 (đều có ref ảnh) lỗi với ĐÚNG 1 thông báo: `"I'm generating your video. This could take a few minutes... / Đang tạo video cho bạn… Quá trình này có thể mất vài phút."` — đây **KHÔNG PHẢI** lời từ chối mà là ACK NGẮN Gemini tự gửi ngay lúc BẮT ĐẦU xử lý. Bằng chứng quyết định: retry NGAY SAU của task #4710 (cùng prompt, cùng ref ảnh) **THÀNH CÔNG** sau ~80s — đúng khoảng thời gian y hệt lần bị báo lỗi trước đó → xác nhận đây là RACE: "Stop response"/loading-indicator của khung chat biến mất RẤT SỚM (ngay khi ACK ngắn render xong) trong khi video THẬT vẫn generate NGẦM Ở BACKGROUND tách rời khỏi trạng thái loading — có lúc worker check đúng vào khe hở đó thì báo lỗi oan. Root cause: `_gemini_video_result_if_ready()` coi `is_generating=false` + có text + không có `<video>` = "từ chối" → trả lỗi NGAY, không phân biệt ACK "đang xử lý" với từ chối THẬT (vd rate-limit đã verify trước đó). Fix: thêm `_GEMINI_VIDEO_STILL_WORKING_MARKERS` (`'generating your video'`/`'đang tạo video'`/`'check back'`/`'a few minutes'`/`'vài phút'`...) — text khớp 1 trong các marker này (không phân biệt hoa/thường) → trả `None` (tiếp tục poll) thay vì lỗi; đồng thời đổi thứ tự check để đọc `<video>` TRƯỚC `is_generating` (video có sẵn thì trả về ngay). `_gemini_video_wait_result(timeout=600)` (không đổi) vẫn là lưới an toàn cuối nếu Gemini THẬT SỰ kẹt. **Root cause + fix suy ra TRỰC TIẾP từ log production thật, không đoán mò** — không attach lại được vào Chrome LIVE của worker đang chạy (CDP chỉ cho 1 session, worker đang giữ). **⚠️ CHƯA verify lại trên browser thật sau fix** — code KHÔNG hot-reload, `main.py` (client_tool GUI) đang chạy vẫn dùng bytecode CŨ trong RAM dù file đã sửa trên đĩa — **cần đóng và mở lại `main.py`** để áp dụng, rồi theo dõi vài task `gemini_video` có ref ảnh để xác nhận hết báo lỗi sai. Lúc điều tra có Stop/Start lại worker profile 31 qua API để thử — nhưng vì `gemini_video` nằm trong `_VEO3_LIKE_WORKER_MODES` (auto-scale), backlog còn nên auto-scale tự khởi động lại NGAY trong CÙNG process (vẫn code cũ, không giúp gì); task #4711 đang xử lý dở bị ngắt giữa chừng lúc Stop sẽ tự phục hồi qua reaper server (`REAP_VIDEO_TIMEOUT_MIN`, tối đa ~15 phút) như thiết kế sẵn có, không cần can thiệp thêm.

---

## 11.28 Fix "Create with Google Flow" — màn hình xen giữa khi navigate thẳng tới URL project (2026-08-10)

Theo yêu cầu user: khi navigate THẲNG tới URL của 1 project cụ thể (`https://labs.google/fx/vi/tools/flow/project/{uuid}`), Google Flow ĐÔI KHI hiện 1 màn hình xen giữa bắt bấm nút "Create with Google Flow" trước khi thực sự vào được project — URL trên thanh địa chỉ VẪN giữ nguyên `/project/{uuid}` suốt lúc này, nên check `'/project/' not in current` sẵn có ở `_ensure_flow_page()`/`_ensure_flow_project()` (`server/worker.py`) KHÔNG phát hiện được case này (worker tưởng đã vào project rồi nhưng thực ra còn kẹt ở màn hình xen giữa, chưa có Slate CE để gõ prompt).

Fix: `_click_create_with_flow_if_present(timeout=6)` (`server/worker.py`, đặt cạnh `_click_new_project_button()`) — poll tìm nút theo `textContent` (KHÔNG dùng class, HTML user gửi là styled-components hash tự sinh đổi mỗi lần Google deploy), match với danh sách biến thể `createWithFlow` trong `i18n_texts.json`/`server/i18n_texts.py::_DEFAULT_I18N_TEXTS` (mirror pattern `newProject` — EN "Create with Google Flow" xác nhận qua HTML thật, VN "Tạo trong Google Flow" là dự đoán best-effort chưa xác nhận). Gọi ngay sau MỌI `driver.get()` tới 1 URL project cụ thể — 2 call site: `_ensure_flow_page()` (dòng navigate khi chưa ở `labs.google`) và `_ensure_flow_project()` (dòng navigate chính) — bấm xong xác nhận lại `current_url` còn đúng `/project/`, lỡ điều hướng ra chỗ khác thì `driver.get()` lại đúng URL project ban đầu ("click rồi quay lại url project như cũ"). KHÔNG áp dụng cho 2 chỗ `driver.get(FLOW_PROJECT_URL)` khác trong file (trang chung, không phải URL 1 project cụ thể — user chỉ nói "url dạng project"). **CHƯA verify trên Chrome/labs.google thật** — cần user tự chạy 1 task thật để xác nhận.

---

## 11.29 ProfilesPage — gọn cột Mode + dropdown "⋮" + Xóa cache/cookie/lịch sử (2026-08-10)

Theo yêu cầu user "thiết kế UI cho hợp lý hơn các badge mode dồn cục quá nhỏ không biết đang chạy gì, đưa các action thành dropdown, thêm xóa cache cho profile: cache, cookie, browing history - all time".

**Cột Mode** (`gui/pages/profiles_page.py`) — trước nhồi 2-3 `Badge` nhỏ liền kề ngang (`task_mode`+`worker_mode`+`x{N}`) trong 130px, dễ bị cắt chữ. Đổi sang layout DỌC 2 dòng (mirror cách cột "Tên / Email" cạnh bên đã làm): dòng 1 = ĐÚNG 1 badge "loại chính" to, màu rõ (`_ENGINE_BADGE`/`_TASK_MODE_ENGINE_BADGE`, khớp đúng combo "Loại *" của `ProfileDialog` — `✨ Gemini`/`🤖 ChatGPT`/`🎬 Gemini Video`/`🖼️🎬 VEO3`/`🖼️ VEO3 · Ảnh`/`🎬 VEO3 · Video`); dòng 2 = `QLabel` muted nhỏ cho chi tiết phụ (worker_mode api/dom, số tab/concurrent). Cột rộng 130→165px.

**Cột Hành động** — 4-5 icon-button rời rạc (start/stop, mở/đóng login, xem logs, sửa, xóa) rút còn: 1 nút chính Start/Stop (giữ bấm trực tiếp, dùng thường xuyên nhất) + 1 nút "⋮" mở `QMenu` (`_build_actions_menu(p, running, login_open)`) gom Mở/Đóng login · Xem logs · Sửa profile · (separator) · **Xóa cache/cookie/lịch sử** (mới, sau đổi thành "Làm mới profile" — xem mục riêng ngay dưới) · (separator) · Xóa profile. Cột hẹp 140→90px. Thêm QSS `QMenu`/`QMenu::item`/`QMenu::separator` vào `gui/style.py` (trước đó KHÔNG có — menu sẽ render theo theme OS sáng mặc định, lệch tông UI tối nếu thiếu).

**Cột Trạng thái/Tokens/Lỗi → gộp 1 cột, bỏ cột Hôm nay (2026-08-10, theo yêu cầu tiếp theo "cột trạng thái / token/ lỗi cũng làm dang badge bỏ cột hôm nay"):** trước đây mỗi cái đứng riêng 1 cột (đã LÀ `Badge` sẵn, chỉ là chiếm 3 cột × 315px). Gộp vào ĐÚNG 1 cột "Trạng thái", layout DỌC 2 dòng mirror cột Mode: dòng 1 = badge trạng thái CHÍNH (Running/Login open/Waiting/Sleeping+countdown/Đã tắt — logic/màu/tooltip giữ NGUYÊN 100%); dòng 2 = hàng ngang 2 `Badge` nhỏ Token + Lỗi (vẫn là Badge thật, không rút gọn thành text). Cột "Hôm nay" (`tasks_done_today`/`tasks_error_today`) bỏ hẳn khỏi bảng — dữ liệu chuyển thành tooltip trên badge Lỗi (`"Hôm nay: ✅ N  ❌ M"`). Bảng 10 cột → 7 cột: `['', 'Tên / Email', 'Mode', 'Trạng thái', 'Task hiện tại', 'Port', 'Hành động']` — renumber `setCellWidget`/`setItem` từ col 3 (Task hiện tại 5→4, Port 8→5, Hành động 9→6). Tiện thể dọn `_TASK_MODE_LABEL` (dict mồ côi sót lại từ bản nháp redesign cột Mode, không dùng ở đâu).

**"Làm mới profile (xóa sạch — về trắng)" — tính năng MỚI, TIẾN HOÁ QUA 3 BƯỚC trong cùng ngày:**
1. Bắt đầu từ yêu cầu "thêm xóa cache cho profile: cache, cookie, browing history - all time" — mirror 3 checkbox "Clear browsing data" của Chrome, xoá THẲNG file/thư mục trên đĩa trong `{profile_dir}/Default/` (không gọi CDP/tương tác `chrome://settings/...` — không đáng tin cậy để script hoá, giống các trường hợp khác trong project đã né tránh tương tác trang `chrome://`).
2. User báo bug thật "đã đóng trình duyệt và xóa cache profile mà vào lại profile vẫn có login sẳn" — bản đầu chỉ xoá `Cookies`/`Network/Cookies` (session cookie TỪNG WEBSITE) nhưng bỏ sót `Web Data`. Chrome có 2 tầng đăng nhập Google TÁCH BIỆT: (1) cookie từng site và (2) đăng nhập CẤP TRÌNH DUYỆT ("Account consistency" — chip tài khoản góc trên) — refresh token OAuth của tầng (2) nằm trong bảng `token_service` bên trong `Web Data`, KHÔNG nằm trong `Cookies`. Chrome tự dùng token đó TỰ SINH LẠI cookie google.com/labs.google mỗi lần khởi động — đây là lý do xoá `Cookies` xong vẫn tự động login lại ngay (khớp đúng hành vi Chrome UI thật: "Clear browsing data" + tick Cookies vẫn ghi chú "You'll remain signed in to your Google Account(s)" — phải vào `chrome://settings/people` "Sign out" mới dứt điểm).
3. User yêu cầu tiếp "Còn gì cần xóa thì xóa sạch coi như 1 profile trắng" — thay vì tiếp tục vá từng chỗ sót (đã sót 1 lần, có thể còn sót thứ khác chưa phát hiện), BỎ HẲN cách chọn lọc từng file/nhóm, đổi sang XOÁ SẠCH TOÀN BỘ nội dung `profile_dir` (không chỉ `Default/` mà cả file gốc `--user-data-dir` như `Local State` — os_crypt key/DNS cache/browser-level prefs).

**Thiết kế CUỐI CÙNG:** `server/managers.py::_wipe_chrome_profile_dir(profile_dir)` — `os.listdir(profile_dir)` rồi xoá TỪNG entry (file hoặc thư mục), giữ nguyên CHÍNH thư mục `profile_dir` (rỗng) để Chrome tự dựng lại toàn bộ cấu trúc khi khởi động tiếp — coi như Chrome CHƯA TỪNG chạy ở đây. `pm.clear_browser_data(profile_id)` gọi hàm này, KHÔNG xoá record DB (khác `delete(delete_data=True)` — action đó xoá LUÔN profile khỏi danh sách; đây chỉ làm sạch dữ liệu Chrome, giữ nguyên name/worker_mode/task_mode/project_url...). Extension load qua flag/path NGOÀI `profile_dir` (xem đầu file) nên không bị ảnh hưởng.

- Route `POST /api/selenium/profiles/<id>/clear_data` (`server/routes.py`) — 409 nếu `pid in _workers` (worker đang chạy) hoặc `pid in _login_drivers` (login browser đang mở) — xoá file lúc Chrome giữ handle dễ corrupt profile, mirror guard `_stop_worker()` đã có ở `delete_profile()`.
- GUI: menu item "🧹 Làm mới profile (xóa sạch — về trắng)" disabled + tooltip khi profile đang chạy/login mở; bấm được → `QMessageBox.question` CẢNH BÁO RÕ mất TOÀN BỘ dữ liệu Chrome (đăng nhập/mật khẩu/bookmark/cache/cookie/lịch sử/mọi cài đặt khác), hành động không hoàn tác, trước khi gọi API.

Verify: test `_wipe_chrome_profile_dir()` trên thư mục giả lập có cả file gốc (`Local State`) lẫn `Default/` (Preferences/Login Data/Web Data/Cache) — xác nhận xoá sạch 100% nội dung, `profile_dir` vẫn còn (rỗng). `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật** — cần user tự thử.

**Verify:** `py_compile`/`pyflakes` sạch 4 file (`gui/pages/profiles_page.py`, `gui/style.py`, `server/managers.py`, `server/routes.py`). Smoke test headless PyQt6 thật (`QT_QPA_PLATFORM=offscreen`) — dựng `ProfilesPage`, `_on_data()` với 5 profile giả (đủ `worker_mode` api/dom/gemini/chatgpt/gemini_video, đủ trạng thái running/login_open/idle/disabled) → populate không lỗi; `_build_actions_menu()` từng profile → đúng action list + đúng enable/disable (profile `login_open` → "Xóa cache" disable, đổi label "Mở"→"Đóng login browser"). Test riêng `_clear_chrome_browser_data()` trên thư mục giả cấu trúc Chrome profile thật (`Cache/`, `Network/Cookies`, `History`, `Favicons`, `Local Storage/` + file PHẢI SỐNG SÓT `Preferences`/`Login Data`/`Bookmarks`/`Extensions/`) — đúng xoá 5 mục cache/cookie/history, đúng giữ nguyên 4 mục còn lại; dọn sạch thư mục test. **CHƯA verify end-to-end qua Flask route thật trên app GUI thật** — môi trường này không launch được app đầy đủ, cần user tự bấm thử.

---

### 11.30 FIX THẬT: VEO chọn sai/không xác nhận model — retry + VERIFY thật sự đã áp dụng (2026-08-07)

User báo "trong tạo image/video bằng VEO tôi thấy phương án chọn model của bạn còn dính lỗi nhiều chọn sai model" — điều tra trực tiếp trên Chrome ĐANG MỞ của profile `huavantien84` (id=27, 1 "login browser" sẵn có, `active_workers: []` — attach qua `_connect_to_chrome(9327)`, KHÔNG mở Chrome mới, không đụng phiên user đang có).

**Bằng chứng thật tìm được:** đọc `profile_logs` — task #4666 (2026-08-05 17:25:12) log `DOM: model "Veo 3.1 - Lite [Lower Priority]" not found in dropdown` — code CŨ (§ "Chọn model" ở §5 phần DOM mode) chỉ log warning rồi **ĐI TIẾP submit task**, để nguyên model CŨ còn lưu từ task trước trong state project Flow. Task báo "thành công" nhưng generate với **SAI MODEL**, không cách nào phát hiện từ log — đúng khớp "chọn sai model". Danh sách model/icon `arrow_drop_down`/`crop` trên trang đã kiểm tra lại, KHÔNG đổi so với lần verify trước cùng ngày, và DUY NHẤT trên trang (không có button trùng icon gây nhầm target) — loại trừ giả thuyết "Google đổi UI".

**Root cause:** block chọn model trong `_dom_configure()` chỉ có **1 lần thử** tìm+click, và dù thành công hay thất bại đều **KHÔNG XÁC NHẬN LẠI** — chỉ tin "tìm thấy item + đã click = xong". Khi tìm-item timeout (dropdown vừa mở, animation/render chưa ổn định — đặc biệt ngay sau khi vừa click ratio/count), code chỉ log warning, KHÔNG retry, KHÔNG chặn task — task tiếp tục chạy, sinh kết quả sai model, tốn quota thật.

**Fix (`server/worker.py`):** `ModelSelectionFailed(RuntimeError)` (exception riêng, không bị nuốt bởi try/except chung của `_dom_configure()`) + `_dom_select_model_verified(model, attempts=3)` — mỗi lần thử: mở dropdown → tìm item (MODEL_MATCH_JS, giữ nguyên logic khớp-chính-xác) → click → **POLL XÁC NHẬN** (đọc lại label trigger `arrow_drop_down`, strip tiền tố trang trí, so khớp ĐÚNG model muốn — 1 bước `_wait_js` kết hợp cả "chờ UI cập nhật" lẫn "xác nhận đúng nội dung") — hết `attempts` lần vẫn không xác nhận được → dọn dẹp dropdown (Escape) rồi `raise ModelSelectionFailed(...)` kèm model THẬT SỰ đang hiển thị. `_dom_configure()`'s except-clause tách riêng `except ModelSelectionFailed: raise` (không nuốt) TRƯỚC `except Exception` chung — các bước KHÁC (tab/ratio/count/upload) vẫn best-effort như cũ, CHỈ RIÊNG model mới siết chặt vì cái giá chọn sai là tốn quota thật. `ModelSelectionFailed` lan thẳng tới except-block sẵn có của `_run_task_dom()`/`_run_tasks_batch()` → task tự động error/retry qua cơ chế ĐÃ CÓ, không cần sửa gì thêm ở 2 call site.

**Bug thứ 2 bắt được NGAY LÚC verify (không phải giả thuyết):** nếu raise mà không dọn dropdown, lần gọi KẾ TIẾP hỏng theo kiểu KHÁC hẳn (không tìm thấy cả nút `arrow_drop_down` — click sau đó TOGGLE ĐÓNG dropdown đang mở dở thay vì mở mới) — đã fix bằng Escape cleanup trước khi raise.

**Verify THẬT trên Chrome đang mở của huavantien84 (attach, không mở mới):**
- 5/5 model video + 2/2 model ảnh chọn đúng ngay lần 1 (điều kiện bình thường, không cần retry).
- Model KHÔNG TỒN TẠI → `ModelSelectionFailed` raise ĐÚNG (không bị nuốt) — verify hard-fail hoạt động.
- Fix cleanup (Escape trước khi raise) — verify lần gọi kế tiếp phục hồi bình thường ngay lần đầu (trước fix: hỏng theo kiểu khác).
- **Test FULL `_dom_configure()`** (tab+ratio+count+model CÙNG LÚC, xen kẽ video/ảnh liên tiếp — đúng ĐÚNG shape 1 task thật gọi, không chỉ test cô lập riêng model) — 5/5 case đúng model.
- Đã khôi phục model thật của profile (`Veo 3.1 - Lite [Lower Priority]`) trước khi kết thúc, không để lại state lạ trên phiên Chrome user đang mở.

**⚠️ FIX THẬT tiếp theo (2026-08-12) — "chớp sáng như click chọn model nhưng ko chọn đúng":** user báo mode video DOM cứ thấy hiệu ứng click nhưng model không đổi đúng. Root cause: `find_drop_btn_js` (tìm nút trigger dropdown, icon `arrow_drop_down`) tìm qua `document.querySelectorAll('button')` — KHÔNG SCOPE vào popup cấu hình, khác `find_btn_in_popup()` (ratio/count/sub-tab) đã scope đúng từ đầu. labs.google thật có NHIỀU nút dropdown khác dùng CHUNG icon này (project picker, account menu...) — `.find()` luôn trả nút ĐẦU TIÊN theo DOM order toàn trang, không chắc đúng nút model trong popup. Click "trúng" nút khác → 1 click THẬT xảy ra ở đâu đó (đúng "chớp sáng" user thấy) nhưng không phải model dropdown → model không đổi; bước verify (dùng ĐÚNG hàm tìm-nút bị lệch) có thể "khớp giả" hoặc luôn báo lệch tuỳ tình huống — cả 2 khớp đúng triệu chứng. Fix: `_dom_select_model_verified()` nhận thêm `popup` (từ `get_popup()`, CÙNG element `find_btn_in_popup()` dùng cho ratio/count) — scope tìm kiếm vào ĐÚNG popup thay vì `document`. Dùng `window.__modelPopupRoot` (không dùng `arguments[N]`) vì JS này bị nhúng qua NHIỀU lớp IIFE lồng nhau (`read_label_js`, khối "applied") — mỗi IIFE gọi không tham số nên `arguments[0]` bên trong luôn `undefined`, chỉ `window` mới "xuyên" được mọi lớp, cùng kỹ thuật `window.__selUpload` ở `_dom_upload_images()`. `MODEL_MATCH_JS` (tìm menu item SAU KHI dropdown mở) CỐ Ý giữ nguyên không scope — menu items Material-style thường render qua portal gắn `document.body`, không nằm trong popup subtree. `finally` dọn `window.__modelPopupRoot=null` sau khi hàm kết thúc (mọi nhánh thoát). Verify: `py_compile`/`pyflakes` sạch, cả 3 lớp JS lồng nhau parse hợp lệ qua `node -e "new Function(js)"`. **CHƯA verify trên Chrome/labs.google thật** (môi trường không có jsdom/browser) — cần user tự chạy 1 task video DOM xác nhận.

---

### 11.31 Mật khẩu Google + tự đăng nhập lại sau khi xoá cache profile — `_ensure_google_login()` (2026-08-08)

User yêu cầu: "trong profile thêm input password: sau khi xóa cache lần chạy sau phải login: https://accounts.google.com/ dùng email/pass để đăng nhập mail xong mới về lại task hiện tại để chạy tiếp".

**Bối cảnh:** "Làm mới profile — xóa sạch" (§11.29) xoá TOÀN BỘ `profile_dir` kể cả session Google đã đăng nhập — lần chạy kế tiếp, MỌI navigate tới labs.google/gemini.google.com/chatgpt.com bị Google tự redirect sang `accounts.google.com`. Trước đây worker kẹt vô thời hạn ở đó (không tìm thấy UI mong đợi → lỗi/timeout liên tục), cần user tự mở "Login browser" đăng nhập tay.

**Lưu trữ (2 repo):**
- `ToolSub` gốc: migration `_ensure_worker_profile_password()` (`backend/core/migrations.py`) — thêm `selenium_profiles.account_password VARCHAR(255) DEFAULT ''` (additive, đã chạy trên DB production thật). `_PROFILE_UPDATE_ALLOWED` + `create_worker_profile()`'s INSERT (`backend/routes/worker_profiles.py`) thêm field này.
- `client_tool`: `gui/profile_dialog.py` — field "Mật khẩu" (`QLineEdit.EchoMode.Password`, che ký tự) cạnh Email, áp dụng cho MỌI loại profile (không riêng nhóm nào — mọi worker_mode dùng chung 1 Chrome profile_dir có thể bị "Làm mới" xoá sạch). `server/managers.py::pm.create()`/`pm.update()` + `server/routes.py::create_profile()` forward field mới xuống backend.

**Bug thật bắt được TÌNH CỜ lúc sửa** (không liên quan mật khẩu) — `pm.create()`'s whitelist `worker_mode` THIẾU `'gemini_video'` từ lúc thêm loại profile đó (§11.27, 2026-08-07): MỌI profile tạo mới qua GUI chọn "🎬 Gemini — Tạo Video" bị ÂM THẦM reset về `'api'` ngay tại `managers.py` TRƯỚC KHI gửi lên backend (backend's whitelist đã đúng từ đầu — chỉ riêng bản sao ở client_tool bị sót). Đã thêm `'gemini_video'` vào. Backend cũng chưa kịp mở comment vá tương tự.

**`_ensure_google_login(target_url)`** (`server/worker.py`) — bản đầu (mô tả ngay trên) chỉ PHẢN ỨNG sau khi đã `driver.get(target_url)` và bị Google redirect sang `accounts.google.com`. **ĐỔI HƯỚNG ngay trong ngày (2026-08-08) theo yêu cầu tiếp theo của user**: "giờ fix lại tất cả khi khởi động phải vào accounts.google.com trước nếu không có input login nghĩa là đã có login thì vào url task cần thiết, còn nếu có input thì login tài khoản vào" — giờ hàm này CHỦ ĐỘNG là điểm điều hướng DUY NHẤT: LUÔN `driver.get('https://accounts.google.com/')` trước, kiểm tra `input[type="email"]` có tồn tại không (CÓ = chưa đăng nhập, KHÔNG = đã đăng nhập sẵn), rồi mới quyết định:
  - KHÔNG có ô email → đã đăng nhập sẵn → `driver.get(target_url)` luôn.
  - CÓ ô email → tự nhập `account_email`/`account_password` đã lưu (nút Next qua `#identifierNext`/`#passwordNext` + fallback text "Next"/"Tiếp theo" — ID ổn định nhiều năm trên Google Identity Platform, xem `_click_google_next(step)`), chờ tối đa 30s rời khỏi `accounts.google.com`, rồi mới `driver.get(target_url)`.

CALLER giờ KHÔNG tự `driver.get(target_url)` trước khi gọi nữa — hàm này tự lo TOÀN BỘ điều hướng (đã bỏ các dòng `driver.get(...)`/`_sleep(...)` cũ ở mọi call site, tránh navigate 2 lần thừa). Trả `False` (không raise) nếu thiếu email/password đã lưu, hoặc Google yêu cầu bước xác minh khác (2FA/captcha/"xác nhận đây là bạn" — KHÔNG tự động hoá được, đứng lại ở accounts.google.com thay vì vào target) — mọi bước đều `self._log(...)` (tiền tố `[google-login]`) vào `profile_logs`.

**Lý do đổi hướng:** cách cũ (phản ứng SAU khi navigate target) có rủi ro không nhận diện được nếu Flow/Gemini/ChatGPT xử lý trạng thái "chưa đăng nhập" theo kiểu KHÁC redirect ngay (vd hiện màn hình chờ/nút "Đăng nhập" cục bộ) — trong khi accounts.google.com LUÔN cho biết chính xác trạng thái đăng nhập qua sự có mặt của `input[type="email"]`, không phụ thuộc trang đích xử lý ra sao.

**Wired vào 6 điểm navigate ĐẦU 1 phiên chạy/1 task (CHỈ VEO3/Gemini, KHÔNG ChatGPT)** — mỗi nơi thay hẳn `driver.get(...)+_sleep(...)` cũ bằng gọi DUY NHẤT `_ensure_google_login(target)`: `_ensure_flow_page()`, `_ensure_flow_project()` (VEO3 dom/api), `_run_task_gemini_video()`, `_run_task_gemini()`, `_run_gemini_loop()` (khởi động), `run()`'s nhánh setup `gemini_video`.

**⚠️ KHÔNG áp dụng cho ChatGPT** (`_run_task_chatgpt()`/`_run_chatgpt_loop()`) — bản đầu (mô tả ngay trên) có gọi "best-effort" ở đây với lý giải "no-op rẻ nếu không cần" — SAI sau khi đổi hướng: hàm giờ LUÔN chủ động detour qua accounts.google.com bất kể có cần hay không, mà ChatGPT dùng hệ đăng nhập RIÊNG của OpenAI (chatgpt.com/auth), không liên quan gì tới trạng thái đăng nhập Google — detour này chỉ tốn thời gian vô ích, không nói lên được gì về việc ChatGPT đã đăng nhập hay chưa. Đã bỏ hẳn 2 lời gọi này, giữ nguyên `driver.get(CHATGPT_URL)` như code gốc.

**⚠️ CHƯA áp dụng cho `_gemini_slot_step()`** (round-robin nhiều tab, `gemini_max_concurrent_tabs>1`) — đây là state machine KHÔNG-BLOCKING (mỗi lần gọi chỉ làm 1 bước nhỏ rồi return để vòng lặp xoay qua tab khác); `_ensure_google_login()` có thể block ~20-30s+ (detour accounts.google.com + gõ email/password nếu cần) — chèn vào đây sẽ treo CẢ round-robin, ảnh hưởng các tab/slot khác đang chờ tới lượt. Cần tách thành các bước nhỏ không-blocking riêng (mirror pattern `_gemini_attach_file_kickoff`/`_gemini_attach_file_poll`) nếu muốn hỗ trợ đầy đủ — CHƯA làm.

**Verify THẬT trên Chrome của huavantien84** (Stop worker + tạm `enabled=0` để tránh tranh chấp driver với worker thật, mở Chrome RIÊNG bằng `_make_driver()` — session Google đã login sẵn từ trước): gọi `_ensure_google_login(project_url)` — log xác nhận ĐÚNG thứ tự: vào accounts.google.com trước → "Đã đăng nhập sẵn (không có ô nhập email)" → tự vào project URL → `current_url` cuối cùng đúng project URL, không còn ở accounts.google.com. Xác nhận nhánh "đã đăng nhập" (nhánh chạy THƯỜNG XUYÊN NHẤT trong thực tế — session Google bền vững qua nhiều task, chỉ mất khi "Làm mới profile") hoạt động đúng trên tài khoản thật. Đã khôi phục profile về `enabled=1` + Start lại trước khi kết thúc. **CHƯA verify được nhánh "chưa đăng nhập, tự nhập email/password"** end-to-end trên browser thật — không có kịch bản an toàn để test (cần wipe 1 profile đang hoạt động thật + biết chắc mật khẩu đúng, rủi ro khoá tài khoản nếu tự động hoá gặp vấn đề).

**⚠️ Bảo mật — LƯU PLAINTEXT:** không có hạ tầng mã hoá field-level nào trong dự án (cùng quy ước với `account_email`/mọi field khác của `selenium_profiles`). Route `/api/worker_profiles*` gate `_require_tool_media` (chỉ user group 'Tool Media') nhưng CHƯA giới hạn theo owner riêng cho field này — mọi thành viên group đọc được password của MỌI profile qua GET. Chỉ nên dùng cho tài khoản Google TẠO RIÊNG cho automation, KHÔNG dùng tài khoản cá nhân.

**Verify:** `py_compile`/`pyflakes` sạch cả `gui/profile_dialog.py`/`server/managers.py`/`server/routes.py`/`server/worker.py`. Migration đã chạy trực tiếp trên DB production thật, xác nhận cột tồn tại đúng kiểu. **⚠️ CHƯA verify luồng đăng nhập THẬT trên browser** (không có sẵn kịch bản an toàn để test — cần 1 profile đã "Làm mới" + email/mật khẩu thật hợp lệ, rủi ro dừng đúng thiết kế nếu tài khoản có 2FA nhưng chưa xác nhận trực tiếp). **Backend (ToolSub) đang chạy live CẦN RESTART** để nạp whitelist mới (nếu không, PATCH/POST `account_password` bị bỏ qua lặng lẽ). **`main.py` (client_tool GUI) cũng CẦN RESTART** để nạp `_ensure_google_login()`.

**FIX THẬT (2026-08-08, cùng ngày) — selector SAI, verify bằng DOM thật của huavantien84@gmail.com + đối chiếu `extensions/panel/panel.js`:** User báo "đang check login accont goole chưa chính xác hãy tự mở profile vào account đăng nhập trực tiếp lấy thì thông tin user huavantien84 để lấy đúng input, ngoài ra: `extensions/` cũng có chức năng login như vậy hãy tham khảo".

- **Tham khảo `extensions/panel/panel.js`** ("Relogin Gmail", PROVEN chạy thật trong production — cơ chế khác: `chrome.scripting.executeScript`, không phải Selenium+CDP) — lộ ra bản đầu SAI hướng ở nút "Next": extension KHÔNG hề dùng id `#identifierNext`/`#passwordNext`, chỉ so khớp theo TEXT với từ khoá đa ngôn ngữ (`'tiếp theo'`,`'next'`,`'đăng nhập'`,`'sign in'`,`'continue'`).
- **Verify TRỰC TIẾP bằng Chrome MỚI HOÀN TOÀN** (temp profile riêng — KHÔNG đụng `profile_dir` thật của huavantien84, tránh rủi ro session production — chỉ gõ **email thật** `huavantien84@gmail.com` để trang load đúng ngữ cảnh, KHÔNG BAO GIỜ gõ/đoán mật khẩu) — dump DOM THẬT, xác nhận **2 bug thật**:
  1. Ô email THẬT `id="identifierId"` `name="identifier"` nhưng **`type="text"`** — KHÔNG PHẢI `type="email"` như bản đầu đoán → selector cũ KHÔNG BAO GIỜ khớp được, đây chính là điều user gọi "chưa chính xác".
  2. Nút "Next" (`jsname="LgbsSe"`) **hoàn toàn không có id** (`id=""` rỗng) — `#identifierNext`/`#passwordNext` không tồn tại trong DOM thật hiện tại.
  3. (Phòng thêm) bước email có sẵn 1 input ẩn `name="hiddenPassword" type="password"` (decoy chống autofill) — cần lọc visibility khi tìm ô mật khẩu, tránh khớp nhầm.
- **Fix:** `_click_google_next()` viết lại HOÀN TOÀN — bỏ hẳn tìm theo id, CHỈ so khớp TEXT (`_GOOGLE_NEXT_KEYWORDS`, cùng danh sách với `extensions/panel/panel.js`) trên mọi `button` đang hiển thị. Selector email đổi chain `#identifierId, input[name="identifier"], input[type="email"]`. Selector password thêm lọc visible + `input[name="Passwd"]`.
- **Verify:** end-to-end THẬT (Chrome mới, không phải profile production) dùng ĐÚNG hàm production trên trang đăng nhập THẬT của `huavantien84@gmail.com`: email tìm đúng qua `#identifierId` → gõ đúng qua CDP → `_click_google_next()` tìm+click đúng → URL chuyển đúng sang `challenge/pwd` → tìm đúng ô password thật (visible), không khớp nhầm field ẩn. Dừng lại đúng ở bước này (không gõ/đoán mật khẩu) — đủ xác nhận cả 2 bug đã fix đúng mà không cần biết mật khẩu thật.

**FIX THẬT (2026-08-08, cùng ngày) — worker vẫn "đá sang Flow" dù đăng nhập THẤT BẠI, không check giá trị trả về:** User báo (sau khi restart `main.py` với các fix ở trên): "sau khi chạy lại client_tool/ sao cứ vào account có input ko chịu mà cứ đá sang flow mặc dù chưa login". Kiểm tra DB thật xác nhận `account_password` RỖNG cho MỌI profile (chưa ai kịp điền qua GUI) — `_ensure_google_login()` hoạt động ĐÚNG thiết kế (vào accounts.google.com, thấy ô email, thấy thiếu mật khẩu đã lưu → log lỗi rõ ràng → trả `False`), nhưng **caller không hề kiểm tra giá trị trả về**.

Root cause: `_ensure_flow_page()`/`_ensure_flow_project()` có sẵn đoạn "recovery" TỪ TRƯỚC (mục "Fix Create with Google Flow", 2026-08-10) — xử lý trường hợp bấm "Create with Google Flow" bị lệch hướng, tự `driver.get(url)` LẠI nếu `current_url` không chứa `/project/`. Đoạn này KHÔNG PHÂN BIỆT ĐƯỢC "vừa bấm Create with Flow bị lệch" (trường hợp gốc) với "đang đứng ở accounts.google.com vì đăng nhập thất bại" (trường hợp mới) — cả 2 đều "current_url không chứa /project/" — nên NHẦM (b) thành (a), cứ `driver.get(url)` lại bất kể đăng nhập được hay chưa.

Fix: `_ensure_flow_page()`/`_ensure_flow_project()` thêm `if not self._ensure_google_login(url): ...; return` NGAY SAU lời gọi — return SỚM, bỏ qua HẲN đoạn recovery phía sau khi đăng nhập thất bại. `_run_task_gemini()`/`_run_task_gemini_video()` thêm `raise RuntimeError(...)` rõ ràng thay vì im lặng đi tiếp (lỗi thật trước đây xảy ra ở bước SAU với thông báo khó hiểu, vd "không tìm thấy mục Tạo video"). `_run_gemini_loop()`/`run()`'s setup `gemini_video` — log "sẵn sàng nhận task" đổi CÓ ĐIỀU KIỆN theo kết quả đăng nhập.

Verify: test THẬT tái hiện ĐÚNG kịch bản user báo (Chrome mới hoàn toàn, profile giả có `account_email` nhưng `account_password` RỖNG — khớp chính xác trạng thái DB thật) — gọi `_ensure_flow_page()`, xác nhận DỪNG LẠI đúng ở accounts.google.com, KHÔNG còn bị "đá sang" labs.google (trước fix: nhảy sang labs.google dù chưa đăng nhập).

**ĐỔI HƯỚNG entry point (2026-08-10) — vào gmail.com thay vì accounts.google.com trực tiếp:** theo yêu cầu user "viết lại luồng check bằng cách vào https://gmail.com/ ko vào accounts.google.com nữa, nếu gmail.com đá sang https://mail.google.com/ thì đã login, còn đá sang domain: https://workspace.google.com/ thì click vào button... để vào trang login". `_ensure_google_login()` giờ `driver.get('https://gmail.com/')` trước (thay vì thẳng `accounts.google.com/`), đọc URL sau redirect: `mail.google.com` = đã đăng nhập (vào thẳng `target_url`); `workspace.google.com` (trang marketing Gmail cho khách chưa đăng nhập) = chưa đăng nhập — tìm nút "Sign in" trên trang đó (khớp `textContent`, danh sách biến thể `googleSignIn` trong `i18n_texts.json`/`_DEFAULT_I18N_TEXTS`, cùng pattern `newProject`/`createWithFlow`) rồi điều hướng tới đích của nó (`accounts.google.com/AccountChooser/signinchooser?continue=...`, xác nhận qua HTML thật user gửi); URL không khớp cả 2 case (hiếm) → best-effort đi thẳng `target_url`. Toàn bộ phần SAU khi vào được trang đăng nhập thật (tìm ô email/mật khẩu, bấm "Tiếp theo", chờ rời khỏi `accounts.google.com`) GIỮ NGUYÊN 100% — chỉ đổi CÁCH VÀO, không đổi DOM interaction đã verify ở mục "FIX THẬT" ngay trên.

**⚠️ ĐỔI NGAY SAU (cùng ngày) — click THẬT thay vì đọc href, tránh Google báo "trình duyệt không hợp lệ":** user yêu cầu tiếp "không đi thẳng vào accounts.google.com mà phải click button đăng nhập từ workspace.google.com tránh bị là trình duyệt ko hợp lệ". Bản đầu ở trên đọc THẲNG `href` của thẻ `<a>` rồi `driver.get(href)` — bỏ qua thao tác click thật, Google's Identity Platform có thể coi điều hướng kiểu này "không tự nhiên" (thiếu ngữ cảnh click/referrer của 1 cú click thật) và trả lỗi "This browser or app may not be secure". Fix: dùng `_cdp_click_el()` (CDP mouse event thật, ĐÃ chứng minh ổn định ở nhiều nơi khác trong file) click THẬT vào nút "Sign in". Nút mang `target="_blank"` nên click thật MỞ TAB MỚI thay vì navigate tab hiện tại — thêm logic theo dõi `window_handles` (poll 8s bắt tab mới), `switch_to.window()` sang tab đó, rồi ĐÓNG tab `workspace.google.com` cũ (tránh tab thừa gây nhiễu code khác trong worker vốn giả định chỉ có 1 tab hoạt động). Phần SAU (tìm ô email/mật khẩu...) chạy trong tab MỚI, logic DOM không đổi — `self._cdp(...)`/`self.driver.execute_script(...)` tự follow đúng tab đang active sau `switch_to.window()` (hành vi chuẩn Selenium 4.x, không cần code thêm).

`py_compile`/`pyflakes` sạch cả 2 lần đổi, JS snippet tìm nút "Sign in" (trả về element, không phải href string) verify qua `node -e "new Function(js)"`. **CHƯA verify trên Chrome/gmail.com thật.**

**⚠️ FIX THẬT (cùng ngày) — bug thật khiến "chạy task gemini chỉ mở profile mà ko vào gemini":** root cause là bước đọc `current_url` sau `driver.get('https://gmail.com/')` dùng `self._sleep(3)` CỐ ĐỊNH rồi đọc NGAY — redirect+bootstrap Gmail đầy đủ có thể lâu hơn 3s thật (mạng chậm/cold start), đọc quá sớm bắt trúng URL trung gian → phân loại SAI trạng thái đăng nhập → rơi nhầm nhánh (kể cả tưởng "chưa đăng nhập" dù tài khoản ĐÃ đăng nhập sẵn — case phổ biến nhất với profile automation tái sử dụng). Fix: đổi sang POLL (tối đa 15s, mỗi 0.5s) tới khi `current_url` khớp `mail.google.com`/`workspace.google.com`, cùng pattern `_wait_js()` đã dùng khắp file — không còn đoán 1 con số sleep cố định cho bước redirect quan trọng nhất của cả luồng. `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật.**

**Vòng tự phục hồi khép kín — tự "Làm mới profile" khi rơi vào sleeping do lỗi nhiều (2026-08-10, cùng ngày):** theo yêu cầu tiếp "ok rồi luồng mới nếu rơi vào trạng thái ngủ do lỗi nhiều, thì sẽ tự 'làm mới profile' đó" — nối `_ensure_google_login()` (đăng nhập lại tự động) với "Làm mới profile" (§11.29, xoá sạch `profile_dir`) thành 1 vòng khép kín. Hàm mới `_clear_profile_if_sleeping(profile_row)` (đặt cạnh `_handle_task_error()`/`_reset_flow_project()`) — gọi trong `finally` của CẢ 4 vòng lặp worker (`run()`, `_run_gemini_loop()`, `_run_gemini_loop_concurrent()`, `_run_chatgpt_loop()`) NGAY SAU `self.driver.quit()` (Chrome đã đóng hẳn, an toàn đụng filesystem). Nếu `profile_row.status == 'sleeping'` (vừa set bởi `_record_error()`/`_handle_task_error()` ngay trước khi thread thoát) → tự dọn dẹp profile — coi lỗi liên tiếp là dấu hiệu cache Chrome đã hỏng theo cách nào đó, dọn trước khi dispatcher đánh thức lại (KHÔNG cần user can thiệp).

**⚠️ (2026-08-12, ĐỔI HƯỚNG) chỉ xoá CACHE, không còn đăng xuất Google:** theo yêu cầu user "lỗi nhiều vào trạng thái ngủ tự xóa cache không xóa cookie" — bản đầu (2026-08-10) gọi `pm.clear_browser_data()` (xoá SẠCH TOÀN BỘ `profile_dir`, kể cả `Cookies`/`Web Data`/`Login Data` — hành vi ĐÚNG cho nút "Làm mới profile" THỦ CÔNG, nhưng quá tay khi áp dụng cho đường TỰ ĐỘNG này: mỗi lần sleeping đều mất đăng nhập, buộc lần chạy kế tiếp phải qua `_ensure_google_login()`'s nhánh nhập mật khẩu, tốn thời gian + tăng rủi ro Google chặn đăng nhập lặp lại). Giờ gọi `pm.clear_cache_only()` (`server/managers.py::_clear_chrome_cache_only()`, MỚI, quét NÔNG gốc `profile_dir`+1 cấp con — CHỈ xoá `Cache`/`Cache2`/`Code Cache`/`GPUCache`/`DawnCache`/`DawnGraphiteCache`/`GrShaderCache`/`ShaderCache`/`Media Cache`, GIỮ NGUYÊN `Cookies`/`Web Data`/`Login Data`/`History`/mọi thứ khác) — profile vẫn đăng nhập sẵn ở lần chạy kế tiếp, `_ensure_google_login()` nhận diện "đã đăng nhập" ngay. Nút "Làm mới profile" thủ công trong GUI (đã có dialog cảnh báo mất đăng nhập) **GIỮ NGUYÊN**, vẫn gọi `pm.clear_browser_data()`/`_wipe_chrome_profile_dir()` như cũ — chỉ đường TỰ ĐỘNG này đổi.

**Fix kèm theo:** `_run_gemini_loop()`/`_run_gemini_loop_concurrent()`/`_run_chatgpt_loop()` TRƯỚC ĐÂY unconditionally ghi đè status→`'offline'` trong `finally`, KHÔNG check 'sleeping' trước (khác nhánh VEO3 của `run()` vốn đã có check này) — status 'sleeping' bị xoá ngay. Giờ cả 4 vòng lặp dùng CHUNG `_clear_profile_if_sleeping()`, đồng nhất hành vi trên MỌI worker_mode.

Verify: unit test cô lập (mock `pm`) — 4 kịch bản (sleeping→gọi đúng+trả True, không sleeping→không gọi+trả False, `None`→không gọi+trả False, sleeping+lỗi khi clear→vẫn trả True không propagate exception) đều pass. `_clear_chrome_cache_only()` verify trực tiếp trên thư mục Chrome-profile giả (2026-08-12) — đúng xoá 4 thư mục cache (kể cả `ShaderCache` ở gốc, KHÁC `Default/`), 7 mục còn lại (`Cookies`/`Web Data`/`Login Data`/`Preferences`/`History`/`Bookmarks`/`Local Storage`) sống sót nguyên vẹn. `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật** — cần user quan sát 1 lần profile sleeping thật, xác nhận log `[auto-refresh] ✔ Đã xoá cache...` và KHÔNG bị đá ra màn hình đăng nhập ở lần chạy kế tiếp.

**Mở rộng — xoá thêm cookie KHÔNG liên quan đăng nhập (2026-08-14):** theo yêu cầu tiếp "khi lỗi quá nhiều ngoài xóa cache xóa thêm các cookie không liên quan đăng nhập". `_purge_non_login_cookies(profile_dir)` (`server/managers.py`, MỚI) — mở TRỰC TIẾP SQLite Cookies DB (`Default/Network/Cookies` — Chrome mới, fallback `Default/Cookies`). Bản đầu: `DELETE FROM cookies WHERE host_key` không khớp bất kỳ keyword nào trong `_LOGIN_COOKIE_HOST_KEYWORDS = ('google','chatgpt','openai')` (2 hệ đăng nhập DUY NHẤT các worker_mode trong dự án chạm tới) — GIỮ NGUYÊN cookie 2 hệ đó, chỉ xoá cookie site khác. `ProfileManager.clear_cache_only()` gọi CẢ `_clear_chrome_cache_only()` LẪN hàm này, gộp kết quả (`removed`/`removedCookies`/`errors`). Nút "Làm mới profile" thủ công (xoá sạch 100% kể cả login) KHÔNG đổi.

**⚠️ ĐỔI HƯỚNG NGAY SAU (2026-08-17) — tiêu chí "cả domain google" QUÁ RỘNG, khiến `removedCookies` gần như luôn 0:** user báo bug thật "khi clear chỉ xóa cache và 0 cookie" — với 1 profile chỉ chạy Flow/Gemini, GẦN NHƯ TOÀN BỘ cookie vốn dĩ đã thuộc domain `*.google.com` (kể cả cookie KHÔNG liên quan đăng nhập — `NID`/`CONSENT`/`1P_JAR`/`AEC`/`DV`/`OTZ`/`ANID`... là analytics/consent/personalization) — giữ NGUYÊN CẢ DOMAIN đồng nghĩa giữ NGUYÊN GẦN NHƯ TẤT CẢ. User làm rõ tiêu chí ĐÚNG: "cookie xóa tất cả để lại SSID" — CHỈ giữ đúng họ cookie "SID" (Google dùng cụm `SID`/`HSID`/`SSID`/`APISID`/`SAPISID` + biến thể `__Secure-1P*`/`__Secure-3P*` mang phiên đăng nhập cross-Google-service thật) — lọc theo **TÊN cookie** (`name`), KHÔNG còn theo domain cho Google nữa. Fix: `_GOOGLE_LOGIN_COOKIE_NAMES` (14 tên chính xác — `SID`/`HSID`/`SSID`/`APISID`/`SAPISID`/`LSID` + 8 biến thể `__Secure-1P*`/`__Secure-3P*SID(CC/TS)`) thay `'google'` khỏi `_LOGIN_COOKIE_HOST_KEYWORDS` (giờ chỉ còn `('chatgpt','openai')` — ChatGPT/OpenAI GIỮ NGUYÊN theo domain, chưa đổi vì không có xác nhận tên cookie session-token thật của ChatGPT để đổi an toàn — đoán sai sẽ vô tình đăng xuất ChatGPT). Câu SQL giờ: giữ lại nếu `name` khớp họ SID **HOẶC** `host_key` khớp domain ChatGPT/OpenAI, xoá mọi thứ còn lại. Kèm fix phụ: `worker.py::_clear_profile_if_sleeping()` TRƯỚC ĐÂY không bao giờ log `result['errors']` (DB bị khoá/lỗi đọc bị nuốt im lặng, hiện ra y hệt "0 cookie đúng nghĩa") — giờ log rõ nếu có lỗi.

Verify: test THẬT (dựng SQLite Cookies DB giả đúng schema Chrome, không mock) — 18 cookie mẫu (7 Google SID-family, 4 Google junk analytics, 1 accounts.google.com junk, 1 `_ga` trên `labs.google`, 2 ChatGPT/OpenAI, 2 domain lạ) → purge đúng xoá **8** (mọi thứ không phải SID-family/ChatGPT-OpenAI), giữ đúng **10** (7 SID-family + 3 ChatGPT/OpenAI) — trước fix chỉ xoá được 2 (2 domain lạ), giữ nguyên 16 (mọi thứ mang `google` trong host_key); DB hỏng/không đọc được → `errors` populate đúng, không còn bị nuốt im lặng; profile chưa từng mở (không có DB) → no-op an toàn. `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome/profile thật** — cần user tự theo dõi 1 lần profile sleeping thật, xác nhận `removedCookies` > 0 khi có cookie junk và vẫn giữ được đăng nhập Google sau đó.

**"Chạy xong batch là xóa cache + cookie luôn" (2026-08-17, cùng ngày) — cơ chế THỨ 2, ĐỘC LẬP, chạy qua CDP thay vì đụng file:** theo yêu cầu tiếp "chạy xong batch là xòa cache + cookie luôn" — MỌI cơ chế ở 2 mục trên (`pm.clear_cache_only()`/`_clear_profile_if_sleeping()`) đụng FILE trên đĩa của `profile_dir`, CHỈ an toàn SAU `driver.quit()` (worker sắp thoát hẳn) — không dùng được ở đây vì worker cần TIẾP TỤC sống qua nhiều batch/heartbeat kế tiếp, không đóng/mở lại Chrome mỗi batch.

`server/worker.py::_cdp_clear_cache_and_cookies()` (MỚI) — dọn dẹp NGAY TRONG LÚC Chrome vẫn đang mở, qua CDP (`Network.enable` đã được gọi sẵn cho MỌI driver lúc mở, xem `_make_driver()`, nên `Network.*` luôn sẵn sàng): `Network.clearBrowserCache` (xoá cache HTTP) + `Storage.getCookies` (đọc TOÀN BỘ cookie của browser context — KHÁC `Network.getCookies` chỉ trả cookie của tab/domain ĐANG ACTIVE, sẽ bỏ sót cookie domain khác đã ghé qua nhưng không có frame nào đang load; fallback `Network.getCookies` nếu `Storage.getCookies` không được hỗ trợ bởi phiên bản CDP/Chrome đang dùng) rồi `Network.deleteCookies` từng cookie (theo `name`+`domain`+`path`) KHÔNG khớp tiêu chí giữ-lại. Import THẲNG `_GOOGLE_LOGIN_COOKIE_NAMES`/`_LOGIN_COOKIE_HOST_KEYWORDS` từ `managers.py` (không định nghĩa lại) — CÙNG tiêu chí với cơ chế disk-based ở 2 mục trên, 2 nơi không lệch nhau theo thời gian nếu 1 trong 2 được sửa sau.

**Wired vào 4 điểm "batch/lô vừa xong"** (mirror đúng 4 điểm `_clear_profile_if_sleeping()` đã có ở worker-lifecycle-end, NHƯNG khác hẳn ở chỗ đây chạy GIỮA CHỪNG vòng lặp, không phải lúc worker thoát):
- `_process_tasks()` (VEO3 dom/api + gemini_video, "batch = tasks nhận từ 1 lần heartbeat", đúng thuật ngữ đã dùng trong docstring/comment sẵn có của hàm này) — biến `escalated` bắt kết quả TRƯỚC khi vào `finally` (cả 3 nhánh dom-batch/api-batch/tuần tự đều gán vào biến này trước mỗi `return`), chỉ dọn nếu KHÔNG escalate — escalate nghĩa là profile sắp `'sleeping'`/worker sắp thoát, để `_clear_profile_if_sleeping()` dọn kỹ hơn qua file ngay sau đó, tránh dọn 2 lần liên tiếp phí công.
- `_run_gemini_loop()`/`_run_chatgpt_loop()` (1-tab tuần tự) — dọn ngay sau MỖI task hoàn tất (1 heartbeat ở 2 luồng này chỉ giao TỐI ĐA 1 prompt, coi là "batch của 1").
- `_run_gemini_loop_concurrent()` (round-robin nhiều tab, §11.24) — dọn ĐÚNG lúc "toàn bộ lô vừa xong" (`not any(s is not None for s in slots)` — mọi slot đã `'done'`+đóng tab), cùng điểm code vừa báo `status='idle'` cho auto-scale.

Best-effort — lỗi CDP (driver vừa chết, tab đã đóng, browser context không hỗ trợ lệnh...) chỉ log, không raise/crash batch.

**Verify:** `py_compile`/`pyflakes` sạch. Test cô lập (mock `_cdp`, không cần Chrome thật) — 3 kịch bản cho `_cdp_clear_cache_and_cookies()`: `Storage.getCookies` hoạt động → xoá đúng tập junk giữ đúng SID-family+ChatGPT/OpenAI; `Storage.getCookies` lỗi → fallback đúng sang `Network.getCookies`, kết quả xoá GIỐNG HỆT; cookie list rỗng → không crash, không gọi `deleteCookies` nào. 5 kịch bản cho biến `escalated` gate trong `_process_tasks()` (dom-batch/api-batch/tuần tự × escalate/không-escalate) — dọn ĐÚNG khi không escalate, KHÔNG dọn khi escalate (đúng thiết kế tránh dọn trùng). **CHƯA verify trên Chrome/quota thật** — cần user tự chạy 1 batch thật (bất kỳ mode nào — VEO3 dom/api, Gemini 1-tab, Gemini round-robin, ChatGPT), xác nhận log `[batch-clean] ✔ Đã xoá cache + N/M cookie...` xuất hiện ngay sau khi batch xong, và session Google/ChatGPT vẫn còn đăng nhập ở batch kế tiếp (không bị đá ra màn hình đăng nhập).

**⚠️ ĐỔI HƯỚNG (2026-08-18, cùng ngày) — TÁCH RIÊNG HẲN 2 cơ chế, danh sách giữ-lại MỚI cho mỗi-batch:** user cung cấp danh sách giữ-lại MỚI (`["SID","HSID","SSID","APISID","SAPISID","__Secure-1P","__Secure-3P","__Secure-next-auth.session-token"]`) + chỉ định phạm vi rõ ràng: "chỉ áp dụng MỖI BATCH giữ lại các cookie trên, còn số 2 khi sleep thì tạm không dùng đến".

- **Mỗi-batch (`_cdp_clear_cache_and_cookies()`):** bỏ hẳn import `_GOOGLE_LOGIN_COOKIE_NAMES`/`_LOGIN_COOKIE_HOST_KEYWORDS` từ `managers.py` — thay bằng danh sách RIÊNG, module-level TRONG `worker.py`: `_BATCH_CLEAN_KEEP_COOKIE_EXACT` (so khớp CHÍNH XÁC — 5 tên Google SID-family cốt lõi + `__Secure-next-auth.session-token`, THAY hẳn domain-based matching cũ cho ChatGPT/OpenAI bằng đúng TÊN cookie session NextAuth thật) + `_BATCH_CLEAN_KEEP_COOKIE_PREFIXES` (so khớp TIỀN TỐ — `__Secure-1P`/`__Secure-3P`, bắt MỌI biến thể `__Secure-1PSID`/`__Secure-1PAPISID`/`__Secure-1PSIDCC`/`__Secure-1PSIDTS`/... trong 1 lần, không cần liệt kê tay từng cái như danh sách CŨ ở `managers.py`) + `_batch_clean_should_keep_cookie(name)` — CHỈ xét TÊN, KHÔNG còn xét `domain`.
- **Sleep-recovery (`managers.py::ProfileManager.clear_cache_only()`):** TẠM TẮT hẳn bước gọi `_purge_non_login_cookies()` — hàm giờ CHỈ còn xoá cache, `removedCookies` luôn `0`. `_purge_non_login_cookies()`/`_GOOGLE_LOGIN_COOKIE_NAMES`/`_LOGIN_COOKIE_HOST_KEYWORDS` GIỮ NGUYÊN code (không xoá) — chỉ tạm không có caller, dễ bật lại sau nếu user đổi ý.
- 2 cơ chế giờ HOÀN TOÀN ĐỘC LẬP (khác file định nghĩa, khác danh sách, khác cách so khớp) — sửa 1 bên không ảnh hưởng bên kia.

Verify: `py_compile`/`pyflakes` sạch. Test THẬT (không mock): `_batch_clean_should_keep_cookie()` — 14 case giữ đúng (8 biến thể `__Secure-1P*`/`__Secure-3P*` qua prefix, `__Secure-next-auth.session-token`), 9 case xoá đúng (gồm `LSID` — có trong danh sách CŨ nhưng KHÔNG có trong danh sách MỚI, xác nhận đúng bị xoá; và `'insider'` — chuỗi chứa substring "sid" nhưng không khớp exact/prefix, xác nhận không match nhầm theo kiểu substring lỏng lẻo). `clear_cache_only()` — dựng SQLite Cookies DB + thư mục Cache giả, xác nhận cache bị xoá đúng nhưng cookie row (kể cả cookie rác rõ ràng) hoàn toàn không bị đụng, `removedCookies=0`. **CHƯA verify trên Chrome thật.**

**⚠️ ĐỔI HƯỚNG LẦN 2 (2026-08-18, cùng ngày) — mỗi-batch bỏ hẳn lọc theo TÊN cookie, chuyển THUẦN theo DOMAIN (chỉ labs.google):** ngay sau bản trên, user yêu cầu tiếp "đổi sang xóa tất cả cookie trong labs.google ko đụng cái khác" — **THAY THẾ HOÀN TOÀN** danh sách giữ-lại theo tên vừa thêm ở mục ngay trên. `_cdp_clear_cache_and_cookies()` giờ chỉ xét `domain` — `_batch_clean_should_delete_cookie(domain)` (THAY `_batch_clean_should_keep_cookie(name)`, tên hàm đảo ngược nghĩa cho khớp logic mới — trả `True` = XOÁ, không phải GIỮ) trả `True` khi domain là `labs.google` hoặc subdomain của nó (so cả 2 dạng cookie Chrome — domain-cookie có tiền tố `.` như `.labs.google`, và host-only cookie không có tiền tố như `labs.google`, cũng như subdomain thật `www.labs.google`). Mọi cookie domain khác (`.google.com`, `accounts.google.com`, `.chatgpt.com`...) hoàn toàn KHÔNG bị đụng — **không cần danh sách "giữ lại" nào nữa**, vì phạm vi xoá đã tự giới hạn đúng qua domain: cookie đăng nhập Google SID-family sống trên `.google.com` (không phải `labs.google`) nên tự động an toàn dù không liệt kê tên. Đã bỏ hẳn 2 hằng số `_BATCH_CLEAN_KEEP_COOKIE_EXACT`/`_BATCH_CLEAN_KEEP_COOKIE_PREFIXES` (thay bằng 1 hằng `_BATCH_CLEAN_TARGET_DOMAIN = 'labs.google'`). Cơ chế #2 (sleep-recovery) KHÔNG đổi gì — vẫn TẠM TẮT xoá cookie như mục ngay trên.

Verify: `py_compile`/`pyflakes` sạch. Test THẬT: `_batch_clean_should_delete_cookie()` 13 case (labs.google + 3 biến thể subdomain/domain-cookie → đúng `True`; `.google.com`/`accounts.google.com`/`.chatgpt.com`/rỗng → đúng `False`; case bẫy `notlabs.google` — trùng hậu tố CHUỖI nhưng KHÔNG phải subdomain thật của labs.google → đúng `False`, xác nhận không match nhầm theo kiểu string-suffix lỏng lẻo). `_cdp_clear_cache_and_cookies()` full end-to-end (mock `_cdp`, 7 cookie mẫu trộn domain: 2 Google SID-family trên `.google.com`, 3 cookie thật trên `labs.google`/`.labs.google`/`www.labs.google`, 1 ChatGPT trên `.chatgpt.com`, 1 cookie bẫy `notlabs.google`) — xoá ĐÚNG 3/7 (chỉ 3 cookie labs.google), giữ nguyên 4 cookie còn lại. **CHƯA verify trên Chrome/quota thật.**

**Bổ sung (2026-08-18, cùng ngày) — sau khi xoá cookie labs.google, check + bấm "Create with Google Flow" rồi quay lại đúng trang project hiện tại:** theo yêu cầu tiếp "sau khi clear cache phải check có nút Create with Google Flow click vào mới quay lại trang project hiện tại". Lý do: `_cdp_clear_cache_and_cookies()` xoá cookie `labs.google` NGAY TRÊN TRANG ĐANG MỞ (không reload) — thao tác/điều hướng KẾ TIẾP trên trang đó có thể bị Google chặn lại bởi màn hình xen giữa "Create with Google Flow" (CÙNG cơ chế `_click_create_with_flow_if_present()` đã có ở §11.28, trước đây chỉ gọi sau `driver.get()` lúc NAVIGATE tới 1 URL project, chưa từng gọi ngay sau bước dọn cache/cookie này).

Hàm mới `_recover_flow_project_page_after_cache_clear()` — gọi `_click_create_with_flow_if_present()` NGAY TRÊN TRANG HIỆN TẠI (không cần `driver.get()` trước, hàm đó tự poll DOM); nếu có bấm và trang bị điều hướng lệch khỏi `/project/{uuid}` ban đầu (đã lưu `current_url` TRƯỚC khi bấm), `driver.get()` LẠI đúng URL đó — mirror CHÍNH XÁC pattern "bấm xong lỡ điều hướng ra khỏi URL project ban đầu — quay lại đúng url cũ" đã có sẵn ở `_ensure_flow_page()`/`_ensure_flow_project()`.

**Chỉ wire vào ĐÚNG 1 trong 4 call site của `_cdp_clear_cache_and_cookies()`** — `_process_tasks()`, gated thêm `if self.worker_mode in ('dom','api')`. Audit cả 4 call site xác nhận CHỈ `_process_tasks()` (phục vụ CHUNG cả `worker_mode='dom'/'api'`, đứng trên labs.google project page thật, LẪN `'gemini_video'`, đứng trên gemini.google.com) có khả năng đang đứng trên trang labs.google project — 3 call site còn lại (`_run_gemini_loop()`, `_run_gemini_loop_concurrent()`, `_run_chatgpt_loop()`) LUÔN đứng trên `gemini.google.com`/`chatgpt.com`, KHÔNG BAO GIỜ có nút này — thêm check ở đó chỉ tốn thời gian poll DOM vô ích mỗi batch, nên KHÔNG wire vào 3 chỗ đó.

Verify: `py_compile`/`pyflakes` sạch. Test THẬT (fake driver mô phỏng `current_url` qua nhiều bước đọc, không mock logic quyết định) — 5 kịch bản: (1) không đứng trên `/project/` → không gọi check gì cả; (2) đứng trên project, nút KHÔNG xuất hiện → check đúng 1 lần, không điều hướng gì; (3) nút xuất hiện, bấm xong vẫn còn `/project/` → không điều hướng thừa; (4) nút xuất hiện, bấm xong bị lệch sang trang chung → `driver.get()` LẠI đúng URL project ban đầu + sleep 4s; (5) driver chết (`current_url` raise) → best-effort, không crash, không gọi check. **CHƯA verify trên Chrome/labs.google thật.**

**⚠️ Bổ sung NGAY SAU (cùng ngày) — thêm khoảng "chờ" TRƯỚC khi check:** theo yêu cầu tiếp "sau khi clear cache phải chờ check có nút Create with Google Flow click vào mới quay lại trang project hiện tại" — bản đầu gọi `_click_create_with_flow_if_present()` NGAY LẬP TỨC, không có khoảng chờ nào trước đó. Vì `_cdp_clear_cache_and_cookies()` không `driver.get()`/reload gì, màn hình "Create with Google Flow" (nếu Google định hiện) cần vài giây để trang tự phát hiện cookie/phiên vừa mất (thường qua 1 lần gọi API nền thất bại) rồi mới render lại UI — check quá sớm dễ bỏ lỡ. `_recover_flow_project_page_after_cache_clear()` thêm tham số `settle_wait_secs: float = 3.0` — `self._sleep(settle_wait_secs)` NGAY TRƯỚC `_click_create_with_flow_if_present()` (hàm đó vẫn tự poll thêm tối đa 6s nữa như cũ — lớp chờ-trước ĐỘC LẬP, không thay thế); `settle_wait_secs<=0` bỏ qua hẳn bước chờ (opt-out). Verify: test THẬT ghi lại đúng thứ tự gọi `_sleep`/`_click_create_with_flow_if_present` — mặc định chờ ĐÚNG TRƯỚC check, giá trị tuỳ chỉnh được tôn trọng, `0` bỏ qua hẳn, full round-trip đúng thứ tự `[sleep(3.0), driver.get(url cũ), sleep(4)]`. **CHƯA verify trên Chrome/labs.google thật.**

**⚠️ ĐỔI HƯỚNG (2026-08-18, cùng ngày) — user mô tả LẠI TOÀN BỘ quy trình mong muốn, viết lại hoàn toàn `_recover_flow_project_page_after_cache_clear()`:** "phần client_tool profile VEO đổi cơ chế mới nhập xong batch xóa cookie labs.google -> refresh trang project -> lấy setting thời gian khoảng cách giữa 2 batch làm thời gian chờ -> xong click nút create with google flow -> rồi lại refresh trang project lấy task đã hoàn tất -> nhận batch tiếp theo nếu có." Thay 2 điểm cốt lõi:

1. **Chủ động `driver.refresh()` NGAY** (thay vì chỉ chờ thụ động 3s cho trang tự phát hiện mất cookie như bản trước) — refresh TRƯỚC, rồi mới chờ.
2. **Thời gian chờ đổi từ hằng số `settle_wait_secs=3.0` sang ĐỌC TRỰC TIẾP setting `task_delay_secs`** (Cài đặt "Delay giữa các task trong batch (giây)", `local_settings.py`) — hệ thống KHÔNG có setting nào tên riêng "khoảng cách giữa 2 batch", đây là setting delay/spacing DUY NHẤT sẵn có nên TÁI DÙNG cho bước này. Tham số hàm `settle_wait_secs` đổi type `float | None`, `None` = mặc định đọc setting, số cụ thể = override (giữ lại chỉ để test).
3. **Bước MỚI cuối hàm** — sau khi click (hoặc không click) "Create with Google Flow" và xác nhận đúng URL project, gọi `_reconcile_project_media(set())` — hàm này tự `driver.refresh()` LẦN 2 rồi đọc `projectInitialData`, tải về/đánh dấu `done` media đã render xong mà server chưa biết — đúng nghĩa đen "refresh trang project lấy task đã hoàn tất". **Đoạn code reconcile TỪNG nằm ở ĐẦU `_process_tasks()`** (xem mục "Mở rộng — reconcile TOÀN BỘ project ĐÃ LƯU..." ở §11.5 phía trên) **đã bị XOÁ** — dời hẳn vào đây (cuối batch TRƯỚC) để tránh refresh+đọc `projectInitialData` 2 LẦN LIÊN TIẾP (mỗi lần tốn ~15s chờ interceptor) cho CÙNG 1 mục đích — "cuối batch N" và "đầu batch N+1" là CÙNG 1 thời điểm về luồng chạy (không có gì xen giữa ngoài chờ heartbeat), gộp 1 chỗ là đủ.

Thứ tự đầy đủ trong `finally` của `_process_tasks()` (không escalate, `worker_mode in ('dom','api')`): `_cdp_clear_cache_and_cookies()` → `_recover_flow_project_page_after_cache_clear()` (refresh → chờ `task_delay_secs` → click "Create with Google Flow" nếu có → quay lại đúng URL project nếu bị điều hướng lệch → `_reconcile_project_media(set())`).

Verify: `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome/labs.google thật** — cần user chạy vài batch thật để xác nhận đúng thứ tự refresh→chờ→click→refresh-lại-lấy-task-hoàn-tất→nhận batch tiếp theo, và xác nhận `task_delay_secs` (mặc định 10s) là khoảng chờ hợp lý cho bước "settle" này (khác ý nghĩa gốc của setting — vốn là "delay giữa các task trong batch" — giờ TÁI DÙNG kép cho cả 2 mục đích).

### 11.32 imageToImage qua API THUẦN — upload ảnh (`flow/uploadImage`) + `imageInputs` (2026-08-12)

Theo yêu cầu user ("_test_textToImage.py đã chạy ok text-to-image hã dùng trinh duyệt bắt sự kiện image-to-image để thực hiện upload image rồi đính kèm image vào prompt chạy qua api") — mở rộng bộ test API-mode (`tests/utils/flow_api.py`/`flow_session.py`, KHÁC HẲN `server/worker.py`'s `SeleniumFlowWorker` production — xem §11 tổng quan) để hỗ trợ **imageToImage chạy THUẦN qua API**, không cần bất kỳ thao tác DOM/picker nào ở bước generate (khác `worker_mode='dom'`'s `_dom_upload_images()`, vốn phải mở picker/upload qua UI thật).

**Khám phá cấu trúc thật (không đoán) — chạy DOM automation THẬT 1 lần để bắt network:** viết 1 script investigation throwaway (`tests/_investigate_image_to_image_api.py`, ĐÃ XOÁ sau khi dùng xong — chỉ giữ lại kết quả trong `FLOW_API_CAPTURE.md`/code) — launch Chrome qua `FlowBrowserSession` (profile "khanh" đã login sẵn, y hệt `_test_textToImage.py`), cài 1 fetch+XHR interceptor bắt CẢ request lẫn response body cho mọi call `labs.google`/`aisandbox-pa.googleapis.com`, rồi gọi TRỰC TIẾP 3 hàm production của `SeleniumFlowWorker` (`_dom_configure()`/`_dom_upload_images()`/`_dom_fill_and_submit()` — instantiate 1 worker với profile dict GIẢ `id=999999999` không có trong DB, gán thẳng `worker.driver = session.driver`, bỏ qua hẳn `_make_driver()` nên KHÔNG cần DB/backend nào — `_profile_log()` best-effort, lỗi ghi log bị nuốt êm) để thực hiện 1 lần imageToImage THẬT qua đúng picker DOM (ảnh test 1x1 PNG). Kết quả bắt được:

1. **`POST https://aisandbox-pa.googleapis.com/v1/flow/uploadImage`** — upload ảnh tham chiếu, endpoint HOÀN TOÀN RIÊNG, KHÔNG cần recaptcha (body không có `clientContext.recaptchaContext`, khác `batchGenerateImages`). Body: `{"clientContext":{"projectId":"...","tool":"PINHOLE"},"imageBytes":"<base64 thuần>","isUserUploaded":true,"isHidden":false,"mimeType":"image/png","fileName":"ref.png"}`. Response: `{"media":{"name":"<uuid>",...}}`.
2. **`POST .../flowMedia:batchGenerateImages`** (endpoint CŨ, y hệt text-to-image) — `requests[].imageInputs` không rỗng: `[{"imageInputType":"IMAGE_INPUT_TYPE_REFERENCE","name":"<uuid từ bước 1>"}]`.

Xem đầy đủ trong `tests/FLOW_API_CAPTURE.md` mục "5b. Image-to-Image".

**Implement** (`tests/utils/flow_api.py`): `UPLOAD_IMAGE` (FlowEndpoint mới, path tuyệt đối `/flow/uploadImage`, KHÔNG dưới `/projects/{id}/`), `build_upload_image_body()`, `parse_uploaded_image_name()`, `build_image_input_ref()`, `build_text_to_image_body(..., image_inputs=[...])` (tham số MỚI, optional — `None`/`[]` giữ nguyên hành vi textToImage cũ 100%, không breaking). `FlowAPIClient.upload_image(image_bytes, file_name, mime_type)` (trả `media.name`) + `FlowAPIClient.generate_image_to_image(prompt, image_paths=[...], image_media_names=[...], ...)` (tự upload từng `image_paths` TUẦN TỰ rồi gộp với `image_media_names` đã có sẵn — cho phép tái dùng ảnh đã upload từ lần gọi trước, không upload lại).

**Test mới** `tests/_test_imageToImage.py` (THAY THẾ HOÀN TOÀN bản cũ — bản cũ là script khám phá DOM dở dang từ trước, dùng `webdriver.Chrome` trực tiếp, chưa từng tìm ra cơ chế thật, đã ghi trong file tree ở đầu file này "UI explored — cần DOM mode test" — không còn đúng, phải sửa) — cùng cấu trúc CLI với `_test_textToImage.py` (`--prompt`/`--aspect-ratio`/`--model`/`--clear-cache`/`--browser`/`-s`), thêm `--image <path>` (ảnh tham chiếu local, mặc định dùng ảnh test 1x1 built-in nếu không truyền) — luồng: `client.upload_image()` → `client.generate_image_to_image()`, KHÔNG đụng DOM/picker ở bất kỳ bước nào.

**Verify end-to-end THẬT (2026-08-12):** cả bước investigation (qua DOM picker thật) LẪN `tests/_test_imageToImage.py` (qua API thuần, ảnh test 1x1) đều chạy thành công trên project "khanh" — HTTP 200 cả `uploadImage` lẫn `batchGenerateImages`, ảnh kết quả sinh ra thật (link `flow-content.google` CDN hợp lệ). `py_compile`/`pyflakes` sạch cho `flow_api.py`/`_test_imageToImage.py`.

**Chưa làm/ngoài phạm vi:** multi-image reference (2+ ảnh trong 1 lần generate — cấu trúc `imageInputs[]` gợi ý rõ là mảng lặp lại đúng shape, nhưng CHƯA test trực tiếp với ≥2 ảnh); không đụng gì tới `server/worker.py`'s DOM mode production (`_dom_upload_images()` vẫn là cơ chế THẬT dùng cho task queue thật — file này chỉ là bộ test/API riêng, KHÔNG thay thế production DOM mode).

### 11.33 Video qua API THUẦN — reCAPTCHA action `VIDEO_GENERATION` + ingredientToVideo/frameToVideo (2026-08-12)

Theo yêu cầu user "grecapcha thay action 'VIDEO_GENERATION' cho tạo video và viết lại file test textToVideo, thêm file test ingredient to video, frame to video" — mở rộng tiếp bộ test API-mode (nối §11.32) sang 3 loại video: textToVideo (viết lại, bản cũ dở dang chưa từng verify), ingredientToVideo/componentsToVideo (N ảnh nguyên liệu), frameToVideo (ảnh khung hình).

**reCAPTCHA action parametrize** (`tests/utils/flow_session.py`) — `RECAPTCHA_FETCH_JS` nhận `action` qua `arguments[2]` (mặc định `'IMAGE_GENERATION'` nếu không truyền — backward-compat); `fetch_recaptcha(action='IMAGE_GENERATION')` — mọi call site VIDEO (`generate_text_to_video`/`generate_ingredient_to_video`/`generate_frame_to_video`) truyền `'VIDEO_GENERATION'` tường minh.

**Khám phá 2 endpoint video CÓ ẢNH — chạy DOM production thật + interceptor (như §11.32), 2 lần riêng:**

1. **ingredientToVideo/componentsToVideo** (sub-tab "Thành phần", N ảnh nguyên liệu) — capture trên project "khanh": `POST /v1/video:batchAsyncGenerateVideoReferenceImages`, `requests[].referenceImages = [{"mediaId":"<uuid từ uploadImage>","imageUsageType":"IMAGE_USAGE_TYPE_ASSET"}]`, `videoModelKey:"veo_3_1_r2v_lite"` (namespace `r2v` KHÁC `t2v` của textToVideo, model do CHÍNH Flow UI tự chọn). **HTTP 200 thật, video tạo thành công.**

2. **frameToVideo** (sub-tab "Khung hình") — user yêu cầu dùng profile PRODUCTION `huavantien84_2` (id=25) để test loại này, xem "Đăng nhập profile" bên dưới. Phát hiện QUAN TRỌNG: sub-tab này dùng UI HOÀN TOÀN KHÁC — 2 div `[aria-haspopup="dialog"]` text "Bắt đầu"/"Kết thúc" (KHÔNG PHẢI nút `add_2` mà `_dom_upload_images()` production dùng cho MỌI mode khác — nghĩa là **frameToVideo hiện KHÔNG được DOM production hỗ trợ đúng**, dead-code cũ `uploadImagesDirect()`/2 nút Bắt đầu-Kết thúc từng bị coi là lỗi thời sau fix 2026-07-17 hoá ra vẫn là UI THẬT của riêng sub-tab này — ngoài phạm vi sửa lần này, chỉ ghi nhận). Endpoint capture: `POST /v1/video:batchAsyncGenerateVideoStartImage`, `requests[].startImage = {"mediaId":"...","cropCoordinates":{...}}`, `videoModelKey:"abra_i2v_8s"`. **`endImage` KHÔNG xác nhận hoạt động** — lần thử với 2 ảnh THẬT khác nhau (đính đúng qua UI) cho request THÀNH CÔNG (HTTP 200) chỉ có `startImage`, hoàn toàn không có `endImage` — model quan sát được chỉ khai `videoModelCapabilities:["VIDEO_MODEL_CAPABILITY_START_IMAGE"]`. Xem đầy đủ (kể cả case lỗi 400 "Unknown name endImage") trong `tests/FLOW_API_CAPTURE.md` mục "6c".

**Đăng nhập profile huavantien84_2 giữa chừng investigate:** profile chưa có mật khẩu lưu sẵn (`account_password=''`) → `_ensure_google_login()` (xem §11.31) đúng thiết kế từ chối tự động, dừng lại ở accounts.google.com. Hỏi user qua AskUserQuestion — user chọn tự gõ mật khẩu trực tiếp vào cửa sổ Chrome do script mở sẵn (KHÔNG đưa mật khẩu cho AI, đúng quy tắc bảo mật). Script Python đầu tiên bị crash/thoát TRƯỚC khi kịp orphan/detach Chrome đúng cách → `undetected_chromedriver.__del__` tự đóng browser lúc process thoát — MAY MẮN cookie đăng nhập đã kịp flush xuống đĩa trước đó, lần chạy lại sau xác nhận "Đã đăng nhập sẵn" thành công.

**Bug thật tự phát hiện lúc investigate (không phải production, chỉ trong script điều tra):** ảnh "_BLUE_PX" (hand-typed base64 cho ảnh khung hình cuối) là **PNG HỎNG** (bad IDAT checksum, xác nhận qua `PIL.Image.verify()`) — khiến `POST /flow/uploadImage` lần 2 trả 400 "Request contains an invalid argument" ngay từ bước upload, Flow UI rơi vào state lỗi (start+end cùng trỏ 1 mediaId cũ, sinh request video có `endImage` sai) — đây chính là nguồn gốc lần capture 400 đầu tiên, KHÔNG phải lỗi API thật. Regenerate lại bằng `PIL.Image.new()` cho lần capture thành công thứ 2.

**Implement** (`tests/utils/flow_api.py`): `INGREDIENT_TO_VIDEO`/`FRAME_TO_VIDEO` (FlowEndpoint mới), `DEFAULT_INGREDIENT_VIDEO_MODEL`/`DEFAULT_FRAME_VIDEO_MODEL`, `build_video_reference_image()`, `FULL_IMAGE_CROP`, `build_ingredient_to_video_body()`, `build_frame_to_video_body(..., end_image_media_name=None)` (optional, docstring cảnh báo rõ chưa proven), `parse_video_workflow()` (parse response shape THẬT `{remainingCredits,workflows[],media[]}` — KHÁC giả định `operations[]`/`name` cũ chưa từng verify của `parse_video_operations()`, giữ hàm cũ lại không xoá). `FlowAPIClient.generate_ingredient_to_video()`/`generate_frame_to_video()` — cùng pattern upload-rồi-generate với `generate_image_to_image()` ở §11.32.

**Test mới:** `tests/_test_textToVideo.py` (viết lại hoàn toàn, cùng cấu trúc CLI với `_test_textToImage.py`), `tests/_test_ingredientToVideo.py` (`--image`, lặp lại được nhiều ảnh), `tests/_test_frameToVideo.py` (`--start-image` mặc định dùng, `--end-image` đánh dấu THỬ NGHIỆM trong help text + docstring).

**Verify cuối (bản implementation thật, gọi qua `FlowAPIClient`, không phải script investigation):** `py_compile`/`pyflakes` sạch toàn bộ. Chạy thật (project "khanh", `--no-proxy-prompt` + stdin `/dev/null` — tránh bị treo ở prompt SOCKS5 tương tác, xem bug "Bash tool tự background do treo prompt" đã gặp lúc verify):
- `_test_textToVideo.py --model veo_3_1_t2v_lite` → HTTP 200 thật, `workflowId`/`mediaId`/`displayName` đọc đúng qua `parse_video_workflow()`. (⚠️ `--model veo_3_1_t2v_lite_low_priority`, DEFAULT_VIDEO_MODEL sẵn có TỪ TRƯỚC — không phải hằng số mới thêm lần này — bị 403 `PUBLIC_ERROR_MODEL_ACCESS_DENIED` trên account "khanh": dropdown video của account này chỉ có 4 model, KHÔNG có biến thể `[Lower Priority]`, xem §11.30's ghi chú tương tự cho "Veo 3.1 - Lite [Lower Priority]" trên project "khanh" — dùng `--model` override khi test account không có access model mặc định).
- `_test_ingredientToVideo.py` (model mặc định `veo_3_1_r2v_lite`) → HTTP 200 thật, `uploadImage` + `batchAsyncGenerateVideoReferenceImages` đều thành công.
- `_test_frameToVideo.py` (model mặc định `abra_i2v_8s`) → `uploadImage` thành công, request build đúng cấu trúc, POST reach server đúng — nhưng HTTP **429** `PUBLIC_ERROR_USER_QUOTA_REACHED` (hết quota THẬT của account "khanh" sau nhiều lần generate liên tiếp trong phiên investigate+verify này, KHÔNG PHẢI lỗi code — request đã tới server, server trả lỗi CẤU TRÚC hợp lệ, không phải lỗi tham số/schema). Endpoint này ĐÃ được verify HTTP 200 THẬT (video tạo thành công) riêng qua script investigation trên account `huavantien84_2` (xem phần trên) — cùng code path (`build_frame_to_video_body()`/`generate_frame_to_video()`), chỉ khác account gọi.

---

### 11.34 `server/model_catalog.py` — DB `veo_models.model_key` làm source of truth cho `imageModelName`/`videoModelKey` (2026-08-13)

**User hỏi:** "nghi vấn chọn sai videoModelKey hãy check project_6 đưa qua client_tool sẽ ra videoModelKey = gì".

**Bug thật tìm được (điều tra DB thật, không đoán):** project_6 — 855 task video, TẤT CẢ `mode='imageToVideo'`, `model="Veo 3.1 - Lite [Lower Priority]"`. Chạy trực tiếp qua `_call_video_api()` xác nhận: `videoModelKey` gửi đi LUÔN LÀ `veo_3_1_r2v_lite` — `task.model` bị **BỎ QUA HOÀN TOÀN**. Nguyên nhân: nhánh `imageToVideo`/`componentsToVideo` gọi `build_ingredient_to_video_body()` mà KHÔNG truyền `video_model_key=`, luôn dùng default cứng `DEFAULT_INGREDIENT_VIDEO_MODEL`.

**Root cause SÂU HƠN — 1 kết luận SAI ở §11.33:** default cứng đó dựa trên DUY NHẤT 1 mẫu capture (project "khanh", 2026-08-12), lúc đó suy ra "Flow UI tự khoá model khi dùng sub-tab Thành phần, KHÔNG theo dropdown user chọn". **User xác nhận trực tiếp suy luận đó SAI** — namespace ingredient (`veo_3_1_r2v_*`) CŨNG có biến thể theo tier giống hệt namespace textToVideo (`veo_3_1_t2v_*`), chỉ khác đúng 3 ký tự `t2v`↔`r2v`: "Veo 3.1 - Lite [Lower Priority]" → `veo_3_1_r2v_lite_low_priority` (KHÁC `veo_3_1_r2v_lite` plain từng bị dùng nhầm cho MỌI tier).

**Fix — 2 lớp:**

1. **DB `veo_models.model_key`** (repo `ToolSub` gốc, xem `ToolSub/CHANGELOG.md` 2026-08-13) — cột mới trên bảng `veo_models` (đã tồn tại từ trước, dùng bởi `GET /api/models` — `admin.py::list_models()`), lưu giá trị THẬT gửi aisandbox (`imageModelName` cho ảnh, `videoModelKey` dạng **t2v** cho video — KHÔNG có cột riêng cho dạng r2v/ingredient, xem lý do ngay dưới). Populate 1 lần các giá trị ĐÃ XÁC NHẬN:

   | `name` (label GUI) | `model_key` |
   |---|---|
   | Nano Banana Pro | `GEM_PIX_2` |
   | Nano Banana 2 | `NARWHAL` |
   | Nano Banana 2 Lite | `HARBOR_SEAL` |
   | Veo 3.1 - Lite [Lower Priority] | `veo_3_1_t2v_lite_low_priority` |
   | Veo 3.1 - Lite | `veo_3_1_t2v_lite` |
   | Veo 3.1 - Fast | `veo_3_1_t2v_fast` |
   | Veo 3.1 - Quality | `veo_3_1_t2v` |

   `Imagen 4`/`Omni Flash` chưa xác nhận, giữ `NULL` (client_tool tự fallback, không đoán). **⚠️ Cột `model_key_ingredient` từng thêm rồi XOÁ NGAY trong cùng phiên** (1 trong số ít ngoại lệ quy ước additive-only của dự án — chỉ áp dụng khi user CHỦ Ý yêu cầu "model_key dùng chung ko phân biệt khi dùng ingredient xóa cột đó đi") — dạng r2v được **DERIVE** ở tầng client_tool (swap `t2v`→`r2v` trong `model_key`), không lưu riêng.

2. **`server/model_catalog.py`** (MỚI) — module DUY NHẤT chịu trách nhiệm resolve tên hiển thị GUI → key thật, đứng TRƯỚC `flow_api.py`'s dict hardcode (giờ chỉ còn vai trò LƯỚI AN TOÀN):
   ```
   resolve_image_model(name) -> (imageModelName, source)
   resolve_video_model(name, ingredient=bool) -> (videoModelKey, source)
   ```
   - Fetch `GET {FLOW_SERVER}/api/models` 1 lần, cache TTL 300s (module-level, `threading.RLock`) — lỗi mạng/backend down → GIỮ NGUYÊN cache cũ (nếu có) thay vì xoá sạch, an toàn hơn 1 lần fetch lỗi thoáng qua làm mất khả năng resolve đúng.
   - Index theo `name` (khớp đúng field GUI lưu trong `tasks_media_flow.model`, gửi qua heartbeat vào `task['model']`).
   - `ingredient=True`: lấy `modelKey` (t2v) từ DB rồi swap `t2v`→`r2v` (`source='db-derived'`); `ingredient=False`: dùng thẳng (`source='db'`).
   - DB miss (model chưa có `model_key`/fetch lỗi) → fallback `flow_api.resolve_image_model_key()`/`resolve_video_model_key()`/`resolve_ingredient_video_model_key()` (dict hardcode cũ, `source='local-fallback'`/`'local-default'`).
   - KHÔNG import `worker.py` (tránh circular — `worker.py` import module này).

**`flow_api.py::resolve_ingredient_video_model_key()` viết lại** — bỏ hẳn `_INGREDIENT_VIDEO_MODEL_LABEL_TO_KEY` (dict riêng, chỉ có đúng 1 cặp xác nhận) — giờ DÙNG CHUNG `_VIDEO_MODEL_LABEL_TO_KEY`/`resolve_video_model_key()` (namespace t2v) rồi derive sang r2v bằng `t2v_key.replace('t2v', 'r2v', 1)` — cùng pattern swap dùng ở `model_catalog.py`, chỉ khác đây là lưới an toàn CỤC BỘ (không cần mạng) khi backend không tới được.

**`worker.py::_call_video_api()`** — nhánh `imageToVideo`/`componentsToVideo` giờ gọi `model_catalog.resolve_video_model(task.get('model'), ingredient=True)` + **TRUYỀN `video_model_key=` vào `build_ingredient_to_video_body()`** (TRƯỚC ĐÂY hoàn toàn không resolve gì, đây chính là bug) + log `Task #id model="..." → videoModelKey=... (ingredient, nguồn: ...)` + warn nếu `nguồn != 'db'`. Nhánh `textToVideo` cũng đổi từ gọi thẳng `flow_api.resolve_video_model_key()` sang qua `model_catalog.resolve_video_model(..., ingredient=False)` (đồng nhất đường đi, ưu tiên DB trước hardcode). `_call_image_api_v2()` tương tự, đổi sang `model_catalog.resolve_image_model()`.

**Verify:** Chạy TRỰC TIẾP logic `_call_video_api()` với dữ liệu project_6 THẬT lấy từ DB (không mock) — xác nhận đúng bug trước fix (`veo_3_1_r2v_lite`, sai tier). `resolve_ingredient_video_model_key()` test độc lập — case đã xác nhận khớp chính xác `veo_3_1_r2v_lite_low_priority`; case Lite plain khớp default cũ (`veo_3_1_r2v_lite`, không đổi hành vi cho tier này); Fast/Quality suy ra hợp lý theo pattern (`veo_3_1_r2v_fast`/`veo_3_1_r2v`, CHƯA có capture riêng xác nhận); model lạ → fallback an toàn. `model_catalog.py` test end-to-end qua backend MỚI (in-process Flask test client, bypass server thật đang chạy chưa restart) — cả `resolve_image_model()` và `resolve_video_model()` (2 nhánh t2v/r2v) trả đúng giá trị DB với `source` chính xác; model không có trong DB → rơi đúng xuống fallback local. `py_compile`/`pyflakes` sạch toàn bộ (`model_catalog.py`, `flow_api.py`, `worker.py`).

**CHƯA verify:** trên browser thật với 1 task video project_6 thật (cần backend `ToolSub` được RESTART trước — `GET /api/models` hiện tại đang chạy LIVE vẫn trả response CŨ không có `modelKey`, xác nhận qua `curl` trực tiếp lúc verify, không tự restart server production).

**⚠️ FIX THẬT (2026-08-18) — log warn SAI cho case `source='db-derived'` ĐÃ ĐÚNG:** user báo log `... KHÔNG có trong veo_models.model_key_ingredient (backend), dùng db-derived: videoModelKey=veo_3_1_r2v_lite_low_priority ...` kèm "dùng hoàn toàn trong db ko dùng mặc định nữa". `model_key_ingredient` là tên cột ĐÃ BỊ XOÁ khỏi backend từ trước (xem đoạn "CHỈ 1 field `modelKey` DÙNG CHUNG" ở trên) — comment/log text sót lại tham chiếu cột không còn tồn tại. Giá trị resolve ra ĐÚNG (khớp chính xác giá trị đã confirm cho model này), nguồn `'db-derived'` = derive `t2v`→`r2v` TỪ chính `veo_models.model_key` đọc từ DB — ĐÂY LÀ "dùng hoàn toàn trong DB", không phải fallback. Bug: điều kiện cũ `model_src != 'db'` coi `'db-derived'` là không-từ-DB nên bắn warn sai. Fix: hằng số `_MODEL_SRC_FROM_DB = frozenset({'db','db-derived'})` — chỉ warn khi THẬT SỰ `'local-fallback'`/`'local-default'`; sửa text warn nhánh ingredient (bỏ tham chiếu cột đã xoá); thêm warn tương tự cho nhánh `textToVideo` thuần (trước đây thiếu hẳn). Verify: test đọc trực tiếp hằng số từ source — 4/4 case đúng (`db`/`db-derived` không warn, `local-fallback`/`local-default` có warn). **CHƯA verify trên browser thật.**

---

### 11.35 `worker_mode='api'` — gửi ẢNH/VIDEO SONG SONG THẬT SỰ (N thread) thay vì tuần tự (2026-08-13)

Theo yêu cầu user "các task image/video veo3 nên chạy đa luồng kiểu có 5 prompt cứ gửi 5 prompt rồi nhận về kq, chứ chạy tuần tự... làm mất thời gian" — làm rõ: "giống đa luồng 5 prompt thì gữi 5 api 5 luồng khác nhau rồi nhận phản hồi" (N thread Python THẬT, mỗi thread tự bắn 1 request rồi block chờ ĐÚNG response của chính nó — không phải chỉ "gửi hết rồi poll sau", vốn ĐÃ tồn tại sẵn 1 phần cho video từ trước).

**Vì sao khả thi (điều tra trước khi sửa):** `_post_aisandbox()` (POST generate thật, dùng bởi `_call_image_api_v2()`/`_call_video_api()`) đi qua `curl_cffi` — HTTP client Python THUẦN, TÁCH RỜI HOÀN TOÀN khỏi Selenium driver (khác nhiều chỗ khác trong file dùng `driver.execute_script`) — nhiều thread gọi đồng thời AN TOÀN. `_extract_project_id()`/`_upload_media_to_flow()`/`_download_source_media()` cũng không đụng driver. CHỈ 1 bước thật sự cần driver: mint reCAPTCHA (`_get_fresh_recaptcha()`, `execute_async_script` — 1 Selenium session không an toàn cho nhiều thread gọi lệnh cùng lúc) — luôn làm TUẦN TỰ TRƯỚC (nhanh, ~1-2s/task) trong 1 vòng "prep", rồi mới bung `ThreadPoolExecutor` cho phần TỐN THỜI GIAN NHẤT (Google xử lý ảnh/video).

**2 hàm mới** (`server/worker.py`, trước `_run_tasks_api_batch()`):
- `_run_image_tasks_concurrent(tasks)` — prep tuần tự (mint captcha + check `has_tokens()`/`_extract_project_id()`) → N thread gọi `_call_image_api_v2()` song song, mỗi thread tự chờ HTTP response chứa `fifeUrl` (ẢNH có "phản hồi" đồng bộ thật). Task thiếu điều kiện Direct API → `_run_task_api()` (UI-driven, driver) tuần tự riêng. Lỗi 403/reCAPTCHA khi gọi song song → retry UI-driven ở vòng sau (giữ nguyên fallback gốc, không âm thầm bỏ).
- `_run_video_tasks_concurrent_submit(tasks, uploaded_by_task)` — cùng pattern cho SUBMIT video (API video không hiện tile trên UI, "phản hồi" mỗi thread chỉ xác nhận đã tạo workflow — kết quả THẬT vẫn lấy SAU qua reconcile `projectInitialData`, KHÔNG đổi bước đó, chỉ đổi bước SUBMIT từ tuần tự→song song). Trả thêm `attempted_ids` để phân biệt task đã thử với task chưa chạm tới vì escalation dừng batch sớm.
- Mọi mutation state không thread-safe (`_handle_task_success`/`_handle_task_error`/`_task_start`/counters) CHỈ gọi từ THREAD CHÍNH (vòng `as_completed`), KHÔNG BAO GIỜ từ bên trong worker thread.
- Không thêm giới hạn số thread nào mới — batch size đã bị chặn bởi `selenium_profiles.max_concurrent` (server chỉ giao tối đa từng đó task/lần heartbeat, xem §11.9), "N thread = N task trong batch" tự nhiên đúng ý user cấu hình.

**Phạm vi — CHỈ `worker_mode='api'`.** DOM mode (`_run_tasks_batch()`, submit qua UI picker) GIỮ NGUYÊN tuần tự — chỉ có 1 browser tab/session, không song song hoá theo cùng cách được (mọi thao tác đều qua driver). Audit lúc sửa: không có profile thật nào `enabled=1`+`worker_mode='dom'` cho VEO3, chỉ có `worker_mode='api'` (id=27, `task_mode='video_only'`, `max_concurrent=2`) — đúng khớp phạm vi thực tế.

**Verify:** `py_compile`/`pyflakes` sạch. Test cô lập (mock, throwaway — không cần Chrome/DB thật, đã xoá sau khi verify) — 5/5 task: (1) 5 ảnh mock sleep 0.4s/task → tổng đo **0.40s** (không phải 2.00s tuần tự) + xác nhận CHỒNG LẤN thời gian thật giữa các task (bằng chứng song song thật, không chỉ đo tổng); (2) 403→retry UI-driven đúng, lỗi khác→báo ngay; (3) thiếu token/captcha/project_id→UI-driven tuần tự đúng; (4) 4 video submit song song→**0.42s** (không phải 1.60s); (5) escalation giữa batch→dừng ngay, task chưa chạm tới không bị coi "đã thử".

**CHƯA verify trên browser/quota thật** — cần user tự chạy 1 batch ≥2 task qua profile `worker_mode='api'` với `max_concurrent≥2`, xác nhận log cho thấy nhiều `▶ Task #...`/POST generate xuất hiện GẦN NHAU thay vì cách nhau `task_delay_secs`, hoàn tất gần như đồng thời thay vì lần lượt.

---

### 11.36 FIX THẬT: "Render lại" báo hoàn tất nhưng KHÔNG thấy media mới — 4 nơi dùng tên tự chế thay vì UUID thật (2026-08-14)

**User báo:** "check lại image/video task khi hoàn tất có check ID đã tồn tại chưa, nếu chưa mới insert thành array chọn mặc định media mới nhất, hiện tại sao tôi thấy tạo lại trên frontend mà báo task hoàn tất mà ko thấy media mới".

**Root cause:** `_apply_media_to_task()` (backend, `backend/services/media_download.py`) tự nó ĐÃ ĐÚNG — dedup theo `name` (UUID), item CHƯA có mới append, luôn chọn `selected_file_index` = item cuối (mới nhất). Bug nằm ở CHÍNH `name` mà `client_tool` gửi lên — **4 nơi** trong `server/worker.py` build payload `media` gửi `POST /task/download` dùng CHUỖI TỰ CHẾ CỐ ĐỊNH theo `task_id` (`f'img_{task_id}_{i+1}'`/`f'dom_{task_id}_{i+1}'`) thay vì UUID THẬT của Google (khác nhau MỖI LẦN generate): `_run_task_api()` (2 nhánh: Direct API + UI-driven fallback), `_run_image_tasks_concurrent()` (§11.35, kế thừa nguyên bug từ code gốc), `_resolve_tile_media()` (DOM mode tile-fallback, dùng chung single-task lẫn batch). Hệ quả: "Render lại" lần 2 của CÙNG 1 task gửi lên ĐÚNG CÁI TÊN như lần đầu → bị dedup nhầm là "đã tồn tại" → không append dù ảnh/video MỚI đã render xong thật — khớp chính xác triệu chứng user báo. **KHÔNG có bug**: `_reconcile_project_media()` (đường CHÍNH của cả DOM lẫn API-video, đọc `name` thật từ `projectInitialData`) và `flow_api.parse_image_results()` (đã trích `media.name` đúng — chỉ 2 caller không dùng tới).

**Fix:** `_extract_cdn_media_name(url)` (hàm mới, regex khớp `_CDN_FLOW_RE` phía backend) trích UUID thật từ CDN URL đã resolve; `_resolve_tile_media()` ưu tiên UUID trích được, fallback tên tự chế CHỈ khi regex không khớp; `_generate_image_via_ui()` thêm trích `m.get('name','')` (trước bỏ qua hoàn toàn dù có sẵn trong response); cả 2 call site còn lại đổi `f'img_{task_id}_{i+1}'` → `u.get('name') or f'img_{task_id}_{i+1}'`.

**Verify:** `py_compile`/`pyflakes` sạch. Test cô lập (mock, throwaway) — quan trọng nhất: `_resolve_tile_media()` gọi 2 lần với CÙNG `task_id` (mô phỏng "Render lại") ra 2 UUID KHÁC NHAU (trước fix: giống hệt nhau cả 2 lần); fallback đúng khi URL lạ; logic build `media` ưu tiên tên thật, fallback đúng khi thiếu. Xem `CHANGELOG.md` 2026-08-14 để biết chi tiết đầy đủ.

**CHƯA verify trên browser/quota thật** — cần user tự "Render lại" 1 task đã có sẵn kết quả (cả 2 mode `api`/`dom`), xác nhận `result_files` thật sự tăng thêm (không giữ nguyên số lượng) và frontend hiển thị đúng media mới nhất.

---

### 11.37 `worker_mode='api'` — bỏ hẳn upload ref ẢNH/VIDEO 1 lượt trước batch, chuyển sang UPLOAD NGAY TRƯỚC lúc khởi động THREAD CỦA CHÍNH TASK ĐÓ + giãn cách random giữa các lần khởi động thread (2026-08-14)

Theo yêu cầu user: "fix lại client_tool không upload reference 1 lượt nữa: cứ upload cùng task: và cho phép setting random giây trong khoảng: 10-15 giây mặc định để khởi động thread tiếp theo. Ví dụ: Log upload reference image TASK #12321 [nếu có ref]→ lấy được id chạy task media → chờ random 10-15 → upload ref → chạy task media …". Đây là bước tiếp theo nối §11.35 (song song hoá THẬT bằng N thread) — bản §11.35 vẫn còn 2 điểm chưa đúng ý user: (1) nhánh video upload ref của **CẢ LÔ** trong 1 vòng tuần tự RIÊNG trước khi mới bắt đầu submit song song (`_run_tasks_api_batch()`'s vòng `for task in video_tasks: ... _prepare_video_uploads(task)`); (2) các thread trong lô (cả ảnh lẫn video) được submit gần như ĐỒNG THỜI (`pool.submit()` liên tiếp không delay) — trông giống bot bắn N request cùng lúc.

**2 setting mới** (`config.py::_DEFAULT_SERVER_SETTINGS`, `local_settings.py::_clamp()` — cùng nhóm float với `step_delay_min_secs`/`step_delay_max_secs`): `thread_stagger_min_secs`/`thread_stagger_max_secs` (mặc định **10.0/15.0**). Sửa qua tab Cài đặt (`gui/pages/settings_page.py::_FIELD_DEFS`, generic — không cần đổi logic render/save, mọi field mới tự động có UI).

**`_run_image_tasks_concurrent()`** viết lại — bỏ hẳn 2 vòng lặp tách biệt "prep tất cả rồi mới submit tất cả" (bản §11.35), gộp thành 1 vòng DUY NHẤT: với MỖI task — mint captcha → (nếu `_parse_source_media(task)` không rỗng) log `⬆ Upload reference image TASK #{id}…` → `pool.submit()` NGAY (ref ảnh tự upload BÊN TRONG `_call_image_api_v2()` của chính thread đó, không đổi) → **CHỜ random `thread_stagger_min_secs`–`thread_stagger_max_secs` giây** (`self._stop.wait(delay)`, dừng sớm nếu `_stop` được set) TRƯỚC KHI chuyển sang task kế tiếp. `ThreadPoolExecutor` bọc NGUYÊN vòng lặp NÀY (không phải chỉ phần submit như trước) — thread đã khởi động vẫn tiếp tục chạy song song bình thường trong lúc vòng lặp đang "ngủ" chờ tới lượt task kế.

**`_run_video_tasks_concurrent_submit(tasks, uploaded_by_task)` → đổi tên + viết lại thành `_run_video_tasks_staggered(tasks)`** — bỏ tham số `uploaded_by_task` (không còn upload trước nữa), gộp bước upload ref VÀO NGAY TRONG vòng lặp chính (mirror ảnh): mỗi task — `/task/processing` → log upload (nếu có ref) → `_prepare_video_uploads(task)` (CHỈ khi có `source_media`, tránh gọi thừa cho task không ref) → mint captcha → `pool.submit()` → chờ random stagger → task kế. Vẫn giữ nguyên ý nghĩa "phản hồi mỗi thread chỉ xác nhận đã tạo workflow, kết quả THẬT lấy SAU qua reconcile" (không đổi bước đó).

**`_run_tasks_api_batch()`** đơn giản hoá — xoá hẳn vòng lặp "upload ref cả lô video trước" (đã chuyển vào trong `_run_video_tasks_staggered()`), gọi thẳng `_run_video_tasks_staggered(video_tasks)` (không lọc/truyền `uploaded_by_task` nữa — filtering theo "có ref hay không" giờ tự nhiên xảy ra bên trong từng task).

**Verify:** `py_compile`/`pyflakes` sạch cả 5 file (`worker.py`/`config.py`/`local_settings.py`/`settings_page.py`). Test mock-based THẬT (đo thời gian thực, không giả lập `time.sleep`) qua venv `client_tool` — throwaway, đã xoá sau verify:
- **Ảnh** (3 task, mỗi task worker giả sleep 0.5s, stagger cố định 0.3s để test deterministic): tổng elapsed **1.12s** (không phải 1.5s tuần tự CŨNG không phải 0.5s toàn song song — đúng "vừa giãn cách vừa chồng lấn"), khoảng cách giữa 3 lần khởi động thread **0.31s/0.30s** (đúng trong range stagger), elapsed < `SLEEP×3` xác nhận có OVERLAP THẬT (task 1 vẫn đang `sleep()` khi task 2/3 đã bắt đầu).
- **Video** (2 task, 1 có `source_media` 1 không): `attempted_ids={10,11}` đúng cả 2; upload ref CHỈ xảy ra cho task 10 (có ref) — task 11 không gọi `_prepare_video_uploads` gì cả; timestamp `upload_ref(10)` xác nhận xảy ra TRƯỚC/NGAY thời điểm `video_worker_start(10)` (không phải 1 vòng riêng trước đó); `uploaded_media_names` truyền đúng vào `_call_video_api()` cho từng task (`['uploaded-10']` cho task có ref, `[]` cho task không ref).

**CHƯA verify trên browser/quota thật** — cần user tự chạy 1 batch ≥2 task (cả ảnh lẫn video, có/không ref) qua profile `worker_mode='api'` với `max_concurrent≥2`, xác nhận log hiện đúng thứ tự `⬆ Upload reference image TASK #X…` → `▶ Task #X — chạy media…` → `⏳ Chờ N.Ns…` lặp lại cho từng task, cách nhau đúng khoảng 10-15s (hoặc giá trị đã tuỳ chỉnh), trong khi task trước đó vẫn tiếp tục xử lý song song.

---

### 11.38 FIX THẬT: `_gemini_type_prompt()` gõ prompt trúng phần tử SAI — nguyên nhân "gemini chat + upload: nhận task chỉ mở profile không vào gemini để chạy task" (2026-08-17)

User báo: "check profile gemini chat + upload khi nhận task bị lỗi chỉ mở profile không vào gemini để chạy task". Log `profile_20.log`/`profile_26.log` (2 profile Gemini Chat vừa chạy thật, 1-tab và round-robin) không lộ traceback rõ ràng — phải đọc kỹ lại `server/worker.py::_gemini_type_prompt()`, hàm DUY NHẤT gõ prompt vào Gemini, DÙNG CHUNG cho cả luồng 1-tab tuần tự (`_run_task_gemini`) LẪN round-robin nhiều tab (`_gemini_slot_step`, §11.24) — bug ở đây ảnh hưởng MỌI task Gemini bất kể cấu hình bao nhiêu tab đồng thời.

**Root cause:** selector cũ — `document.querySelector('.new-input-ui').querySelector('[contenteditable="true"]') || wrapper.querySelector('p')` — SAI vì `.new-input-ui` (Quill editor của Gemini) **TỰ NÓ** mang `contenteditable="true"` ngay trên chính nó (`<div class="ql-editor ... new-input-ui" contenteditable="true">`), KHÔNG PHẢI 1 ancestor bọc quanh 1 phần tử con khác mới có thuộc tính này. `querySelector` chỉ tìm CON CHÁU, không match chính phần tử gọi nó — nhánh đầu LUÔN trả `null`, rơi xuống `wrapper.querySelector('p')` (thẻ hiển thị placeholder bên trong, KHÔNG tự focus được — `focus()`/click không đổi `document.activeElement`). Click + gõ (CDP `Input.insertText`) vào `<p>` không thực sự vào ô nhập liệu thật → prompt vẫn RỖNG → nút Gửi không bao giờ hết `disabled` → task kẹt/timeout ở bước chờ nút Gửi phía sau với thông báo khó hiểu — đúng cảm giác user mô tả "mở profile xong không thấy vào gemini chạy gì cả".

**Đây KHÔNG PHẢI bug mới — Y HỆT bug đã tìm ra + fix ở `extensions/content.js::_findInput()`** (Chrome Extension, cùng DOM Gemini nhưng codebase HOÀN TOÀN riêng — xem `ToolSub` gốc `CLAUDE.md` mục "ROOT CAUSE THẬT SỰ ĐÃ XÁC NHẬN (v4, 2026-07-06)"). Fix đó (ưu tiên trả về CHÍNH wrapper nếu nó tự `contenteditable`) chưa bao giờ được áp dụng sang bản Selenium độc lập ở `client_tool` — 2 codebase không dùng chung code nên fix bên này không tự lan sang bên kia, đây là bài học đáng nhớ khi có ≥2 codebase cùng thao tác DOM của cùng 1 trang web.

**Fix (`server/worker.py::_gemini_type_prompt()`):** kiểm tra CHÍNH `wrapper` có `contenteditable="true"` TRƯỚC (ưu tiên trả về chính nó), chỉ tìm con cháu nếu wrapper không tự editable (phòng DOM đổi khác về sau). Thêm bước **verify SAU KHI gõ** — đọc lại `innerText.length` của `.new-input-ui`, `raise RuntimeError(...)` NGAY nếu vẫn `0` dù `prompt` không rỗng (lộ lỗi rõ ràng đúng chỗ thay vì để task âm thầm timeout mơ hồ ở bước chờ nút Gửi) — lớp phòng vệ THỨ 2 phòng DOM Gemini đổi khác lần nữa mà selector không còn khớp đúng.

**Verify:** `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome/Gemini thật** — cần user RESTART `main.py`/worker rồi chạy lại 1 task Gemini, xác nhận log thấy dòng submit (`[slot N] Gemini prompt submitted` hoặc tương đương luồng 1-tab) xuất hiện ngay sau bước gõ.

### 11.38b FIX THẬT thứ 2 (cùng ngày, phát hiện SAU khi user test lại trên máy khác): round-robin nhiều tab KHÔNG BAO GIỜ navigate tới Gemini lúc khởi động (2026-08-17)

User test lại trên máy KHÁC (khác máy đã dùng để phát hiện bug ở §11.38), gửi log thật của 1 profile round-robin ("Gemini Chat mode — 2 tab đồng thời"): log dừng NGAY sau `Chrome opened — sẵn sàng nhận prompt (round-robin)`, không có gì tiếp theo — "chỉ mở profile trắng ko vào gemini". Đây là bug KHÁC, SỚM HƠN bug ở §11.38 (bug đó chỉ xảy ra SAU KHI đã vào được trang Gemini và có task để gõ — bug này xảy ra TRƯỚC ĐÓ, ngay lúc khởi động, không liên quan gì tới việc có task hay không).

**Root cause:** `_run_gemini_loop_concurrent()` (§11.24) — sau `self.driver = self._make_driver()` — log "sẵn sàng nhận prompt" NGAY LẬP TỨC mà KHÔNG BAO GIỜ navigate đi đâu cả. Tab khởi động vẫn là trang trắng mặc định cho tới khi lô task ĐẦU TIÊN tới — lúc đó `_gemini_slot_step()`'s state `'new'` mới `driver.get(GEMINI_URL)` (KHÔNG check đăng nhập, xem §11.31's ghi chú "CHƯA áp dụng cho `_gemini_slot_step()`"). Chưa có task nào (hoặc phải chờ lâu) → browser đứng TRẮNG vô thời hạn. Khác hẳn luồng 1-tab tuần tự (`_run_gemini_loop`) đã navigate + `_ensure_google_login()` NGAY sau khi mở Chrome (§11.31) — round-robin bị SÓT HOÀN TOÀN bước này ngay từ lúc viết (§11.24), không phải regression mới.

**Fix (`server/worker.py::_run_gemini_loop_concurrent()`):** gọi `_ensure_google_login(GEMINI_URL)` NGAY sau khi mở Chrome — 1 LẦN DUY NHẤT lúc khởi động (không phải mỗi lần chuyển tab, không ảnh hưởng nhịp `gemini_tab_switch_interval`) — khớp trải nghiệm luồng tuần tự. `startup_tab_handle` (tái sử dụng tab khởi động làm slot đầu tiên) đọc `current_window_handle` SAU lời gọi đăng nhập (không phải trước) — `_ensure_google_login()` có thể đóng tab gốc + mở tab mới khi cần đăng nhập lại, đọc quá sớm sẽ bắt nhầm handle tab đã đóng. Các tab mở SAU (`_gemini_open_tab()`, slot 2 trở đi) dùng chung session/cookie Chrome nên `driver.get(GEMINI_URL)` blind ở `_gemini_slot_step` vẫn an toàn.

**Verify:** `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật** — cần user restart worker round-robin, xác nhận thấy `[google-login]...` rồi mới tới `Chrome opened — sẵn sàng nhận prompt (round-robin)` (không còn đứng trang trắng), rồi chạy thử 1 task xác nhận tiếp luôn hoạt động đúng (kết hợp với fix §11.38).

### 11.38c Setting `google_login_check_enabled` — tạm tắt bước "check đăng nhập Google" (2026-08-17)

User yêu cầu: "tạm tắt tính năng check login chạy thẳng vào trang nhận task, khi cần có thể bật lại" — nối tiếp §11.38/§11.38b (2 fix thật cùng ngày cho `_ensure_google_login()`), giờ có 1 công tắc để BỎ QUA HẲN toàn bộ luồng này thay vì sửa code mỗi lần muốn tắt/bật.

**Setting mới `google_login_check_enabled`** (`server/config.py::_DEFAULT_SERVER_SETTINGS`, mặc định **TẮT — `0`**, cùng style bool 0/1 như `debug_log_curl`/`quiet_hours_enabled`) — validate qua `local_settings.py::_clamp()`, sửa qua trang Cài đặt (`gui/pages/settings_page.py`) hoặc `PATCH /api/selenium/local_settings` (route sẵn generic).

**`_ensure_google_login()`** — short-circuit NGAY ĐẦU hàm: TẮT (mặc định) → bỏ qua HOÀN TOÀN bước vào `gmail.com`/kiểm tra ô email/tự nhập email-mật khẩu, chỉ còn `driver.get(target_url)` + `sleep(3)` rồi trả `True` ngay. Vì hàm này là ĐIỂM ĐẾN NAVIGATE DUY NHẤT cho MỌI entry point đã wire (VEO3 dom/api, gemini, gemini_video, gemini round-robin — §11.31/§11.38b), tắt setting áp dụng ĐỒNG LOẠT không cần sửa từng call site. BẬT lại (`1`) khôi phục nguyên vẹn luồng đầy đủ.

**Đánh đổi khi TẮT (chủ ý, không phải side-effect):** không còn tự phục hồi nếu session Google bị đăng xuất giữa chừng — rơi lại hành vi CŨ trước khi có `_ensure_google_login()` (task lỗi ở bước tìm DOM vì đứng ở trang đăng nhập, cần "Mở login browser" xử lý tay).

**Verify:** `py_compile`/`pyflakes` sạch. Máy hiện tại có sẵn `local_settings.json` chưa từng lưu key này — xác nhận cơ chế merge-default trong `get_local_settings()` tự điền `0`, không cần sửa tay JSON. **CHƯA verify trên Chrome thật** — cần user restart worker, xác nhận KHÔNG còn thấy log `[google-login]...` (navigate thẳng vào target).

---

### 11.39 Bậc thang escalation THEO BATCH — batch có ≥1 task thành công = thành công (2026-08-20)

Theo yêu cầu user: *"Nếu batch gửi lên 5 task 1 lúc mà thành công 1 vẫn tính batch thành công -> nhưng nếu cả batch đều không thành công liên tiếp 2 batch liền -> thì mới xóa cache cookie không ảnh hưởng login rồi click button create lại -> nhưng vẫn lỗi tiếp tục batch kế tiếp đó -> thì chuyển sang chế độ ngủ -> trong lúc ngủ xóa tất cả cookie - cache -> rồi check login -> đăng nhập vào project lại như trước -> vòng lặp cứ thế"*.

**Bậc thang THỨ 3 — SONG SONG, KHÔNG thay thế 2 bậc thang THEO TASK đã có** (§11.5/§11.8): (1) `error_count_before_refresh`/`refresh_count_before_new_project` — lỗi LIÊN TIẾP từng task → refresh → project mới → ngủ; (2) `error_window_minutes`/`error_window_max_errors` — N lỗi trong M phút. Bậc mới đếm ở mức **BATCH** (1 lần heartbeat nhận về N task), bắt đúng lớp sự cố 2 bậc kia bỏ sót: **cả lô cùng chết vì 1 nguyên nhân chung** (session/cookie hỏng, project bị Google chặn) chứ không phải vài task lẻ lỗi rải rác.

**Định nghĩa "batch thành công" = có ÍT NHẤT 1 task thành công** (dù N-1 task còn lại lỗi hết). Đếm qua `_batch_success_count`, tăng trong `_handle_task_success()` — điểm funnel **DUY NHẤT** của mọi đường thành công (9 call site: DOM batch/API batch/tuần tự/reconcile) nên 1 chỗ phủ hết mọi `worker_mode`. **Reset ở ĐẦU mỗi `_process_tasks()`** — chỉ khởi tạo ở `__init__` là BUG (bộ đếm tích luỹ qua các batch → 1 task thành công ở batch đầu khiến MỌI batch lỗi về sau bị hiểu nhầm là thành công; bắt được lúc test).

**⚠️ ĐỔI HÀNH VI so với §11.30-mở-rộng (2026-08-17/18)** — theo trả lời AskUserQuestion của user (*"theo phương án 1: nhưng không click create vì chỉ khi xóa cookie mới cần click"*). TRƯỚC ĐÂY `_cdp_clear_cache_and_cookies()` + click "Create with Google Flow" chạy sau **MỌI** batch vô điều kiện; giờ chỉ chạy đúng bậc thang:

| Kết quả batch | refresh + reconcile | xoá cookie+cache | click "Create with Google Flow" |
|---|---|---|---|
| Thành công (≥1 task ok) | ✔ | ✘ | ✘ |
| Lỗi, chưa chạm ngưỡng | ✔ | ✘ | ✘ |
| Lỗi, chạm `batch_fail_count_before_cleanup` (2) | ✔ | ✔ chỉ domain `labs.google` (GIỮ login Google) | ✔ |
| Lỗi, chạm `batch_fail_count_before_sleep` (3) | — | ✔ **TẤT CẢ** cookie + cache (lúc ngủ) | — |

Bước **refresh + reconcile KHÔNG bị gate** ở mọi nhánh — đó là đường DUY NHẤT media đã render xong được ghi nhận `done` về server sau batch (gate nó = task hoàn tất không bao giờ được đánh dấu). Bậc "dọn cookie" **KHÔNG reset bộ đếm** — batch kế tiếp vẫn lỗi thì đi tiếp lên bậc ngủ, đúng ý *"nhưng vẫn lỗi tiếp tục batch kế tiếp đó"*.

**Hàm/state mới:**
- `worker.py::_finish_batch_cleanup()` — đánh giá + áp bậc thang, gọi trong `finally` của `_process_tasks()`.
- `worker.py::_recover_flow_project_page_after_cache_clear(click_create=True)` — `False` bỏ hẳn bước poll 6s tìm nút Create (màn hình xen giữa đó CHỈ xuất hiện khi session/cookie vừa reset).
- `managers.py::_purge_all_cookies()` + `ProfileManager.clear_cache_and_all_cookies()` — xoá SẠCH bảng `cookies` kể cả họ SID. KHÁC `_purge_non_login_cookies()` (giữ login, vẫn không có caller) và KHÁC `clear_browser_data()` (xoá NGUYÊN `profile_dir`, mất cả `Login Data`/mật khẩu Chrome — chỉ dùng cho nút "Làm mới profile" thủ công). Ở đây **GIỮ `Login Data`** để bước tự đăng nhập lại dùng được.
- `state.py::_force_login_check` (set module-level) — **BẮT BUỘC** vì `google_login_check_enabled` mặc định TẮT (0, §11.38c): vừa xoá sạch cookie mà bỏ qua check thì worker mới vào thẳng labs.google ở trạng thái chưa đăng nhập và MỌI task sau đó đều lỗi. `_ensure_google_login()` đọc-và-`discard()` (cờ dùng-1-lần, lần sau trở về đúng theo setting). Phải module-level (không phải trên object worker) vì worker cũ sắp thoát — worker mới sau khi hết ngủ là instance khác hoàn toàn (cùng lý do/pattern `_sleep_until_by_pid`).
- `worker.py::_clear_profile_if_sleeping()` — rẽ nhánh theo `_sleep_wipe_all_cookies`: bậc BATCH → `clear_cache_and_all_cookies()` + set `_force_login_check`; 2 bậc TASK cũ → giữ NGUYÊN `clear_cache_only()` (không đụng cookie, không ép login). 2 đường tách biệt hoàn toàn.

**2 setting mới** (`config.py`, sửa ở GUI trang Cài đặt): `batch_fail_count_before_cleanup` (2), `batch_fail_count_before_sleep` (3). `_clamp()` có **ràng buộc chéo** — ngưỡng ngủ ≤ ngưỡng dọn thì tự nâng lên `dọn + 1`, vì đặt sai sẽ khiến bậc "dọn cookie" KHÔNG BAO GIỜ chạy được (chạm ngưỡng ngủ trước, worker thoát luôn).

**⚠️ Bẫy `return` trong `finally` (tránh ngay lúc code, KHÔNG phải do test fail):** bản nháp đầu định `return escalated` từ trong `finally` của `_process_tasks()` để truyền quyết định "ngủ" ra ngoài — `return` trong `finally` **NUỐT LUÔN exception đang lan ra**, batch lỗi nặng biến mất im lặng, `run()` mất cả log lẫn bước check `_is_driver_alive()`. Thay bằng: `_finish_batch_cleanup()` tự set `self._sleep_until`, `run()` check mốc đó **sau CẢ `try` LẪN `except`** — đặt sau cả 2 nhánh nên bắt được luôn trường hợp batch kết thúc bằng exception (lúc đó `finally` vẫn đã chạy bậc thang batch).

**Verify:** `tests/_test_batch_escalation.py` (chạy code THẬT, chỉ stub bước đụng Chrome/HTTP) — 8/8 nhóm pass, phủ đủ 4 nhánh bậc thang + không-regression bậc TASK cũ + `_clamp()` ràng buộc chéo. Test SQLite THẬT cho `_purge_all_cookies()` (xoá 5/5 kể cả SID/HSID; đối chứng `_purge_non_login_cookies()` vẫn giữ đúng SID/HSID/chatgpt). Round-trip setting thật qua `local_settings`. `py_compile`/`pyflakes` sạch 6 file. **CHƯA verify trên Chrome/labs.google THẬT** — cần user chạy 1 phiên thật.

### 11.39b TẠM TẮT bậc thang "time-window" — nó cướp quyền bậc thang THEO BATCH (2026-08-20, ngay sau §11.39)

User chạy thử bản §11.39 rồi báo log thật: `[error] 5 lỗi trong 50 phút (time-window) — cho profile "ngủ" 60s`, kèm yêu cầu *"theo luồng mới ẩn setting này trước, tạm thời ko dùng nữa gây xáo trộn luồng"*.

**Root cause:** bậc thang "time-window" (`error_window_minutes`/`error_window_max_errors`, trong `_record_error()`) đếm lỗi **TÍCH LUỸ THEO THỜI GIAN**, KHÔNG biết ranh giới batch — 5 lỗi rải rác suốt **50 PHÚT** (có thể xen kẽ nhiều batch THÀNH CÔNG ở giữa) vẫn ép ngủ. Vì `_record_error()` được gọi ở **MỌI** nhánh lỗi VÀ được `_handle_task_error()` gọi **ĐẦU TIÊN** rồi `return True` ngay nếu nó cho ngủ, nó thường xuyên **CƯỚP QUYỀN** bậc thang THEO BATCH trước khi bậc đó kịp chạy hết chuỗi "2 batch lỗi → dọn cookie + click Create → batch 3 → ngủ".

**Thay đổi:** khối time-window trong `_record_error()` bọc `if False:` (GIỮ NGUYÊN code — cùng convention "tạm tắt, không xoá" của `_purge_non_login_cookies()`); hàm giờ LUÔN trả `False`, phần **bookkeeping vẫn chạy nguyên** (`_task_error_count`/`_last_error_msg`/`pm.bump_task_stat` — dashboard không đổi). 2 setting chuyển từ `_FIELD_DEFS` sang `_HIDDEN_FIELD_DEFS` (`gui/pages/settings_page.py`) — **ẩn khỏi UI, KHÔNG xoá khỏi `_DEFAULT_SERVER_SETTINGS`/`_clamp()`**: `local_settings.json` của máy user đã lưu `error_window_minutes: 50`, xoá key sẽ làm giá trị đó biến mất ở lần `update_local_settings()` kế tiếp. Đã verify `_on_save()` chỉ build payload từ `_FIELD_DEFS` rồi PATCH (merge từng key) nên field ẩn không bị wipe khi bấm Lưu. **Bật lại:** `if False:` → `if True:` + chuyển 2 dòng từ `_HIDDEN_FIELD_DEFS` ngược lên `_FIELD_DEFS`.

**2 bậc thang còn lại KHÔNG bị ảnh hưởng** (verify tường minh): escalation LIÊN TIẾP (`_handle_task_error()`) + bậc thang THEO BATCH (§11.39).

**Verify:** `tests/_test_timewindow_disabled.py` — 5/5 pass: bắn 10 lỗi liên tiếp (gấp đôi ngưỡng cũ) → không ngủ/không ghi `_sleep_until_by_pid`/không set `sleeping`, nhưng counter dashboard vẫn đúng; bậc LIÊN TIẾP vẫn refresh đúng 1 lần sau 3 lỗi và vẫn tới được nhánh ngủ cuối; bậc BATCH vẫn đủ 3 nấc; mô phỏng payload GUI bấm Lưu → giá trị `50` của user giữ nguyên. `tests/_test_batch_escalation.py` chạy lại vẫn 8/8 (không regression).

### 11.40 FIX THẬT: chỉ ngưỡng 300 media mới được tạo project mới — đăng nhập lại KHÔNG được tạo mới (2026-08-23)

User: *"xóa cookie đăng nhập lại tới bước click create xong phải vào lại link
dự án cũ, khi nào bằng setting 300 media thì mới tạo project mới -> hiện tại
hình như login lại là đã tạo project mới"*.

**Root cause:** `_ensure_flow_page()`/`_ensure_flow_project()` kết thúc bằng
`if '/project/' not in current: _click_new_project_button()` — chạy khi CHỈ 1
lần `driver.get(project_url)` không về được `/project/`. Sau khi xoá sạch
cookie + đăng nhập lại (bậc thang batch §11.39), lần vào đầu rất hay bị bật ra
trang chung (session mới chưa "ấm") hoặc gặp lại màn hình xen giữa "Create with
Google Flow" (§11.28) — code cũ coi là "project hỏng" và tạo mới NGAY. Thêm lỗ
hổng riêng của `_ensure_flow_page()`: khối navigate nằm TRONG
`if 'labs.google' not in current:` nên khi ĐANG đứng ở trang chung labs.google
(rất hay gặp sau đăng nhập lại), nó **chưa từng thử** `project_url` lần nào.

**Fix:** `_return_to_saved_project(attempts=3)` — kiên trì quay lại đúng
`profile.project_url`, mỗi vòng `driver.get(saved)` → xử lý màn hình xen giữa
(gọi LẠI mỗi vòng, nó có thể hiện lại ở session mới) → đọc lại URL; hết 3 vòng
mới trả `False`. Gọi TRƯỚC nhánh "New project" ở cả 2 hàm, và ở cuối
`_recover_flow_project_page_after_cache_clear()` (đúng yêu cầu "click create
xong phải vào lại link dự án cũ" — chỗ này KHÔNG BAO GIỜ tạo project mới).

**Luân chuyển project vẫn CHỈ do `_rotate_project_if_full()`** (ngưỡng
`max_project_media_items`=300, §11.20e) **và `_reset_flow_project()`** (bậc
`refresh_count_before_new_project`) — 2 đường CỐ Ý, không đụng tới.

⚠️ **Quy tắc chung rút ra:** mọi nhánh "không thấy `/project/` → tạo mới" phải
thử lại `profile.project_url` trước. Thêm điểm navigate mới sau này nhớ gọi
`_return_to_saved_project()` thay vì tự `driver.get()` 1 lần rồi bỏ cuộc.

**Verify:** `tests/_test_return_to_saved_project.py` — 6 nhóm/12 assertion, dựng
worker bằng `object.__new__` + driver giả (không mở Chrome). Quan trọng nhất:
toàn luồng `_ensure_flow_page()` khi đang ở trang chung labs.google → KHÔNG tạo
project mới, vào lại đúng project cũ; project hỏng thật → vẫn tạo mới + log rõ
lý do. **CHƯA verify trên Chrome/labs.google thật.**

**Bổ sung (cùng ngày) — vá 3 kẽ hở khiến project VỪA TẠO bị "mồ côi":** user hỏi
*"có cơ chế khi tạo project mới có lưu id mới để lần sau quay lại project id mới
chưa"*. Có (cả 4 nơi đều `pm.update()` + gán RAM), nhưng audit lộ ra 3 lỗ:
(1) `pm.update()` **raise** khi backend chập chờn (`_api()` không `quiet`) và
raise TRƯỚC dòng gán RAM ⇒ mất cả DB lẫn RAM, exception còn lan ra huỷ batch;
(2) `_wait_for_project_url()` không phân biệt project MỚI với CŨ ⇒ trả URL cũ,
log "✔ Đã tạo project mới" SAI, riêng `_rotate_project_if_full()` còn reset bộ
đếm media về 0 trong khi vẫn ở project đã đầy 300; (3) timeout 20s trong khi
Google điều hướng muộn hơn ⇒ project mới không được ghi lại.

Fix: **`_persist_project_url()`** (điểm DUY NHẤT ghi field này — gán RAM TRƯỚC,
`pm.update()` bọc try/except) · **`_wait_for_project_url(..., exclude_url=)`**
(bỏ qua URL trùng project cũ, cả 4 call site truyền vào) ·
**`_sync_project_url_from_browser()`** (đầu `_ensure_flow_page()`/
`_ensure_flow_project()`: đang đứng ở project khác cái đã lưu ⇒ lưu lại theo cái
đang đứng — so bằng `_project_id_from_url()` nên khác query string không tính là
khác project).

⚠️ **Quy tắc:** KHÔNG gọi thẳng `pm.update(project_url=...)` ở bất kỳ đâu nữa —
luôn qua `_persist_project_url()`.

Verify: test mở rộng 6→11 nhóm/23 assertion (bỏ stub `_wait_for_project_url`,
dùng hàm thật) — gồm ca `pm.update()` ném lỗi (RAM vẫn đúng, không lọt
exception), ca tự đồng bộ, ca khác-query-string không ghi DB thừa.
`_test_batch_escalation.py` 8/8 không regression.

### 11.41 FIX THẬT: "xoá cookie" chưa bao giờ SẠCH — cookie chỉ là 1 trong ~10 kho site-data (2026-08-23)

User: *"clear cookie lúc ngủ ko sạch sẽ hay sao, tôi phải clear bằng tay tất cả
cookie trên trình duyệt thì không bị labs.google bắt lỗi"*.

**Root cause:** mọi cơ chế dọn cookie từ trước tới giờ (`_purge_all_cookies()`
lúc ngủ, `_cdp_clear_cache_and_cookies()` mỗi batch) chỉ đụng **cookie**. Nút
"Clear browsing data → **Cookies and other site data**" mà user bấm tay xoá rộng
hơn nhiều — còn `Local Storage`/`Session Storage`/`IndexedDB`/`Service Worker`
(+ `CacheStorage`, script nền tự sống lại)/`Trust Tokens` (**token chống gian
lận do chính Google phát hành**)/`Network Persistent State`/`Reporting and NEL`/
`TransportSecurity`. Để nguyên số đó ⇒ log báo "đã xoá N cookie" nhưng site vẫn
nhận ra đúng profile cũ.

**Fix 2 tầng:**
- **Lúc NGỦ** — `managers.py::_purge_all_site_data()` (thay `_purge_all_cookies()`
  trong `clear_cache_and_all_cookies()`): xoá theo whitelist tên thư mục/file,
  quét gốc + con 1 cấp + `*/Network/`. **Xoá HẲN file `Cookies`** thay vì DELETE
  rows (không để residue WAL/journal). ⚠️ **PHẢI GIỮ `Local State`** (khoá
  os_crypt để giải mã `Login Data`) và `Login Data*` — xoá là hỏng luôn bước tự
  đăng nhập lại.
- **MỖI BATCH** — `worker.py::_cdp_clear_labs_site_data()`: CDP
  `Storage.clearDataForOrigin` giới hạn ĐÚNG origin `labs.google` nên KHÔNG đụng
  Google login (origin khác). Cố ý không kèm `cookies` trong `storageTypes` —
  cookie đã xử lý chọn lọc theo domain ở hàm gọi.

⚠️ **Quy tắc:** thêm bất kỳ cơ chế "dọn dẹp trình duyệt" nào sau này, nhớ cookie
KHÔNG đủ — phải kèm site data. Test: `tests/_test_purge_site_data.py`.

⚠️ **`storageTypes` của `Storage.clearDataForOrigin` — LUÔN dùng `'all'`, ĐỪNG
liệt kê tay.** Đó là enum CHẶT của CDP; sai 1 tên (vd `filesystems` thay vì
`file_systems`) là Chrome từ chối NGUYÊN LỆNH, mà bước này best-effort bọc
try/except nên chỉ thành 1 dòng warning — **toàn bộ việc dọn âm thầm không xảy
ra**. `'all'` đã giới hạn theo đúng origin nên không đụng origin khác. Bản hiện
tại giữ fallback sang danh sách tường minh (tên đúng spec) phòng CDP không nhận
`'all'`. Chỉ gọi cho `worker_mode in ('dom','api')` — Gemini/ChatGPT không bao
giờ đứng trên labs.google.

### 11.42 FIX THẬT: sau khi xoá cookie không quay lại project → batch sau tạo project mới vô ích (2026-08-23)

`_recover_flow_project_page_after_cache_clear()` nhốt phần quay-lại-project vào
trong `if click_create and _click_create_with_flow_if_present():`. Màn hình xen
giữa "Create with Google Flow" **CHỈ hiện khi ĐIỀU HƯỚNG VÀO url project**,
KHÔNG có trên trang chung — mà refresh ngay sau khi xoá cookie gần như luôn bật
về trang chung ⇒ không thấy nút ⇒ bỏ qua cả khối ⇒ worker nằm lại trang chung ⇒
batch sau `_ensure_flow_page()` thử 3 lần rồi **tạo project mới**.

**Thứ tự ĐÚNG:** refresh → **vào lại link project TRƯỚC** → lúc đó màn hình xen
giữa mới hiện → bấm. `_return_to_saved_project()` vốn đã làm đúng chuỗi này.

Fix: tách 2 việc không lồng nhau — (1) còn ở url project thì thử bấm nút (URL
không đổi khi kẹt màn hình đó); (2) **bất kể (1) ra sao**, không ở `/project/`
thì `_return_to_saved_project()`; (3) không quay lại được → bỏ qua reconcile
(chạy trên trang chung tốn refresh + ~15s mà vô ích). Test:
`tests/_test_batch_clean_recover.py`.

### 11.43 FIX THẬT: `_gemini_response_if_ready()` chốt "kết quả TRƯỚC ĐÓ" — race với model "thinking" / DOM chưa commit xong (2026-09-01)

User: *"code lấy kết quả chat gemini chưa chuẩn response chat trả về đầy đủ mà
lại bắt kết quả trước đó rồi báo JSON không hợp lệ"* — Gemini ĐÃ trả lời XONG
THẬT (đầy đủ trên UI), nhưng worker chốt 1 mẩu nội dung CŨ HƠN bản cuối, không
phải câu trả lời bị cắt cụt.

**Root cause:** hàm này dùng CHUNG cho MỌI field Gemini (kịch bản nháp/Bible/
storyboard viết lại/scene batch, cả 2 luồng 1-tab lẫn round-robin — xem §11.24)
— chỉ đọc `.markdown` của `structured-content-container` CUỐI CÙNG NGAY khi
`is_generating` (nút "Stop response"/loading-indicator) vừa về `false`, không
xác nhận gì thêm. 2 race khớp đúng triệu chứng:
1. Model "thinking" (Gemini 3.x Flash/Pro, dùng thật trong dự án) hiện 1 khối
   "Đang suy nghĩ" RIÊNG trước khối trả lời thật — `is_generating` về `false`
   đúng khe hở giữa 2 khối → `containers[last]` trỏ khối thinking (cũ hơn, gần
   như chắc chắn không phải JSON hợp lệ).
2. Angular re-render `.markdown` không cùng nhịp gỡ nút Stop — đọc `.innerText`
   đúng khung hình đó có thể là DOM CHƯA COMMIT xong đoạn cuối.

**Fix:** `_gemini_response_if_ready(stability_state)` — dict do CALLER giữ
xuyên suốt các lần poll của ĐÚNG 1 tác vụ, chỉ trả kết quả khi CÙNG 1 nội dung
(text, số ảnh) ổn định liên tục ≥1.2s (THỜI GIAN THỰC, không phải số lần poll —
2 caller nhịp poll khác nhau). Nội dung đổi giữa 2 lần đọc → reset đồng hồ.
`_gemini_wait_response()` (1-tab) tự giữ 1 dict cục bộ; round-robin
(`_gemini_slot_step()`) — MỖI SLOT dict RIÊNG (`slot['response_stability']`,
mỗi tab 1 conversation độc lập). Verify: test độc lập trích ĐÚNG công thức
trong code (không mock DOM) — 4 kịch bản PASS (thinking-bị-thay/DOM-commit-dở/
bình-thường/is_generating-flip-lại). **CHƯA verify trên Chrome/Gemini thật.**

---

### 11.44 ⚠️ FIX THẬT LỚN: Google Flow đổi HẲN sang domain/app MỚI `flow.google.com` (Angular Material, thay React cũ) — viết lại toàn bộ `_dom_configure()`/`_dom_upload_images()`/`_dom_fill_and_submit()` (2026-09-03)

User: *"DOM video veo3 trang flow đã thay đổi hãy truy cập profile xem log và
điều khiển profile update theo giao diện mới"* — profile `huavantien84_2`
(id=25, `worker_mode='dom'`, `task_mode='video_only'`) báo **35 lỗi/0 thành
công trong ngày**, mọi task đều fail ngay ở bước cấu hình/đính ảnh:
`DOM: config button not found`, `[imageToVideo] add_2 button not found`, và
với task không ảnh: `DOM: prompt textarea not found (div[role="textbox"])`.

**Root cause — KHÔNG phải 1 selector lẻ đổi, mà là Google thay HẲN app:**
`labs.google/fx/vi/tools/flow/project/{uuid}` giờ **REDIRECT** sang domain
MỚI hoàn toàn **`flow.google.com/project/{uuid}`** — app cũ (React +
styled-components hash + Slate.js editor + icon ligature `<i>`) bị thay bằng
app MỚI (**Angular Material** — `mat-icon`/`flow-*` custom element/CDK
overlay + **ProseMirror** editor). Xác nhận bằng cách attach CDP vào Chrome
ĐANG MỞ THẬT của profile 25 (`POST /api/selenium/profiles/25/open` rồi
`webdriver.Chrome(options={'debuggerAddress':'127.0.0.1:9325'})`, script
throwaway — **KHÔNG BAO GIỜ** bấm "Start generation" trong suốt quá trình
điều tra, chỉ mở menu/gõ chữ/click model rồi `driver.get()` lại URL
project để huỷ mọi state trước khi kết thúc mỗi script, không tốn quota
thật) — KHÔNG đoán từ code cũ.

**Selector MỚI (khác hẳn UI cũ, xem đầy đủ trong code):**
- Ô nhập prompt: `.ProseMirror[contenteditable="true"]` (KHÔNG còn
  `div[role="textbox"]`). Enter KHÔNG còn tự submit như Slate.js cũ — phải
  bấm THẲNG `button[aria-label="Start generation"]` (tự hết `disabled` ngay
  khi ProseMirror có text, verify qua CDP `Input.insertText`).
- Nút mở settings: `button[aria-label="Settings trigger"]` (KHÔNG còn icon
  `crop`). Popup MỚI (`<flow-prompt-box-settings>`, Angular CDK overlay
  `popover="manual"` — **không có `aria-expanded`/`data-state` đáng tin cậy**,
  detect "đang mở" bằng `!!document.querySelector('flow-prompt-box-settings')`)
  gồm các nhóm `<flow-toggles aria-label="Mode|Video type|Aspect ratio|
  Output count">` chứa `<mat-button-toggle-group><button role="radio"
  aria-checked="true|false">` — KHÔNG còn khái niệm "tab" (`role="tab"`)
  như UI cũ. Mapping GIỮ NGUYÊN Ý NGHĨA cũ: Mode Image/Video, "Video type"
  = Frames/Ingredients (Frames↔`frameToVideo`, Ingredients↔`imageToVideo`/
  `componentsToVideo`, đúng như "Khung hình"/"Thành phần" cũ). **Model** là
  1 nút RIÊNG `button[aria-label="Select model family"]` (aria-label DUY
  NHẤT trên trang — bỏ hẳn kỹ thuật scope-theo-popup
  `window.__modelPopupRoot` của bản cũ) mở `<mat-menu>` chứa
  `<flow-menu-item><button mat-menu-item><span class="label">Tên
  model</span></button></flow-menu-item>` — label KHÔNG còn dính icon
  ligature chung element như bản cũ (span RIÊNG), chỉ cần xoá `<mat-icon>`
  con là sạch, bỏ luôn bước `.replace(/^[^A-Za-z0-9]+/, '')`.
- Đính ảnh tham chiếu ("Add ingredients"): nút mở picker đổi từ icon
  `add_2` sang `button[aria-label="Add ingredients to the prompt box"]`.
  Picker MỚI = side-nav (`mat-list-item[role="tab"]`, các category
  All/**Images**/Videos/Voices/Characters/Avatars/Uploads) + ô tìm
  `input[placeholder="Search assets"]` + gallery ảo-cuộn
  (`button.asset-item[role="option"]`, mỗi item text = tên file thật).
  **Ảnh ĐÃ từng upload (Case 1):** search ra ĐÚNG 1 kết quả → click item
  đó **TỰ ĐỘNG đính kèm + đóng cả popover luôn** (verify trực tiếp: 1 click
  duy nhất, KHÔNG còn bước "Thêm vào câu lệnh"/"Add to prompt" riêng cho
  case này — khác hẳn UI cũ). **Ảnh CHƯA từng upload (Case 2, trường hợp
  PHỔ BIẾN NHẤT trong thực tế vì mỗi ảnh scene chỉ dùng đúng 1 lần):** click
  nút "Upload media" (`mattooltip="Upload media"`) để lộ
  `input[type=file][multiple][accept=".png,.jpg,...,.mp4,.webm,..."]` ẩn,
  inject qua DataTransfer (GIỮ NGUYÊN kỹ thuật cũ — API HTML chuẩn, không
  phụ thuộc framework), chờ item hết chữ "Uploading" rồi click nó, rồi bấm
  nút **"Add to prompt"** (persistent bottom-bar, có mặt sẵn trong DOM ngay
  khi mở picker — KHÁC per-item confirm cũ). Chip đính kèm giờ là
  `<flow-image-ingredient-chip><img class="chip-image">` (thay
  `button[data-card-open] img[src*=getMediaUrlRedirect]` cũ) — verify count
  qua `document.querySelectorAll('flow-image-ingredient-chip img.chip-image').length`,
  KHÔNG cần scope thủ công lên container composer như bản cũ nữa (component
  này chỉ dùng ĐÚNG 1 chỗ trên trang).

**⚠️ Bug THỨ 2 tự bắt được lúc verify sống (không phải đoán):** chọn 1
model KHÁC model hiện tại đôi khi làm **ĐÓNG HẲN TOÀN BỘ** settings popup
(không chỉ đóng submenu model) ngay sau khi click item — verify từng bước
cho thấy: 2 overlay pane (settings+submenu) còn nguyên NGAY LÚC vừa click,
rồi CẢ HAI biến mất trong ~0.3s. Hành vi này **KHÔNG NHẤT QUÁN 100%**
(verify lặp lại nhiều lần: có lúc panel VẪN MỞ và label tự cập nhật đúng
trong ~0.3s, có lúc đóng hẳn) — cùng lớp flaky do Google có thể validate
tương thích model×ratio×resolution phía server (đã ghi nhận nhiều lần cho
dropdown model ở bản React cũ, §11.28). Bản `_dom_select_model_verified()`
đầu tiên đọc lại label bằng `document.querySelector('button[aria-label=
"Select model family"]')` **MÀ KHÔNG BAO GIỜ kiểm tra panel còn mở hay
không** — panel đã đóng thì query luôn `null`, verify LUÔN thất bại DÙ
SELECTION ĐÃ ÁP DỤNG ĐÚNG, và mọi lần retry sau đó CŨNG thất bại theo kiểu
KHÁC ("nút Select model family không tìm thấy") vì không hề thử mở lại
panel. Fix: helper `_ensure_settings_open()` — kiểm tra + tự mở lại panel
**TRƯỚC** khi tìm nút model (đầu mỗi attempt) **VÀ TRƯỚC** khi đọc verify
(ngay sau khi click item, panel đã đóng thì mở lại rồi mới đọc).

**⚠️ Phát hiện THỨ 3, KHÔNG PHẢI bug code — nghi vấn hạn chế tài khoản
(CHƯA XÁC NHẬN, cần user tự kiểm tra):** verify sống nhiều lần, đủ kiên
nhẫn (tới 15 giây patient-poll, loại trừ hẳn khả năng "chỉ là chậm settle")
cho thấy model **"Veo 3.1 - Lite"/"Veo 3.1 - Fast"/"Veo 3.1 - Lite [Lower
Priority]"** (model mà HẦU HẾT task video của profile 25 đang cấu hình sẵn
trong DB) **KHÔNG BAO GIỜ áp dụng được trên tài khoản `huavantien84@gmail.com`
hiện tại của profile này** — chọn xong label LUÔN tự quay về "Omni 1.1
Flash" (model mặc định/miễn phí), trong khi **"Veo 3.1 - Quality"** VÀ
"Omni 1.1 Flash" chọn đúng và giữ nguyên bình thường. Item vẫn HIỂN THỊ
trong dropdown, KHÔNG có class/attribute "disabled" rõ ràng nào phân biệt
— chỉ lộ ra khi thật sự chọn rồi verify lại. Nghi là **hạn chế entitlement/
gói AI Premium của chính tài khoản Google này** (Quality dùng nhiều credit
hơn nhưng account có quyền; Lite/Fast — dù RẺ HƠN — lại KHÔNG có quyền,
ngược trực giác nhưng khớp với việc "Omni" luôn là fallback) chứ KHÔNG phải
lỗi selector (code đã tìm ĐÚNG item, click ĐÚNG element, verify ĐÚNG cách —
xem bằng chứng "element AT that point" khớp `SPAN.label` đúng chữ khi chọn
Quality/Omni). **`_dom_select_model_verified()` sau khi fix vẫn đúng cách
xử lý** — báo `ModelSelectionFailed` rõ ràng (không âm thầm generate sai
model) thay vì tiếp tục với "Omni" không đúng ý — nhưng NẾU đúng là hạn chế
tài khoản thật, MỌI task cấu hình model Lite/Fast/Lower-Priority trên
profile này sẽ tiếp tục lỗi `ModelSelectionFailed` mãi mãi cho tới khi user
tự xác nhận: (a) tài khoản có đủ quyền/subscription cho các tier này hay
không (kiểm tra trực tiếp trên labs.google/flow.google.com bằng tay), hoặc
(b) đổi `model` của các task đó sang "Veo 3.1 - Quality" (tốn credit hơn
nhưng CHẮC CHẮN chọn được trên account này).

**Verify đã làm:** đầy đủ qua Chrome đang mở THẬT (không mock) —
`_dom_fill_and_submit()` (type qua CDP insertText + verify độ dài text +
chờ nút Start hết disabled, KHÔNG bấm submit thật), `_dom_configure()` (đủ
4 nhóm toggle Mode/Video type/Aspect ratio/Output count, đọc lại
`aria-checked` xác nhận đúng), `_dom_upload_images()` (Case 1 — attach ảnh
tham chiếu THẬT của 1 task production thật đang lỗi, task #29561, qua
đúng `POST /task/29561`'s `source_media` — verify chip count tăng đúng;
Case 2 — upload file MỚI hoàn toàn qua DataTransfer, quan sát trực tiếp
badge "Uploading" rồi "Add to prompt"), model-selection fix (patient
15s-poll xác nhận KHÔNG phải vấn đề timing). Toàn bộ 55 chuỗi JS
string-literal truyền vào `self._js()`/`self._wait_js()` trong file (AST-walk
tự động, không riêng code mới) đã syntax-check qua Node `new Function()`,
0 lỗi. `py_compile`/`pyflakes` sạch.

**API GENERATE cũng chạy được qua batchexecute — `ogiZ0b` (2026-09-03):**
user yêu cầu *"test liên tục tìm ra hướng chạy qua batchexecute… lấy param cơ
bản lúc vừa vào project truyền vào CURL… kèm đọc script, recaptcha mới"*.
`tests/_test_textToImage_new.py` (2 chế độ `--capture`/`--replay`) đã chạy
THÀNH CÔNG end-to-end: tự dựng lệnh, POST, nhận CDN URL, tải về ĐÚNG ảnh khớp
prompt (`a lone red fox standing in fresh snow at dawn`, 175 KB JPEG).

Shape body KHÔNG đoán từ bundle (proto minified lồng nhau) mà HỌC TỪ CAPTURE
THẬT — chạy đúng luồng DOM sản xuất 1 lần rồi chặn network lấy nguyên `f.req`:

    [1][0][3]            seed (int)
    [1][0][5]            "GEM_PIX_2"   model key
    [1][0][7][5]         projectId
    [1][0][7][10][0]     reCAPTCHA token
    [1][0][8][0][0][0]   prompt
    [1][0][12], [1][0][13], [4][0]     UUID HOA (id request/batch)
    [3]                  clientContext lặp lại (cùng token/projectId)

⚠️ **API MỚI VẪN CẦN reCAPTCHA** — phỏng đoán ban đầu ("batchexecute chỉ cần
bl/f.sid/at") SAI. Site key trang dùng (`enterprise.js?render=6LdsFiUs…`) KHỚP
ĐÚNG hằng `LABS_RECAPTCHA_SITE_KEY` sẵn có, nên mint lại bằng chính
`_get_fresh_recaptcha('IMAGE_GENERATION')` của production. So với đường
`aisandbox` cũ (§11.32/§11.33) thì BỎ được: bearer token, fingerprint
`x-browser-validation`/`x-client-data` (ExtraInfo), và `curl_cffi` impersonate
— gọi thẳng bằng `fetch()` trong trang là đủ.

3 nhóm giá trị PHẢI làm mới mỗi lần replay (token hết hạn ~2 phút, id trùng bị
từ chối): reCAPTCHA, 3 UUID, seed — `refresh_args()` dò theo ĐẶC ĐIỂM giá trị
(độ dài/regex/kiểu) chứ không hardcode path, nên vẫn đúng nếu Google đảo field.

**Bẫy gặp khi chạy (đã sửa):**
- `_batchexecute_call()` timeout cứng 20s → RPC SINH NỘI DUNG (ảnh) chờ lâu
  hơn thế. Đã thêm tham số `timeout` (mặc định 20, test dùng 180).
- Worker stub trong test thiếu `self._tokens` → `_get_fresh_recaptcha()` mint
  xong mới `AttributeError` lúc lưu token.

**Test giờ tự lo hạ tầng** — `tests/utils/profile_target.py` (MỚI): mặc định
attach profile 25, port suy từ CÔNG THỨC production (`9300 + id % 200`),
`project_id` đọc từ client_tool API (hoặc backend nếu GUI không chạy), và
`ensure_chrome()` TỰ MỞ Chrome bằng đúng helper sản xuất nếu chưa mở. Không
cần bật GUI client_tool để chạy test nữa. Cờ: `--profile-id`,
`--debugger-address`, `--no-attach`.

**⚠️ BUG PRODUCTION phát hiện khi test ref ảnh — `_dom_upload_images()` đính
kèm TRÙNG 3 ẢNH rồi vẫn báo lỗi (2026-09-03):** bắt được bằng
`tests/_diag_ref_upload.py` (chạy riêng bước upload, KHÔNG submit → không tốn
quota). Bước B5 TRƯỚC ĐÂY **bắt buộc** tìm thấy nút "Add to prompt", không
thấy thì `raise`. Nhưng trên UI hiện tại, click vào item VỪA UPLOAD đã **tự
đính kèm luôn** và popover tự đóng — nút đó không còn để mà tìm. Hệ quả: raise
→ vòng retry ngoài lặp lại TOÀN BỘ search/upload 3 lần → cuối cùng **4 chip
trong composer** (đo được `chip: 3` khi chỉ cần 1) rồi vẫn báo lỗi. Ảnh hưởng
THẬT: mọi task `imageToImage`/`imageToVideo`/`componentsToVideo` (luôn có ref
ảnh) đều dính — tức phần lớn task VEO production. Fix: kiểm tra chip TRƯỚC
(`_count_attached_refs()`), đủ rồi thì bỏ qua hẳn B5; chưa đủ mới tìm nút, và
không thấy nút cũng KHÔNG raise nữa (để bước validate 20s sẵn có phán quyết).
Verify sau fix: `chip: 1`, không raise.

**4 luồng generate đã chạy THẬT qua batchexecute (2026-09-03):**

| Luồng | rpcid | Kết quả verify |
|---|---|---|
| textToImage | `ogiZ0b` | ✅ ảnh đúng prompt, tải về 175 KB JPEG |
| imageToImage | `ogiZ0b` + `[1][0][2]`=imageInputs | ✅ giữ đúng nhân vật từ ref, đổi cảnh (370 KB) |
| textToVideo | `YhhmEf` | ✅ tạo workflow (async, không có URL ngay) |
| ingredientToVideo | `MZZa6b` | ✅ tạo workflow, ref `[0][0][1][0][1]` |

Body video KHÁC ảnh: model ở `[0][0][1]`/`[0][0][2]` (`abra_t2v_8s` /
`abra_r2v_8s`), prompt ở `[0][0][0][2][0][0][0]`, clientContext ở `[1]`, KHÔNG
có seed. RPC video là **BatchAsync…** nên POST trả về ngay — lấy kết quả sau
bằng reconcile hoặc `jwpduf` (=`BatchCheckAsyncVideoGenerationStatus`, quan
sát được trang tự gọi).

**Hạ tầng test: `tests/utils/flow_rpc.py`** — máy capture-rồi-replay dùng
chung cho mọi RPC (`FlowRpcTest`), 4 script mỏng gọi vào:
`_test_textToImage_new.py`, `_test_imageToImage_new.py`,
`_test_textToVideo_new.py`, `_test_ingredientToVideo_new.py`. Hỗ trợ ảnh tham
chiếu (`--ref`, phục vụ qua HTTP tạm rồi đính kèm bằng chính
`_dom_upload_images()` production), `capture_name` riêng khi 2 luồng dùng
CHUNG rpcid (textToImage/imageToImage đều `ogiZ0b`).

**2 bẫy đã dính khi làm, đáng nhớ:**
- `window.__rpcCap` sống theo vòng đời TRANG — không reload giữa 2 lần chạy
  thì request lần TRƯỚC vẫn còn, capture bắt phải request CŨ mà không báo gì
  (capture "imageToImage" đầu tiên hoá ra là 1 `ogiZ0b` cũ của textToImage,
  body giống hệt). Fix: xoá buffer NGAY TRƯỚC submit + lấy request MỚI NHẤT.
- `refresh_args()` bản đầu thay MỌI uuid chữ thường thành projectId → suýt
  ghi đè uuid ẢNH THAM CHIẾU. Fix: chỉ thay ĐÚNG project id cũ (trích từ
  `source-path` trong URL lúc capture).

**CHƯA làm / rủi ro còn lại:**
- **CHƯA cho 1 task THẬT chạy trọn vẹn qua hàng đợi heartbeat thật** (mọi
  verify đều gọi TRỰC TIẾP các hàm `_dom_configure`/`_dom_upload_images`/
  cấu trúc tương đương, KHÔNG BAO GIỜ gọi `_dom_fill_and_submit()`'s bước
  bấm submit thật để tránh tốn quota trong lúc điều tra — cần user tự bật
  lại profile 25 và theo dõi 1-2 task thật chạy trọn vẹn tới `done`).
- `_click_new_project_button()`/`_click_create_with_flow_if_present()`/
  `_ensure_google_login()`'s text-matching ("New project"/"Create with
  Google Flow"/"Sign in", đọc từ `i18n_texts.json`) **CHƯA re-verify** trên
  domain mới `flow.google.com` — các nhánh này chỉ chạy khi rơi vào trang
  chung (không có `/project/`), mà trong suốt điều tra LUÔN redirect thẳng
  vào đúng project nên chưa có cơ hội quan sát trực tiếp. Có rủi ro tương tự
  (React→Angular) nếu Google cũng đổi lại các màn hình đó.
- Các domain-membership check rải rác `'labs.google' in current_url`
  (`_ensure_flow_page()` dòng ~1117 và 1 vài nơi khác, xem comment cũ trong
  file) **CHƯA cập nhật** nhận diện `flow.google.com` — hệ quả: sau khi
  redirect, `current_url` không còn chứa `'labs.google'` nữa nên điều kiện
  này LUÔN đúng, khiến MỖI TASK đều re-navigate + re-chạy
  `_ensure_google_login()` (vào lại gmail.com kiểm tra đăng nhập) dù đã
  đứng đúng trang từ trước — LÃNG PHÍ ~15-20s/task (không sai chức năng,
  chỉ chậm) — log production xác nhận đúng hành vi này đang xảy ra. CHƯA
  sửa (ngoài phạm vi khẩn cấp của lần fix này — ưu tiên dừng 100% failure
  trước) — nên sửa sau bằng cách nhận diện CẢ 2 domain trong các check này.
- Cookie-purge domain scoping (`_BATCH_CLEAN_TARGET_DOMAIN='labs.google'`,
  §11.39-43) **CHƯA xác minh** có cần đổi/mở rộng sang `flow.google.com`
  hay không (chưa rõ session/cookie thật sự sống trên domain nào giờ đây —
  redirect có thể chỉ là app-shell, còn auth vẫn anchor ở `google.com`
  dùng chung, hoặc `flow.google.com` có cookie riêng — cần điều tra thêm
  nếu batch-cleanup không hiệu quả sau khi đổi domain).
- `projectInitialData` interceptor (dùng bởi `_reconcile_project_media()`,
  chạy cuối mỗi batch để lấy media đã hoàn tất) — log production cùng ngày
  cho thấy **CŨNG ĐANG LỖI** ("reload xong nhưng không bắt được request nào
  sau 15s"). **⚠️ ĐÃ ĐIỀU TRA + VIẾT LẠI HOÀN TOÀN, xem §11.45 — mục này
  ĐÃ LỖI THỜI.** Nguyên nhân thật: `projectInitialData` KHÔNG PHẢI "đổi
  transport" (đã từng thử vá thêm `XMLHttpRequest` bên cạnh `fetch()` dựa
  trên suy đoán "Angular HttpClient dùng XHR" — suy đoán đó SAI) mà là
  **endpoint này KHÔNG CÒN TỒN TẠI** trên `flow.google.com` — thay bằng RPC
  đa dụng `batchexecute` (`Zzl0ze`+`as29s`), xem §11.45.

⚠️ **Client_tool đang chạy (nếu có) CẦN RESTART** (`main.py`) để nạp code
mới — không hot-reload.

**FIX THẬT ngay sau đó (cùng ngày) — batch chỉ có 1 task đi SAI luồng, check
NGAY sau khi vừa submit thay vì submit-hết-batch-rồi-mới-check:** user: *"luồng
là nhập batch mới giống API rôi mới check các thứ chứ sao vừa tao xong 1 task
lại đi check rồi"*. Root cause: `_process_tasks()` chỉ route sang
`_run_tasks_batch()` (submit CẢ BATCH trước, `_wait_and_reconcile_tasks()` SAU
CÙNG — đúng luồng) khi `if self.worker_mode == 'dom' and len(tasks) > 1:` —
batch CHỈ CÓ 1 task (rất phổ biến khi backlog mỏng/`max_concurrent` thấp, xem
log thật §5.7/§11.5 — nhiều lần heartbeat chỉ nhận đúng 1 task) rơi xuống vòng
lặp generic bên dưới → `_run_task_dom()`, hàm này submit RỒI GỌI
`_wait_and_reconcile_tasks()` NGAY LẬP TỨC cho ĐÚNG 1 task đó — khác hẳn
`worker_mode='api'` (nhánh ngay dưới, `_run_tasks_api_batch()`) vốn LUÔN chạy
bất kể `len(tasks)`, không có ngưỡng nào cả. Fix: bỏ điều kiện `and len(tasks)
> 1` — DOM mode giờ ĐỐI XỨNG với API mode, luôn submit hết batch (kể cả batch
chỉ có 1 task) rồi mới reconcile 1 lần cho cả batch. `_run_task_dom()`/
`_run_task()`'s nhánh `dom` GIỮ NGUYÊN trong code (không xoá — vẫn được dùng
trực tiếp bởi `tests/_test_dom_batch.py`'s harness và có thể còn dùng cho mục
đích khác), chỉ KHÔNG CÒN được `_process_tasks()` gọi tới nữa cho `worker_mode
='dom'` — coi như dead code cho đường sản xuất chính, chưa xoá hẳn vì chưa
chắc chắn 100% không còn nơi nào khác cần.

**Verify:** `py_compile`/`pyflakes` sạch. `tests/_test_dom_batch.py` gọi thẳng
`_run_tasks_batch()` (không qua `_process_tasks()`) nên không bị ảnh hưởng bởi
thay đổi điều kiện routing này. **CHƯA verify trên Chrome/hàng đợi thật** —
cần user quan sát 1 batch chỉ có 1 task thật, xác nhận log không còn hiện
reconcile NGAY sau khi vừa submit xong đúng 1 task đó.

### 11.45 ✅ `projectInitialData` chết trên `flow.google.com` — chuyển HẲN sang RPC `batchexecute` (`Zzl0ze`+`as29s`), và mở đường generate qua `ogiZ0b` (2026-09-03, cùng ngày §11.44)

> 📄 **Tài liệu API đầy đủ: [`docs/FLOW_BATCHEXECUTE_API.md`](docs/FLOW_BATCHEXECUTE_API.md)**
> (endpoint, định dạng request/response, bảng 72 rpcid, shape body từng luồng,
> giá trị phải làm mới, bẫy đã dính). Mục này chỉ tóm tắt bối cảnh + quyết định.
>
> **TRẠNG THÁI: ĐÃ NỐI VÀO SẢN XUẤT.** `_dom_fetch_project_media()` /
> `_reconcile_project_media()` giờ chạy bằng `batchexecute`. Bản
> `projectInitialData` cũ (kể cả `_install_project_data_interceptor()`/
> `_media_item_prompt()`/`_media_item_type()`/`_dom_find_tile_src_by_name()`)
> đã XOÁ HẲN — endpoint đó không còn tồn tại nên giữ lại chỉ gây nhầm.
> Trình tự đúng như user yêu cầu: revert về bản cũ trước → chỉ update sau khi
> `tests/_test_textToImage_new.py` chạy thành công (đã thành công, xem dưới).

User: *"hãy chạy thực tế không cần mỗi lần DOM lại nhập test"* → sau khi quan
sát log production thật thấy `projectInitialData: reload xong nhưng không
bắt được request nào sau 15s` lặp lại ĐỀU ĐẶN, tôi đã thử vá THÊM
`XMLHttpRequest` (bên cạnh `fetch()` đã vá sẵn) dựa trên suy đoán "Angular
`HttpClient` mặc định dùng `XhrBackend`" — user CHẤN CHỈNH ngay: *"projectIni
tialData cơ chế mới ko còn link này hãy kiểm tra network và element UI để
tìm ra quy luật load dữ liệu mới"*. Suy đoán đó SAI — không phải vấn đề
transport, **endpoint này hoàn toàn không tồn tại** trên app mới (gọi thẳng
URL cũ trả về nguyên shell HTML SPA, không phải data).

**Điều tra Network tab thật** (attach CDP vào Chrome đang mở của profile
25, cài interceptor `fetch`+`XHR` bắt MỌI request `*batchexecute*`, KHÔNG
BAO GIỜ bấm nút generate — chỉ quan sát request app tự bắn khi vào/reload
project) xác nhận: `flow.google.com` dùng RPC đa dụng
**`batchexecute`** (`/_/AiSandboxAngularFrontend/data/batchexecute`) —
CÙNG protocol Google dùng chung nhiều sản phẩm (Gmail/Drive/Docs...),
không có URL riêng theo mục đích, multiplex nhiều "rpcId" qua 1 endpoint.
User chỉ thẳng hướng tiếp: *"batchexecute có kết quả image kèm prompt của
nó hãy tìm ra nguyên lý"*.

**2 RPC ID cần dùng** (xác nhận qua capture request+response ĐẦY ĐỦ, không
đoán schema):
- **`Zzl0ze`** — `args=["projects/{projectId}",null,null,null,[1]]` → LIỆT
  KÊ mọi media trong project. Response (sau khi bỏ tiền tố XSSI `)]}'` +
  decode 2 lớp — payload LÀ 1 CHUỖI chứa JSON, không phải object trực
  tiếp): `[null, [entry, entry, ...]]`. Mỗi `entry`:
  ```
  [tileId, null, null,
   [title, [ts_sec, ts_nsec], null, null, DETAIL_UUID, GEN_MARKER, [ts2]],
   projectId]
  ```
  `DETAIL_UUID` (index 4 của mảng con index 3) — uuid THẬT cần cho `as29s`
  VÀ khớp CHÍNH XÁC với uuid nhúng trong CDN URL của media đó
  (`flow-content.google/{image,video}/{DETAIL_UUID}?...`). `GEN_MARKER`
  (index 5) — `None` với media UPLOAD THÔ (reference ảnh user tự tải lên),
  khác `None` (1 chuỗi dạng UPPERCASE-với-gạch-ngang, có vẻ là workflow/
  history id — KHÔNG PHẢI 1 uuid media khác) với media ĐÃ QUA GENERATION —
  verify **100% trên 275 entry thật** của project production (130
  generated/145 upload, khớp NGƯỢC hoàn hảo với heuristic phụ "title trông
  như tên file .jpg/.png" — dùng làm bộ lọc trước khi tốn call `as29s`).
- **`as29s`** — `args=["{DETAIL_UUID}"]` → chi tiết 1 item: PROMPT ĐẦY ĐỦ
  (kèm `TASK_{id}:` nếu có, tìm bằng regex trên response đã flatten —
  KHÔNG cần biết đúng vị trí schema, giống `_extract_task_id_from_prompt()`
  cũ) + URL CDN CÓ CHỮ KÝ cho ảnh lẫn video — **CÙNG FORMAT CDN CŨ HỆT**
  (`flow-content.google/{image,video}/{uuid}?Expires=...&KeyName=labs-flow
  -prod-cdn-key&Signature=...`) — backend server-side tải được luôn, KHÔNG
  cần đổi gì bên `backend/services/media_download.py`. Trả `null` (không
  lỗi) cho item KHÔNG qua generation.

**⚠️ Bug thật #1 — dùng SAI uuid, mất cả buổi mới lộ ra:** bản đầu (viết
theo trí nhớ/note từ phiên trước, KHÔNG re-verify) dùng `entry[0]` (tile
id) làm uuid gọi `as29s` — verify sống lần đầu: gọi `as29s(entry[0])` cho
**130/130 candidate** đều trả về payload rỗng (`null`), kể cả với item MỚI
TẠO trong vòng vài phút. Ban đầu nghi là "retention window" (CDN URL hết
hạn, `as29s` chỉ phục vụ item recent — giả thuyết này ĐÚNG MỘT PHẦN, xem
bug #2, nhưng KHÔNG PHẢI root cause chính). Root cause thật: tìm 1 uuid
THẬT mà chính app đã tự `as29s` (bắt được từ capture tự nhiên, không phải
tôi tự construct) — `670e1b3f-...` (task #30806, có prompt+URL đầy đủ) —
rồi tra ngược nó trong `Zzl0ze` listing: nó nằm ở **`meta[4]`**
(`DETAIL_UUID`), KHÔNG PHẢI `entry[0]` (`entry[0]` cho item đó là
`0b34f54e-...` — 1 uuid HOÀN TOÀN KHÁC, có vẻ là "tile/card id" nội bộ của
UI, không dùng được cho `as29s`). Fix: đổi sang dùng `meta[4]` làm cả (a)
argument gọi `as29s` VÀ (b) field `name` gửi lên `/reconcile/check` (khớp
đúng uuid nhúng trong CDN URL — backend dedup theo `name`, sai uuid ở đây
sẽ dedup sai vĩnh viễn). Verify lại ngay lập tức: `as29s(meta[4])` cho
uuid vừa nói trả về ĐÚNG payload 767 byte với prompt+URL thật.

**Bug/quan sát #2 (phụ, không sửa gì — chỉ document):** `as29s` dường như
chỉ phục vụ item **RECENT** — 1 uuid từ ~10 ngày trước (dù `meta[5]` xác
nhận đã qua generation) trả `null` dù request hợp lệ 100% (đối chiếu cấu
trúc request/response TỪNG BYTE với 1 lệnh THẬT app tự gọi — khớp tuyệt
đối). Có vẻ liên quan tới cùng cơ chế CDN URL có hạn `Expires=` (đã biết
từ trước, xem §4.4 root CLAUDE.md "CDN URL validity") — item không được
xem/refresh trong 1 khoảng thời gian thì phía server có thể ngừng phục vụ
generation-detail (dù listing `Zzl0ze` vẫn giữ mãi). **KHÔNG PHẢI vấn đề**
cho use case thật (reconcile batch VỪA submit, luôn trong vài phút) — filter
`reconcile_lookback_secs` (setting mới, mặc định **7200s = 2h**, xem dưới)
tự nhiên đã giới hạn đúng phạm vi này, coi như "may mắn đúng luôn cả 2 lý
do" (giảm số lệnh `as29s`/vòng VÀ né retention window).

**Thiết kế cho `_dom_fetch_project_media()` (ĐÃ VIẾT + VERIFY, rồi REVERT —
xem khung cảnh báo đầu mục; giữ lại đây làm bản thiết kế để nối lại sau khi
`tests/_test_textToImage_new.py` pass):** harvest session token
(`bl`/`f.sid`/`at`) qua 1 lệnh `batchexecute` BẤT KỲ mà chính trang tự bắn
(không cần đúng rpcId, token dùng lại được cho lệnh TỰ CONSTRUCT khác —
verify trực tiếp) → gọi `Zzl0ze` → lọc candidate (`meta[5]` khác `None` +
`meta[1][0]` (timestamp) trong `reconcile_lookback_secs` gần nhất) → gọi
`as29s` TUẦN TỰ cho từng candidate → trích `TASK_id`+URL bằng regex trên
response đã flatten → trả `list[{'name','type','taskId','prompt','url'}]`
— **ĐÃ CÓ SẴN `url`** ngay từ bước fetch (khác hệ thống cũ phải "resolve"
URL riêng ở giai đoạn 2, vì `as29s` trả prompt VÀ url CÙNG 1 lần gọi,
không có cách nào tách rẻ/đắt như trước) — `_reconcile_project_media()`
đơn giản hoá theo, bỏ hẳn 2-giai-đoạn "check trước (không url) → resolve
url cho unknown sau", giờ gửi TẤT CẢ 1 lần lên `/api/media/reconcile/check`
kèm url luôn.

**Setting `reconcile_lookback_secs`** (mặc định 7200 = 2h) — bound số lệnh
`as29s`/vòng reconcile (project có thể có hàng trăm media lịch sử, gọi
`as29s` cho TẤT CẢ mỗi vòng sẽ rất chậm) VÀ tự nhiên né retention window
(bug/quan sát #2). ⚠️ Đã THÊM rồi GỠ khỏi `config.py`/`local_settings.py`/
GUI Cài đặt cùng lúc với việc revert (không để lại setting chết trong UI) —
thêm lại khi nối cơ chế mới vào sản xuất.

**`_extract_task_id_from_prompt()` DÙNG ĐƯỢC CHO CẢ 2 HỆ** — logic
`re.search(r'TASK_(\d+):', ...)` không neo đầu chuỗi vẫn đúng dù INPUT là
"prompt text 1 item" (hệ cũ) hay "response `as29s` đã flatten thành JSON
string" (hệ mới). `_media_item_prompt()`/`_media_item_type()`/
`_dom_find_tile_src_by_name()`/`_install_project_data_interceptor()` từng bị
xoá lúc viết bản mới, nay đã KHÔI PHỤC NGUYÊN VẸN cùng lần revert (đường
sản xuất cần chúng).

**Verify — LIVE END-TO-END trên production thật (không mock, không dùng
capture cũ):**
1. `py_compile`/`pyflakes` sạch. 56/56 JS string literal (AST-walk toàn bộ
   `_js`/`_js_async`/`_wait_js` call, kể cả code KHÔNG MỚI) parse hợp lệ
   qua Node `new Function()`.
2. Attach CDP vào Chrome ĐANG MỞ THẬT của profile 25 (KHÔNG mở Chrome mới,
   KHÔNG BAO GIỜ bấm generate) — gọi TRỰC TIẾP `_batchexecute_harvest_
   session()`/`_dom_fetch_project_media()`/`_reconcile_project_media()`
   (code THẬT, qua `SeleniumFlowWorker.__new__` + gán `self.driver` —
   cùng pattern investigation trước đó) — cả 3 chạy không exception.
3. **Kết quả live:** `Zzl0ze` liệt kê đúng 275 media (khớp phân tích
   offline trước đó). Với `reconcile_lookback_secs=7200` (default thật):
   `_dom_fetch_project_media()` trả về **11 item** — TOÀN BỘ đúng shape
   `{name, type:'video', taskId, prompt, url}`, taskId là số THẬT (30782,
   30784, 30786, 30788, 30800, 30802, 30806, 30822...). `_reconcile_
   project_media(set())` gửi lên backend production thật
   (`api.minhchinhecommerce.com:14443`) — **8 task THẬT được `matched`**
   (backend tự tải + set `done`) — đây là 8 task video **THẬT SỰ ĐANG
   MẮC KẸT** trong DB production (không reconcile được từ lúc Google đổi
   UI) được phục hồi NGAY LÚC VERIFY, không phải dữ liệu giả lập.
4. Đối chiếu request/response TỪNG BYTE của `_batchexecute_call()` (tự
   construct) với lệnh THẬT app tự gọi (URL query param thứ tự giống hệt,
   `f.req` body structure giống hệt) — khớp tuyệt đối.

**Bảng rpcid — 72 cái, trích được TỪ BUNDLE (2026-09-03, cùng ngày):** rpcid
KHÔNG phải chuỗi ngẫu nhiên phải mò — bundle JS nhúng thẳng mỗi rpcid KÈM
TÊN METHOD BACKEND theo pattern `new _.xx("<rpcid>", <ReqMsg>, <RespMsg>,
[_.im,!0,_.hm,"/FlowService.<Method>"])`. `tests/_extract_rpcid_map.py`
(GIỮ LẠI trong repo) tự tải bundle lớn nhất rồi regex ra bảng đầy đủ — chạy
lại khi Google đổi build, không phải mò từ đầu. Xác nhận:
`Zzl0ze`=`/FlowService.GetProjectContents`, `as29s`=`/FlowService.GetMedia`.
Đáng chú ý — có RPC batchexecute cho TOÀN BỘ pipeline generate (đường này
chỉ cần `bl`/`f.sid`/`at`, KHÔNG recaptcha/fingerprint/curl_cffi như đường
`aisandbox` hiện dùng, xem §11.32/§11.33): `maseQ`=UploadImage,
`ogiZ0b`=BatchGenerateImages, `YhhmEf`=…VideoText,
`MZZa6b`=…VideoReferenceImages, `eb1hJf`=…VideoStartImage,
**`nprQif`=…VideoStartAndEndImage** (← trả lời câu hỏi treo ở §11.33 về
`endImage`: CÓ method riêng), `jwpduf`=BatchCheckAsyncVideoGenerationStatus,
`HTrJv`=GetModels. Verify map dùng được thật: gọi `HTrJv` bằng chính
`_batchexecute_call()` → trả về catalog model đầy đủ (`veo_3_1_t2v_lite_
low_priority`, `veo_3_1_r2v_lite_low_priority`, `abra_i2v_8s`…), tiện thể
xác nhận suy luận swap `t2v`→`r2v` ở `model_catalog.py` (§11.34) là ĐÚNG.

**`_reqid` có quy luật, không random (2026-09-03):** `_reqid = seed +
100000*n`, `seed` = SỐ GIÂY tính từ 00:00:00 giờ ĐỊA PHƯƠNG (chốt 1 lần lúc
load trang), `n` = số thứ tự request batchexecute trong lần load đó — verify
qua 24 request thật (bậc thang +100000 không sót nhịp; 2 lần load cách nhau
14s cho seed lệch đúng 14, khớp chính xác đồng hồ lúc đo). `_batchexecute_
call()` hiện dùng số random 6 chữ số — CHẠY ĐƯỢC (đã verify) nhưng lệch quy
ước; từng sửa cho nối đúng chuỗi của trang rồi REVERT theo yêu cầu user.
Muốn sửa lại: lấy `max(_reqid)` trang đã dùng (parse từ `window.__beCalls`)
rồi +100000 mỗi lệnh tự gọi.

**CHƯA làm / rủi ro còn lại:**
- `_wait_until_render_done()` — vẫn dùng selector CŨ `[data-tile-id]` (UI
  React) để detect "còn tile đang render %". Trên UI mới, selector này sẽ
  không tìm thấy gì → hàm luôn nghĩ "không còn gì render", bỏ qua vòng chờ
  sớm hơn dự kiến — KHÔNG crash, chỉ có thể refresh hơi sớm (vẫn có
  `download_wait_secs` sleep + reconcile retry loop bù lại). CHƯA điều
  tra/sửa selector mới cho hàm này (ngoài phạm vi lần fix này).
- `google.com`/`labs.google` domain-membership check (dòng ~1117
  `_ensure_flow_page()`, xem note "CHƯA sửa" cùng ngày ở trên) VẪN CHƯA
  fix — mỗi task vẫn tốn ~15-20s thừa vì tưởng chưa từng vào `labs.google`.
- Cookie-purge domain scoping (`_BATCH_CLEAN_TARGET_DOMAIN='labs.google'`)
  CHƯA xác minh có cần đổi/mở rộng `flow.google.com` hay không.
- ⚠️ **Client_tool đang chạy (nếu có) CẦN RESTART** để nạp code mới —
  không hot-reload.

---

### 11.46 ✅ GENERATE (upload/ảnh/video) chuyển sang `batchexecute` — đường CHÍNH, `aisandbox` còn là DỰ PHÒNG (2026-09-04)

Nối tiếp §11.45 (mới chỉ chuyển **reconcile**). User hỏi "cập nhật client_tool
api tạo image/video theo luồng mới chưa" → chưa, và điều tra cho thấy đường cũ
**đang hỏng thật trong sản xuất**, không chỉ là "chưa tối ưu".

**Bằng chứng (log profile 25, `worker_mode='api'`, 2026-09-04 00:33-00:34):**
mỗi task phải đi qua 1 vòng `curl_cffi` **403 với CẢ 4 impersonation**
(chrome131/136/124/chrome) → tự hái lại bearer token → mới có 1 lệnh HTTP 200;
lặp lại y hệt cho task kế; khi driver rụng thì fallback `fetch()` trong trang
chết vì CORS (`TypeError: Failed to fetch`), dẫn tới `[batch-escalation] 3 batch
lỗi liên tiếp → ngủ 60s + xoá sạch cookie`. Nghi phạm rõ: `_build_headers()`
vẫn hardcode `origin: https://labs.google` trong khi trang thật giờ là
`flow.google.com` (chưa chứng minh là nguyên nhân 403, nhưng chắc chắn sai).

**Quyết định của user (qua AskUserQuestion):** batchexecute làm CHÍNH,
aisandbox làm DỰ PHÒNG; phạm vi **cả 4 luồng đã verify**.

#### Mắt xích chặn: `maseQ` (upload ảnh) — trước đó mới biết tên từ bundle

`imageToImage`/`ingredientToVideo` đều cần uuid do bước upload trả về. Đã
capture shape thật (`tests/_capture_upload.py` — chạy `_dom_upload_images()`
sản xuất 1 lần rồi chặn network rộng) rồi replay thành công
(`tests/_test_uploadImage_new.py`). Shape + response: xem
`docs/FLOW_BATCHEXECUTE_API.md` §7.0b.

⚠️ **`maseQ` VẪN CẦN reCAPTCHA** (`clientContext[10][0]`) — khác đường
aisandbox `/flow/uploadImage` vốn không cần.

⚠️ **Dùng `payload[0][0]` (DETAIL_UUID), KHÔNG phải `payload[0][2]`.** Xác minh
không tốn quota: upload 1 ảnh rồi tra listing `Zzl0ze` **theo TÊN FILE** —
`[0][0]` khớp `meta[4]`, `[0][2]` khớp `entry[0]` (tile id); và uuid mà capture
`imageToImage` dùng làm `imageInputs` chính là `meta[4]`. Đây đúng là cái bẫy
đã trả giá 1 lần ở §11.45.

#### 2 field sản xuất BẮT BUỘC điều khiển được

- **Tỉ lệ khung hình** — chốt bằng diff 2 capture thật (16:9 vs 9:16): ẢNH
  `[1][0][4]` (16:9=3, 9:16=2), VIDEO `[0][0][2]`/`[0][0][3]` (16:9=2, 9:16=1).
  Ảnh lệch 1 bậc vì có thêm SQUARE/4:3/3:4 — khớp bundle (`_.RPa` chỉ khai
  LANDSCAPE/PORTRAIT cho video). Tra bundle KHÔNG ra số (`_.OI` là enum CHUỖI
  `"LANDSCAPE"`), nên phải diff capture. **1:1/4:3/3:4 cố tình KHÔNG đoán** —
  `flow_be.aspect_enum()` trả `None` → rơi về aisandbox thay vì gửi sai tỉ lệ.
- **Số ảnh/lần** — hoá ra **không tồn tại** trong body, và đường aisandbox cũng
  chưa bao giờ gửi (`build_text_to_image_body()` không có field nào) → giữ
  nguyên hành vi, không cần map.

#### Cài đặt

- **`server/flow_be.py` (MỚI)** — builder body cho `maseQ`/`ogiZ0b`/`YhhmEf`/
  `MZZa6b` + parser. Vai trò song song `tests/utils/flow_api.py` (aisandbox).
  Verify shape: dựng body bằng builder rồi so **cấu trúc** với 4 capture thật →
  4/4 khớp tuyệt đối (cùng số phần tử, cùng kiểu ở mọi đường dẫn index).
- **`worker.py`** — 3 entry point GIỮ NGUYÊN TÊN, chỉ thử BE trước rồi rơi về
  aisandbox: `_upload_media_to_flow()` → `_upload_media_to_flow_be()`;
  `_call_image_api_v2()` → `_call_image_api_be()`; `_call_video_api()` →
  `_call_video_api_be()`. Nhờ vậy **không call site nào khác phải sửa** (kể cả
  đường chạy song song N thread ở §11.35/§11.37).
- **`_be_session()`** — cache TTL 600s. Bắt buộc vì
  `_batchexecute_harvest_session()` gọi `driver.refresh()` (~15s), chạy mỗi
  task là không chấp nhận được trên hot path. Ưu tiên đọc lệnh trang TỰ bắn
  (`window.__beCalls`, interceptor sống qua mọi navigate) trước khi chịu refresh.
- **Backoff** — 3 lần hỏng liên tiếp → nghỉ 300s, đi thẳng aisandbox. Vì mỗi
  lần thử BE hỏng vẫn tốn thời gian thật.
- **Setting `generate_via_batchexecute`** (mặc định `1`, trang Cài đặt) — đặt
  `0` để quay lại dùng thẳng aisandbox mà không cần sửa code.
- **`frameToVideo` CHƯA hỗ trợ** (rpcid `eb1hJf` chưa capture shape) → luôn rơi
  về aisandbox.

#### 2 bug thật bắt được LÚC VERIFY (không phải giả thuyết)

1. **imageToImage trả 2 kết quả cho 1 lần sinh.** `parse_image_results()` quét
   uuid trần nên gom CẢ uuid ảnh THAM CHIẾU (đầu vào) — sản xuất sẽ tải chính
   ảnh ref về rồi lưu như ảnh đã sinh. Fix: `parse_image_results(payload,
   exclude=...)` ưu tiên uuid **có CDN URL trong response**, và loại
   `projectId` + mọi uuid ref (`ref_names` gom lúc upload). Verify lại: đúng 1
   kết quả.
2. **Fallback im lặng.** Lần chạy đầu của `textToVideo` rơi về aisandbox mà
   KHÔNG có dòng log nào (4 nhánh `return None` không log), lần chạy ngay sau
   đó lại chạy được — hoá ra `_batchexecute_harvest_session()` hết 15s. Fix:
   harvest tự **thử lại 1 lần**, và MỌI nhánh bỏ qua BE đều phải log.

#### Verify

- `py_compile`/`pyflakes` sạch: `flow_be.py`, `worker.py`, `config.py`,
  `local_settings.py`, `settings_page.py`.
- So shape builder ↔ 4 capture thật: 4/4 khớp.
- **Chạy THẬT qua ĐÚNG entry point sản xuất** (`tests/_verify_generate_be.py`,
  KHÔNG gọi tắt vào hàm `_be`), trên profile 25 với quota thật:
  `_upload_media_to_flow()`→`maseQ` ✔ · `_call_image_api_v2()` textToImage →
  `ogiZ0b`, 1 CDN URL ✔ · imageToImage → `ogiZ0b`+`imageInputs`, 1 CDN URL ✔
  (sau khi vá bug 1) · `_call_video_api()` componentsToVideo → `MZZa6b`,
  workflowId+mediaId ✔ · textToVideo → `YhhmEf` ✔. Model resolve đúng qua
  `model_catalog` (`veo_3_1_r2v_lite_low_priority`, nguồn `db-derived`).

**CHƯA verify:** chạy qua **hàng đợi heartbeat thật** (mọi test đều dựng task
tay); `frameToVideo`; tỉ lệ 1:1/4:3/3:4.

#### Cập nhật cùng ngày — BỎ chạy song song, `aisandbox` bị CẤT hẳn

Theo yêu cầu user *"cất luôn source aisandbox khi nào cần lấy lại chứ ko chạy 2
loại"*: khi `generate_via_batchexecute=1` (mặc định) thì **chỉ** chạy
batchexecute — hỏng là task lỗi rồi thử lại sau, KHÔNG rơi về aisandbox nữa.
Bỏ luôn backoff-3-lỗi-sang-aisandbox. Toàn bộ máy móc aisandbox (bearer token,
fingerprint, `curl_cffi` impersonate, `flow_api.py`) **giữ nguyên vẹn** trong
code nhưng thành nhánh chết — lấy lại bằng cách đặt setting = 0.

Kèm theo: API mode **không còn chờ token aisandbox lúc khởi động** (trước tốn
tới 90s và hay thất bại vì app Flow mới chẳng gọi aisandbox để mà bắt header),
và 2 chỗ `_sync_project_apis()` giữa chừng cũng chỉ chạy cho nhánh aisandbox.

#### ⚠️ `PUBLIC_ERROR_UNUSUAL_ACTIVITY` — chặn ở tầng TÀI KHOẢN, không phải lỗi code

Log sản xuất 2026-09-04 01:20 cho thấy `MZZa6b` trả về entry `wrb.fr` KHÔNG có
payload nhưng CÓ khối lỗi:

```
["wrb.fr","MZZa6b",null,null,null,
 [7,null,[["…rpc.ErrorInfo",["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]],"generic"]
```

Đúng lỗi mà aisandbox trả (`403 reCAPTCHA evaluation failed`) — **cả 2 đường
cùng bị chặn**, đổi đường vô ích. Đây là chống-lạm-dụng theo nhịp: 00:34 còn
chạy được, 01:20 bị chặn, 01:28 lại chạy bình thường.

2 thứ đã sửa vì nó:
- `_batchexecute_parse()` phân biệt **payload null hợp lệ** (vd `as29s` trên
  media upload thô) với **RPC bị từ chối** — trước đây gộp làm một nên báo
  nhầm "payload rỗng", che mất mã lỗi thật. Nay raise
  `flow_be.BatchExecuteError` kèm `.reason`.
- `_be_note_fail()` nhận diện `is_account_block` và log rõ "⛔ Google TỪ CHỐI ở
  tầng tài khoản" thay vì đổ lỗi cho batchexecute.

**⚠️ `RPC_ERROR_CODE_8` (2026-09-13) = gRPC `RESOURCE_EXHAUSTED` — GIỚI HẠN NHỊP tài
khoản, KHÔNG phải lỗi đường batchexecute.** Log profile 25: mọi `maseQ` (upload ảnh
tham chiếu) trả mã 8. Đã thử ĐƯỜNG CŨ bằng `tests/_test_uploadImage_old.py`
(`aisandbox /v1/flow/uploadImage`, curl_cffi origin labs.google): **HTTP 429
`RESOURCE_EXHAUSTED` / `PUBLIC_ERROR_USER_REQUESTS_THROTTLED`** — cùng tài khoản, cùng
lúc ⇒ đổi đường vô ích, phải giảm nhịp (`thread_stagger_*`) hoặc cho tài khoản nghỉ.
Kèm 2 điều cần nhớ nếu có lúc phải dùng lại aisandbox: (1) labs.google KHÔNG còn
session sẵn — `labs.google/fx/tools/flow` redirect sang flow.google.com, và bước dọn
cookie labs.google mỗi batch (§11.39) xoá luôn `__Secure-next-auth.session-token`;
phải đăng nhập lại qua next-auth (`--signin` của file test). (2) Origin/referer
`flow.google.com` bị API key từ chối (403 `API_KEY_HTTP_REFERRER_BLOCKED`) — chỉ gọi
được với origin `labs.google`.

#### ⚠️ Fix tiếp — 4 chỗ vẫn gọi đường aisandbox, spam `session API: TypeError: Failed to fetch`

Log máy user 08:59 (đã chạy bản mới): lặp lại mỗi giây
`[warn] session API: TypeError: Failed to fetch`. Nguồn:
`_fetch_labs_session()` gọi `/fx/api/auth/session` của **labs.google** — từ
trang `flow.google.com` là CROSS-ORIGIN nên CORS chặn. Nó vẫn chạy vì bản đầu
tôi chỉ chặn 3 call site `_sync_project_apis()`, còn **4 call site
`_ensure_api_ready()`** trên chính đường generate (`_run_task_api`,
`_run_video_tasks_staggered`, `_run_tasks_api_batch`, nhánh video của
`_run_task_api`) thì bỏ sót — chúng gọi `_sync_project_apis()` gián tiếp.

Fix: chặn ở **1 chỗ duy nhất** là đầu `_ensure_api_ready()` (`if
self._be_generate_enabled(): return`) — phủ hết mọi call site kể cả thêm mới
sau này, thay vì vá từng nơi. Nhánh legacy (setting=0) không đổi vì lúc đó
`_be_generate_enabled()` là False.

Chặn luôn `_drain_perf_logs()` cùng cách: nó CHỈ lọc URL
`aisandbox-pa.googleapis.com` nên vô dụng trên đường mới, mà chạy ở 9 nơi và
đọc cả buffer performance log mỗi task.

Verify: mock các hàm con rồi đếm lời gọi — batchexecute: `_ensure_api_ready()`
gọi 0 hàm, `_drain_perf_logs()` đọc log 0 lần; setting=0: giữ nguyên hành vi cũ
(3 hàm / 1 lần đọc).

**Bài học:** khi "cất" một đường đi, chặn ở HÀM CHUNG mà mọi nhánh đều phải
qua, đừng chặn ở từng call site — đếm bằng grep dễ sót nhánh gọi gián tiếp.

#### ⚠️ Bug do CHÍNH lần gate trên — `has_tokens()` chặn nhầm cả đường mới

Ngay sau khi chặn `_ensure_api_ready()`, worker KHÔNG còn hái bearer token của
aisandbox nữa → `has_tokens()` LUÔN False. Mà có **6 cổng chặn trên đường
generate** vẫn hỏi `has_tokens()`:

- 3 chỗ `raise RuntimeError('Chưa có API token cho video generation')` → MỌI
  task video hỏng ngay ở bước chuẩn bị.
- 2 chỗ `can_api = has_tokens() and …` → MỌI task ảnh bị đẩy sang nhánh
  UI-driven (gõ prompt qua DOM) chứ không đi API mới.
- 1 chỗ dựng chuỗi `reason`.

Tức là gate ở trên "đúng" nhưng lại làm tê liệt chính đường mới. Bắt được nhờ
đọc lại vòng lặp stagger khi user hỏi về giãn cách — KHÔNG phải do test.

Fix: thêm `can_generate_via_api()` = `True` nếu đang chạy batchexecute (không
cần bearer), ngược lại `has_tokens()`; thay vào cả 6 cổng. `has_tokens()` chỉ
còn dùng bên trong `_sync_project_apis()`/`_ensure_api_ready()`/log — toàn
nhánh aisandbox, chỉ chạy khi setting=0.

**Bài học (lặp lại lần 2 trong ngày):** cất một đường đi thì phải rà cả những
CỔNG CHẶN suy ra từ trạng thái của đường đó, không chỉ các LỜI GỌI tới nó.

#### Giãn cách: nhánh LỖI trước đây không được áp (user phát hiện qua log)

Log 09:07 của user: 4 task video hỏng liên tiếp cách nhau ~1-3 GIÂY, không hề
có dòng `⏳ Chờ …s`. Nguyên nhân: lệnh chờ nằm SAU `pool.submit(...)`, mà task
hỏng ở bước chuẩn bị thì `continue` nhảy thẳng qua — cả lô hỏng chạy hết trong
vài giây, spam `/task/error`, đốt `retry_count` và kích escalation gần như tức
thì.

Sửa: dùng cờ `prepared` thay `continue`, để đoạn chờ chạy cho CẢ 2 nhánh.
Verify (stagger 0.4s, 4 task): thành công 1.22s / 3 lần chờ, hỏng hết 1.21s /
3 lần chờ — trước fix nhánh hỏng ~0s.

Kèm theo, cả 2 vòng (ảnh + video) đổi THỨ TỰ: kiểm tra điều kiện RẺ
(`can_generate_via_api()` + project id) TRƯỚC, mint reCAPTCHA SAU. Mint là 1
lời gọi THẬT tới Google — trước đây task chắc chắn hỏng vẫn đốt 1 token. Verify:
5 task không đủ điều kiện → mint 0 lần (trước: 5 lần).

**Giãn cách CỐ Ý không đổi** (`thread_stagger_min/max_secs` 10-15s,
`task_delay_secs` 10s, `POLL_INTERVAL` 8s). Nhưng nhịp THỰC TẾ nhanh hơn chút
vì đã bỏ `_ensure_api_ready()`/`_drain_perf_logs()` khỏi hot path — 2 hàm đó
vốn tốn thời gian ngoài ý muốn. Nếu lại dính `PUBLIC_ERROR_UNUSUAL_ACTIVITY`
thì nâng `thread_stagger_*` là chỗ chỉnh đúng.

#### ⚠️ RÀNG BUỘC: phải THẬT SỰ ở trang project mới được generate (2026-09-04)

User báo: *"lâu lâu bị xoá cookie và bị đá ra ngoài trang có nút create — đáng
lẽ phải click nút này để về trang project chạy task tiếp, nhưng lại chạy task
ngay tại đây gây lỗi"*.

Đúng, và có 2 lỗ hổng cộng lại:

1. **Nhánh API mode KHÔNG hề kiểm tra trang.** Cả 2 lời gọi
   `_ensure_flow_page()` đều nằm ở nhánh DOM (`_run_tasks_batch`,
   `_run_task_dom`); `_run_tasks_api_batch()` — đường đang chạy sản xuất —
   không có bước nào.
2. **`_extract_project_id()` đọc project id từ DB (`profile['project_url']`),
   KHÔNG phải từ URL đang mở** → luôn trả id hợp lệ kể cả khi trình duyệt đang
   ở màn hình khác ⇒ không có gì chặn.

⚠️ **Kiểm tra URL là KHÔNG ĐỦ** — màn hình xen giữa "Create with Google Flow"
GIỮ NGUYÊN `/project/{uuid}` trên thanh địa chỉ (chính lý do
`_click_create_with_flow_if_present()` tồn tại). Phải soi UI thật.

Fix:
- `_project_page_ready()` — URL có `/project/` **VÀ** có UI làm việc thật
  (`.ProseMirror[contenteditable]` hoặc `button[aria-label="Settings trigger"]`).
- `_ensure_project_page_ready(attempts=3)` — chưa sẵn sàng thì BẤM nút create
  (màn hình xen giữa không đổi URL nên không thể dựa vào điều hướng), rồi mới
  tới đường phục hồi đầy đủ `_ensure_flow_page()`.
- `_run_tasks_api_batch()` gọi nó ĐẦU TIÊN; không vào được thì **không chạy lô
  nào**, báo lỗi từng task (server giao lại) rồi để thang escalation quyết định.
- `_recover_flow_project_page_after_cache_clear()` chốt thêm bằng kiểm tra UI —
  2 nhánh cũ ở đó chỉ soi URL nên bỏ lọt đúng ca kẹt màn hình xen giữa.

Verify: ma trận 4 tình huống (đang ở project / URL đúng nhưng kẹt màn hình
create / bị đá ra trang chung / kẹt hẳn) → True/True/True/False; và
`_run_tasks_api_batch` khi trang chưa sẵn sàng → 0 lần generate, báo lỗi đủ
task, không escalate oan.

⚠️ **client_tool đang chạy CẦN RESTART** để nạp `flow_be.py` + setting mới.

---

### 11.47 🌐 Proxy RIÊNG cho từng profile — `--proxy-server` + extension MV3 cho auth (2026-09-05)

Yêu cầu user: "cho phép nhập proxy trên UI để tất cả profile đi qua proxy đó" → đổi ý ngay sau đó
"proxy setting cho từng profile". Ô **Proxy** trong `ProfileDialog` hiện với MỌI loại profile
(Chrome nào cũng đi qua proxy được nên KHÔNG nằm trong `_type_fields`), lưu ở cột mới
`selenium_profiles.proxy_server` (migration `_ensure_worker_profile_proxy()`, backend `ToolSub`).
Để trống = đi thẳng như trước.

**`server/proxy_config.py` (MỚI)** — `parse_proxy()` (nhận `host:port` · `scheme://host:port` ·
`user:pass@host:port` · `scheme://user:pass@host:port` · `host:port:user:pass`) +
`ensure_proxy_auth_extension()` + `build_proxy_setup(proxy, profile_id)`.

⚠️ **Điểm áp dụng DUY NHẤT: `chrome_utils._build_chrome_options(proxy=, profile_id=)`.** Mọi đường
mở Chrome đều qua đó (worker uc lẫn thường, "Mở login browser" ở `routes.py`, harness
`tests/utils/profile_target.py`) — thêm đường mở Chrome mới sau này nhớ truyền 2 tham số này,
đừng vá ở call site.

⚠️ **Auth user/pass — KHÔNG nhúng được vào `--proxy-server`** (Chrome bỏ qua IM LẶNG phần
credentials). Cách duy nhất còn dùng được trên Chrome hiện đại: extension **MV3** bắt
`chrome.webRequest.onAuthRequired` với quyền **`webRequestAuthProvider`** — quyền Chrome thêm vào
MV3 ĐÚNG cho use case này (thay `webRequestBlocking` của MV2, giờ chỉ còn cho extension cài bằng
policy; MV2 đã bị gỡ hẳn từ Chrome 139 nên mọi hướng dẫn proxy-auth MV2 trên mạng đã CHẾT).
Extension tự sinh vào `client_tool/data/proxy_auth_ext/profile_{id}/` — **thư mục RIÊNG theo
profile**, dùng chung 1 thư mục sẽ khiến profile mở sau ghi đè creds của profile đang chạy. Nạp
qua `--load-extension` **kể cả khi `load_extensions=False`** (cờ đó chỉ để tắt extension Flow cho
worker_mode gemini/chatgpt, không liên quan proxy). Extension CHỈ trả credentials khi
`details.isProxy` — trang web tự hỏi mật khẩu thì để nguyên, không gửi mật khẩu proxy cho site lạ.

⚠️ **SOCKS4/5 không xác thực được** ở bất kỳ dạng nào trên Chrome (kể cả extension) — proxy SOCKS
phải whitelist IP; có creds + SOCKS thì log warning rõ ràng thay vì im lặng chạy sai.

⚠️ **PHẠM VI: chỉ traffic TRÌNH DUYỆT.** Lệnh HTTP do chính Python bắn (heartbeat, tải ref ảnh từ
backend, nhánh dự phòng `aisandbox` qua `curl_cffi`) VẪN đi thẳng — chủ ý. Đường generate CHÍNH
hiện tại (`batchexecute`) chạy bằng `fetch()` TRONG TRANG nên vẫn được proxy che (§11.46). Kèm
`--proxy-bypass-list=localhost;127.0.0.1;[::1]`.

⚠️ Proxy hỏng cấu hình (scheme lạ/port sai/thiếu host) → log lý do rồi BỎ QUA, Chrome vẫn mở bình
thường. ⚠️ Đổi proxy chỉ có hiệu lực ở lần MỞ Chrome KẾ TIẾP (cờ dòng lệnh chỉ đọc lúc launch) —
phải Stop rồi Start lại profile. ⚠️ Lưu PLAINTEXT, cùng rủi ro với `account_password` (§11.31).

`ProfilesPage` hiện dòng thứ 3 `🌐 scheme://user:******@host:port` dùng CHÍNH `parse_proxy()` để
che — KHÔNG tự cắt theo `@`: dạng `host:port:user:pass` không có `@` nào, cắt tay sẽ hiện nguyên
mật khẩu proxy lên màn hình.

**Verify:** `parse_proxy()` 16 định dạng; `build_proxy_setup()` 4 nhánh + 2 profile ra 2 thư mục
ext riêng đúng creds; `_build_chrome_options()` 6 tình huống (gồm `load_extensions=False` vẫn nạp
ext proxy-auth mà không nạp ext Flow, và `uc.ChromeOptions`); GUI headless thật (prefill/get_data,
bảng che đúng mật khẩu); backend end-to-end qua Flask test client + **DB THẬT** (create/GET/PATCH/
clear/LIST/DELETE, dọn sạch). **CHƯA verify trên Chrome + proxy THẬT** — cần user nhập 1 proxy
thật, Start profile rồi xác nhận IP đã đổi.

**⚠️ FIX (2026-09-06) — "chatgpt tạo ảnh đang lỗi" hoá ra là PROFILE BỊ ĐĂNG XUẤT, không phải
DOM đổi.** Log chỉ nói `Không tìm thấy ô nhập liệu ChatGPT (#prompt-textarea)` nên dẫn thẳng
sang hướng "OpenAI đổi DOM" — sai. Bằng chứng thật: cookie `chatgpt.com` chỉ còn loại ẩn
danh/CDN, **mất `__Secure-next-auth.session-token`**; DOM trả về bản chưa-đăng-nhập
(`#mobile-composer-prompt`, class `wm-*`, nút "Create image. Log in to use."). **Lần thứ 2
dính đúng bẫy này** (lần đầu 2026-08-01, cùng bằng chứng "Log in to get answers…").
Đã thêm `_chatgpt_logged_out_reason()`/`_chatgpt_assert_logged_in()` — gọi ở đầu
`_run_task_chatgpt()` (chặn SỚM, trước khi tốn công tải/đính kèm ảnh) và lúc khởi động
`_run_chatgpt_loop()` (trước đây LUÔN log "sẵn sàng nhận prompt" kể cả khi đã đăng xuất).
KHÔNG tự đăng nhập hộ (OpenAI có captcha/2FA riêng) — chỉ báo rõ để user tự đăng nhập vào
ĐÚNG profile Chrome này. Chẩn đoán DOM: `tests/_diag_chatgpt_dom.py` (attach vào login
browser, không gửi tin nhắn nào, dump trạng thái đăng nhập + tình trạng từng selector).
⚠️ CHƯA verify DOM khi ĐÃ đăng nhập — cookie `oai-mweb-route-desktop` cho thấy OpenAI đang
route sang bản **mweb**, nên `#prompt-textarea`/`[data-testid="send-button"]`/
`[data-testid^="conversation-turn-"]` VẪN CÓ THỂ cần cập nhật; đăng nhập xong hãy chạy lại
script chẩn đoán rồi mới kết luận.

---

### 11.48 ✨ Gemini chat — GỬI ẨN qua RPC `StreamGenerate`, đọc kết quả vẫn bằng DOM (2026-09-06)

Yêu cầu user: *"vào trang gemini đọc network để truyền ẩn như VEO qua API khi mở giao diện
https://gemini.google.com/"* (KHÔNG dùng API key chính thức — mở trang bằng profile đã đăng
nhập, lấy endpoint nội bộ + token rồi bắn thẳng request), làm rõ tiếp: *"api chỉ cần gửi nội
dung còn lắng nghe đọc kết quả giống DOM"*.

**Phạm vi rất hẹp và cố ý:** CHỈ thay bước **GỬI**. Bước đọc kết quả giữ nguyên
`_gemini_response_if_ready()`/`_gemini_wait_response()` — KHÔNG sửa 1 dòng nào của DOM reader
(kể cả phần bắt ảnh base64 §11.25 và cơ chế `stability_state` §11.43).

**Vì sao đáng làm:** gõ prompt ~29K ký tự (cỡ prompt `scene_batch`/`full_script` thật) vào
Quill/ProseMirror là nguồn bug dai dẳng nhất của luồng Gemini — xem root `CLAUDE.md` §11.5
(`insertText` không áp hết → phải chunk → chunk lại làm Quill re-render chậm dần → rút lại) và
§11.38 (gõ trúng thẻ `<p>` không focus được). Bắn RPC thì prompt chỉ là 1 chuỗi trong body.

#### Endpoint + shape (capture THẬT, không suy từ bundle)

    POST /_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate
         ?bl=<boq>&f.sid=<sid>&hl=en&_reqid=<rand>&rt=c
    content-type: application/x-www-form-urlencoded;charset=utf-8
    body: f.req=<url-encoded [null,"<inner 99 ô>"]>&at=<xsrf>&

| ô | ý nghĩa |
|---|---|
| `inner[0]` | `[prompt, 0, null×4, 0]` |
| `inner[1]` | `["en"]` |
| `inner[2]` | `[c_id, r_id, rc_id, null×6, ""]` — chat MỚI thì 3 ô đầu `""` |
| `inner[3]` | token BotGuard — **gửi `null` là được**, xem dưới |
| `inner[4]` | id 32-hex, mới mỗi request |

94 ô còn lại = cờ tính năng của app → giữ NGUYÊN từ template
(`gemini_be.STREAM_GENERATE_TEMPLATE`). Response: chuỗi chunk length-prefixed
`[["wrb.fr",null,"<json string>"]]`, mỗi chunk là 1 bản cập nhật TĂNG DẦN — lấy bản dài nhất.

#### 3 phát hiện quyết định (verify trên tài khoản THẬT)

1. ⚠️ **KHÔNG cần token BotGuard.** `window.botguard` CÓ trong trang, blob `!…` ~1.6KB chỉ
   xuất hiện trong chính request gửi (không có ở bất kỳ request nào lúc load) — nhưng gửi
   `inner[3]=null` vẫn **HTTP 200 + hội thoại thật + trả lời đầy đủ**. **KHÁC Flow**
   (`batchexecute` VẪN bắt buộc reCAPTCHA ở `clientContext[10][0]`, §11.46) — đừng suy sang.
2. **Token đọc THẲNG `window.WIZ_global_data`** (`SNlM0e`=at, `cfb2h`=bl, `FdrFJe`=f.sid) —
   KHÔNG phải chờ trang tự bắn 1 lệnh rồi trích từ URL như `_batchexecute_harvest_session()`
   của Flow, nên **không tốn `driver.refresh()` ~15s** nào.
3. ⚠️ **URL hội thoại BỎ tiền tố `c_`** — `/app/c_<id>` ra trang TRỐNG (Angular không nhận
   route: 0 `structured-content-container`, DOM reader timeout 120s); `/app/<id>` mới render.
   Thanh địa chỉ trông "hợp lệ" ở cả 2 nên rất dễ mất thời gian — dùng
   `gemini_be.conversation_url()`, đừng tự ghép chuỗi.

#### Kiến trúc — kickoff+poll, KHÔNG BLOCKING

⚠️ `fetch` StreamGenerate chỉ resolve khi Gemini sinh XONG (vài chục giây). Dùng
`execute_async_script` sẽ **đóng băng cả vòng round-robin nhiều tab** (§11.24). Nên:

- `_gemini_rpc_kickoff(prompt, key)` — bắn rồi return NGAY, kết quả ghi `window.__gmRpc[key]`.
- `_gemini_rpc_poll(key)` — 1 lần đọc, `None` = còn chạy.
- `_gemini_send_via_rpc()` — wrapper BLOCKING (chỉ cho luồng 1-tab tuần tự).

Cùng pattern `_gemini_attach_file_kickoff`/`_gemini_attach_file_poll` sẵn có.

**Nối vào 2 luồng:** `_run_task_gemini()` (1-tab) · `_gemini_slot_step()` (round-robin — state
mới `'rpc_sending'` nhường lượt cho tab khác đúng nhịp `gemini_tab_switch_interval`).

#### ⚠️ Có FILE ĐÍNH KÈM → LUÔN dùng DOM

Upload của Gemini đi qua endpoint resumable riêng (`push.clients6.google.com/upload/…`) — lần
capture này **CHƯA lấy shape**, đoán bừa là cách nhanh nhất để hỏng.
`_gemini_rpc_enabled(has_attachments=True)` trả `False` → rơi về `_gemini_attach_file()`
(`send_keys` native, đã proven). Ảnh hưởng: `video_breakdown` + storyboard có ảnh vẫn chạy DOM.

**Hỏng ở BẤT KỲ bước nào cũng tự rơi về DOM trong CÙNG task** — không bao giờ mất task.

#### Setting

`gemini_send_via_rpc` (mặc định **1**, trang Cài đặt). Đặt `0` = gõ DOM hoàn toàn như cũ.

#### Công cụ

- `tests/_gemini_rpc_capture.py` — **GIỮ LẠI trong repo**: chạy đúng luồng DOM sản xuất 1 lần
  rồi chặn network, xuất `tests/_gemini_rpc_capture/03_send_requests.json`. Google đổi build
  (`bl=boq_assistant-bard-web-server_…`) → chạy lại rồi thay `STREAM_GENERATE_TEMPLATE`.
  Token/cookie trong file xuất ra được CHE mặc định (`--no-redact` để tắt).
- `tests/_test_gemini_rpc_send.py` — verify end-to-end qua ĐÚNG hàm production, prompt 29K.

#### Verify

**6/6 PASS trên tài khoản thật, prompt 29.096 ký tự:** gửi ẩn + mở hội thoại **9.9s**;
`_gemini_wait_response()` đọc **3.6s**; Gemini trả lời đúng câu hỏi đặt ở **CUỐI** prompt
(xác nhận đọc hết chuỗi). Gate đính-kèm→DOM đúng cả 2 chiều. `parse_stream_generate()` verify
offline trên response thật (biết trước đáp án). `py_compile`/`pyflakes` sạch.

#### Bẫy đã dính (ghi lại để khỏi mất thời gian lần sau)

1. **Chrome "sập" đều đặn ~40-50s** ở đủ mọi bước khác nhau → tưởng crash do code. Thực ra
   **client_tool đang chạy** tự đóng Chrome của profile `enabled=1`. Điều tra thì dùng profile
   `enabled=0`.
2. **Probe báo "không có câu trả lời" oan** — regex của chính probe sai (response dùng nháy
   escape `\"`). Đừng regex trên chunk thô, parse đúng 2 lớp JSON.
3. **DOM reader bỏ qua câu trả lời NGẮN** — `_gemini_response_if_ready()` đòi `len(text) > 5`
   nên prompt "chỉ trả lời 1 từ" luôn timeout dù Gemini đã trả lời xong.

#### CHƯA làm

- Endpoint upload (đính kèm vẫn DOM). · Chưa chạy qua **hàng đợi heartbeat thật** end-to-end.
- Chưa test nhánh round-robin `'rpc_sending'` trên browser thật (mới verify nhánh 1-tab).

⚠️ **client_tool đang chạy CẦN RESTART** để nạp `gemini_be.py` + setting mới.
---
### 11.49 ♻️ FIX THẬT: cùng project vẫn upload lại ảnh tham chiếu nhiều lần — cache đổi khoá từ `task_id` sang `(project, URL ảnh gốc)` (2026-09-05)

User báo: *"task image tham chiếu và check id project có khớp với project hiện tại không để tận dụng
không upload ảnh tham chiếu mới giờ đã lỗi khi flow.google upload phiên bản mới... cùng project nhưng
upload lại ảnh tham chiếu nhiều lần"*.

⚠️ **KHÔNG phải do flow.google đổi phiên bản.** Đã kiểm chứng: `_extract_project_id()` vẫn trích đúng
uuid trên URL mới `https://flow.google.com/project/{uuid}` (test trên `project_url` THẬT của 6 profile
trong DB); số call site cache không đổi qua mọi commit của đợt viết lại batchexecute (`git show` từng
commit: luôn 4); đường upload mới `maseQ` vẫn trả `media.name` bình thường. Root cause nằm ở **CÁCH ĐẶT
KHOÁ ngay từ bản v1 (2026-08-13)** — chỉ là trước đây ít lộ:

1. **Khoá theo `task_id` ⇒ không task nào dùng lại được của task nào.** Trong NanoBananaPro, CÙNG 1 ảnh
   CHAR/BG/PROP được hàng chục scene tham chiếu — mỗi scene là 1 task khác nhau nên mỗi task upload 1 bản
   RIÊNG của cùng tấm ảnh vào CÙNG project. Bằng chứng trên máy user: `uploaded_media_cache.json` có
   **196 entry ⇒ 196 `media.name` khác nhau, 0 lần dùng lại**.
2. **Task ẢNH chưa từng có cache.** v1 chỉ được gọi trong `_prepare_video_uploads()`; 3 vòng upload còn
   lại (`_call_image_api_be` — đường CHÍNH hiện tại, `_call_image_api_v2`, nhánh frameToVideo) upload
   thẳng, không hỏi cache lần nào.

Hệ quả kép: tốn băng thông/thời gian mỗi task, VÀ project bị bơm đầy ảnh trùng ⇒ chạm trần
`max_project_media_items` (300, §11.20e) sớm hơn nhiều so với lượng media THẬT ⇒ tự luân chuyển project oan.

**Fix — `_upload_source_media_cached()` (`worker.py`) là ĐIỂM DUY NHẤT được phép upload ảnh tham chiếu.**
Cả 4 vòng cũ giờ gọi vào đây (`_download_source_media()` chỉ còn đúng 1 caller), nên cache áp dụng đồng
nhất, không nhánh nào bỏ sót — thêm đường generate mới sau này cứ gọi helper này, đừng tự lặp lại vòng
tải+upload. `media_upload_cache.py` lên **v2**: khoá `(project_id, URL tuyệt đối của ảnh gốc)`, thêm TTL
7 ngày, bỏ hàm xoá-theo-task.

⚠️ **Vì sao bỏ `clear_cached_media_names(task_id)`** (v1 gọi khi task xong hẳn): với khoá mới, xoá theo
task là XOÁ MẤT bản upload mà các task khác đang dùng chung. Entry sống tới khi hết TTL / đổi project /
bị đẩy ra do trần `_MAX_ENTRIES`.

⚠️ **Vẫn giữ nguyên bảo đảm an toàn của v1:** khác `project_id` là cache miss, upload lại bình thường —
không bao giờ dùng `media.name` của project khác. Không có `project_id` (profile chưa có `project_url`
hợp lệ) → bỏ qua cache, upload thẳng như trước, KHÔNG chặn task.

⚠️ **BẪY TỰ TẠO RA rồi tự bắt lúc verify:** bản nháp dựng `image_inputs` từ `set(names)` cho gọn —
`set` KHÔNG có thứ tự ⇒ ref ảnh đảo lộn ngẫu nhiên giữa các lần chạy. `ref_names` (set) CHỈ được dùng
cho `exclude` ở `parse_image_results()`; `image_inputs` phải dựng từ LIST theo đúng thứ tự `source_media`.

⚠️ Đổi hành vi nhỏ ở `frameToVideo`: giờ cắt `source_media[:1]` TRƯỚC khi tải (không tải thừa ảnh cuối).
Bản cũ tải hết rồi lấy `downloaded[0]` — nếu ảnh ĐẦU tải lỗi thì âm thầm lấy ảnh CUỐI làm khung hình bắt
đầu (sai); giờ báo lỗi rõ ràng.

**Verify:** 8 kịch bản qua code THẬT (`_upload_source_media_cached`, chỉ mock tầng tải/upload + cache file
tạm): 3 scene cùng project dùng chung CHAR+BG → **9 upload/9 download còn 5/5**; retry cùng task → 0
upload mới; **thứ tự ref giữ nguyên** (đảo input thì output đảo theo); đổi project → upload lại, không
dùng name project cũ; không có project_id → vẫn chạy, không crash; TTL hết hạn → tự upload lại; ảnh tải
lỗi → bỏ qua item đó, không hỏng cả lô; file cache v1 cũ → bỏ qua an toàn (log rõ), không crash.
`py_compile`/`pyflakes` sạch. **CHƯA verify trên Flow/quota THẬT** — nhưng việc dùng lại `media.name`
trong cùng project không phải cơ chế mới: v1 đã làm đúng vậy khi retry cùng task và chạy ổn định trong
sản xuất từ 2026-08-13 (196 entry) — v2 chỉ mở rộng phạm vi dùng chung từ 1 task sang mọi task cùng project.

---

### 11.50 🖼️ `worker_mode='gemini_image'` — engine THỨ 2 cho task ẢNH, mirror `gemini_video` (2026-09-09/10)

Theo yêu cầu user *"tích hợp vào project banana và quiz có thể theo kiểu multi check, auto check
VEO khi tạo project và nếu check Gemini thì chạy song song cả 2, gemini thì tách ra check
image/video"* (Quiz để sau — user chọn "Chỉ áp dụng cho project Banana (NanoBananaPro)"; checkbox
cấp PROJECT, không phải per-task; "chạy song song cả 2" = VEO và Gemini đều là "machine" cạnh
tranh CÙNG hàng đợi `tasks_media_flow` qua `FOR UPDATE SKIP LOCKED` sẵn có — task đã bị 1 engine
nhận thì engine khác bỏ qua, KHÔNG generate trùng).

**Backend (`ToolSub` gốc, xem CHANGELOG.md ở đó để biết chi tiết):** `projects.enable_veo`
(mặc định BẬT — auto-check khi tạo project)/`enable_gemini_image`/`enable_gemini_video` (mặc định
TẮT) — 3 cờ độc lập gate MÁY NÀO được `heartbeat.py` giao task của 1 project. `heartbeat.py`'s
`engine_clause` (mới, đứng cạnh `mode_clause` sẵn có): `machine_type='gemini_image_selenium'` →
`p.enable_gemini_image=1` + `t.mode IN ('textToImage','imageToImage')`; `gemini_video_selenium`
→ `p.enable_gemini_video=1` + `t.mode IN ('textToVideo','imageToVideo','componentsToVideo',
'frameToVideo')`; máy VEO3 thường (`selenium_profile`) → `p.enable_veo=1` (hoặc project_id NULL,
backward-compat với task không gắn project). `backend/routes/projects.py`'s create/update/list
đọc-ghi-trả 3 cờ này; `backend/routes/worker_profiles.py::_VALID_WORKER_MODES` thêm `'gemini_image'`.

**client_tool — `_run_task_gemini_image()`/`_upload_image_result()`** (`server/worker.py`, đặt
NGAY SAU `_run_task_gemini_video()`/`_upload_video_result()`, mirror 1:1 shape — xem §11.27 để
biết cấu trúc gốc) — xử lý task `textToImage`/`imageToImage` bằng Gemini chat THƯỜNG, KHÔNG dùng
`_gemini_video_enter_mode()`/`_gemini_video_set_ratio()` (plain chat KHÔNG có ratio selector, đã
verify qua `tests/_test_geminiImageToImage.py` ở phiên trước — Gemini trả ảnh qua
`_gemini_wait_response()`'s field `images`, canvas-based extraction đã proven cho cả Gemini lẫn
ChatGPT, xem §11.25). Luồng: `/task/processing` → **luôn bắt đầu chat MỚI** (tránh lẫn kết quả
giữa các task) → `_ensure_google_login(GEMINI_URL)` → tải `source_media` (nếu có — path TƯƠNG ĐỐI
server trả về, PHẢI tự ghép `FLOW_SERVER` trước khi `req_lib.get()`, cùng bug/fix đã áp dụng cho
`_run_task_gemini_video()` ở §11.27, xem "FIX THẬT (2026-08-07)... Invalid URL... No scheme
supplied") → `_gemini_attach_file()` từng ảnh (tuần tự) → gõ+gửi prompt
(`_gemini_fill_and_submit()`) → `_gemini_wait_response()` → decode base64 từng ảnh trong
`result['images']` (phát hiện PNG/JPEG qua header byte) → `_upload_image_result()` (multipart
`POST /api/media/task/<id>/upload_result`, field `files` — endpoint CÓ SẴN, `request.files.
getlist('files')` đã hỗ trợ NHIỀU FILE 1 LẦN GỌI từ trước, khác `_upload_video_result()` chỉ có
1 file) → `_handle_task_success()`. Không có ảnh nào trả về (Gemini từ chối/hỏi lại) → raise lỗi
rõ ràng, KHÔNG tính là thành công dù có text phản hồi. `tmp_paths` (list, không phải 1 path đơn
như video) tự dọn trong `finally`.

**Wiring còn lại (mirror đúng pattern `gemini_video` ở mọi điểm — xem §11.27):**
- `__init__`'s worker_mode validation + `_make_driver()`'s `load_ext` exclusion — đã thêm
  `'gemini_image'` (cùng nhóm `gemini`/`chatgpt`/`gemini_video`, không cần extension Flow).
- `_run_task()` dispatcher — `elif worker_mode=='gemini_image': return self._run_task_gemini_
  image(task)`.
- `_heartbeat()`'s `machineType` — đổi từ hardcode `'selenium_profile'` sang lookup theo
  `worker_mode` (`{'gemini_video':'gemini_video_selenium','gemini_image':'gemini_image_
  selenium'}.get(self.worker_mode, 'selenium_profile')`) — TRƯỚC ĐÂY chỉ đúng cho veo3/dom/api,
  gemini_video CŨNG BỊ ẢNH HƯỞNG bug này cho tới lần sửa này (đã gửi sai machineType từ lúc ra
  đời §11.27 — may mắn không gây hại vì `heartbeat.py`'s validate list đã có sẵn cả 2 type, chỉ
  là `engine_clause`/gating theo cờ project sẽ KHÔNG áp dụng đúng cho `gemini_video` cho tới khi
  sửa cùng lượt này).
- `run()`'s nhánh setup — `is_gemini_video or is_gemini_image` → navigate thẳng `GEMINI_URL` qua
  `_ensure_google_login()`, bỏ qua `_ensure_flow_page()`/token capture (2 mode này không dùng
  Flow project).
- `_handle_task_error()`'s nhánh "reset project" (tạo project Flow mới sau N lần refresh lỗi) —
  bỏ qua cho CẢ 2 mode (`worker_mode in ('gemini_video', 'gemini_image')`), không chỉ
  `gemini_video` như trước — mỗi task đã tự vào chat mới nên "reset" vô nghĩa với cả 2.

**`server/dispatcher.py`:**
- `_VEO3_LIKE_WORKER_MODES` thêm `'gemini_image'` — profile này LUÔN `task_mode='image_only'`
  (ép ở `ProfileDialog.get_data()`, không có UI chọn khác vì engine chỉ xử lý ảnh) nên tự nhiên
  rơi đúng vào ngân sách `image_budget` của `_veo3_dispatcher_tick()` (§11.20c) — cạnh tranh
  CÙNG pool ảnh với VEO3 `task_mode='image_only'`, KHÔNG cần sửa gì thêm ở logic cấp ngân sách
  (hàm đó thuần theo `task_mode`, không quan tâm `worker_mode`).
- Hằng số mới `_NO_PROJECT_WORKER_MODES = ('gemini_video', 'gemini_image')` — thay 2 chỗ check
  `!= 'gemini_video'` lặp lại (`_start_worker()`, `_auto_scale_veo3_tick()`) — cả 2 mode không
  bắt buộc `project_url`.

**`server/routes.py`'s `start_worker()`** — cùng đổi `!= 'gemini_video'` →
`not in ('gemini_video', 'gemini_image')`.

**`server/managers.py::pm.create()`** — thêm `'gemini_image'` vào whitelist `worker_mode` NGAY
TỪ ĐẦU khi viết engine này — whitelist ở đây là RIÊNG với `backend/routes/worker_profiles.py`
(KHÔNG dùng chung), quên thêm ở đây khiến profile mới tạo qua GUI bị ÂM THẦM reset về `'api'`
TRƯỚC KHI gửi lên backend — đúng bug thật đã xảy ra với `gemini_video` (xem §11.30), chủ động
tránh lặp lại lần này.

**GUI:** `ProfileDialog` combo "Loại" thêm option "🖼️ Gemini — Tạo Ảnh (mới, thay VEO3)"
(`gemini_image`) — mirror `gemini_video` ở cả 4 chỗ (`_type_fields` tái dùng NGUYÊN 2 field
timeout `gemini_attach_to`/`gemini_resp_to` + `_max_concurrent` của nhóm veo3, không có field
mới nào; `_accept()` validate; `get_data()` force `task_mode='image_only'`, `project_url=''`).
`ProfilesPage` — badge riêng "🖼️ Gemini Ảnh" (`_ENGINE_BADGE`, màu `'purple'` — cùng màu
`gemini_video` vì cùng nhóm Gemini, phân biệt qua icon/text), sub-text cột Mode "Tạo ảnh qua
chat", `can_start` không đòi `project_url`.

**Verify:**
- `py_compile`/`pyflakes` sạch toàn bộ file sửa (backend + client_tool, không có warning mới
  nào ngoài các warning PRE-EXISTING đã biết ở `migrations.py`/`projects.py`/`worker_profiles.py`).
- Backend dispatch gating — test THẬT qua `app.test_client()` + **DB THẬT** (không mock): tạo 1
  project với `enable_veo=0, enable_gemini_image=1, enable_gemini_video=0` + 1 task
  `textToImage` + 1 task `imageToVideo` cùng project → heartbeat `gemini_image_selenium` chỉ
  nhận đúng task ảnh (task video bị loại đúng); bật thêm `enable_gemini_video=1` → heartbeat
  `gemini_video_selenium` nhận đúng task video (task ảnh bị loại đúng); tắt lại
  `enable_gemini_image=0` → task ảnh không còn được giao cho `gemini_image_selenium` nữa. Cả 3
  kịch bản PASS, dữ liệu test đã xoá sạch (`DELETE` project + 2 task ngay sau).
- `_run_task_gemini_image()`/`_upload_image_result()` — test mock-based
  (`tests/_test_gemini_image_task.py`, mirror pattern `tests/_test_batch_escalation.py`: 1
  instance `SeleniumFlowWorker` THẬT qua `object.__new__`, chỉ stub Chrome/HTTP, KHÔNG mock
  chính logic điều khiển cần verify) — 5 kịch bản: (1) thành công không có ref ảnh — 1 file
  upload đúng; (2) thành công CÓ ref ảnh (path tương đối) — xác nhận `FLOW_SERVER` được ghép
  đúng trước khi GET, 2 ảnh Gemini trả về upload đủ cả 2, file tạm ref ảnh tự dọn sau khi task
  xong (không sót trên đĩa); (3) Gemini không trả ảnh nào (chỉ text) — lỗi đúng message, KHÔNG
  gọi `_handle_task_success()`; (4) `_upload_image_result()` lỗi (network/backend) — lỗi lan
  đúng message, không crash; (5) `_ensure_google_login()` trả `False` — dừng sớm, KHÔNG chạm
  tới Gemini (không gõ prompt). Cả 5 PASS.

**CHƯA verify:**
- Trên browser/quota THẬT (môi trường phát triển không chạy được Chrome/Selenium) — cần user
  tự tạo 1 profile "🖼️ Gemini — Tạo Ảnh", gán chạy trên 1 project NanoBananaPro có
  `enable_gemini_image=1`, Start, xác nhận nhận đúng task ảnh thật và ảnh generate hợp lệ.
- Round-trip đầy đủ qua hàng đợi heartbeat thật (test chỉ gọi trực tiếp `_run_task_gemini_
  image()`, chưa qua `_process_tasks()`/dispatcher đầy đủ như mọi lần thêm worker_mode mới
  trước đây — cùng giới hạn đã ghi nhận cho `gemini_video` ở §11.27).
- **NanoBananaPro frontend** (`ProjectManager.jsx`'s `ProjectModal`) — 3 checkbox VEO/
  Gemini-Ảnh/Gemini-Video wired vào `enable_veo`/`enable_gemini_image`/`enable_gemini_video` —
  CHƯA làm. Backend CRUD (create/update/list) đã sẵn sàng nhận/trả field này qua API, nhưng
  CHƯA có UI nào gọi tới — user hiện chỉ chỉnh được 3 cờ này qua API trực tiếp (curl/Postman)
  hoặc sửa DB tay, cho tới khi frontend được nối.
- `gui/pages/profiles_page.py` — badge cột Mode CHƯA verify hiển thị thật trên GUI (chỉ verify
  qua code review + so khớp logic với `gemini_video` đã proven, không launch app đầy đủ).
- **Backend đang chạy live (nếu có) CẦN RESTART** để nạp cột `projects.enable_*` mới/
  `heartbeat.py`'s `engine_clause`/`worker_profiles.py`'s whitelist mới — cùng lưu ý "cần
  restart server" đã lặp lại nhiều lần cho các thay đổi route/DB khác trong tài liệu này.

### 11.50b ⚠️ FIX THẬT: "bật tài khoản gemini tạo image/video hiện profile cái tắt liền" (2026-09-12)

User báo *"check lỗi gì bật tài khoản gemini tạo image/video hiện profile cái tắt liền"* — điều
tra `server/dispatcher.py` (`_profile_veo3_eligible()`/`_veo3_dispatcher_tick()`, xem §11.9/§11.20c)
+ backend `admin.py::pending_by_mode()` xác nhận đây là hậu quả TRỰC TIẾP của gap "CHƯA làm" đã ghi
ở §11.50 ngay trên ("NanoBananaPro frontend... 3 checkbox VEO/Gemini-Ảnh/Gemini-Video... CHƯA làm")
cộng với 1 bug backend ĐỘC LẬP không liên quan gì tới việc thiếu UI.

**Root cause — `pending_by_mode()` (backend, nguồn DUY NHẤT `_fetch_pending_by_mode()` dùng để tự
mở/đóng profile veo3-like, xem §11.9) hoàn toàn KHÔNG biết tới 3 cờ engine cấp project `enable_veo`/
`enable_gemini_image`/`enable_gemini_video` (§14.1 root CLAUDE.md, thêm 2026-09-09):** `imageTotal`/
`videoTotal` đếm MỌI task ảnh/video pending bất kể cờ, trong khi `heartbeat.py`'s `engine_clause`
THẬT chỉ giao task `gemini_image_selenium`/`gemini_video_selenium` cho project BẬT đúng cờ (mặc
định TẮT — và vì frontend chưa nối 3 checkbox, GẦN NHƯ KHÔNG project nào bật được cờ này qua UI).
Kèm mismatch thứ 2: `pending_by_mode()` vẫn dùng `p.status != 'paused'` (2026-08-05) trong khi
`heartbeat.py` đã đổi sang `p.status = 'active'` từ 2026-08-08 — project `draft`/`completed`/
`archived` vẫn bị đếm "còn backlog" dù `heartbeat.py` không bao giờ giao.

Hệ quả **2 lớp cộng lại** đúng nghĩa đen "bật cái tắt liền": (1) `_veo3_dispatcher_tick()`'s ngân
sách promote (`image_budget`/`video_budget`, từ `imageTotal`/`videoTotal`) gộp CHUNG cho cả VEO3
(`api`/`dom`) LẪN `gemini_image`/`gemini_video` — 2 loại sau chỉ vì force `task_mode='image_only'`/
`'video_only'` (`ProfileDialog.get_data()`) nên bị xếp nhầm cùng nhóm, được mở Chrome chỉ nhờ backlog
VEO3-only KHÔNG liên quan; (2) `_auto_scale_veo3_tick()`'s `_profile_veo3_eligible()` cũng dùng
nhầm `imageTotal`/`videoTotal` (generic) để quyết định GIỮ hay ĐÓNG — tick kế tiếp (~10s) đánh giá
lại đúng backlog gemini thật = 0 → đóng ngay (chưa từng nhận được task nào).

**Fix (`backend/routes/admin.py` + `client_tool/server/dispatcher.py`):**
1. `pending_by_mode()` — `p.status != 'paused'` → `p.status = 'active'` (đồng bộ `heartbeat.py`).
   `byMode`/`imageTotal`/`videoTotal`/`total` giờ mirror ĐÚNG `engine_clause` mặc định của
   `heartbeat.py` (machine_type='selenium_profile': `project_id IS NULL OR p.enable_veo=1`). Thêm 2
   field MỚI `imageTotalGeminiImage`/`videoTotalGeminiVideo` — mirror ĐÚNG `engine_clause` riêng của
   `gemini_image_selenium`/`gemini_video_selenium`.
2. `_profile_veo3_eligible()` — tra ĐÚNG field theo `worker_mode` (`gemini_image`→
   `imageTotalGeminiImage`, `gemini_video`→`videoTotalGeminiVideo`, còn lại giữ nguyên
   `imageTotal`/`videoTotal`/`total` cũ theo `task_mode`) — fallback về field generic nếu backend cũ
   chưa có field mới (KHÔNG regress).
3. `_veo3_dispatcher_tick()` — tách RIÊNG `gemini_image_budget`/`gemini_video_budget` khỏi
   `image_budget`/`video_budget` (VEO3) — cả lúc trừ phần profile ĐANG CHẠY đã tiêu thụ (theo
   `worker_mode`, không chỉ `task_mode` như trước) lẫn lúc xếp `waiting_pids` (`gemini_img_wait`/
   `gemini_vid_wait` tách RIÊNG khỏi `img_wait`/`vid_wait`/`all_wait`, xét `worker_mode` TRƯỚC khi
   xét `task_mode`). `all_wait` không đổi (`gemini_image`/`gemini_video` không bao giờ có
   `task_mode='all'`).

**Verify:** unit test cô lập (mock `pm.get`/`_start_worker`/`_fetch_pending_by_mode`) — 5 kịch bản
`_profile_veo3_eligible()` + 2 kịch bản `_veo3_dispatcher_tick()` (backlog chỉ VEO3 → chỉ VEO3
được promote, KHÔNG `gemini_image`; ngược lại backlog chỉ `gemini_image` → CHỈ `gemini_image` được
promote) — 11/11 pass. Backend qua `app.test_client()` + **DB THẬT** (production) — 3 project test
(active+cả 2 cờ, active+chỉ VEO, draft+VEO) + 10 task ảnh pending, so delta trước/sau xoá sạch dữ
liệu test: `imageTotal` +5 đúng (project draft bị loại đúng), `imageTotalGeminiImage` +2 đúng (chỉ
đúng 1 project bật cờ) — dữ liệu test đã xoá sạch, xác nhận không sót. Phát hiện thêm: baseline
production HIỆN TẠI đã có sẵn ~27 task ảnh pending thật sự eligible cho `gemini_image_selenium`
(1 project nào đó đã bật `enable_gemini_image=1` qua API/DB tay từ trước) — mismatch này KHÔNG PHẢI
lý thuyết suông, đang ảnh hưởng dữ liệu thật ngay lúc verify. `py_compile`/`pyflakes` sạch cả 2 file.

**CHƯA verify trên Chrome/quota thật** (không launch được PyQt6/Chrome/Selenium từ môi trường phát
triển) — cần user tự bật 1 profile `gemini_image`/`gemini_video` (trên project ĐÃ bật đúng cờ
`enable_gemini_image`/`enable_gemini_video` qua DB/API tay, vì frontend NanaBananaPro CHƯA nối 3
checkbox — xem gap "CHƯA làm" ở §11.50) rồi xác nhận KHÔNG còn hiện tượng "bật cái tắt liền".
**Backend đang chạy (nếu có) CẦN RESTART** để nhận 2 field mới từ `pending_by_mode()`.

### 11.51 Gemini — bỏ check login (navigate thẳng) + "clear cookie như VEO" (domain generic hoá) + fix "tạo image gửi thẳng chat không chọn option" (2026-09-13)

Theo 2 yêu cầu user: (1) *"với các tài khoản gemini ko cần check login, và
clear cookie như veo"*; (2) *"task tạo image, nhưng ko chọn option mà gửi
thẳng chat làm tạo ảnh sai"*.

**Bug thật #1 — `_run_task_gemini_image()` không bao giờ bật mục "Tạo hình
ảnh":** khác `_run_task_gemini_video()` (§11.27, LUÔN gọi `_gemini_video_
enter_mode()` bật chế độ "Tạo video" TRƯỚC khi gõ prompt), nhánh ảnh (§11.50)
gõ+gửi prompt THẲNG vào composer mặc định — Gemini chế độ chat thường có thể
chỉ mô tả bằng lời/hỏi lại/dùng model khác thay vì chắc chắn gọi đúng công cụ
"Create images" chuyên dụng. Đây chính là TODO để lại lúc viết `_gemini_video_
enter_mode()` ("giống cách tìm 'Tạo hình ảnh' nếu cần sau này") — nay cần.

**Fix:** `_gemini_image_enter_mode()` (mirror 1:1 `_gemini_video_enter_mode()`
— mở menu "+", click chip "Tạo hình ảnh"/"Create images", idempotent qua nút
"Bỏ chọn Hình ảnh"/"Deselect images", raise rõ ràng nếu không tìm thấy sau khi
poll 10s), gọi ngay sau bước đăng nhập trong `_run_task_gemini_image()`. i18n
mới `geminiCreateImage`/`geminiDeselectImage` (`i18n_texts.json`/`server/
i18n_texts.py::_DEFAULT_I18N_TEXTS`). **⚠️ CHƯA VERIFY qua DOM Gemini thật**
(môi trường dev không chạy được Chrome) — nhãn là SUY LUẬN theo đúng convention
"Tạo video"/"Create video" đã xác nhận thật (§11.27), KHÔNG PHẢI capture trực
tiếp — sai thì chỉ cần sửa `i18n_texts.json`, không cần đổi code.

**Bỏ hẳn bước check login cho Gemini (`_ensure_google_login()`, §11.31):** hàm
này (dùng chung VEO3+Gemini, KHÔNG dùng cho ChatGPT — đã có hệ đăng nhập RIÊNG
của OpenAI từ trước) giờ short-circuit NGAY ĐẦU cho `worker_mode in ('gemini',
'gemini_video','gemini_image')` — chỉ `driver.get(target_url)` + `sleep(3)`,
bỏ qua HOÀN TOÀN detour gmail.com/tự nhập email-mật khẩu, KHÔNG PHỤ THUỘC
setting `google_login_check_enabled` (§11.38c). Ngoại lệ DUY NHẤT: nếu
`_force_login_check` đang set cho profile này (bậc thang batch §11.39 vừa xoá
SẠCH cookie kể cả cấp tài khoản, `_sleep_wipe_all_cookies`) thì vẫn chạy
full-check 1 lần — đảm bảo còn đường phục hồi nếu account-level session thật
sự mất, dù trường hợp này hiếm khi xảy ra với Gemini (xem lý do domain-scoped
ngay dưới).

**"Clear cookie như VEO" — generic hoá domain-scoped, KHÔI PHỤC cho Gemini
(§11.39/§11.41):** trước đây `_cdp_clear_cache_and_cookies()`/`_cdp_clear_
labs_site_data()` CỐ ĐỊNH domain `labs.google` — vô nghĩa với Gemini
(`gemini.google.com`), chính lý do lời gọi bị BỎ HẲN cho Gemini từ 2026-08-30.
Giờ: `_batch_clean_target_domain(worker_mode)` (hằng số `_BATCH_CLEAN_DOMAIN_
BY_WORKER_MODE`) tra domain đúng theo worker_mode — `dom`/`api`→`labs.google`,
`gemini`/`gemini_video`/`gemini_image`→`gemini.google.com`, `chatgpt`→`None`
(chỉ xoá HTTP cache, không đụng cookie gì — không đổi hành vi cũ của ChatGPT).
`_cdp_clear_cache_and_cookies()` và `_cdp_clear_site_data_for_domain(domain)`
(đổi tên từ `_cdp_clear_labs_site_data()`, nhận `domain` bất kỳ) dùng CHUNG 1
cơ chế cho MỌI worker_mode, không viết bản riêng cho Gemini. `_cookie_matches_
domain()` thay `_batch_clean_should_delete_cookie()` (cùng logic so khớp
domain/subdomain, chỉ đổi tên + thêm tham số `target_domain`).

Khôi phục lời gọi `_cdp_clear_cache_and_cookies()` ở 2 nơi từng bị bỏ
(2026-08-30): `_run_gemini_loop()` (1-tab tuần tự, sau MỖI task) và `_run_
gemini_loop_concurrent()` (round-robin, ngay TRƯỚC khi đưa tab nghỉ về lại
gemini.google.com giữa 2 lô — thứ tự này CỐ Ý để lần navigate kế tiếp TỰ
re-auth qua SSO, tab nghỉ hiện đúng trạng thái đã đăng nhập lại thay vì trang
chưa đăng nhập). `_finish_batch_cleanup()`'s rung 2 (`threshold_clean`, dùng
chung VEO3/gemini_video/gemini_image, KHÔNG gated `is_flow`) tự động dọn đúng
domain nhờ generic hoá — không cần sửa logic, chỉ sửa 1 dòng log cho khớp
domain thật (trước hardcode "labs.google" dù có thể đang chạy gemini_video).

**Nhất quán nội tại** giữa 2 thay đổi: domain-scoped clear (KHÔNG đụng session
cấp TÀI KHOẢN trên `.google.com`/`accounts.google.com`, nơi SID/HSID thật sự
sống) + bỏ check login cho Gemini ăn khớp với nhau — sau khi xoá cookie
`gemini.google.com`, Google tự khôi phục đăng nhập cho site đó qua SSO ngay
khi navigate lại (không cần dò/nhập tay như VEO/labs.google từng cần qua
`_click_create_with_flow_if_present()`/`_return_to_saved_project()`).

**Verify:** `py_compile`/`pyflakes` sạch. `tests/_test_gemini_image_task.py`
cập nhật mock (`_gemini_image_enter_mode`) + thêm assertion — 5/5 pass.
`tests/_test_batch_escalation.py` (8/8) và `tests/_test_timewindow_disabled.py`
(5/5) chạy lại đầy đủ, KHÔNG regression — 2 test này exercise trực tiếp
`_ensure_google_login()`/`_cdp_clear_cache_and_cookies()`/`_finish_batch_
cleanup()`, là bằng chứng mạnh nhất generic hoá không phá vỡ hành vi VEO3 cũ.

**CHƯA verify trên Chrome/Gemini thật** — cần user tự Start 1 profile Gemini
để xác nhận: (a) KHÔNG còn thấy log `[google-login] Kiểm tra trạng thái đăng
nhập...` (chỉ còn navigate thẳng, có thể vẫn thấy `[google-login] (Gemini, bỏ
qua check)...` nếu navigate lỗi — bình thường, best-effort); (b) log
`[batch-clean] ✔ Đã xoá cache + site data + N/M cookie thuộc domain
gemini.google.com` xuất hiện sau mỗi task/lô; (c) sau khi bật mục "Tạo hình
ảnh", ảnh generate đúng ý hơn (không còn là câu trả lời text/ảnh sai) — nếu
KHÔNG tìm thấy chip "Tạo hình ảnh"/"Create images" (raise lỗi rõ ràng trong
log), báo lại nhãn thật trên DOM để sửa `i18n_texts.json`.

⚠️ **Lúc verify phát hiện `tests/_test_batch_clean_recover.py` ĐANG THẤT BẠI**
(mọi nhóm test đều có ≥1 assertion sai) — truy vết code xác nhận nguồn gốc nằm
ở `_ensure_project_page_ready()`/`_ensure_flow_page()` (§11.46, thêm ở 1 phiên
làm việc TRƯỚC, chưa commit — KHÔNG liên quan gì thay đổi trong mục này, bản
thân 2 hàm đó không bị sửa ở đây). Test harness của file đó (viết trước khi 2
hàm này tồn tại) không mock đủ state cho chuỗi gọi mới (`_project_page_ready()`
gọi `self._js()` không có trên fake driver, khiến `_ensure_project_page_
ready()` rơi xuống `_ensure_flow_page()` thật — real method, chưa được mock)
— cần sửa RIÊNG, ngoài phạm vi yêu cầu hôm nay, chỉ ghi nhận lại đây để không
quên khi có phiên làm việc kế tiếp đụng tới khu vực đó.

### 11.52 Log điều hướng `[nav #N]` — mọi refresh/get phải có lý do (2026-09-13)

User: *"refresh linh tinh nhiều, ghi rõ để debug loại bỏ các bước không cần thiết"*.
MỌI điều hướng của worker đi qua `_nav_refresh(reason)` / `_nav_get(url, reason)` /
`_nav_js_href(url, reason)` (`server/worker.py`, cạnh `_log`/`_sleep`) — mỗi lần ghi
1 dòng `[nav #N]`: loại + đích + thời gian, lý do, trang đang đứng, `sau:` (chỉ khi
bị đá sang trang khác đích), cách lần trước (< `_NAV_BURST_SECS`=10s → `⚠ DỒN DẬP`,
level warn), task/batch, chuỗi `hàm:dòng ← …` (chỉ frame của worker.py). Lỗi → log
rồi raise lại nguyên exception. `_process_tasks()` gọi `_nav_batch_begin()` đầu batch
và `_nav_batch_end()` SAU `_finish_batch_cleanup()` → dòng tổng kết gom theo lý do.

⚠️ **Quy tắc:** thêm điểm điều hướng mới thì dùng 3 hàm này, ĐỪNG gọi thẳng
`driver.refresh()`/`driver.get()` — không thì bước đó biến mất khỏi log debug.
State tự khởi tạo lười (`_nav_ensure_state()`) nên test dựng worker bằng
`object.__new__` vẫn chạy. `routes.py` (login browser) không đổi.

⚠️ **Nghi phạm refresh thừa (CHƯA sửa):** `_dom_fetch_project_media()` gọi thẳng
`_batchexecute_harvest_session()` (luôn refresh) chứ không qua `_be_session()` có
cache — mỗi vòng reconcile refresh 1 lần, cuối batch refresh 2 lần liền. Chờ log
`[nav]` thật xác nhận rồi mới bỏ.

### 11.53 Chạy task theo project + email, dùng lại id Flow đã lưu thay vì upload lại (2026-09-14)

User: *"để tránh trường hợp bị khóa upload image. mỗi project nano banana bây giờ sẽ lưu
theo ID flow... trong setting client_tool thêm tính năng check chọn chỉ chạy task theo
project + email... task tạo ảnh phải lưu lại hết id của nó để làm ảnh tham chiếu sau này"*.
Bối cảnh: upload ref qua `maseQ` hay dính `RPC_ERROR_CODE_8` (giới hạn nhịp tài khoản,
§11.46). Nếu mọi task của 1 project luôn chạy trên CÙNG 1 tài khoản + CÙNG 1 project Flow
thì ảnh đã sinh ra trong project đó dùng lại được làm tham chiếu mà không phải upload.

**Backend (ToolSub):** `projects.flow_email` (mới) + `projects.flow_project_id` (có sẵn,
trước giờ không ai dùng). Project có đủ 2 field = "đã gán". Heartbeat: máy VEO gửi
`bindProjectEmail` → CHỈ nhận task của project gán đúng `accountEmail`, cả lô khoá 1
project; máy KHÔNG gửi → bỏ qua mọi project đã gán. Task mang `flowProjectId`/`flowEmail`.
`result_files[].flowProjectId` được đóng dấu khi `/task/download`/reconcile lưu kết quả;
`_resolve_ref_images()` gửi kèm `flowMediaName`/`flowProjectId` cho từng ảnh tham chiếu.
`pending_by_mode` thêm `byFlowEmail`; `imageTotal`/`videoTotal` không còn đếm project đã gán.

**Setting `bind_tasks_to_project_email`** (0/1, mặc định 0). Chỉ áp dụng `worker_mode`
`api`/`dom`, đọc tươi qua `get_local_settings()` mỗi lần dùng (bật/tắt không cần restart).

**Worker khi đang gán** (`_apply_flow_binding()` ở đầu `_process_tasks()`):
- `profile['project_url']` đổi TRONG RAM sang `https://flow.google.com/project/<id>`,
  KHÔNG ghi DB (thôi gán thì trả lại project riêng qua `_own_project_url`).
- `_ensure_flow_page()`/`_ensure_flow_project()` → `_ensure_bound_flow_page()`: không
  bao giờ tạo project mới. `_try_reuse_or_bootstrap_project()` trả False,
  `_rotate_project_if_full()` bỏ qua (kể cả đủ 300 item), `_persist_project_url()` từ chối,
  `_sync_project_url_from_browser()` no-op. `_project_page_ready()` còn so id project
  đang đứng với id đã gán.
- Lô lẫn nhiều project (backend cũ) → chia nhóm theo `flowProjectId`, chạy lần lượt.

**Dùng lại id Flow** (`_upload_source_media_cached()`, áp dụng MỌI chế độ, không riêng lúc
gán): id có `flowProjectId` của project KHÁC → upload. Mọi id còn lại — id đã lưu (có dấu
hoặc chưa) lẫn id trong cache upload (§11.49) — phải **tìm thấy trong project Flow hiện
tại** (`_flow_id_in_project()`, 2026-09-15) mới dùng lại; không thấy → upload ảnh tham
chiếu. Thứ tự ref giữ nguyên. Log `♻ Dùng id Flow đã lưu cho X/Y…`, `🔎 N id ảnh tham chiếu
không tìm thấy… → upload tham chiếu`, và khi đang gán mà vẫn phải upload thì
`⬆ Phải upload N…` (level warn).

**File chỉ mục `flow_media_index.json`** (`server/flow_media_index.py`, gitignore): danh
sách uuid `meta[4]` của từng project Flow. Tra file TRƯỚC — có id thì dùng luôn, không gọi
RPC. Không có (hoặc chưa từng đọc project đó) mới đọc lại `Zzl0ze`
(`_flow_project_media_names(max_age=5)`), tối đa 1 lần/lượt ảnh, rồi GHI ĐÈ file (id đã
xoá khỏi project tự rơi ra). Nguồn ghi: mọi lần đọc đủ danh sách — kể cả bước reconcile
cuối batch (`_dom_fetch_project_media()` → `_flow_listing_store()`, không tốn RPC thêm) —
và id vừa upload (`_flow_listing_add()`; chỉ thêm vào project ĐÃ có chỉ mục, không tạo chỉ
mục thiếu từ vài id lẻ). Giữ tối đa 100 project, ghi atomic (`.tmp` + `os.replace`).

⚠️ `_flow_project_media_names()` trả `None` = KHÔNG tra được (RPC lỗi / đường batchexecute
tắt / không session), `set()` = đã tra, project rỗng. Khi `None`: id có dấu đúng project
và id cache upload vẫn dùng (tránh upload hàng loạt dính `RPC_ERROR_CODE_8`), id chưa có
dấu thì upload.

**Dispatcher:** bật gán thì `_profile_veo3_eligible()` (api/dom) dùng backlog THEO EMAIL
(`_email_backlog()`), `_veo3_dispatcher_tick()` có ngân sách theo email (`bound_wait`
promote trước). Profile VEO không còn bắt buộc `project_url` riêng (cả ở `routes.py` Start).

⚠️ **CHƯA verify trên Chrome/Flow thật.** Chưa chắc Flow nhận id ảnh ĐÃ SINH (DETAIL_UUID,
không phải ảnh upload) làm `imageInputs`/`referenceImages` — nếu Google từ chối, task lỗi ở
bước generate. DOM mode vẫn đính ref qua picker (upload), phần dùng lại id chỉ áp dụng
đường API/batchexecute. Test scratch: 50/50 (worker + dispatcher + file chỉ mục, không
Chrome/mạng).

### 11.54 Khung giờ chạy riêng cho profile VEO — `run_hours` (2026-09-17)

User: *"các tài khoản thuộc veo cho phép chọn thời gian chạy như 1 ngày 24h sẽ có check
chọn 0 ->24 chỉ nhận task trong thời gian được chọn, nếu ko chọn thì chạy liên tục"*.
KHÁC "Khung giờ không nhận task" (§11.21 — toàn máy, qua Master switch): đây là RIÊNG
TỪNG profile, không đụng Master switch.

- **Lưu:** `selenium_profiles.run_hours` (backend, CSV 0..23). Rỗng/đủ 24h = liên tục.
  Giờ máy chạy client_tool. Chỉ `worker_mode` api/dom (`run_hours.applies_to`).
- **Nguồn logic duy nhất:** `server/run_hours.py::in_run_hours(profile)`.
- **2 lớp chặn:** (1) `worker._heartbeat()` ngoài khung → `keepaliveOnly` + trả `[]`
  (bảo đảm kể cả khi worker được mở bằng đường khác); (2) `dispatcher._run_hours_gate()`
  trong `_auto_scale_veo3_tick` — không đưa vào `_desired_veo3`, đóng profile rảnh; vòng
  promote `waiting` của `_veo3_dispatcher_tick` cũng lọc. Task dở chạy xong mới đóng.
- **Start thủ công** ngoài khung → 409 `outside_run_hours` (`routes.start_worker`).
- **GUI:** "Giờ chạy" 24 ô trong `ProfileDialog` (nhóm veo3); bảng Profiles hiện
  `⏰ <khoảng>` ở dòng phụ cột Mode.
- ⚠️ Backend cũ chưa có cột → field vắng mặt → coi như chạy liên tục; PATCH bị whitelist
  bỏ qua im lặng. Phải deploy backend trước khi dùng.
- **CHƯA verify trên Chrome thật.**

### 11.55 Check ĐẦU BATCH — task đã render xong từ trước thì không gửi lại (2026-09-19)

`_precheck_batch_done(tasks)` chạy đầu `_process_tasks()` (`dom`/`api`): quét project Flow
đang mở, task nào `/reconcile/check` trả `matched` → `_handle_task_success()` + bỏ khỏi lô.
Lô có `retry_count>0` quét cửa sổ `reconcile_retry_lookback_secs` (24h); lô không retry mà
vừa quét < `_PRECHECK_SKIP_SECS` (60s) thì bỏ qua. Check retry riêng từng task trong
`_run_tasks_batch()` đã gộp vào đây. Media ở project Flow KHÁC coi như chưa tạo.
**CHƯA verify trên Chrome/Flow thật.**

---

## 12. Ràng buộc khi thay đổi code

1. **Cập nhật CHANGELOG.md** (root project) sau mỗi lần sửa file.
2. **Cập nhật CLAUDE.md này** nếu thay đổi luồng xử lý, API endpoints, hoặc cấu trúc file.
3. **Cập nhật WORKFLOW_SERVER_EXTENSION.md** (root) nếu ảnh hưởng đến giao tiếp server ↔ extension.
4. **Không sửa venv/** — môi trường Python, không phải source code.
5. **Test trước khi deploy** — chạy file tương ứng trong `tests/` để verify.
