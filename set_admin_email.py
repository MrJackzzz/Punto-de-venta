import subprocess, json

cmd = ['docker', 'exec', '-i', 'nexoracore-db-1', 'psql', '-U', 'nexora', '-d', 'nexoracore', '-t', '-c']
r = subprocess.run(cmd + ["SELECT value FROM config WHERE key='owner_email'"], capture_output=True, text=True)
current = r.stdout.strip()
print(f'Current owner_email: {repr(current)}')

# Set it to the admin email
subprocess.run(cmd + ["INSERT INTO config (key, value) VALUES ('owner_email', '91ezequiel.f@gmail.com') ON CONFLICT (key) DO UPDATE SET value='91ezequiel.f@gmail.com'"], capture_output=True, text=True)

# Verify
r = subprocess.run(cmd + ["SELECT value FROM config WHERE key='owner_email'"], capture_output=True, text=True)
print(f'After update: {repr(r.stdout.strip())}')
