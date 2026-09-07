"""Tests du module de génération de noms (port du JS generateFilename)."""
from src import naming


def test_generate_filename_default_tv_format():
    out = naming.generate_filename('x.mkv', 'tv', 2, 5, {
        'title': 'My Show', 'year': 2019, 'tvdbid': '42', 'imdb': 'tt1',
        'episode_title': 'Pilot',
    })
    assert out == 'My Show - S02E05 - Pilot.mkv'


def test_generate_filename_episode_title_absent():
    out = naming.generate_filename('x.mkv', 'tv', 1, 1, {'title': 'Show', 'year': 2020})
    assert out == 'Show - S01E01.mkv'


def test_generate_filename_movie():
    out = naming.generate_filename('x.mkv', 'movie', 1, 1, {'title': 'Movie', 'year': 2021})
    assert out == 'Movie (2021).mkv'


def test_generate_filename_translated_token():
    out = naming.generate_filename('x.mkv', 'tv', 1, 1, {
        'title': 'Show', 'year': 2020,
        'translations': {'fra': 'Spectacle', 'en': 'Show'},
    })
    # Format par défaut sans token de traduction : inchangé.
    assert out == 'Show - S01E01.mkv'


def test_generate_filename_only_s00e00_remains():
    # Format par défaut avec détails vides : seuls les tokens numériques
    # (s00e00) restent ; le séparateur orphelin est conservé (comportement JS).
    out = naming.generate_filename('original.mkv', 'tv', 1, 1, {})
    assert out == '- S01E01.mkv'


def test_series_folder_name_with_ids():
    assert naming.series_folder_name('My Show (2019) [tvdbid-42] - S01E01 - Pilot.mkv') == 'My Show (2019) [tvdbid-42]'


def test_series_folder_name_plain():
    assert naming.series_folder_name('My Show - S01E01 - Pilot.mkv') == 'My Show'
