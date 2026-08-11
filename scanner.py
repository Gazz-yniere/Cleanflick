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
    def __init__(self, input_path: str = "/downloads", ignored_paths: list[str] | None = None):
        self.input_path = input_path
        self.ignored_paths = []
        for p in (ignored_paths or []):
            if p:
                try:
                    self.ignored_paths.append(os.path.abspath(str(p)))
                except Exception:
                    pass

    def _is_ignored(self, path: str) -> bool:
        candidate = os.path.abspath(path)
        for ignored in self.ignored_paths:
            try:
                if candidate == ignored or os.path.commonpath([candidate, ignored]) == ignored:
                    return True
            except ValueError:
                pass
        return False

    def scan(self) -> List[MediaFile]:
        """Scanne les dossiers récursivement"""
        if not os.path.exists(self.input_path):
            return []
        return self._scan_dir(self.input_path)

    def _scan_dir(self, path: str) -> List[MediaFile]:
        """Scan récursif d'un dossier"""
        files = []
        try:
            for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
                if self._is_ignored(entry.path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    files += self._scan_dir(entry.path)
                elif entry.is_file() and self._is_video(entry.name):
                    media_type = self._infer_media_type(entry.name)
                    media = MediaFile(
                        filename=entry.name,
                        path=entry.path,
                        media_type=media_type
                    )
                    media.title = self._extract_title(entry.name, media_type)
                    media.year = self._extract_year(entry.name)
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
            # Couper au pattern SxxExx ou 1x02
            match = re.match(r'^(.+?)\s*[-\s.]*([Ss]\d+[Ee]\d+|\d+x\d{2})', name)
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

    def _extract_year(self, filename: str):
        """Extrait l'année si elle est présente dans le nom du fichier"""
        match = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', filename)
        if match:
            return int(match.group(1))
        return None

    def _infer_media_type(self, filename: str) -> str:
        """Déduit le type probable à partir du nom du fichier"""
        patterns = [
            r'[Ss]\d{1,2}[Ee]\d{1,2}',
            r'\b\d{1,2}x\d{2}\b',
            r'\bseason\s*\d+\b',
            r'\bsaison\s*\d+\b',
        ]
        if any(re.search(pattern, filename, re.IGNORECASE) for pattern in patterns):
            return 'tv'
        return 'movie'
    
    def _extract_episode_info(self, filename: str, media: MediaFile):
        """Extrait season/episode info pour les séries"""
        # Pattern: S01E01, s01e01 ou 1x03
        match = re.search(r"[Ss](\d+)[Ee](\d+)", filename)
        if not match:
            match = re.search(r"\b(\d+)x(\d{2})\b", filename, re.IGNORECASE)
        if match:
            media.season = int(match.group(1))
            media.episode = int(match.group(2))
