import os
import time
from pathlib import Path

import pytest



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


def test_cache_purge_removes_orphans(app_client, tmp_path, monkeypatch):
    from src import db, state
    import time as _time

    # Racines de test avec un dossier série vivant + un chemin mort en cache.
    movie_root = tmp_path / 'out' / 'movies'
    (movie_root / 'Show (2020) [tvdbid-111]').mkdir(parents=True)
    state.config['_movie_output_path'] = str(movie_root)
    state.config['_tv_output_path'] = str(tmp_path / 'out' / 'tv')

    db.file_cache_set('dead', r'C:\nonexistent\file.mkv', 1, 1, 'movie', [])
    db.series_meta_set('111', 'tt111', {'id': '111'})
    db.series_meta_set('222', 'tt222', {'id': '222'})

    r = app_client.post('/api/cache/purge', json={'force': True})
    assert r.status_code == 200
    assert r.get_json()['status'] == 'started'

    deadline = _time.time() + 10
    while _time.time() < deadline and db.meta_get('last_cache_purge', 0) in (None, '', 0):
        _time.sleep(0.1)
    assert db.meta_get('last_cache_purge', 0) not in (None, '', 0), 'purge pas terminée'

    assert db.file_cache_get('dead') is None
    assert db.series_meta_get('111') is not None
    assert db.series_meta_get('222') is None


def test_ensure_series_omdb_dedupes(tmp_db, monkeypatch):
    from src import db
    from src.api import omdb

    calls = []

    def fake_fetch(imdb, title=None, year=None, is_series=False, force=False):
        calls.append(imdb)
        db.omdb_set(imdb, {'imdbRating': '8.0', 'Title': 'X'})
        return db.omdb_get(imdb)

    monkeypatch.setattr(omdb, 'fetch_omdb_single', fake_fetch)
    monkeypatch.setattr(omdb, 'resolve_series_imdb',
                        lambda tvdb_id, title=None, year=None, force=False: 'tt999' if tvdb_id == '111' else '')

    assert omdb.ensure_series_omdb('111') == 'tt999'
    assert db.omdb_get('tt999') is not None
    before = len(calls)
    for _ in range(4):
        omdb.ensure_series_omdb('111')
    assert len(calls) == before, 'fetch OMDb relancé alors que le cache existe'
    assert omdb.ensure_series_omdb('999') == ''


def test_omdb_fetch_title_mismatch_not_stored(tmp_db, monkeypatch, caplog):
    """Garde : si OMDb (recherche par titre) répond pour un autre imdbID, la
    réponse n'est JAMAIS stockée sous notre IMDb (empêche un poster/infos d'un
    autre média de contaminer le cache)."""
    import requests
    from src import db, state
    from src.api import omdb

    state.config = {'omdb_api_key': 'k'}

    fake_resp = {'Response': 'True', 'Title': 'Autre', 'imdbID': 'tt999', 'Poster': 'http://x/a.jpg'}

    class FakeResp:
        def json(self):
            return fake_resp

    monkeypatch.setattr(requests, 'get', lambda *a, **k: FakeResp())
    # Pas de négative en mémoire, pas de cache.
    assert omdb._OMDB_NEG_CACHE.pop('tt111', None) is None

    with caplog.at_level('WARNING'):
        res = omdb.fetch_omdb_single('tt111', 'Mon Titre', 2020)

    assert res is None
    assert db.omdb_get('tt111') is None, 'la réponse d\'un autre titre doit rester hors cache'
    assert db.omdb_get('tt999') is None, 'ne pas créer d\'entrée pour l\'autre titre'


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


