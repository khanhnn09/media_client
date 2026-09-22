# CHANGELOG — client_tool

> Mỗi lần thay đổi code phải thêm entry vào đây. Format: `YYYY-MM-DD | file(s) thay đổi | mô tả`.
>
> Repo này tách ra từ `ToolSub` ngày 2026-07-21 (remote `git@45.117.76.38:khanh.nn/machinemedia.git`) — lịch sử TRƯỚC ngày đó nằm trong CHANGELOG.md của repo `ToolSub` gốc, không copy lại ở đây.

---

### 2026-09-21 | `gui/profile_dialog.py` | Modal thêm/sửa profile: bọc form vào vùng cuộn dọc, dialog tự co theo Loại đang chọn

User: *"modal profile tài khoản của client_tool dài quá cho scroll Y vào"*.

Dialog này dài dần theo thời gian (lưới 24 ô khung giờ chạy, timeout Gemini/ChatGPT, số tab
đồng thời, proxy, ghi chú…) — đo thật ở màn hình cao 800px: loại **VEO3 cần 720px** nội dung
trong khi chỗ dùng được chỉ 540px, nên phần cuối tràn khỏi màn hình và **không bấm tới được
nút Lưu**.

**Sửa:** `QFormLayout` chuyển vào `QScrollArea` (`setWidgetResizable(True)`, tắt cuộn ngang,
nền trong suốt + chừa 4px máng cho thanh cuộn — cùng pattern `gui/pages/settings_page.py` đã
dùng, thanh cuộn ăn sẵn QSS ở `gui/style.py`). **Tiêu đề và 2 nút Hủy/Lưu CỐ Ý nằm NGOÀI vùng
cuộn** để luôn nhìn thấy dù form dài bao nhiêu.

**`_fit_height()` (mới)** — dialog CO/GIÃN theo đúng nội dung đang hiện, chỉ bật thanh cuộn khi
vượt trần `chiều cao màn hình khả dụng − 260px`. Phải gọi lại ở CUỐI `_on_type_change()`: đổi
"Loại" ẩn/hiện hàng nên chiều cao cần thiết đổi rất nhiều, không gọi lại thì dialog kẹt ở chiều
cao của lần dựng đầu (hoặc thừa một mảng trống, hoặc cuộn oan). Bên trong gọi `form.activate()`
trước khi đọc `sizeHint()` — Qt cập nhật layout lười, đọc ngay sau `setVisible()` sẽ ra số cũ.
Cờ `self._built` chặn lần gọi sớm trong `_build()` (lúc đó hàng nút chưa được thêm nên
`adjustSize()` sẽ tính thiếu).

**Verify** (offscreen Qt, màn hình 800px, chạy thật bằng venv của client_tool): 13/13 PASS —
form nằm trong scroll, nút Lưu nằm ngoài, cuộn ngang đã tắt, `get_data()` không đổi, dialog
Sửa cũng đúng. Đo từng loại: `veo3` cần 720px → cuộn; `gemini` 589px → cuộn; `chatgpt` 485px,
`gemini_video`/`gemini_image` 537px → vừa khít, KHÔNG cuộn và dialog tự thu lại. Trên màn hình
1080p (trần ≈780px) thì cả VEO3 cũng hiện trọn, không cuộn — không đổi trải nghiệm của máy màn
hình cao. **Chưa tự tay mở app thật để xem** (chỉ chạy Qt offscreen).

---

### 2026-09-19 | `server/worker.py`, `server/config.py`, `server/local_settings.py`, `gui/pages/settings_page.py` | Check ĐẦU BATCH: task đã render xong từ trước thì tải về, không gửi lại prompt

User: *"mỗi lần vào chạy task có check tất cả task cũ chạy trước đó trong project đó được
donwload về chưa tránh trường hợp chạy nhiều lần 1 task"*. Trước đây có 3 lỗ: (1) mode API
gửi lại task retry thẳng, không check; (2) batch đầu tiên sau khi mở worker/ngủ dậy không có
lần quét nào (chỉ quét CUỐI batch); (3) chỉ xét media tạo trong 2 giờ gần nhất.

- `_precheck_batch_done()` (mới) — gọi đầu `_process_tasks()` cho `dom`/`api`: quét project
  Flow đang mở 1 lần, task nào server vừa khớp/tải (`matched`) thì `_handle_task_success()`
  và bỏ khỏi lô. Cả lô đã xong thì không gửi prompt nào.
- Lô có task chạy lại (`retry_count>0`) → quét cửa sổ rộng `reconcile_retry_lookback_secs`
  (mới, mặc định 86400 = 24h, sửa ở trang Cài đặt). Lô không có retry mà vừa quét < 60s
  (cuối batch trước) → bỏ qua, tránh refresh 2 lần liền.
- `_dom_fetch_project_media()`/`_reconcile_project_media()` nhận thêm `lookback_secs`;
  `_reconcile_project_media()` ghi `_last_reconcile_at` khi quét thành công.
- Bỏ check retry riêng từng task trong `_run_tasks_batch()` (đã gộp vào check đầu batch).
- Media nằm ở project Flow KHÁC coi như chưa tạo (quyết định user).

Verify: test stub 5 kịch bản (bỏ task đã xong, bỏ qua khi vừa quét, cửa sổ 24h khi có
retry, không vào được trang project → giữ lô, gemini_video không đụng);
`_test_batch_escalation.py` 8/8; pyflakes sạch. **CHƯA verify trên Chrome/Flow thật.**
⚠️ Lô có retry quét 24h ⇒ gọi `as29s` cho mọi media đã sinh trong 24h — chậm hơn với project lớn.

---

### 2026-09-17 | `server/run_hours.py` (MỚI), `server/{worker,dispatcher,routes,managers}.py`, `gui/profile_dialog.py`, `gui/pages/profiles_page.py` | Khung giờ chạy riêng cho profile VEO (tick 0..23h)

User: *"tool_client các tài khoản thuộc veo cho phép chọn thời gian chạy như 1 ngày 24h
sẽ có check chọn 0 ->24 chỉ nhận task trong thời gian được chọn, nếu ko chọn thì chạy
liên tục"*.

- **Lưu:** cột mới `selenium_profiles.run_hours` (backend, CSV giờ 0..23). Rỗng hoặc đủ
  24 giờ = chạy liên tục. Giờ `h` = nhận task `h:00–h:59` theo GIỜ MÁY chạy client_tool.
  Chỉ áp dụng `worker_mode` api/dom.
- **`server/run_hours.py`:** `parse/format/summarize_run_hours`, `in_run_hours(profile)`.
  Field vắng mặt (backend cũ chưa có cột) = chạy liên tục.
- **Worker (`_heartbeat`):** ngoài khung giờ → gửi `keepaliveOnly` (giữ `last_seen`), trả
  `[]`, không nhận task mới; task đang chạy dở vẫn chạy xong. Log 1 lần lúc vào/ra khung.
- **Dispatcher:** `_run_hours_gate()` trong `_auto_scale_veo3_tick` — ngoài khung thì bỏ
  khỏi `_desired_veo3`, đóng profile nếu không có task dở. Vòng promote `waiting` cũng bỏ
  qua profile ngoài khung.
- **Start thủ công** ngoài khung giờ → 409 `outside_run_hours`, GUI báo rõ khung giờ.
- **GUI:** `ProfileDialog` (chỉ loại VEO3) thêm "Giờ chạy" — 24 ô 0..23 + nút Chọn
  hết/Bỏ hết/8-17h + dòng tóm tắt; loại khác gửi `run_hours=''`. Bảng Profiles cột Mode
  hiện `⏰ 8-12h, 20-23h`.
- Verify: 27/27 test (helper, heartbeat gating qua `_heartbeat` thật, dispatcher gate,
  `ProfileDialog` headless). Backend: migration + create/PATCH/GET qua route thật trên DB
  thật, đã xoá dữ liệu test. CHƯA verify trên Chrome thật.

---

### 2026-09-15 | `server/worker.py`, `server/flow_media_index.py` (MỚI), `.gitignore` | Không tìm thấy uuid ảnh tham chiếu trong project Flow → upload tham chiếu; lưu file chỉ mục uuid từng project

User: *"nếu ko tìm thấy uuid của ảnh tham chiếu trên project của flow đó thì sẽ upload
tham chiếu"* + *"nhớ lưu file temp để các lịch sử duyệt danh sách uuid assets của project
flow đó để tìm khi cần tham chiếu gán vào tránh phải đọc request danh sách uuid đang có,
chỉ khi ko có mới cần đọc lại"*.

- **Trước:** id có dấu `flowProjectId` trùng project hiện tại được dùng thẳng, không kiểm
  tra id đó còn trong project Flow hay không; id trong cache upload cũng vậy.
- **Giờ:** mọi id dùng lại (id đã lưu lẫn id trong cache upload) phải TÌM THẤY trong
  project Flow hiện tại (`_flow_id_in_project()`), không thấy → upload ảnh tham chiếu.
  Log `🔎 N id ảnh tham chiếu không tìm thấy trong project Flow … → upload tham chiếu`.
- **File chỉ mục `flow_media_index.json`** (`server/flow_media_index.py`, gitignore) — lưu
  danh sách uuid (`meta[4]`) của từng project Flow. Tra file TRƯỚC: có id → dùng luôn,
  không gọi RPC. Không có (hoặc chưa từng đọc project đó) → đọc lại `Zzl0ze` tối đa 1
  lần/lượt ảnh, rồi ghi đè file. Nguồn ghi: mỗi lần đọc đủ danh sách (cả bước reconcile
  cuối batch — không tốn thêm RPC) và id vừa upload. Giữ tối đa 100 project, ghi atomic.
- **Không đọc được danh sách** (RPC lỗi, đường batchexecute tắt): id có dấu đúng project
  vẫn dùng (tránh upload hàng loạt dính giới hạn nhịp), id chưa có dấu thì upload, id
  trong cache upload vẫn dùng như cũ.
- `_flow_project_media_names()` giờ trả `None` khi không tra được (trước trả `set()`,
  không phân biệt được với project rỗng).

Verify: test scratch 50/50 (worker + dispatcher + file chỉ mục qua restart), không
Chrome/mạng. `_test_batch_escalation.py` / `_test_gemini_image_task.py` /
`_test_return_to_saved_project.py` pass. `py_compile`/`pyflakes` sạch. **CHƯA verify
trên Chrome/Flow thật.**

### 2026-09-14 | `server/worker.py`, `server/dispatcher.py`, `server/routes.py`, `server/config.py`, `server/local_settings.py`, `gui/pages/settings_page.py` | "Chỉ chạy task theo project + email" + dùng lại id Flow đã lưu làm ảnh tham chiếu (tránh bị khoá upload)

User: *"để tránh trường hợp bị khóa upload image. mỗi project nano banana bây giờ sẽ
lưu theo ID flow... trong setting client_tool thêm tính năng check chọn chỉ chạy task
theo project + email thì lúc đó task nhận chỉ có cùng email mới dc nhận và truy cập
link flow đó để thực hiện task. Đối với task tạo ảnh phải lưu lại hết id của nó để
làm ảnh tham chiếu sau này... chỉ lấy đúng các image đã lưu để không bị lỗi chặn
upload"*. Phần backend/frontend (cột `projects.flow_email`, lọc heartbeat, lưu
`flowProjectId`) ghi ở CHANGELOG của `ToolSub`.

- **Cài đặt mới `bind_tasks_to_project_email`** (0/1, mặc định 0 — trang Cài đặt).
  Bật: heartbeat gửi `bindProjectEmail` + `accountEmail` → backend chỉ giao task của
  project gán đúng email profile, cả lô 1 project; mỗi task mang `flowProjectId`.
- **Worker vào đúng link Flow của project** — `_apply_flow_binding()` (đầu
  `_process_tasks()`) đổi `project_url` TRONG RAM sang
  `https://flow.google.com/project/<flowProjectId>`, không ghi DB; thôi gán thì trả
  lại project riêng. `_ensure_flow_page()`/`_ensure_flow_project()` chuyển sang
  `_ensure_bound_flow_page()`. Ở chế độ này KHÔNG tạo / luân chuyển (kể cả đủ 300
  item) / đồng bộ project Flow; vào không được thì báo lỗi. `_project_page_ready()`
  còn kiểm tra đúng id project đã gán. Lô lẫn nhiều project (backend cũ) → chạy lần
  lượt từng nhóm.
- **Dùng lại id Flow đã lưu** — `_upload_source_media_cached()` ưu tiên
  `flowMediaName` backend gửi kèm từng ảnh tham chiếu: có dấu `flowProjectId` trùng
  project → dùng thẳng; chưa có dấu (dữ liệu cũ) → xác nhận qua danh sách `Zzl0ze` của
  project (`_flow_project_media_names()`, cache 120s, 1 lần/lô); không có → upload như
  cũ, kèm log cảnh báo khi đang gán. Áp dụng mọi chế độ, không chỉ khi bật cài đặt.
- **Lưu id kèm project khi báo kết quả** — 4 chỗ `POST /task/download` gửi thêm
  `flowProjectId`; item reconcile (`_dom_fetch_project_media()`) cũng mang `flowProjectId`.
- Dispatcher: bật chế độ gán thì backlog/ngân sách mở profile tính theo ĐÚNG email
  (`byFlowEmail` từ `pending_by_mode`), và profile VEO không bắt buộc `project_url`
  riêng (cả ở `routes.py` Start).

**Verify:** `py_compile`/`pyflakes` sạch. Test scratch (worker thật qua `object.__new__`
+ dispatcher thật, không Chrome/mạng) 27/27: gán/bỏ gán, chặn đổi/tạo/luân chuyển
project, page-ready theo id, dùng lại id (có dấu / không dấu có trong project / project
khác / tên tự chế), thứ tự ref giữ nguyên + chỉ upload ảnh thiếu id, chia nhóm lô, backlog
theo email, ngân sách mở đúng profile theo email. **CHƯA verify trên Chrome/Flow thật** —
chưa chắc 100% Flow nhận id ảnh ĐÃ SINH (không phải ảnh upload) làm `imageInputs`/
`referenceImages`; nếu Google từ chối, task sẽ báo lỗi ở bước generate.

---

### 2026-09-13 | `server/worker.py` | Log điều hướng `[nav #N]` — ghi rõ MỌI lần refresh/get để lọc bước thừa

User: *"log nên ghi chi tiết hơn như gọi lệnh refresh vì tôi thấy refresh linh
tinh nhiều, ghi rõ để debug loại bỏ các bước không cần thiết"*.

- 3 hàm mới `_nav_refresh(reason)` / `_nav_get(url, reason)` / `_nav_js_href(url,
  reason)` (qua `_nav()`). Thay TOÀN BỘ 21 chỗ gọi thẳng `driver.refresh()` /
  `driver.get()` / `location.href=` trong worker, mỗi chỗ kèm lý do cụ thể.
- Mỗi lần điều hướng ghi 1 dòng vào `logs/profile_{id}.log` (+ backend): loại,
  đích, thời gian chạy, lý do, trang đang đứng, `sau:` (chỉ hiện khi bị đá sang
  trang khác đích), cách lần trước bao lâu, task/batch, và chuỗi hàm đã gọi tới
  (`hàm:dòng ← hàm:dòng`, chỉ frame của worker.py). Cách lần trước < 10s
  (`_NAV_BURST_SECS`) → cờ `⚠ DỒN DẬP`, level warn. Lỗi → vẫn log rồi raise lại
  nguyên exception (call site xử lý như cũ).
- `_process_tasks()` ghi mốc `[nav] ── Bắt đầu batch #N` và tổng kết cuối batch
  (đặt SAU `_finish_batch_cleanup()` để tính cả bước dọn), gom theo (loại, lý do),
  số trong lý do gộp thành N.
- Log cũ `Reload project để Flow gọi lại…` và `N lỗi liên tiếp → refresh trang`
  được thay bằng dòng `[nav]` (lý do giờ kèm luôn lỗi cuối cùng).
- Không đổi hành vi điều hướng nào — chỉ thêm log.

Verify: `py_compile`/`pyflakes` sạch; test driver giả (không mở Chrome) — số thứ
tự, lý do, chuỗi hàm gọi, cờ dồn dập, `sau:` khi bị redirect, raise lại lỗi, tổng
kết batch gộp đúng nhóm. Test cũ `_test_return_to_saved_project`,
`_test_batch_escalation`, `_test_timewindow_disabled`, `_test_gemini_image_task`
vẫn pass; `_test_batch_clean_recover` vẫn hỏng ĐÚNG như trước (phần reconcile/bấm
nút, đã ghi ở CLAUDE.md §11.51), các kiểm tra điều hướng trong đó vẫn pass.

⚠️ Đọc code lúc làm phát hiện 1 nghi phạm "refresh thừa" (CHƯA sửa, chờ log thật
xác nhận): `_dom_fetch_project_media()` gọi thẳng `_batchexecute_harvest_session()`
(luôn refresh) thay vì `_be_session()` có cache — mỗi vòng reconcile refresh 1 lần,
và cuối batch refresh 2 lần liền (`_recover_flow_project_page_after_cache_clear()`
refresh rồi reconcile lại refresh). `Zzl0ze` đọc thẳng từ server nên về lý thuyết
không cần refresh để thấy media mới.

### 2026-09-13 | `tests/_test_uploadImage_old.py` (MỚI) | Test upload ảnh tham chiếu qua ĐƯỜNG CŨ `aisandbox /v1/flow/uploadImage` — kết luận: lỗi `maseQ RPC_ERROR_CODE_8` là bị Google GIỚI HẠN NHỊP ở tầng tài khoản, đường cũ cũng dính y hệt

User báo *"batchexecute upload ảnh tham chiếu bị lỗi hãy dùng link cũ test upload
ảnh xem được không"*. Log profile 25 (2026-09-13 00:05): mọi task có ảnh tham
chiếu lỗi `RPC maseQ lỗi: RPC_ERROR_CODE_8` — mã gRPC 8 = `RESOURCE_EXHAUSTED`.

File test mới (KHÔNG đụng code sản xuất): attach Chrome profile (mặc định 25),
lấy bearer từ `labs.google/fx/api/auth/session` ở TAB RIÊNG (gọi từ trang
flow.google.com bị CORS chặn), dựng body bằng `flow_api.build_upload_image_body()`
rồi thử 4 cách gửi: A curl_cffi origin labs.google (đúng cách `_post_aisandbox()`),
B curl_cffi origin flow.google.com, C/D `fetch()` trong trang. Cờ `--signin`
đăng nhập lại labs.google bằng next-auth Google SSO (dùng phiên Google sẵn có,
không gõ mật khẩu) khi session trả `{}`; `--all`, `--transport`, `--no-verify`.

Kết quả chạy THẬT trên profile 25 (huavantien84_2):
- labs.google KHÔNG còn session (chỉ còn cookie `csrf-token`/`callback-url`,
  mất `__Secure-next-auth.session-token` — bước dọn cookie labs.google mỗi batch
  §11.39 xoá nó). `labs.google/fx/tools/flow` giờ redirect sang flow.google.com.
  `--signin` đăng nhập lại được, lấy được token `ya29…`.
- **A: HTTP 429 `RESOURCE_EXHAUSTED`, reason `PUBLIC_ERROR_USER_REQUESTS_THROTTLED`**
  — auth/body đều hợp lệ (không 400/401), request bị chặn ở khâu quota. CÙNG
  nguyên nhân với `maseQ` code 8 ⇒ đổi đường KHÔNG giúp gì.
- B: HTTP 403 `API_KEY_HTTP_REFERRER_BLOCKED` — API key chỉ nhận referer labs.google.
- C/D: `TypeError: Failed to fetch` (CORS, đã biết).

Verify: `py_compile`/`pyflakes` sạch. Không có ảnh rác nào bị upload vào project
(cả 4 cách đều hỏng). Login browser mở để test đã đóng lại sau khi chạy.

### 2026-09-12 (d) | `server/chrome_utils.py`, `server/routes.py`, `server/worker.py` | FIX thật: "Mở login browser" lỗi `ChromeDriver only supports Chrome version 151, Current browser version is 153`

User báo mở login profile lỗi đúng câu trên. Bản vá (c) cùng ngày chỉ sửa nhánh
`uc.Chrome` trong `_make_driver()`; nút "Mở login browser" (`routes.py`) đi đường
KHÁC — `webdriver.Chrome(service=_make_service())` — mà `_make_service()` trả
THẲNG `CHROMEDRIVER_PATH` (`data/profiles/chromedriver/chromedriver.exe`, bản
151 còn sót) không hề kiểm tra version. Cùng lỗi ở 2 chỗ khác dùng chung hàm
này: fallback regular webdriver của worker và attach `_connect_to_chrome()`.

Fix tập trung tại `_make_service(chrome_binary='', debug_port=0)`: so major
version file chromedriver cục bộ với Chrome sẽ chạy (`debug_port` → đọc
`/json/version` của Chrome ĐANG chạy qua helper mới `_running_chrome_major()`;
không có thì `_detect_chrome_version(chrome_binary)`). Lệch → bỏ qua file cũ,
trả `Service()` trống để Selenium Manager tự tải đúng bản. aarch64 giữ nguyên
file cục bộ (Selenium Manager không có bản ARM). 3 call site truyền
`portable_exe`/`debug_port` tương ứng.

Verify: đo trên máy thật — file cục bộ 151, Chrome hệ thống 153; gọi
`_make_service()` + `DriverFinder` (không mở Chrome) → log cảnh báo đúng, Selenium
Manager resolve ra `~/.cache/selenium/chromedriver/win64/153.0.8010.36/chromedriver.exe`
(major 153). `py_compile`/`pyflakes` sạch. **CHƯA** bấm thử "Mở login browser"
trên GUI — cần restart `main.py` rồi thử lại.

### 2026-09-13 | `server/worker.py`, `i18n_texts.json`, `server/i18n_texts.py`, `tests/_test_gemini_image_task.py` | Gemini: bỏ check login (chỉ navigate thẳng) + "clear cookie như VEO" (generic hoá domain-scoped) + fix bug thật "tạo image gửi thẳng chat không chọn option sinh ảnh sai"

Theo 2 yêu cầu user: (1) *"với các tài khoản gemini ko cần check login, và clear
cookie như veo"*; (2) *"task tạo image, nhưng ko chọn option mà gửi thẳng chat
làm tạo ảnh sai"*.

**1. Bug thật — `_run_task_gemini_image()` không bao giờ bật mục "Tạo hình
ảnh":** khác `_run_task_gemini_video()` (LUÔN gọi `_gemini_video_enter_mode()`
bật chế độ "Tạo video" trước khi gõ prompt), nhánh ảnh gõ+gửi prompt THẲNG vào
composer mặc định — Gemini ở chế độ chat thường có thể chỉ mô tả bằng lời/hỏi
lại/dùng model khác thay vì chắc chắn gọi đúng công cụ "Create images" chuyên
dụng. Đây chính là TODO để lại lúc viết `_gemini_video_enter_mode()` ("giống
cách tìm 'Tạo hình ảnh' nếu cần sau này") — nay user xác nhận cần. Fix: hàm mới
`_gemini_image_enter_mode()` (mirror 1:1 `_gemini_video_enter_mode()` — mở menu
"+", click chip "Tạo hình ảnh"/"Create images", idempotent, raise rõ ràng nếu
không tìm thấy), gọi ngay sau bước đăng nhập trong `_run_task_gemini_image()`.
i18n mới `geminiCreateImage`/`geminiDeselectImage` trong `i18n_texts.json` —
**⚠️ CHƯA VERIFY qua DOM Gemini thật** (môi trường dev không chạy được Chrome),
nhãn là SUY LUẬN theo đúng convention "Tạo video"/"Create video" đã xác nhận
thật trước đó, KHÔNG PHẢI capture trực tiếp — sai thì chỉ cần sửa
`i18n_texts.json`, không cần đổi code.

**2. Gemini bỏ hẳn bước check login (`_ensure_google_login()`):** hàm này
(dùng chung VEO3+Gemini) giờ short-circuit NGAY ĐẦU cho `worker_mode in
('gemini','gemini_video','gemini_image')` — chỉ `driver.get(target_url)` +
sleep(3), bỏ qua HOÀN TOÀN detour gmail.com/tự nhập email-mật khẩu, KHÔNG PHỤ
THUỘC setting `google_login_check_enabled`. Ngoại lệ: nếu `_force_login_check`
đang set cho profile này (edge case — vừa bị xoá SẠCH cookie kể cả cấp tài
khoản qua bậc thang batch, xem mục 3) thì vẫn chạy full-check 1 lần, đảm bảo
còn đường phục hồi nếu account-level session thật sự mất.

**3. "Clear cookie như VEO" — generic hoá domain-scoped, KHÔI PHỤC cho
Gemini:** trước đây `_cdp_clear_cache_and_cookies()`/`_cdp_clear_labs_site_
data()` CỐ ĐỊNH domain `labs.google` — vô nghĩa với Gemini (`gemini.google.com`,
đã bị BỎ HẲN lời gọi cho Gemini từ 2026-08-30 chính vì lý do này). Giờ:
`_batch_clean_target_domain(worker_mode)` tra domain đúng theo worker_mode
(`dom`/`api`→`labs.google`, `gemini`/`gemini_video`/`gemini_image`→
`gemini.google.com`, `chatgpt`→`None`=chỉ xoá HTTP cache); `_cdp_clear_cache_
and_cookies()` và `_cdp_clear_site_data_for_domain(domain)` (đổi tên từ
`_cdp_clear_labs_site_data()`) dùng CHUNG 1 cơ chế cho mọi worker_mode, không
viết bản riêng. Khôi phục lời gọi trong `_run_gemini_loop()` (sau mỗi task) và
`_run_gemini_loop_concurrent()` (ngay trước khi đưa tab nghỉ về lại
gemini.google.com giữa 2 lô — thứ tự này để lần navigate kế tiếp TỰ re-auth
qua SSO, tab nghỉ hiện đúng trạng thái đã đăng nhập lại). `_finish_batch_
cleanup()`'s rung 2 (VEO3/gemini_video/gemini_image dùng chung, KHÔNG gated
`is_flow`) tự động dọn đúng domain nhờ generic hoá — không cần sửa gì thêm ở
đó, chỉ sửa lại 1 dòng log cho khớp domain thật.

Nhất quán nội tại: domain-scoped clear (KHÔNG đụng session cấp tài khoản trên
`.google.com`/`accounts.google.com`, nơi SID/HSID thật sự sống) + bỏ check
login cho Gemini ăn khớp với nhau — sau khi xoá cookie `gemini.google.com`,
Google tự khôi phục đăng nhập cho site đó qua SSO ngay khi navigate lại, không
cần dò/nhập tay như VEO/labs.google từng cần.

**Verify:** `py_compile`/`pyflakes` sạch. `tests/_test_gemini_image_task.py`
cập nhật mock (`_gemini_image_enter_mode`) — 5/5 pass, thêm assertion xác nhận
mode-select được gọi. `tests/_test_batch_escalation.py` (8/8) và `tests/
_test_timewindow_disabled.py` (5/5) — chạy lại đầy đủ, KHÔNG regression (đúng
2 test này exercise trực tiếp `_ensure_google_login()`/`_cdp_clear_cache_and_
cookies()`/`_finish_batch_cleanup()`). **CHƯA verify trên Chrome/Gemini thật**
— cần user tự Start 1 profile Gemini, xác nhận: (a) không còn thấy log
`[google-login] Kiểm tra trạng thái đăng nhập...` (chỉ còn navigate thẳng);
(b) log `[batch-clean] ✔ Đã xoá cache + site data + N/M cookie thuộc domain
gemini.google.com` xuất hiện sau mỗi task/lô; (c) sau khi bật mục "Tạo hình
ảnh", ảnh generate ra đúng ý hơn (không còn là câu trả lời text/ảnh sai).

⚠️ Lúc verify phát hiện `tests/_test_batch_clean_recover.py` ĐANG THẤT BẠI
(6/6 nhóm có ít nhất 1 assertion sai) — xác nhận qua truy vết code: nguồn gốc
nằm ở `_ensure_project_page_ready()`/`_ensure_flow_page()` (thêm ở 1 phiên làm
việc TRƯỚC, chưa commit, KHÔNG liên quan gì tới thay đổi trong entry này — bản
thân 2 hàm đó không được sửa ở đây). Test harness của file đó (viết trước khi
2 hàm này tồn tại) không mock đủ state cho chuỗi gọi mới — cần sửa RIÊNG,
ngoài phạm vi yêu cầu hôm nay, chỉ ghi nhận lại đây để không quên.

---

### 2026-09-12 (c) | `server/chrome_utils.py`, `server/worker.py` | FIX THẬT SỰ: bản vá (b) KHÔNG đủ — `driver_executable_path` cố định khiến `version_main` bị `undetected_chromedriver` HOÀN TOÀN BỎ QUA, vẫn crash y hệt "ChromeDriver only supports Chrome version 151"

User chạy lại sau bản vá (b), **vẫn lỗi y hệt**: `This version of ChromeDriver only supports
Chrome version 151, Current browser version is 153.0.8010.37`.

**Root cause thật — nằm ở TẦNG KHÁC, không phải bước dò version:** bản vá (b) đã fix ĐÚNG bước
dò version Chrome hiện tại (`_detect_chrome_version()` → 153), nhưng `_make_driver()` (`worker.py`)
gọi `uc.Chrome(driver_executable_path=cdp_path, version_main=chrome_ver)` với `cdp_path =
_detect_chromedriver() or None`. Trên Windows, `_detect_chromedriver()` trả về CỐ ĐỊNH
`CHROMEDRIVER_PATH` (mặc định `data/profiles/chromedriver/chromedriver.exe`) NẾU file đó tồn tại
— bất kể version của chính nó. Đọc thẳng source `undetected_chromedriver/patcher.py::Patcher.auto()`
xác nhận: khi `executable_path` KHÔNG rỗng (`self._custom_exe_path = True`), hàm chỉ kiểm tra file
đó đã được "patch" (chống bot-detection) hay chưa rồi **DÙNG NGUYÊN FILE** — nhánh tải/verify
version (dòng 154-179, nơi `version_main` thật sự được dùng) **KHÔNG BAO GIỜ CHẠY TỚI**. Nghĩa là:
nếu máy user có sẵn 1 file `chromedriver.exe` cũ (bản 151, từ lần cài Chrome trước khi auto-update)
tại đúng đường dẫn mặc định đó, `version_main=153` bị bỏ qua hoàn toàn — uc vẫn khởi động bằng
đúng driver 151 cũ, tái diễn y hệt lỗi ban đầu dù bước dò version đã đúng.

**Verify tại chỗ (không đoán):** đọc trực tiếp `venv/Lib/site-packages/undetected_chromedriver/
patcher.py::Patcher.auto()` — xác nhận đúng nhánh `if self._custom_exe_path: ispatched = ...; if
not ispatched: return self.patch_exe() else: return` đứng TRƯỚC hoàn toàn logic tải/verify version,
không tham chiếu `version_main` ở nhánh này.

**Fix:** `_chromedriver_major_version(path)` (mới, `chrome_utils.py`) — đọc MAJOR version của CHÍNH
file chromedriver (không phải Chrome browser), dùng lại đúng cơ chế `_win_file_version()` (đọc
FileVersion embedded trong PE resource, không chạy file — chromedriver.exe cũng có FileVersion
resource chuẩn như mọi .exe Windows khác). `_make_driver()` giờ, sau khi có cả `cdp_path` lẫn
`chrome_ver`, verify version của `cdp_path` — KHÔNG khớp `chrome_ver` (stale) thì bỏ qua nó
(`cdp_path = None`, kèm log warning rõ ràng) để `uc.Chrome()` rơi về nhánh `driver_executable_path=
None` — lúc đó `Patcher.auto()` mới thật sự chạy nhánh tải+patch driver ĐÚNG version qua
`version_main`, đúng như bản vá (b) dự định nhưng chưa từng phát huy tác dụng.

**Verify:** `py_compile`/`pyflakes` sạch. Test trực tiếp `_chromedriver_major_version()`: path
không tồn tại → `None` ngay lập tức (0.0001s, không treo); test sanity cơ chế đọc PE FileVersion
(dùng lại cho cả chrome.exe lẫn chromedriver.exe) qua `chrome.exe` thật trên máy dev → đọc đúng
`153`, khớp chính xác kết quả `_detect_chrome_version()` đã verify ở bản vá (b) — xác nhận cơ chế
đọc version hoạt động đúng, tái dùng an toàn cho chromedriver.exe. **CHƯA verify launch Chrome thật
với 1 file chromedriver.exe THẬT SỰ stale trên máy Windows** (môi trường dev không có sẵn file
chromedriver.exe nào để test trực tiếp bằng version thật) — cần user restart profile-32 lần nữa để
xác nhận: nếu log giờ xuất hiện dòng cảnh báo `chromedriver cục bộ "..." là bản 151, KHÔNG khớp
Chrome 153...` thì đúng root cause này, và Chrome cần khởi động thành công sau đó (uc tự tải driver
mới lần đầu có thể mất thêm vài giây do phải download).

---

### 2026-09-12 (b) | `server/chrome_utils.py` | FIX THẬT: `_detect_chrome_version()` đọc registry BLBeacon STALE — profile crash "ChromeDriver only supports Chrome version 151, Current browser version is 153" sau khi Chrome tự auto-update

User báo log crash thật: `[profile-32] [error] Worker fatal: Message: session not created: This
version of ChromeDriver only supports Chrome version 151. Current browser version is
153.0.8010.37`.

**Root cause:** `_detect_chrome_version()` (thêm 2026-07-20, xem CLAUDE.md §11.22) — trên Windows,
khi profile KHÔNG dùng Chrome Portable (chrome hệ thống mặc định), hàm CHỈ đọc registry
`HKCU/HKLM...\Google\Chrome\BLBeacon\version`. Registry này do CHÍNH Chrome tự ghi lại **mỗi lần
launch** — nhưng Chrome auto-update thay THẲNG file nhị phân trên đĩa NGAY LẬP TỨC (không cần
process cũ đóng), trong khi BLBeacon chỉ được ghi lại ở LẦN LAUNCH KẾ TIẾP. Nếu lần launch kế tiếp
đó chính là lần worker gọi `_detect_chrome_version()` để lấy `version_main` TRƯỚC KHI Chrome mới
kịp khởi động và tự cập nhật registry, giá trị đọc được vẫn là bản CŨ (151) dù file nhị phân trên
đĩa đã là bản MỚI (153) — `uc.Chrome(version_main=151)` ép dùng chromedriver khớp bản 151, nhưng
Chrome thật sự launch ra là 153 → crash ngay từ `session not created`.

**Verify trực tiếp trên máy test (không giả định):** registry BLBeacon đọc được **152** trong khi
Chrome hệ thống thật (`C:\Program Files\Google\Chrome\Application\chrome.exe`) là **153.0.8010.37**
— xác nhận đúng lớp bug staleness, không phải giả thuyết suông.

**Fix:** hàm mới `_win_resolve_chrome_exe()` — tìm đường dẫn `chrome.exe` THẬT sẽ được
chromedriver/uc launch bằng CÁCH THỨC chromedriver tự dùng khi `binary_location` không set: dò
theo thứ tự `LOCALAPPDATA` → `PROGRAMFILES` → `PROGRAMFILES(X86)` (đúng thứ tự chromedriver's
internal binary finder). `_detect_chrome_version()`'s nhánh Windows-không-Portable giờ ưu tiên đọc
THẲNG FileVersion của file `.exe` này qua `_win_file_version()` (đã có sẵn, dùng cho Chrome
Portable từ 2026-07-20 — không chạy file, không có rủi ro treo) — file trên đĩa LUÔN phản ánh đúng
bản sẽ chạy, loại bỏ hoàn toàn độ trễ của registry. Registry BLBeacon giữ lại làm fallback CUỐI
CÙNG (không xoá, chỉ hạ xuống hàng dự phòng) nếu không resolve được exe.

**⚠️ Bug THỨ 2 tự bắt được lúc verify (không phải giả thuyết) — registry "App Paths" KHÔNG đáng
tin cậy hơn BLBeacon:** bản nháp đầu tiên của `_win_resolve_chrome_exe()` ưu tiên đọc registry
`...\App Paths\chrome.exe` (cơ chế Windows chuẩn để resolve executable theo tên) TRƯỚC các đường
dẫn cố định — verify trực tiếp trên máy test lộ ra key này bị 1 Chrome Portable KHÁC (của tool
khác trên máy) ghi đè, trỏ sang 1 exe HOÀN TOÀN KHÁC (`AutoImage\...\chrome.exe`, version 136) —
KHÁC HẲN Chrome hệ thống thật sự được launch (153, tại `Program Files`). Registry "App Paths" đã bị
hạ xuống thành fallback CUỐI CÙNG (sau cả 3 đường dẫn cố định), không còn là nguồn ưu tiên.

**Verify:** `py_compile`/`pyflakes` sạch (`compileall -q server/`). Chạy trực tiếp
`_win_resolve_chrome_exe()`/`_detect_chrome_version('')` trên máy test THẬT — resolve đúng
`C:\Program Files\Google\Chrome\Application\chrome.exe`, đọc đúng `153.0.8010.37` → trả `153`,
KHỚP CHÍNH XÁC "Current browser version" trong crash log. Mô phỏng cả 2 lớp fallback (fixed-paths
miss → App Paths registry; resolve-exe hoàn toàn thất bại → BLBeacon registry) — cả 2 chạy đúng,
không crash. **CHƯA verify launch Chrome thật qua `uc.Chrome(version_main=153)`** — chỉ verify
được bước resolve version, chưa tự mở Chrome trong môi trường phát triển này (không có PyQt6/
Selenium runtime đầy đủ) — cần user tự Start lại profile-32 để xác nhận hết crash.

---

### 2026-09-12 — FIX THẬT: "bật tài khoản gemini tạo image/video hiện profile cái tắt liền"

User báo *"check lỗi gì bật tài khoản gemini tạo image/video hiện profile cái tắt liền"*.

**Root cause (điều tra qua `server/dispatcher.py` + backend `admin.py::pending_by_mode()`,
xác nhận bằng test qua DB THẬT — không đoán):** `pending_by_mode()` (backend, dùng bởi
`_fetch_pending_by_mode()`/`_profile_veo3_eligible()`/`_veo3_dispatcher_tick()` phía client_tool
để tự mở/đóng profile theo backlog thật, xem CLAUDE.md §11.9) hoàn toàn KHÔNG biết tới 3 cờ engine
cấp project `enable_veo`/`enable_gemini_image`/`enable_gemini_video` (thêm 2026-09-09, §11.50) —
`imageTotal`/`videoTotal` đếm MỌI task ảnh/video pending bất kể cờ nào, trong khi `heartbeat.py`'s
`engine_clause` THẬT chỉ giao task `gemini_image_selenium`/`gemini_video_selenium` cho project BẬT
đúng cờ tương ứng (mặc định TẮT, opt-in — và NanaBananaPro frontend cho 3 checkbox này CHƯA được
nối, nên gần như KHÔNG project nào bật được cờ này qua UI). Kèm 1 mismatch thứ 2: `pending_by_mode()`
vẫn dùng `p.status != 'paused'` (bản 2026-08-05) trong khi `heartbeat.py` đã đổi sang `p.status =
'active'` từ 2026-08-08 — project `draft`/`completed`/`archived` vẫn bị đếm là "còn backlog" dù
không bao giờ được giao.

Hệ quả 2 lớp: (1) `_veo3_dispatcher_tick()`'s ngân sách promote (`image_budget`/`video_budget`, từ
`imageTotal`/`videoTotal`) gộp CHUNG cho cả VEO3 (`api`/`dom`) lẫn `gemini_image`/`gemini_video`
(cả 2 force `task_mode='image_only'`/`'video_only'` nên bị xếp nhầm cùng nhóm) — 1 `gemini_image`
profile được mở Chrome chỉ vì có backlog ảnh VEO3-only không liên quan; (2)
`_auto_scale_veo3_tick()`'s `_profile_veo3_eligible()` cũng dùng nhầm `imageTotal`/`videoTotal`
(generic) để quyết định GIỮ hay ĐÓNG — nếu backlog thật khớp `enable_gemini_image` = 0 (rất phổ
biến, vì UI chưa có), tick kế tiếp (~10s) thấy "hết việc" rồi đóng ngay lập tức. Ghép 2 lớp lại
đúng nghĩa đen "bật cái tắt liền": mở Chrome nhờ ngân sách sai, rồi đóng ngay tick sau vì đánh giá
lại đúng backlog thật = 0.

**Fix:**
1. `backend/routes/admin.py::pending_by_mode()` — đổi `p.status != 'paused'` → `p.status = 'active'`
   (đồng bộ lại với `heartbeat.py`). `byMode`/`imageTotal`/`videoTotal`/`total` giờ mirror ĐÚNG
   `engine_clause` mặc định của `heartbeat.py` (machine_type='selenium_profile':
   `project_id IS NULL OR p.enable_veo=1`). Thêm 2 field MỚI `imageTotalGeminiImage`/
   `videoTotalGeminiVideo` — mirror ĐÚNG `engine_clause` riêng của `gemini_image_selenium`/
   `gemini_video_selenium`.
2. `client_tool/server/dispatcher.py::_profile_veo3_eligible()` — tra ĐÚNG field theo
   `worker_mode` (`gemini_image`→`imageTotalGeminiImage`, `gemini_video`→`videoTotalGeminiVideo`,
   còn lại giữ nguyên `imageTotal`/`videoTotal`/`total` cũ) — fallback về field generic nếu backend
   cũ chưa có field mới (KHÔNG regress).
3. `client_tool/server/dispatcher.py::_veo3_dispatcher_tick()` — tách RIÊNG 2 ngân sách
   `gemini_image_budget`/`gemini_video_budget` khỏi `image_budget`/`video_budget` (VEO3), cả lúc
   trừ phần các profile ĐANG CHẠY đã tiêu thụ lẫn lúc xếp hàng `waiting_pids` (`gemini_img_wait`/
   `gemini_vid_wait` riêng, tách theo `worker_mode` TRƯỚC khi tách theo `task_mode`). `all_wait`
   không đổi (gemini_image/gemini_video không bao giờ có `task_mode='all'`).

**Verify:** unit test cô lập (mock `pm.get`/`_start_worker`/`_fetch_pending_by_mode`) — 5 kịch bản
`_profile_veo3_eligible()` (gemini_image/gemini_video eligible ĐÚNG theo field riêng, KHÔNG bị ăn
theo backlog VEO3 không liên quan; VEO3 giữ nguyên hành vi cũ; fallback backend cũ) + 2 kịch bản
`_veo3_dispatcher_tick()` (backlog chỉ VEO3 → chỉ VEO3 được promote, KHÔNG gemini_image; ngược lại
backlog chỉ gemini_image → CHỈ gemini_image được promote, KHÔNG VEO3) — 11/11 pass. Backend qua
`app.test_client()` + **DB THẬT** (production, không mock) — dựng 3 project test (active+enable_veo
+enable_gemini_image, active+chỉ enable_veo, draft+enable_veo) + 10 task ảnh pending, so delta
trước/sau khi xoá sạch dữ liệu test: `imageTotal` (VEO) tăng đúng 5 (2 project active, project
draft bị loại đúng), `imageTotalGeminiImage` tăng đúng 2 (chỉnh đúng 1 project có cờ bật) — dữ liệu
test đã xoá sạch ngay sau, xác nhận không còn sót. Đồng thời phát hiện: baseline production HIỆN
TẠI đã có sẵn ~27 task ảnh pending thật sự eligible cho `gemini_image_selenium` (project nào đó đã
bật `enable_gemini_image=1` qua API/DB tay) — bằng chứng mismatch này KHÔNG PHẢI lý thuyết suông,
đang ảnh hưởng dữ liệu thật ngay lúc verify. `py_compile`/`pyflakes` sạch cả 2 file.

**CHƯA verify trên Chrome/quota thật** (không launch được PyQt6/Chrome/Selenium từ môi trường
này) — cần user tự bật 1 profile `gemini_image`/`gemini_video` (trên project ĐÃ bật đúng cờ
`enable_gemini_image`/`enable_gemini_video` qua DB/API tay, vì frontend NanaBananaPro CHƯA nối 3
checkbox này) rồi xác nhận KHÔNG còn hiện tượng "bật cái tắt liền". **Backend đang chạy (nếu có)
CẦN RESTART** để nhận 2 field mới từ `pending_by_mode()`.

---

### 2026-09-11 (b) — Project MỚI CHỈ còn tạo ở ngưỡng `max_project_media_items` (300) — project_url ghi nhớ theo EMAIL, dùng chung nhiều PC

User báo *"tôi thấy đang có lỗi create project quá nhiều giờ chỉ check trường hợp quá setting
trong client_tool như 300 media thì tạo project mới còn lại ko được tạo mới, kèm theo url ghi nhớ
project flow sẽ lưu theo email, để có truy cập bất cứ profile ở nhiều PC khác nhau vẫn dùng url
theo cùng user gmail tránh tạo quá nhiều project không cần thiết"*.

**Root cause "tạo project quá nhiều":** có **4 nơi** trong `worker.py` có thể bấm "New project" —
(1)/(2) `_ensure_flow_page()`/`_ensure_flow_project()` (nhánh fallback khi `_return_to_saved_
project()` thất bại), (3) `_rotate_project_if_full()` (ngưỡng 300 media — ĐÚNG Ý user, giữ
nguyên), (4) `_handle_task_error()`'s escalation gọi `_reset_flow_project()` sau
`refresh_count_before_new_project` (mặc định 5) lần refresh liên tiếp không hết lỗi. (1)/(2)/(4)
ĐỀU tạo project mới chỉ vì 1 lần KHÔNG VÀO LẠI ĐƯỢC project cũ — nhưng lỗi liên tục kiểu này RẤT
THƯỜNG do Google chặn/throttle ở TẦNG TÀI KHOẢN (`PUBLIC_ERROR_UNUSUAL_ACTIVITY`/`RPC_ERROR_CODE_8`
— xem entry (a) cùng ngày phía dưới, bắt được CHÍNH TRONG LÚC điều tra report này), KHÔNG phải
project đã hỏng — tạo project mới không sửa được gì (tài khoản vẫn bị chặn), chỉ tạo thêm project
không cần thiết, đúng triệu chứng user báo.

**Fix phần 1 — thu hẹp về ĐÚNG 2 trường hợp hợp lệ để tạo mới:**
1. `_rotate_project_if_full()` — GIỮ NGUYÊN, không đổi (ngưỡng 300 media, chủ đích).
2. Bootstrap/recovery — hàm MỚI `_try_reuse_or_bootstrap_project(self)` (gọi SAU
   `_return_to_saved_project()` thất bại, từ cả 2 call site (1)/(2)) — phân nhánh theo
   `profile['project_url']` ĐÃ TỪNG có hay chưa:
   - **ĐÃ TỪNG có** (tức có project riêng nhưng giờ không vào lại được) → trả `False` NGAY, **KHÔNG
     tạo project mới**, chỉ log lỗi rõ ràng ("có thể do Google đang chặn/throttle tài khoản tạm
     thời") — batch/task tự fail + retry ở heartbeat sau.
   - **CHƯA TỪNG có** (profile mới) → thử dùng lại project của CÙNG tài khoản qua
     `_lookup_shared_project_url_by_email()` (xem phần 2) TRƯỚC; không có mới bấm "New project"
     (nhánh DUY NHẤT còn hợp lệ để bootstrap thật).
3. `_handle_task_error()`'s escalation (nhánh (4)) — bỏ hẳn lời gọi `_reset_flow_project()` (ĐÃ
   XOÁ KHỎI CLASS, dead code), thay bằng gọi `_return_to_saved_project()` (chỉ cố quay lại project
   đã lưu, không tạo mới) — giữ NGUYÊN thứ tự/điều kiện sleep-escalation gốc (kiểm tra ngủ TRƯỚC,
   rồi mới gemini-skip, rồi mới thử recovery — đổi thứ tự sẽ lỡ mất lần sleep-check cho
   `worker_mode` gemini_video/gemini_image, đã tự bắt + sửa lúc viết).

**Fix phần 2 — "url ghi nhớ theo email, dùng chung nhiều PC"** (xem phần backend tương ứng ở
`ToolSub/CHANGELOG.md` 2026-09-11, bảng GLOBAL `flow_account_projects`):
- `server/managers.py::pm.get_shared_project_url(account_email)` — `GET /api/worker_profiles/
  project_url_by_email?email=` (best-effort, `quiet=True` — lỗi mạng/chưa có gì remembered → `''`,
  KHÔNG raise, KHÔNG chặn task).
- `server/worker.py::_lookup_shared_project_url_by_email(self)` — đọc `account_email` của CHÍNH
  profile, gọi `pm.get_shared_project_url()`. Không có email → `''` ngay, không gọi mạng.
- `_persist_project_url()` (điểm DUY NHẤT ghi `project_url`, §11.40) KHÔNG cần sửa — mỗi lần ghi
  đều đi qua `pm.update(project_url=...)`, và backend TỰ upsert `flow_account_projects` theo email
  của profile đó (xem ToolSub side) — client_tool không cần gọi thêm API nào để "ghi nhớ".

Verify: 2 test MỚI (`tests/_test_project_reuse_by_email.py`, 5 nhóm — 3 nhánh
`_try_reuse_or_bootstrap_project()` + xác nhận `_reset_flow_project` đã xoá khỏi class +
`_lookup_shared_project_url_by_email()` xử lý đúng thiếu-email/lỗi-mạng) + cập nhật
`tests/_test_return_to_saved_project.py` (test #6 đổi kỳ vọng từ "có tạo mới" → "KHÔNG tạo mới"
khi ĐÃ TỪNG có project riêng; test #6b/7/8 chuyển sang dùng `saved=''` để vẫn verify được đường
bootstrap-thật hợp lệ) + `tests/_test_timewindow_disabled.py` (stub `_return_to_saved_project`
thay `_reset_flow_project`, xác nhận hàm này chỉ được thử ĐÚNG 1 lần trước khi ngủ). Toàn bộ
regression test sẵn có (`_test_batch_escalation.py` 8/8) chạy lại không lỗi. `py_compile`/
`pyflakes` sạch. Nhân lúc verify `_test_batch_clean_recover.py`, phát hiện + fix 1 bug KHÔNG LIÊN
QUAN (stub `interstitial()` thiếu tham số `timeout` khớp chữ ký thật `_click_create_with_flow_if_
present(timeout=6)`, gây crash `TypeError` ngay khi chạy test — đã sửa để test CHẠY ĐƯỢC; phần
mismatch LOGIC còn lại của test đó (reconcile/interstitial-click counts ở vài kịch bản) là nợ kỹ
thuật CÓ SẴN TỪ TRƯỚC, không liên quan gì tới thay đổi lần này, CHƯA sửa — ngoài phạm vi request).

⚠️ **Backend (`ToolSub`) cần restart + migration mới** (`flow_account_projects` table,
`GET /api/worker_profiles/project_url_by_email`) mới có API để tra. Client_tool đang chạy cần
restart để nạp code mới. **CHƯA verify trên Chrome/quota thật** (chỉ verify qua mock-based test +
backend end-to-end qua DB thật, không gọi browser thật).

---

### 2026-09-11 — Điều tra "imageToVideo lỗi upload" + FIX THẬT: `extract_rpc_error()` bỏ sót shape "mã số trần" (`[8]`, `[5]`…), lộ ra trong lúc test

User báo *"test imagetoVideo đang bị lỗi upload image có thể flow đã đỗi cơ chế"*. Mở lại
client_tool + backend (`server_gemini_flow.py`) cục bộ, attach THẬT vào Chrome của 2 profile
production khác nhau (27 `huavantien84@gmail.com`, 21 `aikhanh251295@gmail.com`) để chạy test
thật, không chỉ đọc log:

1. `tests/_test_ingredientToVideo.py` (đường `aisandbox` CŨ) → **401 UNAUTHENTICATED** ngay ở
   bước `uploadImage` dù bearer token vừa capture sống từ chính 1 request `aisandbox` THẬT do
   trang tự bắn. KHÔNG phải regression mới — khớp đúng lý do đường này đã bị "cất" hẳn trong
   sản xuất từ 2026-09-04 (§11.46 CLAUDE.md, `generate_via_batchexecute=1` mặc định).
2. Đường CHÍNH (`batchexecute`, đang chạy thật trong sản xuất) — `tests/_test_uploadImage_new.py`
   với profile 27: lỗi `[8]` (mã số gRPC trần, KHÔNG kèm text chi tiết `PUBLIC_ERROR_*`) — lặp lại
   y hệt ở lần thử thứ 2 (session + reCAPTCHA hoàn toàn mới mỗi lần). **Cùng bài test với profile
   21 (tài khoản KHÁC) → THÀNH CÔNG HOÀN TOÀN** (upload đúng, verify uuid có trong listing
   `Zzl0ze`) — xác nhận cơ chế batchexecute KHÔNG hỏng, lỗi `[8]` là chặn/hạn chế ở TẦNG TÀI
   KHOẢN của riêng `huavantien84@gmail.com` lúc đó. Gọi tiếp `MZZa6b` (ingredientToVideo) trực
   tiếp bằng đúng uuid ảnh vừa upload thành công của profile 21 → **thành công** (`workflowId`/
   `mediaId` hợp lệ) — xác nhận cả bước tạo video cũng không hề hỏng.

**Kết luận cho user:** Flow KHÔNG đổi cơ chế lần này (mechanism vẫn như §11.44-11.46 đã ghi) —
triệu chứng "lỗi upload" quan sát được là do TÀI KHOẢN `huavantien84@gmail.com` đang bị Google
hạn chế tạm thời (cùng họ với `PUBLIC_ERROR_UNUSUAL_ACTIVITY`/`QUOTA_REACHED` đã biết, chỉ khác là
lần này Google trả về dạng RÚT GỌN không có text chi tiết).

**Bug thật phát lộ trong lúc test** (không phải lý do chính của triệu chứng user báo, nhưng là 1
gap thật trong code): `server/flow_be.py::extract_rpc_error()` CHỈ nhận diện được lỗi RPC khi
entry `wrb.fr` có text chi tiết (`rpc.ErrorInfo`/`PUBLIC_ERROR_*`) — shape "mã số trần" (`[8]`,
`[5]`, …, không kèm gì khác) bị coi NHẦM là "payload null bình thường" (giống case `as29s` gọi
trên ảnh chưa generate, hợp lệ trả null) → lỗi thật bị log thành "payload rỗng/không có uuid"
(mơ hồ) thay vì hiện đúng mã lỗi. Verify trực tiếp bằng nguyên văn response Google trả về lúc
test (`[8]` cho `maseQ`, `[5]`=NOT_FOUND cho `MZZa6b` khi tự gửi nhầm uuid ảnh tham chiếu của
project khác) — cả 2 đều bị nuốt im lặng ở code cũ.

**Fix:** `extract_rpc_error()` viết lại điều kiện nhận diện — kiểm tra `entry[5]` có phải `None`
hay không (đây mới là tín hiệu PHÂN BIỆT đúng "có lỗi" vs "payload null hợp lệ", không phải có
text `rpc.ErrorInfo` hay không) — `entry[5] is not None` LUÔN là lỗi, có text chi tiết thì trả
đúng mã (`PUBLIC_ERROR_*`/tên IN_HOA khác), không có text thì trả `RPC_ERROR_CODE_{n}` (mã số
thô) thay vì `''`. CHỦ Ý KHÔNG đưa mã số thô vào `ACCOUNT_BLOCK_REASONS` — đã tự tái hiện 2 mã số
khác nhau (`8` nghi chặn tài khoản, `5`=NOT_FOUND do lỗi dùng sai uuid của chính test) nên 1 con
số không đủ để khẳng định là chặn tài khoản, chỉ đảm bảo KHÔNG còn bị nuốt im lặng.

Verify: unit test trực tiếp `extract_rpc_error()` (6 case, gồm cả 2 shape lỗi bắt được thật hôm
nay + benign-null + case cũ đã biết) + `_batchexecute_parse()` full round-trip với ĐÚNG raw text
Google trả về lúc test (`maseQ` lỗi `[8]`) → raise đúng `BatchExecuteError(reason='RPC_ERROR_CODE_8',
is_account_block=False)`. Rà lại TẤT CẢ 6 call site của `_batchexecute_parse()` trong `worker.py`
— mọi nơi đều đã bọc try/except Exception sẵn có coi exception và `None` cũ là CÙNG 1 outcome
(`return ''`/`None`/`continue`), nên đổi từ "trả None" sang "raise BatchExecuteError" không đổi
HÀNH VI chức năng ở bất kỳ nơi nào, chỉ cải thiện chất lượng log (`_be_note_fail()` giờ thấy đúng
mã lỗi thay vì "payload rỗng"). `py_compile`/`pyflakes` sạch. Chạy lại 2 bộ test hồi quy sẵn có
(`_test_batch_escalation.py` 8/8, `_test_timewindow_disabled.py` 5/5) — không có regression.

**CHƯA làm:** không sửa `_dom_upload_images()`/DOM selectors gì (không có bằng chứng nào cho
thấy DOM đổi lần này — toàn bộ điều tra đều chỉ qua API, không chạm DOM production). Chưa xác
nhận được account `huavantien84@gmail.com` tự hết hạn chế sau bao lâu (không retry thêm để tránh
làm nặng thêm tình trạng chặn của account đó).

---

### 2026-09-09/10 — THÊM `worker_mode='gemini_image'` — engine THỨ 2 cho task ẢNH (mirror `gemini_video`)

`server/worker.py`, `server/dispatcher.py`, `server/routes.py`, `server/managers.py`,
`gui/profile_dialog.py`, `gui/pages/profiles_page.py`, `tests/_test_gemini_image_task.py` (MỚI)
— cùng lượt với backend `ToolSub` gốc (xem CHANGELOG.md ở đó): migration
`projects.enable_veo`/`enable_gemini_image`/`enable_gemini_video`, `backend/routes/projects.py`
CRUD, `backend/routes/heartbeat.py` (machine_type `gemini_image_selenium`/`gemini_video_selenium`
+ `engine_clause` gate theo cờ project), `backend/routes/worker_profiles.py` (`_VALID_WORKER_MODES`).

Theo yêu cầu user *"tích hợp vào project banana và quiz có thể theo kiểu multi check, auto check
VEO khi tạo project và nếu check Gemini thì chạy song song cả 2, gemini thì tách ra check
image/video"* (Quiz để sau, "chạy song song" = VEO và Gemini đều là "machine" cạnh tranh CÙNG hàng
đợi `tasks_media_flow` qua `FOR UPDATE SKIP LOCKED` — task đã bị 1 engine nhận thì engine kia
không nhận trùng; checkbox ở cấp PROJECT, không phải per-task).

`_run_task_gemini_image()`/`_upload_image_result()` (server/worker.py, đặt ngay sau
`_run_task_gemini_video()`/`_upload_video_result()`, mirror 1:1 shape) — xử lý task ẢNH
(`textToImage`/`imageToImage`) bằng Gemini chat THƯỜNG (KHÔNG có `_gemini_video_enter_mode()`/
`_gemini_video_set_ratio()` — plain chat không có ratio selector, đã verify qua
`tests/_test_geminiImageToImage.py` ở phiên trước). Luôn bắt đầu chat MỚI mỗi task (tránh lẫn kết
quả), tải `source_media` (nếu có, path TƯƠNG ĐỐI server trả về → tự ghép `FLOW_SERVER` — cùng fix
đã áp dụng cho `_run_task_gemini_video()`), `_gemini_attach_file()` từng ảnh, gõ+gửi prompt,
`_gemini_wait_response()` trả `{'text','images'}` (canvas-based extraction đã proven), decode
base64 từng ảnh → `_upload_image_result()` (multipart `files=[...]`, backend
`upload_task_result()` đã hỗ trợ SẴN nhiều file/lần gọi) → `_handle_task_success()`. Không có
ảnh nào trả về (từ chối/hỏi lại) → lỗi rõ ràng, KHÔNG tính là thành công.

Wiring: `__init__`'s worker_mode validation + `_make_driver()`'s `load_ext` exclusion đã có sẵn
từ phiên trước (đã kiểm tra lại, đúng). Mới thêm lần này: `_run_task()` dispatcher
(`elif worker_mode=='gemini_image': return self._run_task_gemini_image(task)`); `_heartbeat()`'s
`machineType` đổi từ hardcode `'selenium_profile'` sang lookup theo `worker_mode`
(`gemini_video`→`gemini_video_selenium`, `gemini_image`→`gemini_image_selenium`, else giữ
`selenium_profile`); `run()`'s nhánh setup (`is_gemini_video or is_gemini_image` — navigate thẳng
`GEMINI_URL` qua `_ensure_google_login()`, bỏ qua `_ensure_flow_page()`/token capture); nhánh
"reset project" trong `_handle_task_error()` (bỏ qua cho CẢ 2 mode, không phải chỉ `gemini_video` —
mỗi task đã tự vào chat mới, "reset" vô nghĩa).

`server/dispatcher.py` — `_VEO3_LIKE_WORKER_MODES` thêm `'gemini_image'` (luôn
`task_mode='image_only'`, ép ở `ProfileDialog.get_data()` — rơi đúng vào ngân sách `image_budget`
của `_veo3_dispatcher_tick()`, cạnh tranh CÙNG pool ảnh với VEO3 `task_mode='image_only'`, KHÔNG
cần sửa gì thêm ở logic cấp ngân sách — hàm đó thuần theo `task_mode`, không theo `worker_mode`).
Hằng số mới `_NO_PROJECT_WORKER_MODES = ('gemini_video', 'gemini_image')` thay 2 chỗ check
`!= 'gemini_video'` lặp lại (project_url không bắt buộc cho 2 mode này).

`server/routes.py`'s `start_worker()` route — cùng đổi `!= 'gemini_video'` → `not in
('gemini_video', 'gemini_image')`.

`server/managers.py::pm.create()` — thêm `'gemini_image'` vào whitelist `worker_mode` NGAY TỪ
ĐẦU (tránh lặp lại đúng bug "quên thêm loại mới vào whitelist riêng của managers.py" đã từng xảy
ra thật với `gemini_video`, xem entry 2026-08-08 — whitelist ở `pm.create()` và
`backend/routes/worker_profiles.py` là 2 whitelist RIÊNG, không dùng chung).

GUI: `ProfileDialog` thêm combo option "🖼️ Gemini — Tạo Ảnh (mới, thay VEO3)" (`gemini_image`) —
mirror `gemini_video` ở cả 4 chỗ (`_type_fields`, `_accept()` validate, `get_data()` force
`task_mode='image_only'`), tái dùng NGUYÊN 2 field timeout + `_max_concurrent` (không có field
mới nào). `ProfilesPage` — badge riêng "🖼️ Gemini Ảnh" (`_ENGINE_BADGE`), sub-text "Tạo ảnh qua
chat", `can_start` không đòi `project_url`.

**Verify:**
- `py_compile`/`pyflakes` sạch toàn bộ file sửa (backend + client_tool).
- Backend dispatch gating: test THẬT qua `app.test_client()` + DB THẬT (project + 2 task
  ảnh/video, 3 kịch bản — `gemini_image_selenium` chỉ nhận đúng task ảnh của project có
  `enable_gemini_image=1`; `gemini_video_selenium` chỉ nhận task video của project có
  `enable_gemini_video=1`; tắt `enable_gemini_image` → task ảnh không còn được giao nữa) — cả 3
  PASS, dữ liệu test đã xoá sạch.
- `_run_task_gemini_image()`/`_upload_image_result()`: test mock-based (`tests/_test_gemini_
  image_task.py`, mirror pattern `_test_batch_escalation.py` — instance `SeleniumFlowWorker`
  thật, chỉ stub Chrome/HTTP) — 5 kịch bản: thành công không ref/có ref (xác nhận path tương đối
  được ghép `FLOW_SERVER`, file tạm tự dọn trong `finally`, upload đúng số lượng ảnh Gemini trả
  về), Gemini không trả ảnh → lỗi đúng message, upload lỗi → lan đúng message, đăng nhập thất bại
  → dừng sớm không chạm Gemini. Cả 5 PASS.

**CHƯA verify:**
- Trên browser/quota THẬT (môi trường phát triển không chạy được Chrome/Selenium) — cần user tự
  tạo 1 profile "🖼️ Gemini — Tạo Ảnh", gán vào 1 project NanoBananaPro có `enable_gemini_image=1`,
  Start, xác nhận nhận đúng task ảnh và ảnh generate ra hợp lệ.
- NanoBananaPro frontend (`ProjectManager.jsx`'s `ProjectModal`) — 3 checkbox VEO/Gemini-Image/
  Gemini-Video wired vào `enable_veo`/`enable_gemini_image`/`enable_gemini_video` — CHƯA làm,
  backend CRUD đã sẵn sàng nhận field này qua API nhưng chưa có UI nào gọi tới.
- `gui/pages/profiles_page.py` — badge cột Mode CHƯA verify hiển thị thật trên GUI (chỉ verify
  qua code review, không launch app đầy đủ).
- Backend đang chạy live (nếu có) CẦN RESTART để nạp `projects.enable_*`/`heartbeat.py`'s
  `engine_clause`/`worker_profiles.py`'s whitelist mới.

---

### 2026-09-06 — FIX: ChatGPT báo lỗi SAI BẢN CHẤT khi profile bị đăng xuất (+ heartbeat crash khi response rỗng)

`server/worker.py`, `tests/_diag_chatgpt_dom.py` (MỚI)

User báo *"chatgpt tạo ảnh đang lỗi"*. Log sản xuất (profile 25 `aikhanh251295`,
2026-09-06 00:56:39 và 00:59:53) chỉ nói đúng 1 câu:

    [chatgpt] Task lỗi: Không tìm thấy ô nhập liệu ChatGPT (#prompt-textarea)

→ dẫn thẳng vào hướng "OpenAI đổi DOM" (cùng lớp §11.44 khi Google đổi hẳn app Flow).
**Sai hướng.** Nguyên nhân THẬT: **profile đã bị ĐĂNG XUẤT khỏi chatgpt.com.**

#### Bằng chứng (2 nguồn độc lập, không đoán)

- **Cookie** (`Storage.getCookies` trên chính profile đó): 7 cookie `chatgpt.com` nhưng
  TOÀN BỘ là ẩn danh/CDN (`oai-did`, `__cf_bm`, `_cfuvid`, `oai-sc`, `__cflb`,
  `oai-mweb-*`) — **KHÔNG còn `__Secure-next-auth.session-token`**.
- **DOM** (`tests/_diag_chatgpt_dom.py`): trang trả về composer bản CHƯA đăng nhập
  (`#mobile-composer-prompt`, class `wm-*`), nút ghi thẳng *"Create image. Log in to
  use."*, body mở đầu *"Log in to get answers based on saved chats, plus create images
  and upload files."* — mọi selector worker đang dùng đều `n=0`.

**Batch-clean KHÔNG phải thủ phạm** (đã kiểm tra kỹ vì nghi trước tiên): log ghi
`0/54 cookie` bị xoá, và `_batch_clean_should_delete_cookie()` chỉ xoá domain
`labs.google`, `_cdp_clear_labs_site_data()` scope đúng origin labs.google. Session
ChatGPT hết hạn/bị đăng xuất theo đường khác.

⚠️ **ĐÂY LÀ LẦN THỨ 2 mất thời gian vì đúng cái bẫy này** — lần đầu 2026-08-01
("user đăng nhập nhầm trình duyệt thường dùng thay vì profile automation", §11.25),
bằng chứng khi đó cũng là CHÍNH câu *"Log in to get answers…"*.

#### Sửa

1. **`_chatgpt_logged_out_reason()` / `_chatgpt_assert_logged_in()`** — phát hiện tường
   minh, ưu tiên tín hiệu CẤU TRÚC (`[commandfor*="auth-dialog"]`,
   `button[aria-label*="Log in to use"]`, `[data-testid="login-button"]`) rồi mới tới
   composer bản chưa-đăng-nhập, cuối cùng mới tới text. Best-effort: JS lỗi → coi như ổn,
   KHÔNG chặn nhầm task.
2. **Gọi ở 2 điểm:** đầu `_run_task_chatgpt()` ngay sau `driver.get()` (chặn SỚM — trước
   đây lỗi chỉ lộ ra sau khi đã tải + đính kèm hết ảnh), và lúc khởi động
   `_run_chatgpt_loop()` (trước đây LUÔN log `"ChatGPT mode — sẵn sàng nhận prompt"` kể
   cả khi đã đăng xuất; giờ log `error` nói rõ, vẫn chạy tiếp phòng user đăng nhập giữa chừng).
3. **Thông báo khi composer thật sự không tìm thấy** giờ phân biệt được 2 ca: chưa đăng
   nhập (raise thông báo riêng) vs đã đăng nhập nhưng DOM đổi (chỉ thẳng sang
   `tests/_diag_chatgpt_dom.py`).
4. **KHÔNG tự đăng nhập giúp** (khác `_ensure_google_login()` của Google): OpenAI có hệ
   đăng nhập riêng, thường kèm captcha/2FA — tự động hoá không đáng tin.

#### Bug thứ 2 (độc lập, thấy trong cùng log)

    Heartbeat (chatgpt) error: 'NoneType' object has no attribute 'get'

`_req()` trả `r.json()`; body `null` từ server/proxy cho ra `None` ⇒ `.get()` nổ. Thêm
`or {}` cho CẢ 3 heartbeat (`_heartbeat_gemini`, `_heartbeat_chatgpt`, VEO3) — cùng 1 lớp
lỗi, sửa 1 thể cho khỏi lệch.

#### Công cụ mới

`tests/_diag_chatgpt_dom.py` — **GIỮ LẠI**: attach vào Chrome đang mở (login browser,
port `9300 + profile_id`), KHÔNG gửi tin nhắn nào, dump trạng thái đăng nhập + tình trạng
TỪNG selector worker đang dùng + ứng viên thay thế (contenteditable/textarea/input file/
mọi `data-testid`). Chạy cái này TRƯỚC khi nghi DOM đổi.

#### Verify

- Chạy `_chatgpt_logged_out_reason()` (hàm production thật) trên ĐÚNG trang chatgpt.com
  đang mở của profile 25 → trả `'nút/hộp thoại đăng nhập có mặt'`, `_chatgpt_assert_logged_in()`
  raise đúng thông báo. `py_compile`/`pyflakes` sạch.
- ⚠️ **CHƯA verify được DOM khi ĐÃ đăng nhập** — không tự đăng nhập hộ được. Cookie
  `oai-mweb-route-desktop`/`oai-mweb-origin` cho thấy OpenAI đang route profile này sang
  bản **mweb**; nếu bản đó giữ nguyên sau khi đăng nhập thì `#prompt-textarea` +
  `[data-testid="send-button"]` + `[data-testid^="conversation-turn-"]` **có thể vẫn cần
  cập nhật**. Sau khi user đăng nhập → chạy lại `tests/_diag_chatgpt_dom.py` rồi mới kết luận.

⚠️ Trong lúc điều tra đã **tạm đặt profile 25 `enabled=0`** (tránh client_tool đang chạy
tranh chấp Chrome — đúng pattern §11.44) và mở sẵn login browser. **Cần bật lại `enabled=1`
sau khi đăng nhập xong.**

---

### 2026-09-06 — Gemini chat: GỬI ẨN qua RPC `StreamGenerate` (không gõ DOM), đọc kết quả vẫn bằng DOM

`server/gemini_be.py` (MỚI), `server/worker.py`, `server/config.py`, `server/local_settings.py`,
`gui/pages/settings_page.py`, `tests/_gemini_rpc_capture.py` (MỚI),
`tests/_test_gemini_rpc_send.py` (MỚI), `tests/_gemini_rpc_capture/` (MỚI)

Yêu cầu user: *"vào trang gemini đọc network để truyền ẩn như VEO qua API khi mở giao diện
https://gemini.google.com/"* — tức KHÔNG dùng API key chính thức, mà mở trang bằng profile đã
đăng nhập, đọc Network lấy endpoint nội bộ + token rồi bắn thẳng request. Làm rõ tiếp:
*"api chỉ cần gửi nội dung còn lắng nghe đọc kết quả giống DOM"* → chỉ thay bước **GỬI**;
đọc kết quả giữ nguyên `_gemini_wait_response()` (DOM reader sẵn có, không sửa 1 dòng).

**Mirror đúng phương pháp đã thành công cho Flow** (§11.45/§11.46): capture request THẬT rồi
replay — KHÔNG suy shape từ bundle minify.

#### Endpoint + shape (capture thật, `tests/_gemini_rpc_capture.py`)

    POST /_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate
         ?bl=<boq>&f.sid=<sid>&hl=en&_reqid=<rand>&rt=c
    body: f.req=<url-encoded [null,"<inner 99 ô>"]>&at=<xsrf>&

`inner[0]`=prompt · `inner[1]`=lang · `inner[2]`=id hội thoại (chat mới = `""`) ·
`inner[3]`=token BotGuard · `inner[4]`=id 32-hex mỗi request. 94 ô còn lại là cờ tính năng —
giữ nguyên từ template.

**3 phát hiện quyết định (verify trên tài khoản THẬT, không đoán):**

1. **KHÔNG cần token BotGuard.** `window.botguard` CÓ tồn tại trong trang (blob `!…` ~1.6KB
   chỉ xuất hiện trong chính request gửi, không có ở request nào lúc load) — nhưng gửi
   `inner[3]=null` vẫn **HTTP 200, tạo hội thoại thật, Gemini trả lời đúng**. Khác hẳn Flow
   (`batchexecute` VẪN cần reCAPTCHA ở `clientContext[10][0]`).
2. **Token đọc THẲNG từ `window.WIZ_global_data`** (`SNlM0e`/`cfb2h`/`FdrFJe`) — không phải
   chờ trang tự bắn 1 request rồi trích từ URL như `flow_be` phải làm, nên **không tốn
   `driver.refresh()` ~15s** nào.
3. ⚠️ **URL hội thoại BỎ tiền tố `c_`.** Mở `/app/c_<id>` ra trang TRỐNG (Angular không nhận
   route — DOM reader thấy 0 `structured-content-container` rồi timeout 120s); `/app/<id>`
   mới render đúng. Mất 2 lượt điều tra mới ra vì URL bar trông "hợp lệ" ở cả 2 trường hợp.

#### Cài đặt

- **`server/gemini_be.py`** — `STREAM_GENERATE_TEMPLATE` (99 ô, 6 ô runtime đã trung hoà),
  `build_f_req()`, `parse_stream_generate()` (bóc chunk length-prefixed → `conv`/`text`),
  `conversation_slug()`/`conversation_url()`. Vai trò song song `flow_be.py`.
- **`worker.py`** — nhóm `_gemini_rpc_*`: `_gemini_rpc_session()` · `_gemini_rpc_kickoff()` /
  `_gemini_rpc_poll()` · `_gemini_rpc_open_conversation()` · `_gemini_send_via_rpc()` (wrapper
  blocking cho 1-tab) · `_gemini_rpc_enabled()`.
  ⚠️ **Kiến trúc kickoff+poll, KHÔNG blocking** — `fetch` chỉ resolve khi Gemini sinh XONG
  (vài chục giây); dùng `execute_async_script` sẽ **đóng băng cả vòng round-robin nhiều tab**
  (§11.24). Cùng pattern `_gemini_attach_file_kickoff`/`_poll` sẵn có.
- **Nối vào 2 luồng:** `_run_task_gemini()` (1-tab) và `_gemini_slot_step()` (round-robin —
  state mới `'rpc_sending'`, poll nhường lượt cho tab khác đúng nhịp `gemini_tab_switch_interval`).
- **Setting `gemini_send_via_rpc`** (mặc định **1**, trang Cài đặt). Đặt `0` để gõ DOM như cũ.

#### ⚠️ CÓ FILE ĐÍNH KÈM → LUÔN dùng DOM

Upload của Gemini đi qua endpoint resumable riêng (`push.clients6.google.com/upload/…`) mà
lần capture này **CHƯA lấy shape**. `_gemini_rpc_enabled(has_attachments=True)` trả `False`,
rơi về `_gemini_attach_file()` (`send_keys` native, đã proven). Ảnh hưởng: `video_breakdown`
và mọi request storyboard có ảnh vẫn chạy đường DOM như trước.

**Hỏng ở BẤT KỲ bước nào cũng tự rơi về DOM trong CÙNG task** — không mất task.

#### Vì sao đáng làm

Gõ prompt ~29K ký tự vào Quill/ProseMirror là nguồn bug dai dẳng nhất của luồng Gemini (root
CLAUDE.md §11.5: `insertText` không áp hết, nút Gửi kẹt `disabled`, retry 3 lần, có lúc gõ
trúng thẻ `<p>` không focus được — §11.38). Bắn RPC thì prompt chỉ là 1 chuỗi trong body.

#### Verify — THẬT, trên tài khoản thật

- **`tests/_test_gemini_rpc_send.py` (gọi ĐÚNG hàm production): 6/6 PASS** với prompt
  **29.096 ký tự** — gửi ẩn + nhận + mở hội thoại **9.9s**; `_gemini_wait_response()` (DOM
  reader KHÔNG sửa gì) đọc ra **3.6s**; Gemini trả lời đúng câu hỏi đặt ở CUỐI prompt
  (`"KETQUA: 12 cộng 30 bằng 42."` → xác nhận đọc hết chuỗi, không phải chỉ nhận phần đầu).
  2 ca gate cũng đúng: có đính kèm → không dùng RPC, không đính kèm → dùng RPC.
- `parse_stream_generate()` verify offline trên response THẬT đã bắt được (biết trước đáp án
  `"2 + 2 = 4."`) — lấy đúng text + đủ `(c_,r_,rc_)`.
- `build_f_req()` giữ đúng 99 ô, chỉ đổi ô cần đổi, `inner[4]` sinh mới mỗi lần.
- `py_compile`/`pyflakes` sạch 5 file.

#### Bẫy đã dính trong lúc làm (ghi lại để khỏi mất thời gian lần sau)

1. **Chrome "sập" đều đặn sau ~40-50s** ở mọi bước khác nhau → tưởng crash do code. Thực ra
   **client_tool đang chạy** tự đóng Chrome của profile `enabled=1`. Dùng profile `enabled=0`
   để điều tra là hết.
2. **Probe báo "không có câu trả lời" oan** — regex của chính probe sai (response dùng dấu
   nháy escape `\"`). Đừng regex trên chuỗi chunk thô: parse đúng 2 lớp JSON.
3. **DOM reader bỏ qua câu trả lời ngắn** — `_gemini_response_if_ready()` đòi `len(text) > 5`,
   nên prompt "chỉ trả lời 1 từ" luôn timeout dù Gemini đã trả lời.

#### CHƯA làm / giới hạn

- Chưa capture endpoint upload → đính kèm vẫn dùng DOM (xem trên).
- Chưa chạy qua **hàng đợi heartbeat thật** end-to-end (verify gọi trực tiếp hàm production).
- Chưa test nhánh round-robin `'rpc_sending'` trên browser thật (mới verify nhánh 1-tab).
- `STREAM_GENERATE_TEMPLATE` gắn với build hiện tại (`boq_assistant-bard-web-server_20260904.05_p0`);
  Google đổi build → chạy lại `tests/_gemini_rpc_capture.py` rồi thay mảng.

⚠️ **client_tool đang chạy CẦN RESTART** để nạp `gemini_be.py` + setting mới.

---

### 2026-09-05 (b) | server/media_upload_cache.py, server/worker.py | ♻️ FIX: cùng project vẫn upload lại ảnh tham chiếu nhiều lần

User báo: *"task image tham chiếu và check id project có khớp với project hiện tại không để tận dụng không
upload ảnh tham chiếu mới giờ đã lỗi khi flow.google upload phiên bản mới... cùng project nhưng upload lại
ảnh tham chiếu nhiều lần"*.

**KHÔNG phải do flow.google đổi phiên bản** (đã loại trừ: `_extract_project_id()` vẫn trích đúng uuid trên
URL `flow.google.com/project/{uuid}` — test trên `project_url` thật của 6 profile; số call site cache không
đổi qua mọi commit đợt viết lại batchexecute; `maseQ` vẫn trả `media.name` bình thường). Root cause là CÁCH
ĐẶT KHOÁ có từ bản v1: (1) khoá theo `task_id` nên hàng chục scene cùng tham chiếu 1 ảnh CHAR vẫn upload
riêng từng bản vào cùng project — file cache thật trên máy user: **196 entry ⇒ 196 media.name, 0 lần dùng
lại**; (2) task ẢNH chưa từng có cache (v1 chỉ gọi ở nhánh video).

Hệ quả kép: tốn băng thông/thời gian, VÀ project bị bơm đầy ảnh trùng ⇒ chạm trần `max_project_media_items`
(300) sớm hơn lượng media thật ⇒ tự luân chuyển project oan.

**Fix:** `media_upload_cache.py` lên v2 — khoá `(project_id, URL ảnh gốc)` + TTL 7 ngày, bỏ hàm xoá-theo-task
(với khoá mới, xoá theo task là xoá mất bản upload các task khác đang dùng chung). `worker.py` thêm
`_upload_source_media_cached()` làm **điểm duy nhất** được upload ref — cả 4 vòng cũ (2 nhánh ảnh + 2 nhánh
video) gọi vào đây, `_download_source_media()` giờ chỉ còn 1 caller.

Giữ nguyên bảo đảm an toàn của v1: khác project → cache miss, upload lại; không có project_id → bỏ qua cache,
không chặn task. File cache v1 cũ tự bị bỏ qua khi đọc (log rõ), không cần migrate.

⚠️ Bẫy tự tạo rồi tự bắt lúc verify: bản nháp dựng `image_inputs` từ `set(names)` → **mất thứ tự ref ảnh**.
`ref_names` (set) chỉ dùng cho `exclude` ở `parse_image_results()`; `image_inputs` phải dựng từ list theo
đúng thứ tự `source_media`. Kèm đổi hành vi nhỏ: `frameToVideo` cắt `source_media[:1]` trước khi tải (bản cũ
tải hết rồi lấy `downloaded[0]` — ảnh đầu lỗi thì âm thầm lấy ảnh CUỐI làm khung hình bắt đầu).

**Verify:** 8 kịch bản qua code thật — 3 scene dùng chung CHAR+BG: **9 upload/9 download còn 5/5**; retry
cùng task 0 upload; thứ tự ref giữ nguyên; đổi project upload lại; thiếu project_id vẫn chạy; TTL hết hạn tự
upload lại; ảnh tải lỗi không hỏng cả lô; file cache v1 cũ bỏ qua an toàn. `py_compile`/`pyflakes` sạch.
**CHƯA verify trên Flow/quota thật** — nhưng dùng lại `media.name` trong cùng project không phải cơ chế mới:
v1 đã làm vậy khi retry cùng task, chạy ổn định trong sản xuất từ 2026-08-13.
---

### 2026-09-05 | server/proxy_config.py (MỚI), server/chrome_utils.py, server/worker.py, server/routes.py, server/managers.py, gui/profile_dialog.py, gui/pages/profiles_page.py | 🌐 Proxy RIÊNG cho từng profile

Theo yêu cầu user: "cho phép nhập proxy trên UI để tất cả profile đi qua proxy đó" → ngay sau đó
đổi ý "proxy setting cho từng profile". Mỗi profile có 1 ô **Proxy** trong `ProfileDialog` (hiện
với MỌI loại profile — VEO3/Gemini/ChatGPT/Gemini Video), lưu vào cột mới
`selenium_profiles.proxy_server` (backend sở hữu bảng — xem CHANGELOG của `ToolSub` cùng ngày).
Để trống = đi thẳng, hành vi cũ không đổi gì.

**Điểm áp dụng DUY NHẤT** — `chrome_utils._build_chrome_options(proxy=, profile_id=)`: mọi đường
mở Chrome đều qua đó (worker uc lẫn thường, "Mở login browser", harness test) nên không phải sửa
từng call site. `parse_proxy()` nhận mọi định dạng người bán proxy hay đưa: `host:port`,
`scheme://host:port`, `user:pass@host:port`, `scheme://user:pass@host:port`, `host:port:user:pass`.
Chuỗi sai/scheme lạ/port ngoài 1-65535 → log rõ lý do rồi BỎ QUA proxy, KHÔNG chặn Chrome khởi
động (cấu hình proxy hỏng không đáng để cả profile không mở được).

**Auth user/pass:** Chrome BỎ QUA IM LẶNG credentials nhúng trong `--proxy-server`. Cách duy nhất
còn dùng được trên Chrome hiện đại (MV2 đã bị gỡ hẳn từ Chrome 139) là extension **MV3** bắt
`chrome.webRequest.onAuthRequired` với quyền `webRequestAuthProvider` (quyền Chrome thêm vào MV3
ĐÚNG cho use case này, thay `webRequestBlocking` của MV2 vốn chỉ còn dành cho extension cài bằng
policy). `ensure_proxy_auth_extension()` tự sinh extension đó vào `client_tool/data/proxy_auth_ext/`
`profile_{id}/` — **thư mục RIÊNG theo profile** (dùng chung 1 thư mục thì profile mở sau ghi đè
creds của profile đang chạy) — rồi nạp qua `--load-extension`, **kể cả khi `load_extensions=False`**
(cờ đó chỉ để tắt extension Flow cho worker_mode gemini/chatgpt, không liên quan proxy). Extension
chỉ trả credentials khi `details.isProxy` — trang web tự hỏi mật khẩu thì để nguyên, không gửi mật
khẩu proxy cho site lạ. Credentials nhúng bằng `json.dumps` nên mật khẩu chứa `"`/`'` không phá JS.

**SOCKS4/5:** Chrome không hỗ trợ xác thực SOCKS ở bất kỳ dạng nào — có creds + SOCKS thì log
warning rõ ràng (phải whitelist IP) thay vì im lặng chạy sai.

⚠️ **PHẠM VI:** proxy chỉ che traffic của TRÌNH DUYỆT. Lệnh HTTP do chính Python bắn (heartbeat,
tải ref ảnh từ backend, nhánh dự phòng `aisandbox` qua `curl_cffi`) VẪN đi thẳng — chủ ý, file nội
bộ không nên vòng qua proxy. Đường generate CHÍNH hiện tại (`batchexecute`) chạy bằng `fetch()`
TRONG TRANG nên vẫn được proxy che. Kèm `--proxy-bypass-list=localhost;127.0.0.1;[::1]`.

⚠️ Đổi proxy chỉ có hiệu lực ở lần MỞ Chrome kế tiếp (Stop rồi Start lại profile) — cờ dòng lệnh
chỉ đọc lúc launch. ⚠️ Lưu PLAINTEXT (có thể chứa user:pass) — cùng quy ước/rủi ro với
`account_password`. UI (`ProfilesPage`) hiện dòng `🌐 scheme://user:******@host:port` dùng CHÍNH
`parse_proxy()` để che — không tự cắt theo `@` vì dạng `host:port:user:pass` không có `@` nào,
cắt tay sẽ hiện nguyên mật khẩu lên màn hình.

**Verify:** `py_compile`/`pyflakes` sạch. `parse_proxy()` 16 định dạng (gồm mật khẩu chứa `:`,
scheme lạ, port 99999/chữ, thiếu host) — đúng hết. `build_proxy_setup()`: không proxy/không auth/
SOCKS+creds/HTTP+creds + 2 profile khác nhau ra 2 thư mục ext riêng đúng creds riêng; mật khẩu có
`"` và `'` escape đúng. `_build_chrome_options()` 6 tình huống — có/không proxy, có/không auth,
`load_extensions=False` vẫn nạp ext proxy-auth mà KHÔNG nạp ext Flow, proxy sai bị bỏ qua,
`uc.ChromeOptions` cũng nhận. GUI headless thật: prefill/get_data đúng, bảng profile che đúng mật
khẩu (assert `SUPERSECRET` không lọt lên UI), profile không proxy không hiện dòng thừa. Backend
end-to-end qua Flask test client + **DB THẬT** (create/GET/PATCH/clear/LIST/DELETE, dọn sạch row
test). **CHƯA verify trên Chrome/proxy thật** — cần user tự nhập 1 proxy thật rồi Start profile,
xác nhận IP đã đổi.
---

### 2026-09-04 (d) | server/worker.py | 🔒 Ràng buộc: chỉ generate khi THẬT SỰ ở trang project

User báo sau khi xoá cookie, worker bị đá ra màn hình có nút "Create with Google Flow" nhưng vẫn
chạy task ngay tại đó gây lỗi. 2 lỗ hổng: (1) nhánh API mode không hề gọi `_ensure_flow_page()` —
chỉ nhánh DOM có; (2) `_extract_project_id()` đọc id từ DB chứ không phải URL nên luôn hợp lệ, không
chặn được gì. Và kiểm tra URL KHÔNG ĐỦ vì màn hình xen giữa giữ nguyên `/project/{uuid}`.

Thêm `_project_page_ready()` (URL + UI thật: `.ProseMirror[contenteditable]` /
`button[aria-label="Settings trigger"]`) và `_ensure_project_page_ready()` (bấm nút create trước,
rồi mới tới `_ensure_flow_page()`). `_run_tasks_api_batch()` gọi đầu tiên — không vào được thì
KHÔNG chạy lô nào, báo lỗi từng task rồi để thang escalation xử lý.
`_recover_flow_project_page_after_cache_clear()` chốt thêm bằng kiểm tra UI (2 nhánh cũ chỉ soi URL).

Verify: ma trận 4 tình huống ra đúng True/True/True/False; batch khi trang chưa sẵn sàng → 0 lần
generate, báo lỗi đủ task, không escalate oan.

---

### 2026-09-04 (c) | server/chrome_utils.py, server/worker.py, server/routes.py | 🐛 FIX: client_tool tự mở một đống tab Chrome

User báo "client_tool có bug tự mở 1 đống tab chrome". Root cause: nơi DUY NHẤT biết "Chrome của
profile này đã mở chưa" là dict RAM `_login_drivers`. Nhưng login browser mở với `detach=True`
(Chrome SỐNG TIẾP sau khi driver kết thúc — chính log của nó ghi "Chrome có thể vẫn còn mở"), rồi
`finally` lại `_login_drivers.pop(pid)`; dict cũng mất sạch khi restart client_tool. Mất dấu ⇒
`_make_driver()` và route `/open` LAUNCH Chrome MỚI với CÙNG `--user-data-dir`. Chrome KHÔNG tạo
instance thứ 2 cho cùng 1 user-data-dir — nó chuyển yêu cầu sang instance đang chạy, instance đó
MỞ THÊM 1 CỬA SỔ/TAB rồi tiến trình vừa gọi thoát. Lặp qua nhiều lần restart worker (escalation
ladder cho ngủ 60s rồi auto-scale mở lại) ⇒ tab chồng chất.

Fix — hỏi THỰC TẾ thay vì hỏi RAM, 2 lớp vì 1 lớp không đủ:
- `_chrome_alive_on_port()` — probe DevTools `/json/version`. Đủ cho login browser (mở bằng
  `webdriver.Chrome` nên giữ cổng mở).
- `_chrome_procs_for_profile()` / `_chrome_running_for_profile()` / `_kill_chrome_for_profile()`
  (psutil, khớp `--user-data-dir`, bỏ qua tiến trình con `--type=`) — CẦN vì worker mở qua
  `undetected_chromedriver`, uc KHÔNG giữ cổng debug mở. ĐO THỰC TẾ trên máy: Chrome đang chạy
  profile 25 → probe cổng False, probe process True.
- `_make_driver()`: attach nếu Chrome sống (không chỉ khi có trong `_login_drivers`); tới nhánh
  launch mà vẫn còn Chrome mồ côi không attach được thì ĐÓNG nó trước rồi mới mở sạch — tự phục
  hồi, không để profile kẹt vĩnh viễn.
- Route `/open`: chặn 409 kèm thông báo rõ khi Chrome của profile đã mở sẵn.

Verify: `_chrome_running_for_profile` đo đúng trên Chrome thật đang chạy (True) trong khi probe
cổng miss (False); ma trận quyết định 4 tình huống (attach được / có trong `_login_drivers` /
mồ côi / không có gì) ra đúng attach / attach / kill+launch / launch.

---

### 2026-09-04 (b) | gui/pages/profiles_page.py | 🔘 Bật/tắt Auto ngay trên danh sách profile

Theo yêu cầu user "bật tắt auto profile chỉnh ngay trên danh sách profile không phải vào edit mới
chỉnh được" — thêm cột "Auto" (82px, trước cột Hành động) với nút 🟢 Bật / ⚪ Tắt bấm 1 phát là
`PATCH /api/selenium/profiles/<id> {enabled}`. Cùng field với checkbox "Bật — cho phép chạy" trong
ProfileDialog (`enabled=0` → auto-scale bỏ qua, Start thủ công cũng bị chặn, xem CLAUDE.md §11.9).
Tắt KHÔNG dừng worker đang chạy dở — auto-scale tự đóng khi profile rảnh, giữ đúng hành vi cũ của
field này. Cột Hành động dời từ index 6 → 7.

Verify: test headless PyQt6 với 3 profile giả (bật/tắt/đang chạy) — bảng dựng đúng 8 cột, nút hiện
đúng trạng thái từng dòng, payload PATCH đúng chiều (đang tắt→gửi 1, đang bật→gửi 0); round-trip
thật qua `pm` trên profile 25 (đọc → đổi → xác nhận → trả lại nguyên trạng).

---

### 2026-09-04 | server/flow_be.py (MỚI), server/worker.py, server/config.py, server/local_settings.py, gui/pages/settings_page.py, tests/* , docs/FLOW_BATCHEXECUTE_API.md, CLAUDE.md | 🔀 GENERATE chuyển sang batchexecute (aisandbox thành dự phòng)

Đường tạo ảnh/video cũ (`aisandbox`) đang HỎNG THẬT trong sản xuất — log profile 25 lúc
00:33-00:34 cho thấy `curl_cffi` 403 với cả 4 impersonation, phải hái lại bearer token mới qua
được 1 lệnh, rồi 403 tiếp; fallback `fetch()` trong trang chết vì CORS → `[batch-escalation]`
ngủ + xoá sạch cookie. Nguyên nhân gốc: app Flow đã bỏ hẳn endpoint aisandbox khi chuyển sang
`flow.google.com` (§11.44/§11.45).

Theo lựa chọn của user: **batchexecute làm CHÍNH, aisandbox làm DỰ PHÒNG**, phạm vi cả 4 luồng.

- `server/flow_be.py` (MỚI) — builder + parser cho `maseQ` (upload), `ogiZ0b` (ảnh, kể cả
  imageToImage qua `imageInputs`), `YhhmEf` (textToVideo), `MZZa6b` (ingredientToVideo).
- `worker.py` — 3 entry point giữ nguyên tên, chỉ thử BE trước rồi rơi về aisandbox nên KHÔNG
  call site nào khác phải sửa. Thêm `_be_session()` (cache TTL 600s, tự thử lại harvest 1 lần),
  backoff 3-lỗi→nghỉ-300s, và log ở MỌI nhánh bỏ qua BE.
- Setting mới `generate_via_batchexecute` (mặc định 1) — đặt 0 để quay lại aisandbox không cần
  sửa code.
- Chốt bằng capture thật: shape `maseQ`; uuid dùng cho `imageInputs` là `payload[0][0]`
  (DETAIL_UUID) chứ KHÔNG phải `[0][2]` (tile id); enum tỉ lệ ẢNH (16:9=3, 9:16=2) khác VIDEO
  (16:9=2, 9:16=1). Tỉ lệ chưa verify (1:1/4:3/3:4) và `frameToVideo` cố tình rơi về aisandbox.

2 bug thật bắt được lúc verify: (1) imageToImage trả 2 kết quả vì uuid ảnh THAM CHIẾU bị tính
thành kết quả — sản xuất sẽ lưu chính ảnh ref như ảnh đã sinh; (2) fallback im lặng (4 nhánh
`return None` không log) khiến 1 lần hỏng không để lại manh mối nào.

Verify: `py_compile`/`pyflakes` sạch; shape builder khớp 4/4 capture thật; chạy THẬT qua đúng
entry point sản xuất (`tests/_verify_generate_be.py`) cả 5 bước upload/textToImage/imageToImage/
componentsToVideo/textToVideo trên quota thật. CHƯA verify qua hàng đợi heartbeat thật.

**Cập nhật cùng ngày:** theo yêu cầu user "cất luôn source aisandbox... ko chạy 2 loại" — bỏ hẳn
fallback: `generate_via_batchexecute=1` (mặc định) thì CHỈ chạy batchexecute, hỏng là task lỗi;
aisandbox giữ nguyên code nhưng thành nhánh chết, lấy lại bằng setting=0. Bỏ luôn backoff và bước
chờ token aisandbox 90s lúc khởi động.

Kèm fix từ log sản xuất 01:20: `PUBLIC_ERROR_UNUSUAL_ACTIVITY` (chống lạm dụng theo nhịp, chặn CẢ
2 đường) bị báo nhầm thành "payload rỗng" vì `_batchexecute_parse()` gộp "payload null hợp lệ" với
"RPC bị từ chối". Nay raise `BatchExecuteError` kèm `.reason` và log rõ là chặn tầng tài khoản.

**Fix tiếp (cùng ngày):** log máy user spam `session API: TypeError: Failed to fetch` mỗi giây —
`_fetch_labs_session()` gọi `/fx/api/auth/session` của labs.google, từ trang flow.google.com là
cross-origin nên CORS chặn. Nó vẫn chạy vì bản đầu chỉ chặn 3 call site `_sync_project_apis()`,
bỏ sót 4 call site `_ensure_api_ready()` gọi gián tiếp. Chặn ở 1 chỗ duy nhất (đầu
`_ensure_api_ready()`) + chặn luôn `_drain_perf_logs()` (chỉ lọc URL aisandbox, chạy 9 nơi mỗi
task). Verify bằng đếm lời gọi ở cả 2 chế độ.

**Fix tiếp #2:** chính lần gate trên làm `has_tokens()` luôn False (không còn hái bearer aisandbox),
mà 6 cổng chặn trên đường generate vẫn hỏi nó — mọi task video raise "Chưa có API token", mọi task
ảnh bị đẩy sang nhánh UI-driven. Thêm `can_generate_via_api()` (True khi chạy batchexecute) thay
vào 6 cổng đó. Giãn cách task/batch (`thread_stagger_*`, `task_delay_secs`, POLL_INTERVAL) KHÔNG
đổi — chỉ mất phần trễ phụ do bỏ `_ensure_api_ready()`/`_drain_perf_logs()` khỏi hot path.

**Fix tiếp #3 (user phát hiện qua log 09:07):** giãn cách không áp cho nhánh LỖI — lệnh chờ nằm sau
`pool.submit()`, task hỏng ở bước chuẩn bị thì `continue` nhảy qua, cả lô hỏng chạy hết trong vài
giây. Dùng cờ `prepared` để đoạn chờ chạy cho cả 2 nhánh. Kèm đổi thứ tự ở cả vòng ảnh lẫn video:
kiểm tra điều kiện rẻ TRƯỚC, mint reCAPTCHA SAU (trước đây task chắc chắn hỏng vẫn đốt 1 token
Google). Verify bằng đo thời gian + đếm số lần mint.

⚠️ client_tool đang chạy CẦN RESTART để nạp module + setting mới.

---

### 2026-09-03 (d) | docs/FLOW_BATCHEXECUTE_API.md (MỚI), CLAUDE.md, tests/FLOW_API_CAPTURE.md | 📄 Tài liệu tham chiếu API Flow mới (batchexecute)

Gom toàn bộ thứ đã verify trong ngày thành 1 tài liệu tra cứu: endpoint + định dạng request
(`f.req` lồng 2 lớp) / response (XSSI + length-prefixed chunk, decode 2 lớp), cách harvest
`bl`/`f.sid`/`at`, quy luật `_reqid`, yêu cầu reCAPTCHA (site key + action + thứ tự harvest-trước-
mint-sau), **bảng 72 rpcid** kèm tên method backend, **shape body 6 luồng** (Zzl0ze, as29s,
textToImage, imageToImage, textToVideo, ingredientToVideo) theo đường dẫn index, 4 nhóm giá trị
phải làm mới, 6 bẫy đã dính, code pointer + cách chạy test, và mục "đã verify / chưa verify".

`tests/FLOW_API_CAPTURE.md` (tài liệu API cũ aisandbox) thêm cảnh báo `projectInitialData` đã chết
+ trỏ sang tài liệu mới. `CLAUDE.md` §11.45 thêm link ở đầu mục.

---

### 2026-09-03 (c) | server/worker.py, tests/utils/flow_rpc.py, tests/_test_*_new.py, CLAUDE.md | ⚠️ FIX bug đính kèm ref ảnh TRÙNG 3 lần; chạy được 4 luồng generate qua batchexecute

**⚠️ BUG PRODUCTION (quan trọng nhất):** `_dom_upload_images()` bước B5 bắt buộc tìm nút "Add to
prompt", không thấy thì raise. Nhưng UI hiện tại: click item VỪA UPLOAD đã tự đính kèm + tự đóng
popover → nút không tồn tại → raise → vòng retry lặp lại toàn bộ upload 3 lần → **đính kèm trùng 3
ảnh rồi vẫn báo lỗi**. Ảnh hưởng mọi task `imageToImage`/`imageToVideo`/`componentsToVideo` (luôn
có ref ảnh) — tức phần lớn task VEO production. Fix: kiểm tra chip TRƯỚC, đủ thì bỏ qua B5; không
thấy nút cũng không raise (để bước validate 20s sẵn có phán quyết). Verify: `chip: 1`, không raise.
Bắt được bằng `tests/_diag_ref_upload.py` (chạy riêng bước upload, không submit → không tốn quota).

**4 luồng generate đã chạy THẬT qua batchexecute:** textToImage (`ogiZ0b`, ảnh 175KB đúng prompt),
imageToImage (`ogiZ0b` + imageInputs ở `[1][0][2]`, ảnh 370KB giữ đúng nhân vật từ ref),
textToVideo (`YhhmEf`), ingredientToVideo (`MZZa6b`). 2 RPC video là BatchAsync → POST trả về ngay,
lấy kết quả sau bằng reconcile hoặc `jwpduf`.

**Hạ tầng test mới:** `tests/utils/flow_rpc.py` (`FlowRpcTest` — capture/replay dùng chung, hỗ trợ
`--ref` ảnh tham chiếu qua HTTP tạm, `capture_name` riêng khi 2 luồng chung rpcid) + 4 script mỏng.

**2 bẫy đã dính:** (1) `window.__rpcCap` sống theo vòng đời trang → capture bắt phải request CŨ của
lần chạy trước mà không báo gì; fix bằng xoá buffer trước submit + lấy request mới nhất. (2)
`refresh_args()` thay mọi uuid chữ thường thành projectId → suýt ghi đè uuid ảnh tham chiếu; fix
bằng chỉ thay đúng project id cũ trích từ `source-path`.

Xem đầy đủ ở `CLAUDE.md` §11.45.

---

### 2026-09-03 (b) | server/worker.py, server/config.py, server/local_settings.py, gui/pages/settings_page.py, tests/* , CLAUDE.md | ✅ Chuyển HẲN reconcile sang RPC `batchexecute`; API generate (`ogiZ0b`) cũng chạy được qua đường này

**Root cause:** `flow.projectInitialData` KHÔNG CÒN TỒN TẠI trên `flow.google.com` (gọi thẳng URL cũ
trả nguyên shell HTML SPA) — không phải "đổi transport" như phỏng đoán ban đầu (vá thêm
`XMLHttpRequest` không giúp gì). App mới dùng RPC đa dụng `batchexecute`
(`/_/AiSandboxAngularFrontend/data/batchexecute`, cùng protocol Gmail/Drive/Docs).

**Reconcile (đã nối vào SẢN XUẤT):** `Zzl0ze` (`/FlowService.GetProjectContents`) liệt kê media →
lọc candidate (đã qua generation + trong `reconcile_lookback_secs`, setting MỚI mặc định 7200s) →
`as29s` (`/FlowService.GetMedia`) lấy prompt + CDN URL đã ký. Bỏ 2-giai-đoạn check-rồi-resolve
(as29s trả cả 2 trong 1 lần gọi). Xoá dead code `_install_project_data_interceptor`/
`_media_item_prompt`/`_media_item_type`/`_dom_find_tile_src_by_name`.
⚠️ **Bug tốn nhiều thời gian nhất:** uuid cho `as29s` là `meta[4]` (DETAIL_UUID, khớp uuid trong
CDN URL), KHÔNG PHẢI `entry[0]` (tile id) — dùng `entry[0]` thì 130/130 candidate trả `null`.
Verify live: liệt kê đúng 275 media, **phục hồi 8 task video THẬT đang mắc kẹt** trong DB production
(30782, 30784, 30786, 30788, 30800, 30802, 30806, 30822).

**Generate qua batchexecute (`ogiZ0b` = `/FlowService.BatchGenerateImages`):**
`tests/_test_textToImage_new.py` chạy THÀNH CÔNG end-to-end — tự dựng lệnh, POST, nhận CDN URL,
tải về đúng ảnh khớp prompt (175 KB JPEG). Shape body học từ CAPTURE THẬT (chế độ `--capture` chạy
đúng luồng DOM sản xuất rồi chặn network lấy nguyên `f.req`), không đoán từ bundle minified.
⚠️ **API mới VẪN CẦN reCAPTCHA** (token nằm trong body ở `[1][0][7][10][0]`) — phỏng đoán "không
cần" là SAI; site key khớp đúng `LABS_RECAPTCHA_SITE_KEY` sẵn có nên mint bằng chính
`_get_fresh_recaptcha()`. Bỏ được: bearer token, fingerprint ExtraInfo, `curl_cffi` impersonate.
`--replay` làm mới reCAPTCHA + 3 UUID + seed (dò theo đặc điểm giá trị, không hardcode path).

**Kèm theo:**
- `_batchexecute_call()` thêm tham số `timeout` (mặc định 20; RPC sinh nội dung cần 90-180).
- `tests/utils/profile_target.py` (MỚI) — mọi script test mặc định chạy profile 25: port suy từ
  công thức production (`9300 + id % 200`), `project_id` đọc từ API (tự đúng khi worker luân chuyển
  project), `ensure_chrome()` tự mở Chrome bằng đúng helper sản xuất nếu chưa mở (không cần bật GUI
  client_tool). Cờ `--profile-id` / `--debugger-address` / `--no-attach`. Đã nối vào 10 file test.
- `tests/_extract_rpcid_map.py` — trích bảng 72 rpcid → tên method backend TỪ BUNDLE JS (gồm
  `nprQif`=`…VideoStartAndEndImage`, trả lời câu hỏi treo ở §11.33 về `endImage`).
- Ghi nhận: `_reqid` có quy luật `(giây từ nửa đêm giờ địa phương) + 100000×n`, không phải random.

Xem đầy đủ ở `CLAUDE.md` §11.45.

⚠️ **Client_tool đang chạy CẦN RESTART** để nạp code + setting mới.

---

### 2026-09-03 | server/worker.py, CLAUDE.md | ⚠️ Google Flow đổi HẲN sang domain/app MỚI `flow.google.com` (Angular Material) — viết lại `_dom_configure()`/`_dom_upload_images()`/`_dom_fill_and_submit()`/`_dom_select_model_verified()`

User: *"DOM video veo3 trang flow đã thay đổi hãy truy cập profile xem log và điều khiển profile
update theo giao diện mới"* — profile `huavantien84_2` (id=25) báo 35/35 task lỗi trong ngày, mọi
task fail ngay ở bước cấu hình/đính ảnh (`config button not found`/`add_2 button not found`/
`prompt textarea not found`).

**Root cause:** `labs.google/fx/vi/tools/flow/project/{uuid}` giờ REDIRECT sang domain MỚI HOÀN
TOÀN `flow.google.com/project/{uuid}` — app cũ (React/Slate.js/icon-ligature `<i>`) bị thay bằng
Angular Material (`mat-icon`/`flow-*`/CDK overlay) + ProseMirror editor. Xác nhận qua Chrome đang
mở THẬT (attach CDP vào profile 25, throwaway scripts — không bao giờ bấm "Start generation",
không tốn quota), KHÔNG đoán từ code cũ.

**Selector MỚI (xem đầy đủ trong `CLAUDE.md` §11.44):** ô nhập prompt `.ProseMirror[contenteditable
="true"]` (submit = bấm `button[aria-label="Start generation"]`, KHÔNG còn Enter); settings mở qua
`button[aria-label="Settings trigger"]`, các nhóm `<flow-toggles aria-label="Mode|Video type|Aspect
ratio|Output count">`; model qua `button[aria-label="Select model family"]` (unique, bỏ hẳn kỹ
thuật scope `window.__modelPopupRoot`); đính ảnh qua `button[aria-label="Add ingredients to the
prompt box"]` → side-nav "Images" → search → click item (đã có sẵn = tự đính kèm luôn) hoặc "Upload
media" (chưa có = upload mới, giữ nguyên kỹ thuật DataTransfer cũ) → "Add to prompt". Verify chip
đính kèm qua `flow-image-ingredient-chip img.chip-image` (thay `[data-card-open]` cũ).

**Bug thứ 2 tự bắt được lúc verify sống:** chọn model KHÁC model hiện tại đôi khi đóng HẲN settings
popup (không chỉ đóng submenu) — verify cũ đọc lại label mà không kiểm tra panel còn mở, luôn thất
bại dù selection đã áp dụng đúng. Fix: `_ensure_settings_open()` — tự mở lại panel trước khi tìm nút
model VÀ trước khi đọc verify.

**Phát hiện thứ 3, KHÔNG PHẢI bug code — nghi hạn chế tài khoản (CHƯA XÁC NHẬN):** patient-poll 15s
xác nhận model "Veo 3.1 - Lite"/"Fast"/"Lite [Lower Priority]" (model hầu hết task video của profile
25 đang cấu hình) KHÔNG BAO GIỜ áp dụng được trên account này — luôn tự quay về "Omni 1.1 Flash",
trong khi "Veo 3.1 - Quality" chọn đúng bình thường. Xem CLAUDE.md §11.44 để biết đầy đủ + việc CHƯA
làm (task thật qua hàng đợi, domain-check `'labs.google' in current_url` chưa nhận `flow.google.com`,
`projectInitialData` interceptor cũng đang lỗi — chưa điều tra lần này).

**Verify:** đầy đủ qua Chrome thật (không mock) cho cả 3 hàm + fix model — xem chi tiết CLAUDE.md
§11.44. AST-walk toàn bộ 55 chuỗi JS trong file syntax-check qua Node, 0 lỗi. `py_compile`/`pyflakes`
sạch. ⚠️ **CHƯA cho 1 task thật chạy trọn vẹn qua hàng đợi heartbeat** — cần user tự bật lại profile
25 và theo dõi. Client_tool đang chạy (nếu có) cần RESTART để nạp code mới.

---

### 2026-09-03 | server/worker.py | FIX: batch DOM chỉ có 1 task đi sai luồng — check ngay sau khi vừa submit thay vì submit-hết-batch-rồi-mới-check

User: *"luồng là nhập batch mới giống API rôi mới check các thứ chứ sao vừa tao xong 1 task lại đi
check rồi"*.

**Root cause:** `_process_tasks()` chỉ route sang `_run_tasks_batch()` (submit CẢ BATCH trước,
reconcile SAU CÙNG — đúng luồng) khi `worker_mode == 'dom' and len(tasks) > 1`. Batch CHỈ CÓ 1 task
(phổ biến khi backlog mỏng) rơi xuống vòng lặp generic → `_run_task_dom()` — submit RỒI GỌI
`_wait_and_reconcile_tasks()` NGAY cho đúng 1 task đó, khác hẳn `worker_mode='api'` (luôn dùng
`_run_tasks_api_batch()`, không có ngưỡng nào).

**Fix:** bỏ điều kiện `and len(tasks) > 1` — DOM mode giờ đối xứng với API mode, luôn submit hết
batch (kể cả batch 1 task) rồi mới reconcile 1 lần. `_run_task_dom()`/`_run_task()` giữ nguyên trong
code (không xoá, vẫn dùng bởi `tests/_test_dom_batch.py`), chỉ không còn được gọi từ đường sản xuất
chính cho `worker_mode='dom'`.

**Verify:** `py_compile`/`pyflakes` sạch. Test cũ không bị ảnh hưởng (gọi thẳng `_run_tasks_batch()`,
không qua `_process_tasks()`). CHƯA verify trên hàng đợi thật.

---

### 2026-09-01 | server/worker.py | FIX: đọc phản hồi Gemini bị RACE — chốt "kết quả trước đó" (khối thinking / DOM chưa commit xong) rồi báo JSON không hợp lệ

User báo: *"code lấy kết quả chat gemini chưa chuẩn response chat trả về đầy đủ mà lại bắt kết quả
trước đó rồi báo JSON không hợp lệ"* — tức Gemini đã trả lời XONG THẬT (đầy đủ), nhưng worker chốt
1 mẩu nội dung CŨ HƠN bản cuối cùng, không phải câu trả lời thiếu.

**Root cause:** `_gemini_response_if_ready()` (dùng chung bởi CẢ luồng 1-tab tuần tự lẫn round-robin
nhiều tab — tức MỌI field Gemini: kịch bản nháp/Bible/storyboard viết lại/scene batch đều dính) chỉ
đọc `.markdown` của `structured-content-container` cuối cùng NGAY khi `is_generating` (nút "Stop
response"/loading-indicator) vừa về `false`, không xác nhận gì thêm. 2 nguồn RACE khớp đúng triệu
chứng "bắt kết quả TRƯỚC ĐÓ":

1. **Model "thinking"** (Gemini 3.x Flash/Pro đang dùng thật trong dự án đều có chế độ suy luận) hiện
   1 khối "Đang suy nghĩ" RIÊNG trước khi khối trả lời thật xuất hiện — nếu `is_generating` về `false`
   đúng vào khe hở giữa 2 khối, `containers[last]` trỏ vào khối thinking — nội dung cũ hơn, gần như
   chắc chắn KHÔNG phải JSON hợp lệ.
2. Angular re-render `.markdown` không cùng nhịp với việc gỡ nút Stop — đọc `.innerText` đúng khung
   hình đó có thể là DOM CHƯA COMMIT xong đoạn cuối.

**Fix:** `_gemini_response_if_ready(stability_state)` — tham số MỚI, 1 dict do caller giữ xuyên suốt
các lần poll của ĐÚNG 1 tác vụ. Chỉ trả kết quả khi CÙNG 1 nội dung (text, số ảnh) được thấy ỔN ĐỊNH
liên tục ≥1.2s (`_GEMINI_RESPONSE_STABLE_SECS`, tính theo THỜI GIAN THỰC — không phải số lần poll, vì
2 caller có nhịp poll khác nhau). Nội dung đổi giữa 2 lần đọc (kể cả `is_generating` đã false) →
reset đồng hồ, tiếp tục chờ — bắt đúng cả 2 dạng race ở trên. `is_generating=true`/ảnh còn placeholder
trắng/chưa có nội dung đáng kể → `stability_state.clear()` (bỏ ứng viên cũ, không tính dở dang).

`_gemini_wait_response()` (1-tab tuần tự) tự giữ 1 dict cục bộ. Round-robin (`_gemini_slot_step()`)
— mỗi SLOT có dict RIÊNG (`slot['response_stability']`, khởi tạo lúc mở slot) vì mỗi tab là 1
conversation độc lập, ứng viên nội dung của tab này không liên quan tab khác. `stability_state=None`
(không truyền) giữ hành vi CŨ — tương thích ngược, không có caller nào còn dùng kiểu này.

Verify: test độc lập trích ĐÚNG công thức trong code (không mock DOM) — 4 kịch bản PASS: (1) khối
"Đang suy nghĩ" bị thay bằng câu trả lời thật ngay sau — KHÔNG BAO GIỜ trả về khối thinking cũ (bug
gốc sẽ trả ngay ở lần đọc đầu); (2) text ngày càng dài (DOM commit dở) — chỉ trả khi text đứng yên đủ
lâu; (3) trường hợp bình thường không có race — vẫn trả đúng kết quả (chỉ chậm thêm ~1.2s, chấp nhận
được); (4) `is_generating` bật lại giữa chừng — `clear()` đúng xoá trạng thái cũ. `py_compile`/
`pyflakes` sạch.

**CHƯA verify trên Chrome/Gemini thật** (môi trường này không launch được Chrome) — cần user restart
worker, chạy vài task Gemini (ưu tiên các bước hay dùng model "thinking" — full_script/rewrite_
storyboard/scene_batch prompt dài) và xác nhận không còn callback báo "JSON không hợp lệ" dù Gemini
đã trả lời đầy đủ trên UI.

---

### 2026-08-31 | server/worker.py | Tab NGHỈ giữa 2 lô tự về gemini.google.com thay vì đứng ở trang trắng

User báo *"các task gemini profile đang mở trang trắng ko vào gemini để chạy task"*. Kiểm tra log
(`logs/profile_26.log`, `logs/profile_27.log` ngày 31/08) — **task VẪN chạy đúng**: nhận lô → submit →
callback ✔ bình thường ở cả 2 profile. Cái user thấy là cửa sổ Chrome lúc **rảnh giữa 2 lô**.

Nguyên nhân: tác dụng phụ của bản sửa 2026-08-30 — `_gemini_close_tab()` mở 1 tab GIỮ CHỖ
(`chrome://new-tab-page`) trước khi đóng tab slot cuối để Chrome không thoát. Xong lô, cửa sổ chỉ còn
đúng tab trắng đó nên trông như "chết", dù worker vẫn heartbeat và lô kế tiếp vẫn chạy đúng (state
`'new'` của `_gemini_slot_step()` tự `driver.get(GEMINI_URL)`). TRƯỚC bản sửa hôm qua không thấy hiện
tượng này vì browser bị đóng sạch sau mỗi lô rồi mở lại + navigate vào Gemini.

Fix: sau khi lô xong và tab giữ chỗ được tái sử dụng, đưa nó về thẳng `gemini.google.com/app` (bỏ qua
nếu đã ở đó). Lợi ích: nhìn là biết profile còn sống/còn đăng nhập, session Google được "chạm" định kỳ
thay vì nằm im ở trang trắng, và lô kế tiếp đỡ 1 lần điều hướng nguội. Best-effort — điều hướng lỗi
chỉ log warning, không làm chết vòng lặp.

Verify: `ast.parse` sạch. **CHƯA chạy thật** — cần khởi động lại client_tool rồi để worker rảnh 1 lô
để xác nhận tab nghỉ đứng ở Gemini.

---

### 2026-08-30 (b) | server/worker.py | Bỏ bước "xoá cache + cookie sau mỗi batch" cho task GEMINI

Theo yêu cầu user *"các task liên quan gemini không cần xóa cache khi xong"*. Gỡ lời gọi
`_cdp_clear_cache_and_cookies()` ở CẢ 2 luồng Gemini: `_run_gemini_loop()` (1 tab tuần tự, gọi sau
mỗi task) và `_run_gemini_loop_concurrent()` (round-robin, gọi sau mỗi lô).

Ngoài việc user không cần, bước này vốn đã VÔ NGHĨA với Gemini: từ 2026-08-18 hàm đó CHỈ xoá cookie
thuộc domain `labs.google` (+ cache HTTP) — mà Gemini chạy trên `gemini.google.com`, không đụng gì
labs.google. Đổi lại nó tốn CDP call và chính là chỗ ném ra log `[batch-clean] … invalid session id`
mỗi lô khi Chrome vừa thoát (xem entry ngay dưới).

GIỮ NGUYÊN 2 nơi còn lại — worker **ChatGPT** (sau mỗi task) và nhánh leo thang lỗi của **Flow/VEO3**
(`_handle_batch_escalation`, xoá cookie labs.google rồi click lại "Create with Google Flow") — không
nằm trong phạm vi yêu cầu. Hàm `_cdp_clear_cache_and_cookies()` giữ nguyên, không xoá.

Verify: `ast.parse` sạch; grep xác nhận chỉ còn đúng 2 call site (ChatGPT + Flow escalation).

---

### 2026-08-30 | server/worker.py | FIX: xong mỗi batch round-robin là Chrome tự thoát (worker restart vô tận) + keepalive quá thưa làm KẾT QUẢ ĐÃ CÓ bị server vứt

User báo: log worker báo `✔ Nhận response 11145 ký tự` rồi `callback → …/api/gemini/callback ✔`,
nhưng dữ liệu KHÔNG lên frontend. Điều tra log `logs/profile_26.log` + DB backend cho thấy
CỨ MỖI BATCH lặp đúng 1 vòng: nhận 2 task → xử lý xong → callback ✔ →
`[batch-clean] Xoá cache lỗi: invalid session id` → `Browser đã đóng — thoát worker loop` →
`Worker stopped` → auto-scale mở lại sau ~20-30s → nhận lại ĐÚNG 2 clip đó → lặp lại.

**1. `_gemini_close_tab()` đóng tab CUỐI CÙNG = đóng cửa sổ cuối của Chrome → trình duyệt
THOÁT HẲN.** Lộ ra từ 2026-07-25 khi tab khởi động được TÁI SỬ DỤNG làm slot đầu tiên (trước
đó tab khởi động luôn còn lại nên browser không bao giờ hết tab). Sau batch, cả 2 tab slot bị
đóng → không còn tab nào → session chết → `_cdp_clear_cache_and_cookies()` lỗi `invalid session
id` → `_is_driver_alive()` false → thoát vòng lặp. Fix: nếu đang đóng tab cuối thì MỞ 1 tab
GIỮ CHỖ trước; tab đó được tái dùng làm slot đầu của lô kế tiếp (`startup_tab_handle` gán lại
trong `_run_gemini_loop_concurrent`) nên không tích tụ tab rác.

**2. Keepalive round-robin quá thưa → server đánh máy `offline` → request bị thu hồi giữa
chừng → callback bị coi là "stale" và KẾT QUẢ BỊ VỨT.** Vòng lặp CHỈ heartbeat ở CUỐI mỗi
vòng, trong khi 1 bước slot có thể block hơn 30s (tải video 100MB+ rồi `send_keys` đính kèm,
`gemini_attach_timeout` mặc định 180s). Backend đánh `offline` sau 30s không heartbeat, reaper
thu hồi request đang xử lý dở → `assign_token` đổi → `gemini_callback()` trả
`{"skipped": true, "reason": "stale_assignment"}` (HTTP **200**, nên client vẫn log ✔) và
KHÔNG lưu gì. Fix: thread nền gửi `keepalive_only` heartbeat đều đặn mỗi `POLL_INTERVAL` suốt
batch — đúng cơ chế `_run_task_gemini` (1-tab) đã dùng từ 2026-07-06; vòng lặp chính không còn
tự gửi nữa.

**3. Log callback không còn báo ✔ khi server BỎ QUA kết quả.** `_gemini_slot_finish()` đọc body
phản hồi: có `skipped` → log WARN kèm `reason` + gợi ý nguyên nhân (mất heartbeat >30s rồi bị
giao lại). Chính chỗ này che mất lỗi suốt thời gian qua.

Backend `ToolSub` sửa song song 2 điểm khiến hậu quả nặng thêm (ân hạn trước khi thu hồi theo
máy offline, và không đánh lỗi oan request chỉ đang xếp hàng) — xem CHANGELOG repo đó, mục
2026-08-30 (d).

Verify: `ast.parse` sạch. **CHƯA chạy thật end-to-end** — cần khởi động lại client_tool (và
restart backend) rồi chạy lại 1 lô để xác nhận Chrome không còn tự đóng sau batch.

---

### 2026-08-27 | server/config.py, server/routes.py | Gửi kèm session cookie khi gọi route log — backend vừa siết auth cho `/api/worker_profiles/<pid>/logs`

Đi kèm đợt fix bảo mật bên `ToolSub` (xem CHANGELOG repo đó, mục 2026-08-27,
phát hiện #3): 3 route log của `worker_profiles_bp` TRƯỚC ĐÂY hoàn toàn không
gate auth — bất kỳ ai trên mạng cũng `GET` đọc log mọi worker (trace đăng nhập
Google, prompt, project URL), `DELETE` xoá sạch, `POST` chèn log giả — vì
control duy nhất là scope theo header `X-Client-Id` mà helper lại trả `'1=1'`
khi header vắng mặt (bỏ header = vô hiệu hoá scope). Backend giờ đã thêm
`@_require_tool_media` cho cả 3 route đó.

Phía client_tool phải gửi kèm session cookie, nếu không MỌI lời gọi log sẽ 401:

- `server/config.py::_profile_log()` — thêm `cookies=get_auth_cookies()`. Import
  TRỄ (trong hàm) giống `client_headers()` vì `auth_client.py` import ngược lại
  `config.py` (`from .config import FLOW_SERVER, log`) → import ở đầu file sẽ
  circular.
- `server/routes.py` — 2 route proxy `GET`/`DELETE /api/selenium/profiles/<pid>/logs`
  thêm `cookies=get_auth_cookies()` (import ở đầu file được, không circular).

`managers.py::_api()` KHÔNG cần sửa — đã gửi `cookies=get_auth_cookies()` sẵn cho
mọi request từ 2026-07-18, nên 4 route `/api/worker_extensions*` (cũng vừa được
gate trong cùng đợt) tự động hoạt động.

Verify: `py_compile` + `pyflakes` sạch cả 2 file. **CHƯA test chạy thật** — cần
mở lại client_tool + restart backend rồi xác nhận log profile vẫn ghi/đọc/xoá
được bình thường (nếu thấy 401 ở log là do backend đã restart mà client_tool
chưa cập nhật, hoặc tài khoản không thuộc group 'Tool Media').

### 2026-08-23 (f) | server/worker.py, tests/_test_batch_clean_recover.py (MỚI) | FIX THẬT: sau khi xoá cookie KHÔNG BAO GIỜ quay lại project (nút xen giữa không có trên trang chung) → batch sau tạo project mới vô ích

User: *"tôi vẫn thấy còn bước lỗi sau khi reconcile liên tục 2 batch ko được
phải xóa cookie -> rồi refresh page -> để click nút create -> vào lại project
hiện có. Lỗi hiện giờ khi xóa cookie lại ra trang
`https://labs.google/fx/vi/tools/flow` -> click tạo dự án -> ... -> lỗi tạo
project mới không cần thiết"*.

#### Root cause

`_recover_flow_project_page_after_cache_clear()` nhốt TOÀN BỘ phần
quay-lại-project vào trong:

```python
if click_create and self._click_create_with_flow_if_present():
    ...   # navigate về project nằm ở ĐÂY
```

Nhưng màn hình xen giữa "Create with Google Flow" **CHỈ hiện khi ĐIỀU HƯỚNG VÀO
1 URL project** — nó KHÔNG có trên trang chung. Mà `driver.refresh()` ngay sau
khi xoá cookie thì gần như LUÔN bị bật về đúng trang chung
(`.../tools/flow`) ⇒ không tìm thấy nút ⇒ **cả khối bị bỏ qua** ⇒ không bao giờ
quay lại project. Hệ quả dây chuyền:
1. `_reconcile_project_media()` vẫn chạy — nhưng TRÊN TRANG CHUNG: tốn 1 lần
   refresh + ~15s chờ interceptor mà không đọc được `projectInitialData` nào.
2. Worker kết thúc bước dọn khi đang đứng ở trang chung.
3. Batch kế tiếp `_ensure_flow_page()` thấy không ở `/project/` → thử
   `_return_to_saved_project()` → hết 3 lần → **TẠO PROJECT MỚI** — đúng "lỗi
   tạo project mới không cần thiết" user báo.

Thứ tự cũ cũng NGƯỢC với thứ tự user mô tả: đang bấm-nút-trước rồi mới (may ra)
điều hướng, trong khi đúng phải là **vào lại link project TRƯỚC → lúc đó màn
hình xen giữa mới hiện → bấm**.

#### Fix

Tách hẳn 2 việc, không lồng nhau nữa:
1. Còn ở URL project sau refresh → thử bấm nút xen giữa (URL KHÔNG đổi khi bị
   kẹt ở màn hình đó nên vẫn phải thử).
2. **Bất kể bước 1 ra sao** — nếu hiện không ở `/project/` thì
   `_return_to_saved_project()`. Hàm này vốn đã làm ĐÚNG chuỗi user mô tả (mỗi
   vòng: `driver.get(saved)` → `_click_create_with_flow_if_present()` → kiểm
   tra), thử tối đa 3 lần và **KHÔNG BAO GIỜ tạo project mới**.
3. Không quay lại được → log cảnh báo rồi **return, BỎ QUA reconcile** (chạy
   trên trang chung là vô ích mà vẫn tốn refresh + ~15s).

Thêm `_sync_project_url_from_browser(before)` ngay đầu hàm — chốt `project_url`
theo project ĐANG làm việc trước khi refresh, để `_return_to_saved_project()`
chắc chắn có đúng URL để quay về.

#### Verify

`tests/_test_batch_clean_recover.py` (MỚI) — FakeDriver mô phỏng đúng hành vi
thật (nút xen giữa CHỈ xuất hiện khi đang ở URL project). 6 nhóm / 14 assertion:
- **Kịch bản bug**: refresh bật về trang chung → KHÔNG tạo project mới, vào lại
  đúng URL cũ, kết thúc Ở trang project, reconcile chạy TRÊN trang project.
- Refresh vẫn ở project nhưng kẹt xen giữa → bấm đúng 1 lần, không điều hướng thừa.
- Bấm xen giữa xong bị đẩy ra ngoài → vẫn quay lại được.
- Không vào lại được → bỏ qua reconcile, không tạo project mới, có cảnh báo.
- `click_create=False` (batch thành công) → không bấm nút, vẫn reconcile.
- Không đứng trên trang project → thoát sớm, không refresh/get/reconcile.

`_test_batch_escalation.py` 8/8 · `_test_return_to_saved_project.py` 11/11 ·
`_test_purge_site_data.py` 6/6 — không regression. `pyflakes` sạch.
**CHƯA verify trên Chrome/labs.google thật.**

---

### 2026-08-23 (e) | server/worker.py | Bậc 2-batch-lỗi: `storageTypes` đổi sang `'all'` (tránh cả lớp bug sai tên enum làm bước dọn ÂM THẦM không chạy) + bỏ qua cho worker không ở labs.google

User hỏi lại: *"thế clear cookie riêng của labs.google khi sai 2 batch đã chuẩn
chưa"*. Audit lại đúng đường đó (`_finish_batch_cleanup()` rung
`batch_fail_count_before_cleanup`) — phần cookie ĐÚNG, nhưng phần site data vừa
thêm ở entry (d) có **1 rủi ro thật** và **1 chỗ lãng phí**.

#### Rủi ro: liệt kê tay `storageTypes` — sai 1 tên là hỏng TOÀN BỘ

`Storage.clearDataForOrigin`'s `storageTypes` là enum CHẶT của CDP
(`Storage.StorageType`). Bản (d) liệt kê tay và viết **`filesystems`** — tên
đúng theo spec là **`file_systems`** (có gạch dưới). Chrome từ chối NGUYÊN LỆNH
khi gặp tên lạ, mà đây là bước best-effort bọc try/except ⇒ lỗi chỉ thành 1 dòng
warning, **toàn bộ việc dọn site data âm thầm không xảy ra** — đúng cái lớp bug
user đang phàn nàn. Không xác minh được enum tại chỗ (không có Chrome/CDP spec
trong môi trường), nên thay vì đoán từng tên: dùng **`'all'`** — giới hạn theo
ĐÚNG origin nên tương đương "Clear site data" của Chrome cho riêng labs.google,
và loại bỏ hẳn cả lớp lỗi chính tả enum. Giữ **fallback** sang danh sách tường
minh (tên viết đúng spec) phòng bản Chrome/CDP nào đó không nhận `'all'`.

#### Lãng phí: gọi cho worker không bao giờ ở labs.google

`_cdp_clear_cache_and_cookies()` dùng CHUNG 4 call site, 3 trong đó là vòng lặp
Gemini/ChatGPT (đứng trên `gemini.google.com`/`chatgpt.com`). Gọi
`clearDataForOrigin` cho labs.google ở đó = 2 round-trip CDP mỗi batch không dọn
được gì. Gate `worker_mode in ('dom','api')` — cùng gate
`_recover_flow_project_page_after_cache_clear()` đã dùng.

#### Verify

Test cô lập (mock `_cdp`), 12 assertion / 4 nhóm: CDP nhận `'all'` → đúng 2
origin, đúng 1 lần/origin, không cảnh báo, vẫn chỉ xoá cookie `.labs.google`
(không đụng `.google.com`) · CDP từ chối `'all'` → fallback chạy đúng, dùng
`file_systems` đúng spec, không cảnh báo thừa · từ chối CẢ HAI → cảnh báo đúng 2
origin, KHÔNG raise · 3 worker_mode gemini/gemini_video/chatgpt → bỏ qua hẳn.

⚠️ 3 assertion đầu tiên lúc viết test báo sai — **lỗi ở TEST, không phải code**:
`'site data' in log` khớp luôn cả dòng tổng kết THÀNH CÔNG. Đã sửa thành chỉ
soi dòng `[warn]`.

`_test_batch_escalation.py` 8/8 · `_test_purge_site_data.py` 6/6 · `pyflakes`
sạch. **CHƯA verify trên Chrome thật** — cần user chạy tới rung 2-batch-lỗi và
xác nhận log `[batch-clean] ✔ Đã xoá cache + site data + N/M cookie...` không
kèm dòng `[warn] Xoá site data ... lỗi`.

---

### 2026-08-23 (d) | server/managers.py, server/worker.py, tests/_test_purge_site_data.py (MỚI) | FIX THẬT: "xoá cookie" lúc ngủ CHƯA SẠCH — bỏ sót localStorage/IndexedDB/Service Worker/Trust Tokens nên labs.google vẫn nhận ra profile cũ

User: *"clear cookie lúc ngủ ko sạch sẽ hay sao, tôi phải clear bằng tay tất cả
cookie trên trình duyệt thì không bị labs.google bắt lỗi"*.

#### Root cause

`_purge_all_cookies()` chỉ chạy `DELETE FROM cookies` trong SQLite. Nhưng nút
**"Clear browsing data → Cookies and other site data"** mà user bấm tay xoá RỘNG
HƠN NHIỀU — cookie chỉ là 1 trong ~10 kho trạng thái mỗi origin. Bỏ sót:

| Kho | Vì sao quan trọng với labs.google |
|---|---|
| `Local Storage` / `Session Storage` | session, cờ trạng thái, dấu vết phiên trước |
| `IndexedDB` | dữ liệu app phía client |
| `Service Worker` (+ `CacheStorage`) | **script nền ĐÃ ĐĂNG KÝ**, tự sống lại sau khi xoá cookie, có kho cache riêng |
| `Trust Tokens` | **token CHỐNG GIAN LẬN do chính Google phát hành** — đúng thứ dùng để nhận diện "trình duyệt đáng ngờ" |
| `Network Persistent State` / `Reporting and NEL` / `TransportSecurity` | dấu vết mạng gắn theo origin |

Xoá mỗi bảng `cookies` để lại toàn bộ số đó ⇒ log báo "đã xoá N cookie" nhưng
site vẫn nhận ra đúng profile cũ — khớp chính xác hiện tượng user gặp.

#### Fix 1 — lúc NGỦ: `_purge_all_site_data()` (managers.py, MỚI)

Thay `_purge_all_cookies()` trong `clear_cache_and_all_cookies()`. Quét GỐC
`profile_dir` + mọi thư mục con 1 cấp (`Default/`, `Profile 1/`...) + `*/Network/`
(cùng kiểu quét nông của `_clear_chrome_cache_only()`), xoá theo 2 whitelist tên
thư mục/tên file.

**XOÁ HẲN FILE `Cookies`** thay vì `DELETE FROM cookies` — dứt điểm hơn (không
để residue trong WAL/journal, không cần VACUUM), Chrome tự tạo lại DB rỗng.
Bậc thang này vốn đã chấp nhận mất đăng nhập nên không có gì cần giữ trong đó.

⚠️ **GIỮ LẠI có chủ đích** (đừng thêm vào danh sách xoá):
- `Login Data`/`Login Data For Account` — mật khẩu Chrome, cần cho
  `_ensure_google_login()` tự đăng nhập lại ở lần chạy sau.
- **`Local State`** (GỐC `profile_dir`) — chứa **KHOÁ os_crypt để giải mã
  `Login Data`**; xoá là mất luôn khả năng đọc mật khẩu đã lưu ⇒ hỏng hẳn bước
  tự đăng nhập lại.
- `Preferences`/`Secure Preferences`/`Web Data`/`History`/`Bookmarks`/`Extensions`.

`_purge_all_cookies()` GIỮ NGUYÊN code (không xoá), chỉ không còn caller — cùng
convention "tạm tắt, không xoá" của `_purge_non_login_cookies()`.

#### Fix 2 — MỖI BATCH: `_cdp_clear_labs_site_data()` (worker.py, MỚI)

Cùng lớp bug ở bước dọn cuối batch: đang xoá đúng cookie `labs.google` nhưng để
nguyên localStorage/IndexedDB/Service Worker của **CÙNG origin** ⇒ trang khôi
phục trạng thái phiên cũ ngay lần load kế tiếp, gần như vô hiệu hoá cả bước dọn.

Dùng CDP `Storage.clearDataForOrigin` giới hạn ĐÚNG theo origin `labs.google` —
**KHÔNG đụng Google login** (`accounts.google.com`/`.google.com` là origin khác),
giữ nguyên tinh thần "mỗi batch không được làm mất đăng nhập". CỐ Ý **không kèm
`cookies`** trong `storageTypes` — cookie đã xử lý CHỌN LỌC theo domain ở hàm
gọi, để CDP xoá thêm lần nữa chỉ làm 2 cơ chế chồng chéo.

#### Verify

`tests/_test_purge_site_data.py` (MỚI) — dựng cây profile GIẢ đúng cấu trúc thật
(gồm cả `Default/Network/`), 6 nhóm pass: xoá đủ **15** kho site-data · giữ
nguyên **10** mục không phải site-data (đặc biệt `Local State` + `Login Data`) ·
không lỗi · `removed` đếm đúng · gọi lần 2 trên profile đã sạch → no-op êm ·
thư mục không tồn tại → ghi `errors`, không raise.

Test cô lập `_cdp_clear_labs_site_data()` (mock `_cdp`) — 11 assertion: đúng 2
origin, `storageTypes` có đủ local_storage/indexeddb/service_workers/cache_storage,
**không** có `cookies`, vẫn chỉ xoá cookie `.labs.google` (không đụng
`.google.com`), site data chạy TRƯỚC bước xoá cookie (không bị return sớm bỏ qua
khi danh sách cookie rỗng), CDP không hỗ trợ → log chứ không raise.

`_test_batch_escalation.py` 8/8 và `_test_return_to_saved_project.py` 11/11 —
không regression. `py_compile`/`pyflakes` sạch.

**CHƯA verify trên Chrome/labs.google thật** — cần user chạy tới bậc ngủ thật rồi
xác nhận không còn phải tự tay Clear browsing data.

---

### 2026-08-23 (c) | server/worker.py, tests/_test_return_to_saved_project.py | Vá 3 kẽ hở khiến project VỪA TẠO bị "mồ côi" (id mới không được lưu) + tự đồng bộ project_url từ trình duyệt

User hỏi: *"có cơ chế khi tạo project mới có lưu id mới để lần sau quay lại
project id mới chưa"*. Có — cả 4 nơi tạo project (`_ensure_flow_page()`,
`_ensure_flow_project()`, `_rotate_project_if_full()`, `_reset_flow_project()`)
đều `pm.update(project_url=...)` + gán RAM. **Nhưng audit lộ ra 3 kẽ hở thật
quanh nó**, cả 3 đều dẫn tới cùng 1 hậu quả: Google ĐÃ tạo project mới, worker
đang làm việc trên đó, nhưng không nơi nào ghi lại id ⇒ lần chạy sau quay về
project CŨ, project mới thành mồ côi.

#### Kẽ hở 1 — `pm.update()` RAISE, và raise TRƯỚC dòng gán RAM

`pm.update()` **KHÔNG best-effort** (`managers.py::_api()` raise `RuntimeError`
khi backend chập chờn / HTTP lỗi / session hết hạn — chỉ `quiet=True` mới nuốt,
mà `update()` không truyền). Thứ tự cũ ở cả 4 nơi:

```python
pm.update(self.profile_id, project_url=new_url)   # ← raise ở đây
self.profile['project_url'] = new_url             # ← KHÔNG BAO GIỜ CHẠY
```

Hậu quả kép: (a) DB không lưu; (b) bản RAM cũng không đổi ⇒ **ngay trong phiên
hiện tại** worker vẫn tưởng mình đang ở project cũ; (c) exception lan ra khỏi
`_ensure_flow_page()`/`_rotate_project_if_full()` — chỗ gọi (`run()` lúc khởi
động, `_process_tasks()`, `_wait_and_reconcile_tasks()`) không hề bọc try/except
cho việc này, 1 lần mạng chập chờn có thể huỷ cả batch.

#### Kẽ hở 2 — `_wait_for_project_url()` không phân biệt project MỚI với CŨ

Chỉ check `'/project/' in cur`. Bấm "New project" mà Google không thật sự tạo
gì (hoặc điều hướng ngược về project cũ) ⇒ trả về URL **CŨ** ngay lần poll đầu
(2s) ⇒ log `✔ Đã tạo project mới` SAI. Nguy hiểm nhất ở
`_rotate_project_if_full()`: nó reset `_last_project_media_count = 0` ngay sau
đó, tức là **coi như đã sang project trống trong khi vẫn đang ở project đã đầy
300** — tiếp tục nhồi media vào project cũ cho tới lần reconcile sau mới phát
hiện lại.

#### Kẽ hở 3 — `_wait_for_project_url()` hết hạn (20s) nhưng project ĐÃ được tạo

Google điều hướng sang project mới muộn hơn 20s ⇒ code chỉ log warn rồi bỏ qua.
Trình duyệt đang ở project mới, DB trỏ project cũ — trạng thái lệch, chỉ tự sửa
khi worker restart (và lúc đó mất luôn project mới).

#### Fix

- **`_persist_project_url(new_url, reason)`** — điểm DUY NHẤT được phép ghi
  `project_url`, dùng bởi cả 4 nơi tạo mới + bước tự đồng bộ. **Gán RAM TRƯỚC,
  ghi DB SAU**, `pm.update()` bọc try/except → ghi DB lỗi thì phiên hiện tại
  vẫn chạy đúng và log cảnh báo, không huỷ batch.
- **`_wait_for_project_url(timeout, exclude_url='')`** — bỏ qua URL trỏ về đúng
  project đang loại trừ. Cả 4 call site truyền `exclude_url=profile['project_url']`.
- **`_sync_project_url_from_browser(current_url)`** — gọi ở đầu
  `_ensure_flow_page()`/`_ensure_flow_project()` khi đã đứng trên 1 trang
  `/project/`: id khác cái đã lưu ⇒ lưu lại theo cái đang đứng. Vá kẽ hở 3 (và
  cả phần ghi-DB-lỗi của kẽ hở 1) mà không cần đoán thêm timeout. So sánh bằng
  `_project_id_from_url()` (trích uuid, bỏ query/fragment) nên khác query string
  KHÔNG bị coi là project khác.
  - An toàn vì trong worker **không có đường nào** đưa trình duyệt tới project
    khác ngoài 4 nơi tạo mới + `_return_to_saved_project()` — đang đứng ở project
    nào tức là đang làm việc trên project đó.

**Không đổi** 2 đường luân chuyển project CỐ Ý: `_rotate_project_if_full()`
(ngưỡng `max_project_media_items` = 300) và `_reset_flow_project()` (bậc thang
`refresh_count_before_new_project`).

#### Verify

`tests/_test_return_to_saved_project.py` mở rộng 6 → **11 nhóm / 23 assertion**
(bỏ stub `_wait_for_project_url`, dùng hàm THẬT để test được `exclude_url`):
- (7) tạo project mới → ghi đúng `project_url` xuống server **và** đổi bản RAM.
- (8) `pm.update()` ném `RuntimeError` → **không** lọt exception ra ngoài, RAM
  vẫn trỏ project MỚI, có log cảnh báo.
- (9) trình duyệt ở project khác cái đã lưu → tự đồng bộ, ghi DB đúng 1 lần,
  **không** tạo project mới.
- (10) đúng project đã lưu nhưng khác query string → **không** ghi DB thừa.
- (11) `exclude_url` trùng project cũ → trả `''`; project khác → trả đúng URL.

`tests/_test_batch_escalation.py` chạy lại 8/8 (không regression).
`py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome/labs.google thật.**

---

### 2026-08-23 (b) | server/worker.py, tests/_test_return_to_saved_project.py (MỚI) | FIX THẬT: đăng nhập lại xong tạo project MỚI thay vì vào lại project CŨ — chỉ ngưỡng 300 media mới được luân chuyển project

User: *"xem lại luồng xóa cookie đăng nhập lại tới bước click create xong phải
vào lại link dự án cũ, khi nào bằng với setting như hiện tại 300 media thì mới
tạo project mới -> hiện tại hình như login lại là đã tạo project mới"*.

#### Root cause

`_ensure_flow_page()` và `_ensure_flow_project()` đều kết thúc bằng CÙNG 1 khối:

```python
if '/project/' not in current:
    if self._click_new_project_button():   # ← TẠO PROJECT MỚI
```

Khối này chạy khi **chỉ 1 lần** `driver.get(project_url)` không đưa được về
trang `/project/`. Ngay sau khi xoá SẠCH cookie + đăng nhập lại (bậc thang
batch, §11.39), lần vào đầu tiên **rất hay bị bật ra trang chung** — session
mới chưa "ấm", hoặc màn hình xen giữa "Create with Google Flow" lại hiện (URL
vẫn giữ `/project/{uuid}` suốt lúc đó, xem §11.28). Code cũ coi đó là "project
cũ hỏng" và bấm "New project" NGAY — trong khi `profile.project_url` vẫn còn
nguyên và project cũ vẫn dùng tốt.

Hai lỗ hổng cụ thể:
1. **Không thử lại.** Đúng 1 lần `driver.get()`, thất bại là tạo mới.
2. **`_ensure_flow_page()` bỏ qua hẳn project_url khi đã ở labs.google.** Khối
   navigate + xử lý màn hình xen giữa nằm TRONG `if 'labs.google' not in
   current:` — đang đứng ở trang CHUNG của labs.google (rất hay gặp sau khi
   đăng nhập lại) thì rơi thẳng xuống nhánh tạo mới, **chưa từng thử** vào
   `project_url` đã lưu lần nào.

Không có code nào xoá `project_url` lúc runtime (đã grep) — URL cũ luôn còn đó
để quay lại, chỉ là không ai thử.

#### Fix

`_return_to_saved_project(attempts=3)` (MỚI) — kiên trì quay lại đúng
`profile.project_url`: mỗi vòng `driver.get(saved)` → xử lý màn hình xen giữa
(`_click_create_with_flow_if_present()`, gọi LẠI mỗi vòng vì nó có thể hiện lại
ở session mới) → đọc lại URL. Chỉ khi HẾT 3 vòng vẫn không vào được mới trả
`False` — lúc đó caller mới được phép coi project cũ là hỏng/đã bị xoá.

3 nơi gọi:
- `_ensure_flow_page()` / `_ensure_flow_project()` — chặn TRƯỚC nhánh "New
  project"; tạo mới giờ kèm log `Không có project cũ khả dụng — tạo project mới.`
  để phân biệt với luân chuyển theo ngưỡng.
- `_recover_flow_project_page_after_cache_clear()` — sau khi click "Create with
  Google Flow", nếu 1 lần `driver.get(before)` vẫn chưa về được `/project/` thì
  gọi tiếp helper này (đúng yêu cầu *"tới bước click create xong phải vào lại
  link dự án cũ"*). **KHÔNG BAO GIỜ tạo project mới ở đây** — dọn dẹp cuối batch
  không phải chỗ luân chuyển project.

**Luân chuyển project vẫn CHỈ do `_rotate_project_if_full()`** (ngưỡng
`max_project_media_items`, mặc định 300, §11.20e) và `_reset_flow_project()`
(bậc thang `refresh_count_before_new_project`) — 2 đường CỐ Ý, không đụng tới.

#### Verify

`tests/_test_return_to_saved_project.py` (MỚI) — dựng `SeleniumFlowWorker` bằng
`object.__new__` + driver giả, kiểm ĐÚNG phần logic quyết định (không mở Chrome,
không cần mạng). 6/6 nhóm pass, 12 assertion:
- Lần vào đầu bị bật ra trang chung, lần 2 vào được → `True`, **0 lần** bấm "New
  project", đã thử đúng URL cũ 2 lần.
- Màn hình xen giữa luôn đẩy ra ngoài → `False` sau đủ số vòng.
- `project_url` rỗng → `False` ngay, không navigate lần nào.
- Project cũ đã bị xoá (luôn bật ra trang chung) → `False` sau đúng 3 lần thử.
- **Toàn luồng `_ensure_flow_page()`, đang ở trang chung labs.google** (đúng
  kịch bản user báo) → KHÔNG tạo project mới, vào lại đúng project cũ,
  `project_url` giữ nguyên.
- Toàn luồng, project cũ hỏng thật → CÓ tạo mới + log đúng lý do.

`py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome/labs.google thật** —
cần user chạy tới bậc thang xoá-cookie thật (2 batch lỗi liên tiếp) rồi xác
nhận worker quay lại đúng project cũ, không sinh project mới.

---

### 2026-08-23 | requirements.txt | Nâng selenium 4.46→4.47.0 và curl_cffi 0.15→0.16.1 (undetected-chromedriver + selenium-stealth ĐÃ là bản mới nhất)

Theo yêu cầu user nâng 4 gói `selenium` / `undetected-chromedriver` /
`selenium-stealth` / `curl_cffi` lên bản mới nhất. Tra PyPI thì **chỉ 2 gói
thực sự có bản mới hơn**:

| Gói | Trước | Sau | Ghi chú |
|---|---|---|---|
| `selenium` | 4.46.0 | **4.47.0** | phát hành 2026-08-10 |
| `curl_cffi` | 0.15.0 | **0.16.1** | phát hành 2026-08-21 |
| `undetected-chromedriver` | 3.5.5 | 3.5.5 | **ĐÃ mới nhất** — upstream ngừng phát hành từ 02/2024 |
| `selenium-stealth` | 1.0.6 | 1.0.6 | **ĐÃ mới nhất** — upstream ngừng phát hành từ 2020 |

⚠️ **Rủi ro chính đã kiểm chứng**: `undetected-chromedriver` 3.5.5 (02/2024)
VÁ NỘI BỘ Selenium, mà Selenium 4.47 phát hành 08/2026 — cách nhau ~2.5 năm,
thừa sức vỡ nếu Selenium bỏ API cũ. **Đã verify THẬT (không mock)**: chạy
`uc.Chrome()` MỞ CHROME THẬT (headless, `user-data-dir` tạm, `version_main`
lấy từ `_detect_chrome_version()` y hệt `worker.py`) → mở được, `driver.get()`
+ `find_element()` đọc DOM bình thường, Chrome major 151. `uc.ChromeOptions()`
(dùng bởi `chrome_utils._build_chrome_options`) và `selenium_stealth.stealth()`
cũng import + gọi được.

**curl_cffi 0.16**: đã verify 4 target impersonate mà
`worker.py::_post_via_curl_cffi()` HARDCODE (`chrome131`/`chrome136`/
`chrome124`/`chrome`) vẫn dùng được — gọi THẬT tới
`https://www.google.com/generate_204`, cả 4 trả HTTP 204. Các thuộc tính
response code phụ thuộc (`.ok`/`.status_code`/`.text`/`.json()`) không đổi.

**Không phải sửa code nào** — audit các API Selenium hay bị gỡ
(`desired_capabilities`, `find_element_by_*`, `webdriver.Chrome(executable_path=)`)
đều KHÔNG xuất hiện trong `server/`. Chỗ duy nhất dùng `executable_path` là
`Service(executable_path=...)` — vẫn là API HỢP LỆ của Selenium 4.x (thứ bị gỡ
từ 4.10 là `webdriver.Chrome(executable_path=)`, khác hẳn), đã verify bằng
`inspect.signature(Service.__init__)`.

**Verify thêm**: `pip check` sạch ở CẢ 2 venv (`venv/` Python 3.10 và
`client_tool/venv/` Python 3.13); `compileall` + `pyflakes` sạch toàn bộ
`server/`, `gui/`, `main.py`, `selenium_flow.py`; import THẬT chuỗi module
nặng nhất (`server.worker`) với Selenium mới → OK, `HAS_CFFI=True`,
`_build_chrome_options(use_uc=True)` dựng đủ 25 argument.

**CHƯA verify**: chưa chạy 1 task THẬT end-to-end qua hàng đợi (mở profile
thật → nhận task → generate). Cần user chạy thử 1 profile để chắc chắn.

---

### 2026-08-20 | server/worker.py, gui/pages/settings_page.py | TẠM TẮT bậc thang "time-window" (`error_window_minutes`/`error_window_max_errors`) + ẩn 2 setting khỏi trang Cài đặt — nó cướp quyền bậc thang THEO BATCH vừa thêm

User báo log thật ngay sau khi chạy bản có bậc thang THEO BATCH (§11.39):

```
13:33 [selenium-flow] INFO [profile-25] [error] 5 lỗi trong 50 phút (time-window) — cho profile "ngủ" 60s
```

kèm yêu cầu *"theo luồng mới ẩn setting này trước, tạm thời ko dùng nữa gây xáo
trộn luồng"*.

**Root cause (đọc thẳng từ log user gửi, không suy đoán):** bậc thang
"time-window" (`_record_error()`, thêm 2026-07-xx) đếm lỗi **TÍCH LUỸ THEO THỜI
GIAN**, hoàn toàn KHÔNG biết ranh giới batch — 5 lỗi rải rác suốt **50 PHÚT**
(hoàn toàn có thể xen kẽ nhiều batch THÀNH CÔNG ở giữa) vẫn ép profile ngủ. Vì
`_record_error()` được gọi ở **MỌI** nhánh lỗi (kể cả 2 nhánh phụ trong
`_run_tasks_batch`) và được `_handle_task_error()` gọi **ĐẦU TIÊN** rồi
`return True` ngay nếu nó cho ngủ, nó thường xuyên **CƯỚP QUYỀN** của bậc thang
THEO BATCH trước khi bậc đó kịp chạy hết chuỗi *"2 batch lỗi → dọn cookie +
click Create → batch thứ 3 → ngủ"* — đúng nghĩa "gây xáo trộn luồng".

**Thay đổi:**
- `worker.py::_record_error()` — khối time-window bọc trong `if False:` (GIỮ
  NGUYÊN code, cùng convention "tạm tắt, không xoá" đã dùng cho
  `_purge_non_login_cookies()`). Hàm giờ LUÔN trả `False`; phần bookkeeping
  (`_task_error_count`/`_last_error_msg`/`_last_error_at`/`pm.bump_task_stat`)
  **VẪN chạy nguyên** — dashboard/thống kê ngày không đổi. Bật lại: `if False:`
  → `if True:`.
- `gui/pages/settings_page.py` — 2 setting chuyển từ `_FIELD_DEFS` sang
  `_HIDDEN_FIELD_DEFS` (danh sách MỚI, chỉ để tài liệu hoá chính xác cần trả
  lại dòng nào khi bật lại; không widget nào đọc nó).

**KHÔNG xoá 2 key khỏi `_DEFAULT_SERVER_SETTINGS`/`_clamp()`** — `local_settings.json`
trên máy user đã lưu sẵn `error_window_minutes: 50`, xoá key sẽ làm giá trị đó
biến mất ở lần `update_local_settings()` kế tiếp. Đã verify: `_on_save()` chỉ
build payload từ `_FIELD_DEFS` rồi `PATCH` (merge từng key), nên field ẩn KHÔNG
bị wipe khi user bấm Lưu.

**2 bậc thang CÒN LẠI KHÔNG bị ảnh hưởng** (verify tường minh, không chỉ suy
luận): (1) escalation LIÊN TIẾP `_handle_task_error()` (`error_count_before_refresh`
→ refresh → `refresh_count_before_new_project` → project mới → ngủ); (2) bậc
thang THEO BATCH §11.39.

**Verify THẬT** (`tests/_test_timewindow_disabled.py`, chạy code THẬT):
5/5 nhóm pass — (1) tái hiện ĐÚNG kịch bản user: bắn 10 lỗi liên tiếp (gấp đôi
ngưỡng cũ 5) → `_record_error()` KHÔNG BAO GIỜ trả `True`, `_sleep_until` vẫn 0,
KHÔNG ghi `_sleep_until_by_pid`, KHÔNG set `status='sleeping'`, nhưng
`_task_error_count`/`lastErrorMsg` VẪN cập nhật đúng; (2) 3 lỗi liên tiếp vẫn
refresh đúng 1 lần; (3) bậc thang LIÊN TIẾP vẫn tới được nhánh ngủ cuối cùng
(không bị tắt nhầm); (4) bậc thang BATCH vẫn chạy đủ 3 nấc; (5) mô phỏng ĐÚNG
payload GUI gửi khi bấm Lưu → `error_window_minutes` giữ nguyên giá trị **50**
thật của user, không bị wipe. Chạy lại `tests/_test_batch_escalation.py` — vẫn
8/8 pass (không regression). `py_compile`/`pyflakes` sạch.

**CHƯA verify trên Chrome THẬT** — cần user restart worker (settings + code đọc
snapshot lúc worker khởi động) rồi xác nhận KHÔNG còn thấy log `(time-window)`
nữa, và chuỗi 3 batch lỗi chạy đúng thứ tự.

---

### 2026-08-20 | server/{config.py,local_settings.py,state.py,managers.py,worker.py}, gui/pages/settings_page.py | Bậc thang escalation THEO BATCH — batch có ≥1 task thành công = thành công; 2 batch lỗi liên tiếp → xoá cookie labs.google + click "Create with Google Flow"; 3 batch → ngủ + xoá TẤT CẢ cookie/cache + ép check đăng nhập Google

Theo yêu cầu user: *"Nếu batch gửi lên 5 task 1 lúc mà thành công 1 vẫn tính
batch thành công -> nhưng nếu cả batch đều không thành công liên tiếp 2 batch
liền -> thì mới xóa cache cookie không ảnh hưởng login rồi click button create
lại -> nhưng vẫn lỗi tiếp tục batch kế tiếp đó -> thì chuyển sang chế độ ngủ ->
trong lúc ngủ xóa tất cả cookie - cache -> rồi check login -> đăng nhập vào
project lại như trước -> vòng lặp cứ thế"*.

**Bậc thang THỨ 3, SONG SONG (KHÔNG thay thế) 2 bậc thang THEO TASK đã có:**
(1) `error_count_before_refresh`/`refresh_count_before_new_project` (lỗi LIÊN
TIẾP từng task → refresh → project mới → ngủ); (2) `error_window_minutes`/
`error_window_max_errors` (N lỗi trong M phút). Bậc thang MỚI đếm ở mức BATCH
(1 lần heartbeat nhận về N task) — bắt đúng lớp sự cố 2 bậc kia bỏ sót: **cả lô
cùng chết vì 1 nguyên nhân chung** (session/cookie hỏng, project bị chặn) chứ
không phải vài task lẻ lỗi rải rác.

**2 setting MỚI** (`config.py::_DEFAULT_SERVER_SETTINGS`, sửa được ở GUI trang
Cài đặt): `batch_fail_count_before_cleanup` (mặc định 2),
`batch_fail_count_before_sleep` (mặc định 3). `local_settings.py::_clamp()` thêm
cả 2 vào `int_min1_keys` + **ràng buộc chéo**: ngưỡng ngủ ≤ ngưỡng dọn thì tự
nâng lên `dọn + 1` — đặt sai sẽ khiến bậc "dọn cookie" KHÔNG BAO GIỜ chạy được
(chạm ngưỡng ngủ trước, worker thoát luôn), tự sửa còn hơn im lặng vô hiệu hoá
mất 1 bậc.

**⚠️ ĐỔI HÀNH VI CŨ (2026-08-17/18)** — theo trả lời của user cho AskUserQuestion
(*"theo phương án 1: nhưng không click create vì chỉ khi xóa cookie mới cần
click"*): TRƯỚC ĐÂY `_cdp_clear_cache_and_cookies()` + click "Create with Google
Flow" chạy sau **MỌI** batch vô điều kiện. Giờ:

| Kết quả batch | refresh + reconcile | xoá cookie+cache | click "Create with Google Flow" |
|---|---|---|---|
| Thành công (≥1 task ok) | ✔ | ✘ | ✘ |
| Lỗi, chưa chạm ngưỡng | ✔ | ✘ | ✘ |
| Lỗi, chạm ngưỡng dọn (2) | ✔ | ✔ (chỉ domain `labs.google`, GIỮ login Google) | ✔ |
| Lỗi, chạm ngưỡng ngủ (3) | — | ✔ **TẤT CẢ** cookie + cache (lúc ngủ) | — |

Bước **refresh + reconcile KHÔNG bị gate** ở mọi nhánh — đó là đường DUY NHẤT
media đã render xong được ghi nhận `done` về server sau batch, gate nó sẽ làm
task hoàn tất không bao giờ được đánh dấu.

**Code:**
- `worker.py::_handle_task_success()` — tăng `_batch_success_count`. Đây là điểm
  funnel **DUY NHẤT** của mọi đường "task thành công" (9 call site: DOM batch/API
  batch/tuần tự/reconcile) nên đếm 1 chỗ là phủ hết mọi `worker_mode`, không cần
  rải counter ra từng nhánh.
- `worker.py::_process_tasks()` — reset `_batch_success_count = 0` ở **ĐẦU mỗi
  batch** (bug thật bắt được lúc test: chỉ khởi tạo ở `__init__` thì bộ đếm tích
  luỹ qua các batch → chỉ cần 1 task thành công ở batch đầu là MỌI batch lỗi về
  sau đều bị hiểu nhầm "thành công", bậc thang không bao giờ kích hoạt).
- `worker.py::_finish_batch_cleanup()` (MỚI) — đánh giá + áp bậc thang, gọi trong
  `finally` của `_process_tasks()`.
- `worker.py::_recover_flow_project_page_after_cache_clear()` — thêm tham số
  `click_create: bool = True`; `False` bỏ hẳn bước poll 6s tìm nút Create (màn
  hình xen giữa đó CHỈ xuất hiện khi session/cookie vừa reset).
- `managers.py::_purge_all_cookies()` + `ProfileManager.clear_cache_and_all_cookies()`
  (MỚI) — xoá SẠCH bảng `cookies` (kể cả họ SID đăng nhập). KHÁC
  `_purge_non_login_cookies()` (giữ login, KHÔNG đụng tới — vẫn tạm không có
  caller) và KHÁC `clear_browser_data()` (xoá NGUYÊN `profile_dir`, mất luôn
  `Login Data`/mật khẩu Chrome đã lưu — chỉ dùng cho nút "Làm mới profile" thủ
  công). Ở đây GIỮ `Login Data` để bước đăng nhập lại tự động dùng được.
- `state.py::_force_login_check` (MỚI, set module-level) + `worker.py::
  _ensure_google_login()` đọc-và-`discard()` (cờ dùng-1-lần) — **BẮT BUỘC** vì
  `google_login_check_enabled` mặc định TẮT (0): vừa xoá sạch cookie mà bỏ qua
  check thì worker mới vào thẳng labs.google ở trạng thái chưa đăng nhập và MỌI
  task sau đó đều lỗi. Phải sống module-level (không phải trên object worker) vì
  worker cũ sắp thoát hẳn — worker mới sau khi hết ngủ là instance khác hoàn
  toàn (cùng lý do/cùng pattern `_sleep_until_by_pid`).
- `worker.py::_clear_profile_if_sleeping()` — rẽ nhánh theo `_sleep_wipe_all_cookies`:
  bậc thang BATCH → `clear_cache_and_all_cookies()` + set `_force_login_check`;
  2 bậc thang TASK cũ → giữ NGUYÊN `clear_cache_only()` (không đụng cookie,
  không ép login) — 2 đường tách biệt hoàn toàn.
- `worker.py::live_info()` — thêm `consecutiveFailedBatches` cho dashboard.

**⚠️ Bẫy `return` trong `finally` (tự phát hiện + tránh ngay khi code, KHÔNG
phải test fail):** bản nháp đầu định `return escalated` từ trong `finally` của
`_process_tasks()` để truyền quyết định "ngủ" ra ngoài — `return` trong `finally`
**NUỐT LUÔN exception đang lan ra**, batch lỗi nặng sẽ biến mất im lặng và
`run()` mất cả log lẫn bước check `_is_driver_alive()`. Thay bằng:
`_finish_batch_cleanup()` tự set `self._sleep_until`, còn `run()` check mốc đó
**sau CẢ `try` LẪN `except`** — nhờ đặt sau cả 2 nhánh nên bắt được luôn trường
hợp batch kết thúc bằng exception (lúc đó `finally` vẫn đã chạy bậc thang batch).

**Verify THẬT** (`tests/_test_batch_escalation.py`, chạy code THẬT — chỉ stub các
bước đụng Chrome/HTTP; `tests/` gitignore theo convention sẵn có):
8/8 nhóm pass — (1) batch 1/5 task ok → reset bộ đếm, KHÔNG xoá cookie, KHÔNG
click Create; (2) lỗi lần 1 → chỉ đếm; (3) lỗi lần 2 → xoá cookie + click Create,
bộ đếm KHÔNG reset; (4) lỗi lần 3 → ngủ đúng 300s + ghi `_sleep_until_by_pid`;
(5) lúc ngủ → gọi đúng `clear_cache_and_all_cookies` (không phải `clear_cache_only`)
+ set `_force_login_check` + cờ tự tắt; (6) worker MỚI → `_ensure_google_login()`
BỊ ÉP vào `gmail.com` dù setting TẮT, cờ bị tiêu thụ đúng 1 lần; (7) sleep theo
bậc thang TASK cũ → vẫn CHỈ xoá cache, không ép login (không regression);
(8) `_clamp()` tự nâng ngưỡng ngủ khi cấu hình sai.
Thêm test SQLite THẬT cho `_purge_all_cookies()`: xoá 5/5 cookie kể cả SID/HSID,
đối chứng `_purge_non_login_cookies()` vẫn giữ đúng SID/HSID/chatgpt (không bị
ảnh hưởng), profile chưa có Cookies DB → trả 0 không raise. Round-trip setting
qua `local_settings` thật: default 2/3, PATCH 4/7 OK, PATCH 0/-5 → clamp 1/2,
reset về 2/3. `py_compile`/`pyflakes` sạch 6 file.

**CHƯA verify trên Chrome/labs.google THẬT** — môi trường này không chạy được
browser; cần user chạy 1 phiên thật để xác nhận chuỗi 3 batch lỗi liên tiếp
kích hoạt đúng thứ tự và bước tự đăng nhập lại sau khi ngủ hoạt động.

---

### 2026-08-20 | server/chrome_utils.py, server/worker.py, server/routes.py | Fix warning "Connection pool is full, discarding connection: localhost" khi chạy batch VEO ảnh/video thời gian dài — tăng maxsize pool urllib3 của Selenium→chromedriver

User báo warning `Connection pool is full, discarding connection: localhost.
Connection pool size: 10` xuất hiện khi chạy batch video/image VEO thời gian
dài. Điều tra loại trừ từng nguồn "localhost" HTTP client có trong repo trước
khi kết luận:

- **Loại trừ mọi lời gọi `requests.get()`/`requests.post()` tới backend**
  (`FLOW_API_URL=http://localhost:13443` — `managers.py::_api()`,
  `worker.py`'s hàng chục lời gọi heartbeat/task-status/upload...): `req_lib`
  = MODULE `requests` (không phải `requests.Session()` instance dùng chung —
  xác nhận qua grep TOÀN REPO, 0 hit `requests.Session()`). Mỗi lời gọi module-
  level `requests.get()`/`requests.post()` tự tạo 1 `Session()` (tức 1
  `PoolManager` RIÊNG) rồi đóng ngay sau khi xong — KHÔNG có pool nào sống đủ
  lâu/dùng chung đủ rộng để tích luỹ >10 connection cùng lúc.
- **Xác nhận nguồn thật: `RemoteConnection` của Selenium nói chuyện với
  chromedriver cục bộ** — `Service.service_url` (`selenium/webdriver/common/
  service.py`) trả về LITERALLY `f"http://localhost:{port}"` (khớp CHÍNH XÁC
  chuỗi "localhost" trong warning — không phải `127.0.0.1`). Đây là HTTP
  client urllib3 DUY NHẤT trong toàn bộ luồng chạy giữ 1 `PoolManager` SỐNG
  SUỐT vòng đời 1 phiên Chrome/chromedriver (`keep_alive=True` mặc định của
  Selenium 4.x) — khớp đúng "batch chạy lâu" (nhiều lệnh DOM/CDP liên tiếp:
  `_wait_js()` poll dồn dập, round-robin Gemini chuyển tab mỗi
  `gemini_tab_switch_interval`≈0.5s, `_drain_perf_logs()`...). urllib3 mặc
  định `HTTPConnectionPool(maxsize=10, block=False)` — vượt quá 10 connection
  "đang dùng" cùng lúc tại 1 thời điểm sẽ bị DISCARD khi trả về pool (không
  chờ, chỉ log warning rồi vứt) thay vì tái sử dụng — vô hại về chức năng
  (Selenium tự mở connection mới) nhưng tốn handshake TCP lặp lại vô ích suốt
  batch, và là nguồn warning user thấy.

**Fix — `chrome_utils.py::_patch_selenium_pool_size(maxsize=30)`** (mới, đọc
override qua env `SELENIUM_POOL_MAXSIZE`) — monkeypatch
`RemoteConnection._get_connection_manager()` (dùng CHUNG bởi CẢ
`webdriver.Chrome()` LẪN `undetected_chromedriver.Chrome()` — cả 2 đều kế
thừa `ChromiumRemoteConnection`→`RemoteConnection`, patch 1 chỗ phủ cả 2): để
NGUYÊN hàm gốc chạy trọn vẹn (giữ đúng mọi nhánh proxy/cert/timeout đã có),
chỉ MUTATE `PoolManager.connection_pool_kw` (dict THUẦN urllib3 dùng LƯỜI mỗi
khi tạo pool con theo host/port) của kết quả trả về TRƯỚC KHI có pool con nào
được tạo — an toàn tuyệt đối vì `_get_connection_manager()` chỉ gọi ĐÚNG 1
LẦN trong `RemoteConnection.__init__()`, PoolManager trả về chưa từng phục vụ
request nào. Idempotent (patch 1 lần/process, guard `_selenium_pool_patched`)
— gọi ở CẢ 3 điểm tạo driver trong repo: `_connect_to_chrome()`
(chrome_utils.py, nhánh attach login browser qua debuggerAddress),
`_make_driver()` (worker.py, nhánh mở Chrome mới, phủ cả `webdriver.Chrome()`
lẫn `uc.Chrome()`), và `routes.py::_open()` (route "Mở login browser" —
`POST /api/selenium/profiles/<id>/open`, vòng lặp giữ Chrome sống có thể chạy
LÂU không kém, cùng đáng patch).

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT (không mock) — dựng 1
`RemoteConnection` thật (Selenium thật, không cần Chrome/chromedriver chạy
sẵn — `_get_connection_manager()` chỉ build `PoolManager`, không tự mở kết
nối) sau khi gọi `_patch_selenium_pool_size(maxsize=42)`: `connection_pool_kw`
đúng `{'maxsize': 42, 'block': False, ...}` (các key gốc `timeout`/
`cert_reqs`/`ca_certs` vẫn nguyên vẹn — xác nhận không phá nhánh xử lý
cert/timeout của hàm gốc); gọi lại `_patch_selenium_pool_size()` lần 2 không
re-wrap (idempotent xác nhận qua so sánh identity của method).

**CHƯA verify trên browser/batch thật** — cần user tự chạy 1 batch VEO
video/image dài như trước, xác nhận warning không còn xuất hiện (hoặc giảm
hẳn tần suất). Nếu VẪN còn xuất hiện sau fix này, đó là bằng chứng nguồn
warning KHÁC với giả thuyết ở trên (vd thật sự có truy cập đồng thời từ
nhiều thread vào CÙNG 1 driver mà chưa phát hiện ra) — cần log lại chính xác
thời điểm/tần suất để điều tra tiếp.

---

### 2026-08-19 | server/worker.py | Fix bug thật: upload ảnh `.jfif` vào Gemini/ChatGPT — không hiện preview, Gemini báo lỗi "không đủ ảnh" không chạy prompt

User báo: "upload file .jfif nó vẫn nhận nhưng ko show được ảnh preview lên
chat rồi gemini bắt lỗi không đủ ảnh hay gì mà ko chạy prompt trong
client_tool".

**Root cause:** khi tải ảnh tham chiếu về từ server rồi đính kèm qua
`send_keys()`/CDP `DOM.setFileInputFiles` vào `<input type="file">` của
Gemini/ChatGPT, worker giữ NGUYÊN tên file gốc (`os.path.basename(url.split
('?')[0])`) — nếu ảnh gốc có đuôi `.jfif`, file tải về trên đĩa cũng mang
đuôi `.jfif`. Chrome tự suy `File.type` (MIME) của object `File` dựa THEO
ĐUÔI TÊN FILE trên đĩa (không đọc nội dung byte) — bảng tra MIME-theo-đuôi
nội bộ của Chrome KHÔNG có entry đáng tin cậy cho `.jfif` trên nhiều máy
Windows (khác `.jpg`/`.jpeg` luôn có sẵn) → `File.type` ra rỗng/
`application/octet-stream`. Input vẫn "nhận" file (không báo lỗi ngay, đúng
điều user quan sát) nhưng JS phía Gemini lọc preview theo
`file.type.startsWith('image/')` — không khớp nên KHÔNG hiện thumbnail; lúc
submit, server Gemini đếm số ảnh THẬT SỰ đính kèm hợp lệ = 0, trả lỗi kiểu
"thiếu ảnh" dù người dùng thấy rõ đã "upload thành công".

**Fix:** `_normalize_attach_filename()` (hàm mới, module-level, cạnh
`_MEDIA_MIME_BY_EXT`) — đổi đuôi `.jfif`/`.pjpeg`/`.pjp` (đều là biến
thể/alias của JPEG File Interchange Format, dữ liệu byte đã LÀ JPEG hợp lệ,
không cần re-encode) → `.jpg` trước khi ghi file tải về ra đĩa. Áp dụng ở
CẢ 4 nơi client_tool tải ảnh tham chiếu rồi đính kèm Gemini/ChatGPT:
`_run_task_gemini()`, `_gemini_slot_download_and_kickoff()` (round-robin
nhiều tab), `_run_task_gemini_video()`, `_run_task_chatgpt()`. KHÔNG ảnh
hưởng luồng DOM Flow VEO3 (`_dom_upload_images()`) — mode đó dùng base64
inject qua JS (`DataTransfer`+`File` dựng thủ công có set `type` tường
minh), không đi qua `send_keys()`/file input native nên không dính bug này.

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT qua venv (không mock) —
8/8 case: `.jfif`→`.jpg`, `.JFIF`→`.jpg` (không phân biệt hoa/thường),
`.pjpeg`→`.jpg`, `.pjp`→`.jpg`, `.jpg`/`.png` giữ nguyên, tên không có đuôi
giữ nguyên, tên có nhiều dấu chấm (`weird.name.jfif`) đổi đúng CHỈ đuôi
cuối. **CHƯA verify trên Chrome/Gemini thật** — cần user tự upload lại 1
file `.jfif` làm ảnh tham chiếu, xác nhận preview hiện đúng trong chat và
prompt chạy được.

---

### 2026-08-18 | server/worker.py | ĐỔI HƯỚNG quy trình cuối mỗi batch — refresh trang project TRƯỚC khi chờ (thay vì chờ thụ động), chờ = setting `task_delay_secs`, VÀ dời bước reconcile "lấy task đã hoàn tất" từ đầu batch SAU sang cuối batch NÀY (ngay sau khi bấm "Create with Google Flow")

Theo yêu cầu user mô tả LẠI TOÀN BỘ quy trình mong muốn: "phần client_tool
profile VEO đổi cơ chế mới nhập xong batch xóa cookie labs.google -> refresh
trang project -> lấy setting thời gian khoảng cách giữa 2 batch làm thời
gian chờ -> xong click nút create with google flow -> rồi lại refresh trang
project lấy task đã hoàn tất -> nhận batch tiếp theo nếu có." — thay thế bản
đầu cùng ngày (mục ngay dưới, "sau khi xoá cache/cookie mỗi batch — check
nút Create with Google Flow...") chỉ CHỜ THỤ ĐỘNG (`settle_wait_secs=3.0`
hardcode) cho trang tự phát hiện mất cookie, KHÔNG chủ động refresh, và KHÔNG
có bước reconcile nào ở cuối batch cả (reconcile trước đó nằm ở ĐẦU batch
KẾ TIẾP, xem entry "Reconcile TOÀN BỘ project..." bên dưới).

**`_recover_flow_project_page_after_cache_clear()` viết lại theo ĐÚNG 5
bước user mô tả** (gọi trong `finally` của `_process_tasks()`, ngay sau
`_cdp_clear_cache_and_cookies()`, chỉ khi `worker_mode in ('dom','api')` và
KHÔNG escalate):
1. `driver.refresh()` NGAY (chủ động, khác bản đầu chỉ chờ thụ động).
2. Chờ `task_delay_secs` (Cài đặt "Delay giữa các task trong batch (giây)")
   — KHÔNG có setting riêng tên "khoảng cách giữa 2 batch" trong hệ thống,
   đây là setting delay/spacing DUY NHẤT sẵn có nên TÁI DÙNG làm thời gian
   chờ bước này (tham số hàm `settle_wait_secs` giữ lại CHỈ để override thủ
   công lúc test, `None` = mặc định đọc setting này).
3. `_click_create_with_flow_if_present()` (không đổi, timeout poll riêng 6s).
4. Quay lại đúng URL project nếu bấm nút lỡ điều hướng đi nơi khác (không đổi).
5. **MỚI** — `_reconcile_project_media(set())` NGAY SAU đó (refresh LẦN 2 +
   đọc `projectInitialData`, tải về/đánh dấu `done` media đã render xong mà
   server chưa biết) — "lấy task đã hoàn tất" đúng nghĩa đen.

**Xoá đoạn code "reconcile ở ĐẦU `_process_tasks()`"** (thêm sáng cùng ngày
này, xem entry "Reconcile TOÀN BỘ project..." bên dưới) — CHUYỂN HẲN sang
làm ở CUỐI batch TRƯỚC (bước 5 ở trên) thay vì đầu batch SAU, đúng thứ tự
user mô tả, tránh refresh+đọc `projectInitialData` 2 LẦN LIÊN TIẾP cho CÙNG
1 mục đích (mỗi lần tốn ~15s chờ interceptor) — "cuối batch N" và "đầu
batch N+1" là CÙNG 1 thời điểm về luồng chạy khi không có gì xen giữa
ngoài chờ heartbeat, nên gộp lại 1 chỗ duy nhất là đủ.

Verify: `python -m py_compile server/worker.py` + `python -m pyflakes`
sạch. **CHƯA verify trên browser thật** (môi trường này không chạy được
Chrome/client_tool) — cần user chạy vài batch thật để xác nhận đúng thứ tự
refresh→chờ→click→refresh-lại-lấy-task-hoàn-tất→nhận batch tiếp theo.

---

### 2026-08-18 | server/worker.py | Reconcile TOÀN BỘ project ĐÃ LƯU 1 lần TRƯỚC khi bắt đầu xử lý batch task MỚI nhận được — nhặt media mồ côi chưa báo lên server, không còn giới hạn theo retry_count

Theo yêu cầu user "check client_tool profile khi phát hiện task thì chưa
nhận phải vào link project đã lưu xem có task nào hoàn tất chưa cập nhật
rồi mới xem task chưa xong thì nhận mỗi lần nhận task mới đều phải check
project hiện tại có chưa".

**Trước đây:** reconcile-trước-khi-submit CHỈ chạy khi `task.retry_count >
0` (`_run_task_dom`/`_run_tasks_batch`, quyết định 2026-07-16 "task lần
đầu chắc chắn chưa từng submit prompt này, không cần tốn 1 lần refresh+15s
chờ interceptor cho mọi task"). Lý do đó ĐÚNG cho CHÍNH task đang xét,
nhưng bỏ sót media của **TASK KHÁC** đang mắc kẹt trong CÙNG project mà
worker hoàn toàn không biết — vd: 1 task video render xong thật trên
project này, nhưng server reap/timeout nó về `pending` rồi giao lại cho
máy khác (hoặc cùng máy này nhưng sau khi `project_url` đã rotate sang
project khác qua `_rotate_project_if_full()`) — media đó mồ côi vĩnh viễn,
không bao giờ được reconcile trừ khi TÌNH CỜ có 1 task retry khác chạy lại
đúng project cũ đó.

**Fix:** `_process_tasks()` — ngay sau khi khởi động keepalive thread
(TRƯỚC dispatch tới `_run_tasks_batch`/`_run_tasks_api_batch`/vòng lặp
`_run_task` tuần tự, tức TRƯỚC KHI đụng vào batch task VỪA nhận được từ
heartbeat) — với `worker_mode in ('dom','api')`: `_ensure_flow_page()`
(đảm bảo đang đứng đúng `profile['project_url']` — "link project đã lưu",
no-op nếu đã ở đúng trang) rồi `_reconcile_project_media(set())` — hàm này
tự xác nhận từ trước (2026-07-17) KHÔNG lọc theo `task_ids` truyền vào, tự
quét TOÀN BỘ `projectInitialData` của project và so với DB, nên truyền
`set()` rỗng vẫn reconcile đầy đủ mọi media mồ côi tìm được. Chạy ĐÚNG 1
LẦN/BATCH (không phải 1 lần/task) — tránh tốn N lần refresh+chờ
interceptor cho 1 batch N task như lo ngại ban đầu của quyết định cũ. Lỗi
ở bước này chỉ log warn, KHÔNG chặn batch (best-effort).

2 chỗ check theo `retry_count` cũ ở `_run_task_dom`/`_run_tasks_batch`
**GIỮ NGUYÊN, không xoá** — mục đích khác: match ĐÚNG 1 `task_id` cụ thể
để quyết định có submit lại hay không (bỏ qua tạo mới nếu đã có media),
không phải quét "nhặt rác" toàn project như bước mới thêm ở trên — 2 cơ
chế bổ sung nhau, không thay thế.

Verify: `python -m py_compile server/worker.py` + `python -m pyflakes` sạch.
Vị trí insertion đặt SAU `keepalive_thread.start()` (đảm bảo `last_seen`
vẫn tươi trong lúc reconcile — có thể mất tới ~15s do
`_dom_fetch_project_media()`'s refresh+chờ interceptor). **CHƯA verify
trên browser thật** (môi trường này không chạy được Chrome/client_tool) —
cần user chạy 1 batch task thật (có sẵn 1 media mồ côi cũ trong project để
kiểm chứng nó được nhặt lại trước khi task mới bắt đầu).

---

### 2026-08-18 | server/worker.py | Sau khi xoá cache/cookie mỗi batch — check nút "Create with Google Flow", bấm rồi quay lại đúng trang project hiện tại

Theo yêu cầu user "sau khi clear cache phải check có nút Create with Google
Flow click vào mới quay lại trang project hiện tại". Bối cảnh: `_cdp_clear_
cache_and_cookies()` (mục ngay dưới) giờ xoá cookie của CHÍNH domain
`labs.google` khỏi trang ĐANG MỞ (không reload) — thao tác/điều hướng KẾ TIẾP
trên trang đó có thể bị Google chặn lại bởi màn hình xen giữa "Create with
Google Flow" (CÙNG cơ chế đã biết ở `_click_create_with_flow_if_present()`,
§11.28 — trước đây chỉ gọi sau `driver.get()` khi NAVIGATE tới 1 URL project,
chưa từng gọi ngay sau bước dọn cache/cookie này).

Hàm mới `_recover_flow_project_page_after_cache_clear()` — gọi
`_click_create_with_flow_if_present()` NGAY TRÊN TRANG HIỆN TẠI (không cần
`driver.get()` trước, hàm đó tự poll DOM); nếu có bấm và trang bị điều hướng
lệch khỏi `/project/{uuid}` ban đầu, `driver.get()` LẠI đúng URL đã lưu trước
đó (đúng yêu cầu "click vào mới quay lại trang project hiện tại") — mirror
CHÍNH XÁC pattern "bấm xong lỡ điều hướng ra khỏi URL project ban đầu — quay
lại đúng url cũ" đã có sẵn ở `_ensure_flow_page()`/`_ensure_flow_project()`.

**Chỉ wire vào ĐÚNG 1 trong 4 call site của `_cdp_clear_cache_and_cookies()`**
— `_process_tasks()`, gated thêm `if self.worker_mode in ('dom','api')`.
Lý do: audit cả 4 call site xác nhận CHỈ `_process_tasks()` (dom/api VEO3
lẫn `gemini_video`) có thể đang đứng trên trang labs.google project thật —
3 call site còn lại (`_run_gemini_loop()`, `_run_gemini_loop_concurrent()`,
`_run_chatgpt_loop()`) LUÔN đứng trên `gemini.google.com`/`chatgpt.com`,
KHÔNG BAO GIỜ có nút "Create with Google Flow" — thêm check ở đó chỉ tốn
thời gian poll DOM vô ích mỗi batch. `_process_tasks()` phục vụ CHUNG cả
`worker_mode='dom'/'api'` (labs.google, cần check) LẪN `'gemini_video'`
(gemini.google.com, không cần) nên phải gate thêm theo `worker_mode`, không
thể chỉ dựa vào việc gọi đúng hàm.

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT (fake driver mô phỏng
`current_url` qua nhiều bước đọc, không mock logic quyết định) — 5 kịch bản:
(1) không đứng trên `/project/` → không gọi check gì cả; (2) đứng trên
project, nút KHÔNG xuất hiện → check đúng 1 lần, không điều hướng gì; (3) nút
xuất hiện, bấm xong vẫn còn `/project/` → không điều hướng thừa; (4) nút xuất
hiện, bấm xong bị lệch sang trang chung → `driver.get()` LẠI đúng URL project
ban đầu + sleep 4s; (5) driver chết (`current_url` raise) → best-effort, không
crash, không gọi check (đúng vì không biết đang ở trang nào). **CHƯA verify
trên Chrome/labs.google thật.**

**⚠️ Bổ sung NGAY SAU (cùng ngày) — thêm khoảng "chờ" TRƯỚC khi check:**
theo yêu cầu tiếp "sau khi clear cache phải chờ check có nút Create with
Google Flow click vào mới quay lại trang project hiện tại" — bản đầu ở trên
gọi `_click_create_with_flow_if_present()` NGAY LẬP TỨC, không có khoảng chờ
nào trước đó. Vì `_cdp_clear_cache_and_cookies()` không hề `driver.get()`/
reload gì, màn hình "Create with Google Flow" (nếu Google định hiện) cần vài
giây để trang tự phát hiện cookie/phiên vừa mất (thường qua 1 lần gọi API
nền thất bại) rồi mới render lại UI — check quá sớm dễ bỏ lỡ.

`_recover_flow_project_page_after_cache_clear()` thêm tham số
`settle_wait_secs: float = 3.0` — `self._sleep(settle_wait_secs)` NGAY TRƯỚC
`_click_create_with_flow_if_present()` (hàm đó vẫn tự poll thêm tối đa 6s
nữa như cũ — đây là lớp chờ-trước ĐỘC LẬP, không thay thế). `settle_wait_secs<=0`
bỏ qua hẳn bước chờ (opt-out, phòng cần tuỳ chỉnh/test sau này).

Verify: `py_compile`/`pyflakes` sạch. Test THẬT (fake driver, ghi lại đúng
THỨ TỰ gọi `_sleep`/`_click_create_with_flow_if_present`) — không đứng trên
project → không chờ, không check; mặc định `settle_wait_secs=3.0` chờ ĐÚNG
TRƯỚC check (thứ tự `[sleep(3.0), click-check]`); giá trị tuỳ chỉnh (5s)
được tôn trọng; `settle_wait_secs=0` bỏ qua hẳn bước chờ; full round-trip
(chờ → check → bấm → lệch trang → quay lại) đúng thứ tự
`[sleep(3.0), driver.get(url cũ), sleep(4)]`. **CHƯA verify trên
Chrome/labs.google thật.**

### 2026-08-18 | server/worker.py | Fix log warn SAI khi model video ingredient được resolve ĐÚNG từ DB (source='db-derived' bị hiểu nhầm là "dùng mặc định")

User báo log `Task #22129 ... KHÔNG có trong veo_models.model_key_ingredient (backend), dùng db-derived: videoModelKey=veo_3_1_r2v_lite_low_priority (có thể KHÔNG đúng tier đã chọn)` kèm yêu cầu "dùng hoàn toàn trong db ko dùng mặc định nữa". Điều tra: `model_key_ingredient` là tên cột DB ĐÃ BỊ XOÁ (§11.34, `backend/core/migrations.py::_ensure_veo_models_key_columns()`) — comment/log text ở `worker.py` sót lại tham chiếu tới cột không còn tồn tại. Giá trị THẬT SỰ resolve ĐÚNG (`veo_3_1_r2v_lite_low_priority` — khớp chính xác giá trị đã xác nhận thủ công cho model này ở §11.34) qua `model_catalog.resolve_video_model(ingredient=True)`, nguồn `'db-derived'` = derive `t2v`→`r2v` TỪ chính giá trị `veo_models.model_key` đọc từ DB (KHÔNG đụng dict hardcode `flow_api.py`) — đây LÀ "dùng hoàn toàn trong DB", không phải fallback/mặc định. Bug thật: điều kiện `if model_src != 'db'` coi `'db-derived'` là KHÔNG-từ-DB nên bắn warn SAI dù dữ liệu hoàn toàn đúng và đến từ DB.

Fix: hằng số mới `_MODEL_SRC_FROM_DB = frozenset({'db','db-derived'})` — cả `resolve_image_model()`/`resolve_video_model()` chỉ warn khi `model_src` THẬT SỰ là `'local-fallback'`/`'local-default'` (dict hardcode cục bộ, backend không tới được hoặc model chưa từng confirm trong DB). Sửa lại text warn của nhánh ingredient (bỏ tham chiếu cột `model_key_ingredient` đã xoá, đổi thành `veo_models.model_key` — đúng tên cột thật). Thêm warn tương tự cho nhánh `textToVideo` thuần (trước đây THIẾU HẲN, chỉ có `info` log — không nhất quán với nhánh ảnh/ingredient).

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT (đọc trực tiếp hằng số từ source, không hardcode lại giá trị): `db`/`db-derived` → không warn; `local-fallback`/`local-default` → có warn — đúng 4/4 case. **CHƯA verify trên browser/quota thật** — cần user chạy lại 1 task video ingredient, xác nhận KHÔNG còn thấy warn cho model đã xác nhận trong DB.

### 2026-08-18 | server/worker.py, server/managers.py | Xoá cookie "mỗi batch" đổi hẳn sang lọc THEO DOMAIN (chỉ labs.google), bỏ hẳn danh sách tên "giữ lại"

Theo yêu cầu tiếp theo của user "đổi sang xóa tất cả cookie trong labs.google ko đụng cái khác" — thay thế HOÀN TOÀN bản trước đó cùng ngày (mục ngay dưới, lọc theo TÊN cookie SID-family/ChatGPT, giữ lại ở MỌI domain). `_cdp_clear_cache_and_cookies()` (`worker.py`, cơ chế "mỗi batch") giờ CHỈ xét `domain` của từng cookie — `_batch_clean_should_delete_cookie(domain)` (thay `_batch_clean_should_keep_cookie(name)`, tên hàm ĐẢO NGƯỢC nghĩa cho khớp logic mới) trả `True` nếu domain là `labs.google` hoặc subdomain của nó (`.labs.google`/`www.labs.google`...) — cookie đó bị XOÁ; mọi domain khác (`.google.com`, `accounts.google.com`, `.chatgpt.com`...) hoàn toàn KHÔNG bị đụng. Bỏ hẳn `_BATCH_CLEAN_KEEP_COOKIE_EXACT`/`_BATCH_CLEAN_KEEP_COOKIE_PREFIXES` — không còn cần danh sách "giữ lại" nào vì phạm vi xoá đã tự giới hạn đúng qua domain (cookie đăng nhập Google SID-family sống trên `.google.com`, không phải `labs.google`, nên tự động an toàn mà không cần liệt kê tên).

Cơ chế #2 (`_clear_profile_if_sleeping()`/`managers.py::clear_cache_only()`, sleep-recovery) KHÔNG bị ảnh hưởng bởi thay đổi này — vẫn TẠM TẮT xoá cookie như đã chốt cùng ngày (mục ngay dưới), 2 cơ chế vẫn tách riêng hoàn toàn.

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT (không mock CDP call logic — chỉ giả lập response, gọi qua đúng hàm production): `_batch_clean_should_delete_cookie()` 13 case (labs.google + subdomain đúng 4 dạng → `True`; `.google.com`/`accounts.google.com`/`.chatgpt.com`/rỗng → `False`; case bẫy `notlabs.google` — trùng hậu tố chuỗi nhưng KHÔNG phải subdomain thật → `False` đúng, không bị matched nhầm theo kiểu string-suffix lỏng lẻo). `_cdp_clear_cache_and_cookies()` full end-to-end (mock `_cdp`, danh sách 7 cookie mẫu trộn domain) — xoá ĐÚNG 3 cookie labs.google, giữ nguyên 4 cookie còn lại (2 Google SID-family trên `.google.com`, 1 ChatGPT, 1 domain-bẫy `notlabs.google`). **CHƯA verify trên Chrome/quota thật** — cần user tự chạy 1 batch thật, xác nhận log `[batch-clean] ✔ Đã xoá cache + N/M cookie thuộc domain labs.google...` và session đăng nhập Google/ChatGPT vẫn còn nguyên sau đó.

### 2026-08-18 — Tách RIÊNG 2 cơ chế xoá cookie (mỗi-batch vs sleep-recovery) — danh sách giữ-lại mới, mỗi-batch TẠM TẮT xoá cookie ở sleep-recovery

User cung cấp danh sách cookie giữ-lại MỚI (`["SID","HSID","SSID","APISID","SAPISID","__Secure-1P","__Secure-3P","__Secure-next-auth.session-token"]`) kèm chỉ định phạm vi: "chỉ áp dụng MỖI BATCH giữ lại các cookie trên, còn số 2 khi sleep thì tạm không dùng đến".

**Mỗi-batch (`worker.py::_cdp_clear_cache_and_cookies()`)** — bỏ hẳn import `_GOOGLE_LOGIN_COOKIE_NAMES`/`_LOGIN_COOKIE_HOST_KEYWORDS` từ `managers.py`, thay bằng danh sách RIÊNG module-level trong `worker.py`: `_BATCH_CLEAN_KEEP_COOKIE_EXACT` (so khớp CHÍNH XÁC — 5 cookie Google SID-family cốt lõi + đúng tên cookie session NextAuth ChatGPT `__Secure-next-auth.session-token`, THAY hẳn domain-based matching cũ cho ChatGPT/OpenAI) + `_BATCH_CLEAN_KEEP_COOKIE_PREFIXES` (so khớp TIỀN TỐ — `__Secure-1P`/`__Secure-3P`, bắt được MỌI biến thể `__Secure-1PSID`/`__Secure-1PAPISID`/`__Secure-1PSIDCC`/`__Secure-1PSIDTS`/... trong 1 lần, không cần liệt kê từng cái) + `_batch_clean_should_keep_cookie(name)` (helper, chỉ xét TÊN cookie, KHÔNG còn xét `domain`).

**Sleep-recovery (`managers.py::ProfileManager.clear_cache_only()`, dùng bởi `worker.py::_clear_profile_if_sleeping()`)** — TẠM TẮT hẳn bước gọi `_purge_non_login_cookies()` — hàm giờ CHỈ còn xoá cache (`_clear_chrome_cache_only()`), `removedCookies` luôn trả `0`. `_purge_non_login_cookies()` GIỮ NGUYÊN code (không xoá), chỉ tạm không có caller nào — dễ bật lại sau. 2 cơ chế giờ HOÀN TOÀN ĐỘC LẬP (khác danh sách, khác tiêu chí khớp, sửa 1 bên không ảnh hưởng bên kia).

**Verify:** `py_compile`/`pyflakes` sạch cả 2 file. Test THẬT (không mock) — `_batch_clean_should_keep_cookie()`: 14 case giữ đúng (kể cả 8 biến thể `__Secure-1P*`/`__Secure-3P*` qua prefix, `__Secure-next-auth.session-token`), 9 case xoá đúng (bao gồm `LSID` — CÓ trong danh sách CŨ nhưng KHÔNG có trong danh sách MỚI, đúng xác nhận bị xoá theo yêu cầu — và `'insider'`, chuỗi chứa substring "sid" nhưng KHÔNG khớp exact/prefix, xác nhận không bị match nhầm). `clear_cache_only()`: dựng SQLite Cookies DB + thư mục Cache giả — xác nhận cache bị xoá đúng, cookie row (kể cả cookie rác rõ ràng `tracking_junk`) HOÀN TOÀN không bị đụng, `removedCookies=0`. **CHƯA verify trên Chrome thật.**

### 2026-08-17 — "Chạy xong batch là xoá cache + cookie luôn" — dọn dẹp LIVE qua CDP, không cần đóng/mở lại Chrome

Theo yêu cầu user "chạy xong batch là xòa cache + cookie luôn" — nối tiếp fix cùng ngày ở `_purge_non_login_cookies()` (giữ đúng họ cookie SID theo tên, xem entry ngay dưới), nhưng đó là cơ chế đụng FILE trên đĩa của `profile_dir`, chỉ chạy được SAU `driver.quit()` (worker sắp thoát hẳn) — không dùng được cho yêu cầu này vì worker cần TIẾP TỤC sống qua nhiều batch/heartbeat kế tiếp, không đóng/mở lại Chrome mỗi batch.

**`server/worker.py::_cdp_clear_cache_and_cookies()`** (MỚI) — dọn dẹp NGAY TRONG LÚC Chrome vẫn đang mở, qua CDP: `Network.clearBrowserCache` (xoá cache HTTP) + `Storage.getCookies` (đọc TOÀN BỘ cookie của browser context — KHÁC `Network.getCookies` chỉ trả cookie của tab/domain đang active, sẽ bỏ sót cookie domain khác đã ghé qua; fallback `Network.getCookies` nếu `Storage.getCookies` không được hỗ trợ) rồi `Network.deleteCookies` từng cookie KHÔNG khớp tiêu chí giữ-lại. Import THẲNG `_GOOGLE_LOGIN_COOKIE_NAMES`/`_LOGIN_COOKIE_HOST_KEYWORDS` từ `managers.py` (không định nghĩa lại) — CÙNG tiêu chí với luồng disk-based, 2 nơi không lệch nhau theo thời gian.

**Wired vào 4 điểm "batch/lô vừa xong"** (mirror đúng 4 điểm `_clear_profile_if_sleeping()` đã có — worker-lifecycle end, xem CLAUDE.md §11.31 — NHƯNG khác hẳn ở chỗ đây chạy GIỮA CHỪNG vòng lặp, không phải lúc worker thoát):
- `_process_tasks()` (VEO3 dom/api + gemini_video, "batch = tasks nhận từ 1 lần heartbeat") — biến `escalated` bắt kết quả TRƯỚC khi vào `finally`, chỉ dọn nếu KHÔNG escalate (nếu escalate = worker sắp sleeping/thoát, để `_clear_profile_if_sleeping()` dọn kỹ hơn qua file ngay sau đó, tránh dọn 2 lần liên tiếp phí công).
- `_run_gemini_loop()`/`_run_chatgpt_loop()` (1-tab tuần tự) — dọn ngay sau MỖI task (1 heartbeat = tối đa 1 task = "batch của 1").
- `_run_gemini_loop_concurrent()` (round-robin nhiều tab, §11.24) — dọn đúng lúc "toàn bộ lô vừa xong" (`not any(s is not None for s in slots)`, cùng điểm code vừa báo `status='idle'`).

Best-effort — lỗi CDP (driver vừa chết, tab đã đóng...) chỉ log, không raise/crash batch.

**Verify:** `py_compile`/`pyflakes` sạch. Test cô lập (mock `_cdp`, không cần Chrome thật) — xác nhận: (1) `_cdp_clear_cache_and_cookies()` gọi đúng `Network.clearBrowserCache` + `Storage.getCookies` + xoá đúng tập cookie junk (giữ SID-family Google + domain ChatGPT/OpenAI), fallback đúng sang `Network.getCookies` khi `Storage.getCookies` lỗi, không crash khi cookie list rỗng; (2) `_process_tasks()`'s biến `escalated` gate đúng — 5 kịch bản (dom/api/sequential × escalated/không) đều dọn ĐÚNG lúc không escalate, KHÔNG dọn khi escalate. **CHƯA verify trên Chrome/quota thật** — cần user tự chạy 1 batch thật (bất kỳ mode nào), xác nhận log `[batch-clean] ✔ Đã xoá cache + N/M cookie...` xuất hiện sau khi batch xong, và session Google/ChatGPT vẫn còn đăng nhập sau đó (không bị đá ra màn hình đăng nhập).

### 2026-08-17 — FIX THẬT: "Xóa cache" tự phục hồi lúc sleeping xoá đúng cache nhưng `removedCookies=0` — đổi tiêu chí giữ cookie Google từ "cả domain" sang "họ cookie SID theo TÊN"

User báo: "xem lại cach xóa cookie trong client_tool khi clear chỉ xóa cache và 0 cookie" — điều tra `server/managers.py::_purge_non_login_cookies()` (dùng bởi `worker.py::_clear_profile_if_sleeping()`, tự dọn cache+cookie khi profile rơi vào `'sleeping'` do lỗi liên tiếp, xem CLAUDE.md §11.31): bản đầu (2026-08-14) GIỮ NGUYÊN cả cookie nào có `host_key` chứa `google`/`chatgpt`/`openai` — với 1 profile chỉ chạy Flow/Gemini, GẦN NHƯ TOÀN BỘ cookie vốn dĩ đã thuộc domain `*.google.com` (kể cả cookie KHÔNG liên quan đăng nhập — `NID`/`CONSENT`/`1P_JAR`/`AEC`/`DV`/`OTZ`/`ANID`... là analytics/consent/personalization) → giữ NGUYÊN CẢ DOMAIN đồng nghĩa giữ NGUYÊN GẦN NHƯ TẤT CẢ, khớp đúng `removedCookies=0` user thấy — không phải bug logic, mà tiêu chí quá rộng.

User làm rõ tiêu chí ĐÚNG: "cookie xóa tất cả để lại SSID" — chỉ giữ đúng họ cookie "SID" (Google dùng cụm cookie `SID`/`HSID`/`SSID`/`APISID`/`SAPISID` + biến thể `__Secure-1P*`/`__Secure-3P*` để mang phiên đăng nhập cross-Google-service thật) — lọc theo TÊN cookie (`name`), KHÔNG theo domain. Fix: `_GOOGLE_LOGIN_COOKIE_NAMES` (14 tên chính xác, thay `google` khỏi `_LOGIN_COOKIE_HOST_KEYWORDS`) — `_purge_non_login_cookies()` giờ giữ lại (không xoá) MỌI cookie khớp tên trong họ SID (bất kể domain nào) HOẶC domain thuộc ChatGPT/OpenAI (giữ nguyên theo domain — user chưa yêu cầu đổi phần này, và không có xác nhận tên cookie session-token thật của ChatGPT để đổi an toàn) — xoá SẠCH mọi cookie còn lại.

**Bug PHỤ tìm thấy kèm theo lúc điều tra** (không phải nguyên nhân chính nhưng đáng fix): `worker.py::_clear_profile_if_sleeping()` gọi `pm.clear_cache_only()` rồi CHỈ log `removed`/`removedCookies`, KHÔNG BAO GIỜ log `result['errors']` (populate bởi `_clear_chrome_cache_only()`/`_purge_non_login_cookies()` khi 1 file/DB bị khoá hoặc lỗi đọc/ghi ngay sau khi Chrome vừa đóng) — khiến "0 cookie xoá" không phân biệt được là ĐÚNG (không có gì để xoá) hay LỖI ẨN (DB bị khoá). Thêm log `⚠ N lỗi lúc dọn cache/cookie: ...` nếu `errors` không rỗng.

**Verify THẬT** (SQLite DB dựng đúng schema Chrome, không mock): 18 cookie mẫu (7 Google SID-family, 4 Google junk analytics, 1 accounts.google.com junk, 1 labs.google `_ga`, 2 ChatGPT/OpenAI, 2 domain lạ) → purge đúng xoá 8 (mọi thứ KHÔNG phải SID-family/ChatGPT-OpenAI), giữ đúng 10 (7 SID-family + 3 ChatGPT/OpenAI) — trước fix chỉ xoá được 2 (2 domain lạ), giữ nguyên 16 (mọi thứ mang `google` trong host_key). Case DB hỏng/không đọc được → `errors` populate đúng 1 phần tử, KHÔNG còn bị nuốt im lặng. `py_compile`/`pyflakes` sạch `server/managers.py`/`server/worker.py`. **CHƯA verify trên Chrome/profile thật** (không có kịch bản an toàn để tự trigger `'sleeping'` từ môi trường này) — cần user tự theo dõi 1 lần profile sleeping thật, xác nhận log `removedCookies` > 0 khi có cookie junk và vẫn giữ được đăng nhập Google sau đó (không bị đá ra màn hình đăng nhập).

### 2026-08-17 — Setting `google_login_check_enabled` — tạm tắt bước "check đăng nhập Google" (chạy thẳng vào trang nhận task)

User yêu cầu: "tạm tắt tính năng check login chạy thẳng vào trang nhận task, khi cần có thể bật lại" — sau 2 fix liên tiếp cùng ngày ở `_ensure_google_login()` (§11.38/§11.38b), user muốn có 1 công tắc để BỎ QUA HẲN toàn bộ luồng check/tự đăng nhập này (đơn giản hoá lại về đúng hành vi `driver.get(target_url)` thẳng như trước khi tính năng này tồn tại), thay vì phải sửa code mỗi lần muốn tắt/bật.

**Setting mới `google_login_check_enabled`** (`server/config.py::_DEFAULT_SERVER_SETTINGS`, mặc định **TẮT — `0`**, cùng style bool 0/1 như `debug_log_curl`/`quiet_hours_enabled` đã có) — validate qua `local_settings.py::_clamp()`, hiển thị/sửa được qua trang Cài đặt (`gui/pages/settings_page.py::_FIELD_DEFS`) hoặc `PATCH /api/selenium/local_settings` (route đã generic sẵn, không cần sửa).

**`server/worker.py::_ensure_google_login()`** — thêm short-circuit NGAY ĐẦU hàm: đọc `self._server_settings.get('google_login_check_enabled')`, TẮT (mặc định) → bỏ qua HOÀN TOÀN bước vào `gmail.com`/kiểm tra ô email/tự nhập email-mật khẩu, chỉ còn `driver.get(target_url)` + `sleep(3)` rồi trả `True` ngay — vì hàm này là ĐIỂM ĐẾN NAVIGATE DUY NHẤT cho MỌI entry point đã wire (VEO3 dom/api, gemini, gemini_video, gemini round-robin — xem CLAUDE.md §11.31/§11.38b), tắt setting này áp dụng ĐỒNG LOẠT cho tất cả mà không cần sửa từng call site. BẬT lại (`1`) khôi phục nguyên vẹn luồng đầy đủ đã có.

**Đánh đổi khi TẮT (đã ghi rõ trong docstring):** không còn tự phục hồi được nếu session Google bị đăng xuất giữa chừng — rơi lại về hành vi CŨ (task lỗi ở bước tìm DOM vì đang đứng ở trang đăng nhập Google, cần user tự "Mở login browser" xử lý tay) — đây là đánh đổi CHỦ Ý theo đúng yêu cầu "chạy thẳng vào trang nhận task", không phải side-effect ngoài ý muốn.

**Verify:** `py_compile`/`pyflakes` sạch cả 4 file. Máy hiện tại (`local_settings.json` đã có sẵn, CHƯA có key mới) — xác nhận cơ chế merge-default trong `get_local_settings()` tự điền `0` cho key thiếu, không cần sửa tay file JSON. **CHƯA verify trên Chrome thật** — cần user RESTART worker, xác nhận log Gemini/VEO3 KHÔNG còn thấy dòng `[google-login] Kiểm tra trạng thái đăng nhập...` nữa mà navigate thẳng.

---

### 2026-08-17 — FIX THẬT thứ 2 (cùng ngày): round-robin nhiều tab (`_run_gemini_loop_concurrent`) KHÔNG BAO GIỜ navigate tới Gemini lúc khởi động — đúng "chỉ mở profile trắng ko vào gemini"

User test lại trên máy KHÁC, gửi log thật của 1 profile round-robin ("Gemini Chat mode — 2 tab đồng thời"): log dừng NGAY sau dòng `Chrome opened — sẵn sàng nhận prompt (round-robin)`, không có gì tiếp theo — xác nhận đây là bug KHÁC (sớm hơn) so với fix `_gemini_type_prompt()` ở entry ngay dưới (bug đó chỉ xảy ra SAU KHI đã vào được trang Gemini và có task để gõ).

**Root cause:** `_run_gemini_loop_concurrent()` — sau `self.driver = self._make_driver()` — log "sẵn sàng nhận prompt" NGAY LẬP TỨC mà KHÔNG BAO GIỜ navigate đi đâu cả. Tab khởi động vẫn là trang trắng mặc định (`chrome://new-tab-page/`) cho tới khi lô task ĐẦU TIÊN tới — lúc đó `_gemini_slot_step()`'s state `'new'` mới `driver.get(GEMINI_URL)` (KHÔNG check đăng nhập). Nếu chưa có task nào trong hàng đợi (hoặc phải chờ lâu), browser đứng TRẮNG vô thời hạn — đúng y hệt "chỉ mở profile trắng ko vào gemini". Khác hẳn luồng 1-tab tuần tự (`_run_gemini_loop`) đã navigate + `_ensure_google_login()` NGAY sau khi mở Chrome (§11.31 CLAUDE.md) — round-robin bị SÓT HOÀN TOÀN bước này khi được viết (§11.24), không phải regression từ thay đổi gần đây.

**Fix (`server/worker.py::_run_gemini_loop_concurrent()`):** gọi `_ensure_google_login(GEMINI_URL)` NGAY sau khi mở Chrome — 1 LẦN DUY NHẤT lúc khởi động (không phải mỗi lần chuyển tab, không ảnh hưởng nhịp round-robin `gemini_tab_switch_interval`) — cho log/phản hồi sớm về trạng thái đăng nhập + đảm bảo tab đầu tiên thật sự ở `gemini.google.com/app` trước khi vào vòng chờ task, khớp trải nghiệm luồng tuần tự. `startup_tab_handle` (tái sử dụng tab khởi động làm slot đầu tiên, §11.24 "khi khởi động đã có sẵn 1 tab...") đọc `current_window_handle` SAU lời gọi đăng nhập (không phải trước) — `_ensure_google_login()` có thể đóng tab gốc + mở tab mới khi cần đăng nhập lại, đọc quá sớm sẽ bắt nhầm handle của tab đã đóng. Các tab mở SAU (`_gemini_open_tab()`, slot 2 trở đi) dùng chung session/cookie Chrome với tab này nên `driver.get(GEMINI_URL)` blind ở `_gemini_slot_step` vẫn an toàn, không cần sửa thêm.

**Verify:** `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật** — cần user restart worker round-robin, xác nhận log thấy `[google-login]...` rồi `Chrome opened — sẵn sàng nhận prompt (round-robin)` xuất hiện NGAY sau khi mở Chrome (không còn đứng trang trắng), sau đó chạy 1 task xác nhận tiếp luôn hoạt động đúng (kết hợp với fix `_gemini_type_prompt()` ở entry ngay dưới).

---

### 2026-08-17 — FIX THẬT: `_gemini_type_prompt()` gõ prompt trúng phần tử SAI (`<p>` không focus được) — nguyên nhân "gemini chat + upload: nhận task chỉ mở profile không vào gemini để chạy task"

User báo: "check profile gemini chat + upload khi nhận task bị lỗi chỉ mở profile không vào gemini để chạy task". Kiểm tra `logs/profile_20.log`/`profile_26.log` (2 profile Gemini Chat vừa chạy hôm nay) không lộ traceback rõ ràng, nên đọc kỹ lại `server/worker.py::_gemini_type_prompt()` — hàm DUY NHẤT gõ prompt vào Gemini, dùng CHUNG cho cả luồng 1-tab tuần tự (`_run_task_gemini`) LẪN round-robin nhiều tab (`_gemini_slot_step`), tức là bug ở đây ảnh hưởng MỌI task Gemini bất kể cấu hình bao nhiêu tab.

**Root cause:** selector `document.querySelector('.new-input-ui').querySelector('[contenteditable="true"]') || wrapper.querySelector('p')` — SAI vì `.new-input-ui` (Quill editor của Gemini) **TỰ NÓ** mang `contenteditable="true"` ngay trên chính nó (`<div class="ql-editor ... new-input-ui" contenteditable="true">`), KHÔNG PHẢI 1 ancestor bọc quanh 1 phần tử con khác mới có thuộc tính này. `querySelector` chỉ tìm CON CHÁU, không match chính phần tử gọi nó — nhánh đầu LUÔN trả `null`, rơi xuống `wrapper.querySelector('p')` — 1 thẻ `<p>` hiển thị placeholder bên trong, KHÔNG tự focus được (`focus()`/click không đổi `document.activeElement`). Click + gõ (CDP `Input.insertText`) vào `<p>` không thực sự vào ô nhập liệu thật → prompt vẫn RỖNG → nút Gửi không bao giờ hết `disabled` → task kẹt/timeout ở bước chờ nút Gửi phía sau (`_gemini_fill_and_submit`/`_gemini_submit_if_ready`) với thông báo khó hiểu — đúng cảm giác user mô tả "mở profile xong không thấy vào gemini chạy gì cả".

**Đây KHÔNG PHẢI bug mới — đã từng gặp Y HỆT ở `extensions/content.js::_findInput()`** (Chrome Extension, cùng DOM Gemini nhưng codebase HOÀN TOÀN riêng, xem `ToolSub` gốc `CLAUDE.md` mục "ROOT CAUSE THẬT SỰ ĐÃ XÁC NHẬN (v4, 2026-07-06)") — fix đó (ưu tiên trả về CHÍNH wrapper nếu nó tự `contenteditable`) chưa bao giờ được áp dụng sang bản Selenium độc lập ở `client_tool`, 2 codebase không dùng chung code nên fix ở bên này không tự động lan sang bên kia.

**Fix (`server/worker.py::_gemini_type_prompt()`):** kiểm tra CHÍNH `wrapper` có `contenteditable="true"` TRƯỚC (ưu tiên trả về chính nó), chỉ tìm con cháu nếu wrapper không tự editable (phòng DOM đổi khác về sau). Thêm bước **verify SAU KHI gõ** — đọc lại `innerText.length` của `.new-input-ui`, nếu vẫn là `0` dù `prompt` không rỗng thì `raise RuntimeError(...)` NGAY tại đây (lộ lỗi rõ ràng đúng chỗ) thay vì để task âm thầm timeout mơ hồ ở bước chờ nút Gửi — lớp phòng vệ THỨ 2 phòng trường hợp DOM Gemini đổi khác lần nữa trong tương lai mà selector không còn khớp đúng.

**Verify:** `py_compile`/`pyflakes` sạch `server/worker.py`. **CHƯA verify trên Chrome/Gemini thật** (môi trường phát triển không chạy được Chrome thật) — cần user RESTART `main.py`/worker rồi chạy lại 1 task Gemini, xác nhận log thấy `[slot N] Gemini prompt submitted` (hoặc tương đương ở luồng 1-tab) xuất hiện đúng ngay sau bước gõ, không còn kẹt ở bước "opened profile" mà không tiến thêm.

---

### 2026-08-14 — `worker_mode='api'`: bỏ upload ref cả lô 1 lượt, chuyển sang upload NGAY TRƯỚC lúc khởi động thread của chính task đó + giãn cách random giữa các lần khởi động thread

**User yêu cầu:** "fix lại client_tool không upload reference 1 lượt nữa: cứ upload cùng task: và cho phép setting random giây trong khoảng: 10-15 giây mặc định để khởi động thread tiếp theo." — nối tiếp §11.35 CLAUDE.md (song song hoá THẬT bằng N thread): sửa 2 điểm chưa đúng ý — (1) video từng upload ref CẢ LÔ trong 1 vòng tuần tự riêng trước khi submit song song; (2) mọi thread trong lô được submit gần như đồng thời, không giãn cách.

**Implement** (`server/config.py`, `server/local_settings.py`, `server/worker.py`, `gui/pages/settings_page.py`): 2 setting mới `thread_stagger_min_secs`/`thread_stagger_max_secs` (mặc định 10.0/15.0, sửa qua tab Cài đặt). `_run_image_tasks_concurrent()` viết lại — gộp prep+submit thành 1 vòng: mint captcha → log `⬆ Upload reference image TASK #{id}…` nếu có ref → `pool.submit()` ngay → chờ random stagger trước khi sang task kế (thread đã chạy vẫn tiếp tục song song). `_run_video_tasks_concurrent_submit(tasks, uploaded_by_task)` đổi tên + viết lại thành `_run_video_tasks_staggered(tasks)` — bỏ tham số `uploaded_by_task`, gộp `_prepare_video_uploads()` vào NGAY TRONG vòng lặp chính (chỉ gọi khi task có `source_media`), cùng pattern giãn cách với ảnh. `_run_tasks_api_batch()` xoá hẳn vòng "upload ref cả lô video trước", gọi thẳng `_run_video_tasks_staggered(video_tasks)`.

**Verify:** `py_compile`/`pyflakes` sạch. Test mock-based THẬT (đo thời gian thực qua venv client_tool, throwaway đã xoá sau verify) — ảnh: 3 task, stagger cố định 0.3s, worker sleep 0.5s → elapsed 1.12s (không phải 1.5s tuần tự, không phải 0.5s toàn song song), gap giữa 3 lần khởi động thread đúng 0.30-0.31s, xác nhận OVERLAP thật (elapsed < 3×0.5s); video: 2 task (1 có ref/1 không) → upload CHỈ xảy ra cho task có ref, xảy ra NGAY TRƯỚC thời điểm launch của chính task đó (không phải 1 vòng riêng trước), `uploaded_media_names` truyền đúng theo từng task. CHƯA verify trên browser/quota thật.

---

### 2026-08-14 — Tự phục hồi khi "ngủ" do lỗi nhiều: xoá thêm cookie KHÔNG liên quan đăng nhập (giữ nguyên đăng nhập Google/ChatGPT)

**User yêu cầu:** "khi lỗi quá nhiều ngoài xóa cache xóa thêm các cookie không liên quan đăng nhập" — mở rộng `_clear_profile_if_sleeping()` (§11.31 CLAUDE.md, chạy khi profile rơi vào `status='sleeping'` do lỗi liên tiếp/trong 1 khoảng thời gian, TRƯỚC ĐÓ chỉ xoá cache thuần Cache/GPUCache/... qua `clear_cache_only()`, cố tình GIỮ NGUYÊN toàn bộ `Cookies` để không phải đăng nhập lại) — giờ thêm bước xoá cookie các domain KHÔNG liên quan đăng nhập (tracking/ads/site khác vô tình dính cookie trong lúc automation), VẪN giữ nguyên cookie Google/ChatGPT (2 hệ đăng nhập duy nhất trong dự án).

**Implement** (`server/managers.py`): `_LOGIN_COOKIE_HOST_KEYWORDS = ('google', 'chatgpt', 'openai')` + `_purge_non_login_cookies(profile_dir)` — mở trực tiếp SQLite Cookies DB (`Default/Network/Cookies` — Chrome mới, fallback `Default/Cookies` — Chrome cũ), `DELETE FROM cookies WHERE host_key NOT LIKE '%google%' AND NOT LIKE '%chatgpt%' AND NOT LIKE '%openai%'`. `ProfileManager.clear_cache_only()` giờ gọi CẢ `_clear_chrome_cache_only()` (cache dirs, không đổi) LẪN `_purge_non_login_cookies()` (mới), gộp kết quả (`removed`/`removedCookies`/`errors`). `worker.py::_clear_profile_if_sleeping()`'s log cập nhật theo (nhắc rõ "giữ nguyên đăng nhập Google/ChatGPT"). Nút "Làm mới profile" THỦ CÔNG (GUI, xoá sạch 100% kể cả login) KHÔNG đổi, vẫn dùng `clear_browser_data()` riêng.

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT (dựng file SQLite Cookies giả ĐÚNG cấu trúc bảng `cookies` của Chrome thật, không mock) — 8 cookie mẫu (5 domain login: `.google.com`/`accounts.google.com`/`.labs.google`/`chatgpt.com`/`.openai.com`, 3 domain không liên quan: `.doubleclick.net`/`picsum.photos`/`.some-ad-network.com`) → sau purge: đúng 3 bị xoá, đúng 5 domain login còn nguyên; profile chưa từng mở (không có Cookies DB) → no-op an toàn không lỗi; `clear_cache_only()` gộp đúng cả 2 kết quả (cache dir bị xoá THẬT + đúng số cookie bị xoá + file Cookies DB vẫn còn, chỉ rows bên trong bị xoá). File test throwaway đã xoá sau verify.

**CHƯA verify trên Chrome thật** — cần user tự trigger 1 profile vào trạng thái `sleeping` (hoặc gọi trực tiếp qua log) để xác nhận log `[auto-refresh] ✔ Đã xoá cache (...) + N cookie không liên quan đăng nhập...` và profile vẫn đăng nhập sẵn ở lần chạy kế tiếp.

---

### 2026-08-14 — FIX THẬT: "task hoàn tất nhưng không thấy media mới" khi Render lại — 4 nơi dùng tên tự chế thay vì UUID thật

**User báo:** "check lại image/video task khi hoàn tất có check ID đã tồn tại chưa, nếu chưa mới insert thành array chọn mặc định media mới nhất, hiện tại sao tôi thấy tạo lại trên frontend mà báo task hoàn tất mà ko thấy media mới".

**Điều tra (không đoán):** `_apply_media_to_task()` (backend, `backend/services/media_download.py`) BẢN THÂN nó ĐÃ ĐÚNG — dedup theo `name` (UUID), item nào CHƯA có mới append vào `result_files`, luôn chọn `selected_file_index = len(merged_files)-1` (item cuối = mới nhất). Bug KHÔNG nằm ở logic dedup/select-newest đó, mà ở CHÍNH `name` mà `client_tool` (`server/worker.py`) gửi lên — 4 nơi build payload `media` gửi `POST /task/download` dùng CHUỖI TỰ CHẾ CỐ ĐỊNH theo `task_id` (`f'img_{task_id}_{i+1}'`/`f'dom_{task_id}_{i+1}'`) thay vì UUID THẬT của Google (khác nhau MỖI LẦN generate) — nghĩa là lần "Render lại" THỨ 2 của CÙNG 1 task luôn gửi lên ĐÚNG CÁI TÊN như lần đầu → `_apply_media_to_task()` tưởng nhầm media MỚI "đã tồn tại rồi" → dedup bỏ qua, KHÔNG append → `result_files` không đổi dù ảnh/video MỚI đã render xong thật — khớp CHÍNH XÁC triệu chứng user báo ("task hoàn tất nhưng không thấy media mới").

**4 nơi có bug (đều trong `server/worker.py`):**
1. `_run_task_api()` — API mode ảnh, nhánh Direct API (`_call_image_api_v2()`), dòng build `media` cuối hàm.
2. `_run_task_api()` — API mode ảnh, nhánh UI-driven fallback (`_generate_image_via_ui()`).
3. `_run_image_tasks_concurrent()` (thêm 2026-08-13, cùng phiên làm việc trước đó — kế thừa NGUYÊN bug từ code gốc lúc viết đa luồng, không phải bug mới) — API mode ảnh song song.
4. `_resolve_tile_media()` — DOM mode, fallback tile-polling (dùng chung bởi CẢ single-task `_collect_task_media_via_tiles` LẪN batch `_run_tasks_batch`'s nhánh cursor) — `dom_{task_id}_{i+1}`.

**KHÔNG có bug** (đã audit, xác nhận dùng ĐÚNG UUID thật từ đầu): `_reconcile_project_media()` (đường CHÍNH của cả DOM lẫn API-video mode — đọc `name` thật từ `flow.projectInitialData`/`media.name`), `flow_api.parse_image_results()` (đã trích `m.get('name')` đúng từ trước — chỉ 2 caller ở trên KHÔNG DÙNG tới field này), video API mode (luôn đi qua reconcile, không tự build `media` list).

**Fix:**
- `_extract_cdn_media_name(url)` (hàm mới, module-level) — regex `flow-content\.google/(?:video|image)/([0-9a-f-]{36})` (khớp CHÍNH XÁC `_CDN_FLOW_RE` phía backend) trích UUID thật từ CDN URL đã resolve.
- `_resolve_tile_media()` — `name = _extract_cdn_media_name(u) or f'dom_{task_id}_{i+1}'` (ưu tiên UUID thật, fallback tên tự chế CHỈ khi regex không khớp — phòng Google đổi định dạng URL).
- `_generate_image_via_ui()` — thêm trích `m.get('name', '')` vào kết quả trả về (TRƯỚC ĐÂY bỏ qua hoàn toàn field này dù có sẵn trong response JSON).
- `_run_task_api()` (2 nhánh) + `_run_image_tasks_concurrent()` — đổi `f'img_{task_id}_{i+1}'` → `u.get('name') or f'img_{task_id}_{i+1}'` (ưu tiên uuid thật từ `results_urls`, fallback CHỈ khi thiếu).

**Verify:** `py_compile`/`pyflakes` sạch. Test cô lập (mock, throwaway, đã xoá sau verify) — `_extract_cdn_media_name()` trích đúng uuid từ URL ảnh/video, rỗng khi không khớp; `_resolve_tile_media()` trả đúng uuid thật (mock `_dom_poll_done`/`_dom_resolve_url`), **quan trọng nhất**: gọi 2 lần với CÙNG `task_id=555` (mô phỏng "Render lại") ra 2 tên KHÁC NHAU (đúng ý nghĩa mỗi lần generate 1 UUID mới) — TRƯỚC FIX sẽ ra tên GIỐNG HỆT (`dom_555_1` cả 2 lần); fallback đúng khi URL không khớp định dạng CDN (không crash); logic build `media` ưu tiên `u.get('name')`, fallback đúng khi thiếu.

**CHƯA verify trên browser/quota thật** (môi trường phát triển không chạy được Chrome/Selenium thật) — cần user tự "Render lại" 1 task ảnh (cả 2 mode `api`/`dom`) đã có sẵn ≥1 kết quả, xác nhận: (1) task báo `done`, (2) `result_files` THẬT SỰ có thêm 1 phần tử mới (không phải giữ nguyên số lượng cũ), (3) frontend tự hiển thị đúng ảnh/video MỚI (nhờ `selected_file_index` trỏ đúng phần tử cuối).

---

### 2026-08-13 — API mode: gửi ẢNH/VIDEO SONG SONG THẬT SỰ (N thread) thay vì tuần tự "gửi 1 → chờ xong → gửi 2"

**User yêu cầu:** "các task image/video veo3 nên chạy đa luồng kiểu có 5 prompt cứ gửi 5 prompt rồi nhận về kq, chứ chạy tuần từ 1 prompt chờ kq, 1 prompt rồi chờ kq làm mất thời gian" — làm rõ thêm: "giống đa luồng 5 prompt thì gữi 5 api 5 luồng khác nhau rồi nhận phản hồi" (N thread Python THẬT, mỗi thread tự bắn 1 request rồi block chờ ĐÚNG response của chính nó).

**Điều tra trước khi sửa (không đoán):** `worker_mode='api'` batch xử lý (`_run_tasks_api_batch()`) — ảnh trước đây gọi `_run_task_api()` TUẦN TỰ từng task (`for i, task in enumerate(image_tasks): ...`, có `task_delay_secs` giữa các lần); video submit cũng tuần tự (có delay) rồi CHỜ CHUNG 1 lần `_wait_and_reconcile_tasks()` cho cả batch. Xác nhận đúng profile thật đang bị ảnh hưởng: `selenium_profiles` id=27 (`huavantien84`, `worker_mode='api'`, `task_mode='video_only'`, `max_concurrent=2`) — user đã tăng `max_concurrent` lên >1 nhưng vẫn thấy "chạy như tuần tự" vì tăng `max_concurrent` chỉ đổi SỐ TASK server giao/lần heartbeat, không đổi CÁCH worker xử lý batch đó (vẫn tuần tự bên trong).

**Vì sao song song hoá THẬT được (không phải giả song song):** đọc kỹ `_post_aisandbox()` (hàm POST generate thật, dùng bởi cả `_call_image_api_v2()`/`_call_video_api()`) xác nhận nó đi qua `curl_cffi` — 1 HTTP client Python THUẦN, HOÀN TOÀN TÁCH RỜI khỏi Selenium driver (không phải `driver.execute_script` như nhiều chỗ khác trong file) — nhiều thread Python gọi đồng thời AN TOÀN, không đụng driver. `_extract_project_id()`/`_upload_media_to_flow()`/`_download_source_media()` cũng không đụng driver (đọc `self.profile` dict, dùng `requests`/`curl_cffi` thuần). CHỈ CÓ 1 bước THẬT SỰ cần driver: mint reCAPTCHA token (`_get_fresh_recaptcha()`, dùng `execute_async_script`) — 1 Selenium session không an toàn cho nhiều thread gọi lệnh cùng lúc, nên bước này LUÔN làm TUẦN TỰ TRƯỚC (nhanh, ~1-2s/task, trong vòng lặp "prep" đầu mỗi hàm mới) — chỉ phần TỐN THỜI GIAN NHẤT (Google xử lý ảnh/video) mới thực sự chạy song song qua `ThreadPoolExecutor`.

**2 hàm mới** (`server/worker.py`, đặt trước `_run_tasks_api_batch()`):
- `_run_image_tasks_concurrent(tasks)` — prep tuần tự (mint captcha, check `has_tokens()`/`_extract_project_id()`) → N thread gọi `_call_image_api_v2()` song song (mỗi thread tự chờ HTTP response chứa `fifeUrl` — ẢNH có "phản hồi" đồng bộ THẬT, khớp đúng mô tả user). Task thiếu điều kiện Direct API (chưa có token/captcha/project_id) rơi về `_run_task_api()` (UI-driven, dùng driver) — xử lý TUẦN TỰ riêng vì không thread-safe. Task lỗi 403/reCAPTCHA khi gọi song song (token vừa mint hỏng ngay lúc dùng, hiếm) được RETRY qua UI-driven ở vòng sau — giữ nguyên fallback đã có ở `_run_task_api()` gốc, không âm thầm bỏ khi chuyển sang song song.
- `_run_video_tasks_concurrent_submit(tasks, uploaded_by_task)` — cùng pattern nhưng cho SUBMIT video (API video không hiện tile trên UI, "phản hồi" mỗi thread nhận chỉ xác nhận đã tạo workflow — kết quả THẬT vẫn lấy SAU qua reconcile `projectInitialData`, KHÔNG đổi bước đó — chỉ đổi bước SUBMIT từ tuần tự sang song song). Trả thêm `attempted_ids` để caller phân biệt task đã thử (thành công/lỗi) với task chưa từng chạm tới vì escalation dừng batch sớm — giữ nguyên hành vi "Dừng trước khi generate" đã có.
- Mọi mutation state KHÔNG thread-safe (`_handle_task_success`/`_handle_task_error`/`_task_start`/counters) CHỈ được gọi từ THREAD CHÍNH (vòng lặp `as_completed`), KHÔNG BAO GIỜ từ bên trong `_worker()` chạy trong `ThreadPoolExecutor` — tránh race condition trên state của `SeleniumFlowWorker`.
- `_run_tasks_api_batch()` viết lại để gọi 2 hàm trên thay vì vòng lặp tuần tự cũ. Không đổi số thread giới hạn nào thêm — batch size đã bị chặn bởi `selenium_profiles.max_concurrent` (server chỉ giao tối đa từng đó task/lần heartbeat) nên "N thread = N task trong batch" tự nhiên đúng ý user cấu hình.

**Phạm vi — CHỈ `worker_mode='api'`, KHÔNG đụng DOM mode:** `_run_tasks_batch()` (DOM mode, submit qua UI picker) giữ nguyên tuần tự — DOM automation vốn đã CHỈ có 1 browser tab/session, không thể song song hoá theo cùng cách (mọi thao tác DOM đều phải qua driver). Không có profile thật nào đang `enabled=1`+`worker_mode='dom'` cho VEO3 lúc audit (chỉ có `worker_mode='api'` id=27 active) nên scope này khớp đúng nhu cầu thực tế.

**Verify:** `py_compile`/`pyflakes` sạch `server/worker.py`. Test cô lập (mock, không cần Chrome/DB thật) — 5 kịch bản: (1) 5 task ảnh với mock sleep 0.4s/task → tổng thời gian thực đo **0.40s** (không phải 2.00s nếu tuần tự) VÀ xác nhận các task CHỒNG LẤN thời gian thật (start task sau < end task trước) — bằng chứng song song thật, không chỉ đo tổng thời gian; (2) lỗi 403 → retry UI-driven đúng, lỗi khác → báo lỗi ngay; (3) thiếu token/captcha/project_id → rơi về UI-driven tuần tự đúng, không gọi Direct API; (4) 4 task video submit song song → **0.42s** (không phải 1.60s); (5) escalation (profile "ngủ") giữa batch → dừng ngay, task CHƯA chạm tới không bị coi là "đã thử" (đúng semantics "Dừng trước khi generate"). File test đã xoá sau khi verify xong (throwaway, không phải test suite thường trực trong `tests/`).

**CHƯA verify trên browser/quota thật** (môi trường phát triển không chạy được Chrome/Selenium/API thật) — cần user tự chạy 1 batch ≥2 task ảnh/video qua profile `worker_mode='api'` với `max_concurrent≥2`, xác nhận qua log: nhiều dòng `▶ Task #...` / `POST .../flowMedia:batchGenerateImages` (hoặc video endpoint) xuất hiện GẦN NHAU trong log thay vì cách nhau đúng `task_delay_secs`, và các task hoàn tất gần như đồng thời thay vì lần lượt.

---

### 2026-08-13 — FIX THẬT + tái thiết kế: `videoModelKey` cho video imageToVideo/componentsToVideo bị BỎ QUA hoàn toàn — thêm `model_catalog.py` (DB `veo_models.model_key` làm source of truth)

**User hỏi:** "nghi vấn chọn sai videoModelKey hãy check project_6 đưa qua client_tool sẽ ra videoModelKey = gì".

**Điều tra trên DB thật:** project_6 ("Cuộc Sống Nông Trại Yên Bình") có 855 task video, TẤT CẢ `mode='imageToVideo'`, `model="Veo 3.1 - Lite [Lower Priority]"`. Chạy THẬT qua `_call_video_api()` (không đoán) xác nhận: `videoModelKey` gửi đi LUÔN LÀ `veo_3_1_r2v_lite` — `task.model` bị BỎ QUA HOÀN TOÀN cho mode này, vì `_call_video_api()`'s nhánh `imageToVideo`/`componentsToVideo` gọi `build_ingredient_to_video_body()` KHÔNG truyền `video_model_key=`, luôn dùng default cứng.

**Root cause SÂU HƠN — 1 suy luận SAI từ trước (§11.33):** default cứng đó dựa trên capture 2026-08-12 (chỉ 1 mẫu, project "khanh") kết luận "Flow UI tự khoá model khi dùng sub-tab Thành phần, KHÔNG theo dropdown user chọn". User XÁC NHẬN TRỰC TIẾP suy luận đó SAI: namespace ingredient (`veo_3_1_r2v_*`) CŨNG có biến thể theo tier giống hệt namespace textToVideo (`veo_3_1_t2v_*`) — "Veo 3.1 - Lite [Lower Priority]" → `veo_3_1_r2v_lite_low_priority`, KHÁC hẳn `veo_3_1_r2v_lite` plain đã bị dùng nhầm cho MỌI tier.

**Fix 2 lớp:**

1. **DB `veo_models.model_key`** (xem `ToolSub/CHANGELOG.md` cùng ngày) — source of truth CHUNG (KHÔNG có cột riêng cho ingredient — user: "model_key dùng chung ko phân biệt khi dùng ingredient xóa cột đó đi") cho `imageModelName`/`videoModelKey` (dạng t2v) thật.
2. **`server/model_catalog.py`** (MỚI) — `resolve_image_model(name)`/`resolve_video_model(name, ingredient=bool)`: fetch `GET {FLOW_SERVER}/api/models` (cache TTL 300s, giữ cache cũ nếu fetch lỗi thay vì xoá sạch), tra theo `name` (khớp đúng field GUI lưu trong `tasks_media_flow.model`). `ingredient=True` → lấy `modelKey` (t2v) từ DB rồi **DERIVE** sang r2v bằng cách thay chuỗi con `t2v`→`r2v` (KHÔNG cần trường DB riêng — pattern đã xác nhận nhất quán). Fallback dict hardcode CŨ trong `flow_api.py` nếu backend không tới được / DB chưa có giá trị cho model đó.

| File | Thay đổi |
|------|---------|
| `server/model_catalog.py` (mới) | `resolve_image_model()`/`resolve_video_model(..., ingredient=)` — DB-first, fallback hardcode, trả kèm `source` (`'db'`/`'db-derived'`/`'local-fallback'`/`'local-default'`) để log rõ nguồn gốc. |
| `server/flow_api.py` | `resolve_ingredient_video_model_key()` viết lại HOÀN TOÀN — bỏ hẳn `_INGREDIENT_VIDEO_MODEL_LABEL_TO_KEY` (dict riêng, chỉ có 1 cặp xác nhận), giờ DÙNG CHUNG `_VIDEO_MODEL_LABEL_TO_KEY`/`resolve_video_model_key()` (t2v) rồi swap `t2v`→`r2v`. |
| `server/worker.py` | `_call_video_api()`'s nhánh `imageToVideo`/`componentsToVideo` — giờ GỌI `model_catalog.resolve_video_model(..., ingredient=True)` + truyền `video_model_key=` vào `build_ingredient_to_video_body()` (TRƯỚC ĐÂY hoàn toàn không resolve gì) + log rõ `model="..." → videoModelKey=... (ingredient, nguồn: ...)` + warn nếu nguồn không phải `'db'`. Nhánh `textToVideo` cũng đổi sang `model_catalog.resolve_video_model(..., ingredient=False)` (trước gọi thẳng `flow_api.resolve_video_model_key()`). `_call_image_api_v2()` đổi sang `model_catalog.resolve_image_model()` (trước gọi thẳng `flow_api.resolve_image_model_key()`), log thêm `(nguồn: ...)`. |

Verify: chạy THẬT qua đúng logic `_call_video_api()` với dữ liệu project_6 lấy từ DB — xác nhận bug (trước fix: `veo_3_1_r2v_lite` SAI tier). `resolve_ingredient_video_model_key()` test độc lập — case xác nhận (`Lite [Lower Priority]` → `veo_3_1_r2v_lite_low_priority`) khớp đúng, case Lite plain khớp default cũ, case Fast/Quality/Omni Flash suy ra hợp lý/fallback an toàn. `model_catalog.py` test end-to-end qua backend MỚI (in-process Flask test client — bypass server thật đang chạy chưa restart) — `resolve_image_model()`/`resolve_video_model()` (cả 2 nhánh t2v/r2v) trả đúng giá trị DB, `source='db'`/`'db-derived'` đúng như kỳ vọng; model không có trong DB → fallback local đúng. `py_compile`/`pyflakes` sạch toàn bộ. **CHƯA verify trên browser thật với 1 task video project_6 thật** — cần user chạy lại 1 task sau khi backend được restart (bắt buộc để `GET /api/models` trả `modelKey`, xem `ToolSub/CHANGELOG.md`) và xác nhận log `videoModelKey=veo_3_1_r2v_lite_low_priority (ingredient, nguồn: db-derived)`.

---

### 2026-08-12 — Port đủ luồng capture token từ `_test` (chỉ bỏ proxy)

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_sync_project_apis()` / `_ensure_api_ready()` — chờ auth → checkApp fingerprint → batchLog `sessionId` (parse `events[].metadata.sessionId`, không chỉ `clientContext`). `_capture_tb_credentials()`, `_merge_browser_headers()` + `sec-ch-ua` từ `navigator.userAgentData`. `Network.enable` CDP. `_build_headers()` luôn có `sec-fetch-*`/`sec-ch-ua` kể cả khi checkApp chưa capture. |
| `server/chrome_utils.py` | `_connect_to_chrome()` set `goog:loggingPrefs` khi ATTACH (trước thiếu → `get_log('performance')` fail, không bắt ExtraInfo). |
| `client_tool/CLAUDE.md` | §4.1 / §11.2 — token capture khớp `_test`, không SOCKS / extension-proxy / token disk cache. |

**Theo yêu cầu user:** "xem lại cách `_test` lấy token coi còn thiếu đâu không, chỉ bỏ đi qua proxy."

Worker trước chỉ coi "tokens ready" = có `authorization`. `_test_textToImage.py` còn bắt `x-browser-validation` + `x-client-data` (Chrome inject ExtraInfo lúc `checkAppAvailability`) và `sessionId` từ `batchLogFrontendEvents` — curl_cffi không tự có các header này. Attach Chrome đang mở cũng thiếu `loggingPrefs` nên ExtraInfo không bao giờ tới.

**Không port:** SOCKS/`chrome_proxy_server`, extension-proxy POST, `.browser_tokens.json`, curl export, debug pause.

---

### 2026-08-12 — Fix uploadImage `TypeError: Failed to fetch` — bật lại curl_cffi (không proxy)

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_post_aisandbox()`: `curl_cffi` impersonate Chrome là PRIMARY (không SOCKS). Page `fetch()` chỉ còn fallback, và lọc bỏ forbidden headers + `x-browser-validation` (Chrome inject ở network layer — set từ JS → CORS preflight fail). |
| `client_tool/CLAUDE.md` | §4.3 / §11.2 — POST lại dùng curl_cffi, không proxy. |

**Bug thật (log 18:52 profile-25):** lô 5 task video, mọi `uploadImage` `HTTP 0: TypeError: Failed to fetch` rồi `/task/error`.

**Nguyên nhân:** lúc "tạm tắt curl_cffi" POST chỉ còn `fetch()` từ tab labs.google. Browser chặn CORS khi script Selenium set header Chrome-injected (`x-browser-validation`, `sec-*`, `user-agent`, `origin`…) — `fetch()` fail ngay, không tới server. `_test_ingredientToVideo.py` cùng ngày cũng fail page/extension rồi **thành công nhờ curl_cffi**.

**Fix:** POST aisandbox (uploadImage + generate) đi curl_cffi như test đã proven, không proxy. Page fetch giữ làm fallback nếu thiếu package.

---

### 2026-08-12 — Map tên model GUI → videoModelKey API (textToVideo)

| File | Thay đổi |
|------|---------|
| `server/flow_api.py` | `resolve_video_model_key()` — `'Veo 3.1 - Fast'` → `veo_3_1_t2v_fast` (và 3 biến thể Lite/Quality/LP). `build_text_to_video_body()` tự map. |
| `tests/utils/flow_api.py` | Cùng mapping (2 file độc lập, phải sửa cả 2). |
| `server/worker.py` | `_call_video_api()` log `videoModelKey=… (task.model=…)` trước khi POST. |
| `client_tool/CLAUDE.md` | §4.3 — textToVideo map tên GUI sang key API. |

**Bug:** heartbeat gửi `task.model` đúng chuỗi GUI lưu trong DB (`Veo 3.1 - Fast`, `veo_models.name`). API mode nhét nguyên chuỗi đó vào `videoModelKey` — aisandbox cần key `veo_3_1_t2v_*`.

| Tên GUI (`veo_models.name`) | `videoModelKey` |
|-----------------------------|-----------------|
| Veo 3.1 - Lite [Lower Priority] | `veo_3_1_t2v_lite_low_priority` |
| Veo 3.1 - Lite | `veo_3_1_t2v_lite` |
| Veo 3.1 - Fast | `veo_3_1_t2v_fast` |
| Veo 3.1 - Quality | `veo_3_1_t2v` |

Key API đã đúng (vd test `--model veo_3_1_t2v_lite`) giữ nguyên. En-dash `–` trên label frontend cũng normalize.

---

### 2026-08-12 — Tắt curl_cffi; hiện ô Task nhận đồng thời cho VEO; batch video upload hết ảnh rồi generate tuần tự

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_post_aisandbox()` CHỈ còn `fetch()` từ tab labs.google — bỏ fallback `curl_cffi`. `_prepare_video_uploads()` tách bước tải+upload ảnh. `_run_tasks_api_batch()`: upload HẾT ảnh đính kèm của cả lô video trước, rồi POST generate tuần tự gán đúng `media.name` từng task. |
| `gui/profile_dialog.py` | Label "Task nhận đồng thời". Fix `_on_type_change()`: widget dùng chung (`_max_concurrent` vừa veo3 vừa gemini_video) không còn bị nhóm sau ẩn mất khi chọn VEO3. |
| `gui/pages/profiles_page.py` | Cột Mode VEO luôn hiện `xN` (số task đồng thời), kể cả N=1. |
| `client_tool/CLAUDE.md` | §4.3 / §11.2 — POST không curl_cffi; batch video pre-upload. |

**Theo yêu cầu user:** "tạm thời ko dùng curl_cff, profile setting đối với veo phải có ô task nhận đồng thời để chỉnh, các lô batch nếu có image đình kèm cứ upload lên hết rồi gán theo đúng task video nó tuần tự".

**Bug UI:** ô `max_concurrent` nằm trong CẢ nhóm `veo3` lẫn `gemini_video`. `_on_type_change()` cũ loop `visible = (group == current)` nên chọn VEO3 thì nhóm `gemini_video` (duyệt sau) ẩn mất spinbox — user không thấy chỗ chỉnh số task nhận đồng thời.

**Batch video:** trước đây mỗi task tự upload ảnh ngay lúc POST generate → dễ lẫn ref giữa các task trong cùng lô. Giờ: (1) upload hết `source_media` của mọi task video, nhớ `media.name` theo `task_id`; (2) generate tuần tự, mỗi POST chỉ dùng đúng list đã upload của task đó; (3) reconcile `projectInitialData` như trước.

---

### 2026-08-12 — API mode: gửi prompt liên tiếp, không chờ tile

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_call_video_api()` bỏ `_collect_task_media_via_tiles`. `_run_tasks_api_batch()` submit hết video rồi mới reconcile `projectInitialData` (`wait_tiles=False`). Prompt video prefix `TASK_{id}:`. |
| `server/flow_api.py` | Docstring `parse_video_workflow()` — không còn nói poll tile. |
| `client_tool/CLAUDE.md` | §2 / §4.3 — API không hiện tile, không chờ DOM. |

**Theo yêu cầu user:** "đối với mode chạy api cứ gửi prompt liên tiếp ko chờ tiles hiện lên vì nó ko có show lên giao diện khi gọi qua API".

Luồng cũ (submit API → chờ tile 90s+) treo vì tile không bao giờ xuất hiện. Giờ: POST generate xong là sang task tiếp; sau cả lô mới đọc `projectInitialData` (refresh trang, không nhìn tile) để server tải + set done.

---

### 2026-08-12 — Fix VEO API mode: recaptcha site-key đảo argument + POST giống `_test_textToImage.py` (không proxy)

| File | Thay đổi |
|------|---------|
| `server/worker.py` | Port stack đã proven ở `tests/utils/flow_session.py` vào production API mode. |
| `tests/utils/flow_session.py` | Sửa docstring `fetch_recaptcha()` (production giờ dùng cùng JS). |
| `client_tool/CLAUDE.md` | Cập nhật §4.1/§4.3/§11.2 — POST không còn qua `requests` Python thuần. |

**Bug thật (log production 16:52):** `reCAPTCHA refresh: Invalid site key or not loaded in api.js: VIDEO_GENERATION` rồi `Chưa có API token cho video generation`.

**3 lệch so với `_test_textToImage.py` (đã proven HTTP 200):**

1. **`execute_async_script` đảo argument** — Selenium luôn nhét callback `done` vào `arguments[CUỐI]`. Worker cũ gán `done=arguments[0]`, `fallback=arguments[1]`, `action=arguments[2]` trong khi gọi `execute_async_script(js, siteKey, action)` → `done` = siteKey string, `fallback` = `'VIDEO_GENERATION'`, `action` = function. `grecaptcha.enterprise.execute('VIDEO_GENERATION', …)` ném đúng lỗi trên (throw đồng bộ, không vào `.catch()` của Promise). Test dùng `var done = arguments[arguments.length - 1]` nên không bị.
2. **Authorization chỉ bắt từ `requestWillBeSent`** (key lowercase cứng) — test còn GET `/fx/api/auth/session` từ tab + merge `requestWillBeSentExtraInfo` (x-browser-validation / đôi khi cả authorization trên Chrome mới, log user là chrome=151). Không có ExtraInfo + không fetch session → `has_tokens()` = False → raise "Chưa có API token" ngay sau recaptcha fail.
3. **POST bằng `requests` Python thuần** (`Content-Type: application/json`) — test POST `fetch()` từ tab labs.google (hoặc curl_cffi impersonate Chrome), `content-type: text/plain;charset=UTF-8`, có `x-goog-api-key`. `requests` thường 403/bị chặn TLS fingerprint. User yêu cầu không dùng proxy.

**Fix:** `_get_fresh_recaptcha()` dùng nguyên `RECAPTCHA_FETCH_JS` của test. `_fetch_labs_session()` + ExtraInfo drain. `_post_aisandbox()` = browser `fetch()` (không proxy) → fallback `curl_cffi` (cũng không SOCKS). Áp dụng cho uploadImage / image API / 3 endpoint video.

**Verify:** `py_compile`/`pyflakes` sạch. **CHƯA verify round-trip profile API thật** — cần restart client_tool rồi chạy 1 task video.

---

### 2026-08-13 — Setting mới: "Log đầy đủ curl" khi gọi API tạo ảnh/video

| File | Thay đổi |
|------|---------|
| `server/config.py` | `_DEFAULT_SERVER_SETTINGS` thêm `debug_log_curl` (0/1, mặc định 0=tắt). |
| `server/local_settings.py` | `_clamp()` — validate `debug_log_curl` về đúng 0/1 (cùng pattern `quiet_hours_enabled`). |
| `server/worker.py` | `_build_curl_command(url, headers, payload)` (mới, staticmethod) — dựng chuỗi `curl` copy-paste được (URL + mọi header + body JSON nguyên văn, tự escape dấu `'` an toàn cho shell). `_post_aisandbox()` — khi `debug_log_curl` bật: log lệnh `curl` NGAY TRƯỚC KHI gửi request (`[curl-debug] Request:...`) + log response body ĐẦY ĐỦ (không cắt 300 ký tự như log lỗi mặc định) ngay sau khi nhận, cả 2 nhánh `curl_cffi` lẫn page-fetch fallback. |
| `gui/pages/settings_page.py` | `_FIELD_DEFS` thêm dòng `debug_log_curl` (text field "0"/"1", cùng style `quiet_hours_enabled` — trang Cài đặt cố ý không thêm loại input mới). |

**Trả lời câu hỏi user** ("setting logs client_tool có thêm bật ghi lại log curl đầy đủ khi gọi tạo video/image qua api không") — TRƯỚC ĐÂY KHÔNG có, log chỉ gọn (`POST aisandbox HTTP 200 (via curl_cffi/chrome131)`, lỗi thì cắt 300 ký tự). Giờ đã thêm, opt-in qua Cài đặt.

**Phạm vi:** `_post_aisandbox()` là điểm gọi DUY NHẤT cho CẢ 3 loại request trực tiếp API — `uploadImage` (ảnh tham chiếu), `batchGenerateImages` (tạo ảnh), và cả 3 endpoint video (`TEXT_TO_VIDEO`/`INGREDIENT_TO_VIDEO`/`FRAME_TO_VIDEO`) — bật 1 setting này log đầy đủ cho TẤT CẢ, không cần bật riêng từng loại.

**⚠️ Bảo mật:** header `authorization` (bearer token phiên đang chạy) KHÔNG bị che trong log khi bật — cố ý, vì mục đích là copy lệnh `curl` dán thẳng vào terminal tái hiện request y hệt (che token sẽ làm mất tác dụng). Mặc định TẮT, chỉ nên bật tạm lúc debug, không chia sẻ log ra ngoài lúc đang bật.

Verify: `_build_curl_command()` test trực tiếp (Python thuần) — dựng đúng cú pháp `curl -sS -X POST '...' -H '...' --data-raw '...'`, escape đúng dấu `'` bên trong payload (`it's a test` → `it'\''s a test`, an toàn copy-paste vào shell). `py_compile`/`pyflakes` sạch. **CHƯA verify trên browser thật** — cần user bật setting rồi chạy 1 task ảnh/video, xác nhận thấy dòng `[curl-debug]` trong log profile.

---

### 2026-08-13 — FIX GUI: trang Logs nhảy lên đầu trang mỗi lần auto-refresh khi đang cuộn đọc + tăng độ tương phản thanh scroll

| File | Thay đổi |
|------|---------|
| `gui/pages/logs_page.py` | `_render()` — LUÔN lưu + khôi phục giá trị scroll TUYỆT ĐỐI (không chỉ khi đang ở đáy như trước) qua mỗi lần render lại. `_log` (QTextEdit) — `setVerticalScrollBarPolicy(ScrollBarAlwaysOn)`. |
| `gui/style.py` | `QScrollBar` (áp dụng TOÀN APP) — width 8px→12px, handle đổi màu từ `C['border']` (gần trùng nền tối) sang `C['surface3']` + viền `C['muted']` — dễ nhận ra hơn trên nền tối. |

**Theo yêu cầu user:** "trang logs profile khi đang scroll đọc log cứ giật lên đầu trang gây khó khăn và thanh bar để trượt scroll cũng ko thấy".

**Root cause "giật lên đầu trang":** `LogsPage.auto_tick()` gọi `reload()` mỗi `LOG_REFRESH_MS` (mặc định 4s, `gui/config.py`) — `_render(rows)` LUÔN `self._log.clear()` rồi build lại TOÀN BỘ nội dung. Bản cũ CHỈ khôi phục vị trí scroll khi đang Ở ĐÁY (`at_bot`) — nếu user đang cuộn LÊN đọc log cũ (đúng lúc đang đọc), vị trí đó KHÔNG được lưu, Qt tự đưa scrollbar về 0 sau `clear()`+insert mới → nhảy lên đầu trang đúng mỗi 4s. Fix: LUÔN lưu `vsb.value()` TRƯỚC khi render; sau khi render xong — đang ở đáy thì tiếp tục theo dõi log mới (auto-scroll xuống đáy mới, hành vi "tail -f"), MỌI vị trí khác đều khôi phục lại ĐÚNG giá trị cũ (clamp theo max mới, phòng nội dung ngắn lại khi đổi filter/limit).

**Root cause "thanh scroll không thấy":** `QScrollBar::handle:vertical` mặc định dùng màu `C['border']` (`#30363d`) — gần như trùng màu nền các khung tối (`C['surface']`/`C['bg']`, `#161b22`/`#0d1117`), độ tương phản thấp trên theme tối của app. Tăng width + đổi màu handle sang tương phản rõ hơn (`C['surface3']` + viền `C['muted']`), áp dụng nhất quán mọi nơi có scrollbar trong app (không riêng trang Logs). Riêng khung log còn ép `ScrollBarAlwaysOn` — luôn hiện, không đợi hover/policy "as needed".

Verify: smoke test headless PyQt6 thật (`QT_QPA_PLATFORM=offscreen`, ép geometry thật để có overflow thật, không chỉ dựng widget suông) — cuộn tới giữa (value=1474/max=2949) → render lại (cùng rows, mô phỏng auto-refresh) → vị trí giữ NGUYÊN 1474 (trước fix sẽ về 0); test tiếp ở đáy → render thêm 1 dòng mới → tự theo tới đáy MỚI. `py_compile`/`pyflakes` sạch. **CHƯA verify hiển thị thật trên GUI** (độ tương phản màu là đánh giá chủ quan, cần user tự nhìn xác nhận) — cần user tự mở lại app.

---

### 2026-08-13 — Log LUÔN mã model (imageModelName) đi kèm tên model, không chỉ lúc fallback

Theo yêu cầu user "log thêm mã model đi theo tên model để biết lấy đúng ko" — `_call_image_api_v2()` trước đó CHỈ log khi KHÔNG khớp được label (fallback về NARWHAL) — task dùng đúng 1 trong 3 label đã biết ("Nano Banana Pro"/"Nano Banana 2"/"Nano Banana 2 Lite", xem entry ngay dưới) hoàn toàn im lặng, không có cách nào tự soi log xác nhận đã map đúng mã hay chưa. Giờ LUÔN log 1 dòng `info` `Task #id model="tên" → imageModelName=mã` (khớp hay không đều log), riêng trường hợp KHÔNG khớp vẫn giữ thêm 1 dòng `warn` để dễ phân biệt bằng mắt (không lẫn vào các dòng info bình thường). Nhánh video (`_call_video_api()`) đã log kiểu này từ trước (`model=... → videoModelKey=...`, luôn log không điều kiện) — không cần sửa gì thêm, chỉ đồng bộ hoá cho nhánh ảnh.

`py_compile`/`pyflakes` sạch. Chỉ đổi log — không đổi logic resolve model nào.

---

### 2026-08-13 — Cache cục bộ `media.name` ảnh tham chiếu đã upload cho task video — retry không phải upload lại

| File | Thay đổi |
|------|---------|
| `server/media_upload_cache.py` (mới) | `get_cached_media_names(task_id, project_id)`/`set_cached_media_names(...)`/`clear_cached_media_names(task_id)` — cache JSON cục bộ (`client_tool/uploaded_media_cache.json`, gitignore), khoá theo CẢ `task_id` LẪN `project_id`, trần `_MAX_ENTRIES=1000` (bỏ entry cũ nhất khi đầy). |
| `server/worker.py` | `_prepare_video_uploads()` — check cache TRƯỚC KHI tải/upload (mode `imageToVideo`/`componentsToVideo`/`frameToVideo`; `textToVideo` không có ảnh nên bỏ qua); khớp `project_id` HIỆN TẠI thì dùng lại ngay, bỏ qua upload; upload MỚI xong thì lưu lại cache. `_wait_and_reconcile_tasks()` — task video reconcile THÀNH CÔNG thì `clear_cached_media_names(tid)` (không còn cần cache nữa). |
| `.gitignore` | Thêm `uploaded_media_cache.json`. |

**Theo yêu cầu user:** "lưu lại luôn id ảnh tham chiếu đã upload thành công kèm id project hiện đang chạy vào task, đề phòng lỗi video đã có ảnh tham chiếu upload thì đính kèm lại ảnh tham chiếu nếu cùng id project ko cần upload mới".

**Lý do:** `_prepare_video_uploads()` tải+upload từng ảnh `source_media` của task video TRƯỚC KHI generate — nếu bước GENERATE (POST sau đó) lỗi/task phải retry (server reap timeout, `_handle_task_error()` escalation, worker sleeping rồi tự phục hồi §11.31, restart app...), upload TRƯỚC ĐÓ đã tốn băng thông/thời gian nhưng bị làm lại từ đầu mỗi lần retry — dù ảnh đã tồn tại sẵn trên server (aisandbox) dưới `media.name` cũ, chỉ cần biết lại UUID đó là dùng được ngay, không cần tải+upload lại.

**Vì sao khoá CẢ `project_id`, không chỉ `task_id`:** `media.name` gắn với 1 project CỤ THỂ trên aisandbox lúc upload — nếu sau đó worker đổi project (`_rotate_project_if_full()` §11.20e, hoặc `_reset_flow_project()` lúc escalation lỗi liên tiếp) trước khi retry, `media.name` cũ có thể không còn dùng được cho project MỚI — so khớp đúng `project_id` hiện tại trước khi tái sử dụng, khác thì coi cache miss, upload lại bình thường (an toàn tuyệt đối — không có rủi ro gửi nhầm media.name của project khác).

Verify: test trực tiếp `media_upload_cache.py` (Python thuần) — hit đúng khi cùng project, miss khi khác project, sống sót qua mô phỏng "restart process" (reload từ disk), xoá đúng sau `clear_cached_media_names()`. `py_compile`/`pyflakes` sạch. **CHƯA verify trên browser thật** — cần user chạy 1 task video lỗi ở bước generate (sau khi upload xong), xác nhận lần retry kế tiếp thấy log "dùng lại N ảnh đã upload trước đó" thay vì upload lại từ đầu.

---

### 2026-08-13 — FIX THẬT: task ẢNH qua API mode lỗi 400 INVALID_ARGUMENT vì gửi thẳng tên hiển thị GUI làm `imageModelName` — đã có mapping ĐẦY ĐỦ 3 model

**Log thật (task #2454, mode=textToImage/imageToImage, model="Nano Banana Pro"):** `uploadImage` 200 OK, nhưng `batchGenerateImages` bị `aisandbox` từ chối **400 "Request contains an invalid argument"** ở CẢ 4 lần thử `curl_cffi` (impersonate chrome131/chrome136/chrome124/chrome — loại trừ hẳn nguyên nhân mạng/proxy/impersonate, đây là server aisandbox THẬT SỰ từ chối payload), rồi fallback page-fetch cũng fail (`TypeError: Failed to fetch`, CORS — hành vi ĐÃ BIẾT từ trước, xem docstring `_headers_for_page_fetch()`) → task lỗi hẳn.

| File | Thay đổi |
|------|---------|
| `server/flow_api.py` | Thêm `_IMAGE_MODEL_LABEL_TO_KEY` (mapping ĐẦY ĐỦ 3 model ảnh, xem dưới) + `_norm_image_model_label()` + `resolve_image_model_key(name) -> (imageModelName, matched)`, cùng cấu trúc `_VIDEO_MODEL_LABEL_TO_KEY`/`resolve_video_model_key()` đã có cho video. |
| `server/worker.py` | `_call_image_api_v2()` — gọi `resolve_image_model_key()` trước khi build body thay vì gửi thẳng `task.get('model')`; label lạ (ngoài 3 cái đã biết) mới fallback về `NARWHAL` + log `warn`. |

**Root cause:** `tasks_media_flow.model` cho task ẢNH lưu TÊN HIỂN THỊ GUI (`ProjectManager.jsx::FALLBACK_IMAGE` — "Nano Banana Pro"/"Nano Banana 2"/"Nano Banana 2 Lite"), giống hệt cách video lưu "Veo 3.1 - Fast" (đã có `resolve_video_model_key()` xử lý từ trước) — nhưng `_call_image_api_v2()` GỬI THẲNG chuỗi này làm `imageModelName` mà KHÔNG qua bước resolve nào.

**Mapping THẬT (user xác nhận trực tiếp, KHÔNG phải đoán):**

| Tên hiển thị GUI | `imageModelName` thật |
|---|---|
| 🍌 Nano Banana Pro | `GEM_PIX_2` |
| 🍌 Nano Banana 2 | `NARWHAL` |
| 🍌 Nano Banana 2 Lite | `HARBOR_SEAL` |

Verify trực tiếp `resolve_image_model_key()` (Python thuần, không cần browser) — cả 3 label (kể cả biến thể khoảng trắng/hoa-thường) map đúng key; key thật gửi thẳng (`'GEM_PIX_2'`) pass-through; label lạ/rỗng/`None` → fallback `NARWHAL` + `matched=False` (kích hoạt log warn). `py_compile`/`pyflakes` sạch.

**CHƯA verify trên browser thật** — cần user chạy lại 1 task ảnh model "Nano Banana Pro" (hoặc "Nano Banana 2 Lite") thật, xác nhận không còn 400 INVALID_ARGUMENT và ảnh sinh ra ĐÚNG model đã chọn (không còn dòng log warn "KHÔNG khớp label model ảnh nào đã biết" cho 3 model này nữa — dòng đó giờ chỉ còn kích hoạt cho model ảnh MỚI/lạ ngoài 3 cái trên).

---

### 2026-08-12 — Log profile API mode: thêm TASK id + model (cả tên hiển thị lẫn `videoModelKey`) vào các dòng log còn thiếu

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_submit_video_api_task()`, `_run_tasks_batch()` (DOM batch), `_run_task_api()` — dòng log "▶ Task #..." lúc bắt đầu task giờ có thêm `model=...` (mirror pattern đã có sẵn ở `_run_task_dom()`). `_call_video_api()`'s nhánh `textToVideo` — dòng log resolve model (`flow_api.resolve_video_model_key()`) giờ có thêm `Task #{id}` để tương quan đúng dòng log với đúng task khi nhiều dòng log xen kẽ. |

**Theo yêu cầu user:** "log profile thêm TASK id + model dùng như: Veo 3.1 - Lite [Lower Priority] / veo_3_1_t2v_lite_low_priority" — ám chỉ đúng cặp `_VIDEO_MODEL_LABEL_TO_KEY` trong `flow_api.py` (tên hiển thị GUI/DB → `videoModelKey` thật gửi API). Trước đây 1 vài dòng log "bắt đầu task" (API mode) chỉ có `Task #id`/`mode`, không có `model` — khó chẩn đoán khi nghi ngờ chọn nhầm model (cùng lớp vấn đề đã gặp nhiều lần ở DOM mode, xem §11.30). Dòng log resolve model (đã có sẵn từ trước trong `_call_video_api()`) có `model`+`videoModelKey` nhưng KHÔNG có task id — khó biết dòng đó thuộc task nào khi xem log 1 profile chạy nhiều task liên tiếp.

Chỉ đổi text log — không đổi logic chọn/resolve model nào. `py_compile`/`pyflakes` sạch.

---

### 2026-08-12 — Tự phục hồi khi "sleeping" đổi sang XOÁ CHỈ CACHE — KHÔNG còn đăng xuất Google

| File | Thay đổi |
|------|---------|
| `server/managers.py` | Thêm `_CACHE_ONLY_DIR_NAMES` + `_clear_chrome_cache_only(profile_dir)` (xoá NÔNG các thư mục cache thuần `Cache`/`Code Cache`/`GPUCache`/`DawnCache`/.../`ShaderCache`, tại gốc `profile_dir` VÀ 1 cấp con — GIỮ NGUYÊN `Cookies`/`Web Data`/`Login Data`/`History`/mọi thứ khác) + `pm.clear_cache_only(profile_id)` (wrapper, mirror `clear_browser_data()`). |
| `server/worker.py` | `_clear_profile_if_sleeping()` — đổi từ gọi `pm.clear_browser_data()` (xoá SẠCH, mất đăng nhập) sang `pm.clear_cache_only()` (chỉ cache). Docstring/log cập nhật theo. |

**Theo yêu cầu user:** "lỗi nhiều vào trạng thái ngủ tự xóa cache không xóa cookie".

**Lý do đổi:** cơ chế tự phục hồi khi profile "ngủ" do lỗi liên tiếp (§11.31, thêm 2026-08-10) TRƯỚC ĐÂY tái dùng NGUYÊN `pm.clear_browser_data()` — vốn là hàm cho nút "Làm mới profile" THỦ CÔNG (xoá SẠCH TOÀN BỘ `profile_dir`, kể cả `Cookies`/`Web Data`/`Login Data` — coi như profile trắng, theo đúng yêu cầu gốc "coi như 1 profile trắng" của TÍNH NĂNG ĐÓ). Áp dụng y hệt cho đường TỰ ĐỘNG là quá tay — mỗi lần 1 profile bị lỗi nhiều/ngủ, nó MẤT ĐĂNG NHẬP GOOGLE ngay, buộc `_ensure_google_login()` (§11.31) phải tự đăng nhập lại bằng mật khẩu đã lưu ở lần chạy kế tiếp — tốn thời gian, và tăng rủi ro Google chặn/đòi xác minh nếu 1 script tự đăng nhập lặp lại quá thường xuyên. Mục đích ban đầu của việc tự dọn dẹp khi sleeping chỉ là "khả năng cao cache Chrome đã hỏng gây lỗi render/JS state kẹt" — không cần đụng tới cookie/session để đạt được điều đó.

**Fix:** hàm mới `_clear_chrome_cache_only()` CHỈ xoá thư mục CACHE THUẦN (Chrome tự tạo lại từ đầu, không chứa gì nhạy cảm) — `Cache`/`Cache2`/`Code Cache`/`GPUCache`/`DawnCache`/`DawnGraphiteCache`/`GrShaderCache`/`ShaderCache`/`Media Cache`, quét NÔNG (gốc `profile_dir` + đúng 1 cấp con, vd `Default/`) — KHÔNG đụng `Cookies`/`Web Data`/`Login Data`/`History`/`Bookmarks`/`Preferences`/`Local Storage`/bất kỳ gì khác. `_clear_profile_if_sleeping()` giờ gọi `pm.clear_cache_only()` thay `pm.clear_browser_data()` — profile vẫn đăng nhập sẵn ở lần chạy kế tiếp, `_ensure_google_login()` nhận diện "đã đăng nhập" ngay (không rơi vào nhánh gõ mật khẩu). Nút "Làm mới profile" thủ công trong GUI (đã có dialog cảnh báo mất đăng nhập từ trước) **GIỮ NGUYÊN 100%**, vẫn gọi `clear_browser_data()`/`_wipe_chrome_profile_dir()` như cũ — chỉ đường TỰ ĐỘNG này đổi.

**Verify:** `py_compile`/`pyflakes` sạch (`server/managers.py`, `server/worker.py`). Test trực tiếp trên thư mục Chrome-profile giả (`Default/Cache`+`Code Cache`+`GPUCache`+root `ShaderCache` + `Default/Cookies`/`Web Data`/`Login Data`/`Preferences`/`History`/`Bookmarks`/`Local Storage`) — xác nhận đúng 4 thư mục cache bị xoá, TOÀN BỘ 7 mục còn lại (kể cả `Local Storage` chứa file bên trong) sống sót nguyên vẹn. **CHƯA verify trên Chrome/profile thật đang sleeping** — cần user quan sát 1 lần profile thật rơi vào sleeping, xác nhận log `[auto-refresh] ✔ Đã xoá cache...` và lần chạy kế tiếp KHÔNG bị đá ra màn hình đăng nhập.

---

### 2026-08-12 — Production: tích hợp API THUẦN (image + 3 loại video) vào `server/worker.py`, thay hẳn body/endpoint SAI đã dùng từ trước

| File | Thay đổi |
|------|---------|
| `server/flow_api.py` (mới) | Port THUẦN DỮ LIỆU từ `tests/utils/flow_api.py` (constants + body builders + parsers, KHÔNG có `FlowBrowserSession`/`FlowAPIClient` — `SeleniumFlowWorker` tự quản lý driver/token riêng). |
| `server/worker.py` | `_get_fresh_recaptcha(action='IMAGE_GENERATION')` — tham số hoá (đã làm ở lượt sửa trước, giờ mới THẬT SỰ dùng: `_run_task_api()` truyền `'IMAGE_GENERATION'`/`'VIDEO_GENERATION'` đúng theo nhánh ảnh/video). Thêm `_parse_source_media()`/`_download_source_media()`/`_upload_media_to_flow()` (helper dùng chung). Viết lại HOÀN TOÀN `_call_image_api_v2()` (dùng `flow_api.build_text_to_image_body()`, hỗ trợ imageToImage qua upload+`imageInputs`) và `_call_video_api()` (branch theo `task['mode']` sang 1 trong 3 endpoint thật `TEXT_TO_VIDEO`/`INGREDIENT_TO_VIDEO`/`FRAME_TO_VIDEO`). Xoá hẳn `_build_body()` (dead code sau khi 2 hàm trên không còn gọi tới). |
| `client_tool/CLAUDE.md` | Cập nhật §11 tổng quan (bảng "Endpoint mới image") — trỏ sang mô tả đầy đủ trong entry này. |

**Theo yêu cầu user:** "tích hợp các cách truyền API để tạo video từ @_test_textToImage, @test_imageToImage, @test_textToVideo, @test_ingredientToVideo, @test_frameToVideo vào cách chạy api của profile /client_tool" — đưa cơ chế đã proven ở `tests/` (§11.32/§11.33) vào ĐƯỜNG CHẠY THẬT của profile `worker_mode='api'`.

**3 bug THẬT tìm thấy ở code production CŨ (đã sửa hoàn toàn):**
1. **Endpoint video SAI/không tồn tại** — `_call_video_api()` cũ POST `{AISANDBOX_BASE}:batchAsyncGenerateVideo` (route Vertex-AI-style, không khớp bất kỳ endpoint capture thật nào) rồi poll `GET {AISANDBOX_BASE}/{op_name}` — không có bằng chứng route này từng hoạt động, không phân biệt textToVideo/imageToVideo/frameToVideo/componentsToVideo (tất cả đi qua CÙNG 1 nhánh).
2. **Body shape SAI** — `_build_body()` cũ dựng theo shape `instances[]`/`parameters{sampleCount,aspectRatio,modelId}` (kiểu Vertex AI cũ) — KHÔNG khớp shape thật đã capture (`requests[]`/`structuredPrompt`/`imageModelName`/`imageInputs`, xem `flow_api.py`).
3. **Ref ảnh bị ÂM THẦM BỎ QUA** — `_build_body()` cũ đọc `source_media[i]['data']` (base64 inline) để build `instance['image']`/`instance['lastFrame']` — nhưng field THẬT server gửi qua heartbeat chỉ có `source_media[i]['url']` (path server-hosted), KHÔNG BAO GIỜ có `.data` → `if images[0].get('data')` luôn `False` → ảnh tham chiếu KHÔNG ĐƯỢC ĐÍNH KÈM cho MỌI task imageToImage/imageToVideo/frameToVideo/componentsToVideo chạy qua API mode, không có exception/log nào tố cáo (task vẫn "thành công", chỉ sai nội dung — cùng lớp bug "âm thầm sai" đã gặp nhiều lần trong file này).

**Thiết kế mới:**
- **Ảnh** (`_call_image_api_v2()`): `task['source_media']` → `_download_source_media()` (tải, mirror cách `_dom_upload_images()` xử lý path tương đối qua `FLOW_SERVER`) → `_upload_media_to_flow()` (POST `/flow/uploadImage`, không cần recaptcha) → `flow_api.build_image_input_ref()` → đính vào `imageInputs` của `build_text_to_image_body()`. `can_api` gate bỏ điều kiện `bool(self._tokens.get('lastRequestBody'))` (không còn cần thiết — body giờ tự dựng, không cần template capture).
- **Video** (`_call_video_api()`): branch theo `mode` — `textToVideo`→`TEXT_TO_VIDEO` (model do user chọn qua `task['model']`); `imageToVideo`/`componentsToVideo`→`INGREDIENT_TO_VIDEO` (model CỐ ĐỊNH `veo_3_1_r2v_lite` — do CHÍNH Flow UI tự chọn khi mở sub-tab "Thành phần", KHÔNG PHẢI model user chọn ở dropdown); `frameToVideo`→`FRAME_TO_VIDEO` (model CỐ ĐỊNH `abra_i2v_8s`, CHỈ dùng ẢNH ĐẦU — `endImage` CHƯA proven, xem §11.33).
- **Lấy kết quả VIDEO — hybrid submit-API + poll-DOM (quyết định user qua AskUserQuestion, chọn "Submit qua API mới + poll kết quả qua tile DOM có sẵn (khuyến nghị)"):** cả 3 endpoint video chỉ xác nhận đã TẠO workflow (async, không trả URL ngay) — KHÔNG có cơ chế poll-qua-API nào đã proven. Thay vì tự dựng poll API chưa ai xác nhận, `_call_video_api()` snapshot `_dom_tile_ids()` NGAY TRƯỚC khi POST, rồi TÁI DÙNG NGUYÊN `_collect_task_media_via_tiles()` (đã proven ổn định ở DOM mode, xem §5.5/§5.6) để chờ tile mới xuất hiện trên trang Flow (đang mở sẵn, cùng project) và resolve CDN URL — không viết cơ chế polling mới nào.
- **Giữ nguyên shape trả về cho caller** (`_run_task_api()` KHÔNG cần sửa gì ngoài 2 lời gọi `_get_fresh_recaptcha()`): `_call_image_api_v2()` vẫn trả `[{'url':...}]`, `_call_video_api()` vẫn trả `[{'uri':...}]`.

**Modes/models hiện tham chiếu — tóm tắt cho user (câu hỏi thứ 2 trong yêu cầu):**
- Model ẢNH mặc định: `NARWHAL` (`flow_api.DEFAULT_IMAGE_MODEL`) — override qua `task['model']` nếu server gửi.
- Model VIDEO textToVideo mặc định: `veo_3_1_t2v_lite_low_priority` (`flow_api.DEFAULT_VIDEO_MODEL`) — override qua `task['model']` (giá trị thật do GUI/DB `selenium_profiles`/`tasks_media_flow.model` set).
- Model VIDEO ingredientToVideo/componentsToVideo: CỐ ĐỊNH `veo_3_1_r2v_lite` (`flow_api.DEFAULT_INGREDIENT_VIDEO_MODEL`) — KHÔNG đọc `task['model']`, vì Flow UI tự chọn model này khi vào sub-tab "Thành phần" (không phải lựa chọn của user).
- Model VIDEO frameToVideo: CỐ ĐỊNH `abra_i2v_8s` (`flow_api.DEFAULT_FRAME_VIDEO_MODEL`) — tương tự, do sub-tab "Khung hình" tự chọn.
- reCAPTCHA action: `IMAGE_GENERATION` (ảnh) / `VIDEO_GENERATION` (cả 3 loại video) — `flow_api.py` không chứa action, action nằm ở `_get_fresh_recaptcha(action)` (`server/worker.py`), truyền tường minh từ `_run_task_api()`.
- **Sửa khi có model/mode mới:** thêm biến thể vào `flow_api.py` (endpoint/model default) rồi cập nhật nhánh `if/elif` trong `_call_video_api()`/tham số `model=` trong `_call_image_api_v2()` — KHÔNG cần đụng `_run_task_api()` (chỉ gọi 2 hàm này, không biết chi tiết mode/model).

**Verify:** `py_compile`/`pyflakes` sạch (`server/worker.py`, `server/flow_api.py`). Endpoint/body/model đều đã verify HTTP 200 thật qua `tests/` (§11.32/§11.33) TRƯỚC KHI port sang production — code production chỉ port lại logic đã proven, không phải logic mới chưa test. **CHƯA verify round-trip qua hàng đợi heartbeat thật với 1 profile `worker_mode='api'` sống** (môi trường này không chạy được Chrome) — cần user tự chạy 1 task ảnh + 1 task mỗi loại video qua profile API thật để xác nhận không regression so với hành vi cũ (vốn đã sai từ đầu nên "không regression" ở đây nghĩa là kết quả ĐÚNG hơn, không phải giữ nguyên hành vi cũ).

---

### 2026-08-12 — Video qua API THUẦN: reCAPTCHA action `VIDEO_GENERATION` + test ingredientToVideo/frameToVideo

| File | Thay đổi |
|------|---------|
| `tests/utils/flow_session.py` | `RECAPTCHA_FETCH_JS`/`fetch_recaptcha(action=...)` — action giờ tham số hoá (mặc định `'IMAGE_GENERATION'`, video truyền `'VIDEO_GENERATION'`), không còn hardcode 1 giá trị duy nhất. |
| `tests/utils/flow_api.py` | Thêm `INGREDIENT_TO_VIDEO`/`FRAME_TO_VIDEO` endpoint, `DEFAULT_INGREDIENT_VIDEO_MODEL`/`DEFAULT_FRAME_VIDEO_MODEL`, `build_video_reference_image()`, `build_ingredient_to_video_body()`, `build_frame_to_video_body()`, `parse_video_workflow()`; `FlowAPIClient.generate_ingredient_to_video()`/`generate_frame_to_video()`; `generate_text_to_video()` đổi sang `fetch_recaptcha('VIDEO_GENERATION')`. |
| `tests/_test_textToVideo.py` | Viết lại HOÀN TOÀN — bản cũ dùng `session.post()` trực tiếp + `clear_cache_on_start=True` (đăng xuất profile mỗi lần chạy, đã cảnh báo sai từ `_test_textToImage.py`), CHƯA từng verify thật ("API explored — cần token hợp lệ"). Bản mới cùng cấu trúc CLI với `_test_textToImage.py`, qua `FlowAPIClient.generate_text_to_video()`. |
| `tests/_test_ingredientToVideo.py` (mới) | Test componentsToVideo/ingredientToVideo — N ảnh nguyên liệu (`--image`, lặp lại được) → video, thuần API. |
| `tests/_test_frameToVideo.py` (mới) | Test frameToVideo — ảnh khung hình đầu (`--start-image`) → video, thuần API. `--end-image` đánh dấu THỬ NGHIỆM (chưa proven server chấp nhận). |
| `tests/FLOW_API_CAPTURE.md` | Thêm mục "6b. Ingredient/Components -> Video" + "6c. Frame -> Video"; sửa mục 6 (text-to-video) — response shape cũ `operations[]`/`name` là giả định CHƯA TỪNG verify, thực tế (capture mới) là `{remainingCredits,workflows[],media[]}`. |
| `client_tool/CLAUDE.md` | Thêm §11.33. |

**Theo yêu cầu user:** "grecapcha thay action 'VIDEO_GENERATION' cho tạo video và viết lại file test textToVideo, thêm file test ingredient to video, frame to video".

**Khám phá qua browser thật (2 endpoint mới, chưa từng biết trước đây):**
- **ingredientToVideo** (sub-tab "Thành phần", N ảnh nguyên liệu) — `POST /v1/video:batchAsyncGenerateVideoReferenceImages`, `requests[].referenceImages=[{mediaId,imageUsageType:"IMAGE_USAGE_TYPE_ASSET"}]`, `videoModelKey:"veo_3_1_r2v_lite"`. Capture trên project "khanh" — HTTP 200, video tạo thành công.
- **frameToVideo** (sub-tab "Khung hình") — theo yêu cầu user dùng profile PRODUCTION `huavantien84_2` để test (profile chưa đăng nhập, user tự gõ mật khẩu vào cửa sổ Chrome do script mở sẵn — AI không đụng vào mật khẩu). `POST /v1/video:batchAsyncGenerateVideoStartImage`, `requests[].startImage={mediaId,cropCoordinates}`, `videoModelKey:"abra_i2v_8s"`. **Phát hiện quan trọng:** sub-tab này dùng UI khác hẳn (2 div "Bắt đầu"/"Kết thúc" riêng, KHÔNG phải nút `add_2` mà DOM production dùng cho mọi mode khác — production hiện KHÔNG hỗ trợ đúng UI này, chỉ ghi nhận, chưa sửa). `endImage` field xuất hiện trong 1 lần thử (do bug ảnh test 2 hỏng — PNG base64 hand-typed sai checksum, khiến upload thất bại và cả 2 ảnh trỏ cùng 1 mediaId cũ) → server 400 "Unknown name endImage". Sau khi fix ảnh test bằng PIL, lần thử với 2 ảnh THẬT khác nhau cho request THÀNH CÔNG (HTTP 200) nhưng CHỈ CÓ `startImage` — `endImage` không được gửi. Kết luận: true "frame interpolation" (2 khung hình) KHÔNG xác nhận được khả dụng qua endpoint/model này.

**Verify:** `py_compile`/`pyflakes` sạch. HTTP 200 thật cho cả 2 endpoint mới qua script investigation (đã xoá sau khi dùng, kết quả ghi lại đầy đủ trong `FLOW_API_CAPTURE.md`). Verify LẠI với bản implementation cuối (`_test_*.py` qua `FlowAPIClient`, không phải script investigation): `_test_textToVideo.py` (`--model veo_3_1_t2v_lite`) và `_test_ingredientToVideo.py` (model mặc định) → HTTP 200 thật trên account "khanh". `_test_frameToVideo.py` → build/upload/POST đúng nhưng hết quota thật (HTTP 429) trên "khanh" sau nhiều lần generate liên tiếp trong phiên test — đã verify HTTP 200 THẬT riêng cho đúng endpoint này qua account `huavantien84_2` lúc investigate. Model mặc định cũ `DEFAULT_VIDEO_MODEL="veo_3_1_t2v_lite_low_priority"` (đã có từ trước, không phải hằng số mới) bị 403 `MODEL_ACCESS_DENIED` trên "khanh" — account này không có biến thể "[Lower Priority]" trong dropdown video (chỉ 4 model: Omni Flash/Lite/Fast/Quality).

---

### 2026-08-12 — imageToImage qua API THUẦN (upload ảnh + `imageInputs`) — không cần DOM/picker

| File | Thay đổi |
|------|---------|
| `tests/utils/flow_api.py` | Thêm `UPLOAD_IMAGE` endpoint, `build_upload_image_body()`, `parse_uploaded_image_name()`, `build_image_input_ref()`; `build_text_to_image_body()` nhận thêm tham số optional `image_inputs` (mặc định `None` — 100% backward-compat với textToImage cũ); `FlowAPIClient.upload_image()`/`generate_image_to_image()` mới. |
| `tests/_test_imageToImage.py` | Viết lại HOÀN TOÀN — bản cũ là script khám phá DOM dở dang (`webdriver.Chrome` trực tiếp, chưa tìm ra cơ chế thật). Bản mới cùng stack `FlowBrowserSession`/`flow_api` với `_test_textToImage.py`, chạy imageToImage THUẦN QUA API (2 bước: upload ảnh → generate với `imageInputs`), không đụng DOM/picker. |
| `tests/FLOW_API_CAPTURE.md` | Thêm mục "5b. Image-to-Image" (endpoint `flow/uploadImage` + shape `imageInputs`) + cập nhật bảng "File liên quan". |
| `client_tool/CLAUDE.md` | Thêm §11.32. |

**Theo yêu cầu user:** "_test_textToImage.py đã chạy ok text-to-image hã dùng trinh duyệt bắt sự kiện image-to-image để thực hiện upload image rồi đính kèm image vào prompt chạy qua api".

**Cách khám phá cấu trúc thật (không đoán):** viết 1 script investigation throwaway — launch Chrome qua `FlowBrowserSession` (profile "khanh" đã login sẵn), cài fetch+XHR interceptor bắt request+response body cho mọi call `labs.google`/`aisandbox-pa.googleapis.com`, rồi gọi TRỰC TIẾP 3 hàm production của `SeleniumFlowWorker` (`server/worker.py`'s `_dom_configure`/`_dom_upload_images`/`_dom_fill_and_submit` — instantiate 1 worker với profile dict GIẢ, gán thẳng `.driver` từ session đang mở, bỏ qua `_make_driver()` nên KHÔNG cần DB nào) để chạy 1 lần imageToImage THẬT qua picker DOM thật (ảnh test 1x1 PNG). Bắt được:

1. `POST /v1/flow/uploadImage` — upload ảnh, KHÔNG cần recaptcha (khác `batchGenerateImages`). Body: `{clientContext:{projectId,tool:"PINHOLE"}, imageBytes:"<base64 thuần>", isUserUploaded:true, isHidden:false, mimeType, fileName}`. Response: `{media:{name:"<uuid>",...}}`.
2. `POST .../flowMedia:batchGenerateImages` (endpoint CŨ) — `requests[].imageInputs = [{"imageInputType":"IMAGE_INPUT_TYPE_REFERENCE","name":"<uuid bước 1>"}]`.

**Verify end-to-end THẬT:** cả bước investigation (qua DOM picker thật) lẫn `tests/_test_imageToImage.py` (qua API thuần) đều chạy thành công — HTTP 200 cả 2 bước, ảnh kết quả sinh ra thật (CDN `flow-content.google` hợp lệ). `py_compile`/`pyflakes` sạch. Script investigation + file capture JSON đã xoá sau khi dùng xong (kết quả đã ghi lại đầy đủ trong `FLOW_API_CAPTURE.md`).

**Chưa test:** multi-image reference (≥2 ảnh/lần generate) — cấu trúc gợi ý rõ là mảng lặp lại, nhưng chưa verify trực tiếp.

---

### 2026-08-12 — `tests/utils/flow_session.py`: đổi action reCAPTCHA sang `IMAGE_GENERATION` (CHỈ trong test, không đụng production)

Theo yêu cầu user "đổi action sang IMAGE_GENERATION cho gọi test api tạo ảnh". `fetch_recaptcha()`'s `RECAPTCHA_FETCH_JS` (dùng bởi `tests/_test_textToImage.py`) gọi `window.grecaptcha.enterprise.execute(siteKey, {action: '...'})` — đổi giá trị `action` từ `'flow_generate'` → `'IMAGE_GENERATION'`.

**Phạm vi CHỦ Ý chỉ giới hạn ở test** — production (`server/worker.py::_get_fresh_recaptcha()`) đang dùng action KHÁC hẳn cả 2 giá trị trên (`'generate_image'`) — hỏi lại user qua AskUserQuestion có đồng bộ luôn production không, user chọn "Không, chỉ đổi trong test trước" (tránh đụng task thật đang chạy khi chưa xác nhận giá trị mới đúng). Vậy hiện tại 3 nơi trong codebase dùng 3 giá trị action KHÁC NHAU cho CÙNG 1 site key `LABS_RECAPTCHA_SITE_KEY` — nếu sau khi test xác nhận `'IMAGE_GENERATION'` đúng/tốt hơn, cần quay lại đồng bộ `server/worker.py` riêng.

`py_compile` sạch. **CHƯA chạy được test thật** (môi trường này không có Chrome/browser) — cần user tự chạy `python tests/_test_textToImage.py` để xác nhận.

### 2026-08-08 — FIX THẬT: worker vẫn "đá sang Flow" dù đăng nhập Google THẤT BẠI (không check giá trị trả về của `_ensure_google_login()`)

**User báo:** "sau khi chạy lại client_tool/ sao cứ vào account có input ko chịu mà cứ đá sang flow mặc dù chưa login".

**Điều tra:** kiểm tra DB thật (`sf.pm.list()`) — xác nhận `account_password` RỖNG cho TẤT CẢ profile (chưa ai kịp điền qua GUI). Test PATCH round-trip trực tiếp xác nhận backend lưu đúng (không phải bug persistence). Vậy kịch bản thật: `_ensure_google_login()` vào accounts.google.com, thấy ô nhập email (chưa đăng nhập), thấy KHÔNG có mật khẩu đã lưu → log lỗi rõ ràng + trả `False` — ĐÚNG như thiết kế. Nhưng **caller không hề kiểm tra giá trị trả về này**.

**Root cause:** `_ensure_flow_page()`/`_ensure_flow_project()` có sẵn 1 đoạn "recovery" TỪ TRƯỚC (§ "Fix Create with Google Flow", 2026-08-10) — xử lý trường hợp bấm nút "Create with Google Flow" bị lệch, tự `driver.get(url)` LẠI nếu `current_url` không chứa `/project/`. Đoạn recovery này KHÔNG PHÂN BIỆT ĐƯỢC 2 tình huống rất khác nhau: (a) vừa bấm "Create with Google Flow" xong bị điều hướng lệch (trường hợp gốc nó được viết ra để xử lý), và (b) **đang đứng ở `accounts.google.com` vì đăng nhập thất bại** (trường hợp mới, chưa tồn tại lúc viết đoạn code đó) — cả 2 đều có đặc điểm chung "`current_url` không chứa `/project/`", nên code cũ NHẦM (b) thành (a) và cứ `driver.get(url)` lại bất kể đã đăng nhập được hay chưa — khiến worker "đá sang flow" dù rõ ràng vẫn đang đứng ở trang đăng nhập.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_ensure_flow_page()`/`_ensure_flow_project()` — thêm `if not self._ensure_google_login(url): ...; return` NGAY SAU lời gọi, return SỚM nếu đăng nhập thất bại — bỏ qua HẲN đoạn "recovery" phía sau (vốn chỉ nên chạy khi ĐÃ đăng nhập xong). `_run_task_gemini()`/`_run_task_gemini_video()` — thêm `raise RuntimeError(...)` rõ ràng nếu đăng nhập thất bại (trước đây im lặng đi tiếp, lỗi thật sự xảy ra ở bước SAU đó với thông báo khó hiểu — vd "không tìm thấy mục Tạo video" — thay vì lỗi ĐÚNG NGUYÊN NHÂN). `_run_gemini_loop()`/`run()`'s setup `gemini_video` — log "ok sẵn sàng nhận task" đổi thành CÓ ĐIỀU KIỆN (chỉ log ok nếu đăng nhập thành công; log lỗi rõ ràng nếu không, dù worker vẫn tiếp tục chạy — mỗi task sau đó sẽ tự báo lỗi đúng nhờ 2 fix trên). |

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT tái hiện ĐÚNG kịch bản user báo (Chrome mới hoàn toàn, profile giả `account_email` có nhưng `account_password` RỖNG — khớp chính xác trạng thái DB thật hiện tại) — gọi `_ensure_flow_page()` — xác nhận: vào accounts.google.com → thấy ô email (chưa đăng nhập) → log lỗi rõ "chưa có mật khẩu lưu sẵn" → **DỪNG LẠI đúng ở accounts.google.com, KHÔNG còn bị "đá sang" labs.google** (trước fix: sẽ nhảy sang labs.google dù chưa đăng nhập). **Cần restart `main.py` để áp dụng.**

---
---

### 2026-08-08 — FIX THẬT: `_ensure_google_login()` dùng SAI selector — verify bằng DOM thật của huavantien84@gmail.com + đối chiếu `extensions/panel/panel.js`

**User báo:** "đang check login accont goole chưa chính xác hãy tự mở profile vào account đăng nhập trực tiếp lấy thì thông tin user huavantien84 để lấy đúng input, ngoài ra: `D:\source\python\ToolSub\extensions` cũng có có chức năng login như vậy hãy tham khảo đọc input trong đó".

**Tham khảo `extensions/panel/panel.js`** (tính năng "Relogin Gmail", PROVEN chạy thật trong production — khác cơ chế: extension dùng `chrome.scripting.executeScript`, ở đây vẫn Selenium+CDP) — phát hiện ngay bản đầu của `_ensure_google_login()` (viết dựa trên "hiểu biết chung" về Google Identity Platform, CHƯA từng đối chiếu DOM thật) đi theo hướng SAI ở nút "Next": extension KHÔNG hề dùng id `#identifierNext`/`#passwordNext` — chỉ so khớp theo TEXT với danh sách từ khoá đa ngôn ngữ (`'tiếp theo'`,`'next'`,`'đăng nhập'`,`'sign in'`,`'continue'`).

**Verify TRỰC TIẾP bằng Chrome MỚI HOÀN TOÀN** (temp profile riêng — **KHÔNG đụng `profile_dir` thật của huavantien84**, tránh rủi ro cho session production đang hoạt động — chỉ gõ **email thật** `huavantien84@gmail.com` để trang load đúng ngữ cảnh tài khoản đó, **KHÔNG BAO GIỜ gõ/đoán mật khẩu**) — dump DOM THẬT Google đang phục vụ, phát hiện **2 bug thật** trong bản đầu:
1. Ô nhập email THẬT có `id="identifierId"` `name="identifier"` nhưng **`type="text"`** — KHÔNG PHẢI `type="email"` như bản đầu đoán. Selector cũ `input[type="email"]` KHÔNG BAO GIỜ khớp được — đây chính là cái user gọi "chưa chính xác".
2. Nút "Next" (`jsname="LgbsSe"`) **hoàn toàn không có id nào cả** (`id=""` rỗng) — `#identifierNext`/`#passwordNext` không tồn tại trong DOM thật hiện tại (có thể đúng ở phiên bản UI cũ của Google, giờ đã đổi).
3. (Phát hiện thêm, không phải lỗi nhưng cần phòng) — bước email CŨNG có sẵn 1 input ẩn `name="hiddenPassword" type="password"` (decoy chống autofill) — nếu không lọc theo visibility, selector tìm ô mật khẩu có thể khớp nhầm field ẩn này.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_click_google_next()` viết lại HOÀN TOÀN — bỏ hẳn nhánh tìm theo id (`#identifierNext`/`#passwordNext`), CHỈ so khớp theo TEXT (hằng số mới `_GOOGLE_NEXT_KEYWORDS`, cùng danh sách từ khoá với `extensions/panel/panel.js`) trên mọi `button` đang hiển thị. `_ensure_google_login()`'s selector email đổi thành chain `#identifierId, input[name="identifier"], input[type="email"]` (ưu tiên 2 cái đầu — đã verify khớp DOM thật; `type="email"` chỉ còn fallback cuối). Selector password thêm lọc `getClientRects().length>0` (visible) + `input[name="Passwd"]` làm tín hiệu phụ, tránh khớp nhầm field ẩn `hiddenPassword`. |

**Verify:** `py_compile`/`pyflakes` sạch. Test end-to-end THẬT (Chrome mới hoàn toàn, không phải profile production) dùng ĐÚNG các hàm production (`_click_google_next()`, 2 selector đã sửa) trên trang đăng nhập Google THẬT của `huavantien84@gmail.com`: email tìm đúng qua `#identifierId` → gõ đúng qua CDP `Input.insertText` → `_click_google_next()` tìm+click đúng nút Next → URL chuyển đúng sang bước password (`challenge/pwd`) → tìm đúng ô password thật (visible, `name="Passwd"`), không khớp nhầm field ẩn. **Dừng lại đúng ở bước này** (không gõ/đoán mật khẩu, không submit) — đủ để xác nhận CẢ 2 bug đã fix đúng, không cần biết mật khẩu thật. **Cần restart `main.py` để áp dụng** (code không hot-reload).

---
---

### 2026-08-08 — ĐỔI HƯỚNG `_ensure_google_login()`: LUÔN chủ động vào accounts.google.com TRƯỚC (thay vì chỉ phản ứng sau khi bị redirect)

**User yêu cầu tiếp theo** (cùng ngày, sau khi đã thêm field Mật khẩu + `_ensure_google_login()` phiên bản đầu): "giờ fix lại tất cả khi khởi động phải vào: https://accounts.google.com/ trước nếu không có input login nghĩa là đã có login thì vào url task cần thiết, còn nếu có input thì login tài khoản vào".

**Khác biệt với bản đầu:** bản đầu chỉ kiểm tra `current_url` SAU KHI đã `driver.get(target_url)` — chỉ hành động nếu tình cờ đúng lúc đó Google ĐÃ redirect sang `accounts.google.com`. Bản mới đảo ngược thứ tự: LUÔN `driver.get('https://accounts.google.com/')` trước tiên, kiểm tra `input[type="email"]` có mặt hay không (tín hiệu đáng tin cậy nhất cho trạng thái đăng nhập, không phụ thuộc trang đích xử lý "chưa đăng nhập" ra sao), rồi mới quyết định vào thẳng `target_url` (đã đăng nhập) hay tự đăng nhập trước (chưa đăng nhập).

| File | Thay đổi |
|------|---------|
| `server/worker.py` | Viết lại toàn bộ `_ensure_google_login(target_url)` — giờ là điểm điều hướng DUY NHẤT (caller không tự `driver.get(target_url)` nữa, hàm tự lo từ accounts.google.com → (đăng nhập nếu cần) → target_url). Cập nhật 6 call site (`_ensure_flow_page`, `_ensure_flow_project`, `_run_task_gemini_video`, `_run_task_gemini`, `_run_gemini_loop`, `run()`'s setup `gemini_video`) — bỏ các dòng `driver.get(...)+_sleep(...)` cũ đứng trước, chỉ còn gọi `_ensure_google_login(target)`. **Bỏ hẳn 2 lời gọi ở ChatGPT** (`_run_task_chatgpt`/`_run_chatgpt_loop`) — bản đầu gọi "best-effort" ở đây nhưng SAI sau khi đổi hướng: hàm giờ LUÔN detour qua accounts.google.com bất kể cần hay không, trong khi ChatGPT dùng hệ đăng nhập RIÊNG của OpenAI, detour này chỉ tốn thời gian vô ích. |

**Verify:** `py_compile`/`pyflakes` sạch. Test THẬT trên Chrome của huavantien84 (Stop worker thật + tạm `enabled=0`, mở Chrome riêng bằng `_make_driver()`, session Google đã login sẵn) — xác nhận đúng thứ tự: vào accounts.google.com trước → phát hiện đã đăng nhập (không có ô email) → tự vào project URL → `current_url` cuối cùng đúng, không còn kẹt ở accounts.google.com. Đã khôi phục profile về trạng thái chạy bình thường sau test. **CHƯA verify nhánh "chưa đăng nhập, tự nhập email/password" thật trên browser** — không có kịch bản an toàn để test (cần wipe 1 profile thật đang hoạt động). **Cần restart `main.py` để áp dụng** (code không hot-reload).

---
---

### 2026-08-08 — Tự đăng nhập lại Google sau khi xoá cache profile (field Mật khẩu mới + `_ensure_google_login()`)

**User yêu cầu:** "trong profile thêm input password: sau khi xóa cache lần chạy sau phải login: https://accounts.google.com/ dùng email/pass để đăng nhập mail xong mới về lại task hiện tại để chạy tiếp".

**Bối cảnh:** tính năng "Làm mới profile — xóa sạch" (§11.29) xoá TOÀN BỘ nội dung `profile_dir` kể cả session Google đã đăng nhập. Trước đây, lần chạy kế tiếp Chrome bị Google tự redirect sang `accounts.google.com` và worker kẹt vô thời hạn ở đó (không tìm thấy UI mong đợi trên labs.google/gemini.google.com/chatgpt.com) — cần user tự mở "Login browser" đăng nhập tay rồi mới chạy lại được.

| File | Thay đổi |
|------|---------|
| `gui/profile_dialog.py` | Field mới "Mật khẩu" (`self._password`, `QLineEdit.EchoMode.Password` — che ký tự trên màn hình) cạnh Email, áp dụng cho MỌI loại profile (không riêng nhóm nào trong `_type_fields` — mọi worker_mode đều dùng chung 1 Chrome profile_dir có thể bị "Làm mới" xoá sạch). `get_data()` gửi `account_password` (KHÔNG `.strip()` — tránh cắt nhầm khoảng trắng nếu mật khẩu thật có). |
| `server/managers.py` | `pm.create()` thêm tham số `password=''`, gửi `account_password` lên backend. **Bug thật bắt được lúc sửa** (không liên quan mật khẩu, phát hiện tình cờ khi rà lại validation): `pm.create()`'s whitelist `worker_mode` THIẾU `'gemini_video'` từ lúc thêm loại profile đó (2026-08-07) — MỌI profile tạo mới qua GUI chọn "🎬 Gemini — Tạo Video" bị ÂM THẦM reset về `'api'` ngay tại đây trước khi gửi lên backend (backend's whitelist ĐÃ đúng từ trước — chỉ riêng bản sao whitelist ở client_tool bị sót). Đã thêm `'gemini_video'` vào. `pm.update()`'s `allowed` set thêm `'account_password'`. |
| `server/routes.py` | `create_profile()` (route `/api/selenium/profiles`) forward thêm `password=d.get('account_password','')` xuống `pm.create()` — trước đây field này bị bỏ sót hoàn toàn ở tầng route dù `pm.create()` đã có tham số, nên POST tạo mới KHÔNG BAO GIỜ gửi được password dù đã sửa `managers.py`. |
| `server/worker.py` | Hàm mới `_ensure_google_login(target_url)` — no-op RẺ (1 lần đọc `current_url`) nếu KHÔNG đang ở `accounts.google.com`; nếu có, tự nhập `account_email`/`account_password` đã lưu (selector `input[type="email"]`/`input[type="password"]`, nút Next qua `#identifierNext`/`#passwordNext` + fallback theo text "Next"/"Tiếp theo" — các ID này ổn định nhiều năm qua trên Google Identity Platform), chờ tối đa 30s rời khỏi `accounts.google.com`, rồi **tự `driver.get(target_url)` quay lại đúng trang đang xử lý** — khớp yêu cầu "xong mới về lại task hiện tại để chạy tiếp". Helper `_click_google_next(step)` dùng chung cho cả 2 bước (email/password). Trả `False` (không raise) nếu thiếu email/password đã lưu, hoặc Google yêu cầu bước xác minh khác (2FA/captcha/"xác nhận đây là bạn" — KHÔNG tự động hoá được, log lỗi rõ ràng hướng dẫn dùng "Mở login browser" thủ công) — caller (các hàm gọi `_ensure_google_login`) hiện đều KHÔNG kiểm tra giá trị trả về (best-effort, giống triết lý các bước DOM khác trong file — lỗi đăng nhập sẽ tự lộ ra ở bước TIẾP THEO khi không tìm thấy UI mong đợi, log đã đủ rõ để chẩn đoán). Toàn bộ hành động đều `self._log(...)` vào `profile_logs` (tiền tố `[google-login]`) theo đúng quy ước ghi log của cả file. |

**Wired vào 8 điểm navigate là ĐIỂM VÀO chính (đầu 1 phiên chạy/1 task, nơi dễ gặp redirect login nhất) — ngay sau `driver.get(...)` + `_sleep(...)`:**
- `_ensure_flow_page()` — VEO3 dom/api, điểm vào chính của MỌI task Flow.
- `_ensure_flow_project()` — nhánh API mode dùng riêng.
- `_run_task_gemini_video()` — mỗi task gemini_video tự vào chat mới.
- `_run_task_gemini()` — mỗi task gemini (pipeline kịch bản/ảnh) tự vào chat mới.
- `_run_gemini_loop()` — khởi động worker gemini (1-tab tuần tự).
- `_run_task_chatgpt()` + `_run_chatgpt_loop()` — tương tự phía ChatGPT (best-effort — ChatGPT có hệ thống đăng nhập RIÊNG không redirect Google trừ khi tài khoản dùng "Continue with Google", nên phần lớn trường hợp đây là no-op).
- `run()`'s nhánh setup `gemini_video` — khởi động worker gemini_video.

**⚠️ CHƯA áp dụng cho `_gemini_slot_step()`** (round-robin nhiều tab, `gemini_max_concurrent_tabs>1`) — đây là state machine KHÔNG-BLOCKING (mỗi lần gọi chỉ làm 1 bước nhỏ rồi return ngay để vòng lặp round-robin xoay qua tab khác), trong khi `_ensure_google_login()` có thể block tới ~30s+ (gõ email/password, chờ chuyển trang) — chèn vào đây sẽ làm TOÀN BỘ round-robin bị treo theo, ảnh hưởng cả các tab/slot khác đang chờ tới lượt. Cần thiết kế lại thành các bước nhỏ không-blocking riêng (tương tự pattern `_gemini_attach_file_kickoff`/`_gemini_attach_file_poll`) nếu muốn hỗ trợ đầy đủ — CHƯA làm trong lần này.

**Bảo mật — LƯU PLAINTEXT:** dự án không có hạ tầng mã hoá field-level nào (cùng quy ước với mọi field khác của `selenium_profiles`). Route `/api/worker_profiles*` gate `_require_tool_media` (chỉ user group 'Tool Media') nhưng CHƯA giới hạn theo owner riêng cho field này — mọi thành viên group đọc được password của MỌI profile qua GET (không riêng profile mình sở hữu). Chỉ nên dùng cho tài khoản Google TẠO RIÊNG cho automation, KHÔNG dùng tài khoản cá nhân.

**Verify:** `py_compile`/`pyflakes` sạch cả `gui/profile_dialog.py`/`server/managers.py`/`server/routes.py`/`server/worker.py`. Migration đã chạy trực tiếp trên DB production thật (`_ensure_worker_profile_password()`), xác nhận cột `account_password VARCHAR(255)` tồn tại đúng. **⚠️ CHƯA verify được luồng đăng nhập THẬT trên browser** (không có sẵn kịch bản an toàn để test — cần 1 profile đã "Làm mới" (xoá sạch) + email/mật khẩu thật hợp lệ để quan sát toàn bộ luồng, rủi ro nếu tài khoản test có 2FA/bước xác minh sẽ dừng lại đúng như thiết kế nhưng chưa xác nhận trực tiếp). **Backend (ToolSub) đang chạy live cần RESTART để nạp whitelist mới** — nếu không, PATCH/POST `account_password` từ client_tool bị bỏ qua lặng lẽ (route cũ trong RAM không biết field này). **`main.py` (client_tool GUI) cũng cần RESTART** để nạp `_ensure_google_login()` — code không hot-reload.

---

### 2026-08-10 — `_ensure_google_login()`: đổi entry point từ accounts.google.com sang gmail.com

Theo yêu cầu user "viết lại luồng check bằng cách vào https://gmail.com/ ko vào accounts.google.com nữa, nếu gmail.com đá sang https://mail.google.com/ thì đã login, còn đá sang domain: https://workspace.google.com/ thì click vào button... để vào trang login".

`_ensure_google_login()` (`server/worker.py`) — bước kiểm tra trạng thái đăng nhập Google trước khi vào Flow/Gemini/ChatGPT — đổi từ `driver.get('https://accounts.google.com/')` sang `driver.get('https://gmail.com/')` rồi đọc URL redirect:
- Redirect → `mail.google.com` → ĐÃ đăng nhập, vào thẳng `target_url`.
- Redirect → `workspace.google.com` (trang marketing Gmail cho khách chưa đăng nhập) → CHƯA đăng nhập — tìm nút "Sign in" (khớp qua `textContent`, danh sách biến thể `googleSignIn` trong `i18n_texts.json`, cùng pattern `newProject`/`createWithFlow`) và điều hướng tới `accounts.google.com/AccountChooser/signinchooser?continue=...`.
- URL không khớp cả 2 case (hiếm) → best-effort đi thẳng `target_url`, không chặn cứng.

**Đọc `href` thay vì click thật:** nút "Sign in" trong HTML thật (user gửi) là `<a target="_blank" href="https://accounts.google.com/AccountChooser/signinchooser?...">` — click thật sẽ MỞ TAB MỚI, phức tạp hoá việc theo dõi/switch tab không cần thiết. Thay vào đó đọc thẳng `href` của thẻ `<a>` qua JS rồi `driver.get(href)` trên CÙNG tab hiện tại — cùng đích đến (`accounts.google.com/AccountChooser/signinchooser`), không cần quản lý nhiều tab.

Toàn bộ phần SAU khi vào được trang đăng nhập thật (tìm ô email, nhập, bấm "Tiếp theo", tìm ô mật khẩu, nhập, bấm "Tiếp theo", chờ rời khỏi `accounts.google.com`) GIỮ NGUYÊN 100% — chỉ đổi CÁCH VÀO trang đăng nhập, không đổi DOM interaction logic đã verify trước đó.

Verify: `py_compile`/`pyflakes` sạch (`server/worker.py`, `server/i18n_texts.py`); `i18n_texts.json` valid JSON; JS snippet tìm nút "Sign in" parse hợp lệ qua `node -e "new Function(js)"`. **CHƯA verify trên Chrome/gmail.com thật** — cần user tự chạy 1 kịch bản thật (profile đã "Làm mới — xóa sạch" + email/mật khẩu hợp lệ) để xác nhận.

---

### 2026-08-12 — FIX THẬT: mode video DOM "chớp sáng như click chọn model" nhưng không đổi đúng model

User báo: "hiện tại mode video DOM thế nào mà sao cứ thấy chớp sáng như click vào chọn model nhưng lại ko chọn lại model phù hợp".

Root cause: `_dom_select_model_verified()`'s `find_drop_btn_js` (`server/worker.py`) tìm nút trigger dropdown model (icon Material `arrow_drop_down`) qua `document.querySelectorAll('button')` — KHÔNG SCOPE vào popup cấu hình, khác hẳn `find_btn_in_popup()` (dùng cho ratio/count/sub-tab) vốn đã scope đúng từ đầu. Trang labs.google thật có NHIỀU nút dropdown khác dùng CHUNG icon này (project picker, account menu...) — `.find()` luôn trả về nút ĐẦU TIÊN theo thứ tự DOM trên toàn trang, không chắc là nút model bên trong popup. Nếu 1 nút khác đứng trước trong DOM, CDP click "trúng" nút đó — 1 click THẬT sự xảy ra ở đâu đó trên trang (đúng là "chớp sáng" user thấy) nhưng KHÔNG PHẢI dropdown model, nên model không bao giờ đổi — và vì bước "verify" cũng dùng ĐÚNG hàm tìm-nút bị lệch này để đọc lại label, kết quả verify có thể trông "khớp giả" (đọc nhầm label nút khác) hoặc luôn báo lệch, tuỳ tình huống — cả 2 đều khớp đúng triệu chứng user mô tả.

Fix: `_dom_select_model_verified()` nhận thêm tham số `popup` (element popup cấu hình, lấy từ `get_popup()` ở `_dom_configure()` — CÙNG element `find_btn_in_popup()` đã dùng cho ratio/count) — SCOPE tìm kiếm vào ĐÚNG popup này thay vì toàn `document`. Dùng `window.__modelPopupRoot` (KHÔNG dùng `arguments[N]` trực tiếp) vì đoạn JS này bị nhúng qua NHIỀU lớp `(function(){...})()` lồng nhau (trong `read_label_js` và khối "applied") — mỗi IIFE gọi không truyền tham số nên `arguments[0]` bên trong luôn `undefined`, không "xuyên" được qua các lớp lồng — biến `window` toàn cục mới thấy được ở MỌI lớp lồng, cùng kỹ thuật `window.__selUpload` đã dùng ở `_dom_upload_images()` trong chính file này. `MODEL_MATCH_JS` (tìm MENU ITEM sau khi dropdown đã mở) CỐ Ý giữ nguyên KHÔNG scope — menu items của Material-style React app thường render qua portal gắn vào `document.body`, KHÔNG nằm trong DOM subtree của popup, scope nhầm chỗ này sẽ làm hỏng phần đang hoạt động đúng.

`_dom_configure()` truyền `get_popup()` vào lời gọi `_dom_select_model_verified(model, popup=get_popup())`. Thêm `finally` dọn `window.__modelPopupRoot = null` sau khi hàm kết thúc (thành công hay raise) — tránh lần gọi sau (task khác) vô tình đọc lại popup CŨ đã đóng.

Verify: `py_compile`/`pyflakes` sạch; toàn bộ JS snippet (`find_drop_btn_js`/`read_label_js`/khối "applied", cả 3 lớp lồng nhau) parse hợp lệ qua `node -e "new Function(js)"`. **CHƯA verify trên Chrome/labs.google thật** (môi trường này không có jsdom/browser để test scoping thực tế) — cần user tự chạy 1 task video DOM để xác nhận model được chọn đúng, không còn "chớp sáng" vào nhầm chỗ.

---

### 2026-08-10 — FIX THẬT: chạy task gemini chỉ mở profile mà không vào được gemini.google.com

User báo: "check lại sao tôi chạy task gemini thì chỉ mở profile mà ko vào gemini".

Root cause: `_ensure_google_login()` (đổi sang luồng gmail.com cùng ngày, xem 2 entry ngay dưới) sau `driver.get('https://gmail.com/')` dùng `self._sleep(3)` CỐ ĐỊNH rồi đọc `current_url` NGAY LẬP TỨC để phân loại đã/chưa đăng nhập. Redirect + bootstrap app Gmail đầy đủ có thể mất LÂU HƠN 3 giây thật (mạng chậm, máy tải nặng, cold start profile mới) — đọc quá sớm bắt được URL TRUNG GIAN (chưa kịp redirect xong tới `mail.google.com`/`workspace.google.com`), khiến hàm phân loại SAI trạng thái đăng nhập rồi rơi vào 1 trong các nhánh xử lý sai (kể cả nhánh "chưa đăng nhập" dù tài khoản THỰC RA đã đăng nhập sẵn — trường hợp phổ biến nhất với profile automation dùng lại nhiều lần) — kết quả: Chrome mở nhưng không (hoặc chậm trễ/sai hướng) vào được `gemini.google.com`.

Fix: đổi `self._sleep(3)` + đọc 1 lần thành VÒNG LẶP POLL (tối đa 15s, mỗi 0.5s đọc lại `current_url`) tới khi khớp `mail.google.com` HOẶC `workspace.google.com` — cùng pattern `_wait_js()`/`_wait_for_project_url()` đã dùng khắp file này cho MỌI chỗ chờ điều hướng/DOM khác (không đoán 1 con số cố định). Nếu hết 15s vẫn không khớp (hiếm), log rõ URL thực tế đang đứng để dễ chẩn đoán, rồi tiếp tục best-effort với giá trị đó (giữ nguyên hành vi fallback đã có).

Verify: `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật** — cần user tự chạy lại 1 task gemini để xác nhận vào được `gemini.google.com` bình thường.

---

### 2026-08-10 — Tự "Làm mới profile" khi rơi vào trạng thái ngủ do lỗi nhiều

Theo yêu cầu user "ok rồi luồng mới nếu rơi vào trạng thái ngủ do lỗi nhiều, thì sẽ tự 'làm mới profile' đó".

Hàm mới `_clear_profile_if_sleeping(profile_row)` (`server/worker.py`, đặt cạnh `_handle_task_error()`/`_reset_flow_project()`) — gọi trong `finally` của CẢ 4 vòng lặp worker (`run()` cho VEO3/gemini_video, `_run_gemini_loop()`, `_run_gemini_loop_concurrent()`, `_run_chatgpt_loop()`) NGAY SAU `self.driver.quit()` — tại thời điểm này Chrome đã đóng hẳn (`driver.quit()` blocking), an toàn để đụng filesystem `profile_dir`. Đọc `profile_row` (đã fetch sẵn từ `pm.get()` trong cùng `finally`, không query lại): nếu `status == 'sleeping'` (do `_record_error()`/`_handle_task_error()` — quá nhiều lỗi trong khoảng thời gian ngắn hoặc liên tiếp — vừa set NGAY TRƯỚC KHI thread thoát), coi là dấu hiệu khả năng cao session/cache Chrome đã hỏng theo cách nào đó → tự gọi `pm.clear_browser_data()` (xoá SẠCH toàn bộ `profile_dir`, xem §11.29 "Làm mới profile — về trắng") trước khi dispatcher đánh thức lại — KHÔNG cần user tự vào GUI bấm tay. Gọi THẲNG hàm cục bộ (không qua HTTP route `/clear_data`) vì worker tự biết chắc Chrome vừa đóng bởi chính `driver.quit()` ngay phía trên, không cần lại guard qua `_workers`/`_login_drivers` như route đó.

Sau khi làm mới, lần chạy kế tiếp sẽ tự đăng nhập lại qua `_ensure_google_login()` (nếu profile có sẵn `account_email`/`account_password`, xem §11.31) — kết hợp 3 tính năng đã xây trong ngày (Làm mới profile / auto-login / auto-refresh-on-sleep) thành 1 vòng tự phục hồi hoàn chỉnh không cần can thiệp thủ công.

**Fix kèm theo (2 trong 4 vòng lặp trước đây có bug tiềm ẩn):** `_run_gemini_loop()`/`_run_gemini_loop_concurrent()`/`_run_chatgpt_loop()` TRƯỚC ĐÂY unconditionally ghi đè status → `'offline'` trong `finally`, KHÔNG kiểm tra 'sleeping' trước (khác `run()`'s VEO3 branch vốn đã có check này từ trước) — nghĩa là status 'sleeping' do `_handle_task_error()` set cho các mode này bị XOÁ NGAY LẬP TỨC. Giờ cả 4 vòng lặp dùng CHUNG `_clear_profile_if_sleeping()`, đồng nhất hành vi (không ghi đè 'sleeping' + tự làm mới) trên MỌI worker_mode.

Verify: unit test cô lập `_clear_profile_if_sleeping()` (mock `pm`) — 4 kịch bản: sleeping→gọi đúng `clear_browser_data(profile_id)` trả True; không sleeping→không gọi, trả False; `profile_row=None`→không gọi, trả False; sleeping nhưng `clear_browser_data()` raise lỗi→vẫn trả True, KHÔNG propagate exception (an toàn trong `finally` đang sắp thoát). `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật** (cần 1 profile thật rơi vào sleeping do lỗi nhiều để xác nhận toàn bộ chuỗi).

---

### 2026-08-10 — `_ensure_google_login()`: click thật nút "Sign in" thay vì đọc href — tránh Google báo "trình duyệt không hợp lệ"

Theo yêu cầu tiếp theo của user "không đi thẳng vào accounts.google.com mà phải click button đăng nhập từ workspace.google.com tránh bị là trình duyệt ko hợp lệ" — ngay sau entry ngay dưới (đổi sang gmail.com/workspace.google.com).

Bản trước đọc THẲNG `href` của thẻ `<a>` "Sign in" rồi `driver.get(href)` — bỏ qua thao tác click thật. Google's Identity Platform có thể coi điều hướng kiểu này là "không tự nhiên" (thiếu ngữ cảnh click/referrer của 1 cú click thật từ trang trước) và trả lỗi "This browser or app may not be secure". Fix: dùng `_cdp_click_el()` (CDP `mouseMoved`+`mousePressed`+`mouseReleased` thật — ĐÃ chứng minh hoạt động ổn định trên labs.google/gemini ở nhiều nơi khác trong file này) click THẬT vào nút "Sign in".

Nút mang `target="_blank"` (xác nhận qua HTML thật) nên click thật sẽ MỞ TAB MỚI thay vì navigate tab hiện tại — thêm logic theo dõi `window_handles` để bắt tab mới xuất hiện (poll 8s), `switch_to.window()` sang tab đó, rồi ĐÓNG LUÔN tab `workspace.google.com` cũ (tránh để lại tab thừa gây nhiễu các đoạn code khác trong worker vốn giả định chỉ có 1 tab đang hoạt động). Toàn bộ phần SAU (tìm ô email/mật khẩu, bấm "Tiếp theo"...) chạy trong tab MỚI, không đổi logic.

Verify: `py_compile`/`pyflakes` sạch; JS snippet tìm nút "Sign in" (giờ trả về element thay vì href string) parse hợp lệ qua `node -e "new Function(js)"`. **CHƯA verify trên Chrome/gmail.com thật.**

---

### 2026-08-10 — ProfilesPage: gộp cột Trạng thái/Tokens/Lỗi thành 1 cột badge, bỏ cột Hôm nay

Theo yêu cầu user "cột trạng thái / token/ lỗi cũng làm dang badge bỏ cột hôm nay" — tiếp nối redesign cột Mode/Hành động cùng ngày. Trước đây Trạng thái/Tokens/Lỗi mỗi thứ đứng riêng 1 cột (đã là Badge sẵn, nhưng chiếm 3 cột × 315px tổng cộng). Gộp cả 3 vào ĐÚNG 1 cột "Trạng thái" (`gui/pages/profiles_page.py`), layout DỌC 2 dòng mirror cột Mode: dòng 1 = badge trạng thái CHÍNH (logic/màu/tooltip giữ NGUYÊN 100% — Running/Login open/Waiting/Sleeping+countdown/Đã tắt/khác); dòng 2 = hàng ngang 2 badge nhỏ Token + Lỗi (VẪN là `Badge` widget, không rút gọn xuống text — chỉ gom vị trí, không đổi bản chất hiển thị). Cột "Hôm nay" (số task hoàn thành/lỗi trong ngày, `tasks_done_today`/`tasks_error_today`) bỏ hẳn khỏi bảng — dữ liệu không mất, chuyển thành tooltip trên badge Lỗi (`"Hôm nay: ✅ N  ❌ M"`, nối thêm sau tooltip lỗi gần nhất nếu có).

Bảng từ 10 cột → 7 cột: `['', 'Tên / Email', 'Mode', 'Trạng thái', 'Task hiện tại', 'Port', 'Hành động']`. Renumber toàn bộ `setCellWidget`/`setItem` từ col 3 trở đi cho khớp (Task hiện tại 5→4, Port 8→5, Hành động 9→6). Tiện thể dọn `_TASK_MODE_LABEL` — dict mồ côi sót lại từ bản nháp redesign cột Mode trước đó, không còn được dùng ở đâu.

Verify: smoke test headless PyQt6 thật với 4 profile giả (idle/running+lỗi/sleeping+countdown/đã tắt) → populate không lỗi, đúng 7 cột, header đúng thứ tự, cell "Trạng thái" dựng được ở mọi hàng, cột Port đọc đúng vị trí mới. `py_compile`/`pyflakes` sạch.

---

### 2026-08-10 — "Xóa cache" mở rộng thành "Làm mới profile (về trắng)" — xoá SẠCH toàn bộ profile_dir

Theo yêu cầu tiếp theo của user "Còn gì cần xóa thì xóa sạch coi như 1 profile trắng" — nối tiếp 2 entry ngay dưới (bắt đầu từ "xóa cache/cookie/history", rồi fix thiếu `Web Data`). Bỏ hẳn cách tiếp cận CHỌN LỌC từng file/nhóm (`_CLEAR_DATA_PATHS`/`_clear_chrome_browser_data()`) — đã CHỨNG MINH dễ sót (sót đúng `Web Data` lần trước, có thể còn sót thứ khác chưa phát hiện) — đổi hẳn sang XOÁ SẠCH TOÀN BỘ nội dung `profile_dir` (`server/managers.py::_wipe_chrome_profile_dir()`, thay thế 2 hàm cũ) — không chỉ subfolder `Default/` mà cả các file gốc `--user-data-dir` (`Local State` — os_crypt key, DNS cache, browser-level prefs...). Chrome tự tạo lại mọi thứ từ đầu ở lần mở kế tiếp, y hệt lần đầu tiên tạo profile.

`pm.clear_browser_data()` giờ gọi `_wipe_chrome_profile_dir()` — KHÔNG xoá record DB (khác `delete(delete_data=True)`, vốn xoá LUÔN profile khỏi danh sách) — chỉ làm sạch dữ liệu Chrome, giữ nguyên cấu hình name/worker_mode/task_mode/project_url. Route `/clear_data` (`routes.py`) và guard (409 nếu worker chạy/login mở) giữ nguyên không đổi.

GUI: đổi tên action từ "🧹 Xóa cache / cookie / lịch sử" → "🧹 Làm mới profile (xóa sạch — về trắng)" (`_build_actions_menu()`, `profiles_page.py`) — cảnh báo trong `_clear_cache()` viết lại rõ ràng hơn nhiều (mất TOÀN BỘ dữ liệu Chrome: đăng nhập/mật khẩu/bookmark/cache/cookie/lịch sử/mọi cài đặt khác — không chỉ 3 mục cache/cookie/history như trước).

Verify: test lại `_wipe_chrome_profile_dir()` trên thư mục giả lập có CẢ file gốc (`Local State`, `First Run`) LẪN `Default/` (Preferences/Login Data/Web Data/Cache) — xác nhận xoá SẠCH 100% nội dung, `profile_dir` chính nó vẫn còn (rỗng, sẵn sàng cho Chrome tạo lại); smoke test PyQt6 xác nhận menu label đổi đúng. `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật.**

---

### 2026-08-10 — FIX THẬT: "Xóa cache/cookie/lịch sử" không thực sự đăng xuất Google — thiếu `Web Data` (refresh token cấp trình duyệt)

User báo: "đã đóng trình duyệt và xóa cache profile mà vào lại profile vẫn có login sẳn".

Root cause: bản đầu của tính năng "Xóa cache" (`server/managers.py::_CLEAR_DATA_PATHS`, xem entry ngay dưới) chỉ xoá `Cookies`/`Network/Cookies` — session cookie của TỪNG WEBSITE — nhưng CỐ Ý bỏ qua `Web Data` (nhầm tưởng chỉ chứa autofill/search-engine). Chrome thực ra có 2 tầng đăng nhập Google TÁCH BIỆT: (1) session cookie từng site (`Cookies`) và (2) đăng nhập CẤP TRÌNH DUYỆT ("Account consistency" — chip tài khoản góc trên cùng) — refresh token OAuth của tầng (2) lưu trong bảng `token_service` bên trong file `Web Data`, KHÔNG nằm trong `Cookies`. Chrome tự dùng refresh token đó để TỰ SINH LẠI session cookie google.com/labs.google mỗi khi khởi động — đây chính là lý do xoá `Cookies` xong vẫn "có login sẵn" ngay khi mở lại. Khớp đúng hành vi Chrome UI thật: "Clear browsing data" + tick Cookies vẫn hiện ghi chú "You'll remain signed in to your Google Account(s)" — phải vào `chrome://settings/people` bấm "Sign out" mới dứt điểm, không có cách nào qua "Clear browsing data" thường.

Fix: thêm `Web Data`/`Web Data-journal` vào nhóm `cookies` trong `_CLEAR_DATA_PATHS` — xoá NGUYÊN file (đơn giản, nhất quán với cách các mục khác trong danh sách đều xoá nguyên file/thư mục thay vì SQL surgery chọn lọc từng bảng) thay vì chỉ xoá bảng `token_service` bên trong. Chấp nhận mất autofill/search-engine tuỳ biến kèm theo — vô hại vì các profile này CHỈ dùng cho automation, không phải browsing cá nhân; Chrome tự tạo lại file rỗng ở lần mở kế tiếp.

Verify: test lại `_clear_chrome_browser_data()` trên thư mục giả lập CÓ THÊM `Web Data`/`Web Data-journal` (đại diện `token_service`) — xác nhận giờ bị xoá đúng, trong khi `Login Data` (mật khẩu autofill, KHÔNG liên quan đăng nhập Google, hàm khác `token_service` hoàn toàn) vẫn sống sót đúng như thiết kế; dọn sạch thư mục test. `py_compile`/`pyflakes` sạch. **CHƯA verify trên Chrome thật** — cần user tự thử lại tính năng "Xóa cache" sau fix này, xác nhận mở lại profile phải hiện MÀN HÌNH ĐĂNG NHẬP thật (không còn tự động login).

---

### 2026-08-10 — Fix "Create with Google Flow" — màn hình xen giữa khi navigate thẳng tới URL project

Theo yêu cầu user: "nếu vào url dạng project: https://labs.google/fx/vi/tools/flow/project/{uuid} đôi khi bắt phải bấm button ở trên để vào, hãy check nếu có thì click rồi quay lại url project như cũ".

Root cause: khi navigate THẲNG tới URL của 1 project cụ thể (`/project/{uuid}`), Google Flow ĐÔI KHI hiện 1 màn hình xen giữa bắt bấm nút "Create with Google Flow" trước khi thực sự vào được project — URL trên thanh địa chỉ VẪN giữ nguyên `/project/{uuid}` suốt lúc này, nên check `'/project/' not in current` sẵn có ở `_ensure_flow_page()`/`_ensure_flow_project()` (`server/worker.py`) KHÔNG phát hiện được case này — worker tưởng đã vào project rồi nhưng thực ra còn kẹt ở màn hình xen giữa, chưa có Slate CE để gõ prompt.

Fix: hàm mới `_click_create_with_flow_if_present(timeout=6)` (`server/worker.py`) — poll tìm nút theo `textContent` (KHÔNG dùng class — HTML user gửi (`sc-fe61cac2-1 iwEYmY`/`sc-fe61cac2-0 dagixW`) là styled-components hash tự sinh, đổi mỗi lần Google deploy lại UI, cùng lớp bug DOM-detection đã lặp lại nhiều lần trong project), cùng pattern `_click_new_project_button()`. Không phải lúc nào cũng xuất hiện — poll ngắn rồi bỏ qua nếu không thấy, không log warning (bình thường). Gọi ngay sau MỌI lần `driver.get()` tới 1 URL project cụ thể (2 call site: `_ensure_flow_page()`, `_ensure_flow_project()`) — bấm xong xác nhận lại `current_url` còn đúng `/project/`, nếu lỡ điều hướng ra chỗ khác (hiếm) thì `driver.get()` lại đúng URL project ban đầu, đúng yêu cầu "click rồi quay lại url project như cũ".

Text nút thêm vào `i18n_texts.json`/`server/i18n_texts.py::_DEFAULT_I18N_TEXTS` (key `createWithFlow`) theo đúng pattern locale-variant đã dùng cho `newProject` — biến thể tiếng Anh "Create with Google Flow" xác nhận qua HTML thật user gửi, biến thể tiếng Việt "Tạo trong Google Flow" là DỰ ĐOÁN best-effort (chưa xác nhận qua tài khoản VN thật) theo đúng tinh thần phòng ngừa đã áp dụng cho `newProject` trước đây (lịch sử: hardcode chỉ tiếng Anh từng gây miss hẳn trên tài khoản Việt).

Verify: `py_compile`/`pyflakes` sạch (`server/worker.py`, `server/i18n_texts.py`); `i18n_texts.json` valid JSON; `get_i18n_texts()` đọc đúng key mới từ file thật; JS snippet sinh ra parse hợp lệ qua `node -e "new Function(js)"`. **CHƯA verify trên Chrome/labs.google thật** — môi trường này không launch được Chrome/client_tool, cần user tự chạy 1 task thật (hoặc test cô lập kiểu `tests/_test_gemini_video.py`) trỏ vào 1 project URL để xác nhận nút thật sự được phát hiện + click đúng.

---

### 2026-08-10 — ProfilesPage: gọn cột Mode + dropdown "⋮" cho action phụ + tính năng Xóa cache/cookie/lịch sử

Theo yêu cầu user "thiết kế UI cho hợp lý hơn các badge mode dồn cục quá nhỏ không biết đang chạy gì, đưa các action thành dropdown, thêm xóa cache cho profile: cache, cookie, browing history - all time".

**Cột Mode (`gui/pages/profiles_page.py`):** trước đây nhồi 2-3 `Badge` nhỏ liền kề ngang (`task_mode` + `worker_mode` + `x{N}`) trong 130px, chữ dễ bị cắt/khó đọc — đổi sang layout DỌC 2 dòng: dòng 1 = ĐÚNG 1 badge "loại chính" (to, màu rõ, khớp đúng combo "Loại *" trong `ProfileDialog` — `✨ Gemini`/`🤖 ChatGPT`/`🎬 Gemini Video`/`🖼️🎬 VEO3`/`🖼️ VEO3 · Ảnh`/`🎬 VEO3 · Video`), dòng 2 = text phụ nhỏ (không phải badge, chỉ `QLabel` muted) cho chi tiết (`worker_mode`, số tab/concurrent). Cột rộng ra 130→165px.

**Cột Hành động:** giảm từ 4-5 icon-button rời rạc (start/stop, mở/đóng login, xem logs, sửa, xóa) xuống còn 1 nút chính (Start/Stop — hành động dùng thường xuyên nhất, giữ bấm trực tiếp không qua menu) + 1 nút "⋮" mở `QMenu` (`_build_actions_menu()`) gom: Mở/Đóng login browser, Xem logs, Sửa profile, (separator), **Xóa cache/cookie/lịch sử** (mới), (separator), Xóa profile. Cột thu hẹp 140→90px. Thêm QSS cho `QMenu`/`QMenu::item`/`QMenu::separator` (`gui/style.py`) — trước đó chưa có, menu sẽ render theo theme OS mặc định (sáng, lệch tông với UI tối) nếu không style.

**Xóa cache/cookie/lịch sử duyệt web (all time) — tính năng MỚI:**
- Backend: `server/managers.py` — `_CLEAR_DATA_PATHS` (3 nhóm path tương đối trong `{profile_dir}/Default/`, mirror ĐÚNG 3 checkbox "Cached images and files"/"Cookies and other site data"/"Browsing history" của Chrome — Cookies xử lý CẢ vị trí cũ `Cookies` lẫn mới `Network/Cookies` Chrome ~96+), `_clear_chrome_browser_data(profile_dir)` (xoá thẳng file/thư mục trên đĩa — "all time" tự đúng nghĩa vì đây là xoá file, không phải query SQLite theo mốc thời gian), `pm.clear_browser_data(profile_id)` (100% CỤC BỘ, không gọi backend chính, mirror `delete(delete_data=True)` nhưng CHỈ xoá 3 nhóm trên — KHÔNG đụng `Preferences`/`Login Data`/`Bookmarks`/`Extensions`/`Web Data`).
- Route mới `POST /api/selenium/profiles/<id>/clear_data` (`server/routes.py`) — chặn 409 nếu worker đang chạy (`pid in _workers`) hoặc login browser đang mở (`pid in _login_drivers`), mirror guard `_stop_worker()` trước khi đụng filesystem đã có ở `delete_profile()`.
- GUI: menu item "🧹 Xóa cache / cookie / lịch sử" — disabled + tooltip giải thích khi profile đang chạy/login mở; bấm được thì hiện `QMessageBox.question` CẢNH BÁO RÕ "xóa cookie đồng nghĩa đăng xuất khỏi Google, cần Mở login browser đăng nhập lại" trước khi gọi API (hành động không hoàn tác được).

Verify: `py_compile`/`pyflakes` sạch cả 4 file. Smoke test headless PyQt6 (`QT_QPA_PLATFORM=offscreen`) — dựng `ProfilesPage` thật, `_on_data()` với 5 profile giả (đủ mọi `worker_mode`: api/dom/gemini/chatgpt/gemini_video, đủ trạng thái running/login_open/idle/disabled) → populate không lỗi; `_build_actions_menu()` cho từng profile → xác nhận đúng action list + đúng enable/disable theo trạng thái (vd profile đang login_open → "Xóa cache" bị disable, đổi "Mở"→"Đóng login browser"). Test riêng `_clear_chrome_browser_data()` trên thư mục giả lập cấu trúc Chrome profile thật (`Cache/`, `Network/Cookies`, `History`, `Favicons`, `Local Storage/` + các file PHẢI SỐNG SÓT `Preferences`/`Login Data`/`Bookmarks`/`Extensions/`) — xác nhận đúng xoá 5 mục cache/cookie/history, đúng giữ nguyên 4 mục còn lại; dọn sạch thư mục test sau. **CHƯA verify end-to-end qua Flask route thật** (`POST .../clear_data`) — cần user tự bấm thử trên app thật để xác nhận HTTP layer/409 guard hoạt động đúng trong tình huống thật (môi trường này không launch được app GUI đầy đủ).

---

### 2026-08-08 — FIX THẬT: gemini_video báo lỗi dù Gemini vẫn đang tạo video thật (race giữa ack-text và video thật)

**User báo:** "xem lại gemini-video có phản hồi video được tạo nhưng báo về server lỗi".

**Điều tra qua log production thật** (profile `gemini-video`, id=31, `worker_mode='gemini_video'`) — task #4710 và #4711 (CẢ 2 đều có ref ảnh tham chiếu) đều lỗi với ĐÚNG 1 thông báo:
```
Gemini từ chối/không tạo được video: I'm generating your video. This could take a few minutes, so check back to see when your video is ready.

Đang tạo video cho bạn…
Quá trình này có thể mất vài phút.
```
Đây **KHÔNG PHẢI** lời từ chối — đây chính là đoạn ACK ngắn Gemini tự gửi ngay khi BẮT ĐẦU xử lý (dịch: "Tôi đang tạo video cho bạn, quá trình này có thể mất vài phút, hãy quay lại kiểm tra sau"). Bằng chứng quyết định: retry NGAY SAU ĐÓ của task #4710 (submit 18:22:08 → `✔ Video xuất hiện` lúc 18:23:28, ~80s sau — cùng khoảng thời gian y hệt lần trước bị coi là "lỗi") **THÀNH CÔNG** — chứng minh đây là RACE, không phải deterministic failure: có lúc Gemini's "Stop response"/loading-indicator biến mất RẤT SỚM (ngay sau khi ACK NGẮN này render xong) trong khi video THẬT vẫn đang generate NGẦM Ở BACKGROUND (tách rời khỏi trạng thái loading của khung chat) — có lúc chỉ báo lỗi khi worker check ĐÚNG vào khe hở đó.

**Root cause:** `_gemini_video_result_if_ready()` coi `is_generating=false` + có text + KHÔNG có `<video>` = "Gemini đã trả lời xong nhưng từ chối" → trả `{'error': text}` NGAY — không phân biệt được ACK "đang xử lý" (còn phải chờ tiếp) với 1 lời từ chối THẬT (vd rate-limit "I'm getting a lot of requests..." đã verify ở phiên trước).

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_gemini_video_result_if_ready()` — thêm `_GEMINI_VIDEO_STILL_WORKING_MARKERS` (`'generating your video'`, `'đang tạo video'`, `'check back'`, `'a few minutes'`/`'few minutes'`, `'vài phút'`) — nếu text khớp 1 trong các marker này (không phân biệt hoa/thường) thì trả `None` (tiếp tục poll) thay vì `{'error': ...}`. Đồng thời đổi thứ tự check: đọc `<video>` TRƯỚC `is_generating` (nếu video đã có sẵn, trả về ngay dù is_generating có lỡ còn `true` vì lý do khác) — cải thiện nhỏ, không phải root cause chính. `_gemini_video_wait_result(timeout=600)` (không đổi) vẫn là lưới an toàn cuối — nếu Gemini THẬT SỰ kẹt (không bao giờ ra video, chỉ lặp lại ACK) thì sau 600s vẫn timeout đúng như thiết kế, không treo vô hạn. |

**Verify:** `py_compile`/`pyflakes` sạch. **Root cause + fix được suy ra TRỰC TIẾP từ log production thật** (không đoán mò) — không cần dựng lại kịch bản trên browser vì bằng chứng đã đủ rõ ràng (2 lần lỗi + 1 lần retry thành công cùng pattern thời gian, cùng thông báo). **CHƯA verify lại trên browser thật SAU KHI fix** — không attach được vào Chrome đang chạy của worker LIVE (port bận, CDP chỉ cho 1 session), và **code KHÔNG hot-reload** — process `main.py` (client_tool GUI) đang chạy vẫn dùng bytecode CŨ trong RAM dù file đã sửa trên đĩa. **Cần user tự đóng và mở lại `main.py`** (hoặc chờ lần restart tiếp theo) để áp dụng fix, rồi theo dõi vài task `gemini_video` có ref ảnh để xác nhận không còn báo lỗi sai nữa.

**Lưu ý phụ:** lúc điều tra đã Stop rồi Start lại worker profile 31 qua API để thử refresh — nhưng vì `worker_mode='gemini_video'` nằm trong `_VEO3_LIKE_WORKER_MODES` (auto-scale, xem §11.27), backlog còn task nên auto-scale tự khởi động lại NGAY trong CÙNG process (không giúp gì — vẫn code cũ). Task #4711 đang xử lý dở bị ngắt giữa chừng lúc Stop — sẽ tự phục hồi qua reaper server (`REAP_VIDEO_TIMEOUT_MIN`, tối đa ~15 phút) như thiết kế sẵn có cho trường hợp worker chết giữa task, không cần can thiệp thêm.

---

### 2026-08-07 — Fix "Invalid URL ... No scheme supplied" khi tải ảnh tham chiếu cho task Gemini-Video

**User báo lỗi thật (log profile-26):** `[GEMINI-VIDEO] Task #4635 lỗi: Invalid URL '/api/media/files/storyboard/STORYBOARD46_2.png': No scheme supplied. Perhaps you meant https:///api/media/files/storyboard/STORYBOARD46_2.png?`

**Root cause:** `_run_task_gemini_video()` (`server/worker.py`, mode mới `worker_mode='gemini_video'` — dùng tính năng "Tạo video" trong chat Gemini thay VEO3 DOM) đọc URL ảnh tham chiếu thẳng từ `task['source_media'][i]['url']` rồi gọi `req_lib.get(url, ...)` NGAY KHÔNG QUA XỬ LÝ GÌ. `source_media` do backend gửi qua heartbeat (`backend/routes/heartbeat.py`, build từ `_resolve_ref_images()`) LUÔN là đường dẫn TƯƠNG ĐỐI (vd `/api/media/files/storyboard/...png`, quy ước dùng NHẤT QUÁN khắp hệ thống — frontend luôn tự ghép `apiBase + url`) — không phải URL tuyệt đối, nên `requests.get()` (Python `requests`, cần scheme `http(s)://`) raise ngay `MissingSchema`.

| File | Thay đổi |
|------|---------|
| `server/worker.py` (`_run_task_gemini_video`) | Trước khi `req_lib.get(url, ...)`, thêm `if url.startswith('/'): url = f'{FLOW_SERVER}{url}'` + skip (log warn) nếu sau đó vẫn không bắt đầu bằng `http` — mirror ĐÚNG pattern đã có sẵn trong `_run_task_dom()` (nhánh upload ảnh qua picker DOM, ~dòng 2000 cùng file), chỉ là nhánh Gemini-Video (mới thêm 2026-08-07) bị BỎ SÓT bước này. |

Đã audit toàn bộ các nơi khác gọi `req_lib.get(url, ...)` với URL đến từ server (`_run_task_gemini`'s `image_urls`, `_gemini_slot_download_and_kickoff`'s `slot['image_urls']`, `_run_task_chatgpt`'s `image_urls`) — cả 3 đều đọc từ field `imageUrls`/`videoUrl` do backend tự build với `SELF_BASE_URL` (đã có scheme sẵn từ phía server, xem `backend/routes/gemini.py::_dispatch_storyboard_sheet_image()` v.v.) nên KHÔNG dính bug này — CHỈ `source_media` (field riêng của luồng VEO3-queue task, dùng bởi cả DOM mode LẪN Gemini-Video mode) mang path tương đối, và chỉ Gemini-Video thiếu bước chuẩn hoá. `py_compile` sạch.

---

### 2026-08-07 — FIX THẬT: VEO chọn sai/không xác nhận model — thêm retry + VERIFY thật sự đã áp dụng, raise nếu không chắc chắn

**User báo:** "trong tạo image/video bằng VEO tôi thấy phương án chọn model của bạn còn dính lỗi nhiều chọn sai model hãy thiết lập lại thật kỹ hãy dùng profile huavantien84 đang mở".

**Điều tra trên Chrome THẬT đang mở của huavantien84 (id=27)** — profile đang có 1 "login browser" mở sẵn (không phải worker đang chạy, `active_workers: []`), attach trực tiếp qua `_connect_to_chrome(9327)` (KHÔNG mở Chrome mới, không đụng phiên đang mở của user) để điều tra + fix + verify tại chỗ:
- Đọc lại `profile_logs` (400 dòng gần nhất) — tìm thấy **bằng chứng thật**: task #4666 (17:25:12) log `DOM: model "Veo 3.1 - Lite [Lower Priority]" not found in dropdown` — code CŨ chỉ log warning rồi **ĐI TIẾP submit task như bình thường**, để nguyên model CŨ còn lưu từ task trước trong state của project Flow. Task vẫn "thành công" (không báo lỗi) nhưng generate với **SAI MODEL** mà không có cách nào phát hiện được từ log — đúng khớp mô tả "chọn sai model" của user.
- Kiểm tra danh sách model thật + icon `arrow_drop_down`/`crop` trên trang — xác nhận DUY NHẤT (không có button trùng icon nào khác trên trang gây nhầm target), danh sách 5 model video không đổi so với lần verify trước (2026-08-07, cùng ngày, phiên trước) — loại trừ giả thuyết "Google đổi UI".

**Root cause thật:** `_dom_configure()`'s block chọn model (trước đây) chỉ có **1 lần thử** (`_wait_js(MODEL_MATCH_JS, timeout=3.0)`), và dù thành công hay thất bại đều **KHÔNG XÁC NHẬN LẠI** — chỉ tin "đã tìm thấy item + đã click = xong". Khi việc tìm-item timeout (dropdown vừa mở, animation/render chưa kịp ổn định — đặc biệt ngay sau khi vừa click ratio/count trước đó), code chỉ log warning rồi để nguyên model cũ, KHÔNG retry, KHÔNG chặn task lại — task tiếp tục chạy và sinh kết quả với model sai, tốn quota thật.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | Thêm `ModelSelectionFailed(RuntimeError)` (exception riêng, PHẢI được re-raise chứ không bị nuốt bởi try/except chung của `_dom_configure()`). Thêm `_dom_select_model_verified(model, attempts=3)` — mỗi lần thử: mở dropdown → tìm item (MODEL_MATCH_JS, giữ nguyên logic khớp-chính-xác đã có) → click → **POLL XÁC NHẬN** (đọc lại label trigger button `arrow_drop_down`, strip tiền tố trang trí, so khớp ĐÚNG model muốn — kết hợp "chờ UI cập nhật" + "xác nhận đúng nội dung" trong 1 bước `_wait_js`) — hết `attempts` lần vẫn không xác nhận được thì dọn dẹp dropdown (Escape, tránh để state mở dở làm hỏng lần gọi kế tiếp) rồi `raise ModelSelectionFailed(...)` kèm model THẬT SỰ đang hiển thị để dễ debug. `_dom_configure()`'s block model đổi thành gọi hàm này; except-clause tách riêng `except ModelSelectionFailed: ...; raise` (không nuốt) trước `except Exception` chung (best-effort như cũ cho các bước khác — tab/ratio/count/upload vẫn giữ triết lý cũ, chỉ RIÊNG model mới siết chặt vì cái giá của việc chọn sai là tốn quota thật + kết quả sai). `ModelSelectionFailed` lan tới `_run_task_dom()`/`_run_tasks_batch()`'s except-block sẵn có → task tự động error/retry qua cơ chế đã có, KHÔNG cần sửa gì thêm ở 2 nơi gọi. |

**Verify THẬT trên Chrome đang mở của huavantien84** (không mở Chrome mới, attach vào đúng phiên user đang có):
- 5/5 model video (Omni Flash, Veo 3.1 - Lite/Fast/Quality/Lite [Lower Priority]) + 2/2 model ảnh (Nano Banana Pro, Nano Banana 2 Lite) chọn đúng qua `_dom_select_model_verified()` — xác nhận LẦN THỨ 1 mỗi lần, không cần retry trong điều kiện bình thường.
- Test cố ý model KHÔNG TỒN TẠI → `ModelSelectionFailed` được raise ĐÚNG (không bị nuốt/silent-continue như code cũ) — xác nhận cơ chế hard-fail hoạt động.
- **Bug thứ 2 bắt được NGAY LÚC verify** (không phải giả thuyết, quan sát trực tiếp): sau khi raise, nếu không dọn dẹp dropdown, lần gọi KẾ TIẾP bị hỏng theo kiểu KHÁC hẳn (không tìm thấy cả nút `arrow_drop_down`, vì click sau đó TOGGLE ĐÓNG 1 dropdown đang mở dở thay vì mở mới) — đã fix bằng bước Escape dọn dẹp trước khi raise, verify lại xác nhận lần gọi kế tiếp phục hồi bình thường ngay lần đầu.
- **Test FULL `_dom_configure()`** (tab+ratio+count+model CÙNG LÚC, đúng thứ tự 1 task thật gọi, xen kẽ video/ảnh liên tiếp mô phỏng nhiều task nối nhau) — 5/5 case đúng model, xác nhận fix hoạt động trong ĐÚNG bối cảnh production (không chỉ khi test cô lập riêng bước model).
- Đã khôi phục lại model thật của profile (`Veo 3.1 - Lite [Lower Priority]`, khớp mọi task gần đây trong log) trước khi kết thúc — không để lại state lạ trên phiên Chrome đang mở của user.

---

### 2026-08-07 — THÊM engine THỨ 2 cho task video: `worker_mode='gemini_video'` (Gemini chat's "Tạo video", thay thế/song song VEO3)

**User yêu cầu:** "nâng cấp model tạo video thành 2 option: Veo là luồng hiện tại, Gemini là luồng sẽ mới ... Trong gemini chon option Tạo video cũng có đính kèm ảnh tham chiếu và ratio. Hãy kết nối profile để tìm luồng đính kèm tham chiếu và tạo option radio phù hợp + viết file test luồng này".

**Khám phá DOM thật** (profile `kqxs0007`, id=26, `worker_mode='gemini'` sẵn có) — Gemini chat có sẵn mục **"Tạo video"** trong menu "+" (mở qua `_gemini_open_composer_menu()` có sẵn), click vào bật 1 "chế độ Video" cho composer hiện tại (nút "Video" đổi `aria-label` thành "Bỏ chọn Video" khi active) với:
- Nút **"Tải tệp lên"** (đính kèm ảnh tham chiếu) — **DÙNG ĐƯỢC NGUYÊN** `_gemini_attach_file()` sẵn có, không cần viết lại (verify trực tiếp qua Selenium thật).
- Nút **tỷ lệ khung hình** (`button[aria-label^="Tỷ lệ khung hình"]`) — click mở `.cdk-overlay-container` chứa đúng 2 option `role="menuitemradio"` ("Ngang (16:9)"/"Dọc (9:16)"), `aria-checked`/label trigger phản ánh đúng lựa chọn — verify đổi ratio rồi đọc lại label, khớp 100% cả 2 chiều.
- Kết quả: `<video crossorigin="use-credentials" src="https://contribution.usercontent.google.com/download?...">` — `src` này CẦN session cookie, server KHÔNG tải trực tiếp được (khác VEO3's CDN URL public) → phải tải NGAY TRONG BROWSER (`fetch(credentials:'include')` → blob → base64, cùng pattern đã proven cho ảnh Gemini) rồi giải mã ở Python, upload multipart qua `/api/media/task/<id>/upload_result` (endpoint CÓ SẴN — dùng cho "Thêm video thủ công", `_apply_media_to_task()` tự set `status='done'`, KHÔNG cần sửa backend).
- Thời gian generate: prompt ngắn không ref ảnh ~53-72s (verify 3 lần thật); có ref ảnh có thể lâu hơn — 1 lần test vượt 150s vẫn chưa xong (chưa xác định rõ do ref ảnh hay do rate-limit tài khoản, xem "Chưa rõ" bên dưới).

**Kiến trúc — mirror `_run_task_dom()`, KHÔNG dùng cơ chế `gemini_pending_requests`:** khác hẳn `_run_task_gemini`/`_run_gemini_loop` (dùng cho pipeline kịch bản/ảnh NanaBananaPro, tiêu thụ hàng đợi RIÊNG `gemini_pending_requests` qua `_heartbeat_gemini()`), `_run_task_gemini_video()` xử lý TRỰC TIẾP task VIDEO trong `tasks_media_flow` — **CÙNG hàng đợi mà VEO3 (`worker_mode='dom'/'api'`) tiêu thụ qua `_heartbeat()`/`/api/media/heartbeat`**, chỉ khác "công cụ tạo". Nhờ vậy: KHÔNG cần sửa gì ở backend `ToolSub` gốc (task dispatch, `pending_by_mode`, reaper... đều generic theo `machineCode`, không phân biệt worker_mode) — toàn bộ thay đổi nằm gọn trong `client_tool`.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | Thêm nhóm hàm `_gemini_video_enter_mode()`/`_gemini_video_set_ratio(aspect_ratio)` (map `'9:16' in aspect_ratio` → Dọc, còn lại → Ngang mặc định)/`_gemini_video_result_if_ready()`/`_gemini_video_wait_result(timeout=600)`/`_gemini_video_extract_base64(video_url)`/`_upload_video_result(task_id, bytes)`/`_run_task_gemini_video(task)` (mirror shape `_run_task_dom`: POST `/task/processing` → chat mới → enter mode → set ratio → tải+đính kèm `source_media` (nếu có) → gõ+gửi → chờ+trích video → upload_result → `_handle_task_success`/`_handle_task_error`). `__init__`'s validation list + `load_ext` exclusion + `_run_task()` dispatcher + `run()` (thêm nhánh setup `is_gemini_video`: navigate thẳng `GEMINI_URL`, bỏ qua `_ensure_flow_page()`/token capture) đều thêm `'gemini_video'`. `_handle_task_error()`'s escalation "tạo project mới" (`_reset_flow_project()`, Flow-specific) skip cho `gemini_video` (mỗi task đã tự navigate `GEMINI_URL` mới, coi như đã "reset"). |
| `server/dispatcher.py` | `_start_worker()` + `_veo3_dispatcher_tick`'s route Start thủ công (`server/routes.py`) không còn bắt buộc `project_url` cho `gemini_video`. `_auto_scale_veo3_tick()` đổi filter `('api','dom')` → hằng số mới `_VEO3_LIKE_WORKER_MODES=('api','dom','gemini_video')` — `gemini_video` LUÔN `task_mode='video_only'` (ép ở `ProfileDialog.get_data()`) nên tự nhiên rơi đúng vào ngân sách `video_budget` đã có sẵn trong `_veo3_dispatcher_tick()` (cạnh tranh CÙNG pool video với VEO3 `video_only`, không cần sửa logic cấp ngân sách theo mode). |
| `server/routes.py` | `start_worker()` route bỏ yêu cầu `project_url` khi `worker_mode=='gemini_video'`. |
| `gui/profile_dialog.py` | Combo "Loại" thêm option thứ 4 `🎬 Gemini — Tạo Video (mới, thay VEO3)` (`gemini_video`) — tái dùng NGUYÊN 2 field timeout của nhóm 'gemini' (Attach/Response timeout — cùng ý nghĩa, chỉ khác thang thời gian, worker tự clamp sàn 600s cho response) + field `max_concurrent` của nhóm 'veo3' (số task/lần heartbeat, xử lý TUẦN TỰ — không có round-robin nhiều tab). `get_data()` ép `task_mode='video_only'`, `project_url=''`. |
| `gui/pages/profiles_page.py` | Badge "🎬 Gemini Video" riêng (thay vì badge task_mode+worker_mode như VEO3); `can_start` không đòi `project_url` cho `gemini_video`. |
| `tests/_test_gemini_video_flow.py` (MỚI) | Test độc lập (không cần task DB thật, trừ khi truyền `--task-id`) — chạy `_gemini_video_enter_mode`/`_gemini_video_set_ratio` (verify lại qua đọc `aria-label`)/`_gemini_attach_file`/`_gemini_fill_and_submit`/`_gemini_video_wait_result`/`_gemini_video_extract_base64`, lưu video ra file + validate header MP4. |

**Verify end-to-end THẬT (Selenium thật, profile `kqxs0007`, tốn quota thật — 2 lần generate thành công + 1 lần bắt đúng lỗi rate-limit):**
- Ratio 9:16 (Dọc): áp dụng đúng (verify qua đọc lại `aria-label`), video 784678 bytes, header MP4 hợp lệ, generate xong sau 53.2s.
- Ratio 16:9 (Ngang, lần chạy khác): áp dụng đúng, video 2576826 bytes hợp lệ (lần discovery trước khi viết test chính thức).
- Đính kèm ảnh tham chiếu: xác nhận tile đính kèm xuất hiện (0.7s) trong chế độ Video, không cần sửa `_gemini_attach_file()`.
- Lỗi thật: 1 lần Gemini từ chối do rate-limit tài khoản ("I'm getting a lot of requests right now...") — `_gemini_video_wait_result()` bắt đúng, raise message rõ ràng thay vì treo/timeout mù.

**⚠️ Chưa verify / cần theo dõi thêm:**
- Full round-trip qua `_run_task_gemini_video()` với 1 task THẬT trong `tasks_media_flow` (route `/task/processing`→`/upload_result`) — mới test các bước nguyên tố (enter mode/ratio/attach/submit/extract) trực tiếp, chưa chạy qua hàng đợi heartbeat thật với 1 task video thật do rate-limit tài khoản test giữa chừng.
- Thời gian generate KHI CÓ ref ảnh — 1 lần test trước đó (không qua production code, qua script discovery) vượt 150s chưa xong; sàn timeout mặc định `_gemini_video_wait_result()` đã đặt 600s nhưng CHƯA xác nhận đủ dư dả cho mọi trường hợp thật (ref ảnh + prompt dài).
- Chưa test qua GUI thật (`main.py`, tạo profile mới chọn "🎬 Gemini — Tạo Video", bấm Start, để auto-scale tự nhận task video thật từ backlog) — mọi verify ở trên đều gọi trực tiếp qua script Python, không qua luồng heartbeat/dispatcher/auto-scale đầy đủ.
- Chưa xác nhận hành vi khi output_count>1 (nhiều ảnh/video 1 lần) — Gemini video mode có vẻ chỉ tạo 1 video/lần submit, `_run_task_gemini_video()` hiện không đọc `task.output_count` (khác VEO3 DOM configure có chọn x1-x4).

---

### 2026-08-04 — FIX THẬT: ChatGPT báo "Bạn đã upload ảnh này" dù 2 ảnh KHÁC NỘI DUNG hoàn toàn — root cause là cách gọi send_keys(), không phải giới hạn sản phẩm

**User yêu cầu:** "chạy test lại chatgpt cho project_storyboards có id = 46 để xem lý do lỗi sao đính kèm 2 ảnh tham chiếu bị lỗi" — sau đó báo "vẫn lỗi 'bạn đã upload ảnh này' mặc dù cũng 2 ảnh đó tôi upload thủ công thì được."

**Điều tra:** đọc log profile 25 (`aikhanh251295`, worker ChatGPT) qua `/api/worker_profiles/25/logs` — xác nhận cả 2 ảnh (`15_CHAR_001_03.jpg` 634KB, `6_BG_008_01.jpg` 1121KB, từ `source_media` của storyboard #46, KHÔNG qua `ref_name_ids` nên không dính bug URL-alias đã fix ở entry ngay dưới) đều tải về + gọi `send_keys()` xác nhận `.files.length` tăng đúng, prompt vẫn gửi được — nhưng response luôn timeout 300s (ChatGPT không bao giờ thực sự trả lời, khớp với việc tin nhắn bị chặn ở phía ChatGPT do lỗi "đã upload"). `curl` trực tiếp 2 URL → SHA256 khác nhau hoàn toàn — xác nhận 2 ảnh KHÔNG trùng nội dung. User xác nhận tự tay chọn CÙNG 2 file qua dialog thật (1 lúc) upload bình thường — bằng chứng quyết định loại trừ giả thuyết "giới hạn sản phẩm ChatGPT Free với ≥2 ảnh" đã ghi nhận trước đó (nếu là giới hạn sản phẩm, thao tác tay cũng phải bị chặn).

**Root cause thật:** `_run_task_chatgpt()` (bản cũ) gọi `_chatgpt_attach_file()` (đơn) RIÊNG cho từng ảnh trong vòng lặp — N lần `send_keys()` tách rời trên CÙNG 1 input TĨNH `#upload-files` (ChatGPT render sẵn 1 lần trong DOM, không mở lại menu mỗi lần như Gemini) — khác thao tác tay (1 lần chọn multi-file = 1 sự kiện `change` duy nhất). Nghi ChatGPT chống-trùng theo tín hiệu KHÁC nội dung byte (session/token nội bộ, hoặc coi 2 lần `change` liên tiếp là "chọn lại") và bị false-positive.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | Thêm `_chatgpt_attach_files(local_paths: list, timeout)` — gộp TOÀN BỘ path vào ĐÚNG 1 lệnh `send_keys('\n'.join(local_paths))` (cú pháp Selenium chuẩn cho `input[multiple]`, dịch sang 1 lệnh CDP `DOM.setFileInputFiles` duy nhất — mô phỏng đúng multi-select thủ công), poll `.files.length >= before_count + N`. `_run_task_chatgpt()` đổi: tải xong HẾT mọi ảnh trước, rồi mới gọi hàm mới NÀY một lần duy nhất (không còn attach xen kẽ trong vòng lặp download). Giữ nguyên `_chatgpt_attach_file()` (đơn) cho case 1 ảnh/call site khác. |
| `tests/_test_chatgpt.py` | Gọi `_chatgpt_attach_files(images, ...)` 1 lần thay vì loop `_chatgpt_attach_file()` per ảnh (bản loop cũ chính là cách test đã "xác nhận" nhầm giới hạn 2 ảnh trước đây). |

**Hệ quả:** nhận định "GIỚI HẠN THẬT — ChatGPT Free ÂM THẦM DROP nếu đính kèm ≥2 ảnh" (ghi ở CLAUDE.md §11.25/§11.26, dựa trên đúng bản test loop-riêng-từng-ảnh) nhiều khả năng là **chẩn đoán sai** — root cause thật là cách gọi `send_keys()`, không phải giới hạn phía OpenAI. Đã cập nhật lại CLAUDE.md ghi rõ cần retest N=2 sau fix, chưa loại trừ khả năng vẫn còn giới hạn thật ở N≥3.

**Verify:** `py_compile`/`pyflakes` sạch. **⚠️ CHƯA verify trên browser thật** — task test trước đó đã timeout 300s trước khi fix kịp deploy; code KHÔNG hot-reload, cần RESTART `main.py` rồi bấm "Chạy lại" trên storyboard #46 (hoặc tương đương) để áp dụng và xác nhận dứt điểm.

**Phát hiện phụ trong lúc điều tra:** 2 process `main.py` chạy song song trên máy user (documented anti-pattern, xem CLAUDE.md §11.10/§11.11 "KHÔNG chạy 2 instance song song") — có thể góp phần vào các lỗi crash Chrome khác quan sát được trong log cùng ngày (`invalid session id: session deleted`, `no such window: target window already closed`). Đã hướng dẫn user đóng bớt 1 instance (giữ PID thực sự bind port 13445).

---

### 2026-08-04 — FIX: đính kèm ảnh trùng lặp khiến ChatGPT báo "ảnh này đã upload lên rồi"

User báo bug thật ngay sau khi dùng tính năng multi-attach (entry ngay dưới): "upload ảnh 1 xong -> upload ảnh 2 nhưng bị báo lỗi ảnh này đã upload lên rồi -> bước upload tuần tự ảnh đang có vấn đề". Root cause nằm ở backend `ToolSub` gốc — `_dispatch_storyboard_sheet_image()` gộp candidate ảnh đính kèm từ 2 nguồn ĐỘC LẬP (`source_media` upload local + `ref_name_ids` asset), không đảm bảo không trùng (hệ thống có sẵn tính năng "gắn từ thư viện" cho 2 asset khác nhau trỏ CÙNG 1 file vật lý) — 2 URL khác nhau nhưng NỘI DUNG giống hệt lọt xuống worker, tải thành 2 file cục bộ tên khác nhau nội dung giống hệt, ChatGPT tự phát hiện trùng THEO NỘI DUNG và từ chối đính kèm lần 2.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_run_task_gemini()`/`_run_task_chatgpt()`/`_gemini_slot_step()` — dedup `image_urls` bằng `list(dict.fromkeys(image_urls))` (giữ nguyên thứ tự) ngay sau khi đọc từ `pending`, trước khi tải bất kỳ file nào — lớp phòng vệ THỨ 2 độc lập, backend `ToolSub` gốc đã fix dedup tại nguồn (`_dispatch_storyboard_sheet_image()`, xem CHANGELOG `ToolSub` 2026-08-04). |

**Verify:** `py_compile`/`pyflakes` sạch. Backend đã verify dedup tại nguồn qua DB thật (xem CHANGELOG `ToolSub`). **Chưa verify trên browser thật** — cần user chạy lại 1 storyboard có ≥2 ref ảnh (đặc biệt nếu có asset dùng chung ảnh qua "Thư viện") để xác nhận không còn gặp lỗi "đã upload lên rồi".

---

### 2026-08-04 — Multi-attach: đính kèm ĐỦ mọi ảnh tham chiếu (không còn giới hạn 1 ảnh/request)

User báo bug thật (backend `ToolSub`): "upload 2 ảnh tham chiếu nhưng qua chatgpt/gemini chỉ thấy đính kèm có 1 ảnh". Backend (`ToolSub` gốc) thêm field mới `imageUrls` (mảng đầy đủ URL) trong `pendingPrompt`/`pendingPrompts` — xem CHANGELOG.md `ToolSub` 2026-08-04 "Multi-attach". Phía client_tool cần đọc field mới này và tải + đính kèm TUẦN TỰ từng ảnh thay vì chỉ 1.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_run_task_gemini()`/`_run_task_chatgpt()` (1-tab tuần tự) — đổi `video_url`/`image_url` (đơn) sang `image_urls = pending.get('imageUrls') or ([videoUrl/imageUrl] nếu có)`, lặp `for idx, url in enumerate(image_urls)` tải + `_gemini_attach_file()`/`_chatgpt_attach_file()` tuần tự; `tmp_path` đơn đổi thành `tmp_paths` (list), dọn sạch tất cả trong `finally`. `_gemini_slot_step()` (round-robin nhiều tab) — state `'new'` lưu `slot['image_urls']`/`image_idx`/`tmp_paths`, tải+kickoff ảnh đầu qua helper mới `_gemini_slot_download_and_kickoff()`; state `'attaching'` sau khi poll xác nhận 1 ảnh xong thì tăng `image_idx` — còn ảnh thì tải+kickoff ảnh kế (VẪN Ở state `'attaching'`, không chuyển state), hết ảnh mới gõ prompt + chuyển `'submitting'`. `_gemini_slot_finish()` dọn `tmp_paths` (list) thay vì `tmp_path` đơn. |

**⚠️ Giới hạn THẬT đã kiểm chứng riêng cho ChatGPT (không phải do fix này, đã ghi nhận từ trước — xem entry 2026-08-01 "worker_mode='chatgpt'"):** tài khoản Free ÂM THẦM DROP tin nhắn nếu đính kèm ≥2 ảnh cùng lúc (test 3 lần độc lập). Fix này đảm bảo client_tool cố gắng đính kèm đủ N ảnh backend yêu cầu, nhưng ChatGPT Free với N≥2 nhiều khả năng vẫn thất bại ở tầng sản phẩm OpenAI, không phải lỗi tool. Gemini không có giới hạn này.

**Verify:** `py_compile`/`pyflakes` sạch. Backend đã verify dispatch + heartbeat exposure qua DB thật (xem CHANGELOG `ToolSub`). **CHƯA verify trên browser thật** — cần user tự chạy 1 request ≥2 ảnh tham chiếu qua cả luồng 1-tab và round-robin nhiều tab để xác nhận đính kèm đủ ảnh trước khi gửi.

---

### 2026-08-03 — FIX: `_extract_response_images_base64()` đôi khi chụp phải placeholder TRẮNG thay vì ảnh Gemini/ChatGPT thật sự generate ra

**Bug user báo (từ phía backend `ToolSub`):** storyboard #34 frame 2 và 11 — lịch sử chat ChatGPT THẬT SỰ có ảnh, worker cũng báo "hoàn tất", nhưng ảnh lưu vào hệ thống là **1 tấm TRẮNG TOÀN BỘ, byte-for-byte GIỐNG HỆT NHAU** giữa 2 frame khác nhau (xác nhận qua `md5sum` — cùng hash) — rõ ràng không phải nội dung thật.

**Root cause:** `_chatgpt_response_if_ready()`/`_gemini_response_if_ready()` coi response "xong" ngay khi tín hiệu is-generating biến mất (nút Stop/`stop-button` biến mất) rồi gọi `_extract_response_images_base64()` NGAY LẬP TỨC — nhưng ChatGPT/Gemini có thể hiện `<img>` PLACEHOLDER (giữ đúng kích thước cuối cùng để không nhảy layout) TRONG LÚC ảnh thật còn đang render/decode — `naturalWidth` của placeholder này đã đủ lớn để qua được filter `min_width`, nên `_extract_response_images_base64()` vẽ nó lên canvas và coi là ảnh hợp lệ — kết quả: 1 canvas TRẮNG được `toDataURL()` thành 1 ảnh PNG "hợp lệ về mặt kỹ thuật" nhưng rỗng nội dung. Vì placeholder trắng luôn cho ra CÙNG 1 kết quả pixel bất kể lúc nào bị chụp, 2 frame khác nhau bị race trúng đúng lúc này ra cùng 1 file y hệt — khớp chính xác triệu chứng quan sát được.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_extract_response_images_base64()` — đổi return type từ `list` sang `tuple (images, pending)`. Thêm `isBlankCanvas(ctx, w, h)` (JS nội bộ) — lấy mẫu 9 điểm pixel (4 góc + 4 điểm giữa cạnh + tâm) qua `ctx.getImageData()`, nếu tất cả gần như cùng 1 màu (lệch kênh màu ≤4, đủ dung sai chống nhiễu anti-aliasing) thì coi là placeholder CHƯA XONG — loại khỏi `out`, set cờ `pending=true` thay vì lưu ảnh trắng. `_gemini_response_if_ready()`/`_chatgpt_response_if_ready()` — đọc thêm `images_pending`, trả `None` (tiếp tục poll thay vì chốt response) nếu `True`, bất kể text đã có hay chưa. Blank-check CHỈ áp dụng cho nhánh canvas (không áp dụng fallback `fetch()` — không vẽ canvas nên không đọc được pixel, nhưng nhánh đó vốn chỉ dùng cho ảnh cross-origin đã tải xong hẳn nên rủi ro thấp hơn nhiều). |

**Verify:** `py_compile`/`pyflakes` sạch. Trích riêng đoạn JS `isBlankCanvas` (đúng từ file, không gõ lại tay), chạy qua Node với mock `ctx.getImageData()` — 4 kịch bản: canvas trắng toàn bộ (blank=true ✓), ảnh thật có pixel đa dạng (blank=false ✓), canvas bị lỗi đọc pixel/tainted (blank=false — KHÔNG chặn nhầm ✓), gần-đồng-màu có nhiễu nhỏ do anti-aliasing (vẫn blank=true, đúng dung sai ✓). **KHÔNG THỂ verify trên browser thật** (môi trường này không chạy được Chrome/Selenium) — cần user chạy lại 1 task ảnh thật (Gemini hoặc ChatGPT) để xác nhận: (1) không còn ảnh trắng nào được lưu; (2) nếu placeholder xuất hiện, tool tự đợi thêm rồi mới lấy đúng ảnh thật (không timeout/kẹt do đợi quá lâu — nếu ảnh KHÔNG BAO GIỜ hết placeholder trong `gemini_response_timeout`/`chatgpt_response_timeout`, task sẽ timeout như bình thường, không có gì thay đổi ở nhánh đó).

---

### 2026-07-30 — Fix "video không tạo được": Google đổi icon tab Video (`play_circle`→`videocam`) trong popup config Flow, `_dom_configure()` không còn khớp được

**Bối cảnh:** User cung cấp HTML thật của popup "config" (chọn Hình ảnh/Video, tỷ lệ khung hình, model, số lượng output) lấy trực tiếp từ Flow — yêu cầu so sánh với `client_tool` để tìm khác biệt gây lỗi "video không tạo được".

**Root cause:** `_dom_configure()`'s bước chọn main-tab (Video/Hình ảnh) tìm 1 `button[role="tab"]` có `<i>` icon Material Symbol khớp **CHÍNH XÁC** (`===`) chuỗi `'play_circle'` cho MỌI mode video (`textToVideo`/`imageToVideo`/`frameToVideo`/`componentsToVideo`). HTML thật user cung cấp cho thấy Google đã đổi icon của tab "Video" thành `videocam` — search cũ luôn trả `null`, tab Video KHÔNG BAO GIỜ được click, task video bị submit trong khi popup vẫn đứng ở tab "Hình ảnh" (mặc định `aria-selected=true`) → generate sai loại media hoặc lỗi ở bước sau (sub-tab "Thành phần"/"Khung hình" chỉ tồn tại khi tab Video active). Cùng lúc phát hiện thêm 1 mismatch nhỏ: nút chọn số lượng output `x1` hiển thị text **`x1`**, nhưng `count_map` hardcode tìm `'1x'` (ngược thứ tự) — không lỗi rõ ràng vì `x1` thường đã là lựa chọn mặc định sẵn của Flow, nhưng vẫn là bug thật (không bao giờ click được nút này).

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_dom_configure()` — `main_tab_icon` (dict giá trị chuỗi đơn) đổi thành `main_tab_icons` (dict giá trị LIST biến thể: `['videocam', 'play_circle']` cho mọi mode video, `['image']` cho mode ảnh) + JS query đổi từ `i.textContent.trim()===icon` sang `icons.includes(i.textContent.trim())` — chấp nhận CẢ icon cũ lẫn mới, sống sót nếu Google đổi lại/A-B test, cùng pattern "khớp theo danh sách biến thể" đã dùng cho `i18n_texts.json`/`MODEL_MATCH_JS` trong chính file này. `count_map` đổi thành `count_variants` (mỗi count → list `['x{n}', '{n}x']`) cho cả 4 giá trị 1-4, không chỉ sửa riêng count=1. |

**Verify:** `py_compile`/`pyflakes` sạch. Script Node.js độc lập (parse THẲNG đoạn HTML thật user gửi bằng regex, không cần jsdom) mô phỏng lại chính xác 2 JS predicate trong `worker.py` — xác nhận: logic CŨ (`icon==='play_circle'`) không tìm thấy tab Video trên HTML thật (tái hiện đúng bug), logic MỚI tìm thấy đúng `trigger-VIDEO`; tương tự cho nút `x1`. Tab Hình ảnh, tỷ lệ khung hình, nút model không bị ảnh hưởng (vẫn khớp đúng như cũ). **CHƯA verify trên browser Flow thật** — cần user chạy lại 1 task video để xác nhận tab Video được chọn đúng và video tạo thành công.

---

### 2026-07-25 — Trả lại mapping `componentsToVideo` trong `_dom_configure()` — cho pipeline NanaBananaPro "Ingredient -> Video" mới

**Bối cảnh:** `ToolSub` gốc (repo cha) vừa thêm project type mới "Ingredient -> Video" (`projects.media_pipeline='ingredient_video'`) — mỗi scene sinh THẲNG 1 task `mode='componentsToVideo'` (Google Flow's "Ingredients to Video", dùng ảnh CHAR/BG/PROP làm nguyên liệu trực tiếp, không qua bước ảnh scene trung gian). Cột `tasks_media_flow.mode` được migrate widen ENUM để chấp nhận giá trị này (trước đây CHƯA có trong ENUM, dù `componentsToVideo` đã là literal hợp lệ ở vài chỗ khác của backend). Quyết định qua AskUserQuestion với user (phiên `ToolSub`): mode này chạy đúng luồng UI "chỉ ảnh đầu" mà `imageToVideo` đang dùng (sub-tab "Thành phần"), nhưng cho phép đính kèm NHIỀU ảnh (giống `imageToImage`) thay vì chỉ 1-2 ảnh khung hình.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_dom_configure()` — trả lại `'componentsToVideo': 'play_circle'` vào `main_tab_icon`, `'componentsToVideo': 'Thành phần'` vào `sub_tab_text` (DÙNG CHUNG sub-tab với `imageToVideo`, không phải sub-tab riêng). Cả 2 mapping từng bị XOÁ ngày 2026-07-21 do hiểu lầm ENUM cột DB chưa hỗ trợ giá trị này — nay `ToolSub` gốc đã migrate xong nên trả lại. `_dom_upload_images()` KHÔNG cần sửa — hàm đã generic từ trước (không branch theo `mode`, chỉ loop qua `source_media` list N phần tử), tự hỗ trợ đính kèm nhiều ảnh đúng yêu cầu. |

**Verify:** `py_compile`/`pyflakes` pass (không warning mới). **CHƯA/KHÔNG THỂ verify trên browser Flow thật** — cần user tự chạy 1 task `componentsToVideo` thật (từ project "Ingredient -> Video" bên `ToolSub`) qua client_tool, xác nhận: (1) đúng sub-tab "Thành phần" được chọn, (2) đính kèm được nhiều ảnh CHAR/BG/PROP, (3) submit + poll kết quả hoạt động bình thường như các mode video khác.

---

### 2026-07-25 — Round-robin nhiều tab: tái sử dụng tab Chrome tự mở lúc khởi động làm slot đầu tiên, không bỏ hoang

**Yêu cầu user:** "ý là khi khởi động đã có sẵn 1 tab thì nếu chạy 2 tab mở thêm 1 tab là đủ, chứ để không tab đầu làm gì" — làm rõ thêm cho báo cáo ngay trước đó ("setting 2 tab, mà 1 tab ko làm gì, chạy có 1 tab").

**Root cause (khác nguyên nhân đã fix ở entry ngay trên — đây là 1 bug ĐỘC LẬP, cùng triệu chứng bề ngoài "1 tab không làm gì"):** `_run_gemini_loop_concurrent()` sau khi `_make_driver()` mở Chrome (luôn có sẵn 1 tab mặc định, blank) KHÔNG BAO GIỜ dùng tới tab này — mỗi lần nhận lô prompt mới, vòng lặp LUÔN gọi `_gemini_open_tab()` (mở tab MỚI hoàn toàn) cho MỌI slot, kể cả slot đầu tiên. Kết quả: với setting `gemini_max_concurrent_tabs=2`, Chrome có 3 tab (1 tab khởi động bỏ hoang + 2 tab mới thật sự làm việc) — đúng hiện tượng "tab đầu không làm gì".

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_run_gemini_loop_concurrent()` — lưu `startup_tab_handle = self.driver.current_window_handle` ngay sau `_make_driver()`. Trong vòng lặp gán slot cho lô đầu tiên: nếu `startup_tab_handle` còn (chưa dùng), TÁI SỬ DỤNG làm `handle` của slot đó (rồi set về `None` — chỉ dùng 1 lần) thay vì gọi `_gemini_open_tab()`; các slot còn lại vẫn mở tab mới như cũ. An toàn vì tab khởi động CŨNG blank giống hệt tab mở qua `_gemini_open_tab()` — cả 2 đều được `_gemini_slot_step()` tự `driver.get(GEMINI_URL)` khi bắt đầu xử lý (state='new'), không phân biệt nguồn gốc. Chỉ áp dụng cho LÔ ĐẦU TIÊN — sau đó mọi tab (kể cả tab tái sử dụng) đều bị đóng khi xong batch (`_gemini_close_tab`), nên các lô kế tiếp không còn tab "mồ côi" nào để tái sử dụng nữa (đúng như vậy, không cần xử lý gì thêm). |

**Verify:** `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật** — cần user xác nhận: mở worker Gemini với `gemini_max_concurrent_tabs=2`, backlog có ≥2 việc — Chrome chỉ có ĐÚNG 2 tab (không phải 3), cả 2 đều xử lý việc thật.

---

### 2026-07-25 — Fix dedup key thiếu batchFrom/batchTo: 2 request scene_batch KHÁC NHAU của cùng 1 script/clip bị coi nhầm là trùng, chỉ mở được 1 tab dù có nhiều việc

**Bug user báo:** "request thì nhiều, setting 2 tab, mà 1 tab ko làm gì, chạy có 1 tab".

**Root cause:** `_pending_item_key()` (thêm hôm qua 2026-07-25, entry ngay dưới — lớp phòng vệ dedup phía client) dùng khoá `(field, target)` với `target = scriptId hoặc clipId hoặc projectId`. Backend `ToolSub` gốc SỬA CÙNG NGÀY (xem CHANGELOG.md của `ToolSub`, "Redesign gemini_pending_requests") đổi `_dispatch_all_scene_batches()` sang tạo SẴN NHIỀU request khác nhau cho CÙNG 1 script/clip — mỗi request 1 khoảng `batchFrom`-`batchTo` riêng (vd script có 10 scene sẽ có 2 request: batch 1-5 và batch 6-10). Khoá dedup phía client KHÔNG có `batchFrom`/`batchTo` nên 2 request THẬT SỰ KHÁC NHAU này bị tính là "trùng" (cùng `field='text_script_scene_batch'`, cùng `target=scriptId`) — request thứ 2 trở đi bị lọc bỏ oan ngay trước khi mở tab, dù server đã giao đủ việc cho cả 2 tab (setting `gemini_max_concurrent_tabs=2`).

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `SeleniumFlowWorker._pending_item_key()` — thêm `meta.get('batchFrom')`/`meta.get('batchTo')` vào khoá (`(field, target, batchFrom, batchTo)`). Field không phải scene_batch (draft/full_script/rewrite_storyboard) luôn có `batchFrom`/`batchTo` là `None` như nhau ở mọi item nên hành vi dedupe cũ (1 việc/field/target) KHÔNG đổi — chỉ scene_batch mới thật sự phân biệt đúng theo khoảng batch. |

**Verify:** `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật** — cần user set lại `gemini_max_concurrent_tabs=2`, chạy 1 script/clip có ≥2 batch scene còn thiếu, xác nhận CẢ 2 tab đều nhận được việc (không còn tab nào đứng yên dù backlog đủ).

---

### 2026-07-25 — Fix round-robin nhiều tab: lọc pendingPrompt trùng trước khi mở tab, không mở thừa tab cho 1 task

**Yêu cầu/bug user báo:** "tuy setting 2 tab chạy đồng thời nhưng chỉ có 1 task thì mở 1 tab thôi" — nối tiếp mục "1 tab ko hoạt động" ở entry ngay dưới (2026-07-24), user xác nhận đây là bất biến CẦN đảm bảo: dù `gemini_max_concurrent_tabs=2`, nếu chỉ có 1 việc thật sự khác nhau thì CHỈ mở 1 tab.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | Thêm `SeleniumFlowWorker._pending_item_key(item)` (staticmethod) — khoá nhận diện `(field, scriptId/clipId/projectId)` trích từ `item['meta']`; trả `None` nếu thiếu `field` (không đủ thông tin, coi là duy nhất, không lọc nhầm). `_run_gemini_loop_concurrent()` — ngay sau khi nhận `pending_list` từ heartbeat, lọc trùng theo khoá trên TRƯỚC KHI mở bất kỳ tab nào: 2 item cùng khoá chỉ giữ item đầu, item sau bị bỏ qua (log warning), đảm bảo số tab mở ra LUÔN ≤ số việc thật sự khác nhau trong lô, bất kể server có lỡ giao trùng (lớp bug "pending_prompt mồ côi/dispatch trùng" đã fix ở backend `ToolSub` gốc ngày 2026-07-24 — fix này là lớp phòng vệ THỨ 2 độc lập, phía client). |

**Verify:** `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật.**

---

### 2026-07-24 — Fix round-robin nhiều tab: profile bị đóng giữa chừng, mất kịch bản chưa kịp báo server

**Bug user báo:** "setting 2 tab 1 lúc: thì có mở 2 task, nhưng server chỉ có 1 task dc giao hiện tại nên 1 tab ko hoạt động, 1 tab chạy sau khi hoàn tất thì đóng tất cả profile nhưng không update kịch bản lên server".

**Root cause:** `_run_gemini_loop_concurrent()` (thêm cùng ngày, xem entry ngay dưới) chỉ gọi `pm.set_status(self.profile_id, 'idle', ...)` ĐÚNG MỘT LẦN lúc mới start — suốt vòng đời xử lý batch (mở tab, gõ prompt, chờ Gemini phản hồi, có thể mất vài phút) KHÔNG BAO GIỜ báo lại `'processing'`. `dispatcher.py::_gemini_profile_busy()` (dùng bởi `_auto_scale_gemini_tick()`, xem CLAUDE.md §11.20) đọc ĐÚNG cột `status` này để quyết định profile có đang bận hay không, tránh đóng nhầm — vì cột kẹt ở `'idle'`, auto-scale tưởng profile rảnh NGAY khi backlog vừa cạn (chỉ cần 1 trong N slot còn việc, backlog server coi như hết) và gọi `_stop_worker()` (đóng Chrome) NGAY GIỮA CHỪNG lúc slot còn lại đang xử lý — Chrome bị giết trước khi `_gemini_slot_finish()` kịp POST callback lên server, kịch bản mất kết quả dù đã generate xong. Đây CÙNG LỚP BUG đã fix cho luồng tuần tự ở §11.20d ("grace period trước khi đóng profile") — nhưng bug lần này ở TẦNG NÔNG HƠN: không phải do race đóng quá sớm, mà do CHƯA BAO GIỜ báo đúng trạng thái 'processing' để guard đó có cơ hội phát huy tác dụng.

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_run_gemini_loop_concurrent()` — thêm `pm.set_status(self.profile_id, 'processing', pid=threading.get_ident())` ngay sau khi mở batch tab mới (mọi slot còn active); thêm `pm.set_status(self.profile_id, 'idle', clear_task=True, pid=threading.get_ident())` ngay khi TOÀN BỘ slot đã đóng (batch xong hẳn) — trước vòng lặp quay lại heartbeat xin lô mới. |

**Về phần "1 tab ko hoạt động" (mở 2 tab nhưng server chỉ có 1 task thật):** `_run_gemini_loop_concurrent()` chỉ mở đúng `len(pending_list)` tab (bị `pending_list[:max_tabs]` giới hạn, không mở thừa tab nào không có prompt tương ứng) — nếu heartbeat trả về 2 item dù chỉ có 1 việc thật, nhiều khả năng liên quan tới lớp bug "pending_prompt mồ côi/dispatch trùng" đã fix cùng ngày ở repo `ToolSub` gốc (`backend/routes/heartbeat.py`/`gemini.py`, xem CHANGELOG.md của `ToolSub`) — 1 script có thể bị dispatch trùng (1 bản trực tiếp qua `pending_prompt`, 1 bản qua backlog `gemini_pending_requests`) khiến heartbeat trả 2 prompt cho cùng 1 việc logic. Chưa có bằng chứng DOM/log trực tiếp để xác nhận 100% đây là đúng nguyên nhân cho trường hợp cụ thể user gặp — cần theo dõi thêm sau khi 2 fix (backend + status ở trên) cùng có hiệu lực.

**Verify:** `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật** — cần user set lại `gemini_max_concurrent_tabs=2`, chạy 1 batch có backlog chỉ đủ 1 task thật, xác nhận: (a) profile KHÔNG bị đóng giữa chừng khi auto-scale tick chạy (mỗi ~10s), (b) kết quả POST callback thành công lên server dù đang chạy round-robin nhiều tab.

---

### 2026-07-24 — Profile Gemini: round-robin nhiều tab chạy đồng thời

| File | Thay đổi |
|------|---------|
| `server/worker.py` | 3 hàm blocking-poll (`_gemini_attach_file`, `_gemini_fill_and_submit`, `_gemini_wait_response`) tách thành cặp "kick off 1 lần" + "check không-block 1 lần" (`_gemini_attach_file_kickoff`/`_gemini_attach_file_poll`, `_gemini_type_prompt`/`_gemini_submit_if_ready`, `_gemini_response_if_ready`) — 3 hàm gốc giữ nguyên chữ ký, giờ là wrapper mỏng gọi lặp lại cặp trên (luồng 1-tab tuần tự `_run_task_gemini` KHÔNG đổi hành vi, zero regression). `_heartbeat_gemini()` đổi chữ ký: thêm `max_concurrent`/`keepalive_only`, trả về **list** (`pendingPrompts`) thay vì 1 dict — `_run_gemini_loop()` (tuần tự) chỉ lấy `list[0]`. Thêm `_run_gemini_loop_concurrent(max_tabs)` (mới) — vòng lặp round-robin: nhận 1 LÔ tối đa `gemini_max_concurrent_tabs` prompt/lần, mở 1 tab riêng/prompt (`_gemini_open_tab()`, Selenium 4 `switch_to.new_window('tab')`), lần lượt `switch_to.window()` qua từng tab (chờ `gemini_tab_switch_interval` giây/lần chuyển — 0.5s mặc định), mỗi lượt ghé thăm chạy 1 bước nhỏ của state machine per-slot (`_gemini_slot_step`: new→[attaching nếu có video]→submitting→waiting_response→done, dùng lại cặp kickoff/check ở trên) — CHỈ khi TOÀN BỘ lô hiện tại xong (mọi slot 'done', tab tự đóng qua `_gemini_close_tab()`) mới heartbeat xin lô tiếp theo. Giữa chừng chỉ heartbeat với `keepalive_only=True` (tái dùng cờ `keepaliveOnly` có sẵn từ VEO3) để giữ `last_seen` tươi mà KHÔNG nhận thêm việc — không cần thread keepalive riêng như luồng tuần tự vì vòng lặp round-robin tự nó không bao giờ block quá `POLL_INTERVAL`. `_run_gemini_loop()` (entry point cũ) đọc `gemini_max_concurrent_tabs` của profile, giao cho nhánh cũ (`==1`, mặc định — HÀNH VI KHÔNG ĐỔI) hay nhánh mới (`>1`). |
| `server/managers.py` | `pm.create()`/`pm.update()` thêm 2 field `gemini_max_concurrent_tabs`/`gemini_tab_switch_interval` vào signature + whitelist `allowed`. |
| `server/routes.py` | `create_profile()` forward 2 field trên vào `pm.create(...)` (PATCH `update_profile()` đã generic passthrough sẵn, không cần sửa). |
| `gui/profile_dialog.py` | `ProfileDialog` — 2 field mới trong nhóm "Gemini" (ẩn/hiện theo `_type_fields`, cùng pattern Attach/Response timeout): **Số tab đồng thời** (`QSpinBox`, 1-10) và **Giãn cách chuyển tab** (giây, mặc định 0.5). `_accept()` validate giãn cách phải parse được `float`; `get_data()` gửi kèm 2 field khi Loại = Gemini. |
| `tests/_test_gemini_dispatcher.py` | Cập nhật theo chữ ký mới của `_heartbeat_gemini()` (trả list) — script test cũ vẫn chỉ xử lý tuần tự nên lấy `list[0]`. |

**Lý do (yêu cầu user):** "nâng cấp profile gemini trong client_tool có thể chạy đồng thời bao nhiêu tab... setting 3 tab thì nhận 1 lần 3 task hoàn tất đủ 3 task thì nhận tiếp, khi chạy task sẽ bấm luân chuyển các tab qua lại cho gemini chạy lệnh như click tab 1 → 2 → 3 rồi về 1 lặp lại đủ task thì nhận lô task mới". Vì Selenium/ChromeDriver chỉ có 1 "current window" cho MỌI lệnh (kể cả CDP passthrough `execute_cdp_cmd` mà `_cdp`/`_cdp_click_el` dùng) tại 1 thời điểm trong 1 phiên trình duyệt, N conversation Gemini "song song" trong CÙNG 1 Chrome chỉ khả thi bằng cách luân phiên `switch_to.window()` qua từng tab, mỗi lượt làm 1 bước nhỏ không-block — không có gì thật sự đa luồng, nhưng vì phần chờ dài nhất (Gemini generate, xác nhận đính kèm) là chờ DOM/mạng (không cần driver bận), N task hoàn tất gần bằng thời-gian-chờ-dài-nhất thay vì N lần thời gian đó như chạy tuần tự.

**Backend liên quan:** `heartbeat.py`'s nhánh gemini đổi để trả được NHIỀU pending prompt/lần (field `pendingPrompts`) — xem CHANGELOG.md của repo `ToolSub` gốc (2026-07-24, "Backend: heartbeat Gemini giao nhiều pending prompt/lần").

**Mặc định `gemini_max_concurrent_tabs=1`** (profile hiện có, chưa từng set field này) — hành vi CŨ giữ nguyên 100%, không có gì tự đổi cho profile đang chạy.

**CHƯA verify trên browser thật** (môi trường phát triển không chạy được Chrome/Gemini) — đã verify: `_run_gemini_loop_concurrent`/`_gemini_slot_step`/`_gemini_open_tab`/mọi hàm mới tồn tại đúng trên class qua `import server.worker` (venv thật, đủ dependency); toàn bộ `client_tool` (`server/`, `gui/`, `main.py`, `selenium_flow.py`) qua `python -m compileall` sạch. Cần user tự tạo 1 profile Gemini, đặt "Số tab đồng thời" = 2-3, chạy thật với vài prompt đang chờ trong hàng đợi để xác nhận: (1) mở đúng N tab, (2) round-robin chuyển tab đúng nhịp, (3) mỗi tab ra kết quả đúng conversation của nó (không lẫn lộn giữa các tab), (4) chỉ nhận lô mới sau khi lô cũ xong hết.

---

### 2026-07-21 — Thêm setting tự luân chuyển project VEO3 khi đầy (mặc định ngưỡng 300 item)

| File | Thay đổi |
|------|---------|
| `server/config.py` | `_DEFAULT_SERVER_SETTINGS` thêm `max_project_media_items` (mặc định 300, 0=tắt). |
| `server/local_settings.py` | `_clamp()` — validate int ≥0 cho setting mới. |
| `server/worker.py` | `SeleniumFlowWorker.__init__` thêm `_last_project_media_count` (khởi tạo 0). `_reconcile_project_media()` — ghi lại `len(media_list)` mỗi lần đọc `projectInitialData` thành công. Hàm mới `_rotate_project_if_full()` — nếu `_last_project_media_count ≥ max_project_media_items`, điều hướng về `FLOW_PROJECT_URL` (`https://labs.google/fx/vi/tools/flow`) rồi bấm "New project"/"Dự án mới" (tái dùng `_click_new_project_button`/`_wait_for_project_url` đã có), cập nhật `project_url` của profile qua `pm.update()`. Gọi từ `_wait_and_reconcile_tasks()` — CHỈ khi `pending` rỗng (toàn bộ batch đã reconciled xong), tránh làm hỏng fallback tile-polling của các task còn dở dang (fallback đó dựa vào DOM/driver hiện tại, sẽ tìm nhầm project nếu vừa chuyển). |
| `gui/pages/settings_page.py` | Thêm field "Số item tối đa 1 project trước khi tự tạo project mới (0=tắt)" vào `_FIELD_DEFS` (dùng chung UI generic sẵn có, không cần code riêng). |

**Lý do:** theo yêu cầu user "thêm setting số task hoàn thành tối đa 1 project, đạt ngưỡng thì chuyển sang labs.google/fx/vi/tools/flow rồi bấm tạo project mới — cụ thể check reconcile mặc định đã có hơn 300 item thì tạo project mới". Đếm số item qua CHÍNH danh sách media đã đọc lúc reconcile (`_dom_fetch_project_media()`/`flow.projectInitialData`, cơ chế có sẵn từ §5.7/11.16) — không cần gọi API/reload riêng chỉ để đếm.

**Verify:** test trực tiếp `_rotate_project_if_full()` (mock `driver`/`_click_new_project_button`/`_wait_for_project_url`/`pm.update`, không mở Chrome thật) — 4 kịch bản: (1) count=250 < ngưỡng 300 → không chuyển; (2) count=305 ≥ ngưỡng → chuyển đúng, `project_url` cập nhật, counter reset về 0; (3) ngưỡng=0 (tắt) + count=1000 → không chuyển; (4) count đủ ngưỡng nhưng KHÔNG tìm thấy nút "New project" → giữ nguyên project cũ, không crash. Validate `_clamp()`: âm→0, không phải số→default 300. `py_compile`/`pyflakes` pass cả 4 file. **CHƯA verify trên browser thật** — cần user chạy 1 project đủ lớn (hoặc hạ ngưỡng tạm thời để test nhanh) để xác nhận hành vi chuyển project thật trên labs.google.

---

### 2026-07-23 — Fix đếm ẢNH ĐÍNH KÈM đếm NHẦM `data-card-open` ở nơi khác trên trang Flow

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_count_attached_refs()` — bỏ query THẲNG trên toàn `document`, đổi sang SCOPE vào đúng khung composer: bắt đầu từ ô nhập liệu prompt (`div[role="textbox"][data-slate-editor="true"]`), đi ngược lên tổ tiên (`parentElement`, tối đa 8 cấp) tới khi gặp container CŨNG chứa nút "add_2" (nút mở picker đính ảnh — luôn nằm CÙNG khung composer với ô nhập liệu), dừng ở đó rồi mới đếm `[data-card-open]` BÊN TRONG container này. |

**Lý do:** user cảnh báo đúng "toàn bộ HTML của Flow còn có các `data-card-open` khác" — bản đầu (2026-07-21) query thẳng `document.querySelectorAll('button[data-card-open] img...')` trên TOÀN TRANG, có thể đếm nhầm bất kỳ phần tử nào khác trên labs.google dùng chung attribute `data-card-open` (vd card trong khu vực kết quả/gallery bên dưới composer) — không riêng gì chip ảnh đính kèm prompt. Không hardcode "đi lên đúng N cấp cha" (dễ vỡ nếu Google chèn/bớt 1 lớp div bọc) — dừng theo TÍN HIỆU ngữ nghĩa (đã tới đúng khung composer, nhận biết qua có nút `add_2` bên trong) an toàn hơn nhiều, cùng pattern `closest()`-tìm-theo-tín-hiệu đã dùng ở B4 (tìm gallery container từ search input).

**Verify:** dựng lại ĐÚNG cấu trúc composer thật (chip ảnh + textbox + nút add_2, theo HTML user đã cung cấp) kèm 1 phần tử `data-card-open` GIẢ LẬP nằm NGOÀI composer (mô phỏng card khác trên trang) — chạy qua Chrome thật (headless), trích XUẤT ĐÚNG đoạn JS từ file nguồn (không gõ lại tay, tránh lệch pha test/production): (A) selector CŨ đếm dư 3 (dính decoy) trong khi selector MỚI đếm đúng 2 (chỉ chip thật); (B) composer chưa có ảnh nào (0 chip thật + decoy bên ngoài) → đếm đúng 0 (không bị decoy làm sai); (C) không tìm thấy composer/textbox nào → trả về 0 an toàn (không lỗi). `py_compile`/`pyflakes` pass.

---

### 2026-07-23 — Bỏ bước "đợi picker đóng" (tín hiệu gián tiếp) — bấm Add to Prompt xong chờ cố định 1s rồi validate thẳng

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_attach_one_reference()` — bỏ hẳn vòng lặp "đợi picker đóng" (dựa vào `_find_picker()`, tìm div còn chứa text "Thêm vào câu lệnh" biến mất) + `self._sleep(0.5)` sau đó. Thay bằng `self._sleep(1)` cố định ngay sau khi click, rồi vào thẳng bước validate (`_count_attached_refs()`, poll 20s đã có sẵn từ trước — KHÔNG đổi). Xoá hàm `_find_picker()` (không còn nơi nào gọi). |

**Lý do:** theo yêu cầu user "check image đính kèm chưa chuẩn, hãy fix khi bấm Add to Prompt chờ 1 giây rồi check đủ image đính kèm chưa". `_find_picker()` là tín hiệu GIÁN TIẾP (kiểm tra text "Thêm vào câu lệnh" biến mất khỏi DOM) — không trực tiếp phản ánh ảnh đã đính kèm hay chưa, và không cần thiết: `button[data-card-open]` (chip đính kèm cạnh ô nhập liệu) là phần tử HOÀN TOÀN KHÁC gallery grid trong picker (gallery dùng `[data-index]`, xem B4) nên đếm đúng dù picker còn mở hay đã đóng — validate thẳng bằng chính tín hiệu quan trọng nhất (ảnh có trong prompt chưa) đáng tin hơn hẳn 1 bước trung gian không chắc chắn. Giữ nguyên độ kiên nhẫn 20s ở bước validate chính (đã fix ngày 2026-07-21 vì lý do khác — xem mục dưới) — 1s cố định chỉ là khoảng nghỉ ngắn cho Angular xử lý xong click, không thay thế patience của validate.

**Verify:** `py_compile`/`pyflakes` pass (xác nhận `_find_picker()` không còn được gọi ở đâu trước khi xoá). **CHƯA verify trên browser thật** — cần user chạy lại task để xác nhận hành vi mới ổn định.

---

### 2026-07-21 — Fix ngay bug do chính validate mới thêm gây ra: poll ~3s quá ngắn → false-negative → upload trùng 3 ảnh

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_attach_one_reference()` — poll validate tăng từ ~3s (15×0.2s) → 20s (100×0.2s). Thêm lưới an toàn: kiểm tra `_count_attached_refs() >= expected_count` NGAY ĐẦU hàm — nếu đã đủ (do attempt trước thật ra đã thành công nhưng validate poll của nó hết hạn trước khi thấy) thì trả `True` luôn, KHÔNG upload thêm bản mới. |

**Lý do — bug thật user báo bằng log chỉ vài giờ sau khi thêm validate (mục ngay dưới):** task chỉ yêu cầu 1 ảnh đính kèm (`images=1`), user xác nhận trực tiếp bằng mắt là ảnh ĐÃ đính kèm thành công, nhưng log cho thấy validate liên tục báo "chưa xác nhận đính kèm" và tự retry, kết quả cuối cùng **3 ảnh** bị đính kèm (3 UUID khác nhau — `206b8b44...`/`db05f666...`/`b1614025...` — xác nhận đây là 3 LẦN UPLOAD THẬT, không phải lỗi đếm). Đối chiếu timestamp log: bước upload NGAY TRƯỚC ĐÓ ("inject via DataTransfer" → "uploaded ✔") đã mất tới ~12-15s dưới cùng điều kiện "2 Chrome+Selenium tranh chấp CPU" đã biết (cùng lý do các timeout khác trong hàm này từng phải tăng 10s→20s/5s→7.5s/30s→40s) — nhưng validate mới thêm (mục dưới) chỉ poll ~3s, LUÔN hết hạn trước khi Angular kịp render chip đính kèm. Kết quả: ảnh đính kèm THẬT SỰ THÀNH CÔNG vẫn bị coi là thất bại → code tự lặp lại TOÀN BỘ search/upload → tải thêm 1 bản MỚI (Google gán UUID mới mỗi lần) mà KHÔNG xoá bản cũ — false-negative gây hại thật (tạo dữ liệu sai), không chỉ chậm.

**Verify:** đối chiếu trực tiếp timestamp trong log thật user cung cấp (upload mất ~12-15s, validate cũ chỉ chờ 3s) — kết luận rõ ràng validate cũ không đủ thời gian, không cần đoán thêm. `py_compile`/`pyflakes` pass. **CHƯA verify lại trên browser thật sau fix lần 2 này** — cần user chạy lại đúng task từng lỗi (hoặc bất kỳ task `imageToImage`/`imageToVideo` nào) để xác nhận chỉ còn đúng 1 ảnh đính kèm, không còn tự nhân bản.

---

### 2026-07-21 — `_dom_upload_images()`: validate ảnh THẬT SỰ đính kèm vào prompt trước khi tiếp tục, tự retry search/upload nếu không

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_dom_upload_images()` — thêm `_count_attached_refs()` (đếm `button[data-card-open] img[src*="getMediaUrlRedirect"]` — chip ảnh THẬT SỰ đính kèm vào prompt, khác hẳn gallery picker). Tách toàn bộ B1-B5 (mở picker → chọn tab → search → upload/chọn → bấm "Thêm vào câu lệnh") thành hàm lồng `_attach_one_reference(img, idx, expected_count)`, cuối hàm poll `_count_attached_refs()` (~3s) và trả `True`/`False` thay vì luôn coi là thành công. Vòng lặp ngoài (`for i, img in enumerate(tmp_files)`) giờ thử tối đa `_MAX_ATTACH_ATTEMPTS=3` lần — click xong mà prompt CHƯA đủ ảnh (hoặc lỗi cấu trúc như không tìm thấy nút) thì lặp lại TOÀN BỘ search/upload cho ảnh đó, hết 3 lần vẫn không đủ mới raise lỗi rõ ràng. |

**Lý do:** user cung cấp HTML thật của prompt sau khi 1 ảnh đính kèm thành công (`<button data-card-open="false"><img src="/fx/api/trpc/media.getMediaUrlRedirect?name=...">...<i>cancel</i></button>`, nằm cạnh ô nhập liệu) và báo lỗi: "upload image thành công nhưng nhấn Add to Prompt đôi khi lại không add ảnh vào prompt". Code cũ coi bấm xong = thành công (chỉ đợi picker đóng rồi log "attached ✔"), không hề kiểm tra ảnh có THẬT SỰ xuất hiện trong prompt hay không — cùng lớp bug "trusted click chạy không lỗi nhưng không có tác dụng thật" đã gặp ở nơi khác trong file này (vd nút Gửi Gemini). `data-card-open` là attribute semantic ổn định (không phải class styled-components hash hay đổi).

**Verify:** load ĐÚNG HTML mẫu user cung cấp vào Chrome thật (`data:text/html`, headless) — selector đếm đúng 1 ảnh; test thêm 0 ảnh (prompt rỗng) → đếm đúng 0; 2 ảnh → đếm đúng 2. `py_compile`/`pyflakes` pass sau khi tách hàm (bắt được lỗi thật lúc tách — 4 chỗ `{i+1}` sót lại tham chiếu biến vòng lặp ngoài `i` thay vì tham số `idx`, đã sửa cả 4 và xác nhận `pyflakes` sạch). **CHƯA verify được retry thật trên labs.google** — cần user chạy 1 task thật, tốt nhất là kịch bản trước đây từng gặp lỗi "add xong không thấy ảnh" để xác nhận nó tự retry và cuối cùng thành công/log rõ nguyên nhân nếu vẫn thất bại.

---

### 2026-07-21 — `_dom_upload_images()` B5: chờ thêm 2s sau khi nút "Thêm vào câu lệnh" click được rồi mới click

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_dom_upload_images()` — sau khi vòng poll (tối đa 4s) tìm thấy nút "Thêm vào câu lệnh"/"Add to Prompt" đã render + có client rect, thêm `self._sleep(2)` trước khi `_cdp_click_el(add_btn)` (trước đây click NGAY khi vừa tìm thấy). |

**Lý do:** theo yêu cầu user, cho luồng upload ảnh qua DataTransfer (Case 2 — ảnh chưa có sẵn trong gallery) thêm thời gian ổn định trước khi click — nút vừa render/hết điều kiện lọc (`getClientRects().length>0`) không đảm bảo Angular đã gắn xong handler xử lý file vừa inject qua `DataTransfer`, click ngay lập tức có thể rơi đúng lúc component chưa sẵn sàng hoàn toàn.

---

### 2026-07-21 — Gemini auto-scale: fix race đóng nhầm profile VỪA được giao task trực tiếp

| File | Thay đổi |
|------|---------|
| `server/state.py` | Thêm `_gemini_idle_since` (dict, profile_id → thời điểm đầu tiên thấy "có vẻ rảnh") + `_GEMINI_CLOSE_GRACE_SECS=20`. |
| `server/dispatcher.py` | `_auto_scale_gemini_tick()` — nhánh đóng profile khi hết backlog giờ chỉ đóng THẬT sau khi profile đã "rảnh liên tục" ≥20s (trước đây đóng NGAY ở tick đầu tiên thấy `has_backlog=False` + `status!='processing'`). Nhánh có backlog (`has_backlog=True`) clear sạch `_gemini_idle_since` trước khi return — đảm bảo đồng hồ grace luôn tính từ đúng lần backlog gần nhất về 0, không cộng dồn nhầm với khoảng rảnh không liên quan trước đó. |

**Lý do — bug thật user báo "task gemini sau khi update cứ chạy 1 tí là dừng... 20/80 chạy dc tí 30/80 là ko truyền task nữa":** `_dispatch_or_queue_gemini_prompt()` (backend) giao prompt TRỰC TIẾP cho máy rảnh bằng cách set `machines_media.pending_prompt` — KHÔNG qua bảng `gemini_pending_requests` (bảng đó chỉ dùng khi HẾT máy rảnh). Máy nhận việc chỉ tự đổi `selenium_profiles.status` → `'processing'` ở **lần heartbeat KẾ TIẾP của chính worker đó** (`_run_task_gemini()`, cách nhau tối đa `POLL_INTERVAL=8s`). Dispatcher tick (chu kỳ 10s) có thể rơi đúng vào khe hở này: `gemini_pending_requests` rỗng (dispatch trực tiếp, không qua hàng đợi) → `has_backlog=False`; máy vẫn đọc `status='idle'` (chưa kịp đổi) → code CŨ coi là rảnh thật và **đóng ngay** — giết chết Chrome đang cầm đúng task vừa được giao, mất vĩnh viễn (task đó chưa từng nằm trong `gemini_pending_requests` nên không có gì để retry). Với pipeline "Chạy Auto tất cả" (nhiều clip × 4 bước, liên tục giao prompt trực tiếp cho máy rảnh), khe hở này bị trúng thường xuyên — mỗi lần trúng, task mất, bước tiếp theo của chain (chờ callback) không bao giờ được gọi → cả pipeline đứng yên (khớp đúng triệu chứng "chạy 1 tí là dừng, không truyền task nữa").

**Fix:** chỉ đóng profile sau khi đã "trông có vẻ rảnh" LIÊN TỤC qua `_GEMINI_CLOSE_GRACE_SECS=20` (hơn 1 chu kỳ heartbeat của worker, đủ thời gian để nó tự cập nhật `status='processing'` nếu THẬT SỰ vừa nhận việc). Cùng pattern debounce đã dùng cho "Server offline" false-positive ở `main_window.py` (xem §11.11).

**Verify:** mô phỏng đúng race qua dispatcher.py thật (mock `_stop_worker`/`pm.list`) — tick 1 (backlog=0, máy vừa "nhận việc" nhưng status còn `idle`) → KHÔNG đóng; tick 2 (+10s, máy khác đã kịp heartbeat → `processing`, máy còn lại vẫn `idle` nhưng mới 10s) → KHÔNG đóng; tick 3 (+15s nữa, tổng 25s vẫn `idle` thật) → ĐÓNG đúng. Verify riêng: backlog xuất hiện lại giữa chừng → `_gemini_idle_since` bị clear sạch (không cộng dồn nhầm). `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật với pipeline auto thật** — cần user chạy lại "Chạy Auto tất cả" để xác nhận không còn dừng giữa chừng.

---

### 2026-07-21 — VEO3 dispatcher: cấp "ngân sách" slot RIÊNG theo từng task_mode thay vì gộp chung `total`

| File | Thay đổi |
|------|---------|
| `server/dispatcher.py` | `_veo3_dispatcher_tick()` — bản fix trước cùng ngày ("không mở nhiều profile hơn số task đang chờ") dùng `slots_free = min(max_conc, pending_total) - running` với `pending_total` gộp chung image+video — CHƯA đủ chính xác khi nhiều profile CÙNG task_mode cạnh tranh 1 số lượng task nhỏ của riêng mode đó (vd 1 task ảnh + 3 task video, 2 profile `image_only` đều "khớp" nhị phân vì `imageTotal=1>0`, dù chỉ có 1 task ảnh thật). Đổi hẳn sang cấp ngân sách RIÊNG: `image_budget=imageTotal`, `video_budget=videoTotal` (trừ phần các profile ĐANG CHẠY cùng task_mode đã tiêu thụ) — profile `image_only`/`video_only` đang `waiting` chỉ được thăng cấp trong giới hạn ngân sách CỦA ĐÚNG loại đó; profile `task_mode='all'` (linh hoạt nhận cả 2 loại) chỉ được lấy từ phần CÒN LẠI sau khi 2 nhóm trên đã lấy phần của mình. |

**Lý do:** user làm rõ thêm yêu cầu "đối với VEO cũng phải phân biệt mode, ví dụ chỉ có task image thì các profile không nhận task image thì không mở" — phần "profile sai mode không mở" ĐÃ đúng từ trước (`_profile_veo3_eligible()`), nhưng đọc lại kỹ bản fix "không mở thừa" cùng ngày phát hiện nó vẫn dùng `total` gộp chung nên CHƯA xử lý đúng trường hợp nhiều profile CÙNG 1 mode cạnh tranh cùng 1 lượng task nhỏ của riêng mode đó — cùng lớp bug với gemini (đã fix) nhưng ở dạng khác (cạnh tranh NỘI BỘ 1 mode, không phải giữa 2 mode khác nhau).

**Verify:** chạy trực tiếp qua dispatcher.py thật (mock `_start_worker`/`pm.list`/`pm.get`) — kịch bản mới: 1 task ảnh + 3 task video, 2 profile `image_only` + 1 profile `video_only`, `max_concurrent=4` → CHỈ 1 profile `image_only` mở (đúng 1 task ảnh), `video_only` mở đủ (3 task video dư dả) — trước fix sẽ mở cả 2 `image_only`. Chạy lại 3 kịch bản đã verify trước đó (không hồi quy): 3 profile `all` + `max_conc=2` + 1 task → mở đúng 1; setup thật của user (1 profile `video_only` + 142 task ảnh + 0 video) → không mở gì; backlog đủ lớn (100 task, `max_conc=2`) → vẫn mở đủ 2, không hồi quy. `py_compile`/`pyflakes` pass.

---

### 2026-07-21 — Gemini: `send_ready_timeout` hardcode 60s quá ngắn cho video lớn, dùng chung `attach_timeout`

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_gemini_fill_and_submit(prompt, send_ready_timeout=60)` — timeout chờ nút Gửi hết `aria-disabled` trước đây HARDCODE 60s, tách biệt hoàn toàn với `attach_timeout` (đã configurable per-profile, mặc định 180s) dùng cho bước chờ TILE đính kèm xuất hiện ngay trước đó. Đổi default `60`→`180`, và `_run_task_gemini()` giờ truyền thẳng `send_ready_timeout=attach_timeout` thay vì để mặc định. |

**Lý do:** user báo lỗi thật `"Timeout 60s: nút Gửi không sẵn sàng."` khi gửi kèm video, kèm quan sát trực tiếp "có khi 1 video upload đến 3 phút mới có thể gửi prompt". 2 bước chờ ("tile đính kèm hiện ra" ở `_gemini_attach_file` và "nút Gửi hết disabled" ở `_gemini_fill_and_submit`) về bản chất là 2 GIAI ĐOẠN của CÙNG 1 việc chờ Gemini xử lý xong file — tile có thể hiện ra sớm nhưng Gemini vẫn cần thêm thời gian xử lý phía server trước khi cho phép gửi, đặc biệt với video lớn. Bước 1 đã có timeout configurable 180s (đủ theo báo cáo user), nhưng bước 2 vẫn hardcode 60s riêng — chính là nguyên nhân timeout dù bước 1 đã qua. Fix bằng cách dùng LẠI `attach_timeout` cho cả 2 bước thay vì thêm setting mới — không cần migration/GUI field mới, user chỉnh 1 chỗ (ProfileDialog "Attach timeout") là áp dụng cho cả 2 giai đoạn. Không ảnh hưởng prompt KHÔNG có video đính kèm — nút Gửi sẵn sàng gần như ngay lập tức nên vòng poll thoát sớm, timeout dài hơn chỉ kéo dài thời gian chờ ở nhánh LỖI (hiếm khi xảy ra), không tốn thời gian ở nhánh thành công bình thường.

**Verify:** `py_compile`/`pyflakes` pass. **CHƯA verify trên browser thật** — cần user chạy lại 1 task video lớn (loại trước đây từng timeout ở mốc ~60s) để xác nhận không còn timeout sớm.

---

### 2026-07-21 — Auto-scale VEO3/Gemini: không mở nhiều profile hơn số task ĐANG THẬT SỰ chờ

| File | Thay đổi |
|------|---------|
| `server/dispatcher.py` | `_veo3_dispatcher_tick()` — `slots_free` trước đây chỉ trừ theo `max_concurrent_veo3_profiles` (`max_conc - len(running_veo3)`), không quan tâm còn bao nhiêu task THẬT trong `pending_by_mode`. Giờ `slots_free = min(max_conc, pending_total) - len(running_veo3)` — nếu backlog thật chỉ có 1 task nhưng có ≥2 profile cùng khớp `task_mode` và `max_concurrent` cho phép 2, chỉ mở đúng 1 profile thay vì cả 2. `_auto_scale_gemini_tick()` — cùng fix, `need = min(max_conc, total_pending) - len(running_pids)` thay vì `max_conc - len(running_pids)`. |

**Lý do:** user báo "khi có 1 task mới của gemini thì tất cả profile VEO, gemini đều bật và khi có task VEO thì gemini cũng bật cùng — tôi không muốn những profile ko liên quan lại start mà không có task được giao". Điều tra bằng cách chạy THẬT `_auto_scale_veo3_tick()`/`_auto_scale_gemini_tick()` (mock `_start_worker` để không mở Chrome thật) với ĐÚNG dữ liệu profile+backlog lấy trực tiếp từ DB tại thời điểm báo lỗi (1 profile veo3 `task_mode=video_only`, 2 profile gemini, 142 task `textToImage` đang pending, `gemini_pending_requests` rỗng) — kết quả: code auto-scale HIỆN TẠI hoàn toàn CÁCH LY đúng giữa 2 loại (`pending_by_mode` chỉ đọc `tasks_media_flow`, `gemini/pending_count` chỉ đọc `gemini_pending_requests`; filter theo `worker_mode` không chồng lấn) — không tái hiện được chiều "VEO bật do gemini" hay "gemini bật do VEO". NHƯNG phát hiện bug THẬT, khác hướng: `local_settings.json` của máy test có `max_concurrent_gemini_profiles=2` và ĐÚNG 2 profile gemini tồn tại — nên dù chỉ 1 prompt gemini pending, code CŨ vẫn mở CẢ 2 profile (vì chỉ cap theo `max_conc`, không cap theo số việc thật có) — khớp chính xác phần "tất cả profile gemini đều bật" mà user mô tả, và giải thích hợp lý nhất cho cảm giác "start mà không có task được giao" (1 trong 2 máy mở ra chắc chắn không có gì làm). Cùng lớp bug tồn tại ở phía VEO3 (slot-promotion không cap theo pending thật) dù chưa tái hiện được với đúng 1 profile veo3 hiện có của user — sửa luôn cho nhất quán, không có rủi ro hồi quy (đã verify: khi backlog ≥ max_concurrent, hành vi mở profile KHÔNG đổi so với trước).

**Verify:** chạy trực tiếp qua dispatcher.py THẬT (mock `_start_worker`/`pm.list`, không mở Chrome) — trước fix: 1 gemini task pending → mở cả 2 profile gemini; sau fix: 1 gemini task → mở đúng 1; 2 gemini task pending → mở đúng cả 2 (không bị siết quá tay khi đủ việc). Tương tự cho VEO3: 3 profile `task_mode=all` + `max_concurrent_veo3_profiles=2` + 1 task pending → trước fix mở 2, sau fix mở đúng 1. `py_compile`/`pyflakes` pass. **CHƯA verify được liệu 2 chiều "VEO bật do gemini"/"gemini bật do VEO" mà user mô tả có còn tái diễn hay không sau fix này** — vì không tái hiện được trong test (khả năng cao là user thấy 2 loại backlog tồn tại ĐỘC LẬP cùng lúc rồi cả 2 auto-scale tick — chạy chung 1 vòng lặp mỗi 10s — cùng mở máy trong 1 khoảng ngắn, trông giống nhân quả dù thực ra không liên quan) — cần user quan sát lại sau khi cập nhật, đối chiếu đúng lúc chỉ CÓ backlog 1 loại xem loại còn lại có tự mở hay không.

---

### 2026-07-21 — Fix mapping sub-tab NGƯỢC cho `imageToVideo` — phải là "Thành phần", không phải "Khung hình"

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_dom_configure()` — 2 dict `main_tab_icon`/`sub_tab_text`: bỏ `'componentsToVideo'` (KHÔNG phải mode hợp lệ — `tasks_media_flow.mode` là ENUM chỉ có `textToImage/textToVideo/imageToImage/imageToVideo/frameToVideo`, xác nhận trực tiếp qua DB); `'imageToVideo'` đổi từ `'Khung hình'` → `'Thành phần'` (user xác nhận trực tiếp qua HTML dropdown thật); thêm `'frameToVideo'` vào cả 2 dict (mode hợp lệ nhưng trước đây bị bỏ sót hoàn toàn — không tự chuyển tab nào). |

**Lý do:** user dán HTML 2 tab thật trong popup cấu hình video của Flow — `"Khung hình"` (icon `crop_free`) và `"Thành phần"` (icon `chrome_extension`) — xác nhận: với mode "chỉ ảnh đầu" (1 ảnh tham chiếu) phải chọn tab **"Thành phần"**, không phải "Khung hình" như code cũ. Tra DB xác nhận mức độ nghiêm trọng: **1457/1457 video task hiện có đều dùng `mode='imageToVideo'`** (100% traffic thật) — nghĩa là bug này đã khiến MỌI task video xử lý qua client_tool DOM mode chọn NHẦM tab suốt từ trước tới giờ. Đồng thời phát hiện `'componentsToVideo'` (xuất hiện trong cả 2 dict) không hề là 1 giá trị `mode` hợp lệ trong DB — dead code, có thể do nhầm lẫn với 1 phiên bản UI/kế hoạch cũ của Flow.

**Lưu ý:** nhánh `'imageToVideo': 'Thành phần'` đã được user XÁC NHẬN TRỰC TIẾP. Nhánh `'frameToVideo': 'Khung hình'` là suy luận hợp lý theo tên (mode "ảnh đầu+ảnh cuối" khớp tab tên "Frame") — **CHƯA có task thật nào dùng mode này để verify** (`frame_mode='first_last_frame'` chưa từng tạo ra task với mode set đúng qua path hiện có, xem `generate_video_tasks()` trong `backend/routes/projects.py` — 1 bug KHÁC, RIÊNG BIỆT, chưa xử lý: hàm này INSERT task video mà không set cột `mode` tường minh, rơi về DEFAULT ENUM `'textToImage'` — đã thấy đúng 5 task video mang `mode='textToImage'` trong DB khớp giả thuyết này).

**Verify (2026-07-21):** `py_compile` PASS. Xác nhận logic `find_btn_in_popup()` (KHÔNG đổi — chỉ đổi giá trị dict tra cứu) vẫn khớp đúng cả 2 tab dù `textContent` có lẫn text icon ligature ở đầu (`"crop_freeKhung hình"`/`"chrome_extensionThành phần"`, test bằng Node.js với đúng HTML thật) — an toàn vì 2 tên tab này KHÔNG phải substring của nhau (khác bug model dropdown ở entry trên), không cần sửa thuật toán match, chỉ cần sửa dữ liệu dict.

---
---

### 2026-07-21 — Fix chọn NHẦM model DOM (Flow UI) khi 1 label là prefix của label khác

| File | Thay đổi |
|------|---------|
| `server/worker.py` | `_dom_configure()` — logic tìm menu-item model trong dropdown Flow: đổi từ `.find()` (lấy phần tử ĐẦU TIÊN khớp `textContent.includes(model)`) sang: lọc TẤT CẢ candidate khớp substring trước, rồi ưU TIÊN candidate khớp CHÍNH XÁC (sau khi bỏ tiền tố không phải chữ/số như emoji `🍌`) — chỉ fallback về candidate substring đầu tiên nếu không có khớp chính xác nào. |

**Lý do:** user dán HTML dropdown model ẢNH thật (`🍌 Nano Banana Pro` / `🍌 Nano Banana 2` / `🍌 Nano Banana 2 Lite`) và dropdown model VIDEO thật (`Omni Flash` / `Veo 3.1 - Lite` / `Veo 3.1 - Fast` / `Veo 3.1 - Quality` / `Veo 3.1 - Lite [Lower Priority]`) — phát hiện danh sách model hiện có ÍT NHẤT 2 cặp label mà 1 cái là PREFIX của cái kia: `"Nano Banana 2"` là prefix của `"Nano Banana 2 Lite"`; `"Veo 3.1 - Lite"` là prefix của `"Veo 3.1 - Lite [Lower Priority]"`. Code cũ dùng `.find()` + `textContent.includes(model)` — nếu DOM đổi thứ tự (Google có thể đổi bất cứ lúc nào) sao cho label DÀI HƠN xuất hiện TRƯỚC label NGẮN mà nó chứa, `.find()` sẽ chọn NHẦM ngay lập tức, không có cách nào phát hiện lỗi (không throw exception, chỉ chọn sai model một cách âm thầm). Cùng lớp bug đã gặp + sửa ở `_gemini_select_model()` (so sánh text rút gọn/đầy đủ trên dropdown Gemini, xem CHANGELOG trước).

**Verify (2026-07-21):** `py_compile` PASS. Test bằng Node.js (logic JS thuần, không cần browser thật) mô phỏng ĐÚNG cấu trúc DOM user cung cấp: (1) dropdown ảnh 3 option — cả 3 model chọn đúng theo yêu cầu; (2) dropdown video 5 option (đúng thứ tự DOM thật: Omni Flash, Lite, Fast, Quality, Lite[Lower Priority]) — cả 5 model chọn đúng; (3) tái hiện TRỰC TIẾP bug của code CŨ bằng 1 thứ tự DOM giả định hợp lý khác (Lite[LP] đứng trước Lite) — xác nhận code cũ THẬT SỰ chọn nhầm "Veo 3.1 - Lite [Lower Priority]" khi được yêu cầu chọn "Veo 3.1 - Lite", còn code MỚI (đã test lại với cùng input) chọn đúng.

**Bug thứ 2 phát hiện lúc viết test THẬT bằng Chrome (`tests/_test_model_select.py`, cùng ngày):** test Node.js ở trên dùng chuỗi text thuần, không phản ánh đúng 100% DOM thật — chạy lại bằng Chrome thật với HTML tái tạo ĐÚNG cấu trúc dropdown VIDEO (mỗi `[role="menuitem"]` còn chứa `<i class="google-symbols">volume_up</i>` — icon ligature, chữ THẬT "volume_up" nằm CHUNG element với label) phát hiện: bản fix đầu (chỉ strip tiền tố `[^A-Za-z0-9]+`) KHÔNG đủ — "volume_up" bắt đầu bằng CHỮ CÁI nên regex không strip được, khiến `stripped` luôn là `"volume_upVeo 3.1 - Lite..."` — KHÔNG BAO GIỜ khớp chính xác cho BẤT KỲ model video nào, vô hiệu hoá hoàn toàn nhánh ưu tiên khớp đúng cho đúng cặp cần fix nhất (`"Veo 3.1 - Lite"` vs `"... [Lower Priority]"`) — quay lại y hệt bug gốc cho video. Tách hằng số `MODEL_MATCH_JS` (module-level, `server/worker.py`) — dùng CHUNG bởi `_dom_configure()` VÀ test, đảm bảo test luôn kiểm tra đúng code production. Fix triệt để: thêm `labelOf(el)` — `cloneNode` rồi xoá HẾT thẻ `<i>` con trước khi lấy `textContent`, loại bỏ hoàn toàn nhiễu từ icon ligature (bất kể icon gì, không cần liệt kê trước) — áp dụng nhất quán cho CẢ bước lọc substring LẪN bước so khớp chính xác.

**Verify v2 (2026-07-21):** `py_compile` PASS. `tests/_test_model_select.py` chạy THẬT bằng Chrome (không cần login Google, chỉ cần 1 Chrome instance) — 11/11 case PASS: 3 model ảnh, 5 model video (đúng thứ tự DOM thật, gồm đúng cặp `Veo 3.1 - Lite`/`Lite [Lower Priority]` từng gây bug), 3 case tái hiện kịch bản đảo thứ tự DOM. Phát hiện + sửa xong TRƯỚC khi báo cáo — không có case nào bị bỏ sót giữa 2 lần chạy test.

---

### 2026-07-21 — Hiển thị version app + tự tăng version mỗi lần push

| File | Thay đổi |
|------|---------|
| `VERSION` (mới) | File version dạng `MAJOR.MINOR.PATCH`, seed `1.0.0`. |
| `server/config.py` | Đọc `VERSION` lúc import, expose `APP_VERSION` — fallback `'0.0.0'` nếu thiếu file/lỗi đọc, không chặn app khởi động. |
| `gui/sidebar.py` | Hiện version dưới logo: `"Selenium Worker Console · v{APP_VERSION}"`. |
| `.git/hooks/pre-push` (mới, **KHÔNG version-controlled** — chỉ tồn tại local trên máy đang push) | Tự tăng PATCH version + tạo commit MỚI (không amend) mỗi lần `git push` thật sự có gì để gửi; chủ động `exit 1` huỷ lần push hiện tại để bắt chạy lại — lần sau commit bump đi kèm bình thường. Bỏ qua nếu: không có gì mới để push, hoặc tip hiện tại đã là commit bump từ lần trước (tránh bump chồng khi retry). |

**Lý do đặt hook thay vì amend:** dự án có nguyên tắc "luôn tạo commit mới, không amend" (tránh rewrite lịch sử đã có thể được push/chia sẻ) — pre-push hook amend trực tiếp tip commit đang push tuy khả thi (nhiều dự án khác làm vậy) nhưng vi phạm nguyên tắc đó. Giải pháp: bump ở 1 commit MỚI, rồi huỷ push lần đầu bắt chạy lại — người dùng chạy `git push` 2 lần cho lần push CÓ thay đổi mới (lần 2 luôn thành công ngay, không cần thao tác gì thêm ngoài gõ lại lệnh), nhưng lịch sử luôn sạch, không commit nào bị sửa sau khi tạo.

**Vì sao hook không track trong git:** `.git/hooks/` không bao giờ được git tự động đồng bộ qua clone/pull (giới hạn/tính năng chuẩn của git) — nhưng điều này CHẤP NHẬN ĐƯỢC vì chỉ máy đang PUSH (thường là máy dev, không phải máy worker — máy worker chỉ `git pull` qua `start.bat`, không bao giờ push) mới cần hook này.

**Verify (2026-07-21):** `py_compile` PASS cho `config.py`/`sidebar.py`. Test trực tiếp `APP_VERSION` load đúng `'1.0.0'` từ file, và fallback đúng `'0.0.0'` khi xoá tạm file VERSION. Test hook bằng 1 bare repo LOCAL tạm (không đụng remote `origin` thật) mô phỏng đúng 3 tình huống: (1) push có commit mới → hook bump `1.0.0→1.0.1`, tạo commit "Bump version to v1.0.1 [auto]", huỷ push (exit 1); (2) chạy `git push` lại ngay sau đó → không bump chồng (nhận diện đúng tip là commit bump), push thành công, xác nhận remote test nhận đủ commit; (3) push khi không có gì mới (đã up-to-date) → không bump, "Everything up-to-date". Xác nhận `git remote -v` của repo thật không đổi gì sau khi test (chỉ dùng path tới bare repo tạm, không phải tên remote `origin`).

---
