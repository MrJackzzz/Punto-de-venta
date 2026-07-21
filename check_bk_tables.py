import subprocess
dump = '/root/backup-nexoracore-/nexoracore_backup.dump'

r = subprocess.run(['pg_restore', '-l', dump], capture_output=True, text=True)
tables = [l.strip() for l in r.stdout.split('\n') if 'TABLE DATA' in l or 'TABLE' in l]
for t in tables:
    print(t)
