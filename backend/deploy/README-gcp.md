# Deploy en Google Cloud (Compute Engine, e2-micro) — Always Free

Host activo en producción desde el 15-jul-2026 (Sesión 14). Oracle Cloud
Free Tier quedó bloqueado por escasez de capacidad en ambos shapes Always
Free (`A1.Flex` y `E2.1.Micro`) en la única región disponible para la cuenta
de prueba (Querétaro) — ver Sesión 13/14 en `HISTORIAL.md`. `README.md` /
`cloud-init.sh` quedan como referencia si Oracle se retoma más adelante.

`e2-micro` en `us-central1`/`us-west1`/`us-east1` es gratis **para siempre**
(no un trial), con 30GB de disco persistente **estándar** (no "balanced" —
ese sí tiene costo) y trae registro para SSH por navegador sin manejar
llaves.

## Pasos manuales (cuenta + VM)

1. **Crear cuenta**: https://cloud.google.com/free — pide tarjeta para
   verificación de identidad, no cobra dentro del trial de $300 USD / Always
   Free.
2. **Compute Engine → Instancias de VM → Crear instancia**:
   - Región: `us-central1` (o `us-west1`/`us-east1`) — son las únicas
     elegibles para Always Free.
   - Tipo de máquina: `e2-micro`.
   - SO y almacenamiento → Cambiar: **Disco persistente estándar**, 30GB
     (el tipo "balanceado" por defecto SÍ cobra; el estándar es el cubierto
     por el free tier).
   - Firewall: marcar **Permitir tráfico HTTP** y **Permitir tráfico
     HTTPS**.
   - Avanzado → Automatización → Secuencia de comandos de inicio: pegar
     [`gcp-startup-script.sh`](gcp-startup-script.sh) tal cual.
   - Crear.
3. Si sale **"currently unavailable in the zone"** (error de capacidad,
   no de configuración): "Editar la configuración y vuelve a intentar" →
   cambiar la Zona (ej. `us-central1-c` → `us-central1-a`) y reintentar. A
   diferencia de Oracle, GCE tiene varias zonas por región, así que esto
   casi siempre resuelve el problema en el segundo o tercer intento.

## Gotcha real que costó ~20 min en el primer despliegue

El primer arranque de una imagen Debian corre `apt-daily`/
`unattended-upgrades` en segundo plano, que puede tener el lock de dpkg
tomado justo cuando el `startup-script` intenta su propio `apt-get install`.
Con `set -euxo pipefail` eso mata el script al segundo intento — sin
instalar Python, Caddy, ni nada más — y **no hay ningún error visible en la
consola web**, solo un backend que nunca responde. Diagnóstico: revisar
`Puerto en serie 1 (consola)` en el detalle de la instancia y buscar
`E: Could not get lock /var/lib/dpkg/lock-frontend`.

`gcp-startup-script.sh` ya trae el fix (`apt_retry()`, reintenta cada
`apt-get` hasta 30 veces con 5s de espera en vez de morir). Si algún día se
vuelve a editar el script, mantener ese wrapper.

## Verificar y reintentar tras un fallo

Si el `startup-script` falla por cualquier motivo, no hace falta recrear la
VM:
1. **Editar instancia** (VM detenida o corriendo) → Avanzado → pegar el
   script corregido en el mismo campo → Guardar.
2. **Restablecer** (reinicio forzado) — GCE re-ejecuta el `startup-script`
   completo en cada arranque (a diferencia de Oracle, que solo lo corre una
   vez). Es seguro si el script nunca llegó a escribir datos importantes.

## Obtener la URL y confirmar

La IP externa aparece en **Instancias de VM**. La URL del backend es esa IP
con puntos reemplazados por guiones, más `.sslip.io` (ej. `35.255.11.50` →
`https://35-255-11-50.sslip.io`).

```bash
curl https://<tu-host>.sslip.io/points
```

