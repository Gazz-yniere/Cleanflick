"""Bibliothèque : tailles de dossiers, épisodes de séries, présence sur disque."""
import os
import re
import time
import logging
from pathlib import Path

from . import db, naming, state

logger = logging.getLogger(__name__)

_FOLDER_DIR_CACHE = {}      # path -> (mtime, size) : taille de dossier (non récursif)
_EP_CACHE = {}              # path -> (mtime, set) : épisodes (saison, numéro) présents
_EP_FILE_CACHE = {}         # path -> (mtime, dict) : (saison, numéro) -> fichiers
VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'}
SUBTITLE_EXT = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx'}
_SUB_MARKER = r'[Ss]\d{1,2}[Ee]\d{1,3}(?:[-–—~]\s*[Ee]\d{1,3}|[Ee]\d{1,3})?|\b\d{1,2}x\d{2}(?:[-–—~]\d{2})?'


def folder_size(path, recursive=True):
    """Taille totale d'un dossier.

    recursive=False : uniquement le contenu direct du dossier (rapide, 1 seul
    scandir) — utilisé par l'affichage de la bibliothèque. La taille réelle
    (récursive) reste disponible via le cache disque si déjà calculée.
    """
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return 0
    if not recursive:
        mem = _FOLDER_DIR_CACHE.get(path)
        if mem and mem[0] == mtime:
            return mem[1]
        # Si une taille (récursive) est déjà connue pour ce statut, on la réutilise.
        try:
            cached = db.folder_size_get(path, mtime)
            if cached is not None:
                return cached['size']
        except Exception:
            pass
        total, direct_files, subdirs = 0, 0, 0
        try:
            for e in os.scandir(path):
                try:
                    if e.is_dir(follow_symlinks=False):
                        subdirs += 1
                    else:
                        direct_files += 1
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
        except OSError:
            pass
        if subdirs and not direct_files:
            # Fichiers rangés en sous-dossiers (saisons) : walk récursif, dont le
            # résultat est mémoïsé en base (invalidé par mtime), payé 1 fois only.
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            try:
                db.folder_size_set(path, total, mtime)
            except Exception:
                pass
        _FOLDER_DIR_CACHE[path] = (mtime, total)
        if len(_FOLDER_DIR_CACHE) > 5000:
            _FOLDER_DIR_CACHE.clear()
        return total
    key = path
    try:
        cached = db.folder_size_get(path, mtime)
        if cached is not None:
            return cached['size']
    except Exception:
        pass
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    try:
        db.folder_size_set(key, total, mtime)
    except Exception:
        pass
    return total


def folder_size_direct(path):
    """Taille des fichiers directement contenus dans un dossier.

    Cette variante ne descend jamais dans les sous-dossiers : elle est destinée
    au premier rendu de l'arbre, où une taille complète serait trop coûteuse.
    """
    try:
        indexed_at = float(db.meta_get('library_file_sizes_indexed_at', '0') or 0)
        if indexed_at and time.time() - indexed_at < 24 * 60 * 60:
            cached = db.library_file_size_sum_direct(os.path.abspath(path))
            if cached is not None:
                return cached['size']
    except Exception as e:
        logger.warning(f"Library file size cache unavailable for {path}: {e}")
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if not entry.is_dir(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def folder_summary_direct(path, cached_size=None):
    """Return the immediate entry count and direct-file size in one directory pass."""
    count = 0
    total = cached_size if cached_size is not None else 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                count += 1
                if cached_size is not None:
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        pass
    return {'count': count, 'size': total}


def _episode_order_candidates(season_types):
    candidates = [('default', 'official')]
    for item in season_types or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get('type') or '').strip().lower()
        label = str(item.get('alternateName') or item.get('name') or '').strip().lower()
        if value in ('', 'official', 'default'):
            if not value and 'netflix' in label:
                candidates.append(('alternate', 'alternate'))
            continue
        order = 'alternate' if value == 'netflix' else value
        candidates.append((order, order))
    unique = []
    seen = set()
    for order, setting in candidates:
        if order not in seen:
            unique.append((order, setting))
            seen.add(order)
    return unique


