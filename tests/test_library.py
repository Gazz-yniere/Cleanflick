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
