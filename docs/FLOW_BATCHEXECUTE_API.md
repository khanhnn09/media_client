# API Flow MỚI — `batchexecute` (flow.google.com)

> Tài liệu tham chiếu cho API của Google Flow **sau khi đổi sang app Angular
> `flow.google.com`** (2026-09-03). Thay thế phần lớn `tests/FLOW_API_CAPTURE.md`
> (tài liệu API CŨ `aisandbox-pa.googleapis.com`, viết 2026-08-12 — vẫn còn
> đúng cho đường generate cũ, nhưng `projectInitialData` trong đó đã CHẾT).
>
> Mọi thứ dưới đây **đã verify bằng request/response THẬT** trên profile 25
> (`huavantien84_2`), không suy đoán từ code minify. Chỗ nào chưa verify đều
> ghi rõ.

---

## 1. Vì sao có tài liệu này

`labs.google/fx/vi/tools/flow/project/{uuid}` giờ **redirect** sang
`flow.google.com/project/{uuid}`. App cũ (React + Slate.js) bị thay bằng
Angular Material + ProseMirror. Kèm theo đó:

- **`flow.projectInitialData` KHÔNG CÒN TỒN TẠI** — gọi thẳng URL cũ trả về
  nguyên shell HTML của SPA, không phải data. (Từng thử vá thêm
  `XMLHttpRequest` vì đoán "Angular HttpClient dùng XHR" — sai hướng: endpoint
  không được app gọi ở bất kỳ transport nào.)
- App mới dùng **RPC đa dụng `batchexecute`** — cùng protocol Google dùng
  chung cho Gmail/Drive/Docs. Không có URL riêng theo mục đích; mọi thứ đi qua
  1 endpoint, phân biệt bằng `rpcids`.

---

## 2. Endpoint & định dạng request

```
POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute
```

**Query params** (đúng thứ tự app dùng):

| Param | Giá trị | Ghi chú |
|---|---|---|
| `rpcids` | vd `ogiZ0b` | id của RPC muốn gọi |
| `source-path` | `/project/{projectId}` | lấy từ `location.pathname` |
| `bl` | vd `boq_labs-ai-sandbox-frontend_20260902.03_p0` | build label |
| `f.sid` | vd `-698735820110569656` | session id |
| `hl` | `en` | |
| `_reqid` | xem §4 | |
| `rt` | `c` | |

**Body** (`application/x-www-form-urlencoded;charset=UTF-8`):

```
f.req=<urlencode(JSON)>&at=<urlencode(token)>&
```

`f.req` là JSON **lồng 2 lớp** — `args` nằm bên trong dưới dạng **CHUỖI**:

```json
[[[ "<rpcid>", "<JSON.stringify(args)>", null, "generic" ]]]
```

⚠️ Gọi bằng `fetch()` **ngay trong trang** (`credentials:'include'`) là đủ —
KHÔNG cần bearer token, KHÔNG cần fingerprint `x-browser-validation`/
`x-client-data`, KHÔNG cần `curl_cffi` impersonate như đường `aisandbox` cũ.

---

## 3. Định dạng response

```
)]}'

767
[["wrb.fr","as29s","[\"e4d4a7b6-…\",…]",null,null,null,"generic"],["di",528],["af.httprm",527,"…",49]]
25
[["e",4,null,null,803]]
```

Cách parse:
1. Bỏ tiền tố XSSI `)]}'`.
2. Duyệt từng **cặp dòng**: dòng "độ dài" (số) → dòng JSON array.
3. Tìm entry `["wrb.fr", "<rpcid>", <payloadStr>, …]`.
4. `payloadStr` **LÀ 1 CHUỖI chứa JSON** → decode **lớp thứ 2**.
5. `payloadStr == null` nghĩa là "không có dữ liệu" (KHÔNG phải lỗi) — vd
   `as29s` gọi cho media không qua generation.

Code: `SeleniumFlowWorker._batchexecute_parse()` (`server/worker.py`).

---

## 4. Tham số phiên: `bl` / `f.sid` / `at` / `_reqid`

**`bl`, `f.sid`, `at`** — không tự sinh được, phải **harvest** từ 1 lệnh
batchexecute mà chính trang tự bắn. Mỗi lần vào/reload project, Angular tự gọi
~24 RPC, nên chỉ cần bắt lệnh đầu tiên bất kỳ rồi dùng lại token đó cho các
lệnh mình TỰ CONSTRUCT (đã verify: token dùng chéo rpcid khác vẫn hợp lệ).

