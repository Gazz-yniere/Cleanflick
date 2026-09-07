// ── Durées en attente (scan rapide) : les fichiers s'affichent d'abord, la
// durée (spinner) est complétée au fur et à mesure qu'elle arrive du serveur.
const _pendingDurations = new Map();   // path -> { node, retry }
let _durFlushTimer = null;
const _DUR_POLL_BATCH = 20;            // nombre max de paths interrogés par tour

function registerPendingDuration(path, node) {
    let p = _pendingDurations.get(path);
    if (p) {
        // Nœud déjà résolu (durée reçue) : un rafraîchissement qui recrée la ligne
        // ne doit pas raviver le spinner pour toujours.
        const resolved = node._meta && (node._meta.runtime || node._meta.episode_runtime || node._meta.episode_date);
        if (node._durPending && !resolved) p.node = node;
    } else {
        _pendingDurations.set(path, { node, retry: 0 });
    }
    if (!_durFlushTimer) _durFlushTimer = setTimeout(flushPendingDurations, 250);
}

function flushPendingDurations() {
    _durFlushTimer = null;
    // Purge les entrées dont le nœud a été retiré de l'arborescence.
    for (const [path, p] of [..._pendingDurations]) {
        if (!p.node.isConnected) _pendingDurations.delete(path);
    }
    if (!_pendingDurations.size) return;
    // Par groupes (dans l'ordre d'affichage) : le serveur calcule au fur et à mesure,
    // on ne sollicite que le lot courant pour que la progression reste visible.
    const paths = [..._pendingDurations.keys()].slice(0, _DUR_POLL_BATCH);
    postJSON('/api/lib-durations', { paths })
        .then(d => {
            const results = d.results || {};
            let remaining = 0;
            for (const [path, p] of _pendingDurations) {
                const dur = results[path];
                if (dur === undefined) { remaining++; p.retry++; continue; }
                const node = p.node;
                if (!node._durPending) { _pendingDurations.delete(path); continue; }
                node._durPending = false;
                if (dur !== null && dur >= 0) {
                    const m = node._meta ? { ...node._meta } : {};
                    if (m.episode) m.episode_runtime = dur + ' min'; else m.runtime = dur + ' min';
                    node._meta = m;
                }
                const nameEl = node.querySelector('.lib-name');
                if (nameEl) {
                    const newMeta = metaText(m) ? metaHtml(m) : '';
                    const oldMeta = nameEl.querySelector('.lib-meta');
                    if (oldMeta) oldMeta.outerHTML = newMeta;
                    else if (newMeta) nameEl.insertAdjacentHTML('beforeend', newMeta);
                }
                _pendingDurations.delete(path);
            }
            if (remaining > 0 && _pendingDurations.size > 0) {
                // Pas encore prête côté serveur (calcul en cours) : nouvelle tentative.
                _durFlushTimer = setTimeout(flushPendingDurations, 1000);
            }
        })
        .catch(() => { _durFlushTimer = setTimeout(flushPendingDurations, 2000); });
}
