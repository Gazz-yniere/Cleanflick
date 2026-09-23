"""Routes de bibliothèque : arborescence, méta (cache/OMDb), épisodes manquants,
enrichissement, retour vers l'input, renommage/suppression de dossiers."""
import os
import re
import json
import time
import uuid
import shutil
import logging
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, redirect

from .. import db, state
from ..auth import login_required
from ..history import append_history, append_history_batch
from ..utils import _move_path, _run_file_op, _migrate_file_cache, _refresh_scan_snapshot
from .. import library
from .. import naming
from .. import duration
from .. import watcher
from ..api import omdb

logger = logging.getLogger(__name__)
bp = Blueprint('library', __name__)

VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'}


@bp.route('/api/library/tvdb-link/<int:tvdb_id>')
@login_required
def api_library_tvdb_link(tvdb_id):
    """Résout le slug web canonique TVDB au moment du clic."""
    try:
        cached = db.series_meta_get(str(tvdb_id))
        cached_data = (cached or {}).get('data') or {}
        cached_slug = cached_data.get('tvdb_slug') or cached_data.get('slug')
        if cached_slug:
            return redirect(f'https://www.thetvdb.com/series/{cached_slug}')
        details = state.api_handler.tvdb.get_series_details(tvdb_id) if state.api_handler.tvdb else {}
        slug = (details or {}).get('tvdb_slug') or (details or {}).get('slug')
        if slug:
            imdb = (details.get('imdbid') or details.get('imdb') or '').strip()
            db.series_meta_set(str(tvdb_id), imdb, details)
            return redirect(f'https://www.thetvdb.com/series/{slug}')
    except Exception as e:
        logger.warning(f'TVDB web link resolution failed for {tvdb_id}: {e}')
    return jsonify({'error': 'Slug TVDB indisponible'}), 503


@bp.route('/api/library/tvdb-orders/<int:tvdb_id>', methods=['GET', 'POST'])
@login_required
def api_library_tvdb_orders(tvdb_id):
    """Retourne les ordres TVDB disponibles après une sélection manuelle."""
    try:
        details = state.api_handler.tvdb.get_series_details(tvdb_id) if state.api_handler.tvdb else {}
        orders = []
        for item in (details or {}).get('seasonTypes') or []:
            if not isinstance(item, dict):
                continue
            order = item.get('type') or 'official'
            label = item.get('alternateName') or item.get('name') or order
            if not any(x.get('value') == order for x in orders):
                orders.append({'value': order, 'label': label})
        return jsonify({'orders': orders, 'details': details or {}})
    except Exception as e:
        logger.warning(f'TVDB order resolution failed for {tvdb_id}: {e}')
        return jsonify({'orders': []})

# Marqueur d'épisode (y compris multi-épisodes : S01E01-E02, S01E01E02, 1x02-03).
_EP_MARKER = r'[Ss]\d{2}[Ee]\d{2}(?:[-–—~]\s*[Ee]\d{2}|[Ee]\d{2})?|\d+x\d{2}(?:[-–—~]\d{2})?'

# Mémo courte des listes d'épisodes (cache BDD) : un F5 re-ouvre toutes les
# séries en parallèle, sans cela chaque nœud relance sa lecture BDD.
_SERIES_EP_MEM = {}
_SERIES_EP_MEM_TTL = 300


def _series_episodes_memo(series_id):
    """Liste d'épisodes depuis le cache BDD uniquement (0 requête TVDB),
    mémoïsée 5 min en mémoire."""
    now = time.time()
    m = _SERIES_EP_MEM.get(series_id)
    if m and now - m[0] < _SERIES_EP_MEM_TTL:
        return m[1]
    eps = library.series_episodes_cached(series_id) or []
    _SERIES_EP_MEM[series_id] = (now, eps)
    if len(_SERIES_EP_MEM) > 1000:
        _SERIES_EP_MEM.clear()
        _SERIES_EP_MEM[series_id] = (now, eps)
    return eps


def _series_ep_counts(series_id, series_path, show_s00):
    """(présents, total, à venir) pour un dossier série, sans requête TVDB."""
    eps = _series_episodes_memo(series_id)
    known = {(e.get('s'), e.get('e')) for e in eps
             if e.get('s') is not None and e.get('e') is not None}
    ep_total = sum(1 for e in eps if ((e.get('s') or 0) > 0 or show_s00) and not library.episode_is_upcoming(e))
    ep_upcoming = sum(1 for e in eps if (e.get('s') or 0) > 0 and library.episode_is_upcoming(e))
    present = library.present_episodes(series_path)
    ep_present = sum(1 for key in present
                     if (key in known) and (key[0] > 0 or show_s00))
    return ep_present, ep_total, ep_upcoming


def _ep_title_from_file(filename):
    """Titre d'épisode extrait d'un nom de fichier local, au format
    « … - S01E01 - Titre » (token {t:en}) : plus lisible que le titre TVDB
    qui est en langue originale (ex. japonais)."""
    stem = os.path.splitext(os.path.basename(str(filename)))[0]
    m = re.search(r'(?:S\d{2,}E\d{2,}(?:-E\d{2,})*|\d+x\d{2,}(?:-\d{2,})*)\s*[-–]\s*(.+)$', stem, re.I)
    return m.group(1).strip() if m else ''


@bp.route('/api/library/missing')
@login_required
def api_library_missing():
    path = request.args.get('path', '').strip()
    if not path or not os.path.isdir(path):
        return jsonify({'error': 'Dossier introuvable'}), 404
    tv_root = os.path.abspath(state.config.get('_tv_output_path') or state.config.get('tv_output_path') or '')
    if not os.path.abspath(path).startswith(tv_root):
        return jsonify({'error': 'Non autorisé hors du dossier TV'}), 403
    name = os.path.basename(path.rstrip('/\\'))
    m = re.search(r'\[tvdbid-([0-9]+)\]', name, re.I)
    if not m:
        return jsonify({'series_id': '', 'count': 0, 'missing': []})
    series_id = m.group(1)
    # Non bloquant : la liste TVDB est lue depuis le cache (mémoire puis BDD).
    # Si le cache est vide (premier affichage, parsing pas encore passé), la
    # réponse part immédiatement avec `available: false` et le cache TVDB est
    # rempli en arrière-plan : le frontend re-polle quelques secondes plus tard.
    episodes = _series_episodes_memo(series_id)
    present = library.present_episodes(path)
    # Épisodes spéciaux (saison 0 / S00) ignorés par défaut ; affichés si l'option
    # « show_s00_specials » est activée dans la configuration.
    show_s00 = bool(state.config.get('show_s00_specials'))
    # Déduplication par (saison, episode) : le cache TVDB peut contenir des doublons.
    missing, upcoming = [], []
    seen = set()
    for e in episodes:
        if e['s'] <= 0 and not show_s00:
            continue
        key = (e['s'], e['e'])
        if key in present or key in seen:
            continue
        seen.add(key)
        (upcoming if library.episode_is_upcoming(e) else missing).append(e)
    missing.sort(key=lambda x: (x['s'], x['e']))
    upcoming.sort(key=lambda x: (x['s'], x['e']))
    return jsonify({
        'series_id': series_id,
        'available': bool(episodes),
        'count': len(missing),
        'missing_count': len(missing),
        'upcoming_count': len(upcoming),
        'missing': [{'s': e['s'], 'e': e['e'], 'label': f"S{int(e['s']):02d}E{int(e['e']):02d}",
                 'title': e.get('title', ''), 'airdate': e.get('airdate', '')} for e in missing],
        'upcoming': [{'s': e['s'], 'e': e['e'], 'label': f"S{int(e['s']):02d}E{int(e['e']):02d}", 'title': e['title'], 'airdate': e.get('airdate', '')} for e in upcoming],
    })


