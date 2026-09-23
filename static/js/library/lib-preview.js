// ── Aperçu des posters au survol (onglet bibliothèque) ───────────────────────
(function () {
    const DELAY = 120;
    const GAP = 12;
    let box = null, img = null, meta = null;
    let timer = null, current = null;

    function previewMeta(entry) {
        const m = entry && entry.meta;
        if (!m) return '';
        const parts = [];
        if (m.title) parts.push(`<div class="lib-poster-preview-title">${esc(m.title)}</div>`);
        const chips = [];
        if (m.episode) chips.push(`<span class="chip chip-ep">${esc(m.episode)}</span>`);
        const date = m.date || '';
        if (date.length > 4) chips.push(`<span class="chip chip-date">${esc(date.slice(0, 10))}</span>`);
        else if (m.year) chips.push(`<span class="chip chip-date">${esc(String(m.year))}</span>`);
        const rating = m.episode_rating ?? m.rating;
        if (rating != null && rating !== '') chips.push(`<span class="chip chip-rating">★ ${esc(rating)}</span>`);
        if (m.certification) chips.push(`<span class="chip chip-cert">${esc(m.certification)}</span>`);
        if (m.genres) chips.push(`<span class="chip chip-genre">${esc(m.genres)}</span>`);
        if (chips.length) parts.push(`<div class="lib-poster-preview-chips">${chips.join('')}</div>`);
        return parts.join('');
    }

    function show(posterImg) {
        const entry = posterImg.closest('.lib-node')?._entry;
        img.src = posterImg.src;
        meta.innerHTML = previewMeta(entry);
        box.hidden = false;
        const r = posterImg.getBoundingClientRect();
        const bw = box.offsetWidth, bh = box.offsetHeight;
        let left = r.right + GAP;
        if (left + bw > window.innerWidth - 8) left = r.left - GAP - bw;
        if (left < 8) left = 8;
        let top = r.top + (r.height - bh) / 2;
        top = Math.max(8, Math.min(top, window.innerHeight - bh - 8));
        box.style.left = left + 'px';
        box.style.top = top + 'px';
    }

    function hide() {
        if (timer) { clearTimeout(timer); timer = null; }
        if (current) { current = null; box.hidden = true; }
    }

    document.addEventListener('mouseover', (e) => {
        const poster = e.target.closest('.lib-poster.lib-lead');
        if (!poster || poster === current) return;
        if (timer) clearTimeout(timer);
        current = poster;
        timer = setTimeout(() => { timer = null; if (current === poster) show(poster); }, DELAY);
    });
    document.addEventListener('mouseout', (e) => {
        const poster = e.target.closest('.lib-poster.lib-lead');
        if (!poster || poster !== current) return;
        if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.lib-poster-preview')) return;
        hide();
    });
    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);

    document.addEventListener('DOMContentLoaded', () => {
        box = document.getElementById('libPosterPreview');
        img = document.getElementById('libPosterPreviewImg');
        meta = document.getElementById('libPosterPreviewMeta');
    });
})();
