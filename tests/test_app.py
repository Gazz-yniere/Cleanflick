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


def test_library_fast_render_skips_recursive_tv_work(app_client, tmp_path, monkeypatch):
    from src import library as library_mod
    from src.routes import library as library_route

    tv_root = tmp_path / 'out' / 'tv'
    series = tv_root / 'Example Show (2024) [tvdbid-123]'
    season = series / 'Season 01'
    season.mkdir(parents=True)
    (season / 'Example Show - S01E01.mkv').write_bytes(b'x')

    def fail_recursive(*args, **kwargs):
        raise AssertionError('recursive size calculation must be deferred')

    monkeypatch.setattr(library_mod, 'folder_size', fail_recursive)
    monkeypatch.setattr(library_route, '_series_ep_counts', fail_recursive)

    response = app_client.get('/api/library?fast=1')

    assert response.status_code == 200
    data = response.get_json()
    assert data['entries'][0]['name'] == 'tv'
    assert data['entries'][0]['size'] == 0

    response = app_client.get('/api/library', query_string={
        'path': str(series), 'type': 'tv', 'fast': '1'})
    assert response.status_code == 200
    assert response.get_json()['entries'][0]['name'] == 'Season 01'


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


def test_api_search_auto_uses_generic_movie_cache(app_client, monkeypatch):
    from src import db, state
    from src.routes.search import params_cache_key

    db.cache_set(params_cache_key({'type': 'movie', 'title': 'Demolition', 'year': 2015}), 'movie', [
        {'id': 123, 'title': 'Demolition', 'year': 2015, 'imdb_id': ''},
    ])

    def fail_search(*args, **kwargs):
        raise AssertionError('TVDB ne doit pas être appelé quand le cache générique existe')

    monkeypatch.setattr(state.api_handler, 'search_auto', fail_search)
    response = app_client.post('/api/search/auto', json={
        'title': 'Demolition', 'year': 2015, 'filename': 'Demolition.2015.mkv',
        'media_hint': 'movie', 'path': '',
    })

    assert response.status_code == 200
    assert response.get_json()['cache_source'] == 'params'
    assert response.get_json()['results'][0]['title'] == 'Demolition'


def test_library_tvdb_link_refreshes_canonical_slug(app_client, monkeypatch):
    from src import db, state

    class FakeTvdb:
        def get_series_details(self, series_id):
            assert series_id == 450686
            return {'id': series_id, 'imdbid': 'tt32063523',
                    'title': 'Bet', 'tvdb_slug': 'kakegurui-live-action'}

    state.api_handler.tvdb = FakeTvdb()
    response = app_client.get('/api/library/tvdb-link/450686')

    assert response.status_code == 302
    assert response.headers['Location'] == 'https://www.thetvdb.com/series/kakegurui-live-action'
    cached = db.series_meta_get('450686')
    assert cached['data']['tvdb_slug'] == 'kakegurui-live-action'

    def fail_tvdb(*args, **kwargs):
        raise AssertionError('TVDB ne doit pas être rappelé si le slug est en cache')

    state.api_handler.tvdb.get_series_details = fail_tvdb
    response = app_client.get('/api/library/tvdb-link/450686')
    assert response.status_code == 302
    assert response.headers['Location'] == 'https://www.thetvdb.com/series/kakegurui-live-action'


def test_library_tvdb_orders_accepts_manual_search_post(app_client, monkeypatch):
    from src import state

    class FakeTvdb:
        def get_series_details(self, series_id):
            return {'id': series_id, 'seasonTypes': [
                {'type': 'official', 'name': 'Aired Order'},
                {'type': 'alternate', 'alternateName': 'Netflix Order'},
            ]}

    state.api_handler.tvdb = FakeTvdb()
    response = app_client.post('/api/library/tvdb-orders/327417')

    assert response.status_code == 200
    assert [order['value'] for order in response.get_json()['orders']] == ['official', 'alternate']


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


