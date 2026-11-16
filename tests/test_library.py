from src import library


def test_episode_refs_single():
    assert library.episode_refs('Show.S01E02.mkv') == [(1, 2)]
    assert library.episode_refs('show s03e10.mkv') == [(3, 10)]
    assert library.episode_refs('Show 1x03.avi') == [(1, 3)]
    assert library.episode_refs('Movie 2020.mkv') == []


def test_episode_refs_range_dash():
    assert library.episode_refs('Korra - S01E01-E02 - Welcome & Leaf.avi') == [(1, 1), (1, 2)]
    assert library.episode_refs('Show.S01E01-E03.mkv') == [(1, 1), (1, 2), (1, 3)]
    assert library.episode_refs('Show 1x02-03.mkv') == [(1, 2), (1, 3)]


def test_episode_refs_consecutive():
    assert library.episode_refs('Show.S01E01E02.mkv') == [(1, 1), (1, 2)]
    assert library.episode_refs('Show.S01E01, E02.mkv') == [(1, 1), (1, 2)]


def test_episode_refs_range_not_confused_with_next_marker():
    # "S01E01 - S02E01" : pas une plage, deux marqueurs distincts
    assert library.episode_refs('Show.S01E01 - S02E01.mkv') == [(1, 1)]


def test_present_episodes_multi_episode_file(tmp_path):
    d = tmp_path / 'series'
    d.mkdir()
    (d / 'Korra - S01E01-E02 - Welcome & Leaf.avi').write_bytes(b'x')
    (d / 'Korra - S01E03 - The Revelation.avi').write_bytes(b'x')
    assert library.present_episodes(str(d)) == {(1, 1), (1, 2), (1, 3)}


def test_episode_order_matching_prefers_official_when_local_season_matches(tmp_path):
    folder = tmp_path / 'series'
    folder.mkdir()
    (folder / 'Show - S01E01 - Pilot.mkv').write_bytes(b'x')
    (folder / 'Show - S01E02 - Finale.mkv').write_bytes(b'x')
    local_files = library.episode_files(str(folder))
    candidates = {
        'default': [
            {'s': 1, 'e': 1, 'title': 'Pilot'},
            {'s': 1, 'e': 2, 'title': 'Finale'},
        ],
        'alternate': [
            {'s': 1, 'e': 1, 'title': 'Part One'},
            {'s': 1, 'e': 2, 'title': 'Part Two'},
            {'s': 2, 'e': 1, 'title': 'The Beginning'},
        ],
    }

    order, episodes, matched = library._best_episode_order(candidates, local_files)

    assert order == 'default'
    assert len(episodes) == 2
    assert matched


def test_episode_order_matching_selects_alternate_when_local_numbers_match_it(tmp_path):
    folder = tmp_path / 'series'
    folder.mkdir()
    (folder / 'Show - S01E01 - Part One.mkv').write_bytes(b'x')
    (folder / 'Show - S02E01 - The Beginning.mkv').write_bytes(b'x')
    local_files = library.episode_files(str(folder))
    candidates = {
        'default': [
            {'s': 1, 'e': 1, 'title': 'Pilot'},
            {'s': 1, 'e': 2, 'title': 'Finale'},
        ],
        'alternate': [
            {'s': 1, 'e': 1, 'title': 'Part One'},
            {'s': 2, 'e': 1, 'title': 'The Beginning'},
        ],
    }

    order, episodes, matched = library._best_episode_order(candidates, local_files)

    assert order == 'alternate'
    assert len(episodes) == 2
    assert matched


def test_tvdb_series_episodes_auto_does_not_assume_netflix_order(tmp_path, tmp_db, monkeypatch):
    from types import SimpleNamespace

    from src import db, state

    folder = tmp_path / 'series'
    folder.mkdir()
    (folder / 'Show - S01E01 - Pilot.mkv').write_bytes(b'x')
    (folder / 'Show - S01E02 - Finale.mkv').write_bytes(b'x')
    orders = {
        'default': [
            {'s': 1, 'e': 1, 'title': 'Pilot'},
            {'s': 1, 'e': 2, 'title': 'Finale'},
        ],
        'alternate': [
            {'s': 1, 'e': 1, 'title': 'Part One'},
            {'s': 1, 'e': 2, 'title': 'Part Two'},
            {'s': 2, 'e': 1, 'title': 'The Beginning'},
        ],
    }
    calls = []

    def get_series_episodes(series_id, season_type):
        calls.append(season_type)
        return {'episodes': orders[season_type]}

    db.series_meta_set('123', '', {
        'season_types': [
            {'type': 'official', 'name': 'Official'},
            {'type': 'alternate', 'alternateName': 'Netflix'},
        ],
    })
    monkeypatch.setattr(state, 'config', {'tvdb_episode_order': 'auto'})
    monkeypatch.setattr(state, 'api_handler', SimpleNamespace(get_series_episodes=get_series_episodes))

    episodes = library.tvdb_series_episodes('123', str(folder))

    assert calls == ['default', 'alternate']
    assert [episode['title'] for episode in episodes] == ['Pilot', 'Finale']
    assert db.series_episodes_get('123')['order'] == 'default'
    assert db.series_meta_get('123')['data']['episode_order'] == 'official'


def test_rename_subtitles_matching_stem(tmp_path):
    d = tmp_path / 'series'
    d.mkdir()
    video = d / 'Show - S01E01.mkv'
    video.write_bytes(b'x')
    sub = d / 'Show - S01E01.srt'
    sub.write_bytes(b'x')
    out = library.rename_subtitles_for_video(str(video), 'Show - S01E01 - Pilot.mkv')
    assert len(out) == 1
    assert (d / 'Show - S01E01 - Pilot.srt').exists()
    assert not sub.exists()


def test_rename_subtitles_orphan_multi_episode(tmp_path):
    """SRT orphelin (marqueurs multi-épisodes) aligné sur le nouveau nom du vidéo."""
    d = tmp_path / 'series'
    d.mkdir()
    video = d / 'Show - S01E01-E02.mkv'
    video.write_bytes(b'x')
    sub = d / 'Show S01E01-E02.srt'
    sub.write_bytes(b'x')
    out = library.rename_subtitles_for_video(str(video), 'Show - S01E01-E02 - Pilot & Second.mkv')
    assert len(out) == 1
    assert (d / 'Show - S01E01-E02 - Pilot & Second.srt').exists()
    assert not sub.exists()


def test_rename_subtitles_other_episode_untouched(tmp_path):
    """Le SRT d'un autre épisode du même dossier n'est pas touché."""
    d = tmp_path / 'series'
    d.mkdir()
    video = d / 'Show - S01E01.mkv'
    video.write_bytes(b'x')
    sub = d / 'Show - S01E01.srt'
    sub.write_bytes(b'x')
    other = d / 'Show - S01E02.srt'
    other.write_bytes(b'x')
    out = library.rename_subtitles_for_video(str(video), 'Show - S01E01 - Pilot.mkv')
    assert len(out) == 1
    assert (d / 'Show - S01E01 - Pilot.srt').exists()
    assert other.exists()  # S01E02 inchangé


def test_rename_subtitles_movie_no_refs(tmp_path):
    """Film (pas de marqueur d'épisode) : seul le SRT au même stem suit."""
    d = tmp_path / 'movies'
    d.mkdir()
    video = d / 'Movie 2020.mkv'
    video.write_bytes(b'x')
    sub = d / 'Movie 2020.srt'
    sub.write_bytes(b'x')
    out = library.rename_subtitles_for_video(str(video), 'Movie (2020).mkv')
    assert len(out) == 1
    assert (d / 'Movie (2020).srt').exists()