def _meta_ctx(fast_scan, lib_type, movie_fmt, tv_fmt):
    """Contexte de méta d'un rendu bibliothèque : caches BDD uniquement
    (search cache, OMDb, series_meta). Réutilisé par l'arborescence
    (api_library) et la liste plate (api_library/flat)."""
    movie_fmt = state.config.get('movie_format', '{n} ({y})')
    tv_fmt    = state.config.get('tv_format',    '{n} - {s00e00} - {t}')

    def is_valid_name(filename, ftype, is_dir=False):
        stem = Path(filename).stem if not is_dir else filename
        fmt = movie_fmt if ftype == 'movie' else tv_fmt
        tokens = set(re.findall(r'\{([a-zA-Z_][\w:]*?)\}', fmt))
        # Normaliser les alias
        if 'imdbid' in tokens:
            tokens.add('imdb')
        if 'imdb' in tokens:
            tokens.add('imdbid')
        if 'tmdbid' in tokens:
            tokens.add('tmdb')
        if 'tmdb' in tokens:
            tokens.add('tmdbid')
        if 'year' in tokens:
            tokens.add('y')
        if 'y' in tokens:
            tokens.add('year')

        checks = {
            # token          : (regex_dans_stem,          applicable_is_dir)
            'y':              (r'\(\d{4}\)',               True),
            'imdb':           (r'imdb(?:id)?-tt\d+',       False),
            'tvdbid':         (r'tvdbid-\d+',              True),
            'tmdb':           (r'tmdb(?:id)?-\d+',         False),
            's00e00':         (r'[Ss]\d{2}[Ee]\d{2}',     False),
            'sxe':            (r'\d+x\d{2}',               False),
        }
        for token, (pattern, dir_ok) in checks.items():
            if token not in tokens:
                continue
            if is_dir and not dir_ok:
                continue
            if not re.search(pattern, stem):
                return False
        return True

    # Charge une seule fois le cache de recherche, puis matche par nom de fichier.
    # (Le cache stocke le chemin d'origine ; après un déplacement vers la librairie
    # le chemin change, mais le nom de fichier reste identique.)
    basename_map = {}
    conn = None
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("SELECT file_path, media_type, results_json, loaded_at FROM file_search_cache")
        for r in cur.fetchall():
            try:
                base = os.path.basename(r['file_path'])
                if base not in basename_map or (r['loaded_at'] or 0) > (basename_map[base]['loaded_at'] or 0):
                    basename_map[base] = {
                        'media_type': r['media_type'],
                        'results': json.loads(r['results_json'] or '[]'),
                        'loaded_at': r['loaded_at'],
                    }
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    # Notes OMDb (par imdb_id) chargées une seule fois, en lecture seule.
    try:
        omdb_map = db.omdb_all()
    except Exception:
        omdb_map = {}
    try:
        series_meta_all = db.series_meta_all()
    except Exception:
        series_meta_all = {}
    try:
        episode_omdb_map = db.omdb_episode_all()
    except Exception:
        episode_omdb_map = {}
    try:
        series_imdb_map = omdb._series_imdb_map()
    except Exception:
        series_imdb_map = {}

    # Mémo par requête : un F5 re-ouvre toutes les séries en parallèle ; sans
    # cela chaque nœud relance sa résolution TVDB→IMDb (1 requête TVDB chacun)
    # et son walk réseau de taille de dossier.
    _req_imdb_mem = {}
    # Séries dont l'OMDb global est déjà en cours d'enrichissement (thread de
    # fond) pour cette requête : 1 thread max par série par rendu.
    _req_series_enrich_queued = set()

    def _req_resolve_imdb(tvdb_id, title=None, year=None):
        tvdb_id = str(tvdb_id or '').strip().lower()
        if not tvdb_id:
            return ''
        if tvdb_id not in _req_imdb_mem:
            _req_imdb_mem[tvdb_id] = omdb.series_imdb_cached(tvdb_id) or omdb.resolve_series_imdb(
                tvdb_id, title=title, year=year)
        return _req_imdb_mem[tvdb_id]

    def _req_enqueue_series_omdb(tvdb_id, title=None, year=None):
        """Enrichit l'OMDb global d'une série en thread de fond (1 TVDB + 1 OMDb
        la première fois) pour que note/date/genres/cert du dossier apparaissent
        au rendu suivant, sans bloquer l'affichage."""
        tvdb_id = str(tvdb_id or '').strip().lower()
        if not tvdb_id or tvdb_id in _req_series_enrich_queued:
            return
        if omdb.series_imdb_cached(tvdb_id) and db.omdb_get(omdb.series_imdb_cached(tvdb_id)) is not None:
            return
        _req_series_enrich_queued.add(tvdb_id)
        threading.Thread(target=omdb.ensure_series_omdb, args=(tvdb_id, title, year), daemon=True).start()

    _req_size_mem = {}

    def _req_folder_size(p):
        if p not in _req_size_mem:
            _req_size_mem[p] = library.folder_size_direct(p) if fast_scan else library.folder_size(p, recursive=False)
        return _req_size_mem[p]

    def _apply_omdb(meta, imdb_id, title=None, year=None, is_series=False, live=True):
        if not imdb_id:
            return meta
        imdb = imdb_id.strip().lower()
        od = omdb_map.get(imdb)
        if not od and live:
            od = omdb.fetch_omdb_single(imdb, title, year, is_series)
            if od:
                omdb_map[imdb] = od
        if not od:
            return meta
        if od.get('imdbRating'):
            meta['rating'] = od['imdbRating']
        if od.get('Released'):
            meta['date'] = omdb.omdb_date(od['Released'])
        if od.get('Genre'):
            meta['genres'] = od['Genre']
        if od.get('Rated') and od.get('Rated') != 'N/A':
            meta['certification'] = od['Rated']
        if od.get('Runtime') and od.get('Runtime') != 'N/A':
            meta['runtime'] = od['Runtime']
        poster = od.get('Poster') or ''
        if not meta.get('poster') and poster and poster != 'N/A':
            meta['poster'] = poster
        return meta

    def _best_result(cached, name):
        """Le meilleur résultat d'un cache de recherche pour un nom de fichier :
        si le nom porte un ID vérifié (imdb/tmdb/tvdb) et qu'un résultat le
        porte, on le préfère au 1er. S'il n'en porte aucun → None : les infos
        du 1er résultat (recherche TVDB par titre, autre titre homonyme) ne
        sont pas fiables pour ce fichier, la chaîne de meta retombe sur le nom."""
        results = (cached or {}).get('results') or []
        if not results:
            return None
        ids = ids_from_name(name)
        if not ids:
            return results[0]
        for r in results:
            if any(str(r.get(k) or '').strip().lower() == v for kind, v in ids
                   for k in (('imdb_id', 'tmdb_id', 'id_tvdb', 'id') if kind == 'tvdb' else (kind,))):
                return r
        return None

    def cache_meta(cached, name=None):
        if not cached:
            return None
        top = _best_result(cached, name) if name else ((cached.get('results') or [None])[0])
        if not top:
            return None
        meta = {
            'title': top.get('title', ''),
            'year': top.get('year'),
            'imdb': top.get('imdb_id', ''),
            'tmdb': top.get('tmdb_id', ''),
            'tvdb': str(top.get('id_tvdb') or top.get('id') or ''),
            'tvdb_slug': top.get('tvdb_slug', ''),
            'genres': top.get('genres', ''),
            'poster': top.get('poster', ''),
            'rating': '', 'date': top.get('date', ''), 'certification': '', 'runtime': '',
            'type': top.get('type') or cached.get('media_type', ''),
        }
        return _apply_omdb(meta, meta['imdb'], live=False)

    # Index par préfixe (24 premiers caractères) pour folder_meta : évite le scan
    # de tout le cache par dossier (lent avec des milliers de fichiers en cache).
    _folder_prefix_idx = {}
    for _base in basename_map:
        _folder_prefix_idx.setdefault(_base[:24], []).append(_base)

    def ids_from_name(name):
        ids = []
        m = re.search(r'\[(?:imdbid?|imdb)-([a-zA-Z0-9]+)\]', name, re.I)
        if m:
            ids.append(('imdb', m.group(1).lower()))
        m = re.search(r'\[tmdb(?:id)?-([0-9]+)\]', name, re.I)
        if m:
            ids.append(('tmdb', m.group(1).lower()))
        m = re.search(r'\[tvdbid-([0-9]+)\]', name, re.I)
        if m:
            ids.append(('tvdb', m.group(1).lower()))
        return ids

    def series_tvdb_id_from_path(path):
        """Remonte l'arborescence (épisode → saison → série) jusqu'au dossier
        portant un [tvdbid-XXX] et renvoie cet ID (ou None)."""
        d = os.path.dirname(path)
        for _ in range(4):
            m = re.search(r'\[tvdbid-([0-9]+)\]', os.path.basename(d), re.I)
            if m:
                return m.group(1).lower()
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return None

    # Index secondaire : (préfixe avant le marqueur d'épisode, marqueur) -> meilleur résultat
    # Permet de retrouver un épisode même si le titre de l'épisode a changé depuis le cache.
    episode_map = {}
    for base, cached in basename_map.items():
        m = re.search(_EP_MARKER, base)
        if m:
            key = (base[:m.start()].strip().lower(), m.group(0).lower())
            if key not in episode_map or (cached['loaded_at'] or 0) > (episode_map[key]['loaded_at'] or 0):
                episode_map[key] = cached

    # Index par identifiants externes (imdb/tmdb/tvdb) du meilleur résultat.
    # Retrouve l'info même si le fichier a été renommé, tant que l'id figure dans le nom.
    id_index = {}
    for cached in basename_map.values():
        top = (cached.get('results') or [{}])[0]
        for kind, val in (('imdb', top.get('imdb_id')), ('tmdb', top.get('tmdb_id')), ('tvdb', top.get('id_tvdb') or top.get('id'))):
            if val:
                key = (kind, str(val).strip().lower())
                if key not in id_index or (cached['loaded_at'] or 0) > (id_index[key]['loaded_at'] or 0):
                    id_index[key] = cached

    def id_meta(name):
        for kind, val in ids_from_name(name):
            cached = id_index.get((kind, val))
            if cached:
                return cache_meta(cached)
        return None

    def series_poster(tvdb_id):
        """Poster + titre d'une série depuis les caches (0 requête API).
        Ordre : cache de recherche (id_index) puis cache OMDb (via l'ID IMDb
        résolu en BDD). Renvoie (poster, titre) ou (None, None)."""
        tvdb_id = str(tvdb_id or '').strip().lower()
        if not tvdb_id:
            return None, None
        series_cache = db.series_meta_get(tvdb_id)
        series_data = (series_cache or {}).get('data') or {}
        poster = series_data.get('poster') or ''
        if poster and poster != 'N/A':
            return poster, series_data.get('title', '')
        cached = id_index.get(('tvdb', tvdb_id))
        if cached:
            smeta = cache_meta(cached)
            if smeta and smeta.get('poster') and smeta['poster'] != 'N/A':
                return smeta['poster'], smeta.get('title', '')
        imdb = series_imdb_map.get(tvdb_id)
        if imdb:
            od = omdb_map.get(imdb.lower())
            if od:
                poster = od.get('Poster') or ''
                if poster and poster != 'N/A':
                    return poster, ''
        return None, None

    def series_poster_meta(tvdb_id):
        """Meta minimale (poster + titre) d'une série depuis les caches, pour
        l'affichage des dossiers en scan rapide (0 requête API)."""
        tvdb_id = str(tvdb_id or '').strip().lower()
        if not tvdb_id:
            return None
        data = (series_meta_all.get(tvdb_id) or {}).get('data') or {}
        poster = data.get('poster') or ''
        if poster and poster != 'N/A':
            return {'poster': poster, 'title': data.get('title', ''), 'tvdb': tvdb_id}
        cached = id_index.get(('tvdb', tvdb_id))
        if cached:
            smeta = cache_meta(cached)
            if smeta and smeta.get('poster') and smeta['poster'] != 'N/A':
                return {'poster': smeta['poster'], 'title': smeta.get('title', ''), 'tvdb': tvdb_id}
        imdb = series_imdb_map.get(tvdb_id)
        if imdb:
            od = omdb_map.get(imdb.lower())
            if od:
                poster = od.get('Poster') or ''
                if poster and poster != 'N/A':
                    return {'poster': poster, 'title': '', 'tvdb': tvdb_id}
        return None

    def series_slug(tvdb_id):
        cached = db.series_meta_get(str(tvdb_id).strip().lower())
        data = (cached or {}).get('data') or {}
        return data.get('tvdb_slug') or data.get('slug') or ''

    def series_meta_cached(tvdb_id):
        cached = db.series_meta_get(str(tvdb_id).strip().lower())
        data = (cached or {}).get('data') or {}
        return data if data.get('id') or data.get('tvdbid') else None

    # Affiche l'info extraite du nom seul (sans API ni cache) :
    # "Titre (Année) [imdbid-XXX] - (Titre FR).ext" -> titre + année + ids.
    def name_meta(name):
        m = re.match(r'^(.*?)\s*\((\d{4})\)', name)
        if not m:
            return None
        meta = {
            'title': m.group(1).strip(),
            'year': m.group(2),
            'imdb': '', 'tmdb': '', 'tvdb': '',
            'genres': '', 'poster': '', 'rating': '', 'date': '', 'type': '',
            'certification': '', 'runtime': '',
        }
        for kind, val in ids_from_name(name):
            if kind == 'imdb':
                meta['imdb'] = val
            elif kind == 'tmdb':
                meta['tmdb'] = val
            elif kind == 'tvdb':
                meta['tvdb'] = val
        return _apply_omdb(meta, meta['imdb'], live=False)

    def folder_meta(name):
        best = None
        cands = _folder_prefix_idx.get(name[:24])
        for base in cands if cands else basename_map:
            if base.startswith(name):
                cached = basename_map[base]
                if best is None or (cached['loaded_at'] or 0) > (best['loaded_at'] or 0):
                    best = cached
        meta = cache_meta(best, name)
        meta = meta or id_meta(name) or name_meta(name)
        if lib_type == 'tv':
            m2 = re.search(r'\[tvdbid-([0-9]+)\]', name, re.I)
            if m2:
                details = series_meta_cached(m2.group(1))
                if details:
                    meta = meta or {'tvdb': m2.group(1)}
                    for key in ('title', 'year', 'genres', 'certification', 'poster', 'date', 'tvdb_slug'):
                        if details.get(key):
                            meta[key] = details[key]
            if m2 and not meta:
                # Dossier série sans meta (nom non calqué sur le cache) : on réutilise
                # le poster de la série depuis les caches (0 requête API).
                poster, title = series_poster(m2.group(1))
                if poster:
                    meta = {'poster': poster, 'title': title, 'tvdb': m2.group(1)}
            if m2:
                slug = series_slug(m2.group(1))
                if slug:
                    meta = meta or {'tvdb': m2.group(1)}
                    meta['tvdb_slug'] = slug
            if meta:
                # Pour un dossier de série : pas de durée (elle n'apparaît que sur les fichiers/épisodes).
                meta.pop('runtime', None)
                meta.pop('episode_runtime', None)
                # Le nom de dossier ne porte que l'ID TVDB : on complète l'ID IMDb
                # (cache BDD, sinon 1 requête TVDB) pour déclencher l'enrichissement
                # OMDb (note, date, genres, certification, poster) côté frontend.
                if not meta.get('imdb'):
                    tvdb_id = (meta.get('tvdb') or '')
                    if not tvdb_id and m2:
                        tvdb_id = m2.group(1)
                    if tvdb_id:
                        tvdb_id = str(tvdb_id).strip().lower()
                        imdb = series_imdb_map.get(tvdb_id) or (None if fast_scan else _req_resolve_imdb(
                            tvdb_id, title=meta.get('title') or name.split(' (')[0].strip(),
                            year=meta.get('year')))
                        if imdb:
                            meta['imdb'] = imdb
                            _apply_omdb(meta, imdb, live=False)
                # L'OMDb global de la série (note, date, genres, cert) manque en
                # base : on le met en cache en thread de fond. Les infos du
                # dossier s'affichent au rendu suivant, sans bloquer la réponse.
                if not fast_scan and not meta.get('rating') and m2:
                    _req_enqueue_series_omdb(
                        meta.get('tvdb') or m2.group(1),
                        title=meta.get('title') or name.split(' (')[0].strip(),
                        year=meta.get('year'))
        return meta

    def file_meta(name, duration_val=None, entry_path=None):
        m = re.search(f'({_EP_MARKER})', name)
        ep_num = None
        ep_name = ''
        if m:
            ep_num = m.group(1).upper()
            tail = name[m.end():].strip(' -')
            if tail:
                ep_name = os.path.splitext(tail.split(' - ')[0].strip())[0] or ''
        meta = cache_meta(basename_map.get(name), name)
        if not meta:
            if m:
                key = (name[:m.start()].strip().lower(), m.group(1).lower())
                meta = cache_meta(episode_map.get(key), name)
            if not meta:
                meta = id_meta(name) or name_meta(name)
        if m:
            if not meta:
                meta = {}
            if not meta.get('poster'):
                # Épisodes sans poster (jamais recherchés / cache vidéé) : on remonte
                # au dossier série [tvdbid-XXX] et on réutilise le poster de la série
                # depuis les caches (0 requête API).
                tvdb_id = series_tvdb_id_from_path(entry_path)
                if tvdb_id:
                    poster, title = series_poster(tvdb_id)
                    if poster:
                        meta['poster'] = poster
                        if not meta.get('title'):
                            meta['title'] = title
            else:
                # Les épisodes héritent toujours du poster actuel de leur série.
                tvdb_id = series_tvdb_id_from_path(entry_path)
                if tvdb_id:
                    poster, _title = series_poster(tvdb_id)
                    if poster:
                        meta['poster'] = poster
        if meta and ep_num:
            meta['episode'] = ep_num
            meta['episode_name'] = ep_name
            m2 = re.match(r'[Ss](\d{2})[Ee](\d{2})', ep_num)
            if duration_val:
                meta['episode_runtime'] = f"{duration_val} min"
            if m2 and meta.get('imdb'):
                s_num, e_num = int(m2.group(1)), int(m2.group(2))
                ekeys = [f"{meta['imdb']}:y{meta.get('year')}:s{s_num:02d}e{e_num:02d}",
                         f"{meta['imdb']}:s{s_num:02d}e{e_num:02d}"]
                ed = {}
                for ek in ekeys:
                    ed = episode_omdb_map.get(ek) or {}
                    if ed:
                        break
                if ed.get('imdbRating'):
                    meta['episode_rating'] = ed['imdbRating']
                if not duration_val and ed.get('Runtime') and ed.get('Runtime') != 'N/A':
                    meta['episode_runtime'] = ed['Runtime']
                if ed.get('Released') and ed.get('Released') != 'N/A':
                    meta['episode_date'] = omdb.omdb_date(ed['Released'])
        elif meta and not ep_num:
            if duration_val:
                meta['runtime'] = f"{duration_val} min"
            elif not meta.get('runtime') and meta.get('imdb'):
                od = omdb_map.get(meta['imdb'].lower())
                if od and od.get('Runtime') and od.get('Runtime') != 'N/A':
                    meta['runtime'] = od['Runtime']
        return meta


    return {
        'is_valid_name': is_valid_name,
        'name_meta': name_meta,
        'id_meta': id_meta,
        'folder_meta': folder_meta,
        'file_meta': file_meta,
        'series_poster_meta': series_poster_meta,
        'enqueue_series_omdb': _req_enqueue_series_omdb,
        'series_imdb_map': series_imdb_map,
    }


