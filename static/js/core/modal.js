// ── Modal ─────────────────────────────────────────────────────────────────────
const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)');

function closeActiveModal(m) {
    if (!m.classList.contains('active')) return;
    const done = () => m.classList.remove('active');
    if (REDUCED.matches || !m.animate) { done(); return; }
    const content = m.querySelector('.modal-content');
    const anims = [
        m.animate(
            [{ background: 'rgba(0,0,0,0.65)' }, { background: 'rgba(0,0,0,0)' }],
            { duration: 180, easing: 'ease-in' }
        )
    ];
    if (content) {
        anims.push(content.animate(
            [
                { opacity: 1, transform: 'scale(1) translateY(0)' },
                { opacity: 0, transform: 'scale(0.96) translateY(6px)' }
            ],
            { duration: 160, easing: 'cubic-bezier(0.4, 0, 1, 1)' }
        ));
    }
    Promise.all(anims.map(a => a.finished)).then(done).catch(done);
}

function closeModal(id) { closeActiveModal(document.getElementById(id)); }

document.addEventListener('click', e => {
    if (e.target.classList.contains('modal')) closeActiveModal(e.target);
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal.active').forEach(closeActiveModal);
    }
});
