"""Service OMDb : notes, dates, genres, runtime — avec gestion de quota,
cache négatif et résolution TVDB → IMDb.

Les fonctions sont module-level (pas de classe) : l'état (config, api_handler)
est lu via `state` au moment de l'appel."""
import re
import time
import logging

import requests

from .. import db, state

logger = logging.getLogger(__name__)

_OMDB_NEG_CACHE = {}       # imdb_id -> timestamp d'expiration des échecs/réponses négatives OMDb
_OMDB_NEG_TTL = 300        # 5 min : évite de marteler OMDb (timeout ~6s) quand il est indisponible
_OMDB_QUOTA_LOCKOUT_SECS = 86400  # 24 h : suspension OMDb une fois le quota journalier atteint
_OMDB_QUOTA_KEY = 'omdb_quota_paused_until'  # clé persistée en base (survit aux redémarrages)


def _omdb_quota_paused_until():
    """Timestamp d'expiration du verrou quota OMDb (0 si aucun verrou)."""
    try:
        return float(db.meta_get(_OMDB_QUOTA_KEY, 0) or 0)
    except Exception:
        return 0


def _omdb_quota_locked():
    """Vrai tant que le quota OMDb est atteint : aucune requête réseau autorisée."""
    return time.time() < _omdb_quota_paused_until()


def _omdb_quota_state():
    """État courant du quota pour l'API : {'locked': bool, 'resumes_in_secs': int}."""
    until = _omdb_quota_paused_until()
    now = time.time()
    if until > now:
        return {'locked': True, 'resumes_in_secs': int(until - now)}
    return {'locked': False, 'resumes_in_secs': 0}


def _omdb_mark_quota_reached():
    """Persiste le verrou quota 24 h et log une alerte claire."""
    until = time.time() + _OMDB_QUOTA_LOCKOUT_SECS
    db.meta_set(_OMDB_QUOTA_KEY, str(int(until)))
    logger.warning("OMDb: quota journalier atteint — requêtes suspendues pendant 24 h")


def fetch_omdb_single(imdb_id, title=None, year=None, is_series=False):
    """Récupère et met en cache l'OMDb d'un seul imdb_id (repli on-demand)."""
    key = (state.config.get('omdb_api_key') or '').strip()
    if not key or not imdb_id:
        return None
    if _omdb_quota_locked():
        return None
    imdb = imdb_id.strip().lower()
    if db.omdb_get(imdb) is not None:
        return db.omdb_get(imdb)
    now = time.time()
    if imdb in _OMDB_NEG_CACHE:
        if now < _OMDB_NEG_CACHE[imdb]:
            return None
        del _OMDB_NEG_CACHE[imdb]
    try:
        if is_series and title:
            params = {'t': title, 'y': year, 'type': 'series', 'apikey': key}
        else:
            params = {'i': imdb, 'apikey': key}
        resp = requests.get('https://www.omdbapi.com/', params=params, timeout=6)
        data = resp.json()
        db.usage_bump('omdb')
        if data.get('Response') != 'True':
            err = str(data.get('Error', '')).lower()
            logger.info(f"[OMDb] {imdb} ({title or '-'}): {data.get('Error', 'erreur')}")
            if 'limit' in err or 'quota' in err or 'request rate' in err:
                _omdb_mark_quota_reached()
            else:
                _OMDB_NEG_CACHE[imdb] = now + _OMDB_NEG_TTL
            return None
        oimdb = (data.get('imdbID') or '').lower()
        db.omdb_set(oimdb or imdb, data)
        if oimdb and oimdb != imdb:
            db.omdb_set(imdb, data)
        _OMDB_NEG_CACHE.pop(imdb, None)
        logger.info(f"[OMDb] {imdb} ({title or '-'}): note={data.get('imdbRating')} « {data.get('Title', '')} »")
        return data
    except Exception as e:
        logger.warning(f"OMDb single fetch error for {imdb}: {e}")
        _OMDB_NEG_CACHE[imdb] = now + _OMDB_NEG_TTL
        return None


def fetch_omdb_ratings(results):
    key = (state.config.get('omdb_api_key') or '').strip()
    if not key:
        return
    for res in results or []:
        imdb = (res.get('imdb_id') or '').strip().lower()
        if not imdb:
            continue
        if db.omdb_get(imdb) is not None:
            continue
        is_series = (res.get('type') or '').lower() in ('series', 'show', 'tv')
        fetch_omdb_single(imdb, res.get('title'), res.get('year'), is_series)


def attach_omdb(results):
    try:
        omdb_map = db.omdb_all()
    except Exception:
        return results
    for r in results or []:
        imdb = (r.get('imdb_id') or '').strip().lower()
        if imdb and imdb in omdb_map:
            r['omdb'] = omdb_map[imdb]
    return results


