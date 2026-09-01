"""État partagé de l'application (config, scanner, handlers, progression, flux SSE).

Initialisé une seule fois par `init()` (appelé dans `create_app`). Les modules
acèdent à l'état via `state.config`, `state.api_handler`, etc. (attributs du
module, pas `from src.state import config`, pour que la réinitialisation soit
visible partout)."""
import threading

from .scanner import MediaScanner
from .api.handler import APIHandler

config = None
scanner = None
api_handler = None
scanned_files = []
move_progress = {}
scan_clients = set()
scan_watch_lock = threading.Lock()
scan_last_snapshot = None


def init(cfg):
    global config, scanner, api_handler
    config = cfg
    scanner = MediaScanner(
        cfg['_input_path'],
        [cfg.get('_movie_output_path'), cfg.get('_tv_output_path')],
    )
    api_handler = APIHandler(cfg)