def _episode_title_key(title):
    return re.sub(r'\W+', '', str(title or '').casefold())


def _local_episode_title(filename):
    stem = Path(filename).stem
    match = re.search(
        r'(?:[Ss]\d{1,2}[Ee]\d{1,3}(?:[-–—~]\s*[Ee]\d{1,3}|[Ee]\d{1,3})?'
        r'|\b\d{1,2}x\d{2}(?:[-–—~]\d{2})?)\s*[-–—]\s*(.+)$',
        stem,
        re.I,
    )
    return _episode_title_key(match.group(1)) if match else ''


def _best_episode_order(candidates, local_files):
    """Choose the TVDB order that best covers the episode numbers already on disk."""
    local_refs = set(local_files)
    if not local_refs or not candidates:
        return 'default', [], False

    best_order = next(iter(candidates))
    best_episodes = candidates[best_order]
    best_score = (-1, -1)
    for order, episodes in candidates.items():
        by_ref = {}
        for episode in episodes:
            try:
                ref = (int(episode.get('s')), int(episode.get('e')))
            except (TypeError, ValueError):
                continue
            by_ref[ref] = _episode_title_key(
                episode.get('title') or episode.get('episode_title') or episode.get('t')
            )
        matched = local_refs & by_ref.keys()
        title_matches = 0
        for ref in matched:
            remote_title = by_ref.get(ref)
            if not remote_title:
                continue
            if any(_local_episode_title(item.get('name')) == remote_title
                   for item in local_files.get(ref, [])):
                title_matches += 1
        score = (len(matched), title_matches)
        if score > best_score:
            best_order, best_episodes, best_score = order, episodes, score
    return best_order, best_episodes, best_score[0] > 0


def _normalized_series_episodes(result):
    episodes = []
    seen = set()
    for episode in (result or {}).get('episodes', []):
        try:
            season = int(episode.get('s', episode.get('season')))
            number = int(episode.get('e', episode.get('episode')))
        except (TypeError, ValueError):
            continue
        key = (season, number)
        if key in seen:
            continue
        seen.add(key)
        episodes.append({
            's': season,
            'e': number,
            'title': episode.get('title') or episode.get('episode_title') or episode.get('t') or '',
            'airdate': episode.get('airdate') or episode.get('d') or '',
        })
    return episodes


