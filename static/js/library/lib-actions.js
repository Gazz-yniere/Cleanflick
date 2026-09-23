// ── Actions bibliothèque : suppression, recherche ────────────────────────────
function confirmDeleteFolder(folderPath) {
    const name = folderPath.split('/').pop() || folderPath.split('\\').pop();
    document.getElementById('delete-folder-msg').textContent = `"${name}"`;
    document.getElementById('confirm-delete-folder-btn').onclick = () => {
        closeModal('confirmDeleteFolderModal');
        postJSON('/api/library/delete-folder', { path: folderPath })
            .then(data => {
                if (!data.success) { alert(`✗ ${data.message}`); return; }
                // Le dossier disparaît : on invalide son entrée cache et celle
                // de son parent (la liste des frères a changé).
                invalidateLibTreePath(folderPath);
                invalidateLibTreePath(dirnameLibPath(folderPath));
                loadLibrary();
            })
            .catch(e => alert(`✗ ${e.message}`));
    };
    document.getElementById('confirmDeleteFolderModal').classList.add('active');
}

function libManualSearchFolder(folderPath, folderName) {
    const title = String(folderName || '')
        .replace(/\.[^.]+$/, '')
        .replace(/\s*\[(?:imdb(?:id)?|tvdbid|tmdb(?:id)?)-[^\]]+\]/gi, '')
        .replace(/\s*\(\d{4}\)/, '')
        .replace(/[._]+/g, ' ')
        .replace(/\s+/g, ' ').trim();
    openManualSearchModal(title, { folderPath, fromLibrary: true });
}

function seriesFolderName(fileName) {
    const stem = String(fileName || '').replace(/\.[^.]+$/, '');
    const m = stem.match(/^(.*?)\s+-\s+(?:S\d{2}E\d{2}|\d+x\d{2})\b.*$/i);
    return (m && m[1] && m[1].trim()) ? m[1].trim() : stem;
}

function libRenameFolder(folderPath, result, episodeOrder) {
    const folderBase = folderPath.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
    const payload = {
        path: folderPath,
        tvdbid: String(result.id || ''),
        title: result.title || '',
        year: result.year || '',
        imdb: result.imdb_id || '',
        tmdb: result.tmdb_id || '',
        tvdb_slug: result.tvdb_slug || '',
        poster: result.poster || '',
        genres: result.genres || '',
        certification: result.certification || '',
        episode_order: episodeOrder || '',
        translations: result.translations || {},
    };
    // Étape 1 : dry-run serveur (aucun fichier déplacé) pour lister les
    // modifications prévues dans la popup de confirmation.
    postJSON('/api/library/rename-folder-series', { ...payload, dry_run: true })
        .then(data => {
            if (!data.success) { alert(`✗ ${data.message}`); return; }
            window._pendingSeriesRename = { payload, folderPath, folderBase,
                                            renames: data.renames || [], folder: data.folder };
            const canApply = (data.renames || []).length > 0
                || !!(data.folder && !data.folder.error);
            showLibRenameConfirm(folderBase, data.renames || [], data.folder, canApply);
        })
        .catch(e => alert(`✗ ${e.message}`));
}

function showLibRenameConfirm(folderBase, renames, folderNote, canApply) {
    const title = document.getElementById('libRenameConfirmTitle');
    const body = document.getElementById('libRenameConfirmBody');
    title.textContent = tr('lib_rename_confirm_title') + ' — ' + folderBase;
    const applyBtn = document.getElementById('libRenameApplyBtn');
    if (!canApply) {
        // Rien à faire : popup simple OK/Annuler, pas de bouton Renommer.
        body.innerHTML = `<div class="lib-rename-summary">${esc(tr('lib_rename_no_changes'))}</div>`;
        if (applyBtn) applyBtn.style.display = 'none';
        document.getElementById('libRenameConfirmModal').classList.add('active');
        return;
    }
    if (applyBtn) applyBtn.style.display = '';
    const total = renames.length;
    let html = total
        ? `<div class="lib-rename-summary">${total} fichier(s) seront renommé(s)</div>`
        : '';
    if (folderNote && !folderNote.error) {
        html += `<div class="lib-rename-folder">Dossier : <s>${esc(folderNote.from)}</s> → <strong>${esc(folderNote.to)}</strong></div>`;
    }
    if (renames.length) {
        html += `<div class="lib-rename-list">${renames.map(r =>
            `<div class="lib-rename-item">
                <span class="lib-rename-from" title="${esc(r.from)}">${esc(r.from)}</span>
                <span class="lib-rename-sep"><i class="mdi mdi-arrow-right"></i></span>
                <span class="lib-rename-to" title="${esc(r.to)}">${esc(r.to)}</span>
            </div>`).join('')}</div>`;
    }
    body.innerHTML = html;
    document.getElementById('libRenameConfirmModal').classList.add('active');
}

