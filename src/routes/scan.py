"""Routes de scan : liste des fichiers détectés + flux SSE de rafraîchissement."""
import threading

from flask import Blueprint, Response, jsonify

from .. import state
from ..auth import login_required
from .. import duration
from .. import library

bp = Blueprint('scan', __name__)


@bp.route('/api/scan')
@login_required
def api_scan():
    state.scanned_files = state.scanner.scan()
    out = []
    for f in state.scanned_files:
        item = {
            'filename': f.filename, 'path': f.path,
            'media_type': f.media_type, 'title': f.title,
            'season': f.season, 'episode': f.episode, 'year': f.year
        }
        try:
            item['duration'] = duration.duration_for(f.path)
        except Exception:
            item['duration'] = None
        out.append(item)
    threading.Thread(target=library.ensure_library_series_episodes, daemon=True).start()
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
