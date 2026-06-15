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

---

## Sesión 3 — 13 jun 2026

### Feat: contorno causante en naranja + flechas de movimiento interior ✅ — 13 jun 2026

**Objetivo:** Reemplazar el marcador circular naranja del eco causante por feedback
más rico: el polígono contorno se dibuja en naranja/grueso, y flechas de movimiento
de campo se muestran dentro de TODOS los ecos.

**Entregado — `frontend/src/components/CellMap.jsx`:**
- Eliminado el `CircleMarker` naranja del eco causante.
- Render de `echoContours` refactorizado con `Fragment`: por cada contorno se detecta
  si es causante (`pointInPolygon` sobre posiciones de causante), se dibuja el
  `Polygon` con `color: theme.orange, weight: 3` si es causante vs `color: theme.text, weight: 1.5`
  para los demás.
- Flechas de campo (`echoArrowPositions`) colocadas dentro de TODOS los contornos,
  no solo los más fuertes. El tooltip del eco causante se migró al `Marker` de la flecha.
- `import { useEffect, Fragment }` — `CircleMarker` eliminado de las importaciones.

**Entregado — `frontend/src/views/MapView.jsx`:**
- Leyenda actualizada: "Eco causante" pasa de "punto naranja" a "Contorno del eco causante"
  (línea naranja de 2px).

**Lint:** ✅ | **Build:** ✅ | **Deploy:** pusheado a `master` → Railway/Vercel

---

### Feat: pestaña Predicción — nowcast de campo con animación temporal ✅ — 13 jun 2026

**Objetivo:** Nueva pestaña `/prediccion` que muestra cómo se moverán todos los
ecos en las próximas 2 horas, con slider temporal de reproducción.

**Motor:** Flujo óptico denso (Farneback) como motor principal + corrección viento
700 hPa en malla 4×4. Horizonte: 120 min en pasos de 15 (8 frames). Cómputo bajo
demanda, cacheado por timestamp del frame base.

#### Backend

**`backend/app/processing/motion.py`:**
- Nueva función `dense_motion_field(frame_older, frame_newer, interval_seconds, bounds) -> np.ndarray | None`
  que devuelve H×W×2 float32 (v_lat, v_lon) en grados/min. None si <_MIN_ECHO_PIXELS.
- `compute_cell_motion` refactorizado para llamar a `dense_motion_field` y promediar;
  API pública idéntica, sin romper tests existentes.

**`backend/app/sources/openmeteo.py`:**
- Nueva `async def sample_wind_grid(client, bounds, nx=4, ny=4) -> list[dict]`
  que muestrea 16 puntos sobre el área del radar usando `fetch_wind_700_at` (ya cacheada).
  Devuelve `[{lat, lon, toward_deg, speed_kmh}]`.

**NUEVO `backend/app/processing/predict.py`:**
- `_wind_grid_to_field(wind_grid, H, W, bounds)` — IDW interpolation pura numpy (sin scipy).
- `blend_motion_field(radar_field, echo_alpha, wind_grid, bounds)` — combina campo radar
  (ponderado por alpha Gaussian del eco) con campo viento (donde no hay eco).
- `advect_image(rgba, motion_field, minutes, bounds) -> PIL.Image` — advección
  semi-Lagrangiana hacia atrás con `cv2.remap`; fondo transparente.
- `build_prediction(frame_older, frame_newer, interval_seconds, bounds, wind_grid, steps_min) -> dict`
  — orquesta pipeline completo; genera contornos por frame y polilíneas de trayectoria.

**`backend/app/main.py`:**
- `app.state.prediction_cache: tuple | None = None` inicializado en lifespan.
- `GET /prediction` → `{available, base_time, bounds, method, steps[{minutes, image_url, contours}], trajectories}`.
  Cachea resultado por `newer_time` del frame base (~90 s).
- `GET /prediction/frame/{idx}.png` → sirve PNG del cache (404 si fuera de rango).

**NUEVO `backend/tests/test_predict.py`:**
- 9 tests nuevos: shape del campo denso, advección preserva tamaño, frame idéntico con
  `minutes=0`, blend sin viento ≈ campo radar, `build_prediction` produce N pasos, etc.
- **17 tests totales en este módulo** (todos verdes).

#### Frontend

**`frontend/src/api.js`:**
- Añadido `getPrediction() -> Promise<PredictionResult>` con typedef completo
  (`available`, `base_time`, `bounds`, `steps[{minutes, image_url, contours}]`, `trajectories`).

**`frontend/src/components/CellMap.jsx`:**
- Nueva prop `trajectories = []` — renderiza cada polilínea como `<Polyline>`
  con `dashArray="4 6"`, color `theme.primary`, opacidad 0.6.

**NUEVO `frontend/src/components/TimeSlider.jsx`:**
- `<input type="range" min=0 max={steps.length}>` + botón play/pause (`setInterval` 750 ms).
- Respeta `prefers-reduced-motion` (no auto-play si reducción de movimiento activa).
- Etiqueta: "Ahora" (step 0) o "+{min} min · HH:MM" en `America/Mexico_City`.
- Opacidad de etiqueta decrece de 1.0 a 0.3 (incertidumbre creciente).
- ESLint fix: apóstrofe en JSX → `&apos;`.

