"""Génération des noms de fichiers/dossiers (port du JS `generateFilename`).

Le renommage de la bibliothèque est calculé côté serveur pour que la route
de renommage de dossier série reste synchrone : le parsing TVDB (titres
d'épisodes, épisodes manquants, enrichissement) passe en arrière-plan."""
import re
from typing import List

from . import state

# Miroir de LANG_MAP côté JS (i18n des tokens {t:fra} etc.).
# TVDB renvoie les traductions avec des codes ISO 639-2/T à 3 lettres
# (eng, fra, deu...) : ce dictionnaire permet d'accepter les codes à 2 lettres.
_LANG_MAP = {
    'en': 'eng', 'fr': 'fra', 'de': 'deu', 'es': 'spa', 'it': 'ita', 'pt': 'por',
    'ru': 'rus', 'ja': 'jpn', 'ko': 'kor', 'zh': 'zho', 'ar': 'ara', 'pl': 'pol',
    'nl': 'nld', 'sv': 'swe', 'no': 'nor', 'da': 'dan', 'fi': 'fin', 'tr': 'tur',
    'cs': 'ces', 'hu': 'hun', 'he': 'heb', 'ro': 'ron', 'uk': 'ukr',
    'el': 'ell', 'et': 'est', 'hr': 'hrv', 'lt': 'lit', 'sr': 'srp',
}
_LANG_MAP_INV = {v: k for k, v in _LANG_MAP.items()}
# Tokens traduits via la map de traductions de la SÉRIE ({code: nom de série}).
# t/episode_title sont volontairement exclus : un {t:lang} doit retomber sur le
# titre d'épisode (langue originale), jamais sur le nom de série.
_TRANS_NAMES = ('n', 'title', 'original_title')

# Défaut du nom de dossier série (config `series_folder_format`).
SERIES_FOLDER_DEFAULT = '{n} ({y}) [tvdbid-{tvdbid}]'


def episode_title_languages(template=None) -> List[str]:
    """TVDB language codes requested by localized episode-title tokens."""
    if template is None:
        template = (state.config or {}).get('tv_format', '')
    codes = re.findall(r'\{(?:t|episode_title):([a-zA-Z]{2,3})\}', template or '')
    languages: List[str] = []
    for code in codes:
        language = code.lower()
        languages.append(_LANG_MAP.get(language) or language)
    return list(dict.fromkeys(languages))


def clean_title(title):
    if not title:
        return ''
    t = re.sub(r'[\x3a:-]', ' ', str(title))
    return re.sub(r'\s+', ' ', t).strip()


def _pad(value, width):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        n = 0
    return str(n).zfill(width)


def _translate(translations, code, fallback):
    """Résolution d'un code de langue : code direct, puis 2→3 lettres, puis
    3→2 lettres (TVDB renvoie des codes à 3 lettres, ex. « eng »)."""
    code = code.lower()
    if code in translations:
        return translations[code]
    mapped = _LANG_MAP.get(code)
    if mapped and mapped in translations:
        return translations[mapped]
    unmapped = _LANG_MAP_INV.get(code)
    if unmapped and unmapped in translations:
        return translations[unmapped]
    return fallback or ''


def interpolate(template, vars_):
    """Port exact de `interpolate()` (app.js) : tokens {name} et {name:fmt}.
    fmt = langue 2-3 lettres uniquement pour n/title/original_title (map de
    traductions de la série) ; pour t/episode_title un fmt de langue retombe
    sur le titre d'épisode (jamais le nom de série). Sinon formatage numérique
    0Nd/d. Valeurs vides -> chaîne vide."""
    translations = vars_.get('_translations') or {}
    episode_translations = vars_.get('_episode_translations') or {}

    def _repl(m):
        name, fmt = m.group(1), m.group(2)
        val = vars_.get(name)
        if fmt:
            code = fmt[1:].lower()
            if re.fullmatch(r'[a-zA-Z]{2,3}', code):
                if name in _TRANS_NAMES:
                    return _translate(translations, code, val)
                if name in ('t', 'episode_title'):
                    return _translate(episode_translations, code, val)
        if val is None or str(val).strip() == '' or str(val) == 'None':
            return ''
        s = str(val).strip()
        if fmt:
            f = fmt[1:]
            pad = re.fullmatch(r'0(\d+)d', f)
            if pad:
                return _pad(s, int(pad.group(1)))
            if re.fullmatch(r'\d*d', f):
                return str(int(s) or 0)
        return s

    return re.sub(r'\{([a-zA-Z_]\w*)(:[^}]*)?\}', _repl, template)


