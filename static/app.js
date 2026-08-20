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
function findFileRow(filePath) { return document.querySelector(`.file-row[data-file-path="${pathKey(filePath)}"]`); }
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
    try { localStorage.setItem(LIB_TAB_KEY, tab); } catch (err) {}
    if (tab === 'files') scanFiles();
    if (tab === 'config') loadConfig();
    if (tab === 'history') loadHistory();
    if (tab === 'library') refreshLibrary();
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

function mergeDetails(details, result) {
    details.imdbid = details.imdbid || result.imdb_id || '';
    details.imdb = details.imdbid;
    details.tmdbid = details.tmdbid || result.tmdb_id || '';
    details.tmdb = details.tmdbid;
    if (!details.translations || !Object.keys(details.translations).length)
        details.translations = result.translations || {};
    return details;
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

function basename(p) {
    const parts = String(p || '').split(/[\\/]/);
    return parts[parts.length - 1] || '';
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
    if (!p || p.loading) return `<div class="loading-cell">◌ ${tr('searching')}</div>`;
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
}

// ── Library ───────────────────────────────────────────────────────────────────
let libSortKey = 'name';
let libSortDir = 'asc';
let libFilter = 'all';
const LIB_EXPANDED_KEY = 'cleanflick_lib_expanded';
const LIB_TAB_KEY = 'cleanflick_active_tab';
function loadExpandedPaths() {
    try { return new Set(JSON.parse(localStorage.getItem(LIB_EXPANDED_KEY) || '[]')); } catch (e) { return new Set(); }
}
function saveExpandedPaths(set) {
    try { localStorage.setItem(LIB_EXPANDED_KEY, JSON.stringify([...set])); } catch (e) {}
}
let expandedPaths = loadExpandedPaths();

function metaText(meta) {
    if (!meta) return '';
    const parts = [];
    if (meta.title) parts.push(meta.title);
    if (meta.episode) parts.push(meta.episode + (meta.episode_name ? ' · ' + meta.episode_name : ''));
    const date = meta.date || '';
    if (date.length > 4) parts.push(date.slice(0, 10));
    else if (meta.year) parts.push(String(meta.year));
    if (meta.genres) parts.push(meta.genres);
    if (meta.rating != null && meta.rating !== '') parts.push('★ ' + meta.rating);
    if (meta.certification) parts.push(meta.certification);
    if (meta.runtime) parts.push(meta.runtime);
    return parts.join(' · ');
}

function metaChips(meta) {
    if (!meta) return '';
    const isEp = !!meta.episode;
    const chips = [];
    if (isEp) chips.push(`<span class="chip chip-ep">${esc(meta.episode)}${meta.episode_name ? ' ' + esc(meta.episode_name) : ''}</span>`);
    if (!isEp && meta.rating != null && meta.rating !== '') chips.push(`<span class="chip chip-rating">★ ${esc(meta.rating)}</span>`);
    if (meta.episode_rating != null && meta.episode_rating !== '') chips.push(`<span class="chip chip-rating">★ ${esc(meta.episode_rating)}</span>`);
    if (isEp) {
        if (meta.episode_date) chips.push(`<span class="chip chip-epdate">ép. ${esc(meta.episode_date.slice(0, 10))}</span>`);
        if (meta.episode_runtime) chips.push(`<span class="chip chip-runtime">${esc(meta.episode_runtime)}</span>`);
    } else {
        const date = meta.date || '';
        if (date.length > 4) chips.push(`<span class="chip chip-date">${esc(date.slice(0, 10))}</span>`);
        else if (meta.year) chips.push(`<span class="chip chip-date">${esc(String(meta.year))}</span>`);
        if (meta.runtime) chips.push(`<span class="chip chip-runtime">${esc(meta.runtime)}</span>`);
    }
    if (meta.certification) chips.push(`<span class="chip chip-cert">${esc(meta.certification)}</span>`);
    if (meta.genres) chips.push(`<span class="chip chip-genre">${esc(meta.genres)}</span>`);
    return chips.join('');
}

function metaHtml(meta) {
    if (!meta) return '';
    const chips = metaChips(meta);
    return chips ? `<span class="lib-meta">${chips}</span>` : '';
}

function libLead(entry, iconClass) {
    if (entry.meta && entry.meta.poster) {
        return `<img class="lib-poster lib-lead" src="${esc(entry.meta.poster)}" alt="" loading="lazy" onerror="this.style.display='none'">`;
    }
    return `<i class="mdi ${iconClass} lib-lead"></i>`;
}

function sortRating(entry) {
    const m = entry.meta || {};
    const v = m.episode ? m.episode_rating : m.rating;
    const n = parseFloat(v);
    return isNaN(n) ? -1 : n;
}

function libSortInfo(entry) {
    return {
        is_dir: !!entry.is_dir,
        name: entry.name,
        size: entry.size ?? 0,
        is_ep: !!(entry.meta && entry.meta.episode),
        rating: sortRating(entry),
    };
}

function libSortCmp(a, b) {
    let c;
    if (libSortKey === 'size') {
        c = (a.size ?? 0) - (b.size ?? 0);
    } else if (libSortKey === 'rating') {
        if (a.is_ep || b.is_ep) {
            c = a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
        } else {
            c = (a.rating ?? -1) - (b.rating ?? -1);
        }
    } else {
        c = a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    }
    return libSortDir === 'desc' ? -c : c;
}

function sortEntries(entries) {
    const dirs = entries.filter(e => e.is_dir).map(e => ({ e, i: libSortInfo(e) })).sort((x, y) => libSortCmp(x.i, y.i)).map(x => x.e);
    const files = entries.filter(e => !e.is_dir).map(e => ({ e, i: libSortInfo(e) })).sort((x, y) => libSortCmp(x.i, y.i)).map(x => x.e);
    return dirs.concat(files);
}

function setLibSort(key) {
    if (libSortKey === key) {
        libSortDir = libSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        libSortKey = key;
        libSortDir = 'asc';
    }
    updateLibSortButtons();
    reSortLib();
}

function updateLibSortButtons() {
    const keys = ['name', 'size', 'rating'];
    keys.forEach(k => {
        const btn = document.getElementById(`lib-sort-${k}`);
        if (!btn) return;
        btn.classList.toggle('active', libSortKey === k);
        const icon = btn.querySelector('i');
        if (!icon) return;
        if (libSortKey === k) {
            icon.className = libSortDir === 'asc' ? 'mdi mdi-sort-ascending' : 'mdi mdi-sort-descending';
        } else {
            icon.className = 'mdi mdi-sort';
        }
    });
}

function reSortLib() {
    const container = document.getElementById('library-tree');
    if (container) sortNodeChildren(container);
}

function sortNodeChildren(container) {
    const nodes = [...container.children];
    const dirs = [];
    const files = [];
    nodes.forEach(n => {
        if (n._sort && n._sort.is_dir) dirs.push(n);
        else if (n._sort && !n._sort.is_dir) files.push(n);
    });
    dirs.sort((a, b) => libSortCmp(a._sort, b._sort));
    files.sort((a, b) => libSortCmp(a._sort, b._sort));
    dirs.forEach(d => container.appendChild(d));
    files.forEach(f => container.appendChild(f));
    nodes.forEach(n => {
        const children = n.querySelector(':scope > .lib-children');
        if (children) sortNodeChildren(children);
    });
}

function loadLibrary() {
    const container = document.getElementById('library-tree');
    container.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    return fetch('/api/library')
        .then(r => r.json())
        .then(data => {
            if (!data.entries?.length) {
                container.innerHTML = `<div class="empty-state">${tr('lib_empty')}</div>`;
                return;
            }
            container.innerHTML = '';
            sortEntries(data.entries).forEach(root => {
                container.appendChild(buildLibNode(root, 0, false));
            });
            applyLibFilter();
            expandStoredPathsDeep(container);
        })
        .catch(e => { container.innerHTML = `<div class="message error">${esc(e.message)}</div>`; });
}

function refreshLibrary() {
    const container = document.getElementById('library-tree');
    const domExpanded = getExpandedPaths(container);
    if (domExpanded.length) {
        expandedPaths = new Set(domExpanded);
        saveExpandedPaths(expandedPaths);
    }
    loadLibrary();
}

function getExpandedPaths(container) {
    const paths = [];
    container.querySelectorAll('.lib-row--dir').forEach(row => {
        const children = row.parentElement.querySelector(':scope > .lib-children');
        if (children && children.style.display !== 'none') paths.push(row.dataset.path);
    });
    return paths;
}

function findDirRow(container, path) {
    const rows = container.querySelectorAll('.lib-row--dir');
    for (const row of rows) {
        if (row.dataset.path === path) return row;
    }
    return null;
}

// Expande les dossiers persistés en profondeur : chaque niveau est ouvert
// séquentiellement en attendant que ses enfants (chargés en async) soient prêts.
async function expandStoredPathsDeep(container) {
    if (!expandedPaths.size) return;
    const sorted = [...expandedPaths].sort((a, b) => a.length - b.length);
    for (const path of sorted) {
        const row = findDirRow(container, path);
        if (!row) continue;
        const children = row.parentElement.querySelector(':scope > .lib-children');
        if (children.style.display === 'block') continue;
        row.click();
        await new Promise(resolve => {
            const poll = setInterval(() => {
                if (!children.querySelector('.lib-loading')) {
                    clearInterval(poll);
                    resolve();
                }
            }, 40);
            setTimeout(() => { clearInterval(poll); resolve(); }, 8000);
        });
    }
}

function setLibFilter(f) {
    libFilter = f;
    updateLibFilterButton();
    applyLibFilter();
}

function cycleLibFilter() {
    const order = ['all', 'valid', 'invalid'];
    const idx = order.indexOf(libFilter);
    libFilter = order[(idx + 1) % order.length];
    setLibFilter(libFilter);
}

function updateLibFilterButton() {
    const btn = document.getElementById('lib-filter');
    if (!btn) return;
    const labels = { all: tr('filter_all'), valid: tr('lib_valid'), invalid: tr('lib_invalid') };
    const icons = { all: 'mdi mdi-filter-variant', valid: 'mdi mdi-check-circle', invalid: 'mdi mdi-alert' };
    btn.innerHTML = `<i class="${icons[libFilter]}"></i><span>${esc(labels[libFilter])}</span>`;
}

function loadLibMissing(path, badgeEl, childrenEl) {
    fetch(`/api/library/missing?path=${encodeURIComponent(path)}`)
        .then(r => r.json())
        .then(d => {
            if (badgeEl) {
                if (d.count > 0) {
                    badgeEl.textContent = d.count;
                    badgeEl.title = `${d.count} ${tr('lib_missing_episodes')}`;
                } else {
                    badgeEl.textContent = '';
                }
            }
            if (childrenEl && d.missing && d.missing.length) {
                const existing = [...childrenEl.querySelectorAll(':scope > .lib-node')];
                d.missing.forEach(m => {
                    const node = document.createElement('div');
                    node.className = 'lib-node';
                    const row = document.createElement('div');
                    row.className = 'lib-row lib-row--missing';
                    row.innerHTML = `
                        <span class="lib-toggle" style="visibility:hidden"><i class="mdi mdi-chevron-right"></i></span>
                        <i class="mdi mdi-file-video lib-icon-file"></i>
                        <span class="lib-name lib-name--file">
                            <span class="lib-name-main">${esc(m.label)}</span>
                            ${m.title ? `<span class="lib-missing-item">${esc(m.title)}</span>` : ''}
                        </span>
                        <span class="lib-badge lib-badge--warn"><i class="mdi mdi-alert"></i></span>`;
                    node.appendChild(row);
                    const se = (parseInt(m.s, 10) || 0) * 1000 + (parseInt(m.e, 10) || 0);
                    let inserted = false;
                    for (const n of existing) {
                        const main = n.querySelector(':scope > .lib-row .lib-name-main');
                        if (!main) continue;
                        const mm = main.textContent.match(/[Ss](\d{1,2})[Ee](\d{1,3})/);
                        if (!mm) continue;
                        const nse = parseInt(mm[1], 10) * 1000 + parseInt(mm[2], 10);
                        if (nse > se) { childrenEl.insertBefore(node, n); inserted = true; break; }
                    }
                    if (!inserted) childrenEl.appendChild(node);
                });
            }
        })
        .catch(() => { if (badgeEl) badgeEl.textContent = ''; });
}

function refreshSeriesBadges(container) {
    if (!container) return;
    [...container.querySelectorAll(':scope > .lib-node > .lib-row > .lib-missing-badge')].forEach(badge => {
        if (badge._loaded) return;
        badge._loaded = true;
        loadLibMissing(badge._path, badge, null);
    });
}

function loadApiUsage() {
    const el = document.getElementById('api-usage');
    if (!el) return;
    fetch('/api/usage')
        .then(r => r.json())
        .then(d => {
            const u = d.usage || {};
            const t = u.tvdb || {}, o = u.omdb || {};
            el.innerHTML = `
                <div class="usage-item">
                    <span class="usage-name">TVDB</span>
                    <span class="usage-bar"><span class="usage-fill usage-tvdb" style="width:${Math.min(100, ((t.total ?? 0) / 50000) * 100)}%"></span></span>
                    <span class="usage-val">${t.day_count ?? 0} <em>auj.</em> · ${t.total ?? 0}/50000 <em>an</em></span>
                </div>
                <div class="usage-item">
                    <span class="usage-name">OMDb</span>
                    <span class="usage-bar"><span class="usage-fill usage-omdb" style="width:${Math.min(100, ((o.day_count ?? 0) / 1000) * 100)}%"></span></span>
                    <span class="usage-val">${o.day_count ?? 0}/1000 <em>auj.</em> · ${o.total ?? 0} <em>total</em></span>
                </div>`;
        })
        .catch(() => { el.innerHTML = ''; });
}

function applyLibFilter() {
    const container = document.getElementById('library-tree');
    if (!container) return;
    function process(containerEl, isRootLevel) {
        const nodes = [...containerEl.children];
        let anyVisible = false;
        nodes.forEach(n => {
            const childrenEl = n.querySelector(':scope > .lib-children');
            let childVisible = false;
            if (childrenEl) childVisible = process(childrenEl, false);
            let selfMatches = libFilter === 'all' ? true :
                libFilter === 'valid' ? n._valid === true :
                n._valid === false;
            const visible = isRootLevel || selfMatches || childVisible;
            n.style.display = visible ? '' : 'none';
            if (visible) anyVisible = true;
        });
        return anyVisible;
    }
    process(container, true);
}

function buildLibNode(entry, depth, autoExpand) {
    const wrap = document.createElement('div');
    wrap.className = 'lib-node';
    wrap.style.paddingLeft = depth > 0 ? '20px' : '0';
    wrap._sort = { is_dir: !!entry.is_dir, name: entry.name, size: entry.size ?? 0, is_ep: !!(entry.meta && entry.meta.episode), rating: sortRating(entry) };
    wrap._valid = entry.valid;

    if (entry.is_dir) {
        const row = document.createElement('div');
        row.className = 'lib-row lib-row--dir';
        row.dataset.path = entry.path;
        const badgeDir = entry.valid === false ? `<span class="lib-badge lib-badge--warn"><i class="mdi mdi-alert"></i></span>` : '';
        const dirActions = document.createElement('div');
        dirActions.className = 'lib-actions';

        // Dossier vide : non-expandable, bouton supprimer uniquement
        if (entry.child_count === 0) {
            row.innerHTML = `
                <span class="lib-toggle" style="visibility:hidden"><i class="mdi mdi-chevron-right"></i></span>
                <i class="mdi mdi-folder-outline lib-icon-dir" style="opacity:0.5"></i>
                <span class="lib-name">${esc(entry.name)}</span>
                ${badgeDir}`;
            const delBtn = document.createElement('button');
            delBtn.className = 'btn-small revert';
            delBtn.title = tr('lib_delete_folder');
            delBtn.innerHTML = '<i class="mdi mdi-folder-remove"></i>';
            delBtn.addEventListener('click', (e) => { e.stopPropagation(); confirmDeleteFolder(entry.path); });
            dirActions.appendChild(delBtn);
            row.appendChild(dirActions);
            wrap.appendChild(row);
            return wrap;
        }

        row.innerHTML = `
            <span class="lib-toggle"><i class="mdi mdi-chevron-right"></i></span>
            ${libLead(entry, 'mdi-folder lib-icon-dir')}
            <span class="lib-name">
                <span class="lib-name-main">${esc(entry.name)}</span>
                ${metaText(entry.meta) ? metaHtml(entry.meta) : ''}
            </span>
            ${badgeDir}
            <span class="lib-size">${formatBytes(entry.size)}</span>
            ${entry.child_count !== undefined ? `<span class="lib-count">${entry.child_count}</span>` : ''}`;
        if (depth > 0 && entry.type === 'tv') {
            const searchBtn = document.createElement('button');
            searchBtn.className = 'btn-small search';
            searchBtn.title = tr('lib_search_tvdb');
            searchBtn.innerHTML = '<i class="mdi mdi-magnify"></i>';
            searchBtn.addEventListener('click', (e) => { e.stopPropagation(); libManualSearchFolder(entry.path, entry.name); });
            dirActions.appendChild(searchBtn);
        }
        row.appendChild(dirActions);
        if (/\[tvdbid-/.test(entry.name)) {
            const mBadge = document.createElement('span');
            mBadge.className = 'lib-missing-badge';
            mBadge._path = entry.path;
            row.insertBefore(mBadge, dirActions);
            row._missingBadge = mBadge;
        }
        const children = document.createElement('div');
        children.className = 'lib-children';
        children.style.display = 'none';
        let loaded = false;

        const toggle = (e) => {
            if (e && e.target.closest('.lib-actions, button')) return;
            const isOpen = children.style.display !== 'none';
            if (isOpen) {
                children.style.display = 'none';
                row.querySelector('.lib-toggle i').className = 'mdi mdi-chevron-right';
            } else {
                children.style.display = 'block';
                row.querySelector('.lib-toggle i').className = 'mdi mdi-chevron-down';
                if (!loaded) {
                    loaded = true;
                    children.innerHTML = `<div class="lib-loading">◌</div>`;
                    fetch(`/api/library?path=${encodeURIComponent(entry.path)}&type=${entry.type}`)
                        .then(r => r.json())
                        .then(data => {
                            children.innerHTML = '';
                            if (!data.entries?.length) {
                                const emptyRow = document.createElement('div');
                                emptyRow.className = 'lib-row lib-row--empty';
                                emptyRow.dataset.path = entry.path;
                                emptyRow.innerHTML = `<span class="lib-empty-label">${tr('lib_folder_empty')}</span>`;
                                const delBtn = document.createElement('button');
                                delBtn.className = 'btn-small revert';
                                delBtn.title = tr('lib_delete_folder');
                                delBtn.innerHTML = '<i class="mdi mdi-folder-remove"></i>';
                                delBtn.addEventListener('click', () => confirmDeleteFolder(entry.path));
                                emptyRow.appendChild(delBtn);
                                children.appendChild(emptyRow);
                            } else {
                                sortEntries(data.entries).forEach(child => children.appendChild(buildLibNode(child, 1, false)));
                                applyLibFilter();
                                if (/\[tvdbid-/.test(entry.name)) {
                                    loadLibMissing(entry.path, row._missingBadge, children);
                                } else {
                                    refreshSeriesBadges(children);
                                }
                            }
                        })
                        .catch(() => { children.innerHTML = `<div class="lib-loading">✗</div>`; });
                }
            }
            expandedPaths[isOpen ? 'delete' : 'add'](entry.path);
            saveExpandedPaths(expandedPaths);
        };
        row.addEventListener('click', toggle);
        wrap.appendChild(row);
        wrap.appendChild(children);
        if (autoExpand) toggle();
    } else {
        const badgeClass = entry.valid ? 'lib-badge--ok' : 'lib-badge--warn';
        const badgeIcon = entry.valid ? 'mdi-check-circle' : 'mdi-alert';
        const row = document.createElement('div');
        row.className = 'lib-row lib-row--file';
        row.dataset.filePath = entry.path;
        row.innerHTML = `
            ${libLead(entry, 'mdi-file-video lib-icon-file')}
            <span class="lib-name lib-name--file">
                <span class="lib-name-main">${esc(entry.name)}</span>
                ${metaText(entry.meta) ? metaHtml(entry.meta) : ''}
            </span>
            <span class="lib-size">${formatBytes(entry.size)}</span>
            <span class="lib-badge ${badgeClass}"><i class="mdi ${badgeIcon}"></i></span>`;
        const actions = document.createElement('div');
        actions.className = 'lib-actions';
        if (!entry.valid) {
            const searchBtn = document.createElement('button');
            searchBtn.className = 'btn-small search';
            searchBtn.title = tr('lib_search_tvdb');
            searchBtn.innerHTML = '<i class="mdi mdi-magnify"></i>';
            searchBtn.addEventListener('click', () => libManualSearch(entry.path));
            actions.appendChild(searchBtn);
        }
        const sendBtn = document.createElement('button');
        sendBtn.className = 'btn-small revert';
        sendBtn.title = tr('lib_send_back');
        sendBtn.innerHTML = '<i class="mdi mdi-undo"></i>';
        sendBtn.addEventListener('click', () => libSendBack(entry.path));
        actions.appendChild(sendBtn);
        row.appendChild(actions);
        wrap.appendChild(row);
    }
    return wrap;
}

function libSendBack(filePath) {
    postJSON('/api/library/send-back', { path: filePath })
        .then(data => {
            if (!data.success) { alert(`✗ ${data.message}`); return; }
            document.querySelectorAll('.lib-row--file').forEach(row => {
                if (row.dataset.filePath === filePath) row.closest('.lib-node')?.remove();
            });
        })
        .catch(e => alert(`✗ ${e.message}`));
}

function confirmDeleteFolder(folderPath) {
    const name = folderPath.split('/').pop() || folderPath.split('\\').pop();
    document.getElementById('delete-folder-msg').textContent = `"${name}"`;
    document.getElementById('confirm-delete-folder-btn').onclick = () => {
        closeModal('confirmDeleteFolderModal');
        postJSON('/api/library/delete-folder', { path: folderPath })
            .then(data => {
                if (!data.success) { alert(`✗ ${data.message}`); return; }
                loadLibrary();
            })
            .catch(e => alert(`✗ ${e.message}`));
    };
    document.getElementById('confirmDeleteFolderModal').classList.add('active');
}

function libManualSearchFolder(folderPath, folderName) {
    const title = String(folderName || '')
        .replace(/\.[^.]+$/, '')
        .replace(/\s*\[(?:imdb(?:id)?|tvdbid|tmdb(?:id)?)-[^\]]+\]/gi, '')
        .replace(/\s*\(\d{4}\)/, '')
        .replace(/[._]+/g, ' ')
        .replace(/\s+/g, ' ').trim();
    openManualSearchModal(title, { folderPath, fromLibrary: true });
}

function seriesFolderName(fileName) {
    const stem = String(fileName || '').replace(/\.[^.]+$/, '');
    const m = stem.match(/^(.*?)\s+-\s+(?:S\d{2}E\d{2}|\d+x\d{2})\b.*$/i);
    return (m && m[1] && m[1].trim()) ? m[1].trim() : stem;
}

function libRenameFolder(folderPath, result) {
    const folderBase = folderPath.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
    return fetch(`/api/library?path=${encodeURIComponent(folderPath)}&type=tv`)
        .then(r => r.json())
        .then(async data => {
            const files = (data.entries || []).filter(e => !e.is_dir && e.meta && e.meta.episode);
            const seriesTitle = cleanTitle(result.title || '');
            const year = result.year || '';
            const tvdbid = String(result.id || '');
            const epTitles = {};
            const renames = [];
            let skipped = 0;
            for (const entry of files) {
                const m = entry.meta.episode.match(/[Ss](\d{1,2})[Ee](\d{1,3})/);
                if (!m) { skipped++; continue; }
                const s = parseInt(m[1], 10), e = parseInt(m[2], 10);
                const ekey = `${s}x${e}`;
                if (!(ekey in epTitles)) {
                    epTitles[ekey] = entry.meta.episode_name || '';
                    try {
                        const det = await fetch(`/api/tv/${tvdbid}?season=${s}&episode=${e}&source=tvdb`).then(r => r.json());
                        const t = (det && (det.episode_title || det.t)) || '';
                        if (t) epTitles[ekey] = t;
                    } catch (err) { /* garde le nom du cache */ }
                }
                const details = {
                    title: seriesTitle, year,
                    tvdbid, tvdb: tvdbid,
                    imdbid: result.imdb_id || '', imdb: result.imdb_id || '',
                    tmdbid: result.tmdb_id || '', tmdb: result.tmdb_id || '',
                    episode_title: epTitles[ekey],
                    translations: result.translations || {},
                };
                const file = { path: entry.path, filename: entry.name, media_type: 'tv', season: s, episode: e };
                const newName = generateFilename(file, details);
                if (!newName || newName === entry.name) { skipped++; continue; }
                const r = await postJSON('/api/rename', { path: entry.path, new_name: newName }).catch(() => null);
                if (r && r.success) renames.push({ from: entry.name, to: newName });
                else skipped++;
            }

            // Vérifier / renommer le dossier de série (même si aucun fichier à renommer)
            const sampleFile = { path: folderPath, filename: 'sample.mkv', media_type: 'tv', season: 1, episode: 1 };
            const sampleDetails = {
                title: seriesTitle, year,
                tvdbid, tvdb: tvdbid,
                imdbid: result.imdb_id || '', imdb: result.imdb_id || '',
                tmdbid: result.tmdb_id || '', tmdb: result.tmdb_id || '',
                episode_title: '', translations: result.translations || {},
            };
            const desiredFolder = seriesFolderName(generateFilename(sampleFile, sampleDetails));
            let folderNote = null;
            if (desiredFolder && desiredFolder !== folderBase) {
                const fr = await postJSON('/api/library/rename-folder', { path: folderPath, new_name: desiredFolder }).catch(() => null);
                folderNote = (fr && fr.success)
                    ? { from: folderBase, to: desiredFolder }
                    : { from: folderBase, to: desiredFolder, error: true };
            }

            refreshLibrary();
            showLibRenameResult(folderBase, renames, skipped, folderNote);
        })
        .catch(e => alert(`✗ ${e.message}`));
}

function showLibRenameResult(folderBase, renames, skipped, folderNote) {
    const title = document.getElementById('libRenameResultTitle');
    const body = document.getElementById('libRenameResultBody');
    title.innerHTML = esc(folderBase);
    const total = renames.length;
    let html = `<div class="lib-rename-summary">${total} fichier(s) renommé(s)${skipped ? ` · ${skipped} ignoré(s)` : ''}</div>`;
    if (folderNote) {
        html += folderNote.error
            ? `<div class="lib-rename-folder lib-rename-folder--error">Dossier : <s>${esc(folderNote.from)}</s> → <strong>${esc(folderNote.to)}</strong> (échec, à renommer manuellement)</div>`
            : `<div class="lib-rename-folder">Dossier renommé : <s>${esc(folderNote.from)}</s> → <strong>${esc(folderNote.to)}</strong></div>`;
    }
    if (renames.length) {
        html += `<div class="lib-rename-list">${renames.map(r =>
            `<div class="lib-rename-item">
                <div class="lib-rename-from">${esc(r.from)}</div>
                <div class="lib-rename-sep"><i class="mdi mdi-arrow-down"></i></div>
                <div class="lib-rename-to">${esc(r.to)}</div>
            </div>`).join('')}</div>`;
    }
    if (!renames.length && !folderNote) {
        html = `<div class="lib-rename-summary">Aucun fichier renommé.</div>`;
    }
    body.innerHTML = html;
    document.getElementById('libRenameResultModal').classList.add('active');
}

function libManualSearch(filePath) {
    const filename = filePath.replace(/\\/g, '/').split('/').pop() || '';
    const tvRoot = globalConfig.tv_output_path || '';
    const isTv = (tvRoot && filePath.startsWith(tvRoot)) || /[Ss]\d{2}[Ee]\d{2}/.test(filename);
    const m = filename.match(/[Ss](\d{1,2})[Ee](\d{1,3})/);
    const season = m ? parseInt(m[1], 10) : 1;
    const episode = m ? parseInt(m[2], 10) : 1;
    // Extraire le titre proprement : supprimer extension, année, IDs, titre traduit entre parenthèses
    const cleanedTitle = filename
        .replace(/\.[^.]+$/, '')
        .replace(/\s*\[(?:imdb(?:id)?|tvdbid|tmdb(?:id)?)-[^\]]+\]/gi, '')
        .replace(/\s*-\s*\([^)]+\)\s*$/, '')
        .replace(/\s*\(\d{4}\)/, '')
        .replace(/\s*[Ss]\d{2}[Ee]\d{2}.*/i, '')
        .replace(/\s*\d+x\d{2}.*/i, '')
        .replace(/[\s\-]+$/, '')
        .replace(/\s+/g, ' ').trim();
    const fakeFile = { path: filePath, filename, media_type: isTv ? 'tv' : 'movie',
                       title: cleanedTitle, season, episode };
    if (!allFiles.find(f => f.path === filePath)) allFiles.push(fakeFile);
    openManualSearchModal(cleanedTitle, { filePath, fromLibrary: true });
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

    // Désactiver les boutons pendant le rename
    const row = findFileRow(filePath);
    if (row) row.querySelectorAll('.btn-small').forEach(b => b.disabled = true);

    postJSON('/api/rename', { path: file.path, new_name: newName })
        .then(data => {
            if (!data.success) {
                if (row) row.querySelectorAll('.btn-small').forEach(b => b.disabled = false);
                alert(`✗ ${tr('err_rename')}\n${data.message}`);
                return;
            }
            applyPathChange(file.path, data.new_path, newName);
        })
        .catch(e => {
            if (row) row.querySelectorAll('.btn-small').forEach(b => b.disabled = false);
            alert(`✗ ${tr('err_rename')}\n${e.message}`);
        });
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
    // Utiliser le path réel de la row DOM (peut différer d'allFiles après un rename)
    const row = findFileRow(filePath);
    const realPath = row ? decodeURIComponent(row.getAttribute('data-file-path')) : filePath;
    const file = allFiles.find(f => f.path === realPath) || allFiles.find(f => f.path === filePath);
    if (!file?.filename) return;
    const p = filesPreviews[file.path];
    if (!p?.data) return;

    const proposedName = generateFilename(file, p.data.details);
    if (proposedName && file.filename && proposedName.trim().toLowerCase() !== file.filename.trim().toLowerCase()) {
        openMoveNameMismatchModal(file, proposedName);
        return;
    }

    await runMove(file.path, file, proposedName || file.filename);
}

