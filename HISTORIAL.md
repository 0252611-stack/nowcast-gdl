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
- ~~Deploy: Railway/Fly.io (backend) + Vercel (frontend)~~ ✅ (ver abajo)
- ~~Fallback RainViewer~~ ✅ (ver abajo)
- Calibración con lluvia real de temporada

### Deploy completo ✅ — 11 jun 2026

**Backend — Railway:**
- URL: `https://nowcast-gdl-production.up.railway.app`
- Builder: nixpacks; Root directory: `/backend`
- Health check: `GET /points` → 200 OK
- Variables: `ALLOWED_ORIGINS=https://nowcast-gdl.vercel.app,http://localhost:5173`

**Frontend — Vercel:**
- URL: `https://nowcast-gdl.vercel.app`
- Root directory: `frontend`; Framework: Vite (auto-detectado)
- Variable: `VITE_API_URL=https://nowcast-gdl-production.up.railway.app`
- Status: Ready (muestra "En línea — datos reales" al cargar)

### Fallback RainViewer ✅ — 11 jun 2026

**Objetivo:** Mostrar radar visual cuando el IAM falla en lugar de solo "no disponible".

**Entregado:**
- `backend/app/sources/rainviewer.py` — `fetch_tile_url(client, lat, lon, zoom=7)`:
  llama a `api.rainviewer.com/public/weather-maps.json`, convierte lat/lon a tile
  Web Mercator, devuelve URL del PNG más reciente (color 4 = esquema meteorológico)
- `backend/app/main.py` — endpoint `/radar` incluye `rainviewer_url` cuando
  `radar_available=False`; URL cacheada 5 min en `app.state` para no sobrecargar la API
- `frontend/src/components/RadarStatus.jsx` — cuando IAM no disponible y
  `rainviewerUrl` es truthy, muestra un thumbnail 80×80 del tile regional
  (enlazado al PNG; se oculta con `onError` si falla la carga)
- `frontend/src/components/PointCard.jsx` + `App.jsx` — propagan el prop
  `rainviewerUrl` desde la respuesta del endpoint
- `backend/tests/test_rainviewer.py` — 9 tests nuevos

**Tests:** 72/72 ✅

---

### Fix: temperatura mostraba datos de medianoche ✅ — 11 jun 2026

**Síntoma:** La tarjeta mostraba ~17°C a las 2 PM (temperaturas de madrugada).

**Causa raíz (2 problemas combinados):**
1. `openmeteo.py` usaba `forecast_days=1` → Open-Meteo entrega desde medianoche;
   con `_MAX_HOURS=12` solo se tomaban las horas 00:00–11:00.
2. `PointCard.jsx` siempre usaba `hourly[0]` (hora 0 = medianoche) en lugar
   de buscar la entrada más cercana a la hora actual.

**Fix:**
- `backend/app/sources/openmeteo.py`: cambiar a `forecast_hours=12` → Open-Meteo
  entrega las próximas 12 horas desde la hora actual.
- `frontend/src/components/PointCard.jsx`: buscar la última entrada con
  `time <= Date.now()` en vez de usar índice 0 fijo.

**Resultado:** 25–26°C a las 2:30 PM, "En línea — datos reales" ✅

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

---

### Sprint 5 — Mapa interactivo, nube causante y panel admin ✅ — 12 jun 2026

**Objetivo:** Agregar contexto espacial (mapas Leaflet), visualización de la nube causante cuando hay ETA activa, y panel de administración con historial y CRUD de puntos.

**Entregado:**

**Backend:**
- `backend/app/schemas.py` — `NowcastResult` ahora expone 3 campos nullable del eco causante:
  `cell_lat`, `cell_lon`, `bearing_cell_to_point_deg` (solo se llenan en método `advection`)
- `backend/app/nowcast/engine.py` — rama `advection` forwardea `cell_lat/cell_lon/bearing_cell_to_point_deg`
  desde `nearest_upstream_echo` y `project_cell` (los datos ya existían, solo faltaba exponerlos)