def test_library_missing_multi_episode_file(app_client, monkeypatch):
    """Un fichier multi-épisodes (S01E01-E02) couvre ses deux épisodes :
    ni E01 ni E02 n'apparaissent dans les épisodes manquants."""
    from src import db

    series_dir = "out/tv/The Legend of Korra (2012) [tvdbid-251085]"
    os.makedirs(series_dir, exist_ok=True)
    with open(f"{series_dir}/Korra - S01E01-E02 - Welcome & Leaf.avi", 'wb') as f:
        f.write(b'x')
    with open(f"{series_dir}/Korra - S01E03 - The Revelation.avi", 'wb') as f:
        f.write(b'x')

    db.series_episodes_set('251085', {'episodes': [
        {'s': 1, 'e': 1, 'title': 'Welcome to Republic City'},
        {'s': 1, 'e': 2, 'title': 'A Leaf in the Wind'},
        {'s': 1, 'e': 3, 'title': 'The Revelation'},
    ]})

    r = app_client.get(f"/api/library/missing?path={series_dir}")
    d = r.get_json()
    assert r.status_code == 200
    assert d['count'] == 0
    assert d['missing'] == []


def test_library_missing_single_episode_file(app_client, monkeypatch):
    """Contrôle : sans plage, l'épisode absent est bien signalé manquant.
    L'airdate est dans le passé : l'épisode est diffusé mais absent du disque."""
    from src import db

    series_dir = "out/tv/Show X [tvdbid-555]"
    os.makedirs(series_dir, exist_ok=True)
    with open(f"{series_dir}/Show X - S01E01 - Pilot.mkv", 'wb') as f:
        f.write(b'x')

    db.series_episodes_set('555', {'episodes': [
        {'s': 1, 'e': 1, 'title': 'Pilot', 'airdate': '2020-01-01'},
        {'s': 1, 'e': 2, 'title': 'Second', 'airdate': '2020-01-08'},
    ]})

    r = app_client.get(f"/api/library/missing?path={series_dir}")
    d = r.get_json()
    assert d['count'] == 1
    assert d['missing'][0]['label'] == 'S01E02'
    assert d['upcoming_count'] == 0


def test_library_upcoming_episode_not_missing(app_client, monkeypatch):
    """Épisode absent du disque avec une date de diffusion future (ou TBA) :
    classifié « à venir » (jaune), pas « manquant » (rouge)."""
    from src import db

    series_dir = "out/tv/Show Y [tvdbid-777]"
    os.makedirs(series_dir, exist_ok=True)

    db.series_episodes_set('777', {'episodes': [
        {'s': 1, 'e': 1, 'title': 'Pilot', 'airdate': '2020-01-01'},
        {'s': 2, 'e': 1, 'title': 'Future', 'airdate': '2999-01-01'},
        {'s': 2, 'e': 2, 'title': 'TBA', 'airdate': ''},
    ]})

    r = app_client.get(f"/api/library/missing?path={series_dir}")
    d = r.get_json()
    assert d['missing_count'] == 1
    assert d['missing'][0]['label'] == 'S01E01'
    assert d['upcoming_count'] == 2
    labels = {u['label'] for u in d['upcoming']}
    assert labels == {'S02E01', 'S02E02'}


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


