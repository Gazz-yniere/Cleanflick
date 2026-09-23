"""Création de l'application Flask et enregistrement des blueprints."""
import os

from flask import Flask, jsonify

from .. import config as config_mod
from .. import state
from .. import cache as cache_mod
from .. import watcher
from ..logging_setup import setup_logging

# Racine du projet (2 niveaux au-dessus de ce fichier : src/routes/ -> racine).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def create_app():
    setup_logging()
    app = Flask(
        __name__,
        template_folder=os.path.join(_ROOT, 'templates'),
        static_folder=os.path.join(_ROOT, 'static'),
    )
    app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
    app.config['JSON_SORT_KEYS'] = False

    # Initialise l'état partagé (config, scanner, api_handler).
    state.init(config_mod.load_config())

    # Démarrage des workers de fond (watcher d'entrée + rafraîchissement des caches).
    watcher.start_watcher()
    cache_mod.start_cache_refresh_worker()
    # Nettoyage des caches orphelins (min 6 h d'écart, en fond) + préchauffage
    # des bibliothèques (recherches manquantes + OMDb global des séries).
    # Désactivé sous pytest : les threads balayeraient les chemins de test.
    if not os.environ.get('PYTEST_CURRENT_TEST'):
        cache_mod.run_purge_background()
        cache_mod.start_media_prewarm()

    from . import auth, browse, scan, search, library, files, config, usage
    app.register_blueprint(auth.bp)
    app.register_blueprint(browse.bp)
    app.register_blueprint(scan.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(library.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(config.bp)
    app.register_blueprint(usage.bp)

    @app.route('/health')
    def health():
        return jsonify({'status': 'ok'})

    return app
