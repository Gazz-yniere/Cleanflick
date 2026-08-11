from flask import Flask, render_template, request, jsonify, session, redirect, Response
from functools import wraps
import json, os, re, time, requests, logging, shutil, threading, uuid
from pathlib import Path
from scanner import MediaScanner, MediaFile
from api_handler import APIHandler
from rename_engine import RenameEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['JSON_SORT_KEYS'] = False

scan_clients = set()
scan_last_snapshot = None
scan_watch_lock = threading.Lock()

CONFIG_FILE = "config.json"
HISTORY_FILE = "rename_history.json"

DEFAULT_CONFIG = {
    "tvdb_api_key": "", "tvdb_pin": "",
    "input_path": "/downloads",
    "movie_output_path": "/downloads/movie",
    "tv_output_path": "/downloads/tv_shows",
    "movie_format": "{n} ({y})",
    "tv_format": "{n} - {s00e00} - {t}",
}

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_password():
    return os.environ.get('CLEANFLICK_PASSWORD', '').strip()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if get_password() and not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

# ── History ───────────────────────────────────────────────────────────────────

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Error loading history: {e}")
        return []

def save_history(history):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving history: {e}")
        raise

def append_history(entry):
    h = load_history()
    h.insert(0, entry)
    save_history(h)

# ── Utils ─────────────────────────────────────────────────────────────────────

def _resolve(path):
    if not path or os.path.exists(path):
        return path
    base = os.path.dirname(os.path.abspath(__file__))
    if path.startswith('/'):
        local = os.path.join(base, path.lstrip('/'))
        if os.path.exists(local):
            return local
    return path

def _ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)

def _move_path(source_path, destination_path, job_id=None):
    src, dst = os.path.abspath(source_path), os.path.abspath(destination_path)
    if src == dst:
        return dst

    _ensure_dir(os.path.dirname(dst))
    if os.path.exists(dst):
        os.remove(dst)

    if job_id is None:
        return shutil.move(src, dst)

    file_size = os.path.getsize(src)
    chunk = 16*1024*1024 if file_size > 1_073_741_824 else \
            8*1024*1024  if file_size > 104_857_600    else \
            2*1024*1024  if file_size > 10_485_760     else \
            512*1024

    copied, last_update, last_copied, speed_avg = 0, time.time(), 0, 0
    start = time.time()
    try:
        with open(src, 'rb') as f_in, open(dst, 'wb') as f_out:
            while buf := f_in.read(chunk):
                f_out.write(buf)
                f_out.flush()
                copied += len(buf)
                now = time.time()
                elapsed = now - last_update
                if job_id in move_progress and (elapsed > 0.2 or copied >= file_size):
                    interval_speed = (copied - last_copied) / elapsed if elapsed > 0 else 0
                    speed_avg = interval_speed if speed_avg == 0 else speed_avg * 0.7 + interval_speed * 0.3
                    eta = (file_size - copied) / speed_avg if speed_avg > 0 else 0
                    percent = round((copied / file_size) * 100) if file_size else 0
                    move_progress[job_id].update({
                        'copied': copied, 'file_size': file_size,
                        'percent': percent,
                        'speed': round(speed_avg), 'eta': round(eta),
                        'phase': 'copying', 'finished': False,
                        'elapsed': round(now - start, 2),
                    })
                    last_update, last_copied = now, copied
        try:
            os.fsync(os.open(dst, os.O_RDONLY))
        except Exception:
            pass
        if not os.path.exists(dst):
            raise FileNotFoundError(f"Le fichier de destination n'a pas été créé : {dst}")
        os.remove(src)
        if os.path.exists(src):
            raise FileExistsError(f"Le fichier source n'a pas quitté le dossier source : {src}")
    except Exception:
        if os.path.exists(dst):
            os.remove(dst)
        raise
    return dst

def _series_folder_name(new_name):
    stem = Path(new_name).stem
    m = re.match(r'^(.*?)(?:\s+-\s+(?:S\d{2}E\d{2}|\d+x\d{2})\s+-\s+.*)?$', stem)
    return (m.group(1).strip() if m and m.group(1).strip() else stem)

def _migrate_legacy_paths(cfg):
    movie_path = cfg.pop('movie_path', None)
    tv_path = cfg.pop('tv_path', None)
    if not cfg.get('input_path'):
        for candidate in (movie_path, tv_path):
            if candidate:
                try:
                    cfg['input_path'] = os.path.commonpath([movie_path, tv_path]) if movie_path and tv_path else os.path.dirname(candidate.rstrip('/\\'))
                    break
                except ValueError:
                    cfg['input_path'] = os.path.dirname(candidate.rstrip('/\\'))
    if movie_path and not cfg.get('movie_output_path'):
        cfg['movie_output_path'] = movie_path
    if tv_path and not cfg.get('tv_output_path'):
        cfg['tv_output_path'] = tv_path

