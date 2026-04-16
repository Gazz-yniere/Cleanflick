'use strict';

// ============= State =============
let currentFilter = '';
let allFiles = [];
let filesPreviews = {};
let globalConfig = { movie_format: '{n} ({y})', tv_format: '{n} - {s00e00} - {t}' };
let renameHistory = {};

// ============= i18n helper (fallback si i18n.js absent) =============
function tr(key) {
    if (typeof TRANSLATIONS !== 'undefined' && typeof currentLang !== 'undefined') {
        return TRANSLATIONS[currentLang]?.[key] || TRANSLATIONS['fr']?.[key] || key;
    }
    return key;
}

// ============= Lang map =============
const LANG_MAP = {
    fr:'fra', de:'deu', es:'spa', it:'ita', pt:'por', ru:'rus',
    ja:'jpn', ko:'kor', zh:'zho', ar:'ara', pl:'pol', nl:'nld',
    sv:'swe', no:'nor', da:'dan', fi:'fin', tr:'tur', cs:'ces',
    hu:'hun', he:'heb', ro:'ron', uk:'ukr'
};

// ============= Format =============
function cleanTitle(title) {
    // Nettoie les titres en supprimant les séparateurs doubles (: et -)
    // qui créent des caractères bizarres (ex: 'Arrow: The Series' -> 'Arrow The Series')
    if (!title) return '';
    title = title.replace(/[\:-]/g, ' ').replace(/\s+/g, ' ').trim();
    return title;
}

function interpolate(template, vars) {
    return template.replace(/\{([a-zA-Z_]\w*)(:[^}]*)?\}/g, (_, name, fmt) => {
        if (name === 'n' && fmt) {
            const code = fmt.slice(1).toLowerCase();
            const trans = vars._translations || {};
            return trans[code] || trans[LANG_MAP[code]] || vars.n || '';
        }
        const val = vars[name];
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
    const ext = file.filename.slice(file.filename.lastIndexOf('.'));
    const fmt = file.media_type === 'movie' ? globalConfig.movie_format : globalConfig.tv_format;
    const s = file.season || 1, e = file.episode || 1;
    const cleanedTitle = cleanTitle(details.title || '');
    const cleanedEpisodeTitle = cleanTitle(details.episode_title || details.t || '');
    const vars = {
        n: cleanedTitle, title: cleanedTitle,
        ny: cleanedTitle && details.year ? `${cleanedTitle} (${details.year})` : cleanedTitle,
        y: details.year || '', year: details.year || '',
        d: details.airdate || details.release_date || '',
        airdate: details.airdate || '', release_date: details.release_date || '',
        t: cleanedEpisodeTitle, episode_title: cleanedEpisodeTitle,
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
        imdbid: details.imdbid || details.imdb || '',
        imdb:   details.imdbid || details.imdb || '',
        tmdbid: details.tmdbid || details.tmdb || '',
        tmdb:   details.tmdbid || details.tmdb || '',
        _translations: details.translations || {},
    };
    const result = interpolate(fmt, vars)
        .replace(/:/g, ' -').replace(/[<>"\/\\|?*]/g, '')
        .replace(/ -\s*-/g, ' -').replace(/\s+/g, ' ').replace(/[\s\-\.]+$/, '').trim();
    return result ? result + ext : file.filename;
}

// ============= Navigation =============
function switchTab(tab, e) {
    if (e) e.preventDefault();
    document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
    const section = document.getElementById(tab);
    if (section) section.classList.add('active');
    if (e && e.target) e.target.classList.add('active');
    if (tab === 'config') loadConfig();
}

// ============= Scan =============
function scanFiles() {
    const tbody = document.getElementById('files-tbody');
    tbody.innerHTML = `<tr><td colspan="3" style="text-align:center;padding:20px;color:#e67e22;">◌ ${tr('scanning')}</td></tr>`;
    fetch('/api/scan')
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
            allFiles = data;
            filesPreviews = {};
            filterFiles(currentFilter);
            loadPreviewsAsync(data);
        })
        .catch(e => {
            if (!e.message.includes('401'))
                tbody.innerHTML = `<tr><td colspan="3" class="message error">${tr('err_scan')} ${esc(e.message)}</td></tr>`;
        });
}

function loadPreviewsAsync(files) {
    files.forEach(file => {
        filesPreviews[file.path] = { loading: true, data: null, error: null };
        const endpoint = file.media_type === 'movie' ? '/api/search/movie' : '/api/search/tv';
        fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: file.title }) })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(results => {
            if (!results || results.length === 0) return null;
            const top = results[0];
            const url = file.media_type === 'movie'
                ? `/api/movie/${top.id}?source=tvdb`
                : `/api/tv/${top.id}?season=${file.season || 1}&episode=${file.episode || 1}&source=tvdb`;
            return fetch(url).then(r => r.json()).then(details => {
                details.imdbid = details.imdbid || top.imdb_id || '';
                details.imdb   = details.imdbid;
                details.tmdbid = details.tmdbid || top.tmdb_id || '';
                details.tmdb   = details.tmdbid;
                if (!details.translations || !Object.keys(details.translations).length)
                    details.translations = top.translations || {};
                return { source: top, details };
            });
        })
        .then(data => { filesPreviews[file.path] = { loading: false, data, error: null }; updateFileRow(file); })
        .catch(e => { filesPreviews[file.path] = { loading: false, data: null, error: e.message }; updateFileRow(file); });
    });
}

