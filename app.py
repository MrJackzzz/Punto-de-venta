from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, send_file, make_response, abort, g
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, AnonymousUserMixin
from models import db, User, Product, Supplier, Sale, SaleItem, MovementLog, Config, Category, PendingOrder, System, DeletedRecord, CashClose, PurchaseOrder
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
AR_TZ = ZoneInfo('America/Argentina/Buenos_Aires')

def set_timezone(tz_name=None):
    global AR_TZ
    name = tz_name or 'America/Argentina/Buenos_Aires'
    try:
        AR_TZ = ZoneInfo(name)
    except Exception:
        AR_TZ = ZoneInfo('America/Argentina/Buenos_Aires')


def to_ar(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AR_TZ)
from werkzeug.utils import secure_filename
from sqlalchemy import func, or_
import os, csv, io, json, smtplib, shutil, zipfile, subprocess, uuid, threading, re, time, queue
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

app = Flask(__name__)
app.jinja_env.globals['to_ar'] = to_ar

def nl2br(text):
    return (text or '').replace('\n', '<br>')
app.jinja_env.filters['nl2br'] = nl2br

def fmt_stock(value, unit_type='unit'):
    if unit_type == 'unit':
        return str(int(value))
    return str(value)
app.jinja_env.filters['fmt_stock'] = fmt_stock


import json as _json
def fromjson(val):
    if not val: return []
    try: return _json.loads(val)
    except: return []
app.jinja_env.filters['fromjson'] = fromjson


def fmt(amount):
    """Formato argentino: 1234567.89 -> $1.234.567,89"""
    s = f"{amount:,.2f}"
    integer_part, decimal_part = s.split('.')
    integer_part = integer_part.replace(',', '.')
    return f"${integer_part},{decimal_part}"


app.jinja_env.filters['fmt'] = fmt
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cambiame-en-produccion')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///sistema.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 5,
    'pool_recycle': 300,
    'pool_pre_ping': True,
    'max_overflow': 2,
}
app.config['SESSION_COOKIE_NAME'] = os.environ.get('SESSION_COOKIE_NAME', 'session')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['LOGO_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'logo')
app.config['FAVICON_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'favicon')
app.config['BACKUP_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

@app.errorhandler(413)
def request_entity_too_large(error):
    flash('El archivo es demasiado grande. Máximo 10MB.', 'danger')
    return redirect(url_for('settings'))

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['LOGO_FOLDER'], exist_ok=True)
os.makedirs(app.config['FAVICON_FOLDER'], exist_ok=True)
os.makedirs(app.config['BACKUP_FOLDER'], exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

import base64 as _b64lib

def _save_uploaded_file(file, subfolder, config_key):
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
    filename = f'{config_key}_{uuid.uuid4().hex[:12]}.{ext}'
    folder = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)
    file.save(filepath)
    cfg = Config.query.filter_by(key=config_key).first()
    if cfg:
        old_path = None
        if cfg.value and not cfg.value.startswith('data:'):
            old_path = os.path.join(folder, cfg.value)
        cfg.value = filename
    else:
        cfg = Config(key=config_key, value=filename)
        db.session.add(cfg)
    db.session.commit()
    try:
        if hasattr(g, '_configs_cached'):
            del g._configs_cached
    except (RuntimeError, AttributeError):
        pass
    if old_path and os.path.exists(old_path) and old_path != filepath:
        try: os.remove(old_path)
        except OSError: pass
    return url_for('static', filename=f'uploads/{subfolder}/{filename}')

def _delete_uploaded_file(config_key, subfolder):
    cfg = Config.query.filter_by(key=config_key).first()
    if cfg and cfg.value and not cfg.value.startswith('data:'):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], subfolder, cfg.value)
        if os.path.exists(filepath):
            try: os.remove(filepath)
            except OSError: pass
        cfg.value = ''
        db.session.commit()
        try:
            if hasattr(g, '_configs_cached'):
                del g._configs_cached
        except (RuntimeError, AttributeError):
            pass

def _migrate_base64_to_file(config_key, subfolder):
    cfg = Config.query.filter_by(key=config_key).first()
    if not cfg or not cfg.value or not cfg.value.startswith('data:'):
        return None
    try:
        header, b64data = cfg.value.split(',', 1)
        mime_map = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}
        mime = header.replace('data:', '').replace(';base64', '').strip()
        ext = mime_map.get(mime, 'png')
        filename = f'{config_key}_{uuid.uuid4().hex[:12]}.{ext}'
        folder = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
        os.makedirs(folder, exist_ok=True)
        filepath = os.path.join(folder, filename)
        data = _b64lib.b64decode(b64data)
        with open(filepath, 'wb') as f:
            f.write(data)
        backup_key = f'{config_key}_b64_backup'
        bkp = Config.query.filter_by(key=backup_key).first()
        if bkp:
            bkp.value = cfg.value
        else:
            db.session.add(Config(key=backup_key, value=cfg.value))
        cfg.value = filename
        db.session.commit()
        return filename
    except Exception:
        return None

def _ensure_uploaded_file(config_key, subfolder):
    cfg = Config.query.filter_by(key=config_key).first()
    if not cfg or not cfg.value or cfg.value.startswith('data:'):
        return None
    folder = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, cfg.value)
    if os.path.exists(filepath):
        return cfg.value
    backup_key = f'{config_key}_b64_backup'
    bkp = Config.query.filter_by(key=backup_key).first()
    if bkp and bkp.value and bkp.value.startswith('data:'):
        try:
            header, b64data = bkp.value.split(',', 1)
            data = _b64lib.b64decode(b64data)
            with open(filepath, 'wb') as f:
                f.write(data)
            return cfg.value
        except Exception:
            pass
    bkp_file = os.path.join(folder, f'{config_key}_b64_backup.txt')
    if os.path.exists(bkp_file):
        try:
            with open(bkp_file) as f:
                raw = f.read().strip()
            if raw.startswith('data:'):
                header, b64data = raw.split(',', 1)
                data = _b64lib.b64decode(b64data)
                with open(filepath, 'wb') as fw:
                    fw.write(data)
                return cfg.value
        except Exception:
            pass
    return None




@app.after_request
def add_cache_headers(response):
    if response.is_streamed or response.status_code >= 400:
        return response
    path = request.path
    if path.startswith('/static/'):
        response.cache_control.max_age = 86400 * 365
        response.cache_control.public = True
    elif path in ('/', '/login') or path.startswith('/api/'):
        response.cache_control.no_cache = True
    return response


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_config(key, default=''):
    try:
        if hasattr(g, '_configs_cached') and key in g._configs_cached:
            return g._configs_cached[key] or default
    except RuntimeError:
        pass
    c = Config.query.filter_by(key=key).first()
    return c.value if c and c.value else default


def get_instance_id():
    c = Config.query.filter_by(key='instance_id').first()
    if c and c.value:
        return c.value
    uid = uuid.uuid4().hex[:12]
    if c:
        c.value = uid
    else:
        db.session.add(Config(key='instance_id', value=uid))
    db.session.commit()
    return uid


@app.context_processor
def inject_globals():
    if not hasattr(g, '_configs_cached'):
        g._configs_cached = {c.key: c.value for c in Config.query.all()}
    cfg = g._configs_cached
    logo_val = cfg.get('logo_filename', '')
    logo_src = ''
    if logo_val:
        if logo_val.startswith('data:'):
            fn = _migrate_base64_to_file('logo_filename', 'logo')
            if fn:
                g._configs_cached['logo_filename'] = fn
                logo_src = url_for('static', filename=f'uploads/logo/{fn}')
        else:
            if _ensure_uploaded_file('logo_filename', 'logo'):
                logo_src = url_for('static', filename=f'uploads/logo/{logo_val}')
    fav_val = cfg.get('favicon_data', '')
    fav_src = ''
    if fav_val:
        if fav_val.startswith('data:'):
            fn = _migrate_base64_to_file('favicon_data', 'favicon')
            if fn:
                g._configs_cached['favicon_data'] = fn
                fav_src = url_for('static', filename=f'uploads/favicon/{fn}')
        else:
            if _ensure_uploaded_file('favicon_data', 'favicon'):
                fav_src = url_for('static', filename=f'uploads/favicon/{fav_val}')
    return {
        'business_name': cfg.get('business_name', 'NexoControl'),
        'local_name': cfg.get('local_name', ''),
        'logo_url': logo_src,
        'logo_src': logo_src,
        'favicon_data': fav_src,
        'now': lambda: datetime.now(AR_TZ),
        'configs': cfg,
    }

_last_bg_check = 0
_BG_INTERVAL = 300  # 5 minutes

@app.before_request
def run_bg_tasks():
    global _last_bg_check
    now = time.time()
    if now - _last_bg_check < _BG_INTERVAL:
        return
    _last_bg_check = now
    try:
        check_critical_stock()
        auto_backup_check()
        demo_auto_reset_check()
    except Exception:
        pass

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.anonymous_user = AnonymousUserMixin

# Add permission methods to AnonymousUserMixin that return False
_perm_methods = ['can_view_products','can_add_products','can_edit_products','can_manage_products',
                 'can_view_suppliers','can_add_suppliers','can_edit_suppliers','can_delete_suppliers',
                 'can_manage_users','can_view_history','can_sell',
                 'can_view_categories','can_add_categories','can_edit_categories','can_delete_categories',
                 'can_view_charts','can_close_cash','can_void_cash_close',
                 'can_view_pending_sales','can_confirm_payment']
for _m in _perm_methods:
    if not hasattr(AnonymousUserMixin, _m):
        setattr(AnonymousUserMixin, _m, lambda self: False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def log_movement(user, action, description):
    log = MovementLog(user_id=user.id, action=action, description=description)
    db.session.add(log)
    db.session.commit()


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Acceso denegado. Se requiere rol Admin.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


def supervisor_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role == 'user':
            flash('Acceso denegado.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


def check_critical_stock():
    try:
        critical = int(get_config('critical_stock_threshold', '5'))
        if critical <= 0:
            return
        triggered = Product.query.filter(Product.stock <= critical).all()
        if not triggered:
            return
        owner_email = get_config('owner_email', '')
        if not owner_email or not can_send_email():
            return
        biz_name = get_config('business_name', 'Mi Negocio')
        items_html = ''.join(
            f'<tr><td>{p.code}</td><td>{p.name}</td><td style="color:red;font-weight:bold;">{p.stock}</td></tr>'
            for p in triggered
        )
        html = f'''<h2>⚠️ Stock Crítico - {biz_name}</h2>
<p>Los siguientes productos tienen stock por debajo del nivel crítico ({critical}):</p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;">
<tr style="background:#ee6c4d;color:white;"><th>Código</th><th>Producto</th><th>Stock</th></tr>{items_html}</table>
<p><small>Este es un mensaje automático de {biz_name}.</small></p>'''
        send_email(owner_email, f'⚠️ Stock Crítico - {biz_name}', html)
        log_movement(
            User.query.filter_by(role='admin').first(),
            'system', f'Alerta stock crítico enviada a {owner_email}'
        )
    except Exception:
        pass


def can_send_email():
    return bool(get_config('smtp_host', '') and get_config('smtp_user', '') and get_config('smtp_password', ''))


def can_send_po_email():
    return bool(get_config('po_smtp_host', '') and get_config('po_smtp_user', '') and get_config('po_smtp_password', ''))


def send_po_email(to, subject, html_body):
    try:
        smtp_host = get_config('po_smtp_host', '')
        smtp_port = int(get_config('po_smtp_port', '587'))
        smtp_user = get_config('po_smtp_user', '')
        smtp_password = get_config('po_smtp_password', '')
        smtp_from = get_config('po_email_from', '') or smtp_user
        if not smtp_host or not smtp_user:
            return False
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = to
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        return False


def send_email(to, subject, html_body):
    try:
        smtp_host = get_config('smtp_host', '')
        smtp_port = int(get_config('smtp_port', '587'))
        smtp_user = get_config('smtp_user', '')
        smtp_password = get_config('smtp_password', '')
        if not smtp_host or not smtp_user:
            return False
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_user
        msg['To'] = to
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        return False


@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/api/public/systems')
def api_public_systems():
    systems = System.query.filter_by(is_active=True).order_by(System.sort_order).all()
    return jsonify([{
        'id': s.id,
        'name': s.name,
        'tagline': s.tagline,
        'description': s.description,
        'logo_url': s.logo_url,
        'price': s.price,
        'category': s.category,
        'demo_url': s.demo_url,
        'features': [f.strip() for f in s.features.split('\n') if f.strip()] if s.features else [],
        'sort_order': s.sort_order,
    } for s in systems])


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.active:
                flash('Usuario desactivado. Contacte al administrador.', 'danger')
                return render_template('login.html')
            login_user(user)
            log_movement(user, 'login', f'Inicio de sesión')
            flash(f'Bienvenido {user.username}', 'success')
            return redirect(url_for('dashboard'))
        flash('Usuario o contraseña incorrectos', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    log_movement(current_user, 'logout', 'Cierre de sesión')
    logout_user()
    flash('Sesión cerrada', 'info')
    return redirect(url_for('login'))


@app.route('/demo/reset', methods=['POST'])
def demo_reset():
    try:
        SaleItem.query.delete()
        Sale.query.delete()
        MovementLog.query.delete()
        PendingOrder.query.delete()
        CashClose.query.delete()
        DeletedRecord.query.delete()
        PurchaseOrder.query.delete()
        Product.query.delete()
        Supplier.query.delete()
        Category.query.delete()
        admin = User.query.filter_by(username='admin').first()
        if admin:
            User.query.filter(User.id != admin.id).delete()
        else:
            User.query.delete()
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
        demo = User.query.filter_by(username='demo').first()
        if not demo:
            demo = User(username='demo', role='supervisor')
            demo.set_password('demo123')
            db.session.add(demo)
            db.session.flush()
        db.session.commit()
        categories_data = ['Bebidas', 'Lácteos', 'Almacén', 'Limpieza', 'Snacks']
        cat_ids = {}
        for cname in categories_data:
            c = Category(name=cname)
            db.session.add(c)
            db.session.flush()
            cat_ids[cname] = c.id
        products_data = [
            ('AGUA01', 'Agua Mineral 500ml', 80, 100, 'Bebidas', 50, 'unit'),
            ('GASE01', 'Coca-Cola 1.5L', 180, 220, 'Bebidas', 30, 'unit'),
            ('GASE02', 'Sprite 500ml', 100, 130, 'Bebidas', 40, 'unit'),
            ('LEC01', 'Leche Entera 1L', 120, 150, 'Lácteos', 25, 'unit'),
            ('YOG01', 'Yogur Natural 200g', 90, 115, 'Lácteos', 20, 'unit'),
            ('QSO01', 'Queso Cremoso x500g', 350, 420, 'Lácteos', 15, 'unit'),
            ('ARR01', 'Arroz 1kg', 150, 185, 'Almacén', 35, 'unit'),
            ('FID01', 'Fideos Tallarín 500g', 80, 105, 'Almacén', 40, 'unit'),
            ('AZU01', 'Azúcar 1kg', 130, 160, 'Almacén', 30, 'unit'),
            ('ACE01', 'Aceite Girasol 900ml', 250, 300, 'Almacén', 20, 'unit'),
            ('JAB01', 'Jabón en Polvo x500g', 200, 250, 'Limpieza', 18, 'unit'),
            ('DET01', 'Detergente 750ml', 120, 155, 'Limpieza', 22, 'unit'),
            ('PAP01', 'Papel Higiénico x4', 180, 225, 'Limpieza', 28, 'unit'),
            ('SAL01', 'Sal Fina 500g', 60, 80, 'Almacén', 50, 'unit'),
            ('GAL01', 'Galletitas Dulces 200g', 90, 115, 'Snacks', 45, 'unit'),
            ('CHI01', 'Chicles Menta x10', 40, 55, 'Snacks', 60, 'unit'),
        ]
        for code, name, cost, price, cat, stock, unit_type in products_data:
            p = Product(code=code, name=name, cost=cost, price=price, category_id=cat_ids.get(cat), stock=stock, unit_type=unit_type)
            db.session.add(p)
        db.session.commit()
        cfg = Config.query.filter_by(key='demo_last_reset').first()
        if cfg:
            cfg.value = datetime.now(timezone.utc).isoformat()
        else:
            db.session.add(Config(key='demo_last_reset', value=datetime.now(timezone.utc).isoformat()))
        db.session.commit()
        login_user(demo)
        return jsonify({'success': True, 'message': 'Demo lista. Bienvenido.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


def get_low_stock_threshold():
    try:
        return int(get_config('low_stock_threshold', '10'))
    except (ValueError, TypeError):
        return 10

def get_critical_stock_threshold():
    try:
        return int(get_config('critical_stock_threshold', '5'))
    except (ValueError, TypeError):
        return 5

@app.route('/dashboard')
@login_required
def dashboard():
    threshold = get_low_stock_threshold()
    today = datetime.now(AR_TZ).date()
    from sqlalchemy import func as sa_func

    total_products = Product.query.count()
    low_stock = Product.query.filter(Product.stock < threshold).count()
    today_sales = Sale.query.filter(sa_func.date(Sale.created_at) == today).count()
    today_revenue = float(db.session.query(sa_func.coalesce(sa_func.sum(Sale.total), 0)).filter(
        sa_func.date(Sale.created_at) == today
    ).scalar() or 0)

    cat_list = Category.query.order_by(Category.name).all()
    return render_template('dashboard.html', total_products=total_products,
                           low_stock=low_stock, today_sales=today_sales,
                           today_revenue=today_revenue,
                           low_stock_threshold=threshold, cat_list=cat_list)


@app.route('/products')
@login_required
def products():
    if not current_user.can_view_products():
        flash('No tienes permiso para ver productos.', 'danger')
        return redirect(url_for('dashboard'))
    threshold = get_low_stock_threshold()
    products_list = Product.query.order_by(Product.name).limit(1000).all()
    suppliers = Supplier.query.order_by(Supplier.name).all()
    categories = Category.query.order_by(Category.name).all()
    unit_types = ['unit', 'kg', 'g', 'liter', 'ml', 'm', 'cm', 'dozen', 'pack']
    return render_template('products.html', products=products_list, suppliers=suppliers,
                           categories=categories, low_stock_threshold=threshold, unit_types=unit_types)


@app.route('/products/add', methods=['POST'])
@login_required
def product_add():
    if not current_user.can_add_products():
        return jsonify({'error': 'Permiso denegado'}), 403
    code = request.form.get('code')
    name = request.form.get('name')
    cost = float(request.form.get('cost', 0))
    markup = float(request.form.get('markup_percentage', 0))
    currency = request.form.get('currency', 'ARS')
    stock = float(request.form.get('stock', 0))
    unit_type = request.form.get('unit_type', 'unit')
    supplier_id = request.form.get('supplier_id')
    category_id = request.form.get('category_id')
    description = request.form.get('description', '')
    wholesale_qty = float(request.form.get('wholesale_qty', 0))
    wholesale_price = float(request.form.get('wholesale_price', 0))

    if Product.query.filter_by(code=code).first():
        flash('Ya existe un producto con ese código.', 'danger')
        return redirect(url_for('products'))

    if get_config('demo_mode', '') == 'on' and Product.query.count() >= 20:
        flash('Modo Demo: máximo 20 productos. Contratá el servicio completo para usar sin límites.', 'warning')
        return redirect(url_for('products'))

    product = Product(
        code=code, name=name, cost=cost,
        markup_percentage=markup, currency=currency,
        stock=stock, unit_type=unit_type, description=description,
        wholesale_qty=wholesale_qty, wholesale_price=wholesale_price
    )
    if supplier_id:
        product.supplier_id = int(supplier_id)
    if category_id:
        product.category_id = int(category_id)
    product.calculate_price()
    db.session.add(product)
    db.session.commit()
    log_movement(current_user, 'product_create', f'Producto creado: {name} ({code})')
    flash('Producto creado correctamente.', 'success')
    return redirect(url_for('products'))


@app.route('/products/edit/<int:id>', methods=['POST'])
@login_required
def product_edit(id):
    if not current_user.can_edit_products():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('products'))
    product = db.session.get(Product, id)
    if not product:
        flash('Producto no encontrado.', 'danger')
        return redirect(url_for('products'))

    old = {
        'code': product.code, 'name': product.name, 'cost': product.cost,
        'markup_percentage': product.markup_percentage, 'currency': product.currency,
        'stock': product.stock, 'description': product.description,
        'supplier': product.supplier.name if product.supplier else 'Ninguno',
        'category': product.category.name if product.category else 'Ninguno',
    }
    product.code = request.form.get('code')
    product.name = request.form.get('name')
    product.cost = float(request.form.get('cost', 0))
    product.markup_percentage = float(request.form.get('markup_percentage', 0))
    product.currency = request.form.get('currency', 'ARS')
    product.stock = float(request.form.get('stock', 0))
    product.unit_type = request.form.get('unit_type', 'unit')
    product.description = request.form.get('description', '')
    product.wholesale_qty = float(request.form.get('wholesale_qty', 0))
    product.wholesale_price = float(request.form.get('wholesale_price', 0))
    sid = request.form.get('supplier_id')
    product.supplier_id = int(sid) if sid else None
    cid = request.form.get('category_id')
    product.category_id = int(cid) if cid else None
    product.calculate_price()
    db.session.commit()
    changes = []
    field_names = {'code': 'Código', 'name': 'Nombre', 'cost': 'Costo', 'markup_percentage': 'Margen %',
                   'stock': 'Stock', 'description': 'Descripción', 'supplier': 'Proveedor', 'category': 'Categoría'}
    new_vals = {
        'supplier': product.supplier.name if product.supplier else 'Ninguno',
        'category': product.category.name if product.category else 'Ninguno',
    }
    for field, label in field_names.items():
        o = old[field]
        n = new_vals.get(field, getattr(product, field, ''))
        if o != n:
            changes.append(f'{label}: {o} → {n}')
    detail = ', '.join(changes) if changes else 'sin cambios'
    log_movement(current_user, 'product_edit', f'{product.name}: {detail}')
    flash('Producto actualizado.', 'success')
    return redirect(url_for('products'))


@app.route('/products/delete/<int:id>', methods=['POST'])
@login_required
def product_delete(id):
    if not current_user.can_manage_products():
        flash('No tienes permiso para eliminar productos.', 'danger')
        return redirect(url_for('products'))
    product = db.session.get(Product, id)
    if product:
        name = product.name
        db.session.delete(product)
        db.session.commit()
        log_movement(current_user, 'product_delete', f'Producto eliminado: {name}')
        flash('Producto eliminado.', 'success')
    return redirect(url_for('products'))


@app.route('/categories')
@login_required
def categories():
    if not current_user.can_view_categories():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    cats = Category.query.order_by(Category.name).all()
    return render_template('categories.html', categories=cats)


@app.route('/categories/add', methods=['POST'])
@login_required
def category_add():
    if not current_user.can_add_categories():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('categories'))
    name = request.form.get('name', '').strip()
    if name and not Category.query.filter_by(name=name).first():
        db.session.add(Category(name=name))
        db.session.commit()
        log_movement(current_user, 'category_create', f'Categoría creada: {name}')
        flash('Categoría creada.', 'success')
    return redirect(url_for('categories'))


@app.route('/categories/delete/<int:id>', methods=['POST'])
@login_required
def category_delete(id):
    if not current_user.can_delete_categories():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('categories'))
    cat = db.session.get(Category, id)
    if cat:
        Product.query.filter_by(category_id=cat.id).update({Product.category_id: None})
        db.session.delete(cat)
        db.session.commit()
        log_movement(current_user, 'category_delete', f'Categoría eliminada: {cat.name}')
        flash('Categoría eliminada.', 'success')
    return redirect(url_for('categories'))


@app.route('/categories/edit/<int:id>', methods=['POST'])
@login_required
def category_edit(id):
    if not current_user.can_edit_categories():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('categories'))
    cat = db.session.get(Category, id)
    if not cat:
        flash('Categoría no encontrada.', 'danger')
        return redirect(url_for('categories'))
    name = request.form.get('name', '').strip()
    if name and name != cat.name and not Category.query.filter_by(name=name).first():
        cat.name = name
        db.session.commit()
        log_movement(current_user, 'category_edit', f'Categoría renombrada: {name}')
        flash('Categoría actualizada.', 'success')
    return redirect(url_for('categories'))


@app.route('/api/chart/sales-week')
@login_required
def chart_sales_week():
    dias_es = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom']
    today = datetime.now(AR_TZ).date()
    week_ago = today - timedelta(days=6)
    from sqlalchemy import func as sa_func
    rows = db.session.query(
        sa_func.date(Sale.created_at).label('day'),
        sa_func.coalesce(sa_func.sum(Sale.total), 0).label('total')
    ).filter(
        sa_func.date(Sale.created_at) >= week_ago,
        sa_func.date(Sale.created_at) <= today
    ).group_by(sa_func.date(Sale.created_at)).order_by(sa_func.date(Sale.created_at)).all()
    day_map = {str(r.day): float(r.total) for r in rows}
    days = []
    amounts = []
    for i in range(7):
        d = week_ago + timedelta(days=i)
        days.append(dias_es[d.weekday()])
        amounts.append(day_map.get(str(d), 0))
    return jsonify({'labels': days, 'data': amounts})


@app.route('/api/chart/top-products')
@login_required
def chart_top_products():
    today = datetime.now(timezone.utc)
    month = request.args.get('month', type=int, default=today.month)
    year = request.args.get('year', type=int, default=today.year)
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    results = db.session.query(
        Product.name, func.sum(SaleItem.quantity).label('total_qty')
    ).join(SaleItem, Product.id == SaleItem.product_id
    ).join(Sale, SaleItem.sale_id == Sale.id
    ).filter(Sale.created_at >= month_start, Sale.created_at < month_end
    ).group_by(Product.id).order_by(func.sum(SaleItem.quantity).desc()).limit(5).all()
    return jsonify({
        'labels': [r.name for r in results],
        'data': [int(r.total_qty) for r in results]
    })


@app.route('/history/export')
@login_required
def history_export():
    if not current_user.can_view_history():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))

    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    query = MovementLog.query

    if user_id:
        query = query.filter(MovementLog.user_id == user_id)
    if action:
        query = query.filter(MovementLog.action == action)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at <= dt_to)
        except ValueError:
            pass

    logs = query.order_by(MovementLog.created_at.desc()).limit(1000).all()
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Fecha', 'Usuario', 'Rol', 'Accion', 'Descripcion'])
    action_map = {
        'login': 'Inicio Sesion', 'logout': 'Cierre Sesion',
        'product_create': 'Crear Producto', 'product_edit': 'Editar Producto',
        'product_delete': 'Eliminar Producto', 'sale': 'Venta',
        'supplier_create': 'Crear Proveedor', 'supplier_edit': 'Editar Proveedor',
        'supplier_delete': 'Eliminar Proveedor', 'user_create': 'Crear Usuario',
        'user_toggle': 'Estado Usuario', 'user_reset_pass': 'Reset Pass'
    }
    for log in logs:
        writer.writerow([
            to_ar(log.created_at).strftime('%d/%m/%Y %H:%M'),
            log.user.get_full_name(), log.user.role,
            action_map.get(log.action, log.action), log.description
        ])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=historial.csv',
                             'Content-Type': 'text/csv; charset=utf-8'})


