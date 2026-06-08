from app import app, db
from sqlalchemy import inspect

from sqlalchemy import text

with app.app_context():
    inspector = inspect(db.engine)
    cols = [c['name'] for c in inspector.get_columns('sale')]
    if 'customer_name' not in cols:
        db.session.execute(text('ALTER TABLE sale ADD COLUMN customer_name VARCHAR(100) DEFAULT ""'))
        db.session.commit()
        print('customer_name column added')
    else:
        print('customer_name column already exists')