// ============= Render =============
function filterFiles(type) {
    currentFilter = type;
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.toggle('active',
            (type === '' && btn.dataset.filter === '') ||
            (type === 'movie' && btn.dataset.filter === 'movie') ||
            (type === 'tv' && btn.dataset.filter === 'tv')
        );
    });
    const filtered = type ? allFiles.filter(f => f.media_type === type) : allFiles;
    const tbody = document.getElementById('files-tbody');
    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="3" class="empty-state">${tr('no_files_found')}</td></tr>`;
        return;
    }
    tbody.innerHTML = filtered.map((file, idx) => `
        <tr data-idx="${idx}">
            <td class="file-name-cell">${esc(file.filename)}</td>
            <td class="preview-cell preview-cell-td">${renderPreview(file)}</td>
            <td class="actions-cell actions-cell-td">${renderActions(file, idx)}</td>
        </tr>`).join('');
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

function renderActions(file, idx) {
    const p = filesPreviews[file.path];
    if (!p || p.loading) return `<button class="btn-small search" disabled><i class="mdi mdi-loading mdi-spin"></i></button>`;
    let html = '';
    if (p.data) {
        const newName = generateFilename(file, p.data.details);
        if (newName === file.filename) {
            html += `<span class="btn-small" style="background:#1e3a2f;color:#5cb85c;cursor:default;"><i class="mdi mdi-check"></i>${tr('btn_ok')}</span>`;
        } else {
            html += `<button class="btn-small rename" onclick="doRename(${idx})"><i class="mdi mdi-pencil"></i>${tr('btn_rename')}</button>`;
        }
    }
    if (renameHistory[file.path]) {
        html += `<button class="btn-small revert" onclick="doRevert(${idx})" title="${esc(renameHistory[file.path].original_name)}"><i class="mdi mdi-undo"></i>${tr('btn_revert')}</button>`;
    }
    html += `<button class="btn-small search" onclick="manualSearchByIdx(${idx})"><i class="mdi mdi-magnify"></i>${tr('btn_other')}</button>`;
    return html;
}

function updateFileRow(file) {
    const idx = allFiles.findIndex(f => f.path === file.path);
    if (idx === -1) return;
    const row = document.querySelector(`tr[data-idx="${idx}"]`);
    if (!row) return;
    row.querySelector('.preview-cell').innerHTML = renderPreview(file);
    row.querySelector('.actions-cell').innerHTML = renderActions(file, idx);
}

// ============= Rename / Revert =============
function doRename(idx) {
    const file = allFiles[idx];
    const p = filesPreviews[file.path];
    if (!p || !p.data) return;
    const newName = generateFilename(file, p.data.details);
    if (newName === file.filename) return;
    fetch('/api/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: file.path, new_name: newName }) })
    .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    })
    .then(data => {
        if (!data.success) { 
            alert(`✗ ${tr('err_rename')}\n${data.message}`); 
            return; 
        }
        const newPath = data.new_path;
        renameHistory[newPath] = { original_path: file.path, original_name: file.filename };
        allFiles[idx] = { ...file, filename: newName, path: newPath };
        filesPreviews[newPath] = filesPreviews[file.path];
        delete filesPreviews[file.path];
        const row = document.querySelector(`tr[data-idx="${idx}"]`);
        if (row) {
            row.querySelector('.file-name-cell').textContent = newName;
            row.querySelector('.actions-cell').innerHTML = renderActions(allFiles[idx], idx);
        }
    })
    .catch(e => alert(`✗ ${tr('err_rename')}\n${e.message}`));
}

function doRevert(idx) {
    const file = allFiles[idx];
    const hist = renameHistory[file.path];
    if (!hist) return;
    fetch('/api/revert', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: file.path }) })
    .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    })
    .then(data => {
        if (!data.success) { 
            alert(`✗ ${tr('err_revert')}\n${data.message}`); 
            return; 
        }
        delete renameHistory[file.path];
        allFiles[idx] = { ...file, filename: hist.original_name, path: hist.original_path };
        filesPreviews[hist.original_path] = filesPreviews[file.path];
        delete filesPreviews[file.path];
        const row = document.querySelector(`tr[data-idx="${idx}"]`);
        if (row) {
            row.querySelector('.file-name-cell').textContent = hist.original_name;
            row.querySelector('.actions-cell').innerHTML = renderActions(allFiles[idx], idx);
        }
    })
    .catch(e => alert(`Erreur: ${e.message}`));
}

function renameAll() {
    const toRename = allFiles.filter(f => filesPreviews[f.path]?.data);
    if (toRename.length === 0) { alert(tr('err_no_files')); return; }
    toRename.forEach(file => {
        const idx = allFiles.findIndex(f => f.path === file.path);
        doRename(idx);
    });
}

// ============= Recherche Manuelle =============
function manualSearchByIdx(idx) {
    const file = allFiles[idx];
    if (!file) return;
    const modal = document.getElementById('manualSearchModal');
    const content = document.getElementById('manualSearchContent');
    let title = file.filename
        .replace(/\.[^.]+$/, '').replace(/\s*\[[^\]]*\]/g, '')
        .replace(/\s*\([^)]{8,}\)/g, '').replace(/\s*\(\d{4}\)/g, '')
        .replace(/[._]/g, ' ').replace(/\s*[-]\s*[Ss]\d+[Ee]\d+.*/i, '')
        .replace(/\s*[Ss]\d+[Ee]\d+.*/i, '').replace(/\s*(19|20)\d{2}.*/i, '')
        .replace(/\s+/g, ' ').trim();
    content.innerHTML = `
        <div class="form-field">
            <label>${tr('search_label')}</label>
            <input type="text" id="search-title" value="${esc(title)}" placeholder="..."
                onkeydown="if(event.key==='Enter') executeManualSearch(${idx})">
        </div>
        <button class="btn btn-primary" style="width:100%" onclick="executeManualSearch(${idx})">${tr('search_btn')}</button>
        <div id="manual-results" style="margin-top:15px;"></div>`;
    modal.classList.add('active');
    setTimeout(() => document.getElementById('search-title')?.focus(), 100);
}

function executeManualSearch(fileIdx) {
    const file = allFiles[fileIdx];
    const title = document.getElementById('search-title').value.trim();
    if (!title) return;
    const resultsDiv = document.getElementById('manual-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div>${tr('searching')}</div>`;
    const endpoint = file.media_type === 'movie' ? '/api/search/movie' : '/api/search/tv';
    fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) })
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(results => {
        if (!results || results.length === 0) { resultsDiv.innerHTML = `<div class="message error">${tr('search_none')}</div>`; return; }
        window._searchResults = results;
        window._searchFileIdx = fileIdx;
        let html = `<p style="color:#888;font-size:0.82em;margin-bottom:10px;">${results.length} ${tr('search_results')}</p><div class="search-results">`;
        results.forEach((r, i) => {
            const poster = r.poster ? `<img src="${esc(r.poster)}" alt="">` : (file.media_type === 'movie' ? '🎬' : '📺');
            html += `<div class="result-item" data-ridx="${i}" onclick="selectResult(this)">
                <div class="result-poster">${poster}</div>
                <div class="result-title">${esc(r.title || '')}</div>
                <div class="result-year">${r.year || 'N/A'}</div>
                <div class="result-type">TVDB #${r.id}</div></div>`;
        });
        html += '</div>';
        resultsDiv.innerHTML = html;
    })
    .catch(e => { resultsDiv.innerHTML = `<div class="message error">${tr('err_scan')} ${esc(e.message)}</div>`; });
}

