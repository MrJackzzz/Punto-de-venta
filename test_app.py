import os, sys, tempfile, pytest
os.environ['TESTING'] = '1'
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db, User, Product, Config, Category, Supplier
from flask import url_for

@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['SERVER_NAME'] = 'test.local'
    with app.app_context():
        db.create_all()
        admin = User(username='admin', role='admin', active=True, first_name='Admin', last_name='Test')
        admin.set_password('admin123')
        db.session.add(admin)
        cat = Category(name='TestCat')
        db.session.add(cat)
        sup = Supplier(name='TestSup')
        db.session.add(sup)
        prod = Product(code='TEST01', name='Test Product', cost=100, price=130, stock=10, category_id=1, supplier_id=1)
        db.session.add(prod)
        db.session.commit()
        yield app.test_client()
        db.drop_all()

def login(client, user='admin', pw='admin123'):
    return client.post('/login', data={'username': user, 'password': pw}, follow_redirects=True)

def test_login(client):
    rv = client.get('/login')
    assert rv.status_code == 200

def test_login_success(client):
    rv = login(client)
    assert rv.status_code == 200
    assert b'Admin' in rv.data or b'Dashboard' in rv.data or b'producto' in rv.data

def test_login_fail(client):
    rv = client.post('/login', data={'username': 'admin', 'password': 'wrong'}, follow_redirects=True)
    assert rv.status_code == 200
    assert b'Incorrecta' in rv.data or b'incorrecta' in rv.data or b'error' in rv.data

def test_dashboard(client):
    login(client)
    rv = client.get('/dashboard')
    assert rv.status_code == 200

def test_products_page(client):
    login(client)
    rv = client.get('/products')
    assert rv.status_code == 200
    assert b'TESTO1' in rv.data or b'Test Product' in rv.data

def test_sell_page(client):
    login(client)
    rv = client.get('/sell')
    assert rv.status_code == 200

def test_history_page(client):
    login(client)
    rv = client.get('/history')
    assert rv.status_code == 200

def test_backups_page(client):
    login(client)
    rv = client.get('/backups')
    assert rv.status_code == 200

def test_settings_page(client):
    login(client)
    rv = client.get('/settings')
    assert rv.status_code == 200

def test_suppliers_page(client):
    login(client)
    rv = client.get('/suppliers')
    assert rv.status_code == 200
    assert b'TestSup' in rv.data

def test_categories_page(client):
    login(client)
    rv = client.get('/categories')
    assert rv.status_code == 200

def test_pending_sales(client):
    login(client)
    rv = client.get('/pending-sales')
    assert rv.status_code == 200

def test_cash_close(client):
    login(client)
    rv = client.get('/cash-close')
    assert rv.status_code == 200

def test_manual(client):
    login(client)
    rv = client.get('/manual')
    assert rv.status_code == 200

def test_checkout(client):
    login(client)
    rv = client.post('/sell/checkout', json={
        'items': [{'product_id': 1, 'quantity': 2}],
        'payment_method': 'cash',
        'amount_paid': 500
    })
    assert rv.status_code == 200
    data = rv.get_json()
    assert data is not None
    assert 'sale_id' in data or 'ticket' in data or 'redirect' in data or 'total' in data

def test_api_product_search(client):
    login(client)
    rv = client.get('/api/products/search?q=Test')
    assert rv.status_code == 200
    data = rv.get_json()
    assert len(data) > 0
    assert data[0]['name'] == 'Test Product'

def test_products_add(client):
    login(client)
    rv = client.post('/products/add', data={
        'code': 'TEST02', 'name': 'New Product', 'cost': '50', 'markup_percentage': '20',
        'price': '60', 'stock': '5', 'unit_type': 'unit', 'currency': 'ARS'
    }, follow_redirects=True)
    assert rv.status_code == 200
    prod = Product.query.filter_by(code='TEST02').first()
    assert prod is not None
    assert prod.name == 'New Product'

def test_onboarding_page(client):
    rv = client.get('/onboarding')
    assert rv.status_code == 200
    assert b'Formulario' in rv.data

def test_onboarding_submit(client):
    rv = client.post('/onboarding/submit', data={
        'business_name': 'Test Business',
        'owner_name': 'Test Owner',
        'email': 'test@test.com'
    })
    assert rv.status_code == 200
    assert b'Formulario recibido' in rv.data

def test_manual_sections_save(client):
    login(client)
    rv = client.post('/api/manual/save', json={
        'mTrash': {'visible': 'hidden'},
        'mCash': {'title': 'Nuevo título'}
    })
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['success'] is True

def test_clear_section(client):
    login(client)
    rv = client.post('/admin/clear-section', data={'section': 'categories'})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['success'] is True

def test_supervisor_backup(client):
    """Supervisor should be able to access backups page"""
    with app.app_context():
        sup = User(username='supervisor', role='supervisor', active=True, first_name='Super', last_name='visor')
        sup.set_password('test123')
        db.session.add(sup)
        db.session.commit()
    login(client, 'supervisor', 'test123')
    rv = client.get('/backups')
    assert rv.status_code == 200

def test_user_permissions(client):
    """Regular user should NOT see settings"""
    with app.app_context():
        u = User(username='user1', role='user', active=True)
        u.set_password('test123')
        db.session.add(u)
        db.session.commit()
    login(client, 'user1', 'test123')
    rv = client.get('/settings', follow_redirects=True)
    assert rv.status_code == 200
    assert b'No tienes permiso' in rv.data or b'danger' in rv.data

def test_send_products_email_without_smtp(client):
    login(client)
    rv = client.post('/api/products/send-email', json={'email': 'test@test.com'})
    assert rv.status_code == 500 or rv.status_code == 200
    data = rv.get_json()
    if rv.status_code == 500:
        assert 'error' in data or 'SMTP' in data.get('error', '')
