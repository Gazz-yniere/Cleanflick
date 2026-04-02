<div align="center">
  <img src="static/CleanFlick.png" alt="CleanFlick" width="120">
  <h1>CleanFlick</h1>
  <p>Automatic media file renamer powered by TVDB API</p>

  ![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
  ![Flask](https://img.shields.io/badge/Flask-2.3-lightgrey?logo=flask)
  ![TVDB](https://img.shields.io/badge/API-TVDB%20v4-orange)
  ![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker)
  ![License](https://img.shields.io/badge/License-MIT-green)
</div>

---

## ✨ Features

- 🔍 Auto-detection of movies and TV shows via TVDB v4
- 📝 Filebot-style naming format (`{n}`, `{y}`, `{s00e00}`, `{t}`, `{imdb}`, `{n:fr}`...)
- 🌍 Multi-language title support (`{n:fr}`, `{n:de}`, `{n:ja}`...)
- 🔗 External IDs in filenames (IMDb, TVDB, TMDB)
- 📂 Recursive folder scanning
- ↩ Rename history with revert support
- 🔒 Optional password protection
- 🇫🇷 🇬🇧 French / English interface
- 🐳 Docker ready

---

## 🚀 Quick Start

### Docker (Recommended)

```bash
cp config.example.json config.json
# Edit config.json with your TVDB API key
docker-compose up -d
```
→ http://localhost:5000

### Local

```bash
pip install -r requirements.txt
cp config.example.json config.json
# Edit config.json with your TVDB API key
python app.py
```

---

## ⚙️ Configuration

Copy `config.example.json` to `config.json` and fill in your settings:

```json
{
  "tvdb_api_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "movie_path": "/downloads/movie",
  "tv_path": "/downloads/tv_shows",
  "movie_format": "{n} ({y})",
  "tv_format": "{n} - {s00e00} - {t}",
  "password": ""
}
```

### TVDB API Key
1. Create a free account at https://www.thetvdb.com
2. Go to **Dashboard → API Access**
3. Copy your API key (UUID format)
4. Paste it in **Settings → TVDB API Key** or directly in `config.json`

---

## 📝 Naming Format

### Movie Variables
| Variable | Description | Example |
|----------|-------------|---------|
| `{n}` | Title | `The Matrix` |
| `{y}` | Year | `1999` |
| `{ny}` | Title + Year | `The Matrix (1999)` |
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
| `{n}` | Series title | `Breaking Bad` |
| `{y}` | Year | `2008` |
| `{s00e00}` | S01E01 format | `S01E01` |
| `{sxe}` | 1x01 format | `1x01` |
| `{s:02d}` | Season padded | `01` |
| `{e:02d}` | Episode padded | `01` |
| `{t}` | Episode title | `Pilot` |
| `{absolute}` | Absolute number | `42` |
| `{airdate}` | Air date | `2008-01-20` |
| `{tvdbid}` | TVDB ID | `81189` |
| `{network}` | Network | `AMC` |

### Format Examples

**Movies:**
```
{n} ({y})                              → The Matrix (1999).mkv
{n} ({y}) [imdbid-{imdb}]             → The Matrix (1999) [imdbid-tt0133093].mkv
{n} ({y}) [imdbid-{imdb}] - {n:fr}   → The Matrix (1999) [imdbid-tt0133093] - Matrix.mkv
```

**TV Shows:**
```
{n} - {s00e00} - {t}                              → Breaking Bad - S01E01 - Pilot.mkv
{n} ({y}) [tvdbid-{tvdbid}] - {s00e00} - {t}     → Breaking Bad (2008) [tvdbid-81189] - S01E01 - Pilot.mkv
```

---

## 🐳 Docker Volumes

```yaml
volumes:
  - ./downloads:/downloads
  - ./config.json:/app/config.json
  - ./rename_history.json:/app/rename_history.json
```

---

## 📁 Project Structure

```
CleanFlick/
├── app.py                  # Flask backend + routes
├── scanner.py              # Recursive folder scanner
├── api_handler.py          # TVDB v4 API client
├── rename_engine.py        # Filebot-style rename engine
├── templates/
│   ├── index.html          # Main UI
│   └── login.html          # Login page
├── static/
│   ├── app.js              # Frontend logic
│   ├── i18n.js             # FR/EN translations
│   ├── base.css            # Base styles
│   ├── files.css           # Files table styles
│   ├── config.css          # Config page styles
│   ├── login.css           # Login page styles
│   ├── CleanFlick.png      # Logo
│   └── CleanFlick.ico      # Favicon
├── config.example.json     # Config template
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 🔒 Security

- Set a password in **Settings → Security** or in `config.json`
- Leave empty to disable protection
- `config.json` is excluded from git (contains your API key)

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

## 📄 License

MIT
