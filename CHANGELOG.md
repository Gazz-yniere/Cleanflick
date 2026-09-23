# Changelog

## [1.0.12] - 2026-09-23

### Added
- **Library grid view** — tree/grid toggle in the library sidebar. New endpoints: `GET /api/library/flat` (flat list of movies + series, 0 external API requests, background enrichment) and `GET /api/library/series/<tvdb_id>` (seasons + episodes with present/missing/upcoming state, 0 TVDB requests via in-memory memo).
- **Expandable series card** — clicking a series card expands it in place (one expanded at a time) into 3 columns: poster + info, season list with TVDB posters, episodes of the selected season. Season 1 opens by default; clicking the poster collapses. Episode rows show number, rating, title, runtime, air date and size; missing = red, upcoming = yellow.
- **Season posters** — `TVDBAPIHandler.get_series_seasons()` (1 request per season without an image, 24 h cache) + new `series_seasons_cache` table.
- **Upcoming episodes in the grid** — separate yellow badge (clock icon) for not-yet-aired episodes next to the present/total counter (red when episodes are missing); `ep_upcoming` propagated in `/api/library` and `/api/library/flat`.
- **Readable episode titles in the grid** — for present episodes the title is extracted from the local filename (`… - S01E01 - {t:en}`, multi-episode `S01E01-E02` / `1x02` supported) instead of the TVDB original-language title; upcoming episodes keep the TVDB title.
- **Poster hover preview** — floating preview box on hover over library/file rows (poster + meta chips, 120 ms delay).
- **Startup media pre-warm** — `cache.start_media_prewarm()` walks the libraries in the background at startup and feeds the worker (missing TVDB searches + global series OMDb).
- **Database cache purge** — `db.purge_stale_cache(roots)` removes orphan entries (dead paths in file_search_cache / media_duration / folder_size, vanished `[tvdbid-…]` in series caches); background pass `cache.run_purge_background()` (6 h minimum interval) + `POST /api/cache/purge`.
- **IMDb-aware search** — the IMDb ID embedded in a filename (`[imdbid-tt…]`) now reorders TVDB search results (matching ID first) and triggers a fresh search when the cache is polluted with a non-matching result; `omdb.ensure_series_omdb()` fetches the global series OMDb entry (1 request, locked).
- **Colored console logging** — new `src/logging_setup.py` (ANSI colors, compact format, noise filter, `NO_COLOR` to disable, `colorama` for Windows); the old `logging.basicConfig` call was removed from `app.py`. `colorama==0.4.6` added to `requirements.txt`.
- **Tests** — ~440 new lines: grid endpoints, season posters, upcoming badges, IMDb-aware search, cache purge.

### Improved
- **Library tree/grid filtering** — search box in the sidebar, debounced grid filter (120 ms), no DOM rebuild when the rendered content is unchanged (signature check).
- **Episode counter** — `_series_ep_counts()` now returns (present, total, upcoming); upcoming episodes excluded from the total.
- **`/api/library/series/<tvdb_id>`** detail endpoint powers the expanded card with 0 TVDB requests per open.
- **`.gitignore`** — dev tooling (node_modules, package*.json) and SQLite WAL files are ignored.

## [1.0.11] - 2026-09-09

### Added
- **Subtitles follow video renames** — `.srt` / `.sub` / `.ass` / `.ssa` / `.vtt` / `.idx` files are renamed alongside their video (same stem, including multi-episode labels), with a folder-wide catch-up pass for subtitles living in other subfolders of the series folder (e.g. SRT at the root, videos in `Season 1/`).
- **TVDB episode order selection** — after a manual search on a series folder, the available TVDB episode orders (official / alternate / Netflix) are listed when the series has more than one; `GET`/`POST /api/library/tvdb-orders/<id>`. New `tvdb_episode_order` config option (`auto` by default: Netflix order is picked automatically when the series offers one).
- **Upcoming episodes** — episodes not yet aired (TBA or future air date) are split out of the missing list: yellow badge + "upcoming" rows in the library, excluded from the series "present/total" counter.
- **TVDB web link** — "open in TVDB" button on series folders, resolved via the canonical TVDB slug (new `tvdb_slug` field, `GET /api/library/tvdb-link/<id>`).
- **Instant library reload (F5)** — the explored tree is cached in `localStorage` (1 h TTL, size-bounded with eviction of the largest folders, debounced saves); persisted folders render immediately and are re-validated in the background. Targeted invalidation on rename/move/delete.
- **`original_title` naming token** — `{n}`-family tokens now also resolve `original_title`; translation codes accept 2- or 3-letter forms (`{n:en}` = `{n:eng}`, TVDB returns ISO 639-2/T codes).
- **Multi-episode filename generation** — `multi_episode_label()` / `multi_episode_label_sxe()` produce `S01E01-E02` / `1x02-03` labels and episode titles are joined (`Title 1 & Title 2`) for `{s00e00}`, `{sxe}` and `{t}` overrides.
- **Config** — `cache_refresh_days` and `tvdb_episode_order` added to `DEFAULT_CONFIG` / `config.example.json`.
- **Tests** — 26 new tests (108 total): subtitle rename/catch-up, multi-episode labels, 2/3-letter translation codes, upcoming episodes, TVDB link/orders routes, OMDb quota retry, generic search cache, fast library render.