function confirmLibRename() {
    const pending = window._pendingSeriesRename;
    if (!pending) { closeModal('libRenameConfirmModal'); return; }
    window._pendingSeriesRename = null;
    closeModal('libRenameConfirmModal');
    // Exécution réelle (le parsing TVDB + 2e passe se poursuit en arrière-plan
    // et déclenche un refresh SSE à la fin).
    postJSON('/api/library/rename-folder-series', pending.payload)
        .then(data => {
            if (!data.success) { alert(`✗ ${data.message}`); return; }
            // Le renommage touche le dossier et tous ses fichiers : le cache
            // d'arborescence est périmé, on le vide (il se reconstruit au fil).
            clearLibTreeCache();
            refreshLibrary();
        })
        .catch(e => alert(`✗ ${e.message}`));
}

function libManualSearch(filePath) {
    const filename = filePath.replace(/\\/g, '/').split('/').pop() || '';
    const tvRoot = globalConfig.tv_output_path || '';
    const isTv = (tvRoot && filePath.startsWith(tvRoot)) || /[Ss]\d{2}[Ee]\d{2}/.test(filename);
    const m = filename.match(/[Ss](\d{1,2})[Ee](\d{1,3})/);
    const season = m ? parseInt(m[1], 10) : 1;
    const episode = m ? parseInt(m[2], 10) : 1;
    // Extraire le titre proprement : supprimer extension, année, IDs, titre traduit entre parenthèses
    const cleanedTitle = filename
        .replace(/\.[^.]+$/, '')
        .replace(/\s*\[(?:imdb(?:id)?|tvdbid|tmdb(?:id)?)-[^\]]+\]/gi, '')
        .replace(/\s*-\s*\([^)]+\)\s*$/, '')
        .replace(/\s*\(\d{4}\)/, '')
        .replace(/\s*[Ss]\d{2}[Ee]\d{2}.*/i, '')
        .replace(/\s*\d+x\d{2}.*/i, '')
        .replace(/[\s\-]+$/, '')
        .replace(/\s+/g, ' ').trim();
    const fakeFile = { path: filePath, filename, media_type: isTv ? 'tv' : 'movie',
                       title: cleanedTitle, season, episode };
    if (!allFiles.find(f => f.path === filePath)) allFiles.push(fakeFile);
    openManualSearchModal(cleanedTitle, { filePath, fromLibrary: true });
}

