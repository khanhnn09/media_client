# API mode — Luồng chạy đầu đến cuối cho tất cả case

> ⚠️ **CẬP NHẬT 2026-09-03 — một phần tài liệu này ĐÃ LỖI THỜI.** Google đổi
> app sang Angular `flow.google.com`: mọi selector DOM đã viết lại (xem
> `CLAUDE.md` §11.44), `flow.projectInitialData` đã CHẾT và reconcile
> chuyển sang RPC `batchexecute` (xem §11.45 +
> [`docs/FLOW_BATCHEXECUTE_API.md`](FLOW_BATCHEXECUTE_API.md)).
> Phần mô tả Slate CE / tile polling / icon-ligature bên dưới là của UI CŨ.

> **API mode** (`worker_mode = 'api'`):  
> - **Image**: Selenium type prompt + Enter vào Slate CE, JS fetch interceptor bắt response từ browser. Browser tự xử lý recaptchaToken — không cần capture thủ công.  
> - **Video**: Gọi Python API trực tiếp đến `aisandbox-pa.googleapis.com` với Authorization token capture từ Chrome perf logs.

---

## 1. Khởi động worker (chung cho mọi case)

```
main.py     → POST /api/selenium/profiles/{id}/start
    ↓
_start_worker(profile)
    ↓
SeleniumFlowWorker(profile, worker_mode='api').run()  [thread]
```

### run()

```
_make_driver()
  ├─ Nếu login browser đang mở → attach qua debuggerAddress (port 9300+id)
  └─ Không có → mở Chrome mới:
       - --user-data-dir = profile['profile_dir']  (session Google persistent)
       - --load-extension = extensions đang enabled
       - goog:loggingPrefs = {performance:ALL, browser:ALL}
       - navigator.webdriver = undefined

_ensure_flow_page()           # navigate → profile['project_url']
_trigger_page_requests()      # scroll → kích network request → _drain_perf_logs()

_wait_for_tokens(timeout=60s)
  └─ loop: _drain_perf_logs() mỗi 3s → bắt Authorization header từ aisandbox calls

loop mỗi 8s:
  _is_driver_alive()
  _drain_browser_logs()
  _drain_perf_logs()                   # refresh token liên tục
  nếu token > 10 phút: _get_fresh_recaptcha()
  _heartbeat()  → nhận task hoặc None
  nếu có task → _run_task_api(task)
```

---

## 2. Token capture — _drain_perf_logs()

```
driver.get_log('performance')
→ tìm method='Network.requestWillBeSent' với url chứa 'aisandbox-pa.googleapis.com'
→ trích từ request.headers:
    authorization        → Bearer ya29.xxx  (OAuth2 token, ~60 phút)
    x-browser-validation
    x-client-data
    x-browser-channel / x-browser-year
→ trích từ request.postData (JSON):
    clientContext.recaptchaContext.token → recaptchaToken
    toàn bộ body → _tokens['lastRequestBody']  (dùng làm template cho video)
```

---

## 3. Nhận task — _heartbeat()

```
POST http://localhost:13443/api/media/heartbeat
Body: {
  machineCode:  'selenium-{profile_id}',
  runningCount: 0,
  waitingCount: 0,
  taskMode:     profile['task_mode'],
  displayName:  profile['display_name'] or '[SEL] {name}',
  machineType:  'selenium_profile'
}
Response: { task: {...} | null }
```

---

## 4. _run_task_api(task) — dispatcher

```python
is_video = 'video' in task['mode'].lower()

if not is_video:
    # IMAGE — UI-driven (Selenium + fetch interceptor)
    results_urls = _generate_image_via_ui(task)
    media = [{'name': f'img_{id}_{i+1}', 'url': u['url']} for i, u in enumerate(results_urls)]
    POST /api/media/task/download {taskId, machineCode, cueId, mode, media}
else:
    # VIDEO — direct Python API
    captcha = _get_fresh_recaptcha()
    video_list = _call_video_api(task, captcha)
    media = [{'name': f'video_{id}_{i+1}', 'url': v['uri']} for i, v in enumerate(video_list)]
    POST /api/media/task/download {taskId, machineCode, cueId, mode, media}
```

---

## 5. Luồng image — _generate_image_via_ui(task)