def tvdb_series_episodes(series_id, series_path=None, season_types=None):
    """Liste complète des épisodes TVDB (toutes saisons), mise en cache 24h."""
    cached = db.series_episodes_get(series_id)
    stale = None
    meta_record = db.series_meta_get(str(series_id)) or {}
    meta = meta_record.get('data') or {}
    configured = str(meta.get('episode_order') or (state.config or {}).get('tvdb_episode_order') or 'auto').lower()
    auto_order = configured == 'auto'
    order = 'default' if auto_order else ('default' if configured in ('official', 'default') else
                                          'alternate' if configured == 'netflix' else configured)
    order_match = False
    local_files = {}
    if auto_order and series_path and os.path.isdir(series_path):
        _EP_FILE_CACHE.pop(series_path, None)
        _EP_CACHE.pop(series_path, None)
        local_files = episode_files(series_path)

    if auto_order and local_files:
        season_types = season_types or meta.get('season_types') or meta.get('seasonTypes') or []
        if not season_types and state.api_handler.tvdb:
            try:
                details = state.api_handler.tvdb.get_series_details(int(series_id)) or {}
                season_types = details.get('seasonTypes') or details.get('season_types') or []
            except Exception as e:
                logger.warning(f"TVDB season-order lookup failed for {series_id}: {e}")
        candidate_episodes = {}
        candidates = _episode_order_candidates(season_types)
        for candidate_order, _setting in candidates:
            try:
                response = state.api_handler.get_series_episodes(series_id, season_type=candidate_order)
                episodes = _normalized_series_episodes(response)
                if episodes:
                    candidate_episodes[candidate_order] = episodes
            except Exception as e:
                logger.warning(f"TVDB episode-order lookup failed for {series_id} ({candidate_order}): {e}")
        if candidate_episodes:
            order, selected_episodes, order_match = _best_episode_order(candidate_episodes, local_files)
            if selected_episodes:
                if order_match:
                    selected_setting = 'official' if order == 'default' else order
                    try:
                        db.series_meta_set(
                            str(series_id),
                            meta_record.get('imdb_id', ''),
                            {**meta, 'episode_order': selected_setting},
                        )
                    except Exception as e:
                        logger.warning(f"TVDB episode-order preference save failed for {series_id}: {e}")
                db.series_episodes_set(series_id, {
                    'episodes': selected_episodes, 'order': order, 'order_match': order_match,
                })
                return selected_episodes

    try:
        if cached and cached.get('episodes'):
            stale = cached['episodes']
    except Exception:
        pass
    if (cached and cached.get('episodes') and cached.get('order', 'default') == order
            and len(cached['episodes']) < 500 and not (auto_order and local_files)):
        # Cache obsolète sans airdate (précédent au split manquant/à venir) : on
        # re-récupère pour que la date de diffusion soit disponible.
        if any('airdate' not in e for e in cached['episodes']):
            stale = cached['episodes']
        else:
            return cached['episodes']
    try:
        res = state.api_handler.get_series_episodes(series_id, season_type=order)
        episodes = _normalized_series_episodes(res)
        if episodes:
            db.series_episodes_set(series_id, {
                'episodes': episodes, 'order': order, 'order_match': order_match,
            })
            return episodes
    except Exception as e:
        logger.warning(f"Missing-episodes TVDB fetch error for {series_id}: {e}")
    return stale or []


def enrich_episode_title_translations(series_id, cached):
    """Ajoute au cache les titres d'épisodes demandés par le format TV."""
    languages = naming.episode_title_languages()
    episodes = (cached or {}).get('episodes') or []
    if not languages or not episodes:
        return cached or {}

    loaded = set((cached or {}).get('episode_title_languages') or [])
    changed = False
    order = (cached or {}).get('order') or 'default'
    for lang in languages:
        if lang in loaded:
            continue
        try:
            response = state.api_handler.get_series_episodes(
                series_id, season_type=order, lang=lang,
            )
            localized = (response or {}).get('episodes') or []
            if not localized:
                logger.warning(f"TVDB episode translations unavailable for {series_id} ({lang})")
                continue
            translated_by_ref = {}
            for episode in localized:
                season = episode.get('s')
                if season is None:
                    season = episode.get('season')
                number = episode.get('e')
                if number is None:
                    number = episode.get('episode')
                translated_by_ref[(season, number)] = (
                    episode.get('episode_title') or episode.get('title') or ''
                )
            for episode in episodes:
                translated = translated_by_ref.get((episode.get('s'), episode.get('e')))
                if translated:
                    episode.setdefault('episode_title_translations', {})[lang] = translated
            loaded.add(lang)
            changed = True
        except Exception as e:
            logger.warning(f"TVDB episode translations failed for {series_id} ({lang}): {e}")

    if changed:
        cached['episode_title_languages'] = sorted(loaded)
        db.series_episodes_set(str(series_id), cached)
    return cached


_EP_REF_PATTERNS = [
    # S01E01-E02 / S01E01E02 / S01E01, E02 (multi-épisodes dans un même fichier)
    re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})[.,\s]*(?:[-–—~]\s*[Ee](\d{1,3})|[Ee](\d{1,3}))'),
    # 1x02-03
    re.compile(r'\b(\d{1,2})x(\d{2})\s*[-–—~]\s*(\d{2})\b'),
    # S01E01 / 1x02
    re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})'),
    re.compile(r'\b(\d{1,2})x(\d{2})\b'),
]


