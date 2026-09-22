"""Catalog model THẬT của Google Flow — đọc lúc chạy qua RPC `HTrJv`
(`/FlowService.GetModels`), thay cho bảng key hardcode/DB.

VÌ SAO CẦN (2026-09-22, bug thật `RPC_ERROR_CODE_5` = gRPC NOT_FOUND):
Google đã đổi cách đặt tên model key — giờ key MÃ HOÁ CẢ thời lượng LẪN tỉ lệ
khung hình. Phép suy `t2v`->`r2v` mà `model_catalog.resolve_video_model()` /
`flow_api.resolve_ingredient_video_model_key()` đang dùng chỉ còn đúng với họ
Lite:

    Veo 3.1 - Lite        -> veo_3_1_r2v_lite                ✔ có thật
    Veo 3.1 - Lite [LP]   -> veo_3_1_r2v_lite_low_priority   ✔ có thật
    Veo 3.1 - Fast        -> veo_3_1_r2v_fast                ✘ KHÔNG TỒN TẠI
                             (thật: ..._fast_landscape / ..._fast_portrait)
    Veo 3.1 - Quality     -> veo_3_1_r2v                     ✘ họ này KHÔNG có
                             key r2v nào cả

Đúng 2 dòng CLAUDE.md §11.34 tự ghi là "suy ra theo pattern, CHƯA có capture
xác nhận" — và cả hai đều sai. Đọc catalog thật thì tự miễn nhiễm với việc
Google đổi tên, VÀ với khác biệt giữa các tài khoản (catalog là PER-ACCOUNT).

⚠️ KHÔNG hardcode thêm bảng key nào vào đây. Mọi thứ suy từ catalog.

SHAPE payload (đã verify trên tài khoản thật, xem docs/FLOW_BATCHEXECUTE_API.md):

    payload[0][4] = danh sách HỌ (family) VIDEO
    payload[0][5] = danh sách HỌ ẢNH
    mỗi family   = [nhãn, [entry...], enabled, familyId]
    mỗi entry    = [key, ...]  với các ô đã dò được:
        [0]  key            vd 'veo_3_1_r2v_fast_landscape'
        [4]  chi phí theo HẠNG gói: [[hạng, [[None, credit]]], ...]
             hạng KHÔNG dùng được key này thì phần tử là [hạng, [None, []]]
        [12] tỉ lệ hỗ trợ: [[1]]=dọc 9:16, [[2]]=ngang 16:9, [[2,1]]=cả hai
        [16] thời lượng (giây)

2 ô [12]/[16] được dò bằng cách ĐỐI CHIẾU với chính tên key (`_4s`/`_6s` và
`_landscape`/`_portrait`) — khớp 56/56 và 19/19, xem CHANGELOG 2026-09-22.
`1=dọc, 2=ngang` cũng trùng đúng enum `flow_be.aspect_enum()` đang dùng.

⚠️ BIẾN THỂ `_ultra` — KHÔNG SUY ĐƯỢC TỪ CATALOG, PHẢI TỰ DÒ:
Mỗi key 8s có 2 bản song song, chia theo HẠNG gói của tài khoản (đọc ô [4]):
`veo_3_1_r2v_fast_landscape` cho hạng 1-2 (20 credit), `..._landscape_ultra`
cho hạng 3 (10 credit). Gửi nhầm bản thì Google trả
`PUBLIC_ERROR_MODEL_ACCESS_DENIED`.

Đã THỬ suy hạng từ cờ `enabled` của family (giả thuyết: family bật ⟺ tài khoản
dùng được ≥1 key trong đó) — **THỰC NGHIỆM BÁC BỎ**: cách đó kết luận tài khoản
`an6669373` KHÔNG phải hạng 3, nhưng dò thật thì chính nó lại dùng được
`veo_3_1_r2v_fast_landscape_ultra` (key chỉ hạng 3 mới dùng). Tức `enabled`
KHÔNG phải cờ phân quyền. Vì vậy module này **không đoán hạng nữa** — trả về
DANH SÁCH ứng viên theo thứ tự, để `worker` thử lần lượt rồi NHỚ biến thể nào
tài khoản dùng được (`prefer_ultra`). Bị từ chối quyền thì Google chặn TRƯỚC
khi sinh nội dung nên lần thử hụt KHÔNG tốn quota.
"""
import re