### Bước 5.1 — Navigate đến project page

```python
_ensure_flow_project()
```
- Kiểm tra `current_url` có `/project/` không.
- Nếu không → `driver.get(profile['project_url'])`.
- Sleep 7s → `_drain_perf_logs()`.

> **Bắt buộc:** `project_url` phải là `/project/{uuid}`. Trang chủ `labs.google/flow` không có Slate CE.

### Bước 5.2 — Cài fetch interceptor

```python
_install_gen_interceptor()
```

```javascript
// Chạy SAU page load — override fetch wrapper của extension
if (!window.__genInterceptorInstalled) {
    window.__genInterceptorInstalled = true;
    window.__genCaptures = [];
    var _prev = window.fetch;
    window.fetch = async function(input, init) {
        var url = typeof input==='string' ? input : input.url;
        if (url.includes('batchGenerateImages') || url.includes('batchAsyncGenerate')) {
            var call = {url, method, ts:Date.now(), response_status:null, response_body:null};
            window.__genCaptures.push(call);
            var resp = await _prev.apply(this, arguments);
            call.response_status = resp.status;
            call.response_body   = await resp.clone().text();
            return resp;
        }
        return _prev.apply(this, arguments);
    };
}
window.__genCaptureStart = window.__genCaptures.length;  // mark start index
```

### Bước 5.3 — [imageToImage only] Attach ảnh nguồn vào CE

> textToImage: bỏ qua bước này.

```python
# Tìm file input ẩn gần CE
file_input = driver.execute_script("""
    return document.querySelector('input[type="file"][accept*="image"]') || null;
""")

# Fetch ảnh nguồn → base64
resp = requests.get(source_url, timeout=30)
b64  = base64.b64encode(resp.content).decode()
data_url = f'data:image/jpeg;base64,{b64}'

# Snapshot srcs trước khi inject
before_srcs = set(driver.execute_script("""
    return [...document.querySelectorAll('img[src]')]
        .map(i=>i.src).filter(s=>s.includes('trpc/media'));
"""))

# Inject qua DataTransfer
driver.execute_script("window.__selUpload = arguments[0];", data_url)
driver.execute_script("""
    var name=arguments[0], inp=arguments[1];
    var parts=window.__selUpload.split(',');
    var mime=parts[0].split(':')[1].split(';')[0];
    var bstr=atob(parts[1]);
    var u8=new Uint8Array(bstr.length);
    for(var j=0;j<bstr.length;j++) u8[j]=bstr.charCodeAt(j);
    var dt=new DataTransfer();
    dt.items.add(new File([new Blob([u8],{type:mime})], name, {type:mime}));
    var ns=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files')?.set;
    if(ns) ns.call(inp,dt.files); else inp.files=dt.files;
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    inp.dispatchEvent(new Event('change',{bubbles:true}));
    window.__selUpload=null;
""", filename, file_input)

# Chờ src mới xuất hiện (tối đa 30s, poll 0.25s)
for _ in range(120):
    after = set(driver.execute_script(...))
    if after - before_srcs: break
    time.sleep(0.25)
```

### Bước 5.4 — Type prompt + Enter vào Slate CE

```python
wait = WebDriverWait(driver, 15)
ce = wait.until(EC.presence_of_element_located(
    (By.CSS_SELECTOR, '[contenteditable="true"]')))
ce.click()
time.sleep(0.2)
ce.send_keys(prompt)
time.sleep(0.3)
ce.send_keys(Keys.RETURN)
```

> **Tại sao RETURN chứ không click button?**  
> CE là Slate.js editor. `send_keys(Keys.RETURN)` kích hoạt `onKeyDown` trong Slate, đọc Slate internal state (có prompt + ảnh attach). Button `arrow_forwardTạo` đọc React component state — lag sau Slate → no-op. ❌

Browser tự gắn:
- `Authorization: Bearer ya29.xxx`
- `recaptchaToken` (grecaptcha.enterprise từ page)
- Request body với `structuredPrompt`, `imageInputs`, `seed`, `batchId`

```
POST https://aisandbox-pa.googleapis.com/v1/projects/{projectId}/flowMedia:batchGenerateImages
```

### Bước 5.5 — Poll response (tối đa 90s, sleep 2s/vòng)