function selectResult(el) {
    const ridx = parseInt(el.getAttribute('data-ridx'));
    const result = window._searchResults[ridx];
    const fileIdx = window._searchFileIdx;
    const file = allFiles[fileIdx];
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
        updateFileRow(file);
        closeModal('manualSearchModal');
    })
    .catch(e => { resultsDiv.innerHTML = `<div class="message error">Erreur: ${esc(e.message)}</div>`; });
}

// ============= File Picker =============
let _pickerTarget = null;
let _pickerCurrentPath = '';

function pickFolder(inputId) {
    _pickerTarget = inputId;
    const current = document.getElementById(inputId)?.value || '';
    browseFolder(current || null);
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
            if (_pickerTarget) document.getElementById(_pickerTarget).value = _pickerCurrentPath;
            closeModal('folderPickerModal');
        };
    }).catch(e => { content.innerHTML = `<div class="message error">Erreur: ${esc(e.message)}</div>`; });
}

// ============= Modal =============
function closeModal(id) { document.getElementById(id).classList.remove('active'); }
document.addEventListener('click', e => { if (e.target.classList.contains('modal')) e.target.classList.remove('active'); });

// ============= Config =============
function loadConfig() {
    fetch('/api/config')
    .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } return r.json(); })
    .then(data => {
        setVal('tvdb_api_key', data.tvdb_api_key || '');
        setVal('tvdb_pin', data.tvdb_pin || '');
        setVal('movie_format', data.movie_format || '');
        setVal('tv_format', data.tv_format || '');
        setVal('movie_path', data.movie_path || '');
        setVal('tv_path', data.tv_path || '');
        setVal('password', '');
        globalConfig.movie_format = data.movie_format || globalConfig.movie_format;
        globalConfig.tv_format = data.tv_format || globalConfig.tv_format;
    });
}

