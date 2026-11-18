// ── Vue grille (arbre / grille) de la bibliothèque ───────────────────────────
// La grille liste TOUS les films (fichiers) et séries (dossiers [tvdbid-…])
// de la bibliothèque, avec posters : /api/library/flat.
// Deux sections dépliables (Films / Séries), chacune avec sa grille de posters ;
// les filtres/tri de la barre latérale s'appliquent (client-side).
const LIB_VIEW_KEY = 'cleanflick_lib_view';
const LIB_GRID_SECTIONS_KEY = 'cleanflick_lib_grid_sections';
const LIB_GRID_CACHE_KEY = 'cleanflick_lib_flat_cache_v1';
const LIB_GRID_PAGE_SIZE = 60;
const LIB_GRID_CACHE_MAX_CHARS = 2_000_000;
let libView = 'tree';
function readLibGridCache() {
    try {
        const raw = localStorage.getItem(LIB_GRID_CACHE_KEY);
        if (!raw) return null;
        if (raw.length > LIB_GRID_CACHE_MAX_CHARS) {
            try { localStorage.removeItem(LIB_GRID_CACHE_KEY); } catch (e) {}
            return null;
        }
        const cache = JSON.parse(raw);
        if (cache?.version !== 1 || !Array.isArray(cache.items) || !Number.isFinite(cache.ts)) {
            try { localStorage.removeItem(LIB_GRID_CACHE_KEY); } catch (e) {}
            return null;
        }
        return cache;
    } catch (e) {
        try { localStorage.removeItem(LIB_GRID_CACHE_KEY); } catch (removeError) {}
        return null;
    }
}
const _cachedLibGrid = readLibGridCache();
let libFlatItems = _cachedLibGrid
    ? _cachedLibGrid.items.map(it => ({ ...it, _imdb: (it.meta && it.meta.imdb) || '', _tvdb: (it.meta && it.meta.tvdb) || '' }))
    : null;
let libGridSections = { movie: true, series: true };
let libGridVisibleItems = { movie: [], series: [] };
let libGridRenderedCount = { movie: 0, series: 0 };
let libGridExpanded = new Set();   // tvdb_id des séries ouvertes (expandées in-line dans la grille)
let libGridExpandedSeasons = new Map(); // tvdb_id -> numéro de saison ouverte (ou absent)
const _gridDetailCache = new Map();     // tvdb_id -> réponse API /api/library/series (détail)
let _gridLoaded = !!_cachedLibGrid;
let _gridTs = _cachedLibGrid?.ts || 0;
let _gridRenderSig = null;
let _gridVisibleSig = null;
let _gridFilterTimer = null;
let _gridLoadObserver = null;
const LIB_GRID_TTL = 60_000;
let _gridFetchSeq = 0;
let _gridFetchPromise = null;
let _gridFetchController = null;
let _gridCacheSaveTimer = null;

function scheduleLibGridCacheSave() {
    if (_gridCacheSaveTimer || !libFlatItems) return;
    _gridCacheSaveTimer = setTimeout(() => {
        _gridCacheSaveTimer = null;
        try {
            const raw = JSON.stringify({ version: 1, ts: _gridTs || Date.now(), items: libFlatItems });
            if (raw.length > LIB_GRID_CACHE_MAX_CHARS) {
                try { localStorage.removeItem(LIB_GRID_CACHE_KEY); } catch (e) {}
                return;
            }
            try {
                localStorage.setItem(LIB_GRID_CACHE_KEY, raw);
            } catch (e) {
                if (!(e?.name === 'QuotaExceededError' || e?.code === 22 || e?.code === 1014)) return;
                for (const key of ['cleanflick_lib_tree_cache', 'cleanflick_history_cache_v1']) {
                    try { localStorage.removeItem(key); } catch (removeError) {}
                    try {
                        localStorage.setItem(LIB_GRID_CACHE_KEY, raw);
                        return;
                    } catch (retryError) {}
                }
            }
        } catch (e) {}
    }, 350);
}

function libGridSortInfo(item) {
    const m = item.meta || {};
    const rating = parseFloat(m.rating);
    let year = -1;
    const d = String(m.date || '');
    if (/^\d{4}/.test(d)) year = parseInt(d.slice(0, 4), 10);
    else if (m.year) { const y = parseInt(m.year, 10); if (!isNaN(y) && y > 0) year = y; }
    return { name: item.name, size: item.size ?? 0, rating: isNaN(rating) ? -1 : rating, year };
}

function libGridCmp(a, b) {
    const A = libGridSortInfo(a), B = libGridSortInfo(b);
    let c;
    if (libSortKey === 'size') c = A.size - B.size;
    else if (libSortKey === 'rating') c = A.rating - B.rating;
    else c = A.name.localeCompare(B.name, undefined, { numeric: true, sensitivity: 'base' });
    if (c === 0) c = A.name.localeCompare(B.name, undefined, { numeric: true, sensitivity: 'base' });
    return libSortDir === 'desc' ? -c : c;
}