```python
deadline = time.time() + 90
while time.time() < deadline:
    time.sleep(2)
    caps = driver.execute_script("""
        var start = arguments[0] || 0;
        return (window.__genCaptures||[]).slice(start)
            .filter(c => c.response_status !== null);
    """, start_idx)

    errors = [c for c in caps if c['response_status'] != 200]
    if errors:
        raise RuntimeError(f'API lỗi {errors[0]["response_status"]}: {errors[0]["response_body"][:300]}')

    done = [c for c in caps if c['response_status']==200 and c.get('response_body')]
    if done:
        for cap in done:
            data = json.loads(cap['response_body'])
            for m in data.get('media', []):
                fife = m.get('image',{}).get('generatedImage',{}).get('fifeUrl','')
                if fife:
                    urls.append({'type':'image', 'url': fife})
        if urls: break

raise RuntimeError('Image generation timeout (90s)')
```

**Response format:**
```json
{
  "media": [
    {
      "name": "{UUID}",
      "image": {
        "generatedImage": {
          "fifeUrl": "https://flow-content.google/image/{UUID}?Expires=...&Signature=..."
        }
      }
    }
  ]
}
```

---

## 6. Luồng video — _call_video_api(task, captcha)

> ⚠️ Hiện trả HTTP 403 nếu recaptchaToken không hợp lệ / hết hạn.  
> Cần Chrome đang ở Flow page để có token mới nhất.

### Bước 6.1 — Build headers

```python
_build_headers()
→ {
    'Authorization': 'Bearer ya29.xxx',
    'Content-Type':  'application/json',
    'Origin':        'https://labs.google',
    'Referer':       'https://labs.google/',
    'x-browser-validation': ...,
    'x-client-data': ...,
    'x-browser-channel': ...,
    'x-browser-year': ...,
}
```

### Bước 6.2 — Build body

```python
_build_body(task, captcha)
# Base: _tokens['lastRequestBody'] làm template (từ request gần nhất bắt được)
# Override:
{
  "instances": [{"prompt": task['prompt_text']}],
  "parameters": {
    "sampleCount":     task['output_count'],
    "aspectRatio":     task['aspect_ratio'],
    "modelId":         task['model'],
    "durationSeconds": task['video_duration'],
  },
  "clientContext": {
    "recaptchaContext": {"token": captcha}
  }
}
```

### Bước 6.3 — Gọi video API

```
POST https://aisandbox-pa.googleapis.com/v1:batchAsyncGenerateVideo
Body: (như trên)
→ Response: {"name": "operations/xxx"}   (async long-running operation)
```

### Bước 6.4 — Poll operation (tối đa 15 phút, sleep 8s/vòng)

```
GET https://aisandbox-pa.googleapis.com/v1/{op_name}
→ {"done": false}  →  tiếp tục poll
→ {"done": true, "response": {"videos": [{"uri": "gs://..."}]}}
```

Trích video URI:
```python
videos = response.get('videos') or response.get('generatedSamples') or []
return [{'type':'video', 'uri': v.get('uri') or v.get('videoUri') or v.get('gcsUri', '')}
        for v in videos]
```

---

## 7. POST /api/media/task/download (chung image + video)

```
POST http://localhost:13443/api/media/task/download
Body: {
  taskId:      <int>,
  machineCode: 'selenium-{id}',
  cueId:       <int>,
  mode:        'textToImage' | 'imageToImage' | 'textToVideo' | ...,
  media:       [
    {name: 'img_{id}_1',   url: 'https://flow-content.google/image/UUID?...'},  # image
    {name: 'video_{id}_1', url: 'gs://...' hoặc CDN URI}                         # video
  ]
}
```

`server_gemini_flow.py`:
```
requests.get(url, stream=True, timeout=60)
→ detect Content-Type → ext
→ lưu media_output/task{id}_cue{id}_{n}.{ext}
UPDATE tasks_media_flow SET status='done', result_files=JSON, completed_at=NOW()
UPDATE machines_media SET status='idle', current_task_id=NULL
```

---

## 8. Luồng riêng từng case

### textToImage ✅ (đã test)

```
source_media = []
↓
_generate_image_via_ui(task):
  _ensure_flow_project()
  _install_gen_interceptor()
  ce.send_keys(prompt) + send_keys(RETURN)
  poll 90s → fifeUrl
→ POST /task/download {media:[{name, url:fifeUrl}]}
```

