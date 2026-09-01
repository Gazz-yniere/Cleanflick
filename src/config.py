"""Chargement / sauvegarde de la configuration (config.json)."""
import json
import os
import platform

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "tvdb_api_key": "", "tvdb_pin": "", "omdb_api_key": "",
    "input_path": "/downloads",
    "movie_output_path": "/downloads/movie",
    "tv_output_path": "/downloads/tv_shows",
    "movie_format": "{n} ({y})",
    "tv_format": "{n} - {s00e00} - {t}",
    "lib_fast_scan": True,
}


def _resolve(path):
    if not path:
        return path
    base = APP_ROOT
    # Sur Linux/Docker : chemin absolu réel → tel quel
    if platform.system() != 'Windows' and os.path.isabs(path):
        return path
    # Sur Windows : chemin absolu Windows (C:\..., D:\...) ou UNC (\\serveur\partage) → tel quel
    if platform.system() == 'Windows' and ((len(path) >= 2 and path[1] == ':') or path.startswith('\\\\')):
        return path
    # Chemin style Unix (/downloads) ou relatif → relatif à l'app
    return os.path.join(base, path.lstrip('/\\'))


def _migrate_legacy_paths(cfg):
    movie_path = cfg.pop('movie_path', None)
    tv_path = cfg.pop('tv_path', None)
    if not cfg.get('input_path'):
        for candidate in (movie_path, tv_path):
            if candidate:
                try:
                    cfg['input_path'] = os.path.commonpath([movie_path, tv_path]) if movie_path and tv_path else os.path.dirname(candidate.rstrip('/\\'))
                    break
                except ValueError:
                    cfg['input_path'] = os.path.dirname(candidate.rstrip('/\\'))
    if movie_path and not cfg.get('movie_output_path'):
        cfg['movie_output_path'] = movie_path
    if tv_path and not cfg.get('tv_output_path'):
        cfg['tv_output_path'] = tv_path


def load_config():
    cfg = json.load(open(CONFIG_FILE, 'r', encoding='utf-8')) if os.path.exists(CONFIG_FILE) else DEFAULT_CONFIG.copy()
    _migrate_legacy_paths(cfg)
    cfg.setdefault('input_path', '/downloads')
    cfg.setdefault('movie_output_path', '/movies')
    cfg.setdefault('tv_output_path', '/tv_shows')
    cfg.setdefault('lib_fast_scan', True)
    cfg['_input_path'] = _resolve(cfg['input_path'])
    cfg['_movie_output_path'] = _resolve(cfg['movie_output_path'])
    cfg['_tv_output_path'] = _resolve(cfg['tv_output_path'])
    save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in cfg.items() if not k.startswith('_')}, f, indent=2, ensure_ascii=False)
