# Changelog

## [1.0.5] - 2026-08-11

### Added
- Backend-driven transfer movement now accepts the current on-disk file name as the source of truth for the target name during move and copy operations.
- A dedicated confirmation modal warns when the current file name differs from the search suggestion that generated the preview rename proposal.
- History rendering now uses a server-side `is_reverted` signal to indicate that a move has been rolled back instead of inferring the state from the row order.

### Changed
- Move operations now always use the active filename as the file to transfer instead of trusting the front-end `new_name` proposal payload.
- The frontend now batches preview loading to avoid saturating the browser with search/detail requests at startup.
- The transfer-progress poll interval was relaxed to reduce unnecessary network churn.

### Fixed
- Revert history entries no longer inherit an incorrect “Fichier introuvable” visual state from a sibling row.
- The move progress UI no longer assumes a fake `moving` phase should drive a 100% static bar for a copy/move operation.
- The backend now exposes a live transfer `progress` contract backed by bytes copied and percentage growth.

---

## [1.0.4] - 2026-06-18

### Added
- `hist_change` i18n key for the history table column header — translated in all 6 languages

### Fixed
- Rename and Move buttons displayed raw key `btn-rename` instead of translated label
- Action buttons in file table wrapped to a second line instead of staying on one row
- File name column overflowed into the poster/preview column on long filenames
- Folder picker modal contained a stray GitHub footer link in the middle of the UI
- History tab label, history buttons and all history-related strings were not translated in ES, DE, IT, PT
- Operation type badges (Rename / Move / Revert) and Revert button in history table were not re-rendered on language switch
- `hist_change` column header was hardcoded in French regardless of selected language
- Language switch now correctly re-renders both the file table and the history table

---

## [1.0.3] - 2026-06-17

### Added
- Real-time move progress bar with speed (MB/s) and ETA for cross-drive transfers
- Smart same-drive detection: instant rename via `shutil.move` with shimmer animation instead of fake byte-level progress
- File-gone verification after move: polls `/api/scan` until the source file is confirmed absent before removing the row — progress bar fills gradually (0% → 95%) then shows ✓ when confirmed
- Custom confirmation modal for history clear — replaces native `confirm()` dialog, styled to match the app theme
- 6-language interface: added ES, DE, IT, PT alongside existing FR and EN

### Changed
- History table rows are now half the height (52px) for a more compact view
- Move progress has three distinct phases: `moving` (shimmer, same-drive rename), `copying` (real % with speed/ETA, cross-drive), `cleaning` (shimmer at 99%)
- File row stays visible with "✓ Déplacé" for 800ms after confirmation before disappearing
- `hist_confirm_clear` i18n key is now a full sentence; added `hist_confirm_title` key in all languages
- `progress-bar-container` uses `isolation: isolate` so the percentage text always renders on top

### Fixed
- Removed double `os.remove()` call in `_run_file_op` — source file was already deleted by `_move_path`
- Same-drive moves no longer simulate fake progress values before the operation completes

---

## [1.0.2] - 2026-06-10

### Changed
- Refactored `api_handler.py`: removed dead methods (`get_series_extended`, `get_episode_details`), extracted shared helpers (`_remote_ids`, `_cert`, `_poster`)
- Refactored `app.py`: factorized move/revert thread logic into `_run_file_op`, removed legacy `/api/rename-history` route
- Refactored `app.js`: added `postJSON` helper, removed dead `doRevert` and `onFormatInput` functions
- CSS: introduced CSS custom properties (`--c-*`) across all stylesheets — no visual changes
- HTML: removed all inline `style=""` attributes, replaced with CSS classes; fixed `login.html` hardcoded `/static/` paths to use `url_for`

### Fixed
- `repair_history.py` was writing `{}` instead of `[]` when resetting history file
- `rename_history.json.example` contained `{}` instead of `[]`

### Removed
- `_archive/` folder (obsolete test scripts and migration docs)

---

## [1.0.1] - 2026-04-16

### Fixed
- Fixed JSON corruption errors in rename history file — now auto-recovers with backup
- Titles with colons or hyphens (e.g. "Arrow: The Series") now display correctly
- Episode titles with colons or hyphens are properly cleaned in filenames

### Changed
- Rename history loader now automatically repairs corrupted files
- Better error reporting in frontend with clearer error messages
- Title cleaning applied to both backend (Python) and frontend (JavaScript)

### Added
- `repair_history.py` script to manually fix corrupted rename history files

---

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
- Rename All and Move All batch actions
- Folder picker for media paths
- Docker support
- MDI icons throughout the UI
- Dark theme (orange & dark grey)
