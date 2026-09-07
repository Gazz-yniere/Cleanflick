// ── File Picker ───────────────────────────────────────────────────────────────
let _pickerTarget = null;
let _pickerCurrentPath = '';

function pickFolder(inputId) {
    _pickerTarget = inputId;
    document.getElementById('folderPickerModal').classList.add('active');
    browseFolder(document.getElementById(inputId)?.value || null);
}

function browseFolder(path) {
    _pickerCurrentPath = path || '';
    const content = document.getElementById('folderPickerContent');
    content.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
    const url = path ? `/api/browse?path=${encodeURIComponent(path)}` : '/api/browse';
    fetch(url).then(r => r.json()).then(data => {
        if (data.error && !data.path) { content.innerHTML = `<div class="message error">${esc(data.error)}</div>`; return; }
        _pickerCurrentPath = data.path || '';
        const sep = _pickerCurrentPath.includes('\\') ? '\\' : '/';
        let html = `<div class="picker-bar">
            <input type="text" class="picker-path-input" placeholder="${tr('picker_type_path')}"
                   value="${esc(_pickerCurrentPath)}" onkeydown="if(event.key==='Enter') pickerGoTo(this.value)">
            <button class="btn btn-secondary" onclick="pickerGoTo(this.previousElementSibling.value)"><i class="mdi mdi-navigation-variant"></i> ${tr('picker_open')}</button>
        </div>`;
        const segs = pickerBreadcrumbs(_pickerCurrentPath);
        const computerView = !!data.is_computer;
        if (segs.length)
            html += `<div class="picker-crumbs">`
                + segs.map((s, i) => (i ? `<span class="sep">▸</span>` : '')
                    + (computerView && i === 0
                        ? `<button class="current-clickable" data-path="" onclick="browseFolder(this.dataset.path)" title="${esc(tr('picker_back_to_home'))}">${esc(s.label)}</button>`
                        : (i === segs.length - 1
                            ? `<span class="current">${esc(s.label)}</span>`
                            : `<button data-path="${esc(s.path)}" onclick="browseFolder(this.dataset.path)">${esc(s.label)}</button>`))).join('')
                + `</div>`;
        if (data.error) html += `<div class="message error">${esc(data.error)}</div>`;
        html += `<div class="picker-list">`;
        if (data.parent !== undefined && data.parent !== null)
            html += `<div class="picker-item up" data-path="${esc(data.parent)}" onclick="browseFolder(this.dataset.path || null)"><i class="mdi mdi-arrow-up-bold"></i> ${tr('picker_up')}</div>`;
        if (!_pickerCurrentPath) {
            html += `<div class="picker-section">${tr('picker_local_drives')}</div>`;
            (data.roots || []).forEach(root =>
                html += `<div class="picker-item" data-path="${esc(root)}" onclick="browseFolder(this.dataset.path)"><i class="mdi mdi-harddisk"></i> ${esc(root)}</div>`);
            html += `<div class="picker-section">${tr('picker_network')}</div>`;
            (data.net_roots || []).forEach(root =>
                html += `<div class="picker-item" data-path="${esc(root)}" onclick="browseFolder(this.dataset.path)"><i class="mdi mdi-desktop-network"></i> ${esc(root)}</div>`);
            html += `<div class="picker-net-list"><div class="picker-net-loading">${tr('picker_net_loading')}</div></div>`;
        }
        (data.dirs || []).forEach(d => {
            const full = _pickerCurrentPath.replace(/[\/\\]+$/, '') + sep + d;
            html += `<div class="picker-item" data-path="${esc(full)}" onclick="browseFolder(this.dataset.path)"><i class="mdi mdi-folder-outline"></i> ${esc(d)}</div>`;
        });
        if (data.path && !(data.dirs?.length) && !data.error)
            html += `<div class="picker-empty-note">${tr('picker_empty')}</div>`;
        html += `</div><div class="picker-actions">
            <button class="btn btn-secondary" onclick="closeModal('folderPickerModal')">${tr('picker_cancel')}</button>
            <button class="btn btn-primary picker-select-btn" id="picker-select-btn">${tr('picker_select')}</button>
        </div>`;
        content.innerHTML = html;
        if (!_pickerCurrentPath) loadPickerNet(content);
        document.getElementById('picker-select-btn').onclick = () => {
            if (_pickerTarget) {
                document.getElementById(_pickerTarget).value = _pickerCurrentPath;
                scheduleSaveConfigField(_pickerTarget, _pickerCurrentPath);
            }
            closeModal('folderPickerModal');
        };
    }).catch(e => { content.innerHTML = `<div class="message error">Erreur: ${esc(e.message)}</div>`; });
}

// Équivalent du fil d'adresse de l'explorateur : chaque segment est cliquable
// pour remonter (D:\films\x → D:\films → D:\ ; \\serveur\partage → \\serveur).
function pickerBreadcrumbs(p) {
    const segs = [];
    if (!p) return segs;
    const norm = p.replace(/\//g, '\\');
    if (norm.startsWith('\\\\')) {
        const parts = norm.replace(/^\\\\+/, '').split('\\').filter(Boolean);
        if (parts.length) {
            segs.push({ label: parts[0], path: '\\\\' + parts[0] });
            for (let i = 1; i < parts.length; i++)
                segs.push({ label: parts[i], path: '\\\\' + parts.slice(0, i + 1).join('\\') });
        }
    } else {
        const m = norm.match(/^([A-Za-z]:)(.*)$/);
        if (m) {
            let cur = m[1] + '\\';
            segs.push({ label: m[1] + '\\', path: cur });
            for (const r of (m[2] || '').split('\\').filter(Boolean)) {
                cur = cur.replace(/[\\]+$/, '') + '\\' + r;
                segs.push({ label: r, path: cur });
            }
        }
    }
    return segs;
}

// Postes de la LAN (groupe de travail + domaine + UNC de la config).
function loadPickerNet(content) {
    const el = content.querySelector('.picker-net-list');
    if (!el) return;
    fetch('/api/browse-net').then(r => r.json()).then(nd => {
        if (!el.isConnected) return;
        const pcs = nd.pcs || [];
        let html = pcs.length
            ? pcs.map(p => `<div class="picker-item" data-path="${esc('\\\\' + p)}" onclick="browseFolder(this.dataset.path)"><i class="mdi mdi-desktop-network"></i> ${esc(p)}</div>`).join('')
            : `<div class="picker-empty-note">${tr('picker_net_none')}</div>`;
        if (nd.err) html += `<div class="picker-net-err">${esc(nd.err)}</div>`;
        el.innerHTML = html;
    }).catch(() => { if (el.isConnected) el.innerHTML = `<div class="picker-empty-note">${tr('picker_net_none')}</div>`; });
}

// Aller directement à un chemin saisi (local ou réseau, ex: \\serveur\partage).
function pickerGoTo(value) {
    const v = (value || '').trim();
    if (v) browseFolder(v);
}
