# run inside container: docker exec -i nexoracore-web-1 python3 < check_logo_fix.py
import os, sys
sys.path.insert(0, '/app')
from app import app, db, Config

with app.app_context():
    cfg = Config.query.filter_by(key='logo_filename').first()
    if cfg:
        print(f"logo_filename value: {cfg.value}")
        print(f"starts with data: {cfg.value.startswith('data:')}")
        # check file
        fpath = os.path.join('/app/static/uploads/logo', cfg.value)
        print(f"file exists at {fpath}: {os.path.exists(fpath)}")
        print(f"dir exists: {os.path.exists('/app/static/uploads/logo')}")
    else:
        print("logo_filename config not found!")

    cfg2 = Config.query.filter_by(key='favicon_data').first()
    if cfg2:
        print(f"favicon_data value: {cfg2.value}")
        print(f"starts with data: {cfg2.value.startswith('data:')}")
        fpath2 = os.path.join('/app/static/uploads/favicon', cfg2.value)
        print(f"file exists at {fpath2}: {os.path.exists(fpath2)}")
    else:
        print("favicon_data config not found!")
    
    # Check if migration needs to run
    from app import _migrate_base64_to_file
    if cfg and cfg.value and cfg.value.startswith('data:'):
        fn = _migrate_base64_to_file('logo_filename', 'logo')
        print(f"migrated logo: {fn}")
    if cfg2 and cfg2.value and cfg2.value.startswith('data:'):
        fn = _migrate_base64_to_file('favicon_data', 'favicon')
        print(f"migrated favicon: {fn}")
