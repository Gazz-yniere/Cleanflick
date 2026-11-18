"""Utilitaires de fichiers : déplacement, progression, versions statiques."""
import hashlib
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import db, state
from .history import append_history

logger = logging.getLogger(__name__)


def _ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def _static_version():
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')
    total = 0
    for root, _dirs, files in os.walk(base):
        for name in files:
            if name.endswith(('.js', '.css')):
                try:
                    total += int(os.path.getmtime(os.path.join(root, name)))
                except OSError:
                    pass
    return total


def _move_path(source_path, destination_path, job_id=None):
    src, dst = os.path.abspath(source_path), os.path.abspath(destination_path)
    if src == dst:
        return dst

    _ensure_dir(os.path.dirname(dst))
    if job_id is None:
        if os.path.exists(dst):
            os.remove(dst)
        return shutil.move(src, dst)

    if os.path.lexists(dst):
        raise FileExistsError(f"Le fichier destination existe déjà : {dst}")

    source_stat = os.stat(src)
    file_size = source_stat.st_size
    chunk = 16*1024*1024 if file_size > 1_073_741_824 else \
            8*1024*1024  if file_size > 104_857_600    else \
            2*1024*1024  if file_size > 10_485_760     else \
            512*1024

    copied, last_update, last_copied, speed_avg = 0, time.time(), 0, 0
    start = time.time()
    temp_dst = os.path.join(
        os.path.dirname(dst), f".{os.path.basename(dst)}.{uuid.uuid4().hex}.partial"
    )
    published = False
    source_digest = hashlib.sha256()
    try:
        with open(src, 'rb') as f_in, open(temp_dst, 'xb') as f_out:
            while buf := f_in.read(chunk):
                f_out.write(buf)
                source_digest.update(buf)
                copied += len(buf)
                now = time.time()
                elapsed = now - last_update
                if job_id in state.move_progress and (elapsed > 0.2 or copied >= file_size):
                    interval_speed = (copied - last_copied) / elapsed if elapsed > 0 else 0
                    speed_avg = interval_speed if speed_avg == 0 else speed_avg * 0.7 + interval_speed * 0.3
                    eta = (file_size - copied) / speed_avg if speed_avg > 0 else 0
                    percent = round((copied / file_size) * 100) if file_size else 0
                    state.move_progress[job_id].update({
                        'copied': copied, 'file_size': file_size,
                        'percent': percent,
                        'speed': round(speed_avg), 'eta': round(eta),
                        'phase': 'copying', 'finished': False,
                        'elapsed': round(now - start, 2),
                    })
                    last_update, last_copied = now, copied
            f_out.flush()
            os.fsync(f_out.fileno())

        current_stat = os.stat(src)
        if (copied != file_size or os.path.getsize(temp_dst) != file_size
                or current_stat.st_size != source_stat.st_size
                or current_stat.st_mtime_ns != source_stat.st_mtime_ns):
            raise OSError(f"La copie de {src} est incomplète ou le fichier source a changé")

        state.move_progress[job_id].update({
            'copied': 0, 'file_size': file_size, 'percent': 0,
            'speed': 0, 'eta': 0, 'phase': 'verifying', 'finished': False,
            'elapsed': round(time.time() - start, 2),
        })
        destination_digest = hashlib.sha256()
        verified_bytes = 0
        last_verify_update = time.time()
        with open(temp_dst, 'rb') as f_check:
            while buf := f_check.read(chunk):
                destination_digest.update(buf)
                verified_bytes += len(buf)
                now = time.time()
                if now - last_verify_update > 0.2 or verified_bytes >= file_size:
                    state.move_progress[job_id].update({
                        'copied': verified_bytes,
                        'percent': round(verified_bytes * 100 / file_size) if file_size else 100,
                        'elapsed': round(now - start, 2),
                    })
                    last_verify_update = now
        if source_digest.digest() != destination_digest.digest():
            raise OSError(f"La vérification du contenu a échoué : {dst}")
        if os.path.lexists(dst):
            raise FileExistsError(f"Le fichier destination existe déjà : {dst}")
        try:
            os.link(temp_dst, dst)
            published = True
            os.remove(temp_dst)
        except FileExistsError:
            raise
        except OSError as link_error:
            if os.path.lexists(dst):
                raise FileExistsError(f"Le fichier destination existe déjà : {dst}") from link_error
            try:
                os.rename(temp_dst, dst)
            except OSError as rename_error:
                raise rename_error from link_error
            published = True
        if not os.path.isfile(dst) or os.path.getsize(dst) != file_size:
            raise OSError(f"La vérification du fichier destination a échoué : {dst}")
    except Exception:
        if os.path.lexists(temp_dst):
            try:
                os.remove(temp_dst)
            except OSError:
                logger.exception("Impossible de supprimer le fichier temporaire %s", temp_dst)
        if published and os.path.exists(src) and os.path.lexists(dst):
            try:
                os.remove(dst)
            except OSError:
                logger.exception("Impossible de nettoyer la destination incomplète %s", dst)
        raise

    try:
        os.remove(src)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise OSError(
            f"Copie vérifiée dans {dst}, mais suppression de la source impossible : {e}"
        ) from e
    return dst


