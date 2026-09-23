"""Routes de recherche (auto/film/série) et détails, avec cache + enrichissement OMDb."""
import logging

from flask import Blueprint, jsonify, request

from .. import db, state
from ..auth import login_required
from ..cache import file_cache_lookup, file_cache_store, params_cache_key
from ..api import omdb

logger = logging.getLogger(__name__)
bp = Blueprint('search', __name__)


def _imdb_from_path(file_path):
    """Extrait l'ID IMDb d'un nom de fichier : « ... [imdb-tt12345678] ... »."""
    import os
    from ..api.tvdb import TVDBAPIHandler
    return TVDBAPIHandler.imdb_from_name(os.path.basename(str(file_path or '')))


def _results_have_imdb(results, imdb_id):
    return bool(imdb_id) and any(
        str(r.get('imdb_id') or '').strip().lower() == imdb_id for r in (results or []))


def _match_imdb(results, imdb_id):
    """Le premier résultat dont l'ID IMDb correspond, ou None."""
    imdb = str(imdb_id or '').strip().lower()
    if not imdb:
        return None
    for r in results or []:
        if str(r.get('imdb_id') or '').strip().lower() == imdb:
            return r
    return None


def _omdb_stub(file_imdb, media_type, title, year):
    """Stub de résultat bâti uniquement depuis OMDb (le média n'existe pas sur
    TVDB) : infos de base, pas de poster, drapeau not_found. None si OMDb ne
    le connaît pas non plus."""
    od = omdb.fetch_omdb_single(file_imdb, title, year, is_series=(media_type == 'tv'))
    if not od or od.get('Response') != 'True':
        return None
    oyear = str(od.get('Year') or '').strip()[:4]
    return [{
        'id': 0, 'id_tvdb': '0',
        'imdb_id': str(file_imdb).strip().lower(), 'tmdb_id': '',
        'title': od.get('Title') or title,
        'year': int(oyear) if oyear.isdigit() else (year or ''),
        'poster': '', 'omdb': od, 'not_found': True,
        'type': 'series' if media_type == 'tv' else 'movie',
        'source': 'omdb',
    }]


def _filter_for_file(results, file_imdb, media_type, title, year):
    """Si le nom de fichier porte un ID IMDb, seuls les résultats qui
    correspondent (ou le stub OMDb) sont servis : jamais les infos d'un autre
    média."""
    if not file_imdb:
        return results
    m = _match_imdb(results, file_imdb)
    if m is not None:
        rest = [r for r in results if r is not m]
        m = dict(m)
        m.pop('not_found', None)
        return [m] + rest
    stub = _omdb_stub(file_imdb, media_type, title, year)
    return stub if stub else []


@bp.route('/api/search/auto', methods=['POST'])
@login_required
def api_search_auto():
    d = request.json or {}
    params = {
        'title': d.get('title', ''), 'year': d.get('year'),
        'filename': d.get('filename', ''), 'season': d.get('season'),
        'episode': d.get('episode'), 'media_hint': d.get('media_hint', ''),
    }
    # If client provided a file path, try file-specific cache (fingerprint based)
    file_path = d.get('path') or d.get('file_path')
    file_imdb = _imdb_from_path(file_path)
    if not file_imdb:
        file_imdb = _imdb_from_path(params.get('filename'))
    cached_file = file_cache_lookup(file_path)
    if cached_file is not None and not (file_imdb and not _results_have_imdb(cached_file.get('results'), file_imdb)):
        logger.info(f"Search auto: cache hit (file) for {file_path}")
        out = dict(cached_file)
        out['cache_source'] = 'file'
        omdb.fetch_omdb_ratings(out.get('results', [])[:3])
        omdb.attach_omdb(out.get('results', []))
        out['results'] = _filter_for_file(out.get('results', []), file_imdb,
                                          out.get('media_type') or 'movie', params['title'], params.get('year'))
        return jsonify(out)
    if cached_file is not None:
        logger.info(f"Search auto: cache (file) INVALIDÉ pour {file_path} (imdb {file_imdb} absent) → re-recherche")

    key = params_cache_key(params)
    cached = db.cache_get(key)
    # Le worker et les routes spécialisées stockent aussi un cache générique
    # par titre. Il doit être consulté avant TVDB, même si la clé « auto »
    # contient des détails différents (nom de fichier, épisode, media_hint).
    generic_keys = []
    if params.get('media_hint') == 'movie':
        generic_keys.append(params_cache_key({'type': 'movie', 'title': params['title'], 'year': params['year']}))
    elif params.get('media_hint') == 'tv':
        generic_keys.extend([
            params_cache_key({'type': 'tv', 'title': params['title'], 'year': params['year']}),
            params_cache_key({'type': 'tv', 'title': params['title']}),
        ])
    if cached is None:
        for generic_key in generic_keys:
            cached = db.cache_get(generic_key)
            if cached is not None and (not file_imdb or _results_have_imdb(cached.get('results'), file_imdb)):
                break
            cached = None
    if cached is not None and (not file_imdb or _results_have_imdb(cached.get('results'), file_imdb)):
        logger.info(f"Search auto: cache hit (params) key={key}")
        out = dict(cached)
        out['cache_source'] = 'params'
        omdb.fetch_omdb_ratings(out.get('results', [])[:3])
        omdb.attach_omdb(out.get('results', []))
        out['results'] = _filter_for_file(out.get('results', []), file_imdb,
                                          out.get('media_type') or 'movie', params['title'], params.get('year'))
        return jsonify(out)

    logger.info(f"Search auto: cache miss title={params['title']} year={params.get('year')} imdb={file_imdb or '-'}"
                + (f" file={file_path}" if file_path else ''))
    result = state.api_handler.search_auto(params['title'], params['year'], params['filename'], params['season'], params['episode'], params['media_hint'])
    omdb.fetch_omdb_ratings(result.get('results', [])[:3])
    if params.get('season') and params.get('episode'):
        omdb.attach_episode_omdb(result.get('results', []), params['season'], params['episode'])
    omdb.attach_omdb(result.get('results', []))
    try:
        db.cache_set(key, result.get('media_type', ''), result.get('results', []))
        media_type = result.get('media_type', '')
        if media_type == 'movie':
            db.cache_set(params_cache_key({'type': 'movie', 'title': params['title'], 'year': params['year']}), media_type, result.get('results', []))
        elif media_type == 'tv':
            db.cache_set(params_cache_key({'type': 'tv', 'title': params['title'], 'year': params['year']}), media_type, result.get('results', []))
    except Exception:
        pass

    # Store per-file cache if path was provided and exists
    file_cache_store(file_path, result.get('media_type', ''), result.get('results', []))
    result_with_source = dict(result)
    result_with_source['results'] = _filter_for_file(result.get('results', []), file_imdb,
                                                     result.get('media_type') or 'movie', params['title'], params.get('year'))
    result_with_source['cache_source'] = 'tvdb'
    logger.info(f"Search auto: returning {len(result_with_source['results'])} results from TVDB for title={params['title']}")
    return jsonify(result_with_source)


