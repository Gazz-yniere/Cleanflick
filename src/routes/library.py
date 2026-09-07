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

from flask import Blueprint, jsonify, request

from .. import db, state
from ..auth import login_required
from ..history import append_history
from ..utils import _move_path, _run_file_op, _migrate_file_cache, _refresh_scan_snapshot
from .. import library
from .. import naming
from .. import duration
from .. import watcher
from ..api import omdb

logger = logging.getLogger(__name__)
bp = Blueprint('library', __name__)

VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'}

# Marqueur d'épisode (y compris multi-épisodes : S01E01-E02, S01E01E02, 1x02-03).
_EP_MARKER = r'[Ss]\d{2}[Ee]\d{2}(?:[-–—~]\s*[Ee]\d{2}|[Ee]\d{2})?|\d+x\d{2}(?:[-–—~]\d{2})?'


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
    episodes = library.series_episodes_cached(series_id)
    present = library.present_episodes(path)
    # Épisodes spéciaux (saison 0 / S00) ignorés par défaut ; affichés si l'option
    # « show_s00_specials » est activée dans la configuration.
    show_s00 = bool(state.config.get('show_s00_specials'))
    # Déduplication par (saison, episode) : le cache TVDB peut contenir des doublons.
    missing = []
    seen = set()
    for e in episodes:
        if e['s'] <= 0 and not show_s00:
            continue
        key = (e['s'], e['e'])
        if key in present or key in seen:
            continue
        seen.add(key)
        missing.append(e)
    missing.sort(key=lambda x: (x['s'], x['e']))
    return jsonify({
        'series_id': series_id,
        'available': bool(episodes),
        'count': len(missing),
        'missing': [{'s': e['s'], 'e': e['e'], 'label': f"S{int(e['s']):02d}E{int(e['e']):02d}", 'title': e['title']} for e in missing]
    })


