// ── State ─────────────────────────────────────────────────────────────────────
let currentFilter = '';
let allFiles = [];
let filesPreviews = {};
let activeTransfers = {};  // job_id -> { idx, pollInterval, startTime }
const PREVIEW_PARALLELISM = 4;
let previewQueue = [];
let activePreviewLoads = 0;
let filePreviewObserver = null;
const previewWaiters = new Map();
let _filesScanPromise = null;
let _filesScanController = null;
let _filesScanSeq = 0;
let _filesLastScanAt = 0;
let _filesScanLoaded = false;
const FILES_SCAN_TTL = 15_000;
const previewAbortControllers = new Map();
const previewDetailCache = new Map();

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
function scanFiles(force = true) {
    const tbody = document.getElementById('files-tbody');
    if (!force && _filesScanLoaded && Date.now() - _filesLastScanAt < FILES_SCAN_TTL) {
        renderTable();
        return Promise.resolve(allFiles);
    }
    if (_filesScanPromise && !force) return _filesScanPromise;
    if (_filesScanController) _filesScanController.abort();
    const controller = new AbortController();
    _filesScanController = controller;
    const seq = ++_filesScanSeq;
    if (!allFiles.length) {
        tbody.innerHTML = Array.from({ length: 6 }, () => `<div class="loading-row skeleton skeleton-row" role="row"><div role="gridcell"></div></div>`).join('');
    } else {
        renderTable();
    }
    const promise = fetch('/api/scan', { signal: controller.signal })
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
            if (seq !== _filesScanSeq) return allFiles;
            const previousFiles = new Map(allFiles.map(file => [file.path, file]));
            const newFiles = new Map((data || []).map(file => [file.path, file]));
            const newPaths = new Set(newFiles.keys());

            // Supprimer les aperçus pour les fichiers qui ont disparu,
            // sauf ceux en cours de déplacement (toujours affichés jusqu'à la fin).
            Object.keys(filesPreviews).forEach(p => {
                const oldFile = previousFiles.get(p);
                const newFile = newFiles.get(p);
                const changed = oldFile && newFile && previewFingerprint(oldFile) !== previewFingerprint(newFile);
                if ((!newPaths.has(p) || changed) && !isTransferring(p)) {
                    previewAbortControllers.get(p)?.abort();
                    previewAbortControllers.delete(p);
                    previewQueue = previewQueue.filter(file => file.path !== p);
                    delete filesPreviews[p];
                    resolvePreviewWaiters({ path: p });
                }
            });

            // Mettre à jour la liste globale des fichiers
            allFiles = data || [];
            _filesScanLoaded = true;
            _filesLastScanAt = Date.now();
            renderTable();
            flashNewFiles(newPaths, tbody);
            return allFiles;
        })
        .catch(e => {
            if (seq === _filesScanSeq && e.name !== 'AbortError' && !e.message.includes('401') && !allFiles.length)
                tbody.innerHTML = `<div class="file-row" role="row"><div role="gridcell" class="message error">${tr('err_scan')} ${esc(e.message)}</div></div>`;
            return allFiles;
        })
        .finally(() => {
            if (seq === _filesScanSeq) {
                _filesScanPromise = null;
                _filesScanController = null;
            }
        });
    _filesScanPromise = promise;
    return promise;
}

function previewFingerprint(file) {
    return [file.path, file.filename, file.title, file.season, file.episode, file.media_type].join('\u001f');
}

function enqueuePreview(file) {
    if (!file?.path) return;
    if (filesPreviews[file.path]?.loading) return;
    filesPreviews[file.path] = { loading: true, data: null, error: null };
    updateFileRow(file.path);
    previewQueue.push(file);
    flushPreviewQueue();
}

