// ── Render ────────────────────────────────────────────────────────────────────
const selectedEpisodePaths = new Set();

function isBatchSearchEligible(file) {
    return file?.media_type === 'tv'
        && file.season !== null && file.season !== undefined && String(file.season).trim() !== ''
        && file.episode !== null && file.episode !== undefined && String(file.episode).trim() !== ''
        && Number.isInteger(Number(file.season)) && Number.isInteger(Number(file.episode));
}

function updateBatchSelectionUI() {
    const toolbar = document.getElementById('files-batch-toolbar');
    const count = document.getElementById('files-selected-count');
    if (toolbar) toolbar.hidden = selectedEpisodePaths.size === 0;
    if (count) count.textContent = String(selectedEpisodePaths.size);
}

function toggleFileRowSelection(row) {
    const path = decodeURIComponent(row.dataset.filePath);
    if (selectedEpisodePaths.has(path)) selectedEpisodePaths.delete(path);
    else selectedEpisodePaths.add(path);
    const selected = selectedEpisodePaths.has(path);
    row.classList.toggle('file-row--selected', selected);
    row.setAttribute('aria-selected', String(selected));
    updateBatchSelectionUI();
}

function bindFileRowSelection(tbody) {
    if (tbody.dataset.selectionBound) return;
    tbody.dataset.selectionBound = 'true';
    tbody.addEventListener('click', event => {
        const row = event.target.closest('.file-row--selectable');
        if (!row || !tbody.contains(row) || event.target.closest('.actions-cell, button, a, input, select, textarea')) return;
        toggleFileRowSelection(row);
    });
    tbody.addEventListener('keydown', event => {
        const row = event.target.closest('.file-row--selectable');
        if (event.target !== row || (event.key !== 'Enter' && event.key !== ' ')) return;
        event.preventDefault();
        toggleFileRowSelection(row);
    });
}

function clearSelectedEpisodes() {
    selectedEpisodePaths.clear();
    renderTable();
}

function manualSearchSelected() {
    const files = [...selectedEpisodePaths]
        .map(path => allFiles.find(file => file.path === path))
        .filter(isBatchSearchEligible);
    if (!files.length) {
        clearSelectedEpisodes();
        return;
    }
    const normalizeTitle = title => String(title || '').normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
    const seriesTitles = new Set(files.map(file => normalizeTitle(file.title)));
    if (seriesTitles.size !== 1 || !files[0].title) {
        alert(tr('err_select_same_series'));
        return;
    }
    openManualSearchModal(files[0].title, { batchPaths: files.map(file => file.path) });
}

function renderTable() {
    selectedEpisodePaths.forEach(path => {
        if (!allFiles.some(file => file.path === path)) selectedEpisodePaths.delete(path);
    });
    const filtered = currentFilter ? allFiles.filter(f => f.media_type === currentFilter) : allFiles;
    const tbody = document.getElementById('files-tbody');
    bindFileRowSelection(tbody);
    let rows = filtered.map(file => buildFileRow(file));
    Object.values(activeTransfers).forEach(t => {
        if (!filtered.some(f => f.path === t.filePath)) {
            rows.push(buildFileRow({ path: t.filePath, filename: basename(t.filePath), media_type: currentFilter || 'movie' }));
        }
    });
    if (!rows.length) {
        tbody.innerHTML = `<div class="file-row" role="row"><div class="empty-state" role="gridcell">${tr('no_files_found')}</div></div>`;
        updateBatchSelectionUI();
        return;
    }
    tbody.innerHTML = rows.join('');
    updateBatchSelectionUI();
    if (tbody.animate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        tbody.animate([{ opacity: 0.4 }, { opacity: 1 }], { duration: 160, easing: 'ease-out' });
    }
}

function buildFileRow(file) {
    const transfer = getActiveTransfer(file.path);
    const dataPath = `data-file-path="${pathKey(file.path)}"`;
    if (transfer) {
        return `<div ${dataPath} class="file-row transferring" role="row" data-job-id="${transfer.jobId}">
            <div class="transfer-cell" role="gridcell">${renderTransferOverlay(file, transfer)}</div>
        </div>`;
    }
    const selectable = isBatchSearchEligible(file);
    const selected = selectable && selectedEpisodePaths.has(file.path);
    return `<div ${dataPath} class="file-row${selectable ? ' file-row--selectable' : ''}${selected ? ' file-row--selected' : ''}" role="row"${selectable ? ` tabindex="0" aria-selected="${selected}"` : ''}>
        <div class="file-name-cell" role="gridcell">${esc(file.filename)}</div>
        <div class="preview-cell preview-cell-td" role="gridcell">${renderPreview(file)}</div>
        <div class="actions-cell actions-cell-td" role="gridcell">${renderActions(file)}</div>
    </div>`;
}

