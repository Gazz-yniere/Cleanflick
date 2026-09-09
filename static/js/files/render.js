// ── Render ────────────────────────────────────────────────────────────────────
function renderTable() {
    const filtered = currentFilter ? allFiles.filter(f => f.media_type === currentFilter) : allFiles;
    const tbody = document.getElementById('files-tbody');
    let rows = filtered.map(file => buildFileRow(file));
    Object.values(activeTransfers).forEach(t => {
        if (!filtered.some(f => f.path === t.filePath)) {
            rows.push(buildFileRow({ path: t.filePath, filename: basename(t.filePath), media_type: currentFilter || 'movie' }));
        }
    });
    if (!rows.length) {
        tbody.innerHTML = `<div class="empty-state">${tr('no_files_found')}</div>`;
        return;
    }
    tbody.innerHTML = rows.join('');
}

function buildFileRow(file) {
    const transfer = getActiveTransfer(file.path);
    const dataPath = `data-file-path="${pathKey(file.path)}"`;
    if (transfer) {
        return `<div ${dataPath} class="file-row transferring" data-job-id="${transfer.jobId}">
            <div class="transfer-cell">${renderTransferOverlay(file, transfer)}</div>
        </div>`;
    }
    return `<div ${dataPath} class="file-row">
        <div class="file-name-cell">${esc(file.filename)}</div>
        <div class="preview-cell preview-cell-td">${renderPreview(file)}</div>
        <div class="actions-cell actions-cell-td">${renderActions(file)}</div>
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
    if (p.loading) return `<div class="loading-cell">◌ ${tr('searching')}</div>`;
    if (p.error) return `<div class="no-preview">❌ ${esc(p.error)}</div>`;
    if (!p.data) return `<div class="no-preview">${tr('no_result')}</div>`;
    const { source, details } = p.data;
    const newName = generateFilename(file, details);
    const poster = source.poster ? `<img src="${esc(source.poster)}" alt="">` : (file.media_type === 'movie' ? '🎬' : '📺');
    const isTv = file.media_type !== 'movie';
    const chips = previewChips(source, details, isTv, file.duration);
    return `<div class="preview-wrap">
        <div class="preview-poster">${poster}</div>
        <div class="preview-info">
            <div class="preview-line1">
                <span class="preview-title">${esc(details.title || '')}</span>${details.year ? ` <span class="preview-year">${esc(String(details.year))}</span>` : ''}
                ${chips ? `<span class="preview-chips">${chips}</span>` : ''}
            </div>
            <div class="rename-preview">➜ ${esc(newName)}</div>
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
        const newName = generateFilename(file, p.data.details);
        if (newName === file.filename)
            html += `<span class="btn-small ok-label" title="${tr('btn_ok')}"><i class="mdi mdi-check"></i></span>`;
        else
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
    filesPreviews[newPath] = filesPreviews[filePath];
    delete filesPreviews[filePath];
    
    const oldRow = findFileRow(filePath);
    if (oldRow) {
        oldRow.setAttribute('data-file-path', pathKey(newPath));
        oldRow.querySelector('.file-name-cell').textContent = newName;
        oldRow.querySelector('.actions-cell').innerHTML = renderActions(allFiles[fileIdx]);
    }
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