@bp.route('/api/library')
@login_required
def api_library():
    path = request.args.get('path', '').strip()
    library_type = request.args.get('type', '')  # 'movie' or 'tv'
    # Le paramètre est explicite : le frontend peut ainsi demander un premier
    # rendu léger puis un rafraîchissement complet, même si l'option de scan
    # rapide reste activée pour les durées de fichiers.
    fast_scan = request.args.get('fast') == '1'

    # Root call — return both movie and tv roots
    if not path:
        roots = []
        for lib_type, cfg_key in [('movie', '_movie_output_path'), ('tv', '_tv_output_path')]:
            lib_path = state.config.get(cfg_key) or state.config.get(cfg_key.lstrip('_'))
            if lib_path and os.path.isdir(lib_path):
                # basename() renvoie '' pour un chemin en « \\serveur\partage\ » :
                # on revient en arrière jusqu'au premier segment non vide pour
                # afficher juste le nom du dossier racine.
                parts = lib_path.replace('/', '\\').rstrip('\\').split('\\')
                root_name = os.path.basename(os.path.normpath(lib_path)) or parts[-1] or parts[-2] or lib_path
                roots.append({'name': root_name, 'path': lib_path, 'type': lib_type, 'is_dir': True,
                              'size': library.folder_size_direct(lib_path) if fast_scan else library.folder_size(lib_path)})
        return jsonify({'path': '', 'type': 'root', 'entries': roots})

    if not os.path.isdir(path):
        return jsonify({'error': 'Dossier introuvable'}), 404

    movie_root = os.path.abspath(state.config.get('_movie_output_path') or state.config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(state.config.get('_tv_output_path')    or state.config.get('tv_output_path')    or '')
    abs_path   = os.path.abspath(path)
    if abs_path.startswith(movie_root):
        lib_type = 'movie'
    elif abs_path.startswith(tv_root):
        lib_type = 'tv'
    else:
        lib_type = library_type or 'movie'

    movie_fmt = state.config.get('movie_format', '{n} ({y})')
    tv_fmt    = state.config.get('tv_format',    '{n} - {s00e00} - {t}')
    ctx = _meta_ctx(fast_scan, lib_type, movie_fmt, tv_fmt)
    is_valid_name = ctx['is_valid_name']
    name_meta = ctx['name_meta']
    id_meta = ctx['id_meta']
    folder_meta = ctx['folder_meta']
    file_meta = ctx['file_meta']
    series_poster_meta = ctx['series_poster_meta']
    _req_enqueue_series_omdb = ctx['enqueue_series_omdb']

    _req_size_mem = {}

    def _req_folder_size(p):
        if p not in _req_size_mem:
            _req_size_mem[p] = library.folder_size_direct(p) if fast_scan else library.folder_size(p, recursive=False)
        return _req_size_mem[p]

    all_entries = []
    try:
        all_entries = list(os.scandir(path))
    except PermissionError:
        return jsonify({'error': 'Permission refusée'}), 403
    except OSError:
        return jsonify({'error': 'Lecture impossible', 'entries': [], 'total': 0, 'offset': 0, 'limit': 0}), 200

    def _sort_key(e):
        try:
            return (not e.is_dir(follow_symlinks=False), e.name.lower())
        except OSError:
            return (True, e.name.lower())

    all_entries.sort(key=_sort_key)

    total = len(all_entries)
    try:
        offset = max(0, int(request.args.get('offset', 0)))
        limit = int(request.args.get('limit', 0))
    except Exception:
        offset, limit = 0, 0
    if limit and limit > 0:
        page = all_entries[offset:offset + limit]
    else:
        page = all_entries[offset:]

    entries = []
    for entry in page:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            try:
                child_count = sum(1 for _ in os.scandir(entry.path))
            except Exception:
                child_count = 0
            valid_dir = is_valid_name(entry.name, lib_type, is_dir=True)
            ep_present = ep_total = ep_upcoming = None
            if lib_type == 'tv' and not fast_scan:
                mep = re.search(r'\[tvdbid-([0-9]+)\]', entry.name, re.I)
                if mep:
                    # Compteur d'épisodes « présents / total TVDB » (S00 inclus
                    # uniquement si l'option show_s00_specials est activée).
                    # Les épisodes « à venir » (non diffusés) sont exclus du total.
                    show_s00 = bool(state.config.get('show_s00_specials'))
                    ep_present, ep_total, ep_upcoming = _series_ep_counts(mep.group(1), entry.path, show_s00)
            if fast_scan:
                meta = name_meta(entry.name) or id_meta(entry.name)
                # Dossier série en scan rapide : folder_meta() est trop coûteuse,
                # on se contente du poster/titre de la série depuis les caches.
                if lib_type == 'tv':
                    mser = re.search(r'\[tvdbid-([0-9]+)\]', entry.name, re.I)
                    if mser:
                        smeta = series_poster_meta(mser.group(1))
                        if smeta:
                            meta = {**(meta or {}), **smeta}
                        # Même enrichissement OMDb global en fond que le scan lent :
                        # la note/infos du dossier apparaissent au F5 suivant.
                        _req_enqueue_series_omdb(mser.group(1), title=(meta or {}).get('title') or entry.name.split(' (')[0].strip())
            else:
                meta = folder_meta(entry.name)
            entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': True, 'child_count': child_count, 'size': _req_folder_size(entry.path), 'valid': valid_dir,
                            'meta': meta,
                            'ep_present': ep_present, 'ep_total': ep_total, 'ep_upcoming': ep_upcoming})
        elif entry.is_file() and Path(entry.name).suffix.lower() in VIDEO_EXT:
            valid = is_valid_name(entry.name, lib_type)
            size = 0
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
            dur = duration.get_duration(entry.path, non_blocking=fast_scan)
            entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': False, 'size': size,
                             'valid': valid, 'meta': file_meta(entry.name, dur, entry.path),
                            'dur_pending': dur is None and state.config.get('lib_fast_scan')
                            and duration.is_pending(entry.path)})
    # Mode scan rapide : get_duration() a déjà mis les durées manquantes dans
    # la file FIFO (worker unique, calcul séquentiel dans l'ordre d'affichage).

    return jsonify({'path': path, 'type': lib_type, 'entries': entries, 'total': total, 'offset': offset, 'limit': limit})


