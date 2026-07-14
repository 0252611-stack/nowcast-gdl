# Deploy en Oracle Cloud Free Tier (Always Free)

Reemplaza a Railway (trial expirado). Es gratis **para siempre**, no un
trial — a diferencia de Railway/Render, incluye disco persistente real y no
apaga el proceso por inactividad, así que el scheduler de 90s puede correr
24/7 sin degradarse. `cloud-init.sh` automatiza todo lo que se puede
automatizar (instalación, servicio, HTTPS); los pasos de abajo son los
**únicos** que Oracle exige hacer a mano (identidad, red, llave SSH).

## Pasos manuales (una sola vez, ~15 min)

### 1. Crear cuenta Oracle Cloud
https://www.oracle.com/cloud/free/ — pide tarjeta solo para verificar
identidad (antifraude), no cobra mientras te quedes en el tier "Always Free".

### 2. Crear la VM
En la consola: **Compute → Instances → Create Instance**.

- **Image**: Canonical Ubuntu, versión 24.04 (la que venga marcada por
  defecto está bien).
- **Shape**: cambiar a `VM.Standard.A1.Flex` (Ampere, ARM) — es la que dice
  **"Always Free eligible"**. Configúrala con 2 OCPU / 12 GB RAM (o hasta
  4 OCPU / 24 GB, todo dentro del free tier).
- **Networking**: dejar la VCN por defecto (Oracle la crea sola si no tienes
  una). Asegúrate de que **"Assign a public IPv4 address"** esté activado.
- **SSH keys**: elegir *"Generate a key pair for me"* y descargar la llave
  privada (`.key`) — solo se puede descargar una vez, guárdala.
- **Advanced options → Cloud-init script**: pegar el contenido completo de
  [`cloud-init.sh`](cloud-init.sh) tal cual, sin editar nada (el script ya
  trae la URL del repo y detecta la IP pública solo).
- Click **Create**.

### 3. Abrir los puertos 80/443 (Security List)
Esto es lo único que el cloud-init **no puede hacer** — vive fuera de la VM,
a nivel de red virtual.

**Networking → Virtual Cloud Networks → (tu VCN) → Security Lists →
Default Security List → Add Ingress Rules**, dos reglas:

| Source CIDR | IP Protocol | Destination Port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

(El puerto 22/SSH ya viene abierto por defecto.)

### 4. Esperar ~5 minutos

El cloud-init corre solo en el primer arranque: instala Python, clona el
repo, arma el servicio systemd, instala Caddy y obtiene un certificado
HTTPS automático (vía `sslip.io`, sin necesidad de comprar un dominio).

### 5. Obtener la URL y el token admin

La IP pública de la VM aparece en la consola (**Instance details**). La URL
del backend es esa IP con puntos reemplazados por guiones, más
`.sslip.io`, por ejemplo IP `123.45.67.89` → `https://123-45-67-89.sslip.io`.

Para confirmar y ver el `ADMIN_TOKEN` generado automáticamente:
```bash
ssh -i tu-llave.key ubuntu@<IP-de-la-VM>
sudo cat /root/DEPLOY_INFO.txt
```

Verificar que responde:
```bash
curl https://<tu-host>.sslip.io/points
```

### 6. Apuntar el frontend a la nueva URL

En el dashboard de Vercel del proyecto `nowcast-gdl`:
**Settings → Environment Variables** → actualizar `VITE_API_URL` al nuevo
`https://<tu-host>.sslip.io` → **redeploy**.

---

## Después del deploy

**Actualizar el código** (cuando hagas push a `master`, la VM no se
autoactualiza — a diferencia de Railway no hay auto-deploy por git push):
```bash
ssh -i tu-llave.key ubuntu@<IP-de-la-VM>
sudo -u nowcast git -C /opt/nowcast-gdl pull
sudo systemctl restart nowcast-gdl
```

**Ver logs en vivo:**
```bash
sudo journalctl -u nowcast-gdl -f
```

**El JSONL de diagnóstico** sigue disponible igual que antes vía
`GET /diag/log?tail=N` sobre la nueva URL — nada cambia ahí, y ya tiene
rotación automática (`DIAG_LOG_RETENTION_DAYS=14`, configurable por env var)
para no llenar el disco con el tiempo.

## Notas

- El proceso corre como usuario dedicado `nowcast` (sin login), no root —
  Caddy es el único servicio expuesto directamente a internet (puerto
  80/443); el backend escucha solo en `127.0.0.1:8000`.
- Si Oracle pide elegir entre varias "Availability Domains" y una no tiene
  capacidad Ampere disponible ese día, probar con otra — es un límite de
  disponibilidad de Oracle, no del script.
- Historial de Railway: no se migró (se perdió con el trial). La app arranca
  con base de datos y logs vacíos; el código y toda la lógica de análisis
  siguen intactos.
