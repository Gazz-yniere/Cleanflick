// ── State ─────────────────────────────────────────────────────────────────────
let currentFilter = '';
let allFiles = [];
let filesPreviews = {};
let activeTransfers = {};  // job_id -> { idx, pollInterval, startTime }
const PREVIEW_PARALLELISM = 4;
let previewQueue = [];
let activePreviewLoads = 0;

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

            // Enqueue only files that don't already have a preview (new files)
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
