"""Routes de configuration : lecture/écriture de la config + test des clés API."""
import logging

import requests
from flask import Blueprint, jsonify, request

from .. import state
from ..config import load_config, save_config
from ..auth import get_password, login_required

logger = logging.getLogger(__name__)
bp = Blueprint('config', __name__)


@bp.route('/api/config', methods=['GET'])
@login_required
def api_get_config():
    safe = {k: v for k, v in state.config.items() if not k.startswith('_')}
    safe['password_set'] = bool(get_password())
    return jsonify(safe)


@bp.route('/api/config', methods=['POST'])
@login_required
def api_set_config():
    cfg = state.config
    d = request.json
    for key, val in d.items():
        if key in ('movie_path', 'tv_path'):
            if val:
                cfg['input_path'] = val
        elif key in ('movie_output_path', 'tv_output_path'):
            cfg[key] = val
        elif key == 'tvdb_api_key' and val and '...' in val:
            continue
        elif key == 'omdb_api_key' and val and '...' in val:
            continue
        elif key == 'password':
            continue
        elif key == 'cache_refresh_days':
            try:
                cfg[key] = max(0, int(val))
            except (TypeError, ValueError):
                cfg[key] = 0
        else:
            cfg[key] = val
    save_config(cfg)
    state.init(load_config())
    return jsonify({"success": True, "message": "Configuration sauvegardée"})


@bp.route('/api/test-keys', methods=['POST'])
@login_required
def api_test_keys():
    d = request.json
    tvdb_key = d.get('tvdb_api_key', '').strip()
    if not tvdb_key or '...' in tvdb_key:
        tvdb_key = state.config.get('tvdb_api_key', '')
    omdb_key = d.get('omdb_api_key', '').strip()
    if not omdb_key or '...' in omdb_key:
        omdb_key = state.config.get('omdb_api_key', '')
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