def _vars_for(file, details):
    s = file.get('season')
    if s is None:
        s = 1
    e = file.get('episode')
    if e is None:
        e = 1
    cleaned_title = clean_title(details.get('title'))
    cleaned_original = clean_title(details.get('original_title'))
    cleaned_ep = clean_title(details.get('episode_title') or details.get('t'))
    year = details.get('year') or ''
    return {
        'n': cleaned_title, 'title': cleaned_title,
        'original_title': cleaned_original,
        'ny': f"{cleaned_title} ({year})" if cleaned_title and year else cleaned_title,
        'y': year, 'year': year,
        'd': details.get('airdate') or details.get('release_date') or '',
        'airdate': details.get('airdate') or '', 'release_date': details.get('release_date') or '',
        't': cleaned_ep, 'episode_title': cleaned_ep,
        's': s, 'season': s, 'e': e, 'episode': e,
        's00e00': f"S{_pad(s, 2)}E{_pad(e, 2)}",
        'sxe': f"{s}x{_pad(e, 2)}",
        'absolute': details.get('absolute') or '', 'sc': details.get('season_count') or '',
        'director': details.get('director') or '', 'rating': details.get('rating') or '',
        'genres': details.get('genres') or '', 'genre': details.get('genre') or '',
        'runtime': details.get('runtime') or '', 'certification': details.get('certification') or '',
        'network': details.get('network') or '', 'studio': details.get('studio') or '',
        'language': details.get('language') or '', 'country': details.get('country') or '',
        'status': details.get('status') or '',
        'tvdbid': details.get('tvdbid') or str(details.get('id') or ''),
        'imdbid': details.get('imdbid') or details.get('imdb') or '',
        'imdb': details.get('imdbid') or details.get('imdb') or '',
        'tmdbid': details.get('tmdbid') or details.get('tmdb') or '',
        'tmdb': details.get('tmdbid') or details.get('tmdb') or '',
        '_translations': details.get('translations') or {},
        '_episode_translations': details.get('episode_title_translations') or {},
    }


def multi_episode_label(season, episodes):
    """Étiquette SxxExx[-Exx] pour un fichier multi-épisodes (même saison).
    Plage continue → « S01E01-E02 » ; sinon liste chaque épisode."""
    eps = sorted(episodes)
    if len(eps) == 1:
        return f"S{season:02d}E{eps[0]:02d}"
    start, end = eps[0], eps[-1]
    if end - start == len(eps) - 1:
        return f"S{season:02d}E{start:02d}-E{end:02d}"
    return f"S{season:02d}" + '-'.join(f"E{e:02d}" for e in eps)


def multi_episode_label_sxe(season, episodes):
    """Étiquette NxNN[-NN] (format alternatif) pour un multi-épisodes."""
    eps = sorted(episodes)
    if len(eps) == 1:
        return f"{season}x{eps[0]:02d}"
    start, end = eps[0], eps[-1]
    if end - start == len(eps) - 1:
        return f"{season}x{start:02d}-{end:02d}"
    return f"{season}x" + '-'.join(f"{e:02d}" for e in eps)


def generate_filename(filename, media_type, season, episode, details,
                      episode_label=None, sxe_label=None, episode_title=None):
    """Port exact de `generateFilename()` (app.js) : nom final (extension comprise).
    Revenant au nom d'origine si le format produit une chaîne vide.
    `episode_label` / `sxe_label` / `episode_title` surchargent les tokens
    {s00e00} / {sxe} / {t} (fichiers multi-épisodes)."""
    if not filename:
        return ''
    dot = filename.rfind('.')
    ext = filename[dot:] if dot >= 0 else ''
    cfg = state.config or {}
    fmt = cfg.get('movie_format') if media_type == 'movie' else cfg.get('tv_format')
    if not fmt:
        fmt = '{n} ({y})' if media_type == 'movie' else '{n} - {s00e00} - {t}'
    file = {'season': season, 'episode': episode}
    vars_ = _vars_for(file, details)
    if episode_label is not None:
        vars_['s00e00'] = episode_label
    if sxe_label is not None:
        vars_['sxe'] = sxe_label
    if episode_title is not None:
        vars_['t'] = episode_title
        vars_['episode_title'] = episode_title
    result = interpolate(fmt, vars_)
    result = result.replace(':', ' -')
    result = re.sub(r'[<>"\\/\|?*]', '', result)
    result = re.sub(r' - -', ' -', result, count=1)
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'[\s\-.]+$', '', result)
    result = result.strip()
    return (result + ext) if result else filename


def series_folder(details):
    """Rendu du nom de dossier série depuis `series_folder_format`
    (tokens identiques à generate_filename : {n}, {n:en}, {y}, {tvdbid}...).
    Renvoie '' si aucun format n'est défini et le rendu est vide."""
    cfg = state.config or {}
    fmt = (cfg.get('series_folder_format') or '').strip() or SERIES_FOLDER_DEFAULT
    details = details or {}
    cleaned_title = clean_title(details.get('title'))
    cleaned_original = clean_title(details.get('original_title'))
    year = details.get('year') or ''
    tvdb = details.get('tvdbid') or str(details.get('id') or '')
    imdb = details.get('imdbid') or details.get('imdb') or ''
    tmdb = details.get('tmdbid') or details.get('tmdb') or ''
    vars_ = {
        'n': cleaned_title, 'title': cleaned_title,
        'original_title': cleaned_original,
        'ny': f"{cleaned_title} ({year})" if cleaned_title and year else cleaned_title,
        'y': year, 'year': year,
        'tvdbid': tvdb, 'tvdb': tvdb,
        'imdbid': imdb, 'imdb': imdb,
        'tmdbid': tmdb, 'tmdb': tmdb,
        '_translations': details.get('translations') or {},
    }
    result = interpolate(fmt, vars_)
    result = result.replace(':', ' -')
    result = re.sub(r'[<>"\\/\|?*]', '', result)
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'[\s\-.]+$', '', result)
    return result.strip()


def series_folder_name(filename):
    """Port de `seriesFolderName()` (app.js) : extrait le nom de dossier de
    série d'un nom d'épisode complet (ex. « Show - S01E01 - T » -> « Show »)."""
    stem = re.sub(r'\.[^.]*$', '', str(filename or ''))
    m = re.match(r'^(.*?)\s+-\s+(?:S\d{2}E\d{2}|\d+x\d{2})\b.*$', stem, re.I)
    return (m.group(1).strip() if m and m.group(1).strip() else stem)