Code: `_batchexecute_harvest_session()` — cài interceptor qua CDP
`Page.addScriptToEvaluateOnNewDocument` (chạy TRƯỚC JS của trang nên không lỡ
request bắn ra lúc mount), `driver.refresh()`, đọc lệnh bắt được.

**`_reqid` có quy luật, KHÔNG phải random** (verify qua 24 request thật):

```
_reqid = seed + 100000 × n
  seed = SỐ GIÂY tính từ 00:00:00 giờ ĐỊA PHƯƠNG (chốt 1 lần lúc load trang)
  n    = số thứ tự request batchexecute trong lần load đó (0,1,2,…)
```

Bằng chứng: 24 request liên tiếp cho bậc thang +100000 không sót nhịp; 2 lần
load cách nhau 14s cho seed lệch đúng 14; seed khớp chính xác đồng hồ lúc đo.

Hiện `_batchexecute_call()` dùng số random 6 chữ số — **vẫn chạy được**, chỉ
lệch quy ước. Muốn đúng chuẩn: lấy `max(_reqid)` trang đã dùng rồi +100000 mỗi
lệnh tự gọi.

---

## 5. reCAPTCHA — VẪN CẦN

⚠️ Phỏng đoán "batchexecute chỉ cần `bl`/`f.sid`/`at`" là **SAI**. Mọi RPC
GENERATE đều mang token reCAPTCHA trong body.

- Site key trang dùng: `6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV` — **khớp
  đúng** hằng `LABS_RECAPTCHA_SITE_KEY` sẵn có trong `server/worker.py`, nên
  mint lại bằng chính `_get_fresh_recaptcha(action)` của production.
- `action`: `IMAGE_GENERATION` (ảnh) / `VIDEO_GENERATION` (video).
- Token dài ~2400 ký tự, **hết hạn ~2 phút** → phải mint mới mỗi lần gọi.
- ⚠️ Thứ tự: **harvest session TRƯỚC** (nó reload trang), rồi MỚI mint
  reCAPTCHA — làm ngược lại thì token mất theo lần reload.

RPC chỉ ĐỌC (`Zzl0ze`, `as29s`, `HTrJv`…) **không cần** reCAPTCHA.

---

## 6. Bảng rpcid (72 cái)

rpcid được **nhúng thẳng trong bundle JS kèm tên method backend**:

```js
new _.xx("as29s",  _.Dx, zRa, [_.im,!0,_.hm, "/FlowService.GetMedia"]);
new _.xx("Zzl0ze", …,   HZa, [_.im,!0,_.hm, "/FlowService.GetProjectContents"]);
```

Bundle: `gstatic.com/_/mss/boq-labs-ai-sandbox/…AiSandboxAngularFrontend…`
(cái to nhất trong 3 chunk, ~3.4 MB).

**Trích lại khi Google đổi build:** `python tests/_extract_rpcid_map.py`

### Dùng trong client_tool

| rpcid | Method | Dùng ở đâu |
|---|---|---|
| `Zzl0ze` | `/FlowService.GetProjectContents` | reconcile — liệt kê media |
| `as29s` | `/FlowService.GetMedia` | reconcile — prompt + CDN URL |
| `ogiZ0b` | `/FlowService.BatchGenerateImages` | textToImage, imageToImage |
| `YhhmEf` | `/VideoFxService.BatchAsyncGenerateVideoText` | textToVideo |
| `MZZa6b` | `…BatchAsyncGenerateVideoReferenceImages` | ingredientToVideo |

### Chưa dùng nhưng đáng chú ý