### Improved
- **Server-side media pre-warming** — a background worker (`src/cache.py`) queues every scanned/loaded media file: TVDB search + OMDb enrichment + duration parsing run off the HTTP request; series episodes and IMDb resolution are pre-fetched from series folder names.
- **OMDb quota retry** — when the daily quota locks OMDb for 24 h, a flag schedules one automatic re-pass over the whole library after the lock lifts; OMDb errors are also cached (negative entries) to stop hammering a failing API.
- **Long series episodes** — TVDB episode paging no longer stops at 20 pages (Pokémon etc.); it follows `totalPages` / `next` links up to a safety cap of 100.
- **TVDB translations** — fetched from the TVDB translation endpoint when the search payload has none (original language + English).
- **History batch insert** — `db.add_history_batch()` commits a whole series-folder rename in a single transaction (much faster on network shares).
- **Fast library render** — `?fast=1` mode: non-recursive folder sizes, name-derived metadata only, durations queued non-blockingly; full enrichment re-validated right after first paint.
- **Search cache** — `/api/search/auto` now also consults and writes the generic per-title cache (movie and TV), so a manual search reuses earlier results.
- **Library perf** — per-request memoization of TVDB→IMDb resolution and folder sizes (F5 no longer re-queries TVDB per series), episode-list memo (5 min), `dur_pending` only while the duration is actually queued.
- **Filters** — filters pre-load collapsed folders so they can evaluate their content; series rating no longer leaks into episode-level filter matches; folders kept visible while loading.
- **Files tab** — files without a preview yet show a manual-search button instead of a spinner; auto-enqueue only for files without a preview.

### Fixed
- **OMDb quota not retried** after the 24 h lockout; OMDb "not found" results were not cached.
- **Series with a Netflix alternate order** got the official episode list (wrong numbering) — now auto-detected from `seasonTypes`.
- **Episode counters** included not-yet-aired episodes in the total.
- **Manual search selection** did not become the cached series truth (a previous wrong association could persist) — the confirmed choice now replaces the cached series metadata.
- **`/api/scan`** triggered an implicit TVDB episode pre-fetch — only local duration work runs in the background now (TVDB needs explicit validation).
- **History status** re-checked every path on disk per entry — existence is now normalized once per unique path.
- **TVDB→IMDb resolution failures** were retried on every render (F5 with many series) — short in-memory negative cache added.
- **Rename result modal** was too narrow for long filenames — wider modal + ellipsized rows.
- **Language tokens** failed when TVDB returned 3-letter codes (`eng`) while the template used 2-letter (`{n:en}`).

## [1.0.10] - 2026-09-07

### Added
- **Frontend rework** — `static/app.js` (2539 lines), `static/app.css` and `static/i18n.js` are split into per-feature files: `static/js/` (`core/`, `files/`, `library/`, `history/`, `config/` + `app.js`) and `static/css/` (8 stylesheets). Purely structural: classic scripts, global functions, no build step.
- **`src/naming.py`** — server-side port of the JS `generateFilename` / `interpolate` / `seriesFolderName`: library renaming is now computed in Python, translation tokens (`{n:fr}`) and numeric formats included.
- **Full series-folder rename** (`POST /api/library/rename-folder-series`) — from a manual search on a library folder: files **and** folder are renamed synchronously per the configured TV format; TVDB parsing (episode titles), a second rename pass and OMDb enrichment continue in the background, followed by a `library-refresh` SSE event.
- **Episode "present / total" counter** on library series folders (`ep_present` / `ep_total`; S00 included only when the option is enabled).
- **Multi-episode files** — `S01E01-E02`, `S01E01E02`, `S01E01, E02` and `1x02-03` are recognized in the missing-episode detection (one file covers every episode in the range).
- **Library: filter sidebar** — genre facets, age/size/rating/date ranges (dual sliders) + a "Missing" filter, sorting moved into the sidebar.
- **`show_s00_specials` option** (Config) — include special episodes (season 0) in missing episodes and episode counters.
- **Tests** — `tests/test_naming.py` (filename generation) and `tests/test_library.py` (episode refs, multi-episode); 3 route tests added to `tests/test_app.py` (multi-episode missing episodes, synchronous series-folder rename).

### Changed
- **Static cache-bust** — `_static_version()` walks every `.js`/`.css` under `static/` instead of a fixed list of 3 files.
- **`scripts/check_js.py`** — checks brace/paren balance of all `static/js/**/*.js` instead of only `static/app.js`.
- **`present_episodes()` memoized** by folder mtime (`_EP_CACHE`) — re-scanning a series folder costs a single `scandir`.
- **Shared helpers** — `_refresh_scan_snapshot()` and `_migrate_file_cache()` moved from `src/routes/files.py` to `src/utils.py` (reused by the library routes).
- **README** — `static/` tree updated for the js/css split.

