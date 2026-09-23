"""Configuration du logging : couleurs ANSI, format compact, filtre du bruit.

Les couleurs sont émises inconditionnellement (Docker `docker logs` et les
terminaux modernes rendent les codes ANSI ; `colorama.just_fix_windows_console`
active le processeur VT sur les vieux consoles Windows). Désactivable via la
variable d'environnement `NO_COLOR` (ou `NO_COLOR=1`).
"""
import os
import logging


def _color_enabled():
    if os.environ.get('NO_COLOR'):
        return False
    try:
        import colorama
        colorama.just_fix_windows_console()
    except Exception:
        pass
    return True


_CODES = _color_enabled()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _CODES else str(text)


# Couleurs par famille de logger (message normal).
_LOGGER_COLORS = [
    ('src.api.tvdb', 36),    # cyan
    ('src.api.omdb', 35),    # magenta
    ('src.routes', 32),      # vert
    ('werkzeug', 90),        # gris
]


def _name_color(name):
    for prefix, code in _LOGGER_COLORS:
        if name == prefix or name.startswith(prefix + '.') or name.startswith(prefix):
            return code
    return 0  # pas de couleur


class ColoredFormatter(logging.Formatter):
    def format(self, record):
        ts = self.formatTime(record, self.datefmt)
        level = record.levelname
        if level == 'WARNING':
            msg = _c(93, f"{record.name} │ {record.getMessage()}")   # jaune
        elif level in ('ERROR', 'CRITICAL'):
            msg = _c(91, f"{record.name} │ {record.getMessage()}")   # rouge
        else:
            code = _name_color(record.name)
            text = f"{record.name} │ {record.getMessage()}"
            msg = _c(code, text) if code else text
        return f"{ts} │ {msg}"


def _quiet_static(record):
    """Supprime le bruit : fichiers statiques et /health (les /api/* restent)."""
    m = record.getMessage()
    if '/static/' in m or ' /health' in m:
        return False
    return True


def setup_logging(level=logging.INFO):
    root = logging.getLogger()
    root.setLevel(level)
    # Évite les handlers dupliqués si setup appelé plusieurs fois (tests, reloader).
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter(datefmt='%H:%M:%S'))
    root.addHandler(handler)
    # Bruit werkzeug : /static/ + /health masqués.
    werkzeug = logging.getLogger('werkzeug')
    werkzeug.addFilter(_quiet_static)
    werkzeug.setLevel(level)
    # Pas de propagation du double-log (werkzeug est enfant de root).
    werkzeug.propagate = True
