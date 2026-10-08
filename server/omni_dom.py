"""Tạo video Omni (Google Vids) bằng GIAO DIỆN — "chế độ DOM" của làn Omni.

Song song với đường API (`omni_be.py` + `OmniLane._kickoff`): thao tác đúng như người
dùng — mở panel "Tạo đoạn video bằng AI", gõ prompt, chỉnh chip cài đặt (độ phân giải /
khổ / thời lượng), bấm "Tạo", chờ video mới hiện ra rồi lấy URL từ thẻ <video>.

Locator đo thật trên Vids tiếng Việt (2026-10-06) — ưu tiên `aria-label`:
  * mở panel : [aria-label="Tạo đoạn video bằng AI"]  (nút "Video AI" bên phải)
  * ô prompt : [role=textbox][aria-label^="Mô tả video"]
  * chip     : button aria-label "Cài đặt tạo video: Omni, 720p, Khổ ngang, 10 giây"
  * khổ      : 2 nút role=radio trong popup chip (ngang, dọc)
  * thời lượng: input[type=range][role=slider] (3-10)
  * độ phân giải: dropdown trong popup chip, mục "720p"/"1080p"
  * gửi      : button aria-label "Tạo" (KHÔNG có role=tab — tab "Tạo" thì có)
  * đang tạo : khối hiện "N%" + nút "Huỷ"; xong thì thẻ video mới (<video src=…download…>)

Ảnh thành phần: `add_ingredients()` bấm nút thêm thành phần rồi gán file thẳng vào
<input type=file> (móc `HTMLInputElement.click` để không hiện hộp thoại Windows). Tab Omni là tab NỀN nên bật
`Emulation.setFocusEmulationEnabled` để chuột/bàn phím CDP có tác dụng.
"""
from __future__ import annotations

import json
import random
import re
import time

# Chip cài đặt: "Cài đặt tạo video: Omni, 720p, Khổ ngang, 10 giây" (vi) /
# "Video settings: Omni, 720p, Landscape, 10 seconds" (en, chưa đo thật) — regex không dính chữ cố định.
_CHIP_RE = re.compile(r':\s*([^,]+),\s*(\d{3,4}p),\s*([^,]+),\s*(\d+)', re.I)
_PORTRAIT_WORDS = ('khổ dọc', 'portrait', 'vertical', 'tall')
_LANDSCAPE_WORDS = ('khổ ngang', 'landscape', 'horizontal', 'wide')

# Biến thể nhãn theo ngôn ngữ giao diện (vi + en). Nhãn tiếng Anh là SUY LUẬN — không có tài
# khoản tiếng Anh để đo. Phần quan trọng (nút mở panel, ô prompt, chip, nút Tạo) còn có
# cách chọn KHÔNG phụ thuộc chữ (lớp CSS / role / vị trí) nên vẫn chạy nếu nhãn sai.
_RAIL_BTN = '.appsSketchyContentLibraryRailToolbarButtonContainerRefreshed'
_RE_CLEAR = '/^(xoá|xóa|clear|clear all|reset)$/i'
_RE_CLOSE = '/^(đóng|close|dismiss|got it|no thanks)$/i'
_RE_CREATE = '/^(tạo|create|generate)$/i'
_RE_BUSY = '/(huỷ|hủy|cancel|stop generating)/i'
_RE_AGREE = '/^(đồng ý|agree|accept|i agree|ok|got it)$/i'
_BUSY_WORDS = ('Huỷ', 'Hủy', 'Cancel')
_ERR_WORDS = ('không thể tạo', 'đã xảy ra lỗi', 'vi phạm', 'chính sách', 'thử lại sau',
              "couldn't generate", 'something went wrong')


# Nhãn ô prompt đổi theo trạng thái: lúc trống/đã đính ảnh thành phần.
_TB = ("[...document.querySelectorAll('[role=textbox]')].find(e=>{const q=e.getBoundingClientRect();"
       "return q.width>0&&q.x>window.innerWidth*0.45})")


class OmniDomError(RuntimeError):
    pass


class OmniContentRejected(OmniDomError):
    """Vids từ chối nội dung — thẻ video hỏng hiện lý do ("That request looks like it goes
    against our terms…"). KHÔNG nên thử lại cùng nội dung: task cần đá về draft để sửa."""
    def __init__(self, reason: str):
        super().__init__(f'Vids từ chối nội dung: {reason}')
        self.reason = reason