function libGridItemVisible(item) {
    if (libFilter !== 'all') {
        if (libFilter === 'valid' && item.valid !== true) return false;
        if (libFilter === 'invalid' && item.valid !== false) return false;
    }
    const meta = item.meta || {};
    if (libGenreFilter.size) {
        const genres = String(meta.genres || '').split(',').map(g => g.trim().toLowerCase());
        for (const g of libGenreFilter) if (!genres.includes(g.toLowerCase())) return false;
    }
    if (libAgeRange) {
        const idx = AGE_BUCKETS.indexOf(ageBucket(meta.certification || ''));
        if (idx < libAgeRange[0] || idx > libAgeRange[1]) return false;
    }
    if (libSizeRange && (item.size < libSizeRange[0] || item.size > libSizeRange[1])) return false;
    if (libRatingRange) {
        const r = parseFloat(meta.rating);
        if (isNaN(r) || r < libRatingRange[0] || r > libRatingRange[1]) return false;
    }
    if (libDateRange) {
        let y = -1;
        const d = String(meta.date || '');
        if (/^\d{4}/.test(d)) y = parseInt(d.slice(0, 4), 10);
        else if (meta.year) y = parseInt(meta.year, 10) || -1;
        if (y < libDateRange[0] || y > libDateRange[1]) return false;
    }
    if (libMissingOnly && !(item.type === 'series' && item.ep_present != null && item.ep_total > 0
        && item.ep_present < item.ep_total)) return false;
    if (libSearchQuery !== '') {
        const title = String(meta.title || '');
        const hay = libNormSearch(item.name + ' ' + title);
        if (!hay.includes(libNormSearch(libSearchQuery))) return false;
    }
    return true;
}

function libGridPosterHtml(item) {
    const p = item.meta && item.meta.poster && item.meta.poster !== 'N/A' ? item.meta.poster : '';
    if (p) return `<img class="lib-card-poster" src="${esc(p)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`;
    const icon = item.type === 'series' ? 'mdi-television-box-outline' : 'mdi-filmstrip';
    return `<div class="lib-card-poster lib-card-poster--ph"><i class="mdi ${icon}"></i></div>`;
}

function libCardHtml(item, i, expanded = false) {
    const meta = item.meta || {};
    // Badges comme dans la vue arbre : film = toujours (✓/⚠).
    // Série = compteur d'épisodes en haut à gauche (vert/orange/rouge).
    let left = '';
    if (item.type === 'movie') {
        left = item.valid
            ? `<span class="lib-badge lib-badge--ok lib-card-badge"><i class="mdi mdi-check-circle"></i></span>`
            : `<span class="lib-badge lib-badge--warn lib-card-badge"><i class="mdi mdi-alert"></i></span>`;
    } else {
        const pp = item.ep_present, tt = item.ep_total, up = item.ep_upcoming;
        if (pp != null && tt > 0) {
            // Rouge = manquant ; jaune = à venir (TBA) ; les deux = rouge + jaune.
            const cls = (pp < tt) ? ' lib-card-count--missing' : ((up > 0) ? ' lib-card-count--upcoming' : '');
            let counts = `<span class="lib-card-count${cls}" title="${pp}/${tt} ${tr('lib_episodes')}${up > 0 ? ` · ${up} à venir` : ''}">${pp}/${tt}</span>`;
            if (pp < tt && up > 0) counts += `<span class="lib-card-count lib-card-count--upcoming" title="${up} ${tr('lib_episodes')} à venir">${up} <i class="mdi mdi-clock-outline"></i></span>`;
            left = `<span class="lib-card-counts">${counts}</span>`;
        } else if (item.valid === false) {
            left = `<span class="lib-badge lib-badge--warn lib-card-badge"><i class="mdi mdi-alert"></i></span>`;
        }
    }
    const tvdb = String(meta.tvdb || '');
    const searchBtn = `<button class="lib-card-search" title="${esc(tr('lib_search_tvdb'))}" onclick="libCardSearch(event, this)"><i class="mdi mdi-magnify"></i></button>`;
    const tvdbLink = (item.type === 'series' && tvdb)
        ? `<a class="lib-card-tvdb" href="/api/library/tvdb-link/${esc(tvdb)}" target="_blank" rel="noopener noreferrer" title="Ouvrir la série sur TVDB"><i class="mdi mdi-open-in-new"></i></a>` : '';
    const clickAttr = (item.type === 'series') ? ' onclick="libGridOpenSeries(event, this)"' : '';
    const sizeChip = item.size ? `<span class="chip chip-size">${esc(formatBytes(item.size))}</span>` : '';
    const title = libDisplayTitle(item.name, meta.title);
    const cardInner = `
            ${left}
            <div class="lib-card-actions">${searchBtn}${tvdbLink}</div>
            ${libGridPosterHtml(item)}
            <div class="lib-card-body">
                <div class="lib-card-title" title="${esc(title)}">${esc(title)}</div>
                <div class="lib-card-meta">${metaChips(meta)}${sizeChip}</div>
            </div>`;
    if (expanded) {
        // Toute la largeur de la grille : col1 = poster + infos (clic poster = collapse),
        // col2 = saisons, col3 = épisodes de la saison sélectionnée.
        const p = meta.poster && meta.poster !== 'N/A' ? meta.poster : '';
        const poster = p
            ? `<img class="lib-card-poster" src="${esc(p)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'" onclick="libGridCollapseSeries(event, this)">`
            : `<div class="lib-card-poster lib-card-poster--ph" onclick="libGridCollapseSeries(event, this)" title="${esc(tr('lib_grid_collapse'))}"><i class="mdi mdi-television-box-outline"></i></div>`;
        return `
        <div class="lib-card lib-card--expanded" data-path="${esc(item.path)}" data-tvdb="${esc(tvdb)}" data-i="${i}">
            <div class="lib-sd-info">
                ${left}
                <div class="lib-card-actions">${searchBtn}${tvdbLink}</div>
                ${poster}
                <div class="lib-card-body">
                    <div class="lib-card-title" title="${esc(title)}">${esc(title)}</div>
                    <div class="lib-card-meta">${metaChips(meta)}${sizeChip}</div>
                </div>
            </div>
            <div class="lib-sd-seasons">${libSeasonsHtml(tvdb)}</div>
            <div class="lib-sd-eps">${libEpsHtml(tvdb)}</div>
        </div>`;
    }
    return `
        <div class="lib-card" data-path="${esc(item.path)}" data-tvdb="${esc(tvdb)}" data-i="${i}"${clickAttr}>
        ${cardInner}
        </div>`;
}