function saveConfig() {
    const tvdb = getVal('tvdb_api_key').trim();
    const pwd = getVal('password');
    const cfg = {
        tvdb_pin: getVal('tvdb_pin'),
        movie_format: getVal('movie_format'),
        tv_format: getVal('tv_format'),
        movie_path: getVal('movie_path'),
        tv_path: getVal('tv_path'),
    };
    if (tvdb) cfg.tvdb_api_key = tvdb;
    if (pwd) cfg.password = pwd;
    fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) })
    .then(r => r.json())
    .then(data => {
        const msg = document.getElementById('config-message');
        msg.innerHTML = `<div class="message ${data.success ? 'success' : 'error'}">${data.success ? '✓' : '✗'} ${esc(data.message)}</div>`;
        if (data.success) {
            globalConfig.movie_format = cfg.movie_format || globalConfig.movie_format;
            globalConfig.tv_format = cfg.tv_format || globalConfig.tv_format;
            setTimeout(() => msg.innerHTML = '', 3000);
        }
    });
}

function testKeys() {
    const resultsDiv = document.getElementById('api-test-results');
    resultsDiv.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    fetch('/api/test-keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tvdb_api_key: getVal('tvdb_api_key'), tvdb_pin: getVal('tvdb_pin') }) })
    .then(r => r.json())
    .then(data => {
        const t = data.tvdb;
        resultsDiv.innerHTML = `<div class="test-result ${t?.valid ? 'valid' : 'invalid'}">${esc(t?.message || 'N/A')}</div>`;
    })
    .catch(e => { resultsDiv.innerHTML = `<div class="test-result invalid">Erreur: ${esc(e.message)}</div>`; });
}

// ============= Helpers =============
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function getVal(id) { return document.getElementById(id)?.value || ''; }
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v; }

// ============= Init =============
document.addEventListener('DOMContentLoaded', () => {
    // Appliquer les traductions i18n si disponibles
    if (typeof applyTranslations === 'function') applyTranslations();

    // Charger config + historique puis scanner
    Promise.all([
        fetch('/api/config').then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } return r.json(); }),
        fetch('/api/rename-history').then(r => r.ok ? r.json() : {})
    ]).then(([cfg, hist]) => {
        if (cfg.movie_format) globalConfig.movie_format = cfg.movie_format;
        if (cfg.tv_format) globalConfig.tv_format = cfg.tv_format;
        renameHistory = hist || {};
        scanFiles();
    }).catch(e => { if (!e.message.includes('401')) scanFiles(); });
});
