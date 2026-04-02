import os
from pathlib import Path
from dataclasses import dataclass
from typing import List
import re

@dataclass
class MediaFile:
    filename: str
    path: str
    media_type: str  # 'movie' or 'tv'
    title: str = ""
    season: int = None
    episode: int = None
    year: int = None

class MediaScanner:
    def __init__(self, movie_path: str = "/downloads/movie", tv_path: str = "/downloads/tv_shows"):
        self.movie_path = movie_path
        self.tv_path = tv_path
        
    def scan(self) -> List[MediaFile]:
        """Scanne les dossiers récursivement"""
        files = []
        if os.path.exists(self.movie_path):
            files += self._scan_dir(self.movie_path, 'movie')
        if os.path.exists(self.tv_path):
            files += self._scan_dir(self.tv_path, 'tv')
        return files

    def _scan_dir(self, path: str, media_type: str) -> List[MediaFile]:
        """Scan récursif d'un dossier"""
        files = []
        try:
            for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
                if entry.is_dir(follow_symlinks=False):
                    files += self._scan_dir(entry.path, media_type)
                elif entry.is_file() and self._is_video(entry.name):
                    media = MediaFile(
                        filename=entry.name,
                        path=entry.path,
                        media_type=media_type
                    )
                    media.title = self._extract_title(entry.name, media_type)
                    if media_type == 'tv':
                        self._extract_episode_info(entry.name, media)
                    files.append(media)
        except PermissionError:
            pass
        return files
    
    def _is_video(self, filename: str) -> bool:
        """Vérifie si c'est un fichier vidéo"""
        video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm'}
        return Path(filename).suffix.lower() in video_extensions
    
    def _extract_title(self, filename: str, media_type: str) -> str:
        """Extrait le titre du nom de fichier"""
        name = Path(filename).stem
        
        # Supprimer les tags qualité/source communs
        quality_tags = re.compile(
            r'\b(\d{3,4}p|WEB[-.]?(?:RIP|DL)?|BluRay|BDRip|DVDRip|HDTV|AMZN|NF|DSNP'
            r'|H\.?264|H\.?265|HEVC|AVC|x264|x265|AAC|AC3|DTS|MULTI|MULTi'
            r'|VOSTFR|SUBFRENCH|FASTSUB|FRENCH|TRUEFRENCH|VFF|VFQ|VF'
            r'|PROPER|REPACK|EXTENDED|THEATRICAL|UNRATED|DIRECTORS'
            r'|[A-Z0-9]{2,8}-[A-Z0-9]{2,10})\b.*',
            re.IGNORECASE
        )
        
        if media_type == "tv":
            # Enlever tags [xxx] et (yyyy)
            name = re.sub(r'\s*\[[^\]]*\]', '', name)
            name = re.sub(r'\s*\(\d{4}\)', '', name)
            # Couper au pattern SxxExx
            match = re.match(r'^(.+?)\s*[-\s.]*[Ss]\d+[Ee]\d+', name)
            if match:
                title = match.group(1)
            else:
                title = name
        else:
            # Enlever tags [xxx] et (texte long)
            name = re.sub(r'\s*\[[^\]]*\]', '', name)
            name = re.sub(r'\s*\([^)]{8,}\)', '', name)
            # Couper à l'année
            match = re.match(r'^(.+?)\s*[\(\[\s.\-]((?:19|20)\d{2})', name)
            if match:
                title = match.group(1)
            else:
                # Couper aux tags qualité
                title = quality_tags.sub('', name).strip()
                if not title:
                    title = name
        
        # Remplacer les points par des espaces (sauf si déjà des espaces)
        if '.' in title and ' ' not in title:
            title = title.replace('.', ' ')
        
        # Nettoyer
        title = re.sub(r'[-_]+$', '', title)  # tirets/underscores en fin
        title = re.sub(r'\s+', ' ', title).strip()
        return title
    
    def _extract_episode_info(self, filename: str, media: MediaFile):
        """Extrait season/episode info pour les séries"""
        # Pattern: S01E01 ou s01e01
        match = re.search(r"[Ss](\d+)[Ee](\d+)", filename)
        if match:
            media.season = int(match.group(1))
            media.episode = int(match.group(2))
