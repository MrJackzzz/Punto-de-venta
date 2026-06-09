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
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.toggle('collapsed');
    localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
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

document.addEventListener('shown.bs.modal', function(e) {
    const first = e.target.querySelector('[autofocus]');
    if (first) setTimeout(function() { first.focus(); }, 50);
});

document.addEventListener('input', function(e) {
    const tag = e.target.tagName;
    if ((tag === 'INPUT' || tag === 'TEXTAREA') && !e.target.closest('.users-page') && !e.target.closest('.login-form')) {
        if (e.target.type === 'password') return;
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