async function runMove(filePath, file, proposedName) {
    try {
        const data = await postJSON('/api/move', {
            path: file.path,
            media_type: file.media_type || 'movie'
        });
        if (!data.success) throw new Error(data.message);
        const jobId = data.job_id;
        activeTransfers[jobId] = { filePath, jobId, pollInterval: null, startTime: Date.now(), progress: null };
        renderTable();
        trackMoveProgress(jobId, filePath, file, data.new_name || proposedName || file.filename);
    } catch (e) {
        alert(`✗ ${tr('err_move')}\n${e.message}`);
    }
}

function unmarkFileAsTransferring(filePath) {
    const row = findFileRow(filePath);
    if (row) {
        row.classList.remove('transferring');
        row.removeAttribute('data-job-id');
    }
}

function updateTransferRow(filePath, jobId) {
    const row = findFileRow(filePath);
    if (!row) return;
    const transfer = activeTransfers[jobId];
    if (!transfer) return;
    const file = allFiles.find(f => f.path === filePath) || { path: filePath, filename: basename(filePath) };
    const cell = row.querySelector('.transfer-cell');
    if (cell) cell.innerHTML = renderTransferOverlay(file, transfer);
}

function showTransferResult(filePath, isError, msg) {
    const row = findFileRow(filePath);
    if (!row) return;
    const cell = row.querySelector('.transfer-cell');
    if (cell) {
        cell.innerHTML = `<div class="transfer-overlay transfer-overlay--done ${isError ? 'transfer-overlay--error' : ''}">
            <div class="transfer-overlay-title"><i class="mdi mdi-${isError ? 'alert-circle' : 'check-circle'}"></i> ${isError ? esc(msg || tr('transfer_done')) : tr('transfer_done')}</div>
        </div>`;
    }
    if (isError) {
        unmarkFileAsTransferring(filePath);
    }
}

