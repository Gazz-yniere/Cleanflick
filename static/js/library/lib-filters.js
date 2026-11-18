// ── Tranches d'âge : les certifications brutes (OMDb/TVDB) sont hétérogènes
// (MPAA, TV ratings, « N/A », « Unrated »…). On les regroupe en buckets
// compréhensibles ; les données brutes ne sont pas modifiées. ────────────────
const AGE_BUCKETS = [
    'Tous publics',
    '7 et +',
    '10 et +',
    '13 et +',
    '16 et +',
    'Adultes',
    'Non classé',
];
const AGE_BUCKET_MAP = {
    'G': 'Tous publics', 'TV-G': 'Tous publics', 'TV-Y': 'Tous publics',
    'PG': '7 et +', 'TV-PG': '7 et +', 'TV-Y7': '7 et +', 'TV-Y7-FV': '7 et +',
    '10': '10 et +', '10+': '10 et +', '10 years and up': '10 et +',
    '12': '13 et +', '12+': '13 et +', '13': '13 et +', '13+': '13 et +',
    '13 years and up': '13 et +', '12 years and up': '13 et +', '14': '13 et +',
    'PG-13': '13 et +', 'TV-14': '13 et +', 'MA-17': '13 et +',
    '16': '16 et +', '16+': '16 et +', '16 years and up': '16 et +',
    '15 years and up': '16 et +', '15': '16 et +',
    'R': 'Adultes', 'NC-17': 'Adultes', 'TV-MA': 'Adultes', '18': 'Adultes', '18+': 'Adultes',
    'N/A': 'Non classé', 'NR': 'Non classé', 'Not Rated': 'Non classé', 'Unrated': 'Non classé',
};
function ageBucket(cert) {
    if (!cert) return '';
    const c = String(cert).trim();
    const key = c.toLowerCase().replace(/\s+/g, ' ');
    if (AGE_BUCKET_MAP[c] !== undefined) return AGE_BUCKET_MAP[c];
    if (AGE_BUCKET_MAP[key] !== undefined) return AGE_BUCKET_MAP[key];
    const m = c.match(/^(\d{1,2})\+?$/);
    if (m) {
        const n = parseInt(m[1], 10);
        if (n <= 7) return 'Tous publics';
        if (n <= 10) return '7 et +';
        if (n <= 13) return '13 et +';
        if (n <= 16) return '16 et +';
        return 'Adultes';
    }
    return 'Non classé';
}

function libMetaMatches(node) {
    const meta = node._meta;
    if (libGenreFilter.size && !(meta && meta.genres)) return false;
    if (libAgeRange && !(meta && meta.certification)) return false;
    if (libGenreFilter.size) {
        // ET : TOUS les genres cochés doivent être présents.
        const genres = String((meta && meta.genres) || '').split(',').map(g => g.trim().toLowerCase());
        for (const g of libGenreFilter) if (!genres.includes(g.toLowerCase())) return false;
    }
    if (libAgeRange) {
        const idx = AGE_BUCKETS.indexOf(ageBucket((meta && meta.certification) || ''));
        if (idx < libAgeRange[0] || idx > libAgeRange[1]) return false;
    }
    return true;
}

function libAnyFilterActive() {
    return libFilter !== 'all' || libSearchQuery !== '' || libGenreFilter.size > 0 || libAgeRange !== null
        || libSizeRange !== null || libRatingRange !== null || libDateRange !== null
        || libMissingOnly;
}

// ── Recherche par chaîne (nom de fichier/dossier ou titre) ──────────────────
// Normalisation : minuscules, accents retirés, extension du fichier retirée.
function libNormSearch(s) {
    return String(s || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/\.[a-z0-9]{2,5}$/i, '')
        .toLowerCase();
}
function libNodeSearchText(n) {
    const name = n._entry ? n._entry.name : '';
    const title = (n._meta && n._meta.title) ? n._meta.title : '';
    const source = name + ' ' + title;
    if (n._searchSource !== source) {
        n._searchSource = source;
        n._searchText = libNormSearch(source);
    }
    return n._searchText || '';
}
function libSearchInput(v) {
    libSearchQuery = String(v || '').trim();
    updateLibFacetButtons();
    scheduleApplyLibFilter();
}
function libSearchReset() {
    const el = document.getElementById('lib-search');
    if (el) el.value = '';
    libSearchQuery = '';
    updateLibFacetButtons();
    scheduleApplyLibFilter();
}