def load_config():
    cfg = json.load(open(CONFIG_FILE, 'r', encoding='utf-8')) if os.path.exists(CONFIG_FILE) else DEFAULT_CONFIG.copy()
    _migrate_legacy_paths(cfg)
    cfg.setdefault('input_path', '/downloads')
    cfg.setdefault('movie_output_path', '/movies')
    cfg.setdefault('tv_output_path', '/tv_shows')
    cfg['_input_path'] = _resolve(cfg['input_path'])
    cfg['_movie_output_path'] = _resolve(cfg['movie_output_path'])
    cfg['_tv_output_path'] = _resolve(cfg['tv_output_path'])
    save_config(cfg)
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in cfg.items() if not k.startswith('_')}, f, indent=2, ensure_ascii=False)

# ── App state ─────────────────────────────────────────────────────────────────

config = load_config()
scanner = MediaScanner(config['_input_path'], [config.get('_movie_output_path'), config.get('_tv_output_path')])
api_handler = APIHandler(config)
rename_engine = RenameEngine(config)
scanned_files = []
move_progress = {}  # job_id -> progress dict

# Watch the input folder from the backend and only trigger UI refresh on a true filesystem change.
def _scan_snapshot():
    try:
        files = scanner.scan()
        return tuple(sorted(f.path for f in files))
    except Exception as e:
        logger.warning(f"Scan snapshot error: {e}")
        return tuple()


def _broadcast_scan_refresh():
    payload = json.dumps({'event': 'scan-refresh', 'ts': time.time()})
    dead = []
    for client in list(scan_clients):
        try:
            client.put(payload)
        except Exception:
            dead.append(client)
    for client in dead:
        try:
            scan_clients.remove(client)
        except Exception:
            pass


def _scan_watcher():
    global scan_last_snapshot
    while True:
        try:
            current = _scan_snapshot()
            with scan_watch_lock:
                previous = scan_last_snapshot or tuple()
                added = set(current) - set(previous)
                if added and current:
                    _broadcast_scan_refresh()
                    logger.info(f"New input files detected: {len(added)}")
                scan_last_snapshot = current
        except Exception as e:
            logger.warning(f"Input watcher error: {e}")
        time.sleep(1.5)

threading.Thread(target=_scan_watcher, daemon=True).start()

# ── Progress helper ───────────────────────────────────────────────────────────

def _make_progress(file_size=0, finished=False, error=None, phase='copying', **extra):
    return {'copied': 0, 'file_size': file_size, 'percent': 0 if not finished else 100,
            'speed': 0, 'eta': 0, 'elapsed': 0,
            'finished': finished, 'error': error, 'phase': phase, **extra}

