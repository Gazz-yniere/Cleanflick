# CleanFlick v1.0.10

## ✨ New
- **Frontend rework** — `app.js` / `app.css` / `i18n.js` split into per-feature files (`static/js/`: core, files, library, history, config; `static/css/`: 8 stylesheets), no build step
- **Full series-folder rename** — manual search on a library folder: files + folder renamed synchronously per the configured TV format, TVDB parsing and OMDb enrichment in the background (SSE refresh at the end)
- **`src/naming.py`** — Python port of the JS `generateFilename` (library renaming computed server-side)
- **Episode "present / total" counter** on series folders
- **Multi-episode files** (`S01E01-E02`, `S01E01E02`, `1x02-03`) recognized in the missing-episode detection
- **Library: filter sidebar** — genre, age, size, rating, date (sliders) + "Missing" filter
- **`show_s00_specials` option** — special episodes (S00) in missing episodes and counters
- **Tests** — `test_naming.py`, `test_library.py` and 3 route tests (82 tests total)

## 🔧 Improved
- Static cache-bust: `os.walk` over every `.js`/`.css` in `static/`
- `check_js.py`: checks all `static/js/**/*.js`
- `present_episodes()` memoized by folder mtime
- Shared `_refresh_scan_snapshot()` / `_migrate_file_cache()` helpers in `src/utils.py`

## 🐛 Fixed
- Multi-episode files under-counted (an `S01E01-E02` file only covered E01)
- Per-filename episode index: multi-episode ranges were ignored
