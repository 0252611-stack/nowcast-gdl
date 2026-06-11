# Historial de desarrollo — Nowcast GDL

---

## Sesión 1 — 10–11 jun 2026

### Sprint 0 — Setup y contrato de datos ✅

**Objetivo:** Definir el contrato Pydantic y hacer el scaffold completo del repo.

**Entregado:**
- `backend/app/schemas.py` — contrato congelado con 4 modelos Pydantic v2:
  - `HourlyForecast` + `PointForecast` (Open-Meteo, 12 h, viento en km/h, lista de objetos)
  - `RadarCategory` (enum) + `RadarReading` (lecturas válidas; degradación = `None` en el endpoint)
  - `NowcastResult` (preliminar, se refina en Sprint 3)
- Scaffold completo: stubs con firmas y docstrings en todos los módulos del plan
- `backend/app/config.py` con 2 puntos de ejemplo y constantes operativas
- Setup de pytest: `pytest.ini`, `conftest.py`, `requirements.txt`
- Fixtures del radar: `backend/tests/fixtures/frame1.png`, `frame1.kml`, `leyenda.png`
  descargados en vivo del API del IAM (frame real del 11-jun-2026 03:44 UTC)

**Decisiones de diseño tomadas:**
- Serie horaria como lista de objetos (no arrays paralelos)
- Velocidad de viento en km/h (default Open-Meteo, sin conversión)
- Radar "no disponible" → `RadarReading | None` + flag `radar_available` en el endpoint

**Tests:** 4/4 ✅

---

### Sprint 1 — Tres tracks en paralelo ✅

**Objetivo:** Implementar clientes de APIs + procesamiento de imagen + frontend con mocks.

**Track 1 — api-engineer:** `backend/app/sources/openmeteo.py`
- Cliente async Open-Meteo con 7 variables (precip, viento 10m y 700 hPa, temperatura)
- Cache por `(point_id, hora_truncada)` — ≤ 1 req/punto/hora, objetivo < 200 calls/día
- 3 retries con backoff exponencial via `tenacity`
- Timestamps con `ZoneInfo("America/Mexico_City")`
- Tests: 4/4 ✅

**Track 2 — radar-engineer:** `backend/app/sources/radar_iam.py` + `processing/pixel_extract.py` + `processing/colormap.py`
- `bounds_from_kml`: parsea `LatLonBox` con `xml.etree.ElementTree`
- `fetch_current_frame`: POST al IAM con fecha UTC, idempotencia por kmz_url, `RadarUnavailable`
- `latlon_to_pixel`: interpolación lineal EPSG:4326 (sin Mercator, sin pyproj), error < 2 px
- `load_colormap`: muestrea 200 píxeles de `leyenda.png`, mapeo lineal -31.5..78.0 dBZ
- Tests con fixtures reales (incluyendo test trampa medianoche UTC): 7/7 ✅

**Track 3 — frontend:** scaffold React + Vite
- Proyecto Vite + Recharts inicializado en `frontend/`
- 5 componentes: `PointCard`, `HourlyChart` (Recharts), `RadarStatus`, `WindCompass`, `HourlyChart`
- Datos mock realistas (5 puntos AMG, lluvia activa/inactiva, un punto con radar=null)
- `npm run build` ✅

**Tests totales:** 15/15 ✅

---

### Sprint 2 — Integración + scheduler ✅

**Objetivo:** Conectar todas las piezas; backend funcional + frontend con datos reales.

**Backend:**
- `backend/app/storage.py` — SQLite con tablas `radar_frames` + `point_readings`; funciones: `init_db`, `save_frame` (idempotente por kmz_url), `save_reading`, `get_latest_reading`, `get_recent_frames`, `purge_old_frames`
- `backend/app/scheduler.py` — `RadarState` (dataclass); `run_radar_loop`: loop 90 s → IAM → extrae dBZ por punto → SQLite → purge; manejo de fallos con contador; `run_forecast_loop`: precalienta cache Open-Meteo cada hora
- `backend/app/main.py` — FastAPI con lifespan (init DB + lanza schedulers); CORS para `localhost:5173`; endpoints: `GET /points`, `GET /points/{id}/forecast`, `GET /points/{id}/radar` con `{radar, radar_available, nowcast: null}`
- Fix: `USER_AGENT` sin acento (`académico` → `academico`) — los headers HTTP deben ser ASCII

**Frontend:**
- `frontend/src/api.js` — `fetchJson` helper con `AbortController` + timeout 5 s; `getPoints`, `getForecast`, `getRadar`
- `frontend/src/App.jsx` — intenta backend real al montar; fallback a mocks si falla; badge verde "En línea" / naranja "Modo offline"; botón "Reintentar"; auto-refresh cada 90 s en modo real
- `frontend/src/components/PointCard.jsx` — prop `loading` con skeleton animado
- `frontend/src/index.css` — `@keyframes pulse` y `skeleton-shimmer`
- `frontend/.env` creado con `VITE_API_URL=http://localhost:8000`

