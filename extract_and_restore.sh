#!/bin/bash
set -e
docker cp /root/backup-nexoracore-/nexoracore_backup.dump nexoracore-db-1:/tmp/bk.dump
docker exec -i nexoracore-db-1 createdb -U nexora tmp_r 2>/dev/null || true
docker exec -i nexoracore-db-1 pg_restore -U nexora -d tmp_r /tmp/bk.dump 2>&1 | tail -1

# Export base64 values to files
docker exec -i nexoracore-db-1 psql -U nexora -d tmp_r -t -A -c "SELECT value FROM config WHERE key='logo_filename';" > /tmp/bk_logo_b64.txt
docker exec -i nexoracore-db-1 psql -U nexora -d tmp_r -t -A -c "SELECT value FROM config WHERE key='favicon_data';" > /tmp/bk_favicon_b64.txt

echo "logo file size: $(wc -c < /tmp/bk_logo_b64.txt) bytes"
echo "favicon file size: $(wc -c < /tmp/bk_favicon_b64.txt) bytes"

# Cleanup temp DB
docker exec -i nexoracore-db-1 dropdb -U nexora tmp_r
docker exec -i nexoracore-db-1 rm /tmp/bk.dump
