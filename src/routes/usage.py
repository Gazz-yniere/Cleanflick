"""Route de consommation des API (usage TVDB/OMDb + quota OMDb)."""
from flask import Blueprint, jsonify

from .. import db
from ..api import omdb
from ..auth import login_required

bp = Blueprint('usage', __name__)


@bp.route('/api/usage')
@login_required
def api_usage():
    return jsonify({'usage': db.usage_get(), 'omdb_quota': omdb._omdb_quota_state()})
