// ── Move ──────────────────────────────────────────────────────────────────────
let pendingMoveNameMismatch = null;

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
    if (!p?.data) return;

    const proposedName = generateFilename(file, p.data.details);
    if (proposedName && file.filename && proposedName.trim().toLowerCase() !== file.filename.trim().toLowerCase()) {
        openMoveNameMismatchModal(file, proposedName);
        return;
    }

    await runMove(file.path, file, proposedName || file.filename);
}

async function runMove(filePath, file, proposedName) {
    try {
        const data = await postJSON('/api/move', {
            path: file.path,
            media_type: file.media_type || 'movie'
        });
        if (!data.success) throw new Error(data.message);
        const jobId = data.job_id;
        activeTransfers[jobId] = { filePath, jobId, pollInterval: null, startTime: Date.now(), progress: null };
        renderTable();
        trackMoveProgress(jobId, filePath, file, data.new_name || proposedName || file.filename);
    } catch (e) {
        alert(`✗ ${tr('err_move')}\n${e.message}`);
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

function trackMoveProgress(jobId, filePath, file, newName) {
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
                    showTransferResult(filePath, true, prog.error);
                    setTimeout(() => { unmarkFileAsTransferring(filePath); renderTable(); }, 2500);
                    return;
                }
                showTransferResult(filePath, false);
                setTimeout(() => removeFileFromList(filePath), 900);
                refreshLibrary();
                return;
            }

            transfer.progress = prog;
            updateTransferRow(filePath, jobId);
        } catch (e) {
            console.error('Progress poll error:', e);
        }
    }, 400);

    if (activeTransfers[jobId]) {
        activeTransfers[jobId].pollInterval = pollInterval;
    }
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

function moveAll() {
    const toMove = allFiles.filter(f => filesPreviews[f.path]?.data);
    if (!toMove.length) { alert(tr('err_no_files')); return; }
    toMove.forEach(file => doMove(file.path));
}
