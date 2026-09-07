// ── Library : état global ────────────────────────────────────────────────────
let libFilter = 'all';
let libGenreFilter = new Set();
let libAgeRange = null;      // [iMin, iMax] indices AGE_BUCKETS, ou null
let libSizeRange = null;     // [minOctets, maxOctets], ou null
let libRatingRange = null;   // [min, max] 0-10, ou null
let libDateRange = null;     // [anneeMin, anneeMax], ou null
let libMissingOnly = false;
let libShowS00 = false;
const LIB_EXPANDED_KEY = 'cleanflick_lib_expanded';
const LIB_TAB_KEY = 'cleanflick_active_tab';
const LIB_PAGE_SIZE = 200;
function loadExpandedPaths() {
    try { return new Set(JSON.parse(localStorage.getItem(LIB_EXPANDED_KEY) || '[]')); } catch (e) { return new Set(); }
}
function saveExpandedPaths(set) {
    try { localStorage.setItem(LIB_EXPANDED_KEY, JSON.stringify([...set])); } catch (e) {}
}
let expandedPaths = loadExpandedPaths();

// ── Métadonnées : affichage ──────────────────────────────────────────────────
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

function metaChips(meta, opts = {}) {
    const isEp = !!(meta && meta.episode);
    const chips = [];
    if (meta) {
        if (isEp) chips.push(`<span class="chip chip-ep">${esc(meta.episode)}${meta.episode_name ? ' ' + esc(meta.episode_name) : ''}</span>`);
        if (!isEp && meta.rating != null && meta.rating !== '') chips.push(`<span class="chip chip-rating">★ ${esc(meta.rating)}</span>`);
        if (meta.episode_rating != null && meta.episode_rating !== '') chips.push(`<span class="chip chip-rating">★ ${esc(meta.episode_rating)}</span>`);
        if (isEp) {
            if (meta.episode_date) chips.push(`<span class="chip chip-epdate">ép. ${esc(meta.episode_date.slice(0, 10))}</span>`);
            if (opts.dur_pending) chips.push(`<span class="chip chip-runtime chip-duration-pending"><span class="lib-spinner lib-spinner--chip"></span> min</span>`);
            else if (meta.episode_runtime) chips.push(`<span class="chip chip-runtime">${esc(meta.episode_runtime)}</span>`);
        } else {
            const date = meta.date || '';
            if (date.length > 4) chips.push(`<span class="chip chip-date">${esc(date.slice(0, 10))}</span>`);
            else if (meta.year) chips.push(`<span class="chip chip-date">${esc(String(meta.year))}</span>`);
            if (opts.dur_pending) chips.push(`<span class="chip chip-runtime chip-duration-pending"><span class="lib-spinner lib-spinner--chip"></span> min</span>`);
            else if (meta.runtime) chips.push(`<span class="chip chip-runtime">${esc(meta.runtime)}</span>`);
        }
        if (meta.certification) chips.push(`<span class="chip chip-cert">${esc(meta.certification)}</span>`);
        if (meta.genres) chips.push(`<span class="chip chip-genre">${esc(meta.genres)}</span>`);
    } else if (opts.dur_pending) {
        chips.push(`<span class="chip chip-runtime chip-duration-pending"><span class="lib-spinner lib-spinner--chip"></span> min</span>`);
    }
    return chips.join('');
}

function metaHtml(meta, opts) {
    if (!meta) return '';
    const chips = metaChips(meta, opts);
    return chips ? `<span class="lib-meta">${chips}</span>` : '';
}

function libLead(entry, iconClass) {
    // OMDb renvoie parfois "N/A" en guise d'URL : on filtre pour éviter un 404.
    const poster = entry.meta && entry.meta.poster && entry.meta.poster !== 'N/A' ? entry.meta.poster : '';
    if (poster) {
        return `<img class="lib-poster lib-lead" src="${esc(poster)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`;
    }
    return `<i class="mdi ${iconClass} lib-lead"></i>`;
}

function epCountHtml(entry) {
    if (entry.ep_present === null || entry.ep_present === undefined) return '';
    const present = entry.ep_present;
    const total = entry.ep_total;
    const text = (total && total > 0) ? `${present}/${total}` : String(present);
    const label = (total && total > 0) ? `${present}/${total} ${tr('lib_episodes')}` : `${present} ${tr('lib_episodes')}`;
    return `<span class="lib-ep-count" title="${esc(label)}">${text}</span>`;
}

// ── Chargement ───────────────────────────────────────────────────────────────
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
            collectLibFacets();
            expandStoredPathsDeep(container);
        })
        .catch(e => { container.innerHTML = `<div class="message error">${esc(e.message)}</div>`; });
}

