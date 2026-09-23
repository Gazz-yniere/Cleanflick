// ── Enrichissement OMDb en arrière-plan (non bloquant) ───────────────────────
// Les films ont un ID IMDb dans leur nom ; les séries n'ont que l'ID TVDB :
// le serveur résout TVDB → IMDb (1 requête TVDB, mise en cache) puis enrichit.
let enrichQueue = [];
let tvdbEnrichQueue = [];
let epEnrichQueue = [];
let enrichRunning = false;
let _epEnrichPending = new Set();

function _episodeLabelsFromText(text, cap = 100) {
    // Labels SxxExx pour un texte de nom de fichier, en développant les
    // multi-épisodes : S01E01-E02, S01E01E02, S01E01, E02, 1x02-03...
    const labels = [];
    const range = /[Ss](\d{1,2})[Ee](\d{1,3})\s*(?:[-–—~]\s*[Ee](\d{1,3})|[Ee](\d{1,3}))/;
    let m = range.exec(text);
    while (m) {
        const s = parseInt(m[1], 10);
        const a = parseInt(m[2], 10);
        const b = m[3] ? parseInt(m[3], 10) : a;
        for (let e = Math.min(a, b); e <= Math.max(a, b); e++) labels.push(`S${String(s).padStart(2, '0')}E${String(e).padStart(2, '0')}`);
        text = text.slice(m.index + m[0].length);
        m = range.exec(text);
    }
    if (!labels.length) {
        const single = /[Ss](\d{1,2})[Ee](\d{1,3})/.exec(text);
        if (single) labels.push(`S${single[1]}E${single[2]}`);
    }
    return labels.slice(0, cap);
}

function _seriesEpLabels(node, cap = 100) {
    const labels = [];
    const childrenEl = node.querySelector(':scope > .lib-children');
    if (!childrenEl) return labels;
    childrenEl.querySelectorAll(':scope > .lib-node > .lib-row.lib-row--file').forEach(row => {
        const n = row.closest('.lib-node');
        if (n && n._meta && n._meta.episode_rating) return;
        const main = row.querySelector('.lib-name-main');
        if (!main) return;
        labels.push(..._episodeLabelsFromText(String(main.textContent || ''), cap - labels.length));
    });
    return labels.slice(0, cap);
}

function collectUncachedImdb(entries) {
    const ids = [];
    const tvdbIds = [];
    const series = [];
    for (const e of entries) {
        const m = e.meta;
        if (!m) continue;
        if (e.is_dir) {
            let tv = m.tvdb ? String(m.tvdb) : '';
            if (!tv) {
                const mtv = String(e.name || '').match(/\[tvdbid-(\d+)\]/i);
                if (mtv) tv = mtv[1];
            }
            // Dossier série sans note globale : on demande l'enrichissement
            // OMDb (note/date/genres/cert) via son ID TVDB (résolution mise
            // en cache côté serveur, 1 OMDb max par série).
            if (tv && !m.rating) tvdbIds.push(tv);
            continue;
        }
        if (m.episode) continue; // épisodes : enrichis via leur série
        if (m.imdb && !m.rating) ids.push(m.imdb);
    }
    // Épisodes des dossiers séries ouverts : note/durée/date OMDb par épisode.
    document.querySelectorAll('#library-tree .lib-node').forEach(n => {
        if (!n._tvdb || n._epEnriched || _epEnrichPending.has(n._tvdb)) return;
        const labels = _seriesEpLabels(n);
        if (labels.length) {
            _epEnrichPending.add(n._tvdb);
            series.push({
                tvdbid: n._tvdb,
                imdb: n._imdb || '',
                title: n._meta && n._meta.title ? n._meta.title : '',
                year: n._meta && n._meta.year ? String(n._meta.year) : '',
                episodes: labels,
            });
        }
    });
    if (ids.length || tvdbIds.length || series.length) enqueueLibraryEnrich(ids, tvdbIds, series);
}

function enqueueLibraryEnrich(ids, tvdbIds, series) {
    enrichQueue = enrichQueue.concat(ids || []);
    tvdbEnrichQueue = tvdbEnrichQueue.concat(tvdbIds || []);
    (series || []).forEach(s => epEnrichQueue.push(s));
    flushEnrichQueue();
}

