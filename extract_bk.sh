#!/bin/bash
set -e
docker cp /root/backup-nexoracore-/nexoracore_backup.dump nexoracore-db-1:/tmp/bk.dump
docker exec -i nexoracore-db-1 createdb -U nexora tmp_r 2>/dev/null || true
docker exec -i nexoracore-db-1 pg_restore -U nexora -d tmp_r /tmp/bk.dump 2>&1 | tail -2
echo "=== logo_filename ==="
docker exec -i nexoracore-db-1 psql -U nexora -d tmp_r -t -A -c "SELECT value FROM config WHERE key='logo_filename';"
echo "=== favicon_data ==="
docker exec -i nexoracore-db-1 psql -U nexora -d tmp_r -t -A -c "SELECT value FROM config WHERE key='favicon_data';"
docker exec -i nexoracore-db-1 dropdb -U nexora tmp_r
docker exec -i nexoracore-db-1 rm /tmp/bk.dump