@bp.route('/api/search/movie', methods=['POST'])
@login_required
def api_search_movie():
    d = request.json or {}
    params = {'title': d.get('title', ''), 'year': d.get('year')}
    force_refresh = bool(d.get('force_refresh'))
    # file-specific cache support
    file_path = d.get('path') or d.get('file_path')
    file_imdb = _imdb_from_path(file_path)
    cached_file = cached = None
    if not force_refresh:
        cached_file = file_cache_lookup(file_path)
        if cached_file is not None and not (file_imdb and not _results_have_imdb(cached_file.get('results'), file_imdb)):
            logger.info(f"Search movie: cache hit (file) for {file_path}")
            out = {'results': cached_file.get('results', []), 'cache_source': 'file'}
            omdb.fetch_omdb_ratings(out['results'][:3])
            omdb.attach_omdb(out['results'])
            out['results'] = _filter_for_file(out['results'], file_imdb, 'movie', params['title'], params.get('year'))
            return jsonify(out)

    key = params_cache_key({'type': 'movie', **params})
    if not force_refresh:
        cached = db.cache_get(key)
        if cached is not None and not (file_imdb and not _results_have_imdb(cached.get('results'), file_imdb)):
            logger.info(f"Search movie: cache hit (params) key={key}")
            out = {'results': cached.get('results', []), 'cache_source': 'params'}
            omdb.fetch_omdb_ratings(out['results'][:3])
            omdb.attach_omdb(out['results'])
            out['results'] = _filter_for_file(out['results'], file_imdb, 'movie', params['title'], params.get('year'))
            return jsonify(out)
    if cached_file is not None:
        logger.info(f"Search movie: cache (file) INVALIDÉ pour {file_path} (imdb {file_imdb} absent) → re-recherche")
    elif cached is not None:
        logger.info(f"Search movie: cache (params) INVALIDÉ key={key} (imdb {file_imdb} absent) → re-recherche")
    res = state.api_handler.search_movie(params['title'], params['year'], file_imdb)
    omdb.fetch_omdb_ratings(res[:3])
    omdb.attach_omdb(res)
    try:
        db.cache_set(key, 'movie', res)
    except Exception:
        pass

    file_cache_store(file_path, 'movie', res)
    res = _filter_for_file(res, file_imdb, 'movie', params['title'], params.get('year'))
    logger.info(f"Search movie: returning {len(res)} results from TVDB for title={params['title']}")
    return jsonify({'results': res, 'cache_source': 'tvdb'})


