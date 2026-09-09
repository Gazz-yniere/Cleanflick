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

# Préchauffage serveur des médias non encore recherchés. Les clés de file sont
# les empreintes de fichiers : un même fichier ne peut donc déclencher deux
# recherches concurrentes après un scan ou l'ouverture de la bibliothèque.
_MEDIA_QUEUE = {}
_MEDIA_QUEUE_LOCK = threading.Lock()
_MEDIA_WORKER = None
_SERIES_QUEUE = set()


def enqueue_media_files(paths, include_cached=False):
    """Programme les recherches manquantes sans bloquer la requête HTTP."""
    global _MEDIA_WORKER
    if not paths:
        return
    with _MEDIA_QUEUE_LOCK:
        for path in paths:
            fp = file_fingerprint(path)
            if not fp:
                continue
            parent = os.path.dirname(path)
            for _ in range(4):
                match = re.search(r'\[tvdbid-([0-9]+)\]', os.path.basename(parent), re.I)
                if match:
                    _SERIES_QUEUE.add(match.group(1).lower())
                    break
                next_parent = os.path.dirname(parent)
                if next_parent == parent:
                    break
                parent = next_parent
            if not include_cached and file_cache_lookup(path) is not None:
                continue
            media = state.scanner._infer_media_type(os.path.basename(path))
            title = state.scanner._extract_title(os.path.basename(path), media)
            year = state.scanner._extract_year(os.path.basename(path))
            season = episode = None
            if media == 'tv':
                probe = type('MediaProbe', (), {'season': None, 'episode': None})()
                state.scanner._extract_episode_info(os.path.basename(path), probe)
                season, episode = probe.season, probe.episode
            _MEDIA_QUEUE[fp['fkey']] = {
                'path': path, 'media_type': media, 'title': title, 'year': year,
                'season': season, 'episode': episode,
            }
        if (_MEDIA_QUEUE or _SERIES_QUEUE) and (_MEDIA_WORKER is None or not _MEDIA_WORKER.is_alive()):
            _MEDIA_WORKER = threading.Thread(target=_media_worker_loop, daemon=True)
            _MEDIA_WORKER.start()


def enqueue_media_roots(roots, include_cached=False):
    """Parcourt les racines hors requête HTTP puis alimente la file média."""
    roots = [r for r in (roots or []) if r]
    if not roots:
        return

    def _walk():
        paths = []
        for root in roots:
            try:
                for folder, _dirs, files in os.walk(root):
                    paths.extend(os.path.join(folder, name) for name in files
                                 if os.path.splitext(name)[1].lower() in {
                                     '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'
                                 })
            except OSError:
                continue
        enqueue_media_files(paths, include_cached=include_cached)

    threading.Thread(target=_walk, daemon=True).start()


def _process_media_item(item):
    path = item['path']
    file_cached = file_cache_lookup(path)
    params = {
        'type': item['media_type'], 'title': item['title'], 'year': item['year'],
    }
    key = params_cache_key(params)
    cached = db.cache_get(key)
    results = (file_cached or {}).get('results', []) if file_cached else (cached or {}).get('results', [])
    if not cached and not file_cached:
        if item['media_type'] == 'tv':
            results = state.api_handler.search_tv(item['title'], item['year'])
        else:
            results = state.api_handler.search_movie(item['title'], item['year'])
    # Le cache TVDB peut exister avant le cache OMDb (interruption, ancienne
    # version) : l'enrichissement doit quand même être repris sans nouvelle
    # recherche TVDB.
    omdb.fetch_omdb_ratings(results[:1])
    if item['media_type'] == 'tv' and item.get('season') and item.get('episode') and results:
        top = results[0]
        omdb.fetch_omdb_episode(top.get('imdb_id', ''), top.get('title', item['title']),
                                top.get('year', item['year']), item['season'], item['episode'])
    if item['media_type'] == 'tv' and results:
        tvdb_id = (results[0].get('id_tvdb') or results[0].get('id') or '')
        if tvdb_id:
            _SERIES_QUEUE.add(str(tvdb_id).lower())
    if not cached and not file_cached:
        db.cache_set(key, item['media_type'], results)
    file_cache_store(path, item['media_type'], results)
    try:
        from . import duration
        duration.get_duration(path, non_blocking=True)
    except Exception:
        pass


