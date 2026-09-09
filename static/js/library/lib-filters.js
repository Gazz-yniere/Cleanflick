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
    return libFilter !== 'all' || libGenreFilter.size > 0 || libAgeRange !== null
        || libSizeRange !== null || libRatingRange !== null || libDateRange !== null
        || libMissingOnly;
}

// Pré-charge les enfants de tous les dossiers collapsed (sans les déplier) pour
// que le filtre puisse évaluer leur contenu : le DOM ne contient que les nœuds
// déjà rendus, or les enfants d'un dossier non ouvert sont chargés à la volée.
function preloadAllFolders(container) {
    const walk = (el) => {
        for (const n of el.children) {
            if (!n._sort || !n._sort.is_dir) continue;
            if (n._loaded) continue;
            const childrenEl = n.querySelector(':scope > .lib-children');
            const row = n.querySelector(':scope > .lib-row');
            if (!childrenEl || !row || !n._entry) continue;
            n._loaded = true;
            loadLibChildren(n._entry, childrenEl, row, 0, LIB_PAGE_SIZE, true);
        }
    };
    walk(container);
    container.querySelectorAll('.lib-children').forEach(c => walk(c));
}

function applyLibFilter() {
    const container = document.getElementById('library-tree');
    if (!container) return;
    if (libAnyFilterActive()) preloadAllFolders(container);
    const facetsActive = libGenreFilter.size > 0 || libAgeRange !== null;
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
            const childrenEl = n.querySelector(':scope > .lib-children');
            let childVisible = false;
            if (childrenEl) childVisible = process(childrenEl, false, metaOk);
            let selfMatches = libFilter === 'all' ? true :
                libFilter === 'valid' ? n._valid === true :
                n._valid === false;
            // Une ligne d'épisode manquant est par nature un « manquant » : on ne
            // l'exclut pas du filtre (elle ne porte pas de _missingCount).
            // Les lignes « à venir » (jaunes) n'ont ni _missingCount ni classe
            // --missing : elles sont donc masquées quand le filtre est actif.
            const isMissingRow = !!n.querySelector(':scope > .lib-row.lib-row--missing');
            const missingOk = !libMissingOnly || isMissingRow || (n._missingCount !== undefined && n._missingCount > 0);
            // Dossier en cours de chargement : on ne peut pas encore évaluer ses
            // enfants (non présents dans le DOM) → on le garde visible le temps
            // du chargement, puis le filtre est ré-appliqué (scheduleApplyLibFilter).
            // Pour une série, la note appartient au dossier série. Une note
            // d'épisode ne doit donc pas faire apparaître la série dans le filtre.
            const isSeriesNode = !!(n._sort && n._sort.is_dir && n._tvdb);
            const childCanMatch = !(libRatingRange && isSeriesNode);
            const visible = isRootLevel || n._loading || ((selfMatches && metaOk && rangeOk(n) && missingOk)
                || (childCanMatch && childVisible));
            n.style.display = visible ? '' : 'none';
            if (visible) anyVisible = true;
        });
        return anyVisible;
    }
    process(container, true, true);
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
    if (_filterDebounceTimer) return;
    _filterDebounceTimer = setTimeout(() => { _filterDebounceTimer = null; applyLibFilter(); }, 250);
}
function collectLibFacets() {
    const genres = new Set();
    document.querySelectorAll('#library-tree .lib-node').forEach(n => {
        const m = n._meta;
        if (m && m.genres) String(m.genres).split(',').forEach(g => { g = g.trim(); if (g) genres.add(g); });
    });
    renderLibFacetList('lib-genre-list', genres, libGenreFilter);
    updateLibFacetButtons();
    libSizeRebuild();
    libDateRebuild();
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
    const g = document.getElementById('lib-filter-genre');
    if (g) g.classList.toggle('active', libGenreFilter.size > 0);
    document.querySelector('#lib-age-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libAgeRange !== null);
    document.querySelector('#lib-size-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libSizeRange !== null);
    document.querySelector('#lib-rating-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libRatingRange !== null);
    document.querySelector('#lib-date-slider')?.closest('.lib-side-slider')?.classList.toggle('active', libDateRange !== null);
    const m = document.getElementById('lib-filter-missing');
    if (m) m.classList.toggle('active', libMissingOnly);
}

function toggleLibFilterPanel(kind) {
    const panel = document.getElementById(`lib-${kind}-panel`);
    if (!panel) return;
    const open = panel.classList.toggle('open');
    document.querySelectorAll('.lib-filter-panel').forEach(p => { if (p !== panel) p.classList.remove('open'); });
    if (open) collectLibFacets();
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