**Thời gian:** ~30s/ảnh.

---

### imageToImage ⚠️ (chưa test)

```
source_media = [{url, filename}]   # 1 ảnh nguồn
↓
_generate_image_via_ui(task):
  _ensure_flow_project()
  _install_gen_interceptor()
  [THÊM] inject source image vào file input trước send_keys
  ce.send_keys(prompt) + send_keys(RETURN)
  poll 90s → fifeUrl
→ POST /task/download
```

> **⚠️ Chưa implement:** `_generate_image_via_ui()` hiện tại chưa xử lý `source_media`.  
> Cần thêm bước 5.3 (DataTransfer inject) trước `send_keys(RETURN)`.

---

### textToVideo ⚠️ (cần token hợp lệ)

```
source_media = []
↓
_call_video_api(task, captcha):
  POST /v1:batchAsyncGenerateVideo
  → operation name
  Poll GET /v1/{op_name} mỗi 8s → done=true
  → videos[].uri
→ POST /task/download {media:[{name, url:video_uri}]}
```

> **⚠️ HTTP 403** nếu recaptchaToken hết hạn. Token chỉ có khi Chrome đang render Flow page và user vừa trigger một action.

---

### imageToVideo ❌ (chưa có endpoint API)

Endpoint `batchAsyncGenerateVideo` có thể nhận `instance.image` (start frame) và `instance.lastFrame` (end frame) qua base64, nhưng chưa verify được do HTTP 403.

`_build_body()` đã có code xử lý `source_media`:
```python
if images:
    instance['image']     = {'bytesBase64Encoded': images[0]['data'].split(',')[-1]}
if len(images) > 1:
    instance['lastFrame'] = {'bytesBase64Encoded': images[1]['data'].split(',')[-1]}
```

Cần token hợp lệ để test.

---

## 9. _get_fresh_recaptcha()

```python
# Chạy grecaptcha.enterprise.execute() trong browser context
driver.execute_async_script("""
    var done = arguments[0];
    var key = document.querySelector('[data-recaptcha-key]');
    var siteKey = key ? key.dataset.recaptchaKey : '';
    if (!siteKey || !window.grecaptcha?.enterprise) { done(null); return; }
    window.grecaptcha.enterprise.execute(siteKey, {action:'generate'})
        .then(t => done(t)).catch(() => done(null));
""")
```

Token hợp lệ khi `len(token) > 20`. Hết hạn sau vài phút.

---

## 10. Xử lý lỗi

| Tình huống | Kết quả |
|-----------|---------|
| Không có token sau 60s | Log warn, vẫn chạy, retry khi có task |
| CE không xuất hiện sau 15s | `WebDriverWait` timeout → RuntimeError |
| API trả HTTP 403 | RuntimeError → `POST /task/error` → server retry |
| Image generation timeout 90s | RuntimeError → `POST /task/error` |
| Video operation timeout 15 phút | RuntimeError → `POST /task/error` |
| Driver chết | `_is_driver_alive()` → break loop → worker offline |

---

## 11. Task fields

| Field | Bắt buộc | Mô tả |
|-------|----------|-------|
| `id` | ✅ | Task ID |
| `mode` | ✅ | `textToImage` / `imageToImage` / `textToVideo` / `imageToVideo` |
| `prompt_text` | ✅ | Text prompt |
| `source_media` | imageToImage/imageToVideo | `[{url, filename}]` |
| `aspect_ratio` | | `16:9`, `1:1`, `9:16`, ... |
| `output_count` | | 1–4 |
| `video_duration` | Video mode | Số giây |
| `model` | | Model ID (parameters.modelId) |
| `cue_id` | | ID cue |

---

## 12. Trạng thái implementation

| Case | Trạng thái |
|------|-----------|
| textToImage | ✅ Implemented + tested |
| imageToImage | ⚠️ Chưa implement (thiếu source_media inject trong `_generate_image_via_ui`) |
| textToVideo | ✅ Implemented — bị block bởi HTTP 403 (recaptchaToken) |
| imageToVideo | ⚠️ Code có sẵn trong `_build_body`, chưa verify |
