"""Caches de recherche : empreintes fichiers + worker de rafraîchissement.

Le worker (optionnel, `cache_refresh_days > 0`) re-réinterroge les API
(TVDB/OMDb) pour les entrées plus vieilles que le délai. 0 = jamais."""
import json
import os
import re
import time
import threading
import hashlib
import logging

from . import db, state
from .api import omdb

logger = logging.getLogger(__name__)


def file_fingerprint(file_path):
    """Compute the cache fingerprint for a file (path + size + mtime)."""
    try:
        if file_path and os.path.exists(file_path):
            fsize = os.path.getsize(file_path)
            fmtime = int(os.path.getmtime(file_path))
            return {
                'fkey': hashlib.md5(f"{os.path.abspath(file_path)}|{fsize}|{fmtime}".encode('utf-8')).hexdigest(),
                'abspath': os.path.abspath(file_path),
                'size': fsize,
                'mtime': fmtime,
            }
    except Exception:
        pass
    return None


def file_cache_lookup(file_path):
    """Return the cached file search results for a path, or None."""
    fp = file_fingerprint(file_path)
    if not fp:
        return None
    try:
        return db.file_cache_get(fp['fkey'])
    except Exception:
        return None


def file_cache_store(file_path, media_type, results):
    """Persist search results against a file fingerprint."""
    fp = file_fingerprint(file_path)
    if not fp:
        return
    try:
        db.file_cache_set(fp['fkey'], fp['abspath'], fp['size'], fp['mtime'], media_type, results)
    except Exception:
        pass


def params_cache_key(params):
    return hashlib.md5(json.dumps(params, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


# ── Cache auto-refresh (optionnel, en fond) ───────────────────────────────────
# Par défaut le cache est permanent : les infos sont cherchées une fois, puis
# servies sans requête API. Si cache_refresh_days > 0, un worker en fond
# re-réinterroge les API (TVDB/OMDb) pour les entrées plus vieilles que le
# délai. 0 = jamais.
_refresh_lock = threading.Lock()
_refresh_running = False


def _refresh_days():
    try:
        return max(0, int(state.config.get('cache_refresh_days') or 0))
    except (TypeError, ValueError):
        return 0


def _refresh_search_key(key):
    try:
        if key not in db.refresh_due('search_cache', _refresh_days()):
            return
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute('SELECT media_type, results_json FROM search_cache WHERE key = ?', (key,))
        r = cur.fetchone()
        conn.close()
    except Exception:
        return
    if not r:
        return
    try:
        results = json.loads(r['results_json'] or '[]')
    except Exception:
        return
    if not results:
        return
    first = results[0]
    title = first.get('title', '')
    if not title:
        return
    try:
        if r['media_type'] == 'tv':
            res = state.api_handler.search_tv(title)
        else:
            res = state.api_handler.search_movie(title, first.get('year'))
    except Exception as e:
        logger.warning(f"Cache refresh search failed for {title!r}: {e}")
        return
    if not res:
        return
    omdb.fetch_omdb_ratings(res[:3])
    omdb.attach_omdb(res)
    try:
        db.cache_set(key, r['media_type'], res)
    except Exception:
        pass


def _refresh_file_cache(fkey):
    try:
        if fkey not in db.refresh_due('file_search_cache', _refresh_days()):
            return
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute('SELECT file_path, media_type, results_json FROM file_search_cache WHERE fkey = ?', (fkey,))
        r = cur.fetchone()
        conn.close()
    except Exception:
        return
    if not r:
        return
    fp = file_fingerprint(r['file_path'])
    if not fp or fp['fkey'] != fkey:
        return
    try:
        results = json.loads(r['results_json'] or '[]')
    except Exception:
        return
    if not results:
        return
    first = results[0]
    title = first.get('title', '')
    if not title:
        return
    try:
        if r['media_type'] == 'tv':
            res = state.api_handler.search_tv(title)
        else:
            res = state.api_handler.search_movie(title, first.get('year'))
    except Exception as e:
        logger.warning(f"Cache refresh file search failed for {title!r}: {e}")
        return
    if not res:
        return
    omdb.fetch_omdb_ratings(res[:3])
    omdb.attach_omdb(res)
    try:
        db.file_cache_set(fkey, fp['abspath'], fp['size'], fp['mtime'], r['media_type'], res)
    except Exception:
        pass


def _refresh_details(kind, cid):
    try:
        rows = db.refresh_due('details_cache', _refresh_days(), now=time.time())
    except Exception:
        return
    if f"{kind}:{cid}" not in rows:
        return
    try:
        if kind == 'tv':
            m = re.match(r'^(\d+):s(\d+)e(\d+)$', cid)
            if not m:
                return
            tv_id, season, episode = int(m.group(1)), int(m.group(2)), int(m.group(3))
            details = state.api_handler.get_tv_details(str(tv_id), season, episode)
            if details.get('title') and season and episode:
                omdb.fetch_omdb_episode(details.get('imdbid') or details.get('imdb') or '', details.get('title'), details.get('year'), season, episode)
        else:
            details = state.api_handler.get_movie_details(cid)
    except Exception as e:
        logger.warning(f"Cache refresh details failed for {kind}:{cid}: {e}")
        return
    try:
        db.details_set(kind, cid, details)
    except Exception:
        pass


def _refresh_series_episodes(series_id):
    try:
        rows = db.refresh_due('series_episodes_cache', _refresh_days(), now=time.time())
    except Exception:
        return
    if str(series_id).lower() not in rows:
        return
    from .library import tvdb_series_episodes
    tvdb_series_episodes(series_id)


def start_cache_refresh_worker():
    def _cache_refresh_worker():
        while True:
            days = _refresh_days()
            if days > 0:
                try:
                    for key in db.refresh_due('search_cache', days):
                        _refresh_search_key(key)
                    for fkey in db.refresh_due('file_search_cache', days):
                        _refresh_file_cache(fkey)
                    for cid in db.refresh_due('details_cache', days):
                        kind, _, cid2 = cid.partition(':')
                        if cid2:
                            _refresh_details(kind, cid2)
                    for sid in db.refresh_due('series_episodes_cache', days):
                        _refresh_series_episodes(sid)
                except Exception as e:
                    logger.warning(f"Cache refresh worker error: {e}")
            time.sleep(300)

    threading.Thread(target=_cache_refresh_worker, daemon=True).start()
