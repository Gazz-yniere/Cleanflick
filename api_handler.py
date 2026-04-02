import tvdb_v4_official
import time
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class TVDBAPIHandler:
    """Gestionnaire API TVDB v4 officiel"""
    
    def __init__(self, api_key: str, pin: Optional[str] = None):
        """
        Initialise le client TVDB
        
        Args:
            api_key: Clé API TVDB
            pin: PIN utilisateur (optionnel)
        """
        self.api_key = api_key
        self.pin = pin
        try:
            if pin:
                self.client = tvdb_v4_official.TVDB(api_key, pin=pin)
            else:
                self.client = tvdb_v4_official.TVDB(api_key)
            logger.info("TVDB API client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize TVDB client: {e}")
            raise
    
    def search_series(self, title: str, year: Optional[int] = None) -> List[Dict]:
        try:
            search_results = self.client.search(title, type="series")
            if not search_results:
                return []
            
            results = []
            for result in search_results[:10]:
                try:
                    # first_air_time est le bon champ (pas first_air_date)
                    air_time = result.get('first_air_time') or result.get('first_air_date', '')
                    result_year = result.get('year') or (air_time.split('-')[0] if air_time else None)
                    if result_year:
                        result_year = int(result_year)
                    
                    if year and result_year and abs(result_year - year) > 1:
                        continue
                    
                    tvdb_id = result.get('tvdb_id') or result.get('id')
                    remote_ids = result.get('remote_ids', [])
                    imdb_id = next((r['id'] for r in remote_ids if r.get('sourceName') == 'IMDB'), '')
                    tmdb_id = next((r['id'] for r in remote_ids if 'TheMovieDB' in r.get('sourceName', '')), '')
                    results.append({
                        'id': tvdb_id,
                        'id_tvdb': str(tvdb_id),
                        'imdb_id': imdb_id,
                        'tmdb_id': tmdb_id,
                        'title': result.get('name', ''),
                        'year': result_year,
                        'source': 'tvdb',
                        'type': result.get('type', 'series'),
                        'overview': result.get('overview', ''),
                        'poster': result.get('image_url', ''),
                        'translations': result.get('translations', {}),
                        'url': f"https://www.thetvdb.com/series/{tvdb_id}"
                    })
                except Exception as e:
                    logger.debug(f"Error parsing series result: {e}")
                    continue
            
            logger.info(f"Found {len(results)} series for '{title}'")
            return results
        except Exception as e:
            logger.error(f"Series search error: {e}")
            return []
    
    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict]:
        try:
            search_results = self.client.search(title, type="movie")
            if not search_results:
                return []
            
            results = []
            for result in search_results[:10]:
                try:
                    # 'year' est directement disponible dans les résultats de recherche
                    result_year = result.get('year')
                    if result_year:
                        result_year = int(result_year)
                    # Fallback sur first_air_time
                    if not result_year:
                        air_time = result.get('first_air_time', '')
                        result_year = int(air_time.split('-')[0]) if air_time else None
                    
                    if year and result_year and abs(result_year - year) > 1:
                        continue
                    
                    tvdb_id = result.get('tvdb_id') or result.get('id')
                    # Extraire les IDs externes depuis remote_ids
                    remote_ids = result.get('remote_ids', [])
                    imdb_id = next((r['id'] for r in remote_ids if r.get('sourceName') == 'IMDB'), '')
                    tmdb_id = next((r['id'] for r in remote_ids if 'TheMovieDB' in r.get('sourceName', '')), '')
                    results.append({
                        'id': tvdb_id,
                        'id_tvdb': str(tvdb_id),
                        'imdb_id': imdb_id,
                        'tmdb_id': tmdb_id,
                        'title': result.get('name', ''),
                        'year': result_year,
                        'source': 'tvdb',
                        'type': result.get('type', 'movie'),
                        'overview': result.get('overview', ''),
                        'poster': result.get('image_url', ''),
                        'director': result.get('director', ''),
                        'genres': ', '.join(result.get('genres', [])) if isinstance(result.get('genres'), list) else '',
                        'translations': result.get('translations', {}),
                        'url': f"https://www.thetvdb.com/movies/{tvdb_id}"
                    })
                except Exception as e:
                    logger.debug(f"Error parsing movie result: {e}")
                    continue
            
            logger.info(f"Returned {len(results)} movies for '{title}'")
            return results
        except Exception as e:
            logger.error(f"Movie search error: {e}", exc_info=True)
            return []
    
    def get_series_extended(self, series_id: int) -> Dict:
        """
        Récupère les détails complets d'une série (avec saisons et épisodes)
        
        Args:
            series_id: ID TVDB de la série
            
        Returns:
            Dictionnaire avec détails étendus
        """
        try:
            series = self.client.get_series_extended(series_id)
            
            details = {
                'id': series_id,
                'id_tvdb': str(series_id),
                'title': series.get('name', ''),
                'overview': series.get('overview', ''),
                'status': series.get('status', {}).get('name', ''),
                'year': series.get('first_air_date', '')[:4] if series.get('first_air_date') else None,
                'source': 'tvdb',
                'url': f"https://www.thetvdb.com/series/{series_id}",
                'seasons': []
            }
            
            # Ajouter les saisons
            if 'seasons' in series:
                for season in series['seasons']:
                    season_info = {
                        'id': season.get('id'),
                        'number': season.get('number'),
                        'type': season.get('type', {}).get('name', 'Aired Order'),
                        'episodes': []
                    }
                    details['seasons'].append(season_info)
            
            # Ajouter les images
            if 'artworks' in series:
                for artwork in series['artworks']:
                    if artwork.get('type') == 1:  # Type 1 = Poster
                        details['poster'] = artwork.get('image', '')
                        break
            
            logger.info(f"Retrieved series details for '{details.get('title', series_id)}'")
            return details
            
        except Exception as e:
            logger.error(f"Series extended details error: {e}")
            return {'id': series_id, 'id_tvdb': str(series_id), 'source': 'tvdb'}
    
    def get_series_details(self, series_id: int, search_data: Dict = None) -> Dict:
        try:
            series = self.client.get_series_extended(series_id)
            
            # remoteIds
            remote_ids = series.get('remoteIds') or []
            imdb_id = next((r['id'] for r in remote_ids if r.get('sourceName') == 'IMDB'), '')
            tmdb_id = next((r['id'] for r in remote_ids if 'TheMovieDB' in r.get('sourceName', '')), '')
            if not imdb_id and search_data:
                imdb_id = search_data.get('imdb_id', '')
            if not tmdb_id and search_data:
                tmdb_id = search_data.get('tmdb_id', '')
            
            translations = (search_data or {}).get('translations', {})
            orig_lang = series.get('originalLanguage', 'en')
            original_title = translations.get(orig_lang, '') or series.get('name', '')
            
            # firstAired est le bon champ dans extended
            first_aired = series.get('firstAired') or series.get('first_air_date') or series.get('first_air_time', '')
            year = first_aired[:4] if first_aired else series.get('year', '')
            
            genres_list = [g.get('name', '') for g in series.get('genres', [])]
            
            # certification
            cert = ''
            for cr in (series.get('contentRatings') or []):
                if cr.get('country') in ('usa', 'us', 'USA'):
                    cert = cr.get('name', '')
                    break
            if not cert and series.get('contentRatings'):
                cert = series['contentRatings'][0].get('name', '')
            
            # network depuis companies
            network = ''
            for company in (series.get('companies') or []):
                if isinstance(company, dict):
                    ctype = company.get('companyType', {}).get('companyTypeName', '') if isinstance(company.get('companyType'), dict) else ''
                    if 'network' in ctype.lower() or not network:
                        network = company.get('name', '')
                        if 'network' in ctype.lower():
                            break
            
            # poster
            poster = series.get('image', '')
            if not poster:
                for art in series.get('artworks', []):
                    if art.get('type') == 2:  # type 2 = series poster
                        poster = art.get('image', '')
                        break
            
            # actors/creators depuis characters
            creators, actors = [], []
            for char in (series.get('characters') or []):
                ptype = char.get('peopleType', '')
                name = char.get('personName', '') or char.get('name', '')
                if not name:
                    continue
                if ptype in ('Creator', 'ShowRunner'):
                    creators.append(name)
                elif ptype in ('Actor', 'Actress') and len(actors) < 5:
                    actors.append(name)
            
            # saisons (exclure les saisons spéciales type=0)
            real_seasons = [s for s in (series.get('seasons') or []) if s.get('type', {}).get('type') == 'official']
            season_count = len(real_seasons) if real_seasons else len(series.get('seasons') or [])
            
            details = {
                'id': series_id,
                'tvdbid': str(series_id),
                'imdbid': imdb_id,
                'imdb': imdb_id,
                'tmdbid': tmdb_id,
                'tmdb': tmdb_id,
                'title': series.get('name', ''),
                'n': series.get('name', ''),
                'original_title': original_title,
                'translations': translations,
                'year': str(year) if year else '',
                'y': str(year) if year else '',
                'startdate': first_aired,
                'genres': ', '.join(genres_list),
                'genre': genres_list[0] if genres_list else '',
                'certification': cert,
                'language': orig_lang,
                'country': series.get('originalCountry', ''),
                'network': network,
                'status': series.get('status', {}).get('name', '') if isinstance(series.get('status'), dict) else '',
                'season_count': season_count,
                'sc': season_count,
                'score': series.get('score'),
                'rating': None,
                'director': ', '.join(creators) if creators else '',
                'actors': actors,
                'actor': actors[0] if actors else '',
                'poster': poster,
                'overview': '',
                'source': 'tvdb',
                'url': f"https://www.thetvdb.com/series/{series_id}"
            }
            
            logger.info(f"Series details: {details['title']} ({details['year']}) network={details['network']}")
            return details
        except Exception as e:
            logger.error(f"Series details error: {e}")
            return {'id': series_id, 'tvdbid': str(series_id), 'title': '', 'year': '', 'source': 'tvdb'}
    
    def get_movie_details(self, movie_id: int, search_data: Dict = None) -> Dict:
        try:
            movie = self.client.get_movie_extended(movie_id)
            
            # remoteIds depuis extended (plus complet que search)
            remote_ids = movie.get('remoteIds') or []
            imdb_id = next((r['id'] for r in remote_ids if r.get('sourceName') == 'IMDB'), '')
            tmdb_id = next((r['id'] for r in remote_ids if 'TheMovieDB' in r.get('sourceName', '')), '')
            wikidata_id = next((r['id'] for r in remote_ids if r.get('sourceName') == 'Wikidata'), '')
            # Fallback depuis search_data si extended n'a pas les IDs
            if not imdb_id and search_data:
                imdb_id = search_data.get('imdb_id', '')
            if not tmdb_id and search_data:
                tmdb_id = search_data.get('tmdb_id', '')
            
            # Translations depuis search_data (plus complètes)
            translations = (search_data or {}).get('translations', {})
            orig_lang = movie.get('originalLanguage', 'en')
            original_title = translations.get(orig_lang, '') or movie.get('name', '')
            
            # year: champ direct, sinon releases[0]['date']
            year = movie.get('year')
            if not year:
                releases = movie.get('releases', [])
                if releases:
                    year = releases[0].get('date', '')[:4] or None
            
            # release_date depuis releases
            release_date = ''
            releases = movie.get('releases', [])
            if releases:
                release_date = releases[0].get('date', '')
            
            # genres
            genres_list = [g.get('name', '') for g in movie.get('genres', [])]
            
            # certification
            cert = ''
            for cr in (movie.get('contentRatings') or []):
                if cr.get('country') in ('usa', 'us', 'USA'):
                    cert = cr.get('name', '')
                    break
            if not cert and movie.get('contentRatings'):
                cert = movie['contentRatings'][0].get('name', '')
            
            # country / language
            country = movie.get('originalCountry', '')
            language = movie.get('originalLanguage', 'en')
            
            # studios
            studio = ''
            for company in (movie.get('companies') or []):
                if isinstance(company, dict):
                    studio = company.get('name', '')
                    break
            
            # poster
            poster = movie.get('image', '')
            if not poster:
                for art in movie.get('artworks', []):
                    if art.get('type') == 14:  # type 14 = movie poster
                        poster = art.get('image', '')
                        break
            
            # characters: director/actors depuis le champ 'director' de la recherche
            # get_movie_extended retourne characters avec peopleType
            directors, actors = [], []
            for char in (movie.get('characters') or []):
                ptype = char.get('peopleType', '')
                name = char.get('personName', '') or char.get('name', '')
                if not name:
                    continue
                if ptype == 'Director':
                    directors.append(name)
                elif ptype in ('Actor', 'Actress'):
                    actors.append(name)
            
            details = {
                'id': movie_id,
                'tvdbid': str(movie_id),
                'imdbid': imdb_id,
                'imdb': imdb_id,
                'tmdbid': tmdb_id,
                'tmdb': tmdb_id,
                'wikidataid': wikidata_id,
                'title': movie.get('name', ''),
                'n': movie.get('name', ''),
                'original_title': original_title,
                'translations': translations,
                'year': str(year) if year else '',
                'y': str(year) if year else '',
                'release_date': release_date,
                'd': release_date,
                'runtime': movie.get('runtime'),
                'score': movie.get('score'),
                'rating': None,
                'genres': ', '.join(genres_list),
                'genre': genres_list[0] if genres_list else '',
                'certification': cert,
                'language': orig_lang,
                'country': country,
                'studio': studio,
                'director': ', '.join(directors) if directors else '',
                'actors': actors,
                'actor': actors[0] if actors else '',
                'poster': poster,
                'overview': '',
                'status': movie.get('status', {}).get('name', '') if isinstance(movie.get('status'), dict) else '',
                'source': 'tvdb',
                'url': f"https://www.thetvdb.com/movies/{movie_id}"
            }
            
            logger.info(f"Movie details: {details['title']} ({details['year']}) dir={details['director']}")
            return details
        except Exception as e:
            logger.error(f"Movie details error: {e}", exc_info=True)
            return {'id': movie_id, 'tvdbid': str(movie_id), 'title': '', 'year': '', 'source': 'tvdb'}
    
    def get_series_episodes(self, series_id: int, season: Optional[int] = None, page: int = 0) -> Dict:
        """
        Récupère les épisodes d'une série avec tous les détails Filebot
        Supporte la pagination pour trouver l'épisode demandé
        
        Args:
            series_id: ID TVDB de la série
            season: Numéro de saison (optionnel)
            page: Numéro de page (par défaut 0)
            
        Returns:
            Dictionnaire avec informations et épisodes
        """
        try:
            current_page = page
            all_episodes = []
            
            # Faire au moins un appel
            while True:
                result = self.client.get_series_episodes(series_id, page=current_page)
                
                found_season = False
                if 'episodes' in result:
                    for episode in result['episodes']:
                        ep_season = episode.get('seasonNumber')
                        
                        # Si on cherche une saison spécifique
                        if season is not None:
                            if ep_season == season:
                                found_season = True
                                all_episodes.append(self._parse_episode(episode))
                            elif ep_season > season and found_season:
                                # On a dépassé la saison cherchée et on l'avait trouvée
                                return {
                                    'series_id': series_id,
                                    'episodes': all_episodes,
                                    'page': current_page,
                                    'total_pages': result.get('totalPages', 1),
                                    'source': 'tvdb'
                                }
                        else:
                            all_episodes.append(self._parse_episode(episode))
                
                # Vérifier si on doit continuer la pagination
                total_pages = result.get('totalPages', 1)
                if current_page >= total_pages - 1:
                    break
                    
                # Si on cherche une saison spécifique et qu'on ne l'a pas encore trouvée ou qu'on est dedans
                # on continue à la page suivante
                current_page += 1
                
                # Sécurité pour éviter les boucles infinies ou trop d'appels
                if current_page > 20: # Max 10000 épisodes
                    break
            
            return {
                'series_id': series_id,
                'episodes': all_episodes,
                'page': current_page,
                'total_pages': total_pages,
                'source': 'tvdb'
            }
            
        except Exception as e:
            logger.error(f"Series episodes error: {e}")
            return {
                'series_id': series_id,
                'episodes': [],
                'page': page,
                'source': 'tvdb'
            }

    def _parse_episode(self, episode: Dict) -> Dict:
        """Parse un épisode brut TVDB en format compatible Filebot"""
        ep_info = {
            # Identifiants
            'id': episode.get('id'),
            'tvdbid': str(episode.get('id')),
            
            # Numérotation
            'season': episode.get('seasonNumber'),
            's': episode.get('seasonNumber'),
            'episode': episode.get('number'),
            'e': episode.get('number'),
            'absolute': episode.get('absoluteNumber'),
            
            # Format de numérotation
            'sxe': f"{episode.get('seasonNumber')}x{str(episode.get('number')).zfill(2)}" if episode.get('seasonNumber') and episode.get('number') else None,
            's00e00': f"S{str(episode.get('seasonNumber')).zfill(2)}E{str(episode.get('number')).zfill(2)}" if episode.get('seasonNumber') and episode.get('number') else None,
            
            # Titres
            'title': episode.get('name', ''),
            't': episode.get('name', ''),
            'episode_title': episode.get('name', ''),
            
            # Description
            'overview': episode.get('overview', ''),
            
            # Dates
            'airdate': episode.get('aired', ''),
            'd': episode.get('aired', ''),
            
            # Runtime
            'runtime': episode.get('runtime'),
            
            # Notes
            'rating': round(episode.get('score', 0) / 10, 1) if episode.get('score') else None,
            'score': episode.get('score'),
            
            # Source
            'source': 'tvdb',
            'url': f"https://www.thetvdb.com/episodes/{episode.get('id')}"
        }
        
        if 'image' in episode:
            ep_info['image'] = episode['image']
            ep_info['poster'] = episode['image']
            
        return ep_info
    
    def get_episode_details(self, episode_id: int) -> Dict:
        """
        Récupère les détails d'un épisode
        
        Args:
            episode_id: ID TVDB de l'épisode
            
        Returns:
            Dictionnaire avec détails de l'épisode
        """
        try:
            episode = self.client.get_episode_extended(episode_id)
            
            details = {
                'id': episode_id,
                'title': episode.get('name', ''),
                'overview': episode.get('overview', ''),
                'season': episode.get('seasonNumber'),
                'episode': episode.get('number'),
                'air_date': episode.get('aired', ''),
                'runtime': episode.get('runtime'),
                'source': 'tvdb',
                'url': f"https://www.thetvdb.com/episodes/{episode_id}"
            }
            
            if 'image' in episode:
                details['image'] = episode['image']
            
            logger.info(f"Retrieved episode details: S{details.get('season')}E{details.get('episode')}")
            return details
            
        except Exception as e:
            logger.error(f"Episode details error: {e}")
            return {'id': episode_id, 'source': 'tvdb'}


