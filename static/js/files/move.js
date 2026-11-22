// ── Move ──────────────────────────────────────────────────────────────────────
let pendingMoveNameMismatch = null;
const pendingMovePaths = new Set();
let moveAllInProgress = false;

function openMoveNameMismatchModal(file, proposedName) {
    const modal = document.getElementById('confirmMoveNameMismatchModal');
    if (!modal) return false;
    const actualName = file?.filename || '';
    document.getElementById('moveCurrentFilename').textContent = actualName;
    document.getElementById('moveProposedFilename').textContent = proposedName || actualName;
    pendingMoveNameMismatch = { filePath: file.path, file, proposedName };
    modal.classList.add('active');
    return true;
}

function confirmMoveNameMismatch() {
    if (!pendingMoveNameMismatch) return;
    closeModal('confirmMoveNameMismatchModal');
    const { filePath, file, proposedName } = pendingMoveNameMismatch;
    pendingMoveNameMismatch = null;
    runMove(filePath, file, proposedName);
}

async function doMove(filePath) {
    // Utiliser le path réel de la row DOM (peut différer d'allFiles après un rename)
    const row = findFileRow(filePath);
    const realPath = row ? decodeURIComponent(row.getAttribute('data-file-path')) : filePath;
    const file = allFiles.find(f => f.path === realPath) || allFiles.find(f => f.path === filePath);
    if (!file?.filename) return;
    const p = filesPreviews[file.path];
    const proposedName = p?.data ? generateFilename(file, p.data.details) : '';
    if (proposedName && proposedName.trim().toLowerCase() !== file.filename.trim().toLowerCase()) {
        if (openMoveNameMismatchModal(file, proposedName)) return;
    }

    await runMove(file.path, file, proposedName || file.filename);
}

async function runMove(filePath, file, proposedName, options = {}) {
    if (pendingMovePaths.has(filePath) || Object.values(activeTransfers).some(t => t.filePath === filePath)) {
        const error = tr('err_move');
        if (!options.silent) alert(error);
        return { success: false, error };
    }

    pendingMovePaths.add(filePath);
    try {
        const body = {
            path: filePath,
            media_type: file.media_type
        };
        if (proposedName && proposedName !== file.filename) body.new_name = proposedName;
        if (file.media_type === 'tv') {
            // Métas série pour que le backend rende le nom du dossier
            // depuis `series_folder_format` (aperçu prioritaire, sinon champs du scan).
            const p = filesPreviews[filePath];
            const det = (p && p.data && p.data.details) || {};
            const tvdbFromName = String(file.filename || '').match(/\[tvdbid-(\d+)\]/i);
            body.series = {
                title: det.title || file.title || '',
                year: det.year || file.year || '',
                tvdbid: String(det.tvdbid || det.id || (tvdbFromName ? tvdbFromName[1] : '') || ''),
            };
            if (det.translations) body.series.translations = det.translations;
            body.series_name = file.title || '';
        }
        const data = await postJSON('/api/move', body);
        if (!data.success) throw new Error(data.message);
        const jobId = data.job_id;
        activeTransfers[jobId] = { filePath, jobId, pollInterval: null, startTime: Date.now(), progress: null };
        pendingMovePaths.delete(filePath);
        renderTable();
        return await trackMoveProgress(jobId, filePath, file, data.new_name || proposedName || file.filename, options);
    } catch (e) {
        pendingMovePaths.delete(filePath);
        if (!options.silent) alert(`✗ ${tr('err_move')}\n${e.message}`);
        return { success: false, error: e.message };
    }
}

function unmarkFileAsTransferring(filePath) {
    const row = findFileRow(filePath);
    if (row) {
        row.classList.remove('transferring');
        row.removeAttribute('data-job-id');
    }
}

function updateTransferRow(filePath, jobId) {
    const row = findFileRow(filePath);
    if (!row) return;
    const transfer = activeTransfers[jobId];
    if (!transfer) return;
    const file = allFiles.find(f => f.path === filePath) || { path: filePath, filename: basename(filePath) };
    const cell = row.querySelector('.transfer-cell');
    if (cell) cell.innerHTML = renderTransferOverlay(file, transfer);
}