def test_api_history_pagination(app_client):
    from src import db

    for i in range(3):
        db.add_history({
            'id': f'page-{i}', 'op': 'rename', 'date': '2026-01-01 00:00:00',
            'from_path': f'/missing/{i}', 'from_name': f'{i}.mkv',
            'to_path': f'/missing/{i}-new', 'to_name': f'{i}-new.mkv',
        })

    first_page = app_client.get('/api/history?limit=2&offset=0')
    assert [entry['id'] for entry in first_page.get_json()] == ['page-2', 'page-1']
    assert first_page.headers['X-History-Has-More'] == 'true'

    last_page = app_client.get('/api/history?limit=2&offset=2')
    assert [entry['id'] for entry in last_page.get_json()] == ['page-0']
    assert last_page.headers['X-History-Has-More'] == 'false'
    assert app_client.get('/api/history?limit=invalid').status_code == 400


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


def test_api_move_conflict_preserves_both_files(app_client, tmp_path):
    source = tmp_path / 'downloads' / 'movie.mkv'
    source.write_bytes(b'new movie')
    destination = tmp_path / 'out' / 'movies' / source.name
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b'existing movie')

    response = app_client.post('/api/move', json={
        'path': str(source), 'media_type': 'movie',
    })

    assert response.status_code == 409
    assert source.read_bytes() == b'new movie'
    assert destination.read_bytes() == b'existing movie'


def test_api_move_rejects_unknown_type_and_unsafe_name(app_client, tmp_path):
    source = tmp_path / 'downloads' / 'movie.mkv'
    source.write_bytes(b'movie')

    missing_type = app_client.post('/api/move', json={'path': str(source)})
    unsafe_name = app_client.post('/api/move', json={
        'path': str(source), 'media_type': 'movie', 'new_name': '..\\outside.mkv',
    })

    assert missing_type.status_code == 400
    assert unsafe_name.status_code == 400
    assert source.read_bytes() == b'movie'