def _series_folder_name(new_name):
    stem = Path(new_name).stem
    m = re.match(r'^(.*?)(?:[\s._-]*(?:S\d{1,2}E\d{1,2}|\d{1,2}x\d{2})\b.*)?$', stem, re.I)
    return (m.group(1).strip() if m and m.group(1).strip() else stem)


def _safe_path_component(value):
    component = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', str(value or ''))
    component = re.sub(r'\s+', ' ', component).strip(' .')
    if not component or component in {'.', '..'}:
        raise ValueError("Nom de dossier de série invalide")
    if component.upper().split('.')[0] in {
            'CON', 'PRN', 'AUX', 'NUL',
            *(f'COM{i}' for i in range(1, 10)),
            *(f'LPT{i}' for i in range(1, 10))}:
        component = f'_{component}'
    return component


def _refresh_scan_snapshot():
    """Met à jour le snapshot de scan pour que le watcher ne traite pas le
    changement comme un nouveau fichier externe."""
    from . import watcher
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
            old_fkey = hashlib.md5(f"{os.path.abspath(old_p)}|{os.path.getsize(old_p) if os.path.exists(old_p) else 0}|{int(os.path.getmtime(old_p)) if os.path.exists(old_p) else 0}".encode('utf-8')).hexdigest()
            new_fkey = hashlib.md5(f"{os.path.abspath(new_p)}|{fsize}|{fmtime}".encode('utf-8')).hexdigest()
            db.file_cache_migrate(old_fkey, new_fkey, os.path.abspath(new_p), fsize, fmtime, os.path.basename(old_p))
            db.library_file_size_migrate(old_p, new_p, fsize, fmtime)
    except Exception as e:
        logger.warning(f"File cache migration failed for {old_p} -> {new_p}: {e}")


def _make_progress(file_size=0, finished=False, error=None, phase='copying', **extra):
    return {'copied': 0, 'file_size': file_size, 'percent': 0 if not finished else 100,
            'speed': 0, 'eta': 0, 'elapsed': 0,
            'finished': finished, 'error': error, 'phase': phase, **extra}


def _run_file_op(job_id, src_path, dst_path, history_entry):
    from . import watcher
    file_size = os.path.getsize(src_path)
    state.move_progress[job_id] = _make_progress(
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
            if not (target_exists and not source_exists and os.path.getsize(dst_path) == file_size):
                raise OSError(f"Vérification impossible après déplacement : {src_path} -> {dst_path}")
            state.move_progress[job_id] = _make_progress(
                file_size, finished=True, phase='done',
                copied=file_size, percent=100,
                new_path=dst_path, new_name=Path(dst_path).name,
                verified=True,
                source_exists=False,
                target_exists=True,
                destination=dst_path,
                source=src_path,
            )
            try:
                append_history(history_entry)
            except Exception:
                logger.exception("Le fichier a été déplacé, mais son historique n'a pas été enregistré")
            try:
                with state.scan_watch_lock:
                    state.scan_last_snapshot = watcher._scan_snapshot()
            except Exception:
                logger.exception("Impossible de mettre à jour le snapshot après déplacement")
            _migrate_file_cache(str(src_path), str(dst_path))
        except Exception as e:
            state.move_progress[job_id] = _make_progress(
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