### Fixed
- **Multi-episode files under-counted** — missing-episode detection only recognized the first episode of an `S01E01-E02` file (the rest wrongly showed as missing).
- **Incomplete episode marker** — the per-filename episode index in the library ignored multi-episode ranges.

## [1.0.9] - 2026-09-01

### Added
- **CI GitHub Actions** — lint (ruff) + tests (pytest) sur push/PR, avec rapport HTML (artifact `test-report`), couverture de code (artifact `coverage`) et un check-run par test (`dorny/test-reporter`).
- **Endpoint `/health`** + `HEALTHCHECK` Docker et `healthcheck` compose pour surveiller le conteneur.
- **Posters dans la bibliothèque** — les épisodes et dossiers de série sans poster héritent du poster de la série (cache de recherche ou cache OMDb, sans requête API).
- **Badge CI** dans le README.

### Changed
- **Restructuration en paquet `src/`** — le code applicatif est empaqueté sous `src/` (métier + `src/api/` + `src/routes/`) ; `app.py` ne fait plus que l'assemblage et le lancement.
- **Build Docker sur PR** — l'image est construite (sans push) sur chaque pull request pour valider le Dockerfile.

### Fixed
- **`USER` mal placé** dans le Dockerfile (exécuté comme commande shell dans un `RUN`), ce qui cassait le build.

### Removed
- **`gevent`** retiré des dépendances de production (le worker `gthread` + le fallback stdlib des files SSE suffisent).

## [1.0.8] - 2026-08-20

### Added
- **SQLite persistence** — l'historique (avec revert) et l'ensemble des caches (recherches, résultats par fichier, détails, OMDb) sont désormais stockés dans une base unique `cleanflick.db` au lieu de fichiers JSON.
- **Cache avec expiration de 7 jours** — résultats de recherche, résultats par fichier et détails expirent automatiquement après 7 jours et sont re-récupérés depuis TVDB/OMDb au prochain accès.
- **Recherche manuelle forcée via l'API** (`force_refresh`) — on peut toujours trouver un titre alternatif, même quand une proposition est déjà en cache.
- **Sélections manuelles confirmées persistées** dans le cache par fichier, pour que l'auto-proposition renvoie le titre choisi.
- **Phase `verifying` du transfert** — après la copie, le backend vérifie la destination (100 %) avant de supprimer la source.
- **Bibliothèque** — parcours de l'arborescence de sortie, badges d'épisodes manquants, tri/filtre, renvoi vers la source, renommage/suppression de dossiers.

### Changed
- **Table Fichiers convertie de `<table>` en grille CSS** (`1fr 2fr 185px`) — répartition garantie 1/3–2/3 avec colonne Actions fixe ; colonne « Progression » supprimée.
- **Overlay de transfert repensé sur 2 lignes** — titre (icône + libellé + nom du fichier) puis barre de progression avec vitesse/ETA à côté.
- **Scan / scan auto : cache d'abord, sinon API** — l'onglet Fichiers se rafraîchit désormais de façon fiable pendant et après les transferts.
- **Boutons de filtre de la bibliothèque harmonisés** avec ceux de l'onglet Fichiers (même style).
- **Bouton Renommer en orange** (accent), comme les boutons de configuration.
- **Config API** — les clés TVDB et OMDb sont côte à côte sur deux colonnes, avec le bouton « tester la clé » juste après l'input.
- **Boutons Historique** — « Actualiser » en orange (primaire), « Tout effacer » en gris (secondaire).

### Fixed
- **Clé de cache OMDb des épisodes incohérente** — les métadonnées d'épisode sont cherchées avec la clé qualifiée par l'année (avec repli sur l'ancienne clé) ; l'auto-search utilise un rattachement OMDb d'épisode cohérent (top 3) qui ignore les résultats hors séries (économie de quota OMDb).
- **Fichiers réapparaissant après un « renvoi » non détectés** — suppression des réinitialisations de `scan_last_snapshot` dans les endpoints de renvoi pour que le watcher les détecte.
- **Table Fichiers non rafraîchie pendant les transferts** — suppression de la suppression SSE de `scanFiles()` durant les transferts.

### Removed
- **Bouton « Vider le cache TVDB » et route `/api/cache/clear`** — inutiles car les caches expirent seuls après 7 jours et la recherche manuelle rafraîchit instantanément.
- **Code mort** : `rename_engine.py` (module inutilisé), `db.find_history`, `db.file_cache_delete`, fonctions JS inutilisées (`pathFromKey`, `loadPreviewsAsync`, `omdbChips`, bloc d'auto-scan inerte, doublon `formatBytes`), règles CSS inutilisées et 20 clés i18n inutilisées.

### Refactored
- **`app.py`** — extraction de helpers de cache (`_file_fingerprint`, `_file_cache_lookup`, `_file_cache_store`, `_params_cache_key`) réutilisés par les 4 endpoints de recherche/cache (clés inchangées).
- **`app.js`** — extraction de `mergeDetails` et `openManualSearchModal` pour supprimer la duplication.
- **`files.css`** — fusion des styles d'en-tête partagés entre la table et la grille.

---

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