// ── Épisodes manquants / à venir ─────────────────────────────────────────────
// « missing »   : diffusés mais absents du disque (rouge)
// « upcoming »  : non diffusés — TBA ou date de sortie future (jaune)
function loadLibMissing(path, missingBadgeEl, upcomingBadgeEl, childrenEl) {
    fetch(`/api/library/missing?path=${encodeURIComponent(path)}`)
        .then(r => r.json())
        .then(d => {
            // Cache TVDB pas encore prêt (premier affichage, parsing en
            // arrière-plan) : on re-polle quelques secondes plus tard.
            if (!d.available && missingBadgeEl) {
                const tries = (missingBadgeEl._pollTries || 0) + 1;
                missingBadgeEl._pollTries = tries;
                if (tries <= 3) setTimeout(() => loadLibMissing(path, missingBadgeEl, upcomingBadgeEl, childrenEl), 3000);
            }
            const node = missingBadgeEl && missingBadgeEl.closest('.lib-node');
            const missingCount = d.missing_count || 0;
            const upcomingCount = d.upcoming_count || 0;
            if (node) node._missingCount = missingCount;
            if (missingBadgeEl) {
                if (missingCount > 0) {
                    missingBadgeEl.textContent = missingCount;
                    missingBadgeEl.title = `${missingCount} ${tr('lib_missing_episodes')}`;
                } else {
                    missingBadgeEl.textContent = '';
                }
            }
            if (upcomingBadgeEl) {
                if (upcomingCount > 0) {
                    upcomingBadgeEl.textContent = upcomingCount;
                    upcomingBadgeEl.title = `${upcomingCount} ${tr('lib_upcoming_episodes')}`;
                } else {
                    upcomingBadgeEl.textContent = '';
                }
            }
            if (libMissingOnly) applyLibFilter();
            if (childrenEl) {
                // Nettoyage : supprimer les anciens nœuds manquants/à venir avant
                // d'en ajouter, sinon l'actualisation dupliquerait chaque épisode.
                [...childrenEl.querySelectorAll(':scope > .lib-node > .lib-row.lib-row--missing, :scope > .lib-node > .lib-row.lib-row--upcoming')].forEach(n => n.remove());
                const insertEpRow = (m, cls, icon, dateHtml) => {
                    const epNode = document.createElement('div');
                    epNode.className = 'lib-node';
                    const row = document.createElement('div');
                    row.className = `lib-row ${cls}`;
                    row.innerHTML = `
                        <span class="lib-toggle" style="visibility:hidden"><i class="mdi mdi-chevron-right"></i></span>
                        <i class="mdi mdi-file-video lib-icon-file"></i>
                        <span class="lib-name lib-name--file">
                            <span class="lib-name-main">${esc(m.label || `S${String(m.s || 0).padStart(2, '0')}E${String(m.e || 0).padStart(2, '0')}`)}</span>
                            ${m.title ? `<span class="lib-missing-item">${esc(m.title)}</span>` : ''}
                        </span>
                        ${dateHtml}
                        <span class="lib-badge"><i class="mdi ${icon}"></i></span>`;
                    epNode.appendChild(row);
                    const se = (parseInt(m.s, 10) || 0) * 1000 + (parseInt(m.e, 10) || 0);
                    // Tri : comparer avec le DOM en direct (vrais fichiers + épisodes déjà insérés).
                    let inserted = false;
                    for (const n of childrenEl.children) {
                        const main = n.querySelector(':scope > .lib-row .lib-name-main');
                        if (!main) continue;
                        const mm = main.textContent.match(/[Ss](\d{1,2})[Ee](\d{1,3})/);
                        if (!mm) continue;
                        const nse = parseInt(mm[1], 10) * 1000 + parseInt(mm[2], 10);
                        if (nse > se) { childrenEl.insertBefore(epNode, n); inserted = true; break; }
                    }
                    if (!inserted) childrenEl.appendChild(epNode);
                };
                (d.missing || []).forEach(m => insertEpRow(m, 'lib-row--missing', 'mdi-alert',
                    m.airdate ? `<span class="lib-upcoming-date">${esc(m.airdate)}</span>` : ''));
                (d.upcoming || []).forEach(m => insertEpRow(m, 'lib-row--upcoming', 'mdi-calendar-clock',
                    m.airdate ? `<span class="lib-upcoming-date">${esc(m.airdate)}</span>` : ''));
            }
        })
        .catch(() => {
            if (missingBadgeEl) missingBadgeEl.textContent = '';
            if (upcomingBadgeEl) upcomingBadgeEl.textContent = '';
        });
}

function refreshSeriesBadges(container) {
    if (!container) return;
    [...container.querySelectorAll(':scope > .lib-node > .lib-row > .lib-missing-badge')].forEach(badge => {
        if (badge._loaded) return;
        badge._loaded = true;
        const row = badge.closest('.lib-row');
        const upBadge = row && row.querySelector('.lib-upcoming-badge');
        loadLibMissing(badge._path, badge, upBadge, null);
    });
}

function toggleLibMissingFilter() {
    libMissingOnly = !libMissingOnly;
    updateLibFacetButtons();
    if (libMissingOnly) {
        // Forcer le chargement des badges manquants des séries encore inconnues.
        document.querySelectorAll('#library-tree .lib-missing-badge').forEach(badge => {
            const row = badge.closest('.lib-row');
            const node = row && row.closest('.lib-node');
            if (!node || node.style.display === 'none') return;
            if (badge._loaded) return; // compteur déjà connu
            badge._loaded = true;
            const upBadge = row && row.querySelector('.lib-upcoming-badge');
            loadLibMissing(badge._path, badge, upBadge, node.querySelector(':scope > .lib-children'));
        });
    }
    applyLibFilter();
}
