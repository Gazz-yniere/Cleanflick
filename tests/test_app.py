import os
import time



def test_index_requires_login(app_client, monkeypatch):
    monkeypatch.setenv('CLEANFLICK_PASSWORD', 'pw')
    r = app_client.get('/')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_login_flow(app_client, monkeypatch):
    monkeypatch.setenv('CLEANFLICK_PASSWORD', 'pw')
    r = app_client.post('/login', data={'password': 'wrong'}, follow_redirects=False)
    assert r.status_code == 200
    r = app_client.post('/login', data={'password': 'pw'}, follow_redirects=False)
    assert r.status_code == 302
    r = app_client.get('/')
    assert r.status_code == 200


def test_index_open_without_password(app_client):
    assert app_client.get('/').status_code == 200


def test_health_endpoint(app_client):
    r = app_client.get('/health')
    assert r.status_code == 200
    assert r.get_json() == {'status': 'ok'}


def test_api_scan_empty_folder(app_client):
    r = app_client.get('/api/scan')
    assert r.status_code == 200
    assert r.get_json() == []


def test_api_scan_lists_videos(app_client, tmp_path):
    dl = tmp_path / 'downloads'
    (dl / 'Movie 2020 1080p.mkv').write_bytes(b'x' * 100)
    (dl / 'Show.S01E01.mkv').write_bytes(b'x' * 100)
    (dl / 'notes.txt').write_text('hello')
    r = app_client.get('/api/scan')
    data = r.get_json()
    assert r.status_code == 200
    names = sorted(f['filename'] for f in data)
    assert names == ['Movie 2020 1080p.mkv', 'Show.S01E01.mkv']
    by_name = {f['filename']: f for f in data}
    assert by_name['Show.S01E01.mkv']['media_type'] == 'tv'
    assert by_name['Show.S01E01.mkv']['season'] == 1
    assert by_name['Show.S01E01.mkv']['episode'] == 1
    assert by_name['Movie 2020 1080p.mkv']['media_type'] == 'movie'
    assert by_name['Movie 2020 1080p.mkv']['year'] == 2020