def episode_refs(filename):
    """Liste des (saison, episode) référencés dans un nom de fichier.
    Gère les fichiers multi-épisodes : S01E01-E02, S01E01E02, S01E01, E02,
    1x02-03... (un seul fichier peut donc couvrir plusieurs épisodes)."""
    for pat in _EP_REF_PATTERNS:
        m = pat.search(filename)
        if m:
            g = m.groups()
            season = int(g[0])
            first = int(g[1])
            last = int(g[2] or g[3]) if (len(g) > 2 and (g[2] or g[3])) else first
            if last < first:
                first, last = last, first
            return [(season, e) for e in range(first, last + 1)]
    return []


def present_episodes(path):
    """Ensemble des (saison, episode) présents dans un dossier série (scan récursif,
    car les épisodes peuvent être répartis dans des sous-dossiers par saison).
    Mémoïsé par mtime du dossier : le re-scan ne coûte qu'un seul scandir."""
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return set()
    mem = _EP_CACHE.get(path)
    if mem and mem[0] == mtime:
        return mem[1]
    present = set()
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if Path(f).suffix.lower() in VIDEO_EXT:
                    present.update(episode_refs(f))
    except Exception:
        pass
    _EP_CACHE[path] = (mtime, present)
    if len(_EP_CACHE) > 2000:
        _EP_CACHE.clear()
    return present


def episode_files(path):
    """Carte (saison, episode) → [{'name', 'size', 'path'}] des fichiers du
    dossier série. Même balayage que present_episodes (mémoïsé)."""
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return {}
    mem = _EP_FILE_CACHE.get(path)
    if mem and mem[0] == mtime:
        return mem[1]
    fmap = {}
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if Path(f).suffix.lower() in VIDEO_EXT:
                    full = os.path.join(root, f)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                    for key in episode_refs(f):
                        fmap.setdefault(key, []).append({'name': f, 'size': size, 'path': full})
    except Exception:
        pass
    _EP_FILE_CACHE[path] = (mtime, fmap)
    if len(_EP_FILE_CACHE) > 2000:
        _EP_FILE_CACHE.clear()
    return fmap


def _stem_prefix(name):
    """Partie du stem avant le marqueur d'épisode (ou le stem complet)."""
    stem = Path(name).stem
    m = re.search(_SUB_MARKER, stem, re.I)
    return (stem[:m.start()] if m else stem).strip()


def _norm_prefix(name):
    """Préfixe de série normalisé (séparateurs retirés) pour comparaison :
    « Show - » et « Show » → « show » ; « Breaking Bad » → « breakingbad »."""
    p = _stem_prefix(name)
    p = re.sub(r'[\s\-._]+', '', p).lower()
    return p


def rename_subtitles_for_video(video_path, new_video_name, refs=None, old_name=None,
                               dry_run=False):
    """Renomme les sous-titres d'un fichier vidéo en conservant le même stem.

    Deux cas gérés :
    1. Sous-titres « propres » : même stem que le nom actuel (ou `old_name`)
       du vidéo (ex. « Show - S01E01.mkv » + « Show - S01E01.srt ») →
       renommés en parallèle du vidéo.
    2. Sous-titres orphelins : leur nom contient les mêmes marqueurs
       d'épisode que le vidéo (SxxExx / NxNN, multi-épisodes inclus) et le
       même préfixe de série ; le nouveau stem du vidéo est alors imposé au
       sous-titre (ex. « Show S01E01-E02.srt » + « Show - S01E01-E02.mkv »).

    `old_name` : nom précédent du vidéo (si déjà renommé) — sinon déduit du
    chemin. `refs` : liste de (saison, épisode) du vidéo, pour le cas 2.
    Renvoie la liste des paires (ancien_chemin, nouveau_chemin)."""
    video_path = os.path.abspath(video_path)
    d = os.path.dirname(video_path)
    base = old_name or os.path.basename(video_path)
    new_video_name = new_video_name or base
    new_stem = Path(new_video_name).stem
    old_stem_l = Path(base).stem.lower()
    old_prefix = _norm_prefix(base)
    if not refs:
        refs = episode_refs(base)
    renames = []
    try:
        entries = list(os.scandir(d))
    except OSError:
        return renames
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        ext = Path(entry.name).suffix.lower()
        if ext not in SUBTITLE_EXT:
            continue
        sub_stem_l = Path(entry.name).stem.lower()
        # Cas 1 : même stem que l'ancien nom du vidéo.
        if sub_stem_l == old_stem_l:
            target = os.path.join(d, new_stem + ext)
            if os.path.abspath(entry.path) == target:
                continue
            if not dry_run:
                try:
                    os.replace(entry.path, target)
                except OSError:
                    pass
            renames.append((entry.path, target))
            continue
        # Cas 2 : le sous-titre porte exactement les mêmes marqueurs
        # d'épisode que le vidéo et le même préfixe de série → on lui
        # impose le nouveau stem.
        if not refs:
            continue
        refs_sub = episode_refs(entry.name)
        if not refs_sub or set(refs_sub) != set(refs):
            continue
        sub_prefix = _norm_prefix(entry.name)
        if sub_prefix and old_prefix and sub_prefix != old_prefix:
            continue
        target = os.path.join(d, new_stem + ext)
        if os.path.abspath(entry.path) == target:
            continue
        if not dry_run:
            try:
                os.replace(entry.path, target)
            except OSError:
                pass
        renames.append((entry.path, target))
    return renames


