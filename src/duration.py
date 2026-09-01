"""Durée des fichiers vidéo : cache mémoire + base + worker FIFO (scan rapide).

`mediaduration` fait le parse binaire ; ce module gère la mise en cache,
l'invalidation (taille/mtime) et le calcul en arrière-plan non bloquant."""
import os
import time
import threading

from . import db, mediaduration, state

_MEDIA_DUR_CACHE = {}
_MEDIA_DUR_CACHE_KEY = object()  # sentinelle : absent du cache (distinct de None)
_MEDIA_DUR_PENDING = {}     # path -> mtime : file FIFO des durées à calculer (ordre d'affichage)

_dur_queue_lock = threading.Lock()
_dur_worker = None
_MEDIA_DUR_GIVINGUP = {}   # path -> nb de tours de polling sans durée trouvée
_DUR_GIVEUP_MAX = 4        # tours avant de renoncer (spinner levé côté client)
_DUR_UNKNOWN = -1          # valeur renvoyée quand on renonce : durée non lue


def _parse_duration_cached(path, mt, sz):
    """Parse + persistance de la durée ; une durée illisible (None) est aussi
    mise en cache pour ne pas reparsenter sans fin à chaque refresh."""
    minutes = mediaduration.get_duration_minutes(path)
    _MEDIA_DUR_CACHE[(path, mt)] = minutes
    try:
        db.media_dur_set(path, minutes, sz, mt)
    except Exception:
        pass
    return minutes


def get_duration(path):
    """Durée réelle (min) du fichier vidéo, lue en Python pur (Docker-safe),
    mise en cache en mémoire + en base (invalidée si taille/mtime change).
    En mode scan rapide : durée non bloquante (un chargement en arrière-plan
    démarre et la durée est renvoyée au premier accès suivant)."""
    try:
        st = os.stat(path)
        mt = int(st.st_mtime)
        sz = st.st_size
    except Exception:
        return None
    key = (path, mt)
    if key in _MEDIA_DUR_CACHE:
        return _MEDIA_DUR_CACHE[key]
    try:
        cached = db.media_dur_get(path)
        if cached and cached.get('size') == sz and cached.get('mtime') == mt:
            minutes = cached.get('minutes')
            _MEDIA_DUR_CACHE[key] = minutes
            return minutes
    except Exception:
        pass
    if state.config.get('lib_fast_scan'):
        _dur_enqueue(path, mt)
        return None
    return _parse_duration_cached(path, mt, sz)


def duration_for(path):
    """Durée pour l'affichage du scan : cache mémoire d'abord, sinon parse direct
    (non bloquant, sans file) — utilisé par /api/scan."""
    try:
        mt = int(os.path.getmtime(path))
    except Exception:
        return None
    key = (path, mt)
    if key in _MEDIA_DUR_CACHE:
        return _MEDIA_DUR_CACHE[key]
    minutes = mediaduration.get_duration_minutes(path)
    _MEDIA_DUR_CACHE[key] = minutes
    return minutes


def _dur_enqueue(path, mt):
    """Met le chemin dans la file FIFO des durées à calculer et démarre le
    worker unique s'il n'est pas actif. L'ordre d'arrivée correspond à
    l'ordre alphabétique de l'affichage de la liste."""
    global _dur_worker
    with _dur_queue_lock:
        if path in _MEDIA_DUR_PENDING:
            del _MEDIA_DUR_PENDING[path]
        _MEDIA_DUR_PENDING[path] = mt
        if _dur_worker is None or not _dur_worker.is_alive():
            _dur_worker = threading.Thread(target=_dur_worker_loop, daemon=True)
            _dur_worker.start()


def _dur_worker_loop():
    """Worker unique : vide la file une entrée à la fois, dans l'ordre
    d'affichage, pour que les durées s'affichent progressivement au lieu
    d'un scan parallèle de toute la bibliothèque."""
    while True:
        with _dur_queue_lock:
            p = next(iter(_MEDIA_DUR_PENDING), None)
        if p is None:
            time.sleep(0.25)
            continue
        cur = _MEDIA_DUR_PENDING.get(p)
        try:
            st = os.stat(p)
            if int(st.st_mtime) == cur:
                minutes = mediaduration.get_duration_minutes(p)
                _MEDIA_DUR_CACHE[(p, cur)] = minutes
                # None = durée encore introuvable : on ne la persiste pas, pour que
                # le prochain tour (parser amélioré ou fichier complété) puisse
                # retenter le calcul au lieu de rester en attente éternelle.
                if minutes is not None:
                    db.media_dur_set(p, minutes, st.st_size, cur)
        except Exception:
            pass
        with _dur_queue_lock:
            if _MEDIA_DUR_PENDING.get(p) == cur:
                _MEDIA_DUR_PENDING.pop(p, None)


def lib_durations(paths):
    """Résout les durées (minutes) des fichiers de bibliothèque listés au
    retour d'un premier affichage en scan rapide. La durée est lue depuis le
    cache disque (calculée en arrière-plan) ; si absente, le fichier est
    calculé ici au besoin. Retourne {path: minutes|None|_DUR_UNKNOWN}."""
    results = {}
    for p in paths[:300]:
        if not isinstance(p, str) or not p:
            continue
        try:
            st = os.stat(p)
            mt, sz = int(st.st_mtime), st.st_size
            dur = _MEDIA_DUR_CACHE.get((p, mt), _MEDIA_DUR_CACHE_KEY)
            if dur is _MEDIA_DUR_CACHE_KEY:
                cached = db.media_dur_get(p)
                if cached and cached.get('size') == sz and cached.get('mtime') == mt:
                    dur = cached.get('minutes')
                elif p in _MEDIA_DUR_PENDING:
                    # Déjà dans la file de calcul en arrière-plan : on ne rejoue
                    # pas le parse ici, le prochain tour de polling verra la valeur.
                    dur = None
                else:
                    dur = _parse_duration_cached(p, mt, sz)
                _MEDIA_DUR_CACHE[(p, mt)] = dur
            if dur is not None:
                results[p] = dur
                _MEDIA_DUR_GIVINGUP.pop(p, None)
            elif p not in _MEDIA_DUR_PENDING:
                # Pas encore prêt : après quelques tours de polling on renonce
                # (le frontend lève le spinner ; le fichier sera repassé au
                # prochain chargement de la liste plutôt que d'attendre éternel).
                n = _MEDIA_DUR_GIVINGUP.get(p, 0) + 1
                _MEDIA_DUR_GIVINGUP[p] = n
                if n >= _DUR_GIVEUP_MAX:
                    results[p] = _DUR_UNKNOWN
        except Exception:
            continue
    return results
