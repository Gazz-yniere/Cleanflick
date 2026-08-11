<div align="center">
  <img src="static/CleanFlick.png" alt="CleanFlick" width="120">
  <h1>CleanFlick</h1>
  <p>Automatic media file renamer powered by TVDB API</p>
</div>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-blue?logo=python" alt="Python"></a>
  <a href="https://flask.palletsprojects.com/"><img src="https://img.shields.io/badge/Flask-2.3-green?logo=flask" alt="Flask"></a>
  <img src="https://img.shields.io/badge/TVDB-v4-orange" alt="TVDB v4">
  <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/Docker-blue?logo=docker" alt="Docker"></a>
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

---

## Table of Contents

- [Features](#features)
- [Getting Started](#getting-started)
  - [Docker](#docker)
  - [Local Installation](#local-installation)
- [Configuration](#configuration)
  - [TVDB API Key](#tvdb-api-key)
  - [Config File](#config-file)
- [Naming Format](#naming-format)
  - [Movie Variables](#movie-variables)
  - [TV Show Variables](#tv-show-variables)
  - [Format Examples](#format-examples)
- [Project Structure](#project-structure)
- [Docker Volumes](#docker-volumes)
- [Security](#security)
- [Changelog](#changelog)
- [License](#license)

---

## Features

- **TVDB v4 API** integration for accurate movie and TV show metadata
- **Filebot-style naming** with customizable format strings (`{n}`, `{y}`, `{s00e00}`, `{t}`, `{imdb}`...)
- **Multi-language titles** (`{n:fr}`, `{n:de}`, `{n:ja}`...)
- **External IDs** in filenames (IMDb, TVDB, TMDB)
- **Recursive folder scanning** with auto-refresh every 8 seconds
- **Rename history** with revert support (persistent across restarts)
- **Manual search** with full TVDB results selection
- **Rename All / Move All** batch processing
- **Real-time move progress** with speed, ETA, and file-gone verification
- **Folder picker** for media paths
- **Password protection** (optional)
- **6 languages** interface (FR, EN, ES, DE, IT, PT) with language switch
- **Dark theme** (orange & dark grey)
- **Docker support** for easy deployment

---

## Getting Started

### Docker

The fastest way to run CleanFlick:

```bash
# Clone the repository
git clone https://github.com/gazzyniere/CleanFlick.git
cd CleanFlick

# Build and start
docker-compose up -d
```

Open **http://localhost:5000** in your browser.

> ⚠️ **Before running in production**, edit `docker-compose.yml` to set a strong `SECRET_KEY` and a `CLEANFLICK_PASSWORD`. See the [Security](#security) section below.

### Local Installation

```bash
# Clone the repository
git clone https://github.com/gazzyniere/CleanFlick.git
cd CleanFlick

# Install dependencies
pip install -r requirements.txt

# Configure
cp config.example.json config.json
# Edit config.json with your TVDB API key

# Run
python app.py
```

Open **http://localhost:5000** in your browser.

---

## Configuration

### TVDB API Key

1. Create a free account at [https://www.thetvdb.com](https://www.thetvdb.com)
2. Go to **Dashboard → API Access**
3. Copy your API key (UUID format)
4. Paste it in **Settings → TVDB API Key** or in `config.json`

### Config File

Copy `config.example.json` to `config.json` and fill in your settings:

```json
{
  "tvdb_api_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "tvdb_pin": "",
  "input_path": "/downloads",
  "movie_output_path": "/movies",
  "tv_output_path": "/tv_shows",
  "movie_format": "{n} ({y})",
  "tv_format": "{n} - {s00e00} - {t}"
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `tvdb_api_key` | Your TVDB v4 API key | *(required)* |
| `tvdb_pin` | TVDB PIN (optional) | `""` |
| `input_path` | Source folder for media files | `/downloads` |
| `movie_output_path` | Destination for renamed movies | `/movies` |
| `tv_output_path` | Destination for renamed TV shows | `/tv_shows` |
| `movie_format` | Naming format for movies | `{n} ({y})` |
| `tv_format` | Naming format for TV shows | `{n} - {s00e00} - {t}` |

---

## Naming Format

### Movie Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{n}` | Title | `The Matrix` |
| `{y}` | Year | `1999` |
| `{ny}` | Title (no year in title) | `The Matrix` |
| `{imdb}` | IMDb ID | `tt0133093` |
| `{tmdb}` | TMDB ID | `603` |
| `{tvdbid}` | TVDB ID | `2239` |
| `{director}` | Director | `Wachowski` |
| `{genres}` | Genres | `Action, Sci-Fi` |
| `{runtime}` | Duration (min) | `136` |
| `{certification}` | Rating | `R` |
| `{studio}` | Studio | `Warner Bros.` |
| `{n:fr}` | French title | `Matrix` |
| `{n:de}` | German title | `Matrix` |
| `{n:ja}` | Japanese title | `マトリックス` |

### TV Show Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{n}` | Show title | `Breaking Bad` |
| `{y}` | Year | `2008` |
| `{s}` | Season number | `1` |
| `{s:02d}` | Season padded | `01` |
| `{e}` | Episode number | `1` |
| `{e:02d}` | Episode padded | `01` |
| `{s00e00}` | Season + Episode | `S01E01` |
| `{t}` | Episode title | `Pilot` |
| `{absolute}` | Absolute number | `42` |
| `{airdate}` | Air date | `2008-01-20` |
| `{tvdbid}` | TVDB ID | `81189` |
| `{network}` | Network | `AMC` |
| `{n:fr}` | French title | `Breaking Bad` |

### Format Examples

**Movies:**

```
{n} ({y})                              → The Matrix (1999).mkv
{n} ({y}) [imdbid-{imdb}]             → The Matrix (1999) [imdbid-tt0133093].mkv
{n} ({y}) [imdbid-{imdb}] - {n:fr}    → The Matrix (1999) [imdbid-tt0133093] - Matrix.mkv
```

**TV Shows:**

```
{n} - {s00e00} - {t}                              → Breaking Bad - S01E01 - Pilot.mkv
{n} ({y}) [tvdbid-{tvdbid}] - {s00e00} - {t}    → Breaking Bad (2008) [tvdbid-81189] - S01E01 - Pilot.mkv
```

---

## Project Structure

```
CleanFlick/
├── app.py                    # Flask application & routes
├── scanner.py                # Media file scanner
├── api_handler.py            # TVDB API handler
├── rename_engine.py          # Rename logic
├── repair_history.py         # History repair utility (run manually if history is corrupted)
├── config.example.json       # Configuration template
├── requirements.txt          # Python dependencies
├── Dockerfile                # Docker build
├── docker-compose.yml        # Docker Compose config
├── templates/
│   ├── index.html            # Main page
│   └── login.html            # Login page
├── static/
│   ├── app.js                # Frontend logic
│   ├── i18n.js               # Translations (fr, en, es, de, it, pt)
│   ├── base.css              # Base styles & CSS variables
│   ├── files.css             # Files & history table styles
│   ├── config.css            # Config page styles
│   ├── login.css             # Login page styles
│   ├── CleanFlick.png        # Logo
│   └── CleanFlick.ico        # Favicon
```

---

## Docker Volumes

```yaml
volumes:
  - ./downloads:/downloads
  - ./config.json:/app/config.json
  - ./rename_history.json:/app/rename_history.json
  - ./output/movies:/movies
  - ./output/tv_shows:/tv_shows
```

---

## Security

### Password Protection (Docker)

CleanFlick uses the `CLEANFLICK_PASSWORD` environment variable from `docker-compose.yml` for authentication:

- **Set a password** → login page is enabled
- **Leave empty** (`CLEANFLICK_PASSWORD=`) → no login page, direct access

```yaml
environment:
  - CLEANFLICK_PASSWORD=your-secure-password
```

> **Note:** The password is stored only as an environment variable, never in `config.json`.

### SECRET_KEY (Docker)

The `SECRET_KEY` environment variable is used to secure user sessions. Generate a strong random value before deploying:

```bash
# Linux / macOS
openssl rand -hex 32

# Python (any platform)
python -c "import secrets; print(secrets.token_hex(32))"
```

Then set it in `docker-compose.yml`:

```yaml
environment:
  - SECRET_KEY=your-generated-value-here
```

> ⚠️ Never commit your `SECRET_KEY` to git. If left to the default, sessions will be invalidated on every container restart.

---

## Changelog

### [1.0.5] - 2026-08-11

**Added**
- Backend-driven move/copy naming is now anchored on the file currently selected from the source path.
- A confirmation modal appears when the live file name differs from the generated search suggestion, so the user can confirm or cancel the move before the operation starts.
- The UI now flags reverted history entries via a server-provided `is_reverted` field instead of trying to infer rollback state from adjacent history rows.

**Changed**
- The frontend now limits preview loading concurrency and reduces the polling frequency used by move progress updates.
- Move requests do not rely on a `new_name` proposal in the payload, which keeps transfer targets aligned with the actual file discovered on disk.

**Fixed**
- History rendering no longer shows a false "Fichier introuvable" marker for a successfully reverted file.
- The move dialog/progress contract is now consistent with the real file operations and the service-side progress payload.

### [1.0.4] - 2026-06-18

**Added**
- `hist_change` i18n key for the history table column header — translated in all 6 languages

**Fixed**
- Rename and Move buttons displayed raw key `btn-rename` instead of translated label
- Action buttons in file table wrapped to a second line instead of staying on one row
- File name column overflowed into the poster/preview column on long filenames
- Folder picker modal contained a stray GitHub footer link in the middle of the UI
- History tab label, buttons and all history-related strings were not translated in ES, DE, IT, PT
- Operation type badges and Revert button in history table were not re-rendered on language switch
- Language switch now correctly re-renders both the file table and the history table

### [1.0.3] - 2026-06-17

**Added**
- Real-time move progress bar with speed (MB/s) and ETA for cross-drive transfers
- Smart same-drive detection: instant rename via `shutil.move` with shimmer animation instead of fake byte-level progress
- File-gone verification after move: polls `/api/scan` until the source file is confirmed absent before removing the row
- Progress bar fills gradually during verification phase (0% → 95%) then shows ✓ when confirmed
- Custom confirmation modal for history clear — replaces native `confirm()` dialog, styled to match the app theme

**Changed**
- History table rows are now half the height (52px) for a more compact view
- Move progress display now has three distinct phases: `moving` (shimmer), `copying` (real %), `cleaning` (shimmer at 99%)
- File row stays visible with "✓ Déplacé" for 800ms after confirmation before disappearing
- i18n: added `hist_confirm_title` key in FR and EN; `hist_confirm_clear` is now a full sentence

**Fixed**
- Removed double `os.remove()` call in `_run_file_op` (source file was already deleted by `_move_path`)
- Progress text now always renders on top of the progress bar (`isolation: isolate`)

### [1.0.2] - 2026-06-10

**Changed**
- Refactored Python backend and JavaScript frontend — no functional changes
- CSS: introduced CSS custom properties for colors and spacing across all stylesheets
- HTML: removed all inline `style=""` attributes; fixed `login.html` hardcoded paths

**Fixed**
- `repair_history.py` was writing `{}` instead of `[]` when resetting history
- `rename_history.json.example` contained wrong format (`{}` → `[]`)

**Removed**
- `_archive/` folder (obsolete test scripts)

### [1.0.1] - 2026-04-16

**Fixed**
- Fixed JSON corruption errors in rename history file — now auto-recovers with backup
- Titles with colons or hyphens (e.g., "Arrow: The Series") now display correctly
- Episode titles with colons or hyphens are properly cleaned in filenames

**Changed**
- Rename history loader now automatically repairs corrupted files
- Better error reporting in frontend with clearer error messages
- Title cleaning applied to both backend (Python) and frontend (JavaScript)

**Added**
- `repair_history.py` script to manually fix corrupted rename history files

### [1.0.0] - 2025-04-01

**Added**
- TVDB v4 API integration for movies and TV shows
- Filebot-style naming format
- Multi-language title support
- External IDs in filenames (IMDb, TVDB, TMDB)
- Recursive folder scanning
- Rename history with revert support
- Optional password protection
- French / English interface
- Manual search with full TVDB results selection
- Rename All and Move All batch actions
- Folder picker
- Docker support
- MDI icons throughout the UI
- Dark theme (orange & dark grey)

---

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.