@app.route('/history/pdf')
@login_required
def history_pdf():
    if not current_user.can_view_history():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))

    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    query = MovementLog.query

    if user_id:
        query = query.filter(MovementLog.user_id == user_id)
    if action:
        query = query.filter(MovementLog.action == action)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at <= dt_to)
        except ValueError:
            pass

    logs = query.order_by(MovementLog.created_at.desc()).limit(500).all()
    biz_name = get_config('business_name', 'NexoControl')
    action_map = {
        'login': 'Inicio Sesion', 'logout': 'Cierre Sesion',
        'product_create': 'Crear Producto', 'product_edit': 'Editar Producto',
        'product_delete': 'Eliminar Producto', 'sale': 'Venta',
        'supplier_create': 'Crear Proveedor', 'supplier_edit': 'Editar Proveedor',
        'supplier_delete': 'Eliminar Proveedor', 'user_create': 'Crear Usuario',
        'user_toggle': 'Estado Usuario', 'user_reset_pass': 'Reset Pass'
    }
    return render_template('history_pdf.html', logs=logs, biz_name=biz_name, action_map=action_map,
                           date_from=date_from, date_to=date_to)


@app.route('/profits')
@login_required
def profits():
    if not current_user.can_view_history():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    product_id = request.args.get('product_id', type=int)
    page = request.args.get('page', 1, type=int)
    per_page = 200

    query = Sale.query
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(Sale.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(Sale.created_at <= dt_to)
        except ValueError:
            pass
    if product_id:
        query = query.filter(Sale.items.any(SaleItem.product_id == product_id))

    total_count = query.count()
    sales = query.order_by(Sale.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    sale_ids = [s.id for s in sales]
    all_items = SaleItem.query.filter(SaleItem.sale_id.in_(sale_ids)).options(db.joinedload(SaleItem.product)).all()
    items_by_sale = {}
    for item in all_items:
        items_by_sale.setdefault(item.sale_id, []).append(item)

    total_revenue = 0
    total_cost = 0
    total_profit = 0
    items_detail = []
    product_totals = {}

    for s in sales:
        revenue = s.total
        cost = 0
        sale_items = items_by_sale.get(s.id, [])
        for item in sale_items:
            c = item.product.cost * item.quantity if item.product else 0
            cost += c
            pid = item.product_id
            if pid not in product_totals:
                product_totals[pid] = {'name': item.product.name if item.product else f'#{pid}',
                                       'qty': 0, 'revenue': 0, 'cost': 0}
            product_totals[pid]['qty'] += item.quantity
            product_totals[pid]['revenue'] += item.subtotal
            product_totals[pid]['cost'] += c

        profit = revenue - cost
        total_revenue += revenue
        total_cost += cost
        total_profit += profit
        items_detail.append({
            'id': s.id,
            'date': to_ar(s.created_at).strftime('%d/%m/%Y %H:%M'),
            'user': s.user.get_full_name() if s.user else '?',
            'items_count': len(sale_items),
            'revenue': revenue,
            'cost': cost,
            'profit': profit,
            'margin': (profit / revenue * 100) if revenue > 0 else 0
        })

    product_breakdown = []
    for pid, data in product_totals.items():
        pprofit = data['revenue'] - data['cost']
        product_breakdown.append({
            'product_id': pid,
            'name': data['name'],
            'qty': data['qty'],
            'revenue': data['revenue'],
            'cost': data['cost'],
            'profit': pprofit,
            'margin': (pprofit / data['revenue'] * 100) if data['revenue'] > 0 else 0
        })
    product_breakdown.sort(key=lambda x: x['qty'], reverse=True)

    margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    products = Product.query.order_by(Product.name).all()
    return render_template('profits.html', items=items_detail,
                           total_revenue=total_revenue, total_cost=total_cost,
                           total_profit=total_profit, total_margin=margin,
                           date_from=date_from, date_to=date_to,
                           product_id=product_id, products=products,
                           product_breakdown=product_breakdown,
                           page=page, total_pages=total_pages)


@app.route('/profits/pdf')
@login_required
def profits_pdf():
    if not current_user.can_view_history():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    product_id = request.args.get('product_id', type=int)
    query = Sale.query
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(Sale.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(Sale.created_at <= dt_to)
        except ValueError:
            pass
    sales = query.order_by(Sale.created_at.desc()).all()

    if product_id:
        filtered = []
        for s in sales:
            if s.items.filter_by(product_id=product_id).count() > 0:
                filtered.append(s)
        sales = filtered

    total_revenue = 0
    total_cost = 0
    total_profit = 0
    items_detail = []
    product_totals = {}

    for s in sales:
        if product_id:
            filtered_items = s.items.filter_by(product_id=product_id).all()
            if not filtered_items:
                continue
            revenue = sum(i.subtotal for i in filtered_items)
            cost = sum(i.product.cost * i.quantity for i in filtered_items if i.product)
            for i in filtered_items:
                pid = i.product_id
                if pid not in product_totals:
                    product_totals[pid] = {'name': i.product.name if i.product else f'#{pid}',
                                           'qty': 0, 'revenue': 0, 'cost': 0}
                product_totals[pid]['qty'] += i.quantity
                product_totals[pid]['revenue'] += i.subtotal
                product_totals[pid]['cost'] += i.product.cost * i.quantity if i.product else 0
        else:
            revenue = s.total
            cost = 0
            for item in s.items:
                c = item.product.cost * item.quantity if item.product else 0
                cost += c
                pid = item.product_id
                if pid not in product_totals:
                    product_totals[pid] = {'name': item.product.name if item.product else f'#{pid}',
                                           'qty': 0, 'revenue': 0, 'cost': 0}
                product_totals[pid]['qty'] += item.quantity
                product_totals[pid]['revenue'] += item.subtotal
                product_totals[pid]['cost'] += c

        profit = revenue - cost
        total_revenue += revenue
        total_cost += cost
        total_profit += profit
        items_detail.append({
            'id': s.id,
            'date': to_ar(s.created_at).strftime('%d/%m/%Y %H:%M'),
            'user': s.user.get_full_name(),
            'items_count': len(filtered_items) if product_id else s.items.count(),
            'revenue': revenue,
            'cost': cost,
            'profit': profit,
            'margin': (profit / revenue * 100) if revenue > 0 else 0
        })

    product_breakdown = []
    for pid, data in product_totals.items():
        pprofit = data['revenue'] - data['cost']
        product_breakdown.append({
            'product_id': pid,
            'name': data['name'],
            'qty': data['qty'],
            'revenue': data['revenue'],
            'cost': data['cost'],
            'profit': pprofit,
            'margin': (pprofit / data['revenue'] * 100) if data['revenue'] > 0 else 0
        })
    product_breakdown.sort(key=lambda x: x['qty'], reverse=True)

    margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    biz_name = get_config('business_name', 'NexoControl')
    return render_template('profits_pdf.html', items=items_detail,
                           total_revenue=total_revenue, total_cost=total_cost,
                           total_profit=total_profit, total_margin=margin,
                           date_from=date_from, date_to=date_to, biz_name=biz_name,
                           product_breakdown=product_breakdown)


@app.route('/top-products')
@login_required
def top_products():
    if not current_user.can_view_products():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    query = db.session.query(
        Product.id, Product.name, Product.code,
        func.sum(SaleItem.quantity).label('total_qty'),
        func.sum(SaleItem.subtotal).label('total_amount')
    ).join(SaleItem, Product.id == SaleItem.product_id
    ).join(Sale, SaleItem.sale_id == Sale.id)
    if date_from:
        try:
            query = query.filter(Sale.created_at >= datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(Sale.created_at <= datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc))
        except ValueError:
            pass
    results = query.group_by(Product.id).order_by(func.sum(SaleItem.quantity).desc()).all()
    return render_template('top_products.html', products=results, date_from=date_from, date_to=date_to)


@app.route('/sell')
@login_required
def sell():
    if not current_user.can_sell():
        flash('No tienes permiso para vender.', 'danger')
        return redirect(url_for('dashboard'))
    products_list = Product.query.filter(Product.stock > 0).order_by(Product.name).all()
    unit_types = ['unit', 'kg', 'g', 'liter', 'ml', 'm', 'cm', 'dozen', 'pack']
    return render_template('sell.html', products=products_list, unit_types=unit_types)


@app.route('/scanner')
@login_required
def scanner_page():
    base_url = request.host_url.rstrip('/')
    scan_url = base_url + url_for('api_remote_scan') + '?code='
    app_url = base_url + url_for('scanner_app_redirect')
    return render_template('scanner.html', scan_url=scan_url, base_url=base_url, app_url=app_url)


@app.route('/scan-mobile')
@login_required
def scan_mobile():
    return render_template('scan_mobile.html')


@app.route('/scanner-app')
def scanner_app_redirect():
    ua = (request.headers.get('User-Agent') or '').lower()
    if 'iphone' in ua or 'ipad' in ua or 'ios' in ua:
        return redirect('https://itunes.apple.com/app/id1180168368')
    return redirect('https://play.google.com/store/apps/details?id=com.barcodetopc')


# Global queue + SSE for remote barcode scans (phone → browser)
_remote_scans = []
_sse_clients = []
_sse_lock = threading.Lock()

@app.route('/api/remote-scan', methods=['GET', 'POST'])
def api_remote_scan():
    code = (request.args.get('code') or request.form.get('code') or '').strip()
    if not code:
        return jsonify({'error': 'Falta código'}), 400
    _remote_scans.append(code)
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(code)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)
    return jsonify({'ok': True, 'code': code})

@app.route('/api/remote-scan/next')
@login_required
def api_remote_scan_next():
    if _remote_scans:
        return jsonify({'code': _remote_scans.pop(0)})
    return jsonify({'code': None})

@app.route('/api/events')
@login_required
def sse_events():
    q = queue.Queue()
    with _sse_lock:
        _sse_clients.append(q)
    def stream():
        try:
            while True:
                code = q.get()
                yield f"data: {json.dumps({'code': code})}\n\n"
        except GeneratorExit:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)
    return Response(stream(), mimetype='text/event-stream')


@app.route('/inventory-scan')
@login_required
def inventory_scan():
    return render_template('inventory_scan.html')


@app.route('/api/product/<code>')
@login_required
def api_product_by_code(code):
    product = Product.query.filter_by(code=code).first()
    if not product:
        return jsonify({'error': 'Producto no encontrado'}), 404
    return jsonify({
        'id': product.id,
        'code': product.code,
        'name': product.name,
        'price': product.price,
        'currency': product.currency,
        'stock': product.stock,
        'unit_type': product.unit_type
    })


@app.route('/api/product/<int:product_id>/stock', methods=['POST'])
@login_required
def api_product_stock(product_id):
    data = request.get_json()
    if not data or 'stock' not in data:
        return jsonify({'error': 'Falta stock'}), 400
    product = Product.query.get(product_id)
    if not product:
        return jsonify({'error': 'Producto no encontrado'}), 404
    new_stock = float(data['stock'])
    product.stock = new_stock
    db.session.commit()
    log = MovementLog(user_id=current_user.id, action='stock_update',
                      description=f'Ajuste manual: {product.name} → {new_stock}')
    db.session.add(log)
    db.session.commit()
    return jsonify({'ok': True, 'stock': new_stock})


@app.route('/api/products/search')
@login_required
def api_products_search():
    q = request.args.get('q', '')
    products_list = Product.query.filter(
        Product.stock > 0,
        (Product.name.ilike(f'%{q}%') | Product.code.ilike(f'%{q}%'))
    ).limit(20).all()
    return jsonify([{
        'id': p.id, 'code': p.code, 'name': p.name,
        'price': p.price, 'currency': p.currency, 'stock': p.stock,
        'unit_type': p.unit_type
    } for p in products_list])


@app.route('/sell/checkout', methods=['POST'])
@login_required
def checkout():
    data = request.get_json()
    if not data or 'items' not in data or not data['items']:
        return jsonify({'error': 'Carrito vacío'}), 400

    items_data = data['items']
    payment_method = data.get('payment_method', 'cash')
    amount_paid = float(data.get('amount_paid', 0))
    customer_email = data.get('customer_email', '').strip()
    customer_name = data.get('customer_name', '').strip()
    pending_payment = data.get('pending_payment', False)
    if pending_payment:
        payment_method = 'pending'

    total = 0
    sale_items = []

    product_ids = [item['product_id'] for item in items_data]
    products_map = {p.id: p for p in Product.query.filter(Product.id.in_(product_ids)).all()}

    for item in items_data:
        product = products_map.get(item['product_id'])
        if not product or product.stock < item['quantity']:
            return jsonify({'error': f'Stock insuficiente para {product.name if product else "producto"}'}), 400
        qty = float(item['quantity'])
        if product.wholesale_qty and product.wholesale_price and qty >= product.wholesale_qty:
            unit_price = product.wholesale_price
        else:
            unit_price = product.price
        subtotal = round(unit_price * qty, 2)
        total += subtotal
        sale_items.append({
            'product': product,
            'quantity': qty,
            'unit_price': unit_price,
            'subtotal': subtotal
        })

    total = round(total, 2)
    change = round(max(0, amount_paid - total), 2)

    if payment_method == 'cash' and amount_paid < total:
        return jsonify({'error': 'El monto recibido es menor al total'}), 400

    sale = Sale(
        user_id=current_user.id, total=total,
        payment_method=payment_method, amount_paid=amount_paid,
        change_amount=change, customer_email=customer_email,
        customer_name=customer_name,
        payment_status='pending' if pending_payment else 'paid'
    )
    db.session.add(sale)
    db.session.flush()

    items_json = []
    for si in sale_items:
        item = SaleItem(
            sale_id=sale.id, product_id=si['product'].id,
            quantity=si['quantity'], unit_price=si['unit_price'],
            subtotal=si['subtotal']
        )
        si['product'].stock -= si['quantity']
        db.session.add(item)
        items_json.append({
            'product_name': si['product'].name,
            'quantity': si['quantity'],
            'unit_price': si['unit_price'],
            'subtotal': si['subtotal'],
            'unit_type': si['product'].unit_type
        })

    db.session.commit()
    log_movement(current_user, 'sale', f'Venta #{sale.id} - Total: ${total}')

    if customer_email:
        threading.Thread(target=send_ticket_email, args=(sale, items_json, customer_email, current_user.get_full_name()), daemon=True).start()

    return jsonify({
        'success': True,
        'sale_id': sale.id,
        'total': total,
        'amount_paid': amount_paid,
        'change': change,
        'payment_method': payment_method,
        'items': items_json,
        'user': current_user.get_full_name(),
        'customer_name': customer_name,
        'customer_email': customer_email
    })


@app.route('/sale/refund/<int:sale_id>', methods=['POST'])
@login_required
def refund_sale(sale_id):
    if not current_user.can_refund_sales():
        flash('No tienes permiso para anular ventas.', 'danger')
        return redirect(url_for('history'))
    sale = db.session.get(Sale, sale_id)
    if not sale:
        flash('Venta no encontrada.', 'danger')
        return redirect(url_for('history'))
    if sale.refunded:
        flash('Esta venta ya fue anulada.', 'warning')
        return redirect(url_for('history'))
    for item in sale.items:
        product = item.product
        if product:
            product.stock += item.quantity
    sale.refunded = True
    sale.refunded_at = datetime.now(timezone.utc)
    sale.refunded_by = current_user.id
    db.session.commit()
    log_movement(current_user, 'refund', f'Venta #{sale.id} anulada - Total: ${sale.total}')
    flash(f'Venta #{sale.id} anulada y stock devuelto.', 'success')
    return redirect(url_for('history'))


@app.route('/pending-sales')
@login_required
def pending_sales():
    if not current_user.can_view_pending_sales():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    sales = Sale.query.filter_by(payment_status='pending', refunded=False).order_by(Sale.created_at.desc()).all()
    return render_template('pending_sales.html', sales=sales)


@app.route('/sale/confirm-payment/<int:sale_id>', methods=['POST'])
@login_required
def confirm_payment(sale_id):
    if not current_user.can_confirm_payment():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('pending_sales'))
    sale = db.session.get(Sale, sale_id)
    if not sale:
        flash('Venta no encontrada.', 'danger')
        return redirect(url_for('pending_sales'))
    sale.payment_status = 'paid'
    db.session.commit()
    log_movement(current_user, 'payment_confirm', f'Pago confirmado Venta #{sale.id}')
    flash(f'Venta #{sale.id} marcada como pagada.', 'success')
    return redirect(url_for('pending_sales'))