- `backend/app/storage.py` — tabla `monitored_points` + funciones:
  `seed_points` (idempotente, siembra `config.POINTS` en DB vacía),
  `list_points`, `add_point`, `update_point`, `delete_point`, `get_predictions`
- `backend/app/config.py` — `ADMIN_TOKEN` desde env var (fail-closed si no configurado)
- `backend/app/main.py` — refactor completo:
  - Puntos dinámicos: `list_points(app.state.db)` por request (no más `_POINTS_BY_ID` estático)
  - `seed_points` en lifespan tras `init_db`
  - `GET /predictions` — historial de filas individuales de `nowcast_predictions`
  - `POST /points`, `PUT /points/{id}`, `DELETE /points/{id}` — escritura protegida por `require_admin`
  - CORS ampliado a `["GET","POST","PUT","DELETE","OPTIONS"]`
- `backend/app/scheduler.py` — `run_radar_loop` y `run_forecast_loop` usan `list_points(conn)`
  (refleja altas/bajas de puntos en caliente, sin reiniciar el servidor)

**Frontend:**
- Dependencias: `react-router-dom`, `leaflet`, `react-leaflet@4`
- `frontend/src/main.jsx` — envuelto en `<BrowserRouter>`
- `frontend/src/api.js` — JSDoc actualizado (`NowcastResult` con 3 campos del eco);
  funciones nuevas: `getPredictions`, `createPoint`, `updatePoint`, `deletePoint`
- `frontend/src/components/CellMap.jsx` (nuevo) — mapa Leaflet reutilizable:
  OSM + capa RainViewer (deriva plantilla `{z}/{x}/{y}` desde URL de tile);
  marcadores de puntos, marcador del eco causante (círculo naranja), flecha SVG rotada por
  `bearing_cell_to_point_deg`, polyline eco→punto; modo compacto (mini-mapa)
- `frontend/src/views/MapView.jsx` (nuevo) — `/mapa`: mapa grande con todos los puntos,
  ecos y flechas; leyenda; auto-carga al montar
- `frontend/src/views/AdminView.jsx` (nuevo) — `/admin`:
  - Token admin en sessionStorage (no en código)
  - CRUD de puntos con formulario inline
  - Tabla de historial de predicciones (filtrable por punto_id)
- `frontend/src/App.jsx` — react-router con rutas `/`, `/mapa`, `/admin`;
  nav links en el header; mini-mapa de nube causante en `PointCard` cuando hay `cell_lat`
- `frontend/src/components/PointCard.jsx` — mini-`CellMap` compacto bajo el badge de ETA
  cuando `nowcast.cell_lat != null`
- `frontend/vercel.json` — rewrite SPA para react-router (evita 404 al recargar `/mapa`)
- `backend/tests/test_points_crud.py` (nuevo) — 23 tests: seed idempotente,
  CRUD de puntos, `get_predictions`, campos del eco en `NowcastResult`,
  auth admin (401/503/201), endpoint `/predictions`

**Fixes post-implementación:**
- `backend/app/main.py` — CORS: añadido `allow_origin_regex=r"http://localhost:\d+"` para
  cubrir cualquier puerto local (Vite usa 5173/5174/5175 según disponibilidad)
- `frontend/src/api.js` — timeout `fetchJson` aumentado de 5 s → 12 s (el endpoint `/radar`
  encadena Open-Meteo + motor de advección y tarda ~7 s en primera carga)

**Tests:** 95/95 ✅ | **Lint:** ✅ | **Frontend build:** ✅

**Verificado en navegador:**
- `/` — tarjetas AMG con datos reales, mini-mapa de nube causante cuando hay ETA
- `/mapa` — mapa Leaflet grande con 3 marcadores, capas RainViewer, flechas de trayectoria
- `/admin` — tabla de 3 puntos, historial de predicciones en tiempo real, token en sessionStorage

**Nota de deploy:**
- Railway: agregar variable `ADMIN_TOKEN=<secreto>` para habilitar escritura desde el panel admin
- El rewrite de Vercel ya está en `vercel.json`; se aplica en el siguiente deploy

**Pendiente:**
- Calibración con lluvia real de temporada