function trackMoveProgress(jobId, filePath, file, newName) {
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/move-progress/${jobId}`);
            const prog = await response.json();
            const transfer = activeTransfers[jobId];
            if (!transfer) { clearInterval(pollInterval); return; }

            if (prog.finished) {
                clearInterval(pollInterval);
                delete activeTransfers[jobId];
                if (prog.error) {
                    showTransferResult(filePath, true, prog.error);
                    setTimeout(() => { unmarkFileAsTransferring(filePath); renderTable(); }, 2500);
                    return;
                }
                showTransferResult(filePath, false);
                setTimeout(() => removeFileFromList(filePath), 900);
                return;
            }

            transfer.progress = prog;
            updateTransferRow(filePath, jobId);
        } catch (e) {
            console.error('Progress poll error:', e);
        }
    }, 400);

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
            html += `<div style="padding:12px;color:#666;font-size:1em;">${tr('picker_empty')}</div>`;
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
            ['tvdb_api_key','omdb_api_key','movie_format','tv_format','input_path','movie_output_path','tv_output_path'].forEach(k => setVal(k, data[k] || ''));
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
        { id: 'omdb_api_key',       key: 'omdb_api_key' },
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

function testKeys(service) {
    const payload = {};
    const btnIds = { tvdb: 'tvdb_test_btn', omdb: 'omdb_test_btn' };
    const id = btnIds[service];
    const btn = document.getElementById(id);
    if (btn) { btn.classList.remove('valid', 'invalid'); btn.disabled = true; }
    if (service === 'tvdb') payload.tvdb_api_key = getVal('tvdb_api_key');
    if (service === 'omdb') payload.omdb_api_key = getVal('omdb_api_key');
    postJSON('/api/test-keys', payload)
        .then(data => {
            const res = data[service];
            if (btn) { btn.classList.toggle('valid', !!res?.valid); btn.classList.toggle('invalid', !res?.valid); btn.disabled = false; }
        })
        .catch(() => { if (btn) { btn.classList.add('invalid'); btn.disabled = false; } });
}

// ── Init ──────────────────────────────────────────────────────────────────────
// No periodic auto-scan is enabled. A new file discovery on disk is not observable
// from the browser runtime itself, so the page is initialized with a one-shot scan only.

document.addEventListener('DOMContentLoaded', () => {
    if (typeof applyTranslations === 'function') applyTranslations();
    initConfigAutoSave();
    loadApiUsage();

    const savedTab = localStorage.getItem(LIB_TAB_KEY);
    if (savedTab && ['files', 'library', 'history', 'config'].includes(savedTab)) {
        switchTab(savedTab);
    }

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
            if (cfg.tv_output_path) globalConfig.tv_output_path = cfg.tv_output_path;
            // Initial scan: populate the table but preserve previews if present
            scanFiles();
        })
        .catch(e => { if (!e.message.includes('401')) {
            scanFiles();
        } });
});
