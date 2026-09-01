
from src.api.tvdb import TVDBAPIHandler
from src.api.handler import APIHandler


def _mk_handler(movies=None, series=None):
    h = TVDBAPIHandler.__new__(TVDBAPIHandler)
    h.api_key = 'k'
    h.pin = None
    h.client = None
    h._movies = movies or []
    h._series = series or []
    h.search_movie = lambda title, year=None: [
        r for r in h._movies
        if not year or not r.get('year') or abs(r['year'] - year) <= 1
    ]
    h.search_series = lambda title, year=None: [
        r for r in h._series
        if not year or not r.get('year') or abs(r['year'] - year) <= 1
    ]
    return h


def test_normalize_year():
    h = _mk_handler()
    assert h._normalize_year('1999') == 1999
    assert h._normalize_year(2020) == 2020
    assert h._normalize_year(None) is None
    assert h._normalize_year('') is None
    assert h._normalize_year('None') is None
    assert h._normalize_year('abc') is None


def test_looks_like_tv():
    h = _mk_handler()
    assert h._looks_like_tv(filename='Show.S01E02.mkv')
    assert h._looks_like_tv(filename='show 1x03.mkv')
    assert h._looks_like_tv(filename='Show Season 2.mkv')
    assert h._looks_like_tv(season=1, episode=1)
    assert not h._looks_like_tv(filename='Movie 2020.mkv')


def test_score():
    h = _mk_handler()
    assert h._score([], 'T', 2020) == -1
    exact = [{'title': 'Matrix', 'year': 1999}]
    other = [{'title': 'Matrix Reloaded', 'year': 2003}]
    assert h._score(exact, 'Matrix', 1999) > h._score(other, 'Matrix', 1999)


def test_search_auto_prefers_tv_for_episode_filename():
    h = _mk_handler(
        movies=[{'title': 'Show', 'year': 2020, 'id': 'm1'}],
        series=[{'title': 'Show', 'year': 2020, 'id': 's1'}],
    )
    res = h.search_auto('Show', 2020, filename='Show.S01E02.mkv')
    assert res['media_type'] == 'tv'
    assert res['results'][0]['id'] == 's1'


def test_search_auto_prefers_movie_for_plain_title():
    h = _mk_handler(
        movies=[{'title': 'Movie', 'year': 2020, 'id': 'm1'}],
        series=[{'title': 'Movie Show', 'year': 2019, 'id': 's1'}],
    )
    res = h.search_auto('Movie', 2020, filename='movie 2020.mkv')
    assert res['media_type'] == 'movie'
    assert res['results'][0]['id'] == 'm1'


def test_search_auto_media_hint():
    h = _mk_handler(
        movies=[{'title': 'X', 'year': 2020, 'id': 'm1'}],
        series=[{'title': 'X', 'year': 2020, 'id': 's1'}],
    )
    res = h.search_auto('X', 2020, media_hint='tv')
    assert res['media_type'] == 'tv'
    res2 = h.search_auto('X', 2020, media_hint='movie')
    assert res2['media_type'] == 'movie'


def test_search_auto_empty_results():
    h = _mk_handler()
    res = h.search_auto('Nope', 1999, filename='Show.S01E01.mkv')
    assert res == {'media_type': 'tv', 'results': []}
    res2 = h.search_auto('Nope', 1999)
    assert res2 == {'media_type': 'movie', 'results': []}


def test_api_handler_without_key():
    h = APIHandler({})
    assert h.tvdb is None
    assert h.search_movie('x') == []
    assert h.search_tv('x') == []
    assert h.search_auto('x') == {'media_type': 'movie', 'results': []}
    assert h.get_movie_details('123') == {'id': '123', 'source': 'tvdb'}
    assert h.get_tv_details('123') == {'id': '123', 'source': 'tvdb'}
    assert h.get_series_episodes('123') == {'series_id': '123', 'episodes': [], 'source': 'tvdb'}


def test_api_handler_with_mocked_tvdb():
    inner = _mk_handler(movies=[{'title': 'M', 'year': 2020, 'id': 'm1'}])
    h = APIHandler({'tvdb_api_key': 'k'})
    h.tvdb = inner
    assert h.search_movie('M')[0]['id'] == 'm1'
    assert h.search_auto('M', 2020, filename='M 2020.mkv')['media_type'] == 'movie'
    assert h.get_movie_details('abc') == {'id': 'abc', 'source': 'tvdb'}


def test_parse_episode():
    h = _mk_handler()
    ep = h._parse_episode({
        'id': 5, 'seasonNumber': 1, 'number': 2, 'absoluteNumber': 2,
        'name': 'Pilot', 'overview': 'ov', 'aired': '2020-01-01',
        'runtime': 42, 'score': 8.5,
    })
    assert ep['s00e00'] == 'S01E02'
    assert ep['sxe'] == '1x02'
    assert ep['t'] == 'Pilot'
    assert ep['rating'] == 0.8
    assert ep['d'] == '2020-01-01'
