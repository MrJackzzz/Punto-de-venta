function fmt(amount) {
    return '$' + Number(amount).toLocaleString('es-AR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function openModal(id) {
    const el = document.getElementById(id);
    if (el) {
        const modal = bootstrap.Modal.getOrCreateInstance(el);
        modal.show();
    }
}

function closeModal(id) {
    const el = document.getElementById(id);
    if (el) {
        const modal = bootstrap.Modal.getInstance(el);
        if (modal) modal.hide();
    }
}

function toggleMenu() {
    document.getElementById('sidebar').classList.toggle('collapsed');
}

function toggleMobileMenu() {
    document.getElementById('mobileNav').classList.toggle('open');
}

document.addEventListener('DOMContentLoaded', function() {
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function(a) {
        setTimeout(function() {
            a.style.transition = 'opacity 0.5s';
            a.style.opacity = '0';
            setTimeout(function() { a.remove(); }, 500);
        }, 4000);
    });
});

document.addEventListener('input', function(e) {
    const tag = e.target.tagName;
    if ((tag === 'INPUT' || tag === 'TEXTAREA') && !e.target.closest('.users-page')) {
        if (e.target.type === 'text' || e.target.type === 'search' || !e.target.type) {
            if (!e.target.classList.contains('no-upper')) {
                const start = e.target.selectionStart;
                const end = e.target.selectionEnd;
                e.target.value = e.target.value.toUpperCase();
                e.target.setSelectionRange(start, end);
            }
        }
    }
});