Token admin y detalles: `sudo cat /root/DEPLOY_INFO.txt` por SSH (botón
"SSH" en la consola, abre una terminal en el navegador sin necesidad de
llaves).

## Apuntar el frontend

Vercel → proyecto `nowcast-gdl` → Settings → Environment Variables →
`VITE_API_URL` = `https://<tu-host>.sslip.io` → **Redeploy** (Production).

## Después del deploy

```bash
# Por SSH (botón "SSH" en la consola de GCE)
sudo -u nowcast git -C /opt/nowcast-gdl pull && sudo systemctl restart nowcast-gdl
sudo journalctl -u nowcast-gdl -f
```

Igual que en Oracle: no hay auto-deploy por git push, hay que actualizar a
mano. El JSONL de diagnóstico sigue disponible vía `GET /diag/log?tail=N`
sin cambios, con su rotación automática existente.

## Subida semanal a GCS + BigQuery (sesión 16)

`backend/deploy/weekly_log_upload.py` sube el JSONL de diagnóstico cada
semana: una copia comprimida a Cloud Storage (respaldo crudo) y una carga
a BigQuery (consulta directa por SQL, sin descargar nada). No modifica el
JSONL original — es de solo lectura sobre el archivo, la retención de 180
días + tope de 2GB en el disco sigue funcionando exactamente igual.

**Recursos creados** (proyecto `genial-post-502521-m2`, región `us-central1`):
- Bucket GCS: `gs://nowcast-gdl-diag-logs-genial-post-502521-m2`
- Dataset BigQuery: `nowcast_diag` (tabla `cycles`, se crea sola en la
  primera carga vía `autodetect`)
- Cuenta de servicio dedicada: `nowcast-log-uploader@genial-post-502521-m2.iam.gserviceaccount.com`
  — permisos mínimos: `roles/storage.objectCreator` (solo en ese bucket),
  `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` (proyecto). Su
  llave JSON vive en la VM en `/etc/nowcast-gdl/gcs-key.json` (600,
  `nowcast:nowcast`) — **no está en git**, si hay que recrearla:
  ```bash
  gcloud iam service-accounts keys create /tmp/key.json \
    --iam-account=nowcast-log-uploader@genial-post-502521-m2.iam.gserviceaccount.com
  gcloud compute scp /tmp/key.json nowcast-gdl:/tmp/key.json \
    --zone=us-central1-a --tunnel-through-iap
  # luego por SSH: sudo mv /tmp/key.json /etc/nowcast-gdl/gcs-key.json && \
  #   sudo chown nowcast:nowcast /etc/nowcast-gdl/gcs-key.json && \
  #   sudo chmod 600 /etc/nowcast-gdl/gcs-key.json
  ```

**Dependencias** (en el venv de la app, no en el sistema — evita instalar
el SDK completo de gcloud en un e2-micro de 1GB RAM):
```bash
sudo -u nowcast /opt/nowcast-gdl/venv/bin/pip install google-cloud-storage google-cloud-bigquery
```

**Cron** (`/etc/cron.d/nowcast-log-upload`, corre como `nowcast`, lunes 03:00 UTC ≈ domingo 21:00 GDL):
```
0 3 * * 1 nowcast /opt/nowcast-gdl/venv/bin/python3 /opt/nowcast-gdl/backend/deploy/weekly_log_upload.py >> /opt/nowcast-gdl/data/logs/weekly_upload.log 2>&1
```

**Consultar los datos** — directo en BigQuery, sin descargar nada:
```sql
SELECT frame_time, n_det, cell.id, cell.lat, cell.lon, cell.proj15
FROM `genial-post-502521-m2.nowcast_diag.cycles`,
     UNNEST(cells) AS cell
WHERE cell.id = 107
ORDER BY frame_time
```

**Correr manualmente / probar:**
```bash
sudo -u nowcast /opt/nowcast-gdl/venv/bin/python3 /opt/nowcast-gdl/backend/deploy/weekly_log_upload.py
```