---

### Rediseño UI — Tema claro + etiquetas de fuente + anti-slop ✅ — 12 jun 2026

**Objetivo:** Migrar de tema oscuro a tema claro profesional, mostrar la fuente
de cada dato, y eliminar patrones de "AI slop" identificados en investigación.

**Entregado:**

**Infraestructura de diseño:**
- `frontend/src/theme.js` (nuevo) — tokens semánticos centralizados:
  fondo `#FAFAFA`, tarjetas `#FFFFFF`, primario `#1E40AF`, acento `#D97706`;
  un solo lugar con hex, todos los componentes importan de aquí
- `frontend/src/index.css` — variables CSS actualizadas a la paleta clara;
  `color-scheme: light`; tipografía base Fira Sans + Fira Code
- `frontend/index.html` — Google Fonts: Fira Sans (300–700) + Fira Code (400–600)
  con `preconnect` y `display=swap`

**Etiquetas de fuente:**
- `frontend/src/components/SourceTag.jsx` (nuevo) — caption "● Open-Meteo",
  "● Radar IAM-UdeG", "● Motor Nowcast", "● RainViewer" bajo cada dato;
  punto de color + texto (no solo color, cumple `color-not-only`)

**Iconos SVG:**
- `frontend/src/components/Icons.jsx` (nuevo) — 7 íconos stroke-based
  (SunIcon, CloudRainIcon, ClockIcon, DropletIcon, ThermometerIcon, WindIcon,
  CloudIcon) en viewBox 24×24, mismo estilo que WindCompass

**Componentes refactorizados (paleta clara + mejoras):**
- `App.jsx` — chips de filtro con punto de color en vez de emoji 🌧/☀️
- `PointCard.jsx` — sin ALL-CAPS labels; badges con border-radius cuadrado (8px)
  en vez de pill; emojis → SVG; borde superior de color por estado de lluvia
  (verde = lluvia activa, ámbar = ETA próxima — jerarquía funcional, no decorativa)
- `RadarStatus.jsx` — CATEGORY_STYLES recalibrados para ≥4.5:1 sobre blanco
- `WindCompass.jsx` — SVG a tema claro (flecha en `primary`, círculo en `surfaceMuted`)
- `HourlyChart.jsx` — Recharts: grid claro, tooltip con SVG inline en vez de emoji
- `MapView.jsx` + `AdminView.jsx` — paleta clara, th sin ALL-CAPS
- `CellMap.jsx` — colores de marcadores/flechas/polyline via `theme`

