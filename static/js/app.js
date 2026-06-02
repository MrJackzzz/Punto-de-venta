function fmt(amount) {
    return '$' + Number(amount).toLocaleString('es-AR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function openModal(id) {
    document.getElementById(id).classList.add('active');
}

function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}

function toggleMenu() {
    document.getElementById('sidebar').classList.toggle('collapsed');
}

function toggleMobileMenu() {
    document.getElementById('mobileNav').classList.toggle('open');
}

document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal')) {
        e.target.classList.remove('active');
    }
});

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
