#!/bin/bash
# Startup script de Nowcast GDL para Google Cloud (Compute Engine, e2-micro).
# Pegar TAL CUAL en "Avanzado -> Automatizacion -> Secuencia de comandos de
# inicio" al crear la VM. GCE lo ejecuta como root en cada arranque (a
# diferencia de Oracle, que solo lo corre una vez); el script es idempotente.
set -euxo pipefail

REPO_URL="https://github.com/0252611-stack/nowcast-gdl.git"
APP_DIR="/opt/nowcast-gdl"
DATA_DIR="/opt/nowcast-gdl/data"
SERVICE_USER="nowcast"
FRONTEND_ORIGIN="https://nowcast-gdl.vercel.app"

# --- IP publica -> hostname sslip.io (HTTPS real sin comprar dominio) ---
PUBLIC_IP="$(curl -s -4 https://ifconfig.me || curl -s -4 https://icanhazip.com)"
SSLIP_HOST="${PUBLIC_IP//./-}.sslip.io"

# --- Swap: e2-micro solo tiene 1GB RAM, insuficiente para compilar/instalar
#     opencv-python-headless + numpy sin swap. ---
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- Paquetes del sistema ---
export DEBIAN_FRONTEND=noninteractive

# El primer arranque de Debian corre apt-daily/unattended-upgrades en
# background y puede tener el lock de dpkg tomado; reintentar en vez de
# morir con set -e (causa real de la primera falla de este script).
apt_retry() {
    local n=0
    until "$@"; do
        n=$((n+1))
        if [ "$n" -ge 30 ]; then
            echo "apt_retry: agotados los reintentos para: $*" >&2
            return 1
        fi
        sleep 5
    done
}

apt_retry apt-get update -y
apt_retry apt-get install -y python3-venv python3-pip git libgl1 libglib2.0-0 \
    libsm6 libxext6 curl gnupg ufw openssl debian-keyring \
    debian-archive-keyring apt-transport-https

# --- Caddy (reverse proxy + HTTPS automatico) ---
if ! command -v caddy &>/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | tee /etc/apt/sources.list.d/caddy-stable.list
    apt_retry apt-get update -y
    apt_retry apt-get install -y caddy
fi

# --- Usuario dedicado, sin login (aisla el proceso de la app) ---
id -u "$SERVICE_USER" &>/dev/null || \
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"

# --- Clonar el repo ---
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$DATA_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

# --- Entorno virtual + dependencias del backend ---
sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

# --- ADMIN_TOKEN: se genera una sola vez, se conserva entre reinicios ---
if [ ! -f /etc/nowcast-gdl.env ]; then
    ADMIN_TOKEN="$(openssl rand -hex 24)"
    cat > /etc/nowcast-gdl.env <<EOF
DATA_DIR=$DATA_DIR
LOG_LEVEL=INFO
ADMIN_TOKEN=$ADMIN_TOKEN
ALLOWED_ORIGINS=$FRONTEND_ORIGIN,http://localhost:5173
EOF
    chmod 600 /etc/nowcast-gdl.env
    chown "$SERVICE_USER":"$SERVICE_USER" /etc/nowcast-gdl.env
fi

# --- Servicio systemd (arranca solo, se reinicia solo si crashea) ---
cat > /etc/systemd/system/nowcast-gdl.service <<EOF
[Unit]
Description=Nowcast GDL backend
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR/backend
EnvironmentFile=/etc/nowcast-gdl.env
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now nowcast-gdl
systemctl restart nowcast-gdl

# --- Caddy: HTTPS automatico via sslip.io, solo Caddy expuesto a internet ---
cat > /etc/caddy/Caddyfile <<EOF
$SSLIP_HOST {
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl restart caddy
systemctl enable caddy

# --- Firewall del sistema operativo (ademas de las reglas de red de GCE,
#     que ya abren 80/443 via las etiquetas http-server/https-server) ---
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# --- Resumen legible (SSH: sudo cat /root/DEPLOY_INFO.txt) ---
cat > /root/DEPLOY_INFO.txt <<EOF
==========================================================
Nowcast GDL desplegado en Google Cloud (e2-micro)
==========================================================
URL del backend : https://$SSLIP_HOST
ADMIN_TOKEN      : ver /etc/nowcast-gdl.env

Siguiente paso (fuera de esta VM): en Vercel, actualizar la
variable de entorno VITE_API_URL a https://$SSLIP_HOST y
redesplegar el frontend.

Nota: si la primera visita a la URL falla, espera 1-2 min a
que Caddy obtenga el certificado (Let's Encrypt via sslip.io).

Comandos utiles (por SSH, boton "SSH" en la consola de GCE):
  sudo systemctl status nowcast-gdl
  sudo journalctl -u nowcast-gdl -f
  sudo -u nowcast git -C $APP_DIR pull && sudo systemctl restart nowcast-gdl
==========================================================
EOF
chmod 600 /root/DEPLOY_INFO.txt