@app.route('/cash-close')
@login_required
def cash_close_page():
    if not current_user.can_close_cash():
        flash('No tienes permiso para ver cierre de caja.', 'danger')
        return redirect(url_for('dashboard'))
    today = datetime.now(AR_TZ).date()
    sales = Sale.query.filter(
        db.func.date(Sale.created_at) == today,
        Sale.refunded == False
    ).all()
    cash_sales = sum(s.total for s in sales if s.payment_method == 'cash')
    card_sales = sum(s.total for s in sales if s.payment_method == 'card')
    transfer_sales = sum(s.total for s in sales if s.payment_method == 'transfer')
    mp_sales = sum(s.total for s in sales if s.payment_method == 'mercadopago')
    total_sales = cash_sales + card_sales + transfer_sales + mp_sales
    refunds_today = Sale.query.filter(
        db.func.date(Sale.refunded_at) == today,
        Sale.refunded == True
    ).all()
    total_refunds = sum(abs(s.total) for s in refunds_today)
    today_sales_qty = len(sales)
    last_close = CashClose.query.order_by(CashClose.closed_at.desc()).first()
    all_closes = CashClose.query.order_by(CashClose.closed_at.desc()).limit(200).all()
    return render_template('cash_close.html', cash_sales=cash_sales, card_sales=card_sales,
                           transfer_sales=transfer_sales, mp_sales=mp_sales,
                           total_sales=total_sales, total_refunds=total_refunds,
                           today_sales_qty=today_sales_qty, last_close=last_close,
                           all_closes=all_closes)


@app.route('/cash-close/save', methods=['POST'])
@login_required
def cash_close_save():
    if not current_user.can_close_cash():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    today = datetime.now(AR_TZ).date()
    sales = Sale.query.filter(
        db.func.date(Sale.created_at) == today,
        Sale.refunded == False
    ).all()
    cash_sales = sum(s.total for s in sales if s.payment_method == 'cash')
    card_sales = sum(s.total for s in sales if s.payment_method == 'card')
    transfer_sales = sum(s.total for s in sales if s.payment_method == 'transfer')
    mp_sales = sum(s.total for s in sales if s.payment_method == 'mercadopago')
    total_sales = cash_sales + card_sales + transfer_sales + mp_sales
    refunds_today = Sale.query.filter(
        db.func.date(Sale.refunded_at) == today,
        Sale.refunded == True
    ).all()
    total_refunds = sum(abs(s.total) for s in refunds_today)
    initial_amount = float(request.form.get('initial_amount', 0))
    declared_cash = float(request.form.get('declared_cash', 0))
    expected_cash = initial_amount + cash_sales - total_refunds
    cc = CashClose(
        user_id=current_user.id, opened_at=datetime.now(timezone.utc),
        initial_amount=initial_amount, cash_sales=cash_sales,
        card_sales=card_sales, transfer_sales=transfer_sales,
        mp_sales=mp_sales, total_sales=total_sales,
        total_refunds=total_refunds, expected_cash=expected_cash,
        declared_cash=declared_cash, difference=declared_cash - expected_cash,
        notes=request.form.get('notes', '')
    )
    db.session.add(cc)
    db.session.commit()
    log_movement(current_user, 'cash_close', f'Cierre de caja: efectivo ${declared_cash}')
    flash('Cierre de caja guardado.', 'success')
    return redirect(url_for('cash_close_page'))


@app.route('/cash-close/void/<int:id>', methods=['POST'])
@login_required
def cash_close_void(id):
    if not current_user.can_void_cash_close():
        flash('No tienes permiso para anular cierres.', 'danger')
        return redirect(url_for('cash_close_page'))
    cc = db.session.get(CashClose, id)
    if not cc:
        flash('Cierre no encontrado.', 'danger')
        return redirect(url_for('cash_close_page'))
    cc.voided = True
    cc.voided_at = datetime.now(timezone.utc)
    cc.voided_by = current_user.id
    db.session.commit()
    log_movement(current_user, 'cash_close_void', f'Cierre de caja #{cc.id} anulado')
    flash('Cierre de caja anulado.', 'success')
    return redirect(url_for('cash_close_page'))


@app.route('/history/export-excel')
@login_required
def history_export_excel():
    if not current_user.can_view_history():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    query = MovementLog.query
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at <= dt_to)
        except ValueError:
            pass
    logs = query.order_by(MovementLog.created_at.desc()).limit(500).all()
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(['Fecha', 'Usuario', 'Rol', 'Accion', 'Descripcion'])
    for log in logs:
        writer.writerow([
            to_ar(log.created_at).strftime('%d/%m/%Y %H:%M'),
            log.user.get_full_name(), log.user.role,
            log.action, log.description
        ])
    mem = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    return send_file(mem, mimetype='text/csv', as_attachment=True,
                     download_name=f'historial_{date_from or "todo"}_{date_to or "todo"}.csv')


@app.route('/api/create-mp-payment', methods=['POST'])
@login_required
def create_mp_payment():
    if not current_user.can_sell():
        return jsonify({'error': 'Permiso denegado'}), 403

    access_token = get_config('mp_access_token', '')
    if not access_token:
        return jsonify({'error': 'Mercado Pago no configurado. Andá a Config → Mercado Pago'}), 400

    data = request.get_json()
    sale_id = data.get('sale_id')
    total = data.get('total')
    description = data.get('description', 'Venta Punto de Venta')

    if not sale_id or not total:
        return jsonify({'error': 'Faltan datos'}), 400

    try:
        import requests
        host = request.host_url.rstrip('/')
        resp = requests.post('https://api.mercadopago.com/checkout/preferences', json={
            'items': [{
                'title': description,
                'quantity': 1,
                'unit_price': float(total),
                'currency_id': 'ARS'
            }],
            'back_urls': {
                'success': host + url_for('mp_success', sale_id=sale_id),
                'failure': host + url_for('sell'),
                'pending': host + url_for('sell')
            },
            'auto_return': 'approved',
            'notification_url': host + url_for('mp_webhook'),
            'external_reference': str(sale_id)
        }, headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        })
        data = resp.json()
        if 'id' not in data:
            return jsonify({'error': 'Error de Mercado Pago: ' + str(data.get('message', 'desconocido'))}), 400

        sale = db.session.get(Sale, sale_id)
        if sale:
            sale.mp_status = 'pending'
            db.session.commit()

        return jsonify({
            'success': True,
            'payment_id': data['id'],
            'init_point': data['init_point'],
            'sandbox_init_point': data.get('sandbox_init_point', '')
        })
    except Exception as e:
        return jsonify({'error': 'Error al conectar con Mercado Pago: ' + str(e)[:100]}), 500


@app.route('/mp-success/<int:sale_id>')
@login_required
def mp_success(sale_id):
    flash('Pago procesado. Verificá el estado en el historial.', 'success')
    return redirect(url_for('sell'))


@app.route('/mp-membership-success')
@login_required
def mp_membership_success():
    flash('Pago procesado. La membresía se actualizará automáticamente.', 'success')
    return redirect(url_for('membership'))


@app.route('/api/create-mp-membership-payment', methods=['POST'])
@login_required
def create_mp_membership_payment():
    access_token = get_config('mp_membership_access_token', '')
    if not access_token:
        return jsonify({'error': 'Mercado Pago no configurado. Ingresá tu Access Token en Membresía.'}), 400

    price = get_config('membership_price', '10')
    try:
        price = float(price)
    except ValueError:
        price = 10

    try:
        import requests
        host = request.host_url.rstrip('/')
        resp = requests.post('https://api.mercadopago.com/checkout/preferences', json={
            'items': [{
                'title': 'Suscripción mensual - ' + get_config('business_name', 'SmartPost'),
                'quantity': 1,
                'unit_price': price,
                'currency_id': 'ARS'
            }],
            'back_urls': {
                'success': host + url_for('mp_membership_success'),
                'failure': host + url_for('membership'),
                'pending': host + url_for('membership')
            },
            'auto_return': 'approved',
            'notification_url': host + url_for('mp_webhook'),
            'external_reference': 'membership_' + get_instance_id()
        }, headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        })
        data = resp.json()
        if 'id' not in data:
            return jsonify({'error': 'Error MP: ' + str(data.get('message', 'desconocido'))}), 400

        cfg = Config.query.filter_by(key='mp_membership_preference_id').first()
        if not cfg:
            cfg = Config(key='mp_membership_preference_id', value='')
            db.session.add(cfg)
        cfg.value = data['id']
        db.session.commit()

        return jsonify({
            'success': True,
            'payment_id': data['id'],
            'init_point': data['init_point'],
            'sandbox_init_point': data.get('sandbox_init_point', '')
        })
    except Exception as e:
        return jsonify({'error': 'Error al conectar con MP: ' + str(e)[:100]}), 500


@app.route('/api/mp-membership-status')
@login_required
def mp_membership_status():
    cfg = Config.query.filter_by(key='mp_membership_preference_id').first()
    pref_id = cfg.value if cfg else ''
    if not pref_id:
        return jsonify({'status': 'none'})

    access_token = get_config('mp_membership_access_token', '')
    if not access_token:
        return jsonify({'status': 'none'})

    try:
        import requests
        ref = 'membership_' + get_instance_id()
        resp = requests.get(f'https://api.mercadopago.com/v1/payments/search?external_reference={ref}&sort=date_created&criteria=desc&limit=1',
                            headers={'Authorization': f'Bearer {access_token}'})
        data = resp.json()
        results = data.get('results', [])
        if results:
            pay = results[0]
            status = pay.get('status', 'pending')
            if status == 'approved':
                cfg.value = ''
                db.session.commit()
                return jsonify({'status': 'approved', 'payment_id': pay.get('id')})
            return jsonify({'status': status, 'payment_id': pay.get('id')})
        return jsonify({'status': 'pending'})
    except Exception:
        return jsonify({'status': 'pending'})


@app.route('/api/mp-webhook', methods=['POST'])
def mp_webhook():
    try:
        data = request.get_json()
        if data and data.get('type') == 'payment':
            payment_id = data.get('data', {}).get('id')
            if payment_id:
                import requests
                tokens = [
                    ('membership', get_config('mp_membership_access_token', '')),
                    ('store', get_config('mp_access_token', ''))
                ]
                for token_type, access_token in tokens:
                    if not access_token:
                        continue
                    try:
                        resp = requests.get(f'https://api.mercadopago.com/v1/payments/{payment_id}',
                                            headers={'Authorization': f'Bearer {access_token}'})
                        pay = resp.json()
                    except Exception:
                        continue
                    ext_ref = pay.get('external_reference')
                    if not ext_ref:
                        continue
                    if ext_ref == 'membership_' + get_instance_id():
                        if pay.get('status') == 'approved':
                            from dateutil.relativedelta import relativedelta
                            today = datetime.now(AR_TZ).date()
                            cfg = Config.query.filter_by(key='membership_expiry').first()
                            if cfg and cfg.value:
                                try:
                                    current = datetime.strptime(cfg.value, '%Y-%m-%d').date()
                                except (ValueError, TypeError):
                                    current = today
                                base = max(current, today)
                            else:
                                base = today
                                if not cfg:
                                    cfg = Config(key='membership_expiry', value='')
                                    db.session.add(cfg)
                            new_expiry = base + relativedelta(months=1, day=1)
                            cfg.value = new_expiry.strftime('%Y-%m-%d')
                            pref = Config.query.filter_by(key='mp_membership_preference_id').first()
                            if pref:
                                pref.value = ''
                            db.session.commit()
                        break
                    else:
                        try:
                            sale_id = int(ext_ref)
                        except (ValueError, TypeError):
                            continue
                        sale = db.session.get(Sale, sale_id)
                        if sale:
                            sale.mp_payment_id = str(payment_id)
                            sale.mp_status = pay.get('status', 'unknown')
                            if pay.get('status') == 'approved':
                                sale.payment_method = 'mercadopago'
                                if sale.payment_status == 'pending':
                                    sale.payment_status = 'paid'
                            db.session.commit()
                        break
    except Exception:
        pass
    return '', 200


@app.route('/api/mp-status/<int:sale_id>')
@login_required
def mp_status(sale_id):
    sale = db.session.get(Sale, sale_id)
    if not sale:
        return jsonify({'error': 'Venta no encontrada'}), 404
    # Also try direct query to MP if pending
    if sale.mp_status in ('pending', '') and sale.mp_payment_id:
        access_token = get_config('mp_access_token', '')
        if access_token:
            try:
                import requests
                resp = requests.get(f'https://api.mercadopago.com/v1/payments/{sale.mp_payment_id}',
                                    headers={'Authorization': f'Bearer {access_token}'})
                pay = resp.json()
                if 'status' in pay:
                    sale.mp_status = pay['status']
                    if pay['status'] == 'approved':
                        sale.payment_method = 'mercadopago'
                    db.session.commit()
            except Exception:
                pass
    return jsonify({
        'sale_id': sale.id,
        'mp_payment_id': sale.mp_payment_id or '',
        'mp_status': sale.mp_status or 'pending',
        'payment_method': sale.payment_method,
        'total': sale.total
    })


@app.route('/ticket/<int:id>')
@login_required
def ticket(id):
    sale = db.session.get(Sale, id)
    if not sale:
        flash('Venta no encontrada.', 'danger')
        return redirect(url_for('sell'))
    items = SaleItem.query.filter_by(sale_id=sale.id).all()
    thermal = request.args.get('thermal') == '1'
    return render_template('ticket.html', sale=sale, items=items, thermal=thermal)


@app.route('/ticket/<int:id>/pdf')
@login_required
def ticket_pdf(id):
    import weasyprint
    sale = db.session.get(Sale, id)
    if not sale:
        abort(404)
    items = SaleItem.query.filter_by(sale_id=sale.id).all()
    html = render_template('ticket_pdf.html', sale=sale, items=items)
    pdf = weasyprint.HTML(string=html).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ticket_{id}.pdf'
    return response


@app.route('/api/sale/<int:id>/send-email', methods=['POST'])
@login_required
def api_send_ticket_email(id):
    data = request.get_json()
    email = data.get('email', '').strip()
    if not email:
        return jsonify({'error': 'Email requerido'}), 400
    if not current_user.can_view_history():
        return jsonify({'error': 'Permiso denegado'}), 403
    sale = db.session.get(Sale, id)
    if not sale:
        return jsonify({'error': 'Venta no encontrada'}), 404
    items = SaleItem.query.filter_by(sale_id=sale.id).all()
    items_json = [{
        'product_name': item.product.name if item.product else 'Eliminado',
        'quantity': item.quantity,
        'unit_price': item.unit_price,
        'subtotal': item.subtotal,
        'unit_type': item.product.unit_type if item.product else 'unit'
    } for item in items]
    if send_ticket_email(sale, items_json, email, current_user.get_full_name()):
        return jsonify({'success': True, 'message': 'Ticket enviado por email'})
    else:
        return jsonify({'error': 'No se pudo enviar el email. Verificá la configuración SMTP en Admin > Config.'}), 500


@app.route('/api/products/send-email', methods=['POST'])
@login_required
def api_send_products_email():
    if not current_user.can_view_products():
        return jsonify({'error': 'Permiso denegado'}), 403
    data = request.get_json()
    email = (data.get('email') or '').strip()
    if not email:
        return jsonify({'error': 'Email requerido'}), 400
    products_list = Product.query.order_by(Product.name).all()
    rows = ''.join(f'<tr><td>{p.code}</td><td>{p.name}</td><td>${p.price:,.2f}</td><td>{p.stock}</td></tr>'
                    for p in products_list)
    html = f'''<h3>Lista de Productos</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;font-family:sans-serif;">
<thead style="background:#3d5a80;color:#fff;"><tr><th>Código</th><th>Nombre</th><th>Precio</th><th>Stock</th></tr></thead>
<tbody>{rows}</tbody></table>
<p style="color:#888;font-size:12px;margin-top:20px;">Enviado desde NexoControl</p>'''
    if send_email(email, 'Lista de Productos', html):
        return jsonify({'success': True, 'message': 'Lista enviada por email'})
    return jsonify({'error': 'No se pudo enviar. Verificá la configuración SMTP en Config.'}), 500


@app.route('/api/send-payment-link', methods=['POST'])
@login_required
def api_send_payment_link():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    link = (data.get('link') or '').strip()
    sale_id = data.get('sale_id', '')
    if not email or not link:
        return jsonify({'error': 'Email y link requeridos'}), 400
    biz = get_config('business_name', 'NexoControl')
    html = f'''<h2 style="color:#3d5a80;">Pago pendiente</h2>
<p>Hacé click en el siguiente link para pagar tu compra:</p>
<p style="text-align:center;margin:20px 0;">
<a href="{link}" style="background:#3d5a80;color:#fff;padding:12px 30px;border-radius:6px;text-decoration:none;font-size:18px;">Pagar ahora</a>
</p>
<p class="text-muted" style="font-size:13px;">Link generado por {biz}</p>'''
    if send_email(email, f'Link de pago - {biz}', html):
        return jsonify({'success': True})
    return jsonify({'error': 'No se pudo enviar. Verificá la configuración SMTP en Config.'}), 500


# ─── Onboarding (formulario para el cliente) ───────────────────
ONBOARDING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'onboarding')
os.makedirs(ONBOARDING_DIR, exist_ok=True)


@app.route('/onboarding')
def onboarding():
    vis = app.config.get('ONBOARDING_VISIBLE_SECTIONS')
    if vis is None:
        raw = get_config('onboarding_visible_sections', '')
        try:
            vis = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            vis = []
        app.config['ONBOARDING_VISIBLE_SECTIONS'] = vis
    fc = app.config.get('ONBOARDING_FIELDS_CONFIG')
    if fc is None:
        raw_fc = get_config('onboarding_fields_config', '')
        try:
            fc = json.loads(raw_fc) if raw_fc else {}
        except (json.JSONDecodeError, TypeError):
            fc = {}
        # Auto-migrate old config keys (roles_role_admin etc) -> reset to empty so new defaults show
        if any(k.startswith('roles_role_') for k in fc):
            fc = {}
            cfg = Config.query.filter_by(key='onboarding_fields_config').first()
            if cfg:
                db.session.delete(cfg)
                db.session.commit()
        app.config['ONBOARDING_FIELDS_CONFIG'] = fc
    return render_template('onboarding.html', visible_sections=vis, field_config=fc)


