# DOM mode — Luồng chạy đầu đến cuối cho tất cả case

> ⚠️ **CẬP NHẬT 2026-09-03 — một phần tài liệu này ĐÃ LỖI THỜI.** Google đổi
> app sang Angular `flow.google.com`: mọi selector DOM đã viết lại (xem
> `CLAUDE.md` §11.44), `flow.projectInitialData` đã CHẾT và reconcile
> chuyển sang RPC `batchexecute` (xem §11.45 +
> [`docs/FLOW_BATCHEXECUTE_API.md`](FLOW_BATCHEXECUTE_API.md)).
> Phần mô tả Slate CE / tile polling / icon-ligature bên dưới là của UI CŨ.

> **DOM mode** (`worker_mode = 'dom'`): Selenium điều khiển toàn bộ Flow UI qua CDP — mở config popup, upload ảnh qua picker, type prompt, poll tile cho đến khi done, resolve CDN URL bằng session cookie của browser.  
> Hoạt động cho: `textToImage`, `imageToImage`, `imageToVideo`, `componentsToVideo`, `textToVideo`.

---

## 1. Khởi động worker (chung cho mọi case)

```
main.py     → POST /api/selenium/profiles/{id}/start
    ↓
_start_worker(profile)
    ↓
SeleniumFlowWorker(profile, worker_mode='dom').run()  [thread]
```

### run()

```
_make_driver()
  ├─ Nếu login browser đang mở → attach qua debuggerAddress (port 9300+id)
  └─ Không có → mở Chrome mới:
       - --user-data-dir = profile['profile_dir']  (session Google persistent)
       - --load-extension = extensions đang enabled
       - goog:loggingPrefs = {performance:ALL, browser:ALL}
       - navigator.webdriver = undefined (Page.addScriptToEvaluateOnNewDocument)

_ensure_flow_page()          # navigate → profile['project_url']
sleep(6s)

log: "DOM mode — sẵn sàng nhận task"

loop mỗi 8s:
  _is_driver_alive()         # Chrome còn sống?
  _drain_browser_logs()      # console errors → profile log
  _heartbeat()               # POST /api/media/heartbeat → nhận task hoặc None
  nếu có task → _run_task_dom(task)
```

---

## 2. Nhận task — _heartbeat()

```
POST http://localhost:13443/api/media/heartbeat
Body: {
  machineCode:  'selenium-{profile_id}',
  runningCount: 0,
  waitingCount: 0,
  taskMode:     profile['task_mode'],    # 'all' | 'image_only'
  displayName:  profile['display_name'] or '[SEL] {name}',
  machineType:  'selenium_profile'
}

Response: { task: {...} | null }
```

---

## 3. _run_task_dom(task) — dispatcher chung

```
POST /api/media/task/processing {taskId, machineCode}

_ensure_flow_page()     # về đúng labs.google project page
sleep(3s)

_dom_configure(task)    # Step 4

nếu source_media:
  _dom_upload_images(source_media, mode)   # Step 5

before_ids = _dom_tile_ids()

_dom_fill_and_submit(f'TASK_{task_id}:{prompt}')   # Step 6

new_ids = _dom_wait_new_tiles(before_ids, count, timeout=90s)

results = _dom_poll_done(new_ids, timeout=600s)    # Step 7

cdn_urls = [_dom_resolve_url(r['src']) for r in results]   # Step 8

POST /api/media/task/download {taskId, machineCode, cueId, mode, media}   # Step 9
```

Nếu exception → `POST /api/media/task/error {taskId, errorMessage}`

---

## 4. _dom_configure(task)

Mở config popup → chọn tab/sub-tab/ratio/count/model.

```
1. Tìm config button: button[aria-controls] → có popup element
2. _fire_click(config_btn) nếu chưa mở
   (_fire_click = dispatch pointer+mouse events + .click() — cần cho React)

3. Chọn main tab (nếu cần):
   textToImage / imageToImage  → tab có icon 'image'
   textToVideo / imageToVideo  → tab có icon 'play_circle'

4. Chọn sub-tab (nếu cần):
   imageToVideo      → button[role="tab"] text 'Khung hình'
   componentsToVideo → button[role="tab"] text 'Thành phần'

5. Chọn aspect ratio: tìm button text = task['aspect_ratio'] trong popup
   _cdp_click_el(ratio_btn)

6. Chọn output count:
   {1:'1x', 2:'x2', 3:'x3', 4:'x4'}
   _cdp_click_el(count_btn)

7. Chọn model (nếu có):
   click button có icon 'arrow_drop_down'
   sleep(0.8s) → tìm [role="menuitem"] text chứa model name
   _cdp_click_el(menu_item)

8. Đóng popup: _fire_click(config_btn) nếu vẫn mở
```

