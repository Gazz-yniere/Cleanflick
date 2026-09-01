import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """BDD SQLite isolée : db.DB_PATH pointe vers un fichier dans tmp_path."""
    from src import db
    db_path = str(tmp_path / 'test.db')
    monkeypatch.setattr(db, 'DB_PATH', db_path)
    db.init_db()
    return db_path


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """App Flask isolée : BDD temporaire, config de test, pas de mot de passe,
    scan d'un dossier vide, client TVDB mocké (aucun appel réseau)."""
    monkeypatch.chdir(tmp_path)
    with open(tmp_path / 'config.json', 'w', encoding='utf-8') as f:
        json.dump({
            'tvdb_api_key': 'test-tvdb-key',
            'tvdb_pin': '',
            'omdb_api_key': '',
            'input_path': str(tmp_path / 'downloads'),
            'movie_output_path': str(tmp_path / 'out' / 'movies'),
            'tv_output_path': str(tmp_path / 'out' / 'tv'),
            'movie_format': '{n} ({y})',
            'tv_format': '{n} - {s00e00} - {t}',
            'lib_fast_scan': False,
        }, f)
    (tmp_path / 'downloads').mkdir()
    monkeypatch.setenv('CLEANFLICK_PASSWORD', '')
    monkeypatch.setenv('SECRET_KEY', 'test-secret')

    from src import db
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()

    # Mock le client TVDB (aucun appel réseau) : patch là où handler.py l'importe.
    from src.api import handler as api_handler_mod
    monkeypatch.setattr(api_handler_mod, 'TVDBAPIHandler', lambda key, pin=None: object())

    from src import state
    from src.routes import create_app
    app = create_app()
    app.config['TESTING'] = True
    state.scanned_files = []
    state.move_progress.clear()
    with app.test_client() as c:
        yield c
