from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, send_file, make_response, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, AnonymousUserMixin
from models import db, User, Product, Supplier, Sale, SaleItem, MovementLog, Config, Category
from datetime import datetime, timezone, timedelta
AR_TZ = timezone(timedelta(hours=-3))


def to_ar(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AR_TZ)
from werkzeug.utils import secure_filename
from sqlalchemy import func
import os, csv, io, json, smtplib, shutil, zipfile, subprocess
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.jinja_env.globals['to_ar'] = to_ar

def nl2br(text):
    return (text or '').replace('\n', '<br>')
app.jinja_env.filters['nl2br'] = nl2br


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
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['BACKUP_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['BACKUP_FOLDER'], exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_config(key, default=''):
    c = Config.query.filter_by(key=key).first()
    return c.value if c and c.value else default


@app.context_processor
def inject_globals():
    return {
        'business_name': get_config('business_name', 'NexoControl'),
        'local_name': get_config('local_name', ''),
        'logo_url': get_config('logo_filename', ''),
        'now': lambda: datetime.now(AR_TZ)
    }

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.anonymous_user = AnonymousUserMixin

# Add permission methods to AnonymousUserMixin that return False
_perm_methods = ['can_view_products','can_add_products','can_edit_products','can_manage_products',
                 'can_view_suppliers','can_add_suppliers','can_edit_suppliers','can_delete_suppliers',
                 'can_manage_users','can_view_history','can_sell',
                 'can_view_categories','can_add_categories','can_edit_categories','can_delete_categories',
                 'can_view_charts']
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


def get_low_stock_threshold():
    c = Config.query.filter_by(key='low_stock_threshold').first()
    return int(c.value) if c and c.value else 10

@app.route('/dashboard')
@login_required
def dashboard():
    check_critical_stock()
    threshold = get_low_stock_threshold()
    total_products = Product.query.count()
    categories = Category.query.count()
    low_stock = Product.query.filter(Product.stock < threshold).count()
    today = datetime.now(timezone.utc).date()
    today_sales = Sale.query.filter(
        db.func.date(Sale.created_at) == today
    ).count()
    cat_list = Category.query.order_by(Category.name).all()
    return render_template('dashboard.html', total_products=total_products,
                           low_stock=low_stock, today_sales=today_sales,
                           low_stock_threshold=threshold, categories_count=categories,
                           cat_list=cat_list)


@app.route('/products')
@login_required
def products():
    if not current_user.can_view_products():
        flash('No tienes permiso para ver productos.', 'danger')
        return redirect(url_for('dashboard'))
    threshold = get_low_stock_threshold()
    products_list = Product.query.order_by(Product.name).all()
    suppliers = Supplier.query.order_by(Supplier.name).all()
    categories = Category.query.order_by(Category.name).all()
    return render_template('products.html', products=products_list, suppliers=suppliers,
                           categories=categories, low_stock_threshold=threshold)


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
    stock = int(request.form.get('stock', 0))
    supplier_id = request.form.get('supplier_id')
    category_id = request.form.get('category_id')
    description = request.form.get('description', '')

    if Product.query.filter_by(code=code).first():
        flash('Ya existe un producto con ese código.', 'danger')
        return redirect(url_for('products'))

    product = Product(
        code=code, name=name, cost=cost,
        markup_percentage=markup, currency=currency,
        stock=stock, description=description
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
    product.stock = int(request.form.get('stock', 0))
    product.description = request.form.get('description', '')
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
    if current_user.role != 'admin':
        flash('Solo el Admin puede eliminar productos.', 'danger')
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
    today = datetime.now(timezone.utc).date()
    days = []
    amounts = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_sales = Sale.query.filter(
            db.func.date(Sale.created_at) == day
        ).all()
        total = round(sum(s.total for s in day_sales), 2)
        days.append(dias_es[day.weekday()])
        amounts.append(total)
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
            log.user.username, log.user.role,
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

    # Filter sales by product if specified
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
            'user': s.user.username,
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
    products = Product.query.order_by(Product.name).all()
    return render_template('profits.html', items=items_detail,
                           total_revenue=total_revenue, total_cost=total_cost,
                           total_profit=total_profit, total_margin=margin,
                           date_from=date_from, date_to=date_to,
                           product_id=product_id, products=products,
                           product_breakdown=product_breakdown)


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
            'user': s.user.username,
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
    return render_template('sell.html', products=products_list)


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
        'stock': product.stock
    })


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
        'price': p.price, 'currency': p.currency, 'stock': p.stock
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

    total = 0
    sale_items = []
    for item in items_data:
        product = db.session.get(Product, item['product_id'])
        if not product or product.stock < item['quantity']:
            return jsonify({'error': f'Stock insuficiente para {product.name if product else "producto"}'}), 400
        qty = int(item['quantity'])
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
        change_amount=change, customer_email=customer_email
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
            'subtotal': si['subtotal']
        })

    db.session.commit()
    log_movement(current_user, 'sale', f'Venta #{sale.id} - Total: ${total}')

    sale_items_objs = SaleItem.query.filter_by(sale_id=sale.id).all()
    send_ticket_email(sale, sale_items_objs, customer_email)

    return jsonify({
        'success': True,
        'sale_id': sale.id,
        'total': total,
        'amount_paid': amount_paid,
        'change': change,
        'payment_method': payment_method,
        'items': items_json,
        'user': current_user.username
    })


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


