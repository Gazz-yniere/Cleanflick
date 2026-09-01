# Changelog

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
