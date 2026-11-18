"""Routes de scan : liste des fichiers détectés + flux SSE de rafraîchissement."""
from flask import Blueprint, Response, jsonify

from .. import state
from ..auth import login_required
from .. import duration

bp = Blueprint('scan', __name__)


@bp.route('/api/scan')
@login_required
def api_scan():
    files = state.scanned_files = state.scanner.scan()
    duration_map = duration.durations_for(f.path for f in files)
    out = []
    for f in files:
        item = {
            'filename': f.filename, 'path': f.path,
            'media_type': f.media_type, 'title': f.title,
            'season': f.season, 'episode': f.episode, 'year': f.year
        }
        item['duration'] = duration_map.get(f.path)
        out.append(item)
    # Une recherche TVDB/OMDb nécessite une validation explicite de l'utilisateur.
    # Seul le calcul local des durées peut continuer en arrière-plan.
    return jsonify(out)


@bp.route('/api/scan/events')
@login_required
def api_scan_events():
    try:
        from gevent.queue import Queue as GQueue
        q = GQueue()
    except ImportError:
        import queue
        q = queue.Queue()
    state.scan_clients.add(q)

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
                state.scan_clients.remove(q)
            except Exception:
                pass

    return Response(stream(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })
