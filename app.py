from flask import Flask, render_template, request, jsonify, session, redirect
from functools import wraps
import json, os, re, time, requests, logging
from pathlib import Path
from scanner import MediaScanner, MediaFile
from api_handler import APIHandler
from rename_engine import RenameEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['JSON_SORT_KEYS'] = False

CONFIG_FILE = "config.json"
RENAME_HISTORY_FILE = "rename_history.json"

DEFAULT_CONFIG = {
    "tvdb_api_key": "",
    "tvdb_pin": "",
    "movie_path": "/downloads/movie",
    "tv_path": "/downloads/tv_shows",
    "movie_format": "{n} ({y})",
    "tv_format": "{n} - {s00e00} - {t}",
    "password": ""
}

def load_rename_history():
    if os.path.exists(RENAME_HISTORY_FILE):
        with open(RENAME_HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_rename_history(history):
    with open(RENAME_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def _resolve(path):
    """Résout un chemin: si inexistant, cherche downloads/ local"""
    if not path:
        return path
    if os.path.exists(path):
        return path
    base = os.path.dirname(os.path.abspath(__file__))
    # Cas Docker: /downloads/movie -> <script>/downloads/movie
    if path.startswith('/'):
        local = os.path.join(base, path.lstrip('/'))
        if os.path.exists(local):
            return local
    return path

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    else:
        cfg = DEFAULT_CONFIG.copy()
    # Résoudre les chemins
    cfg['_movie_path'] = _resolve(cfg.get('movie_path', '/downloads/movie'))
    cfg['_tv_path'] = _resolve(cfg.get('tv_path', '/downloads/tv_shows'))
    return cfg

def save_config(cfg):
    # Ne pas sauvegarder les clés internes _*
    to_save = {k: v for k, v in cfg.items() if not k.startswith('_')}
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(to_save, f, indent=2, ensure_ascii=False)

config = load_config()
scanner = MediaScanner(config['_movie_path'], config['_tv_path'])
api_handler = APIHandler(config)
rename_engine = RenameEngine(config)
scanned_files = []

# ============= Auth =============
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if config.get('password') and not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if pwd == config.get('password', ''):
            session['logged_in'] = True
            return redirect('/')
        return render_template('login.html', error='Mot de passe incorrect')
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ============= Routes =============
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/browse', methods=['GET'])
@login_required
def api_browse():
    path = request.args.get('path', '').strip()
    import platform
    is_windows = platform.system() == 'Windows'

    # Pas de chemin = afficher les racines
    if not path:
        if is_windows:
            import string
            roots = [f'{d}:\\' for d in string.ascii_uppercase if os.path.exists(f'{d}:\\')]
            return jsonify({'path': '', 'dirs': [], 'roots': roots, 'parent': None})
        path = '/'

    path = os.path.abspath(path)
    if not os.path.exists(path):
        # Chemin invalide: remonter jusqu'à trouver un chemin valide
        p = Path(path)
        while p != p.parent:
            p = p.parent
            if p.exists():
                path = str(p)
                break
    try:
        dirs = sorted(
            [e.name for e in os.scandir(path) if e.is_dir(follow_symlinks=False)],
            key=str.lower
        )
        # Parent: None si on est à la racine d'un lecteur Windows
        p = Path(path)
        parent = str(p.parent) if p.parent != p else None
        return jsonify({'path': path, 'dirs': dirs, 'roots': [], 'parent': parent})
    except PermissionError:
        return jsonify({'path': path, 'dirs': [], 'roots': [], 'parent': str(Path(path).parent)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/scan', methods=['GET'])
@login_required
def api_scan():
    global scanned_files
    scanned_files = scanner.scan()
    return jsonify([{
        'filename': f.filename, 'path': f.path,
        'media_type': f.media_type, 'title': f.title,
        'season': f.season, 'episode': f.episode
    } for f in scanned_files])

@app.route('/api/search/movie', methods=['POST'])
@login_required
def api_search_movie():
    data = request.json
    return jsonify(api_handler.search_movie(data.get('title'), data.get('year')))

@app.route('/api/search/tv', methods=['POST'])
@login_required
def api_search_tv():
    data = request.json
    return jsonify(api_handler.search_tv(data.get('title')))

@app.route('/api/movie/<int:movie_id>', methods=['GET'])
@login_required
def api_movie_details(movie_id):
    return jsonify(api_handler.get_movie_details(str(movie_id), request.args.get('source', 'tvdb')))

@app.route('/api/tv/<int:tv_id>', methods=['GET'])
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
    data = request.json
    old_path = Path(data.get('path'))
    new_name = data.get('new_name')
    try:
        new_path = old_path.parent / new_name
        old_path.rename(new_path)
        history = load_rename_history()
        history[str(new_path)] = {'original_path': str(old_path), 'original_name': old_path.name}
        save_rename_history(history)
        return jsonify({"success": True, "new_path": str(new_path), "new_name": new_name})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@app.route('/api/revert', methods=['POST'])
@login_required
def api_revert():
    data = request.json
    current_path = data.get('path')
    history = load_rename_history()
    entry = history.get(current_path)
    if not entry:
        return jsonify({"success": False, "message": "Aucun historique"}), 404
    try:
        Path(current_path).rename(Path(entry['original_path']))
        del history[current_path]
        save_rename_history(history)
        return jsonify({"success": True, "original_name": entry['original_name']})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@app.route('/api/rename-history', methods=['GET'])
@login_required
def api_rename_history():
    return jsonify(load_rename_history())

@app.route('/api/config', methods=['GET'])
@login_required
def api_get_config():
    safe = {k: v for k, v in config.items() if not k.startswith('_')}
    # Ne jamais exposer le mot de passe
    safe['password'] = '***' if safe.get('password') else ''
    # La clé TVDB est retournée en clair (pas de raison de la masquer côté config)
    return jsonify(safe)

@app.route('/api/config', methods=['POST'])
@login_required
def api_set_config():
    global config, scanner, api_handler, rename_engine
    data = request.json
    for key, val in data.items():
        if key == 'tvdb_api_key' and val and '...' in val:
            continue  # Ne pas écraser avec la valeur masquée
        if key == 'password' and val == '***':
            continue  # Ne pas écraser avec la valeur masquée
        config[key] = val
    save_config(config)
    config = load_config()
    scanner = MediaScanner(config['_movie_path'], config['_tv_path'])
    api_handler = APIHandler(config)
    rename_engine = RenameEngine(config)
    return jsonify({"success": True, "message": "Configuration sauvegardée"})

@app.route('/api/test-keys', methods=['POST'])
@login_required
def api_test_keys():
    data = request.json
    tvdb_key = data.get('tvdb_api_key', '').strip()
    if not tvdb_key or '...' in tvdb_key:
        tvdb_key = config.get('tvdb_api_key', '')
    results = {"tvdb": None}
    if tvdb_key:
        try:
            resp = requests.post("https://api4.thetvdb.com/v4/login", json={"apikey": tvdb_key}, timeout=5)
            results["tvdb"] = {"valid": resp.status_code == 200, "message": "✓ TVDB OK" if resp.status_code == 200 else f"✗ HTTP {resp.status_code}"}
        except Exception as e:
            results["tvdb"] = {"valid": False, "message": f"✗ {str(e)}"}
    else:
        results["tvdb"] = {"valid": False, "message": "Clé non fournie"}
    return jsonify(results)

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=5000)