@app.route('/onboarding/submit', methods=['POST'])
def onboarding_submit():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    data = {
        'submitted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'business_name': request.form.get('business_name', '').strip(),
        'local_name': request.form.get('local_name', '').strip(),
        'owner_name': request.form.get('owner_name', '').strip(),
        'address': request.form.get('address', '').strip(),
        'phone': request.form.get('phone', '').strip(),
        'email': request.form.get('email', '').strip(),
        'cuit': request.form.get('cuit', '').strip(),
        'timezone': request.form.get('timezone', 'America/Argentina/Buenos_Aires'),
        'currency': request.form.get('currency', 'ARS'),
        'default_markup': request.form.get('default_markup', '30'),
        'low_stock_threshold': request.form.get('low_stock_threshold', '10'),
        'critical_stock_threshold': request.form.get('critical_stock_threshold', '5'),
        'ticket_header': request.form.get('ticket_header', '').strip(),
        'ticket_footer': request.form.get('ticket_footer', '').strip(),
        'ticket_show_logo': True if request.form.get('ticket_show_logo') else False,
        'ticket_show_cuit': True if request.form.get('ticket_show_cuit') else False,
        'admin_user': request.form.get('admin_user', 'admin').strip(),
        'admin_password': request.form.get('admin_password', '').strip(),
        'port': request.form.get('port', '').strip(),
        'domain_option': request.form.get('domain_option', 'subdominio'),
        'custom_domain': request.form.get('custom_domain', '').strip(),
        'smtp_host': request.form.get('smtp_host', ''),
        'smtp_port': request.form.get('smtp_port', '587'),
        'smtp_user': request.form.get('smtp_user', ''),
        'smtp_password': request.form.get('smtp_password', ''),
        'mp_access_token': request.form.get('mp_access_token', ''),
        'drive_enabled': True if request.form.get('drive_enabled') else False,
        'backup_frequency': request.form.get('backup_frequency', 'daily'),
        'extra_users': request.form.get('extra_users', '').strip(),
        'permissions': {k.replace('perm_', ''): True for k, v in request.form.items() if k.startswith('perm_')},
        'categories': request.form.get('categories', '').strip(),
        'suppliers': request.form.get('suppliers', '').strip(),
        'notes': request.form.get('notes', ''),
        'confidencialidad_aceptado': True if request.form.get('confidencialidad_aceptado') else False,
        'confidencialidad_fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S') if request.form.get('confidencialidad_aceptado') else '',
    }
    filename = f'onboarding_{ts}'
    filepath = os.path.join(ONBOARDING_DIR, filename + '.json')
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Save uploaded files
    allowed_images = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico'}
    for field in ['logo', 'favicon', 'drive_json', 'products_file']:
        f = request.files.get(field)
        if f and f.filename:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            save_name = f'{filename}_{field}.{ext}' if ext else f'{filename}_{field}'
            save_path = os.path.join(ONBOARDING_DIR, save_name)
            f.save(save_path)
            data[f'{field}_file'] = save_name

    # Update JSON with file refs
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return render_template('onboarding_thanks.html', name=data['business_name'] or data['owner_name'] or 'Cliente')


@app.route('/admin/onboarding')
@login_required
def admin_onboarding():
    if current_user.role != 'admin':
        flash('Solo admin.', 'danger')
        return redirect(url_for('dashboard'))
    submissions = []
    for f in sorted(os.listdir(ONBOARDING_DIR), reverse=True):
        if f.startswith('onboarding_') and f.endswith('.json'):
            fpath = os.path.join(ONBOARDING_DIR, f)
            with open(fpath, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            data['_file'] = f
            data['_ts'] = f.replace('onboarding_', '').replace('.json', '')
            submissions.append(data)
    raw_config = Config.query.filter_by(key='onboarding_visible_sections').first()
    try:
        current_sections = json.loads(raw_config.value) if raw_config and raw_config.value else []
    except (json.JSONDecodeError, TypeError):
        current_sections = []
    return render_template('onboarding_list.html', submissions=submissions, config={'onboarding_visible_sections': current_sections})


@app.route('/admin/onboarding/<filename>/delete', methods=['POST'])
@login_required
def admin_onboarding_delete(filename):
    if current_user.role != 'admin':
        flash('Solo admin.', 'danger')
        return redirect(url_for('dashboard'))
    fpath = os.path.join(ONBOARDING_DIR, filename)
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        os.remove(fpath)
        for key in list(data.keys()):
            if key.endswith('_file') and data[key]:
                fdel = os.path.join(ONBOARDING_DIR, data[key])
                if os.path.exists(fdel):
                    os.remove(fdel)
        flash('Eliminado.', 'success')
    return redirect(url_for('admin_onboarding'))


@app.route('/onboarding/confidencialidad')
def confidencialidad():
    cfg = Config.query.all()
    configs = {c.key: c.value for c in cfg}
    return render_template('confidencialidad.html',
                           business_name=configs.get('business_name', 'SmartPost'),
                            now=lambda: datetime.now(AR_TZ))


@app.route('/onboarding/files/<filename>')
@login_required
def onboarding_file(filename):
    fpath = os.path.join(ONBOARDING_DIR, filename)
    if not os.path.exists(fpath):
        abort(404)
    return send_file(fpath)


@app.route('/admin/onboarding/config', methods=['POST'])
@login_required
def admin_onboarding_config():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    sections = request.form.getlist('sections')
    val = json.dumps(sections)
    c = Config.query.filter_by(key='onboarding_visible_sections').first()
    if c:
        c.value = val
    else:
        db.session.add(Config(key='onboarding_visible_sections', value=val))
    app.config.pop('ONBOARDING_VISIBLE_SECTIONS', None)
    flash('Formulario actualizado.', 'success')
    return redirect(url_for('admin_onboarding'))


@app.route('/admin/onboarding/field-config', methods=['POST'])
@login_required
def admin_onboarding_field_config():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    raw = request.form.get('config', '{}')
    try:
        val = json.dumps(json.loads(raw), ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        flash('JSON inválido.', 'danger')
        return redirect(url_for('admin_onboarding'))
    c = Config.query.filter_by(key='onboarding_fields_config').first()
    if c:
        c.value = val
    else:
        db.session.add(Config(key='onboarding_fields_config', value=val))
    app.config.pop('ONBOARDING_FIELDS_CONFIG', None)
    app.config.pop('ONBOARDING_VISIBLE_SECTIONS', None)
    flash('Campos actualizados.', 'success')
    return redirect(url_for('admin_onboarding'))


@app.route('/admin/onboarding/field-config/editor', methods=['GET', 'POST'])
@login_required
def admin_onboarding_field_editor():
    if current_user.role != 'admin':
        flash('Solo admin.', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        config = {}
        raw_keys = request.form.getlist('key[]')
        raw_labels = request.form.getlist('label[]')
        raw_placeholder = request.form.getlist('placeholder[]')
        raw_help = request.form.getlist('help[]')
        raw_default = request.form.getlist('default[]')
        raw_visible = request.form.getlist('visible[]')
        for i, k in enumerate(raw_keys):
            if not k.strip():
                continue
            entry = {}
            lbl = raw_labels[i].strip() if i < len(raw_labels) else ''
            if lbl:
                entry['label'] = lbl
            ph = raw_placeholder[i].strip() if i < len(raw_placeholder) else ''
            if ph:
                entry['placeholder'] = ph
            hp = raw_help[i].strip() if i < len(raw_help) else ''
            if hp:
                entry['help'] = hp
            df = raw_default[i].strip() if i < len(raw_default) else ''
            if df:
                entry['default'] = df
            entry['visible'] = k in raw_visible
            config[k] = entry
        val = json.dumps(config, ensure_ascii=False)
        c = Config.query.filter_by(key='onboarding_fields_config').first()
        if c:
            c.value = val
        else:
            db.session.add(Config(key='onboarding_fields_config', value=val))
        app.config.pop('ONBOARDING_FIELDS_CONFIG', None)
        app.config.pop('ONBOARDING_VISIBLE_SECTIONS', None)
        flash('Campos actualizados.', 'success')
        return redirect(url_for('admin_onboarding'))

    # GET - load current config
    raw_fc = get_config('onboarding_fields_config', '')
    try:
        fc = json.loads(raw_fc) if raw_fc else {}
    except (json.JSONDecodeError, TypeError):
        fc = {}
    return render_template('onboarding_field_editor.html', field_config=fc)


@app.route('/onboarding/confidencialidad/pdf')
def confidencialidad_pdf():
    cfg = Config.query.all()
    configs = {c.key: c.value for c in cfg}
    return render_template('confidencialidad.html',
                           business_name=configs.get('business_name', 'SmartPost'),
                           now=lambda: datetime.now(AR_TZ),
                           pdf_mode=True)


@app.route('/admin/onboarding/<filename>/confidencialidad')
@login_required
def admin_confidencialidad(filename):
    if current_user.role != 'admin':
        flash('Solo admin.', 'danger')
        return redirect(url_for('dashboard'))
    fpath = os.path.join(ONBOARDING_DIR, filename)
    if not os.path.exists(fpath):
        flash('Archivo no encontrado.', 'danger')
        return redirect(url_for('admin_onboarding'))
    with open(fpath, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    cfg = Config.query.all()
    configs = {c.key: c.value for c in cfg}
    return render_template('confidencialidad.html',
                           business_name=configs.get('business_name', 'SmartPost'),
                           cliente=data.get('business_name', ''),
                           cliente_titular=data.get('owner_name', ''),
                           cliente_email=data.get('email', ''),
                           cliente_direccion=data.get('address', ''),
                           cliente_cuit=data.get('cuit', ''),
                            now=lambda: datetime.now(AR_TZ))


@app.route('/api/landing-contact', methods=['POST'])
def landing_contact():
    import smtplib
    from email.mime.text import MIMEText
    name = request.form.get('name', '').strip()
    company = request.form.get('company', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    message = request.form.get('message', '').strip()
    if not name or not email or not message:
        return jsonify({'ok': False, 'error': 'Faltan campos obligatorios'}), 400
    body = f"""Nuevo contacto desde Nexora Apps Landing

Nombre: {name}
Empresa: {company or '—'}
Email: {email}
Teléfono: {phone or '—'}

Mensaje:
{message}
"""
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = f'Nuevo contacto: {name} - {company or email}'
        msg['From'] = 'sistemas.nexoraapps@gmail.com'
        msg['To'] = 'sistemas.nexoraapps@gmail.com'
        s = smtplib.SMTP('127.0.0.1', 25)
        s.send_message(msg)
        s.quit()
        return jsonify({'ok': True, 'redirect': 'https://www.nexoraapps.com.ar?sent=ok'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/suppliers')
@login_required
def suppliers():
    if not current_user.can_view_suppliers():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    suppliers_list = Supplier.query.order_by(Supplier.name).limit(500).all()
    return render_template('suppliers.html', suppliers=suppliers_list)


@app.route('/suppliers/add', methods=['POST'])
@login_required
def supplier_add():
    if not current_user.can_add_suppliers():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('suppliers'))
    if get_config('demo_mode', '') == 'on' and Supplier.query.count() >= 10:
        flash('Modo Demo: máximo 10 proveedores. Contratá el servicio completo.', 'warning')
        return redirect(url_for('suppliers'))
    supplier = Supplier(
        name=request.form.get('name'),
        contact=request.form.get('contact', ''),
        phone=request.form.get('phone', ''),
        email=request.form.get('email', ''),
        address=request.form.get('address', '')
    )
    db.session.add(supplier)
    db.session.commit()
    log_movement(current_user, 'supplier_create', f'Proveedor creado: {supplier.name}')
    flash('Proveedor agregado.', 'success')
    return redirect(url_for('suppliers'))


@app.route('/suppliers/edit/<int:id>', methods=['POST'])
@login_required
def supplier_edit(id):
    if not current_user.can_edit_suppliers():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('suppliers'))
    supplier = db.session.get(Supplier, id)
    if not supplier:
        flash('No encontrado.', 'danger')
        return redirect(url_for('suppliers'))
    supplier.name = request.form.get('name')
    supplier.contact = request.form.get('contact', '')
    supplier.phone = request.form.get('phone', '')
    supplier.email = request.form.get('email', '')
    supplier.address = request.form.get('address', '')
    db.session.commit()
    log_movement(current_user, 'supplier_edit', f'Proveedor editado: {supplier.name}')
    flash('Proveedor actualizado.', 'success')
    return redirect(url_for('suppliers'))


@app.route('/suppliers/delete/<int:id>', methods=['POST'])
@login_required
def supplier_delete(id):
    if not current_user.can_delete_suppliers():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('suppliers'))
    supplier = db.session.get(Supplier, id)
    if supplier:
        db.session.delete(supplier)
        db.session.commit()
        log_movement(current_user, 'supplier_delete', f'Proveedor eliminado: {supplier.name}')
        flash('Proveedor eliminado.', 'success')
    return redirect(url_for('suppliers'))


@app.route('/users')
@login_required
def users():
    if not current_user.can_manage_users():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    users_list = User.query.order_by(User.username).all()
    if current_user.role != 'admin':
        users_list = [u for u in users_list if u.role != 'admin']
    configs = {c.key: c.value for c in Config.query.all()}
    return render_template('users.html', users=users_list, configs=configs)


@app.route('/users/add', methods=['POST'])
@login_required
def user_add():
    if not current_user.can_manage_users():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('users'))
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'user')
    first_name = request.form.get('first_name', '')
    last_name = request.form.get('last_name', '')

    if User.query.filter_by(username=username).first():
        flash('El usuario ya existe.', 'danger')
        return redirect(url_for('users'))

    if current_user.role != 'admin':
        max_users = get_config('max_users', '0')
        if max_users and max_users != '0':
            non_admin_count = User.query.filter(User.role != 'admin').count()
            if non_admin_count >= int(max_users):
                flash(f'Límite alcanzado: máximo {max_users} usuarios (sin contar admin).', 'danger')
                return redirect(url_for('users'))

    user = User(username=username, role=role, active=True, first_name=first_name, last_name=last_name)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    log_movement(current_user, 'user_create', f'Usuario creado: {username} ({role})')
    flash('Usuario creado.', 'success')
    return redirect(url_for('users'))


@app.route('/users/toggle/<int:id>', methods=['POST'])
@login_required
def user_toggle(id):
    if not current_user.can_toggle_users():
        flash('No tienes permiso para activar/desactivar usuarios.', 'danger')
        return redirect(url_for('users'))
    user = db.session.get(User, id)
    if user and user.id != current_user.id:
        user.active = not user.active
        db.session.commit()
        state = 'activado' if user.active else 'desactivado'
        log_movement(current_user, 'user_toggle', f'Usuario {user.username} {state}')
        flash(f'Usuario {state}.', 'success')
    return redirect(url_for('users'))


@app.route('/users/reset-password/<int:id>', methods=['POST'])
@login_required
def user_reset_password(id):
    if not current_user.can_reset_user_password():
        flash('No tienes permiso para resetear contraseñas.', 'danger')
        return redirect(url_for('users'))
    user = db.session.get(User, id)
    if not user:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('users'))
    password = request.form.get('password', '123456')
    user.set_password(password)
    db.session.commit()
    log_movement(current_user, 'user_reset_pass', f'Contraseña reseteada para {user.username}')
    flash(f'Contraseña de {user.username} reseteada.', 'success')
    return redirect(url_for('users'))


@app.route('/users/delete/<int:id>', methods=['POST'])
@login_required
def user_delete(id):
    if not current_user.can_delete_users():
        flash('No tienes permiso para eliminar usuarios.', 'danger')
        return redirect(url_for('users'))
    user = db.session.get(User, id)
    if not user:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('users'))
    if user.id == current_user.id:
        flash('No podés eliminarte a vos mismo.', 'danger')
        return redirect(url_for('users'))
    if user.role == 'admin':
        flash('No podés eliminar a otro admin.', 'danger')
        return redirect(url_for('users'))
    username = user.username
    MovementLog.query.filter_by(user_id=id).update({MovementLog.user_id: current_user.id})
    Sale.query.filter_by(user_id=id).update({Sale.user_id: current_user.id})
    db.session.delete(user)
    db.session.commit()
    log_movement(current_user, 'user_delete', f'Usuario eliminado: {username}')
    flash(f'Usuario "{username}" eliminado. Sus movimientos y ventas fueron reasignados a vos.', 'success')
    return redirect(url_for('users'))


@app.route('/admin/change-password', methods=['POST'])
@login_required
def admin_change_password():
    if current_user.role != 'admin':
        flash('Solo admin.', 'danger')
        return redirect(url_for('settings'))
    current_pw = request.form.get('current_password', '')
    new_pw = request.form.get('new_password', '')
    if not current_user.check_password(current_pw):
        flash('Contraseña actual incorrecta.', 'danger')
        return redirect(url_for('settings'))
    if len(new_pw) < 4:
        flash('La nueva contraseña debe tener al menos 4 caracteres.', 'danger')
        return redirect(url_for('settings'))
    current_user.set_password(new_pw)
    db.session.commit()
    flash('Contraseña cambiada correctamente.', 'success')
    return redirect(url_for('settings'))


@app.route('/history')
@login_required
def history():
    if not current_user.can_view_history():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.order_by(User.username).all()
    products = Product.query.order_by(Product.name).all()
    return render_template('history.html', users=users, products=products)