# Vị trí các ô trong 1 entry model (xem docstring)
_IDX_KEY, _IDX_RATIOS, _IDX_DURATION = 0, 12, 16

# Enum tỉ lệ — GIỐNG `flow_be.aspect_enum(video=True)` (1=dọc, 2=ngang)
_RATIO_ENUM = {'16:9': 2, '9:16': 1}

KIND_T2V = 't2v'          # textToVideo
KIND_R2V = 'r2v'          # imageToVideo / componentsToVideo (ảnh nguyên liệu)
KIND_I2V = 'i2v'          # frameToVideo — chỉ ảnh đầu
KIND_I2V_FL = 'i2v_fl'    # frameToVideo — ảnh đầu + ảnh cuối


def kind_of(key):
    """Phân loại 1 model key về đúng mode task. `None` = không phải key sinh
    video (extend/upsample/edit...)."""
    k = key or ''
    if '_r2v' in k:
        return KIND_R2V
    if '_i2v' in k or 'interpolation' in k:
        # `_fl` / `first_last` / `interpolation` = dùng CẢ ảnh đầu và ảnh cuối
        if '_fl' in k or 'first_last' in k or 'interpolation' in k:
            return KIND_I2V_FL
        return KIND_I2V
    if '_t2v' in k:
        return KIND_T2V
    return None