**NUEVO `frontend/src/views/PredictionView.jsx`:**
- Carga `getPoints()` + `getRadar(pts[0].id)` (contornos actuales) + `getPrediction()` en paralelo.
- Estado `step` (0=ahora, 1-8=frames predichos).
- `radarImageUrl`: `/radar/image` (step 0) o `/prediction/frame/{step-1}.png` (steps 1-8).
- `echoContours`: actuales (step 0) o `steps[step-1].contours` (steps 1-8).
- `trajectories`: visible solo en step 0 (polilíneas de t=0 a +120 min).
- Aviso de incertidumbre cuando step > 2: "La precisión disminuye después de ~30 min".
- Badge "Adv. semi-Lagrangiana" o "Persistencia" cuando disponible.
- Mensaje claro cuando radar no disponible.

**`frontend/src/App.jsx`:**
- `import PredictionView` + `<NavLink to="/prediccion">Predicción</NavLink>`.
- `<Route path="/prediccion" element={<PredictionView />} />`.

**Sin dependencias nuevas:** `cv2.remap`, `numpy`, `PIL` ya estaban en `requirements.txt`.

**Limitación honesta comunicada en UI:** la extrapolación Lagrangiana es confiable
~20-30 min; más allá no modela formación/disipación de ecos. El aviso en pantalla
y la opacidad decreciente del TimeSlider comunican esto visualmente.

**Nota operativa:** Railway reinicia con estado vacío. La primera llamada a
`/prediction` devuelve `available: false, method: "insufficient_frames"` hasta
acumular ≥2 frames (~3 min de uptime). Comportamiento correcto, no un bug.

**Tests:** 17/17 nuevos ✅ (95+17 = 112 totales) | **Lint:** ✅ | **Build:** ✅
**Verificado en navegador** (`https://nowcast-gdl.vercel.app/prediccion`):
- Badge "Adv. semi-Lagrangiana", mapa con ecos reales, slider funcional play/pause.
- Animación avanza del frame actual a +120 min; contornos y overlay se desplazan.
- Aviso de incertidumbre visible en steps > 2.

**Deploy:** pusheado a `master` → Railway/Vercel auto-deploy.

---

### Estado actual — inicio de próxima sesión

**Stack completo:**
- Backend Railway: `https://nowcast-gdl-production.up.railway.app`
- Frontend Vercel: `https://nowcast-gdl.vercel.app`

**Pendiente:**
- Calibración fina con lluvia real de temporada
- Verificar en campo la precisión de los ETA post-calibración del colormap

---

## Sesión 4 — 13 jun 2026 — Mejoras de predicción en 3 fases

Plan aprobado por el usuario: UX (más flechas, pasos 5 min, slider auto-play+arrastre,
panel de variabilidad) + motor A–E + blend NWP real + Doppler `_VR_`. Se ejecuta en
**3 fases independientes y testeables**. Este bloque se va actualizando con cada avance
por si la sesión se cierra (el usuario pidie llevar registro incremental aquí).

**Diagnóstico de variabilidad de la ETA:** la ETA colapsaba el campo denso a un vector
global, el flujo se calculaba solo entre 2 frames, y `nearest_upstream_echo` /
`find_context_echoes` usaban `np.random.choice` → **no determinista** (misma imagen,
distinta ETA cada ciclo). Solución: determinismo + EMA + vector por celda + multi-frame.

### Fase 1 — UX + estabilidad + motor A–E (pragmático)

Estado: **COMPLETADA** ✅ (13-jun-2026)

Checklist:
- [x] 1.1 `motion.py`: determinismo (`np.linspace` stride), `multi_frame_motion_field`,
      `field_to_global_vector`, `sample_field_at`, `vector_to_speed_bearing`
- [x] 1.2 `engine.py`: param `motion_field`, vector por celda (B), growth/decay (D),
      blend NWP confianza (E), `intensity_trend`, `model_agreement`
- [x] 1.3 `schemas.py` + `api.js`: `intensity_trend`, `model_agreement` (MISMO COMMIT)
- [x] 1.4 `scheduler.py`: `motion_field_ema`, `last_eta`, EMA del campo, logs `ETA[...]Δ...`
- [x] 1.5 `main.py`: usa EMA en `/radar`, endpoint `GET /eta-stability`, docstring 24 pasos
- [x] 1.6 `predict.py`: `_DEFAULT_STEPS_MIN=range(5,121,5)` (24 pasos), `frames_recent`,
      decay alpha por paso cuando `intensity_trend < 0`
- [x] 1.7 `storage.py`: `get_eta_stability` (jitter, method_changes, series, etc.)
- [x] 1.8 `CellMap.jsx`: `maxArrows=15`, umbral `span<0.05`, `spacing=max(0.05, span/5)`
- [x] 1.9 `TimeSlider.jsx`: play sin cortar arrastre, `PLAY_INTERVAL_MS=350`,
      marcas cada 30 min
- [x] 1.10 `api.js`: `getEtaStability(hours=6)` + typedef
- [x] 1.11 `AdminView.jsx`: panel "Estabilidad de la ETA" con sparkline SVG, jitter
      colorizado (verde/ámbar/rojo), filtro por horas