@app.route('/admin/clear-history', methods=['POST'])
@login_required
def clear_history():
    if current_user.role != 'admin':
        flash('Solo el admin puede limpiar el historial', 'danger')
        return redirect(url_for('history'))
    date_from = request.form.get('date_from', '').strip()
    date_to = request.form.get('date_to', '').strip()
    query_sales = Sale.query
    query_logs = MovementLog.query
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query_sales = query_sales.filter(Sale.created_at >= dt_from)
            query_logs = query_logs.filter(MovementLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query_sales = query_sales.filter(Sale.created_at <= dt_to)
            query_logs = query_logs.filter(MovementLog.created_at <= dt_to)
        except ValueError:
            pass
    sales = query_sales.all()
    sale_ids = [s.id for s in sales]
    for sale in sales:
        items = SaleItem.query.filter_by(sale_id=sale.id).all()
        db.session.add(DeletedRecord(
            record_type='sale', record_id=sale.id,
            data_json=json.dumps({
                'total': sale.total, 'payment_method': sale.payment_method,
                'amount_paid': sale.amount_paid, 'change_amount': sale.change_amount,
                'customer_email': sale.customer_email,
                'user_id': sale.user_id, 'created_at': sale.created_at.isoformat() if sale.created_at else None,
                'items': [{'product_id': i.product_id, 'quantity': i.quantity,
                           'unit_price': i.unit_price, 'subtotal': i.subtotal} for i in items]
            }),
            deleted_by=current_user.id,
        ))
    logs = query_logs.all()
    for log in logs:
        db.session.add(DeletedRecord(
            record_type='movement', record_id=log.id,
            data_json=json.dumps({'action': log.action, 'description': log.description,
                                  'user_id': log.user_id, 'created_at': log.created_at.isoformat() if log.created_at else None}),
            deleted_by=current_user.id,
        ))
    if sale_ids:
        SaleItem.query.filter(SaleItem.sale_id.in_(sale_ids)).delete(synchronize_session=False)
        Sale.query.filter(Sale.id.in_(sale_ids)).delete(synchronize_session=False)
    count = query_logs.delete(synchronize_session=False)
    db.session.commit()
    flash(f'Historial limpiado: {len(sale_ids)} ventas y {count} movimientos eliminados', 'success')
    return redirect(url_for('history'))


@app.route('/api/log/<int:log_id>/delete', methods=['POST'])
@login_required
def delete_log(log_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    log = db.session.get(MovementLog, log_id)
    if not log:
        return jsonify({'error': 'Movimiento no encontrado'}), 404
    deleted = DeletedRecord(
        record_type='movement',
        record_id=log.id,
        data_json=json.dumps({'action': log.action, 'description': log.description,
                              'user_id': log.user_id, 'created_at': log.created_at.isoformat() if log.created_at else None}),
        deleted_by=current_user.id,
    )
    db.session.add(deleted)
    if log.action == 'sale':
        import re
        match = re.search(r'#(\d+)', log.description)
        if match:
            sale_id = int(match.group(1))
            sale = db.session.get(Sale, sale_id)
            if sale:
                items = SaleItem.query.filter_by(sale_id=sale_id).all()
                deleted2 = DeletedRecord(
                    record_type='sale',
                    record_id=sale.id,
                    data_json=json.dumps({
                        'total': sale.total, 'payment_method': sale.payment_method,
                        'amount_paid': sale.amount_paid, 'change_amount': sale.change_amount,
                        'customer_email': sale.customer_email,
                        'user_id': sale.user_id, 'created_at': sale.created_at.isoformat() if sale.created_at else None,
                        'items': [{'product_id': i.product_id, 'quantity': i.quantity,
                                   'unit_price': i.unit_price, 'subtotal': i.subtotal} for i in items]
                    }),
                    deleted_by=current_user.id,
                )
                db.session.add(deleted2)
                SaleItem.query.filter_by(sale_id=sale_id).delete()
                Sale.query.filter_by(id=sale_id).delete()
    db.session.delete(log)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/trash')
@login_required
def trash():
    if not current_user.can_view_trash():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    deleted = DeletedRecord.query.order_by(DeletedRecord.deleted_at.desc()).limit(200).all()
    users = {u.id: u for u in User.query.all()}
    return render_template('trash.html', deleted=deleted, users=users)


@app.route('/api/trash')
@login_required
def api_trash():
    if not current_user.can_view_trash():
        return jsonify({'error': 'Permiso denegado'}), 403
    record_type = request.args.get('type', '').strip()
    query = DeletedRecord.query
    if record_type in ('movement', 'sale'):
        query = query.filter(DeletedRecord.record_type == record_type)
    records = query.order_by(DeletedRecord.deleted_at.desc()).limit(200).all()
    users = {u.id: u.get_full_name() for u in User.query.all()}
    return jsonify([{
        'id': r.id, 'type': r.record_type, 'record_id': r.record_id,
        'data': json.loads(r.data_json) if r.data_json else {},
        'deleted_by': users.get(r.deleted_by, '?'),
        'deleted_at': to_ar(r.deleted_at).strftime('%d/%m/%Y %H:%M') if r.deleted_at else '',
        'restored_at': to_ar(r.restored_at).strftime('%d/%m/%Y %H:%M') if r.restored_at else None,
    } for r in records])


@app.route('/api/trash/<int:id>/restore', methods=['POST'])
@login_required
def restore_trash(id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    rec = db.session.get(DeletedRecord, id)
    if not rec:
        return jsonify({'error': 'Registro no encontrado'}), 404
    if rec.restored_at:
        return jsonify({'error': 'Ya fue restaurado'}), 400
    data = json.loads(rec.data_json) if rec.data_json else {}
    if rec.record_type == 'movement':
        log = MovementLog(
            action=data.get('action', 'unknown'),
            description=data.get('description', ''),
            user_id=data.get('user_id', current_user.id),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else datetime.now(timezone.utc),
        )
        db.session.add(log)
    elif rec.record_type == 'sale':
        sale = Sale(
            total=data.get('total', 0),
            payment_method=data.get('payment_method', 'cash'),
            amount_paid=data.get('amount_paid', 0),
            change_amount=data.get('change_amount', 0),
            customer_email=data.get('customer_email', ''),
            user_id=data.get('user_id', current_user.id),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else datetime.now(timezone.utc),
        )
        db.session.add(sale)
        db.session.flush()
        for item_data in data.get('items', []):
            db.session.add(SaleItem(
                sale_id=sale.id,
                product_id=item_data['product_id'],
                quantity=item_data['quantity'],
                unit_price=item_data['unit_price'],
                subtotal=item_data['subtotal'],
            ))
        movement = MovementLog(
            user_id=current_user.id,
            action='sale',
            description=f'Venta #%d (restaurada)' % sale.id,
            created_at=datetime.now(timezone.utc),
        )
        movement.description = f'Venta #{sale.id} (restaurada)'
        db.session.add(movement)
    rec.restored_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'ok': True, 'type': rec.record_type})


@app.route('/api/history')
@login_required
def api_history():
    if not current_user.can_view_history():
        return jsonify({'error': 'Permiso denegado'}), 403

    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    product_id = request.args.get('product_id', type=int)
    customer_name = request.args.get('customer_name', '').strip()

    query = MovementLog.query

    if user_id:
        query = query.filter(MovementLog.user_id == user_id)
    if action:
        query = query.filter(MovementLog.action == action)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(MovementLog.created_at <= dt_to)
        except ValueError:
            pass

    if product_id:
        product = db.session.get(Product, product_id)
        if product:
            sale_ids_with_product = [
                r[0] for r in db.session.query(SaleItem.sale_id).filter(
                    SaleItem.product_id == product_id
                ).distinct().all()
            ]
            if sale_ids_with_product:
                query = query.filter(
                    db.or_(
                        MovementLog.description.ilike(f'%{product.code}%'),
                        MovementLog.description.ilike(f'%{product.name}%'),
                        db.and_(
                            MovementLog.action == 'sale',
                            db.or_(*[MovementLog.description.contains(f'#{sid}') for sid in sale_ids_with_product])
                        )
                    )
                )
            else:
                query = query.filter(
                    MovementLog.description.ilike(f'%{product.code}%') |
                    MovementLog.description.ilike(f'%{product.name}%')
                )

    if customer_name:
        sale_ids_by_customer = [r[0] for r in db.session.query(Sale.id).filter(
            Sale.customer_name.ilike(f'%{customer_name}%')
        ).all()]
        if sale_ids_by_customer:
            query = query.filter(db.and_(
                MovementLog.action == 'sale',
                db.or_(*[MovementLog.description.contains(f'#{sid}') for sid in sale_ids_by_customer])
            ))
        else:
            query = query.filter(False)

    logs = query.order_by(MovementLog.created_at.desc()).limit(500).all()

    hide_admin = get_config('hide_admin_history', 'false')
    if hide_admin == 'true' and current_user.role != 'admin':
        admin_ids = [u.id for u in User.query.filter_by(role='admin').all()]
        logs = [log for log in logs if log.user_id not in admin_ids]

    sale_ids = []
    for log in logs:
        if log.action == 'sale':
            desc = log.description
            if '#' in desc:
                try:
                    sid = int(desc.split('#')[1].split(' ')[0])
                    sale_ids.append(sid)
                except (IndexError, ValueError):
                    pass

    # Bulk load users and sales to avoid N+1
    user_ids = list(set(log.user_id for log in logs))
    users_map = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()}
    sale_map = {}
    total_sales_amount = 0
    total_sales_qty = 0
    if sale_ids:
        sales = Sale.query.filter(Sale.id.in_(sale_ids)).options(db.joinedload(Sale.items)).all()
        sale_map = {s.id: s for s in sales}
        total_sales_amount = sum(s.total for s in sales)
        total_sales_qty = sum(
            sum(item.quantity for item in s.items) for s in sales
        )

    action_map = {
        'login': 'Inicio Sesión', 'logout': 'Cierre Sesión',
        'product_create': 'Crear Producto', 'product_edit': 'Editar Producto',
        'product_delete': 'Eliminar Producto', 'sale': 'Venta',
        'supplier_create': 'Crear Proveedor', 'supplier_edit': 'Editar Proveedor',
        'supplier_delete': 'Eliminar Proveedor', 'user_create': 'Crear Usuario',
        'user_toggle': 'Estado Usuario', 'user_reset_pass': 'Reset Pass',
        'user_delete': 'Eliminar Usuario',
        'category_create': 'Crear Categoría', 'category_edit': 'Editar Categoría',
        'category_delete': 'Eliminar Categoría',
        'refund': 'Anular Venta', 'payment_confirm': 'Confirmar Pago',
        'cash_close': 'Cierre de Caja', 'cash_close_void': 'Anular Cierre',
        'order_create': 'Crear Pedido', 'order_complete': 'Completar Pedido',
        'order_cancel': 'Cancelar Pedido',
        'purchase_create': 'Crear OC', 'purchase_receive': 'Recibir OC',
        'purchase_cancel': 'Cancelar OC', 'purchase_receive_item': 'Recibir Ítem OC',
        'system_reset': 'Reset del Sistema'
    }

    log_list = []
    for log in logs:
        u = users_map.get(log.user_id)
        entry = {
            'id': log.id,
            'user': u.get_full_name() if u else '?',
            'role': u.role if u else '',
            'action': action_map.get(log.action, log.action),
            'action_key': log.action,
            'description': log.description,
            'time': to_ar(log.created_at).strftime('%d/%m/%Y %H:%M'),
        }
        if log.action == 'sale' and '#' in log.description:
            try:
                sid = int(log.description.split('#')[1].split(' ')[0])
                entry['sale_id'] = sid
                s = sale_map.get(sid)
                if s and s.customer_name:
                    entry['customer_name'] = s.customer_name
            except (IndexError, ValueError):
                pass
        log_list.append(entry)

    return jsonify({
        'logs': log_list,
        'summary': {
            'total_logs': len(log_list),
            'total_sales_amount': total_sales_amount,
            'total_sales_qty': total_sales_qty,
            'sale_count': len(sale_ids),
        }
    })


@app.route('/api/log/<int:id>')
@login_required
def api_log_detail(id):
    if not current_user.can_view_history():
        return jsonify({'error': 'Permiso denegado'}), 403
    log = db.session.get(MovementLog, id)
    if not log:
        return jsonify({'error': 'No encontrado'}), 404
    result = {
        'id': log.id,
        'user': log.user.get_full_name(),
        'role': log.user.role,
        'action': log.action,
        'description': log.description,
        'time': to_ar(log.created_at).strftime('%d/%m/%Y %H:%M'),
    }
    if log.action == 'product_edit':
        # Parse "NombreProducto: Campo: viejo → nuevo, Campo2: viejo → nuevo"
        parts = log.description.split(': ', 1)
        if len(parts) == 2:
            product_name = parts[0]
            changes_str = parts[1]
            changes = []
            for change in changes_str.split(', '):
                if ' → ' in change:
                    if ': ' in change:
                        field, rest = change.split(': ', 1)
                    else:
                        field = ''
                        rest = change
                    arrow = rest.split(' → ')
                    old_val = arrow[0] if len(arrow) > 0 else ''
                    new_val = arrow[1] if len(arrow) > 1 else arrow[0]
                    changes.append({'field': field, 'old': old_val, 'new': new_val})
            result['changes'] = changes
            result['product_name'] = product_name
    if log.action == 'sale':
        if '#' in log.description:
            try:
                result['sale_id'] = int(log.description.split('#')[1].split(' ')[0])
            except (IndexError, ValueError):
                pass
    return jsonify(result)


@app.route('/api/stats')
@login_required
def api_stats():
    threshold = get_low_stock_threshold()
    today = datetime.now(AR_TZ).date()
    today_sales = Sale.query.filter(
        db.func.date(Sale.created_at) == today
    ).count()
    today_revenue = float(db.session.query(db.func.coalesce(db.func.sum(Sale.total), 0)).filter(
        db.func.date(Sale.created_at) == today
    ).scalar() or 0)
    return jsonify({
        'total_products': Product.query.count(),
        'categories_count': Category.query.count(),
        'low_stock': Product.query.filter(Product.stock < threshold).count(),
        'critical_stock': Product.query.filter(Product.stock < critical).count(),
        'pending_orders': PendingOrder.query.filter_by(status='pending').count(),
        'today_sales': today_sales,
        'today_revenue': today_revenue,
        'low_stock_threshold': threshold
    })


@app.route('/api/sale/<int:sale_id>/items')
@login_required
def api_sale_items(sale_id):
    if not current_user.can_view_history():
        return jsonify({'error': 'Permiso denegado'}), 403
    sale = db.session.get(Sale, sale_id)
    if not sale:
        return jsonify({'error': 'Venta no encontrada'}), 404
    items = [{
        'product_name': item.product.name if item.product else 'Eliminado',
        'quantity': item.quantity,
        'unit_price': item.unit_price,
        'subtotal': item.subtotal,
        'unit_type': item.product.unit_type if item.product else 'unit'
    } for item in sale.items]
    return jsonify({
        'id': sale.id,
        'user': sale.user.username,
        'total': sale.total,
        'payment_method': sale.payment_method,
        'date': to_ar(sale.created_at).strftime('%d/%m/%Y %H:%M'),
        'items': items
    })


@app.route('/api/config/<key>')
@login_required
def api_config(key):
    config = Config.query.filter_by(key=key).first()
    if config:
        return jsonify({'key': key, 'value': config.value})
    return jsonify({'key': key, 'value': None})


def send_ticket_email(sale, sale_items, customer_email=None, user_full_name=''):
    with app.app_context():
        if not can_send_email():
            return False

        owner_email = get_config('owner_email', '')
        if not owner_email:
            return False

        if not customer_email and not owner_email:
            return False

        method_names = {'cash': 'Efectivo', 'card': 'Tarjeta', 'transfer': 'Transferencia'}
        items_html = ''.join(
            f'<tr><td>{item["product_name"]}</td><td style="text-align:center">{item["quantity"]}</td>'
            f'<td style="text-align:right">${item["unit_price"]:.2f}</td>'
            f'<td style="text-align:right">${item["subtotal"]:.2f}</td></tr>'
            for item in sale_items
        )

        biz_name = get_config('business_name', 'NexoControl')
        local = get_config('local_name', '')
        html = f"""
        <div style="font-family:Arial;max-width:400px;margin:0 auto;">
            <div style="text-align:center;background:#3d5a80;color:#fff;padding:15px;border-radius:8px 8px 0 0;">
                <h2 style="margin:0;">{biz_name}</h2>
                {f'<p style="margin:2px 0 0;font-size:12px;">{local}</p>' if local else ''}
                <p style="margin:5px 0 0;font-size:13px;">Ticket #{sale.id}</p>
            </div>
            <div style="background:#f9f9f9;padding:15px;border:1px solid #ddd;">
                <p style="font-size:12px;color:#555;">{to_ar(sale.created_at).strftime('%d/%m/%Y %H:%M')} | Atendió: {user_full_name}</p>
                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <tr style="font-weight:700;border-bottom:2px solid #ddd;">
                        <td style="padding:5px;">Producto</td>
                        <td style="padding:5px;text-align:center;">Cant</td>
                        <td style="padding:5px;text-align:right;">P/U</td>
                        <td style="padding:5px;text-align:right;">Subtotal</td>
                    </tr>
                    {items_html}
                </table>
                <div style="border-top:2px solid #3d5a80;margin:10px 0;padding-top:10px;text-align:right;font-size:18px;font-weight:700;">
                    TOTAL: ${sale.total:.2f}
                </div>
                <p style="font-size:13px;">Método de pago: {method_names.get(sale.payment_method, sale.payment_method)}</p>
                {f'<p style="font-size:13px;">Recibido: ${sale.amount_paid:.2f} | Vuelto: <span style="color:green;">${sale.change_amount:.2f}</span></p>' if sale.payment_method == 'cash' else ''}
            </div>
            <div style="text-align:center;padding:10px;font-size:12px;color:#555;">
                ¡Gracias por su compra!
            </div>
        </div>
        """

        recipients = []
        if customer_email:
            recipients.append(customer_email)
        if owner_email and owner_email != customer_email:
            recipients.append(owner_email)

        if not recipients:
            return

        for to in recipients:
            try:
                sent = send_email(to, f'Tu Ticket #{sale.id} - {biz_name}', html)
                if not sent:
                    return False
            except Exception:
                return False
        return True


@app.route('/orders')
@login_required
def orders():
    if not current_user.can_take_orders():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    products = Product.query.order_by(Product.name).all()
    categories = Category.query.order_by(Category.name).all()
    pending = PendingOrder.query.filter_by(status='pending').order_by(PendingOrder.created_at.desc()).all()
    products_json = [{'id': p.id, 'code': p.code, 'name': p.name, 'price': p.price, 'unit_type': getattr(p, 'unit_type', 'unit')} for p in products]
    unit_types = ['unit', 'kg', 'g', 'liter', 'ml', 'm', 'cm', 'dozen', 'pack']
    return render_template('orders.html', products=products_json, categories=categories, pending=pending, unit_types=unit_types)


@app.route('/api/orders')
@login_required
def api_orders():
    if not current_user.can_take_orders():
        return {'error': 'Permiso denegado'}, 403
    orders_list = PendingOrder.query.filter_by(status='pending').order_by(PendingOrder.created_at.desc()).all()
    return {'orders': [{
        'id': o.id,
        'user': o.user.get_full_name(),
        'items': json.loads(o.items_json),
        'customer_name': o.customer_name,
        'notes': o.notes,
        'total': o.total,
        'created_at': to_ar(o.created_at).strftime('%d/%m/%Y %H:%M'),
    } for o in orders_list]}


@app.route('/api/orders/create', methods=['POST'])
@login_required
def api_create_order():
    if not current_user.can_take_orders():
        return {'error': 'Permiso denegado'}, 403
    data = request.get_json(force=True)
    items = data.get('items', [])
    if not items:
        return {'error': 'Carrito vacío'}, 400
    customer_name = data.get('customer_name', '').strip()
    notes = data.get('notes', '').strip()
    total = sum(item.get('subtotal', 0) for item in items)

    pending_items = []
    for item in items:
        pending_items.append({
            'product_id': item['product_id'],
            'code': item.get('code', ''),
            'name': item.get('name', ''),
            'quantity': item['quantity'],
            'unit_price': item['unit_price'],
            'subtotal': item['subtotal'],
        })

    order = PendingOrder(
        user_id=current_user.id,
        items_json=json.dumps(pending_items, ensure_ascii=False),
        customer_name=customer_name,
        notes=notes,
        total=total,
        status='pending'
    )
    db.session.add(order)
    db.session.commit()
    log_movement(current_user, 'order_create', f'Pedido #{order.id} creado - ${total:.2f}')
    return {'success': True, 'id': order.id}


@app.route('/api/orders/<int:order_id>/complete', methods=['POST'])
@login_required
def api_complete_order(order_id):
    if not current_user.can_take_orders():
        return {'error': 'Permiso denegado'}, 403
    order = db.session.get(PendingOrder, order_id)
    if not order or order.status != 'pending':
        return {'error': 'Pedido no encontrado o ya procesado'}, 404

    items_data = json.loads(order.items_json)
    sale = Sale(
        user_id=current_user.id,
        total=order.total,
        payment_method='pending_order',
        amount_paid=order.total,
        change_amount=0,
        customer_email='',
    )
    db.session.add(sale)
    db.session.flush()

    for item in items_data:
        sale_item = SaleItem(
            sale_id=sale.id,
            product_id=item['product_id'],
            quantity=item['quantity'],
            unit_price=item['unit_price'],
            subtotal=item['subtotal'],
        )
        db.session.add(sale_item)
        product = db.session.get(Product, item['product_id'])
        if product:
            product.stock -= item['quantity']

    order.status = 'completed'
    order.completed_at = datetime.now(timezone.utc)
    order.sale_id = sale.id
    db.session.commit()
    log_movement(current_user, 'order_complete', f'Pedido #{order.id} facturado como venta #{sale.id}')
    return {'success': True, 'sale_id': sale.id}


@app.route('/api/orders/<int:order_id>/cancel', methods=['POST'])
@login_required
def api_cancel_order(order_id):
    if not current_user.can_take_orders():
        return {'error': 'Permiso denegado'}, 403
    order = db.session.get(PendingOrder, order_id)
    if not order or order.status != 'pending':
        return {'error': 'Pedido no encontrado o ya procesado'}, 404
    order.status = 'cancelled'
    order.completed_at = datetime.now(timezone.utc)
    db.session.commit()
    log_movement(current_user, 'order_cancel', f'Pedido #{order.id} cancelado')
    return {'success': True}


# ─── Manual de uso ───────────────────


