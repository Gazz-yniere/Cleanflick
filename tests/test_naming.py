"""Tests du module de génération de noms (port du JS generateFilename)."""
from src import naming, state


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


def test_interpolate_lang_two_letter_code():
    # TVDB renvoie des clés à 3 lettres : {n:en} doit résoudre via en→eng.
    out = naming.interpolate('{n:en}', {'n': 'Show', '_translations': {'eng': 'Show EN', 'fra': 'Spectacle'}})
    assert out == 'Show EN'


def test_interpolate_lang_three_letter_code():
    out = naming.interpolate('{n:fra}', {'n': 'Show', '_translations': {'fra': 'Spectacle'}})
    assert out == 'Spectacle'


def test_interpolate_lang_fallback_to_default():
    # Langue absente des traductions : retour au titre par défaut.
    out = naming.interpolate('{n:de}', {'n': 'Show', '_translations': {'eng': 'Show EN'}})
    assert out == 'Show'


def test_interpolate_original_title_token():
    out = naming.interpolate('{original_title}', {'original_title': 'Original'})
    assert out == 'Original'


def test_interpolate_episode_title_lang_falls_back_to_episode_title():
    # Regression : {t:en} doit retourner le titre d'épisode (langue originale),
    # JAMAIS le nom de série présent dans la map de traductions de la série.
    out = naming.interpolate('{t:en}', {
        't': 'Rouge : la Passion',
        '_translations': {'eng': 'Sonic X'},
    })
    assert out == 'Rouge : la Passion'


def test_interpolate_episode_title_token_no_lang():
    out = naming.interpolate('{t}', {'t': 'Rouge : la Passion', '_translations': {'eng': 'Sonic X'}})
    assert out == 'Rouge : la Passion'


def test_episode_title_language_tokens_map_to_tvdb_codes():
    assert naming.episode_title_languages(
        '{t:en} - {episode_title:fr} - {t:EN}'
    ) == ['eng', 'fra']


def test_interpolate_episode_title_languages():
    out = naming.interpolate('{t:en} / {episode_title:fr}', {
        't': 'Original title',
        'episode_title': 'Original title',
        '_episode_translations': {'eng': 'English title', 'fra': 'Titre français'},
    })
    assert out == 'English title / Titre français'


def test_generate_filename_user_tv_format_uses_episode_title(monkeypatch):
    # Format réel de l'utilisateur : {n:en} ({y}) [tvdbid-{tvdbid}] - {s00e00} - {t:en}
    monkeypatch.setattr(state, 'config', {
        'tv_format': '{n:en} ({y}) [tvdbid-{tvdbid}] - {s00e00} - {t:en}'
    })
    out = naming.generate_filename('Sonic X.avi', 'tv', 1, 1, {
        'title': 'Sonic X', 'year': 2003, 'tvdbid': '72459',
        'translations': {'eng': 'Sonic X'},
        'episode_title': 'Rouge : la Passion',
    })
    assert out == 'Sonic X (2003) [tvdbid-72459] - S01E01 - Rouge la Passion.avi'


def test_generate_filename_only_s00e00_remains():
    # Format par défaut avec détails vides : seuls les tokens numériques
    # (s00e00) restent ; le séparateur orphelin est conservé (comportement JS).
    out = naming.generate_filename('original.mkv', 'tv', 1, 1, {})
    assert out == '- S01E01.mkv'


def test_series_folder_renders_user_format(monkeypatch):
    monkeypatch.setattr(state, 'config', {
        'series_folder_format': '{n:en} ({y}) [tvdbid-{tvdbid}]'
    })
    out = naming.series_folder({
        'title': 'The Boys', 'year': 2019, 'tvdbid': 269096,
        'translations': {'eng': 'The Boys', 'fra': 'Les Garçons'},
    })
    assert out == 'The Boys (2019) [tvdbid-269096]'


def test_series_folder_translated_token(monkeypatch):
    monkeypatch.setattr(state, 'config', {'series_folder_format': '{n:fra} ({y})'})
    out = naming.series_folder({
        'title': 'The Boys', 'year': 2019, 'tvdbid': '269096',
        'translations': {'fra': 'Les Garçons'},
    })
    assert out == 'Les Garçons (2019)'


def test_series_folder_default_when_config_empty(monkeypatch):
    # Format vide → défaut « {n} ({y}) [tvdbid-{tvdbid}] ».
    monkeypatch.setattr(state, 'config', {'series_folder_format': ''})
    out = naming.series_folder({'title': 'Show', 'year': 2020, 'tvdbid': '42'})
    assert out == 'Show (2020) [tvdbid-42]'


def test_series_folder_sanitizes_illegal_chars(monkeypatch):
    monkeypatch.setattr(state, 'config', {'series_folder_format': '{n} ({y})'})
    out = naming.series_folder({'title': 'Show: Origins', 'year': 2021})
    # clean_title remplace « : » par espace (parité avec generate_filename).
    assert out == 'Show Origins (2021)'


def test_series_folder_name_with_ids():
    assert naming.series_folder_name('My Show (2019) [tvdbid-42] - S01E01 - Pilot.mkv') == 'My Show (2019) [tvdbid-42]'


def test_series_folder_name_plain():
    assert naming.series_folder_name('My Show - S01E01 - Pilot.mkv') == 'My Show'


def test_multi_episode_label_range():
    assert naming.multi_episode_label(1, [1, 2]) == 'S01E01-E02'
    assert naming.multi_episode_label(3, [10, 11, 12]) == 'S03E10-E12'


def test_multi_episode_label_single():
    assert naming.multi_episode_label(1, [5]) == 'S01E05'


def test_multi_episode_label_non_contiguous():
    assert naming.multi_episode_label(1, [1, 3, 5]) == 'S01E01-E03-E05'


def test_multi_episode_label_sxe_range():
    assert naming.multi_episode_label_sxe(1, [1, 2]) == '1x01-02'
    assert naming.multi_episode_label_sxe(1, [5]) == '1x05'


def test_generate_filename_multi_episode():
    out = naming.generate_filename('x.mkv', 'tv', 1, 1, {
        'title': 'My Show', 'year': 2019,
    }, episode_label='S01E01-E02', episode_title='Pilot & Second')
    assert out == 'My Show - S01E01-E02 - Pilot & Second.mkv'


def test_generate_filename_multi_episode_no_title():
    out = naming.generate_filename('x.mkv', 'tv', 1, 1, {
        'title': 'My Show',
    }, episode_label='S01E01-E02')
    assert out == 'My Show - S01E01-E02.mkv'