- [x] **Tests:** 117/117 ✅ (105 anteriores + 12 nuevos de Fase 1)
- [x] **Lint:** 0 warnings ✅
- [x] **Build:** ✅

### Fase 2 — Blend NWP real + upgrades Open-Meteo

Estado: **COMPLETADA** ✅ (14-jun-2026)

Checklist:
- [x] 2.1 `openmeteo.py`:
      - `fetch_wind_at(client, lat, lon, level=700)` generalizado — cache incluye `level` en clave
      - `fetch_wind_700_at` como alias de compatibilidad
      - `sample_wind_grid` actualizado a malla 6×6 (antes 4×4) y acepta `level`
      - `sample_precip_grid(client, bounds, nx=6, ny=6)` — precipitación mm/h en malla,
        cache por (lat 0.1°, lon 0.1°, hora UTC)
      - `fetch_minutely_15(client, lat, lon, n_steps=8)` — precipitación cada 15 min
      - `fetch_ensemble(client, lat, lon)` — fracción de miembros ICON-EPS con precip > 0.1 mm,
        cache por (lat 0.1°, lon 0.1°, hora UTC)
      - Caches separados: `_wind_cache`, `_precip_cache`, `_ensemble_cache`
- [x] 2.2 `predict.py`:
      - `precip_to_dbz(precip_mmh) -> float` — Marshall-Palmer (Z=200·R^1.6, retorna -31.5 para R≤0)
      - `_precip_grid_to_dbz_field(precip_grid, H, W, bounds)` — IDW de precip→dBZ a H×W
      - `_dbz_to_rgba(dbz_field, ref_image)` — convierte dBZ a RGBA (alpha=0 bajo umbral)
      - `blend_radar_nwp(advected_rgba, nwp_dbz, minutes, max_minutes=120)` — blend seamless
        INCA-like: alpha_radar decrece 1.0→0.3 en 0→120 min; NWP rellena lo que el radar pierde
      - `build_prediction` acepta `frames_recent` (multi-frame) y `precip_grid` (Fase 2);
        precomputa campo NWP una vez por call, aplica blend en cada paso
- [x] 2.3 `engine.py`:
      - Nuevo param `ensemble_prob: float | None = None` en `estimate_arrival`
      - Si `ensemble_prob` disponible se usa en lugar de `precipitation_probability` del horario
      - Expone `model_agreement = ensemble_prob` (o `model_prob` del horario)
- [x] 2.4 `scheduler.py` + `main.py`:
      - `scheduler.py`: llama `fetch_ensemble(fc, lat, lon)` por ciclo por punto,
        pasa como `ens_prob` a `estimate_arrival`; fallo silencioso
      - `main.py`: endpoint `/radar` llama `fetch_ensemble` y pasa al engine;
        endpoint `/prediction` llama `sample_precip_grid`, pasa `precip_grid` a `build_prediction`
- [x] **Fix de tests:** timestamps del test de `get_eta_stability` eran fechas hardcodeadas
      del 13-jun; corregidos a offsets relativos (`datetime.now(utc) - timedelta(minutes=N)`)
- [x] **Tests:** 117/117 ✅ | **Lint:** 0 warnings ✅ | **Build:** ✅

### Pendiente — Notificaciones push / PWA

Feature diferida para sesión futura. Objetivo: avisar al usuario cuando se
detecta ETA < 30 min en algún punto monitoreado. Requiere:
- Service Worker + cache offline (PWA)
- VAPID keys + endpoint de suscripción en el backend
- UI para activar/desactivar notificaciones por punto
- Push desde el scheduler cuando `eta_minutes <= 30` y `confidence >= 0.5`

---

---

## Sesión 5 — 14 jun 2026 — Auditoría + mejoras de motor, logging y UI

Plan ejecutado en dos bloques (A urgente + B sustancial) + logging. Todos los cambios
son no-rompedores: tests de 117 → 121, lint 0 warnings, build verde en cada paso.

### Bloque A — Correcciones urgentes ✅

**A1 — Fuga de memoria del cache Open-Meteo:**
`openmeteo.py` tenía 4 dicts de cache que crecían sin cota → RAM creciente en Railway.
Fix: purga horaria atómica via `_maybe_purge_all(bucket)` — cuando cambia la hora UTC,
se eliminan todas las entradas de la hora anterior en los 4 caches simultáneamente.
Funciones nuevas: `get_cache_stats()` para observabilidad, `_record_miss()` para el
contador de requests reales.

**A2 — `DBZ_RAIN_THRESHOLD` demasiado bajo:**
Umbral subido de 13.0 → **18.0 dBZ**, alineado con `DBZ_THRESHOLD` de tracking.
A 13 dBZ se incluía virga y ecos de ruido como "lloviendo ahora", inflando falsas alarmas.

**A3 — Guard de `forecast.hourly[0]` (posible IndexError):**
`engine.py:161` accedía a `forecast.hourly[0]` sin verificar si la lista estaba vacía.
Fix: guard `if forecast.hourly:` antes del acceso; si vacío, `wind_speed_700 = 0.0`
(degradación a solo radar sin corrección de viento 700 hPa). Test nuevo con
`PointForecast.model_construct(hourly=[])` para bypassear la validación Pydantic
(`min_length=1` impide instanciarlo de otro modo).