@bp.route('/api/library/flat')
@login_required
def api_library_flat():
    """Liste plate de la bibliothèque (vue grille) : 1 item par film
    (fichier) et 1 item par série (dossier [tvdbid-…]).
    Caches BDD uniquement (search cache, OMDb, series_meta) : 0 requête API
    externe, la note/poster manquants arrivent ensuite via l'enrichissement."""
    movie_root = os.path.abspath(state.config.get('_movie_output_path') or state.config.get('movie_output_path') or '')
    tv_root = os.path.abspath(state.config.get('_tv_output_path') or state.config.get('tv_output_path') or '')
    movie_fmt = state.config.get('movie_format', '{n} ({y})')
    tv_fmt = state.config.get('tv_format', '{n} - {s00e00} - {t}')
    show_s00 = bool(state.config.get('show_s00_specials'))

    items = []
    seen = set()
    # lib_type='tv' : requis par folder_meta (dossiers série) ; file_meta et
    # is_valid_name sont indépendants du type.
    ctx = _meta_ctx(fast_scan=False, lib_type='tv', movie_fmt=movie_fmt, tv_fmt=tv_fmt)

    def _add(path, typ, name, meta, valid, size=0, ep_present=None, ep_total=None, ep_upcoming=None):
        key = (path, typ)
        if key in seen:
            return
        seen.add(key)
        items.append({'type': typ, 'name': name, 'path': path, 'valid': valid, 'size': size,
                      'meta': meta, 'ep_present': ep_present, 'ep_total': ep_total, 'ep_upcoming': ep_upcoming})

    def _walk_files(root, depth=0, max_depth=3):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except (PermissionError, OSError):
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    _walk_files(e.path, depth + 1, max_depth)
                elif e.is_file(follow_symlinks=False) and Path(e.name).suffix.lower() in VIDEO_EXT:
                    size = 0
                    try:
                        size = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
                    meta = ctx['file_meta'](e.name, None, e.path)
                    _add(e.path, 'movie', e.name, meta, ctx['is_valid_name'](e.name, 'movie'), size)
            except OSError:
                continue

    def _walk_series(root, depth=0, max_depth=3):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except (PermissionError, OSError):
            return
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
                m = re.search(r'\[tvdbid-([0-9]+)\]', e.name, re.I)
                if m:
                    meta = ctx['folder_meta'](e.name)
                    if not meta:
                        meta = {'tvdb': m.group(1)}
                    ep_present = ep_total = ep_upcoming = None
                    try:
                        ep_present, ep_total, ep_upcoming = _series_ep_counts(m.group(1), e.path, show_s00)
                    except Exception:
                        pass
                    size = 0
                    try:
                        size = library.folder_size_direct(e.path)
                    except Exception:
                        pass
                    _add(e.path, 'series', e.name, meta, True, size, ep_present, ep_total, ep_upcoming)
                else:
                    _walk_series(e.path, depth + 1, max_depth)
            except OSError:
                continue

    if movie_root and os.path.isdir(movie_root):
        _walk_files(movie_root)
    if tv_root and os.path.isdir(tv_root):
        _walk_series(tv_root)
    items.sort(key=lambda it: ((it['type'] != 'movie'), str(it['name']).lower()))
    return jsonify({'items': items, 'total': len(items)})


