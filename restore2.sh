#!/bin/bash
set -e

# Read base64 data from files
LOGO_B64=$(cat /tmp/bk_logo_b64.txt)
FAVICON_B64=$(cat /tmp/bk_favicon_b64.txt)

# Extract just the base64 part (after the comma)
LOGO_DATA="${LOGO_B64#*,}"
FAVICON_DATA="${FAVICON_B64#*,}"

# Decode to temp files
echo "$LOGO_DATA" > /tmp/logo_b64_clean.txt
echo "$FAVICON_DATA" > /tmp/favicon_b64_clean.txt

base64 -d < /tmp/logo_b64_clean.txt > /tmp/logo_restored.png
base64 -d < /tmp/favicon_b64_clean.txt > /tmp/favicon_restored.png

echo "Decoded: $(wc -c < /tmp/logo_restored.png) bytes logo, $(wc -c < /tmp/favicon_restored.png) bytes favicon"

# Copy to container
docker exec -i nexoracore-web-1 mkdir -p /app/static/uploads/logo /app/static/uploads/favicon
docker cp /tmp/logo_restored.png nexoracore-web-1:/app/static/uploads/logo/logo_restored.png
docker cp /tmp/favicon_restored.png nexoracore-web-1:/app/static/uploads/favicon/favicon_restored.png

# Update DB
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "UPDATE config SET value='logo_restored.png' WHERE key='logo_filename';"
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "UPDATE config SET value='favicon_restored.png' WHERE key='favicon_data';"

# Store backup keys in DB filesystem (value too large for SQL)
# Store full base64 in files inside container
docker exec -i nexoracore-web-1 sh -c "cat > /app/static/uploads/logo/logo_b64_backup.txt" < /tmp/bk_logo_b64.txt
docker exec -i nexoracore-web-1 sh -c "cat > /app/static/uploads/favicon/favicon_b64_backup.txt" < /tmp/bk_favicon_b64.txt

echo "=== Verification ==="
docker exec -i nexoracore-web-1 ls -la /app/static/uploads/logo/
docker exec -i nexoracore-web-1 ls -la /app/static/uploads/favicon/
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "SELECT key, value FROM config WHERE key IN ('logo_filename','favicon_data');"

# Clean up
rm -f /tmp/logo_b64_clean.txt /tmp/favicon_b64_clean.txt /tmp/logo_restored.png /tmp/favicon_restored.png /tmp/bk_logo_b64.txt /tmp/bk_favicon_b64.txt