**A4 — Fetch de pronóstico puede bloquear el loop de radar:**
Cada punto llamaba a Open-Meteo sin timeout de pared dentro del ciclo de 90 s.
Fix: `asyncio.wait_for(fetch_forecast(...), timeout=12.0)` por punto; si expira, se
propaga como excepción y se loguea como warning, sin tumbar el ciclo.

**A5 — Tooltips educativos en la UI:**
- `RadarStatus.jsx`: tooltip en el valor dBZ explicando la escala en español simple.
- `PointCard.jsx`: tooltip en el badge de lluvia/virga/sin eco explicando dBZ ≥18;
  tooltip en el badge ETA explicando qué es el "Tiempo Estimado de Llegada".

**Tests nuevos (A1 + A3):** `test_cache_purge_removes_stale_entries`,
`test_cache_purge_noop_same_bucket`, `test_get_cache_stats_returns_expected_keys`,
`test_engine_empty_hourly_forecast_no_crash`. Tests totales: **121/121** ✅

---

### Logging — L1 a L5 ✅

Todas las mejoras usan `logging` estándar, sin dependencias nuevas.

- **L1** — `scheduler.py`: loguea el tamaño del cache Open-Meteo tras cada ciclo
  (`total`, desglose por tipo, requests reales de la hora actual).
- **L2** — `scheduler.py`: alerta si los bounds del frame nuevo difieren > 0.01° de
  los anteriores (`state.last_bounds`) — indica reencuadre del IAM.
- **L3** — `engine.py`: `log.warning` si `conf_radar` sale de `[0,1]` antes del clamp
  del blend — caza bugs de fórmula que el clamp silenciaría.
- **L4** — `scheduler.py`: una vez por hora (en `run_forecast_loop`), loguea
  POD/FAR/CSI/Acc globales desde `get_skill_metrics`.
- **L5** — Incluido en L1: el contador de misses reales (requests a Open-Meteo fuera
  de cache) por hora es visible directamente en el log de cache.

---

### Bloque B — Mejoras sustanciales ✅

**B1 — Búsqueda multicelular (ecos upstream):**
El motor evaluaba solo el eco más cercano corriente arriba. Ahora `find_upstream_echoes`
(nueva en `motion.py`) devuelve hasta 5 candidatos dentro del cono. El engine itera
todos, proyecta la ETA de cada uno, y elige el de **menor ETA** (el que llega primero).
El cono upstream se amplió de ±90° a **±120°** (constante `_UPSTREAM_CONE_DEG`).
La función `nearest_upstream_echo` existente delega a `_upstream_candidates` para
mantener compatibilidad de API (sin romper tests).

**B2 — Confianza interpretable:**
`NowcastResult` expone 3 nuevos campos nullable (actualizado en `schemas.py` y
`api.js` en el mismo commit):
- `conf_radar`: confianza cruda del radar (optical flow + alineación) antes del blend NWP.
- `weight_radar`: peso `w` del radar en el blend final.
- `mult_trend`: multiplicador de tendencia de área.
El tooltip de confianza en `PointCard.jsx` muestra el desglose:
"radar X% · modelo Y% · tendencia ×Z.ZZ (peso radar W%)".

**B3 — Toggles de capas en el mapa:**
`MapView.jsx` añade 4 botones pill (`Radar / Contornos / Flechas / Puntos`) con
estado React local (`showRadar`, `showContours`, `showArrows`, `showPoints`).
Cada botón tiene `aria-pressed` y `title` descriptivo; color lleno (primary) cuando
activo, borde gris cuando inactivo.
`CellMap.jsx` acepta los 4 props y los aplica condicionalmente en cada bloque de render.
El alto del mapa se ajusta de `100vh - 220px` a `100vh - 260px` para acomodar la barra.

**B4 — `API_BASE` centralizado:**
Nuevo `frontend/src/config.js` con `export const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"`.
Los 3 archivos que lo duplicaban (`api.js`, `MapView.jsx`, `PredictionView.jsx`)
ahora importan de aquí. Grid de tarjetas: `minmax(340px, 1fr)` → `minmax(280px, 1fr)`
para evitar desbordamiento horizontal en móvil.

**B5 — Refinamientos del motor:**
- *Colormap LUT*: `color_to_dbz` en `colormap.py` ahora verifica coincidencia exacta
  en el colormap (O(1)) antes de hacer la búsqueda NN O(N). `pixel_extract.py` mantiene
  un `_color_lut` módulo-global que cachea colores resueltos por NN entre frames, evitando
  repetir el scan para el mismo píxel visto en frames anteriores.
- *Magnitud del viento en confianza*: `project_cell` en `motion.py` ahora escala
  `conf_wind` por `min(1.0, wind_700_speed_kmh / 20.0)`. Un viento < 5 km/h (calma)
  ya no aporta señal de alineación, evitando falsa confianza alta con viento nulo.
