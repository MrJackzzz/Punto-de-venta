import os, sys
sys.path.insert(0, '/app')
from app import app, db, Config

with app.app_context():
    for key in ['logo_filename', 'favicon_data']:
        cfg = Config.query.filter_by(key=key).first()
        if cfg:
            fpath = os.path.join(app.config['UPLOAD_FOLDER'], 
                                 'logo' if key == 'logo_filename' else 'favicon', 
                                 cfg.value)
            if not cfg.value.startswith('data:') and not os.path.exists(fpath):
                print(f"{key}: file missing ({cfg.value}), clearing config value")
                cfg.value = ''
                db.session.commit()
    print("Done. User can re-upload images from Settings.")
