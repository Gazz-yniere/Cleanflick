from flask import Flask, render_template, request, jsonify, session, redirect, Response
from functools import wraps
import json, os, re, time, requests, logging, shutil, threading, uuid
import hashlib
import db
from pathlib import Path
import mediaduration

_MEDIA_DUR_CACHE = {}
_OMDB_NEG_CACHE = {}       # imdb_id -> timestamp d'expiration des échecs/réponses négatives OMDb
_OMDB_NEG_TTL = 300        # 5 min : évite de marteler OMDb (timeout ~6s) quand il est indisponible
_OMDB_QUOTA_LOCKOUT_SECS = 86400  # 24 h : suspension OMDb une fois le quota journalier atteint
_OMDB_QUOTA_KEY = 'omdb_quota_paused_until'  # clé persistée en base (survit aux redémarrages)


def _omdb_quota_paused_until():
    """Timestamp d'expiration du verrou quota OMDb (0 si aucun verrou)."""
    try:
        return float(db.meta_get(_OMDB_QUOTA_KEY, 0) or 0)
    except Exception:
        return 0


def _omdb_quota_locked():
    """Vrai tant que le quota OMDb est atteint : aucune requête réseau autorisée."""
    return time.time() < _omdb_quota_paused_until()


def _omdb_quota_state():
    """État courant du quota pour l'API : {'locked': bool, 'resumes_in_secs': int}."""
    until = _omdb_quota_paused_until()
    now = time.time()
    if until > now:
        return {'locked': True, 'resumes_in_secs': int(until - now)}
    return {'locked': False, 'resumes_in_secs': 0}


def _omdb_mark_quota_reached():
    """Persiste le verrou quota 24 h et log une alerte claire."""
    until = time.time() + _OMDB_QUOTA_LOCKOUT_SECS
    db.meta_set(_OMDB_QUOTA_KEY, str(int(until)))
    logger.warning("OMDb: quota journalier atteint — requêtes suspendues pendant 24 h")
from scanner import MediaScanner, MediaFile
from api_handler import APIHandler
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['JSON_SORT_KEYS'] = False

scan_clients = set()
scan_last_snapshot = None
scan_watch_lock = threading.Lock()

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "tvdb_api_key": "", "tvdb_pin": "", "omdb_api_key": "",
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

