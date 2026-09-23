"""Flask API endpoints (/api/selenium/...) — CRUD profile/extension, điều khiển
worker (start/stop/open/close), logs, status, master switch, health check."""

import json, os, threading, time

from flask import jsonify, request

from .config import app, log, FLOW_SERVER, PORT, PROFILES_DIR, LOGS_DIR, _profile_log, _DEFAULT_SERVER_SETTINGS, req_lib
from .managers import pm, em
from .client_identity import client_headers, get_identity, set_client_name
from .auth_client import get_auth_cookies
from .chrome_utils import (
    _chrome_debug_port, _detect_chrome_portables, CHROME_DEBUG_PORT_BASE, CHROME_PORTABLE_DIR,
    _chrome_alive_on_port,
    _build_chrome_options, _usable_profile_dir, _make_service, _patch_selenium_pool_size,
)
from .state import (
    _workers, _login_drivers, _desired_veo3, _sleep_until_by_pid,
    _pending_cache, _pending_fail_streak,
)
from . import state as _state  # _master_task_intake_enabled bị REASSIGN — qua module-qualified access
from .dispatcher import (
    _start_worker, _stop_worker, _reap_dead_workers, _veo3_dispatcher_tick, _set_master_task_intake,
    _fetch_pending_by_mode, _is_in_quiet_hours,
)
from .local_settings import get_local_settings, update_local_settings, reset_local_settings
from .run_hours import in_run_hours, summarize_run_hours

# ═════════════════════════════════════════════════════════════════════════════
# API Endpoints
# ═════════════════════════════════════════════════════════════════════════════

# ── Profiles CRUD ─────────────────────────────────────────────────────────────

@app.route('/api/selenium/profiles', methods=['GET'])
def list_profiles():
    _reap_dead_workers()
    profiles = pm.list()
    for p in profiles:
        p['worker_running'] = p['id'] in _workers
        p['worker_waiting'] = p['id'] in _desired_veo3 and p['id'] not in _workers
    return jsonify(profiles)


