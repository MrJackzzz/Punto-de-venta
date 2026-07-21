import subprocess, sys, os, uuid, base64

dump = '/root/backup-nexoracore-/nexoracore_backup.dump'

# Create temp database
subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c', 
    'createdb -U nexora tmp_r 2>/dev/null'], capture_output=True)

try:
    # Restore full backup to temp db
    r = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        f'pg_restore -U nexora -d tmp_r {dump} 2>&1'], capture_output=True, text=True)
    print("Restore stderr (sample):", r.stderr[:500])
    
    # Extract config values
    r2 = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        "psql -U nexora -d tmp_r -At -F'|' -c \"SELECT key, value FROM config WHERE key IN ('logo_filename','favicon_data')\""], 
        capture_output=True, text=True)
    print("Config output:", r2.stdout)
    print("Config stderr:", r2.stderr[:200])
    
    # If we have base64 data, write it to files in the container
    for line in r2.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('|', 1)
        if len(parts) != 2:
            continue
        key, val = parts
        if val and val.startswith('data:'):
            # Determine subfolder
            subfolder = 'logo' if 'logo' in key else 'favicon'
            # Parse base64
            try:
                header, b64data = val.split(',', 1)
                mime_map = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}
                mime = header.replace('data:', '').replace(';base64', '').strip()
                ext = mime_map.get(mime, 'png')
                filename = f'{key}_{uuid.uuid4().hex[:12]}.{ext}'
                folder = os.path.join('/app/static/uploads', subfolder)
                os.makedirs(folder, exist_ok=True)
                filepath = os.path.join(folder, filename)
                data = base64.b64decode(b64data)
                with open(filepath, 'wb') as f:
                    f.write(data)
                print(f"Recovered {key} -> {filepath}")
                
                # Update config in the running DB
                subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
                    f'psql -U nexora -d nexoracore -c "UPDATE config SET value=\'{filename}\' WHERE key=\'{key}\'"'],
                    capture_output=True)
                print(f"Updated {key} to {filename}")
            except Exception as e:
                print(f"Error processing {key}: {e}")
finally:
    subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        'dropdb -U nexora tmp_r 2>/dev/null'], capture_output=True)