def test_api_move_uses_series_and_confirmed_filename(app_client, tmp_path, monkeypatch):
    from src import utils

    monkeypatch.setattr(utils, '_migrate_file_cache', lambda *_args: None)
    source = tmp_path / 'downloads' / 'unparsed-name.mkv'
    source.write_bytes(b'episode')
    response = app_client.post('/api/move', json={
        'path': str(source),
        'media_type': 'tv',
        'series_name': 'Superman & Lois',
        'new_name': 'Superman & Lois - S01E01 - Pilot.mkv',
    })
    assert response.status_code == 200
    body = response.get_json()
    assert body['new_path'] == str(
        tmp_path / 'out' / 'tv' / 'Superman & Lois' / 'Superman & Lois - S01E01 - Pilot.mkv'
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        progress = app_client.get(f"/api/move-progress/{body['job_id']}").get_json()
        if progress.get('finished'):
            break
        time.sleep(0.1)

    assert progress['verified'] is True
    assert os.path.exists(body['new_path'])
    assert Path(body['new_path']).read_bytes() == b'episode'
    assert not source.exists()


def test_async_move_keeps_verified_copy_if_source_cleanup_fails(tmp_path, monkeypatch):
    from src import state, utils

    source = tmp_path / 'source.mkv'
    destination = tmp_path / 'destination.mkv'
    source.write_bytes(b'content')
    job_id = 'cleanup-failure'
    state.move_progress[job_id] = utils._make_progress(7)
    original_remove = os.remove

    def fail_source_remove(path):
        if os.path.abspath(path) == os.path.abspath(source):
            raise PermissionError('source is locked')
        original_remove(path)

    monkeypatch.setattr(utils.os, 'remove', fail_source_remove)
    with pytest.raises(OSError, match='Copie vérifiée'):
        utils._move_path(str(source), str(destination), job_id=job_id)

    assert source.read_bytes() == b'content'
    assert destination.read_bytes() == b'content'


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
    assert r.status_code == 404
    assert r.get_json()['verified'] is False


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


def test_library_movie_file_meta_not_matched_in_cache(app_client, tmp_path):
    """Bibliothèque : un fichier [imdb-ttX] dont le cache ne contient que des
    homonymes TVDB → la meta vient du nom + OMDb, PAS du 1er résultat (poster/
    infos d'un autre titre)."""
    from src import db

    movies = tmp_path / 'out' / 'movies'
    movies.mkdir(parents=True)
    fname = 'Pilote (2022) [imdb-tt222].mkv'
    (movies / fname).write_bytes(b'x')
    # Cache de recherche pollué : le 1er résultat est un autre titre.
    db.file_cache_set('fkey1', str(movies / fname), 1, 0, 'movie', [
        {'id': 1, 'title': 'Autre Film', 'year': 2010, 'imdb_id': 'tt111', 'poster': 'http://x/wrong.jpg'},
    ])
    # OMDb connaît le vrai film.
    od = {'Response': 'True', 'Title': 'The Pilot', 'Year': '2022', 'imdbID': 'tt222',
          'Genre': 'Action', 'Runtime': '75 min', 'imdbRating': '6.0',
          'Released': '10 May 2022', 'Poster': 'http://x/pilot.jpg'}
    db.omdb_set('tt222', od)

    r = app_client.get('/api/library', query_string={
        'path': str(movies), 'type': 'movie', 'fast': '1'})
    meta = r.get_json()['entries'][0]['meta']
    assert meta['imdb'] == 'tt222'
    assert meta['title'] == 'Pilote', 'titre attendu depuis le nom, pas "Autre Film"'
    assert meta['year'] == '2022'
    assert meta['poster'] == 'http://x/pilot.jpg', 'poster OMDb du vrai film'
    assert meta['poster'] != 'http://x/wrong.jpg'
    assert meta['genres'] == 'Action'


def test_library_flat_endpoint(app_client, tmp_path):
    """Vue grille : /api/library/flat renvoie 1 item par film + 1 par série,
    avec meta (titre/année/IMDb) et compteur d'épisodes (caches BDD)."""
    from src import db

    movies = tmp_path / 'out' / 'movies'
    (movies / 'sub').mkdir(parents=True)
    (movies / 'Film A (2021) [imdb-tt100].mkv').write_bytes(b'x')
    (movies / 'sub' / 'Film B (2019) [imdb-tt200].mkv').write_bytes(b'x')
    (movies / 'notes.txt').write_bytes(b'non')

    series = tmp_path / 'out' / 'tv' / 'Serie X (2020) [tvdbid-555]'
    season = series / 'Season 01'
    season.mkdir(parents=True)
    (season / 'Serie X - S01E01 - Ep.mkv').write_bytes(b'x')
    (season / 'Serie X - S01E02 - Ep.mkv').write_bytes(b'x')

    db.omdb_set('tt100', {'Title': 'Film A', 'Year': '2021', 'imdbID': 'tt100',
                          'Genre': 'Action', 'Poster': 'http://x/a.jpg'})
    db.series_meta_set('555', 'tt300', {'id': '555', 'title': 'Serie X', 'year': '2020',
                                        'poster': 'http://x/s.jpg', 'tvdb_slug': 'serie-x'})
    db.series_episodes_set('555', {'episodes': [
        {'s': 1, 'e': 1, 'airdate': '2020-01-05'},
        {'s': 1, 'e': 2, 'airdate': '2020-01-12'},
    ], 'order': 'official'})

    r = app_client.get('/api/library/flat')
    assert r.status_code == 200
    data = r.get_json()
    items = data['items']
    assert data['total'] == 3, '2 films + 1 série (txt exclu, pas de doublon dossier)'

    films = [i for i in items if i['type'] == 'movie']
    series_items = [i for i in items if i['type'] == 'series']
    assert len(films) == 2 and len(series_items) == 1

    fa = next(i for i in films if 'Film A' in i['name'])
    assert fa['valid'] is True
    assert fa['meta']['imdb'] == 'tt100'
    assert fa['meta']['title'] == 'Film A'
    assert fa['meta']['poster'] == 'http://x/a.jpg'

    fb = next(i for i in films if 'Film B' in i['name'])
    assert fb['valid'] is True, 'nom conforme au format → valide'

    sx = series_items[0]
    assert sx['meta']['title'] == 'Serie X'
    assert sx['meta']['tvdb'] == '555'
    assert sx['meta']['poster'] == 'http://x/s.jpg'
    assert sx['ep_present'] == 2 and sx['ep_total'] == 2


def test_api_library_series_detail(app_client, tmp_path, monkeypatch):
    """Détail série (vue grille) : saisons (posters TVDB) + épisodes avec leur
    état présent / manquant / à venir."""
    from src import db, state
    from src.routes import library as lib_routes

    lib_routes._SERIES_EP_MEM.clear()

    series = tmp_path / 'out' / 'tv' / 'Serie X (2020) [tvdbid-888]'
    s1 = series / 'Season 01'
    s1.mkdir(parents=True)
    (s1 / 'Serie X - S01E01 - Ep.mkv').write_bytes(b'x')
    (s1 / 'Serie X - S01E02 - Ep.mkv').write_bytes(b'x')

    db.series_meta_set('888', 'tt300', {'id': '888', 'title': 'Serie X', 'year': '2020'})
    db.series_episodes_set('888', {'episodes': [
        {'s': 1, 'e': 1, 'airdate': '2020-01-05'},
        {'s': 1, 'e': 2, 'airdate': '2020-01-12'},
        {'s': 1, 'e': 3, 'airdate': '2020-01-19'},        # manquant
        {'s': 2, 'e': 1, 'airdate': '2099-01-01'},         # à venir
    ], 'order': 'official'})
    db.series_seasons_set('888', {'seasons': [
        {'number': 1, 'id': 10, 'poster': 'http://x/s1.jpg', 'year': '2020'},
        {'number': 2, 'id': 20, 'poster': '', 'year': '2021'},
    ]})
    monkeypatch.setattr(state.api_handler, 'get_series_seasons',
                        lambda sid: db.series_seasons_get(sid) or {'seasons': []})

    r = app_client.get('/api/library/series/888')
    assert r.status_code == 200
    data = r.get_json()
    assert data['tvdb_id'] == '888'
    assert data['meta']['title'] == 'Serie X'
    assert data['episodes_available'] is True
    s1 = next(s for s in data['seasons'] if s['number'] == 1)
    assert s1['poster'] == 'http://x/s1.jpg'
    assert s1['total'] == 3 and s1['present'] == 2 and s1['missing'] == 1 and s1['upcoming'] == 0
    states = {e['e']: e['state'] for e in s1['episodes']}
    assert states == {1: 'present', 2: 'present', 3: 'missing'}

    s2 = next(s for s in data['seasons'] if s['number'] == 2)
    assert s2['total'] == 1 and s2['upcoming'] == 1
    assert s2['episodes'][0]['state'] == 'upcoming'
    files01 = s1['episodes'][0]['files']
    assert files01 and files01[0]['name'].endswith('.mkv')


def test_api_library_size_range_uses_persisted_cache(app_client, tmp_path):
    import time

    from src import db

    file_path = str(tmp_path / 'out' / 'movies' / 'Film.mkv')
    db.library_file_sizes_set([{
        'path': file_path,
        'parent': str(tmp_path / 'out' / 'movies'),
        'size': 4096,
        'mtime': 123,
        'is_video': True,
    }])
    db.meta_set('library_file_sizes_indexed_at', int(time.time()))

    response = app_client.get('/api/library/size-range')

    assert response.status_code == 200
    assert response.get_json() == {
        'min': 4096,
        'max': 4096,
        'count': 1,
        'ready': True,
        'loading': False,
        'error': None,
    }


def test_library_size_index_persists_and_prunes_file_entries(app_client, tmp_path):
    from src import db
    from src.routes import library as lib_routes

    root = tmp_path / 'out' / 'movies'
    nested = root / 'Film'
    nested.mkdir(parents=True)
    video = nested / 'Film.mkv'
    video.write_bytes(b'video-data')
    subtitle = nested / 'Film.srt'
    subtitle.write_bytes(b'subtitle')
    stale = str(root / 'removed.mkv')
    db.library_file_sizes_set([{
        'path': stale,
        'parent': str(root),
        'size': 100,
        'mtime': 1,
        'is_video': True,
    }], scan_id=1)

    lib_routes._library_size_index_worker([str(root)], 2)

    indexed = db.library_file_sizes_get([str(video), str(subtitle), stale])
    assert indexed[str(video)]['size'] == len(b'video-data')
    assert indexed[str(video)]['is_video'] is True
    assert indexed[str(subtitle)]['is_video'] is False
    assert stale not in indexed
    assert db.library_file_sizes_range()['count'] == 1
    assert db.meta_get('library_file_sizes_indexed_at')


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

    def fake_search(title, year=None, imdb_id=None):
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
                        lambda title, year=None, imdb_id=None: [{'id': 3, 'title': 'Show', 'year': 2020, 'imdb_id': ''}])
    r = app_client.post('/api/search/tv', json={'title': 'Show', 'season': 1, 'episode': 1})
    assert r.status_code == 200
    assert r.get_json()['results'][0]['title'] == 'Show'