def parse_duration(raw):
    """`'8s'` / `8` / `'8'` -> `8`. Rỗng/không đọc được -> `None` (bỏ qua ràng
    buộc thời lượng, không chặn task)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw) or None
    m = re.search(r'(\d+)', str(raw))
    return int(m.group(1)) if m else None


def _ratios_of(entry):
    v = entry[_IDX_RATIOS] if len(entry) > _IDX_RATIOS else None
    if isinstance(v, list) and len(v) == 1 and isinstance(v[0], list):
        return set(x for x in v[0] if isinstance(x, int))
    return set()


def _norm_label(s):
    """Chuẩn hoá nhãn model để so khớp: bỏ emoji/ký tự trang trí, gộp khoảng
    trắng, về chữ thường."""
    s = re.sub(r'[^0-9a-zA-Z\[\]\.\- ]+', ' ', str(s or ''))
    return re.sub(r'\s+', ' ', s).strip().lower()


def parse_catalog(payload):
    """payload của `HTrJv` -> `{'video': [...], 'image': [...]}`.
    `None` nếu shape không như mong đợi (caller tự fallback)."""
    try:
        root = payload[0]
        fams_video, fams_image = root[4], root[5]
    except Exception:
        return None
    if not isinstance(fams_video, list):
        return None

    def build(fams):
        out = []
        for fam in fams or []:
            try:
                label, entries, enabled, fid = fam[0], fam[1], fam[2], fam[3]
            except Exception:
                continue
            items = []
            for e in entries or []:
                if not (isinstance(e, list) and e and isinstance(e[0], str)):
                    continue
                items.append({
                    'key': e[_IDX_KEY],
                    'kind': kind_of(e[_IDX_KEY]),
                    'duration': e[_IDX_DURATION] if len(e) > _IDX_DURATION else None,
                    'ratios': _ratios_of(e),
                })
            # ⚠️ `enabled` KHÔNG phải cờ phân quyền (xem docstring module) —
            # giữ lại chỉ để hiển thị, KHÔNG dùng để loại key.
            out.append({'label': label, 'family_id': fid,
                        'enabled': enabled is True, 'entries': items})
        return out

    return {'video': build(fams_video), 'image': build(fams_image)}


def _find_family(catalog, label, group='video'):
    """Khớp nhãn model. ƯU TIÊN KHỚP CHÍNH XÁC — bắt buộc, vì có cặp nhãn mà
    cái này là TIỀN TỐ của cái kia ("Veo 3.1 - Lite" nằm trong "Veo 3.1 - Lite
    [Lower Priority]"); khớp lỏng trước sẽ chọn nhầm model một cách âm thầm.
    Cùng bẫy đã ghi ở CLAUDE.md §11.30 cho `MODEL_MATCH_JS`."""
    want = _norm_label(label)
    if not want:
        return None
    fams = catalog.get(group) or []
    for f in fams:
        if _norm_label(f['label']) == want:
            return f
    # Chỉ chấp nhận khớp chứa-nhau khi DUY NHẤT 1 ứng viên (không mơ hồ)
    loose = [f for f in fams
             if want in _norm_label(f['label']) or _norm_label(f['label']) in want]
    return loose[0] if len(loose) == 1 else None


def resolve_candidates(catalog, label, kind, aspect_ratio='16:9',
                       duration=None, prefer_ultra=False):
    """Danh sách model key khớp (nhãn + mode + tỉ lệ + thời lượng), ĐÃ SẮP
    theo thứ tự nên thử.

    Trả `(keys, note)` — `keys` rỗng nghĩa là không chọn được, `note` là lý do
    (để caller ghi log rồi tự fallback).

    `prefer_ultra` = biến thể mà tài khoản này ĐÃ ĐƯỢC XÁC NHẬN dùng được (do
    `worker` học qua lần thử trước); chưa biết thì để `False`."""
    fam = _find_family(catalog, label)
    if not fam:
        return [], 'không có họ model nào tên "%s" trong catalog' % label

    cands = [e for e in fam['entries'] if e['kind'] == kind]
    if not cands:
        return [], ('họ "%s" KHÔNG có key nào cho mode %s — model này không '
                    'làm được loại task đó' % (fam['label'], kind))

    want_ratio = _RATIO_ENUM.get(str(aspect_ratio).strip())
    if want_ratio is not None:
        by_ratio = [e for e in cands if not e['ratios'] or want_ratio in e['ratios']]
        if not by_ratio:
            return [], ('họ "%s" không có key %s nào hỗ trợ tỉ lệ %s'
                        % (fam['label'], kind, aspect_ratio))
        cands = by_ratio

    note_dur = ''
    if duration:
        exact = [e for e in cands if e['duration'] == duration]
        if exact:
            cands = exact
        else:
            avail = sorted(set(e['duration'] for e in cands if e['duration']))
            if avail:
                # Không có đúng số giây yêu cầu -> lấy gần nhất + BÁO RÕ, thay vì
                # để task chết hẳn (vd r2v của mọi họ Veo 3.1 CHỈ có 8s).
                near = min(avail, key=lambda d: (abs(d - duration), d))
                cands = [e for e in cands if e['duration'] == near]
                note_dur = ('; CANH BAO khong co %ss cho %s -> dung %ss '
                            '(ho nay chi co: %s)' % (duration, kind, near, avail))

    # Thứ tự thử: biến thể đã biết tài khoản dùng được trước -> không phải
    # 360p -> tên ngắn nhất (bản "gốc" thay vì biến thể) — ỔN ĐỊNH giữa các lần.
    cands.sort(key=lambda e: (('_ultra' not in e['key']) if prefer_ultra
                              else ('_ultra' in e['key']),
                              '_360p' in e['key'], len(e['key']), e['key']))
    note = ('catalog %s/%s %ss %s%s'
            % (fam['label'], kind, cands[0]['duration'], aspect_ratio, note_dur))
    return [e['key'] for e in cands], note


def resolve(catalog, label, kind, aspect_ratio='16:9', duration=None,
            prefer_ultra=False):
    """Bản 1-key của `resolve_candidates()` (ứng viên tốt nhất)."""
    keys, note = resolve_candidates(catalog, label, kind, aspect_ratio,
                                    duration, prefer_ultra)
    return (keys[0] if keys else None), note
