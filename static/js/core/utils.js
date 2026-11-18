// ── i18n ──────────────────────────────────────────────────────────────────────
function tr(key) {
    if (typeof TRANSLATIONS !== 'undefined' && typeof currentLang !== 'undefined')
        return TRANSLATIONS[currentLang]?.[key] || TRANSLATIONS['fr']?.[key] || key;
    return key;
}

// ── Lang map (mirrors Python) ─────────────────────────────────────────────────
// TVDB renvoie les traductions avec des codes ISO 639-2/T à 3 lettres (eng, fra...).
const LANG_MAP = {
    en:'eng', fr:'fra', de:'deu', es:'spa', it:'ita', pt:'por', ru:'rus',
    ja:'jpn', ko:'kor', zh:'zho', ar:'ara', pl:'pol', nl:'nld',
    sv:'swe', no:'nor', da:'dan', fi:'fin', tr:'tur', cs:'ces',
    hu:'hun', he:'heb', ro:'ron', uk:'ukr', el:'ell', et:'est', hr:'hrv', lt:'lit', sr:'srp'
};
const LANG_MAP_INV = Object.fromEntries(Object.entries(LANG_MAP).map(([k, v]) => [v, k]));

function translateLang(trans, code, fallback) {
    code = String(code || '').toLowerCase();
    if (trans[code]) return trans[code];
    if (LANG_MAP[code] && trans[LANG_MAP[code]]) return trans[LANG_MAP[code]];
    if (LANG_MAP_INV[code] && trans[LANG_MAP_INV[code]]) return trans[LANG_MAP_INV[code]];
    return fallback || '';
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function pathKey(path) { return encodeURIComponent(path); }
function findFileRow(filePath) { return document.querySelector(`.file-row[data-file-path="${pathKey(filePath)}"]`); }
function getVal(id) { return document.getElementById(id)?.value || ''; }
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v; }

function postJSON(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(async r => {
            let data;
            try { data = await r.json(); } catch { data = null; }
            if (!r.ok) throw new Error(data?.message || data?.error || `HTTP ${r.status}`);
            if (data === null) throw new Error(`Réponse invalide du serveur (HTTP ${r.status})`);
            return data;
        });
}

function basename(p) {
    const parts = String(p || '').split(/[\\/]/);
    return parts[parts.length - 1] || '';
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

function formatSeconds(seconds) {
    if (seconds < 60) return Math.round(seconds) + 's';
    if (seconds < 3600) return Math.round(seconds / 60) + 'm';
    return Math.round(seconds / 3600) + 'h';
}
