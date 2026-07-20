#!/usr/bin/env python3
"""
Script para agregar un nuevo cliente como subdominio de nexoraapps.com.ar
Uso: python3 add_client_subdomain.py <nombre> <puerto_local>

Ejemplo:
  python3 add_client_subdomain.py cliente2 8002

Esto crea:
  - cliente2.nexoraapps.com.ar → proxy_pass http://127.0.0.1:8002
  - SSL con certbot automático
"""

import sys
import os
import subprocess

NGINX_AVAILABLE = "/etc/nginx/sites-available"
NGINX_ENABLED = "/etc/nginx/sites-enabled"
DOMAIN_BASE = "nexoraapps.com.ar"
EMAIL = "alejandro@nexoraapps.com.ar"


def main():
    if len(sys.argv) != 3:
        print(f"Uso: {sys.argv[0]} <nombre> <puerto_local>")
        print(f"Ej: {sys.argv[0]} cliente2 8002")
        sys.exit(1)

    name = sys.argv[1].lower().strip()
    port = sys.argv[2].strip()
    domain = f"{name}.{DOMAIN_BASE}"

    config = f"""server {{
    listen 80;
    server_name {domain};

    client_max_body_size 20M;

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""

    # Write nginx config
    filepath = os.path.join(NGINX_AVAILABLE, domain)
    with open(filepath, "w") as f:
        f.write(config)
    print(f"✓ Creado {filepath}")

    # Symlink
    linkpath = os.path.join(NGINX_ENABLED, domain)
    if not os.path.exists(linkpath):
        os.symlink(filepath, linkpath)
        print(f"✓ Activado {linkpath}")

    # Test nginx
    result = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"✗ Error nginx: {result.stderr}")
        sys.exit(1)
    print("✓ nginx config OK")

    # Reload nginx
    subprocess.run(["systemctl", "reload", "nginx"])
    print("✓ nginx reloaded")

    # Get SSL cert
    result = subprocess.run(
        ["certbot", "--nginx", "-d", domain,
         "--non-interactive", "--agree-tos", "--email", EMAIL],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"✓ SSL certificate for https://{domain}")
    else:
        print(f"✗ Certbot error: {result.stderr}")

    print(f"\n✅ https://{domain} → http://127.0.0.1:{port}")


if __name__ == "__main__":
    main()
