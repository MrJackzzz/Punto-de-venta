#!/bin/bash
set -e
cd /var/www/nexoracore

# 1. Sync code
git fetch origin && git reset --hard origin/main

# 2. Nginx config - serve static files directly for core
cat > /etc/nginx/sites-enabled/core.nexoraapps.com.ar <<'NGINX'
server {
    server_name core.nexoraapps.com.ar;
    client_max_body_size 20M;

    location /static/ {
        alias /var/www/nexoracore/static/;
        expires 365d;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120;
        proxy_connect_timeout 5;
        proxy_send_timeout 10;
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/core.nexoraapps.com.ar/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/core.nexoraapps.com.ar/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
server {
    if ($host = core.nexoraapps.com.ar) {
        return 301 https://$host$request_uri;
    }
    listen 80;
    server_name core.nexoraapps.com.ar;
    return 404;
}
NGINX

# 3. Also update fallback nexoracore config
cat > /etc/nginx/sites-enabled/nexoracore <<'NGINX_FB'
server {
    listen 80;
    server_name _;
    client_max_body_size 20M;
    proxy_read_timeout 120;
    proxy_connect_timeout 5;
    proxy_send_timeout 10;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }
}
NGINX_FB

# 4. Test and reload nginx
nginx -t && systemctl reload nginx

# 5. Rebuild container
docker compose up -d --build web

# 6. Ensure static dir is synced from container
docker cp nexoracore-web-1:/app/static/. /var/www/nexoracore/static/ 2>/dev/null || true

echo "Deploy complete!"