@app.route('/api/mp-webhook', methods=['POST'])
def mp_webhook():
    try:
        data = request.get_json()
        if data and data.get('type') == 'payment':
            payment_id = data.get('data', {}).get('id')
            if payment_id:
                access_token = get_config('mp_access_token', '')
                if access_token:
                    import requests
                    resp = requests.get(f'https://api.mercadopago.com/v1/payments/{payment_id}',
                                        headers={'Authorization': f'Bearer {access_token}'})
                    pay = resp.json()
                    sale_id = pay.get('external_reference')
                    if sale_id:
                        try:
                            sale_id = int(sale_id)
                        except (ValueError, TypeError):
                            sale_id = None
                        if sale_id:
                            sale = db.session.get(Sale, sale_id)
                            if sale:
                                sale.mp_payment_id = str(payment_id)
                                sale.mp_status = pay.get('status', 'unknown')
                                if pay.get('status') == 'approved':
                                    sale.payment_method = 'mercadopago'
                                db.session.commit()
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
    return render_template('ticket.html', sale=sale, items=items)


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
    if send_ticket_email(sale, items, email):
        return jsonify({'success': True, 'message': 'Ticket enviado por email'})
    else:
        return jsonify({'error': 'No se pudo enviar el email. Verificá la configuración SMTP en Admin > Config.'}), 500


@app.route('/suppliers')
@login_required
def suppliers():
    if not current_user.can_view_suppliers():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    suppliers_list = Supplier.query.order_by(Supplier.name).all()
    return render_template('suppliers.html', suppliers=suppliers_list)


@app.route('/suppliers/add', methods=['POST'])
@login_required
def supplier_add():
    if not current_user.can_add_suppliers():
        flash('Permiso denegado.', 'danger')
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
    return render_template('users.html', users=users_list)


