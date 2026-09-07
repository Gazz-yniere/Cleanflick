// ── Modal ─────────────────────────────────────────────────────────────────────
function closeModal(id) { document.getElementById(id).classList.remove('active'); }
document.addEventListener('click', e => { if (e.target.classList.contains('modal')) e.target.classList.remove('active'); });