function flushEnrichQueue() {
    if (enrichRunning || (!enrichQueue.length && !tvdbEnrichQueue.length && !epEnrichQueue.length)) return;
    enrichRunning = true;
    const imdbIds = [...new Set(enrichQueue.splice(0, 120))];
    const tvdbIds = [...new Set(tvdbEnrichQueue.splice(0, 120))];
    const series = epEnrichQueue.splice(0, 50);
    postJSON('/api/library/enrich', { imdb_ids: imdbIds, tvdb_ids: tvdbIds })
        .then(d => { if (d.results) applyEnrichResults(d.results); })
        .catch(() => {})
        .finally(() => {
            enrichRunning = false;
            if (enrichQueue.length || tvdbEnrichQueue.length || epEnrichQueue.length) setTimeout(flushEnrichQueue, 300);
        });
    if (series.length) {
        postJSON('/api/library/enrich-episodes', { series })
            .then(d => { if (d.results) applyEpisodeEnrichResults(d.results); })
            .catch(() => {});
    }
}

function applyEnrichResults(results) {
    const tvdbResults = {};
    Object.keys(results).forEach(k => {
        if (k.startsWith('tvdb:')) { tvdbResults[k.slice(5)] = results[k]; }
    });
    // Un seul parcours de l'arbre (au lieu d'un querySelectorAll par identifiant).
    document.querySelectorAll('.lib-node').forEach(n => {
        if (n._imdb && results[n._imdb]) applyEnrichedMeta(n, results[n._imdb]);
        if (n._tvdb && tvdbResults[String(n._tvdb)]) applyEnrichedMeta(n, tvdbResults[String(n._tvdb)]);
    });
    // Vue grille : applique les mêmes résultats à la liste plate.
    if (window.libGridApplyEnrich) window.libGridApplyEnrich(results);
}

function applyEpisodeEnrichResults(results) {
    Object.keys(results).forEach(key => {
        document.querySelectorAll('.lib-node').forEach(node => {
            if (!node._tvdb || String(node._tvdb) !== key) return;
            node._epEnriched = true;
            const childrenEl = node.querySelector(':scope > .lib-children');
            if (!childrenEl) return;
            childrenEl.querySelectorAll(':scope > .lib-node').forEach(epNode => {
                const row = epNode.querySelector(':scope > .lib-row');
                const main = row && row.querySelector('.lib-name-main');
                if (!main) return;
                const m = String(main.textContent || '').match(/[Ss](\d{1,2})[Ee](\d{1,3})/);
                if (!m) return;
                const info = results[`S${m[1]}E${m[2]}`];
                if (!info) return;
                const meta = epNode._meta ? { ...epNode._meta } : {};
                if (info.rating) meta.episode_rating = info.rating;
                if (info.runtime && !meta.episode_runtime) meta.episode_runtime = info.runtime;
                if (info.date) meta.episode_date = info.date.slice(0, 10);
                epNode._meta = meta;
                if (epNode._sort) { epNode._sort.rating = sortRating({ meta }); epNode._sort.year = sortYear({ meta }); }
                const nameEl = epNode.querySelector('.lib-name');
                if (!nameEl) return;
                const newMeta = metaText(meta) ? metaHtml(meta) : '';
                const oldMeta = nameEl.querySelector('.lib-meta');
                if (oldMeta) oldMeta.outerHTML = newMeta;
                else if (newMeta) nameEl.insertAdjacentHTML('beforeend', newMeta);
            });
        });
    });
}

function applyEnrichedMeta(node, en) {
    const m = node._meta || {};
    if (en.imdb) { m.imdb = en.imdb; node._imdb = en.imdb; }
    if (en.tvdb_slug) m.tvdb_slug = en.tvdb_slug;
    if (en.rating) m.rating = en.rating;
    if (en.date) m.date = en.date;
    if (en.genres) m.genres = en.genres;
    if (en.certification) m.certification = en.certification;
    if (en.runtime) m.runtime = en.runtime;
    if (en.poster && !m.poster) m.poster = en.poster;
    node._meta = m;
    if (node._sort) { node._sort.rating = sortRating({ meta: m }); node._sort.year = sortYear({ meta: m }); }
    const nameEl = node.querySelector('.lib-name');
    if (!nameEl) return;
    const newMeta = metaText(m) ? metaHtml(m, { dur_pending: !!node._durPending }) : '';
    const oldMeta = nameEl.querySelector('.lib-meta');
    if (oldMeta) oldMeta.outerHTML = newMeta;
    else if (newMeta) nameEl.insertAdjacentHTML('beforeend', newMeta);
    if (m.genres || m.certification) scheduleCollectFacets();
}