| rpcid | Method |
|---|---|
| `maseQ` | `/FlowService.UploadImage` |
| `eb1hJf` | `…BatchAsyncGenerateVideoStartImage` (frameToVideo) |
| `nprQif` | `…BatchAsyncGenerateVideoStartAndEndImage` ← ảnh đầu **+ cuối** |
| `jwpduf` | `…BatchCheckAsyncVideoGenerationStatus` (poll render) |
| `fZytfe` | `…BatchAsyncGenerateVideoExtendVideo` |
| `jIps6` | `…BatchAsyncGenerateVideoEditVideo` |
| `p0UkFb` | `…BatchAsyncGenerateVideoUpsampleVideo` |
| `HTrJv` | `/FlowService.GetModels` (catalog model đầy đủ) |
| `SPrCad` | `/FlowService.UpsampleImage` |
| `sAVZzc` | `/FlowService.TransformImage` |
| `agJzFb` | `/FlowService.GenerateContent` |
| `no0P6` | `/FlowService.BatchGenerateAudio` |
| `cz8Z4b` | `/FlowService.BatchDeleteAssets` |
| `lt8g5` | `/FlowService.UpdateMedia` |
| `Sc7aEb` | `/FlowService.CopyProjectMedia` |
| `UpteDb` | `/FlowService.GetProjects` |
| `ngNC2` | `/AiSandbox.GetProject` |
| `QI2zvc` | `/AiSandbox.DeleteProject` |
| `OylIJd` | `/AiSandbox.SearchUserProjects` |
| `o8DA4` | `/AiSandbox.UpdateProjectInfo` |
| `rqZuUc` / `OSd63c` / `BpMsoe` | `/FlowService.CreateScene` / `CopyScene` / `UpdateScene` |
| `oWTRd` / `XKxtXb` / `mYWVGd` / `pGCYOe` | Workflow: Add/Copy/Update/BatchUpdate |
| `uwAyfb` / `GoMJte` | `GetSceneWorkflows` / `UpdateSceneWorkflows` |
| `Uxbujd` / `O2COMe` / `kVdhHf` | Collection: Create / Update / BatchMoveTo |
| `iVqlKd` | `/FlowService.UpdateVideoOffset` |
| `QZasKb` | `/VideoFxService.GeneratePinholeGif` |

Còn ~30 rpcid thuộc `FlowAppletAgentService` / `FlowCreationAgentService` /
`EntityService` (phần Applet/Agent của sản phẩm, không liên quan client_tool).

---

## 7. Shape body từng luồng (đã verify thật)

> Ghi theo **đường dẫn index** trong `args`. Chỗ nào không liệt kê là `null`.

### 7.0 `clientContext` + enum tỉ lệ khung hình

`clientContext` lặp lại ở nhiều vị trí trong CÙNG 1 body — luôn là:

```
[null, 22, null, null, null, <projectId>, null, null, null, null, [<reCAPTCHA>, 1]]
```

Enum tỉ lệ — **ẢNH và VIDEO KHÁC NHAU**, xác minh bằng diff 2 capture thật
(16:9 vs 9:16) ngày 2026-09-04:

| | 16:9 | 9:16 | vị trí |
|---|---|---|---|
| Ảnh (`ogiZ0b`) | `3` | `2` | `[1][0][4]` |
| Video (`YhhmEf`) | `2` | `1` | `[0][0][2]` |
| Video (`MZZa6b`) | `2` | `1` | `[0][0][3]` |

Ảnh lệch 1 bậc vì có thêm SQUARE/4:3/3:4 — khớp bundle (`_.RPa` chỉ khai
`VIDEO_ASPECT_RATIO_LANDSCAPE`/`_PORTRAIT` cho video). **1:1 / 4:3 / 3:4 CHƯA
verify** — `flow_be.aspect_enum()` cố tình trả `None` cho chúng để rơi về
đường aisandbox thay vì gửi sai tỉ lệ.

**KHÔNG có field số lượng ảnh/lần** — đường aisandbox cũng không gửi (luôn 1
ảnh/lần), nên `output_count` của task không ảnh hưởng body.

### 7.0b `maseQ` — upload ảnh tham chiếu

```
[0]                    clientContext (xem 7.0) — CÓ reCAPTCHA
[1]                    base64 ảnh (KHÔNG tiền tố data:)
[2]                    mimeType, vd "image/png"
[3]                    1
[8]                    fileName
[10], [11]             UUID HOA                ← làm mới
```

Response: `[[<DETAIL_UUID>, <projectId>, <tileId>, "CAE", …]]`

⚠️ **Dùng `payload[0][0]` (DETAIL_UUID)** làm id cho `imageInputs`/
`referenceImages` — KHÔNG phải `payload[0][2]`. Xác minh 2026-09-04: upload 1
ảnh rồi tra listing `Zzl0ze` theo TÊN FILE — `[0][0]` khớp `meta[4]`
(DETAIL_UUID), `[0][2]` khớp `entry[0]` (tile id); và uuid mà capture
imageToImage dùng làm `imageInputs` chính là `meta[4]`. Đây đúng là cái bẫy đã
trả giá 1 lần ở reconcile (xem mục 9).