function libCardSearch(ev, btn) {
    ev.stopPropagation();
    const path = btn.closest('.lib-card')?.dataset.path;
    const item = libFlatItems ? libFlatItems.find(it => it.path === path) : null;
    if (!item) return;
    if (item.type === 'series') libManualSearchFolder(item.path, item.name);
    else libManualSearch(item.path);
}

// ── Détail série : expansion in-line dans la grille ──────────────────────────
// Le clic sur une carte série l'expand sur toute la largeur de la grille :
// carte compacte (re-clic = collapse) + saisons/épisodes en dessous.
// Une seule série est expandée à la fois. Les transitions se font par
// replacement in-place des cartes (pas de re-render global : pas de
// clignotement des posters).
function libGridOpenSeries(ev, el) {
    ev.stopPropagation();
    const card = el.closest('.lib-card');
    if (!card) return;
    const tvdb = card.dataset.tvdb;
    if (!tvdb) return;
    const i = card.dataset.i || 0;
    const item = libFlatItems ? libFlatItems.find(it => it.path === card.dataset.path) : null;
    if (!item) return;
    // Une seule série expandée à la fois : collapse de la précédente in-place.
    for (const prevTvdb of [...libGridExpanded]) {
        if (prevTvdb === tvdb) continue;
        const prevCard = document.querySelector(`.lib-card--expanded[data-tvdb="${CSS.escape(prevTvdb)}"]`);
        if (!prevCard) continue;
        const prevItem = libFlatItems ? libFlatItems.find(it => it.path === prevCard.dataset.path) : null;
        if (prevItem) libGridReplaceCard(prevCard, libCardHtml(prevItem, prevCard.dataset.i || 0));
    }
    libGridExpanded.clear();
    libGridExpandedSeasons.clear();
    libGridExpanded.add(tvdb);
    // Ouverture par défaut sur la saison 1 (si connue).
    const d = _gridDetailCache.get(tvdb);
    const seasons = (d && d.seasons) || [];
    const first = seasons.length ? Math.min(...seasons.map(s => s.number)) : 1;
    libGridExpandedSeasons.set(tvdb, first);
    const newCard = libGridReplaceCard(card, libCardHtml(item, i, true));
    if (!newCard) return;
    if (_gridDetailCache.has(tvdb)) { libGridUpdateDetailCols(newCard, tvdb); return; }
    fetch(`/api/library/series/${encodeURIComponent(tvdb)}`)
        .then(r => r.json())
        .then(data => {
            if (!libGridExpanded.has(tvdb)) return;
            _gridDetailCache.set(tvdb, data);
            // Recaler la saison ouverte par défaut sur la saison 1 réelle.
            const s1 = (data.seasons || []).map(s => s.number).sort((a, b) => a - b)[0];
            if (s1 != null) libGridExpandedSeasons.set(tvdb, s1);
            const el = document.querySelector(`.lib-card--expanded[data-tvdb="${CSS.escape(tvdb)}"]`);
            if (el) libGridUpdateDetailCols(el, tvdb);
        })
        .catch(() => {
            if (!libGridExpanded.has(tvdb)) return;
            _gridDetailCache.set(tvdb, { error: true, seasons: [] });
            const el = document.querySelector(`.lib-card--expanded[data-tvdb="${CSS.escape(tvdb)}"]`);
            if (el) libGridUpdateDetailCols(el, tvdb);
        });
}