def test_search_movie_imdb_not_in_tvdb_omdb_stub(app_client, monkeypatch, tmp_path):
    """Le film n'existe pas sur TVDB : le seul résultat servi est un stub OMDb
    (infos du bon IMDb), sans poster et avec not_found — jamais les infos d'un
    autre titre."""
    from src import state
    from src.api import omdb

    monkeypatch.setattr(state.api_handler, 'search_movie',
                        lambda title, year=None, imdb_id=None: [
                            {'id': 1, 'title': 'Autre Film', 'year': 2010, 'imdb_id': 'tt111',
                             'poster': 'http://x/wrong.jpg'}])
    od = {'Response': 'True', 'Title': 'Pilote', 'Year': '2022', 'imdbID': 'tt222',
          'Genre': 'Documentary, Sport', 'Runtime': '75 min', 'imdbRating': '6.0',
          'Rated': 'TV-14', 'Released': '01 Jan 2022', 'Poster': 'N/A'}
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: od)

    f = tmp_path / 'Pilote (2022) [imdb-tt222].mkv'
    f.write_bytes(b'x')
    r = app_client.post('/api/search/movie', json={
        'title': 'Pilote', 'year': 2022, 'path': str(f), 'force_refresh': True,
    })
    body = r.get_json()
    assert len(body['results']) == 1
    got = body['results'][0]
    assert got['not_found'] is True
    assert got['imdb_id'] == 'tt222'
    assert got['title'] == 'Pilote'
    assert got['year'] == 2022
    assert got['poster'] == ''
    assert got['omdb']['imdbRating'] == '6.0'


