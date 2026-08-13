'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let currentFilter = '';
let allFiles = [];
let filesPreviews = {};
let globalConfig = { movie_format: '{n} ({y})', tv_format: '{n} - {s00e00} - {t}' };
let activeTransfers = {};  // job_id -> { idx, pollInterval, startTime }
let scanEventSource = null;
let pendingMoveNameMismatch = null;
const PREVIEW_PARALLELISM = 4;
let previewQueue = [];
let activePreviewLoads = 0;

// ── i18n ──────────────────────────────────────────────────────────────────────
function tr(key) {
    if (typeof TRANSLATIONS !== 'undefined' && typeof currentLang !== 'undefined')
        return TRANSLATIONS[currentLang]?.[key] || TRANSLATIONS['fr']?.[key] || key;
    return key;
}

// ── Lang map (mirrors Python) ─────────────────────────────────────────────────
const LANG_MAP = {
    fr:'fra', de:'deu', es:'spa', it:'ita', pt:'por', ru:'rus',
    ja:'jpn', ko:'kor', zh:'zho', ar:'ara', pl:'pol', nl:'nld',
    sv:'swe', no:'nor', da:'dan', fi:'fin', tr:'tur', cs:'ces',
    hu:'hun', he:'heb', ro:'ron', uk:'ukr'
};

// ── Format ────────────────────────────────────────────────────────────────────
function cleanTitle(title) {
    if (!title) return '';
    return title.replace(/[\:-]/g, ' ').replace(/\s+/g, ' ').trim();
}

function interpolate(template, vars) {
    return template.replace(/\{([a-zA-Z_]\w*)(:[^}]*)?\}/g, (_, name, fmt) => {
        const val = vars[name];
        if (fmt) {
            const code = fmt.slice(1).toLowerCase();
            const trans = vars._translations || {};
            if (/^[a-zA-Z]{2,3}$/.test(code) && ['n','title','original_title','t','episode_title'].includes(name))
                return trans[code] || trans[LANG_MAP[code]] || val || '';
        }
        if (val === undefined || val === null || String(val).trim() === '' || String(val) === 'None') return '';
        const s = String(val).trim();
        if (fmt) {
            const f = fmt.slice(1);
            const pad = f.match(/^0(\d+)d$/);
            if (pad) return (parseInt(s) || 0).toString().padStart(parseInt(pad[1]), '0');
            if (/^\d*d$/.test(f)) return String(parseInt(s) || 0);
        }
        return s;
    });
}

