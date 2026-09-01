"""Surveillance du dossier d'entrée + diffusion temps réel (SSE) + push usage.

Le watcher détecte les ajouts/suppressions de fichiers dans l'input path et
diffuse un événement `scan-refresh` à tous les clients SSE connectés."""
import json
import time
import threading
import logging

from . import db, state

logger = logging.getLogger(__name__)


def _scan_snapshot():
    try:
        files = state.scanner.scan()
        return tuple(sorted(f.path for f in files))
    except Exception as e:
        logger.warning(f"Scan snapshot error: {e}")
        return tuple()


def broadcast_scan_refresh():
    payload = json.dumps({'event': 'scan-refresh', 'ts': time.time()})
    dead = []
    for client in list(state.scan_clients):
        try:
            client.put(payload)
        except Exception:
            dead.append(client)
    for client in dead:
        try:
            state.scan_clients.remove(client)
        except Exception:
            pass


_usage_last_push = [0.0]
_usage_pending = [False]


def _usage_push_now():
    _usage_last_push[0] = time.time()
    _usage_pending[0] = False
    payload = json.dumps({'event': 'usage-refresh', 'ts': time.time()})
    for client in list(state.scan_clients):
        try:
            client.put(payload)
        except Exception:
            pass


def usage_changed(service):
    """Re-pousse l'état des compteurs (throttle 1 s, push différé si besoin)
    pour la UI temps réel."""
    if time.time() - _usage_last_push[0] < 1.0:
        if not _usage_pending[0]:
            _usage_pending[0] = True
            t = threading.Timer(1.0, _usage_push_now)
            t.daemon = True
            t.start()
        return
    _usage_push_now()


def _scan_watcher():
    while True:
        try:
            current = _scan_snapshot()
            with state.scan_watch_lock:
                previous = state.scan_last_snapshot or tuple()
                added = set(current) - set(previous)
                removed = set(previous) - set(current)
                if (added or removed) and current is not None:
                    broadcast_scan_refresh()
                    if added and removed:
                        logger.info(f"Input files changed: +{len(added)} / -{len(removed)}")
                    elif added:
                        logger.info(f"New input files detected: {len(added)}")
                    else:
                        logger.info(f"Input files removed: {len(removed)}")
                state.scan_last_snapshot = current
        except Exception as e:
            logger.warning(f"Input watcher error: {e}")
        time.sleep(1.5)


def start_watcher():
    """Démarrage du watcher d'entrée + écouteur de usage (une seule fois)."""
    db.usage_set_listener(usage_changed)
    threading.Thread(target=_scan_watcher, daemon=True).start()