def test_library_rename_folder_series_sync(app_client, monkeypatch, tmp_path):
    """Renommage manuel d'un dossier série : fichiers + dossier renommés de
    façon synchrone ; le parsing TVDB (cache épisodes) passe en arrière-plan."""
    import time

    from src import db
    from src import library
    from src.api import omdb

    monkeypatch.setattr(omdb, '_SERIES_META_MEM', {})
    monkeypatch.setattr(omdb, 'resolve_series_imdb', lambda *a, **k: 'tt9999999')
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: None)
    episodes = [{'s': 1, 'e': 1, 'title': 'Pilot'}, {'s': 1, 'e': 2, 'title': 'Second'}]
    monkeypatch.setattr(library, 'tvdb_series_episodes',
                        lambda sid: db.series_episodes_set(sid, {'episodes': episodes}) or episodes)

    tv_root = str(tmp_path / 'out' / 'tv')
    folder = os.path.join(tv_root, 'bad folder')
    os.makedirs(folder)
    f1 = os.path.join(folder, 'Breaking Bad S01E01.mkv')
    f2 = os.path.join(folder, 'Breaking Bad S01E02.mkv')
    for p in (f1, f2):
        with open(p, 'wb') as f:
            f.write(b'x')
    db.series_meta_set('81189', 'tt0903747', {'title': 'Breaking Bad', 'year': '2008'})

    r = app_client.post('/api/library/rename-folder-series', json={
        'path': folder, 'tvdbid': '81189', 'title': 'Breaking Bad', 'year': 2008,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert body['background'] is True
    assert body['imdb'] == 'tt0903747'
    assert len(body['renames']) == 2
    new_folder = body['new_path']
    assert os.path.isdir(new_folder)
    assert not os.path.isdir(folder)
    # Le dossier a été renommé (le format par défaut ne porte pas d'ID).
    assert body['new_name'] == 'Breaking Bad'

    # Le parsing TVDB + 2e passe de renommage se font en arrière-plan :
    # on attend que les titres d'épisodes soient complétés dans les noms.
    deadline = time.time() + 10
    names = []
    while time.time() < deadline:
        names = sorted(os.listdir(new_folder))
        if all('- Pilot.mkv' in n or '- Second.mkv' in n for n in names):
            break
        time.sleep(0.1)
    assert names == ['Breaking Bad - S01E01 - Pilot.mkv',
                     'Breaking Bad - S01E02 - Second.mkv']
    # Le parsing TVDB s'est exécuté en arrière-plan (cache BDD rempli).
    cached = db.series_episodes_get('81189')
    assert cached and len(cached['episodes']) == 2


def _wait_names(folder, pred, timeout=10):
    deadline = time.time() + timeout
    names = []
    while time.time() < deadline:
        names = sorted(os.listdir(folder))
        if pred(names):
            return names
        time.sleep(0.1)
    return names


def test_rename_folder_series_subtitles_follow(app_client, monkeypatch, tmp_path):
    """Sous-titres (propre + orphelin) renommés en parallèle du vidéo."""
    from src import db
    from src.api import omdb

    monkeypatch.setattr(omdb, '_SERIES_META_MEM', {})
    monkeypatch.setattr(omdb, 'resolve_series_imdb', lambda *a, **k: 'tt9999999')
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: None)
    # Cache TVDB pré-rempli : le 1er passage renomme déjà avec les titres.
    db.series_episodes_set('555', {'episodes': [
        {'s': 1, 'e': 1, 'title': 'Pilot'},
        {'s': 1, 'e': 2, 'title': 'Second'},
        {'s': 1, 'e': 3, 'title': 'Third'}]})

    tv_root = str(tmp_path / 'out' / 'tv')
    folder = os.path.join(tv_root, 'bad folder')
    os.makedirs(folder)
    files = [
        'Show S01E01.mkv', 'Show S01E01.srt',          # vidéo + SRT propre
        'Show S01E02-E03.mkv', 'Show S01E02-E03.srt',   # multi-ép + SRT orphelin
    ]
    for n in files:
        (tmp_path / 'out' / 'tv' / 'bad folder' / n).write_bytes(b'x')

    r = app_client.post('/api/library/rename-folder-series', json={
        'path': folder, 'tvdbid': '555', 'title': 'Show', 'year': 2020,
    })
    body = r.get_json()
    assert body['success'] is True
    new_folder = body['new_path']
    to_names = [x['to'] for x in body['renames']]
    assert 'Show - S01E01 - Pilot.mkv' in to_names
    assert 'Show - S01E01 - Pilot.srt' in to_names
    assert 'Show - S01E02-E03 - Second & Third.mkv' in to_names
    assert 'Show - S01E02-E03 - Second & Third.srt' in to_names
    names = _wait_names(new_folder, lambda ns:
        all(x in ns for x in ('Show - S01E01 - Pilot.mkv', 'Show - S01E01 - Pilot.srt',
                              'Show - S01E02-E03 - Second & Third.mkv',
                              'Show - S01E02-E03 - Second & Third.srt')))
    assert len(names) == 4


def test_rename_folder_series_s00_specials(app_client, monkeypatch, tmp_path):
    """S00 : renommé seulement si la série a des épisodes spéciaux en TVDB."""
    from src import db
    from src.api import omdb

    monkeypatch.setattr(omdb, '_SERIES_META_MEM', {})
    monkeypatch.setattr(omdb, 'resolve_series_imdb', lambda *a, **k: 'tt9999999')
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: None)
    # Série 556 : pas d'épisode S00 dans TVDB.
    db.series_episodes_set('556', {'episodes': [{'s': 1, 'e': 1, 'title': 'Pilot'}]})
    # Série 557 : a un épisode S00.
    db.series_episodes_set('557', {'episodes': [
        {'s': 0, 'e': 1, 'title': 'Special'}, {'s': 1, 'e': 1, 'title': 'Pilot'}]})

    tv_root = str(tmp_path / 'out' / 'tv')
    folder = os.path.join(tv_root, 'bad folder')
    os.makedirs(folder)
    (tmp_path / 'out' / 'tv' / 'bad folder' / 'Show S00E01.mkv').write_bytes(b'x')
    (tmp_path / 'out' / 'tv' / 'bad folder' / 'Show S01E01.mkv').write_bytes(b'x')

    r = app_client.post('/api/library/rename-folder-series', json={
        'path': folder, 'tvdbid': '556', 'title': 'Show', 'year': 2020,
    })
    new_folder = r.get_json()['new_path']
    names = _wait_names(new_folder, lambda ns: any('S01E01 - Pilot.mkv' in n for n in ns))
    # S00 absent du cache TVDB → inchangé ; S01 renommé.
    assert 'Show S00E01.mkv' in names
    assert 'Show - S01E01 - Pilot.mkv' in names

    # Avec un épisode S00 dans TVDB : le S00 est renommé.
    folder2 = os.path.join(tv_root, 'bad folder 2')
    os.makedirs(folder2)
    (tmp_path / 'out' / 'tv' / 'bad folder 2' / 'ShowB S00E01.mkv').write_bytes(b'x')
    r = app_client.post('/api/library/rename-folder-series', json={
        'path': folder2, 'tvdbid': '557', 'title': 'ShowB', 'year': 2020,
    })
    new_folder2 = r.get_json()['new_path']
    names2 = _wait_names(new_folder2, lambda ns: any('S00E01 - Special.mkv' in n for n in ns))
    assert 'ShowB - S00E01 - Special.mkv' in names2