// Collapse de la carte expandée : uniquement via le clic sur le poster/col1
// (le reste de la row doit rester cliquable/selectionnable).
function libGridCollapseSeries(ev, el) {
    ev.stopPropagation();
    const card = el.closest('.lib-card--expanded');
    if (!card) return;
    const tvdb = card.dataset.tvdb;
    if (!tvdb) return;
    const item = libFlatItems ? libFlatItems.find(it => it.path === card.dataset.path) : null;
    if (!item) return;
    libGridExpanded.delete(tvdb);
    libGridExpandedSeasons.delete(tvdb);
    libGridReplaceCard(card, libCardHtml(item, card.dataset.i || 0));
}

function libGridReplaceCard(oldCard, html) {
    const frag = document.createElement('div');
    frag.innerHTML = html;
    const next = frag.firstElementChild;
    if (!next) return null;
    oldCard.replaceWith(next);
    return next;
}

function updateLibGridSeriesTile(oldPath, newPath, name, meta, tvdbid) {
    if (!libFlatItems) return;
    const id = String(tvdbid || meta.tvdb || meta.tvdbid || '');
    const item = libFlatItems.find(candidate => candidate.type === 'series'
        && (candidate.path === oldPath || candidate.path === newPath
            || (id && String(candidate._tvdb || candidate.meta?.tvdb || '') === id)));
    if (!item) return;
    const card = [...document.querySelectorAll('.lib-card[data-path]')].find(candidate =>
        candidate.dataset.path === oldPath || candidate.dataset.path === newPath
        || (id && candidate.dataset.tvdb === id));
    const previousId = String(item._tvdb || item.meta?.tvdb || '');
    const nextMeta = { ...(item.meta || {}) };
    if (id && previousId && id !== previousId) {
        ['rating', 'date', 'genres', 'certification', 'runtime', 'poster', 'imdb', 'imdbid', 'tmdb', 'tmdbid']
            .forEach(key => delete nextMeta[key]);
    }
    item.path = newPath;
    item.name = name;
    item.meta = { ...nextMeta, ...meta };
    if (id) {
        item._tvdb = id;
        item.meta.tvdb = id;
    }
    item._imdb = item.meta.imdb || '';
    if (id && previousId && id !== previousId) {
        libGridExpanded.delete(previousId);
        libGridExpandedSeasons.delete(previousId);
    }

    const visible = libFlatItems.filter(libGridItemVisible).sort(libGridCmp);
    const seriesItems = visible.filter(candidate => candidate.type === 'series');
    libGridVisibleItems = {
        movie: visible.filter(candidate => candidate.type === 'movie'),
        series: seriesItems,
    };
    _gridVisibleSig = visible.map(candidate => candidate.path).join('|');
    const count = Math.min(libGridRenderedCount.series, seriesItems.length);
    const index = seriesItems.indexOf(item);
    const grid = card?.parentElement
        || document.querySelector('#library-grid .lib-grid-section[data-key="series"] .lib-grid');
    if (!grid) {
        _gridRenderSig = null;
        return;
    }
    _gridRenderSig = [libSortKey, libSortDir, [...libGridExpanded].sort().join(','), _gridVisibleSig].join('¦');
    const findCard = candidate => [...document.querySelectorAll('.lib-card[data-path]')].find(element =>
        element.dataset.path === candidate.path
        || (candidate._tvdb && element.dataset.tvdb === String(candidate._tvdb)));
    const createCard = (candidate, itemIndex) => {
        const wrapper = document.createElement('div');
        const expanded = libGridExpanded.has(String(candidate._tvdb || ''));
        wrapper.innerHTML = libCardHtml(candidate, itemIndex, expanded);
        return wrapper.firstElementChild;
    };
    const insertCard = (element, itemIndex) => {
        const next = seriesItems.slice(itemIndex + 1, count).map(findCard).find(Boolean);
        if (next) grid.insertBefore(element, next);
        else grid.appendChild(element);
    };
    const updateLoadMore = () => {
        const button = grid.parentElement?.querySelector('.lib-grid-load-more');
        if (!button) return;
        const shown = Math.min(libGridRenderedCount.series, seriesItems.length);
        if (shown >= seriesItems.length) button.remove();
        else button.textContent = `${tr('lib_grid_load_more')} (${shown}/${seriesItems.length})`;
    };
    if (card && index < 0) {
        card.remove();
        libGridRenderedCount.series = Math.min(Math.max(0, count - 1), seriesItems.length);
        if (!seriesItems.length) grid.closest('.lib-grid-section')?.remove();
        updateLoadMore();
        return;
    }
    if (card && index >= count) {
        card.remove();
        const promotedIndex = count - 1;
        const promoted = seriesItems[promotedIndex];
        if (promoted && !findCard(promoted)) {
            const element = createCard(promoted, promotedIndex);
            if (element) grid.appendChild(element);
        }
        return;
    }
    const updatedCard = card
        ? libGridReplaceCard(card, libCardHtml(item, index, libGridExpanded.has(String(item._tvdb || ''))))
        : (index >= 0 && index < count ? createCard(item, index) : null);
    if (updatedCard) insertCard(updatedCard, index);
    if (!card && updatedCard && count > 0) {
        const last = findCard(seriesItems[count - 1]);
        if (last && last !== updatedCard) last.remove();
    }
    if (updatedCard) collectGridEnrich([item]);
    collectGridEnrich([item]);
}

