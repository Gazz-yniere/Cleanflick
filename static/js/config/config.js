// ── Config ────────────────────────────────────────────────────────────────────
let globalConfig = { movie_format: '{n} ({y})', tv_format: '{n} - {s00e00} - {t}', series_folder_format: '{n} ({y}) [tvdbid-{tvdbid}]' };
let configSaveTimer = null;
let pendingConfigChanges = {};

function scheduleSaveConfigField(key, value) {
    pendingConfigChanges[key] = value;
    if (configSaveTimer) clearTimeout(configSaveTimer);
    configSaveTimer = setTimeout(() => {
        const cfg = { ...pendingConfigChanges };
        pendingConfigChanges = {};
        saveConfig(cfg);
    }, 500);
}

function saveConfig(cfg) {
    postJSON('/api/config', cfg).then(data => {
        const msg = document.getElementById('config-message');
        if (!msg) return;
        if (data.success) {
            if ('movie_format' in cfg) { localStorage.setItem('cleanflick_movie_format', cfg.movie_format || ''); globalConfig.movie_format = cfg.movie_format; }
            if ('tv_format' in cfg)    { localStorage.setItem('cleanflick_tv_format', cfg.tv_format || '');       globalConfig.tv_format = cfg.tv_format; }
            if ('series_folder_format' in cfg) { localStorage.setItem('cleanflick_series_folder_format', cfg.series_folder_format || ''); globalConfig.series_folder_format = cfg.series_folder_format; }
            if ('show_s00_specials' in cfg) {
                libShowS00 = !!cfg.show_s00_specials;
                const c = document.getElementById('library-tree');
                if (c && c.children.length && !c.querySelector('.loading, .empty-state')) {
                    // Le S00 change les compteurs : on invalide les compteurs
                    // manquants (re-calculés au rechargement des dossiers ouverts).
                    c.querySelectorAll('.lib-node').forEach(n => { n._missingCount = undefined; });
                    refreshLibrary();
                }
            }
            msg.innerHTML = `<div class="message success">✓ Configuration enregistrée</div>`;
            setTimeout(() => { if (msg) msg.innerHTML = ''; }, 2000);
        } else {
            msg.innerHTML = `<div class="message error">✗ ${esc(data.message)}</div>`;
        }
    }).catch(e => {
        const msg = document.getElementById('config-message');
        if (msg) msg.innerHTML = `<div class="message error">✗ Erreur: ${esc(e.message)}</div>`;
    });
}

function loadConfig() {
    fetch('/api/config')
        .then(r => { if (r.status === 401) { window.location='/login'; throw new Error('401'); } return r.json(); })
        .then(data => {
            ['tvdb_api_key','omdb_api_key','movie_format','tv_format','series_folder_format','input_path','movie_output_path','tv_output_path','cache_refresh_days'].forEach(k => setVal(k, data[k] ?? ''));
            const s00 = document.getElementById('show_s00_specials');
            if (s00) s00.checked = data.show_s00_specials === true;
            libShowS00 = data.show_s00_specials === true;
            const mf = localStorage.getItem('cleanflick_movie_format');
            const tf = localStorage.getItem('cleanflick_tv_format');
            const sf = localStorage.getItem('cleanflick_series_folder_format');
            if (mf) setVal('movie_format', mf);
            if (tf) setVal('tv_format', tf);
            if (sf) setVal('series_folder_format', sf);
            globalConfig.movie_format = getVal('movie_format') || globalConfig.movie_format;
            globalConfig.tv_format    = getVal('tv_format')    || globalConfig.tv_format;
            globalConfig.series_folder_format = getVal('series_folder_format') || globalConfig.series_folder_format;
        });
}

function initConfigAutoSave() {
    [
        { id: 'tvdb_api_key',       key: 'tvdb_api_key' },
        { id: 'omdb_api_key',       key: 'omdb_api_key' },
        { id: 'input_path',         key: 'input_path' },
        { id: 'movie_output_path',  key: 'movie_output_path' },
        { id: 'tv_output_path',     key: 'tv_output_path' },
        { id: 'movie_format',       key: 'movie_format' },
        { id: 'tv_format',          key: 'tv_format' },
        { id: 'series_folder_format', key: 'series_folder_format' },
        { id: 'cache_refresh_days', key: 'cache_refresh_days' },
    ].forEach(({ id, key }) => {
        document.getElementById(id)?.addEventListener('input', e => {
            const value = e.target.value.trim();
            if (key === 'movie_format') { localStorage.setItem('cleanflick_movie_format', value); globalConfig.movie_format = value; }
            if (key === 'tv_format')    { localStorage.setItem('cleanflick_tv_format', value);    globalConfig.tv_format = value; }
            if (key === 'series_folder_format') { localStorage.setItem('cleanflick_series_folder_format', value); globalConfig.series_folder_format = value; }
            scheduleSaveConfigField(key, value);
        });
    });
    document.getElementById('show_s00_specials')?.addEventListener('change', e => {
        scheduleSaveConfigField('show_s00_specials', e.target.checked);
    });
}

function testKeys(service) {
    const payload = {};
    const btnIds = { tvdb: 'tvdb_test_btn', omdb: 'omdb_test_btn' };
    const id = btnIds[service];
    const btn = document.getElementById(id);
    if (btn) { btn.classList.remove('valid', 'invalid'); btn.disabled = true; }
    if (service === 'tvdb') payload.tvdb_api_key = getVal('tvdb_api_key');
    if (service === 'omdb') payload.omdb_api_key = getVal('omdb_api_key');
    postJSON('/api/test-keys', payload)
        .then(data => {
            const res = data[service];
            if (btn) { btn.classList.toggle('valid', !!res?.valid); btn.classList.toggle('invalid', !res?.valid); btn.disabled = false; }
        })
        .catch(() => { if (btn) { btn.classList.add('invalid'); btn.disabled = false; } });
}

function loadApiUsage() {
    const el = document.getElementById('api-usage');
    if (!el) return;
    fetch('/api/usage')
        .then(r => r.json())
        .then(d => {
            const u = d.usage || {};
            const t = u.tvdb || {}, o = u.omdb || {};
            el.innerHTML = `
                <div class="usage-item">
                    <span class="usage-name">TVDB</span>
                    <span class="usage-bar"><span class="usage-fill usage-tvdb" style="width:${Math.min(100, ((t.total ?? 0) / 50000) * 100)}%"></span></span>
                    <span class="usage-val">${t.day_count ?? 0} <em>auj.</em> · ${t.total ?? 0}/50000 <em>an</em></span>
                </div>
                <div class="usage-item">
                    <span class="usage-name">OMDb</span>
                    <span class="usage-bar"><span class="usage-fill usage-omdb" style="width:${Math.min(100, ((o.day_count ?? 0) / 1000) * 100)}%"></span></span>
                    <span class="usage-val">${o.day_count ?? 0}/1000 <em>auj.</em> · ${o.total ?? 0} <em>total</em></span>
                </div>`;
        })
        .catch(() => { el.innerHTML = ''; });
}