- *EMA de tendencia de área*: `estimate_arrival` acepta nuevo param `prev_trend_ema: float|None`.
  Si se pasa, aplica EMA α=0.5: `trend = 0.5 * raw_trend + 0.5 * prev_trend_ema`.
  `RadarState` en `scheduler.py` añade `trend_ema: dict` y lo actualiza cada ciclo,
  suavizando el ruido de fotograma a fotograma en `mult_trend` e `intensity_trend`.

---

### Estado actual

**Tests:** 121/121 ✅ | **Lint:** 0 warnings ✅ | **Build:** ✅

**Pendiente operativo:**
- `ADMIN_TOKEN` en Railway Variables (token del Sprint 5, pendiente de configurar).
- Verificar skill 24–48h con nuevo umbral 18 dBZ y motor multicelular.

---

### Fase 3 — Doppler `_VR_`

Estado: **BLOQUEADA — VR no disponible en API pública** (investigado 14-jun-2026)

#### Hallazgos de la investigación

**Coordenadas del radar (resuelto):**
La posición del radar se extrae del campo `<lookAt>` del `doc.kml` que acompaña
cada frame ZH. Es constante en todos los frames; se añadió a `config.py`:
```python
RADAR_SITE_LAT = 20.67555618286133   # Av. Vallarta 2602, Guadalajara
RADAR_SITE_LON = -103.3858337402344
```

**`_VR_` no disponible en la API pública (bloqueante):**
Se probó el endpoint `api_radar.php?tipo_solicitud=kmz_act` con `radar=_VR_`,
`_ZDR_`, `_KDP_`, `_PHIDP_` y `_RHOHV_`. Todos devuelven `"error"`.
La API pública del IAM solo expone el producto `_ZH_` (reflectividad horizontal).
El producto de velocidad Doppler no está accesible sin credenciales adicionales.

#### Investigación SMN/CONAGUA (opción 2) — 14-jun-2026

Se investigó el visor de radares del SMN en busca de una ruta alternativa para
obtener datos VEL del mismo radar IAM-UdG.

**Hallazgos:**
- El SMN tiene páginas dedicadas al radar UDG con producto velocidad:
  `https://smn.conagua.gob.mx/tools/GUI/visor_radares_v2/radares/udg/udg_vel.php`
- URL de imágenes: `…/ecos/udg/velocidad/udg_vel_YYYYMMDD_HHMMSS.png`
- Bounds del VEL: N=21.759372, S=19.591604, W=-104.537519, E=-102.234149
- La descarga funciona con httpx + headers de Referer/User-Agent correctos
- Imagen descargada: PNG 600×600 px, 16KB, **colormap bipolar confirmado**
  (cian = acercándose, rosa/magenta = alejándose)
- **CRÍTICO: el SMN sirve datos congelados de 2023-02-07**. La página
  devuelve siempre `udg_vel_20230207_131035.png`. El feed UDG→SMN está
  inactivo; no es tiempo real.

**Fixture guardado:** `backend/tests/fixtures/frame_vel_smn.png` — muestra
el colormap bipolar y es útil para calibración si se obtiene acceso real.

**Conclusión de la opción 2:**
La ruta SMN no da datos en tiempo real para el radar IAM-UdG. El feed está
congelado y el SMN no lo actualiza.

#### Opciones para desbloquear

1. **Contactar al IAM directamente** — pedir acceso al feed VR o al API
   privado. Contacto: (33) 36 16 49 37 | iam@cucei.udg.mx
2. **Descartarlo (recomendado)** — el optical flow Farneback multi-frame
   (Fases 1–2) ya aproxima el campo de velocidad 2D razonablemente bien.
   La ganancia marginal de VR real vs. el OFM actual no justifica la
   complejidad adicional dado que la fuente no está disponible.

#### Estado práctico

Las coordenadas del radar se añadieron a `config.py`. El fixture VEL se guarda
para referencia futura.

**Fase 3: CANCELADA / pendiente** — no procede sin feed VR en tiempo real.
Reactivar solo si el IAM proporciona acceso directo: (33) 36 16 49 37.

---

## Sesión 6 — 14 jun 2026 — Esquema híbrido 3 capas (TITAN + leading-edge)

### Etapa 1 — Capa 2 backend: tracking de celdas ✅ (Compuerta 1)

Constantes nuevas en `config.py`: `CELL_MIN_PX=30`, `CELL_MATCH_MAX_KM=15.0`,
`CELL_MAX_MISSED=1`, `CELL_HISTORY_LEN=8`.

**`backend/app/processing/tracking.py`** (nuevo):
- `TrackedCell` — dataclass con id persistente, historial de centroides/área, EMA de velocidad
- `detect_cells(image, bounds)` — colormap LUT → máscara → connectedComponents → contornos
- `update_tracks(prev, dets, scan_time, bounds, interval_s, next_id)` — greedy con gating,
  determinista, EMA α=0.5 en bearing/speed, split/merge ligero

`RadarState` + `run_radar_loop` en `scheduler.py` actualizados para mantener y actualizar
el estado de celdas por ciclo. **Tests: 20 nuevos en `test_tracking.py` — 138/138 ✅**

---

### Etapa 2 — Capa 3 motor: ETA leading-edge ✅ (Compuerta 2)

**`motion.py`** — `leading_edge_point(ring, lat, lon, bounds)`: vértice del ring más cercano
al punto monitoreado + distancia en km.