def test_search_movie_imdb_match_kept(app_client, monkeypatch, tmp_path):
    """Le résultat TVDB porte l'IMDb du fichier : il est servi tel quel
    (poster conservé, pas de not_found)."""
    from src import state
    from src.api import omdb

    monkeypatch.setattr(state.api_handler, 'search_movie',
                        lambda title, year=None, imdb_id=None: [
                            {'id': 1, 'title': 'Autre', 'year': 2010, 'imdb_id': 'tt111', 'poster': 'http://x/a.jpg'},
                            {'id': 2, 'title': 'Pilote', 'year': 2022, 'imdb_id': 'tt222', 'poster': 'http://x/p.jpg'}])
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: None)

    f = tmp_path / 'Pilote (2022) [imdb-tt222].mkv'
    f.write_bytes(b'x')
    r = app_client.post('/api/search/movie', json={
        'title': 'Pilote', 'year': 2022, 'path': str(f), 'force_refresh': True,
    })
    body = r.get_json()
    top = body['results'][0]
    assert top['imdb_id'] == 'tt222'
    assert top['poster'] == 'http://x/p.jpg'
    assert 'not_found' not in top


def test_search_tv_not_found_empty_when_no_omdb(app_client, monkeypatch, tmp_path):
    """Série absente de TVDB et inconnue d'OMDb : aucune info d'un autre
    média n'est servie (liste vide)."""
    from src import state
    from src.api import omdb

    monkeypatch.setattr(state.api_handler, 'search_tv',
                        lambda title, year=None, imdb_id=None: [
                            {'id': 9, 'title': 'Autre Serie', 'year': 2015, 'imdb_id': 'tt999',
                             'poster': 'http://x/wrong.jpg'}])
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: None)

    f = tmp_path / 'Mon Show [imdb-tt222] S01E01.mkv'
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b'x')
    r = app_client.post('/api/search/tv', json={
        'title': 'Mon Show', 'season': 1, 'episode': 1,
        'path': str(f), 'force_refresh': True,
    })
    assert r.get_json()['results'] == []


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
                        lambda sid, series_path=None, season_types=None:
                        db.series_episodes_set(sid, {'episodes': episodes}) or episodes)

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


