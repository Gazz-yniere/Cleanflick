"""Bibliothèque : tailles de dossiers, épisodes de séries, présence sur disque."""
import os
import re
import time
import logging
from pathlib import Path

from . import db, state

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


def tvdb_series_episodes(series_id):
    """Liste complète des épisodes TVDB (toutes saisons), mise en cache 24h."""
    cached = db.series_episodes_get(series_id)
    stale = None
    order = 'default'
    try:
        meta = (db.series_meta_get(str(series_id)) or {}).get('data') or {}
        configured = str(meta.get('episode_order') or state.config.get('tvdb_episode_order') or 'auto').lower()
        types = meta.get('season_types') or []
        has_netflix = any('netflix' in str(t.get('alternateName') or '').lower()
                          for t in types if isinstance(t, dict))
        if configured in ('alternate', 'netflix') or (configured == 'auto' and has_netflix):
            order = 'alternate'
    except Exception:
        pass
    if cached and cached.get('episodes'):
        stale = cached['episodes']
    if cached and cached.get('episodes') and cached.get('order', 'default') == order and len(cached['episodes']) < 500:
        # Cache obsolète sans airdate (précédent au split manquant/à venir) : on
        # re-récupère pour que la date de diffusion soit disponible.
        if any('airdate' not in e for e in cached['episodes']):
            stale = cached['episodes']
        else:
            return cached['episodes']
    try:
        res = state.api_handler.get_series_episodes(series_id, season_type=order)
        raw = [{'s': e.get('s'), 'e': e.get('e'), 'title': e.get('title', ''), 'airdate': e.get('airdate', '')}
               for e in res.get('episodes', []) if e.get('s') is not None and e.get('e') is not None]
        # Déduplication par (saison, episode) : la réponse TVDB peut contenir des doublons.
        episodes = []
        seen = set()
        for e in raw:
            key = (e['s'], e['e'])
            if key in seen:
                continue
            seen.add(key)
            episodes.append(e)
        if episodes:
            db.series_episodes_set(series_id, {'episodes': episodes, 'order': order})
            return episodes
    except Exception as e:
        logger.warning(f"Missing-episodes TVDB fetch error for {series_id}: {e}")
    return stale or []


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
                    tvdb_series_episodes(m.group(1))
    except Exception as e:
        logger.warning(f"Library series episodes prefill error: {e}")
