import time


from src import db


def test_init_db_creates_tables(tmp_db):
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {r[0] for r in cur.fetchall()}
    conn.close()
    for t in ('history', 'search_cache', 'file_search_cache', 'details_cache',
              'omdb_cache', 'omdb_episode_cache', 'series_episodes_cache',
              'series_meta_cache', 'usage_counters', 'media_duration_cache',
              'folder_size_cache', 'app_meta'):
        assert t in tables


def test_history_roundtrip(tmp_db):
    db.add_history({'id': 'h1', 'op': 'rename', 'date': '2026-01-01 00:00:00',
                    'from_path': '/a/x.mkv', 'from_name': 'x.mkv',
                    'to_path': '/a/y.mkv', 'to_name': 'y.mkv', 'revert_of': None})
    db.add_history({'id': 'h2', 'op': 'move', 'date': '2026-01-02 00:00:00',
                    'from_path': '/a/x.mkv', 'from_name': 'x.mkv',
                    'to_path': '/b/y.mkv', 'to_name': 'y.mkv', 'extra_field': 'v'})
    history = db.get_history()
    assert [h['id'] for h in history] == ['h2', 'h1']
    assert history[0]['extra_field'] == 'v'
    db.clear_history()
    assert db.get_history() == []


def test_search_cache_roundtrip_and_expiry(tmp_db):
    assert db.cache_get('k1') is None
    db.cache_set('k1', 'movie', [{'id': 1}])
    got = db.cache_get('k1')
    assert got is not None
    assert got['media_type'] == 'movie'
    assert got['results'] == [{'id': 1}]
    assert db.cache_get('k1', max_age_seconds=-1) is None


def test_file_cache_roundtrip_and_migrate(tmp_db):
    assert db.file_cache_get('f1') is None
    db.file_cache_set('f1', '/a/x.mkv', 100, 1234, 'movie', [{'id': 1}])
    got = db.file_cache_get('f1')
    assert got is not None
    assert got['results'] == [{'id': 1}]

    assert db.file_cache_migrate('f1', 'f2', '/b/y.mkv', 100, 1234, 'x.mkv') is True
    assert db.file_cache_get('f1') is None
    got2 = db.file_cache_get('f2')
    assert got2 is not None
    assert got2['results'] == [{'id': 1}]

    # le nom a changé → pas de migration
    db.file_cache_set('f3', '/a/z.mkv', 1, 1, 'movie', [{'id': 3}])
    assert db.file_cache_migrate('f3', 'f4', '/b/w.mkv', 1, 1, 'other.mkv') is False
    assert db.file_cache_get('f3') is not None


def test_details_cache_roundtrip(tmp_db):
    assert db.details_get('movie', '42') is None
    db.details_set('movie', '42', {'title': 'M'})
    assert db.details_get('movie', '42') == {'title': 'M'}
    assert db.details_get('movie', '42', max_age_seconds=-1) is None


def test_omdb_cache_roundtrip(tmp_db):
    assert db.omdb_get('tt1') is None
    db.omdb_set('TT1', {'imdbRating': '8.5', 'Title': 'X'})
    assert db.omdb_get('tt1') == {'imdbRating': '8.5', 'Title': 'X'}
    assert 'tt1' in db.omdb_all()


def test_omdb_episode_cache(tmp_db):
    assert db.omdb_episode_get('k') is None
    db.omdb_episode_set('K1', {'imdbRating': '7.0'})
    assert db.omdb_episode_get('k1') == {'imdbRating': '7.0'}
    assert 'k1' in db.omdb_episode_all()


def test_series_caches(tmp_db):
    assert db.series_episodes_get('81189') is None
    db.series_episodes_set('81189', {'episodes': [1, 2]})
    assert db.series_episodes_get('81189') == {'episodes': [1, 2]}
    assert db.series_episodes_get('81189', max_age=-1) is None

    assert db.series_meta_get('81189') is None
    db.series_meta_set('81189', 'tt0913949', {'name': 'Breaking Bad'})
    meta = db.series_meta_get('81189')
    assert meta['imdb_id'] == 'tt0913949'
    assert meta['data'] == {'name': 'Breaking Bad'}
    assert '81189' in db.series_meta_all()


def test_usage_counters(tmp_db):
    db.usage_bump('omdb')
    db.usage_bump('omdb')
    db.usage_bump('tvdb')
    u = db.usage_get()
    assert u['omdb']['total'] == 2
    assert u['omdb']['day_count'] == 2
    assert u['tvdb']['total'] == 1
    assert u['omdb']['day'] == time.strftime('%Y-%m-%d')


def test_usage_listener(tmp_db):
    calls = []
    db.usage_set_listener(lambda s: calls.append(s))
    db.usage_bump('tvdb')
    db.usage_set_listener(None)
    assert calls == ['tvdb']


def test_meta_roundtrip(tmp_db):
    assert db.meta_get('missing', 'dft') == 'dft'
    db.meta_set('k', 123)
    assert db.meta_get('k') == '123'


def test_media_duration_cache(tmp_db):
    assert db.media_dur_get('/a/x.mkv') is None
    db.media_dur_set('/a/x.mkv', 62, 100, 1234)
    got = db.media_dur_get('/a/x.mkv')
    assert got == {'size': 100, 'mtime': 1234, 'minutes': 62}


def test_folder_size_cache(tmp_db):
    assert db.folder_size_get('/a', 1234) is None
    db.folder_size_set('/a', 500, 1234)
    assert db.folder_size_get('/a', 1234) == {'size': 500}
    assert db.folder_size_get('/a', 9999) is None


def test_refresh_due(tmp_db):
    db.cache_set('old', 'movie', [{'id': 1}])
    db.file_cache_set('f1', '/a/x.mkv', 1, 1, 'movie', [{'id': 1}])
    db.details_set('movie', '42', {'title': 'M'})
    db.series_episodes_set('81189', {'episodes': [1]})
    # entrée récente → jamais due, quelle que soit la valeur
    assert db.refresh_due('search_cache', 7) == []
    assert db.refresh_due('file_search_cache', 7) == []
    assert db.refresh_due('details_cache', 7) == []
    assert db.refresh_due('series_episodes_cache', 7) == []
    # 0 jours → jamais
    assert db.refresh_due('search_cache', 0) == []
    # now fixé au futur → tout est dû
    future = time.time() + 8 * 86400
    assert db.refresh_due('search_cache', 7, now=future) == ['old']
    assert db.refresh_due('file_search_cache', 7, now=future) == ['f1']
    assert db.refresh_due('details_cache', 7, now=future) == ['movie:42']
    assert db.refresh_due('series_episodes_cache', 7, now=future) == ['81189']
