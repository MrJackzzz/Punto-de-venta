#!/bin/bash
set -e
cd /var/www/nexoracore
# 1. Sync code
git fetch origin
git reset --hard origin/main

# 2. Backup uploaded files from old volume (if any)
if docker volume ls -q | grep -q 'nexoracore_uploads'; then
    echo "Copying files from old uploads volume..."
    docker run --rm -v nexoracore_uploads:/old -v $(pwd)/static:/new alpine sh -c "cp -r /old/* /new/ 2>/dev/null || true"
fi

# 3. Update nginx config with timeouts
cat > /etc/nginx/sites-enabled/nexoracore <<'NGINX'
server {
    listen 80;
    server_name _;
    client_max_body_size 20M;
    proxy_read_timeout 300;
    proxy_connect_timeout 10;
    proxy_send_timeout 10;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache_bypass $http_upgrade;
    }
}
NGINX
nginx -t && systemctl reload nginx

# 4. Rebuild and deploy
docker compose up -d --build web

# 5. Copy any missing static files into the bind mount
echo "Syncing static files from container to host..."
docker cp nexoracore-web-1:/app/static/. /var/www/nexoracore/static/ 2>/dev/null || true
