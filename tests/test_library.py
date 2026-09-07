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