@app.route('/api/selenium/profiles', methods=['POST'])
def create_profile():
    d = request.json or {}
    name = (d.get('profile_name') or '').strip()
    if not name:
        return jsonify({'error': 'profile_name required'}), 400
    try:
        new_id = pm.create(
            name,
            email                    = d.get('account_email', ''),
            password                 = d.get('account_password', ''),
            display_name             = d.get('display_name', ''),
            project_url              = d.get('project_url', ''),
            task_mode                = d.get('task_mode', 'all'),
            worker_mode              = d.get('worker_mode', 'api'),
            notes                    = d.get('notes', ''),
            gemini_attach_timeout    = d.get('gemini_attach_timeout', 180),
            gemini_response_timeout = d.get('gemini_response_timeout', 300),
            max_concurrent           = d.get('max_concurrent', 1),
            enabled                  = d.get('enabled', 1),
            gemini_max_concurrent_tabs  = d.get('gemini_max_concurrent_tabs', 1),
            gemini_tab_switch_interval  = d.get('gemini_tab_switch_interval', 0.5),
            proxy_server                = d.get('proxy_server', ''),
            run_hours                   = d.get('run_hours', ''),
        )
        return jsonify({'id': new_id, 'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/selenium/profiles/<int:pid>', methods=['PATCH'])
def update_profile(pid):
    pm.update(pid, **(request.json or {}))
    return jsonify({'ok': True})


@app.route('/api/selenium/profiles/<int:pid>', methods=['DELETE'])
def delete_profile(pid):
    _stop_worker(pid)
    pm.delete(pid, delete_data=request.args.get('deleteData') == 'true')
    return jsonify({'ok': True})


@app.route('/api/selenium/profiles/<int:pid>/clear_data', methods=['POST'])
def clear_profile_data(pid):
    """(2026-08-10, mở rộng theo yêu cầu user "còn gì cần xóa thì xóa sạch
    coi như 1 profile trắng") — xoá SẠCH toàn bộ `profile_dir` (không chỉ
    cache/cookie/history) — xem `managers.py::pm.clear_browser_data()`.
    Profile PHẢI đang ĐÓNG (không worker chạy, không login browser mở) —
    xoá file trong lúc Chrome giữ handle dễ lỗi/corrupt profile, mirror cách
    `delete_profile()` ở trên luôn `_stop_worker()` trước khi đụng filesystem."""
    if pid in _workers:
        return jsonify({'error': 'Worker đang chạy — Stop worker trước khi làm mới profile'}), 409
    if pid in _login_drivers:
        return jsonify({'error': 'Login browser đang mở — đóng trước khi làm mới profile'}), 409
    try:
        result = pm.clear_browser_data(pid)
        return jsonify({'ok': True, **result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Worker control ────────────────────────────────────────────────────────────

@app.route('/api/selenium/profiles/<int:pid>/start', methods=['POST'])
def start_worker(pid):
    if not _state._master_task_intake_enabled:
        return jsonify({'error': 'master_switch_off'}), 423
    profile = pm.get(pid)
    if not profile:
        return jsonify({'error': 'not found'}), 404
    # 2026-07-17 — cột `enabled` đã có sẵn (dùng bởi auto-scale, xem client_tool/
    # CLAUDE.md §11.9) nhưng trước đây CHỈ chặn auto-scale, không chặn bấm Start
    # thủ công ở đây — 1 profile "tắt" vẫn start được qua nút Start, không nhất
    # quán. Chặn ngay tại đây để trả lỗi rõ ràng thay vì để rơi xuống
    # _start_worker() (dispatcher.py, cũng có check tương tự làm lưới an toàn
    # cho nhánh promote qua dispatcher tick, không đi qua route này).
    if not profile.get('enabled', 1):
        return jsonify({'error': 'profile_disabled'}), 403

    # gemini/chatgpt không bị giới hạn max_concurrent_veo3_profiles — start ngay như cũ.
    if profile.get('worker_mode') in ('gemini', 'chatgpt'):
        err = _start_worker(profile)
        if err == 'already_running':
            return jsonify({'error': 'already running'}), 409
        if err:
            return jsonify({'error': err}), 400
        return jsonify({'ok': True})

    # Veo3-like (api/dom/gemini_video/gemini_image) — do dispatcher quyết định
    # start ngay hay 'waiting' theo server setting max_concurrent_veo3_profiles
    # (xem _veo3_dispatcher_tick). gemini_video (2026-08-07)/gemini_image
    # (2026-09-09) KHÔNG cần project_url (dùng gemini.google.com), nhưng vẫn đi
    # qua CÙNG cơ chế slot/_desired_veo3 như VEO3 vì cạnh tranh CÙNG hàng đợi
    # task video/ảnh tương ứng — chỉ VEO3 (api/dom) thật sự bắt buộc phải có
    # project_url.
    # (2026-09-14) Bật "chạy theo project + email" thì link Flow lấy theo project
    # Nano Banana của từng lô task — profile không cần project_url riêng.
    _bind_on = bool(int(get_local_settings().get('bind_tasks_to_project_email', 0) or 0))
    if (profile.get('worker_mode') not in ('gemini_video', 'gemini_image')
            and not profile.get('project_url') and not _bind_on):
        return jsonify({'error': 'project_url_required'}), 400
    # (2026-09-17) Profile VEO có khung giờ chạy riêng mà giờ này không nằm trong
    # khung → không mở Chrome (auto-scale cũng sẽ đóng ngay), báo rõ lý do.
    if not in_run_hours(profile):
        return jsonify({'error': 'outside_run_hours',
                        'run_hours': summarize_run_hours(profile.get('run_hours'))}), 409
    if pid in _workers:
        return jsonify({'error': 'already running'}), 409
    _desired_veo3.add(pid)
    _sleep_until_by_pid.pop(pid, None)
    pm.set_status(pid, 'waiting')
    _veo3_dispatcher_tick()
    return jsonify({'ok': True})


@app.route('/api/selenium/profiles/<int:pid>/stop', methods=['POST'])
def stop_worker_ep(pid):
    # Stop thủ công = bỏ hẳn "ý muốn chạy" — nếu không, dispatcher sẽ tự start
    # lại ở tick kế tiếp vì vẫn còn trong _desired_veo3.
    _desired_veo3.discard(pid)
    _sleep_until_by_pid.pop(pid, None)
    if pid not in _workers:
        profile = pm.get(pid)
        if profile and profile.get('status') in ('waiting', 'sleeping'):
            pm.set_status(pid, 'offline', clear_task=True, pid=None)
            return jsonify({'ok': True})
        return jsonify({'error': 'not running'}), 404
    _stop_worker(pid)
    return jsonify({'ok': True})


@app.route('/api/selenium/profiles/<int:pid>/clear_sleep', methods=['POST'])
def clear_sleep_ep(pid):
    """Xoá thời gian ngủ (2026-09-23) — đánh thức NGAY profile đang 'sleeping'
    thay vì chờ hết `error_sleep_secs`. Còn trong _desired_veo3 (VEO3 đang muốn
    chạy) → về 'waiting' để dispatcher cấp slot lại ngay; ngoài ra → 'idle'
    (auto-scale tự mở lại khi có backlog)."""
    profile = pm.get(pid)
    if not profile:
        return jsonify({'error': 'not found'}), 404
    had_timer = _sleep_until_by_pid.pop(pid, None) is not None
    if profile.get('status') != 'sleeping' and not had_timer:
        return jsonify({'error': 'Profile không ở trạng thái ngủ'}), 409
    if pid not in _workers:
        new_status = 'waiting' if pid in _desired_veo3 else 'idle'
        pm.set_status(pid, new_status, clear_task=True, pid=None)
    _profile_log(pid, 'info', '⏰ Đã xoá thời gian ngủ thủ công — đánh thức profile')
    if pid in _desired_veo3:
        _veo3_dispatcher_tick()
    return jsonify({'ok': True})


@app.route('/api/selenium/profiles/<int:pid>/open', methods=['POST'])
def open_browser(pid):
    """Mở Chrome với profile này để đăng nhập Google — không chạy task."""
    profile = pm.get(pid)
    if not profile:
        return jsonify({'error': 'not found'}), 404

    # Chặn nếu worker đang dùng cùng profile_dir — tránh Chrome conflict
    if pid in _workers:
        return jsonify({'error': 'Worker đang chạy trên profile này — Stop worker trước khi đăng nhập.'}), 409

    # Chặn nếu login browser đã mở cho profile này
    if pid in _login_drivers:
        try:
            _login_drivers[pid].current_url  # kiểm tra driver còn sống
            return jsonify({'error': 'Login browser đang mở — hãy đóng cửa sổ đó trước.'}), 409
        except Exception:
            # Driver đã chết (user đóng Chrome) → dọn dẹp
            _login_drivers.pop(pid, None)

    # ⚠️ (2026-09-04, fix bug "tự mở 1 đống tab") Dict `_login_drivers` ở trên
    # KHÔNG đủ: login browser mở với `detach=True` nên Chrome SỐNG TIẾP sau khi
    # thread kết thúc, mà `finally` lại pop entry đi (và dict cũng mất sạch khi
    # restart client_tool). Mất dấu ⇒ chạy tiếp xuống dưới LAUNCH Chrome mới với
    # CÙNG `--user-data-dir` ⇒ Chrome chuyển yêu cầu sang instance đang chạy và
    # MỞ THÊM 1 CỬA SỔ/TAB. Bấm nhiều lần ⇒ một đống tab. Hỏi thẳng DevTools
    # port để biết SỰ THẬT thay vì tin trí nhớ của tiến trình.
    _existing_port = _chrome_debug_port(pid)
    if _chrome_alive_on_port(_existing_port):
        _profile_log(pid, 'info',
                     f'Chrome của profile này đã mở sẵn (port {_existing_port}) — '
                     f'không mở thêm instance (tránh Chrome đẻ thêm tab vào cửa sổ cũ).')
        return jsonify({
            'error': f'Chrome của profile này đang mở sẵn (port {_existing_port}) — '
                     f'dùng cửa sổ đó để đăng nhập, hoặc đóng hẳn nó rồi bấm lại.'
        }), 409

    def _open():
        from selenium import webdriver

        _patch_selenium_pool_size()

        port = _chrome_debug_port(pid)

        # Chọn Chrome Portable nếu có
        portable_exe = ''
        portables    = _detect_chrome_portables()
        if portables:
            idx          = (pid - 1) % len(portables)
            portable_exe = portables[idx]['exe']

        opts = _build_chrome_options(
            profile_dir     = _usable_profile_dir(profile),
            debug_port      = port,
            portable_exe    = portable_exe,
            load_extensions = True,
            detach          = True,   # Chrome sống khi driver.quit() — worker sẽ attach
            proxy           = profile.get('proxy_server') or '',
            profile_id      = pid,
        )

        driver = webdriver.Chrome(service=_make_service(chrome_binary=portable_exe), options=opts)
        _login_drivers[pid] = driver

        url = profile.get('project_url') or 'https://accounts.google.com'
        driver.get(url)
        _profile_log(pid, 'info', f'Login browser opened → {url} (port={port}, detach=True)')

        # Giữ thread sống cho đến khi Chrome đóng hoặc worker lấy quyền kiểm soát
        try:
            while True:
                time.sleep(3)
                _ = driver.current_url   # throws nếu Chrome đã đóng / session bị lấy
        except Exception:
            pass
        finally:
            _login_drivers.pop(pid, None)
            _profile_log(pid, 'info', 'Login browser session ended — Chrome có thể vẫn còn mở')

    threading.Thread(target=_open, daemon=True, name=f'login-{pid}').start()
    return jsonify({'ok': True, 'message': f'Browser opened cho "{profile["profile_name"]}" — đăng nhập rồi đóng để lưu session'})


@app.route('/api/selenium/profiles/<int:pid>/close', methods=['POST'])
def close_login_browser(pid):
    """Đóng login browser đang mở cho profile này."""
    driver = _login_drivers.get(pid)
    if not driver:
        return jsonify({'error': 'Không có login browser đang mở'}), 404
    try:
        driver.quit()
    except Exception:
        pass
    _login_drivers.pop(pid, None)
    return jsonify({'ok': True})


# ── Profile Logs ──────────────────────────────────────────────────────────────

@app.route('/api/selenium/profiles/<int:pid>/logs', methods=['GET'])
def get_profile_logs(pid):
    """Proxy tới backend (2026-07-17 — không còn get_conn() trực tiếp, xem CHANGELOG
    "client_tool: bỏ kết nối DB trực tiếp")."""
    limit = min(int(request.args.get('limit', 200)), 1000)
    level = request.args.get('level', '')   # filter: info/ok/warn/error
    params = {'limit': limit}
    if level:
        params['level'] = level
    # (2026-08-26) PHAI gui kem session cookie — route log ben backend gio yeu
    # cau dang nhap + group 'Tool Media' (truoc day khong gate auth gi ca nen
    # ai cung doc/xoa duoc log cua moi worker, xem worker_profiles.py).
    r = req_lib.get(f'{FLOW_SERVER}/api/worker_profiles/{pid}/logs', params=params,
                     headers=client_headers(), cookies=get_auth_cookies(), timeout=10)
    return jsonify((r.json() or {}).get('logs', []))


@app.route('/api/selenium/profiles/<int:pid>/logs', methods=['DELETE'])
def clear_profile_logs(pid):
    req_lib.delete(f'{FLOW_SERVER}/api/worker_profiles/{pid}/logs',
                   headers=client_headers(), cookies=get_auth_cookies(), timeout=10)
    # Xóa cả file log
    log_path = LOGS_DIR / f'profile_{pid}.log'
    try:
        log_path.unlink(missing_ok=True)
    except Exception:
        pass
    return jsonify({'ok': True})


@app.route('/api/selenium/profiles/<int:pid>/logfile', methods=['GET'])
def get_profile_logfile(pid):
    """Trả về nội dung file log (tail N dòng cuối)."""
    tail = min(int(request.args.get('tail', 500)), 5000)
    log_path = LOGS_DIR / f'profile_{pid}.log'
    if not log_path.exists():
        return jsonify({'lines': [], 'path': str(log_path)})
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return jsonify({'lines': lines[-tail:], 'total': len(lines),
                        'path': str(log_path)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Token status (debug) ──────────────────────────────────────────────────────

@app.route('/api/selenium/profiles/<int:pid>/tokens', methods=['GET'])
def get_token_status(pid):
    if pid not in _workers:
        return jsonify({'running': False})
    w, _ = _workers[pid]
    t = w._tokens
    return jsonify({
        'running':    True,
        'hasAuth':    bool(t.get('authorization')),
        'hasRecaptcha': bool(t.get('recaptchaToken')),
        'ageSeconds': round(w.token_age_secs()),
        'authPreview': (t.get('authorization') or '')[:30],
    })


# ── Extensions ───────────────────────────────────────────────────────────────

@app.route('/api/selenium/extensions', methods=['GET'])
def list_extensions():
    return jsonify(em.list())


@app.route('/api/selenium/extensions', methods=['POST'])
def add_extension():
    d    = request.json or {}
    path = (d.get('ext_path') or '').strip()
    name = (d.get('ext_name') or os.path.basename(path) or 'extension').strip()
    if not path:
        return jsonify({'error': 'ext_path required'}), 400
    try:
        new_id = em.add(name, path)
        return jsonify({'id': new_id, 'ok': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/selenium/extensions/<int:eid>', methods=['PATCH'])
def toggle_extension(eid):
    em.set_enabled(eid, bool((request.json or {}).get('enabled', True)))
    return jsonify({'ok': True})


@app.route('/api/selenium/extensions/<int:eid>', methods=['DELETE'])
def remove_extension(eid):
    em.remove(eid)
    return jsonify({'ok': True})


@app.route('/api/selenium/extensions/verify', methods=['POST'])
def verify_extension():
    path     = (request.json or {}).get('ext_path', '').strip()
    manifest = os.path.join(path, 'manifest.json')
    if not os.path.isfile(manifest):
        return jsonify({'ok': False, 'error': f'manifest.json không tìm thấy: {path}'}), 400
    try:
        with open(manifest, encoding='utf-8') as f:
            m = json.load(f)
        return jsonify({'ok': True, 'name': m.get('name', ''), 'version': m.get('version', '')})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


# ── Status & Health ───────────────────────────────────────────────────────────

@app.route('/api/selenium/status', methods=['GET'])
def get_status():
    try:
        profiles = pm.list()
    except Exception as e:
        log.warning(f'[get_status] pm.list() failed: {e}')
        return jsonify({'profiles': [], 'active_workers': [], 'flow_server': FLOW_SERVER, 'error': str(e)})
    try:
        for p in profiles:
            pid = p['id']
            p['worker_running'] = pid in _workers
            p['login_open']     = pid in _login_drivers
            p['debug_port']     = _chrome_debug_port(pid)
            if pid in _workers:
                w, _ = _workers[pid]
                p['has_tokens'] = w.has_tokens()
                p['token_age']  = round(w.token_age_secs())
                p.update(w.live_info())
            elif p.get('status') == 'sleeping':
                # Worker thread đã thoát (xem _handle_task_error) — sleepUntil chỉ còn
                # sống trong dict module-level, không còn object worker để hỏi live_info().
                wake_at = _sleep_until_by_pid.get(pid)
                if wake_at:
                    p['sleepUntil'] = wake_at
    except Exception as e:
        log.warning(f'[get_status] worker info failed: {e}')
    return jsonify({
        'profiles':             profiles,
        'active_workers':       list(_workers.keys()),
        'flow_server':          FLOW_SERVER,
        'master_switch_enabled': _state._master_task_intake_enabled,
    })


@app.route('/api/selenium/settings_debug', methods=['GET'])
def get_settings_debug():
    """Snapshot đầy đủ mọi setting/tín hiệu dispatcher THẬT ĐANG DÙNG — thêm
    2026-07-17 theo yêu cầu user ("show thêm các setting default hoặc server
    gửi xuống để check") sau khi debug vụ auto-scale chỉ chạy 1/2 profile dù
    server cho phép 2.

    CẬP NHẬT cùng ngày: `local_settings` KHÔNG còn đồng bộ từ FLOW_SERVER nữa
    (xem local_settings.py) — đây giờ là settings CỤC BỘ thật, có thể sửa qua
    `PATCH /api/selenium/local_settings`. `local_defaults` chỉ còn ý nghĩa "giá
    trị factory-reset" (dùng bởi nút Reset trên GUI), không phải fallback-khi-
    chưa-fetch-được như trước."""
    local_settings = get_local_settings()
    pending        = _fetch_pending_by_mode()
    now = time.time()
    return jsonify({
        'local_defaults':      _DEFAULT_SERVER_SETTINGS,
        'local_settings':      local_settings,
        'pending':             pending,
        'pending_age_secs':    (round(now - _pending_cache['fetched_at'], 1)
                                 if _pending_cache['fetched_at'] else None),
        'pending_fail_streak': _pending_fail_streak['count'],
        'desired_veo3':          list(_desired_veo3),
        'active_workers':        list(_workers.keys()),
        'master_switch_enabled': _state._master_task_intake_enabled,
        'quiet_hours_active':    _is_in_quiet_hours(local_settings),
    })


@app.route('/api/selenium/local_settings', methods=['GET', 'PATCH'])
def local_settings_ep():
    """Đọc/sửa settings CỤC BỘ (2026-07-17) — xem local_settings.py. PATCH nhận
    dict các field muốn đổi (chỉ field nằm trong _DEFAULT_SERVER_SETTINGS được
    áp dụng, field lạ bị bỏ qua), validate/clamp, lưu file, trả lại settings đầy
    đủ sau khi áp dụng."""
    if request.method == 'GET':
        return jsonify(get_local_settings())
    body = request.get_json(silent=True) or {}
    updated = update_local_settings(body)
    return jsonify(updated)


@app.route('/api/selenium/local_settings/reset', methods=['POST'])
def local_settings_reset_ep():
    """Reset settings cục bộ về mặc định gốc (_DEFAULT_SERVER_SETTINGS)."""
    return jsonify(reset_local_settings())


@app.route('/api/selenium/portables', methods=['GET'])
def list_portables():
    """Danh sách Chrome Portable instances đã phát hiện trong CHROME_PORTABLE_DIR."""
    portables = _detect_chrome_portables()
    return jsonify({
        'portables':   portables,
        'count':       len(portables),
        'base_dir':    CHROME_PORTABLE_DIR,
        'port_base':   CHROME_DEBUG_PORT_BASE,
    })


@app.route('/api/selenium/master_switch', methods=['GET'])
def get_master_switch():
    return jsonify({'enabled': _state._master_task_intake_enabled})


@app.route('/api/selenium/master_switch', methods=['POST'])
def set_master_switch():
    enabled = bool((request.json or {}).get('enabled', True))
    _set_master_task_intake(enabled)
    return jsonify({'ok': True, 'enabled': _state._master_task_intake_enabled})


@app.route('/api/selenium/client_identity', methods=['GET'])
def get_client_identity():
    """Danh tính CỤC BỘ của installation client_tool này (2026-07-18) — GUI đọc để
    hiển thị (trang Cài đặt). Xem client_identity.py."""
    return jsonify(get_identity())


@app.route('/api/selenium/client_identity', methods=['PATCH'])
def patch_client_identity():
    """Chỉ cho sửa `client_name` (tên gợi nhớ, KHÔNG phải `client_id` — id cố định
    vĩnh viễn, đổi sẽ khiến backend coi đây là 1 client_tool mới, mất quyền thấy
    profile cũ)."""
    name = (request.json or {}).get('clientName', '')
    return jsonify(set_client_name(name))


@app.route('/api/selenium/health', methods=['GET'])
def health():
    """`db_ok` (2026-07-17) — không còn tự kết nối DB để test; giờ nghĩa là
    "backend (và DB phía sau nó) có phản hồi được không" — proxy 1 request nhẹ tới
    `GET /api/media/health` (backend chính). Xem CHANGELOG "client_tool: bỏ kết
    nối DB trực tiếp"."""
    try:
        r = req_lib.get(f'{FLOW_SERVER}/api/media/health', timeout=5)
        db_ok = r.ok
    except Exception:
        db_ok = False
    portables = _detect_chrome_portables()
    return jsonify({
        'ok':            db_ok,
        'port':          PORT,
        'profiles_dir':  PROFILES_DIR,
        'portables':     len(portables),
        'portable_dir':  CHROME_PORTABLE_DIR,
    })


