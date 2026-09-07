// ── Actions bibliothèque : send-back, transferts, suppression, recherche ─────
function libSendBack(filePath) {
    postJSON('/api/library/send-back', { path: filePath })
        .then(data => {
            if (!data.success) { alert(`✗ ${data.message}`); return; }
            const jobId = data.job_id;
            if (!jobId) { removeLibFileRow(filePath); return; }
            activeTransfers[jobId] = { filePath, jobId, pollInterval: null, startTime: Date.now(), progress: null };
            markLibRowTransferring(filePath, jobId);
            trackLibMoveProgress(jobId, filePath);
        })
        .catch(e => alert(`✗ ${e.message}`));
}

function findLibRow(filePath) {
    let found = null;
    document.querySelectorAll('.lib-row--file').forEach(row => {
        if (row.dataset.filePath === filePath) found = row;
    });
    return found;
}

function removeLibFileRow(filePath) {
    const row = findLibRow(filePath);
    if (row) row.closest('.lib-node')?.remove();
}

function markLibRowTransferring(filePath, jobId) {
    const row = findLibRow(filePath);
    if (!row) return;
    row.classList.add('transferring');
    row.querySelectorAll('button').forEach(b => b.setAttribute('disabled', ''));
    let cell = row.querySelector('.transfer-cell');
    if (!cell) {
        cell = document.createElement('div');
        cell.className = 'transfer-cell';
        row.appendChild(cell);
    }
    const file = { path: filePath, filename: basename(filePath) };
    cell.innerHTML = renderTransferOverlay(file, activeTransfers[jobId]);
}

function updateLibTransferRow(filePath, jobId) {
    const row = findLibRow(filePath);
    const cell = row && row.querySelector('.transfer-cell');
    if (!cell) return;
    const transfer = activeTransfers[jobId];
    if (!transfer) return;
    const file = { path: filePath, filename: basename(filePath) };
    cell.innerHTML = renderTransferOverlay(file, transfer);
}

function unmarkLibRowTransferring(filePath) {
    const row = findLibRow(filePath);
    if (!row) return;
    row.classList.remove('transferring');
    row.querySelectorAll('button').forEach(b => b.removeAttribute('disabled'));
    row.querySelector('.transfer-cell')?.remove();
}