function generateFilename(file, details) {
    if (!file?.filename) return '';
    const ext = file.filename.slice(file.filename.lastIndexOf('.'));
    const fmt = file.media_type === 'movie' ? globalConfig.movie_format : globalConfig.tv_format;
    const s = file.season || 1, e = file.episode || 1;
    const cleanedTitle = cleanTitle(details.title || '');
    const cleanedEpTitle = cleanTitle(details.episode_title || details.t || '');
    const vars = {
        n: cleanedTitle, title: cleanedTitle,
        ny: cleanedTitle && details.year ? `${cleanedTitle} (${details.year})` : cleanedTitle,
        y: details.year || '', year: details.year || '',
        d: details.airdate || details.release_date || '',
        airdate: details.airdate || '', release_date: details.release_date || '',
        t: cleanedEpTitle, episode_title: cleanedEpTitle,
        s, season: s, e, episode: e,
        s00e00: `S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`,
        sxe: `${s}x${String(e).padStart(2,'0')}`,
        absolute: details.absolute || '', sc: details.season_count || '',
        director: details.director || '', rating: details.rating || '',
        genres: details.genres || '', genre: details.genre || '',
        runtime: details.runtime || '', certification: details.certification || '',
        network: details.network || '', studio: details.studio || '',
        language: details.language || '', country: details.country || '',
        status: details.status || '',
        tvdbid: details.tvdbid || String(details.id || ''),
        imdbid: details.imdbid || details.imdb || '', imdb: details.imdbid || details.imdb || '',
        tmdbid: details.tmdbid || details.tmdb || '', tmdb: details.tmdbid || details.tmdb || '',
        _translations: details.translations || {},
    };
    const result = interpolate(fmt, vars)
        .replace(/:/g, ' -').replace(/[<>"\/\\|?*]/g, '')
        .replace(/ -\s*-/g, ' -').replace(/\s+/g, ' ').replace(/[\s\-\.]+$/, '').trim();
    return result ? result + ext : file.filename;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function pathKey(path) { return encodeURIComponent(path); }
function pathFromKey(key) { return decodeURIComponent(key); }
function findFileRow(filePath) { return document.querySelector(`tr[data-file-path="${pathKey(filePath)}"]`); }
function getVal(id) { return document.getElementById(id)?.value || ''; }
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v; }

function postJSON(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
}

// ── Navigation ────────────────────────────────────────────────────────────────
function switchTab(tab, e) {
    if (e) e.preventDefault();
    if (Object.keys(pendingConfigChanges).length > 0) {
        const cfg = { ...pendingConfigChanges };
        pendingConfigChanges = {};
        if (configSaveTimer) clearTimeout(configSaveTimer);
        saveConfig(cfg);
    }
    document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
    document.getElementById(tab)?.classList.add('active');
    if (e?.target) e.target.closest('.nav-link').classList.add('active');
    if (tab === 'config') loadConfig();
    if (tab === 'history') loadHistory();
}

// ── Scan ──────────────────────────────────────────────────────────────────────
function scanFiles() {
    const tbody = document.getElementById('files-tbody');
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:20px;color:#e67e22;">◌ ${tr('scanning')}</td></tr>`;
    allFiles = [];
    fetch('/api/scan')
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
            const newPaths = new Set((data || []).map(f => f.path));

            // Supprimer les aperçus pour les fichiers qui ont disparu
            Object.keys(filesPreviews).forEach(p => { if (!newPaths.has(p)) delete filesPreviews[p]; });

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
            details.imdbid = details.imdbid || top.imdb_id || '';
            details.imdb = details.imdbid;
            details.tmdbid = details.tmdbid || top.tmdb_id || '';
            details.tmdb = details.tmdbid;
            if (!details.translations || !Object.keys(details.translations).length)
                details.translations = top.translations || {};

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

// Deprecated: use loadPreviewForFile instead
function loadPreviewsAsync(files) {
    if (!files) return;
    files.forEach(file => loadPreviewForFile(file));
}

// ── Render ────────────────────────────────────────────────────────────────────
function renderTable() {
    const filtered = currentFilter ? allFiles.filter(f => f.media_type === currentFilter) : allFiles;
    const tbody = document.getElementById('files-tbody');
    if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${tr('no_files_found')}</td></tr>`;
        return;
    }
    tbody.innerHTML = filtered.map(file => `
        <tr data-file-path="${pathKey(file.path)}">
            <td class="file-name-cell">${esc(file.filename)}</td>
            <td class="preview-cell preview-cell-td">${renderPreview(file)}</td>
            <td class="progress-info-td">${renderProgressCell(file)}</td>
            <td class="actions-cell actions-cell-td">${renderActions(file)}</td>
        </tr>`).join('');
}

function filterFiles(type) {
    currentFilter = type;
    document.querySelectorAll('.filter-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.filter === type));
    renderTable();
}

function renderPreview(file) {
    const p = filesPreviews[file.path];
    if (!p || p.loading) return `<div class="loading-cell">◌ ${tr('searching')}</div>`;
    if (p.error) return `<div class="no-preview">❌ ${esc(p.error)}</div>`;
    if (!p.data) return `<div class="no-preview">${tr('no_result')}</div>`;
    const { source, details } = p.data;
    const newName = generateFilename(file, details);
    const poster = source.poster ? `<img src="${esc(source.poster)}" alt="">` : (file.media_type === 'movie' ? '🎬' : '📺');
    let meta = '';
    if (details.year) meta += `<span>📅 ${esc(String(details.year))}</span>`;
    if (file.media_type === 'tv' && details.episode_title) meta += `<span>🎞️ ${esc(details.episode_title)}</span>`;
    return `<div class="preview-wrap">
        <div class="preview-poster">${poster}</div>
        <div class="preview-info">
            <div class="preview-title">${esc(details.title || '')}${details.year ? ` (${details.year})` : ''}</div>
            <div class="rename-preview">➜ ${esc(newName)}</div>
            <div class="preview-meta">${meta}</div>
        </div></div>`;
}

function renderProgressCell(file) {
    const p = filesPreviews[file.path];
    if (!p || p.loading || !p.data) return '';
    return `<div class="progress-cell"></div>`;
}

function renderActions(file) {
    const p = filesPreviews[file.path];
    if (!p || p.loading) return `<div class="btn-group"><button class="btn-small search" disabled><i class="mdi mdi-loading mdi-spin"></i></button></div>`;
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
    const progressCell = row.querySelector('.progress-info-td');
    if (progressCell) progressCell.innerHTML = renderProgressCell(file);
}

// ── History ───────────────────────────────────────────────────────────────────
function loadHistory() {
    const tbody = document.getElementById('history-tbody');
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:20px;color:#e67e22;">◌ ${tr('searching')}</td></tr>`;
    fetch('/api/history').then(r => r.json()).then(entries => {
        window._historyEntries = entries;
        if (!entries.length) {
            tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${tr('hist_empty')}</td></tr>`;
            return;
        }
        tbody.innerHTML = entries.map((e, i) => {
            const opLabel = { rename: tr('hist_op_rename'), move: tr('hist_op_move'), revert: tr('hist_op_revert') }[e.op] || e.op;
            const opClass = { rename: 'op-rename', move: 'op-move', revert: 'op-revert' }[e.op] || 'op-rename';

            let actionHtml = '';
            const rs = e.revert_status;
            if (rs === 'available') {
                actionHtml = `<button class="btn-small revert" title="${tr('btn_revert')}" onclick="revertEntry(${i})"><i class="mdi mdi-undo"></i></button>`;
            } else if (rs === 'reverted') {
                actionHtml = `<span class="hist-status hist-status--reverted"><i class="mdi mdi-undo"></i> ${tr('hist_op_revert')}</span>`;
            } else if (rs === 'missing') {
                actionHtml = `<span class="hist-status hist-status--missing"><i class="mdi mdi-alert-circle"></i> ${tr('hist_file_missing')}</span>`;
            } else if (rs === 'conflict') {
                actionHtml = `<span class="hist-status hist-status--conflict"><i class="mdi mdi-alert"></i></span>`;
            }

            const statusLine = `<div class="hist-name-section">
                <div class="hist-name-from">${esc(e.from_name)}</div>
                <div class="hist-arrow">→</div>
                <div class="hist-name-to">${esc(e.to_name)}</div>
            </div>`;

            return `<tr data-hist-idx="${i}">
                <td class="hist-date">${esc(e.date)}</td>
                <td><span class="hist-op ${opClass}">${opLabel}</span></td>
                <td class="hist-combined">${statusLine}</td>
                <td class="hist-actions"><div id="hist-prog-${i}">${actionHtml}</div></td>
            </tr>`;
        }).join('');
    }).catch(e => { tbody.innerHTML = `<tr><td colspan="4" class="empty-state">Erreur: ${esc(e.message)}</td></tr>`; });
}

async function revertEntry(i) {
    const entry = window._historyEntries?.[i];
    if (!entry) return;
    const progEl = document.getElementById(`hist-prog-${i}`);
    if (progEl) progEl.innerHTML = `<span style="color:#e67e22">◌ ${tr('searching')}</span>`;
    try {
        const data = await postJSON('/api/revert', { id: entry.id });
        if (!data.success) throw new Error(data.message);
        if (progEl) progEl.innerHTML = `<span style="color:#27ae60">✓ ${tr('hist_reverted')}</span>`;
        // Toujours rescanner après un revert pour mettre à jour le tableau des fichiers
        setTimeout(() => {
            loadHistory();
            scanFiles();
        }, 600);
    } catch(e) {
        if (progEl) progEl.innerHTML = `<span style="color:#e74c3c">✗ ${esc(e.message)}</span>`;
    }
}

function clearHistory() {
    document.getElementById('confirmClearModal').classList.add('active');
}

function confirmClearHistory() {
    closeModal('confirmClearModal');
    fetch('/api/history/clear', { method: 'POST' })
        .then(() => loadHistory())
        .catch(e => alert(`Erreur: ${e.message}`));
}

// ── Rename / Move ─────────────────────────────────────────────────────────────
function doRename(filePath) {
    const file = allFiles.find(f => f.path === filePath);
    if (!file || !file.filename) return;
    const p = filesPreviews[file.path];
    if (!p?.data) return;
    const newName = generateFilename(file, p.data.details);
    if (newName === file.filename) return;
    postJSON('/api/rename', { path: file.path, new_name: newName })
        .then(data => {
            if (!data.success) { alert(`✗ ${tr('err_rename')}\n${data.message}`); return; }
            applyPathChange(file.path, data.new_path, newName);
        })
        .catch(e => alert(`✗ ${tr('err_rename')}\n${e.message}`));
}

function openMoveNameMismatchModal(file, proposedName) {
    const modal = document.getElementById('confirmMoveNameMismatchModal');
    if (!modal) return false;
    const actualName = file?.filename || '';
    document.getElementById('moveCurrentFilename').textContent = actualName;
    document.getElementById('moveProposedFilename').textContent = proposedName || actualName;
    pendingMoveNameMismatch = { filePath: file.path, file, proposedName };
    modal.classList.add('active');
    return true;
}

function confirmMoveNameMismatch() {
    if (!pendingMoveNameMismatch) return;
    closeModal('confirmMoveNameMismatchModal');
    const { filePath, file, proposedName } = pendingMoveNameMismatch;
    pendingMoveNameMismatch = null;
    runMove(filePath, file, proposedName);
}

async function doMove(filePath) {
    const file = allFiles.find(f => f.path === filePath);
    if (!file?.filename) return;
    const p = filesPreviews[file.path];
    if (!p?.data) return;

    const proposedName = generateFilename(file, p.data.details);
    if (proposedName && file.filename && proposedName.trim().toLowerCase() !== file.filename.trim().toLowerCase()) {
        openMoveNameMismatchModal(file, proposedName);
        return;
    }

    await runMove(filePath, file, proposedName || file.filename);
}

async function runMove(filePath, file, proposedName) {
    try {
        const data = await postJSON('/api/move', {
            path: file.path,
            media_type: file.media_type || 'movie'
        });
        if (!data.success) throw new Error(data.message);
        const jobId = data.job_id;
        activeTransfers[jobId] = { filePath, pollInterval: null, startTime: Date.now() };
        markFileAsTransferring(filePath, jobId);
        trackMoveProgress(jobId, filePath, file, data.new_name || proposedName || file.filename);
    } catch (e) {
        alert(`✗ ${tr('err_move')}\n${e.message}`);
    }
}

function markFileAsTransferring(filePath, jobId) {
    const row = findFileRow(filePath);
    if (row) {
        row.classList.add('transferring');
        row.setAttribute('data-job-id', jobId);
    }
}

function unmarkFileAsTransferring(filePath) {
    const row = findFileRow(filePath);
    if (row) {
        row.classList.remove('transferring');
        row.removeAttribute('data-job-id');
    }
}

function trackMoveProgress(jobId, filePath, file, newName) {
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/move-progress/${jobId}`);
            const prog = await response.json();
            updateProgressDisplay(filePath, jobId, prog);

            if (prog.finished) {
                clearInterval(pollInterval);
                delete activeTransfers[jobId];
                unmarkFileAsTransferring(filePath);

                if (prog.error) {
                    const row = findFileRow(filePath);
                    const progressCell = row?.querySelector('.progress-cell');
                    if (progressCell) progressCell.innerHTML = `<div class="progress-done progress-error-state"><i class="mdi mdi-alert-circle"></i> ${esc(prog.error)}</div>`;
                    return;
                }

                const row = findFileRow(filePath);
                const progressCell = row?.querySelector('.progress-cell');
                if (progressCell) {
                    progressCell.innerHTML = `<div class="progress-done"><i class="mdi mdi-check-circle"></i> Déplacé</div>`;
                }
                setTimeout(() => removeFileFromList(filePath), 800);
            }
        } catch (e) {
            console.error('Progress poll error:', e);
        }
    }, 500);

    if (activeTransfers[jobId]) {
        activeTransfers[jobId].pollInterval = pollInterval;
    }
}

function removeFileFromList(filePath) {
    // Remove from allFiles array
    const idx = allFiles.findIndex(f => f.path === filePath);
    if (idx >= 0) {
        allFiles.splice(idx, 1);
    }
    
    // Remove row from DOM with animation
    const row = findFileRow(filePath);
    if (row) {
        row.style.opacity = '0';
        row.style.transition = 'opacity 0.3s ease';
        setTimeout(() => row.remove(), 300);
    }
}

function updateProgressDisplay(filePath, jobId, prog) {
    const row = findFileRow(filePath);
    if (!row) return;
    const progressCell = row.querySelector('.progress-cell');
    if (!progressCell) return;

    if (prog.finished && prog.error) {
        progressCell.innerHTML = `<div class="progress-done progress-error-state"><i class="mdi mdi-alert-circle"></i> Erreur</div>`;
        return;
    }
    if (prog.finished) {
        progressCell.innerHTML = `<div class="progress-done"><i class="mdi mdi-check-circle"></i> Déplacé</div>`;
        return;
    }

    const phase = prog.phase || 'copying';
    const percent = Math.max(0, Math.min(100, Number(prog.percent) || 0));

    const statsHtml = prog.speed > 0
        ? `<span>${formatBytes(prog.speed)}/s</span>${prog.eta > 0 ? `<span>ETA ${formatSeconds(prog.eta)}</span>` : ''}`
        : '';

    progressCell.innerHTML = `
        <div class="progress-bar-container">
            <div class="progress-bar ${phase === 'copying' ? '' : 'progress-bar--pulse'}" style="width:${percent}%"></div>
            <div class="progress-text">${percent}%</div>
        </div>
        ${statsHtml ? `<div class="progress-stats">${statsHtml}</div>` : ''}`;
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

function formatSeconds(seconds) {
    if (seconds < 60) return Math.round(seconds) + 's';
    if (seconds < 3600) return Math.round(seconds / 60) + 'm';
    return Math.round(seconds / 3600) + 'h';
}

function renameAll() {
    const toRename = allFiles.filter(f => filesPreviews[f.path]?.data);
    if (!toRename.length) { alert(tr('err_no_files')); return; }
    toRename.forEach(file => doRename(file.path));
}

function moveAll() {
    const toMove = allFiles.filter(f => filesPreviews[f.path]?.data);
    if (!toMove.length) { alert(tr('err_no_files')); return; }
    toMove.forEach(file => doMove(file.path));
}

// ── Manual Search ─────────────────────────────────────────────────────────────
function manualSearch(filePath) {
    const file = allFiles.find(f => f.path === filePath);
    if (!file) return;
    const title = file.filename
        .replace(/\.[^.]+$/, '').replace(/\s*\[[^\]]*\]/g, '')
        .replace(/\s*\([^)]{8,}\)/g, '').replace(/\s*\(\d{4}\)/g, '')
        .replace(/[._]/g, ' ').replace(/\s*[-]\s*[Ss]\d+[Ee]\d+.*/i, '')
        .replace(/\s*[Ss]\d+[Ee]\d+.*/i, '').replace(/\s*(19|20)\d{2}.*/i, '')
        .replace(/\s+/g, ' ').trim();
    window._manualSearchFilePath = filePath;
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

function executeManualSearch() {
    const filePath = window._manualSearchFilePath;
    const file = allFiles.find(f => f.path === filePath);
    if (!file) return;
    const title = document.getElementById('search-title').value.trim();
    if (!title) return;
    const resultsDiv = document.getElementById('manual-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div>${tr('searching')}</div>`;
    const endpoint = file.media_type === 'movie' ? '/api/search/movie' : '/api/search/tv';
    postJSON(endpoint, { title, path: filePath })
    .then(results => {
        // Accept either the old array response or the new envelope { results: [], cache_source: '...' }
        let list = results;
        if (results && results.results) list = results.results;
        if (!list?.length) { resultsDiv.innerHTML = `<div class="message error">${tr('search_none')}</div>`; return; }
        window._searchResults = list;
        let html = `<p style="color:#888;font-size:0.82em;margin-bottom:10px;">${list.length} ${tr('search_results')}</p><div class="search-results">`;
        list.forEach((r, i) => {
            const poster = r.poster ? `<img src="${esc(r.poster)}" alt="">` : (file.media_type === 'movie' ? '🎬' : '📺');
            html += `<div class="result-item" data-ridx="${i}" onclick="selectResult(this)">
                <div class="result-poster">${poster}</div>
                <div class="result-title">${esc(r.title || '')}</div>
                <div class="result-year">${r.year || 'N/A'}</div>
                <div class="result-type">TVDB #${r.id}</div></div>`;
        });
        resultsDiv.innerHTML = html + '</div>';
    })
    .catch(e => { resultsDiv.innerHTML = `<div class="message error">${tr('err_scan')} ${esc(e.message)}</div>`; });
}

function selectResult(el) {
    const result = window._searchResults[parseInt(el.getAttribute('data-ridx'))];
    const filePath = window._manualSearchFilePath;
    const file = allFiles.find(f => f.path === filePath);
    const resultsDiv = document.getElementById('manual-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    const url = file.media_type === 'movie'
        ? `/api/movie/${result.id}?source=tvdb`
        : `/api/tv/${result.id}?season=${file.season || 1}&episode=${file.episode || 1}&source=tvdb`;
    fetch(url).then(r => r.json()).then(details => {
        details.imdbid = details.imdbid || result.imdb_id || '';
        details.imdb   = details.imdbid;
        details.tmdbid = details.tmdbid || result.tmdb_id || '';
        details.tmdb   = details.tmdbid;
        if (!details.translations || !Object.keys(details.translations).length)
            details.translations = result.translations || {};
        filesPreviews[file.path] = { loading: false, data: { source: result, details }, error: null };
        // Persist the manual selection to server-side file cache so refresh won't overwrite it
        try {
            postJSON('/api/search/cache-file', { path: file.path, media_type: file.media_type || 'movie', results: [ { id: result.id, imdb_id: details.imdbid, title: details.title || result.title, year: details.year || result.year, poster: result.poster, translations: details.translations || {}, details: details } ] })
            .catch(() => {});
        } catch (e) { /* ignore */ }
        updateFileRow(file);
        closeModal('manualSearchModal');
    }).catch(e => { resultsDiv.innerHTML = `<div class="message error">Erreur: ${esc(e.message)}</div>`; });
}

// ── File Picker ───────────────────────────────────────────────────────────────
let _pickerTarget = null;
let _pickerCurrentPath = '';

function pickFolder(inputId) {
    _pickerTarget = inputId;
    browseFolder(document.getElementById(inputId)?.value || null);
    document.getElementById('folderPickerModal').classList.add('active');
}

function browseFolder(path) {
    _pickerCurrentPath = path || '';
    const content = document.getElementById('folderPickerContent');
    content.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    const url = path ? `/api/browse?path=${encodeURIComponent(path)}` : '/api/browse';
    fetch(url).then(r => r.json()).then(data => {
        if (data.error && !data.path) { content.innerHTML = `<div class="message error">${esc(data.error)}</div>`; return; }
        _pickerCurrentPath = data.path || '';
        const sep = (_pickerCurrentPath || '').includes('\\') ? '\\' : '/';
        let html = `<div class="picker-path">${esc(data.path || tr('picker_root'))}</div><div class="picker-list">`;
        if (data.parent !== null && data.parent !== undefined)
            html += `<div class="picker-item up" data-path="${esc(data.parent)}" onclick="browseFolder(this.dataset.path)">📁 ..</div>`;
        (data.roots || []).forEach(root =>
            html += `<div class="picker-item" data-path="${esc(root)}" onclick="browseFolder(this.dataset.path)">💾 ${esc(root)}</div>`);
        (data.dirs || []).forEach(d => {
            const full = _pickerCurrentPath.replace(/[\/\\]+$/, '') + sep + d;
            html += `<div class="picker-item" data-path="${esc(full)}" onclick="browseFolder(this.dataset.path)">📁 ${esc(d)}</div>`;
        });
        if (!data.roots?.length && !data.dirs?.length)
            html += `<div style="padding:12px;color:#666;font-size:0.85em;">${tr('picker_empty')}</div>`;
        html += `</div><div class="picker-actions">
            <button class="btn btn-primary" id="picker-select-btn">${tr('picker_select')}</button>
            <button class="btn btn-secondary" onclick="closeModal('folderPickerModal')">${tr('picker_cancel')}</button>
        </div>`;
        content.innerHTML = html;
        document.getElementById('picker-select-btn').onclick = () => {
            if (_pickerTarget) {
                document.getElementById(_pickerTarget).value = _pickerCurrentPath;
                scheduleSaveConfigField(_pickerTarget, _pickerCurrentPath);
            }
            closeModal('folderPickerModal');
        };
    }).catch(e => { content.innerHTML = `<div class="message error">Erreur: ${esc(e.message)}</div>`; });
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function closeModal(id) { document.getElementById(id).classList.remove('active'); }
document.addEventListener('click', e => { if (e.target.classList.contains('modal')) e.target.classList.remove('active'); });

// ── Config ────────────────────────────────────────────────────────────────────
let configSaveTimer = null;
let pendingConfigChanges = {};

function scheduleSaveConfigField(key, value) {
    pendingConfigChanges[key] = value;
    if (configSaveTimer) clearTimeout(configSaveTimer);
    configSaveTimer = setTimeout(() => {
        const cfg = { ...pendingConfigChanges };
        pendingConfigChanges = {};
        saveConfig(cfg);
    }, 500);
}

function saveConfig(cfg) {
    postJSON('/api/config', cfg).then(data => {
        const msg = document.getElementById('config-message');
        if (!msg) return;
        if (data.success) {
            if ('movie_format' in cfg) { localStorage.setItem('cleanflick_movie_format', cfg.movie_format || ''); globalConfig.movie_format = cfg.movie_format; }
            if ('tv_format' in cfg)    { localStorage.setItem('cleanflick_tv_format', cfg.tv_format || '');       globalConfig.tv_format = cfg.tv_format; }
            msg.innerHTML = `<div class="message success">✓ Configuration enregistrée</div>`;
            setTimeout(() => { if (msg) msg.innerHTML = ''; }, 2000);
        } else {
            msg.innerHTML = `<div class="message error">✗ ${esc(data.message)}</div>`;
        }
    }).catch(e => {
        const msg = document.getElementById('config-message');
        if (msg) msg.innerHTML = `<div class="message error">✗ Erreur: ${esc(e.message)}</div>`;
    });
}

function loadConfig() {
    fetch('/api/config')
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } return r.json(); })
        .then(data => {
            ['tvdb_api_key','movie_format','tv_format','input_path','movie_output_path','tv_output_path'].forEach(k => setVal(k, data[k] || ''));
            const mf = localStorage.getItem('cleanflick_movie_format');
            const tf = localStorage.getItem('cleanflick_tv_format');
            if (mf) setVal('movie_format', mf);
            if (tf) setVal('tv_format', tf);
            globalConfig.movie_format = getVal('movie_format') || globalConfig.movie_format;
            globalConfig.tv_format    = getVal('tv_format')    || globalConfig.tv_format;
        });
}

function initConfigAutoSave() {
    [
        { id: 'tvdb_api_key',       key: 'tvdb_api_key' },
        { id: 'input_path',         key: 'input_path' },
        { id: 'movie_output_path',  key: 'movie_output_path' },
        { id: 'tv_output_path',     key: 'tv_output_path' },
        { id: 'movie_format',       key: 'movie_format' },
        { id: 'tv_format',          key: 'tv_format' },
    ].forEach(({ id, key }) => {
        document.getElementById(id)?.addEventListener('input', e => {
            const value = e.target.value.trim();
            if (key === 'movie_format') { localStorage.setItem('cleanflick_movie_format', value); globalConfig.movie_format = value; }
            if (key === 'tv_format')    { localStorage.setItem('cleanflick_tv_format', value);    globalConfig.tv_format = value; }
            scheduleSaveConfigField(key, value);
        });
    });
}

function testKeys() {
    const btn = document.getElementById('tvdb_test_btn');
    if (btn) { btn.classList.remove('valid', 'invalid'); btn.disabled = true; }
    postJSON('/api/test-keys', { tvdb_api_key: getVal('tvdb_api_key') })
        .then(data => {
            if (btn) { btn.classList.toggle('valid', !!data.tvdb?.valid); btn.classList.toggle('invalid', !data.tvdb?.valid); btn.disabled = false; }
        })
        .catch(() => { if (btn) { btn.classList.add('invalid'); btn.disabled = false; } });
}

// ── Init ──────────────────────────────────────────────────────────────────────
// No periodic auto-scan is enabled. A new file discovery on disk is not observable
// from the browser runtime itself, so the page is initialized with a one-shot scan only.
let autoScanInterval = null;
const AUTO_SCAN_INTERVAL = 0;
let lastScannedFileCount = 0;
let lastScannedPaths = new Set();

function startAutoScan() {
    // Keep it inert: only an explicit scan/reset should refresh the list.
    if (autoScanInterval) {
        clearInterval(autoScanInterval);
    }
    autoScanInterval = null;
}

function addNewFilesToTable(newFiles) {
    renderTable();
}

function stopAutoScan() {
    if (autoScanInterval) {
        clearInterval(autoScanInterval);
        autoScanInterval = null;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (typeof applyTranslations === 'function') applyTranslations();
    initConfigAutoSave();

    if (window.EventSource) {
        try {
            scanEventSource = new EventSource('/api/scan/events');
            scanEventSource.addEventListener('message', (event) => {
                try {
                    const data = JSON.parse(event.data || '{}');
                    if (data.event === 'scan-refresh') {
                        scanFiles();
                    }
                } catch (e) {
                    console.warn('Bad scan refresh payload', e);
                }
            });
            scanEventSource.onerror = () => {
                console.warn('Scan refresh stream disconnected');
            };
        } catch (e) {
            console.warn('Scan refresh SSE unavailable', e);
        }
    }

    fetch('/api/config')
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } return r.json(); })
        .then(cfg => {
            if (cfg.movie_format) globalConfig.movie_format = cfg.movie_format;
            if (cfg.tv_format)    globalConfig.tv_format    = cfg.tv_format;
            // Initial scan: populate the table but preserve previews if present
            scanFiles();
        })
        .catch(e => { if (!e.message.includes('401')) {
            scanFiles();
        } });
});
