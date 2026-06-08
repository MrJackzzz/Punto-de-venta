import json
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')
    active = db.Column(db.Boolean, default=True)
    first_name = db.Column(db.String(100), default='')
    last_name = db.Column(db.String(100), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_full_name(self):
        if self.first_name and self.last_name:
            return f'{self.last_name}, {self.first_name}'
        if self.first_name:
            return self.first_name
        if self.last_name:
            return self.last_name
        return self.username

    def _role_perm(self, perm, default=False):
        cfg = Config.query.filter_by(key=f'perms_{self.role}').first()
        if cfg and cfg.value:
            try:
                perms = json.loads(cfg.value)
                if perm in perms:
                    return perms[perm]
            except (json.JSONDecodeError, TypeError):
                pass
        return default

    def can_manage_users(self):
        return self._role_perm('can_manage_users', self.role in ('admin', 'supervisor'))

    def can_view_products(self):
        return self._role_perm('can_view_products', True)

    def can_add_products(self):
        return self._role_perm('can_add_products', True)

    def can_edit_products(self):
        return self._role_perm('can_edit_products', self.role in ('admin', 'supervisor'))

    def can_manage_products(self):
        return self._role_perm('can_manage_products', self.role in ('admin', 'supervisor'))

    def can_manage_suppliers(self):
        return self._role_perm('can_manage_suppliers', self.role in ('admin', 'supervisor'))

    def can_view_suppliers(self):
        return self._role_perm('can_view_suppliers', self.role in ('admin', 'supervisor'))

    def can_add_suppliers(self):
        return self._role_perm('can_add_suppliers', self.role in ('admin', 'supervisor'))

    def can_edit_suppliers(self):
        return self._role_perm('can_edit_suppliers', self.role in ('admin', 'supervisor'))

    def can_delete_suppliers(self):
        return self._role_perm('can_delete_suppliers', self.role in ('admin', 'supervisor'))

    def can_view_history(self):
        return self._role_perm('can_view_history', self.role in ('admin', 'supervisor'))

    def can_sell(self):
        return self._role_perm('can_sell', True)

    def can_access_categories(self):
        return self._role_perm('can_access_categories', self.role == 'admin')

    def can_view_categories(self):
        return self._role_perm('can_view_categories', self.role in ('admin', 'supervisor'))

    def can_add_categories(self):
        return self._role_perm('can_add_categories', self.role in ('admin', 'supervisor'))

    def can_edit_categories(self):
        return self._role_perm('can_edit_categories', self.role in ('admin', 'supervisor'))

    def can_delete_categories(self):
        return self._role_perm('can_delete_categories', self.role in ('admin', 'supervisor'))

    def can_view_charts(self):
        return self._role_perm('can_view_charts', True)

    def can_pay_membership(self):
        return self._role_perm('can_pay_membership', self.role in ('admin', 'supervisor'))

    def can_take_orders(self):
        return self._role_perm('can_take_orders', self.role in ('admin', 'supervisor'))

    def can_view_barcodes(self):
        return self._role_perm('can_view_barcodes', self.role == 'admin')

    def can_view_trash(self):
        return self._role_perm('can_view_trash', self.role == 'admin')

    def can_toggle_users(self):
        return self._role_perm('can_toggle_users', self.role == 'admin')

    def can_reset_user_password(self):
        return self._role_perm('can_reset_user_password', self.role == 'admin')

    def can_delete_users(self):
        return self._role_perm('can_delete_users', self.role == 'admin')


class PendingOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    items_json = db.Column(db.Text, nullable=False, default='[]')
    customer_name = db.Column(db.String(200), default='')
    notes = db.Column(db.Text, default='')
    total = db.Column(db.Float, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default='pending')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=True)

    user = db.relationship('User', backref='pending_orders')
    sale = db.relationship('Sale', backref='pending_order')


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    cost = db.Column(db.Float, nullable=False, default=0)
    markup_percentage = db.Column(db.Float, nullable=False, default=0)
    price = db.Column(db.Float, nullable=False, default=0)
    currency = db.Column(db.String(10), nullable=False, default='ARS')
    stock = db.Column(db.Float, nullable=False, default=0)
    unit_type = db.Column(db.String(20), nullable=False, default='unit')
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    supplier = db.relationship('Supplier', backref='products')
    category = db.relationship('Category', backref='products')

    def calculate_price(self):
        self.price = round(self.cost * (1 + self.markup_percentage / 100), 2)
        return self.price


class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact = db.Column(db.String(200), default='')
    phone = db.Column(db.String(50), default='')
    email = db.Column(db.String(100), default='')
    address = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total = db.Column(db.Float, nullable=False, default=0)
    payment_method = db.Column(db.String(20), nullable=False, default='cash')
    amount_paid = db.Column(db.Float, nullable=False, default=0)
    change_amount = db.Column(db.Float, nullable=False, default=0)
    customer_email = db.Column(db.String(100), default='')
    customer_name = db.Column(db.String(100), default='')
    mp_payment_id = db.Column(db.String(100), default='')
    mp_status = db.Column(db.String(20), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='sales')
    items = db.relationship('SaleItem', backref='sale', lazy='dynamic')


class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)

    product = db.relationship('Product')


class MovementLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='logs')


class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)


class System(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    tagline = db.Column(db.String(300), default='')
    description = db.Column(db.Text, default='')
    logo_url = db.Column(db.String(500), default='')
    price = db.Column(db.String(100), default='')
    category = db.Column(db.String(100), default='')
    demo_url = db.Column(db.String(500), default='')
    features = db.Column(db.Text, default='')
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class DeletedRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    record_type = db.Column(db.String(20), nullable=False)
    record_id = db.Column(db.Integer)
    data_json = db.Column(db.Text, nullable=False)
    deleted_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    deleted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    restored_at = db.Column(db.DateTime)

    deleter = db.relationship('User', backref='deleted_records')