**`engine.py`** — nueva ruta `cell_tracking` en `estimate_arrival`:
- Filtra celdas upstream (cono ±120°), usa distancia al borde (no al centroide)
- `mult_trend` de `cell.area_history`; expone `cell_id`, `cell_age_minutes`,
  `leading_edge_distance_km`, `method="cell_tracking"`
- Sin celda válida → fallback a `advection` sin regresión

**Tests: 7 nuevos en `test_nowcast.py` — 148/148 ✅**

---

### Etapa 3 — Contrato + endpoint ✅ (Compuerta 3)

**`schemas.py`** — `NowcastResult` + `cell_id`, `cell_age_minutes`, `leading_edge_distance_km`
(nullable). Nuevo modelo `TrackedCellSchema` con `id, lat, lon, mean_dbz, area_px, velocity_kmh,
bearing_deg, age_minutes, ring, track`.

**`api.js`** — typedefs actualizados en mismo commit (`NowcastResult` + `TrackedCell` +
`tracked_cells: TrackedCell[]` en `getRadar`).

**`main.py`** — `_serialize_tracked_cells` helper; `tracked_cells` en `/radar` SIEMPRE
serializado (fuera del bloque de frames, warmup funciona). **Tests: 10 nuevos — 158/158 ✅**

---

### Etapa 4 — UI completa ✅ (Compuerta 4)

