from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Product, Supplier, Sale, SaleItem, MovementLog, Config, Category
from datetime import datetime, timezone, timedelta
from werkzeug.utils import secure_filename
from sqlalchemy import func
import os, csv, io, json, smtplib, shutil, zipfile, subprocess
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
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
        'logo_url': get_config('logo_filename', '')
    }

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


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
    log_movement(current_user, 'product_edit', f'Producto editado: {product.name}')
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
    if not current_user.can_access_categories():
        flash('Permiso denegado.', 'danger')
        return redirect(url_for('dashboard'))
    cats = Category.query.order_by(Category.name).all()
    return render_template('categories.html', categories=cats)


@app.route('/categories/add', methods=['POST'])
@login_required
def category_add():
    if not current_user.can_access_categories():
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
    if not current_user.can_access_categories():
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
            log.created_at.strftime('%d/%m/%Y %H:%M'),
            log.user.username, log.user.role,
            action_map.get(log.action, log.action), log.description
        ])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=historial.csv',
                             'Content-Type': 'text/csv; charset=utf-8'})


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


@app.route('/ticket/<int:id>')
@login_required
def ticket(id):
    sale = db.session.get(Sale, id)
    if not sale:
        flash('Venta no encontrada.', 'danger')
        return redirect(url_for('sell'))
    items = SaleItem.query.filter_by(sale_id=sale.id).all()
    return render_template('ticket.html', sale=sale, items=items)


@app.route('/suppliers')
@login_required
def suppliers():
    if not current_user.can_manage_suppliers():
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('dashboard'))
    suppliers_list = Supplier.query.order_by(Supplier.name).all()
    return render_template('suppliers.html', suppliers=suppliers_list)


@app.route('/suppliers/add', methods=['POST'])
@login_required
def supplier_add():
    if not current_user.can_manage_suppliers():
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
    if not current_user.can_manage_suppliers():
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
    if current_user.role != 'admin':
        flash('Solo Admin puede eliminar.', 'danger')
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

    return jsonify({
        'logs': [{
            'id': log.id,
            'user': log.user.username,
            'role': log.user.role,
            'action': action_map.get(log.action, log.action),
            'action_key': log.action,
            'description': log.description,
            'time': log.created_at.strftime('%d/%m/%Y %H:%M')
        } for log in logs],
        'summary': {
            'total_logs': len(logs),
            'total_sales_amount': round(total_sales_amount, 2),
            'total_sales_qty': total_sales_qty,
            'sale_count': len(sale_ids)
        }
    })


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


@app.route('/api/config/<key>')
@login_required
def api_config(key):
    config = Config.query.filter_by(key=key).first()
    if config:
        return jsonify({'key': key, 'value': config.value})
    return jsonify({'key': key, 'value': None})


def send_ticket_email(sale, sale_items, customer_email=None):
    owner_email = Config.query.filter_by(key='owner_email').first()
    owner_email = owner_email.value if owner_email else ''

    if not customer_email and not owner_email:
        return

    method_names = {'cash': 'Efectivo', 'card': 'Tarjeta', 'transfer': 'Transferencia'}
    items_html = ''.join(
        f'<tr><td>{item.product.name}</td><td style="text-align:center">{item.quantity}</td>'
        f'<td style="text-align:right">${item.unit_price:.2f}</td>'
        f'<td style="text-align:right">${item.subtotal:.2f}</td></tr>'
        for item in sale_items
    )

    biz_name = get_config('business_name', 'NexoControl')
    html = f"""
    <div style="font-family:Arial;max-width:400px;margin:0 auto;">
        <div style="text-align:center;background:#3d5a80;color:#fff;padding:15px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">{biz_name}</h2>
            <p style="margin:5px 0 0;font-size:13px;">Ticket #{sale.id}</p>
        </div>
        <div style="background:#f9f9f9;padding:15px;border:1px solid #ddd;">
            <p style="font-size:12px;color:#555;">{sale.created_at.strftime('%d/%m/%Y %H:%M')} | Atendió: {sale.user.username}</p>
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

    smtp_host = Config.query.filter_by(key='smtp_host').first()
    smtp_port = Config.query.filter_by(key='smtp_port').first()
    smtp_user = Config.query.filter_by(key='smtp_user').first()
    smtp_pass = Config.query.filter_by(key='smtp_password').first()

    host = smtp_host.value if smtp_host else ''
    port = int(smtp_port.value) if smtp_port and smtp_port.value else 587
    user = smtp_user.value if smtp_user else ''
    pwd = smtp_pass.value if smtp_pass else ''

    if not host or not user or not pwd:
        return

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'Ticket #{sale.id} - NexoControl'
        msg['From'] = user
        msg['To'] = ', '.join(recipients)
        msg.attach(MIMEText(html, 'html'))

        server = smtplib.SMTP(host, port)
        server.starttls()
        server.login(user, pwd)
        server.sendmail(user, recipients, msg.as_string())
        server.quit()
    except Exception as e:
        print(f'Error sending email: {e}')


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if current_user.role != 'admin':
        flash('Solo Admin puede acceder a configuración.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        keys = ['owner_email', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_password',
                'low_stock_threshold', 'default_currency', 'business_name',
                'backup_interval']
        for key in keys:
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


@app.route('/settings/permissions', methods=['POST'])
@login_required
def save_permissions():
    if current_user.role != 'admin':
        flash('Solo Admin.', 'danger')
        return redirect(url_for('settings'))
    perm_keys = ['can_view_products', 'can_add_products', 'can_edit_products',
                 'can_manage_products', 'can_manage_suppliers', 'can_manage_users',
                 'can_view_history', 'can_sell', 'can_access_categories',
                 'can_view_charts']
    for role in ['admin', 'supervisor', 'user']:
        perms = {}
        for pk in perm_keys:
            perms[pk] = request.form.get(f'{role}_{pk}') == 'on'
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


def init_app():
    with app.app_context():
        db.create_all()
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
        }
        for k, v in defaults.items():
            if not Config.query.filter_by(key=k).first():
                db.session.add(Config(key=k, value=v))

        default_perms = {
            'admin': {'can_view_products':True,'can_add_products':True,'can_edit_products':True,'can_manage_products':True,'can_manage_suppliers':True,'can_manage_users':True,'can_view_history':True,'can_sell':True,'can_access_categories':True,'can_view_charts':True},
            'supervisor': {'can_view_products':True,'can_add_products':True,'can_edit_products':True,'can_manage_products':True,'can_manage_suppliers':True,'can_manage_users':True,'can_view_history':True,'can_sell':True,'can_access_categories':True,'can_view_charts':True},
            'user': {'can_view_products':True,'can_add_products':True,'can_edit_products':False,'can_manage_products':False,'can_manage_suppliers':False,'can_manage_users':False,'can_view_history':False,'can_sell':True,'can_access_categories':False,'can_view_charts':True}
        }
        for role, perms in default_perms.items():
            key = f'perms_{role}'
            if not Config.query.filter_by(key=key).first():
                db.session.add(Config(key=key, value=json.dumps(perms, ensure_ascii=False)))
        db.session.commit()


init_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