function isTransferring(filePath) {
    return Object.values(activeTransfers).some(t => t.filePath === filePath);
}

function getActiveTransfer(filePath) {
    return Object.values(activeTransfers).find(t => t.filePath === filePath);
}

function renderTransferOverlay(file, transfer) {
    const p = transfer.progress || {};
    const percent = Math.max(0, Math.min(100, Number(p.percent) || 0));
    const phase = p.phase || 'copying';
    const label = phase === 'verifying' ? tr('transfer_verify') : (phase === 'done' ? tr('transfer_done') : tr('transfer_move'));
    const fileLabel = file.filename || basename(file.path);
    const stats = p.speed > 0
        ? `<span>${formatBytes(p.speed)}/s</span>${p.eta > 0 ? `<span>ETA ${formatSeconds(p.eta)}</span>` : ''}`
        : '';
    return `<div class="transfer-overlay">
        <div class="transfer-overlay-title">
            <i class="mdi mdi-file-move-outline ${phase === 'verifying' ? 'mdi-spin' : ''}"></i>
            <span class="transfer-overlay-label">${label}</span>
            <span class="transfer-overlay-file" title="${esc(fileLabel)}">${esc(fileLabel)}</span>
        </div>
        <div class="transfer-progress-line">
            <div class="progress-bar-container">
                <div class="progress-bar ${phase === 'copying' ? '' : 'progress-bar--pulse'}" style="width:${percent}%"></div>
                <div class="progress-text">${percent}%</div>
            </div>
            ${stats ? `<div class="progress-stats">${stats}</div>` : ''}
        </div>
    </div>`;
}

