// ── Navigation ────────────────────────────────────────────────────────────────
let scanEventSource = null;

function switchTab(tab, e) {
    if (e) e.preventDefault();
    if (Object.keys(pendingConfigChanges).length > 0) {
        const cfg = { ...pendingConfigChanges };
        pendingConfigChanges = {};
        if (configSaveTimer) clearTimeout(configSaveTimer);
        saveConfig(cfg);
    }
    document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
    document.getElementById(tab)?.classList.add('active');
    if (e?.target) e.target.closest('.nav-link').classList.add('active');
    try { localStorage.setItem(LIB_TAB_KEY, tab); } catch (err) {}
    if (tab === 'files') scanFiles();
    if (tab === 'config') loadConfig();
    if (tab === 'history') loadHistory();
    if (tab === 'library') {
        const c = document.getElementById('library-tree');
        if (!c || !c.children.length || c.querySelector('.loading, .empty-state')) loadLibrary();
        // sinon : on garde l'état déjà affiché (chargement lazy conservé), pas de rechargement.
    }
}

// ── Init ──────────────────────────────────────────────────────────────────────
// No periodic auto-scan is enabled. A new file discovery on disk is not observable
// from the browser runtime itself, so the page is initialized with a one-shot scan only.

document.addEventListener('DOMContentLoaded', () => {
    if (typeof applyTranslations === 'function') applyTranslations();
    initConfigAutoSave();
    loadApiUsage();
    initLibSliders();

    const savedTab = localStorage.getItem(LIB_TAB_KEY);
    if (savedTab && ['files', 'library', 'history', 'config'].includes(savedTab)) {
        switchTab(savedTab);
    }

    if (window.EventSource) {
        try {
            scanEventSource = new EventSource('/api/scan/events');
            scanEventSource.addEventListener('message', (event) => {
                try {
                    const data = JSON.parse(event.data || '{}');
                    if (data.event === 'scan-refresh') {
                        scanFiles();
                    } else if (data.event === 'library-refresh') {
                        // Renommage de dossier série terminé (parsing TVDB en
                        // arrière-plan achevé) : les fichiers ont pu être renommés
                        // une 2e fois → le cache d'arborescence est périmé.
                        if (typeof clearLibTreeCache === 'function') clearLibTreeCache();
                        const c = document.getElementById('library-tree');
                        if (c && c.children.length) refreshLibrary();
                    } else if (data.event === 'usage-refresh') {
                        loadApiUsage();
                    }
                } catch (e) {
                    console.warn('Bad scan refresh payload', e);
                }
            });
            scanEventSource.onerror = () => {
                console.warn('Scan refresh stream disconnected');
            };
        } catch (e) {
            console.warn('Scan refresh SSE unavailable', e);
        }
    }

    fetch('/api/config')
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } return r.json(); })
        .then(cfg => {
            if (cfg.movie_format) globalConfig.movie_format = cfg.movie_format;
            if (cfg.tv_format)    globalConfig.tv_format    = cfg.tv_format;
            if (cfg.tv_output_path) globalConfig.tv_output_path = cfg.tv_output_path;
            // Initial scan: populate the table but preserve previews if present
            scanFiles();
        })
        .catch(e => { if (!e.message.includes('401')) {
            scanFiles();
        } });
});
