// ── Navigation ────────────────────────────────────────────────────────────────
let scanEventSource = null;
let _appInitializing = true;

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
    if (tab === 'files' && !_appInitializing) scanFiles(false);
    if (tab === 'config') loadConfig();
    if (tab === 'history') loadHistory(false, false);
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
    _appInitializing = false;

    if (window.EventSource) {
        try {
            scanEventSource = new EventSource('/api/scan/events');
            scanEventSource.addEventListener('message', (event) => {
                try {
                    const data = JSON.parse(event.data || '{}');
                    if (data.event === 'scan-refresh') {
                        scanFiles();
                    } else if (data.event === 'library-refresh') {
                        if (data.new_path && typeof updateRenamedLibrarySeries === 'function') {
                            updateRenamedLibrarySeries(
                                data.old_path, data.new_path, data.new_name, data.meta, data.tvdbid
                            );
                        } else {
                            if (typeof clearLibTreeCache === 'function') clearLibTreeCache();
                            const c = document.getElementById('library-tree');
                            if (c && c.children.length) refreshLibrary();
                        }
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
            if (cfg.series_folder_format) globalConfig.series_folder_format = cfg.series_folder_format;
            if (cfg.tv_output_path) globalConfig.tv_output_path = cfg.tv_output_path;
            // Initial scan: populate the table but preserve previews if present
            if (document.getElementById('files')?.classList.contains('active') && !_filesScanLoaded) scanFiles(false);
        })
        .catch(e => { if (!e.message.includes('401')) {
            if (document.getElementById('files')?.classList.contains('active') && !_filesScanLoaded) scanFiles(false);
        } });
});
