#!/bin/bash
docker exec -i nexoracore-db-1 psql -U nexora -d nexoracore <<'SQL'
SELECT key, value FROM config WHERE key IN ('logo_filename','favicon_data','logo_filename_b64_backup','favicon_data_b64_backup');
SQL