def catchup_subtitles_for_folder(folder, videos, dry_run=False):
    """Rattrapage folder-wide des sous-titres (scan récursif du dossier série).

    Un dossier de série = une seule série : chaque sous-titre portant des
    marqueurs d'épisode est renommé pour porter le stem du vidéo correspondant
    (même stem), quel que soit le sous-dossier (SRT à la racine, vidéo dans
    « Season 1/ », etc.). Idempotent : un SRT déjà au bon nom est ignoré.

    `videos` : liste de (nom_final_video, refs) — refs = [(s, e), ...].
    Renvoie la liste des paires (ancien_chemin, nouveau_chemin)."""
    by_refs = {}
    for name, refs in videos:
        if not refs:
            continue
        by_refs.setdefault(frozenset(refs), Path(name).stem)
    renames = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if not x.startswith('.')]
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in SUBTITLE_EXT:
                continue
            refs_sub = episode_refs(fname)
            if not refs_sub:
                continue
            new_stem = by_refs.get(frozenset(refs_sub))
            if not new_stem:
                continue
            sub_path = os.path.join(root, fname)
            target = os.path.join(root, new_stem + ext)
            if os.path.abspath(sub_path) == os.path.abspath(target):
                continue
            if not dry_run:
                try:
                    os.replace(sub_path, target)
                except OSError:
                    pass
            renames.append((sub_path, target))
    return renames


def episode_is_upcoming(episode):
    """Un épisode absent du disque est « à venir » s'il n'a pas encore été
    diffusé : date de diffusion absente (TBA) ou postérieure à aujourd'hui.
    Dès que la date est passée (airdate <= aujourd'hui), il devient manquant."""
    airdate = str(episode.get('airdate') or '').strip()
    if not airdate:
        return True
    today = time.strftime('%Y-%m-%d')
    return airdate[:10] > today


def series_episodes_cached(series_id):
    """Lit la liste d'épisodes depuis le cache uniquement (aucune requête TVDB)."""
    cached = db.series_episodes_get(series_id)
    if cached and cached.get('episodes'):
        return cached['episodes']
    return []


def ensure_library_series_episodes():
    """Pré-remplit le cache TVDB des épisodes pour toutes les séries de la librairie TV."""
    tv_root = os.path.abspath(state.config.get('_tv_output_path') or state.config.get('tv_output_path') or '')
    if not tv_root or not os.path.isdir(tv_root):
        return
    try:
        for entry in os.scandir(tv_root):
            if entry.is_dir(follow_symlinks=False):
                m = re.search(r'\[tvdbid-([0-9]+)\]', entry.name, re.I)
                if m:
                    tvdb_series_episodes(m.group(1), entry.path)
    except Exception as e:
        logger.warning(f"Library series episodes prefill error: {e}")