def _media_worker_loop():
    while True:
        with _MEDIA_QUEUE_LOCK:
            fkey, item = next(iter(_MEDIA_QUEUE.items()), (None, None))
            if fkey:
                _MEDIA_QUEUE.pop(fkey, None)
                series_id = None
            else:
                series_id = next(iter(_SERIES_QUEUE), None)
                if series_id:
                    _SERIES_QUEUE.remove(series_id)
        if not item:
            if not series_id:
                return
            try:
                from . import library
                cached_episodes = db.series_episodes_get(series_id)
                episodes = (cached_episodes or {}).get('episodes') or []
                if not episodes or len(episodes) >= 500:
                    library.tvdb_series_episodes(series_id)
                imdb = omdb.series_imdb_cached(series_id) or omdb.resolve_series_imdb(series_id)
                if imdb and db.omdb_get(imdb) is None:
                    omdb.fetch_omdb_single(imdb, is_series=True)
            except Exception as e:
                logger.warning(f"Series metadata preload failed for {series_id}: {e}")
            continue
        try:
            _process_media_item(item)
        except Exception as e:
            logger.warning(f"Media metadata preload failed for {item.get('path')}: {e}")


def _schedule_omdb_quota_retry():
    if not omdb.quota_retry_due():
        return
    roots = [state.config.get('_input_path'), state.config.get('_movie_output_path'),
             state.config.get('_tv_output_path')]
    omdb.mark_quota_retry_scheduled()
    enqueue_media_roots(roots, include_cached=True)


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


def _refresh_omdb(imdb_id):
    try:
        imdb = str(imdb_id).strip().lower()
        if imdb:
            omdb.fetch_omdb_single(imdb, force=True)
    except Exception as e:
        logger.warning(f"Cache refresh OMDb failed for {imdb_id}: {e}")


def _refresh_omdb_episode(cache_key):
    match = re.match(r'^([^:]+):y([^:]+):s(\d+)e(\d+)$', str(cache_key or '').lower())
    if not match:
        return
    imdb, year, season, episode = match.groups()
    try:
        data = db.omdb_episode_get(cache_key) or {}
        title = data.get('Series') or data.get('Title') or ''
        if title:
            omdb.fetch_omdb_episode(imdb, title, year, int(season), int(episode), force=True)
    except Exception as e:
        logger.warning(f"Cache refresh OMDb episode failed for {cache_key}: {e}")


def _refresh_series_meta(tvdb_id):
    try:
        imdb = omdb.resolve_series_imdb(tvdb_id, force=True)
        if imdb:
            omdb.fetch_omdb_single(imdb, is_series=True, force=True)
    except Exception as e:
        logger.warning(f"Cache refresh series metadata failed for {tvdb_id}: {e}")


def start_cache_refresh_worker():
    global _refresh_running
    with _refresh_lock:
        if _refresh_running:
            return
        _refresh_running = True

    def _cache_refresh_worker():
        while True:
            days = _refresh_days()
            if days > 0:
                try:
                    for key in db.refresh_due('search_cache', days)[:25]:
                        _refresh_search_key(key)
                    for fkey in db.refresh_due('file_search_cache', days)[:25]:
                        _refresh_file_cache(fkey)
                    for cid in db.refresh_due('details_cache', days)[:25]:
                        kind, _, cid2 = cid.partition(':')
                        if cid2:
                            _refresh_details(kind, cid2)
                    for sid in db.refresh_due('series_episodes_cache', days)[:25]:
                        _refresh_series_episodes(sid)
                    for imdb in db.refresh_due('omdb_cache', days)[:25]:
                        _refresh_omdb(imdb)
                    for key in db.refresh_due('omdb_episode_cache', days)[:25]:
                        _refresh_omdb_episode(key)
                    for tvdb_id in db.refresh_due('series_meta_cache', days)[:25]:
                        _refresh_series_meta(tvdb_id)
                except Exception as e:
                    logger.warning(f"Cache refresh worker error: {e}")
            _schedule_omdb_quota_retry()
            time.sleep(300)

    threading.Thread(target=_cache_refresh_worker, daemon=True).start()
