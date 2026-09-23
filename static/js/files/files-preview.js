// ── Aperçu des posters au survol (onglet fichiers) ───────────────────────────
(function () {
    const DELAY = 120;
    const GAP = 12;
    let box = null, img = null, meta = null;
    let timer = null, current = null;

    function previewMeta(preview, file) {
        if (!preview || !preview.data) return '';
        const { source, details } = preview.data;
        const od = source.omdb || {};
        const eo = details.episode_omdb || {};
        const parts = [];
        if (details.title) parts.push(`<div class="files-poster-preview-title">${esc(details.title)}${details.year ? ` <span class="files-poster-preview-year">${esc(String(details.year))}</span>` : ''}</div>`);
        const chips = [];
        const isTv = file && file.media_type !== 'movie';
        if (isTv && file.season && file.episode)
            chips.push(`<span class="chip chip-ep">S${String(file.season).padStart(2, '0')}E${String(file.episode).padStart(2, '0')}</span>`);
        if (eo.imdbRating && eo.imdbRating !== 'N/A') chips.push(`<span class="chip chip-eprating">ép. ★ ${esc(eo.imdbRating)}</span>`);
        if (od.imdbRating) chips.push(`<span class="chip chip-rating">★ ${esc(od.imdbRating)}</span>`);
        if (od.Rated && od.Rated !== 'N/A') chips.push(`<span class="chip chip-cert">${esc(od.Rated)}</span>`);
        const dur = file && file.duration ? `${file.duration} min`
            : (isTv && eo.Runtime && eo.Runtime !== 'N/A' ? `ép. ${eo.Runtime}`
            : (od.Runtime && od.Runtime !== 'N/A' ? od.Runtime : ''));
        if (dur) chips.push(`<span class="chip chip-runtime">${esc(dur)}</span>`);
        if (od.Genre && od.Genre !== 'N/A') chips.push(`<span class="chip chip-genre">${esc(od.Genre)}</span>`);
        if (chips.length) parts.push(`<div class="files-poster-preview-chips">${chips.join('')}</div>`);
        return parts.join('');
    }

    function show(posterImg) {
        const row = posterImg.closest('.file-row');
        const p = row ? filesPreviews[decodeURIComponent(row.dataset.filePath)] : null;
        const file = row ? allFiles.find(f => f.path === decodeURIComponent(row.dataset.filePath)) : null;
        img.src = posterImg.src;
        meta.innerHTML = previewMeta(p, file);
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
        const poster = e.target.closest('.preview-poster img');
        if (!poster || poster === current) return;
        if (timer) clearTimeout(timer);
        current = poster;
        timer = setTimeout(() => { timer = null; if (current === poster) show(poster); }, DELAY);
    });
    document.addEventListener('mouseout', (e) => {
        const poster = e.target.closest('.preview-poster img');
        if (!poster || poster !== current) return;
        if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.files-poster-preview')) return;
        hide();
    });
    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);

    document.addEventListener('DOMContentLoaded', () => {
        box = document.getElementById('filesPosterPreview');
        img = document.getElementById('filesPosterPreviewImg');
        meta = document.getElementById('filesPosterPreviewMeta');
    });
})();
