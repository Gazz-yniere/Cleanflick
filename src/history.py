"""Historique des opérations (persisté en base)."""
import os
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from . import db

logger = logging.getLogger(__name__)

_PATH_STATUS_TTL = 15
_PATH_STATUS_LOCK = threading.Lock()
_PATH_STATUS_CACHE = {}
_PATH_STATUS_INFLIGHT = {}
_PATH_STATUS_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix='history-path')


def _path_status_key(path):
    return os.path.normcase(os.path.abspath(path)) if path else ''


def _path_exists(path):
    if not path:
        return False
    try:
        os.stat(path)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def _cache_path_status(key, future):
    try:
        exists = future.result()
    except Exception:
        logger.warning("History path status refresh failed for %s", key, exc_info=True)
    else:
        with _PATH_STATUS_LOCK:
            if _PATH_STATUS_INFLIGHT.get(key) is future:
                _PATH_STATUS_CACHE[key] = (exists, time.monotonic())
    finally:
        with _PATH_STATUS_LOCK:
            if _PATH_STATUS_INFLIGHT.get(key) is future:
                _PATH_STATUS_INFLIGHT.pop(key, None)


def _submit_path_status_locked(key, path, force=False):
    current = _PATH_STATUS_INFLIGHT.get(key)
    if current is not None and not force:
        return current, None
    future = _PATH_STATUS_EXECUTOR.submit(_path_exists, path)
    _PATH_STATUS_INFLIGHT[key] = future
    return future, (key, future)


def path_existence_statuses(paths, refresh=False):
    """Return file presence, serving recent/stale cache entries while refreshing."""
    paths = list(paths or [])
    unique_paths = {}
    for path in paths:
        key = _path_status_key(path)
        unique_paths.setdefault(key, path)

    now = time.monotonic()
    by_key = {}
    waiting = {}
    callbacks = []
    with _PATH_STATUS_LOCK:
        for key, path in unique_paths.items():
            cached = _PATH_STATUS_CACHE.get(key)
            callback = None
            if refresh:
                future, callback = _submit_path_status_locked(key, path, force=True)
                waiting[key] = future
            elif cached and now - cached[1] < _PATH_STATUS_TTL:
                by_key[key] = cached[0]
            elif cached:
                by_key[key] = cached[0]
                _, callback = _submit_path_status_locked(key, path)
            else:
                future, callback = _submit_path_status_locked(key, path)
                waiting[key] = future
            if callback:
                callbacks.append(callback)

    for key, future in callbacks:
        future.add_done_callback(lambda done, cache_key=key: _cache_path_status(cache_key, done))

    for key, future in waiting.items():
        try:
            exists = future.result()
        except Exception as e:
            raise RuntimeError(f"History path status unavailable: {key}") from e
        by_key[key] = exists
        with _PATH_STATUS_LOCK:
            if _PATH_STATUS_INFLIGHT.get(key) is future:
                _PATH_STATUS_CACHE[key] = (exists, time.monotonic())

    return {path: by_key[_path_status_key(path)] for path in paths}


def invalidate_path_statuses(paths=None):
    with _PATH_STATUS_LOCK:
        if paths is None:
            _PATH_STATUS_CACHE.clear()
            _PATH_STATUS_INFLIGHT.clear()
            return
        for path in paths:
            key = _path_status_key(path)
            _PATH_STATUS_CACHE.pop(key, None)
            _PATH_STATUS_INFLIGHT.pop(key, None)


def load_history():
    try:
        return db.get_history()
    except Exception as e:
        logger.error(f"Error loading history from DB: {e}")
        return []


def load_history_page(limit, offset):
    return db.get_history(limit=limit, offset=offset)


def load_history_entry(entry_id):
    try:
        return db.get_history_entry(entry_id)
    except Exception as e:
        logger.error(f"Error loading history entry from DB: {e}")
        return None


def load_reverted_history_ids(entry_ids=None):
    return db.get_reverted_history_ids(entry_ids)


def save_history(history):
    # Not used: history is persisted via DB
    try:
        # Replace DB contents with provided list
        db.clear_history()
        for entry in (history or []):
            db.add_history(entry)
        invalidate_path_statuses()
    except Exception as e:
        logger.error(f"Error saving history to DB: {e}")
        raise


def append_history(entry):
    try:
        db.add_history(entry)
        invalidate_path_statuses((entry.get('from_path'), entry.get('to_path')))
    except Exception as e:
        logger.error(f"Error appending history to DB: {e}")


def append_history_batch(entries):
    try:
        db.add_history_batch(entries)
        invalidate_path_statuses(
            path for entry in entries for path in (entry.get('from_path'), entry.get('to_path'))
        )
    except Exception as e:
        logger.error(f"Error appending history batch to DB: {e}")