### 7.1 `Zzl0ze` — liệt kê media project (reconcile bước 1)

```
args = ["projects/{projectId}", null, null, null, [1]]
```

Response: `[null, [entry, entry, …]]`, mỗi `entry`:

```
[ tileId, null, null,
  [ title, [ts_sec, ts_nsec], null, null, DETAIL_UUID, GEN_MARKER, [ts2] ],
  projectId ]
```

⚠️ **BẪY LỚN NHẤT:** uuid để gọi `as29s` là **`meta[4]`** (`DETAIL_UUID` —
khớp uuid nhúng trong CDN URL), **KHÔNG PHẢI `entry[0]`** (`entry[0]` là
tile/card id nội bộ UI). Dùng `entry[0]` thì **130/130 candidate trả `null`**.

`GEM_MARKER` (`meta[5]`) khác `null` = item **đã qua generation** — verify
100% trên 275 entry thật (upload thô luôn `null`).

### 7.2 `as29s` — chi tiết 1 media (reconcile bước 2)

```
args = ["{DETAIL_UUID}"]
```

Response chứa: prompt ĐẦY ĐỦ (kèm `TASK_{id}:` nếu có) + **URL CDN đã ký** cho
cả ảnh lẫn video, **cùng format CDN cũ hệt**:

```
https://flow-content.google/{image|video}/{uuid}?Expires=…&KeyName=labs-flow-prod-cdn-key&Signature=…
```

→ backend server-side tải được luôn, không phải đổi gì.

⚠️ **Quan sát:** `as29s` dường như chỉ phục vụ item **RECENT** — uuid ~10 ngày
trước trả `null` dù request hợp lệ 100%. Không phải vấn đề cho reconcile (luôn
xét media vừa submit), và setting `reconcile_lookback_secs` (2h) tự né vùng này.

### 7.3 `ogiZ0b` — textToImage

```
[0]                    null
[1][0][3]              seed (int)              ← làm mới
[1][0][4]              3                       enum TỈ LỆ ẢNH (16:9=3, 9:16=2)
[1][0][5]              "GEM_PIX_2"             model key
[1][0][7]              clientContext:
    [7][1]             22                      (tool enum)
    [7][5]             projectId
    [7][10][0]         reCAPTCHA token         ← làm mới
    [7][10][1]         1
[1][0][8][0][0][0]     prompt                  ← thay
[1][0][12], [1][0][13] UUID HOA                ← làm mới
[2]                    1
[3]                    clientContext (lặp lại — cùng token/projectId)
[4][0]                 UUID HOA                ← làm mới
```

### 7.4 `ogiZ0b` — imageToImage

Giống hệt 7.3, **cộng thêm** mảng `imageInputs`:

```
[1][0][2]              [[ mediaUuid, null, null, null, 1 ]]
[1][0][2][0][0]        uuid ảnh tham chiếu (chữ thường)
```

⚠️ uuid này **chữ thường** giống projectId — logic "làm mới" phải phân biệt,
nếu thay bừa mọi uuid chữ thường thành projectId sẽ hỏng ref mà không báo lỗi.

### 7.5 `YhhmEf` — textToVideo

```
[0][0][0][2][0][0][0]  prompt                  ← thay
[0][0][1]              "abra_t2v_8s"           model key video
[0][0][2]              2
[0][0][4][4], [0][0][4][5]   UUID HOA          ← làm mới
[1]                    clientContext:
    [1][1]             22
    [1][5]             projectId
    [1][10][0]         reCAPTCHA token         ← làm mới
[2][0]                 UUID HOA                ← làm mới
[2][1]                 2
```

**KHÔNG có seed.** RPC `BatchAsync…` → POST trả về NGAY (chỉ xác nhận đã tạo
workflow), **không có CDN URL** trong response.

### 7.6 `MZZa6b` — ingredientToVideo

```
[0][0][0][2][0][0][0]  prompt                  ← thay
[0][0][1][0][1]        uuid ảnh nguyên liệu    ← GIỮ NGUYÊN
[0][0][2]              "abra_r2v_8s"           model key (r2v)
[0][0][3]              2
[0][0][5][4], [0][0][5][5]   UUID HOA          ← làm mới
[1]                    clientContext (như 7.5)
[2][0]                 UUID HOA                ← làm mới
```

---

## 8. Giá trị PHẢI làm mới mỗi lần gọi

