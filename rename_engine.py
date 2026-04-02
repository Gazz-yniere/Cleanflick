import re
from typing import Dict
from pathlib import Path


class RenameEngine:
    """Moteur de renommage selon le format FileBot"""

    def __init__(self, config: Dict):
        self.config = config
        self.movie_format = config.get("movie_format", "{n} ({y})")
        self.tv_format = config.get("tv_format", "{n} - {s00e00} - {t}")

    def generate_name(self, file_info: Dict, details: Dict, extension: str) -> str:
        """Génère le nom selon le type (movie/tv) et le format configuré"""
        is_tv = file_info.get("media_type") == "tv"
        fmt = self.tv_format if is_tv else self.movie_format

        season = file_info.get("season") or 1
        episode = file_info.get("episode") or 1

        vars = {
            # Identifiants courts Filebot
            "n": details.get("title") or details.get("n") or "",
            "y": details.get("year") or details.get("y") or "",
            "t": details.get("episode_title") or details.get("t") or "",
            "d": details.get("airdate") or details.get("release_date") or details.get("d") or "",
            "s": season,
            "e": episode,
            "absolute": details.get("absolute") or "",

            # Formes longues
            "title": details.get("title") or "",
            "year": details.get("year") or "",
            "episode_title": details.get("episode_title") or "",
            "season": season,
            "episode": episode,
            "airdate": details.get("airdate") or "",
            "release_date": details.get("release_date") or "",

            # Formats composés
            "sxe": f"{season}x{str(episode).zfill(2)}",
            "s00e00": f"S{str(season).zfill(2)}E{str(episode).zfill(2)}",
            "ny": f"{details.get('title', '')} ({details.get('year', '')})" if details.get("title") and details.get("year") else details.get("title", ""),

            # Métadonnées
            "director": details.get("director") or "",
            "rating": details.get("rating") or "",
            "score": details.get("score") or "",
            "genres": details.get("genres") or "",
            "genre": details.get("genre") or "",
            "runtime": details.get("runtime") or "",
            "overview": details.get("overview") or "",
            "network": details.get("network") or "",
            "studio": details.get("studio") or "",
            "status": details.get("status") or "",
            "language": details.get("language") or "",
            "country": details.get("country") or "",
            "certification": details.get("certification") or "",
            "sc": details.get("season_count") or "",
            "tvdbid": details.get("tvdbid") or str(details.get("id", "")),
            "imdb": details.get("imdb") or details.get("imdbid") or "",
            "imdbid": details.get("imdbid") or details.get("imdb") or "",
            "tmdb": details.get("tmdb") or details.get("tmdbid") or "",
            "tmdbid": details.get("tmdbid") or details.get("tmdb") or "",
            "_translations": details.get("translations") or {},
        }

        result = self._interpolate(fmt, vars)
        result = self._sanitize(result)
        return (result + extension) if result else (
            (details.get("title", "Unknown") or "Unknown") + extension
        )

    LANG_MAP = {
        'fr':'fra','de':'deu','es':'spa','it':'ita','pt':'por','ru':'rus',
        'ja':'jpn','ko':'kor','zh':'zho','ar':'ara','pl':'pol','nl':'nld',
        'sv':'swe','no':'nor','da':'dan','fi':'fin','tr':'tur','cs':'ces',
        'hu':'hun','he':'heb','ro':'ron','uk':'ukr',
    }

    def _interpolate(self, template: str, variables: Dict) -> str:
        """Remplace {var} et {var:02d} et {n:fr} dans le template"""
        def replace(m):
            name, fmt = m.group(1), m.group(2)
            # Support {n:fr} = traduction
            if name == 'n' and fmt:
                code = fmt[1:].lower()
                trans = variables.get('_translations') or {}
                val = trans.get(code) or trans.get(self.LANG_MAP.get(code, '')) or ''
                return val or variables.get('n', '')
            val = variables.get(name)
            if val is None or str(val).strip() in ("", "None"):
                return ""
            val = str(val).strip()
            if fmt:
                fmt = fmt[1:]
                pad = re.match(r"^0(\d+)d$", fmt)
                if pad:
                    return str(int(val) if val.isdigit() else 0).zfill(int(pad.group(1)))
                if re.match(r"^\d*d$", fmt):
                    return str(int(val) if val.isdigit() else 0)
            return val

        return re.sub(r"\{([a-zA-Z_]\w*)(:[^}]*)?\}", replace, template)

    def _sanitize(self, filename: str) -> str:
        filename = filename.replace(':', ' -')          # ':' -> ' -' (titres avec sous-titres)
        filename = re.sub(r'[<>"/\\|?*]', '', filename) # autres caractères interdits Windows
        filename = re.sub(r' -\s*-', ' -', filename)    # éviter ' - -'
        filename = re.sub(r"\s+", " ", filename).strip()
        filename = re.sub(r"[\s\-\.]+$", "", filename)
        return filename

    # Compatibilité ancienne interface
    def generate_movie_name(self, data: Dict, extension: str = ".mp4") -> str:
        return self.generate_name({"media_type": "movie"}, data, extension)

    def generate_tv_name(self, data: Dict, season: int = 1, episode: int = 1, extension: str = ".mp4") -> str:
        return self.generate_name({"media_type": "tv", "season": season, "episode": episode}, data, extension)
