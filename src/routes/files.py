"""Routes de gestion de fichiers : déplacement, renommage, restauration, historique."""
import os
import time
import uuid
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from .. import db, state
from ..auth import login_required
from ..history import load_history, save_history, append_history
from ..utils import _move_path, _run_file_op, _ensure_dir, _series_folder_name
from .. import watcher

logger = logging.getLogger(__name__)
bp = Blueprint('files', __name__)


def _refresh_scan_snapshot():
    """Met à jour le snapshot de scan pour que le watcher ne traite pas le
    changement comme un nouveau fichier externe."""
    try:
        with state.scan_watch_lock:
            state.scan_last_snapshot = watcher._scan_snapshot()
    except Exception:
        pass


def _migrate_file_cache(old_p, new_p):
    """Migre l'entrée de cache de recherche de l'ancien vers le nouveau chemin."""
    try:
        if os.path.exists(new_p):
            fsize = os.path.getsize(new_p)
            fmtime = int(os.path.getmtime(new_p))
            import hashlib
            old_fkey = hashlib.md5(f"{os.path.abspath(old_p)}|{os.path.getsize(old_p) if os.path.exists(old_p) else 0}|{int(os.path.getmtime(old_p)) if os.path.exists(old_p) else 0}".encode('utf-8')).hexdigest()
            new_fkey = hashlib.md5(f"{os.path.abspath(new_p)}|{fsize}|{fmtime}".encode('utf-8')).hexdigest()
            db.file_cache_migrate(old_fkey, new_fkey, os.path.abspath(new_p), fsize, fmtime, os.path.basename(old_p))
    except Exception:
        pass


@bp.route('/api/rename', methods=['POST'])
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
        _refresh_scan_snapshot()
        _migrate_file_cache(str(old_path), str(new_path))
        return jsonify({"success": True, "new_path": str(new_path), "new_name": new_name})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@bp.route('/api/move', methods=['POST'])
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
        target_dir = Path(state.config.get('_tv_output_path') or state.config.get('tv_output_path')) / _series_folder_name(folder_source)
    else:
        target_dir = Path(state.config.get('_movie_output_path') or state.config.get('movie_output_path'))

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


@bp.route('/api/move-progress/<job_id>')
@login_required
def api_move_progress(job_id):
    prog = state.move_progress.get(job_id)
    if prog is None:
        return jsonify({'finished': True, 'percent': 100, 'copied': 0, 'file_size': 0,
                        'speed': 0, 'eta': 0, 'elapsed': 0, 'error': None,
                        'verified': True, 'source_exists': False, 'target_exists': True})
    resp = {k: prog.get(k) for k in ('finished', 'percent', 'copied', 'file_size', 'speed', 'eta', 'elapsed', 'error', 'phase', 'new_path', 'new_name', 'verified', 'source_exists', 'target_exists')}
    return jsonify(resp)


@bp.route('/api/revert', methods=['POST'])
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
            _refresh_scan_snapshot()
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


@bp.route('/api/history')
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


@bp.route('/api/history/clear', methods=['POST'])
@login_required
def api_history_clear():
    save_history([])
    return jsonify({'success': True})
