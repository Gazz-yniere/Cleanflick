# Changelog

## [1.0.1] - 2026-04-16

### Fixed

- Fixed JSON corruption errors in rename history file - now auto-recovers with backup
- Fixed error messages being too technical (now shows user-friendly popups)
- Titles with colons or hyphens (e.g., "Arrow: The Series") now display correctly without special characters
- Episode titles with colons or hyphens are properly cleaned in filenames
- Improved error handling in title parsing to avoid malformed filenames

### Changed

- Rename history loader now automatically repairs corrupted files
- Better error reporting in frontend with clearer error messages
- Title cleaning applied to both backend (Python) and frontend (JavaScript)

### Added

- New `repair_history.py` script to manually fix corrupted rename history files

## [1.0.0] - 2025-04-01

### Added

- TVDB v4 API integration for movies and TV shows
- Filebot-style naming format (`{n}`, `{y}`, `{s00e00}`, `{t}`, `{imdb}`...)
- Multi-language title support (`{n:fr}`, `{n:de}`, `{n:ja}`...)
- External IDs in filenames (IMDb, TVDB, TMDB)
- Recursive folder scanning for movies and TV shows
- Rename history with revert support (persistent across restarts)
- Optional password protection
- French / English interface with language switch
- Manual search with full TVDB results selection
- Rename All button
- Folder picker for media paths
- Docker support
- MDI icons throughout the UI
- Dark theme (orange & dark grey)