@bp.route('/api/search/tv', methods=['POST'])
@login_required
def api_search_tv():
    d = request.json or {}
    params = {'title': d.get('title', '')}
    season = d.get('season')
    episode = d.get('episode')
    force_refresh = bool(d.get('force_refresh'))
    file_path = d.get('path') or d.get('file_path')
    file_imdb = _imdb_from_path(file_path)
    cached_file = cached = None
    if not force_refresh:
        cached_file = file_cache_lookup(file_path)
        if cached_file is not None and not (file_imdb and not _results_have_imdb(cached_file.get('results'), file_imdb)):
            logger.info(f"Search tv: cache hit (file) for {file_path}")
            out = {'results': cached_file.get('results', []), 'cache_source': 'file'}
            omdb.fetch_omdb_ratings(out['results'][:3])
            omdb.attach_omdb(out['results'])
            omdb.attach_episode_omdb(out['results'], season, episode)
            out['results'] = _filter_for_file(out['results'], file_imdb, 'tv', params['title'], None)
            return jsonify(out)

    key = params_cache_key({'type': 'tv', **params})
    if not force_refresh:
        cached = db.cache_get(key)
        if cached is not None and not (file_imdb and not _results_have_imdb(cached.get('results'), file_imdb)):
            logger.info(f"Search tv: cache hit (params) key={key}")
            out = {'results': cached.get('results', []), 'cache_source': 'params'}
            omdb.fetch_omdb_ratings(out['results'][:3])
            omdb.attach_omdb(out['results'])
            omdb.attach_episode_omdb(out['results'], season, episode)
            out['results'] = _filter_for_file(out['results'], file_imdb, 'tv', params['title'], None)
            return jsonify(out)
    if cached_file is not None:
        logger.info(f"Search tv: cache (file) INVALIDÉ pour {file_path} (imdb {file_imdb} absent) → re-recherche")
    elif cached is not None:
        logger.info(f"Search tv: cache (params) INVALIDÉ key={key} (imdb {file_imdb} absent) → re-recherche")
    res = state.api_handler.search_tv(params['title'], imdb_id=file_imdb)
    omdb.fetch_omdb_ratings(res[:3])
    omdb.attach_omdb(res)
    omdb.attach_episode_omdb(res, season, episode)
    try:
        db.cache_set(key, 'tv', res)
    except Exception:
        pass

    file_cache_store(file_path, 'tv', res)
    res = _filter_for_file(res, file_imdb, 'tv', params['title'], None)
    logger.info(f"Search tv: returning {len(res)} results from TVDB for title={params['title']}")
    return jsonify({'results': res, 'cache_source': 'tvdb'})


@bp.route('/api/movie/<int:movie_id>')
@login_required
def api_movie_details(movie_id):
    source = request.args.get('source', 'tvdb')
    # Try details cache first
    try:
        cached = db.details_get('movie', str(movie_id))
        if cached is not None:
            logger.info(f"Movie details: cache hit for {movie_id}")
            out = dict(cached)
            out['cache_source'] = 'details_cache'
            return jsonify(out)
    except Exception:
        pass
    details = state.api_handler.get_movie_details(str(movie_id), source)
    try:
        db.details_set('movie', str(movie_id), details)
    except Exception:
        pass
    details['cache_source'] = 'tvdb'
    return jsonify(details)


@bp.route('/api/tv/<int:tv_id>')
@login_required
def api_tv_details(tv_id):
    season = request.args.get('season', 1, type=int)
    episode = request.args.get('episode', 1, type=int)
    source = request.args.get('source', 'tvdb')
    cache_key = f"{tv_id}:s{season}e{episode}"
    try:
        cached = db.details_get('tv', cache_key)
        if cached is not None:
            logger.info(f"TV details: cache hit for {tv_id} S{season}E{episode}")
            out = dict(cached)
            out['cache_source'] = 'details_cache'
            out.update({'season': season, 'episode': episode})
            return jsonify(out)
    except Exception:
        pass
    details = state.api_handler.get_tv_details(str(tv_id), season, episode, source)
    try:
        db.details_set('tv', cache_key, details)
    except Exception:
        pass
    if details.get('title') and season and episode:
        try:
            omdb.fetch_omdb_episode(details.get('imdbid') or details.get('imdb') or '', details.get('title'), details.get('year'), season, episode)
            ekey = f"{(details.get('imdbid') or details.get('imdb') or '').strip().lower()}:y{details.get('year')}:s{int(season):02d}e{int(episode):02d}"
            ed = db.omdb_episode_get(ekey)
            if ed:
                details['episode_omdb'] = ed
        except Exception:
            pass
    details['cache_source'] = 'tvdb'
    return jsonify(details)


@bp.route('/api/search/cache-file', methods=['POST'])
@login_required
def api_search_cache_file():
    """Permet au client de forcer le stockage en cache d'une recherche pour un fichier donné.
    Payload: { path: <file_path>, media_type: 'movie'|'tv', results: [...] }
    """
    d = request.json or {}
    file_path = d.get('path')
    results = d.get('results')
    media_type = d.get('media_type') or ''
    if not file_path or results is None:
        return jsonify({'success': False, 'message': 'Missing path or results'}), 400
    try:
        from ..cache import file_fingerprint
        if not file_fingerprint(file_path):
            return jsonify({'success': False, 'message': 'File not found'}), 400
        file_cache_store(file_path, media_type, results)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error caching file search: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
