// ── Slider double poignée (2 <input type=range> superposés) ─────────────────
function createDualSlider(host, cfg) {
    host.innerHTML = `
        <div class="dual-slider__values">
            <span class="dual-slider__val dual-slider__val--lo"></span>
            <span class="dual-slider__val dual-slider__val--hi"></span>
        </div>
        <div class="dual-slider__track">
            <div class="dual-slider__fill"></div>
            <input type="range" class="dual-slider__range dual-slider__range--lo">
            <input type="range" class="dual-slider__range dual-slider__range--hi">
        </div>
        <div class="dual-slider__ticks"></div>`;
    const lo = host.querySelector('.dual-slider__range--lo');
    const hi = host.querySelector('.dual-slider__range--hi');
    const fill = host.querySelector('.dual-slider__fill');
    const valLo = host.querySelector('.dual-slider__val--lo');
    const valHi = host.querySelector('.dual-slider__val--hi');
    const step = cfg.step || 1;
    const pct = v => ((v - cfg.min) / (cfg.max - cfg.min)) * 100;
    function sync() {
        const l = Math.min(lo.valueAsNumber, hi.valueAsNumber);
        const h = Math.max(lo.valueAsNumber, hi.valueAsNumber);
        fill.style.left = pct(l) + '%';
        fill.style.width = (pct(h) - pct(l)) + '%';
        valLo.textContent = cfg.format(l);
        valHi.textContent = cfg.format(h);
    }
    function onChange() {
        sync();
        const l = Math.min(lo.valueAsNumber, hi.valueAsNumber);
        const h = Math.max(lo.valueAsNumber, hi.valueAsNumber);
        cfg.onChange(l, h);
    }
    lo.min = hi.min = cfg.min;
    lo.max = hi.max = cfg.max;
    lo.step = hi.step = step;
    lo.value = cfg.lo;
    hi.value = cfg.hi;
    lo.addEventListener('input', onChange);
    hi.addEventListener('input', onChange);
    if (cfg.ticks) host.querySelector('.dual-slider__ticks').innerHTML = cfg.ticks.map(t => `<span>${esc(t)}</span>`).join('');
    sync();
    return {
        set(loV, hiV) {
            lo.value = loV;
            hi.value = hiV;
            sync();
            cfg.onChange(Math.min(loV, hiV), Math.max(loV, hiV));
        },
    };
}

// ── Slider tranche d'âge : 6 positions, 1 position = 1 tranche (step = 1). ──
const AGE_SLIDER_TICKS = ['Tous', '7+', '10+', '13+', '16+', '18+'];
let libAgeSliderCtl = null;
const libAgeSliderCfg = {
    min: 0, max: 5, lo: 0, hi: 5, step: 1,
    ticks: AGE_SLIDER_TICKS,
    format: v => AGE_SLIDER_TICKS[Math.min(5, Math.max(0, Math.round(v)))] || '',
    onChange: (l, h) => {
        const a = Math.round(l), b = Math.round(h);
        libAgeRange = (a === 0 && b === 5) ? null : [a, b];
        updateLibFacetButtons();
        applyLibFilter();
    },
};
function libAgeSliderSync() {
    const [a, b] = libAgeRange || [0, 5];
    if (libAgeSliderCtl) libAgeSliderCtl.set(a, b);
}
function libAgeReset() {
    libAgeRange = null;
    libAgeSliderSync();
}

// ── Slider note : 0–10, pas 0.5 ─────────────────────────────────────────────
let libRatingSliderCtl = null;
const libRatingSliderCfg = {
    min: 0, max: 10, lo: 0, hi: 10, step: 0.5,
    format: v => (Math.round(v * 2) / 2).toFixed(1).replace('.', ','),
    onChange: (l, h) => {
        libRatingRange = (l === 0 && h === 10) ? null : [l, h];
        updateLibFacetButtons();
        applyLibFilter();
    },
};
function libRatingSliderSync() {
    const [l, h] = libRatingRange || [0, 10];
    if (libRatingSliderCtl) libRatingSliderCtl.set(l, h);
}
function libRatingReset() {
    libRatingRange = null;
    libRatingSliderSync();
}

