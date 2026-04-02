const TRANSLATIONS = {
    fr: {
        nav_files: 'Fichiers',
        nav_config: 'Configuration',
        nav_logout: 'Déconnexion',
        filter_all: 'Tous',
        filter_movies: 'Films',
        filter_tv: 'Séries',
        btn_scan: 'Scanner',
        btn_rename_all: 'Tout renommer',
        th_file: 'Fichier',
        th_preview: 'Aperçu Renommage',
        th_actions: 'Actions',
        scanning: 'Scan en cours...',
        searching: 'Recherche...',
        no_files: 'Cliquez sur "Scanner" pour charger les fichiers',
        no_result: 'Aucun résultat',
        no_files_found: 'Aucun fichier trouvé',
        btn_rename: 'Renommer',
        btn_revert: 'Revert',
        btn_other: 'Rechercher',
        btn_ok: 'OK',
        cfg_tvdb_key: 'API Key TVDB v4',
        cfg_tvdb_pin: 'PIN TVDB (optionnel)',
        cfg_test_key: 'Tester la clé',
        cfg_movie_path: 'Dossier Films',
        cfg_tv_path: 'Dossier Séries',
        cfg_movie_path_hint: 'Chemin absolu vers le dossier contenant les films',
        cfg_tv_path_hint: 'Chemin absolu vers le dossier contenant les séries',
        cfg_password: 'Mot de passe',
        cfg_password_hint: 'Laisser vide pour désactiver la protection',
        cfg_movie_format: 'Format Films',
        cfg_tv_format: 'Format Séries',
        cfg_save: 'Sauvegarder',
        cfg_reload: 'Recharger',
        cfg_vars_title: 'Référence des variables',
        cfg_tvdb_section: 'Clé API TVDB',
        cfg_paths_section: 'Chemins des médias',
        cfg_security_section: 'Sécurité',
        cfg_formats_section: 'Formats de renommage',
        search_title: 'Recherche manuelle',
        search_label: 'Titre à rechercher :',
        search_btn: 'Chercher',
        search_results: 'résultat(s)',
        search_none: 'Aucun résultat trouvé',
        picker_title: 'Choisir un dossier',
        picker_select: 'Choisir ce dossier',
        picker_cancel: 'Annuler',
        picker_empty: 'Dossier vide',
        picker_root: 'Racine',
        err_scan: 'Erreur :',
        err_rename: 'Erreur renommage :',
        err_no_files: 'Aucun fichier prêt',
        var_title_section: 'Titre',
        var_date_section: 'Date',
        var_tv_section: 'Numérotation Séries',
        var_meta_section: 'Métadonnées',
        var_ids_section: 'Identifiants externes',
        var_trans_section: 'Traductions',
    },
    en: {
        nav_files: 'Files',
        nav_config: 'Settings',
        nav_logout: 'Logout',
        filter_all: 'All',
        filter_movies: 'Movies',
        filter_tv: 'TV Shows',
        btn_scan: 'Scan',
        btn_rename_all: 'Rename All',
        th_file: 'File',
        th_preview: 'Rename Preview',
        th_actions: 'Actions',
        scanning: 'Scanning...',
        searching: 'Searching...',
        no_files: 'Click "Scan" to load files',
        no_result: 'No result',
        no_files_found: 'No files found',
        btn_rename: 'Rename',
        btn_revert: 'Revert',
        btn_other: 'Search',
        btn_ok: 'OK',
        cfg_tvdb_key: 'TVDB v4 API Key',
        cfg_tvdb_pin: 'TVDB PIN (optional)',
        cfg_test_key: 'Test key',
        cfg_movie_path: 'Movies Folder',
        cfg_tv_path: 'TV Shows Folder',
        cfg_movie_path_hint: 'Absolute path to the movies folder',
        cfg_tv_path_hint: 'Absolute path to the TV shows folder',
        cfg_password: 'Password',
        cfg_password_hint: 'Leave empty to disable protection',
        cfg_movie_format: 'Movie Format',
        cfg_tv_format: 'TV Format',
        cfg_save: 'Save',
        cfg_reload: 'Reload',
        cfg_vars_title: 'Variables Reference',
        cfg_tvdb_section: 'TVDB API Key',
        cfg_paths_section: 'Media Paths',
        cfg_security_section: 'Security',
        cfg_formats_section: 'Rename Formats',
        search_title: 'Manual Search',
        search_label: 'Search title:',
        search_btn: 'Search',
        search_results: 'result(s)',
        search_none: 'No results found',
        picker_title: 'Choose a folder',
        picker_select: 'Select this folder',
        picker_cancel: 'Cancel',
        picker_empty: 'Empty folder',
        picker_root: 'Root',
        err_scan: 'Error:',
        err_rename: 'Rename error:',
        err_no_files: 'No files ready',
        var_title_section: 'Title',
        var_date_section: 'Date',
        var_tv_section: 'TV Numbering',
        var_meta_section: 'Metadata',
        var_ids_section: 'External IDs',
        var_trans_section: 'Translations',
    }
};

let currentLang = localStorage.getItem('cleanflick_lang') || 'fr';

function t(key) {
    return TRANSLATIONS[currentLang]?.[key] || TRANSLATIONS['fr'][key] || key;
}

function setLang(lang) {
    currentLang = lang;
    localStorage.setItem('cleanflick_lang', lang);
    applyTranslations();
    // Re-rendre les boutons du tableau
    if (typeof allFiles !== 'undefined' && allFiles.length > 0) {
        document.querySelectorAll('tr[data-idx]').forEach(row => {
            const idx = parseInt(row.getAttribute('data-idx'));
            const file = allFiles[idx];
            if (!file) return;
            const cell = row.querySelector('.actions-cell');
            if (cell && typeof renderActions === 'function')
                cell.innerHTML = renderActions(file, idx);
        });
    }
}

function applyTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        const text = t(key);
        const icon = el.querySelector('i.mdi');

        if (icon) {
            // Supprimer tous les text nodes et spans i18n existants
            [...el.childNodes].forEach(node => {
                if (node.nodeType === Node.TEXT_NODE) node.remove();
            });
            const old = el.querySelector('.i18n-text');
            if (old) old.remove();
            // Ajouter le texte dans un span dédié après l'icône
            const span = document.createElement('span');
            span.className = 'i18n-text';
            span.textContent = text;
            el.appendChild(span);
        } else {
            el.textContent = text;
        }
    });

    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
    });

    const btn = document.getElementById('lang-switch');
    if (btn) btn.textContent = currentLang === 'fr' ? '🇬🇧 EN' : '🇫🇷 FR';
}