function refreshLibrary() {
    const container = document.getElementById('library-tree');
    if (!container) return;
    const domExpanded = getExpandedPaths(container);
    if (domExpanded.length) {
        expandedPaths = new Set(domExpanded);
        saveExpandedPaths(expandedPaths);
    }
    if (!container.children.length || container.querySelector('.loading, .empty-state')) {
        loadLibrary();
        return;
    }
    // Rafraîchissement incrémental : ne met à jour que les différences, sans tout reconstruire.
    fetch('/api/library')
        .then(r => r.json())
        .then(data => {
            if (!data.entries?.length) return;
            reconcileContainer(container, data.entries, 0);
            applyLibFilter();
            collectLibFacets();
        })
        .catch(() => {});
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

// Expande les dossiers persistés : tous les dossiers d'un même niveau sont
// ouverts en parallèle (requêtes HTTP simultanées), puis le niveau suivant.
async function expandStoredPathsDeep(container) {
    if (!expandedPaths.size) return;
    const byDepth = new Map();
    for (const p of expandedPaths) {
        const d = p.split(/[\\/]/).filter(Boolean).length;
        if (!byDepth.has(d)) byDepth.set(d, []);
        byDepth.get(d).push(p);
    }
    const depths = [...byDepth.keys()].sort((a, b) => a - b);
    for (const d of depths) {
        await Promise.all(byDepth.get(d).map(path => new Promise(resolve => {
            const row = findDirRow(container, path);
            if (!row) return resolve();
            const children = row.parentElement.querySelector(':scope > .lib-children');
            if (children.style.display === 'block') return resolve();
            row.click();
            const poll = setInterval(() => {
                if (!children.querySelector('.lib-loading')) {
                    clearInterval(poll);
                    resolve();
                }
            }, 40);
            setTimeout(() => { clearInterval(poll); resolve(); }, 8000);
        })));
    }
}

// ── Nœuds ────────────────────────────────────────────────────────────────────
function buildLibNode(entry, depth, autoExpand) {
    const wrap = document.createElement('div');
    wrap.className = 'lib-node';
    wrap.style.paddingLeft = depth > 0 ? '20px' : '0';
    wrap._sort = { is_dir: !!entry.is_dir, name: entry.name, size: entry.size ?? 0, is_ep: !!(entry.meta && entry.meta.episode), rating: sortRating(entry), year: sortYear(entry) };
    wrap._valid = entry.valid;
    wrap._key = entry.path;
    wrap._path = entry.path;
    wrap._loaded = false;
    wrap._nextOffset = 0;
    wrap._missingBadge = null;
    wrap._imdb = (entry.meta && entry.meta.imdb) ? entry.meta.imdb : '';
    let _tvdbId = (entry.meta && entry.meta.tvdb) ? String(entry.meta.tvdb) : '';
    if (!_tvdbId) {
        const _mtv = String(entry.name || '').match(/\[tvdbid-(\d+)\]/i);
        if (_mtv) _tvdbId = _mtv[1];
    }
    wrap._tvdb = _tvdbId;
    wrap._meta = entry.meta || null;
    wrap._durPending = !entry.is_dir && !!entry.dur_pending;

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
            ${epCountHtml(entry)}
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
            wrap._missingBadge = mBadge;
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
                    wrap._loaded = true;
                    loadLibChildren(entry, children, row, 0, LIB_PAGE_SIZE);
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
                ${(metaText(entry.meta) || entry.dur_pending) ? metaHtml(entry.meta, { dur_pending: !!entry.dur_pending }) : ''}
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
        if (entry.dur_pending) registerPendingDuration(entry.path, wrap);
    }
    return wrap;
}

