// ── Manual Search ─────────────────────────────────────────────────────────────
function openManualSearchModal(title, opts = {}) {
    window._manualSearchFilePath = opts.filePath ?? null;
    window._manualSearchFolderPath = opts.folderPath ?? null;
    window._manualSearchBatchPaths = opts.batchPaths ?? null;
    window._manualSearchFromLibrary = !!opts.fromLibrary;
    document.getElementById('manualSearchContent').innerHTML = `
        <div class="form-field">
            <label>${tr('search_label')}</label>
            <input type="text" id="search-title" value="${esc(title)}" placeholder="..."
                onkeydown="if(event.key==='Enter') executeManualSearch()">
        </div>
        <button class="btn btn-primary" style="width:100%" onclick="executeManualSearch()">${tr('search_btn')}</button>
        <div id="manual-results" style="margin-top:15px;"></div>`;
    document.getElementById('manualSearchModal').classList.add('active');
    setTimeout(() => document.getElementById('search-title')?.focus(), 100);
}

function manualSearch(filePath) {
    const file = allFiles.find(f => f.path === filePath);
    if (!file) return;
    const title = file.filename
        .replace(/\.[^.]+$/, '').replace(/\s*\[[^\]]*\]/g, '')
        .replace(/\s*\([^)]{8,}\)/g, '').replace(/\s*\(\d{4}\)/g, '')
        .replace(/[._]/g, ' ').replace(/\s*[-]\s*[Ss]\d+[Ee]\d+.*/i, '')
        .replace(/\s*[Ss]\d+[Ee]\d+.*/i, '').replace(/\s*(19|20)\d{2}.*/i, '')
        .replace(/\s+/g, ' ').trim();
    openManualSearchModal(title, { filePath });
}