function applyLibFilter() {
    const container = document.getElementById('library-tree');
    if (!container) return;
    const facetsActive = libGenreFilter.size > 0 || libAgeRange !== null;
    const searchActive = libSearchQuery !== '';
    const q = searchActive ? libNormSearch(libSearchQuery) : '';
    function rangeOk(n) {
        // Taille : filtrage des fichiers uniquement (les dossiers suivent leurs enfants).
        if (libSizeRange && !(n._sort && n._sort.is_dir)) {
            const s = n._sort && n._sort.size;
            if (typeof s !== 'number' || s < libSizeRange[0] || s > libSizeRange[1]) return false;
        }
        if (libRatingRange) {
            const r = n._sort && n._sort.rating;
            if (typeof r !== 'number' || r < 0 || r < libRatingRange[0] || r > libRatingRange[1]) return false;
        }
        if (libDateRange) {
            const y = n._sort && n._sort.year;
            if (typeof y !== 'number' || y < 0 || y < libDateRange[0] || y > libDateRange[1]) return false;
        }
        return true;
    }
    function process(containerEl, isRootLevel, parentMetaOk) {
        const nodes = [...containerEl.children];
        let anyVisible = false;
        nodes.forEach(n => {
            const hasMeta = !!(n._meta && (n._meta.genres || n._meta.certification));
            const metaOk = hasMeta ? libMetaMatches(n) : (facetsActive ? parentMetaOk : true);
            const childrenEl = n._childrenEl;
            let childVisible = false;
            if (childrenEl) childVisible = process(childrenEl, false, metaOk);
            let selfMatches = libFilter === 'all' ? true :
                libFilter === 'valid' ? n._valid === true :
                n._valid === false;
            // Une ligne d'épisode manquant est par nature un « manquant » : on ne
            // l'exclut pas du filtre (elle ne porte pas de _missingCount).
            // Les lignes « à venir » (jaunes) n'ont ni _missingCount ni classe
            // --missing : elles sont donc masquées quand le filtre est actif.
            const isMissingRow = !!n._row?.classList.contains('lib-row--missing');
            const missingOk = !libMissingOnly || isMissingRow || (n._missingCount !== undefined && n._missingCount > 0);
            // Dossier en cours de chargement : on ne peut pas encore évaluer ses
            // enfants (non présents dans le DOM) → on le garde visible le temps
            // du chargement, puis le filtre est ré-appliqué (scheduleApplyLibFilter).
            // Pour une série, la note appartient au dossier série. Une note
            // d'épisode ne doit donc pas faire apparaître la série dans le filtre.
            const isSeriesNode = !!(n._sort && n._sort.is_dir && n._tvdb);
            const childCanMatch = !(libRatingRange && isSeriesNode);
            // Recherche : la ligne correspond si son propre nom/titre contient la
            // chaîne ; un dossier peut aussi apparaître via un descendant (récursif).
            const searchSelfOk = !searchActive || libNodeSearchText(n).includes(q);
            // Un dossier non chargé reste visible pendant le filtre : son contenu
            // est inconnu, mais on n'explore pas en réseau tous les dossiers fermés.
            const unknownChildren = !!(n._childrenEl && !n._loaded);
            const visible = isRootLevel || n._loading || unknownChildren || ((selfMatches && metaOk && rangeOk(n) && missingOk && searchSelfOk)
                || ((searchActive || childCanMatch) && childVisible));
            n.style.display = visible ? '' : 'none';
            if (visible) anyVisible = true;
        });
        return anyVisible;
    }
    process(container, true, true);
    applyLibGridFilter();
}

// ── Filtres : facettes (genre) + sliders de plage (âge / taille / note) ─────
// Le recalcul (refacet + rebuild des sliders de domaine) est un parcours plein
// de l'arbre : on le regroupe (debounce) pendant le chargement paginé.
let _facetDebounceTimer = null;
function scheduleCollectFacets() {
    if (_facetDebounceTimer) return;
    _facetDebounceTimer = setTimeout(() => { _facetDebounceTimer = null; collectLibFacets(); }, 250);
}
let _filterDebounceTimer = null;
function scheduleApplyLibFilter() {
    if (_filterDebounceTimer) clearTimeout(_filterDebounceTimer);
    _filterDebounceTimer = setTimeout(() => {
        _filterDebounceTimer = null;
        requestAnimationFrame(applyLibFilter);
    }, 90);
}
// Facettes en cache : le parcours de l'arbre complet coûte (des milliers de
// nœuds). Les valeurs sont calculées en débouncé (250 ms) et les panneaux /
// sliders sont rendus depuis ce cache → l'ouverture d'un panneau est instantanée.
const _libFacetCache = { genres: null, size: null, year: null };
let _libFacetPreloadRequested = false;
let _libSizeRangeLoaded = false;
let _libSizeRangeRequest = false;