MANUAL_DEFAULT_SECTIONS = {
    'mDash': {'title': 'Dashboard (Inicio)', 'icon': 'speedometer2', 'color': '#3d5a80',
        'content': '<p>Al iniciar sesión ves el panel principal con <strong>7 tarjetas</strong> que resumen el estado del negocio.</p><div class=\"row g-2 mb-3\"><div class=\"col-6 col-md-3\"><span class=\"badge d-block p-2\" style=\"background:#3d5a80;\">📦 Productos</span></div><div class=\"col-6 col-md-3\"><span class=\"badge d-block p-2\" style=\"background:#98c1d9;color:#000;\">🏷️ Categorías</span></div><div class=\"col-6 col-md-3\"><span class=\"badge d-block p-2\" style=\"background:#ee6c4d;\">⚠️ Stock bajo</span></div><div class=\"col-6 col-md-3\"><span class=\"badge d-block p-2\" style=\"background:#293241;\">🔴 Stock crítico</span></div><div class=\"col-6 col-md-3\"><span class=\"badge d-block p-2\" style=\"background:#3d5a80;\">💰 Ventas hoy</span></div><div class=\"col-6 col-md-3\"><span class=\"badge d-block p-2\" style=\"background:#98c1d9;color:#000;\">📊 Ingresos hoy</span></div><div class=\"col-6 col-md-3\"><span class=\"badge d-block p-2\" style=\"background:#ee6c4d;\">📋 Pedidos pendientes</span></div></div><p>También hay un <strong>gráfico de barras</strong> con ventas de los últimos 7 días y un <strong>widget de clima</strong> actual. El panel se actualiza solo cada 5 segundos.</p>',
        'visible': 'all'},
    'mSell': {'title': 'Vender (cobrar)', 'icon': 'cart3', 'color': '#ee6c4d',
        'content': '<ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Escanear o buscar:</strong> pasá el código de barras por el scanner o escribí el código/nombre en el campo de búsqueda. Aparecen los productos debajo, hacé click para agregarlos.</li><li class=\"list-group-item px-0\"><strong>Cantidad:</strong> antes de escanear, podés cambiar el número en el campo \"Cantidad\" (default 1). Cada escaneo agrega esa cantidad. Al agregar un producto, la cantidad vuelve a 1 automáticamente.</li><li class=\"list-group-item px-0\"><strong>Precio mayorista:</strong> si el producto tiene precio mayorista configurado, se aplica automáticamente al superar la cantidad mínima.</li><li class=\"list-group-item px-0\"><strong>Cliente:</strong> opcional, podés escribir el nombre del cliente para que aparezca en el ticket e historial.</li><li class=\"list-group-item px-0\"><strong>Pendiente de pago:</strong> si activás esta opción, la venta se registra pero no resta el dinero del cierre de caja. Aparece en \"Pendientes\" para cobrar después.</li><li class=\"list-group-item px-0\"><strong>Método de pago:</strong> seleccioná Efectivo, Tarjeta, Transferencia o Mercado Pago.</li><li class=\"list-group-item px-0\"><strong>Finalizar:</strong> click en \"Cobrar\". Se descarga el ticket automáticamente. Si hay vuelto, se calcula solo.</li></ol><p class=\"mt-2 mb-0 text-muted small\">Podés enviar el ticket por email si configuraste SMTP en Config.</p>',
        'visible': 'all'},
    'mProducts': {'title': 'Productos', 'icon': 'box-seam', 'color': '#3d5a80',
        'content': '<p>Gestión completa del catálogo de productos.</p><ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Agregar:</strong> click en \"+ Nuevo Producto\". Completá código, nombre, costo, margen de ganancia, precio de venta, stock, tipo de unidad (unidad o kilo), categoría y proveedor.</li><li class=\"list-group-item px-0\"><strong>Precio mayorista:</strong> en el mismo formulario, configurá cantidad mínima y precio especial. Se aplica automáticamente al vender.</li><li class=\"list-group-item px-0\"><strong>Editar:</strong> click en <i class=\"bi bi-pencil text-primary\"></i>. Todos los campos se pueden modificar.</li><li class=\"list-group-item px-0\"><strong>Eliminar:</strong> click en <i class=\"bi bi-trash3 text-danger\"></i>. El producto va a la Papelera y se puede restaurar.</li><li class=\"list-group-item px-0\"><strong>Buscar/filtrar:</strong> usá el campo de búsqueda o el filtro por categoría y orden.</li></ol><p class=\"mt-2 mb-0 text-muted small\">El código de barras es único. No podés tener dos productos con el mismo código.</p>',
        'visible': 'all'},
    'mSuppliers': {'title': 'Proveedores', 'icon': 'building', 'color': '#98c1d9',
        'content': '<ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Agregar:</strong> click en \"+ Nuevo Proveedor\". Completá nombre (obligatorio), contacto, teléfono, email y dirección.</li><li class=\"list-group-item px-0\"><strong>Editar:</strong> click en <i class=\"bi bi-pencil text-primary\"></i> para modificar cualquier campo.</li><li class=\"list-group-item px-0\"><strong>Eliminar:</strong> click en <i class=\"bi bi-trash3 text-danger\"></i>. Se puede restaurar desde la Papelera.</li><li class=\"list-group-item px-0\"><strong>Buscar:</strong> escribí en el campo de búsqueda para filtrar por nombre, teléfono, email o contacto.</li></ol><p class=\"mt-2 mb-0 text-muted small\">Los proveedores se vinculan a productos y a Órdenes de Compra.</p>',
        'visible': 'all'},
    'mCats': {'title': 'Categorías', 'icon': 'tags', 'color': '#ee6c4d',
        'content': '<ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Agregar:</strong> click en \"+ Nueva Categoría\", escribí el nombre y guardá.</li><li class=\"list-group-item px-0\"><strong>Editar:</strong> click en <i class=\"bi bi-pencil text-primary\"></i> para renombrar.</li><li class=\"list-group-item px-0\"><strong>Eliminar:</strong> click en <i class=\"bi bi-trash3 text-danger\"></i>. Los productos de esa categoría pasan a \"Sin categoría\".</li></ol><p class=\"mt-2 mb-0 text-muted small\">Las categorías se usan para organizar productos y filtrar en el listado.</p>',
        'visible': 'all'},
    'mPO': {'title': 'Órdenes de Compra', 'icon': 'truck', 'color': '#293241',
        'content': '<p>Gestión de compras a proveedores (recepción de mercadería tipo Carrefour).</p><div class=\"d-flex gap-2 mb-3 flex-wrap\"><span class=\"badge d-inline-block p-2\" style=\"background:#ee6c4d;\">📝 Crear OC</span><span class=\"badge d-inline-block p-2\" style=\"background:#ffc107;color:#000;\">⏳ Pendiente</span><span class=\"badge d-inline-block p-2\" style=\"background:#198754;\">📦 Recibir</span><span class=\"badge d-inline-block p-2\" style=\"background:#6c757d;\">✅ Recibida</span></div><ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Crear OC:</strong> seleccioná el proveedor, escaneá productos para agregar, ajustá cantidad/precio, click en \"Crear OC\".</li><li class=\"list-group-item px-0\"><strong>Recibir:</strong> cuando llega la mercadería, click en \"Recibir\". Escaneá cada producto (como Carrefour). Cada escaneo suma +1 al stock. La fila se pone verde cuando está completa.</li><li class=\"list-group-item px-0\"><strong>OC Recibida:</strong> badge verde, no se puede modificar.</li></ol><p class=\"mt-2 mb-0 text-muted small\">Cada escaneo registra un movimiento en el historial del sistema.</p>',
        'visible': 'admin'},
    'mOrders': {'title': 'Pedidos (clientes)', 'icon': 'journal-text', 'color': '#3d5a80',
        'content': '<p>Registrá pedidos de clientes que no se cobran en el momento.</p><ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Crear:</strong> desde la página de Pedidos, agregá productos, nombre del cliente, notas y total estimado.</li><li class=\"list-group-item px-0\"><strong>Completar:</strong> cuando el cliente viene a retirar, click en \"Completar\" y se genera una venta automáticamente restando stock.</li><li class=\"list-group-item px-0\"><strong>Cancelar:</strong> si el cliente cancela, podés cancelar el pedido.</li></ol>',
        'visible': 'all'},
    'mPending': {'title': 'Ventas Pendientes de Pago', 'icon': 'hourglass-split', 'color': '#ee6c4d',
        'content': '<p>Cuando activás \"Pendiente de pago\" al vender, la venta queda registrada con status <span class=\"badge bg-warning text-dark\">pending</span> y no suma al cierre de caja.</p><ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\">Las ventas pendientes aparecen en la pestaña \"Pendientes\" del sidebar.</li><li class=\"list-group-item px-0\">Ves el total, cliente, fecha y productos.</li><li class=\"list-group-item px-0\">Cuando el cliente paga, click en <strong>\"Cobrar\"</strong>. Se marca como pagada y pasa al cierre de caja.</li><li class=\"list-group-item px-0\">Tiene su propio permiso: <code>can_view_pending_sales</code> y <code>can_confirm_payment</code>.</li></ol><p class=\"mt-2 mb-0 text-muted small\">El stock se resta en el momento de la venta, no al cobrar. Si anulás la venta, el stock se devuelve.</p>',
        'visible': 'all'},
    'mHistory': {'title': 'Historial / Ganancias / Ranking', 'icon': 'clock-history', 'color': '#98c1d9',
        'content': '<ul class=\"list-group list-group-flush\"><li class=\"list-group-item px-0\"><i class=\"bi bi-journal-text me-2\" style=\"color:#3d5a80;\"></i> <strong>Historial:</strong> todas las ventas. Podés filtrar por fecha, cliente o método de pago. Cada venta tiene botón \"Ver\" con detalle y opción de <strong>Anular venta</strong> (devuelve stock). Exportá a Excel.</li><li class=\"list-group-item px-0\"><i class=\"bi bi-graph-up me-2\" style=\"color:#ee6c4d;\"></i> <strong>Ganancias:</strong> ganancia neta por producto (precio venta - costo). Filtrable por fecha.</li><li class=\"list-group-item px-0\"><i class=\"bi bi-trophy me-2\" style=\"color:#ffc107;\"></i> <strong>Ranking:</strong> productos más vendidos, ordenados por cantidad.</li></ul>',
        'visible': 'all'},
    'mTrash': {'title': 'Papelera', 'icon': 'trash3', 'color': '#293241',
        'content': '<p>Los elementos eliminados (productos, proveedores, etc.) van a la papelera en lugar de borrarse definitivamente.</p><ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Restaurar:</strong> click en \"Restaurar\" para recuperar el elemento con todos sus datos originales.</li><li class=\"list-group-item px-0\"><strong>Eliminar definitivamente:</strong> si querés borrarlo para siempre, hay un botón aparte.</li></ol><p class=\"mt-2 mb-0 text-muted small\">Al eliminar un usuario, sus ventas se reasignan al administrador para no perder datos.</p>',
        'visible': 'admin'},
    'mCash': {'title': 'Cierre de Caja', 'icon': 'cash-stack', 'color': '#3d5a80',
        'content': '<ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Abrir caja:</strong> al empezar el día, registrá el monto inicial en efectivo.</li><li class=\"list-group-item px-0\"><strong>Cerrar caja:</strong> el sistema muestra ventas por método de pago, total de devoluciones, efectivo esperado. Declarás el efectivo real y calcula la diferencia.</li><li class=\"list-group-item px-0\"><strong>Historial de cierres:</strong> todos los cierres anteriores con detalle.</li><li class=\"list-group-item px-0\"><strong>Anular cierre:</strong> solo admin, si hubo un error.</li></ol><p class=\"mt-2 mb-0 text-muted small\">Permiso <code>can_close_cash</code> para ver/cerrar y <code>can_void_cash_close</code> para anular.</p>',
        'visible': 'all'},
    'mUsers': {'title': 'Usuarios y Roles', 'icon': 'people', 'color': '#ee6c4d',
        'content': '<p>Hay 3 roles predefinidos:</p><div class=\"d-flex gap-2 mb-3 flex-wrap\"><span class=\"badge d-inline-block p-2\" style=\"background:#3d5a80;\">🔑 Admin — acceso total</span><span class=\"badge d-inline-block p-2\" style=\"background:#98c1d9;color:#000;\">👁️ Supervisor — gestión + informes</span><span class=\"badge d-inline-block p-2\" style=\"background:#293241;\">👤 User — solo vender</span></div><p>Los permisos son <strong>granulares</strong> y se configuran desde Config &gt; Permisos. Podés activar o desactivar cada permiso individualmente por rol.</p>',
        'visible': 'admin'},
    'mBackups': {'title': 'Backups', 'icon': 'database', 'color': '#293241',
        'content': '<ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\"><strong>Backup manual:</strong> click en \"Crear Backup Ahora\". Se descarga un .zip con toda la base de datos.</li><li class=\"list-group-item px-0\"><strong>Restaurar:</strong> subí un archivo .zip de backup. <strong>Cuidado:</strong> reemplaza TODOS los datos actuales.</li><li class=\"list-group-item px-0\"><strong>Backup automático:</strong> configurá días de la semana y horario. Se genera automáticamente.</li><li class=\"list-group-item px-0\"><strong>Google Drive:</strong> si configuraste cuenta de servicio, los backups se suben a Drive.</li></ol>',
        'visible': 'all'},
    'mBarcodes': {'title': 'Códigos de Barras y QR', 'icon': 'upc-scan', 'color': '#3d5a80',
        'content': '<ol class=\"list-group list-group-numbered list-group-flush\"><li class=\"list-group-item px-0\">Seleccioná los productos para imprimir.</li><li class=\"list-group-item px-0\">Elegí el tamaño de etiqueta (chica, mediana, grande).</li><li class=\"list-group-item px-0\">Descargá el PDF listo para imprimir en hojas autoadhesivas.</li></ol>',
        'visible': 'all'},
    'mConfig': {'title': 'Configuración', 'icon': 'gear', 'color': '#ee6c4d',
        'content': '<p>Solo visible para administradores:</p><div class=\"row g-2\"><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#3d5a80;\">🏪 Negocio</span></div><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#98c1d9;color:#000;\">🌍 Zona horaria</span></div><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#ee6c4d;\">📦 Umbrales stock</span></div><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#293241;\">📧 SMTP Email</span></div><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#3d5a80;\">☁️ Google Drive</span></div><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#98c1d9;color:#000;\">🔄 Multi-sucursal</span></div><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#ee6c4d;\">🔐 Permisos</span></div><div class=\"col-6 col-md-4\"><span class=\"badge d-block p-2\" style=\"background:#293241;\">🗑️ Resetear sistema</span></div></div>',
        'visible': 'admin'},
}

@app.route('/manual')
@login_required
def manual():
    raw = get_config('manual_sections', '')
    if raw:
        sections = json.loads(raw)
    else:
        sections = MANUAL_DEFAULT_SECTIONS
        c = Config.query.filter_by(key='manual_sections').first()
        if not c:
            db.session.add(Config(key='manual_sections', value=json.dumps(sections, ensure_ascii=False)))
            db.session.commit()
    is_admin = current_user.role == 'admin'
    if not is_admin:
        sections = {k: v for k, v in sections.items() if v.get('visible') == 'all'}
    return render_template('manual.html', sections=sections, is_admin=is_admin)


@app.route('/api/manual/save', methods=['POST'])
@login_required
def api_manual_save():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Datos requeridos'}), 400
    c = Config.query.filter_by(key='manual_sections').first()
    if c:
        existing = json.loads(c.value) if c.value else {}
    else:
        existing = {k: dict(v) for k, v in MANUAL_DEFAULT_SECTIONS.items()}
        c = Config(key='manual_sections', value=json.dumps(existing, ensure_ascii=False))
        db.session.add(c)
    for key, val in data.items():
        if key in existing:
            existing[key].update(val)
        elif key in MANUAL_DEFAULT_SECTIONS:
            s = dict(MANUAL_DEFAULT_SECTIONS[key])
            s.update(val)
            existing[key] = s
        else:
            continue
    c.value = json.dumps(existing, ensure_ascii=False)
    db.session.commit()
    # Clear cache
    if hasattr(g, '_configs_cached'):
        del g._configs_cached
    return jsonify({'success': True})


# ─── Purchase Orders (Recepción / Orden de Compra) ───────────────────


@app.route('/purchase-orders')
@login_required
def purchase_orders():
    if not current_user.can_manage_purchases():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    configs = {c.key: c.value for c in Config.query.all()}
    orders = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(200).all()
    suppliers = Supplier.query.order_by(Supplier.name).all()
    products = Product.query.order_by(Product.name).limit(1000).all()
    products_data = [{'id': p.id, 'code': p.code, 'name': p.name, 'cost': p.cost, 'unit_type': p.unit_type} for p in products]
    suppliers_data = [{'id': s.id, 'name': s.name, 'email': s.email} for s in suppliers]
    orders_data = []
    for o in orders:
        orders_data.append({
            'id': o.id,
            'supplier_name': o.supplier.name if o.supplier else None,
            'items_json': o.items_json,
            'total': o.total,
            'status': o.status,
            'created_at': o.created_at.isoformat() if o.created_at else None,
        })
    return render_template('purchase_orders.html', orders=orders, orders_data=json.dumps(orders_data, ensure_ascii=False), products=products, products_data=json.dumps(products_data, ensure_ascii=False), suppliers=suppliers, suppliers_data=json.dumps(suppliers_data, ensure_ascii=False), configs=configs)


@app.route('/purchase-orders/create', methods=['POST'])
@login_required
def purchase_order_create():
    if not current_user.can_manage_purchases():
        return {'error': 'Permiso denegado'}, 403
    supplier_id = request.form.get('supplier_id', type=int)
    notes = request.form.get('notes', '').strip()
    items_json = request.form.get('items_json', '[]')
    items = json.loads(items_json)
    if not items:
        flash('Agregá al menos un producto.', 'warning')
        return redirect(url_for('purchase_orders'))
    total = sum(item.get('subtotal', 0) for item in items)
    po = PurchaseOrder(
        user_id=current_user.id,
        supplier_id=supplier_id,
        items_json=json.dumps(items, ensure_ascii=False),
        notes=notes,
        total=total,
        status='pending'
    )
    db.session.add(po)
    db.session.commit()
    log_movement(current_user, 'purchase_create', f'OC #{po.id} creada por ${total:.2f}')
    flash(f'Orden de Compra #{po.id} creada.', 'success')
    return redirect(url_for('purchase_orders'))


@app.route('/purchase-orders/<int:po_id>/receive', methods=['POST'])
@login_required
def purchase_order_receive(po_id):
    if not current_user.can_manage_purchases():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('purchase_orders'))
    po = db.session.get(PurchaseOrder, po_id)
    if not po or po.status != 'pending':
        flash('OC no encontrada o ya recibida.', 'warning')
        return redirect(url_for('purchase_orders'))
    items = json.loads(po.items_json)
    for item in items:
        received = float(item.get('received_qty', item.get('quantity', 0)))
        product = db.session.get(Product, item['product_id'])
        if product:
            product.stock += received
            item['received_qty'] = received
    po.items_json = json.dumps(items, ensure_ascii=False)
    po.status = 'received'
    po.completed_at = datetime.now(AR_TZ)
    db.session.commit()
    log_movement(current_user, 'purchase_receive', f'OC #{po.id} recibida, {len(items)} productos actualizados')
    flash(f'OC #{po.id} recibida. Stock actualizado.', 'success')
    return redirect(url_for('purchase_orders'))


@app.route('/purchase-orders/<int:po_id>/cancel', methods=['POST'])
@login_required
def purchase_order_cancel(po_id):
    if not current_user.can_manage_purchases():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('purchase_orders'))
    po = db.session.get(PurchaseOrder, po_id)
    if not po or po.status != 'pending':
        flash('OC no encontrada o ya procesada.', 'warning')
        return redirect(url_for('purchase_orders'))
    po.status = 'cancelled'
    po.completed_at = datetime.now(AR_TZ)
    db.session.commit()
    log_movement(current_user, 'purchase_cancel', f'OC #{po.id} cancelada')
    flash(f'OC #{po.id} cancelada.', 'warning')
    return redirect(url_for('purchase_orders'))


@app.route('/api/purchase-orders/<int:po_id>/receive-scan', methods=['POST'])
@login_required
def purchase_order_receive_scan(po_id):
    if not current_user.can_manage_purchases():
        return {'error': 'Permiso denegado'}, 403
    po = db.session.get(PurchaseOrder, po_id)
    if not po or po.status != 'pending':
        return {'error': 'OC no encontrada'}, 404
    data = request.get_json() or {}
    code = data.get('code', '').strip()
    qty = float(data.get('quantity', 1))
    product = Product.query.filter_by(code=code).first()
    if not product:
        return {'error': 'Producto no encontrado'}, 404
    items = json.loads(po.items_json)
    found = False
    for item in items:
        if item['product_id'] == product.id:
            received = item.get('received_qty', 0)
            item['received_qty'] = received + qty
            found = True
            break
    if not found:
        return {'error': 'El producto no está en esta OC'}, 400
    po.items_json = json.dumps(items, ensure_ascii=False)
    product.stock += qty
    db.session.commit()
    log_movement(current_user, 'purchase_receive_item', f'OC #{po.id}: +{qty} x {product.name}')
    return {'success': True, 'product': product.name, 'received': float(item['received_qty']), 'ordered': float(item['quantity'])}


@app.route('/api/purchase-orders/<int:po_id>/finish', methods=['POST'])
@login_required
def purchase_order_finish(po_id):
    if not current_user.can_manage_purchases():
        return {'error': 'Permiso denegado'}, 403
    po = db.session.get(PurchaseOrder, po_id)
    if not po or po.status != 'pending':
        return {'error': 'OC no encontrada'}, 404
    po.status = 'received'
    po.completed_at = datetime.now(AR_TZ)
    db.session.commit()
    log_movement(current_user, 'purchase_receive', f'OC #{po.id} finalizada por escaneo')
    return {'success': True}


@app.route('/purchase-orders/<int:po_id>/share-whatsapp')
@login_required
def purchase_order_whatsapp(po_id):
    if not current_user.can_manage_purchases():
        return {'error': 'Permiso denegado'}, 403
    po = db.session.get(PurchaseOrder, po_id)
    if not po:
        return {'error': 'OC no encontrada'}, 404
    items = json.loads(po.items_json)
    biz = get_config('business_name', 'Mi Negocio')
    lines = [f'🧾 *ORDEN DE COMPRA #{po.id}* - {biz}']
    lines.append(f'📅 {to_ar(po.created_at).strftime("%d/%m/%Y %H:%M")}')
    if po.supplier:
        lines.append(f'🏢 Proveedor: {po.supplier.name}')
    lines.append('')
    for i, item in enumerate(items, 1):
        p = db.session.get(Product, item['product_id'])
        name = item.get('name', p.name if p else '?')
        qty = item.get('quantity', 0)
        u = p.unit_type if p else 'unit'
        unit_label = '' if u == 'unit' else f' {u}'
        lines.append(f'{i}. {name} x{qty}{unit_label}')
    lines.append(f'\n💰 Total: ${po.total:.2f}')
    msg = '\n'.join(lines)
    from urllib.parse import quote
    url = f'https://wa.me/?text={quote(msg)}'
    return redirect(url)


@app.route('/purchase-orders/<int:po_id>/email', methods=['POST'])
@login_required
def purchase_order_email(po_id):
    if not current_user.can_manage_purchases():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('purchase_orders'))
    po = db.session.get(PurchaseOrder, po_id)
    if not po:
        flash('OC no encontrada.', 'danger')
        return redirect(url_for('purchase_orders'))
    to = request.form.get('to', '').strip()
    if not to or '@' not in to:
        flash('Email inválido.', 'danger')
        return redirect(url_for('purchase_orders'))
    if not can_send_po_email():
        flash('SMTP de OC no configurado. Andá a Config > SMTP > OC.', 'danger')
        return redirect(url_for('purchase_orders'))
    items = json.loads(po.items_json)
    biz = get_config('business_name', 'Mi Negocio')
    rows = ''
    for item in items:
        p = db.session.get(Product, item['product_id'])
        name = item.get('name', p.name if p else '?')
        qty = item.get('quantity', 0)
        u = p.unit_type if p else 'unit'
        unit_label = '' if u == 'unit' else f' {u}'
        subtotal = item.get('subtotal', item.get('quantity', 0) * item.get('cost', 0))
        rows += f'<tr><td>{name}</td><td>{qty}{unit_label}</td><td>${item.get("cost",0):.2f}</td><td>${subtotal:.2f}</td></tr>'
    html = f'''<h2 style="color:#3d5a80;">🧾 OC #{po.id}</h2>
<p><strong>{biz}</strong> — {to_ar(po.created_at).strftime("%d/%m/%Y %H:%M")}</p>
<p>Proveedor: <strong>{po.supplier.name if po.supplier else "—"}</strong></p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;">
<tr style="background:#3d5a80;color:#fff;"><th>Producto</th><th>Cant.</th><th>Costo U.</th><th>Subtotal</th></tr>
{rows}
</table>
<h3 style="text-align:right;">Total: ${po.total:.2f}</h3>'''
    try:
        send_po_email(to, f'🧾 OC #{po.id} - {biz}', html)
        flash(f'OC #{po.id} enviada a {to}', 'success')
    except Exception as e:
        flash(f'Error al enviar: {str(e)}', 'danger')
    return redirect(url_for('purchase_orders'))