function filterFiles(type) {
    currentFilter = type;
    document.querySelectorAll('#files .filter-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.filter === type));
    renderTable();
}

function renderPreview(file) {
    const p = filesPreviews[file.path];
    if (!p) return `<div class="no-preview">${tr('btn_other')}</div>`;
    if (p.loading) return `<div class="loading-cell"><i class="mdi mdi-loading mdi-spin"></i> ${tr('searching')}</div>`;
    if (p.error) return `<div class="no-preview"><i class="mdi mdi-alert-circle-outline"></i> ${esc(p.error)}</div>`;
    if (!p.data) return `<div class="no-preview">${tr('no_result')}</div>`;
    const { source, details } = p.data;
    const notFound = details.not_found || source.not_found;
    // Film : le format par défaut n'a besoin que de titre/année (dispos OMDb).
    // Série : le titre d'épisode est inconnu sans TVDB → pas de renommage.
    const newName = (notFound && file.media_type !== 'movie') ? '' : generateFilename(file, details);
    const poster = source.poster ? `<img src="${esc(source.poster)}" alt="">` : `<i class="mdi ${file.media_type === 'movie' ? 'mdi-film' : 'mdi-television'}"></i>`;
    const isTv = file.media_type !== 'movie';
    const chips = previewChips(source, details, isTv, file.duration);
    const notFoundBadge = notFound
        ? `<div class="search-badge search-badge--notfound" title="${esc(tr('search_not_found'))}"><i class="mdi mdi-alert-circle-outline"></i> ${esc(tr('search_not_found'))}</div>`
        : '';
    const secondLine = notFound
        ? notFoundBadge
        : `<div class="rename-preview"><i class="mdi mdi-arrow-right"></i> ${esc(newName)}</div>`;
    return `<div class="preview-wrap">
        <div class="preview-poster">${poster}</div>
        <div class="preview-info">
            <div class="preview-line1">
                <span class="preview-title">${esc(details.title || '')}</span>${details.year ? ` <span class="preview-year">${esc(String(details.year))}</span>` : ''}
                ${chips ? `<span class="preview-chips">${chips}</span>` : ''}
            </div>
            ${secondLine}
        </div></div>`;
}

function previewChips(source, details, isTv, realDur) {
    const od = source.omdb || {};
    const eo = details.episode_omdb || {};
    const chips = [];
    if (od.imdbRating) chips.push(`<span class="chip chip-rating">★ ${esc(od.imdbRating)}</span>`);
    if (od.Rated && od.Rated !== 'N/A') chips.push(`<span class="chip chip-cert">${esc(od.Rated)}</span>`);
    if (isTv) {
        if (od.Genre && od.Genre !== 'N/A') chips.push(`<span class="chip chip-genre">${esc(od.Genre)}</span>`);
        if (eo.imdbRating && eo.imdbRating !== 'N/A') chips.push(`<span class="chip chip-eprating">ép. ★ ${esc(eo.imdbRating)}</span>`);
        const dur = realDur ? `${realDur} min` : (eo.Runtime && eo.Runtime !== 'N/A' ? eo.Runtime : '');
        if (dur) chips.push(`<span class="chip chip-epruntime">ép. ${esc(dur)}</span>`);
        if (eo.Released && eo.Released !== 'N/A') chips.push(`<span class="chip chip-epdate">ép. ${esc(eo.Released)}</span>`);
    } else {
        const dur = realDur ? `${realDur} min` : (od.Runtime && od.Runtime !== 'N/A' ? od.Runtime : '');
        if (dur) chips.push(`<span class="chip chip-runtime">${esc(dur)}</span>`);
        if (od.Genre && od.Genre !== 'N/A') chips.push(`<span class="chip chip-genre">${esc(od.Genre)}</span>`);
        if (od.Released && od.Released !== 'N/A') chips.push(`<span class="chip chip-date">${esc(od.Released)}</span>`);
    }
    return chips.join('');
}

function renderActions(file) {
    const p = filesPreviews[file.path];
    if (!p) return `<div class="btn-group"><button class="btn-small search" title="${tr('btn_other')}" onclick="manualSearch(decodeURIComponent('${pathKey(file.path)}'))"><i class="mdi mdi-magnify"></i></button></div>`;
    if (p.loading) return `<div class="btn-group"><button class="btn-small search" disabled><i class="mdi mdi-loading mdi-spin"></i></button></div>`;
    let html = '<div class="btn-group">';
    if (p.data) {
        const notFound = p.data.details.not_found || p.data.source.not_found;
        const canRename = !notFound || file.media_type === 'movie';
        const newName = generateFilename(file, p.data.details);
        if (canRename && newName === file.filename)
            html += `<span class="btn-small ok-label" title="${tr('btn_ok')}"><i class="mdi mdi-check"></i></span>`;
        else if (canRename && newName)
            html += `<button class="btn-small rename" title="${tr('btn_rename')}" onclick="doRename(decodeURIComponent('${pathKey(file.path)}'))"><i class="mdi mdi-pencil"></i></button>`;
        html += `<button class="btn-small move" title="${tr('btn_move')}" onclick="doMove(decodeURIComponent('${pathKey(file.path)}'))"><i class="mdi mdi-folder-move"></i></button>`;
    }
    html += `<button class="btn-small search" title="${tr('btn_other')}" onclick="manualSearch(decodeURIComponent('${pathKey(file.path)}'))"><i class="mdi mdi-magnify"></i></button>`;
    html += '</div>';
    return html;
}

function applyPathChange(filePath, newPath, newName) {
    const fileIdx = allFiles.findIndex(f => f.path === filePath);
    if (fileIdx === -1) return;
    
    allFiles[fileIdx] = { ...allFiles[fileIdx], filename: newName, path: newPath };
    if (selectedEpisodePaths.delete(filePath)) selectedEpisodePaths.add(newPath);
    filesPreviews[newPath] = filesPreviews[filePath];
    delete filesPreviews[filePath];
    
    const oldRow = findFileRow(filePath);
    if (oldRow) {
        oldRow.setAttribute('data-file-path', pathKey(newPath));
        oldRow.querySelector('.file-name-cell').textContent = newName;
        oldRow.querySelector('.actions-cell').innerHTML = renderActions(allFiles[fileIdx]);
    }
    updateBatchSelectionUI();
}

function updateFileRow(fileOrPath) {
    const filePath = typeof fileOrPath === 'string' ? fileOrPath : fileOrPath.path;
    const file = allFiles.find(f => f.path === filePath);
    if (!file) return;
    const row = findFileRow(filePath);
    if (!row) return;
    const previewCell = row.querySelector('.preview-cell-td');
    if (previewCell) previewCell.innerHTML = renderPreview(file);
    const actionsCell = row.querySelector('.actions-cell-td');
    if (actionsCell) actionsCell.innerHTML = renderActions(file);
}
