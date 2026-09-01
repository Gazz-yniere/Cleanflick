
import pytest

from src.scanner import MediaScanner, MediaFile


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / 'media'
    d.mkdir()
    (d / 'movie 2020 1080p WEBRip x264.mkv').touch()
    (d / 'Show.Name.S01E02.Pilot.720p.mkv').touch()
    (d / 'Show 2x03.avi').touch()
    (d / 'readme.txt').touch()
    sub = d / 'sub'
    sub.mkdir()
    (sub / 'Movie 2019.mp4').touch()
    return d


def test_scan_finds_videos_recursively(media_dir):
    s = MediaScanner(str(media_dir))
    files = s.scan()
    names = [f.filename for f in files]
    assert sorted(names) == [
        'Movie 2019.mp4',
        'Show 2x03.avi',
        'Show.Name.S01E02.Pilot.720p.mkv',
        'movie 2020 1080p WEBRip x264.mkv',
    ]
    # ordre de parcours : fichiers du dossier courant d'abord (trié, insensible à la casse),
    # puis sous-dossiers dans l'ordre de tri
    assert names[0] == 'movie 2020 1080p WEBRip x264.mkv'
    assert names[-1] == 'Movie 2019.mp4'


def test_scan_missing_dir_returns_empty(tmp_path):
    assert MediaScanner(str(tmp_path / 'absent')).scan() == []


def test_scan_ignores_paths(media_dir):
    s = MediaScanner(str(media_dir), ignored_paths=[str(media_dir / 'sub')])
    names = [f.filename for f in s.scan()]
    assert 'Movie 2019.mp4' not in names
    assert 'movie 2020 1080p WEBRip x264.mkv' in names


def test_infer_media_type_tv_patterns():
    s = MediaScanner()
    assert s._infer_media_type('Show.S01E02.mkv') == 'tv'
    assert s._infer_media_type('show s03e10.mkv') == 'tv'
    assert s._infer_media_type('Show.1x02.mkv') == 'tv'
    assert s._infer_media_type('Show.Season 2.mkv') == 'tv'
    assert s._infer_media_type('Movie 2020.mkv') == 'movie'


def test_extract_episode_info():
    s = MediaScanner()
    m = MediaFile(filename='x', path='x', media_type='tv')
    s._extract_episode_info('Show.S01E02.mkv', m)
    assert m.season == 1
    assert m.episode == 2

    m2 = MediaFile(filename='x', path='x', media_type='tv')
    s._extract_episode_info('Show 1x03.mkv', m2)
    assert m2.season == 1
    assert m2.episode == 3

    m3 = MediaFile(filename='x', path='x', media_type='tv')
    s._extract_episode_info('Show.mkv', m3)
    assert m3.season is None
    assert m3.episode is None


def test_extract_year():
    s = MediaScanner()
    assert s._extract_year('Movie (2020) 1080p.mkv') == 2020
    assert s._extract_year('Movie.1999.mkv') == 1999
    assert s._extract_year('Movie.mkv') is None


def test_extract_title_movie_with_year():
    s = MediaScanner()
    assert s._extract_title('Movie 2020 1080p WEBRip x264.mkv', 'movie') == 'Movie'


def test_extract_title_tv_episode():
    s = MediaScanner()
    title = s._extract_title('Show.Name.S01E02.Pilot.720p.mkv', 'tv')
    assert title == 'Show Name'


def test_extract_title_fallback_no_year():
    s = MediaScanner()
    title = s._extract_title('Movie 1080p WEBRip x264.mkv', 'movie')
    assert title == 'Movie'


def test_is_video_extensions():
    s = MediaScanner()
    for ext in ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm'):
        assert s._is_video(f'file{ext}'), ext
    assert not s._is_video('file.txt')
    assert not s._is_video('file.MP4'.replace('MP4', 'mp4x'))
