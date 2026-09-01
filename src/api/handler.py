"""Façade API : déléguée à TVDB (les clés sont lues depuis la config)."""
import re
import logging
from typing import Dict, List, Optional

from .tvdb import TVDBAPIHandler

logger = logging.getLogger(__name__)


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