def _run_file_op(job_id, src_path, dst_path, history_entry):
    file_size = os.path.getsize(src_path)
    move_progress[job_id] = _make_progress(
        file_size,
        phase='copying',
        percent=0,
        copied=0,
        speed=0,
        eta=0,
    )

    def _run():
        try:
            _move_path(src_path, dst_path, job_id=job_id)
            source_exists = os.path.exists(src_path)
            target_exists = os.path.exists(dst_path)
            if target_exists and not source_exists:
                verified = True
                move_progress[job_id] = _make_progress(
                    file_size, finished=True, phase='done',
                    copied=file_size, percent=100,
                    new_path=dst_path, new_name=Path(dst_path).name,
                    verified=True,
                    source_exists=False,
                    target_exists=True,
                    destination=dst_path,
                    source=src_path,
                )
                append_history(history_entry)
            else:
                raise FileNotFoundError(f"Vérification impossible après déplacement : {src_path} -> {dst_path}")
        except Exception as e:
            move_progress[job_id] = _make_progress(
                file_size,
                finished=True,
                error=str(e),
                phase='error',
                verified=False,
                source_exists=os.path.exists(src_path),
                target_exists=os.path.exists(dst_path),
            )
    threading.Thread(target=_run, daemon=True).start()
    return file_size

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not get_password():
        return redirect('/')
    if request.method == 'POST':
        if request.form.get('password', '') == get_password():
            session['logged_in'] = True
            return redirect('/')
        return render_template('login.html', error='Mot de passe incorrect')
    return render_template('login.html', error=None)

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/browse')
@login_required
def api_browse():
    import platform
    path = request.args.get('path', '').strip()
    if not path and platform.system() == 'Windows':
        import string
        roots = [f'{d}:\\' for d in string.ascii_uppercase if os.path.exists(f'{d}:\\')]
        return jsonify({'path': '', 'dirs': [], 'roots': roots, 'parent': None})
    path = os.path.abspath(path or '/')
    if not os.path.exists(path):
        p = Path(path)
        while p != p.parent:
            p = p.parent
            if p.exists():
                path = str(p)
                break
    try:
        dirs = sorted([e.name for e in os.scandir(path) if e.is_dir(follow_symlinks=False)], key=str.lower)
        p = Path(path)
        return jsonify({'path': path, 'dirs': dirs, 'roots': [], 'parent': str(p.parent) if p.parent != p else None})
    except PermissionError:
        return jsonify({'path': path, 'dirs': [], 'roots': [], 'parent': str(Path(path).parent)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/scan')
@login_required
def api_scan():
    global scanned_files
    scanned_files = scanner.scan()
    return jsonify([{
        'filename': f.filename, 'path': f.path,
        'media_type': f.media_type, 'title': f.title,
        'season': f.season, 'episode': f.episode, 'year': f.year
    } for f in scanned_files])

@app.route('/api/scan/events')
@login_required
def api_scan_events():
    import queue
    q = queue.Queue()
    scan_clients.add(q)

    def stream():
        try:
            while True:
                try:
                    payload = q.get(timeout=1)
                    yield f'data: {payload}\n\n'
                except Exception:
                    yield ': keepalive\n\n'
        finally:
            try:
                scan_clients.remove(q)
            except Exception:
                pass

    return Response(stream(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })

@app.route('/api/search/auto', methods=['POST'])
@login_required
def api_search_auto():
    d = request.json or {}
    return jsonify(api_handler.search_auto(
        d.get('title', ''), d.get('year'), d.get('filename', ''),
        d.get('season'), d.get('episode'), d.get('media_hint', '')
    ))

@app.route('/api/search/movie', methods=['POST'])
@login_required
def api_search_movie():
    d = request.json
    return jsonify(api_handler.search_movie(d.get('title'), d.get('year')))

@app.route('/api/search/tv', methods=['POST'])
@login_required
def api_search_tv():
    d = request.json
    return jsonify(api_handler.search_tv(d.get('title')))

@app.route('/api/movie/<int:movie_id>')
@login_required
def api_movie_details(movie_id):
    return jsonify(api_handler.get_movie_details(str(movie_id), request.args.get('source', 'tvdb')))

@app.route('/api/tv/<int:tv_id>')
@login_required
def api_tv_details(tv_id):
    return jsonify(api_handler.get_tv_details(
        str(tv_id),
        request.args.get('season', 1, type=int),
        request.args.get('episode', 1, type=int),
        request.args.get('source', 'tvdb')
    ))

@app.route('/api/rename', methods=['POST'])
@login_required
def api_rename():
    d = request.json
    old_path = Path(d.get('path'))
    new_name = d.get('new_name')
    try:
        new_path = old_path.parent / new_name
        _move_path(str(old_path), str(new_path))
        append_history({
            'id': str(uuid.uuid4()), 'op': 'rename',
            'date': time.strftime('%Y-%m-%d %H:%M:%S'),
            'from_path': str(old_path), 'from_name': old_path.name,
            'to_path': str(new_path), 'to_name': new_name,
        })
        return jsonify({"success": True, "new_path": str(new_path), "new_name": new_name})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@app.route('/api/move', methods=['POST'])
@login_required
def api_move():
    d = request.json or {}
    old_path = Path(d.get('path'))

    # Le déplacement / copie utilise le fichier courant trouvé sur disque.
    # Le nom proposé par la recherche est une suggestion de renommage UI,
    # pas une base de vérité pour l'écriture du mouvement côté backend.
    new_name = old_path.name
    media_type = d.get('media_type') or d.get('type') or 'movie'
    job_id = str(uuid.uuid4())

    if not old_path.exists():
        return jsonify({"success": False, "message": "Fichier source introuvable"}), 400

    if media_type == 'tv':
        folder_source = Path(old_path).stem
        target_dir = Path(config.get('_tv_output_path') or config.get('tv_output_path')) / _series_folder_name(folder_source)
    else:
        target_dir = Path(config.get('_movie_output_path') or config.get('movie_output_path'))

    new_path = target_dir / new_name

    # Le doublon n'est plus un blocage métier ; on le remplace proprement à l'écriture.
    if new_path.exists():
        try:
            os.remove(new_path)
        except Exception as e:
            return jsonify({"success": False, "message": f"Impossible de remplacer la destination: {e}"}), 400

    try:
        _ensure_dir(str(target_dir))
    except Exception as e:
        return jsonify({"success": False, "message": f"Impossible de créer le dossier destination: {e}"}), 400

    file_size = _run_file_op(job_id, str(old_path), str(new_path), {
        'id': job_id, 'op': 'move',
        'date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'from_path': str(old_path), 'from_name': old_path.name,
        'to_path': str(new_path), 'to_name': new_name,
    })
    return jsonify({"success": True, "job_id": job_id, "file_size": file_size, "new_path": str(new_path), "new_name": new_name})

@app.route('/api/move-progress/<job_id>')
@login_required
def api_move_progress(job_id):
    prog = move_progress.get(job_id)
    if prog is None:
        return jsonify({'finished': True, 'percent': 100, 'copied': 0, 'file_size': 0,
                        'speed': 0, 'eta': 0, 'elapsed': 0, 'error': None,
                        'verified': True, 'source_exists': False, 'target_exists': True})
    resp = {k: prog.get(k) for k in ('finished', 'percent', 'copied', 'file_size', 'speed', 'eta', 'elapsed', 'error', 'phase', 'new_path', 'new_name', 'verified', 'source_exists', 'target_exists')}
    return jsonify(resp)

@app.route('/api/revert', methods=['POST'])
@login_required
def api_revert():
    d = request.json
    entry_id = d.get('id')
    history = load_history()
    entry = next((e for e in history if e.get('id') == entry_id), None)
    if not entry:
        return jsonify({"success": False, "message": "Entrée introuvable"}), 404
    if not os.path.exists(entry['to_path']):
        return jsonify({"success": False, "message": "Fichier introuvable sur le disque"}), 400

    if os.path.exists(entry['from_path']):
        try:
            os.remove(entry['from_path'])
        except Exception as e:
            return jsonify({"success": False, "message": f"Impossible de remplacer la destination: {e}"}), 400

    job_id = str(uuid.uuid4())
    file_size = _run_file_op(job_id, entry['to_path'], entry['from_path'], {
        'id': str(uuid.uuid4()), 'op': 'revert',
        'date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'from_path': entry['to_path'], 'from_name': entry['to_name'],
        'to_path': entry['from_path'], 'to_name': entry['from_name'],
        'revert_of': entry_id,
    })
    return jsonify({"success": True, "job_id": job_id, "file_size": file_size,
                    "from_path": entry['from_path'], "from_name": entry['from_name']})

@app.route('/api/history')
@login_required
def api_history():
    history = load_history()
    for e in history:
        e['is_reverted'] = any(
            candidate.get('op') == 'revert' and candidate.get('revert_of') == e.get('id')
            for candidate in history
        )
        e['can_revert'] = (e.get('op') != 'revert' and
                           os.path.exists(e.get('to_path', '')) and
                           not os.path.exists(e.get('from_path', '')))
    return jsonify(history)

@app.route('/api/history/clear', methods=['POST'])
@login_required
def api_history_clear():
    save_history([])
    return jsonify({'success': True})

@app.route('/api/config', methods=['GET'])
@login_required
def api_get_config():
    safe = {k: v for k, v in config.items() if not k.startswith('_')}
    safe['password_set'] = bool(get_password())
    return jsonify(safe)

@app.route('/api/config', methods=['POST'])
@login_required
def api_set_config():
    global config, scanner, api_handler, rename_engine
    d = request.json
    for key, val in d.items():
        if key in ('movie_path', 'tv_path'):
            if val:
                config['input_path'] = val
        elif key in ('movie_output_path', 'tv_output_path'):
            config[key] = val
        elif key == 'tvdb_api_key' and val and '...' in val:
            continue
        elif key == 'password':
            continue
        else:
            config[key] = val
    save_config(config)
    config = load_config()
    scanner = MediaScanner(
        config['_input_path'],
        [config.get('_movie_output_path'), config.get('_tv_output_path')]
    )
    api_handler = APIHandler(config)
    rename_engine = RenameEngine(config)
    return jsonify({"success": True, "message": "Configuration sauvegardée"})

@app.route('/api/test-keys', methods=['POST'])
@login_required
def api_test_keys():
    d = request.json
    tvdb_key = d.get('tvdb_api_key', '').strip()
    if not tvdb_key or '...' in tvdb_key:
        tvdb_key = config.get('tvdb_api_key', '')
    if not tvdb_key:
        return jsonify({"tvdb": {"valid": False, "message": "Clé non fournie"}})
    try:
        resp = requests.post("https://api4.thetvdb.com/v4/login", json={"apikey": tvdb_key}, timeout=5)
        ok = resp.status_code == 200
        return jsonify({"tvdb": {"valid": ok, "message": "✓ TVDB OK" if ok else f"✗ HTTP {resp.status_code}"}})
    except Exception as e:
        return jsonify({"tvdb": {"valid": False, "message": f"✗ {e}"}})

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=5000, threaded=True)