def test_api_rename(app_client, tmp_path):
    dl = tmp_path / 'downloads'
    src = dl / 'old name.mkv'
    src.write_bytes(b'data')
    r = app_client.post('/api/rename', json={
        'path': str(src), 'new_name': 'New Name.mkv'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert (dl / 'New Name.mkv').exists()
    assert not src.exists()
    r = app_client.post('/api/rename', json={'path': str(dl / 'absent.mkv'), 'new_name': 'x.mkv'})
    assert r.status_code == 400


def test_api_history_and_revert(app_client, tmp_path):
    dl = tmp_path / 'downloads'
    src = dl / 'a.mkv'
    src.write_bytes(b'data')
    r = app_client.post('/api/rename', json={'path': str(src), 'new_name': 'b.mkv'})
    assert r.get_json()['success'] is True

    r = app_client.get('/api/history')
    history = r.get_json()
    assert len(history) == 1
    entry = history[0]
    assert entry['op'] == 'rename'
    assert entry['revert_status'] == 'available'
    assert entry['can_revert'] is True
    assert entry['is_reverted'] is False

    r = app_client.post('/api/revert', json={'id': entry['id']})
    assert r.status_code == 200
    assert r.get_json()['success'] is True
    assert src.exists()
    assert not (dl / 'b.mkv').exists()

    r = app_client.get('/api/history')
    history = r.get_json()
    assert len(history) == 2
    original = next(e for e in history if e['op'] == 'rename')
    assert original['revert_status'] == 'reverted'
    assert original['can_revert'] is False


def test_api_history_clear(app_client):
    r = app_client.post('/api/history/clear')
    assert r.status_code == 200
    assert app_client.get('/api/history').get_json() == []


def test_api_move_and_progress(app_client, tmp_path):
    dl = tmp_path / 'downloads'
    src = dl / 'movie.mkv'
    src.write_bytes(b'z' * 4096)
    r = app_client.post('/api/move', json={'path': str(src), 'media_type': 'movie'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    job_id = body['job_id']
    assert body['new_name'] == 'movie.mkv'

    deadline = time.time() + 10
    prog = None
    while time.time() < deadline:
        prog = app_client.get(f"/api/move-progress/{job_id}").get_json()
        if prog.get('finished'):
            break
        time.sleep(0.1)
    assert prog is not None
    assert prog['finished'] is True
    assert prog['percent'] == 100
    assert not src.exists()
    dest = tmp_path / 'out' / 'movies' / 'movie.mkv'
    assert dest.exists()
    assert dest.read_bytes() == b'z' * 4096

    r = app_client.get('/api/history')
    assert any(e['op'] == 'move' for e in r.get_json())


def test_api_move_missing_source(app_client, tmp_path):
    r = app_client.post('/api/move', json={'path': str(tmp_path / 'nope.mkv'), 'media_type': 'movie'})
    assert r.status_code == 400


def test_api_move_tv_creates_series_folder(app_client, tmp_path):
    dl = tmp_path / 'downloads'
    src = dl / 'My Show - S01E01 - Pilot.mkv'
    src.write_bytes(b'data')
    r = app_client.post('/api/move', json={'path': str(src), 'media_type': 'tv'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert 'My Show' in body['new_path']
    deadline = time.time() + 10
    while time.time() < deadline:
        prog = app_client.get(f"/api/move-progress/{body['job_id']}").get_json()
        if prog.get('finished'):
            break
        time.sleep(0.1)
    assert prog['finished'] is True
    assert os.path.exists(body['new_path'])


def test_api_move_progress_unknown_job(app_client):
    r = app_client.get('/api/move-progress/unknown-job')
    assert r.status_code == 200
    assert r.get_json()['finished'] is True


def test_api_config_get_post(app_client):
    r = app_client.get('/api/config')
    assert r.status_code == 200
    cfg = r.get_json()
    assert cfg['movie_format']
    r = app_client.post('/api/config', json={**cfg, 'movie_format': '{n} ({y}) [imdbid-{imdb}]'})
    assert r.status_code == 200
    assert app_client.get('/api/config').get_json()['movie_format'] == '{n} ({y}) [imdbid-{imdb}]'


def test_api_search_auto_cache(app_client, monkeypatch):
    from src import state
    fake = {'media_type': 'movie', 'results': [{'id': 1, 'title': 'Fake', 'year': 2020, 'imdb_id': ''}]}
    calls = []

    def fake_search(title, year=None, filename='', season=None, episode=None, media_hint=''):
        calls.append(title)
        return fake

    monkeypatch.setattr(state.api_handler, 'search_auto', fake_search)
    r = app_client.post('/api/search/auto', json={'title': 'Fake', 'year': 2020})
    assert r.status_code == 200
    body = r.get_json()
    assert body['results'][0]['title'] == 'Fake'
    assert body['cache_source'] == 'tvdb'
    r = app_client.post('/api/search/auto', json={'title': 'Fake', 'year': 2020})
    assert r.get_json()['cache_source'] in ('params', 'file')
    assert len(calls) == 1


def test_api_search_movie_force_refresh(app_client, monkeypatch):
    from src import state
    calls = []

    def fake_search(title, year=None):
        calls.append(title)
        return [{'id': 2, 'title': 'Fresh', 'year': 2020, 'imdb_id': ''}]

    monkeypatch.setattr(state.api_handler, 'search_movie', fake_search)
    r = app_client.post('/api/search/movie', json={'title': 'Fresh'})
    assert r.get_json()['results'][0]['title'] == 'Fresh'
    r = app_client.post('/api/search/movie', json={'title': 'Fresh', 'force_refresh': True})
    assert r.get_json()['results'][0]['title'] == 'Fresh'
    assert len(calls) == 2


def test_api_search_tv(app_client, monkeypatch):
    from src import state
    monkeypatch.setattr(state.api_handler, 'search_tv',
                        lambda title, year=None: [{'id': 3, 'title': 'Show', 'year': 2020, 'imdb_id': ''}])
    r = app_client.post('/api/search/tv', json={'title': 'Show', 'season': 1, 'episode': 1})
    assert r.status_code == 200
    assert r.get_json()['results'][0]['title'] == 'Show'


def test_api_details_cache(app_client, monkeypatch):
    from src import state
    calls = []

    def fake_details(movie_id, source='tvdb'):
        calls.append(movie_id)
        return {'id': movie_id, 'title': 'Details', 'year': '2020', 'source': 'tvdb'}

    monkeypatch.setattr(state.api_handler, 'get_movie_details', fake_details)
    r = app_client.get('/api/movie/123')
    assert r.status_code == 200
    assert r.get_json()['title'] == 'Details'
    r = app_client.get('/api/movie/123')
    assert r.get_json()['cache_source'] == 'details_cache'
    assert len(calls) == 1


def test_api_usage(app_client):
    r = app_client.get('/api/usage')
    assert r.status_code == 200
    assert isinstance(r.get_json(), dict)


def test_api_unauthorized_without_session(app_client, monkeypatch):
    monkeypatch.setenv('CLEANFLICK_PASSWORD', 'pw')
    r = app_client.get('/api/scan')
    assert r.status_code == 401


def test_library_episode_inherits_series_poster(app_client):
    """Épisode sans poster dans le cache : il hérite du poster de la série
    (dossier [tvdbid-XXX]) depuis le cache de recherche, sans requête API."""
    import time

    from src import db
    from src import cache as cache_mod

    tv_root = 'out/tv'
    series_dir = f"{tv_root}/Show [tvdbid-123]"
    season_dir = f"{series_dir}/Season 1"
    os.makedirs(season_dir, exist_ok=True)
    ep_path = f"{season_dir}/Show - S01E01 - Pilot.mkv"
    with open(ep_path, 'wb') as f:
        f.write(b'x')

    src_dir = 'downloads'
    os.makedirs(src_dir, exist_ok=True)
    src_file = f"{src_dir}/Show [tvdbid-123].mkv"
    with open(src_file, 'wb') as f:
        f.write(b'x')

    fp = cache_mod.file_fingerprint(src_file)
    results = [{'id': 123, 'id_tvdb': '123', 'title': 'Show', 'year': 2020,
                'imdb_id': '', 'poster': 'https://img.test/show.jpg'}]
    db.file_cache_set(fp['fkey'], src_file, 1, int(time.time()), 'tv', results)

    r = app_client.get(f"/api/library?path={tv_root}")
    d = r.get_json()
    series = [e for e in d['entries'] if e['name'] == 'Show [tvdbid-123]'][0]
    assert series['meta'] and series['meta'].get('poster') == 'https://img.test/show.jpg'

    r2 = app_client.get(f"/api/library?path={season_dir}")
    ep = [e for e in r2.get_json()['entries'] if e['name'].endswith('.mkv')][0]
    assert ep['meta'] is not None
    assert ep['meta'].get('poster') == 'https://img.test/show.jpg'
    assert ep['meta'].get('episode') == 'S01E01'


def test_library_episode_poster_from_omdb_cache(app_client, monkeypatch):
    """Le poster de la série provient du cache OMDb (pas du cache de recherche) :
    l'épisode sans meta doit quand même l'afficher."""
    from src import db
    from src.api import omdb

    # Le cache mémoire de résolution TVDB→IMDb est partagé entre tests : on le vide.
    monkeypatch.setattr(omdb, '_SERIES_META_MEM', {})

    tv_root = 'out/tv'
    series_dir = f"{tv_root}/Some Series [tvdbid-777]"
    season_dir = f"{series_dir}/S01"
    os.makedirs(season_dir, exist_ok=True)
    ep_path = f"{season_dir}/Some Series - S01E01 - Pilot.mkv"
    with open(ep_path, 'wb') as f:
        f.write(b'x')

    db.series_meta_set('777', 'tt0111111', {})
    db.omdb_set('tt0111111', {'imdbRating': '8.0', 'Poster': 'https://img.test/omdb.jpg'})

    r = app_client.get(f"/api/library?path={season_dir}")
    ep = [e for e in r.get_json()['entries'] if e['name'].endswith('.mkv')][0]
    assert ep['meta'] is not None
    assert ep['meta'].get('poster') == 'https://img.test/omdb.jpg'