function libGridUpdateDetailCols(card, tvdb) {
    const seasons = card.querySelector(':scope > .lib-sd-seasons');
    const eps = card.querySelector(':scope > .lib-sd-eps');
    if (seasons) seasons.innerHTML = libSeasonsHtml(tvdb);
    if (eps) {
        eps.innerHTML = libEpsHtml(tvdb);
        eps.classList.remove('lib-sd-eps--swap');
        void eps.offsetWidth;
        eps.classList.add('lib-sd-eps--swap');
    }
}

function libGridOpenSeason(ev, tvdb, num) {
    ev.stopPropagation();
    if (!libGridExpanded.has(tvdb)) return;
    const cur = libGridExpandedSeasons.get(tvdb);
    if (cur === num) libGridExpandedSeasons.delete(tvdb);
    else libGridExpandedSeasons.set(tvdb, num);
    // Mise à jour ciblée (pas de re-render global : évite le clignotement
    // des posters) — on ne touche qu'aux classes des saisons et à la colonne
    // des épisodes.
    const card = document.querySelector(`.lib-card--expanded[data-tvdb="${CSS.escape(tvdb)}"]`);
    if (!card) { renderLibGrid(); return; }
    const openSeason = libGridExpandedSeasons.get(tvdb);
    card.querySelectorAll('.lib-sd-season').forEach(b => {
        b.classList.toggle('lib-sd-season--active', parseInt(b.dataset.season, 10) === openSeason);
    });
    const eps = card.querySelector(':scope > .lib-sd-eps');
    if (eps) {
        eps.innerHTML = libEpsHtml(tvdb);
        eps.classList.remove('lib-sd-eps--swap');
        void eps.offsetWidth;
        eps.classList.add('lib-sd-eps--swap');
    }
}

function libSeasonRowHtml(tvdb, season, openSeason) {
    const p = season.poster && season.poster !== 'N/A' ? season.poster : '';
    const poster = p
        ? `<img class="lib-sd-season-poster" src="${esc(p)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=&quot;lib-sd-season-poster lib-sd-season-poster--ph&quot;>${esc(tr('lib_grid_season'))} ${esc(season.number)}</div>'">`
        : `<div class="lib-sd-season-poster lib-sd-season-poster--ph">${esc(tr('lib_grid_season'))} ${esc(season.number)}</div>`;
    const counts = [`<span class="lib-sd-count">${season.present}/${season.total}</span>`];
    if (season.missing > 0) counts.push(`<span class="lib-sd-count lib-sd-count--missing" title="${season.missing} ${tr('lib_episodes')} manquants">${season.missing} <i class="mdi mdi-alert-circle-outline"></i></span>`);
    if (season.upcoming > 0) counts.push(`<span class="lib-sd-count lib-sd-count--upcoming" title="${season.upcoming} ${tr('lib_episodes')} à venir">${season.upcoming} <i class="mdi mdi-clock-outline"></i></span>`);
    return `<button class="lib-sd-season${openSeason === season.number ? ' lib-sd-season--active' : ''}" data-season="${season.number}" onclick="libGridOpenSeason(event, '${esc(tvdb)}', ${season.number})">
        ${poster}
        <div class="lib-sd-season-info">
            <span class="lib-sd-season-name">${esc(tr('lib_grid_season'))} ${esc(season.number)}${season.year ? ` <span style="color:var(--c-text3);font-weight:400">${esc(season.year)}</span>` : ''}</span>
            <span class="lib-sd-counts">${counts.join('')}</span>
        </div>
    </button>`;
}

function libEpRowHtml(ep) {
    const icon = ep.state === 'present' ? 'mdi-check' : (ep.state === 'upcoming' ? 'mdi-clock-outline' : 'mdi-minus-circle-outline');
    const size = ep.files && ep.files.length ? esc(formatBytes(ep.files.reduce((a, f) => a + (f.size || 0), 0))) : '';
    const rating = ep.rating ? `<span class="chip chip-rating">★ ${esc(ep.rating)}</span>` : '';
    const runtime = ep.runtime ? `<span class="chip chip-runtime">${esc(ep.runtime)}</span>` : '';
    const date = ep.airdate ? `<span class="chip chip-epdate">${esc(ep.airdate)}</span>` : '';
    const filename = ep.files && ep.files.length ? esc(ep.files[0].name) : '';
    return `<div class="lib-ep-row lib-ep-row--${ep.state}" title="${esc(ep.title || '')}${filename ? ' — ' + filename : ''}">
        <span class="lib-ep-badge"><i class="mdi ${icon}"></i></span>
        <span class="lib-ep-label">${esc(tr('lib_grid_episode'))} ${ep.e}</span>
        <span class="lib-ep-chips">${rating}</span>
        <span class="lib-ep-title">${esc(ep.title || '')}</span>
        <span class="lib-ep-chips">${runtime}${date}</span>
        ${size ? `<span class="lib-ep-size">${size}</span>` : ''}
    </div>`;
}

