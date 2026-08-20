import tvdb_v4_official
import re
import logging
import db
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class TVDBAPIHandler:
    def __init__(self, api_key: str, pin: Optional[str] = None):
        self.api_key = api_key
        self.pin = pin
        try:
            self.client = tvdb_v4_official.TVDB(api_key, pin=pin) if pin else tvdb_v4_official.TVDB(api_key)
            logger.info("TVDB API client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize TVDB client: {e}")
            raise

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _remote_ids(self, data: dict) -> tuple[str, str]:
        ids = data.get('remoteIds') or data.get('remote_ids', [])
        imdb = next((r['id'] for r in ids if r.get('sourceName') == 'IMDB'), '')
        tmdb = next((r['id'] for r in ids if 'TheMovieDB' in r.get('sourceName', '')), '')
        return imdb, tmdb

    def _cert(self, data: dict) -> str:
        ratings = data.get('contentRatings') or []
        for cr in ratings:
            if cr.get('country') in ('usa', 'us', 'USA'):
                return cr.get('name', '')
        return ratings[0].get('name', '') if ratings else ''

    def _poster(self, data: dict, poster_type: int) -> str:
        p = data.get('image', '')
        if not p:
            for art in data.get('artworks', []):
                if art.get('type') == poster_type:
                    return art.get('image', '')
        return p

    def _normalize_year(self, year) -> Optional[int]:
        try:
            return int(year) if year not in (None, '', 'None') else None
        except (TypeError, ValueError):
            return None

    # ── Search ───────────────────────────────────────────────────────────────

    def _parse_search_result(self, result: dict, media_type: str) -> dict:
        air_time = result.get('first_air_time') or result.get('first_air_date', '')
        year = result.get('year') or (air_time.split('-')[0] if air_time else None)
        year = self._normalize_year(year)
        tvdb_id = result.get('tvdb_id') or result.get('id')
        remote_ids = result.get('remote_ids', [])
        imdb = next((r['id'] for r in remote_ids if r.get('sourceName') == 'IMDB'), '')
        tmdb = next((r['id'] for r in remote_ids if 'TheMovieDB' in r.get('sourceName', '')), '')
        return {
            'id': tvdb_id, 'id_tvdb': str(tvdb_id),
            'imdb_id': imdb, 'tmdb_id': tmdb,
            'title': result.get('name', ''), 'year': year,
            'date': air_time,
            'score': result.get('score'),
            'source': 'tvdb', 'type': result.get('type', media_type),
            'overview': result.get('overview', ''),
            'poster': result.get('image_url', ''),
            'translations': result.get('translations', {}),
        }

    def search_series(self, title: str, year: Optional[int] = None) -> List[Dict]:
        try:
            results = []
            filtered = []
            data = self.client.search(title, type="series") or []
            db.usage_bump('tvdb')
            for r in data[:10]:
                try:
                    item = self._parse_search_result(r, 'series')
                    results.append(item)
                    if year and item['year'] and abs(item['year'] - year) > 1:
                        continue
                    filtered.append(item)
                except Exception as e:
                    logger.debug(f"Error parsing series result: {e}")
            picked = filtered or results
            logger.info(f"Found {len(picked)} series for '{title}'")
            return picked
        except Exception as e:
            logger.error(f"Series search error: {e}")
            return []

    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict]:
        try:
            results = []
            filtered = []
            data = self.client.search(title, type="movie") or []
            db.usage_bump('tvdb')
            for r in data[:10]:
                try:
                    item = self._parse_search_result(r, 'movie')
                    item['director'] = r.get('director', '')
                    item['genres'] = ', '.join(r['genres']) if isinstance(r.get('genres'), list) else ''
                    results.append(item)
                    if year and item['year'] and abs(item['year'] - year) > 1:
                        continue
                    filtered.append(item)
                except Exception as e:
                    logger.debug(f"Error parsing movie result: {e}")
            picked = filtered or results
            logger.info(f"Found {len(picked)} movies for '{title}'")
            return picked
        except Exception as e:
            logger.error(f"Movie search error: {e}")
            return []

    # ── Auto search ──────────────────────────────────────────────────────────

    def _looks_like_tv(self, title='', filename='', season=None, episode=None) -> bool:
        if season or episode:
            return True
        patterns = [r'[Ss]\d{1,2}[Ee]\d{1,2}', r'\b\d{1,2}x\d{2}\b', r'\bsea?son\s*\d+\b', r'\bepisode\s*\d+\b']
        return any(re.search(p, f"{title} {filename}", re.IGNORECASE) for p in patterns)

    def _score(self, results, title, year) -> int:
        if not results:
            return -1
        score = len(results)
        top = results[0]
        if str(top.get('title', '')).strip().lower() == str(title or '').strip().lower():
            score += 3
        if year and top.get('year'):
            try:
                if abs(int(top['year']) - int(year)) <= 1:
                    score += 2
            except (TypeError, ValueError):
                pass
        return score

    def search_auto(self, title, year=None, filename='', season=None, episode=None, media_hint='') -> Dict:
        year = self._normalize_year(year)
        movies = self.search_movie(title, year)
        series = self.search_series(title, year)
        is_tv = self._looks_like_tv(title, filename, season, episode)

        ms = self._score(movies, title, year) + (1 if media_hint == 'movie' else 0)
        ss = self._score(series, title, year) + (5 if is_tv else 0) + (1 if media_hint == 'tv' else 0)

        if ss > ms:
            if series:
                return {'media_type': 'tv', 'results': series}
            if movies:
                return {'media_type': 'movie', 'results': movies}
        elif ms > ss:
            if movies:
                return {'media_type': 'movie', 'results': movies}
            if series:
                return {'media_type': 'tv', 'results': series}
        if is_tv and series:
            return {'media_type': 'tv', 'results': series}
        if movies:
            return {'media_type': 'movie', 'results': movies}
        if series:
            return {'media_type': 'tv', 'results': series}
        return {'media_type': 'tv' if is_tv else 'movie', 'results': []}

    # ── Details ──────────────────────────────────────────────────────────────

    def get_series_details(self, series_id: int, search_data: Dict = None) -> Dict:
        try:
            s = self.client.get_series_extended(series_id)
            db.usage_bump('tvdb')
            imdb, tmdb = self._remote_ids(s)
            sd = search_data or {}
            imdb = imdb or sd.get('imdb_id', '')
            tmdb = tmdb or sd.get('tmdb_id', '')
            translations = sd.get('translations', {})

            first_aired = s.get('firstAired') or s.get('first_air_date') or s.get('first_air_time', '')
            year = first_aired[:4] if first_aired else s.get('year', '')
            genres = [g.get('name', '') for g in s.get('genres', [])]

            network = ''
            for co in (s.get('companies') or []):
                if isinstance(co, dict):
                    ctype = co.get('companyType', {}).get('companyTypeName', '') if isinstance(co.get('companyType'), dict) else ''
                    if 'network' in ctype.lower() or not network:
                        network = co.get('name', '')
                        if 'network' in ctype.lower():
                            break

            creators, actors = [], []
            for ch in (s.get('characters') or []):
                ptype, name = ch.get('peopleType', ''), ch.get('personName', '') or ch.get('name', '')
                if not name:
                    continue
                if ptype in ('Creator', 'ShowRunner'):
                    creators.append(name)
                elif ptype in ('Actor', 'Actress') and len(actors) < 5:
                    actors.append(name)

            real_seasons = [x for x in (s.get('seasons') or []) if x.get('type', {}).get('type') == 'official']
            season_count = len(real_seasons) or len(s.get('seasons') or [])

            details = {
                'id': series_id, 'tvdbid': str(series_id),
                'imdbid': imdb, 'imdb': imdb, 'tmdbid': tmdb, 'tmdb': tmdb,
                'title': s.get('name', ''), 'n': s.get('name', ''),
                'original_title': translations.get(s.get('originalLanguage', 'en'), '') or s.get('name', ''),
                'translations': translations,
                'year': str(year) if year else '', 'y': str(year) if year else '',
                'startdate': first_aired,
                'genres': ', '.join(genres), 'genre': genres[0] if genres else '',
                'certification': self._cert(s),
                'language': s.get('originalLanguage', ''),
                'country': s.get('originalCountry', ''),
                'network': network,
                'status': s.get('status', {}).get('name', '') if isinstance(s.get('status'), dict) else '',
                'season_count': season_count, 'sc': season_count,
                'score': s.get('score'), 'rating': None,
                'director': ', '.join(creators),
                'actors': actors, 'actor': actors[0] if actors else '',
                'poster': self._poster(s, 2),
                'overview': '', 'source': 'tvdb',
            }
            logger.info(f"Series: {details['title']} ({details['year']})")
            return details
        except Exception as e:
            logger.error(f"Series details error: {e}")
            return {'id': series_id, 'tvdbid': str(series_id), 'title': '', 'year': '', 'source': 'tvdb'}

    def get_movie_details(self, movie_id: int, search_data: Dict = None) -> Dict:
        try:
            m = self.client.get_movie_extended(movie_id)
            db.usage_bump('tvdb')
            imdb, tmdb = self._remote_ids(m)
            wikidata = next((r['id'] for r in (m.get('remoteIds') or []) if r.get('sourceName') == 'Wikidata'), '')
            sd = search_data or {}
            imdb = imdb or sd.get('imdb_id', '')
            tmdb = tmdb or sd.get('tmdb_id', '')
            translations = sd.get('translations', {})

            year = m.get('year')
            releases = m.get('releases', [])
            if not year and releases:
                year = releases[0].get('date', '')[:4] or None
            release_date = releases[0].get('date', '') if releases else ''

            genres = [g.get('name', '') for g in m.get('genres', [])]
            studio = next((co.get('name', '') for co in (m.get('companies') or []) if isinstance(co, dict)), '')

            directors, actors = [], []
            for ch in (m.get('characters') or []):
                ptype, name = ch.get('peopleType', ''), ch.get('personName', '') or ch.get('name', '')
                if not name:
                    continue
                if ptype == 'Director':
                    directors.append(name)
                elif ptype in ('Actor', 'Actress'):
                    actors.append(name)

            details = {
                'id': movie_id, 'tvdbid': str(movie_id),
                'imdbid': imdb, 'imdb': imdb, 'tmdbid': tmdb, 'tmdb': tmdb,
                'wikidataid': wikidata,
                'title': m.get('name', ''), 'n': m.get('name', ''),
                'original_title': translations.get(m.get('originalLanguage', 'en'), '') or m.get('name', ''),
                'translations': translations,
                'year': str(year) if year else '', 'y': str(year) if year else '',
                'release_date': release_date, 'd': release_date,
                'runtime': m.get('runtime'), 'score': m.get('score'), 'rating': None,
                'genres': ', '.join(genres), 'genre': genres[0] if genres else '',
                'certification': self._cert(m),
                'language': m.get('originalLanguage', ''),
                'country': m.get('originalCountry', ''),
                'studio': studio,
                'director': ', '.join(directors),
                'actors': actors, 'actor': actors[0] if actors else '',
                'poster': self._poster(m, 14),
                'overview': '', 'source': 'tvdb',
                'status': m.get('status', {}).get('name', '') if isinstance(m.get('status'), dict) else '',
            }
            logger.info(f"Movie: {details['title']} ({details['year']})")
            return details
        except Exception as e:
            logger.error(f"Movie details error: {e}")
            return {'id': movie_id, 'tvdbid': str(movie_id), 'title': '', 'year': '', 'source': 'tvdb'}

    def get_series_episodes(self, series_id: int, season: Optional[int] = None, page: int = 0) -> Dict:
        try:
            all_episodes = []
            current_page = page
            total_pages = 1
            while current_page <= 20:
                result = self.client.get_series_episodes(series_id, page=current_page)
                db.usage_bump('tvdb')
                found_season = False
                for ep in result.get('episodes', []):
                    ep_season = ep.get('seasonNumber')
                    if season is not None:
                        if ep_season == season:
                            found_season = True
                            all_episodes.append(self._parse_episode(ep))
                        elif ep_season > season and found_season:
                            return {'series_id': series_id, 'episodes': all_episodes, 'source': 'tvdb'}
                    else:
                        all_episodes.append(self._parse_episode(ep))
                total_pages = result.get('totalPages', 1)
                if current_page >= total_pages - 1:
                    break
                current_page += 1
            return {'series_id': series_id, 'episodes': all_episodes, 'source': 'tvdb'}
        except Exception as e:
            logger.error(f"Series episodes error: {e}")
            return {'series_id': series_id, 'episodes': [], 'source': 'tvdb'}

    def _parse_episode(self, ep: dict) -> dict:
        s, e = ep.get('seasonNumber'), ep.get('number')
        return {
            'id': ep.get('id'), 'tvdbid': str(ep.get('id')),
            'season': s, 's': s, 'episode': e, 'e': e,
            'absolute': ep.get('absoluteNumber'),
            'sxe': f"{s}x{str(e).zfill(2)}" if s and e else None,
            's00e00': f"S{str(s).zfill(2)}E{str(e).zfill(2)}" if s and e else None,
            'title': ep.get('name', ''), 't': ep.get('name', ''),
            'episode_title': ep.get('name', ''),
            'overview': ep.get('overview', ''),
            'airdate': ep.get('aired', ''), 'd': ep.get('aired', ''),
            'runtime': ep.get('runtime'),
            'rating': round(ep.get('score', 0) / 10, 1) if ep.get('score') else None,
            'score': ep.get('score'), 'source': 'tvdb',
            **(({'image': ep['image'], 'poster': ep['image']}) if 'image' in ep else {}),
        }