function trackLibMoveProgress(jobId, filePath) {
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/move-progress/${jobId}`);
            const prog = await response.json();
            const transfer = activeTransfers[jobId];
            if (!transfer) { clearInterval(pollInterval); return; }

            if (prog.finished) {
                clearInterval(pollInterval);
                delete activeTransfers[jobId];
                if (prog.error) {
                    const row = findLibRow(filePath);
                    const cell = row && row.querySelector('.transfer-cell');
                    if (cell) cell.innerHTML = `<div class="transfer-overlay transfer-overlay--done transfer-overlay--error">
                        <div class="transfer-overlay-title"><i class="mdi mdi-alert-circle"></i> ${esc(prog.error || tr('transfer_done'))}</div>
                    </div>`;
                    setTimeout(() => unmarkLibRowTransferring(filePath), 2500);
                    return;
                }
                removeLibFileRow(filePath);
                return;
            }

            transfer.progress = prog;
            updateLibTransferRow(filePath, jobId);
        } catch (e) {
            console.error('Lib progress poll error:', e);
        }
    }, 400);
    if (activeTransfers[jobId]) activeTransfers[jobId].pollInterval = pollInterval;
}

function confirmDeleteFolder(folderPath) {
    const name = folderPath.split('/').pop() || folderPath.split('\\').pop();
    document.getElementById('delete-folder-msg').textContent = `"${name}"`;
    document.getElementById('confirm-delete-folder-btn').onclick = () => {
        closeModal('confirmDeleteFolderModal');
        postJSON('/api/library/delete-folder', { path: folderPath })
            .then(data => {
                if (!data.success) { alert(`✗ ${data.message}`); return; }
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

function libRenameFolder(folderPath, result) {
    const folderBase = folderPath.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
    // Le renommage (fichiers + dossier) est synchrone côté serveur ; le parsing
    // TVDB (titres d'épisodes, épisodes manquants, enrichissement OMDb) se
    // poursuit en arrière-plan et déclenche un refresh SSE à la fin.
    postJSON('/api/library/rename-folder-series', {
        path: folderPath,
        tvdbid: String(result.id || ''),
        title: result.title || '',
        year: result.year || '',
        imdb: result.imdb_id || '',
        tmdb: result.tmdb_id || '',
        translations: result.translations || {},
    }).then(data => {
        if (!data.success) { alert(`✗ ${data.message}`); return; }
        refreshLibrary();
        showLibRenameResult(folderBase, data.renames || [], null, data.folder);
    }).catch(e => alert(`✗ ${e.message}`));
}

function showLibRenameResult(folderBase, renames, skipped, folderNote) {
    const title = document.getElementById('libRenameResultTitle');
    const body = document.getElementById('libRenameResultBody');
    title.innerHTML = esc(folderBase);
    const total = renames.length;
    let html = `<div class="lib-rename-summary">${total} fichier(s) renommé(s)${skipped ? ` · ${skipped} ignoré(s)` : ''}</div>`;
    if (folderNote) {
        html += folderNote.error
            ? `<div class="lib-rename-folder lib-rename-folder--error">Dossier : <s>${esc(folderNote.from)}</s> → <strong>${esc(folderNote.to)}</strong> (échec, à renommer manuellement)</div>`
            : `<div class="lib-rename-folder">Dossier renommé : <s>${esc(folderNote.from)}</s> → <strong>${esc(folderNote.to)}</strong></div>`;
    }
    if (renames.length) {
        html += `<div class="lib-rename-list">${renames.map(r =>
            `<div class="lib-rename-item">
                <div class="lib-rename-from">${esc(r.from)}</div>
                <div class="lib-rename-sep"><i class="mdi mdi-arrow-down"></i></div>
                <div class="lib-rename-to">${esc(r.to)}</div>
            </div>`).join('')}</div>`;
    }
    if (!renames.length && !folderNote) {
        html = `<div class="lib-rename-summary">Aucun fichier renommé.</div>`;
    }
    body.innerHTML = html;
    document.getElementById('libRenameResultModal').classList.add('active');
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

// ── Épisodes manquants ───────────────────────────────────────────────────────
function loadLibMissing(path, badgeEl, childrenEl) {
    fetch(`/api/library/missing?path=${encodeURIComponent(path)}`)
        .then(r => r.json())
        .then(d => {
            const node = badgeEl && badgeEl.closest('.lib-node');
            if (node) node._missingCount = d.count || 0;
            if (badgeEl) {
                if (d.count > 0) {
                    badgeEl.textContent = d.count;
                    badgeEl.title = `${d.count} ${tr('lib_missing_episodes')}`;
                } else {
                    badgeEl.textContent = '';
                }
            }
            if (libMissingOnly) applyLibFilter();
            if (childrenEl && d.missing && d.missing.length) {
                // Nettoyage : supprimer les anciens nœuds "manquants" avant d'en ajouter,
                // sinon l'actualisation dupliquerait chaque épisode manquant.
                [...childrenEl.querySelectorAll(':scope > .lib-node > .lib-row.lib-row--missing')].forEach(n => n.remove());
                d.missing.forEach(m => {
                    const node = document.createElement('div');
                    node.className = 'lib-node';
                    const row = document.createElement('div');
                    row.className = 'lib-row lib-row--missing';
                    row.innerHTML = `
                        <span class="lib-toggle" style="visibility:hidden"><i class="mdi mdi-chevron-right"></i></span>
                        <i class="mdi mdi-file-video lib-icon-file"></i>
                        <span class="lib-name lib-name--file">
                            <span class="lib-name-main">${esc(m.label)}</span>
                            ${m.title ? `<span class="lib-missing-item">${esc(m.title)}</span>` : ''}
                        </span>
                        <span class="lib-badge lib-badge--warn"><i class="mdi mdi-alert"></i></span>`;
                    node.appendChild(row);
                    const se = (parseInt(m.s, 10) || 0) * 1000 + (parseInt(m.e, 10) || 0);
                    // Tri : comparer avec le DOM en direct (vrais fichiers + manquants déjà insérés).
                    let inserted = false;
                    for (const n of childrenEl.children) {
                        const main = n.querySelector(':scope > .lib-row .lib-name-main');
                        if (!main) continue;
                        const mm = main.textContent.match(/[Ss](\d{1,2})[Ee](\d{1,3})/);
                        if (!mm) continue;
                        const nse = parseInt(mm[1], 10) * 1000 + parseInt(mm[2], 10);
                        if (nse > se) { childrenEl.insertBefore(node, n); inserted = true; break; }
                    }
                    if (!inserted) childrenEl.appendChild(node);
                });
            }
        })
        .catch(() => { if (badgeEl) badgeEl.textContent = ''; });
}

function refreshSeriesBadges(container) {
    if (!container) return;
    [...container.querySelectorAll(':scope > .lib-node > .lib-row > .lib-missing-badge')].forEach(badge => {
        if (badge._loaded) return;
        badge._loaded = true;
        loadLibMissing(badge._path, badge, null);
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
            loadLibMissing(badge._path, badge, node.querySelector(':scope > .lib-children'));
        });
    }
    applyLibFilter();
}