**Tests:** 28/28 ✅ | **Frontend build:** ✅

---

### Sprint 3 — Nowcasting real (advección) ✅

**Objetivo:** Motor de ETA de lluvia por optical flow + viento 700 hPa.

**Entregado:**
- `backend/app/processing/motion.py` — implementación completa:
  - `compute_cell_motion`: Farneback optical flow entre 2 frames; px→km con bounds; bearing/speed del campo
  - `nearest_upstream_echo`: búsqueda vectorizada de eco más cercano corriente arriba; filtra por dBZ ≥ 18
  - `project_cell`: ETA = distancia/velocidad; confidence = acuerdo flow vs viento 700 hPa + dirección al punto
- `backend/app/nowcast/engine.py` — `estimate_arrival` completo con 7 métodos:
  `radar_unavailable | radar_current | insufficient_frames | no_echo | no_motion | no_approaching_cell | advection`
- `backend/app/storage.py` — `get_recent_frames` ahora devuelve `list[tuple[bytes, datetime]]`
- `backend/app/scheduler.py` — `RadarState.last_bounds` guarda los bounds frescos del último frame
- `backend/app/main.py` — `/radar` conectado al engine; fallback `nowcast=None` si falla
- `backend/app/schemas.py` — `NowcastResult.method` actualizado con valores reales
- `frontend/src/api.js` — JSDoc actualizado (`NowcastResult|null`)
- `requirements.txt` — añadido `numpy>=1.26`

**Tests:** 42/42 ✅ | **Frontend build:** ✅

---

## Estado actual — Inicio de próxima sesión

**Rama:** Sprint 3 completado. Listo para Sprint 4.

**Para arrancar el stack:**
```bash
# Backend (desde backend/)
uvicorn app.main:app --reload
# Esperar ≥3 min para acumular 2 frames en SQLite y habilitar el optical flow

# Frontend (desde frontend/)
npm run dev
```

**Nota operativa:** Con 1 solo frame, el engine devuelve `method=insufficient_frames` y `nowcast.eta_minutes=null`. Tras ≥2 frames el motor calcula la advección.

### Sprint 4 parte 1 — Logging de aciertos ✅

**Objetivo:** Registrar predicciones y verificarlas contra la realidad observada.

**Entregado:**
- `backend/app/storage.py` — tabla `nowcast_predictions` + 4 funciones:
  `save_prediction`, `verify_predictions`, `get_skill_metrics`, `purge_old_predictions`
- `backend/app/scheduler.py` — emite una predicción por punto cada ciclo (90 s)
  y verifica predicciones cuyo horizonte ya expiró; purge de 7 días
- `backend/app/main.py` — `GET /metrics` → POD, FAR, CSI, accuracy (overall + forecast_only + by_method)
- `frontend/src/api.js` — `getMetrics()`
- `frontend/src/App.jsx` — componente `SkillBar` en el footer
  ("Skill: Acc X · POD X · FAR X · CSI X · n=N" o "Acumulando datos…" si n=0)
- `backend/tests/test_metrics.py` — 21 tests nuevos (hit/miss/fa/cn, lógica forecast_only,
  by_method, purge, endpoint)

**Tests:** 63/63 ✅ | **Frontend build:** ✅

**Pendiente — Sprint 4 (resto):**
- Deploy: Railway/Fly.io (backend) + Vercel (frontend)
- Calibración con lluvia real de temporada
- Fallback RainViewer

### Puntos reales AMG definidos ✅

Coordenadas verificadas en Google Maps y actualizadas en `backend/app/config.py`:

| id            | name                       | lat        | lon          |
|---------------|----------------------------|------------|--------------|
| `up_gdl`      | Universidad Panamericana   | 20.68263   | -103.44197   |
| `puerta_lomas`| Puerta Las Lomas           | 20.7054792 | -103.4363111 |
| `club_atlas`  | Club Atlas Colomos         | 20.7143976 | -103.4025069 |

`backend/tests/test_api.py` actualizado para usar `up_gdl` (reemplaza `centro`).
**Tests:** 63/63 ✅

**Nota operativa:** El IAM actualiza cada ~90 s. El scheduler debe estar corriendo para acumular frames en SQLite antes de que `motion.py` pueda calcular optical flow. Con 2 frames (~3 min de uptime) ya es suficiente para Sprint 3.

**Puntos reales del AMG** — están pendientes de definir por el usuario en `backend/app/config.py` (actualmente solo hay 2 ejemplos: Centro GDL y Zapopan).