**`CellMap.jsx`** — `trackedCellColor(dbz)` (paleta violeta), `trackedCellArrowIcon(bearing,dbz)`,
props `trackedCells=[]`/`showCells=false`, bloque de render Fragment:
Polygon ring + Polyline track + Marker tooltip (Celda #id, dBZ, vel, edad, área).

**`MapView.jsx`** — toggle "Celdas" en `LAYERS`, estado `trackedCells`, leyenda violeta,
pasa `showCells`/`trackedCells` a CellMap.

**`FieldGridView.jsx`** — toggle "Celdas" + `trackedCells` state.

**`PointCard.jsx`** — badge violeta `Celda #id · X.X min` cuando `method === "cell_tracking"`.

**Verificación Compuerta 4:**
- `npm run lint` 0 warnings ✅ | `npm run build` ✅ | `pytest` 158/158 ✅
- Browser (fetch mock): toggle "Celdas" ON → polígono violeta + flecha + trayectoria visibles ✅

---

## Sesión 7 — 14 jun 2026 — Observabilidad: logging, definición de celdas y calidad

**Objetivo:** Hacer visible lo que el sistema ya calcula pero no muestra:
(a) arreglar el logging (root en WARNING → root en INFO via `LOG_LEVEL`),
(b) exponer diagnósticos de detección/tracking por ciclo,
(c) quality score por celda, endpoint JSON `/radar/cells`, máscara PNG, y
(d) overlay + malla de calidad + panel de skill en la UI.

---

### Etapa 0 — Logging útil y JSONL estructurado ✅

**Problema raíz:** `logging.root` queda en WARNING con uvicorn; los `log.info`
del tracker (merge/split/purga/celdas vivas) y los L1–L5 de skill/cache
nunca aparecen en producción.

**Cambios:**
- `config.py`: `LOG_LEVEL` (default `INFO`) + `DIAG_LOG_PATH` (default
  `<DATA_DIR>/logs/nowcast_diag.jsonl`).
- `main.py`: `logging.root.setLevel(...)` en el lifespan startup → ahora
  todos los loggers `app.*` heredan el nivel correcto.
- `tracking.py`: `update_tracks` devuelve 3-tupla `(tracks, next_id, diag)`
  con: `n_alive`, `n_new`, `n_continued`, `n_purged`, `n_split`, `n_merge`,
  `gate_rejects`, `match_cost_mean`. Conteo determinista (sin np.random).
- `scheduler.py`: desempaca el 3er valor; añade línea `key=value` en
  `log.info` por ciclo (greppeable: `grep cycle_s backend.log`); escribe una
  línea JSON por ciclo en `DIAG_LOG_PATH` (stdlib `json` + `pathlib`, sin deps
  nuevas). `import json` y `from pathlib import Path` añadidos al módulo.
- `test_tracking.py`: todos los `update_tracks(...)` actualizados a 3-tupla;
  nuevos tests `TestDiagDict` (Compuerta 0): 3-tupla, claves requeridas, frame
  inicial todo-nuevo, continuado en 2º frame, purge, serialización JSONL,
  determinismo del diag.

**Compuerta 0:** `pytest` 165/165 ✅ (158 base + 7 nuevos).

---

### Estado actual

**Tests:** 165/165 ✅ | **Lint:** pendiente (Etapas 1–4) | **Build:** pendiente

**Push pendiente** — con consentimiento explícito del usuario.

### Etapas 1–3 — Quality score + Endpoint JSON + Máscara PNG ✅

**Etapa 1 — Quality score:**
- `config.py`: constantes `CELL_QUALITY_W_AREA/SOLIDITY/AGE/STABILITY` (pesos),
  `CELL_QUALITY_AREA_REF`, `CELL_QUALITY_AGE_REF`.
- `tracking.py`: `detect_cells` devuelve `solidity` y `extent` por celda
  (clip a 1.0 por artefacto subpíxel de convex hull). `TrackedCell` gana
  campo `quality: float = 0.0`. Helper `_cell_quality(area_px, solidity,
  age_frames, area_history, missed_frames) → float` determinista; penalización
  por `missed_frames`. `detection_mask(image, bounds) → np.ndarray` expuesto
  para el endpoint de máscara.

**Etapa 2 — Endpoint JSON `/radar/cells`:**
- `schemas.py` + `api.js` (mismo commit): `quality` aditivo en
  `TrackedCellSchema`; nuevos modelos `CellDetectionSchema`,
  `CellDebugDiagSchema`, `CellDebugSchema`; typedef `getCellDebug()` en `api.js`.
- `scheduler.py`: `RadarState` guarda `last_detections`, `last_track_diag`,
  `last_frame_time` tras cada ciclo de tracking.
- `main.py`: endpoint `GET /radar/cells` read-only, sin auth; deserializa el
  estado y devuelve `CellDebugSchema` con detecciones crudas + tracks + diag.

**Etapa 3 — Máscara PNG `/radar/cells/mask.png`:**
- `main.py`: endpoint `GET /radar/cells/mask.png` — llama a `detection_mask`,
  convierte a RGBA (blanco transparente) y devuelve `image/png`. 404 si no
  hay frame o bounds.

**Compuertas 1–3:** `pytest` 183/183 ✅ | `npm run lint` 0 warnings ✅ |
`npm run build` ✅.

---

### Estado actual

**Tests:** 183/183 ✅ | **Lint:** 0 warnings ✅ | **Build:** ✅

**Push pendiente** — con consentimiento explícito del usuario.

### Etapa 4 — UI: malla de calidad + panel de skill + debug de detecciones ✅

**`CellMap.jsx`:**
- `trackedCellColor(mean_dbz)` reemplazado por `qualityColor(q)` —
  escala rojo/ámbar/verde según `quality` (0–1), no solo por dBZ.
- `trackedCellArrowIcon(bearing, quality)` usa `qualityColor`.
- Polígonos de celdas coloreados por `quality`; tooltip incluye
  `Calidad: {q}%`, `mean_dbz`, `velocity`, `bearing`, `age`, `area`.
- Nueva capa de **detecciones crudas** (`rawDetections` prop + `showRawDetections`
  toggle): polígonos grises punteados pre-tracking con tooltip
  (dBZ, área, solidity, extent).

**`FieldGridView.jsx`:**
- Importa `getCellDebug` de `api.js`.
- Estados nuevos: `rawDetections`, `cellDiag`, `skillMetrics`, `showRawDetections`.
- Toggle nuevo **"Debug celdas"** (detecciones crudas pre-tracking).
- Carga `getCellDebug()` y `/metrics` en el useEffect (degradación silenciosa).
- **Panel de skill** (POD/FAR/CSI/Exactitud + pendientes) — colores semafórico.
- **Panel de diagnóstico de celdas** (n_det, n_alive, n_new, n_continued,
  n_purged, n_split, n_merge, gate_rejects, match_cost_mean, umbrales de config).
- **Leyenda de calidad** (verde/ámbar/rojo) visible cuando toggle "Celdas" está ON.

**Compuerta 4:** `pytest` 183/183 ✅ | `npm run lint` 0 warnings ✅ |
`npm run build` ✅.

---

### Estado actual

**Tests:** 183/183 ✅ | **Lint:** 0 warnings ✅ | **Build:** ✅

**Push pendiente** — con consentimiento explícito del usuario.

**Pendiente:**
- Verificación visual en navegador (uvicorn + npm run dev).
- Calibración de `CELL_MIN_PX`/`CELL_MATCH_MAX_KM` con tráfico real, guiada por los paneles de diagnóstico.

---

## Sesión 8 — 15 jun 2026 — Deploy a producción con volumen persistente

### Configuración Railway + Vercel completa ✅

**Objetivo:** Dejar el stack completamente desplegado en línea con persistencia de datos.

**Cambios realizados:**

- `backend/requirements.txt` — removidos `pytest` y `pytest-asyncio` (dev-only).
  No pertenecen al build de producción en Railway.
- `backend/requirements-dev.txt` (nuevo) — `-r requirements.txt` + pytest ≥8.2 + pytest-asyncio ≥0.23.
  Usar `pip install -r requirements-dev.txt` en desarrollo local.
- `backend/railway.toml` — sección `[variables]` con `LOG_LEVEL=INFO` y `DATA_DIR=/data`.
- `frontend/.env.example` — comentario de producción con URL de Railway.
- `backend/app/main.py` — **fix crítico**: `from pathlib import Path` +
  `Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)` en lifespan startup,
  antes de `init_db`. Sin esto, `sqlite3.connect("/data/nowcast.db")` falla si el
  directorio `/data` no existe aún (contenedor sin volumen montado).

**Volumen persistente Railway:**
- Nombre: `nowcast-gdl-volume`, montado en `/data`.
- Garantiza que SQLite (`/data/nowcast.db`) y el JSONL de diagnóstico
  (`/data/logs/nowcast_diag.jsonl`) sobreviven a cada redeploy.

**Commits:**
- `chore(deploy)`: configuración Railway + Vercel + requirements-dev.
- `fix(deploy): create DATA_DIR before sqlite3.connect to avoid crash on Railway`
  (commit `6d25c86`) — previene crash en arranque cuando `DATA_DIR=/data` y
  el directorio aún no existe en el contenedor.

**Estado de producción verificado:**
- `GET /points` → 200 JSON con los 3 puntos AMG ✅
- `GET /radar/cells` → JSON con `frame_time`, `n_detections=19`, `n_tracks=19`,
  `diagnostics keys: n_det/n_alive/n_new/n_continued/n_purged` ✅
- `GET /radar/cells/mask.png` → 200 `image/png` ✅
- Railway canvas: `nowcast-gdl` Online + `nowcast-gdl-volume` visibles ✅

**Tests:** 183/183 ✅ (sin regresiones)

---

## Sesión 9 — 15 jun 2026 — Visibilidad de capas Celdas / Debug celdas en /malla

### Problema reportado
Los toggles "Celdas" y "Debug celdas" en `/malla` no mostraban diferencia
visible al activarse/desactivarse.

### Diagnóstico (vía inspección de DOM y API en Chrome)

**Causa 1 — Opacidades demasiado bajas:**
- `rawDetections` (Debug celdas): `fillOpacity: 0.05`, `weight: 1` — prácticamente invisible.
- `trackedCells` (Celdas): `fillOpacity: 0.10`, `weight: 2` — indetectable sobre contornos de eco.

**Causa 2 — Blob gigante de Celda #1:**
La Celda #1 tenía un ring de 770 puntos abarcando lat 22.03 → 20.7 (todo el
sistema de lluvia como un componente conectado único). Su fill cubría TODO el
mapa con un tinte de fondo, haciendo imposible ver las celdas individuales.
Verificado vía `window.__radarData.tracked_cells[0]` en consola JS.