class APIHandler:
    """Orchestrateur API (compatible avec l'ancienne interface)"""
    
    def __init__(self, config: Dict = None):
        """
        Initialise le gestionnaire API
        
        Args:
            config: Configuration contenant les clés API
        """
        self.config = config or {}
        self.tvdb_pin = self.config.get('tvdb_pin') or None
        
        # Accepter la clé depuis tvdb_api_key OU tmdb_api_key (UUID TVDB)
        # Un UUID TVDB ressemble à: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        import re
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
        
        tvdb_key = self.config.get('tvdb_api_key', '').strip()
        if not tvdb_key:
            # Fallback: si tmdb_api_key ressemble à un UUID TVDB, l'utiliser
            candidate = self.config.get('tmdb_api_key', '').strip()
            if candidate and uuid_pattern.match(candidate):
                tvdb_key = candidate
                logger.info("Using tmdb_api_key field as TVDB key (UUID format detected)")
        
        self.tvdb_api_key = tvdb_key
        
        if self.tvdb_api_key:
            try:
                self.tvdb = TVDBAPIHandler(self.tvdb_api_key, self.tvdb_pin)
                logger.info(f"TVDB initialized with key: {self.tvdb_api_key[:8]}...")
            except Exception as e:
                logger.error(f"Failed to initialize TVDB handler: {e}")
                self.tvdb = None
        else:
            logger.warning("No TVDB API key provided")
            self.tvdb = None
    
    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict]:
        """
        Recherche un film
        
        Args:
            title: Titre du film
            year: Année (optionnel)
            
        Returns:
            Liste des résultats
        """
        if not self.tvdb:
            logger.error("TVDB handler not initialized")
            return []
        
        return self.tvdb.search_movie(title, year)
    
    def search_tv(self, title: str, year: Optional[int] = None) -> List[Dict]:
        """
        Recherche une série
        
        Args:
            title: Titre de la série
            year: Année (optionnel)
            
        Returns:
            Liste des résultats
        """
        if not self.tvdb:
            logger.error("TVDB handler not initialized")
            return []
        
        return self.tvdb.search_series(title, year)
    
    def get_movie_details(self, movie_id: str, source: str = 'tvdb', search_data: Dict = None) -> Dict:
        if not self.tvdb or source != 'tvdb':
            return {'id': movie_id, 'source': source}
        try:
            return self.tvdb.get_movie_details(int(movie_id), search_data)
        except ValueError:
            logger.error(f"Invalid movie ID: {movie_id}")
            return {'id': movie_id, 'source': source}
    
    def get_tv_details(self, tv_id: str, season: int = 1, episode: int = 1, source: str = 'tvdb', search_data: Dict = None) -> Dict:
        if not self.tvdb or source != 'tvdb':
            return {'id': tv_id, 'source': source}
        try:
            series_id = int(tv_id)
            details = self.tvdb.get_series_details(series_id, search_data)
            
            # Ajouter les numéros de saison/épisode même sans détails complets
            details['season'] = season
            details['s'] = season
            details['episode'] = episode
            details['e'] = episode
            details['sxe'] = f"{season}x{str(episode).zfill(2)}"
            details['s00e00'] = f"S{str(season).zfill(2)}E{str(episode).zfill(2)}"
            
            # Si season/episode sont demandés, chercher les détails complets de l'épisode
            if season and episode:
                try:
                    ep_details = self.tvdb.get_series_episodes(series_id, season=season)
                    for ep in ep_details.get('episodes', []):
                        if ep.get('season') == season and ep.get('episode') == episode:
                            # Ajouter TOUS les détails de l'épisode
                            details['episode_title'] = ep.get('episode_title', ep.get('title', ''))
                            details['t'] = ep.get('episode_title', ep.get('title', ''))  # Alias court
                            details['episode_overview'] = ep.get('overview', '')
                            details['airdate'] = ep.get('airdate', '')
                            details['absolute'] = ep.get('absolute')
                            details['episode_rating'] = ep.get('rating')
                            details['episode_score'] = ep.get('score')
                            details['episode_runtime'] = ep.get('runtime')
                            break
                except Exception as e:
                    logger.warning(f"Could not fetch episode details for S{season}E{episode}: {e}")
                    # Continuer avec les détails de la série seule
            
            return details
        except ValueError:
            logger.error(f"Invalid series ID: {tv_id}")
            return {'id': tv_id, 'source': source}
    
    def get_episode_details(self, title_id: str, season: int, episode: int, source: str = 'tvdb') -> Optional[Dict]:
        """
        Récupère les infos d'un épisode
        
        Args:
            title_id: ID de la série
            season: Numéro de saison
            episode: Numéro d'épisode
            source: Source (tvdb par défaut)
            
        Returns:
            Dictionnaire avec détails ou None
        """
        if not self.tvdb or source != 'tvdb':
            return None
        
        try:
            series_id = int(title_id)
            episodes = self.tvdb.get_series_episodes(series_id, season=season)
            
            # Chercher l'épisode demandé
            for ep in episodes.get('episodes', []):
                if ep.get('season') == season and ep.get('episode') == episode:
                    return ep
            
            logger.warning(f"Episode not found: S{season}E{episode}")
            return None
        except ValueError:
            logger.error(f"Invalid series ID: {title_id}")
            return None