@bp.route('/api/library')
@login_required
def api_library():
    path = request.args.get('path', '').strip()
    library_type = request.args.get('type', '')  # 'movie' or 'tv'

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
                roots.append({'name': root_name, 'path': lib_path, 'type': lib_type, 'is_dir': True, 'size': library.folder_size(lib_path)})
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
        episode_omdb_map = db.omdb_episode_all()
    except Exception:
        episode_omdb_map = {}
    try:
        series_imdb_map = omdb._series_imdb_map()
    except Exception:
        series_imdb_map = {}

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

    def cache_meta(cached):
        if not cached:
            return None
        results = cached.get('results') or []
        if not results:
            return None
        top = results[0]
        meta = {
            'title': top.get('title', ''),
            'year': top.get('year'),
            'imdb': top.get('imdb_id', ''),
            'tmdb': top.get('tmdb_id', ''),
            'tvdb': str(top.get('id_tvdb') or top.get('id') or ''),
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
        meta = cache_meta(best)
        meta = meta or id_meta(name) or name_meta(name)
        if lib_type == 'tv':
            m2 = re.search(r'\[tvdbid-([0-9]+)\]', name, re.I)
            if m2 and not meta:
                # Dossier série sans meta (nom non calqué sur le cache) : on réutilise
                # le poster de la série depuis les caches (0 requête API).
                poster, title = series_poster(m2.group(1))
                if poster:
                    meta = {'poster': poster, 'title': title, 'tvdb': m2.group(1)}
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
                        imdb = series_imdb_map.get(tvdb_id) or omdb.resolve_series_imdb(
                            tvdb_id, title=meta.get('title') or name.split(' (')[0].strip(),
                            year=meta.get('year'))
                        if imdb:
                            meta['imdb'] = imdb
                            _apply_omdb(meta, imdb, live=False)
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
        meta = cache_meta(basename_map.get(name))
        if not meta:
            if m:
                key = (name[:m.start()].strip().lower(), m.group(1).lower())
                meta = cache_meta(episode_map.get(key))
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
            ep_present = ep_total = None
            if lib_type == 'tv':
                mep = re.search(r'\[tvdbid-([0-9]+)\]', entry.name, re.I)
                if mep:
                    # Compteur d'épisodes « présents / total TVDB » (S00 inclus
                    # uniquement si l'option show_s00_specials est activée).
                    show_s00 = bool(state.config.get('show_s00_specials'))
                    eps = library.series_episodes_cached(mep.group(1))
                    ep_total = sum(1 for e in eps if (e.get('s') or 0) > 0 or show_s00)
                    present = library.present_episodes(entry.path)
                    ep_present = sum(1 for (s, _e) in present if s > 0 or show_s00)
            entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': True, 'child_count': child_count, 'size': library.folder_size(entry.path, recursive=False), 'valid': valid_dir, 'meta': folder_meta(entry.name), 'ep_present': ep_present, 'ep_total': ep_total})
        elif entry.is_file() and Path(entry.name).suffix.lower() in VIDEO_EXT:
            valid = is_valid_name(entry.name, lib_type)
            size = 0
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
            dur = duration.get_duration(entry.path)
            entries.append({'name': entry.name, 'path': entry.path, 'type': lib_type, 'is_dir': False, 'size': size,
                             'valid': valid, 'meta': file_meta(entry.name, dur, entry.path),
                            'dur_pending': dur is None and state.config.get('lib_fast_scan')})
    # Mode scan rapide : get_duration() a déjà mis les durées manquantes dans
    # la file FIFO (worker unique, calcul séquentiel dans l'ordre d'affichage).

    return jsonify({'path': path, 'type': lib_type, 'entries': entries, 'total': total, 'offset': offset, 'limit': limit})


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
            imdb = omdb.series_imdb_cached(tvdb_id) or omdb.resolve_series_imdb(tvdb_id, title=title, year=year)
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


@bp.route('/api/library/send-back-folder', methods=['POST'])
@login_required
def api_library_send_back_folder():
    d = request.json or {}
    folder = d.get('path')
    if not folder or not os.path.isdir(folder):
        return jsonify({'success': False, 'message': 'Dossier introuvable'}), 400
    movie_root = os.path.abspath(state.config.get('_movie_output_path') or state.config.get('movie_output_path') or '')
    tv_root    = os.path.abspath(state.config.get('_tv_output_path')    or state.config.get('tv_output_path')    or '')
    abs_folder = os.path.abspath(folder)
    if not (abs_folder.startswith(movie_root) or abs_folder.startswith(tv_root)):
        return jsonify({'success': False, 'message': 'Opération non autorisée hors des dossiers médias'}), 403
    input_path = state.config.get('_input_path') or state.config.get('input_path')
    moved, errors = [], []
    for entry in os.scandir(folder):
        if entry.is_file() and Path(entry.name).suffix.lower() in VIDEO_EXT:
            dst = os.path.join(input_path, entry.name)
            try:
                _move_path(entry.path, dst)
                append_history({'id': str(uuid.uuid4()), 'op': 'move', 'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                                'from_path': entry.path, 'from_name': entry.name,
                                'to_path': dst, 'to_name': entry.name})
                moved.append(entry.name)
            except Exception as e:
                errors.append(f"{entry.name}: {e}")
    if errors:
        return jsonify({'success': False, 'message': '\n'.join(errors), 'moved': moved}), 400
    return jsonify({'success': True, 'moved': moved})


@bp.route('/api/library/send-back', methods=['POST'])
@login_required
def api_library_send_back():
    d = request.json or {}
    src = d.get('path')
    if not src or not os.path.isfile(src):
        return jsonify({'success': False, 'message': 'Fichier introuvable'}), 400
    dst = os.path.join(state.config.get('_input_path') or state.config.get('input_path'), os.path.basename(src))
    job_id = str(uuid.uuid4())
    try:
        _run_file_op(job_id, src, dst, {
            'id': job_id, 'op': 'move',
            'date': time.strftime('%Y-%m-%d %H:%M:%S'),
            'from_path': src, 'from_name': os.path.basename(src),
            'to_path': dst, 'to_name': os.path.basename(dst),
        })
        return jsonify({'success': True, 'job_id': job_id})
    except Exception as e:
        logger.error(f"Send-back error for {src} -> {dst}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 400


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
    (saison, épisode) extraits du nom. Un fichier multi-épisodes (S01E01-E02)
    génère une entrée par épisode. Renvoie [(s, e, path, name), ...]."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for name in sorted(files):
            if Path(name).suffix.lower() not in VIDEO_EXT:
                continue
            refs = library.episode_refs(name)
            if refs:
                s, e = refs[0]
                out.append((s, e, os.path.join(root, name), name))
    return out


def _folder_series_rename(folder, series_title, year, tvdb_id, imdb_id, tmdb_id, translations):
    """Renomme les fichiers d'un dossier série selon le format TV configuré.
    Les titres d'épisodes viennent du cache TVDB ; au 1er passage le cache est
    vide (parsing en arrière-plan) et le renommage est complété après parsing.
    Renvoie [ {from, to}, ... ]."""
    ep_titles = {}
    try:
        cached = db.series_episodes_get(str(tvdb_id))
        for ep in (cached or {}).get('episodes') or []:
            if ep.get('s') is not None and ep.get('e') is not None:
                ep_titles[(ep.get('s'), ep.get('e'))] = ep.get('title') or ''
    except Exception:
        pass
    details = {
        'title': series_title, 'year': year or '',
        'tvdbid': str(tvdb_id or ''), 'tvdb': str(tvdb_id or ''),
        'imdbid': imdb_id or '', 'imdb': imdb_id or '',
        'tmdbid': tmdb_id or '', 'tmdb': tmdb_id or '',
        'translations': translations or {},
    }
    renames = []
    for s, e, path, name in _folder_series_files(folder):
        details['episode_title'] = ep_titles.get((s, e), '')
        new_name = naming.generate_filename(name, 'tv', s, e, details)
        if not new_name or new_name == name:
            continue
        dst = os.path.join(os.path.dirname(path), new_name)
        try:
            _move_path(path, dst)
        except Exception as ex:
            logger.warning(f"[Rename série] échec {name} → {new_name}: {ex}")
            continue
        append_history({'id': str(uuid.uuid4()), 'op': 'rename',
                        'date': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'from_path': path, 'from_name': name,
                        'to_path': dst, 'to_name': new_name})
        _migrate_file_cache(path, dst)
        renames.append({'from': name, 'to': new_name})
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
    details = {}
    try:
        details = db.series_meta_get(tvdb_id) or {}
    except Exception:
        details = {}
    series_title = naming.clean_title(details.get('data', {}).get('title') or d.get('title') or '')
    if not series_title:
        series_title = naming.clean_title(os.path.basename(abs_folder))
    imdb_id = (details.get('imdb_id') or d.get('imdb') or '').strip().lower()
    tmdb_id = str(d.get('tmdb') or '').strip()
    if not imdb_id:
        try:
            imdb_id = omdb.resolve_series_imdb(tvdb_id, title=series_title, year=year)
        except Exception:
            imdb_id = ''

    renames = _folder_series_rename(abs_folder, series_title, year, tvdb_id, imdb_id, tmdb_id, translations)

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
        if not os.path.exists(dst):
            try:
                shutil.move(abs_folder, dst)
                new_folder = dst
                folder_note = {'from': folder_base, 'to': desired_folder}
            except Exception as e:
                folder_note = {'from': folder_base, 'to': desired_folder, 'error': str(e)}
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
