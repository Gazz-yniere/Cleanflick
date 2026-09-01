"""Bibliothèque : tailles de dossiers, épisodes de séries, présence sur disque."""
import os
import re
import logging
from pathlib import Path

from . import db, state

logger = logging.getLogger(__name__)

_FOLDER_DIR_CACHE = {}      # path -> (mtime, size) : taille de dossier (non récursif)
VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.flv', '.webm'}


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


def tvdb_series_episodes(series_id):
    """Liste complète des épisodes TVDB (toutes saisons), mise en cache 24h."""
    cached = db.series_episodes_get(series_id)
    if cached and cached.get('episodes'):
        return cached['episodes']
    try:
        res = state.api_handler.get_series_episodes(series_id)
        raw = [{'s': e.get('s'), 'e': e.get('e'), 'title': e.get('title', '')}
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
            db.series_episodes_set(series_id, {'episodes': episodes})
            return episodes
    except Exception as e:
        logger.warning(f"Missing-episodes TVDB fetch error for {series_id}: {e}")
    return cached.get('episodes', []) if cached else []


def present_episodes(path):
    """Ensemble des (saison, episode) présents dans un dossier série (scan récursif,
    car les épisodes peuvent être répartis dans des sous-dossiers par saison)."""
    present = set()
    sxe = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if Path(f).suffix.lower() in VIDEO_EXT:
                    m = sxe.search(f)
                    if m:
                        present.add((int(m.group(1)), int(m.group(2))))
    except Exception:
        pass
    return present


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
