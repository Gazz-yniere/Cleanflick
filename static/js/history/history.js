// ── History ───────────────────────────────────────────────────────────────────
function loadHistory() {
    const tbody = document.getElementById('history-tbody');
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:20px;color:#e67e22;">◌ ${tr('searching')}</td></tr>`;
    fetch('/api/history').then(r => r.json()).then(entries => {
        window._historyEntries = entries;
        if (!entries.length) {
            tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${tr('hist_empty')}</td></tr>`;
            return;
        }
        tbody.innerHTML = entries.map((e, i) => {
            const opLabel = { rename: tr('hist_op_rename'), move: tr('hist_op_move'), revert: tr('hist_op_revert') }[e.op] || e.op;
            const opClass = { rename: 'op-rename', move: 'op-move', revert: 'op-revert' }[e.op] || 'op-rename';

            let actionHtml = '';
            const rs = e.revert_status;
            if (rs === 'available') {
                actionHtml = `<button class="btn-small revert" title="${tr('btn_revert')}" onclick="revertEntry(${i})"><i class="mdi mdi-undo"></i></button>`;
            } else if (rs === 'reverted') {
                actionHtml = `<span class="hist-status hist-status--reverted"><i class="mdi mdi-undo"></i> ${tr('hist_op_revert')}</span>`;
            } else if (rs === 'missing') {
                actionHtml = `<span class="hist-status hist-status--missing"><i class="mdi mdi-alert-circle"></i> ${tr('hist_file_missing')}</span>`;
            } else if (rs === 'conflict') {
                actionHtml = `<span class="hist-status hist-status--conflict"><i class="mdi mdi-alert"></i></span>`;
            }

            const statusLine = `<div class="hist-name-section">
                <div class="hist-name-from">${esc(e.from_name)}</div>
                <div class="hist-arrow">→</div>
                <div class="hist-name-to">${esc(e.to_name)}</div>
            </div>`;

            return `<tr data-hist-idx="${i}">
                <td class="hist-date">${esc(e.date)}</td>
                <td><span class="hist-op ${opClass}">${opLabel}</span></td>
                <td class="hist-combined">${statusLine}</td>
                <td class="hist-actions"><div id="hist-prog-${i}">${actionHtml}</div></td>
            </tr>`;
        }).join('');
    }).catch(e => { tbody.innerHTML = `<tr><td colspan="4" class="empty-state">Erreur: ${esc(e.message)}</td></tr>`; });
}

async function revertEntry(i) {
    const entry = window._historyEntries?.[i];
    if (!entry) return;
    const progEl = document.getElementById(`hist-prog-${i}`);
    if (progEl) progEl.innerHTML = `<span style="color:#e67e22">◌ ${tr('searching')}</span>`;
    try {
        const data = await postJSON('/api/revert', { id: entry.id });
        if (!data.success) throw new Error(data.message);
        if (progEl) progEl.innerHTML = `<span style="color:#27ae60">✓ ${tr('hist_reverted')}</span>`;
        // Toujours rescanner après un revert pour mettre à jour le tableau des fichiers
        setTimeout(() => {
            loadHistory();
            scanFiles();
        }, 600);
    } catch(e) {
        if (progEl) progEl.innerHTML = `<span style="color:#e74c3c">✗ ${esc(e.message)}</span>`;
    }
}

function clearHistory() {
    document.getElementById('confirmClearModal').classList.add('active');
}

function confirmClearHistory() {
    closeModal('confirmClearModal');
    fetch('/api/history/clear', { method: 'POST' })
        .then(() => loadHistory())
        .catch(e => alert(`Erreur: ${e.message}`));
}