### Fixes aplicados (3 commits)

**commit `dcdbfb2` — `fix(ui): make Celdas and Debug-celdas layers visually distinct`**
- `CellMap.jsx`: `fillOpacity` de raw detections `0.05→0.25`, `weight: 1→2`,
  color cambiado a violeta `#7C3AED` (contrasta con contornos de eco).
- `CellMap.jsx`: `fillOpacity` de tracked cells `0.10→0.35`, `weight: 2→3`.
- `FieldGridView.jsx`: badges de conteo en los botones ("Celdas 18", "Debug celdas 14").
- `FieldGridView.jsx`: mensaje de empty-state cuando toggle activo pero sin datos.

**commit `fcb1935` — `fix(ui): skip polygon fill for storm-system-sized blobs`**
- Nueva función `ringLatSpan(ring)` en `CellMap.jsx`: calcula el span de
  latitud del ring.
- Constante `RING_MAX_SPAN_DEG = 0.3` (~33 km): threshold para considerar
  un blob "gigante".
- Celdas con ring span > 0.3° **no renderizan el Polygon de relleno** (la flecha
  de centroide y la trayectoria histórica sí se muestran). Esto elimina el
  tinte de fondo y deja visible la información real.
- Mismo filtro aplicado a `rawDetections` (Debug celdas).

### Qué se ve ahora al activar cada capa

**"Celdas"** — celdas rastreadas post-TITAN:
- Polígonos con borde coloreado por calidad en el área de lluvia activa:
  verde `#16A34A` (calidad ≥70%), ámbar `#D97706` (40–69%), rojo `#DC2626` (<40%).
- Flecha de centroide apuntando en la dirección de movimiento; tooltip con
  id, calidad, dBZ, km/h, bearing, edad, área_px.
- Línea punteada de trayectoria histórica de centroides.
- Celdas "blob gigante" solo muestran la flecha (sin fill que inunde el mapa).

**"Debug celdas"** — detecciones crudas pre-tracking (violeta):
- Polígonos violeta `#7C3AED` con borde discontinuo — cada blob que `detect_cells`
  encontró antes de que el tracker los asocie a IDs persistentes.
- Tooltip: dBZ promedio/máximo, área_px, solidity, extent.
- Sirve para calibrar `CELL_MIN_PX` y el threshold dBZ: si hay muchos blobs
  pequeños ruidosos → subir umbral; si las celdas se parten en muchos fragmentos
  → bajar threshold o ajustar morfología.

**Badge de conteo:** el número junto al botón ("Celdas 18") indica cuántas
celdas hay ahora mismo; si marca "0" hay cielo despejado, no un bug.

### Hallazgo de tracking a corregir (pendiente, backend)
La Celda #1 siempre tiene un ring de ~700+ puntos que abarca todo el AMG
porque el algoritmo TITAN detecta todo el sistema de lluvia conectado como
una sola celda. Falta ajustar `CELL_MIN_PX` o implementar un split de
componentes conectados grandes para que el tracker produzca celdas
individuales de tamaño razonable (~0.1–0.2° span).

### Estado final
- `npm run lint` 0 warnings ✅ | `npm run build` ✅
- Verificado en https://nowcast-gdl.vercel.app/malla con lluvia activa.
- 2 commits pusheados a `origin/master`.
