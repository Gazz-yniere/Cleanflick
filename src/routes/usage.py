"""Route de consommation des API (usage TVDB/OMDb + quota OMDb)."""
from flask import Blueprint, jsonify, request

from .. import db
from ..api import omdb
from ..auth import login_required

bp = Blueprint('usage', __name__)


@bp.route('/api/usage')
@login_required
def api_usage():
    return jsonify({'usage': db.usage_get(), 'omdb_quota': omdb._omdb_quota_state()})


@bp.route('/api/cache/purge', methods=['POST'])
@login_required
def api_cache_purge():
    """Supprime de la BDD les entrées qui ne correspondent plus à rien sur le
    disque (fichiers/séries supprimés). Balayage des bibliothèques → peut être
    lent sur un partage réseau : renvoi immédiat + purge en arrière-plan."""
    from .. import cache as _cache
    force = bool((request.json or {}).get('force'))
    started = _cache.run_purge_background(force)
    return jsonify({'status': 'started' if started else 'skipped'})