function libSeasonsHtml(tvdb) {
    const d = _gridDetailCache.get(tvdb);
    if (!d) return `<div class="lib-sd-eps-empty"><span class="lib-spinner"></span></div>`;
    if (d.error) return `<div class="lib-sd-eps-empty">${esc(tr('lib_grid_empty'))}</div>`;
    const seasons = d.seasons || [];
    const openSeason = libGridExpandedSeasons.get(tvdb);
    return seasons.length
        ? seasons.map(s => libSeasonRowHtml(tvdb, s, openSeason)).join('')
        : `<div class="lib-sd-eps-empty">${esc(tr('lib_grid_empty'))}</div>`;
}

function libEpsHtml(tvdb) {
    const d = _gridDetailCache.get(tvdb);
    if (!d || d.error) return '';
    const openSeason = libGridExpandedSeasons.get(tvdb);
    if (openSeason == null) return '';
    const season = (d.seasons || []).find(s => s.number === openSeason);
    if (!season) return '';
    const eps = season.episodes || [];
    if (!eps.length) return `<div class="lib-sd-eps-empty">${esc(tr('lib_grid_empty'))}</div>`;
    return `<div class="lib-sd-eps-head">${esc(tr('lib_grid_season'))} ${esc(season.number)}<span class="lib-sd-counts"><span class="lib-sd-count">${season.present}/${season.total}</span>${season.missing > 0 ? `<span class="lib-sd-count lib-sd-count--missing" title="${season.missing} ${tr('lib_episodes')} manquants">${season.missing} <i class="mdi mdi-alert-circle-outline"></i></span>` : ''}${season.upcoming > 0 ? `<span class="lib-sd-count lib-sd-count--upcoming" title="${season.upcoming} ${tr('lib_episodes')} à venir">${season.upcoming} <i class="mdi mdi-clock-outline"></i></span>` : ''}</span></div>` + eps.map(libEpRowHtml).join('');
}

function libGridToggleSection(key) {
    const sec = document.querySelector(`.lib-grid-section[data-key="${key}"]`);
    if (!sec) return;
    const body = sec.querySelector('.lib-grid-section-body');
    const open = body.hidden;
    body.hidden = !open;
    sec.querySelector('.lib-grid-section-head > i:first-child').classList.toggle('open', open);
    libGridSections[key] = open;
    try { localStorage.setItem(LIB_GRID_SECTIONS_KEY, JSON.stringify(libGridSections)); } catch (e) {}
}

function libGridSectionHtml(labelHtml, items, key) {
    const open = libGridSections[key] !== false;
    const emptyLabel = key === 'movie' ? 'lib_grid_empty_movies' : 'lib_grid_empty_series';
    const count = Math.min(libGridRenderedCount[key] || LIB_GRID_PAGE_SIZE, items.length);
    libGridRenderedCount[key] = count;
    const body = items.length
        ? `<div class="lib-grid">${items.slice(0, count).map((it, i) => libCardHtml(it, i)).join('')}</div>
            ${libGridLoadMoreButton(key, count, items.length)}`
        : `<div class="lib-grid"><div class="empty-state">${tr(emptyLabel)}</div></div>`;
    return `<section class="lib-grid-section" data-key="${key}">
        <div class="lib-grid-section-head" onclick="libGridToggleSection('${key}')">
            <i class="mdi mdi-chevron-right${open ? ' open' : ''}"></i>
            ${labelHtml}
            <span class="lib-grid-section-count">${items.length}</span>
        </div>
        <div class="lib-grid-section-body" ${open ? '' : 'hidden'}>${body}</div>
    </section>`;
}

function libGridSeriesSectionHtml(series) {
    const count = Math.min(libGridRenderedCount.series || LIB_GRID_PAGE_SIZE, series.length);
    libGridRenderedCount.series = count;
    return `<section class="lib-grid-section" data-key="series">
        <div class="lib-grid-section-head" onclick="libGridToggleSection('series')">
            <i class="mdi mdi-chevron-right${libGridSections.series !== false ? ' open' : ''}"></i>
            <i class="mdi mdi-television"></i> ${tr('lib_grid_series')}
            <span class="lib-grid-section-count">${series.length}</span>
        </div>
        <div class="lib-grid-section-body" ${libGridSections.series !== false ? '' : 'hidden'}>
            <div class="lib-grid">${series.slice(0, count).map((it, i) => libCardHtml(it, i, libGridExpanded.has(String(it._tvdb || '')))).join('')}</div>
            ${libGridLoadMoreButton('series', count, series.length)}
        </div>
    </section>`;
}

function libGridLoadMoreButton(key, count, total) {
    if (count >= total) return '';
    return `<button type="button" class="btn btn-secondary lib-grid-load-more" data-grid-key="${key}" onclick="libGridLoadMore('${key}')">
        ${esc(tr('lib_grid_load_more'))} (${count}/${total})
    </button>`;
}

function observeLibGridLoadMore(container) {
    if (typeof IntersectionObserver === 'undefined') return;
    if (!_gridLoadObserver) {
        _gridLoadObserver = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                _gridLoadObserver.unobserve(entry.target);
                libGridLoadMore(entry.target.dataset.gridKey);
            });
        }, { rootMargin: '400px 0px' });
    }
    container.querySelectorAll('.lib-grid-load-more').forEach(button => _gridLoadObserver.observe(button));
}