function observeFilePreviews(root) {
    const rows = root.querySelectorAll('.file-row[data-file-path]');
    const loadRowPreview = row => {
        const path = decodeURIComponent(row.dataset.filePath);
        const file = allFiles.find(item => item.path === path);
        if (file && !filesPreviews[path]) enqueuePreview(file);
    };
    if (typeof IntersectionObserver === 'undefined') {
        rows.forEach(loadRowPreview);
        return;
    }
    if (!filePreviewObserver) {
        filePreviewObserver = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                filePreviewObserver.unobserve(entry.target);
                loadRowPreview(entry.target);
            });
        }, { rootMargin: '200px 0px' });
    }
    rows.forEach(row => {
        const path = decodeURIComponent(row.dataset.filePath);
        if (!filesPreviews[path] && row.dataset.previewObserved !== 'true') {
            row.dataset.previewObserved = 'true';
            filePreviewObserver.observe(row);
        }
    });
}

function waitForFilePreview(file) {
    const preview = filesPreviews[file.path];
    if (preview && !preview.loading) return Promise.resolve();
    const promise = new Promise(resolve => {
        const waiters = previewWaiters.get(file.path) || [];
        waiters.push(resolve);
        previewWaiters.set(file.path, waiters);
    });
    if (!preview) enqueuePreview(file);
    return promise;
}

function ensureFilePreviews(files) {
    return Promise.all(files.map(waitForFilePreview))
        .then(() => files.filter(file => filesPreviews[file.path]?.data));
}

function resolvePreviewWaiters(file) {
    const waiters = previewWaiters.get(file.path) || [];
    previewWaiters.delete(file.path);
    waiters.forEach(resolve => resolve());
}

function flushPreviewQueue() {
    while (previewQueue.length && activePreviewLoads < PREVIEW_PARALLELISM) {
        const next = previewQueue.shift();
        if (!next?.path) continue;
        activePreviewLoads += 1;
        loadPreviewForFile(next)
            .finally(() => {
                activePreviewLoads -= 1;
                resolvePreviewWaiters(next);
                flushPreviewQueue();
            });
    }
}

function loadPreviewForFile(file) {
    if (!file?.path) return Promise.resolve();
    const controller = new AbortController();
    previewAbortControllers.set(file.path, controller);
    filesPreviews[file.path] = { loading: true, data: null, error: null, fingerprint: previewFingerprint(file) };
    updateFileRow(file.path);

    return fetch('/api/search/auto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
        title: file.title,
        filename: file.filename,
        season: file.season,
        episode: file.episode,
        media_hint: file.media_type,
        path: file.path
        })
    }).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(result => {
        if (controller.signal.aborted || !allFiles.some(item => item.path === file.path
            && previewFingerprint(item) === previewFingerprint(file))) return;
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

        const provisional = { title: top.title || file.title || '', year: top.year || '' };
        filesPreviews[file.path] = { loading: true, data: { source: top, details: provisional }, error: null, fingerprint: previewFingerprint(file) };
        updateFileRow(file.path);
        let detailPromise = previewDetailCache.get(url);
        if (!detailPromise) {
            detailPromise = fetch(url).then(r => {
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.json();
            });
            previewDetailCache.set(url, detailPromise);
            detailPromise.catch(() => previewDetailCache.delete(url));
            if (previewDetailCache.size > 500) previewDetailCache.delete(previewDetailCache.keys().next().value);
        }
        return detailPromise.then(details => {
            if (controller.signal.aborted || !allFiles.some(item => item.path === file.path
                && previewFingerprint(item) === previewFingerprint(file))) return;
            mergeDetails(details, top);

            filesPreviews[file.path] = { loading: false, data: { source: top, details }, error: null, fingerprint: previewFingerprint(file) };
            updateFileRow(file.path);
        });
    })
    .catch(e => {
        if (e.name === 'AbortError' || controller.signal.aborted) return;
        console.error('Preview load error for', file.path, ':', e);
        if (!allFiles.some(item => item.path === file.path && previewFingerprint(item) === previewFingerprint(file))) return;
        filesPreviews[file.path] = { loading: false, data: null, error: e.message };
        updateFileRow(file.path);
    }).finally(() => {
        if (previewAbortControllers.get(file.path) === controller) previewAbortControllers.delete(file.path);
    });
}
