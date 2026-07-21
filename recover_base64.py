# Run on server: scp + docker exec
import subprocess, sys, os, tempfile, json

# Extract config table from backup dump into a temp DB
dump_path = '/root/backup-nexoracore-/nexoracore_backup.dump'

# Use pg_restore to list contents, then extract just config table
result = subprocess.run(
    ['pg_restore', '-l', dump_path],
    capture_output=True, text=True
)
for line in result.stdout.split('\n'):
    if 'config' in line.lower() and 'TABLE' in line:
        print(line)

# Try to extract as text
result2 = subprocess.run(
    ['pg_restore', '-f', '-', '--data-only', '-t', 'config', dump_path],
    capture_output=True, text=True
)
print("STDOUT:", result2.stdout[:2000])
print("STDERR:", result2.stderr[:500])
