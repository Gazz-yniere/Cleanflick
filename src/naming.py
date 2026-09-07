"""Génération des noms de fichiers/dossiers (port du JS `generateFilename`).

Le renommage de la bibliothèque est calculé côté serveur pour que la route
de renommage de dossier série reste synchrone : le parsing TVDB (titres
d'épisodes, épisodes manquants, enrichissement) passe en arrière-plan."""
import re

from . import state

# Miroir de LANG_MAP côté JS (i18n des tokens {t:fra} etc.)
_LANG_MAP = {
    'fr': 'fra', 'de': 'deu', 'es': 'spa', 'it': 'ita', 'pt': 'por', 'ru': 'rus',
    'ja': 'jpn', 'ko': 'kor', 'zh': 'zho', 'ar': 'ara', 'pl': 'pol', 'nl': 'nld',
    'sv': 'swe', 'no': 'nor', 'da': 'dan', 'fi': 'fin', 'tr': 'tur', 'cs': 'ces',
    'hu': 'hun', 'he': 'heb', 'ro': 'ron', 'uk': 'ukr',
}
_TRANS_NAMES = ('n', 'title', 'original_title', 't', 'episode_title')


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


def interpolate(template, vars_):
    """Port exact de `interpolate()` (app.js) : tokens {name} et {name:fmt}
    (fmt = langue 2-3 lettres pour n/title/original_title/t/episode_title,
    sinon formatage numérique 0Nd/d). Valeurs vides -> chaîne vide."""
    translations = vars_.get('_translations') or {}

    def _repl(m):
        name, fmt = m.group(1), m.group(2)
        val = vars_.get(name)
        if fmt:
            code = fmt[1:].lower()
            if re.fullmatch(r'[a-zA-Z]{2,3}', code) and name in _TRANS_NAMES:
                return translations.get(code) or translations.get(_LANG_MAP.get(code)) or val or ''
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
    s = file.get('season') or 1
    e = file.get('episode') or 1
    cleaned_title = clean_title(details.get('title'))
    cleaned_ep = clean_title(details.get('episode_title') or details.get('t'))
    year = details.get('year') or ''
    return {
        'n': cleaned_title, 'title': cleaned_title,
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
    }


def generate_filename(filename, media_type, season, episode, details):
    """Port exact de `generateFilename()` (app.js) : nom final (extension comprise).
    Revenant au nom d'origine si le format produit une chaîne vide."""
    if not filename:
        return ''
    dot = filename.rfind('.')
    ext = filename[dot:] if dot >= 0 else ''
    cfg = state.config or {}
    fmt = cfg.get('movie_format') if media_type == 'movie' else cfg.get('tv_format')
    if not fmt:
        fmt = '{n} ({y})' if media_type == 'movie' else '{n} - {s00e00} - {t}'
    file = {'season': season, 'episode': episode}
    result = interpolate(fmt, _vars_for(file, details))
    result = result.replace(':', ' -')
    result = re.sub(r'[<>"\\/\|?*]', '', result)
    result = re.sub(r' - -', ' -', result, count=1)
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'[\s\-.]+$', '', result)
    result = result.strip()
    return (result + ext) if result else filename


def series_folder_name(filename):
    """Port de `seriesFolderName()` (app.js) : extrait le nom de dossier de
    série d'un nom d'épisode complet (ex. « Show - S01E01 - T » -> « Show »)."""
    stem = re.sub(r'\.[^.]*$', '', str(filename or ''))
    m = re.match(r'^(.*?)\s+-\s+(?:S\d{2}E\d{2}|\d+x\d{2})\b.*$', stem, re.I)
    return (m.group(1).strip() if m and m.group(1).strip() else stem)