@app.route('/purchase-orders/<int:po_id>/pdf')
@login_required
def purchase_order_pdf(po_id):
    if not current_user.can_manage_purchases():
        return {'error': 'Permiso denegado'}, 403
    po = db.session.get(PurchaseOrder, po_id)
    if not po:
        return {'error': 'OC no encontrada'}, 404
    items = json.loads(po.items_json)
    biz = get_config('business_name', 'Mi Negocio')
    rows = ''
    for i, item in enumerate(items, 1):
        p = db.session.get(Product, item['product_id'])
        name = item.get('name', p.name if p else '?')
        qty = item.get('quantity', 0)
        u = p.unit_type if p else 'unit'
        unit_label = '' if u == 'unit' else f' {u}'
        subtotal = item.get('subtotal', qty * item.get('cost', 0))
        rows += f'<tr><td>{i}</td><td>{name}</td><td>{qty}{unit_label}</td><td>${item.get("cost",0):.2f}</td><td>${subtotal:.2f}</td></tr>'
    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>OC #{po.id}</title>
<style>
body {{ font-family: Arial, sans-serif; padding: 30px; color: #293241; }}
.header {{ text-align: center; border-bottom: 3px solid #3d5a80; padding-bottom: 15px; margin-bottom: 20px; }}
.header h1 {{ color: #3d5a80; margin: 0; }} .header p {{ color: #666; margin: 5px 0; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ background: #3d5a80; color: #fff; padding: 10px; text-align: left; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #ddd; }}
.total {{ text-align: right; font-size: 1.2em; margin-top: 20px; padding-top: 10px; border-top: 2px solid #3d5a80; }}
.footer {{ margin-top: 40px; font-size: 0.85em; color: #999; text-align: center; }}
.badge {{ display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 0.8em; }}
.badge-pending {{ background: #ffc107; color: #000; }}
.badge-received {{ background: #198754; color: #fff; }}
</style></head><body>
<div class="header"><h1>🧾 ORDEN DE COMPRA #{po.id}</h1>
<p><strong>{biz}</strong></p>
<p>Fecha: {to_ar(po.created_at).strftime("%d/%m/%Y %H:%M")} | Estado: <span class="badge badge-{po.status}">{po.status}</span></p>
<p>Proveedor: <strong>{po.supplier.name if po.supplier else "—"}</strong></p></div>
<table><thead><tr><th>#</th><th>Producto</th><th>Cantidad</th><th>Costo U.</th><th>Subtotal</th></tr></thead><tbody>{rows}</tbody></table>
<div class="total">Total: <strong>${po.total:.2f}</strong></div>
<div class="footer">Documento generado por {biz} — {to_ar(datetime.now(AR_TZ)).strftime("%d/%m/%Y %H:%M")}</div>
</body></html>'''
    return html
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if current_user.role != 'admin':
        flash('Solo Admin puede acceder a configuración.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # Only save keys that are actually present in the form (each form has its own fields)
        processed = set()
        for key in request.form:
            if key in ('csrf_token', 'form_section') or key in processed:
                continue
            vals = request.form.getlist(key)
            val = vals[-1].strip() if vals else ''
            config = Config.query.filter_by(key=key).first()
            if config:
                config.value = val
            else:
                db.session.add(Config(key=key, value=val))
        db.session.commit()
        # Apply timezone change immediately
        if 'timezone' in request.form:
            set_timezone(request.form.get('timezone'))
        flash('Configuración guardada.', 'success')
        return redirect(url_for('settings'))

    configs = {c.key: c.value for c in Config.query.all()}
    role_perms = {}
    for role in ['admin', 'supervisor', 'user']:
        raw = configs.get(f'perms_{role}', '{}')
        try:
            role_perms[role] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            role_perms[role] = {}
    return render_template('settings.html', configs=configs, role_perms=role_perms)


@app.route('/admin/reset-system', methods=['POST'])
@login_required
def admin_reset_system():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    import shutil
    try:
        SaleItem.query.delete()
        Sale.query.delete()
        MovementLog.query.delete()
        PurchaseOrder.query.delete()
        PendingOrder.query.delete()
        CashClose.query.delete()
        DeletedRecord.query.delete()
        Product.query.delete()
        Supplier.query.delete()
        Category.query.delete()
        db.session.commit()
        log_movement(current_user, 'system_reset', 'Sistema limpiado: todos los datos eliminados excepto usuarios')
        flash('Sistema limpiado exitosamente. Todos los datos fueron eliminados excepto usuarios y configuración.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al limpiar: {str(e)}', 'danger')
    return redirect(url_for('settings'))


@app.route('/admin/clear-section', methods=['POST'])
@login_required
def admin_clear_section():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    section = request.form.get('section', '')
    try:
        if section == 'products':
            SaleItem.query.delete()
            Product.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todos los productos eliminados')
        elif section == 'suppliers':
            Product.query.filter(Product.supplier_id.isnot(None)).update({Product.supplier_id: None})
            PurchaseOrder.query.delete()
            Supplier.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todos los proveedores eliminados')
        elif section == 'categories':
            Product.query.filter(Product.category_id.isnot(None)).update({Product.category_id: None})
            Category.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todas las categorías eliminadas')
        elif section == 'cash_closes':
            CashClose.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todos los cierres de caja eliminados')
        elif section == 'sales':
            SaleItem.query.delete()
            Sale.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todas las ventas eliminadas')
        elif section == 'history':
            MovementLog.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todo el historial eliminado')
        elif section == 'trash':
            DeletedRecord.query.delete()
            log_movement(current_user, 'bulk_delete', 'Papelera vaciada')
        elif section == 'purchase_orders':
            PurchaseOrder.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todas las OC eliminadas')
        elif section == 'pending_orders':
            PendingOrder.query.delete()
            log_movement(current_user, 'bulk_delete', 'Todos los pedidos eliminados')
        else:
            return jsonify({'error': 'Sección inválida'}), 400
        db.session.commit()
        return jsonify({'success': True, 'message': f'Sección "{section}" limpiada'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/settings/backup-config', methods=['POST'])
@login_required
def save_backup_config():
    if not current_user.can_view_backups():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('settings'))
    keys_handled = ['backup_interval', 'backup_max_count', 'backup_time']
    for key in keys_handled:
        val = request.form.get(key, '').strip()
        config = Config.query.filter_by(key=key).first()
        if config:
            config.value = val
        else:
            db.session.add(Config(key=key, value=val))
    days = ','.join(request.form.getlist('backup_days'))
    config = Config.query.filter_by(key='backup_days').first()
    if config:
        config.value = days
    else:
        db.session.add(Config(key='backup_days', value=days))
    db.session.commit()
    flash('Configuración de backups guardada.', 'success')
    trim_backups()
    return redirect(url_for('backups'))


@app.route('/settings/permissions', methods=['POST'])
@login_required
def save_permissions():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    perm_keys = ['can_view_products', 'can_add_products', 'can_edit_products',
                 'can_manage_products',
                 'can_view_suppliers', 'can_add_suppliers', 'can_edit_suppliers', 'can_delete_suppliers',
                 'can_manage_users', 'can_toggle_users', 'can_reset_user_password', 'can_delete_users',
                 'can_view_history', 'can_sell',
                 'can_view_categories', 'can_add_categories', 'can_edit_categories', 'can_delete_categories',
                 'can_view_charts',
                 'can_take_orders', 'can_pay_membership',
                 'can_view_barcodes',
                 'can_view_trash',
                 'can_refund_sales',
                 'can_close_cash',
                 'can_void_cash_close',
                 'can_view_pending_sales',
                   'can_confirm_payment',
                   'can_view_backups',
                   'can_manage_purchases']
    for role in ['admin', 'supervisor', 'user']:
        perms = {}
        for pk in perm_keys:
            perms[pk] = 'on' in request.form.getlist(f'{role}_{pk}')
        cfg = Config.query.filter_by(key=f'perms_{role}').first()
        val = json.dumps(perms, ensure_ascii=False)
        if cfg:
            cfg.value = val
        else:
            db.session.add(Config(key=f'perms_{role}', value=val))
    db.session.commit()
    flash('Permisos guardados.', 'success')
    return redirect(url_for('settings'))


@app.route('/upload-logo', methods=['POST'])
@login_required
def upload_logo():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    if 'logo' not in request.files:
        flash('No se seleccionó archivo.', 'danger')
        return redirect(url_for('settings'))
    file = request.files['logo']
    if file.filename == '' or not allowed_file(file.filename):
        flash('Formato no válido. Usá PNG, JPG o GIF.', 'danger')
        return redirect(url_for('settings'))
    _save_uploaded_file(file, 'logo', 'logo_filename')
    flash('Logo actualizado.', 'success')
    return redirect(url_for('settings'))


@app.route('/delete-logo', methods=['POST'])
@login_required
def delete_logo():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    _delete_uploaded_file('logo_filename', 'logo')
    flash('Logo eliminado.', 'success')
    return redirect(url_for('settings'))


@app.route('/upload-favicon', methods=['POST'])
@login_required
def upload_favicon():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    if 'favicon' not in request.files:
        flash('No se seleccionó archivo.', 'danger')
        return redirect(url_for('settings'))
    file = request.files['favicon']
    if file.filename == '' or not allowed_file(file.filename):
        flash('Formato no válido. Usá PNG, JPG o GIF.', 'danger')
        return redirect(url_for('settings'))
    _save_uploaded_file(file, 'favicon', 'favicon_data')
    flash('Favicon actualizado.', 'success')
    return redirect(url_for('settings'))


@app.route('/delete-favicon', methods=['POST'])
@login_required
def delete_favicon():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    _delete_uploaded_file('favicon_data', 'favicon')
    flash('Favicon eliminado.', 'success')
    return redirect(url_for('settings'))


@app.route('/favicon.ico')
def favicon_ico():
    cfg = Config.query.filter_by(key='favicon_data').first()
    if cfg and cfg.value:
        if cfg.value.startswith('data:'):
            try:
                header, b64 = cfg.value.split(',', 1)
                mime = header.replace('data:', '').replace(';base64', '').strip()
                data = _b64lib.b64decode(b64)
                return Response(data, mimetype=mime)
            except Exception:
                pass
        else:
            _ensure_uploaded_file('favicon_data', 'favicon')
            filepath = os.path.join(app.config['FAVICON_FOLDER'], cfg.value)
            if os.path.exists(filepath):
                return send_file(filepath)
    return Response('', status=204)


BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
os.makedirs(BACKUP_DIR, exist_ok=True)


def trim_backups():
    max_count = int(get_config('backup_max_count', '0'))
    if max_count <= 0:
        return
    files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_') and f.endswith('.zip')], reverse=True)
    while len(files) > max_count:
        old = files.pop()
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def auto_backup_check():
    backup_days_str = get_config('backup_days', '')
    backup_time_str = get_config('backup_time', '')
    interval = int(get_config('backup_interval', '0'))
    now = datetime.now(AR_TZ)
    today_dow = now.strftime('%a')
    if backup_days_str and backup_time_str and ':' in backup_time_str:
        days_list = [d.strip() for d in backup_days_str.split(',') if d.strip()]
        if days_list and today_dow not in days_list:
            return
        try:
            h, m = backup_time_str.split(':')
            sched_min = int(h) * 60 + int(m)
            now_min = now.hour * 60 + now.minute
            if now_min < sched_min:
                return
        except ValueError:
            pass
        last_date = get_config('last_backup_date', '')
        today_str = now.strftime('%Y-%m-%d')
        if last_date == today_str:
            return
    elif interval > 0:
        files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_') and f.endswith('.zip')], reverse=True)
        if files:
            last = files[0]
            ts_str = last.replace('backup_', '').replace('.zip', '')
            try:
                last_time = datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
                if (datetime.now() - last_time).total_seconds() < interval * 3600:
                    return
            except ValueError:
                pass
    else:
        return
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    zip_path = None
    try:
        if db_url.startswith('sqlite'):
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'sistema.db')
            if os.path.exists(src):
                dst = os.path.join(BACKUP_DIR, f'backup_{ts}.db')
                shutil.copy2(src, dst)
                zip_path = dst + '.zip'
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(dst, 'sistema.db')
                os.remove(dst)
        else:
            dump = subprocess.run(['pg_dump', db_url], capture_output=True, text=True, timeout=30)
            if dump.returncode == 0:
                dst = os.path.join(BACKUP_DIR, f'backup_auto_{ts}.sql')
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write(dump.stdout)
                zip_path = dst + '.zip'
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(dst, f'backup_auto_{ts}.sql')
                os.remove(dst)
    except Exception:
        pass
    if zip_path:
        cfg_last = Config.query.filter_by(key='last_backup_date').first()
        if cfg_last:
            cfg_last.value = datetime.now(AR_TZ).strftime('%Y-%m-%d')
        else:
            db.session.add(Config(key='last_backup_date', value=datetime.now(AR_TZ).strftime('%Y-%m-%d')))
        db.session.commit()
        if get_config('drive_enabled', '') == 'on':
            try:
                upload_to_drive(zip_path, f'backup_auto_{ts}.zip')
            except Exception:
                pass
    trim_backups()


def demo_auto_reset_check():
    if get_config('demo_mode', '') != 'on':
        return
    interval = int(get_config('demo_reset_interval', '24'))
    last_reset = get_config('demo_last_reset', '')
    if last_reset:
        try:
            last = datetime.fromisoformat(last_reset)
            if (datetime.now(timezone.utc) - last).total_seconds() < interval * 3600:
                return
        except ValueError:
            pass
    with app.app_context():
        try:
            SaleItem.query.delete()
            Sale.query.delete()
            MovementLog.query.delete()
            PendingOrder.query.delete()
            CashClose.query.delete()
            DeletedRecord.query.delete()
            PurchaseOrder.query.delete()
            Product.query.delete()
            Supplier.query.delete()
            Category.query.delete()
            admin = User.query.filter_by(username='admin').first()
            if admin:
                User.query.filter(User.id != admin.id).delete()
            db.session.commit()
            cfg = Config.query.filter_by(key='demo_last_reset').first()
            if cfg:
                cfg.value = datetime.now(timezone.utc).isoformat()
            else:
                db.session.add(Config(key='demo_last_reset', value=datetime.now(timezone.utc).isoformat()))
            db.session.commit()
        except Exception:
            db.session.rollback()


@app.route('/backups')
@login_required
def backups():
    if not current_user.can_view_backups():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    auto_backup_check()
    configs = {c.key: c.value for c in Config.query.all()}
    files = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        fpath = os.path.join(BACKUP_DIR, f)
        if os.path.isfile(fpath) and f.startswith('backup_') and f.endswith('.zip'):
            try:
                ts_str = f.replace('backup_', '').replace('.zip', '').replace('_auto', '')
                ts = datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
                ts = ts.replace(tzinfo=timezone.utc).astimezone(AR_TZ)
                display_time = ts.strftime('%d/%m/%Y %H:%M')
            except ValueError:
                display_time = datetime.fromtimestamp(os.path.getmtime(fpath), tz=AR_TZ).strftime('%d/%m/%Y %H:%M')
            files.append({
                'name': f,
                'size': os.path.getsize(fpath),
                'mtime': display_time
            })
    return render_template('backups.html', backups=files, configs=configs)


@app.route('/backups/create', methods=['POST'])
@login_required
def backup_create():
    if not current_user.can_view_backups():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    zip_path = None
    if db_url.startswith('sqlite'):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'sistema.db')
        if os.path.exists(src):
            dst = os.path.join(BACKUP_DIR, f'backup_{ts}.db')
            shutil.copy2(src, dst)
            zip_path = dst + '.zip'
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(dst, 'sistema.db')
            os.remove(dst)
            flash(f'Backup creado: backup_{ts}.zip', 'success')
        else:
            flash('Base de datos no encontrada.', 'danger')
    else:
        try:
            dump = subprocess.run(['pg_dump', db_url], capture_output=True, text=True, timeout=30)
            if dump.returncode == 0:
                dst = os.path.join(BACKUP_DIR, f'backup_{ts}.sql')
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write(dump.stdout)
                zip_path = dst + '.zip'
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(dst, f'backup_{ts}.sql')
                os.remove(dst)
                flash(f'Backup creado: backup_{ts}.zip', 'success')
            else:
                flash(f'Error pg_dump: {dump.stderr[:200]}', 'danger')
        except FileNotFoundError:
            flash('pg_dump no está instalado en el servidor.', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)[:200]}', 'danger')
    if zip_path and get_config('drive_enabled', '') == 'on':
        try:
            result = upload_to_drive(zip_path, f'backup_{ts}.zip')
            if result:
                flash(f'Respaldado en Drive: {result.get("name")}', 'success')
        except Exception:
            pass
    trim_backups()
    return redirect(url_for('backups'))


@app.route('/backups/download/<name>')
@login_required
def backup_download(name):
    if not current_user.can_view_backups():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    fpath = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(fpath):
        flash('Archivo no encontrado.', 'danger')
        return redirect(url_for('backups'))
    return send_file(fpath, as_attachment=True)


@app.route('/backups/restore/<name>', methods=['POST'])
def backup_restore(name):
    if not current_user.can_view_backups():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('backups'))
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    try:
        if db_url.startswith('sqlite'):
            with zipfile.ZipFile(fpath, 'r') as zf:
                zf.extract('sistema.db', BACKUP_DIR)
            dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'sistema.db')
            shutil.copy2(os.path.join(BACKUP_DIR, 'sistema.db'), dst)
            os.remove(os.path.join(BACKUP_DIR, 'sistema.db'))
            flash('Base restaurada. Recargá la página.', 'success')
        else:
            with zipfile.ZipFile(fpath, 'r') as zf:
                sql_name = [n for n in zf.namelist() if n.endswith('.sql')][0]
                zf.extract(sql_name, BACKUP_DIR)
            sql_path = os.path.join(BACKUP_DIR, sql_name)
            result = subprocess.run(['psql', db_url], stdin=open(sql_path, 'r'),
                                    capture_output=True, text=True, timeout=60)
            os.remove(sql_path)
            if result.returncode == 0:
                flash('Base restaurada. Recargá la página.', 'success')
            else:
                flash(f'Error al restaurar: {result.stderr[:200]}', 'danger')
    except Exception as e:
        flash(f'Error: {str(e)[:200]}', 'danger')
    return redirect(url_for('backups'))


@app.route('/backups/upload', methods=['POST'])
def backup_upload():
    if not current_user.can_view_backups():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('backups'))
    f = request.files['backup_file']
    if f.filename == '' or not f.filename.endswith('.zip'):
        flash('Seleccioná un archivo .zip válido.', 'danger')
        return redirect(url_for('backups'))
    tmp = os.path.join(BACKUP_DIR, '_upload_temp.zip')
    f.save(tmp)
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    try:
        if db_url.startswith('sqlite'):
            with zipfile.ZipFile(tmp, 'r') as zf:
                names = zf.namelist()
                db_file = next((n for n in names if n.endswith('.db')), None)
                if not db_file:
                    flash('El .zip no contiene un archivo .db', 'danger')
                    os.remove(tmp)
                    return redirect(url_for('backups'))
                zf.extract(db_file, BACKUP_DIR)
            src = os.path.join(BACKUP_DIR, db_file)
            dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'sistema.db')
            shutil.copy2(src, dst)
            os.remove(src)
        else:
            with zipfile.ZipFile(tmp, 'r') as zf:
                names = zf.namelist()
                sql_file = next((n for n in names if n.endswith('.sql')), None)
                if not sql_file:
                    flash('El .zip no contiene un archivo .sql', 'danger')
                    os.remove(tmp)
                    return redirect(url_for('backups'))
                zf.extract(sql_file, BACKUP_DIR)
            sql_path = os.path.join(BACKUP_DIR, sql_file)
            with open(sql_path, 'r') as sf:
                result = subprocess.run(['psql', db_url], stdin=sf, capture_output=True, text=True, timeout=60)
            os.remove(sql_path)
            if result.returncode != 0:
                flash(f'Error: {result.stderr[:200]}', 'danger')
                os.remove(tmp)
                return redirect(url_for('backups'))
        flash('Backup restaurado desde archivo. Recargá la página.', 'success')
    except Exception as e:
        flash(f'Error: {str(e)[:200]}', 'danger')
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return redirect(url_for('backups'))


# ── Google Drive Backup ──

def get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        svc_json = get_config('drive_service_account_json', '')
        if not svc_json:
            return None
        creds = service_account.Credentials.from_service_account_info(
            json.loads(svc_json),
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        return build('drive', 'v3', credentials=creds, cache_discovery=False)
    except Exception:
        return None


def upload_to_drive(filepath, filename):
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None
    try:
        service = get_drive_service()
        if not service:
            return None
        folder_id = get_config('drive_folder_id', '')
        if not folder_id:
            return None
        media = MediaFileUpload(filepath, mimetype='application/zip', resumable=True)
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
            'description': f'SmartPost backup created at {datetime.now().strftime("%Y-%m-%d %H:%M")}'
        }
        f = service.files().create(body=file_metadata, media_body=media, fields='id,name,createdTime').execute()
        return f
    except Exception:
        return None


@app.route('/settings/drive-config', methods=['POST'])
@login_required
def save_drive_config():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    for key in ['drive_service_account_json', 'drive_folder_id', 'drive_enabled']:
        val = request.form.get(key, '').strip()
        cfg = Config.query.filter_by(key=key).first()
        if cfg:
            cfg.value = val
        else:
            db.session.add(Config(key=key, value=val))
    db.session.commit()
    flash('Configuración de Google Drive guardada.', 'success')
    # Test connection
    service = get_drive_service()
    if service:
        flash('Conexión con Drive exitosa.', 'success')
    else:
        flash('No se pudo conectar con Drive. Revisá el JSON de la cuenta de servicio.', 'warning')
    return redirect(url_for('settings'))


@app.route('/api/backups/drive')
@login_required
def api_drive_backups():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    folder_id = get_config('drive_folder_id', '')
    if not folder_id:
        return jsonify({'error': 'Drive no configurado', 'files': []})
    service = get_drive_service()
    if not service:
        return jsonify({'error': 'Error de conexión', 'files': []})
    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            orderBy='createdTime desc',
            pageSize=50,
            fields='files(id,name,size,createdTime,description)'
        ).execute()
        files = results.get('files', [])
        return jsonify({'files': [{
            'id': f['id'],
            'name': f['name'],
            'size': int(f.get('size', 0)),
            'created': f.get('createdTime', ''),
        } for f in files]})
    except Exception as e:
        return jsonify({'error': str(e)[:200], 'files': []})


@app.route('/backups/drive-restore/<file_id>', methods=['POST'])
@login_required
def drive_restore(file_id):
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('backups'))
    service = get_drive_service()
    if not service:
        flash('Error de conexión con Drive.', 'danger')
        return redirect(url_for('backups'))
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError:
        flash('Faltan librerías de Google.', 'danger')
        return redirect(url_for('backups'))
    try:
        request = service.files().get_media(fileId=file_id)
        tmp = os.path.join(BACKUP_DIR, '_drive_restore.zip')
        with open(tmp, 'wb') as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        db_url = app.config['SQLALCHEMY_DATABASE_URI']
        if db_url.startswith('sqlite'):
            with zipfile.ZipFile(tmp, 'r') as zf:
                names = zf.namelist()
                db_file = next((n for n in names if n.endswith('.db')), None)
                if not db_file:
                    flash('El .zip no contiene .db', 'danger')
                    os.remove(tmp)
                    return redirect(url_for('backups'))
                zf.extract(db_file, BACKUP_DIR)
            src = os.path.join(BACKUP_DIR, db_file)
            dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'sistema.db')
            shutil.copy2(src, dst)
            os.remove(src)
        else:
            with zipfile.ZipFile(tmp, 'r') as zf:
                names = zf.namelist()
                sql_file = next((n for n in names if n.endswith('.sql')), None)
                if not sql_file:
                    flash('El .zip no contiene .sql', 'danger')
                    os.remove(tmp)
                    return redirect(url_for('backups'))
                zf.extract(sql_file, BACKUP_DIR)
            sql_path = os.path.join(BACKUP_DIR, sql_file)
            with open(sql_path, 'r') as sf:
                result = subprocess.run(['psql', db_url], stdin=sf, capture_output=True, text=True, timeout=60)
            os.remove(sql_path)
            if result.returncode != 0:
                flash(f'Error: {result.stderr[:200]}', 'danger')
                os.remove(tmp)
                return redirect(url_for('backups'))
        flash('Backup restaurado desde Drive. Recargá la página.', 'success')
        os.remove(tmp)
    except Exception as e:
        flash(f'Error: {str(e)[:200]}', 'danger')
        if os.path.exists(tmp): os.remove(tmp)
    return redirect(url_for('backups'))


@app.route('/membership', methods=['GET', 'POST'])
@login_required
def membership():
    if current_user.role != 'admin':
        flash('Solo admin puede acceder.', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        keys = ['membership_enabled', 'membership_price', 'membership_grace_days',
                'membership_expiry', 'membership_payment_info', 'mp_membership_access_token']
        for key in keys:
            val = request.form.get(key, '')
            cfg = Config.query.filter_by(key=key).first()
            if cfg:
                cfg.value = val
            else:
                db.session.add(Config(key=key, value=val))
        db.session.commit()
        flash('Configuración de membresía guardada.', 'success')
        return redirect(url_for('membership'))
    data = {key: get_config(key) for key in
            ['membership_enabled', 'membership_price', 'membership_grace_days',
             'membership_expiry', 'membership_payment_info', 'mp_membership_access_token']}
    data['instance_id'] = get_instance_id()
    return render_template('membership.html', m=data)


@app.route('/api/membership/register-payment', methods=['POST'])
@login_required
def api_register_payment():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    from dateutil.relativedelta import relativedelta
    cfg = Config.query.filter_by(key='membership_expiry').first()
    today = datetime.now(AR_TZ).date()
    if cfg and cfg.value:
        try:
            current = datetime.strptime(cfg.value, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            current = today
        base = max(current, today)
    else:
        base = today
        if not cfg:
            cfg = Config(key='membership_expiry', value='')
            db.session.add(cfg)
    new_expiry = base + relativedelta(months=1, day=1)
    cfg.value = new_expiry.strftime('%Y-%m-%d')
    db.session.commit()
    return jsonify({'success': True, 'new_expiry': cfg.value})


@app.route('/membership-blocked')
def membership_blocked():
    return render_template('membership_blocked.html',
                           payment_info=get_config('membership_payment_info'))


@app.route('/planes')
@login_required
def planes():
    plans_data = get_config('plans_data', '')
    plans = json.loads(plans_data) if plans_data else []
    return render_template('planes.html', plans=plans)


@app.route('/api/planes/save', methods=['POST'])
@login_required
def planes_save():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    data = request.get_json()
    if not data or 'plans' not in data:
        return jsonify({'error': 'Datos inválidos'}), 400
    cfg = Config.query.filter_by(key='plans_data').first()
    if cfg:
        cfg.value = json.dumps(data['plans'], ensure_ascii=False)
    else:
        db.session.add(Config(key='plans_data', value=json.dumps(data['plans'], ensure_ascii=False)))
    db.session.commit()
    return jsonify({'success': True})


@app.route('/planes/pdf')
@login_required
def planes_pdf():
    rendered = render_template('planes_pdf.html')
    try:
        from weasyprint import HTML
        pdf = HTML(string=rendered).write_pdf()
        return Response(pdf, mimetype='application/pdf',
                        headers={'Content-Disposition': 'inline; filename=planes_smartpost.pdf'})
    except Exception:
        flash('Error al generar PDF. Descargá la página como PDF desde el navegador.', 'danger')
        return redirect(url_for('planes'))


@app.route('/api/planes/send-email', methods=['POST'])
@login_required
def planes_send_email():
    if current_user.role != 'admin':
        return jsonify({'error': 'Solo admin'}), 403
    owner_email = get_config('owner_email', '')
    if not owner_email:
        return jsonify({'error': 'Configurá el email del dueño en Settings primero.'}), 400
    rendered = render_template('planes_pdf.html')
    html_body = render_template('planes.html')
    try:
        from weasyprint import HTML
        pdf = HTML(string=rendered).write_pdf()
    except Exception:
        return jsonify({'error': 'Error al generar PDF en el servidor.'}), 500
    try:
        msg = MIMEMultipart()
        msg['Subject'] = f'Planes SmartPost - {get_config("business_name", "SmartPost")}'
        msg['From'] = get_config('smtp_user', '')
        msg['To'] = owner_email
        text = MIMEText('Adjuntamos los planes y precios de SmartPost. Podés verlos también en la web.', 'plain', 'utf-8')
        msg.attach(text)
        attach = MIMEBase('application', 'pdf')
        attach.set_payload(pdf)
        encoders.encode_base64(attach)
        attach.add_header('Content-Disposition', 'attachment', filename='planes_smartpost.pdf')
        msg.attach(attach)
        with smtplib.SMTP(get_config('smtp_host', ''), int(get_config('smtp_port', 587))) as server:
            server.starttls()
            server.login(get_config('smtp_user', ''), get_config('smtp_password', ''))
            server.send_message(msg)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': 'Error al enviar email: ' + str(e)[:100]}), 500


@app.route('/barcodes')
@login_required
def barcodes():
    if not current_user.can_view_barcodes():
        flash('No tienes permiso para ver esta página.', 'danger')
        return redirect(url_for('dashboard'))
    products_list = Product.query.order_by(Product.name).all()
    return render_template('barcodes.html', products=products_list)


@app.route('/admin/systems')
@login_required
def admin_systems():
    if current_user.role != 'admin':
        flash('Solo admin.', 'danger')
        return redirect(url_for('dashboard'))
    systems = System.query.order_by(System.sort_order).all()
    return render_template('admin_systems.html', systems=systems)


@app.route('/admin/systems/add', methods=['POST'])
@login_required
def admin_systems_add():
    if current_user.role != 'admin':
        return jsonify({'error': 'Permiso denegado'}), 403
    system = System(
        name=request.form.get('name', ''),
        tagline=request.form.get('tagline', ''),
        description=request.form.get('description', ''),
        logo_url=request.form.get('logo_url', ''),
        price=request.form.get('price', ''),
        category=request.form.get('category', ''),
        demo_url=request.form.get('demo_url', ''),
        features=request.form.get('features', ''),
        sort_order=int(request.form.get('sort_order', 0)),
        is_active='is_active' in request.form
    )
    db.session.add(system)
    db.session.commit()
    flash('Sistema creado.', 'success')
    return redirect(url_for('admin_systems'))


@app.route('/admin/systems/edit/<int:id>', methods=['POST'])
@login_required
def admin_systems_edit(id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Permiso denegado'}), 403
    system = db.session.get(System, id)
    if not system:
        flash('Sistema no encontrado.', 'danger')
        return redirect(url_for('admin_systems'))
    system.name = request.form.get('name', '')
    system.tagline = request.form.get('tagline', '')
    system.description = request.form.get('description', '')
    system.logo_url = request.form.get('logo_url', '')
    system.price = request.form.get('price', '')
    system.category = request.form.get('category', '')
    system.demo_url = request.form.get('demo_url', '')
    system.features = request.form.get('features', '')
    system.sort_order = int(request.form.get('sort_order', 0))
    system.is_active = 'is_active' in request.form
    db.session.commit()
    flash('Sistema actualizado.', 'success')
    return redirect(url_for('admin_systems'))


@app.route('/admin/systems/delete/<int:id>', methods=['POST'])
@login_required
def admin_systems_delete(id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Permiso denegado'}), 403
    system = db.session.get(System, id)
    if system:
        db.session.delete(system)
        db.session.commit()
        flash('Sistema eliminado.', 'success')
    return redirect(url_for('admin_systems'))


def init_app():
    with app.app_context():
        db.create_all()
        # Add new columns for existing databases (safe to run multiple times)
        for col, col_type in [('mp_payment_id', 'VARCHAR(100)'), ('mp_status', 'VARCHAR(20)'), ('customer_name', 'VARCHAR(100)')]:
            try:
                db.session.execute(db.text(f'ALTER TABLE sale ADD COLUMN {col} {col_type}'))
                db.session.commit()
            except Exception:
                db.session.rollback()
        for col in ['wholesale_qty', 'wholesale_price']:
            try:
                db.session.execute(db.text(f'ALTER TABLE product ADD COLUMN {col} FLOAT DEFAULT 0'))
                db.session.commit()
            except Exception:
                db.session.rollback()
        for col in ['refunded']:
            try:
                db.session.execute(db.text('ALTER TABLE sale ADD COLUMN refunded BOOLEAN DEFAULT FALSE'))
                db.session.commit()
            except Exception:
                db.session.rollback()
        for col in ['refunded_at']:
            try:
                db.session.execute(db.text('ALTER TABLE sale ADD COLUMN refunded_at TIMESTAMP'))
                db.session.commit()
            except Exception:
                db.session.rollback()
        for col in ['refunded_by']:
            try:
                db.session.execute(db.text('ALTER TABLE sale ADD COLUMN refunded_by INTEGER'))
                db.session.commit()
            except Exception:
                db.session.rollback()
        try:
            db.session.execute(db.text("ALTER TABLE sale ADD COLUMN payment_status VARCHAR(20) DEFAULT 'paid'"))
            db.session.commit()
        except Exception:
            db.session.rollback()
        for col, col_type in [('first_name', 'VARCHAR(100)'), ('last_name', 'VARCHAR(100)')]:
            try:
                db.session.execute(db.text(f'ALTER TABLE "user" ADD COLUMN {col} {col_type}'))
                db.session.commit()
            except Exception:
                db.session.rollback()
        try:
            db.session.execute(db.text('ALTER TABLE product ADD COLUMN unit_type VARCHAR(20) DEFAULT \'unit\''))
            db.session.commit()
        except Exception:
            db.session.rollback()
        try:
            db.session.execute(db.text('ALTER TABLE sale_item ALTER COLUMN quantity TYPE FLOAT'))
            db.session.commit()
        except Exception:
            db.session.rollback()
        # Ensure CashClose table exists
        try:
            db.session.execute(db.text('SELECT 1 FROM cash_close LIMIT 1'))
        except Exception:
            try:
                db.session.execute(db.text('''
                    CREATE TABLE cash_close (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES "user"(id),
                        opened_at TIMESTAMP NOT NULL,
                        closed_at TIMESTAMP,
                        initial_amount FLOAT DEFAULT 0,
                        cash_sales FLOAT DEFAULT 0,
                        card_sales FLOAT DEFAULT 0,
                        transfer_sales FLOAT DEFAULT 0,
                        mp_sales FLOAT DEFAULT 0,
                        total_sales FLOAT DEFAULT 0,
                        total_refunds FLOAT DEFAULT 0,
                        expected_cash FLOAT DEFAULT 0,
                        declared_cash FLOAT DEFAULT 0,
                        difference FLOAT DEFAULT 0,
                        notes TEXT DEFAULT ''
                    )
                '''))
                db.session.commit()
            except Exception:
                db.session.rollback()
                try:
                    db.session.execute(db.text('''
                        CREATE TABLE cash_close (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            opened_at TIMESTAMP NOT NULL,
                            closed_at TIMESTAMP,
                            initial_amount FLOAT DEFAULT 0,
                            cash_sales FLOAT DEFAULT 0,
                            card_sales FLOAT DEFAULT 0,
                            transfer_sales FLOAT DEFAULT 0,
                            mp_sales FLOAT DEFAULT 0,
                            total_sales FLOAT DEFAULT 0,
                            total_refunds FLOAT DEFAULT 0,
                            expected_cash FLOAT DEFAULT 0,
                            declared_cash FLOAT DEFAULT 0,
                            difference FLOAT DEFAULT 0,
                            notes TEXT DEFAULT ''
                        )
                    '''))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        try:
            db.session.execute(db.text('SELECT 1 FROM pending_order LIMIT 1'))
        except Exception:
            try:
                db.session.execute(db.text('''
                    CREATE TABLE pending_order (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES "user"(id),
                        items_json TEXT NOT NULL DEFAULT '[]',
                        customer_name VARCHAR(200) DEFAULT '',
                        notes TEXT DEFAULT '',
                        total FLOAT NOT NULL DEFAULT 0,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        created_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        sale_id INTEGER REFERENCES sale(id)
                    )
                '''))
                db.session.commit()
            except Exception:
                db.session.rollback()
                try:
                    db.session.execute(db.text('''
                        CREATE TABLE pending_order (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL REFERENCES "user"(id),
                            items_json TEXT NOT NULL DEFAULT '[]',
                            customer_name VARCHAR(200) DEFAULT '',
                            notes TEXT DEFAULT '',
                            total FLOAT NOT NULL DEFAULT 0,
                            status VARCHAR(20) NOT NULL DEFAULT 'pending',
                            created_at DATETIME,
                            completed_at DATETIME,
                            sale_id INTEGER REFERENCES sale(id)
                        )
                    '''))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        # Migrate cash_close voided columns
        for col in ['voided', 'voided_at', 'voided_by']:
            try:
                db.session.execute(db.text(f'ALTER TABLE cash_close ADD COLUMN {col} {"BOOLEAN DEFAULT FALSE" if col == "voided" else "TIMESTAMP" if col == "voided_at" else "INTEGER"}'))
                db.session.commit()
            except Exception:
                db.session.rollback()
        # Migrate base64 images to file storage
        _migrate_base64_to_file('logo_filename', 'logo')
        _migrate_base64_to_file('favicon_data', 'favicon')

        # Create missing indexes for performance
        for idx_name, table, cols in [
            ('ix_sale_created_at', 'sale', 'created_at'),
            ('ix_sale_payment_status', 'sale', 'payment_status'),
            ('ix_sale_item_sale_id', 'sale_item', 'sale_id'),
            ('ix_sale_item_product_id', 'sale_item', 'product_id'),
            ('ix_log_created_at', 'movement_log', 'created_at'),
            ('ix_product_stock', 'product', 'stock'),
            ('ix_product_name', 'product', 'name'),
            ('ix_sale_user_id', 'sale', 'user_id'),
            ('ix_movement_log_user_id', 'movement_log', 'user_id'),
            ('ix_sale_item_subtotal', 'sale_item', 'subtotal'),
            ('ix_deleted_record_type', 'deleted_record', 'record_type'),
        ]:
            try:
                db.session.execute(db.text(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols})'))
                db.session.commit()
            except Exception:
                db.session.rollback()

        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin', active=True)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print('Usuario admin creado (admin / admin123)')
        defaults = {
            'default_currency': 'ARS',
            'default_markup': '30',
            'low_stock_threshold': '10',
            'critical_stock_threshold': '5',
            'local_name': '',
            'owner_email': '91ezequiel.f@gmail.com',
            'membership_enabled': 'false',
            'membership_price': '10',
            'membership_grace_days': '5',
            'membership_expiry': '',
            'membership_payment_info': 'Alias: nesxocontrol.mp\nCBU: 0000000000000000000000',
            'multi_branch_enabled': 'false',
            'timezone': 'America/Argentina/Buenos_Aires',
            'demo_mode': '',
            'demo_reset_interval': '24',
            'weather_lat': '-34.6037',
            'weather_lon': '-58.3816',
            'po_email_from': '91ezequiel.f@gmail.com',
            'po_smtp_host': '',
            'po_smtp_port': '587',
            'po_smtp_user': '',
            'po_smtp_password': '',
        }
        for k, v in defaults.items():
            if not Config.query.filter_by(key=k).first():
                db.session.add(Config(key=k, value=v))

        if not Config.query.filter_by(key='instance_id').first():
            db.session.add(Config(key='instance_id', value=uuid.uuid4().hex[:12]))

        all_perm_keys = ['can_view_products','can_add_products','can_edit_products','can_manage_products',
                         'can_view_suppliers','can_add_suppliers','can_edit_suppliers','can_delete_suppliers',
                         'can_manage_users','can_toggle_users','can_reset_user_password','can_delete_users',
                         'can_view_history','can_sell',
                         'can_view_categories','can_add_categories','can_edit_categories','can_delete_categories',
                         'can_view_charts','can_pay_membership','can_take_orders',
                         'can_view_barcodes','can_view_trash','can_refund_sales',
                         'can_close_cash','can_void_cash_close',
                         'can_view_pending_sales','can_confirm_payment',
                         'can_view_backups','can_manage_purchases']
        default_perms = {
            'admin': {k: True for k in all_perm_keys},
            'supervisor': {k: k not in ('can_toggle_users','can_reset_user_password','can_delete_users','can_view_trash','can_void_cash_close','can_confirm_payment') for k in all_perm_keys},
            'user': {k: k in ('can_view_products','can_add_products','can_sell','can_view_charts',
                              'can_view_suppliers','can_view_categories','can_pay_membership') for k in all_perm_keys}
        }
        for role, perms in default_perms.items():
            key = f'perms_{role}'
            existing = Config.query.filter_by(key=key).first()
            if existing:
                try:
                    stored = json.loads(existing.value)
                except (json.JSONDecodeError, TypeError):
                    stored = {}
                changed = False
                for pk in all_perm_keys:
                    if pk not in stored:
                        stored[pk] = perms[pk]
                        changed = True
                if not changed and role == 'admin' and all(not stored.get(pk, True) for pk in all_perm_keys):
                    stored = {k: True for k in all_perm_keys}
                    changed = True
                if changed:
                    existing.value = json.dumps(stored, ensure_ascii=False)
            else:
                db.session.add(Config(key=key, value=json.dumps(perms, ensure_ascii=False)))
        db.session.commit()
        # Load timezone from config
        tz_cfg = Config.query.filter_by(key='timezone').first()
        if tz_cfg and tz_cfg.value:
            set_timezone(tz_cfg.value)


if not os.environ.get('TESTING'):
    init_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