def test_rename_subtitles_catchup_pass(app_client, tmp_path):
    """Passe de rattrapage : vidéo déjà au bon nom, SRT orphelin renommé."""
    from src import db
    from src.routes.library import _folder_series_rename

    tv_root = str(tmp_path / 'out' / 'tv')
    folder = os.path.join(tv_root, 'Show [tvdbid-777]')
    os.makedirs(folder)
    (tmp_path / 'out' / 'tv' / 'Show [tvdbid-777]' / 'Show - S01E01.mkv').write_bytes(b'x')
    (tmp_path / 'out' / 'tv' / 'Show [tvdbid-777]' / 'Show S01E01.srt').write_bytes(b'x')
    db.series_episodes_set('777', {'episodes': [{'s': 1, 'e': 1, 'title': 'Pilot'}]})

    renames = _folder_series_rename(folder, 'Show', 2020, '777', '', '', {})
    assert (tmp_path / 'out' / 'tv' / 'Show [tvdbid-777]' / 'Show - S01E01 - Pilot.srt').exists()
    assert not (tmp_path / 'out' / 'tv' / 'Show [tvdbid-777]' / 'Show S01E01.srt').exists()
    # Le vidéo (déjà correct) n'est pas renommé, seul le SRT est dans renames.
    assert any(r['from'] == 'Show S01E01.srt' and r['to'] == 'Show - S01E01 - Pilot.srt'
               for r in renames)


def test_api_rename_subtitles_follow(app_client, tmp_path):
    """/api/rename renomme aussi le SRT au même stem."""
    dl = tmp_path / 'downloads'
    (dl / 'Show S01E01.mkv').write_bytes(b'x')
    (dl / 'Show S01E01.srt').write_bytes(b'x')
    r = app_client.post('/api/rename', json={
        'path': str(dl / 'Show S01E01.mkv'), 'new_name': 'Show - S01E01 - Pilot.mkv'})
    assert r.status_code == 200
    assert (dl / 'Show - S01E01 - Pilot.mkv').exists()
    assert (dl / 'Show - S01E01 - Pilot.srt').exists()
    assert not (dl / 'Show S01E01.srt').exists()


def test_rename_subtitles_catchup_across_subfolders(tmp_path):
    """Rattrapage folder-wide : SRT à la racine du dossier série, vidéo dans
    un sous-dossier (Season 1/) — le SRT prend le nom final du vidéo."""
    from src import db
    from src.routes.library import _folder_series_rename

    series = tmp_path / 'out' / 'tv' / 'Show [tvdbid-778]'
    season = series / 'Season 1'
    season.mkdir(parents=True)
    (season / 'Show - S01E01.mkv').write_bytes(b'x')
    (series / 'Show S01E01.srt').write_bytes(b'x')
    db.series_episodes_set('778', {'episodes': [{'s': 1, 'e': 1, 'title': 'Pilot'}]})

    renames = _folder_series_rename(str(series), 'Show', 2020, '778', '', '', {})
    assert (series / 'Show - S01E01 - Pilot.srt').exists()
    assert not (series / 'Show S01E01.srt').exists()
    assert any(r['from'] == 'Show S01E01.srt' and r['to'] == 'Show - S01E01 - Pilot.srt'
               for r in renames)
