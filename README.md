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
- [How It Works](#how-it-works)
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

- **TVDB v4 + OMDb API** integration for accurate movie, TV show and episode metadata
- **Filebot-style naming** with customizable format strings (`{n}`, `{y}`, `{s00e00}`, `{t}`, `{imdb}`...)
- **Multi-language titles** (`{n:fr}`, `{n:de}`, `{n:ja}`...)
- **External IDs** in filenames (IMDb, TVDB, TMDB)
- **Recursive folder scanning** with live refresh, reacting to changes in real time
- **Smart caching** with 7-day expiry — auto-scan uses the cache first (fast), manual search always queries the API fresh to find alternatives
- **SQLite persistence** — history and all caches survive restarts in a single `cleanflick.db`
- **Rename history** with revert support (persistent across restarts)
- **Manual search** with full TVDB results selection (confirmed choices are remembered)
- **Rename All / Move All** batch processing
- **Real-time move progress** with speed, ETA, a `verifying` phase, and file-gone verification
- **Library tab** — browse the output tree, missing-episode badges, send-back to source, folder rename/delete
- **Folder picker** for media paths
- **Password protection** (optional)
- **6 languages** interface (FR, EN, ES, DE, IT, PT) with language switch
- **Dark theme** (orange & dark grey)
- **Docker support** for easy deployment

---

## How It Works

CleanFlick turns messy download folders into a clean, organized media library. The interface is split into **4 tabs**:

### 1. Files

- CleanFlick **recursively scans** your `input_path` and lists every movie / TV episode file.
- For each file it queries **TVDB** (and **OMDb** for episode ratings) and proposes a cleaned filename using your `movie_format` / `tv_format`.
- Results are **cached for 7 days** so repeated scans are instant. The **scan/auto-scan** path uses the cache first and only queries the API when needed; the **manual search** (`🔍`) always forces a fresh API call so you can pick an alternative title even when a proposal is already cached. Once you confirm a manual selection, it is remembered per-file.
- Each row offers **Rename** and **Move** (plus a transfer overlay with a live progress bar, speed, ETA and a final *verifying* phase that confirms the file really moved before removing the row).

### 2. Library

- Browses the **output** folders (`movie_output_path`, `tv_output_path`) as a tree.
- Highlights **missing episodes** (badges on season folders).
- Lets you **rename a folder**, **delete a folder**, or **send a file/folder back** to the source folder.

### 3. History

- Every Rename / Move / Revert is stored in SQLite and shown here.
- Entries expose a **revert status** (`available` / `reverted` / `missing` / `done`) so you can roll back a rename with one click. History survives restarts.

### 4. Config

- Enter your **TVDB** and **OMDb** API keys (side by side) with a **test** button each, adjust paths and naming formats, and switch the interface language.

> **Caching summary:** searches, per-file results and details are cached in SQLite with a **7-day expiry** and re-fetched automatically, so there is no cache to manage manually.

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
  "omdb_api_key": "xxxxxxxx",
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
| `omdb_api_key` | OMDb API key (episode ratings, optional) | `""` |
| `input_path` | Source folder for media files | `/downloads` |
| `movie_output_path` | Destination for renamed movies | `/movies` |
| `tv_output_path` | Destination for renamed TV shows | `/tv_shows` |
| `movie_format` | Naming format for movies | `{n} ({y})` |
| `tv_format` | Naming format for TV shows | `{n} - {s00e00} - {t}` |

> **Note:** Both keys can also be entered directly in the **Settings → Config** tab of the UI. The "test" button next to each input checks connectivity before saving.

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
├── api_handler.py            # TVDB/OMDb API handlers
├── db.py                     # SQLite persistence (history + caches)
├── mediaduration.py          # Media duration probing
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

### [1.0.8] - 2026-08-20

**Added**
- SQLite persistence — history and all caches (search, per-file results, details, OMDb) now live in a single `cleanflick.db` instead of JSON files
- Caches auto-expire after 7 days and re-fetch from TVDB/OMDb — no manual cache management
- Manual search now forces a fresh API query (`force_refresh`); confirmed manual selections are remembered per-file
- Transfer `verifying` phase — destination is verified (100%) before the source is deleted
- Library tab — output tree browsing, missing-episode badges, sort/filter, send-back to source, folder rename/delete

**Changed**
- Files table converted from `<table>` to a CSS grid (`1fr 2fr 185px`) — guaranteed 1/3–2/3 column split with a fixed Actions column; the progress column was removed
- Transfer overlay redesigned to 2 lines — title (icon + label + filename) and a progress bar with speed/ETA beside it
- Scan/auto-scan uses cache-first-then-API; the Files tab refreshes reliably during and after transfers
- Library filter buttons harmonized with the Files tab; Rename button now orange (accent)
- Config API section — TVDB and OMDb keys side by side, with a "test key" button right after each input
- History buttons — refresh is orange (primary), clear-all is gray (secondary)

**Fixed**
- OMDb episode cache key mismatch — episode metadata now uses the year-qualified key (with legacy fallback); auto-search uses consistent top-3 episode OMDb that skips non-series results (saves OMDb quota)
- Files reappearing after a "send back" were not detected — removed the `scan_last_snapshot` resets in the send-back endpoints
- Files table was not refreshing during transfers — removed the SSE suppression of `scanFiles()` while transfers are active

**Removed**
- "Vider le cache TVDB" button and `/api/cache/clear` route (caches expire automatically)
- Dead code: `rename_engine.py`, `db.find_history`, `db.file_cache_delete`, unused JS functions, unused CSS rules, and 20 unused i18n keys

**Refactored**
- `app.py`: extracted cache helpers (`_file_fingerprint`, `_file_cache_lookup`, `_file_cache_store`, `_params_cache_key`) reused across the 4 search/cache endpoints (keys unchanged)
- `app.js`: extracted `mergeDetails` and `openManualSearchModal` to remove duplication
- `files.css`: merged the shared header styles of the files table and grid

### [1.0.6] - 2026-08-12

**Fixed**
- `revert_of` field was never persisted in history DB — `add_history` was reading `entry.get('extra', {})` instead of saving all non-standard root fields, causing `reverted_ids` to always be empty and showing a false "Fichier introuvable" badge on renamed entries after a revert
- Revert of a same-folder rename now runs synchronously (`_move_path` + immediate `scan_last_snapshot` update) instead of going through the async `_run_file_op` thread, preventing a race condition where the filesystem watcher would pick up the change before the snapshot was updated, leading to a 400 error on the next rename
- File row was not reappearing in the table after a revert — `revertEntry()` now calls `scanFiles()` in addition to `loadHistory()` (600 ms delay)
- History table showed a false "Fichier introuvable" badge on the original rename entry after a successful revert

**Changed**
- Action buttons in the file table are now icon-only (34×34 px) with `title` tooltips, displayed on a single centered row
- Revert button in the history table is now icon-only (34×34 px) with a `title` tooltip
- History entries expose a `revert_status` field (`available` / `reverted` / `missing` / `done`) used by the frontend to render the correct badge or button
- `allFiles` array is reset to `[]` before each `scanFiles()` fetch to prevent duplicate rows between the previous state and the new scan result
- Cross-folder reverts remain asynchronous; only same-folder reverts are synchronous

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
