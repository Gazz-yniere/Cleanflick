// ── Tri de la bibliothèque ───────────────────────────────────────────────────
let libSortKey = 'name';
let libSortDir = 'asc';

function sortRating(entry) {
    const m = entry.meta || {};
    const v = m.episode ? m.episode_rating : m.rating;
    const n = parseFloat(v);
    return isNaN(n) ? -1 : n;
}

function sortYear(entry) {
    const m = entry.meta || {};
    if (m.episode) {
        const d = String(m.episode_date || '').slice(0, 4);
        return /^\d{4}$/.test(d) ? parseInt(d, 10) : -1;
    }
    if (m.date && /^\d{4}/.test(String(m.date))) return parseInt(String(m.date).slice(0, 4), 10);
    if (m.year) { const y = parseInt(m.year, 10); if (!isNaN(y) && y > 0) return y; }
    return -1;
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
    applyLibGridFilter();
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
