// ── Manual Search ─────────────────────────────────────────────────────────────
function openManualSearchModal(title, opts = {}) {
    window._manualSearchFilePath = opts.filePath ?? null;
    window._manualSearchFolderPath = opts.folderPath ?? null;
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
    const title = document.getElementById('search-title').value.trim();
    if (!title) return;
    const resultsDiv = document.getElementById('manual-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div>${tr('searching')}</div>`;
    const isFolder = !!folderPath;
    const file = isFolder ? null : allFiles.find(f => f.path === filePath);
    const mediaType = isFolder ? 'tv' : (file ? (file.media_type === 'movie' ? 'movie' : 'tv') : 'tv');
    const endpoint = mediaType === 'movie' ? '/api/search/movie' : '/api/search/tv';
    const payload = { title, path: isFolder ? folderPath : filePath, force_refresh: true };
    if (!isFolder && mediaType !== 'movie') { payload.season = file?.season; payload.episode = file?.episode; }
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
            const poster = (r.poster || od.Poster) ? `<img src="${esc(r.poster || od.Poster)}" alt="">` : (mediaType === 'movie' ? '🎬' : '📺');
            html += `<div class="result-item" data-ridx="${i}" onclick="selectResult(this)">
                <div class="result-poster">${poster}</div>
                <div class="result-info">
                    <div class="result-title">${esc(r.title || '')}</div>
                    <div class="result-year">${r.year || 'N/A'}</div>
                    ${omdbLine}
                    <div class="result-type">TVDB #${r.id}</div>
                </div></div>`;
        });
        resultsDiv.innerHTML = html + '</div>';
    })
    .catch(e => { resultsDiv.innerHTML = `<div class="message error">${tr('err_scan')} ${esc(e.message)}</div>`; });
}

function selectResult(el) {
    const result = window._searchResults[parseInt(el.getAttribute('data-ridx'))];
    const folderPath = window._manualSearchFolderPath;
    if (folderPath) {
        window._manualSearchFolderPath = null;
        window._manualSearchFromLibrary = false;
        closeModal('manualSearchModal');
        libRenameFolder(folderPath, result);
        return;
    }
    const filePath = window._manualSearchFilePath;
    const file = allFiles.find(f => f.path === filePath);
    const resultsDiv = document.getElementById('manual-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    const url = file.media_type === 'movie'
        ? `/api/movie/${result.id}?source=tvdb`
        : `/api/tv/${result.id}?season=${file.season || 1}&episode=${file.episode || 1}&source=tvdb`;
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
            refreshLibrary();
        })
        .catch(e => { alert(`✗ ${tr('err_rename')}\n${e.message}`); refreshLibrary(); });
}
