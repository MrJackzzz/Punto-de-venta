import subprocess, sys, uuid, base64, os

dump = '/root/backup-nexoracore-/nexoracore_backup.dump'

# Create temp database
subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
    'createdb -U nexora tmp_r 2>/dev/null'], capture_output=True)

try:
    # Restore full backup to temp db
    r = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        f'pg_restore -U nexora -d tmp_r {dump} 2>&1'], capture_output=True, text=True)
    
    # Extract config values
    r2 = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        "psql -U nexora -d tmp_r -At -F'|' -c \"SELECT key, value FROM config WHERE key IN ('logo_filename','favicon_data')\""], 
        capture_output=True, text=True)
    print("Config output:", r2.stdout)
    
    for line in r2.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('|', 1)
        if len(parts) != 2:
            continue
        key, val = parts
        print(f"{key}: starts with data: {val.startswith('data:') if val else 'empty'}, len={len(val) if val else 0}")
        if val and val.startswith('data:'):
            subfolder = 'logo' if 'logo' in key else 'favicon'
            try:
                header, b64data = val.split(',', 1)
                mime_map = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}
                mime = header.replace('data:', '').replace(';base64', '').strip()
                ext = mime_map.get(mime, 'png')
                filename = f'{key}_{uuid.uuid4().hex[:12]}.{ext}'
                
                # Create file in container
                data = base64.b64decode(b64data)
                folder = f'/app/static/uploads/{subfolder}'
                subprocess.run(['docker', 'exec', '-i', 'nexoracore-web-1', 'sh', '-c',
                    f'mkdir -p {folder}'], capture_output=True)
                
                # Write file via base64 encoding to avoid pipe issues
                b64_encoded = base64.b64encode(data).decode()
                subprocess.run(['docker', 'exec', '-i', 'nexoracore-web-1', 'sh', '-c',
                    f'echo "{b64_encoded}" | base64 -d > {folder}/{filename}'], capture_output=True)
                
                print(f"Recovered {key} -> {folder}/{filename}")
                
                # Update config in the running DB
                subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
                    f'psql -U nexora -d nexoracore -c "UPDATE config SET value=\'{filename}\' WHERE key=\'{key}\'"'],
                    capture_output=True)
                print(f"Updated DB {key} -> {filename}")
            except Exception as e:
                print(f"Error processing {key}: {e}")
finally:
    subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        'dropdb -U nexora tmp_r 2>/dev/null'], capture_output=True)
