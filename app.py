from flask import Flask, render_template, request, jsonify, session, redirect, Response
from functools import wraps
import json, os, re, time, requests, logging, shutil, threading, uuid
import hashlib
import db
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
    try:
        return db.get_history()
    except Exception as e:
        logger.error(f"Error loading history from DB: {e}")
        return []

def save_history(history):
    # Not used: history is persisted via DB
    try:
        # Replace DB contents with provided list
        db.clear_history()
        for entry in (history or []):
            db.add_history(entry)
    except Exception as e:
        logger.error(f"Error saving history to DB: {e}")
        raise

def append_history(entry):
    try:
        db.add_history(entry)
    except Exception as e:
        logger.error(f"Error appending history to DB: {e}")

# ── Utils ─────────────────────────────────────────────────────────────────────

def _resolve(path):
    if not path:
        return path
    import platform
    base = os.path.dirname(os.path.abspath(__file__))
    # Sur Linux/Docker : chemin absolu réel → tel quel
    if platform.system() != 'Windows' and os.path.isabs(path):
        return path
    # Sur Windows : chemin absolu Windows (C:\... D:\...) → tel quel
    if platform.system() == 'Windows' and len(path) >= 2 and path[1] == ':':
        return path
    # Chemin style Unix (/downloads) ou relatif → relatif à l'app
    return os.path.join(base, path.lstrip('/\\'))

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
                removed = set(previous) - set(current)
                if (added or removed) and current is not None:
                    _broadcast_scan_refresh()
                    if added and removed:
                        logger.info(f"Input files changed: +{len(added)} / -{len(removed)}")
                    elif added:
                        logger.info(f"New input files detected: {len(added)}")
                    else:
                        logger.info(f"Input files removed: {len(removed)}")
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
                # After a successful async move, update the scan snapshot so the watcher
                # does not treat the change as a new external file.
                try:
                    global scan_last_snapshot
                    with scan_watch_lock:
                        scan_last_snapshot = _scan_snapshot()
                except Exception:
                    pass
                # Attempt to migrate file cache from old path fingerprint to new path
                try:
                    old_p = str(src_path)
                    new_p = str(dst_path)
                    if os.path.exists(new_p):
                        fsize = os.path.getsize(new_p)
                        fmtime = int(os.path.getmtime(new_p))
                        old_fkey = hashlib.md5(f"{os.path.abspath(old_p)}|{os.path.getsize(old_p) if os.path.exists(old_p) else 0}|{int(os.path.getmtime(old_p)) if os.path.exists(old_p) else 0}".encode('utf-8')).hexdigest()
                        new_fkey = hashlib.md5(f"{os.path.abspath(new_p)}|{fsize}|{fmtime}".encode('utf-8')).hexdigest()
                        db.file_cache_migrate(old_fkey, new_fkey, os.path.abspath(new_p), fsize, fmtime)
                except Exception:
                    pass
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
    try:
        from gevent.queue import Queue as GQueue
        q = GQueue()
    except ImportError:
        import queue
        q = queue.Queue()
    scan_clients.add(q)

    def stream():
        try:
            while True:
                try:
                    payload = q.get(timeout=25)
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
    params = {
        'title': d.get('title', ''), 'year': d.get('year'),
        'filename': d.get('filename', ''), 'season': d.get('season'),
        'episode': d.get('episode'), 'media_hint': d.get('media_hint', ''),
    }
    # If client provided a file path, try file-specific cache (fingerprint based)
    file_path = d.get('path') or d.get('file_path')
    if file_path:
        try:
            if os.path.exists(file_path):
                fsize = os.path.getsize(file_path)
                fmtime = int(os.path.getmtime(file_path))
                fkey_raw = f"{os.path.abspath(file_path)}|{fsize}|{fmtime}"
                fkey = hashlib.md5(fkey_raw.encode('utf-8')).hexdigest()
                cached_file = db.file_cache_get(fkey)
                if cached_file is not None:
                    logger.info(f"Search auto: cache hit (file) for {file_path}")
                    out = dict(cached_file)
                    out['cache_source'] = 'file'
                    return jsonify(out)
        except Exception:
            pass

    key = hashlib.md5(json.dumps(params, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    cached = db.cache_get(key)
    if cached is not None:
        logger.info(f"Search auto: cache hit (params) key={key}")
        out = dict(cached)
        out['cache_source'] = 'params'
        return jsonify(out)

    logger.info(f"Search auto: cache miss for title={params['title']} year={params.get('year')}")
    result = api_handler.search_auto(params['title'], params['year'], params['filename'], params['season'], params['episode'], params['media_hint'])
    try:
        db.cache_set(key, result.get('media_type', ''), result.get('results', []))
    except Exception:
        pass

    # Store per-file cache if path was provided and exists
    if file_path:
        try:
            if os.path.exists(file_path):
                fsize = os.path.getsize(file_path)
                fmtime = int(os.path.getmtime(file_path))
                fkey_raw = f"{os.path.abspath(file_path)}|{fsize}|{fmtime}"
                fkey = hashlib.md5(fkey_raw.encode('utf-8')).hexdigest()
                db.file_cache_set(fkey, os.path.abspath(file_path), fsize, fmtime, result.get('media_type', ''), result.get('results', []))
        except Exception:
            pass
    result_with_source = dict(result)
    result_with_source['cache_source'] = 'tvdb'
    logger.info(f"Search auto: returning {len(result.get('results', []))} results from TVDB for title={params['title']}")
    return jsonify(result_with_source)

@app.route('/api/search/movie', methods=['POST'])
@login_required
def api_search_movie():
    d = request.json or {}
    params = {'title': d.get('title', ''), 'year': d.get('year')}
    # file-specific cache support
    file_path = d.get('path') or d.get('file_path')
    if file_path:
        try:
            if os.path.exists(file_path):
                fsize = os.path.getsize(file_path)
                fmtime = int(os.path.getmtime(file_path))
                fkey_raw = f"{os.path.abspath(file_path)}|{fsize}|{fmtime}"
                fkey = hashlib.md5(fkey_raw.encode('utf-8')).hexdigest()
                cached_file = db.file_cache_get(fkey)
                if cached_file is not None:
                    logger.info(f"Search movie: cache hit (file) for {file_path}")
                    out = {'results': cached_file.get('results', []), 'cache_source': 'file'}
                    return jsonify(out)
        except Exception:
            pass

    key = hashlib.md5(json.dumps({'type': 'movie', **params}, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    cached = db.cache_get(key)
    if cached is not None:
        logger.info(f"Search movie: cache hit (params) key={key}")
        out = {'results': cached.get('results', []), 'cache_source': 'params'}
        return jsonify(out)
    res = api_handler.search_movie(params['title'], params['year'])
    try:
        db.cache_set(key, 'movie', res)
    except Exception:
        pass

    if file_path:
        try:
            if os.path.exists(file_path):
                fsize = os.path.getsize(file_path)
                fmtime = int(os.path.getmtime(file_path))
                fkey_raw = f"{os.path.abspath(file_path)}|{fsize}|{fmtime}"
                fkey = hashlib.md5(fkey_raw.encode('utf-8')).hexdigest()
                db.file_cache_set(fkey, os.path.abspath(file_path), fsize, fmtime, 'movie', res)
        except Exception:
            pass
    logger.info(f"Search movie: returning {len(res)} results from TVDB for title={params['title']}")
    return jsonify({'results': res, 'cache_source': 'tvdb'})

@app.route('/api/search/tv', methods=['POST'])
@login_required
def api_search_tv():
    d = request.json or {}
    params = {'title': d.get('title', '')}
    file_path = d.get('path') or d.get('file_path')
    if file_path:
        try:
            if os.path.exists(file_path):
                fsize = os.path.getsize(file_path)
                fmtime = int(os.path.getmtime(file_path))
                fkey_raw = f"{os.path.abspath(file_path)}|{fsize}|{fmtime}"
                fkey = hashlib.md5(fkey_raw.encode('utf-8')).hexdigest()
                cached_file = db.file_cache_get(fkey)
                if cached_file is not None:
                    logger.info(f"Search tv: cache hit (file) for {file_path}")
                    out = {'results': cached_file.get('results', []), 'cache_source': 'file'}
                    return jsonify(out)
        except Exception:
            pass

    key = hashlib.md5(json.dumps({'type': 'tv', **params}, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    cached = db.cache_get(key)
    if cached is not None:
        logger.info(f"Search tv: cache hit (params) key={key}")
        out = {'results': cached.get('results', []), 'cache_source': 'params'}
        return jsonify(out)
    res = api_handler.search_tv(params['title'])
    try:
        db.cache_set(key, 'tv', res)
    except Exception:
        pass

    if file_path:
        try:
            if os.path.exists(file_path):
                fsize = os.path.getsize(file_path)
                fmtime = int(os.path.getmtime(file_path))
                fkey_raw = f"{os.path.abspath(file_path)}|{fsize}|{fmtime}"
                fkey = hashlib.md5(fkey_raw.encode('utf-8')).hexdigest()
                db.file_cache_set(fkey, os.path.abspath(file_path), fsize, fmtime, 'tv', res)
        except Exception:
            pass
    logger.info(f"Search tv: returning {len(res)} results from TVDB for title={params['title']}")
    return jsonify({'results': res, 'cache_source': 'tvdb'})

@app.route('/api/movie/<int:movie_id>')
@login_required
def api_movie_details(movie_id):
    source = request.args.get('source', 'tvdb')
    # Try details cache first
    try:
        cached = db.details_get('movie', str(movie_id))
        if cached is not None:
            logger.info(f"Movie details: cache hit for {movie_id}")
            out = dict(cached)
            out['cache_source'] = 'details_cache'
            return jsonify(out)
    except Exception:
        pass
    details = api_handler.get_movie_details(str(movie_id), source)
    try:
        db.details_set('movie', str(movie_id), details)
    except Exception:
        pass
    details['cache_source'] = 'tvdb'
    return jsonify(details)

@app.route('/api/tv/<int:tv_id>')
@login_required
def api_tv_details(tv_id):
    season = request.args.get('season', 1, type=int)
    episode = request.args.get('episode', 1, type=int)
    source = request.args.get('source', 'tvdb')
    cache_key = f"{tv_id}:s{season}e{episode}"
    try:
        cached = db.details_get('tv', cache_key)
        if cached is not None:
            logger.info(f"TV details: cache hit for {tv_id} S{season}E{episode}")
            out = dict(cached)
            out['cache_source'] = 'details_cache'
            out.update({'season': season, 'episode': episode})
            return jsonify(out)
    except Exception:
        pass
    details = api_handler.get_tv_details(str(tv_id), season, episode, source)
    try:
        db.details_set('tv', cache_key, details)
    except Exception:
        pass
    details['cache_source'] = 'tvdb'
    return jsonify(details)

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
        # Update the scan snapshot to avoid the background watcher triggering
        try:
            global scan_last_snapshot
            with scan_watch_lock:
                scan_last_snapshot = _scan_snapshot()
        except Exception:
            pass
        # Migrate file cache entry if present
        try:
            old_p = str(old_path)
            new_p = str(new_path)
            if os.path.exists(new_p):
                fsize = os.path.getsize(new_p)
                fmtime = int(os.path.getmtime(new_p))
                old_fkey = hashlib.md5(f"{os.path.abspath(old_p)}|{os.path.getsize(old_p) if os.path.exists(old_p) else 0}|{int(os.path.getmtime(old_p)) if os.path.exists(old_p) else 0}".encode('utf-8')).hexdigest()
                new_fkey = hashlib.md5(f"{os.path.abspath(new_p)}|{fsize}|{fmtime}".encode('utf-8')).hexdigest()
                db.file_cache_migrate(old_fkey, new_fkey, os.path.abspath(new_p), fsize, fmtime)
        except Exception:
            pass
        return jsonify({"success": True, "new_path": str(new_path), "new_name": new_name})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@app.route('/api/search/cache-file', methods=['POST'])
@login_required
def api_search_cache_file():
    """Permet au client de forcer le stockage en cache d'une recherche pour un fichier donné.
    Payload: { path: <file_path>, media_type: 'movie'|'tv', results: [...] }
    """
    d = request.json or {}
    file_path = d.get('path')
    results = d.get('results')
    media_type = d.get('media_type') or ''
    if not file_path or results is None:
        return jsonify({'success': False, 'message': 'Missing path or results'}), 400
    try:
        if os.path.exists(file_path):
            fsize = os.path.getsize(file_path)
            fmtime = int(os.path.getmtime(file_path))
            fkey_raw = f"{os.path.abspath(file_path)}|{fsize}|{fmtime}"
            fkey = hashlib.md5(fkey_raw.encode('utf-8')).hexdigest()
            db.file_cache_set(fkey, os.path.abspath(file_path), fsize, fmtime, media_type, results)
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'File not found'}), 400
    except Exception as e:
        logger.error(f"Error caching file search: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

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

    src = entry['to_path']
    dst = entry['from_path']
    same_dir = os.path.dirname(os.path.abspath(src)) == os.path.dirname(os.path.abspath(dst))

    revert_entry = {
        'id': str(uuid.uuid4()), 'op': 'revert',
        'date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'from_path': src, 'from_name': entry['to_name'],
        'to_path': dst, 'to_name': entry['from_name'],
        'revert_of': entry_id,
    }

    if same_dir:
        # Rename dans le même dossier : opération synchrone, snapshot mis à jour immédiatement
        try:
            _move_path(src, dst)
            append_history(revert_entry)
            global scan_last_snapshot
            with scan_watch_lock:
                scan_last_snapshot = _scan_snapshot()
            return jsonify({"success": True, "job_id": revert_entry['id'],
                            "from_path": dst, "from_name": entry['from_name']})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 400
    else:
        # Déplacement cross-dossier : opération asynchrone avec progress
        job_id = str(uuid.uuid4())
        file_size = _run_file_op(job_id, src, dst, revert_entry)
        return jsonify({"success": True, "job_id": job_id, "file_size": file_size,
                        "from_path": dst, "from_name": entry['from_name']})

@app.route('/api/history')
@login_required
def api_history():
    history = load_history()
    reverted_ids = {
        candidate.get('revert_of')
        for candidate in history
        if candidate.get('op') == 'revert' and candidate.get('revert_of')
    }
    for e in history:
        is_reverted = e.get('id') in reverted_ids
        e['is_reverted'] = is_reverted
        if e.get('op') == 'revert':
            e['can_revert'] = False
            e['revert_status'] = 'done'
        elif is_reverted:
            e['can_revert'] = False
            e['revert_status'] = 'reverted'
        else:
            to_exists   = os.path.exists(e.get('to_path', ''))
            from_exists = os.path.exists(e.get('from_path', ''))
            if not to_exists:
                e['can_revert'] = False
                e['revert_status'] = 'missing'
            elif from_exists:
                # fichier déjà revenu à sa place (revert sans revert_of enregistré, ou doublon)
                e['can_revert'] = False
                e['revert_status'] = 'reverted'
            else:
                e['can_revert'] = True
                e['revert_status'] = 'available'
    return jsonify(history)

@app.route('/api/library')
@login_required
def api_library():
    path = request.args.get('path', '').strip()
    library_type = request.args.get('type', '')  # 'movie' or 'tv'

    # Root call — return both movie and tv roots
    if not path:
        roots = []
        for lib_type, cfg_key in [('movie', '_movie_output_path'), ('tv', '_tv_output_path')]:
            lib_path = config.get(cfg_key) or config.get(cfg_key.lstrip('_'))
            if lib_path and os.path.isdir(lib_path):
                roots.append({'name': os.path.basename(lib_path) or lib_path, 'path': lib_path, 'type': lib_type, 'is_dir': True})
        return jsonify({'path': '', 'type': 'root', 'entries': roots})

    if not os.path.isdir(path):
        return jsonify({'error': 'Dossier introuvable'}), 404

    movie_root = os.path.abspath(config.get('_movie_output_path') or config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(config.get('_tv_output_path')    or config.get('tv_output_path')    or '')
    abs_path   = os.path.abspath(path)
    if abs_path.startswith(movie_root):
        lib_type = 'movie'
    elif abs_path.startswith(tv_root):
        lib_type = 'tv'
    else:
        lib_type = library_type or 'movie'

    movie_fmt = config.get('movie_format', '{n} ({y})')
    tv_fmt    = config.get('tv_format',    '{n} - {s00e00} - {t}')

    VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'}

    def is_valid_name(filename, ftype, is_dir=False):
        stem = Path(filename).stem if not is_dir else filename
        fmt = movie_fmt if ftype == 'movie' else tv_fmt
        tokens = set(re.findall(r'\{([a-zA-Z_][\w:]*?)\}', fmt))
        # Normaliser les alias
        if 'imdbid' in tokens: tokens.add('imdb')
        if 'imdb' in tokens: tokens.add('imdbid')
        if 'tmdbid' in tokens: tokens.add('tmdb')
        if 'tmdb' in tokens: tokens.add('tmdbid')
        if 'year' in tokens: tokens.add('y')
        if 'y' in tokens: tokens.add('year')

        checks = {
            # token          : (regex_dans_stem,          applicable_is_dir)
            'y':              (r'\(\d{4}\)',               True),
            'imdb':           (r'imdb(?:id)?-tt\d+',       False),
            'tvdbid':         (r'tvdbid-\d+',              True),
            'tmdb':           (r'tmdb(?:id)?-\d+',         False),
            's00e00':         (r'[Ss]\d{2}[Ee]\d{2}',     False),
            'sxe':            (r'\d+x\d{2}',               False),
        }
        for token, (pattern, dir_ok) in checks.items():
            if token not in tokens:
                continue
            if is_dir and not dir_ok:
                continue
            if not re.search(pattern, stem):
                return False
        return True

    entries = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.is_dir(follow_symlinks=False):
                try:
                    child_count = sum(1 for _ in os.scandir(entry.path))
                except Exception:
                    child_count = 0
                valid_dir = is_valid_name(entry.name, lib_type, is_dir=True)
                entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': True, 'child_count': child_count, 'valid': valid_dir})
            elif entry.is_file() and Path(entry.name).suffix.lower() in VIDEO_EXT:
                valid = is_valid_name(entry.name, lib_type)
                entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': False, 'valid': valid})
    except PermissionError:
        return jsonify({'error': 'Permission refusée'}), 403

    return jsonify({'path': path, 'type': lib_type, 'entries': entries})


@app.route('/api/library/send-back-folder', methods=['POST'])
@login_required
def api_library_send_back_folder():
    d = request.json or {}
    folder = d.get('path')
    if not folder or not os.path.isdir(folder):
        return jsonify({'success': False, 'message': 'Dossier introuvable'}), 400
    movie_root = os.path.abspath(config.get('_movie_output_path') or config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(config.get('_tv_output_path')    or config.get('tv_output_path')    or '')
    abs_folder = os.path.abspath(folder)
    if not (abs_folder.startswith(movie_root) or abs_folder.startswith(tv_root)):
        return jsonify({'success': False, 'message': 'Opération non autorisée hors des dossiers médias'}), 403
    input_path = config.get('_input_path') or config.get('input_path')
    VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'}
    moved, errors = [], []
    for entry in os.scandir(folder):
        if entry.is_file() and Path(entry.name).suffix.lower() in VIDEO_EXT:
            dst = os.path.join(input_path, entry.name)
            try:
                _move_path(entry.path, dst)
                append_history({'id': str(uuid.uuid4()), 'op': 'move', 'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                                'from_path': entry.path, 'from_name': entry.name,
                                'to_path': dst, 'to_name': entry.name})
                moved.append(entry.name)
            except Exception as e:
                errors.append(f"{entry.name}: {e}")
    global scan_last_snapshot
    with scan_watch_lock:
        scan_last_snapshot = _scan_snapshot()
    if errors:
        return jsonify({'success': False, 'message': '\n'.join(errors), 'moved': moved}), 400
    return jsonify({'success': True, 'moved': moved})


@app.route('/api/library/send-back', methods=['POST'])
@login_required
def api_library_send_back():
    d = request.json or {}
    src = d.get('path')
    if not src or not os.path.isfile(src):
        return jsonify({'success': False, 'message': 'Fichier introuvable'}), 400
    dst = os.path.join(config.get('_input_path') or config.get('input_path'), os.path.basename(src))
    try:
        _move_path(src, dst)
        append_history({'id': str(uuid.uuid4()), 'op': 'move', 'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'from_path': src, 'from_name': os.path.basename(src),
                        'to_path': dst, 'to_name': os.path.basename(dst)})
        global scan_last_snapshot
        with scan_watch_lock:
            scan_last_snapshot = _scan_snapshot()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/library/delete-folder', methods=['POST'])
@login_required
def api_library_delete_folder():
    d = request.json or {}
    path = d.get('path')
    if not path or not os.path.isdir(path):
        return jsonify({'success': False, 'message': 'Dossier introuvable'}), 400
    # Safety: only allow deletion inside movie/tv output paths
    movie_root = os.path.abspath(config.get('_movie_output_path') or config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(config.get('_tv_output_path')    or config.get('tv_output_path')    or '')
    abs_path   = os.path.abspath(path)
    if not (abs_path.startswith(movie_root) or abs_path.startswith(tv_root)):
        return jsonify({'success': False, 'message': 'Suppression non autorisée hors des dossiers médias'}), 403
    if any(True for _ in os.scandir(path)):
        return jsonify({'success': False, 'message': 'Le dossier n\'est pas vide'}), 400
    try:
        os.rmdir(path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/cache/clear', methods=['POST'])
@login_required
def api_cache_clear():
    conn = db.get_conn()
    conn.execute('DELETE FROM search_cache')
    conn.execute('DELETE FROM file_search_cache')
    conn.execute('DELETE FROM details_cache')
    conn.commit()
    conn.close()
    return jsonify({'success': True})

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