@app.route('/users/add', methods=['POST'])
@login_required
def user_add():
    if not current_user.can_manage_users():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('users'))
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'user')

    if User.query.filter_by(username=username).first():
        flash('El usuario ya existe.', 'danger')
        return redirect(url_for('users'))

    user = User(username=username, role=role, active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    log_movement(current_user, 'user_create', f'Usuario creado: {username} ({role})')
    flash('Usuario creado.', 'success')
    return redirect(url_for('users'))


@app.route('/users/toggle/<int:id>', methods=['POST'])
@login_required
def user_toggle(id):
    if current_user.role != 'admin':
        flash('Solo Admin puede activar/desactivar usuarios.', 'danger')
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
    if current_user.role != 'admin':
        flash('Solo Admin puede resetear contraseñas.', 'danger')
        return redirect(url_for('users'))
    user = db.session.get(User, id)
    if user:
        new_pass = request.form.get('password', '123456')
        user.set_password(new_pass)
        db.session.commit()
        log_movement(current_user, 'user_reset_pass', f'Contraseña reseteada para {user.username}')
        flash('Contraseña actualizada.', 'success')
    return redirect(url_for('users'))


@app.route('/history')
@login_required
def history():
    if not current_user.can_view_history():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.order_by(User.username).all()
    return render_template('history.html', users=users)


@app.route('/api/history')
@login_required
def api_history():
    if not current_user.can_view_history():
        return jsonify({'error': 'Permiso denegado'}), 403

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

    total_sales_amount = 0
    total_sales_qty = 0
    if sale_ids:
        sales = Sale.query.filter(Sale.id.in_(sale_ids)).all()
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
        'user_toggle': 'Estado Usuario', 'user_reset_pass': 'Reset Pass'
    }

    log_list = []
    for log in logs:
        entry = {
            'id': log.id,
            'user': log.user.username,
            'role': log.user.role,
            'action': action_map.get(log.action, log.action),
            'action_key': log.action,
            'description': log.description,
            'time': to_ar(log.created_at).strftime('%d/%m/%Y %H:%M'),
        }
        if log.action == 'sale' and '#' in log.description:
            try:
                entry['sale_id'] = int(log.description.split('#')[1].split(' ')[0])
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
        'user': log.user.username,
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
    total_products = Product.query.count()
    categories_count = Category.query.count()
    low_stock = Product.query.filter(Product.stock < threshold).count()
    today = datetime.now(timezone.utc).date()
    today_sales = Sale.query.filter(
        db.func.date(Sale.created_at) == today
    ).count()
    return jsonify({
        'total_products': total_products,
        'categories_count': categories_count,
        'low_stock': low_stock,
        'today_sales': today_sales,
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
        'subtotal': item.subtotal
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


def send_ticket_email(sale, sale_items, customer_email=None):
    if not can_send_email():
        return False

    owner_email = Config.query.filter_by(key='owner_email').first()
    owner_email = owner_email.value if owner_email else ''

    if not customer_email and not owner_email:
        return False

    method_names = {'cash': 'Efectivo', 'card': 'Tarjeta', 'transfer': 'Transferencia'}
    items_html = ''.join(
        f'<tr><td>{item.product.name}</td><td style="text-align:center">{item.quantity}</td>'
        f'<td style="text-align:right">${item.unit_price:.2f}</td>'
        f'<td style="text-align:right">${item.subtotal:.2f}</td></tr>'
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
            <p style="font-size:12px;color:#555;">{to_ar(sale.created_at).strftime('%d/%m/%Y %H:%M')} | Atendió: {sale.user.username}</p>
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


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if current_user.role != 'admin':
        flash('Solo Admin puede acceder a configuración.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # Only save keys that are actually present in the form (each form has its own fields)
        for key in request.form:
            if key in ('csrf_token',):  # skip any non-config keys if needed
                continue
            val = request.form.get(key, '').strip()
            config = Config.query.filter_by(key=key).first()
            if config:
                config.value = val
            else:
                db.session.add(Config(key=key, value=val))
        db.session.commit()
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


@app.route('/settings/backup-config', methods=['POST'])
@login_required
def save_backup_config():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    for key in ['backup_interval', 'backup_max_count']:
        val = request.form.get(key, '').strip()
        config = Config.query.filter_by(key=key).first()
        if config:
            config.value = val
        else:
            db.session.add(Config(key=key, value=val))
    db.session.commit()
    flash('Configuración de backups guardada.', 'success')
    trim_backups()
    return redirect(url_for('settings'))


@app.route('/settings/permissions', methods=['POST'])
@login_required
def save_permissions():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    perm_keys = ['can_view_products', 'can_add_products', 'can_edit_products',
                 'can_manage_products',
                 'can_view_suppliers', 'can_add_suppliers', 'can_edit_suppliers', 'can_delete_suppliers',
                 'can_manage_users',
                 'can_view_history', 'can_sell',
                 'can_view_categories', 'can_add_categories', 'can_edit_categories', 'can_delete_categories',
                 'can_view_charts']
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
    filename = 'logo.' + file.filename.rsplit('.', 1)[1].lower()
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    cfg = Config.query.filter_by(key='logo_filename').first()
    val = 'uploads/' + filename
    if cfg:
        cfg.value = val
    else:
        db.session.add(Config(key='logo_filename', value=val))
    db.session.commit()
    flash('Logo actualizado.', 'success')
    return redirect(url_for('settings'))


@app.route('/delete-logo', methods=['POST'])
@login_required
def delete_logo():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    cfg = Config.query.filter_by(key='logo_filename').first()
    if cfg and cfg.value:
        fpath = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(cfg.value))
        if os.path.exists(fpath):
            os.remove(fpath)
        cfg.value = ''
        db.session.commit()
    flash('Logo eliminado.', 'success')
    return redirect(url_for('settings'))


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
    interval = int(get_config('backup_interval', '0'))
    if interval <= 0:
        return
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
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    try:
        if db_url.startswith('sqlite'):
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'sistema.db')
            if os.path.exists(src):
                dst = os.path.join(BACKUP_DIR, f'backup_{ts}.db')
                shutil.copy2(src, dst)
                with zipfile.ZipFile(dst + '.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(dst, 'sistema.db')
                os.remove(dst)
        else:
            dump = subprocess.run(['pg_dump', db_url], capture_output=True, text=True, timeout=30)
            if dump.returncode == 0:
                dst = os.path.join(BACKUP_DIR, f'backup_auto_{ts}.sql')
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write(dump.stdout)
                with zipfile.ZipFile(dst + '.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(dst, f'backup_auto_{ts}.sql')
                os.remove(dst)
    except Exception:
        pass
    trim_backups()
@app.route('/backups')
@login_required
def backups():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('dashboard'))
    auto_backup_check()
    configs = {c.key: c.value for c in Config.query.all()}
    files = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        fpath = os.path.join(BACKUP_DIR, f)
        if os.path.isfile(fpath) and f.startswith('backup_') and f.endswith('.zip'):
            files.append({
                'name': f,
                'size': os.path.getsize(fpath),
                'mtime': datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%d/%m/%Y %H:%M')
            })
    return render_template('backups.html', backups=files, configs=configs)


@app.route('/backups/create', methods=['POST'])
@login_required
def backup_create():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('dashboard'))
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    if db_url.startswith('sqlite'):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'sistema.db')
        if os.path.exists(src):
            dst = os.path.join(BACKUP_DIR, f'backup_{ts}.db')
            shutil.copy2(src, dst)
            with zipfile.ZipFile(dst + '.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
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
                with zipfile.ZipFile(dst + '.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(dst, f'backup_{ts}.sql')
                os.remove(dst)
                flash(f'Backup creado: backup_{ts}.zip', 'success')
            else:
                flash(f'Error pg_dump: {dump.stderr[:200]}', 'danger')
        except FileNotFoundError:
            flash('pg_dump no está instalado en el servidor.', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)[:200]}', 'danger')
    trim_backups()
    return redirect(url_for('backups'))


@app.route('/backups/download/<name>')
@login_required
def backup_download(name):
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('dashboard'))
    fpath = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(fpath):
        flash('Archivo no encontrado.', 'danger')
        return redirect(url_for('backups'))
    return send_file(fpath, as_attachment=True)


@app.route('/backups/restore/<name>', methods=['POST'])
@login_required
def backup_restore(name):
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('dashboard'))
    fpath = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(fpath):
        flash('Archivo no encontrado.', 'danger')
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


