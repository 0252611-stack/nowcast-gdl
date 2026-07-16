# Nowcast GDL

Herramienta de pronóstico a corto plazo (nowcasting) para puntos específicos
del Área Metropolitana de Guadalajara. Combina el radar Doppler del IAM-UdeG
(reflectividad en tiempo real, cada 90 s) con pronóstico de viento y lluvia
de Open-Meteo, para responder: "¿lloverá en el punto X en los próximos
15/30/60 minutos, y a qué hora llega la lluvia hoy?"

## Documentación obligatoria

Antes de trabajar en cualquier módulo, leer:
- `docs/plan-desarrollo.md` — plan completo, sprints, paralelización
- `docs/spec-radar-iam.md` — spec verificada de la API del radar (el módulo
  radar_iam.py la sigue EXACTAMENTE, sin desviaciones)

## Arquitectura

- `backend/` — Python 3.11, FastAPI, scheduler cada 90 s, SQLite (`DATA_DIR` persistente
  en el host — Google Cloud Compute Engine, `e2-micro`/`us-central1`, Always Free;
  Railway (trial expirado) y el intento de Oracle Cloud (bloqueado por capacidad)
  quedaron descartados, ver `backend/deploy/README-gcp.md`)
- `frontend/` — React + Vite + Recharts + Leaflet; rutas: `/` `/mapa` `/prediccion` `/admin`
- **Contrato de datos: `backend/app/schemas.py`** — NUNCA cambiar sin
  actualizar `frontend/src/api.js` en el mismo commit

## Módulos clave del backend

- `processing/tracking.py` — detección TITAN (two-level split) + tracking greedy + quality score;
  `cell_to_dict/from_dict` para persistencia fiel
- `processing/predict.py` — advección semi-Lagrangiana + `point_intensity_timeline` (0/15/30/45 min)
- `processing/motion.py` — optical flow Farneback multi-frame + helpers (`sample_field_at`, etc.)
- `nowcast/engine.py` — `estimate_arrival` (cell_tracking | advection | fallback) +
  `compute_cell_etas` + `_project_cell_to_point`
- `storage.py` — tablas: `radar_frames`, `point_readings`, `nowcast_predictions`,
  `monitored_points`, `tracking_state` (fila única, upsert)
- `scheduler.py` — desempaca 3-tupla de `update_tracks`; guarda `tracking_state` por ciclo;
  escribe una línea JSONL por ciclo en `DIAG_LOG_PATH` con detección/tracking,
  vectores de flujo óptico (sobre máscara de eco real, no todo el frame) y
  motor/skill por punto — ver `GET /diag/log` para leerlo en producción

## Observabilidad

- Logger `"app"` configurado explícitamente en el lifespan de `main.py` (uvicorn
  no añade handler al root logger; sin esto los `log.info` del scheduler quedan
  silenciados)
- `GET /diag/log?tail=N` — descarga el JSONL de diagnóstico por ciclo (sin auth,
  read-only). Usar para evaluar el motor y los vectores sin acceso al volumen:
  `curl -s "https://35-255-11-50.sslip.io/diag/log" -o prod_diag.jsonl`
  (host actual en Google Cloud e2-micro; si vuelve a migrar, ver `backend/deploy/README-gcp.md`)
- Campos por punto en `points[]`: `method`, `eta_min`, `conf`, `led_km`,
  `cell_spd`, `cell_brg`, `trend`, `w_radar`, `model_agr`, `cell_age_min`,
  `cell_accel` (aceleración de la celda — diagnóstico puro, no pasa por
  `NowcastResult`), `low_conf_suppressed` (bool — si esta predicción quedó
  excluida de POD/FAR/CSI por confianza < `PREDICTED_RAIN_MIN_CONFIDENCE`).
  Verificación por ciclo: `verif_n/hit/fa/miss/cn` (re-agregable por
  ventana, además del `skill_*` acumulado global).