@bp.route('/api/library/series/<tvdb_id>')
@login_required
def api_library_series(tvdb_id):
    """Détail d'une série pour la vue grille : meta, saisons (posters TVDB,
    cache 24h) et épisodes avec leur état (présent / manquant / à venir).
    Les posters de saison font 1 requête TVDB par saison sans image au 1er
    affichage, puis 0 (cache BDD)."""
    if not tvdb_id.isdigit():
        return jsonify({'error': 'id invalide'}), 400
    show_s00 = bool(state.config.get('show_s00_specials'))
    tv_root = os.path.abspath(state.config.get('_tv_output_path') or state.config.get('tv_output_path') or '')
    # Chemine du dossier série : chemin absolu fourni ou recherche [tvdbid-…]
    path = request.args.get('path', '').strip()
    if not (path and os.path.isdir(path) and path.lower().startswith(tv_root.lower())):
        path = ''
        try:
            for root, dirs, files in os.walk(tv_root):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for d in dirs:
                    if re.search(rf'\[tvdbid-{tvdb_id}\]', d, re.I):
                        path = os.path.join(root, d)
                        break
                if path:
                    break
        except OSError:
            pass
    if not path:
        return jsonify({'error': 'Dossier série introuvable'}), 404

    meta = ((db.series_meta_get(tvdb_id) or {}).get('data')) or {}
    episodes = _series_episodes_memo(tvdb_id)
    present = library.present_episodes(path)
    fmap = library.episode_files(path)
    seasons = (state.api_handler.get_series_seasons(tvdb_id)
               if state.api_handler.tvdb else {'seasons': []}).get('seasons') or []

    try:
        ep_omdb = db.omdb_episode_all()
    except Exception:
        ep_omdb = {}
    series_imdb = ((db.series_meta_get(tvdb_id) or {}).get('imdb_id')) or ''
    year = (meta.get('year') or str(meta.get('date') or '')[:4] or '')

    def _ep_omdb(s, ep):
        for ek in (f"{series_imdb}:y{year}:s{s:02d}e{ep:02d}", f"{series_imdb}:s{s:02d}e{ep:02d}"):
            ed = ep_omdb.get(ek)
            if ed:
                return ed
        return {}

    by_season = {}
    for e in episodes:
        s, ep = e.get('s'), e.get('e')
        if s is None or ep is None:
            continue
        if s <= 0 and not show_s00:
            continue
        if (s, ep) in present:
            ep_state = 'present'
        elif library.episode_is_upcoming(e):
            ep_state = 'upcoming'
        else:
            ep_state = 'missing'
        ed = _ep_omdb(int(s), int(ep))
        files = fmap.get((s, ep), [])
        # Titre TVDB = langue originale (ex. japonais) : pour les épisodes
        # présents on préfère le titre du fichier local ({t:en}), plus lisible.
        ep_title = e.get('title', '')
        if ep_state == 'present' and files:
            local_title = _ep_title_from_file(files[0]['name'])
            if local_title:
                ep_title = local_title
        by_season.setdefault(int(s), []).append(
            {'s': int(s), 'e': int(ep), 'title': ep_title,
             'airdate': e.get('airdate', ''), 'state': ep_state,
             'runtime': ed.get('Runtime') if ed.get('Runtime') not in (None, '', 'N/A') else '',
             'rating': ed.get('imdbRating') if ed.get('imdbRating') not in (None, '', 'N/A') else '',
             'files': fmap.get((s, ep), [])})
    for eps in by_season.values():
        eps.sort(key=lambda x: x['e'])

    out_seasons = []
    for sd in seasons:
        num = sd.get('number')
        eps = by_season.get(num, [])
        if not eps:
            continue
        out_seasons.append({
            'number': num, 'poster': sd.get('poster', ''), 'year': sd.get('year', ''),
            'total': len(eps),
            'present': sum(1 for x in eps if x['state'] == 'present'),
            'missing': sum(1 for x in eps if x['state'] == 'missing'),
            'upcoming': sum(1 for x in eps if x['state'] == 'upcoming'),
            'episodes': eps,
        })
    # Saisons présentes sur disque mais absentes de TVDB (spéciaux S00…)
    known = {s['number'] for s in out_seasons}
    for num in sorted(by_season):
        if num in known:
            continue
        eps = by_season[num]
        out_seasons.append({
            'number': num, 'poster': '', 'year': '',
            'total': len(eps),
            'present': sum(1 for x in eps if x['state'] == 'present'),
            'missing': sum(1 for x in eps if x['state'] == 'missing'),
            'upcoming': sum(1 for x in eps if x['state'] == 'upcoming'),
            'episodes': eps,
        })
    out_seasons.sort(key=lambda s: s['number'])

    return jsonify({
        'tvdb_id': tvdb_id, 'meta': meta,
        'episodes_available': bool(episodes),
        'seasons': out_seasons,
    })