function executeManualSearch() {
    const folderPath = window._manualSearchFolderPath;
    const filePath = window._manualSearchFilePath;
    const batchPaths = window._manualSearchBatchPaths || [];
    const isBatch = batchPaths.length > 0;
    const title = document.getElementById('search-title').value.trim();
    if (!title) return;
    const resultsDiv = document.getElementById('manual-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div>${tr('searching')}</div>`;
    const isFolder = !!folderPath;
    const file = isFolder ? null : allFiles.find(f => f.path === filePath);
    const mediaType = isFolder || isBatch ? 'tv' : (file ? (file.media_type === 'movie' ? 'movie' : 'tv') : 'tv');
    const endpoint = mediaType === 'movie' ? '/api/search/movie' : '/api/search/tv';
    const payload = { title, force_refresh: true };
    if (isFolder) payload.path = folderPath;
    else if (!isBatch && filePath) payload.path = filePath;
    if (!isFolder && !isBatch && mediaType !== 'movie') { payload.season = file?.season; payload.episode = file?.episode; }
    postJSON(endpoint, payload)
    .then(results => {
        // Accept either the old array response or the new envelope { results: [], cache_source: '...' }
        let list = results;
        if (results && results.results) list = results.results;
        if (!list?.length) { resultsDiv.innerHTML = `<div class="message error">${tr('search_none')}</div>`; return; }
        window._searchResults = list;
        let html = `<p style="color:#888;font-size:1em;margin-bottom:10px;">${list.length} ${tr('search_results')}</p><div class="search-results">`;
        list.forEach((r, i) => {
            const isTv = mediaType !== 'movie';
            const od = r.omdb || {};
            const eod = r.episode_omdb || {};
            const omdbParts = [];
            if (isTv) {
                if (eod.imdbRating) omdbParts.push('ép. ★ ' + eod.imdbRating);
                if (eod.Runtime && eod.Runtime !== 'N/A') omdbParts.push('ép. ' + eod.Runtime);
            } else {
                if (od.imdbRating) omdbParts.push('★ ' + od.imdbRating);
                if (od.Rated && od.Rated !== 'N/A') omdbParts.push(od.Rated);
                if (od.Runtime && od.Runtime !== 'N/A') omdbParts.push(od.Runtime);
                if (od.Genre && od.Genre !== 'N/A') omdbParts.push(od.Genre);
            }
            const omdbLine = omdbParts.length ? `<div class="result-omdb">${omdbParts.map(esc).join(' · ')}</div>` : '';
            const notFound = r.not_found
                ? `<div class="search-badge search-badge--notfound" title="${esc(tr('search_not_found'))}"><i class="mdi mdi-alert-circle-outline"></i> ${esc(tr('search_not_found'))}</div>`
                : '';
            const poster = (r.poster || od.Poster) ? `<img src="${esc(r.poster || od.Poster)}" alt="">` : (mediaType === 'movie' ? '🎬' : '📺');
            html += `<div class="result-item" data-ridx="${i}" onclick="selectResult(this)">
                <div class="result-poster">${poster}</div>
                <div class="result-info">
                    <div class="result-title">${esc(r.title || '')}</div>
                    <div class="result-year">${r.year || 'N/A'}</div>
                    ${omdbLine}
                    ${notFound}
                    <div class="result-type">${r.not_found ? 'IMDb ' + esc(r.imdb_id || '') : 'TVDB #' + r.id}</div>
                </div></div>`;
        });
        resultsDiv.innerHTML = html + '</div>';
    })
    .catch(e => { resultsDiv.innerHTML = `<div class="message error">${tr('err_scan')} ${esc(e.message)}</div>`; });
}

function selectResult(el) {
    const result = window._searchResults[parseInt(el.getAttribute('data-ridx'))];
    const folderPath = window._manualSearchFolderPath;
    if (result?.not_found) {
        // Film introuvable sur TVDB : les infos OMDb (imdbid du nom du fichier)
        // sont sélectionnables → mises en cache, renommage laissé manuel.
        if (!folderPath) {
            const file = allFiles.find(f => f.path === window._manualSearchFilePath);
            if (file && file.media_type === 'movie' && result.omdb) {
                applyOmdbStubSelection(file, result);
                return;
            }
        }
        // Série (ou film sans OMDb) : pas de métadonnées d'épisodes sans TVDB.
        alert(tr('search_not_found'));
        return;
    }
    const batchPaths = window._manualSearchBatchPaths || [];
    if (batchPaths.length) {
        applySeriesResultToSelectedFiles(result, batchPaths);
        return;
    }
    if (folderPath) {
        postJSON(`/api/library/tvdb-orders/${encodeURIComponent(result.id)}`, { path: folderPath })
            .then(data => {
                const orders = data.orders || [];
                if (data.recommended_order) {
                    const selectedPath = window._manualSearchFolderPath;
                    window._manualSearchFolderPath = null;
                    window._manualSearchFromLibrary = false;
                    closeModal('manualSearchModal');
                    libRenameFolder(selectedPath, result, data.recommended_order);
                    return;
                }
                if (orders.length <= 1) {
                    const order = orders[0]?.value || 'official';
                    const selectedPath = window._manualSearchFolderPath;
                    window._manualSearchFolderPath = null;
                    window._manualSearchFromLibrary = false;
                    closeModal('manualSearchModal');
                    libRenameFolder(selectedPath, result, order);
                    return;
                }
                window._pendingSeriesChoice = { folderPath, result, orders };
                document.getElementById('manualSearchContent').innerHTML = `
                    <p class="series-order-question">Choisissez l'ordre des épisodes pour le renommage :</p>
                    <div class="series-order-list">${orders.map((o, i) =>
                        `<button class="btn btn-secondary series-order-choice" data-order-index="${i}" onclick="chooseSeriesOrder(this)">${esc(o.label)}</button>`
                    ).join('')}</div>`;
            })
            .catch(e => { alert(`Erreur TVDB: ${e.message}`); });
        return;
    }
    const filePath = window._manualSearchFilePath;
    const file = allFiles.find(f => f.path === filePath);
    const resultsDiv = document.getElementById('manual-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    const url = file.media_type === 'movie'
        ? `/api/movie/${result.id}?source=tvdb`
        : `/api/tv/${result.id}?season=${file.season ?? 1}&episode=${file.episode ?? 1}&source=tvdb`;
    fetch(url).then(r => r.json()).then(details => {
        mergeDetails(details, result);
        filesPreviews[file.path] = { loading: false, data: { source: result, details }, error: null };
        // Persist the manual selection to server-side file cache so refresh won't overwrite it
        try {
            postJSON('/api/search/cache-file', { path: file.path, media_type: file.media_type || 'movie', results: [ { id: result.id, imdb_id: details.imdbid, title: details.title || result.title, year: details.year || result.year, poster: result.poster, translations: details.translations || {}, details: details } ] })
            .catch(() => {});
        } catch (e) { /* ignore */ }
        updateFileRow(file);
        closeModal('manualSearchModal');
        const fromLibrary = window._manualSearchFromLibrary;
        window._manualSearchFromLibrary = false;
        if (fromLibrary) libApplyRename(filePath);
    }).catch(e => { resultsDiv.innerHTML = `<div class="message error">Erreur: ${esc(e.message)}</div>`; });
}

async function applySeriesResultToSelectedFiles(result, paths) {
    const files = paths.map(path => allFiles.find(file => file.path === path)).filter(Boolean);
    const resultsDiv = document.getElementById('manual-results');
    if (!files.length) {
        resultsDiv.innerHTML = `<div class="message error">${esc(tr('no_files_found'))}</div>`;
        return;
    }

    let completed = 0;
    let succeeded = 0;
    const failures = [];
    resultsDiv.innerHTML = `<div id="batch-search-progress" class="loading"><div class="spinner"></div></div>`;
    const updateProgress = () => {
        const progress = document.getElementById('batch-search-progress');
        if (progress) progress.innerHTML = `<div class="spinner"></div>${esc(tr('batch_search_progress'))} ${completed}/${files.length}`;
    };
    updateProgress();

    const queue = [...files];
    const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
        while (queue.length) {
            const file = queue.shift();
            if (!file) continue;
            let applied = false;
            try {
                const season = Number(file.season);
                const episode = Number(file.episode);
                const response = await fetch(`/api/tv/${result.id}?season=${season}&episode=${episode}&source=tvdb`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const details = await response.json();
                if (!details?.title) throw new Error(tr('no_result'));
                mergeDetails(details, result);
                const source = { ...result, imdb_id: details.imdbid || result.imdb_id };
                filesPreviews[file.path] = { loading: false, data: { source, details }, error: null };
                updateFileRow(file);
                applied = true;
                const cached = await postJSON('/api/search/cache-file', {
                    path: file.path,
                    media_type: 'tv',
                    results: [{ ...source, title: details.title || result.title, year: details.year || result.year, details }]
                });
                if (!cached.success) throw new Error(cached.message || 'Cache save failed');
                succeeded++;
            } catch (error) {
                failures.push({ filename: file.filename, message: error.message });
                if (!applied) {
                    filesPreviews[file.path] = { loading: false, data: null, error: error.message };
                    updateFileRow(file);
                }
            } finally {
                completed++;
                updateProgress();
            }
        }
    });
    await Promise.all(workers);

    const summaryKey = failures.length ? 'batch_search_partial' : 'batch_search_complete';
    const failureList = failures.length
        ? `<ul>${failures.map(failure => `<li>${esc(failure.filename)}: ${esc(failure.message)}</li>`).join('')}</ul>`
        : '';
    resultsDiv.innerHTML = `<div class="message ${failures.length ? 'error' : ''}">${esc(tr(summaryKey))} ${succeeded}/${files.length}</div>${failureList}
        <button class="btn btn-secondary" onclick="closeModal('manualSearchModal')">${esc(tr('batch_search_done'))}</button>`;
    window._manualSearchBatchPaths = null;
}

// Sélection d'un stub OMDb (film absent de TVDB) : construit les détails depuis
// le payload OMDb, met le résultat en cache serveur pour que le F5 le retrouve
// sans re-recherche, puis met à jour la ligne. Le renommage reste manuel.
function applyOmdbStubSelection(file, result) {
    const od = result.omdb || {};
    const genre = (od.Genre && od.Genre !== 'N/A') ? od.Genre : '';
    const details = {
        title: result.title || od.Title || '',
        year: result.year || '',
        imdbid: result.imdb_id || '', imdb: result.imdb_id || '',
        genres: genre, genre: genre.split(',')[0].trim() || '',
        runtime: (od.Runtime && od.Runtime !== 'N/A') ? od.Runtime : '',
        rating: (od.imdbRating && od.imdbRating !== 'N/A') ? od.imdbRating : '',
        release_date: od.Released || '', airdate: od.Released || '',
        not_found: true,
    };
    filesPreviews[file.path] = { loading: false, data: { source: result, details }, error: null };
    try {
        postJSON('/api/search/cache-file', { path: file.path, media_type: 'movie', results: [result] })
            .catch(() => {});
    } catch (e) { /* ignore */ }
    updateFileRow(file);
    closeModal('manualSearchModal');
}

function chooseSeriesOrder(button) {
    const pending = window._pendingSeriesChoice;
    if (!pending) return;
    const order = pending.orders[parseInt(button.dataset.orderIndex, 10)];
    if (!order) return;
    const { folderPath, result } = pending;
    window._pendingSeriesChoice = null;
    window._manualSearchFolderPath = null;
    window._manualSearchFromLibrary = false;
    closeModal('manualSearchModal');
    libRenameFolder(folderPath, result, order.value);
}

function libApplyRename(filePath) {
    const file = allFiles.find(f => f.path === filePath);
    if (!file || !file.filename) { refreshLibrary(); return; }
    const p = filesPreviews[file.path];
    if (!p?.data) { refreshLibrary(); return; }
    const newName = generateFilename(file, p.data.details);
    if (newName === file.filename) { refreshLibrary(); return; }
    postJSON('/api/rename', { path: file.path, new_name: newName })
        .then(data => {
            if (!data.success) { alert(`✗ ${tr('err_rename')}\n${data.message}`); refreshLibrary(); return; }
            // Le nom du fichier a changé : l'entrée cache de son dossier est périmée.
            if (typeof invalidateLibTreePath === 'function') invalidateLibTreePath(dirnameLibPath(file.path));
            refreshLibrary();
        })
        .catch(e => { alert(`✗ ${tr('err_rename')}\n${e.message}`); refreshLibrary(); });
}