@app.route('/membership', methods=['GET', 'POST'])
@login_required
def membership():
    if current_user.role != 'admin':
        flash('Solo admin puede acceder.', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        keys = ['membership_enabled', 'membership_price', 'membership_grace_days',
                'membership_expiry', 'membership_payment_info']
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
             'membership_expiry', 'membership_payment_info']}
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


@app.before_request
def check_membership():
    if request.endpoint in ('login', 'logout', 'static', 'membership', 'membership_blocked'):
        return
    if current_user.is_authenticated and current_user.role == 'admin':
        return
    enabled = get_config('membership_enabled') == 'true'
    if not enabled:
        return
    expiry_str = get_config('membership_expiry')
    if not expiry_str:
        return
    try:
        expiry = datetime.strptime(expiry_str, '%Y-%m-%d').replace(tzinfo=AR_TZ)
    except (ValueError, TypeError):
        return
    grace = int(get_config('membership_grace_days', '5'))
    now = datetime.now(AR_TZ)
    if expiry + timedelta(days=grace) < now:
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Membresía vencida'}), 403
        return redirect(url_for('membership_blocked'))
    if expiry < now:
        remaining = (expiry + timedelta(days=grace) - now).days
        pay_info = get_config('membership_payment_info', '')
        flash(f'⚠️ Membresía vencida. Quedan {remaining} días antes del bloqueo.', 'warning')
        if pay_info:
            flash(f'📌 Datos de pago:\n{pay_info}', 'warning')
    elif (expiry - now).days <= 10:
        flash(f'⚠️ Membresía vence en {(expiry - now).days} días.', 'warning')


@app.route('/membership-blocked')
def membership_blocked():
    return render_template('membership_blocked.html',
                           payment_info=get_config('membership_payment_info'))


def init_app():
    with app.app_context():
        db.create_all()
        # Add new columns for existing databases (safe to run multiple times)
        for col, col_type in [('mp_payment_id', 'VARCHAR(100)'), ('mp_status', 'VARCHAR(20)')]:
            try:
                db.session.execute(db.text(f'ALTER TABLE sale ADD COLUMN {col} {col_type} DEFAULT \'\''))
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
            'membership_enabled': 'false',
            'membership_price': '10',
            'membership_grace_days': '5',
            'membership_expiry': '',
            'membership_payment_info': 'Alias: nesxocontrol.mp\nCBU: 0000000000000000000000',
        }
        for k, v in defaults.items():
            if not Config.query.filter_by(key=k).first():
                db.session.add(Config(key=k, value=v))

        all_perm_keys = ['can_view_products','can_add_products','can_edit_products','can_manage_products',
                         'can_view_suppliers','can_add_suppliers','can_edit_suppliers','can_delete_suppliers',
                         'can_manage_users','can_view_history','can_sell',
                         'can_view_categories','can_add_categories','can_edit_categories','can_delete_categories',
                         'can_view_charts']
        default_perms = {
            'admin': {k: True for k in all_perm_keys},
            'supervisor': {k: True for k in all_perm_keys},
            'user': {k: k in ('can_view_products','can_add_products','can_sell','can_view_charts',
                              'can_view_suppliers','can_view_categories') for k in all_perm_keys}
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


init_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