@bp.route('/api/lib-durations', methods=['POST'])
@login_required
def api_lib_durations():
    """Renvoie les durées (en minutes) des fichiers de bibliothèque listés au
    retour d'un premier affichage en scan rapide. La durée est lue depuis le
    cache disque (calculée en arrière-plan sur le serveur) ; si absente, le
    fichier est calculé ici au besoin (1er accès lent, ensuite vite)."""
    d = request.json or {}
    paths = d.get('paths') or []
    results = duration.lib_durations(paths)
    return jsonify({'results': results})


@bp.route('/api/library/enrich', methods=['POST'])
@login_required
def api_library_enrich():
    """Enrichit en arrière-plan le cache OMDb des imdb_ids non encore cachés,
    pour compléter les notes/dates/genres des entrées de la bibliothèque.
    Accepte aussi des tvdb_ids (dossiers séries) : l'ID IMDb est résolu via TVDB
    (1 requête, mis en cache) puis enrichi via OMDb.
    Ne bloque pas l'affichage : appelé par le frontend après le rendu."""
    d = request.json or {}
    ids = d.get('imdb_ids') or []
    seen = set()
    clean = []
    for x in ids:
        s = str(x).strip().lower()
        if s and s not in seen:
            seen.add(s)
            clean.append(s)
    results = {}
    for imdb in clean:
        if db.omdb_get(imdb) is not None:
            continue
        od = omdb.fetch_omdb_single(imdb)
        if od:
            results[imdb] = {
                'rating': od.get('imdbRating') or '',
                'date': omdb.omdb_date(od.get('Released') or ''),
                'genres': od.get('Genre') or '',
                'certification': od.get('Rated') or '',
                'runtime': od.get('Runtime') or '',
                'poster': od.get('Poster') or '',
            }
    # Dossiers séries : résolution TVDB → IMDb (1 requête TVDB par série, mise en cache)
    # puis enrichissement OMDb de la série.
    for x in d.get('tvdb_ids') or []:
        tvdb_id = str(x).strip().lower()
        if not tvdb_id or tvdb_id in seen:
            continue
        seen.add(tvdb_id)
        imdb = omdb.series_imdb_cached(tvdb_id)
        if not imdb:
            imdb = omdb.resolve_series_imdb(tvdb_id)
        if not imdb:
            continue
        if db.omdb_get(imdb) is None:
            od = omdb.fetch_omdb_single(imdb, is_series=True)
            if not od:
                continue
        else:
            od = db.omdb_get(imdb)
        results['tvdb:' + tvdb_id] = {
            'imdb': imdb,
            'tvdb_slug': (db.series_meta_get(tvdb_id) or {}).get('data', {}).get('tvdb_slug', ''),
            'rating': od.get('imdbRating') or '',
            'date': omdb.omdb_date(od.get('Released') or ''),
            'genres': od.get('Genre') or '',
            'certification': od.get('Rated') or '',
            'runtime': od.get('Runtime') or '',
            'poster': od.get('Poster') or '',
        }
    if clean or (d.get('tvdb_ids') or []):
        logger.info(f"[Enrich] bibliothèque : {len(clean)} film(s) + {len(d.get('tvdb_ids') or [])} série(s) → {len(results)} résultat(s) enrichi(s)")
    return jsonify({'results': results})


@bp.route('/api/library/enrich-episodes', methods=['POST'])
@login_required
def api_library_enrich_episodes():
    """Enrichit les épisodes (note/durée/date OMDb) des dossiers séries affichés.
    Payload: { series: [ { tvdbid, imdb?, title?, year?, episodes: ["S01E02", ...] } ] }
    L'ID IMDb est résolu via TVDB si absent (1 requête par série, mis en cache).
    Chaque épisode absent du cache OMDb coûte 1 requête OMDb (quota géré)."""
    d = request.json or {}
    results = {}
    for s in (d.get('series') or [])[:50]:
        tvdb_id = str(s.get('tvdbid') or '').strip().lower()
        imdb = str(s.get('imdb') or '').strip().lower()
        title = s.get('title') or ''
        year = s.get('year') or None
        if not imdb and tvdb_id:
            imdb = omdb.series_imdb_cached(tvdb_id)
        if not imdb:
            continue
        out = {}
        for label in (s.get('episodes') or [])[:100]:
            m = re.match(r'^[Ss](\d{1,2})[Ee](\d{1,3})$', str(label).strip())
            if not m:
                continue
            sn, en = int(m.group(1)), int(m.group(2))
            ekey = f"{imdb}:y{year}:s{sn:02d}e{en:02d}"
            ed = db.omdb_episode_get(ekey)
            if not ed:
                omdb.fetch_omdb_episode(imdb, title, year, sn, en)
                ed = db.omdb_episode_get(ekey)
            if not ed:
                continue
            rating = (ed.get('imdbRating') or '').strip()
            runtime = (ed.get('Runtime') or '').strip()
            released = (ed.get('Released') or '').strip()
            if rating in ('N/A', ''):
                rating = ''
            if runtime in ('N/A', ''):
                runtime = ''
            if released in ('N/A', ''):
                released = ''
            if not (rating or runtime or released):
                continue
            out[label.upper()] = {
                'rating': rating,
                'runtime': runtime,
                'date': omdb.omdb_date(released),
            }
        if out:
            results[tvdb_id or imdb] = out
    series_list = (d.get('series') or [])[:50]
    if series_list:
        logger.info(f"[Enrich épisodes] {len(series_list)} série(s), {sum(len(r) for r in results.values())} épisode(s) enrichi(s) sur {sum(len(s.get('episodes') or []) for s in series_list)} demandé(s)")
    return jsonify({'results': results})


