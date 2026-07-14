#!/bin/bash
# Cloud-init de Nowcast GDL para Oracle Cloud Free Tier (Always Free).
# Pegar TAL CUAL en "Advanced options -> Cloud-init script" al crear la VM.
# Se ejecuta una sola vez, como root, en el primer arranque. No requiere SSH.
#
# Qué hace: instala Python + deps del sistema, clona el repo, crea un venv,
# arma un servicio systemd (reinicio automático), instala Caddy como reverse
# proxy con HTTPS automático (Let's Encrypt vía sslip.io, sin dominio propio),
# genera un ADMIN_TOKEN aleatorio, y deja un resumen en /root/DEPLOY_INFO.txt.
set -euxo pipefail

REPO_URL="https://github.com/0252611-stack/nowcast-gdl.git"
APP_DIR="/opt/nowcast-gdl"
DATA_DIR="/opt/nowcast-gdl/data"
SERVICE_USER="nowcast"
# Origen del frontend en Vercel — ajustar aquí si cambia.
FRONTEND_ORIGIN="https://nowcast-gdl.vercel.app"

# --- IP pública -> hostname sslip.io (permite HTTPS real sin comprar dominio) ---
PUBLIC_IP="$(curl -s -4 https://ifconfig.me || curl -s -4 https://icanhazip.com)"
SSLIP_HOST="${PUBLIC_IP//./-}.sslip.io"

# --- Paquetes del sistema ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip git libgl1 libglib2.0-0 \
    libsm6 libxext6 curl gnupg ufw openssl debian-keyring \
    debian-archive-keyring apt-transport-https

# --- Caddy (reverse proxy + HTTPS automático) ---
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update -y
apt-get install -y caddy

# --- Usuario dedicado, sin login (aísla el proceso de la app) ---
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

# --- ADMIN_TOKEN generado automáticamente (cero input manual) ---
ADMIN_TOKEN="$(openssl rand -hex 24)"

cat > /etc/nowcast-gdl.env <<EOF
DATA_DIR=$DATA_DIR
LOG_LEVEL=INFO
ADMIN_TOKEN=$ADMIN_TOKEN
ALLOWED_ORIGINS=$FRONTEND_ORIGIN,http://localhost:5173
EOF
chmod 600 /etc/nowcast-gdl.env
chown "$SERVICE_USER":"$SERVICE_USER" /etc/nowcast-gdl.env

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

# --- Caddy: HTTPS automático vía sslip.io, solo Caddy expuesto a internet ---
cat > /etc/caddy/Caddyfile <<EOF
$SSLIP_HOST {
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl restart caddy
systemctl enable caddy

# --- Firewall del sistema operativo (además de la Security List de OCI,
#     que se abre manualmente en la consola web — ver README.md) ---
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# --- Resumen legible para el usuario (SSH: cat /root/DEPLOY_INFO.txt) ---
cat > /root/DEPLOY_INFO.txt <<EOF
==========================================================
Nowcast GDL desplegado en Oracle Cloud
==========================================================
URL del backend : https://$SSLIP_HOST
ADMIN_TOKEN      : $ADMIN_TOKEN

Siguiente paso (fuera de esta VM): en Vercel, actualizar la
variable de entorno VITE_API_URL a https://$SSLIP_HOST y
redesplegar el frontend.

Nota: si la primera visita a la URL falla, es probable que la
Security List de la VCN aún no tenga abiertos los puertos 80/443
(paso manual en la consola de Oracle, ver README.md). Caddy
reintenta el certificado solo, no hace falta reiniciar nada.

Comandos útiles (por SSH):
  sudo systemctl status nowcast-gdl
  sudo journalctl -u nowcast-gdl -f
  sudo -u nowcast git -C $APP_DIR pull && sudo systemctl restart nowcast-gdl
==========================================================
EOF
chmod 600 /root/DEPLOY_INFO.txt