def test_rename_folder_series_dry_run_no_side_effect(app_client, monkeypatch, tmp_path):
    """dry_run=True : la liste des renommages est calculée SANS toucher au disque
    ni au dossier (aperçu pour la popup de confirmation)."""
    from src import db
    from src.api import omdb

    monkeypatch.setattr(omdb, '_SERIES_META_MEM', {})
    monkeypatch.setattr(omdb, 'resolve_series_imdb', lambda *a, **k: 'tt9999999')
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: None)
    # Cache TVDB pré-rempli : le dry-run renomme déjà avec les vrais titres.
    db.series_episodes_set('555', {'episodes': [
        {'s': 1, 'e': 1, 'title': 'Pilot'},
        {'s': 1, 'e': 2, 'title': 'Second'}]})

    tv_root = str(tmp_path / 'out' / 'tv')
    folder = os.path.join(tv_root, 'bad folder')
    os.makedirs(folder)
    f1 = os.path.join(folder, 'Show S01E01.mkv')
    f2 = os.path.join(folder, 'Show S01E02.mkv')
    for p in (f1, f2):
        with open(p, 'wb') as f:
            f.write(b'x')

    r = app_client.post('/api/library/rename-folder-series', json={
        'path': folder, 'tvdbid': '555', 'title': 'Show', 'year': 2020,
        'dry_run': True,
    })
    body = r.get_json()
    assert body['success'] is True
    assert body['dry_run'] is True
    assert 'background' not in body
    # Les renommages sont calculés (aperçu) mais pas exécutés.
    to_names = [x['to'] for x in body['renames']]
    assert 'Show - S01E01 - Pilot.mkv' in to_names
    assert 'Show - S01E02 - Second.mkv' in to_names
    # Aucun fichier déplacé : les noms d'origine sont intacts.
    assert os.path.isfile(f1), 'le fichier S01E01 aurait dû rester en place'
    assert os.path.isfile(f2), 'le fichier S01E02 aurait dû rester en place'
    assert not os.path.exists(os.path.join(folder, 'Show - S01E01 - Pilot.mkv'))
    # Le dossier n'a pas été renommé non plus.
    assert os.path.isdir(folder)