@bp.route('/api/library/rename-folder', methods=['POST'])
@login_required
def api_library_rename_folder():
    d = request.json or {}
    folder = d.get('path')
    new_name = d.get('new_name')
    if not folder or not os.path.isdir(folder):
        return jsonify({'success': False, 'message': 'Dossier introuvable'}), 400
    movie_root = os.path.abspath(state.config.get('_movie_output_path') or state.config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(state.config.get('_tv_output_path')    or state.config.get('tv_output_path')    or '')
    abs_folder = os.path.abspath(folder)
    if not (abs_folder.startswith(movie_root) or abs_folder.startswith(tv_root)):
        return jsonify({'success': False, 'message': 'Renommage non autorisé hors des dossiers médias'}), 403
    new_name = (new_name or '').strip().rstrip('/\\')
    new_name = os.path.basename(new_name)
    if not new_name or new_name in ('.', '..'):
        return jsonify({'success': False, 'message': 'Nom invalide'}), 400
    if os.path.sep in new_name or (os.path.altsep and os.path.altsep in new_name):
        return jsonify({'success': False, 'message': 'Nom invalide'}), 400
    parent = os.path.dirname(abs_folder)
    dst = os.path.join(parent, new_name)
    if dst == abs_folder:
        return jsonify({'success': True, 'new_path': dst, 'new_name': new_name})
    if os.path.exists(dst):
        return jsonify({'success': False, 'message': f"Le dossier '{new_name}' existe déjà"}), 400
    try:
        shutil.move(abs_folder, dst)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    try:
        with state.scan_watch_lock:
            state.scan_last_snapshot = watcher._scan_snapshot()
    except Exception:
        pass
    return jsonify({'success': True, 'new_path': dst, 'new_name': new_name})


def _folder_series_files(folder):
    """Fichiers vidéo d'un dossier série (scan récursif, ordre stable) avec
    toutes les (saison, épisode) référencées dans le nom. Un fichier
    multi-épisodes (S01E01-E02) renvoie donc plusieurs refs.
    Renvoie [(saison, [(s, e), ...], path, name), ...]."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for name in sorted(files):
            if Path(name).suffix.lower() not in VIDEO_EXT:
                continue
            refs = library.episode_refs(name)
            if refs:
                out.append((refs[0][0], refs, os.path.join(root, name), name))
    return out


def _folder_series_rename(folder, series_title, year, tvdb_id, imdb_id, tmdb_id, translations,
                          dry_run=False):
    """Renomme les fichiers d'un dossier série selon le format TV configuré.
    Les titres d'épisodes viennent du cache TVDB ; au 1er passage le cache est
    vide (parsing en arrière-plan) et le renommage est complété après parsing.
    Les sous-titres (.srt, .sub, .ass, ...) suivent le vidéo, même quand le
    vidéo est déjà au bon nom (passe de rattrapage).
    Renvoie [ {from, to}, ... ].
    `dry_run=True` : calcule les renommages (vidéos + sous-titres) SANS toucher
    au disque ni à l'historique — aperçu pour la popup de confirmation."""
    ep_titles = {}
    try:
        cached = db.series_episodes_get(str(tvdb_id))
        for ep in (cached or {}).get('episodes') or []:
            if ep.get('s') is not None and ep.get('e') is not None:
                ep_titles[(ep.get('s'), ep.get('e'))] = ep.get('title') or ''
    except Exception:
        pass
    # Épisodes spéciaux (S00) : renommés uniquement si la série en a dans TVDB.
    has_s00 = any(s == 0 for (s, _e) in ep_titles)
    details = {
        'title': series_title, 'year': year or '',
        'tvdbid': str(tvdb_id or ''), 'tvdb': str(tvdb_id or ''),
        'imdbid': imdb_id or '', 'imdb': imdb_id or '',
        'tmdbid': tmdb_id or '', 'tmdb': tmdb_id or '',
        'translations': translations or {},
    }
    renames = []
    final_videos = []
    history_entries = []
    for s, refs, path, name in _folder_series_files(folder):
        if s == 0 and not has_s00:
            continue
        old_name = name
        e = refs[0][1]
        titles = []
        for r in refs:
            t = ep_titles.get(r, '')
            if t and t not in titles:
                titles.append(t)
        if len(refs) > 1:
            label = naming.multi_episode_label(s, [r[1] for r in refs])
            new_name = naming.generate_filename(name, 'tv', s, e, details,
                                                episode_label=label,
                                                sxe_label=naming.multi_episode_label_sxe(s, [r[1] for r in refs]),
                                                episode_title=' & '.join(titles) or None)
        else:
            details['episode_title'] = titles[0] if titles else ''
            new_name = naming.generate_filename(name, 'tv', s, e, details)
        if new_name and new_name != name:
            dst = os.path.join(os.path.dirname(path), new_name)
            if not dry_run:
                try:
                    _move_path(path, dst)
                except Exception as ex:
                    logger.warning(f"[Rename série] échec {name} → {new_name}: {ex}")
                    continue
                history_entries.append({'id': str(uuid.uuid4()), 'op': 'rename',
                                        'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                                        'from_path': path, 'from_name': name,
                                        'to_path': dst, 'to_name': new_name})
                _migrate_file_cache(path, dst)
            renames.append({'from': name, 'to': new_name})
            path, name = dst, new_name
        final_videos.append((name, refs))
        # Sous-titres du même dossier que le vidéo (cas propre + orphelin).
        for old_sub, new_sub in library.rename_subtitles_for_video(path, name, refs=refs,
                                                                   old_name=old_name,
                                                                   dry_run=dry_run):
            if not dry_run:
                history_entries.append({'id': str(uuid.uuid4()), 'op': 'rename',
                                        'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                                        'from_path': old_sub, 'from_name': os.path.basename(old_sub),
                                        'to_path': new_sub, 'to_name': os.path.basename(new_sub)})
            renames.append({'from': os.path.basename(old_sub), 'to': os.path.basename(new_sub)})
    # Rattrapage folder-wide : SRT dans d'autres sous-dossiers du dossier série
    # (ex. SRT à la racine, vidéo dans « Season 1/ ») alignés sur le nom final.
    for old_sub, new_sub in library.catchup_subtitles_for_folder(folder, final_videos,
                                                                 dry_run=dry_run):
        if not dry_run:
            history_entries.append({'id': str(uuid.uuid4()), 'op': 'rename',
                                    'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                                    'from_path': old_sub, 'from_name': os.path.basename(old_sub),
                                    'to_path': new_sub, 'to_name': os.path.basename(new_sub)})
        renames.append({'from': os.path.basename(old_sub), 'to': os.path.basename(new_sub)})
    if not dry_run:
        append_history_batch(history_entries)
    return renames


@bp.route('/api/library/rename-folder-series', methods=['POST'])
@login_required
def api_library_rename_folder_series():
    """Renommage complet d'un dossier série sélectionné manuellement :
    synchrone (fichiers + dossier), le parsing TVDB (titres d'épisodes,
    épisodes manquants, enrichissement OMDb) continue en arrière-plan."""
    d = request.json or {}
    folder = d.get('path')
    if not folder or not os.path.isdir(folder):
        return jsonify({'success': False, 'message': 'Dossier introuvable'}), 400
    tv_root = os.path.abspath(state.config.get('_tv_output_path') or state.config.get('tv_output_path') or '')
    abs_folder = os.path.abspath(folder)
    if not abs_folder.startswith(tv_root):
        return jsonify({'success': False, 'message': 'Renommage non autorisé hors du dossier TV'}), 403
    tvdb_id = str(d.get('tvdbid') or '').strip().lower()
    if not tvdb_id:
        return jsonify({'success': False, 'message': 'ID TVDB manquant'}), 400
    year = d.get('year') or None
    try:
        year = int(year) if year not in (None, '', 'None') else None
    except (TypeError, ValueError):
        year = None
    translations = d.get('translations') or {}
    dry_run = bool(d.get('dry_run'))
    details = {}
    try:
        details = db.series_meta_get(tvdb_id) or {}
    except Exception:
        details = {}
    # Le choix manuel devient la nouvelle vérité du cache série. Il remplace
    # une association précédente qui pouvait pointer vers une mauvaise série.
    selected = {
        'id': tvdb_id,
        'tvdbid': tvdb_id,
        'title': d.get('title') or '',
        'year': str(year or ''),
        'tvdb_slug': d.get('tvdb_slug') or '',
        'imdbid': d.get('imdb') or '',
        'imdb': d.get('imdb') or '',
        'tmdbid': d.get('tmdb') or '',
        'tmdb': d.get('tmdb') or '',
        'poster': d.get('poster') or '',
        'genres': d.get('genres') or '',
        'certification': d.get('certification') or '',
        'episode_order': d.get('episode_order') or '',
        'translations': translations,
    }
    previous_data = details.get('data') or {}
    selected = {**previous_data, **{k: v for k, v in selected.items() if v}}
    if not dry_run:
        db.series_meta_set(tvdb_id, selected.get('imdbid', ''), selected)
    series_title = naming.clean_title(details.get('data', {}).get('title') or d.get('title') or '')
    if not series_title:
        series_title = naming.clean_title(os.path.basename(abs_folder))
    imdb_id = (details.get('imdb_id') or d.get('imdb') or '').strip().lower()
    tmdb_id = str(d.get('tmdb') or '').strip()
    if not imdb_id and not dry_run:
        try:
            imdb_id = omdb.resolve_series_imdb(tvdb_id, title=series_title, year=year)
        except Exception:
            imdb_id = ''

    # La validation manuelle charge aussi les types d'ordre TVDB. Cela permet
    # de choisir automatiquement l'ordre Netflix pour les séries qui le
    # proposent avant le premier renommage.
    if state.api_handler.tvdb:
        if not dry_run:
            try:
                tvdb_details = state.api_handler.tvdb.get_series_details(int(tvdb_id)) or {}
                if tvdb_details:
                    db.series_meta_set(tvdb_id, imdb_id, {**(details.get('data') or {}), **tvdb_details})
            except Exception as e:
                logger.warning(f"[Rename série] détails TVDB {tvdb_id}: {e}")
        # Le cache d'épisodes est rempli même en dry-run (idempotent, TVDB 24h)
        # pour que l'aperçu utilise les vrais titres d'épisodes.
        try:
            library.tvdb_series_episodes(tvdb_id)
        except Exception as e:
            logger.warning(f"[Rename série] cache épisodes {tvdb_id}: {e}")

    renames = _folder_series_rename(abs_folder, series_title, year, tvdb_id, imdb_id, tmdb_id,
                                    translations, dry_run=dry_run)

    # Nom du dossier : dérivé du nom d'épisode généré (même format que le JS).
    sample_name = naming.generate_filename('sample.mkv', 'tv', 1, 1, {
        'title': series_title, 'year': year or '',
        'tvdbid': tvdb_id, 'tvdb': tvdb_id,
        'imdbid': imdb_id, 'imdb': imdb_id,
        'tmdbid': tmdb_id, 'tmdb': tmdb_id,
        'translations': translations,
    })
    desired_folder = naming.series_folder_name(sample_name)
    folder_note = None
    folder_base = os.path.basename(abs_folder)
    new_folder = abs_folder
    if desired_folder and desired_folder != folder_base:
        dst = os.path.join(os.path.dirname(abs_folder), desired_folder)
        if dry_run:
            # Aperçu : on signale le futur renommage de dossier sans le faire.
            if not os.path.exists(dst):
                folder_note = {'from': folder_base, 'to': desired_folder}
        elif not os.path.exists(dst):
            try:
                shutil.move(abs_folder, dst)
                new_folder = dst
                folder_note = {'from': folder_base, 'to': desired_folder}
            except Exception as e:
                folder_note = {'from': folder_base, 'to': desired_folder, 'error': str(e)}
    if dry_run:
        return jsonify({'success': True, 'dry_run': True, 'renames': renames,
                        'folder': folder_note, 'new_folder': new_folder})
    _refresh_scan_snapshot()

    def _background():
        try:
            library.tvdb_series_episodes(tvdb_id)
        except Exception as e:
            logger.warning(f"[Rename série] cache épisodes {tvdb_id}: {e}")
        # 2e passe de renommage : le parsing TVDB a rempli le cache des
        # épisodes, les titres manquant au 1er passage sont complétés ici.
        try:
            _folder_series_rename(new_folder, series_title, year, tvdb_id, imdb_id, tmdb_id, translations)
            _refresh_scan_snapshot()
        except Exception as e:
            logger.warning(f"[Rename série] 2e passe {tvdb_id}: {e}")
        try:
            imdb2 = omdb.series_imdb_cached(tvdb_id) or imdb_id
            if imdb2:
                od = omdb.fetch_omdb_single(imdb2, is_series=True)
                if od and od.get('Poster') and od.get('Poster') != 'N/A':
                    try:
                        db.series_meta_set(tvdb_id, imdb2, {
                            **(details.get('data') or {}),
                            'poster': od['Poster'],
                        })
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"[Rename série] enrichissement {tvdb_id}: {e}")
        watcher.broadcast_library_refresh()

    threading.Thread(target=_background, daemon=True).start()
    return jsonify({'success': True, 'new_path': new_folder, 'new_name': os.path.basename(new_folder),
                    'renames': renames, 'folder': folder_note,
                    'background': True, 'tvdbid': tvdb_id, 'imdb': imdb_id})


@bp.route('/api/library/delete-folder', methods=['POST'])
@login_required
def api_library_delete_folder():
    d = request.json or {}
    path = d.get('path')
    if not path or not os.path.isdir(path):
        return jsonify({'success': False, 'message': 'Dossier introuvable'}), 400
    # Safety: only allow deletion inside movie/tv output paths
    movie_root = os.path.abspath(state.config.get('_movie_output_path') or state.config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(state.config.get('_tv_output_path')    or state.config.get('tv_output_path')    or '')
    abs_path   = os.path.abspath(path)
    if not (abs_path.startswith(movie_root) or abs_path.startswith(tv_root)):
        return jsonify({'success': False, 'message': 'Suppression non autorisée hors des dossiers médias'}), 403
    if any(True for _ in os.scandir(path)):
        return jsonify({'success': False, 'message': 'Le dossier n\'est pas vide'}), 400
    try:
        os.rmdir(path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400