def _static_version():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    total = 0
    for name in ('app.js', 'i18n.js', 'base.css', 'files.css', 'config.css'):
        try:
            total += int(os.path.getmtime(os.path.join(base, name)))
        except OSError:
            pass
    return total

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
            fd = os.open(dst, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception:
            pass
        if not os.path.exists(dst):
            raise FileNotFoundError(f"Le fichier de destination n'a pas été créé : {dst}")
        move_progress[job_id].update({
            'copied': file_size, 'file_size': file_size, 'percent': 100,
            'speed': 0, 'eta': 0, 'phase': 'verifying', 'finished': False,
            'elapsed': round(time.time() - start, 2),
        })
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
                        db.file_cache_migrate(old_fkey, new_fkey, os.path.abspath(new_p), fsize, fmtime, os.path.basename(old_p))
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
    return render_template('index.html', cache_bust=_static_version())

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
    out = []
    for f in scanned_files:
        item = {
            'filename': f.filename, 'path': f.path,
            'media_type': f.media_type, 'title': f.title,
            'season': f.season, 'episode': f.episode, 'year': f.year
        }
        try:
            mt = int(os.path.getmtime(f.path))
            key = (f.path, mt)
            if key in _MEDIA_DUR_CACHE:
                item['duration'] = _MEDIA_DUR_CACHE[key]
            else:
                item['duration'] = mediaduration.get_duration_minutes(f.path)
                _MEDIA_DUR_CACHE[key] = item['duration']
        except Exception:
            item['duration'] = None
        out.append(item)
    threading.Thread(target=_ensure_library_series_episodes, daemon=True).start()
    return jsonify(out)

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

def _fetch_omdb_single(imdb_id, title=None, year=None, is_series=False):
    """Récupère et met en cache l'OMDb d'un seul imdb_id (repli on-demand)."""
    key = (config.get('omdb_api_key') or '').strip()
    if not key or not imdb_id:
        return None
    if _omdb_quota_locked():
        return None
    imdb = imdb_id.strip().lower()
    if db.omdb_get(imdb) is not None:
        return db.omdb_get(imdb)
    now = time.time()
    if imdb in _OMDB_NEG_CACHE:
        if now < _OMDB_NEG_CACHE[imdb]:
            return None
        del _OMDB_NEG_CACHE[imdb]
    try:
        if is_series and title:
            params = {'t': title, 'y': year, 'type': 'series', 'apikey': key}
        else:
            params = {'i': imdb, 'apikey': key}
        resp = requests.get('https://www.omdbapi.com/', params=params, timeout=6)
        data = resp.json()
        db.usage_bump('omdb')
        if data.get('Response') != 'True':
            err = str(data.get('Error', '')).lower()
            if 'limit' in err or 'quota' in err or 'request rate' in err:
                _omdb_mark_quota_reached()
            else:
                _OMDB_NEG_CACHE[imdb] = now + _OMDB_NEG_TTL
            return None
        oimdb = (data.get('imdbID') or '').lower()
        db.omdb_set(oimdb or imdb, data)
        if oimdb and oimdb != imdb:
            db.omdb_set(imdb, data)
        _OMDB_NEG_CACHE.pop(imdb, None)
        return data
    except Exception as e:
        logger.warning(f"OMDb single fetch error for {imdb}: {e}")
        _OMDB_NEG_CACHE[imdb] = now + _OMDB_NEG_TTL
        return None


def _fetch_omdb_ratings(results):
    key = (config.get('omdb_api_key') or '').strip()
    if not key:
        return
    for res in results or []:
        imdb = (res.get('imdb_id') or '').strip().lower()
        if not imdb:
            continue
        if db.omdb_get(imdb) is not None:
            continue
        is_series = (res.get('type') or '').lower() in ('series', 'show', 'tv')
        _fetch_omdb_single(imdb, res.get('title'), res.get('year'), is_series)

def _attach_omdb(results):
    try:
        omdb_map = db.omdb_all()
    except Exception:
        return results
    for r in results or []:
        imdb = (r.get('imdb_id') or '').strip().lower()
        if imdb and imdb in omdb_map:
            r['omdb'] = omdb_map[imdb]
    return results

def _attach_episode_omdb(results, season, episode):
    """Attache l'OMDb de l'épisode (durée/note/date) à chaque résultat TV, distinct de la série."""
    try:
        omdb_ep = db.omdb_episode_all()
    except Exception:
        omdb_ep = {}
    for r in (results or [])[:3]:
        imdb = (r.get('imdb_id') or '').strip().lower()
        title = r.get('title')
        year = r.get('year')
        rtype = (r.get('type') or '').lower()
        if not imdb or not title or not season or not episode:
            continue
        if rtype and rtype not in ('series', 'show', 'tv'):
            continue
        _fetch_omdb_episode(imdb, title, year, season, episode)
        ekey = f"{imdb}:y{year}:s{int(season):02d}e{int(episode):02d}"
        ed = omdb_ep.get(ekey) or db.omdb_episode_get(ekey)
        if ed:
            r['episode_omdb'] = ed
    return results

def _fetch_omdb_episode(series_imdb, title, year, season, episode):
    """Récupère la note OMDb d'un épisode (titre + année + saison + épisode) et la met en cache."""
    key = (config.get('omdb_api_key') or '').strip()
    if not key or not title or not season or not episode:
        return
    if _omdb_quota_locked():
        return
    cache_key = f"{series_imdb}:y{year}:s{int(season):02d}e{int(episode):02d}"
    if db.omdb_episode_get(cache_key) is not None:
        return
    try:
        params = {
            't': title, 'Season': int(season), 'Episode': int(episode), 'type': 'series', 'apikey': key
        }
        if year:
            params['y'] = year
        resp = requests.get('https://www.omdbapi.com/', params=params, timeout=6)
        data = resp.json()
        db.usage_bump('omdb')
        if data.get('Response') == 'True':
            db.omdb_episode_set(cache_key, data)
        else:
            err = str(data.get('Error', '')).lower()
            if 'limit' in err or 'quota' in err or 'request rate' in err:
                _omdb_mark_quota_reached()
    except Exception as e:
        logger.warning(f"OMDb episode fetch error for {title} S{season}E{episode}: {e}")

# ── Search cache helpers ──────────────────────────────────────────────────────

def _file_fingerprint(file_path):
    """Compute the cache fingerprint for a file (path + size + mtime)."""
    try:
        if file_path and os.path.exists(file_path):
            fsize = os.path.getsize(file_path)
            fmtime = int(os.path.getmtime(file_path))
            return {
                'fkey': hashlib.md5(f"{os.path.abspath(file_path)}|{fsize}|{fmtime}".encode('utf-8')).hexdigest(),
                'abspath': os.path.abspath(file_path),
                'size': fsize,
                'mtime': fmtime,
            }
    except Exception:
        pass
    return None

def _file_cache_lookup(file_path):
    """Return the cached file search results for a path, or None."""
    fp = _file_fingerprint(file_path)
    if not fp:
        return None
    try:
        return db.file_cache_get(fp['fkey'])
    except Exception:
        return None

def _file_cache_store(file_path, media_type, results):
    """Persist search results against a file fingerprint."""
    fp = _file_fingerprint(file_path)
    if not fp:
        return
    try:
        db.file_cache_set(fp['fkey'], fp['abspath'], fp['size'], fp['mtime'], media_type, results)
    except Exception:
        pass

def _params_cache_key(params):
    return hashlib.md5(json.dumps(params, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

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
    cached_file = _file_cache_lookup(file_path)
    if cached_file is not None:
        logger.info(f"Search auto: cache hit (file) for {file_path}")
        out = dict(cached_file)
        out['cache_source'] = 'file'
        _fetch_omdb_ratings(out.get('results', [])[:3])
        _attach_omdb(out.get('results', []))
        return jsonify(out)

    key = _params_cache_key(params)
    cached = db.cache_get(key)
    if cached is not None:
        logger.info(f"Search auto: cache hit (params) key={key}")
        out = dict(cached)
        out['cache_source'] = 'params'
        _fetch_omdb_ratings(out.get('results', [])[:3])
        _attach_omdb(out.get('results', []))
        return jsonify(out)

    logger.info(f"Search auto: cache miss for title={params['title']} year={params.get('year')}")
    result = api_handler.search_auto(params['title'], params['year'], params['filename'], params['season'], params['episode'], params['media_hint'])
    _fetch_omdb_ratings(result.get('results', [])[:3])
    if params.get('season') and params.get('episode'):
        _attach_episode_omdb(result.get('results', []), params['season'], params['episode'])
    _attach_omdb(result.get('results', []))
    try:
        db.cache_set(key, result.get('media_type', ''), result.get('results', []))
    except Exception:
        pass

    # Store per-file cache if path was provided and exists
    _file_cache_store(file_path, result.get('media_type', ''), result.get('results', []))
    result_with_source = dict(result)
    result_with_source['cache_source'] = 'tvdb'
    logger.info(f"Search auto: returning {len(result.get('results', []))} results from TVDB for title={params['title']}")
    return jsonify(result_with_source)

@app.route('/api/search/movie', methods=['POST'])
@login_required
def api_search_movie():
    d = request.json or {}
    params = {'title': d.get('title', ''), 'year': d.get('year')}
    force_refresh = bool(d.get('force_refresh'))
    # file-specific cache support
    file_path = d.get('path') or d.get('file_path')
    if not force_refresh:
        cached_file = _file_cache_lookup(file_path)
        if cached_file is not None:
            logger.info(f"Search movie: cache hit (file) for {file_path}")
            out = {'results': cached_file.get('results', []), 'cache_source': 'file'}
            _fetch_omdb_ratings(out['results'][:3])
            _attach_omdb(out['results'])
            return jsonify(out)

    key = _params_cache_key({'type': 'movie', **params})
    if not force_refresh:
        cached = db.cache_get(key)
        if cached is not None:
            logger.info(f"Search movie: cache hit (params) key={key}")
            out = {'results': cached.get('results', []), 'cache_source': 'params'}
            _fetch_omdb_ratings(out['results'][:3])
            _attach_omdb(out['results'])
            return jsonify(out)
    res = api_handler.search_movie(params['title'], params['year'])
    _fetch_omdb_ratings(res[:3])
    _attach_omdb(res)
    try:
        db.cache_set(key, 'movie', res)
    except Exception:
        pass

    _file_cache_store(file_path, 'movie', res)
    logger.info(f"Search movie: returning {len(res)} results from TVDB for title={params['title']}")
    return jsonify({'results': res, 'cache_source': 'tvdb'})

@app.route('/api/search/tv', methods=['POST'])
@login_required
def api_search_tv():
    d = request.json or {}
    params = {'title': d.get('title', '')}
    season = d.get('season')
    episode = d.get('episode')
    force_refresh = bool(d.get('force_refresh'))
    file_path = d.get('path') or d.get('file_path')
    if not force_refresh:
        cached_file = _file_cache_lookup(file_path)
        if cached_file is not None:
            logger.info(f"Search tv: cache hit (file) for {file_path}")
            out = {'results': cached_file.get('results', []), 'cache_source': 'file'}
            _fetch_omdb_ratings(out['results'][:3])
            _attach_omdb(out['results'])
            _attach_episode_omdb(out['results'], season, episode)
            return jsonify(out)

    key = _params_cache_key({'type': 'tv', **params})
    if not force_refresh:
        cached = db.cache_get(key)
        if cached is not None:
            logger.info(f"Search tv: cache hit (params) key={key}")
            out = {'results': cached.get('results', []), 'cache_source': 'params'}
            _fetch_omdb_ratings(out['results'][:3])
            _attach_omdb(out['results'])
            _attach_episode_omdb(out['results'], season, episode)
            return jsonify(out)
    res = api_handler.search_tv(params['title'])
    _fetch_omdb_ratings(res[:3])
    _attach_omdb(res)
    _attach_episode_omdb(res, season, episode)
    try:
        db.cache_set(key, 'tv', res)
    except Exception:
        pass

    _file_cache_store(file_path, 'tv', res)
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
    if details.get('title') and season and episode:
        try:
            _fetch_omdb_episode(details.get('imdbid') or details.get('imdb') or '', details.get('title'), details.get('year'), season, episode)
            ekey = f"{(details.get('imdbid') or details.get('imdb') or '').strip().lower()}:y{details.get('year')}:s{int(season):02d}e{int(episode):02d}"
            ed = db.omdb_episode_get(ekey)
            if ed:
                details['episode_omdb'] = ed
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
                db.file_cache_migrate(old_fkey, new_fkey, os.path.abspath(new_p), fsize, fmtime, os.path.basename(old_p))
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
        fp = _file_fingerprint(file_path)
        if not fp:
            return jsonify({'success': False, 'message': 'File not found'}), 400
        _file_cache_store(file_path, media_type, results)
        return jsonify({'success': True})
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

def _folder_size(path):
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _tvdb_series_episodes(series_id):
    """Liste complète des épisodes TVDB (toutes saisons), mise en cache 24h."""
    cached = db.series_episodes_get(series_id)
    if cached and cached.get('episodes'):
        return cached['episodes']
    try:
        res = api_handler.get_series_episodes(series_id)
        raw = [{'s': e.get('s'), 'e': e.get('e'), 'title': e.get('title', '')}
               for e in res.get('episodes', []) if e.get('s') is not None and e.get('e') is not None]
        # Déduplication par (saison, episode) : la réponse TVDB peut contenir des doublons.
        episodes = []
        seen = set()
        for e in raw:
            key = (e['s'], e['e'])
            if key in seen:
                continue
            seen.add(key)
            episodes.append(e)
        if episodes:
            db.series_episodes_set(series_id, {'episodes': episodes})
            return episodes
    except Exception as e:
        logger.warning(f"Missing-episodes TVDB fetch error for {series_id}: {e}")
    return cached.get('episodes', []) if cached else []


def _present_episodes(path):
    """Ensemble des (saison, episode) présents dans un dossier série (scan récursif,
    car les épisodes peuvent être répartis dans des sous-dossiers par saison)."""
    VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'}
    present = set()
    sxe = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if Path(f).suffix.lower() in VIDEO_EXT:
                    m = sxe.search(f)
                    if m:
                        present.add((int(m.group(1)), int(m.group(2))))
    except Exception:
        pass
    return present


def _series_episodes_cached(series_id):
    """Lit la liste d'épisodes depuis le cache uniquement (aucune requête TVDB)."""
    cached = db.series_episodes_get(series_id)
    if cached and cached.get('episodes'):
        return cached['episodes']
    return []


def _ensure_library_series_episodes():
    """Pré-remplit le cache TVDB des épisodes pour toutes les séries de la librairie TV."""
    tv_root = os.path.abspath(config.get('_tv_output_path') or config.get('tv_output_path') or '')
    if not tv_root or not os.path.isdir(tv_root):
        return
    try:
        for entry in os.scandir(tv_root):
            if entry.is_dir(follow_symlinks=False):
                m = re.search(r'\[tvdbid-([0-9]+)\]', entry.name, re.I)
                if m:
                    _tvdb_series_episodes(m.group(1))
    except Exception as e:
        logger.warning(f"Library series episodes prefill error: {e}")


@app.route('/api/library/missing')
@login_required
def api_library_missing():
    path = request.args.get('path', '').strip()
    if not path or not os.path.isdir(path):
        return jsonify({'error': 'Dossier introuvable'}), 404
    tv_root = os.path.abspath(config.get('_tv_output_path') or config.get('tv_output_path') or '')
    if not os.path.abspath(path).startswith(tv_root):
        return jsonify({'error': 'Non autorisé hors du dossier TV'}), 403
    name = os.path.basename(path.rstrip('/\\'))
    m = re.search(r'\[tvdbid-([0-9]+)\]', name, re.I)
    if not m:
        return jsonify({'series_id': '', 'count': 0, 'missing': []})
    series_id = m.group(1)
    episodes = _series_episodes_cached(series_id)
    present = _present_episodes(path)
    # Ignore les épisodes spéciaux (saison 0 / S00) : seules les saisons S01..S0X sont prises en compte.
    # Déduplication par (saison, episode) : le cache TVDB peut contenir des doublons.
    missing = []
    seen = set()
    for e in episodes:
        if e['s'] <= 0:
            continue
        key = (e['s'], e['e'])
        if key in present or key in seen:
            continue
        seen.add(key)
        missing.append(e)
    missing.sort(key=lambda x: (x['s'], x['e']))
    return jsonify({
        'series_id': series_id,
        'available': bool(episodes),
        'count': len(missing),
        'missing': [{'s': e['s'], 'e': e['e'], 'label': f"S{int(e['s']):02d}E{int(e['e']):02d}", 'title': e['title']} for e in missing]
    })


@app.route('/api/usage')
@login_required
def api_usage():
    return jsonify({'usage': db.usage_get(), 'omdb_quota': _omdb_quota_state()})


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
                roots.append({'name': os.path.basename(lib_path) or lib_path, 'path': lib_path, 'type': lib_type, 'is_dir': True, 'size': _folder_size(lib_path)})
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

    OMDB_MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}

    def _media_duration(path):
        """Durée réelle (min) du fichier vidéo, lue en Python pur (Docker-safe),
        mise en cache en mémoire + en base (invalidée si taille/mtime change)."""
        try:
            st = os.stat(path)
            mt = int(st.st_mtime)
            sz = st.st_size
        except Exception:
            return None
        key = (path, mt)
        if key in _MEDIA_DUR_CACHE:
            return _MEDIA_DUR_CACHE[key]
        try:
            cached = db.media_dur_get(path)
            if cached and cached.get('size') == sz and cached.get('mtime') == mt:
                minutes = cached.get('minutes')
                _MEDIA_DUR_CACHE[key] = minutes
                return minutes
        except Exception:
            pass
        minutes = mediaduration.get_duration_minutes(path)
        _MEDIA_DUR_CACHE[key] = minutes
        try:
            db.media_dur_set(path, minutes, sz, mt)
        except Exception:
            pass
        return minutes

    def _omdb_date(s):
        m = re.match(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', s or '')
        if m:
            mon = OMDB_MONTHS.get(m.group(2).capitalize())
            if mon:
                return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
        return s or ''

    def _apply_omdb(meta, imdb_id, title=None, year=None, is_series=False, live=True):
        if not imdb_id:
            return meta
        imdb = imdb_id.strip().lower()
        od = omdb_map.get(imdb)
        if not od and live:
            od = _fetch_omdb_single(imdb, title, year, is_series)
            if od:
                omdb_map[imdb] = od
        if not od:
            return meta
        if od.get('imdbRating'):
            meta['rating'] = od['imdbRating']
        if od.get('Released'):
            meta['date'] = _omdb_date(od['Released'])
        if od.get('Genre'):
            meta['genres'] = od['Genre']
        if od.get('Rated') and od.get('Rated') != 'N/A':
            meta['certification'] = od['Rated']
        if od.get('Runtime') and od.get('Runtime') != 'N/A':
            meta['runtime'] = od['Runtime']
        if not meta.get('poster') and od.get('Poster'):
            meta['poster'] = od['Poster']
        return meta

    def cache_meta(cached):
        if not cached:
            return None
        results = cached.get('results') or []
        if not results:
            return None
        top = results[0]
        meta = {
            'title': top.get('title', ''),
            'year': top.get('year'),
            'imdb': top.get('imdb_id', ''),
            'tmdb': top.get('tmdb_id', ''),
            'tvdb': str(top.get('id_tvdb') or top.get('id') or ''),
            'genres': top.get('genres', ''),
            'poster': top.get('poster', ''),
            'rating': '', 'date': top.get('date', ''), 'certification': '', 'runtime': '',
            'type': top.get('type') or cached.get('media_type', ''),
        }
        return _apply_omdb(meta, meta['imdb'], live=False)

    # Charge une seule fois le cache de recherche, puis matche par nom de fichier.
    # (Le cache stocke le chemin d'origine ; après un déplacement vers la librairie
    # le chemin change, mais le nom de fichier reste identique.)
    basename_map = {}
    conn = None
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("SELECT file_path, media_type, results_json, loaded_at FROM file_search_cache")
        for r in cur.fetchall():
            try:
                base = os.path.basename(r['file_path'])
                if base not in basename_map or (r['loaded_at'] or 0) > (basename_map[base]['loaded_at'] or 0):
                    basename_map[base] = {
                        'media_type': r['media_type'],
                        'results': json.loads(r['results_json'] or '[]'),
                        'loaded_at': r['loaded_at'],
                    }
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    # Notes OMDb (par imdb_id) chargées une seule fois, en lecture seule.
    try:
        omdb_map = db.omdb_all()
    except Exception:
        omdb_map = {}
    try:
        episode_omdb_map = db.omdb_episode_all()
    except Exception:
        episode_omdb_map = {}

    def folder_meta(name):
        best = None
        for base, cached in basename_map.items():
            if base.startswith(name):
                if best is None or (cached['loaded_at'] or 0) > (best['loaded_at'] or 0):
                    best = cached
        meta = cache_meta(best)
        meta = meta or id_meta(name) or name_meta(name)
        if meta and lib_type == 'tv':
            # Pour un dossier de série : pas de durée (elle n'apparaît que sur les fichiers/épisodes).
            meta.pop('runtime', None)
            meta.pop('episode_runtime', None)
        return meta

    # Index secondaire : (préfixe avant le marqueur d'épisode, marqueur) -> meilleur résultat
    # Permet de retrouver un épisode même si le titre de l'épisode a changé depuis le cache.
    episode_map = {}
    for base, cached in basename_map.items():
        m = re.search(r'([Ss]\d{2}[Ee]\d{2}|\d+x\d{2})', base)
        if m:
            key = (base[:m.start()].strip().lower(), m.group(1).lower())
            if key not in episode_map or (cached['loaded_at'] or 0) > (episode_map[key]['loaded_at'] or 0):
                episode_map[key] = cached

    # Index par identifiants externes (imdb/tmdb/tvdb) du meilleur résultat.
    # Retrouve l'info même si le fichier a été renommé, tant que l'id figure dans le nom.
    id_index = {}
    for cached in basename_map.values():
        top = (cached.get('results') or [{}])[0]
        for kind, val in (('imdb', top.get('imdb_id')), ('tmdb', top.get('tmdb_id')), ('tvdb', top.get('id_tvdb') or top.get('id'))):
            if val:
                key = (kind, str(val).strip().lower())
                if key not in id_index or (cached['loaded_at'] or 0) > (id_index[key]['loaded_at'] or 0):
                    id_index[key] = cached

    def ids_from_name(name):
        ids = []
        m = re.search(r'\[(?:imdbid?|imdb)-([a-zA-Z0-9]+)\]', name, re.I)
        if m:
            ids.append(('imdb', m.group(1).lower()))
        m = re.search(r'\[tmdb(?:id)?-([0-9]+)\]', name, re.I)
        if m:
            ids.append(('tmdb', m.group(1).lower()))
        m = re.search(r'\[tvdbid-([0-9]+)\]', name, re.I)
        if m:
            ids.append(('tvdb', m.group(1).lower()))
        return ids

    def id_meta(name):
        for kind, val in ids_from_name(name):
            cached = id_index.get((kind, val))
            if cached:
                return cache_meta(cached)
        return None

    # Affiche l'info extraite du nom seul (sans API ni cache) :
    # "Titre (Année) [imdbid-XXX] - (Titre FR).ext" -> titre + année + ids.
    def name_meta(name):
        m = re.match(r'^(.*?)\s*\((\d{4})\)', name)
        if not m:
            return None
        meta = {
            'title': m.group(1).strip(),
            'year': m.group(2),
            'imdb': '', 'tmdb': '', 'tvdb': '',
            'genres': '', 'poster': '', 'rating': '', 'date': '', 'type': '',
            'certification': '', 'runtime': '',
        }
        for kind, val in ids_from_name(name):
            if kind == 'imdb':
                meta['imdb'] = val
            elif kind == 'tmdb':
                meta['tmdb'] = val
            elif kind == 'tvdb':
                meta['tvdb'] = val
        return _apply_omdb(meta, meta['imdb'], live=False)

    def file_meta(name, duration=None):
        m = re.search(r'([Ss]\d{2}[Ee]\d{2}|\d+x\d{2})', name)
        ep_num = None
        ep_name = ''
        if m:
            ep_num = m.group(1).upper()
            tail = name[m.end():].strip(' -')
            if tail:
                ep_name = os.path.splitext(tail.split(' - ')[0].strip())[0] or ''
        meta = cache_meta(basename_map.get(name))
        if not meta:
            if m:
                key = (name[:m.start()].strip().lower(), m.group(1).lower())
                meta = cache_meta(episode_map.get(key))
            if not meta:
                meta = id_meta(name) or name_meta(name)
        if meta and ep_num:
            meta['episode'] = ep_num
            meta['episode_name'] = ep_name
            m2 = re.match(r'[Ss](\d{2})[Ee](\d{2})', ep_num)
            if duration:
                meta['episode_runtime'] = f"{duration} min"
            if m2 and meta.get('imdb'):
                s_num, e_num = int(m2.group(1)), int(m2.group(2))
                ekeys = [f"{meta['imdb']}:y{meta.get('year')}:s{s_num:02d}e{e_num:02d}",
                         f"{meta['imdb']}:s{s_num:02d}e{e_num:02d}"]
                ed = {}
                for ek in ekeys:
                    ed = episode_omdb_map.get(ek) or {}
                    if ed:
                        break
                if ed.get('imdbRating'):
                    meta['episode_rating'] = ed['imdbRating']
                if not duration and ed.get('Runtime') and ed.get('Runtime') != 'N/A':
                    meta['episode_runtime'] = ed['Runtime']
                if ed.get('Released') and ed.get('Released') != 'N/A':
                    meta['episode_date'] = _omdb_date(ed['Released'])
        elif meta and not ep_num and duration:
            meta['runtime'] = f"{duration} min"
        return meta

    all_entries = []
    try:
        all_entries = list(os.scandir(path))
    except PermissionError:
        return jsonify({'error': 'Permission refusée'}), 403
    except OSError:
        return jsonify({'error': 'Lecture impossible', 'entries': [], 'total': 0, 'offset': 0, 'limit': 0}), 200

    def _sort_key(e):
        try:
            return (not e.is_dir(follow_symlinks=False), e.name.lower())
        except OSError:
            return (True, e.name.lower())

    all_entries.sort(key=_sort_key)

    total = len(all_entries)
    try:
        offset = max(0, int(request.args.get('offset', 0)))
        limit = int(request.args.get('limit', 0))
    except Exception:
        offset, limit = 0, 0
    if limit and limit > 0:
        page = all_entries[offset:offset + limit]
    else:
        page = all_entries[offset:]

    entries = []
    for entry in page:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            try:
                child_count = sum(1 for _ in os.scandir(entry.path))
            except Exception:
                child_count = 0
            valid_dir = is_valid_name(entry.name, lib_type, is_dir=True)
            entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': True, 'child_count': child_count, 'size': _folder_size(entry.path), 'valid': valid_dir, 'meta': folder_meta(entry.name)})
        elif entry.is_file() and Path(entry.name).suffix.lower() in VIDEO_EXT:
            valid = is_valid_name(entry.name, lib_type)
            size = 0
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
            dur = _media_duration(entry.path)
            entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': False, 'size': size, 'valid': valid, 'meta': file_meta(entry.name, dur)})

    return jsonify({'path': path, 'type': lib_type, 'entries': entries, 'total': total, 'offset': offset, 'limit': limit})


@app.route('/api/library/enrich', methods=['POST'])
@login_required
def api_library_enrich():
    """Enrichit en arrière-plan le cache OMDb des imdb_ids non encore cachés,
    pour compléter les notes/dates/genres des entrées de la bibliothèque.
    Ne bloque pas l'affichage : appelé par le frontend après le rendu."""
    d = request.json or {}
    ids = d.get('imdb_ids') or []
    seen = set()
    clean = []
    for x in ids:
        s = str(x).strip().lower()
        if s and s not in seen:
            seen.add(s)
            clean.append(s)
    results = {}
    for imdb in clean:
        if db.omdb_get(imdb) is not None:
            continue
        od = _fetch_omdb_single(imdb)
        if od:
            results[imdb] = {
                'rating': od.get('imdbRating') or '',
                'date': od.get('Released') or '',
                'genres': od.get('Genre') or '',
                'certification': od.get('Rated') or '',
                'runtime': od.get('Runtime') or '',
                'poster': od.get('Poster') or '',
            }
    return jsonify({'results': results})


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
    job_id = str(uuid.uuid4())
    try:
        _run_file_op(job_id, src, dst, {
            'id': job_id, 'op': 'move',
            'date': time.strftime('%Y-%m-%d %H:%M:%S'),
            'from_path': src, 'from_name': os.path.basename(src),
            'to_path': dst, 'to_name': os.path.basename(dst),
        })
        return jsonify({'success': True, 'job_id': job_id})
    except Exception as e:
        logger.error(f"Send-back error for {src} -> {dst}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/library/rename-folder', methods=['POST'])
@login_required
def api_library_rename_folder():
    d = request.json or {}
    folder = d.get('path')
    new_name = d.get('new_name')
    if not folder or not os.path.isdir(folder):
        return jsonify({'success': False, 'message': 'Dossier introuvable'}), 400
    movie_root = os.path.abspath(config.get('_movie_output_path') or config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(config.get('_tv_output_path')    or config.get('tv_output_path')    or '')
    abs_folder = os.path.abspath(folder)
    if not (abs_folder.startswith(movie_root) or abs_folder.startswith(tv_root)):
        return jsonify({'success': False, 'message': 'Renommage non autorisé hors des dossiers médias'}), 403
    new_name = (new_name or '').strip().rstrip('/\\')
    new_name = os.path.basename(new_name)
    if not new_name or new_name in ('.', '..'):
        return jsonify({'success': False, 'message': 'Nom invalide'}), 400
    if os.path.sep in new_name or (os.path.altsep and os.path.altsep in new_name):
        return jsonify({'success': False, 'message': 'Nom invalide'}), 400
    parent = os.path.dirname(abs_folder)
    dst = os.path.join(parent, new_name)
    if dst == abs_folder:
        return jsonify({'success': True, 'new_path': dst, 'new_name': new_name})
    if os.path.exists(dst):
        return jsonify({'success': False, 'message': f"Le dossier '{new_name}' existe déjà"}), 400
    try:
        shutil.move(abs_folder, dst)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    try:
        global scan_last_snapshot
        with scan_watch_lock:
            scan_last_snapshot = _scan_snapshot()
    except Exception:
        pass
    return jsonify({'success': True, 'new_path': dst, 'new_name': new_name})


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
    global config, scanner, api_handler
    d = request.json
    for key, val in d.items():
        if key in ('movie_path', 'tv_path'):
            if val:
                config['input_path'] = val
        elif key in ('movie_output_path', 'tv_output_path'):
            config[key] = val
        elif key == 'tvdb_api_key' and val and '...' in val:
            continue
        elif key == 'omdb_api_key' and val and '...' in val:
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
    return jsonify({"success": True, "message": "Configuration sauvegardée"})

@app.route('/api/test-keys', methods=['POST'])
@login_required
def api_test_keys():
    d = request.json
    tvdb_key = d.get('tvdb_api_key', '').strip()
    if not tvdb_key or '...' in tvdb_key:
        tvdb_key = config.get('tvdb_api_key', '')
    omdb_key = d.get('omdb_api_key', '').strip()
    if not omdb_key or '...' in omdb_key:
        omdb_key = config.get('omdb_api_key', '')
    out = {}
    if tvdb_key:
        try:
            resp = requests.post("https://api4.thetvdb.com/v4/login", json={"apikey": tvdb_key}, timeout=5)
            ok = resp.status_code == 200
            out["tvdb"] = {"valid": ok, "message": "✓ TVDB OK" if ok else f"✗ HTTP {resp.status_code}"}
        except Exception as e:
            out["tvdb"] = {"valid": False, "message": f"✗ {e}"}
    else:
        out["tvdb"] = {"valid": False, "message": "Clé TVDB non fournie"}
    if omdb_key:
        try:
            resp = requests.get("https://www.omdbapi.com/", params={'i': 'tt0266915', 'apikey': omdb_key}, timeout=5)
            data = resp.json()
            ok = data.get('Response') == 'True'
            rating = data.get('imdbRating', '')
            out["omdb"] = {"valid": ok, "message": f"✓ OMDb OK (note {rating})" if ok else f"✗ {data.get('Error', 'réponse invalide')}"}
        except Exception as e:
            out["omdb"] = {"valid": False, "message": f"✗ {e}"}
    else:
        out["omdb"] = {"valid": False, "message": "Clé OMDb non fournie"}
    return jsonify(out)

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=5000, threaded=True)
