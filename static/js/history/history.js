// ── History ───────────────────────────────────────────────────────────────────
const HISTORY_PAGE_SIZE = 100;
const HISTORY_CACHE_KEY = 'cleanflick_history_cache_v1';
const HISTORY_CACHE_MAX_CHARS = 350_000;
let _historyRequestId = 0;
let _historyHasMore = false;
function readHistoryCache() {
    try {
        const raw = localStorage.getItem(HISTORY_CACHE_KEY);
        if (!raw) return null;
        if (raw.length > HISTORY_CACHE_MAX_CHARS) {
            try { localStorage.removeItem(HISTORY_CACHE_KEY); } catch (e) {}
            return null;
        }
        const cache = JSON.parse(raw);
        if (cache?.version !== 1 || !Array.isArray(cache.entries) || !Number.isFinite(cache.ts)) {
            try { localStorage.removeItem(HISTORY_CACHE_KEY); } catch (e) {}
            return null;
        }
        return cache;
    } catch (e) {
        try { localStorage.removeItem(HISTORY_CACHE_KEY); } catch (removeError) {}
        return null;
    }
}
const _cachedHistory = readHistoryCache();
window._historyEntries = _cachedHistory?.entries || [];
_historyHasMore = !!_cachedHistory?.hasMore;
let _historyLoaded = !!_cachedHistory;
let _historyUpdatedAt = _cachedHistory?.ts || 0;
let _historyController = null;
let _historyPromise = null;
const HISTORY_CACHE_TTL = 30_000;

function saveHistoryCache() {
    try {
        const entries = window._historyEntries || [];
        let cachedEntries = entries;
        let raw;
        const makeRaw = list => JSON.stringify({
            version: 1,
            ts: _historyUpdatedAt || Date.now(),
            hasMore: _historyHasMore || list.length < entries.length,
            entries: list,
        });
        raw = makeRaw(cachedEntries);
        while (raw.length > HISTORY_CACHE_MAX_CHARS && cachedEntries.length > 1) {
            cachedEntries = cachedEntries.slice(0, Math.max(1, Math.floor(cachedEntries.length * 0.8)));
            raw = makeRaw(cachedEntries);
        }
        if (raw.length > HISTORY_CACHE_MAX_CHARS) {
            try { localStorage.removeItem(HISTORY_CACHE_KEY); } catch (e) {}
            return;
        }
        try {
            localStorage.setItem(HISTORY_CACHE_KEY, raw);
        } catch (e) {
            if (!(e?.name === 'QuotaExceededError' || e?.code === 22 || e?.code === 1014)) return;
            for (const key of ['cleanflick_lib_tree_cache', 'cleanflick_lib_flat_cache_v1']) {
                try { localStorage.removeItem(key); } catch (removeError) {}
                try {
                    localStorage.setItem(HISTORY_CACHE_KEY, raw);
                    return;
                } catch (retryError) {}
            }
        }
    } catch (e) {}
}

function renderHistoryEntries(entries, startIndex) {
    const tbody = document.getElementById('history-tbody');
    const markup = entries.map((e, index) => {
            const i = startIndex + index;
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
    if (startIndex === 0) tbody.innerHTML = markup;
    else tbody.insertAdjacentHTML('beforeend', markup);
}

function loadHistory(loadMore = false, force = true) {
    const tbody = document.getElementById('history-tbody');
    const button = document.getElementById('history-load-more');
    const entries = window._historyEntries || [];
    if (!loadMore && _historyLoaded) {
        if (entries.length) {
            tbody.innerHTML = '';
            renderHistoryEntries(entries, 0);
        } else {
            tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${tr('hist_empty')}</td></tr>`;
        }
        if (button) {
            button.hidden = !_historyHasMore;
            button.disabled = false;
        }
        if (!force && Date.now() - _historyUpdatedAt < HISTORY_CACHE_TTL)
            return Promise.resolve(entries);
    }
    if (_historyPromise && !force) return _historyPromise;
    if (_historyController) _historyController.abort();
    const controller = new AbortController();
    _historyController = controller;
    const offset = loadMore ? entries.length : 0;
    const requestId = ++_historyRequestId;
    if (!loadMore) {
        if (!_historyLoaded) {
            window._historyEntries = [];
            _historyHasMore = false;
            if (button) button.hidden = true;
            tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${tr('searching')}</td></tr>`;
        }
    } else if (button) {
        button.disabled = true;
    }

    const promise = fetch(`/api/history?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`, { signal: controller.signal })
        .then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json().then(data => ({ data, hasMore: r.headers.get('X-History-Has-More') === 'true' }));
        })
        .then(({ data, hasMore }) => {
            if (requestId !== _historyRequestId) return;
            if (!loadMore) window._historyEntries = [];
            const startIndex = window._historyEntries.length;
            window._historyEntries.push(...data);
            _historyHasMore = hasMore;
            _historyLoaded = true;
            _historyUpdatedAt = Date.now();
            saveHistoryCache();
            if (!window._historyEntries.length) {
                tbody.innerHTML = `<tr><td colspan="4" class="empty-state">${tr('hist_empty')}</td></tr>`;
            } else {
                renderHistoryEntries(data, startIndex);
            }
            if (button) {
                button.hidden = !_historyHasMore;
                button.disabled = false;
            }
        })
        .catch(e => {
            if (requestId !== _historyRequestId) return;
            if (e.name === 'AbortError') return;
            if (button) button.disabled = false;
            if (!loadMore && !_historyLoaded) tbody.innerHTML = `<tr><td colspan="4" class="empty-state">Erreur: ${esc(e.message)}</td></tr>`;
        })
        .finally(() => {
            if (requestId === _historyRequestId) {
                _historyController = null;
                _historyPromise = null;
            }
        });
    _historyPromise = promise;
    return promise;
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