**Investigación anti-slop:**
Análisis de ~1,600 páginas (Adrian Krebs) identificó 16 patrones de "AI slop".
Score inicial: 2–3 patrones. Cambios aplicados:
1. ALL-CAPS labels eliminados (patrón #16 — el más citado)
2. Emojis funcionales → SVG consistentes (patrón de iconografía genérica)
3. Grid de cards idénticas → jerarquía por borde superior de estado

**Lint:** ✅ | **Build:** ✅ | **Deploy:** Vercel (push directo)

---

## Sesión 2 — 12–13 jun 2026

### Fix: umbral de lluvia y separación de constantes ✅ — 12 jun 2026

**Síntoma:** Puntos con lluvia confirmada en campo mostraban "Sin lluvia" (dBZ leído
como -8.4 en lugar del valor real ≥18 dBZ).

**Causa raíz (descubierta en dos pasos):**
1. *Workaround:* `DBZ_THRESHOLD = 18.0` excluía toda la categoría "Débil" (-10 a 18 dBZ).
   Se separó en dos constantes: `DBZ_THRESHOLD = 18.0` (tracking / optical flow) y
   `DBZ_RAIN_THRESHOLD = -10.0` (frontera Ruido/Débil del IAM — cualquier eco real = lluvia).
2. *Causa raíz real:* Bug de calibración del colormap (ver siguiente entrada).

**Frontend:**
- `PointCard.jsx` — tercer estado "Eco débil" (badge ámbar + CloudIcon) cuando
  `!raining_now && radar.category === "Débil"`; distingue eco débil presente de
  cielo totalmente despejado.

**Tests:** 95/95 ✅

---

### Fix crítico: calibración del colormap — interpolación por tramos ✅ — 13 jun 2026

**Síntoma:** Lluvia fuerte en campo reportada como "lluvia débil". Lecturas de dBZ
consistentemente 15–30 dBZ por debajo del valor real (verde brillante = Ligera = 18 dBZ
se leía como -9.5 dBZ).

**Causa raíz:**
La función `load_colormap` asumía que los 16 tick-marks de la leyenda IAM estaban
distribuidos **uniformemente en dBZ** a lo largo de los 399 px de la imagen. En realidad:
- Los ticks SÍ están igualmente espaciados en píxeles (~26.5 px c/u)
- Los valores dBZ NO son uniformes: primeros dos saltos son 21.5 y 23 dBZ; el resto = 5 dBZ
- El mapeo lineal resultante introducía errores de hasta **30 dBZ**

| x pixel (leyenda) | dBZ correcto | dBZ anterior | Error |
|---|---|---|---|
| x=27 (teal) | -10.0 | -24.1 | −14 dBZ |
| x=53 (lima) | 13.0 | -16.9 | −30 dBZ |
| x=80 (verde brillante) | 18.0 | -9.5 | −27.5 dBZ |
| x=159 (amarillo) | 33.0 | 12.5 | −20.5 dBZ |
| x=212 (naranja) | 43.0 | 26.7 | −16.3 dBZ |

**Fix:** Reemplazar la asignación lineal en `load_colormap` por **interpolación piecewise**
entre los 16 ticks. La posición fraccional `t = (x/width-1) * (n_ticks-1)` ubica cada
píxel en el intervalo correcto y aplica el paso dBZ real de ese intervalo.

**Validación post-fix:**
- Débil/Ligera boundary (verde brillante): 18.1 dBZ (error +0.1) ✅
- Moderada (naranja, 43 dBZ): 42.9 dBZ ✅
- Lluvia fuerte (rojo, 48 dBZ): 50.5 dBZ ✅

**Archivos:** `backend/app/processing/colormap.py`
**Tests:** 95/95 ✅

---

### Feat: flechas de dirección en ecos lejanos ✅ — 13 jun 2026

**Síntoma:** Las flechas de dirección del campo solo aparecían cerca de GDL; los ecos
lejanos (Tototlán, Zapotlán del Rey) no mostraban ninguna flecha.

**Causas y fixes:**

1. **Slots insuficientes:** `selectArrowPositions` elegía solo 4 posiciones; los 4 slots
   se llenaban con ecos de GDL (más fuertes). Fix: aumentar a `n=10, minDistKm=25`.

2. **Bloqueo por `hasMotion`:** Las flechas no aparecían cuando el optical flow devolvía
   `speed_kmh = 0` (ecos estacionarios o 1 solo frame disponible). Fix: usar el
   **viento 700 hPa** del primer nowcast disponible como dirección fallback cuando
   `speed_kmh ≤ 1`. Se muestra `hasDirection = flow_ok || wind_fallback_available`.

3. **Tooltip diferenciado:** "Campo: X° · Y km/h" (optical flow) vs
   "Viento 700 hPa: X°" (fallback) según la fuente usada para cada flecha.

**Archivos:** `frontend/src/components/CellMap.jsx`
**Lint:** ✅ | **Build:** ✅

---

### Estado actual — inicio de próxima sesión

**Commits en esta sesión:** 5 (todos pusheados a `master` → Railway auto-deploy)

**Stack completo:**
- Backend Railway: `https://nowcast-gdl-production.up.railway.app`
- Frontend Vercel: `https://nowcast-gdl.vercel.app`

**Pendiente:**
- Calibración fina con lluvia real de temporada (ahora el colormap está corregido,
  se puede medir la precisión real)
- El badge "Eco débil" quedó en el código pero con el colormap fijo los ecos reales
  leen ≥10 dBZ → raining_now=True; puede revisarse si aplica removerlo
- Verificar en campo la precisión de los ETA post-calibración
