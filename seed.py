import random, sys
from models import db, Product, Category, Supplier, Sale, SaleItem, MovementLog, User, Config
from datetime import datetime, timezone, timedelta


def seed_data():
    if Product.query.count() > 0:
        print('La base ya tiene productos. Omitiendo seed.')
        return

    cat_names = ['Alimentos', 'Bebidas', 'Perfumería', 'Limpieza', 'Golosinas', 'Lácteos', 'Cigarrillos']
    cat_map = {}
    for name in cat_names:
        c = Category.query.filter_by(name=name).first()
        if not c:
            c = Category(name=name)
            db.session.add(c)
            db.session.flush()
        cat_map[name] = c.id
    db.session.commit()

    supp_data = [
        ('Distribuidora Sur', 'Carlos', '011-4567-8901', 'sur@email.com', 'Av. Rivadavia 1200'),
        ('Mayorista Norte', 'María', '011-4123-4567', 'norte@email.com', 'Av. Cabildo 3400'),
        ('Droguería Central', 'José', '011-4987-6543', 'central@email.com', 'Av. Corrientes 2500'),
        ('Alimentos SA', 'Ana', '011-4777-8888', 'alimentos@email.com', 'Av. Santa Fe 1800'),
        ('Perfumería Luján', 'Laura', '011-4666-5555', 'lujan@email.com', 'Av. Scalabrini 900'),
    ]
    supp_ids = []
    for name, contact, phone, email, addr in supp_data:
        s = Supplier.query.filter_by(name=name).first()
        if not s:
            s = Supplier(name=name, contact=contact, phone=phone, email=email, address=addr)
            db.session.add(s)
            db.session.flush()
        supp_ids.append(s.id)
    db.session.commit()
    print(f'Proveedores: {len(supp_ids)}')

    products = [
        ('Alfajor Tofi', 0, 250, 30, 450, 20),
        ('Alfajor Jorgito', 0, 180, 25, 320, 15),
        ('Papas Lays 120g', 0, 400, 20, 700, 30),
        ('Papas Lays 250g', 0, 700, 20, 1200, 25),
        ('Chocolate Milka 100g', 0, 500, 30, 900, 18),
        ('Chocolate Águila 70g', 0, 350, 25, 600, 22),
        ('Turrón Águila', 0, 120, 30, 220, 40),
        ('Mantecol 90g', 0, 250, 28, 440, 16),
        ('Bizcochitos Don Satur', 0, 200, 25, 360, 35),
        ('Pepas Terrabusi 180g', 0, 300, 22, 520, 14),
        ('Coca-Cola 500ml', 1, 320, 25, 550, 40),
        ('Coca-Cola 2.25L', 1, 700, 25, 1200, 30),
        ('Sprite 500ml', 1, 300, 25, 520, 35),
        ('Fanta 500ml', 1, 300, 25, 520, 25),
        ('Agua Villavicencio 500ml', 1, 180, 30, 320, 30),
        ('Agua Villavicencio 2L', 1, 350, 30, 600, 20),
        ('Gatorade Naranja 500ml', 1, 400, 25, 700, 20),
        ('Gatorade Mango 500ml', 1, 400, 25, 700, 18),
        ('Cerveza Quilmes 473ml', 1, 500, 30, 880, 25),
        ('Cerveza Stella 473ml', 1, 600, 30, 1050, 20),
        ('Shampoo Pantene 200ml', 2, 500, 35, 920, 12),
        ('Acondicionador Pantene 200ml', 2, 500, 35, 920, 10),
        ('Jabón Rexona 90g', 2, 150, 40, 280, 25),
        ('Desodorante Rexona Aerosol', 2, 400, 35, 740, 15),
        ('Perfume Avon 30ml', 2, 800, 50, 1600, 8),
        ('Crema Nivea 250ml', 2, 600, 30, 1050, 10),
        ('Protector Solar Nivea F50', 2, 900, 30, 1550, 6),
        ('Pasta Dental Colgate 90g', 2, 250, 40, 470, 20),
        ('Cepillo Dental Colgate', 2, 200, 50, 400, 15),
        ('Talco Axion 100g', 2, 120, 35, 220, 18),
        ('Lavandina Ayudín 1L', 3, 150, 30, 260, 25),
        ('Detergente Magistral 500ml', 3, 200, 30, 350, 20),
        ('Jabón Líquido Ala 750ml', 3, 300, 25, 510, 15),
        ('Limpiavidrios Mr. Músculo', 3, 250, 30, 440, 12),
        ('Esponja Scotch-Brite', 3, 80, 40, 150, 20),
        ('Bolsa de Residuos 45L x10', 3, 180, 35, 330, 18),
        ('Papel Higiénico Higienol x6', 3, 350, 30, 620, 22),
        ('Servilletas Elite x100', 3, 200, 28, 360, 16),
        ('Limpiapisos Poett 1L', 3, 280, 30, 500, 10),
        ('Desodorante Ambiental 200ml', 3, 180, 35, 330, 14),
        ('Chicle Bazooka x10', 4, 80, 40, 150, 25),
        ('Caramelo Menthoplus x8', 4, 60, 40, 110, 30),
        ('Chupetín Pico Dulce', 4, 50, 50, 100, 20),
        ('Gomitas Mogul 80g', 4, 150, 30, 270, 20),
        ('Huevo Kinder Sorpresa', 4, 350, 35, 650, 12),
        ('Leche La Serenísima 1L', 5, 400, 20, 680, 15),
        ('Yogur Ser 190g', 5, 250, 25, 440, 18),
        ('Queso Cremoso Mendicrim 200g', 5, 500, 25, 880, 10),
        ('Marlboro Box 20', 6, 1200, 15, 2000, 20),
        ('Philip Morris Box 20', 6, 1150, 15, 1900, 18),
    ]

    cat_list = list(cat_map.values())
    pcount = 0
    for name, cat_idx, cost, markup, price, stock in products:
        barcode = '779' + str(random.randint(1000000, 9999999)).zfill(7)
        if not Product.query.filter_by(name=name).first():
            p = Product(
                code=barcode, name=name, cost=cost,
                markup_percentage=markup, price=price, stock=stock,
                category_id=cat_list[cat_idx], supplier_id=random.choice(supp_ids)
            )
            db.session.add(p)
            pcount += 1
    db.session.commit()
    print(f'Productos: {pcount}')

    admin = User.query.filter_by(username='admin').first()
    sales = 0
    for _ in range(30):
        days_ago = random.randint(0, 60)
        sale_time = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=random.randint(0, 23))
        method = random.choice(['cash', 'card', 'transfer'])
        prods = random.sample(list(Product.query.all()), min(random.randint(1, 6), Product.query.count()))
        total = 0
        items_data = []
        for p in prods:
            qty = random.randint(1, 4)
            subtotal = p.price * qty
            total += subtotal
            items_data.append({'product_id': p.id, 'quantity': qty, 'unit_price': p.price, 'subtotal': subtotal})
        paid = total if method != 'cash' else total + round(random.uniform(0, total * 0.5), 2)
        s = Sale(user_id=admin.id, total=total, payment_method=method, amount_paid=paid,
                 change_amount=round(max(0, paid - total), 2), created_at=sale_time)
        db.session.add(s)
        db.session.flush()
        for sd in items_data:
            db.session.add(SaleItem(sale_id=s.id, **sd))
        db.session.add(MovementLog(user_id=admin.id, action='sale',
                                   description=f'Venta #{s.id} - ${total:.2f}', created_at=sale_time))
        sales += 1
    db.session.commit()
    print(f'Ventas: {sales}')
    print('Seed completado.')


if __name__ == '__main__':
    from app import app
    with app.app_context():
        from app import init_app
        init_app()
        seed_data()