function loadLibChildren(entry, children, row, offset, limit) {
    const wrap = row.parentElement;
    if (offset === 0) children.innerHTML = `<div class="lib-loading"><span class="lib-spinner"></span></div>`;
    fetch(`/api/library?path=${encodeURIComponent(entry.path)}&type=${entry.type}&offset=${offset}&limit=${limit}`)
        .then(r => r.json())
        .then(data => {
            if (offset === 0) children.innerHTML = '';
            if (data.entries?.length) {
                sortEntries(data.entries).forEach(child => children.appendChild(buildLibNode(child, 1, false)));
                collectUncachedImdb(data.entries);
            }
            const loaded = offset + (data.entries?.length || 0);
            const total = data.total ?? loaded;
            wrap._loaded = true;
            wrap._nextOffset = loaded;
            if (loaded < total && data.entries?.length > 0) {
                // Chargement séquentiel automatique : la page suivante se charge d'elle-même
                // et les entrées s'affichent au fur et à mesure, sans bouton de pagination.
                loadLibChildren(entry, children, row, loaded, limit);
                return;
            }
            if (loaded === 0) {
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
            }
            if (/\[tvdbid-/.test(entry.name)) loadLibMissing(entry.path, wrap._missingBadge, children);
            else refreshSeriesBadges(children);
            scheduleApplyLibFilter();
            scheduleCollectFacets();
        })
        .catch(() => { if (offset === 0) children.innerHTML = `<div class="lib-loading lib-loading--error">✗</div>`; });
}

// ── Reconciliation incrémentale de la bibliothèque ───────────────────────────
function reconcileContainer(container, entries, depth) {
    const existing = new Map();
    Array.from(container.children).forEach(n => { if (n._key) existing.set(n._key, n); });
    const serverPaths = new Set(entries.map(e => e.path));
    for (const [key, node] of existing) {
        if (!serverPaths.has(key)) node.remove();
    }
    for (const entry of entries) {
        const node = existing.get(entry.path);
        if (node) {
            updateLibNode(node, entry);
            if (entry.is_dir && node._loaded) {
                const childrenEl = node.querySelector(':scope > .lib-children');
                if (childrenEl && childrenEl.style.display === 'block') {
                    syncLibFolder(entry, childrenEl, depth + 1, node);
                }
            }
        } else {
            insertLibNodeSorted(container, buildLibNode(entry, depth, false));
        }
    }
}

function updateLibNode(node, entry) {
    const sizeEl = node.querySelector('.lib-size');
    if (sizeEl && entry.size !== undefined) sizeEl.textContent = formatBytes(entry.size);
    const countEl = node.querySelector('.lib-count');
    if (countEl && entry.child_count !== undefined) countEl.textContent = entry.child_count;
    const epEl = node.querySelector('.lib-ep-count');
    if (entry.ep_present !== null && entry.ep_present !== undefined) {
        const html = epCountHtml(entry);
        if (epEl) {
            if (html) { const tmp = document.createElement('div'); tmp.innerHTML = html; const ne = tmp.firstElementChild; epEl.textContent = ne.textContent; epEl.title = ne.title; }
            else epEl.remove();
        } else if (html) {
            const tmp = document.createElement('div');
            tmp.innerHTML = html;
            const ne = tmp.firstElementChild;
            if (sizeEl) sizeEl.insertAdjacentElement('beforebegin', ne);
        }
    }
    node._valid = entry.valid;
    if (entry.meta) {
        node._meta = entry.meta;
        if (entry.meta.imdb) node._imdb = entry.meta.imdb;
        if (entry.meta.tvdb) node._tvdb = String(entry.meta.tvdb);
    }
    // La durée arrive plus tard : ré-enregistre le nœud s'il a été recréé
    // entre-temps (réconciliation) et perd sa référence dans _pendingDurations.
    if (!entry.is_dir) {
        const wasPending = node._durPending;
        node._durPending = !!entry.dur_pending;
        if (entry.dur_pending && wasPending && !_pendingDurations.has(entry.path))
            registerPendingDuration(entry.path, node);
    }
    const nameEl = node.querySelector('.lib-name');
    const newMeta = metaText(entry.meta) ? metaHtml(entry.meta, { dur_pending: !!node._durPending }) : '';
    if (nameEl) {
        const oldMeta = nameEl.querySelector('.lib-meta');
        if (oldMeta && newMeta && oldMeta.outerHTML !== newMeta) oldMeta.outerHTML = newMeta;
        else if (!oldMeta && newMeta) nameEl.insertAdjacentHTML('beforeend', newMeta);
        else if (oldMeta && !newMeta) oldMeta.remove();
    }
    if (node._sort) {
        node._sort.name = entry.name;
        node._sort.size = entry.size ?? 0;
        node._sort.is_dir = !!entry.is_dir;
        node._sort.is_ep = !!(entry.meta && entry.meta.episode);
        node._sort.rating = sortRating(entry);
        node._sort.year = sortYear(entry);
    }
}

function insertLibNodeSorted(container, node) {
    const sibs = Array.from(container.children);
    let before = null;
    for (const s of sibs) {
        if (s === node || !s._sort) continue;
        if (node._sort.is_dir && !s._sort.is_dir) { before = s; break; }
        if (node._sort.is_dir === s._sort.is_dir && libSortCmp(node._sort, s._sort) < 0) { before = s; break; }
    }
    container.insertBefore(node, before);
}

function syncLibFolder(entry, childrenEl, depth, node) {
    const limit = LIB_PAGE_SIZE;
    const upto = Math.max(0, node._nextOffset || 0);
    const acc = [];
    let fetched = 0;
    const load = (offset) => {
        fetch(`/api/library?path=${encodeURIComponent(entry.path)}&type=${entry.type}&offset=${offset}&limit=${limit}`)
            .then(r => r.json())
            .then(data => {
                if (data.entries?.length) acc.push(...data.entries);
                fetched = offset + (data.entries?.length || 0);
                const total = data.total ?? fetched;
                if (fetched < upto && total > fetched) load(fetched);
                else finalizeSync(entry, childrenEl, depth, node, acc, total);
            })
            .catch(() => {});
    };
    load(0);
}

function finalizeSync(entry, childrenEl, depth, node, acc, total) {
    node._nextOffset = Math.min(acc.length, total);
    reconcileContainer(childrenEl, acc, depth);
    if (/\[tvdbid-/.test(entry.name)) loadLibMissing(entry.path, node._missingBadge, childrenEl);
    else refreshSeriesBadges(childrenEl);
    applyLibFilter();
    collectLibFacets();
}