function libGridLoadMore(key) {
    const items = libGridVisibleItems[key] || [];
    const section = document.querySelector(`#library-grid .lib-grid-section[data-key="${key}"]`);
    const grid = section?.querySelector('.lib-grid');
    const button = section?.querySelector('.lib-grid-load-more');
    if (!grid || !button || button.disabled) return;
    button.disabled = true;
    _gridLoadObserver?.unobserve(button);

    const start = libGridRenderedCount[key];
    const end = Math.min(start + LIB_GRID_PAGE_SIZE, items.length);
    const nextItems = items.slice(start, end);
    const html = nextItems.map((item, index) => {
        const expanded = key === 'series' && libGridExpanded.has(String(item._tvdb || ''));
        return libCardHtml(item, start + index, expanded);
    }).join('');
    grid.insertAdjacentHTML('beforeend', html);
    libGridRenderedCount[key] = end;
    collectGridEnrich(nextItems);

    if (end >= items.length) button.remove();
    else {
        button.textContent = `${tr('lib_grid_load_more')} (${end}/${items.length})`;
        button.disabled = false;
        if (_gridLoadObserver) _gridLoadObserver.observe(button);
    }
}

function renderLibGrid() {
    const container = document.getElementById('library-grid');
    if (!container) return;
    if (!libFlatItems) return;
    const visible = libFlatItems.filter(libGridItemVisible).sort(libGridCmp);
    // Signature du contenu affiché : si identique au rendu précédent, on ne
    // reconstruit pas le DOM (pas de rechargement des posters / clignotement).
    const sig = [libSortKey, libSortDir, [...libGridExpanded].sort().join(','), visible.map(it => it.path).join('|')].join('¦');
    if (sig === _gridRenderSig) return;
    _gridRenderSig = sig;
    const visibleSig = visible.map(it => it.path).join('|');
    if (visibleSig !== _gridVisibleSig) libGridRenderedCount = { movie: 0, series: 0 };
    _gridVisibleSig = visibleSig;
    const movies = visible.filter(it => it.type === 'movie');
    const series = visible.filter(it => it.type === 'series');
    libGridVisibleItems = { movie: movies, series };
    _gridLoadObserver?.disconnect();
    if (!libFlatItems.length) {
        container.innerHTML = `<div class="empty-state">${tr('lib_grid_empty')}</div>`;
        return;
    }
    if (!movies.length && !series.length) {
        container.innerHTML = `<div class="empty-state lib-grid-empty">
            <span>${esc(tr('lib_grid_no_matches'))}</span>
            <button class="btn btn-secondary" onclick="resetLibFilters()">${esc(tr('lib_filter_reset'))}</button>
        </div>`;
        return;
    }
    let html = '';
    if (movies.length) {
        html += libGridSectionHtml(`<i class="mdi mdi-movie"></i> ${tr('lib_grid_movies')}`, movies, 'movie');
    }
    if (series.length) {
        // Séries ouvertes : cartes expandées in-line dans la grille (pleine
        // largeur, saisons + épisodes en dessous) ; les autres restent cliquables.
        html += libGridSeriesSectionHtml(series);
    }
    container.innerHTML = html;
    observeLibGridLoadMore(container);
    collectGridEnrich();
}

function applyLibGridFilter() {
    if (libView !== 'grid' || !_gridLoaded) return;
    // Debounce : pendant le drag des sliders, un seul re-render à la fin
    // (le sig check de renderLibGrid évite de plus les rebuilds inutiles).
    if (_gridFilterTimer) return;
    _gridFilterTimer = setTimeout(() => { _gridFilterTimer = null; renderLibGrid(); }, 120);
}

function setLibView(v) {
    if (libView === v) return;
    libView = v;
    if (v !== 'grid' && _gridFetchController) {
        _gridFetchController.abort();
        _gridFetchController = null;
        _gridFetchPromise = null;
        _gridFetchSeq += 1;
    }
    try { localStorage.setItem(LIB_VIEW_KEY, v); } catch (e) {}
    document.getElementById('lib-view-tree')?.classList.toggle('active', v === 'tree');
    document.getElementById('lib-view-grid')?.classList.toggle('active', v === 'grid');
    const tree = document.getElementById('library-tree');
    const grid = document.getElementById('library-grid');
    tree.hidden = v !== 'tree';
    grid.hidden = v !== 'grid';
    if (v === 'grid') loadLibGrid();
}