> `_cdp_click_el(el)` = CDP `Input.dispatchMouseEvent` (mousePressed + mouseReleased) — đáng tin hơn `.click()` vì đi qua event pipeline đầy đủ.

---

## 5. _dom_upload_images(source_media, mode)

Áp dụng cho: `imageToImage`, `imageToVideo`, `componentsToVideo`.

```
Fetch từng ảnh → lưu temp dir → encode base64 → data_url

Với mỗi ảnh:
  ├─ B1: Mở picker
  │    imageToVideo:  click div[aria-haspopup="dialog"] text='Bắt đầu' (i=0) / 'Kết thúc' (i=1)
  │    các mode khác: click button có icon 'add_2'
  │    Chờ picker mở: button 'Thêm vào câu lệnh' + input[placeholder='Tìm kiếm thành phần'] cùng visible
  │
  ├─ B2: Click tab 'Hình ảnh'
  │    button[role="tab"] có icon 'image' và text 'Hình ảnh'
  │    CDP click → chờ aria-selected='true'
  │
  ├─ B3: Search tên file
  │    CDP click input[placeholder='Tìm kiếm thành phần']
  │    Ctrl+A → CDP insertText(search_name)  ← tên file bỏ extension
  │    sleep(1.5s) — đợi gallery filter render
  │
  ├─ B4: Kiểm tra gallery
  │    đếm [data-index] items trong picker container
  │
  │    [Case 1 — gallery_count > 0: ảnh đã có]
  │    → bỏ qua upload, chuyển thẳng sang B5
  │
  │    [Case 2 — gallery_count == 0: ảnh chưa có → DataTransfer inject]
  │    Tìm input[type="file"] trong picker (hoặc toàn trang)
  │    Snapshot before_srcs (img.src chứa 'trpc/media')
  │    execute_script:
  │      window.__selUpload = data_url
  │      atob() → Uint8Array → Blob → File → DataTransfer
  │      HTMLInputElement.prototype.files setter (hoặc defineProperty)
  │      dispatch 'input' + 'change' events
  │    Poll 30s: after_srcs - before_srcs → src mới xuất hiện ✔
  │    Re-trigger search input để gallery refresh (React state setter)
  │
  └─ B5: Click 'Thêm vào câu lệnh'
       _cdp_click_el(add_btn)
       Đợi picker đóng (tối đa 4s)
```

---

## 6. _dom_fill_and_submit(prompt)

```
Tìm div[role="textbox"] (tối đa 30s, poll 1s)

CDP mousePressed + mouseReleased vào textarea (click để focus)
sleep(0.3s)

Type 20 ký tự đầu từng char:
  CDP insertText(char), sleep(70-170ms/char) — mô phỏng typing

CDP insertText(phần còn lại)
sleep(0.5s)

CDP dispatchKeyEvent Enter (keyDown + keyUp, keyCode=13)
```

> Prefix `TASK_{id}:` trước prompt để reconcile nhận dạng tile sau refresh.

---

## 7. _dom_poll_done(tile_ids, timeout=600s)

```
Poll mỗi 5s:
  Với mỗi tile_id:
    _dom_tile_status(tile_id):
      - video[src]          → {status:'done', type:'video', src:vid.src}
      - img[src] (non-data) → {status:'done', type:'image', src:img.src}
      - i.textContent='warning' → {status:'error'}
      - text /\d+%/         → {status:'generating', pct:N}
      - else                → {status:'waiting'}

  Nếu có error → raise RuntimeError
  Nếu tất cả done → return results
  Log avg%
```

---

## 8. _dom_resolve_url(url)

```
execute_async_script (timeout=25s):
  fetch(url, {credentials:'same-origin', redirect:'follow', cache:'no-store'})
  → browser gửi labs.google session cookie
  → 302 → https://flow-content.google/image/UUID?Expires=...&Signature=...
  → return res.url  (CDN public URL, không cần auth)
```

---

## 9. POST /api/media/task/download

```
POST http://localhost:13443/api/media/task/download
Body: {
  taskId:      <int>,
  machineCode: 'selenium-{id}',
  cueId:       <int>,
  mode:        'textToImage' | 'imageToImage' | ...,
  media:       [{name: 'dom_{id}_1', url: 'https://flow-content.google/...'}]
}
```

`server_gemini_flow.py` xử lý:
```
requests.get(cdn_url, stream=True)
→ detect Content-Type → lưu media_output/task{id}_cue{id}_{n}.{ext}
UPDATE tasks_media_flow SET status='done', result_files=JSON, completed_at=NOW()
UPDATE machines_media SET status='idle', current_task_id=NULL
```