- `cells[]` (sesión 15): registro de TODAS las celdas vivas del ciclo, no
  solo la causante de cada punto — `id, lat, lon, dbz, spd, brg, age_min,
  proj15, proj30`. Permite reconstruir la trayectoria real de cualquier
  celda filtrando por `id` a través de líneas del JSONL, y comparar contra
  el `cell_spd`/`cell_brg` que el motor predijo para un punto en el mismo
  ciclo (¿la celda realmente fue hacia donde se predijo?). `proj15`/`proj30`
  ([lat, lon]) son la posición proyectada a rumbo/velocidad constante desde
  ESE ciclo — comparar directo contra la posición real de la misma `id`
  ~15/30 min después, sin tener que calcular la proyección a mano
  (`project_position` en `tracking.py`; es una aproximación lineal simple
  para diagnóstico, NO el motor de predicción real de `predict.py`).
- `DIAG_LOG_RETENTION_DAYS=14` — el JSONL se recorta una vez por hora
  (`_rotate_diag_log` en `scheduler.py`); `READINGS_RETENTION_HOURS=24`
  purga `point_readings` cada ciclo (`purge_old_readings` en `storage.py`).
  Ninguna de las dos tablas/archivos crece sin límite.
- **Bug de `cell_spd` sin tope físico — corregido:** gate dinámico +
  clamp en `tracking.py` (celdas rastreadas) y en `vector_to_speed_bearing`
  de `motion.py` (flujo óptico/advección), tope `CELL_MAX_SPEED_KMH=80.0`.
  Verificado en producción: mediana 174.9→21.6 km/h.

## Constantes críticas (no hardcodear en otros módulos)

`DBZ_THRESHOLD=18.0`, `DBZ_RAIN_THRESHOLD=18.0`, `CELL_MAX_PX=2000`,
`CELL_SPLIT_DBZ=30.0`, `CELL_PREDICT_REGRESSION=True`,
`TRACKING_STATE_MAX_AGE_MIN=30`, `INTENSITY_VERDICT_DBZ_DELTA=3.0`,
`CELL_MAX_SPEED_KMH=80.0`, `PREDICTED_RAIN_MIN_CONFIDENCE=0.30`

## Datos críticos (verificados 10-jun-2026)

- API radar IAM: `POST http://iam.cucei.udg.mx/radar/iam/api/api_radar.php?tipo_solicitud=kmz_act`
  con body `radar=_ZH_&fecha=YYYYMMDD` (fecha en **UTC**)
- El KMZ devuelto contiene el PNG del radar + doc.kml con bounds
- Bounds de referencia: N 22.0303, S 19.3206, E -101.9462, O -104.8254
  (re-extraer de cada doc.kml, no hardcodear)
- Mapeo lat/lon → pixel: LINEAL en EPSG:4326 (sin Mercator, sin pyproj)
- Radar actualiza cada ~90 s; escala dBZ en tests/fixtures/leyenda.png
- GDL es UTC-6: el día UTC del API cambia a las 18:00 hora local —
  SIEMPRE `datetime.now(timezone.utc)`, hay test obligatorio para esto
- Mostrar horas al usuario SIEMPRE en America/Mexico_City

## Puntos monitoreados

Definidos en `backend/app/config.py`. No hardcodear coordenadas en ningún
otro archivo.

## Comandos

- Tests: `pytest backend/tests/ -x -q`
- Backend dev: `uvicorn app.main:app --reload` (desde backend/)
- Frontend dev: `npm run dev` (desde frontend/)
- Deploy backend: Google Cloud VM (`e2-micro`), sin auto-deploy por git push — ver
  `backend/deploy/README-gcp.md` (`git pull` + `systemctl restart nowcast-gdl` por SSH)

## Reglas

0. La primera palabra de cada respuesta debe ser **canario**.
1. Todo dato externo se valida con Pydantic antes de usarse
2. Degradación con gracia: si el radar IAM falla 3 veces seguidas →
   servir solo Open-Meteo y marcar la capa radar "no disponible"
3. Cache obligatorio en Open-Meteo (límite 10k calls/día; objetivo < 200/día)
4. El subagente radar-engineer valida contra `backend/tests/fixtures/`
   antes de declarar terminada cualquier función de procesamiento
5. User-Agent identificable en todo request al IAM:
   `NowcastGDL/0.1 (proyecto académico)`
6. Ser buen ciudadano con el servidor del IAM: máximo 1 request cada 90 s