class APIHandler:
    def __init__(self, config: Dict = None):
        self.config = config or {}
        tvdb_key = self.config.get('tvdb_api_key', '').strip()
        if not tvdb_key:
            candidate = self.config.get('tmdb_api_key', '').strip()
            if candidate and re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', candidate, re.I):
                tvdb_key = candidate
        self.tvdb_api_key = tvdb_key
        pin = self.config.get('tvdb_pin') or None
        if tvdb_key:
            try:
                self.tvdb = TVDBAPIHandler(tvdb_key, pin)
                logger.info(f"TVDB initialized with key: {tvdb_key[:8]}...")
            except Exception as e:
                logger.error(f"Failed to initialize TVDB handler: {e}")
                self.tvdb = None
        else:
            logger.warning("No TVDB API key provided")
            self.tvdb = None

    def _require_tvdb(self):
        if not self.tvdb:
            logger.error("TVDB handler not initialized")
        return self.tvdb

    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict]:
        return self.tvdb.search_movie(title, year) if self._require_tvdb() else []

    def search_tv(self, title: str, year: Optional[int] = None) -> List[Dict]:
        return self.tvdb.search_series(title, year) if self._require_tvdb() else []

    def search_auto(self, title, year=None, filename='', season=None, episode=None, media_hint='') -> Dict:
        if not self._require_tvdb():
            return {'media_type': 'movie', 'results': []}
        return self.tvdb.search_auto(title, year, filename, season, episode, media_hint)

    def get_movie_details(self, movie_id: str, source: str = 'tvdb', search_data: Dict = None) -> Dict:
        if not self._require_tvdb() or source != 'tvdb':
            return {'id': movie_id, 'source': source}
        try:
            return self.tvdb.get_movie_details(int(movie_id), search_data)
        except ValueError:
            return {'id': movie_id, 'source': source}

    def get_tv_details(self, tv_id: str, season: int = 1, episode: int = 1, source: str = 'tvdb', search_data: Dict = None) -> Dict:
        if not self._require_tvdb() or source != 'tvdb':
            return {'id': tv_id, 'source': source}
        try:
            series_id = int(tv_id)
            details = self.tvdb.get_series_details(series_id, search_data)
            details.update({
                'season': season, 's': season, 'episode': episode, 'e': episode,
                'sxe': f"{season}x{str(episode).zfill(2)}",
                's00e00': f"S{str(season).zfill(2)}E{str(episode).zfill(2)}",
            })
            if season and episode:
                try:
                    for ep in self.tvdb.get_series_episodes(series_id, season=season).get('episodes', []):
                        if ep.get('season') == season and ep.get('episode') == episode:
                            details['episode_title'] = ep.get('episode_title', ep.get('title', ''))
                            details['t'] = details['episode_title']
                            details['episode_overview'] = ep.get('overview', '')
                            details['airdate'] = ep.get('airdate', '')
                            details['absolute'] = ep.get('absolute')
                            details['episode_rating'] = ep.get('rating')
                            details['episode_runtime'] = ep.get('runtime')
                            break
                except Exception as e:
                    logger.warning(f"Could not fetch episode S{season}E{episode}: {e}")
            return details
        except ValueError:
            return {'id': tv_id, 'source': source}

    def get_series_episodes(self, series_id: str, season: Optional[int] = None, page: int = 0) -> Dict:
        tvdb = self._require_tvdb()
        if not tvdb:
            return {'series_id': series_id, 'episodes': [], 'source': 'tvdb'}
        try:
            return tvdb.get_series_episodes(int(series_id), season, page)
        except ValueError:
            return {'series_id': series_id, 'episodes': [], 'source': 'tvdb'}
