#!/bin/bash
set -e

# Read base64 data from files
LOGO_B64=$(cat /tmp/bk_logo_b64.txt)
FAVICON_B64=$(cat /tmp/bk_favicon_b64.txt)

# Extract just the base64 part (after the comma)
LOGO_DATA="${LOGO_B64#*,}"
FAVICON_DATA="${FAVICON_B64#*,}"

# Generate filenames
LOGO_FILENAME="logo_filename_restored_$(date +%s).png"
FAVICON_FILENAME="favicon_data_restored_$(date +%s).png"

# Write files into the container's upload volume
echo "$LOGO_DATA" | base64 -d | docker exec -i nexoracore-web-1 sh -c "mkdir -p /app/static/uploads/logo && cat > /app/static/uploads/logo/$LOGO_FILENAME"
echo "$FAVICON_DATA" | base64 -d | docker exec -i nexoracore-web-1 sh -c "mkdir -p /app/static/uploads/favicon && cat > /app/static/uploads/favicon/$FAVICON_FILENAME"

# Update DB
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "UPDATE config SET value='$LOGO_FILENAME' WHERE key='logo_filename';"
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "UPDATE config SET value='$FAVICON_FILENAME' WHERE key='favicon_data';"

# Also store base64 backups for future recovery
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "INSERT INTO config (key, value) VALUES ('logo_filename_b64_backup', '$LOGO_B64') ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;"
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "INSERT INTO config (key, value) VALUES ('favicon_data_b64_backup', '$FAVICON_B64') ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;"

# Verify
echo "=== Verification ==="
docker exec -i nexoracore-web-1 ls -la /app/static/uploads/logo/
docker exec -i nexoracore-web-1 ls -la /app/static/uploads/favicon/
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore -c "SELECT key, value FROM config WHERE key IN ('logo_filename','favicon_data','logo_filename_b64_backup','favicon_data_b64_backup');"

# Clean up temp files
rm -f /tmp/bk_logo_b64.txt /tmp/bk_favicon_b64.txt /tmp/extract_and_restore.sh