def attach_episode_omdb(results, season, episode):
    """Attache l'OMDb de l'épisode (durée/note/date) à chaque résultat TV, distinct de la série."""
    try:
        omdb_ep = db.omdb_episode_all()
    except Exception:
        omdb_ep = {}
    for r in (results or [])[:3]:
        imdb = (r.get('imdb_id') or '').strip().lower()
        title = r.get('title')
        year = r.get('year')
        rtype = (r.get('type') or '').lower()
        if not imdb or not title or not season or not episode:
            continue
        if rtype and rtype not in ('series', 'show', 'tv'):
            continue
        fetch_omdb_episode(imdb, title, year, season, episode)
        ekey = f"{imdb}:y{year}:s{int(season):02d}e{int(episode):02d}"
        ed = omdb_ep.get(ekey) or db.omdb_episode_get(ekey)
        if ed:
            r['episode_omdb'] = ed
    return results


def fetch_omdb_episode(series_imdb, title, year, season, episode):
    """Récupère la note OMDb d'un épisode (titre + année + saison + épisode) et la met en cache."""
    key = (state.config.get('omdb_api_key') or '').strip()
    if not key or not title or not season or not episode:
        return
    if _omdb_quota_locked():
        return
    cache_key = f"{series_imdb}:y{year}:s{int(season):02d}e{int(episode):02d}"
    if db.omdb_episode_get(cache_key) is not None:
        return
    try:
        params = {
            't': title, 'Season': int(season), 'Episode': int(episode), 'type': 'series', 'apikey': key
        }
        if year:
            params['y'] = year
        resp = requests.get('https://www.omdbapi.com/', params=params, timeout=6)
        data = resp.json()
        db.usage_bump('omdb')
        if data.get('Response') == 'True':
            db.omdb_episode_set(cache_key, data)
            logger.info(f"[OMDb épisode] {title} S{int(season):02d}E{int(episode):02d}: note={data.get('imdbRating')} durée={data.get('Runtime')}")
        else:
            err = str(data.get('Error', '')).lower()
            logger.info(f"[OMDb épisode] {title} S{int(season):02d}E{int(episode):02d}: {data.get('Error', 'erreur')}")
            if 'limit' in err or 'quota' in err or 'request rate' in err:
                _omdb_mark_quota_reached()
    except Exception as e:
        logger.warning(f"OMDb episode fetch error for {title} S{season}E{episode}: {e}")


_OMDB_MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}


def omdb_date(s):
    """Convertit une date OMDb ('21 Apr 2021') en ISO (2021-04-21)."""
    m = re.match(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', s or '')
    if m:
        mon = _OMDB_MONTHS.get(m.group(2).capitalize())
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
    return s or ''


# ── Résolution TVDB → IMDb (les dossiers séries ne portent que l'ID TVDB) ───
# Cache mémoire court (évite de relire la BDD à chaque rendu de page).
_SERIES_META_MEM = {}
_SERIES_META_MEM_TTL = 600


def _series_imdb_map():
    now = time.time()
    if now - _SERIES_META_MEM.get('_t', 0) > _SERIES_META_MEM_TTL:
        try:
            data = db.series_meta_all()
        except Exception:
            data = {}
        _SERIES_META_MEM.clear()
        for k, v in data.items():
            _SERIES_META_MEM[k] = v.get('imdb_id', '')
        _SERIES_META_MEM['_t'] = now
    return {k: v for k, v in _SERIES_META_MEM.items() if k != '_t'}


def series_imdb_cached(tvdb_id):
    return _series_imdb_map().get(str(tvdb_id).strip().lower(), '')


def resolve_series_imdb(tvdb_id, title=None, year=None, force=False):
    """Récupère l'ID IMDb d'une série depuis TVDB (1 requête TVDB, cache BDD permanent).
    Les détails TVDB de la série sont aussi mis en cache.
    Fallback : recherche TVDB par titre si l'ID TVDB est inconnu côté API."""
    tvdb_id = str(tvdb_id).strip().lower()
    if not tvdb_id:
        return ''
    if not force:
        cached = db.series_meta_get(tvdb_id)
        if cached and cached.get('data'):
            return cached.get('imdb_id', '')
    api_handler = state.api_handler
    if not getattr(api_handler, 'tvdb', None):
        return ''
    try:
        imdb = ''
        details = {}
        try:
            details = api_handler.tvdb.get_series_details(int(tvdb_id)) or {}
            imdb = (details.get('imdbid') or details.get('imdb') or '').strip()
        except Exception:
            details = {}
        if not imdb and title:
            res = api_handler.tvdb.search_series(title, year)
            if res:
                details = res[0]
                imdb = (details.get('imdb_id') or '').strip()
        if details or imdb:
            db.series_meta_set(tvdb_id, imdb, details)
            _SERIES_META_MEM[tvdb_id] = imdb
            _SERIES_META_MEM['_t'] = time.time()
        return imdb
    except Exception as e:
        logger.warning(f"Series TVDB→IMDb resolution error for {tvdb_id}: {e}")
        return ''