# Thẻ video HỎNG trong feed có lớp CSS "Failedvideogenerationthumbnail…" (không phụ thuộc ngôn ngữ).
_FAILED_MSG_SEL = '[class*=FailedvideogenerationthumbnailErrorMessage]:not([class*=MessageContainer])'


class OmniDom:
    def __init__(self, tab, log=None):
        self.tab = tab
        self._log = log or (lambda lv, m: None)

    # ── tiện ích CDP ────────────────────────────────────────────────────────
    def _eval(self, expr: str, timeout: float = 30):
        return self.tab.evaluate(expr, timeout=timeout, await_promise=False)

    def _find_rect(self, finder_js: str):
        """finder_js: biểu thức JS trả phần tử (hoặc null) → toạ độ tâm {x,y} (CSS px)
        hoặc None. KHÔNG scrollIntoView: cuộn làm popup của chip đóng/lệch chỗ."""
        return self._eval(
            "(()=>{const e=(%s);if(!e)return null;"
            "const r=e.getBoundingClientRect();"
            "return r.width&&r.height?{x:r.left+r.width/2,y:r.top+r.height/2}:null})()" % finder_js)

    def _mouse(self, x: float, y: float):
        """Di chuột tới (đợi hover kịp ghi nhận) rồi nhấn/thả — widget Wiz của Vids bỏ qua
        cú bấm nếu chuột "nhảy" tới và bấm ngay."""
        ev = lambda t, **k: self.tab.send('Input.dispatchMouseEvent', {  # noqa: E731
            'type': t, 'x': x, 'y': y, 'button': k.get('button', 'none'),
            'buttons': k.get('buttons', 0), 'clickCount': k.get('clickCount', 0)})
        ev('mouseMoved')
        time.sleep(0.3)
        ev('mousePressed', button='left', buttons=1, clickCount=1)
        time.sleep(0.1)
        ev('mouseReleased', button='left', buttons=0, clickCount=1)
        time.sleep(0.2)

    def _click(self, finder_js: str, what: str, wait: float = 0.8) -> bool:
        r = self._find_rect(finder_js)
        if not r:
            return False
        self._mouse(r['x'], r['y'])
        time.sleep(wait)
        return True

    def _need_click(self, finder_js: str, what: str, wait: float = 0.8):
        if not self._click(finder_js, what, wait):
            raise OmniDomError(f'DOM Omni: không thấy {what}')

    @staticmethod
    def _vis(sel: str) -> str:
        """Phần tử ĐANG HIỆN đầu tiên khớp selector (loại bản ẩn cùng aria-label)."""
        return ("[...document.querySelectorAll(%s)].find(e=>e.offsetParent!==null"
                "&&e.getBoundingClientRect().width>0)" % json.dumps(sel))

    # ── panel + trạng thái ──────────────────────────────────────────────────
    def _panel_open(self) -> bool:
        return bool(self._eval("!!(%s)" % _TB))

    def open_panel(self):
        self.tab.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
        if self._panel_open():
            return
        # Hộp thoại chào mừng che giao diện — đóng nếu có.
        if self._click("[...document.querySelectorAll('button,[role=button]')].find(b=>b.getBoundingClientRect().width>0&&"
                       "%s.test((b.getAttribute('aria-label')||b.innerText||'').trim().normalize('NFC')))" % _RE_CLOSE,
                       'nút Đóng hộp thoại', 1.2):
            self._log('info', 'DOM Omni: đã đóng hộp thoại chào')
        # Nút "Video AI" = nút ĐẦU TIÊN của cột công cụ bên phải (lớp CSS, không phụ thuộc ngôn ngữ);
        # dự phòng theo nhãn vi/en.
        self._need_click("(%s)||(%s)" % (
            "[...document.querySelectorAll(%s)].filter(e=>e.getBoundingClientRect().width>0)"
            ".sort((a,b)=>a.getBoundingClientRect().y-b.getBoundingClientRect().y)[0]" % json.dumps(_RAIL_BTN),
            "[...document.querySelectorAll('[aria-label]')].find(e=>e.getBoundingClientRect().width>0&&"
            "/^(tạo đoạn video bằng ai|(create|generate) (an? )?ai video( clip)?|ai video)/i.test(e.getAttribute('aria-label')))"),
            'nút "Video AI"', 2.5)
        for _ in range(10):
            if self._panel_open():
                break
            time.sleep(0.5)
        if not self._panel_open():
            raise OmniDomError('DOM Omni: mở panel "Video AI" không được (không thấy ô prompt)')
        # Tab "Tạo" (mặc định đã chọn) — bấm cho chắc.
        self._click("[...document.querySelectorAll('[role=tab]')].find(e=>e.getBoundingClientRect().width>0&&"
                    "%s.test((e.getAttribute('aria-label')||e.innerText||'').trim().normalize('NFC')))" % _RE_CREATE,
                    'tab Tạo', 0.5)

    def _chip_state(self) -> dict | None:
        label = self._eval(
            "(()=>{const e=[...document.querySelectorAll('button')].find(b=>b.offsetParent!==null&&"
            "/\\b(720|1080)p\\b/.test(b.getAttribute('aria-label')||''));"
            "return e?e.getAttribute('aria-label'):null})()")
        if not label:
            return None
        m = _CHIP_RE.search(label)
        if not m:
            return {'raw': label}
        return {'raw': label, 'model': m.group(1).strip(), 'res': m.group(2).lower(),
                'aspect': m.group(3).strip().lower(), 'dur': int(m.group(4))}

    def _failed_msgs(self) -> list:
        """Lý do của các thẻ video HỎNG đang hiện trong feed (theo thứ tự DOM)."""
        return self._eval(
            "[...document.querySelectorAll(%s)].filter(e=>e.getBoundingClientRect().width>0)"
            ".map(e=>(e.innerText||'').trim()).filter(Boolean)" % json.dumps(_FAILED_MSG_SEL)) or []

    def _remove_failed_thumbs(self):
        """Dọn thẻ hỏng khỏi feed (nút "Remove from feed" / "Xoá khỏi nguồn cấp") để không lẫn
        với task kế tiếp. Best-effort."""
        finder = ("[...document.querySelectorAll('[class*=FailedvideogenerationthumbnailErrorMessage]:not([class*=MessageContainer])')]"
                  ".map(m=>{let p=m;for(let i=0;i<6&&p;i++){p=p.parentElement;"
                  "const b=p&&[...p.querySelectorAll('button')].find(b=>b.getBoundingClientRect().width>0&&"
                  "/(remove|xoá|xóa|delete)/i.test((b.innerText||'').trim().normalize('NFC')));if(b){b.scrollIntoView({block:'center'});return b}}return null})"
                  ".find(Boolean)")
        try:
            for _ in range(6):
                if not self._click(finder, 'nút Remove thẻ hỏng', 0.8):
                    break
        except Exception:
            pass

    def _video_srcs(self) -> set:
        return set(self._eval(
            "[...document.querySelectorAll('video')].map(v=>v.currentSrc||v.src||'').filter(Boolean)") or [])

    # ── gõ prompt ───────────────────────────────────────────────────────────
    def _set_prompt(self, prompt: str):
        self._need_click(_TB, 'ô prompt', 0.5)
        # Xoá chữ cũ (nút "Xoá" chỉ hiện khi có chữ) rồi gõ.
        self._click("[...document.querySelectorAll('button')].find(b=>b.offsetParent!==null&&"
                    "%s.test((b.innerText||'').trim().normalize('NFC')))" % _RE_CLEAR, 'nút Xoá', 0.6)
        self._need_click(_TB, 'ô prompt', 0.4)
        self.tab.send('Input.insertText', {'text': prompt})
        time.sleep(0.8)
        got = self._eval("(()=>{const e=%s;return e?(e.innerText||'').trim().length:0})()"
                         % _TB) or 0
        if got < max(1, len(prompt.strip()) * 0.5):
            raise OmniDomError(f'DOM Omni: gõ prompt thất bại ({got}/{len(prompt.strip())} ký tự)')

    # ── thành phần (ảnh đính kèm prompt) ────────────────────────────────────
    # Móc `HTMLInputElement.click`: Vids tạo <input type=file> tạm rồi .click() để mở hộp
    # thoại Windows — móc lại để KHÔNG hiện hộp thoại, giữ input đó rồi tự gán file.
    _HOOK_JS = ("(()=>{if(window.__oiHooked)return 1;window.__oiHooked=1;"
                "const o=HTMLInputElement.prototype.click;"
                "HTMLInputElement.prototype.click=function(){"
                "if(this.type==='file'){window.__oiInput=this;return}return o.apply(this,arguments)};"
                "return 1})()")
    _ADD_RE = "/(thành phần|thêm ảnh|thêm hình|tham chiếu|ingredient|add (image|photo|reference))/i"
    _UPLOAD_RE = "/(tải (lên|ảnh|tệp)|từ (máy|thiết bị)|upload|computer|device)/i"

    def _visible_labels(self) -> list:
        return self._eval(
            "[...document.querySelectorAll('button,[role=button],[role=menuitem],[role=tab],[role=option]')]"
            ".filter(b=>b.getBoundingClientRect().width>0)"
            ".map(b=>(b.getAttribute('aria-label')||b.innerText||'').trim().slice(0,40)).filter(Boolean)") or []

    _THUMBS = "[...document.querySelectorAll('[class*=inputPreviewItem] img')].filter(i=>i.getBoundingClientRect().width>0)"

    def _count_attached(self) -> int:
        """Số ảnh thành phần đang đính kèm (thẻ xem trước trong composer)."""
        return int(self._eval("(%s).length" % self._THUMBS) or 0)

    def _add_one(self, data: bytes, mime: str, name: str):
        import base64
        n0 = self._count_attached()
        self._eval("window.__oiInput=null")
        # Ô "Thành phần" (lúc chưa có ảnh) / nút "Thêm" (đã có ảnh) chỉ hiện khi ô prompt đang focus.
        self._need_click(_TB, 'ô prompt', 0.5)
        for t in ('keyDown', 'keyUp'):                       # con trỏ về cuối chữ
            self.tab.send('Input.dispatchKeyEvent', {'type': t, 'key': 'End', 'code': 'End',
                                                     'windowsVirtualKeyCode': 35, 'modifiers': 2})
        add = ("[...document.querySelectorAll('div,button,span,[role=button]')].filter(e=>{const q=e.getBoundingClientRect();"
               "return q.width>0&&q.x>window.innerWidth*0.45&&/^(thành phần|thêm|ingredients?|add)$/i.test("
               "(e.innerText||'').trim().normalize('NFC'))})"
               ".sort((a,b)=>b.getBoundingClientRect().width-a.getBoundingClientRect().width)[0]")
        if not self._click(add, 'vùng Thành phần / nút Thêm', 1.5):
            raise OmniDomError('DOM Omni: không thấy vùng "Thành phần". Đang hiện: '
                               + ' | '.join(self._visible_labels()[:40]))
        if not self._eval("!!window.__oiInput"):           # có menu trung gian → chọn mục tải lên
            # Menu nhỏ "Hình đại diện | Tải lên" — KHÔNG lấy nút "Tải lên" của thanh bên phải (x > 88% màn hình).
            up = ("[...document.querySelectorAll('div,button,span,[role=menuitem],[role=button]')].filter(e=>{"
                  "const q=e.getBoundingClientRect();return q.width>0&&q.x<window.innerWidth*0.88&&q.x>window.innerWidth*0.45&&"
                  "e.children.length<4&&%s.test((e.innerText||'').trim().normalize('NFC'))&&(e.innerText||'').trim().length<14})"
                  ".sort((a,b)=>a.getBoundingClientRect().width*a.getBoundingClientRect().height-"
                  "b.getBoundingClientRect().width*b.getBoundingClientRect().height).pop()" % self._UPLOAD_RE)
            self._click(up, 'mục tải ảnh lên', 1.2)
        for _ in range(8):
            if self._eval("!!window.__oiInput"):
                break
            time.sleep(0.4)
        if not self._eval("!!window.__oiInput"):
            raise OmniDomError('DOM Omni: không bắt được ô chọn tệp. Đang hiện: '
                               + ' | '.join(self._visible_labels()[:40]))
        payload = [{'b64': base64.b64encode(data).decode(), 'mime': mime or 'image/png', 'name': name}]
        self._eval(
            "(()=>{const f=%s,dt=new DataTransfer();for(const x of f){const s=atob(x.b64),u=new Uint8Array(s.length);"
            "for(let i=0;i<s.length;i++)u[i]=s.charCodeAt(i);dt.items.add(new File([u],x.name,{type:x.mime}))}"
            "const el=window.__oiInput;el.files=dt.files;"
            "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));"
            "return 1})()" % json.dumps(payload), timeout=60)
        time.sleep(1.5)
        # Lần đầu Vids hiện hộp chính sách ảnh — đồng ý nếu có.
        self._click("[...document.querySelectorAll('button')].find(b=>b.getBoundingClientRect().width>0&&"
                    "%s.test((b.innerText||'').trim().normalize('NFC')))" % _RE_AGREE, 'nút Đồng ý', 1.0)
        for _ in range(30):                                # chờ ảnh tải xong và hiện thẻ
            if self._count_attached() > n0:
                return
            time.sleep(1)
        raise OmniDomError(f'DOM Omni: đính "{name}" nhưng không thấy thẻ ảnh (đếm {self._count_attached()}). '
                           'Đang hiện: ' + ' | '.join(self._visible_labels()[:40]))

    def add_ingredients(self, images: list):
        """images: [(bytes, mime, filename)] — tối đa 3, đính TỪNG ảnh một (ô chọn tệp của Vids
        chỉ nhận 1 ảnh/lần). Gán file thẳng vào <input type=file> bị chặn hộp thoại Windows."""
        if not images:
            return
        self._eval(self._HOOK_JS)
        for data, mime, name in images:
            self._add_one(data, mime, name)
        self._log('info', f'DOM Omni: đã đính {len(images)} ảnh thành phần')

    # ── chip cài đặt ────────────────────────────────────────────────────────
    _CHIP = ("[...document.querySelectorAll('button')].find(b=>b.offsetParent!==null&&"
             "/\\b(720|1080)p\\b/.test(b.getAttribute('aria-label')||''))")
    _RADIOS = ("[...document.querySelectorAll('button[role=radio]')].filter(b=>b.getBoundingClientRect().width>0)"
               ".sort((a,b)=>a.getBoundingClientRect().x-b.getBoundingClientRect().x)")

    def _popup_open(self) -> bool:
        # popup đóng thì 2 radio vẫn còn trong DOM nhưng rộng 0px (offsetParent không phân biệt được)
        return bool(self._eval("[...document.querySelectorAll('button[role=radio]')]"
                               ".filter(b=>b.getBoundingClientRect().width>0).length>=2"))

    def _ensure_popup(self):
        if not self._popup_open():
            self._need_click(self._CHIP, 'chip cài đặt tạo video', 1.0)

    def _fix_resolution(self, want_res: str):
        self._ensure_popup()
        trig = ("[...document.querySelectorAll('div,span,button,[role=button],[role=combobox]')]"
                ".filter(e=>e.offsetParent!==null&&/^(720p|1080p)$/.test((e.innerText||'').trim())"
                "&&e.getBoundingClientRect().width>40)"
                ".sort((a,b)=>a.getBoundingClientRect().y-b.getBoundingClientRect().y)[0]")
        self._need_click(trig, 'dropdown độ phân giải', 0.9)
        opt = ("[...document.querySelectorAll('span,div,li,[role=option]')]"
               ".filter(e=>e.offsetParent!==null&&(e.innerText||'').trim()===%s&&e.children.length===0)"
               ".sort((a,b)=>b.getBoundingClientRect().y-a.getBoundingClientRect().y)[0]" % json.dumps(want_res))
        self._need_click(opt, f'mục {want_res}', 0.9)

    def _fix_aspect(self, portrait: bool):
        self._ensure_popup()
        idx = 1 if portrait else 0              # 2 radio xếp trái→phải: ngang, dọc
        self._need_click("(%s)[%d]" % (self._RADIOS, idx), 'khổ khung hình', 0.8)

    def _fix_duration(self, duration: int) -> bool:
        self._ensure_popup()
        return bool(self._eval(
            "(()=>{const s=document.querySelector('input[type=range][role=slider]');if(!s)return false;"
            "const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "set.call(s,%d);s.dispatchEvent(new Event('input',{bubbles:true}));"
            "s.dispatchEvent(new Event('change',{bubbles:true}));return true})()" % duration))

    def _close_popup(self):
        """Đóng popup chip. ⚠️ KHÔNG bấm vào ô prompt — popup phủ lên đó, cú bấm trúng
        radio "Khổ dọc" làm đổi khổ ngược lại. Bấm lại chip (toggle) đóng được."""
        if self._popup_open():
            self._click(self._CHIP, 'chip cài đặt tạo video', 0.8)
        for _ in range(3):
            if not self._popup_open():
                return
            for t in ('keyDown', 'keyUp'):
                self.tab.send('Input.dispatchKeyEvent', {'type': t, 'key': 'Escape', 'code': 'Escape',
                                                         'windowsVirtualKeyCode': 27})
            time.sleep(0.5)

    def _wait_chip(self, pred, secs: float = 3.0):
        """Chip cập nhật nhãn trễ sau khi bấm — đợi tới khi `pred(trạng_thái)` đúng."""
        t0 = time.time()
        while time.time() - t0 < secs:
            if pred(self._chip_state() or {}):
                return True
            time.sleep(0.3)
        return False

    @staticmethod
    def _is_portrait(aspect_text: str) -> bool:
        t = (aspect_text or '').lower()
        return any(w in t for w in _PORTRAIT_WORDS)

    def _apply_settings(self, aspect_portrait: bool, resolution: str, duration: int):
        want_res = resolution.lower()
        got: dict = {}
        for _ in range(8):                       # mỗi vòng sửa 1 mục lệch rồi đọc lại chip
            got = self._chip_state() or {}
            self._log('info', f'DOM Omni chip: {got.get("raw")}')
            if got.get('res') != want_res:
                self._log('info', f'  → đổi độ phân giải {want_res}')
                self._fix_resolution(want_res)
                self._wait_chip(lambda c: c.get('res') == want_res)
            elif self._is_portrait(got.get('aspect', '')) != aspect_portrait:
                self._log('info', f'  → đổi khổ {"dọc" if aspect_portrait else "ngang"}')
                self._fix_aspect(aspect_portrait)
                self._wait_chip(lambda c: self._is_portrait(c.get('aspect', '')) == aspect_portrait)
            elif got.get('dur') != duration:
                if not self._fix_duration(duration):
                    self._log('warn', 'DOM Omni: không thấy thanh thời lượng — giữ giá trị mặc định')
                    break
                time.sleep(0.6)
            else:
                break
        self._close_popup()
        got = self._chip_state() or {}
        if got.get('res') != want_res or self._is_portrait(got.get('aspect', '')) != aspect_portrait:
            raise OmniDomError(f'DOM Omni: chip chưa đúng cài đặt — muốn {want_res}/{"dọc" if aspect_portrait else "ngang"}, '
                               f'đang {got.get("raw")}')
        if got.get('dur') != duration:
            self._log('warn', f'DOM Omni: thời lượng đang {got.get("dur")}s (muốn {duration}s)')

    # ── gõ giả (chế độ API) ─────────────────────────────────────────────────
    _TEXTBOX = None

    def _backspace(self, n: int = 1):
        for _ in range(n):
            for t in ('keyDown', 'keyUp'):
                self.tab.send('Input.dispatchKeyEvent', {'type': t, 'key': 'Backspace', 'code': 'Backspace',
                                                         'windowsVirtualKeyCode': 8})
            time.sleep(0.04 + random.random() * 0.06)

    @staticmethod
    def _neighbor(ch: str) -> str:
        rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
        low = ch.lower()
        for row in rows:
            i = row.find(low)
            if i >= 0:
                out = row[i + 1] if i + 1 < len(row) else row[i - 1]
                return out.upper() if ch.isupper() else out
        return ch

    def fake_type(self, prompt: str):
        """Gõ prompt vào ô "Mô tả video" như người thật (gõ sai rồi Backspace) mà KHÔNG
        bấm Tạo — lệnh tạo vẫn đi bằng API. Dùng khi `api_fake_typing` bật. Best-effort."""
        try:
            self.open_panel()
            self.fake_clear()
            self._need_click(_TB, 'ô prompt', 0.4)
            head, rest = prompt[:60], prompt[60:]
            typos = 0
            for ch in head:
                if ch.isalpha() and random.random() < 0.07 and typos < 4:
                    self.tab.send('Input.insertText', {'text': self._neighbor(ch)})
                    time.sleep(0.2 + random.random() * 0.3)
                    self._backspace(1)
                    time.sleep(0.1 + random.random() * 0.15)
                    typos += 1
                self.tab.send('Input.insertText', {'text': ch})
                time.sleep(0.06 + random.random() * 0.10)
            for k in range(0, len(rest), 40):
                chunk = rest[k:k + 40]
                self.tab.send('Input.insertText', {'text': chunk})
                time.sleep(0.12 + random.random() * 0.2)
                if len(chunk) >= 12 and random.random() < 0.25:
                    n = random.randint(3, 8)
                    time.sleep(0.3 + random.random() * 0.4)
                    self._backspace(n)
                    time.sleep(0.15 + random.random() * 0.2)
                    self.tab.send('Input.insertText', {'text': chunk[-n:]})
            self._log('info', f'⌨ Đã gõ prompt vào ô Video AI ({len(prompt)} ký tự) — không bấm Tạo')
        except Exception as e:
            self._log('warn', f'Gõ prompt giả (Omni) lỗi, bỏ qua: {e}')

    def fake_clear(self):
        """Xoá chữ trong ô prompt (nút "Xoá", dự phòng Ctrl+A + Backspace)."""
        try:
            n = self._eval("(()=>{const e=%s;return e?(e.innerText||'').trim().length:0})()"
                           % _TB) or 0
            if not n:
                return
            if not self._click("[...document.querySelectorAll('button')].find(b=>b.offsetParent!==null&&"
                               "%s.test((b.innerText||'').trim().normalize('NFC')))" % _RE_CLEAR, 'nút Xoá', 0.5):
                self._click(_TB, 'ô prompt', 0.3)
                for typ in ('keyDown', 'keyUp'):
                    self.tab.send('Input.dispatchKeyEvent', {'type': typ, 'key': 'a', 'code': 'KeyA',
                                                             'windowsVirtualKeyCode': 65, 'modifiers': 2})
                self._backspace(1)
        except Exception as e:
            self._log('warn', f'Xoá prompt giả (Omni) lỗi, bỏ qua: {e}')

    # ── tạo ─────────────────────────────────────────────────────────────────
    def generate(self, prompt: str, aspect_portrait: bool, resolution: str,
                 duration: int, timeout: float = 300, images: list | None = None) -> str:
        """Trả URL video mới (contribution-rt…download…). Raise OmniDomError nếu hỏng.
        `images`: [(bytes, mime, tên file)] ảnh thành phần đính kèm trước khi gõ prompt."""
        self.open_panel()
        before = self._video_srcs()
        failed_before = len(self._failed_msgs())
        self._set_prompt(prompt)
        if images:
            self.add_ingredients(images)
        self._apply_settings(aspect_portrait, resolution, duration)
        send = ("[...document.querySelectorAll('button')].find(b=>b.offsetParent!==null&&"
                "%s.test((b.getAttribute('aria-label')||'').normalize('NFC'))&&b.getAttribute('role')!=='tab'&&!b.disabled)" % _RE_CREATE)
        self._need_click(send, 'nút gửi "Tạo"', 1.5)
        t0 = time.time()
        saw_progress = False
        while time.time() - t0 < timeout:
            time.sleep(2.5)
            txt = (self._eval("document.body.innerText.normalize('NFC')") or '')
            busy = any(w in txt for w in _BUSY_WORDS)
            saw_progress = saw_progress or busy
            # Thẻ video HỎNG mới (Vids từ chối nội dung / lỗi tạo) — đọc lý do rồi đá task về draft.
            fails = self._failed_msgs()
            if len(fails) > failed_before:
                reason = fails[0]
                self._log('warn', f'DOM Omni: thẻ video hỏng — "{reason[:200]}"')
                self._remove_failed_thumbs()
                raise OmniContentRejected(reason)
            new = [s for s in self._video_srcs() - before if 'usercontent.google.com' in s]
            if new and not busy:
                return new[0]
            if not busy and (time.time() - t0) > 15 and not new:
                low = txt.lower()
                hit = next((w for w in _ERR_WORDS if w in low), None)
                if hit or saw_progress:
                    raise OmniDomError(f'DOM Omni: tạo thất bại ({hit or "tiến trình biến mất, không có video mới"})')
        raise OmniDomError(f'DOM Omni: quá {int(timeout)}s chưa có video mới')