---

## 10. Luồng riêng từng case

### textToImage

```
task.mode = 'textToImage'
task.source_media = []

_dom_configure: tab 'image', ratio, count
(bỏ qua _dom_upload_images — không có ảnh)
_dom_fill_and_submit(prompt)
→ poll tiles → resolve URL → download
```

---

### imageToImage

```
task.mode = 'imageToImage'
task.source_media = [{url, filename}]   # 1 ảnh nguồn

_dom_configure: tab 'image', ratio, count
_dom_upload_images(1 ảnh, mode='imageToImage'):
  - click 'add_2' → picker → tab 'Hình ảnh' → search → inject/chọn → 'Thêm vào câu lệnh'
_dom_fill_and_submit(prompt)
→ poll tiles → resolve URL → download
```

---

### imageToVideo (frameToVideo)

```
task.mode = 'imageToVideo'
task.source_media = [
  {url: start_frame_url, filename: 'start.jpg'},   # Bắt đầu
  {url: end_frame_url,   filename: 'end.jpg'}       # Kết thúc
]

_dom_configure: tab 'play_circle' (Video), sub-tab 'Khung hình', count, duration
_dom_upload_images(2 ảnh, mode='imageToVideo'):
  - Ảnh 0: click div[aria-haspopup="dialog"] text='Bắt đầu' → picker → upload
  - Ảnh 1: click div[aria-haspopup="dialog"] text='Kết thúc' → picker → upload
_dom_fill_and_submit(prompt)
→ poll tiles (type='video') → resolve URL → download
```

---

### componentsToVideo

```
task.mode = 'componentsToVideo'
task.source_media = [{url, filename}, ...]   # N ảnh thành phần

_dom_configure: tab 'play_circle' (Video), sub-tab 'Thành phần'
_dom_upload_images(N ảnh, mode='componentsToVideo'):
  - Mỗi ảnh: click 'add_2' → picker → upload/chọn → 'Thêm vào câu lệnh'
_dom_fill_and_submit(prompt)
→ poll tiles → resolve URL → download
```

---

### textToVideo

```
task.mode = 'textToVideo'
task.source_media = []

_dom_configure: tab 'play_circle' (Video), không chọn sub-tab
(bỏ qua _dom_upload_images)
_dom_fill_and_submit(prompt)
→ poll tiles (type='video') → resolve URL → download
```

---

## 11. Xử lý lỗi

| Tình huống | Kết quả |
|-----------|---------|
| Config button không tìm thấy | Log warn, bỏ qua config (best-effort) |
| Picker không mở sau 10s | `raise RuntimeError('[mode] picker không mở')` |
| Upload timeout 30s | `raise RuntimeError('Upload timeout')` |
| Tile error (warning icon) | `raise RuntimeError('Tile error')` |
| Poll timeout 600s | `raise RuntimeError('DOM tile polling timeout')` |
| Driver chết giữa chừng | `_is_driver_alive()` → break loop → worker offline |
| Bất kỳ exception | `POST /api/media/task/error` → server retry hoặc mark error |

---

## 12. Task fields

| Field | Bắt buộc | Mô tả |
|-------|----------|-------|
| `id` | ✅ | Task ID |
| `mode` | ✅ | `textToImage` / `imageToImage` / `imageToVideo` / `componentsToVideo` / `textToVideo` |
| `prompt_text` | ✅ | Text prompt |
| `source_media` | Tùy mode | `[{url, filename}]` — ảnh nguồn |
| `aspect_ratio` | | `16:9`, `1:1`, `9:16`, ... |
| `output_count` | | 1–4 |
| `video_duration` | Video mode | Số giây (int hoặc string `"8s"`) |
| `model` | | Tên model hiển thị trong dropdown |
| `cue_id` | | ID cue để kết nối kết quả |

---

## 13. Trạng thái implementation

| Method | Trạng thái |
|--------|-----------|
| `_dom_configure()` | ✅ Implemented |
| `_dom_upload_images()` | ✅ Implemented (DataTransfer inject + gallery check) |
| `_dom_fill_and_submit()` | ✅ Implemented |
| `_dom_wait_new_tiles()` | ✅ Implemented |
| `_dom_poll_done()` | ✅ Implemented |
| `_dom_resolve_url()` | ✅ Implemented |
| End-to-end test textToImage | ⚠️ Chưa test (dùng API mode cho textToImage) |
| End-to-end test imageToImage | ❌ Chưa test |
| End-to-end test imageToVideo | ❌ Chưa test |
| End-to-end test textToVideo | ❌ Chưa test |