function collectLibFacets() {
    const genres = new Set();
    const years = [];
    document.querySelectorAll('#library-tree .lib-node').forEach(n => {
        const s = n._sort;
        if (s && typeof s.year === 'number' && s.year > 0) years.push(s.year);
        const m = n._meta;
        if (m && m.genres) String(m.genres).split(',').forEach(g => { g = g.trim(); if (g) genres.add(g); });
    });
    years.sort((a, b) => a - b);
    _libFacetCache.genres = genres;
    _libFacetCache.year = years.length ? [years[0], years[years.length - 1]] : null;
    renderLibFacetList('lib-genre-list', genres, libGenreFilter);
    updateLibFacetButtons();
    libSizeRender();
    libDateRender();
}

function loadLibSizeRange() {
    if (_libSizeRangeLoaded || _libSizeRangeRequest) return;
    _libSizeRangeRequest = true;
    fetch('/api/library/size-range')
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(data => {
            if (data.ready) {
                _libFacetCache.size = data.count ? [data.min, data.max] : null;
                _libSizeRangeLoaded = true;
                libSizeRender();
                return;
            }
            if (data.error) throw new Error(data.error);
            setTimeout(loadLibSizeRange, data.loading ? 750 : 1500);
        })
        .catch(error => {
            const host = document.getElementById('lib-size-slider');
            if (host) host.innerHTML = `<div class="lib-slider-empty">${esc(error.message)}</div>`;
            console.error('Library size range unavailable:', error);
        })
        .finally(() => { _libSizeRangeRequest = false; });
}

function setLibFacetPreload(enabled) {
    _libFacetPreloadRequested = enabled;
    if (enabled) continueLibFacetPreload();
}

function continueLibFacetPreload() {
    if (!_libFacetPreloadRequested) return;
    _libFacetPreloadRequested = false;
    collectLibFacets();
}

function renderLibFacetList(listId, values, selected) {
    const list = document.getElementById(listId);
    if (!list) return;
    const vals = [...values].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
    list.innerHTML = vals.map(v =>
        `<label class="lib-facet-item"><input type="checkbox" value="${esc(v)}" ${selected.has(v) ? 'checked' : ''}> ${esc(v)}</label>`
    ).join('');
    list.querySelectorAll('input[type=checkbox]').forEach(cb => {
        cb.addEventListener('change', () => {
            if (cb.checked) selected.add(cb.value); else selected.delete(cb.value);
            updateLibFacetButtons();
            applyLibFilter();
        });
    });
}

function libFacetSelectAll(kind, on) {
    const sel = libGenreFilter;
    sel.clear();
    const list = document.getElementById('lib-genre-list');
    if (list) list.querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = !!on; if (on) sel.add(cb.value); });
    updateLibFacetButtons();
    applyLibFilter();
}

function updateLibFacetButtons() {
    document.getElementById('lib-search')?.closest('.lib-side-search')?.classList.toggle('active', libSearchQuery !== '');
    const genreSection = document.getElementById('lib-genre-section');
    const genreCount = document.getElementById('lib-genre-count');
    if (genreSection) genreSection.classList.toggle('active', libGenreFilter.size > 0);
    if (genreCount) {
        genreCount.textContent = String(libGenreFilter.size);
        genreCount.hidden = libGenreFilter.size === 0;
    }
    let activeCount = libGenreFilter.size;
    if (libAgeRange) activeCount++;
    if (libSizeRange) activeCount++;
    if (libRatingRange) activeCount++;
    if (libDateRange) activeCount++;
    const advancedCount = document.getElementById('lib-advanced-count');
    if (advancedCount) {
        advancedCount.textContent = String(activeCount);
        advancedCount.hidden = activeCount === 0;
    }
    document.querySelector('#lib-age-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libAgeRange !== null);
    document.querySelector('#lib-size-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libSizeRange !== null);
    document.querySelector('#lib-rating-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libRatingRange !== null);
    document.querySelector('#lib-date-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libDateRange !== null);
    const m = document.getElementById('lib-filter-missing');
    if (m) m.classList.toggle('active', libMissingOnly);
}

function setLibFilter(f) {
    libFilter = f;
    updateLibFilterButton();
    applyLibFilter();
}

function updateLibFilterButton() {
    const select = document.getElementById('lib-filter');
    if (select) select.value = libFilter;
}

function resetLibFilters() {
    libFilter = 'all';
    libSearchQuery = '';
    libGenreFilter.clear();
    libAgeRange = null;
    libSizeRange = null;
    libRatingRange = null;
    libDateRange = null;
    libMissingOnly = false;

    const search = document.getElementById('lib-search');
    if (search) search.value = '';
    document.querySelectorAll('#lib-genre-list input[type=checkbox]').forEach(input => { input.checked = false; });
    document.getElementById('lib-advanced-filters').open = false;
    document.getElementById('lib-genre-section').open = false;

    updateLibFilterButton();
    updateLibFacetButtons();
    libAgeSliderSync();
    libRatingSliderSync();
    libSizeRender();
    libDateRender();
    applyLibFilter();
}