| Nhóm | Nhận diện | Vì sao |
|---|---|---|
| reCAPTCHA | chuỗi > 200 ký tự, không có khoảng trắng | hết hạn ~2 phút |
| UUID request/batch | uuid dạng **HOA** | trùng thì bị từ chối |
| seed | int > 1.000.000 | dùng lại ra đúng kết quả cũ |
| projectId | uuid **chữ thường** khớp `source-path` lúc capture | project có thể đã luân chuyển |

**KHÔNG được đụng:** uuid ảnh tham chiếu (cũng chữ thường) — chỉ thay đúng
project id cũ trích từ `source-path`, không thay mọi uuid chữ thường.

Code: `tests/utils/flow_rpc.py::collect_dynamic()` / `refresh_args()` — dò
theo **đặc điểm giá trị**, không hardcode path, nên vẫn đúng nếu Google đảo
field.

---

## 9. Bẫy đã dính (đừng lặp lại)

1. **Đoán schema từ bundle minify** — proto lồng nhau, decode ra field number
   là đoán. Đã trả giá 2 lần trong 1 ngày. Cách đúng: **capture request thật**
   rồi replay.
2. **`entry[0]` vs `meta[4]`** ở `Zzl0ze` — xem §7.1.
3. **`window.__rpcCap` sống theo vòng đời TRANG** — không reload giữa 2 lần
   chạy script thì request lần trước vẫn còn trong buffer; capture bắt phải
   request CŨ mà không báo gì (body giống hệt, tưởng "ref ảnh không vào").
   → xoá buffer NGAY TRƯỚC submit + lấy request MỚI NHẤT.
4. **Thay mọi uuid chữ thường thành projectId** → ghi đè uuid ảnh tham chiếu.
5. **Timeout** — `_batchexecute_call()` mặc định 20s đủ cho RPC đọc, nhưng RPC
   sinh ẢNH chờ tới lúc render xong → cần 90-180s. (RPC video là async nên
   trả về nhanh.)
6. **Mint reCAPTCHA trước khi harvest session** → token mất theo lần reload
   mà `_batchexecute_harvest_session()` thực hiện.

---

7. **`parse_image_results()` phải loại uuid ảnh THAM CHIẾU.** Response
   `ogiZ0b` của imageToImage chứa CẢ uuid ảnh đầu vào lẫn uuid ảnh sinh ra —
   quét uuid trần sẽ trả 2 kết quả cho 1 lần sinh, khiến sản xuất tải chính
   ảnh ref về rồi lưu như ảnh đã sinh. Bug thật, bắt được lúc verify
   2026-09-04. Nay `parse_image_results(payload, exclude=...)` ưu tiên uuid có
   CDN URL trong response và loại `projectId` + mọi uuid ref.

8. **Đừng để fallback im lặng.** Mọi nhánh bỏ qua batchexecute trong
   `worker.py` đều phải log — bản đầu có 4 nhánh `return None` không log, gặp
   đúng 1 lần "chạy lần 1 hỏng, lần 2 chạy" mà không có manh mối nào trong log
   (hoá ra harvest session hết 15s). Nay harvest tự thử lại 1 lần và mọi lần
   bỏ qua đều có dòng log.

## 10. Code trong client_tool

| Thứ | Ở đâu |
|---|---|
| Harvest session | `server/worker.py::_batchexecute_harvest_session()` |
| Gọi 1 RPC | `server/worker.py::_batchexecute_call(rpc_id, args, session, timeout=20)` |
| Parse response | `server/worker.py::_batchexecute_parse(raw, rpc_id)` |
| Reconcile (SẢN XUẤT) | `_dom_fetch_project_media()` + `_reconcile_project_media()` |
| Mint reCAPTCHA | `server/worker.py::_get_fresh_recaptcha(action)` |
| Setting cửa sổ lọc | `reconcile_lookback_secs` (mặc định 7200s), trang Cài đặt |
| **Builder body generate** | `server/flow_be.py` (song song `tests/utils/flow_api.py` của aisandbox) |
| Upload (SẢN XUẤT) | `_upload_media_to_flow()` → thử `_upload_media_to_flow_be()` trước |
| Sinh ảnh (SẢN XUẤT) | `_call_image_api_v2()` → thử `_call_image_api_be()` trước |
| Sinh video (SẢN XUẤT) | `_call_video_api()` → thử `_call_video_api_be()` trước |
| Session có cache | `_be_session()` (TTL 600s, tự thử lại harvest 1 lần) |
| Backoff khi hỏng | `_be_note_fail()` — 3 lần liên tiếp → nghỉ 300s, dùng thẳng aisandbox |
| Công tắc | `generate_via_batchexecute` (mặc định 1), trang Cài đặt |

