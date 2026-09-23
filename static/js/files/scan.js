// ── State ─────────────────────────────────────────────────────────────────────
let currentFilter = '';
let allFiles = [];
let filesPreviews = {};
let activeTransfers = {};  // job_id -> { idx, pollInterval, startTime }
const PREVIEW_PARALLELISM = 4;
let previewQueue = [];
let activePreviewLoads = 0;

// ── Flash des nouveaux fichiers : les éléments apparus depuis le dernier scan
// clignotent le temps de s'installer (pas au 1er scan après chargement).
const LIB_FLASH_KEY = 'cleanflick_flash_paths';
const FLASH_TTL = 30 * 60 * 1000;
function loadSeenPaths() {
    try {
        const raw = JSON.parse(localStorage.getItem(LIB_FLASH_KEY) || '[]');
        return new Map(Array.isArray(raw) ? raw : []);
    } catch (e) { return new Map(); }
}
function flashNewElements(rootEl) {
    if (!rootEl) return;
    const el = rootEl.querySelector?.('.file-row[data-file-path]');
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    try {
        el.animate(
            [{ opacity: 1 }, { opacity: 0.25 }, { opacity: 1 }, { opacity: 0.25 }, { opacity: 1 }],
            { duration: 2400, easing: 'ease-in-out' }
        );
    } catch (e) {}
}
function flashNewFiles(paths, rootEl) {
    const now = Date.now();
    const seen = loadSeenPaths();
    // 1er scan après rechargement : tout est « nouveau » → on ne flashe pas.
    const fresh = seen.size ? [...paths].filter(p => !seen.has(p)) : [];
    if (fresh.length) {
        fresh.forEach(p => seen.set(p, now));
        try { localStorage.setItem(LIB_FLASH_KEY, JSON.stringify([...seen])); } catch (e) {}
        setTimeout(() => flashNewElements(rootEl), 60);
    } else {
        paths.forEach(p => { if (!seen.has(p)) seen.set(p, now); });
        const pruned = [...seen].filter(([p, ts]) => now - ts < FLASH_TTL);
        if (pruned.length !== seen.size) {
            try { localStorage.setItem(LIB_FLASH_KEY, JSON.stringify(pruned)); } catch (e) {}
        }
    }
}

// ── Scan ──────────────────────────────────────────────────────────────────────
function scanFiles() {
    const tbody = document.getElementById('files-tbody');
    tbody.innerHTML = `<div class="loading-row"><span class="spinner"></span> ${tr('scanning')}</div>`;
    allFiles = [];
    fetch('/api/scan')
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
            const newPaths = new Set((data || []).map(f => f.path));

            // Supprimer les aperçus pour les fichiers qui ont disparu,
            // sauf ceux en cours de déplacement (toujours affichés jusqu'à la fin).
            Object.keys(filesPreviews).forEach(p => { if (!newPaths.has(p) && !isTransferring(p)) delete filesPreviews[p]; });

            // Mettre à jour la liste globale des fichiers
            allFiles = data || [];
            renderTable();
            flashNewFiles(newPaths, tbody);

            // L'onglet Fichiers prépare automatiquement les aperçus et noms
            // proposés; le renommage reste une action explicite de l'utilisateur.
            allFiles.forEach(file => {
                if (!filesPreviews[file.path]) enqueuePreview(file);
            });
        })
        .catch(e => { if (!e.message.includes('401')) tbody.innerHTML = `<tr><td colspan="4" class="message error">${tr('err_scan')} ${esc(e.message)}</td></tr>`; });
}

function enqueuePreview(file) {
    if (!file?.path) return;
    if (filesPreviews[file.path]?.loading) return;
    filesPreviews[file.path] = { loading: true, data: null, error: null };
    updateFileRow(file.path);
    previewQueue.push(file);
    flushPreviewQueue();
}

function flushPreviewQueue() {
    while (previewQueue.length && activePreviewLoads < PREVIEW_PARALLELISM) {
        const next = previewQueue.shift();
        if (!next?.path) continue;
        activePreviewLoads += 1;
        loadPreviewForFile(next)
            .finally(() => {
                activePreviewLoads -= 1;
                flushPreviewQueue();
            });
    }
}

function loadPreviewForFile(file) {
    if (!file?.path) return Promise.resolve();
    filesPreviews[file.path] = { loading: true, data: null, error: null };
    updateFileRow(file.path);

    return postJSON('/api/search/auto', {
        title: file.title,
        filename: file.filename,
        season: file.season,
        episode: file.episode,
        media_hint: file.media_type,
        path: file.path
    })
    .then(result => {
        const results = result?.results || [];
        if (!results.length) {
            filesPreviews[file.path] = { loading: false, data: null, error: null };
            updateFileRow(file.path);
            return Promise.resolve();
        }

        const top = results[0];

        // Média introuvable sur TVDB : les infos proviennent d'OMDb (pas de
        // poster) ; pas d'appel /api/movie|tv pour éviter d'importer les
        // métadonnées d'un autre titre.
        if (top.not_found) {
            const od = top.omdb || {};
            const genre = (od.Genre && od.Genre !== 'N/A') ? od.Genre : '';
            const details = {
                title: top.title || '', year: top.year || '',
                imdbid: top.imdb_id || '', imdb: top.imdb_id || '',
                genres: genre, genre: genre.split(',')[0].trim() || '',
                runtime: (od.Runtime && od.Runtime !== 'N/A') ? od.Runtime : '',
                rating: od.imdbRating && od.imdbRating !== 'N/A' ? od.imdbRating : '',
                release_date: od.Released || '', airdate: od.Released || '',
                not_found: true,
            };
            filesPreviews[file.path] = { loading: false, data: { source: top, details }, error: null };
            updateFileRow(file.path);
            return Promise.resolve();
        }

        const url = result.media_type === 'movie'
            ? `/api/movie/${top.id}?source=tvdb`
            : `/api/tv/${top.id}?season=${file.season || 1}&episode=${file.episode || 1}&source=tvdb`;

        return fetch(url).then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
        }).then(details => {
            mergeDetails(details, top);

            filesPreviews[file.path] = { loading: false, data: { source: top, details }, error: null };
            updateFileRow(file.path);
        });
    })
    .catch(e => {
        console.error('Preview load error for', file.path, ':', e);
        filesPreviews[file.path] = { loading: false, data: null, error: e.message };
        updateFileRow(file.path);
    });
}