function showTransferResult(filePath, isError, msg) {
    const row = findFileRow(filePath);
    if (!row) return;
    const cell = row.querySelector('.transfer-cell');
    if (cell) {
        cell.innerHTML = `<div class="transfer-overlay transfer-overlay--done ${isError ? 'transfer-overlay--error' : ''}">
            <div class="transfer-overlay-title"><i class="mdi mdi-${isError ? 'alert-circle' : 'check-circle'}"></i> ${isError ? esc(msg || tr('transfer_done')) : tr('transfer_done')}</div>
        </div>`;
    }
    if (isError) {
        unmarkFileAsTransferring(filePath);
    }
}

function trackMoveProgress(jobId, filePath, file, newName, options = {}) {
    return new Promise(resolve => {
        let consecutiveErrors = 0;
        let polling = false;
        let pollInterval = null;
        const finish = result => {
            if (pollInterval) clearInterval(pollInterval);
            delete activeTransfers[jobId];
            resolve(result);
        };
        const fail = message => {
            showTransferResult(filePath, true, message || tr('err_move'));
            setTimeout(() => { unmarkFileAsTransferring(filePath); renderTable(); }, 2500);
            finish({ success: false, error: message || tr('err_move') });
        };
        const poll = async () => {
            if (polling) return;
            polling = true;
            try {
                const response = await fetch(`/api/move-progress/${jobId}`);
                const prog = await response.json();
                if (!response.ok) throw new Error(prog.error || `HTTP ${response.status}`);
                consecutiveErrors = 0;

                const transfer = activeTransfers[jobId];
                if (!transfer) {
                    finish({ success: false, error: tr('err_move') });
                    return;
                }
                if (prog.finished) {
                    const verified = prog.verified === true
                        && prog.source_exists === false && prog.target_exists === true;
                    if (prog.error || !verified) {
                        fail(prog.error || tr('err_move'));
                        return;
                    }
                    showTransferResult(filePath, false);
                    setTimeout(() => removeFileFromList(filePath), 900);
                    if (options.refreshLibrary !== false) refreshLibrary();
                    finish({ success: true, newName });
                    return;
                }

                transfer.progress = prog;
                updateTransferRow(filePath, jobId);
            } catch (e) {
                consecutiveErrors += 1;
                if (consecutiveErrors >= 10) {
                    fail(`${tr('err_move')}: ${e.message}`);
                } else {
                    console.error('Progress poll error:', e);
                }
            } finally {
                polling = false;
            }
        };

        pollInterval = setInterval(poll, 800);
        if (activeTransfers[jobId]) activeTransfers[jobId].pollInterval = pollInterval;
    });
}

function removeFileFromList(filePath) {
    // Remove from allFiles array
    const idx = allFiles.findIndex(f => f.path === filePath);
    if (idx >= 0) {
        allFiles.splice(idx, 1);
    }
    
    // Remove row from DOM with animation
    const row = findFileRow(filePath);
    if (row) {
        row.style.opacity = '0';
        row.style.transition = 'opacity 0.3s ease';
        setTimeout(() => row.remove(), 300);
    }
}

async function moveAll() {
    if (moveAllInProgress) return;
    const toMove = allFiles.filter(file => file?.path && file.filename
        && !pendingMovePaths.has(file.path)
        && !Object.values(activeTransfers).some(transfer => transfer.filePath === file.path));
    if (!toMove.length) { alert(tr('err_no_files')); return; }

    moveAllInProgress = true;
    let next = 0;
    let moved = 0;
    const failures = [];
    const worker = async () => {
        while (next < toMove.length) {
            const file = toMove[next++];
            const result = await runMove(file.path, file, file.filename, {
                silent: true,
                refreshLibrary: false
            });
            if (result.success) moved += 1;
            else failures.push({ filename: file.filename, error: result.error });
        }
    };

    try {
        await Promise.all(Array.from({ length: Math.min(2, toMove.length) }, worker));
    } finally {
        moveAllInProgress = false;
    }

    if (moved) refreshLibrary();
    if (failures.length) {
        const details = failures.slice(0, 8)
            .map(item => `${item.filename}: ${item.error}`)
            .join('\n');
        const remainder = failures.length > 8 ? `\n… ${failures.length - 8} autres` : '';
        alert(`${moved} déplacé(s), ${failures.length} échec(s).\n${details}${remainder}`);
    }
}