def test_rename_folder_series_dry_run_no_changes(app_client, monkeypatch, tmp_path):
    """dry_run sur un dossier déjà conforme : aucun renames, pas de renommage
    de dossier → le front n'affiche que la popup OK/Annuler (pas Renommer)."""
    from src import db
    from src.api import omdb

    monkeypatch.setattr(omdb, '_SERIES_META_MEM', {})
    monkeypatch.setattr(omdb, 'resolve_series_imdb', lambda *a, **k: 'tt9999999')
    monkeypatch.setattr(omdb, 'fetch_omdb_single', lambda *a, **k: None)
    db.series_episodes_set('555', {'episodes': [
        {'s': 1, 'e': 1, 'title': 'Pilot'},
        {'s': 1, 'e': 2, 'title': 'Second'}]})

    tv_root = str(tmp_path / 'out' / 'tv')
    folder = os.path.join(tv_root, 'Show')  # déjà au bon nom (format par défaut)
    os.makedirs(folder)
    for n in ('Show - S01E01 - Pilot.mkv', 'Show - S01E02 - Second.mkv'):
        with open(os.path.join(folder, n), 'wb') as f:
            f.write(b'x')

    r = app_client.post('/api/library/rename-folder-series', json={
        'path': folder, 'tvdbid': '555', 'title': 'Show', 'year': 2020,
        'dry_run': True,
    })
    body = r.get_json()
    assert body['success'] is True
    assert body['dry_run'] is True
    assert body['renames'] == []
    assert body['folder'] is None


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


def test_folder_series_rename_uses_localized_episode_titles(app_client, tmp_path, monkeypatch):
    from src import db, state
    from src.routes.library import _folder_series_rename

    monkeypatch.setattr(state, 'config', {
        **(state.config or {}),
        'tv_format': '{n} - {s00e00} - {t:en}',
    })
    folder = tmp_path / 'Show'
    folder.mkdir()
    (folder / 'Show S01E01-E02.mkv').write_bytes(b'x')
    db.series_episodes_set('779', {
        'order': 'default',
        'episode_title_languages': ['eng'],
        'episodes': [
            {'s': 1, 'e': 1, 'title': 'Pilot One',
             'episode_title_translations': {'eng': 'English One'}},
            {'s': 1, 'e': 2, 'title': 'Pilot Two',
             'episode_title_translations': {'eng': 'English Two'}},
        ],
    })

    renames = _folder_series_rename(
        str(folder), 'Show', 2020, '779', '', '', {}, dry_run=True,
    )
    assert renames == [{
        'from': 'Show S01E01-E02.mkv',
        'to': 'Show - S01E01-E02 - English One & English Two.mkv',
    }]


def test_enrich_episode_title_translations_uses_cached_tvdb_season_data(
        app_client, monkeypatch):
    from src import db, library, state

    monkeypatch.setattr(state, 'config', {
        **(state.config or {}),
        'tv_format': '{n} - {s00e00} - {t:en}',
    })
    monkeypatch.setattr(
        state.api_handler,
        'get_series_episodes',
        lambda series_id, season_type='default', lang=None, **kwargs: {
            'episodes': [{
                'season': 1, 'episode': 2, 'episode_title': 'English title',
            }],
        },
    )
    cached = {
        'order': 'default',
        'episodes': [{'s': 1, 'e': 2, 'title': 'Titre original'}],
    }

    enriched = library.enrich_episode_title_translations('780', cached)

    assert enriched['episodes'][0]['episode_title_translations'] == {
        'eng': 'English title',
    }
    assert db.series_episodes_get('780')['episode_title_languages'] == ['eng']


def test_tv_details_cache_varies_by_episode_title_language(app_client, monkeypatch):
    from src import db, state

    config = {**(state.config or {}), 'tv_format': '{t:en}'}
    monkeypatch.setattr(state, 'config', config)
    cache_keys = []
    calls = []
    monkeypatch.setattr(db, 'details_get', lambda _kind, key: cache_keys.append(key))
    monkeypatch.setattr(db, 'details_set', lambda *_args: None)

    def get_details(*args, **kwargs):
        calls.append((args, kwargs))
        return {'title': ''}

    monkeypatch.setattr(state.api_handler, 'get_tv_details', get_details)
    assert app_client.get('/api/tv/781').status_code == 200

    config['tv_format'] = '{t:fr}'
    assert app_client.get('/api/tv/781').status_code == 200

    assert cache_keys == ['781:s1e1:lang-eng', '781:s1e1:lang-fra']
    assert [call[1]['episode_title_languages'] for call in calls] == [
        ['eng'], ['fra'],
    ]