function loadLibGrid(force) {
    if (libView !== 'grid') return;
    if (libFlatItems && !force && Date.now() - _gridTs < LIB_GRID_TTL) {
        renderLibGrid();
        return Promise.resolve(libFlatItems);
    }
    if (_gridFetchPromise && !force) return _gridFetchPromise;
    if (_gridFetchController) _gridFetchController.abort();
    const controller = new AbortController();
    _gridFetchController = controller;
    const seq = ++_gridFetchSeq;
    const grid = document.getElementById('library-grid');
    if (libFlatItems) renderLibGrid();
    else if (grid) grid.innerHTML = `<div class="skeleton-grid">${'<div class="skeleton skeleton-card"></div>'.repeat(8)}</div>`;
    const promise = fetch('/api/library/flat', { signal: controller.signal })
        .then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            if (!(r.headers.get('content-type') || '').includes('application/json'))
                throw new Error(tr('lib_grid_invalid_response'));
            return r.json();
        })
        .then(data => {
            if (seq !== _gridFetchSeq) return;
            if (!Array.isArray(data.items)) throw new Error(tr('lib_grid_invalid_response'));
            libFlatItems = data.items.map(it => ({ ...it, _imdb: (it.meta && it.meta.imdb) || '', _tvdb: (it.meta && it.meta.tvdb) || '' }));
            _gridLoaded = true;
            _gridTs = Date.now();
            scheduleLibGridCacheSave();
            _gridRenderSig = null;
            _gridVisibleSig = null;
            renderLibGrid();
        })
        .catch(error => {
            if (seq !== _gridFetchSeq) return;
            if (error.name === 'AbortError') return;
            console.error('Library grid load failed:', error);
            if (!libFlatItems && grid) grid.innerHTML = `<div class="message error lib-grid-load-error">
                <strong>${esc(tr('lib_grid_load_error'))}</strong>
                <small>${esc(error.message)}</small>
                <button class="btn btn-secondary" onclick="loadLibGrid(true)">${esc(tr('btn_refresh_library'))}</button>
            </div>`;
        })
        .finally(() => {
            if (seq === _gridFetchSeq) {
                _gridFetchPromise = null;
                _gridFetchController = null;
            }
        });
    _gridFetchPromise = promise;
    return promise;
}

// Enrichissement (note/genres/poster manquants) des items de la grille :
// file throttlée commune à l'arbre (120 IDs/120 TVDB par batch + délai).
function collectGridEnrich(items) {
    if (!libFlatItems) return;
    const candidates = items || [
        ...libGridVisibleItems.movie.slice(0, libGridRenderedCount.movie),
        ...libGridVisibleItems.series.slice(0, libGridRenderedCount.series),
    ];
    const ids = [];
    const tvdbIds = [];
    for (const it of candidates) {
        const m = it.meta || {};
        if (it.type === 'series') {
            if (it._tvdb && !m.rating) tvdbIds.push(it._tvdb);
        } else if (it._imdb && !m.rating) {
            ids.push(it._imdb);
        }
    }
    if (ids.length || tvdbIds.length) enqueueLibraryEnrich(ids, tvdbIds, []);
}

// Appliqué par applyEnrichResults (lib-enrich.js) : met à jour la liste plate.
window.libGridApplyEnrich = (results) => {
    if (!libFlatItems) return;
    const changed = [];
    libFlatItems.forEach(it => {
        let en = null;
        if (it._imdb && results[it._imdb]) en = results[it._imdb];
        if (it._tvdb && results['tvdb:' + it._tvdb]) en = results['tvdb:' + it._tvdb];
        if (!en) return;
        const m = it.meta || {};
        if (en.imdb) { m.imdb = en.imdb; it._imdb = en.imdb; }
        if (en.rating) m.rating = en.rating;
        if (en.date) m.date = en.date;
        if (en.genres) m.genres = en.genres;
        if (en.certification) m.certification = en.certification;
        if (en.runtime && !m.runtime) m.runtime = en.runtime;
        if (en.poster && !m.poster) m.poster = en.poster;
        it.meta = m;
        changed.push(it);
    });
    if (!changed.length || libView !== 'grid') return;
    scheduleLibGridCacheSave();
    // Update in-place des cartes touchées (pas de re-render global).
    for (const it of changed) {
        const card = document.querySelector(`.lib-card[data-path="${CSS.escape(it.path)}"]`);
        if (!card) continue;
        const tvdb = String((it.meta && it.meta.tvdb) || it._tvdb || '');
        const expanded = tvdb && libGridExpanded.has(tvdb);
        const next = libGridReplaceCard(card, libCardHtml(it, card.dataset.i || 0, expanded));
        if (next && expanded) libGridUpdateDetailCols(next, tvdb);
    }
};

document.addEventListener('DOMContentLoaded', () => {
    try {
        const v = localStorage.getItem(LIB_VIEW_KEY);
        if (v === 'grid' || v === 'tree') libView = v;
        const s = JSON.parse(localStorage.getItem(LIB_GRID_SECTIONS_KEY) || 'null');
        if (s && typeof s === 'object') libGridSections = { ...libGridSections, ...s };
    } catch (e) {}
    if (libView === 'grid') {
        document.getElementById('lib-view-tree')?.classList.remove('active');
        document.getElementById('lib-view-grid')?.classList.add('active');
        const tree = document.getElementById('library-tree');
        const grid = document.getElementById('library-grid');
        if (tree) tree.hidden = true;
        if (grid) grid.hidden = false;
        loadLibGrid();
    }
});