### Test

| Script | Luồng | rpcid |
|---|---|---|
| `tests/_test_textToImage_new.py` | textToImage | `ogiZ0b` |
| `tests/_test_imageToImage_new.py` | imageToImage | `ogiZ0b` |
| `tests/_test_textToVideo_new.py` | textToVideo | `YhhmEf` |
| `tests/_test_ingredientToVideo_new.py` | ingredientToVideo | `MZZa6b` |
| `tests/_verify_reconcile_new.py` | reconcile sản xuất | `Zzl0ze`+`as29s` |
| `tests/_verify_new_api_image.py` | tải + kiểm ảnh vừa tạo | `as29s` |
| `tests/_extract_rpcid_map.py` | trích lại bảng rpcid từ bundle | — |

Máy dùng chung: `tests/utils/flow_rpc.py` (`FlowRpcTest` — capture/replay).
Chọn profile: `tests/utils/profile_target.py` (mặc định profile 25, tự mở
Chrome nếu chưa chạy, không cần bật GUI client_tool).

```bash
# học shape 1 lần (tốn 1 lượt quota)
python tests/_test_textToImage_new.py --capture

# chạy lại tuỳ ý
python tests/_test_textToImage_new.py --replay -p "a red fox in the snow"

# xem capture đã lưu (không tốn quota)
python tests/_test_textToImage_new.py --show
```

---

## 11. Đã verify / chưa verify

**Đã chạy THẬT, có kết quả kiểm chứng:**

| Luồng | Kết quả |
|---|---|
| reconcile (`Zzl0ze`+`as29s`) | liệt kê đúng 275 media; **phục hồi 8 task video thật** đang mắc kẹt trong DB production |
| textToImage (`ogiZ0b`) | ảnh 175 KB JPEG đúng prompt |
| imageToImage (`ogiZ0b`) | ảnh 370 KB — giữ đúng nhân vật từ ref, đổi cảnh |
| textToVideo (`YhhmEf`) | tạo workflow, tiêu đề tự sinh khớp prompt |
| ingredientToVideo (`MZZa6b`) | tạo workflow, giữ đúng ref |
| `HTrJv` (GetModels) | trả catalog model đầy đủ |
| `maseQ` (UploadImage) | upload ảnh thật, uuid trả về khớp `meta[4]` trong listing |
| Enum tỉ lệ ảnh & video | diff 2 capture 16:9 vs 9:16 (xem 7.0) |

**Đã chạy THẬT qua ĐÚNG entry point sản xuất** (2026-09-04, không gọi tắt vào
hàm `_be`) — `tests/_verify_generate_be.py`:

| Entry point | Đi đường | Kết quả |
|---|---|---|
| `_upload_media_to_flow()` | `maseQ` | uuid hợp lệ |
| `_call_image_api_v2()` textToImage | `ogiZ0b` | 1 CDN URL |
| `_call_image_api_v2()` imageToImage | `ogiZ0b` + `imageInputs` | 1 CDN URL (sau khi vá lọc uuid ref) |
| `_call_video_api()` componentsToVideo | `MZZa6b` | workflowId + mediaId |
| `_call_video_api()` textToVideo | `YhhmEf` | workflowId + mediaId |

**Chưa verify:**

- `eb1hJf` (frameToVideo), `nprQif` (ảnh đầu+cuối), `jwpduf` (poll status) —
  mới biết tên method, chưa gọi thử. `frameToVideo` vì vậy vẫn đi aisandbox.
- Tỉ lệ **1:1 / 4:3 / 3:4** — chưa có capture, cố tình không đoán; task dùng
  các tỉ lệ này tự rơi về aisandbox.
- Ý nghĩa vài field enum còn lại (`clientContext[1]=22`, `[2][1]=2` ở video) —
  giữ nguyên giá trị capture.
- **Chưa chạy qua hàng đợi heartbeat thật** — mọi verify đều gọi trực tiếp
  entry point với task dựng tay, chưa để 1 task thật của backend chạy trọn vẹn
  tới `done`.

---

*Cập nhật 2026-09-04. Xem thêm `CLAUDE.md` §11.44 (đổi DOM), §11.45 (đổi
reconcile) và §11.46 (đổi generate).*
