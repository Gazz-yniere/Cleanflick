<div align="center">
  <img src="static/CleanFlick.png" alt="CleanFlick" width="120">
  <h1>CleanFlick</h1>
  <p>Automatic media file renamer powered by TVDB API</p>
</div>

<p align="center">
  <a href="https://github.com/Gazz-yniere/Cleanflick/actions/workflows/ci.yml"><img src="https://github.com/Gazz-yniere/Cleanflick/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
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
- [Changelog](CHANGELOG.md)
- [License](#license)

---

## Features

- **TVDB v4 + OMDb API** integration for accurate movie, TV show and episode metadata
- **Filebot-style naming** with customizable format strings (`{n}`, `{y}`, `{s00e00}`, `{t}`, `{imdb}`...)
- **Multi-language titles** (`{n:fr}`, `{n:de}`, `{n:ja}`...)
- **External IDs** in filenames (IMDb, TVDB, TMDB)
- **Recursive folder scanning** with live refresh, reacting to changes in real time
- **Smart caching** — cached info is kept forever by default (no repeated API calls); an optional auto-refresh can re-query the API in the background, manual search always queries fresh to find alternatives
- **SQLite persistence** — history and all caches survive restarts in a single `cleanflick.db`
- **Rename history** with revert support (persistent across restarts)
- **Manual search** with full TVDB results selection (confirmed choices are remembered)
- **Rename All / Move All** batch processing
- **Real-time move progress** with speed, ETA, a `verifying` phase, and file-gone verification
- **Library tab** — tree **and grid** views (toggle), instant F5 via a local tree cache, missing **and upcoming** episode badges, expandable series card (poster + seasons with TVDB posters + episodes with state), per-file manual search, folder rename/delete, full series-folder rename from manual search (episode order selection, subtitles follow the video), TVDB web link, poster hover preview, filter sidebar (search, genre, age, size, rating, date, missing) and episode "present/total" counters
- **Folder picker** for media paths
- **Password protection** (optional)
- **6 languages** interface (FR, EN, ES, DE, IT, PT) with language switch
- **Dark theme** (orange & dark grey)
- **Docker support** for easy deployment
- **Unit test suite** (`tests/`) — scanner, duration parser, SQLite layer, API handlers and Flask routes, runnable via `pytest` and in CI

---

## How It Works

CleanFlick turns messy download folders into a clean, organized media library. The interface is split into **4 tabs**:

### 1. Files

- CleanFlick **recursively scans** your `input_path` and lists every movie / TV episode file.
- For each file it queries **TVDB** (and **OMDb** for episode ratings) and proposes a cleaned filename using your `movie_format` / `tv_format`.
- Results are **cached permanently by default** so repeated scans are instant. The **scan/auto-scan** path uses the cache first and only queries the API when there is no cached info; the **manual search** (`🔍`) always forces a fresh API call so you can pick an alternative title even when a proposal is already cached. Once you confirm a manual selection, it is remembered per-file.
- Each row offers **Rename** and **Move** (plus a transfer overlay with a live progress bar, speed, ETA and a final *verifying* phase that confirms the file really moved before removing the row).

### 2. Library

- Browses the **output** folders (`movie_output_path`, `tv_output_path`) as a **tree** or a **grid** (toggle in the sidebar). The explored tree is cached locally so **F5 is near-instant**; entries are re-validated in the background.
- **Grid view**: one card per movie/series with poster, rating and episode counters (red = missing, yellow = upcoming). Clicking a series card **expands it in place** into three columns — poster + info, **seasons with TVDB posters**, and the **episodes** of the selected season (number, rating, title, runtime, air date, size; red/yellow/green state per episode). Season 1 opens by default; one series is expanded at a time.
- Highlights **missing episodes** (red) and **upcoming episodes** (yellow, not yet aired) with badges, and shows a **present/total episode counter** on series folders.
- **Filter sidebar**: genre facets, age/size/rating/date ranges (dual sliders) and a "Missing" filter, plus sorting.
- Lets you **rename a folder** or **delete a folder** (with confirmation).
- A manual search on a series folder triggers a **full series-folder rename** (files + folder, synchronous, per the configured TV format) while TVDB/OMDb enrichment continues in the background. If the series offers several TVDB episode orders (official / alternate / Netflix), you are asked to pick one; **subtitles** (`.srt`, `.ass`, ...) are renamed alongside their video.
- Each series folder has an **open-in-TVDB** link (canonical TVDB slug).

### 3. History

- Every Rename / Move / Revert is stored in SQLite and shown here.
- Entries expose a **revert status** (`available` / `reverted` / `missing` / `done`) so you can roll back a rename with one click. History survives restarts.

### 4. Config

- Enter your **TVDB** and **OMDb** API keys (side by side) with a **test** button each, adjust paths and naming formats, and switch the interface language.

> **Caching summary:** searches, per-file results and details are cached in SQLite **permanently by default** — no cache to manage manually, no repeated API calls. The optional **auto-refresh** (Config → "Rafraîchissement automatique des infos") re-queries the API in the background for entries older than the given number of days; `0` (default) disables it.

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

### Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

The suite covers the scanner (title/year/episode parsing), the pure-Python duration parser (MP4/MKV/AVI), the SQLite layer (`db.py`), the TVDB API handlers (mocked, no network), the Flask routes (isolated app instance with a temp database), the library episode detection (including multi-episode files) and the filename generation module.

Linting uses [ruff](https://docs.astral.sh/ruff/):

```bash
ruff check src tests app.py
```

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
  "tv_format": "{n} - {s00e00} - {t}",
  "lib_fast_scan": true,
  "cache_refresh_days": 0,
  "tvdb_episode_order": "auto",
  "show_s00_specials": false
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
| `lib_fast_scan` | Fast library scan (non-recursive folder sizes) | `true` |
| `cache_refresh_days` | Auto-refresh of cached API data in the background (0 = disabled) | `0` |
| `tvdb_episode_order` | TVDB episode order: `auto`, `official`, `alternate` or `netflix` (per-series choice from manual search wins) | `auto` |
| `show_s00_specials` | Include special episodes (season 0) in missing episodes and episode counters | `false` |

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
| `{n:en}` | English title (2- or 3-letter codes, TVDB translations) | `The Matrix` |

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
| `{n:en}` | English title (2- or 3-letter codes, TVDB translations) | `Breaking Bad` |

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
├── app.py                    # Lanceur : crée l'app Flask et démarre le serveur
├── src/                      # Code applicatif (métier + routes)
│   ├── state.py              # État partagé (config, scanner, api_handler, progress)
│   ├── config.py             # Chargement / sauvegarde de la config
│   ├── auth.py               # Authentification (mot de passe)
│   ├── history.py            # Historique des opérations (persisté en base)
│   ├── utils.py              # Déplacement de fichiers, progression, helpers
│   ├── naming.py             # Génération des noms (port du JS generateFilename)
│   ├── db.py                 # SQLite (history + caches)
│   ├── scanner.py            # Scan des fichiers médias (MediaScanner)
│   ├── mediaduration.py      # Lecture de durée (MP4/MKV/AVI, Python pur)
│   ├── duration.py           # Cache durée + worker FIFO (scan rapide)
│   ├── network.py            # Découverte réseau (SMB, LDAP, postes LAN)
│   ├── library.py            # Bibliothèque (tailles, épisodes, présence)
│   ├── cache.py              # Caches de recherche + worker de rafraîchissement
│   ├── logging_setup.py      # Logging coloré (ANSI/Windows, NO_COLOR)
│   ├── watcher.py            # Surveillance du dossier d'entrée + SSE
│   ├── api/
│   │   ├── tvdb.py           # Client TVDB (recherche, détails, épisodes)
│   │   ├── handler.py        # Façade API (délégation TVDB)
│   │   └── omdb.py           # Service OMDb (notes, quota, résolution TVDB→IMDb)
│   └── routes/               # Blueprints Flask (1 fichier par domaine)
│       ├── __init__.py       # create_app() : assemblage + workers de fond
│       ├── auth.py           # /login, /
│       ├── browse.py         # /api/browse, /api/browse-net
│       ├── scan.py           # /api/scan, /api/scan/events
│       ├── search.py         # /api/search/*, /api/movie/*, /api/tv/*
│       ├── library.py        # /api/library*, /api/lib-durations
│       ├── files.py          # /api/move*, /api/rename, /api/revert, /api/history*
│       ├── config.py         # /api/config, /api/test-keys
│       └── usage.py          # /api/usage
├── scripts/
│   └── check_js.py           # Vérifie l'équilibrage des accolades de app.js
├── .github/workflows/
│   ├── ci.yml                # CI : lint (ruff) + tests (pytest) + coverage
│   └── docker.yml            # Build Docker (push sur release, build sur PR)
├── config.example.json       # Configuration template
├── requirements.txt          # Python dependencies
├── requirements-dev.txt      # Test dependencies (pytest, ruff)
├── ruff.toml                 # Config lint (ruff)
├── tests/                    # Unit tests (pytest)
│   ├── conftest.py           # Fixtures: isolated DB + Flask app
│   ├── test_scanner.py
│   ├── test_mediaduration.py
│   ├── test_db.py
│   ├── test_api_handler.py
│   ├── test_app.py
│   ├── test_library.py       # Références d'épisodes (multi-épisodes inclus)
│   └── test_naming.py        # Génération de noms (port JS)
├── Dockerfile                # Docker build
├── docker-compose.yml        # Docker Compose config
├── templates/
│   ├── index.html            # Main page
│   └── login.html            # Login page
├── static/
│   ├── css/                  # Styles par partie
│   │   ├── base.css          # Variables, layout, header, nav, boutons, modal
│   │   ├── files.css         # Grille fichiers, aperçus, actions, transfert
│   │   ├── library.css       # Bibliothèque : filtres, sliders, arbre, grille
│   │   ├── history.css       # Historique
│   │   ├── config.css        # Configuration
│   │   ├── picker.css        # Sélecteur de dossiers
│   │   ├── search.css        # Résultats de recherche
│   │   └── login.css         # Page de connexion
│   ├── js/                   # Logique frontend par partie
│   │   ├── i18n.js           # Translations (fr, en, es, de, it, pt)
│   │   ├── core/             # utils, format, modal
│   │   ├── files/            # scan, render, rename, move, search, preview
│   │   ├── library/          # tree, sort, filters, sliders, enrich, durations, actions, view (grille), preview
│   │   ├── history/          # Historique
│   │   ├── config/           # config, picker
│   │   └── app.js            # Navigation + init
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

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.
