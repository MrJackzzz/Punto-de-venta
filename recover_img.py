import subprocess, sys

dump = '/root/backup-nexoracore-/nexoracore_backup.dump'
keys = ["logo_filename", "favicon_data", "logo_filename_b64_backup", "favicon_data_b64_backup"]

# Create temp DB
subprocess.run(['createdb', '-U', 'nexora', '-h', 'localhost', 'tmp_r'], capture_output=True)

try:
    # Restore config table
    subprocess.run(['pg_restore', '-U', 'nexora', '-h', 'localhost', '-d', 'tmp_r', '--data-only', '-t', 'config', dump], capture_output=True)
    
    for key in keys:
        r = subprocess.run(['psql', '-U', 'nexora', '-h', 'localhost', '-d', 'tmp_r', '-t', '-A', '-F', '|', '-c', f"SELECT value FROM config WHERE key='{key}'"], capture_output=True, text=True)
        val = r.stdout.strip()
        if val:
            print(f"{key}: {val[:120]}...")
        else:
            print(f"{key}: NOT FOUND")
finally:
    subprocess.run(['dropdb', '-U', 'nexora', '-h', 'localhost', 'tmp_r'], capture_output=True)