// ── Slider taille : domaine [min, max] des tailles chargées, échelle log,
//    ~100 positions (step calculé sur le domaine log). ───────────────────────
function libSizeShowEmpty() {
    const host = document.getElementById('lib-size-slider');
    if (host) host.innerHTML = `<div class="lib-slider-empty">${tr('lib_filter_no_range')}</div>`;
}
function libSizeRebuild() {
    const host = document.getElementById('lib-size-slider');
    if (!host) return;
    const sizes = [];
    document.querySelectorAll('#library-tree .lib-node').forEach(n => {
        const s = n._sort && n._sort.size;
        // Fichiers uniquement : les tailles de dossiers/racines sont récursives et
        // faussent le domaine (ex. racine = toute la bibliothèque).
        if (n._sort && !n._sort.is_dir && typeof s === 'number' && s > 0) sizes.push(s);
    });
    if (!sizes.length) { libSizeShowEmpty(); return; }
    sizes.sort((a, b) => a - b);
    const lo = sizes[0];
    const hi = sizes[sizes.length - 1];
    const loL = Math.max(0, Math.log10(lo));
    const hiL = Math.log10(Math.max(hi, lo * 10));
    const span = hiL - loL;
    const step = span / 100;
    const snap = v => Math.min(hiL, Math.max(loL, loL + Math.round((v - loL) / step) * step));
    const cur = libSizeRange || [lo, hi];
    createDualSlider(host, {
        min: loL, max: hiL, step,
        lo: snap(Math.log10(Math.max(cur[0], 1))),
        hi: snap(Math.log10(Math.max(cur[1], 1))),
        format: v => formatBytes(Math.round(Math.pow(10, v))),
        onChange: (l, h) => {
            const minB = Math.round(Math.pow(10, l));
            const maxB = Math.round(Math.pow(10, h));
            libSizeRange = (minB <= lo && maxB >= hi) ? null : [minB, maxB];
            updateLibFacetButtons();
            applyLibFilter();
        },
    });
}
function libSizeReset() {
    libSizeRange = null;
    libSizeRebuild();
}

// ── Slider date : domaine [anneeMin, anneeMax] des années de sortie connues. ─
function libDateRebuild() {
    const host = document.getElementById('lib-date-slider');
    if (!host) return;
    const years = [];
    document.querySelectorAll('#library-tree .lib-node').forEach(n => {
        const y = n._sort && n._sort.year;
        if (typeof y === 'number' && y > 0) years.push(y);
    });
    if (!years.length) {
        host.innerHTML = `<div class="lib-slider-empty">${tr('lib_filter_no_range')}</div>`;
        return;
    }
    years.sort((a, b) => a - b);
    const lo = years[0];
    const hi = years[years.length - 1];
    const cur = libDateRange || [lo, hi];
    createDualSlider(host, {
        min: lo, max: hi, step: 1,
        lo: Math.min(Math.max(cur[0], lo), hi),
        hi: Math.min(Math.max(cur[1], lo), hi),
        format: v => String(Math.round(v)),
        onChange: (l, h) => {
            const a = Math.round(l), b = Math.round(h);
            libDateRange = (a <= lo && b >= hi) ? null : [a, b];
            updateLibFacetButtons();
            applyLibFilter();
        },
    });
}
function libDateReset() {
    libDateRange = null;
    libDateRebuild();
}

function initLibSliders() {
    const ageHost = document.getElementById('lib-age-slider');
    if (ageHost && !libAgeSliderCtl) libAgeSliderCtl = createDualSlider(ageHost, libAgeSliderCfg);
    const ratingHost = document.getElementById('lib-rating-slider');
    if (ratingHost && !libRatingSliderCtl) libRatingSliderCtl = createDualSlider(ratingHost, libRatingSliderCfg);
    libAgeSliderSync();
    libRatingSliderSync();
    libSizeRebuild();
    libDateRebuild();
}
