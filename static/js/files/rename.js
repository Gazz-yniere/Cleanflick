// ── Rename ────────────────────────────────────────────────────────────────────
function doRename(filePath) {
    const file = allFiles.find(f => f.path === filePath);
    if (!file || !file.filename) return;
    const p = filesPreviews[file.path];
    if (!p?.data) return;
    const newName = generateFilename(file, p.data.details);
    if (newName === file.filename) return;

    // Désactiver les boutons pendant le rename
    const row = findFileRow(filePath);
    if (row) row.querySelectorAll('.btn-small').forEach(b => b.disabled = true);

    postJSON('/api/rename', { path: file.path, new_name: newName })
        .then(data => {
            if (!data.success) {
                if (row) row.querySelectorAll('.btn-small').forEach(b => b.disabled = false);
                alert(`✗ ${tr('err_rename')}\n${data.message}`);
                return;
            }
            applyPathChange(file.path, data.new_path, newName);
        })
        .catch(e => {
            if (row) row.querySelectorAll('.btn-small').forEach(b => b.disabled = false);
            alert(`✗ ${tr('err_rename')}\n${e.message}`);
        });
}

function renameAll() {
    const toRename = allFiles.filter(f => filesPreviews[f.path]?.data);
    if (!toRename.length) { alert(tr('err_no_files')); return; }
    toRename.forEach(file => doRename(file.path));
}
