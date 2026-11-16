"""Routes de gestion de fichiers : déplacement, renommage, restauration, historique."""
import os
import time
import uuid
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from .. import state
from .. import library
from ..auth import login_required
from ..history import (load_history_entry, load_history_page,
                       load_reverted_history_ids, save_history, append_history)
from ..utils import _move_path, _run_file_op, _ensure_dir, _series_folder_name, _refresh_scan_snapshot, _migrate_file_cache

logger = logging.getLogger(__name__)
bp = Blueprint('files', __name__)


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
        # Les sous-titres du fichier suivent le renommage (même stem).
        for old_sub, new_sub in library.rename_subtitles_for_video(
                str(new_path), new_name, old_name=old_path.name):
            append_history({
                'id': str(uuid.uuid4()), 'op': 'rename',
                'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                'from_path': old_sub, 'from_name': os.path.basename(old_sub),
                'to_path': new_sub, 'to_name': os.path.basename(new_sub),
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
    entry = load_history_entry(entry_id)
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
    try:
        limit = min(500, max(1, int(request.args.get('limit', 100))))
        offset = max(0, int(request.args.get('offset', 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "Paramètres de pagination invalides"}), 400
    history = load_history_page(limit + 1, offset)
    has_more = len(history) > limit
    history = history[:limit]
    reverted_ids = load_reverted_history_ids()
    # Le statut reste calculé côté serveur, mais les chemins sont normalisés
    # une seule fois pour limiter les accès disque quand l'historique est long.
    existence = {}
    for e in history:
        for key in ('to_path', 'from_path'):
            path = e.get(key, '')
            if path not in existence:
                existence[path] = os.path.exists(path)
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
            to_exists   = existence.get(e.get('to_path', ''), False)
            from_exists = existence.get(e.get('from_path', ''), False)
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
    response = jsonify(history)
    response.headers['X-History-Has-More'] = 'true' if has_more else 'false'
    return response


@bp.route('/api/history/clear', methods=['POST'])
@login_required
def api_history_clear():
    save_history([])
    return jsonify({'success': True})
