"""Utilitaires de fichiers : déplacement, progression, versions statiques."""
import hashlib
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import db, state
from .history import append_history


def _ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def _static_version():
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')
    total = 0
    for name in ('app.js', 'i18n.js', 'app.css'):
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
        state.move_progress[job_id].update({
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
            if target_exists and not source_exists:
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
                append_history(history_entry)
                # After a successful async move, update the scan snapshot so the watcher
                # does not treat the change as a new external file.
                try:
                    with state.scan_watch_lock:
                        state.scan_last_snapshot = watcher._scan_snapshot()
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
