import subprocess

dump = '/root/backup-nexoracore-/nexoracore_backup.dump'

# Create temp DB
subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
    'createdb -U nexora tmp_r 2>/dev/null'], capture_output=True)

try:
    # Restore
    r = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        f'pg_restore -U nexora -d tmp_r {dump} 2>&1'], capture_output=True, text=True)
    print("pg_restore exit:", r.returncode)
    print("pg_restore stderr:", r.stderr[:1000])
    
    # Check tables
    r2 = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        "psql -U nexora -d tmp_r -At -c 'SELECT tablename FROM pg_tables WHERE schemaname='"'""'public'"'""''"],
        capture_output=True, text=True)
    print("Tables:", r2.stdout)
    
    # Check config
    r3 = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        "psql -U nexora -d tmp_r -At -c 'SELECT COUNT(*) FROM config'"],
        capture_output=True, text=True)
    print("Config rows:", r3.stdout)
    
    r4 = subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        "psql -U nexora -d tmp_r -At -F'|' -c \"SELECT key, value FROM config WHERE key LIKE '%logo%' OR key LIKE '%favicon%'\""],
        capture_output=True, text=True)
    print("Logo/favicon config:", r4.stdout)

finally:
    subprocess.run(['docker', 'exec', '-i', 'nexoracore-db-1', 'sh', '-c',
        'dropdb -U nexora tmp_r 2>/dev/null'], capture_output=True)
