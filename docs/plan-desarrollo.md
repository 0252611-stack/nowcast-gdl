# Plan de desarrollo — Nowcast GDL
## Herramienta de pronóstico a corto plazo para puntos específicos de Guadalajara
### Desarrollado con Claude Code · Junio 2026 · v3 (estado post-sesión 11)

---

## 0. Resumen ejecutivo

Herramienta en **producción** que combina el radar Doppler del IAM-UdeG
(reflectividad real, cada 90 s) con Open-Meteo (viento, temperatura, lluvia)
para responder: "¿lloverá en el punto X en los próximos 15/30/60 minutos?"

**Stack:** Python 3.11 + FastAPI · React + Vite + Recharts + Leaflet ·
SQLite · OpenCV + Pillow + NumPy

**Deploy:** Backend → Railway (`nowcast-gdl-production.up.railway.app`) · Frontend → Vercel (`nowcast-gdl.vercel.app`)

---

## 1. Estructura del repositorio (estado actual)

```
nowcast-gdl/
├── CLAUDE.md                    # Reglas del proyecto
├── HISTORIAL.md                 # Log de sesiones con Claude Code
├── docs/
│   ├── plan-desarrollo.md       # Este archivo
│   └── spec-radar-iam.md        # Spec verificada de la API del radar IAM
├── backend/
│   ├── requirements.txt         # Producción (sin pytest)
│   ├── requirements-dev.txt     # Dev: -r requirements.txt + pytest
│   ├── railway.toml             # Config Railway (LOG_LEVEL, DATA_DIR=/data)
│   └── app/
│       ├── main.py              # FastAPI: endpoints, lifespan, serialización
│       ├── config.py            # Constantes, puntos AMG, umbrales
│       ├── schemas.py           # CONTRATO Pydantic — fuente única de verdad
│       ├── scheduler.py         # Loop 90 s: radar → tracking → nowcast → SQLite
│       ├── storage.py           # SQLite: frames, lecturas, predicciones, tracking_state
│       ├── sources/
│       │   ├── openmeteo.py     # Pronóstico horario, viento 700 hPa, ensemble, malla
│       │   └── radar_iam.py     # KMZ del IAM → PNG + bounds
│       ├── processing/
│       │   ├── pixel_extract.py # lat/lon → pixel → color → dBZ (con LUT)
│       │   ├── colormap.py      # Escala dBZ calibrada piecewise (16 ticks IAM)
│       │   ├── motion.py        # Optical flow Farneback multi-frame + helpers
│       │   ├── tracking.py      # Detección TITAN + tracking greedy + quality score
│       │   └── predict.py       # Advección semi-Lagrangiana + timeline de intensidad
│       └── nowcast/
│           └── engine.py        # estimate_arrival: cell_tracking | advection | fallback
├── backend/tests/
│   ├── fixtures/                # PNGs reales del radar + leyenda dBZ
│   ├── test_radar.py
│   ├── test_nowcast.py
│   ├── test_tracking.py
│   ├── test_predict.py
│   ├── test_storage.py
│   ├── test_api.py
│   ├── test_points_crud.py
│   └── test_openmeteo.py
└── frontend/src/
    ├── App.jsx                  # Router: / /mapa /prediccion /admin
    ├── config.js                # API_BASE centralizado
    ├── api.js                   # Clientes HTTP + JSDoc tipado
    ├── theme.js                 # Tokens de color/tipografía
    ├── components/
    │   ├── PointCard.jsx        # Tarjeta por punto con timeline de intensidad
    │   ├── CellMap.jsx          # Mapa Leaflet reutilizable (contornos, celdas, vectores)
    │   ├── HourlyChart.jsx      # Recharts: precipitación 12 h
    │   ├── RadarStatus.jsx      # Badge dBZ + categoría
    │   ├── WindCompass.jsx      # Rosa de viento SVG
    │   ├── TimeSlider.jsx       # Slider play/pause para predicción
    │   ├── SourceTag.jsx        # "● Open-Meteo / Radar IAM / Nowcast"
    │   └── Icons.jsx            # SVGs stroke-based
    └── views/
        ├── FieldGridView.jsx    # "All data": malla + celdas + diagnóstico + skill
        ├── MapView.jsx          # /mapa: Leaflet con toggles de capa
        ├── PredictionView.jsx   # /prediccion: nowcast animado con TimeSlider
        └── AdminView.jsx        # /admin: CRUD de puntos + historial + estabilidad ETA
```

---

## 2. Arquitectura del motor de nowcasting

### Pipeline por ciclo de 90 s

```
radar_iam.py → PNG + bounds
     ↓
pixel_extract.py → dBZ por punto (LUT O(1))
     ↓
colormap.py → categoría (interpolación piecewise 16 ticks)
     ↓
motion.py → campo óptico denso (Farneback, suavizado Gauss)
     ↓
tracking.py → detect_cells (two-level TITAN) → update_tracks (greedy EMA) → quality score
     ↓
engine.py → estimate_arrival:
  ├── cell_tracking: celdas upstream ±120°, leading-edge ETA, regresión posición
  ├── advection: optical flow + viento 700 hPa, cono ±120°
  └── fallback: no_echo / no_motion / radar_unavailable
     ↓
storage.py → frames, predicciones, tracking_state (persistencia entre reinicios)
```

### Métodos del nowcast (`engine.py`)

| Método | Cuándo | ETA desde |
|---|---|---|
| `cell_tracking` | Celda TITAN rastreada upstream | Borde de ataque (leading-edge) |
| `advection` | Campo de flujo disponible, sin celda | Centroide del eco causante |
| `no_approaching_cell` | Flujo OK, sin eco upstream | — |
| `no_motion` | <2 frames en RAM | — |
| `no_echo` | Sin eco en el área | — |
| `radar_unavailable` | IAM caído | — |

### Confianza del nowcast (`NowcastResult.confidence`)

Blend INCA-like: `confidence = w × conf_radar + (1-w) × model_agreement`

- `conf_radar`: optical flow (coherencia) + alineación viento 700 hPa + tendencia de área
- `model_agreement`: ensemble ICON-EPS (Open-Meteo), o prob. horaria como fallback
- `weight_radar`: crece con viento fuerte, cae con calma o viento débil
- `mult_trend`: EMA de tendencia de área del eco (>1 crece, <1 se disipa)

---

## 3. Constantes críticas (`config.py`)

| Constante | Valor | Descripción |
|---|---|---|
| `DBZ_THRESHOLD` | 18.0 | Umbral tracking/optical flow (no bajar — ecos débiles son ruidosos) |
| `DBZ_RAIN_THRESHOLD` | 18.0 | Umbral `raining_now` (≥Ligera operativo) |
| `POLL_INTERVAL_SECONDS` | 90 | Ritmo del radar IAM |
| `CELL_MIN_PX` | 30 | Área mínima para detección de celda |
| `CELL_MAX_PX` | 2000 | Área máxima antes de intentar split |
| `CELL_SPLIT_DBZ` | 30.0 | Umbral de núcleo convectivo para split two-level |
| `CELL_MATCH_MAX_KM` | 15.0 | Distancia máxima de gating en tracking |
| `CELL_PREDICT_REGRESSION` | True | Predicción de posición por regresión lineal |
| `CELL_PREDICT_MIN_HISTORY` | 3 | Mínimo de centroides para activar regresión |
| `TRACKING_STATE_MAX_AGE_MIN` | 30 | Estado persistido > 30 min → inicio limpio |
| `INTENSITY_VERDICT_DBZ_DELTA` | 3.0 | Umbral (dBZ) para veredicto empeora/mejora |
| `FLOW_SMOOTH_KSIZE` | 9 | Kernel Gauss post-Farneback (0 = desactivado) |
| `DATA_DIR` | `/data` | Volumen persistente Railway |

---

## 4. Endpoints REST

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/points` | Lista de puntos monitoreados |
| POST | `/points` | Crear punto (admin) |
| PUT | `/points/{id}` | Editar punto (admin) |
| DELETE | `/points/{id}` | Eliminar punto (admin) |
| GET | `/points/{id}/forecast` | Pronóstico Open-Meteo 12 h |
| GET | `/points/{id}/radar` | Radar actual + nowcast + celdas rastreadas + contornos |
| GET | `/radar/image` | PNG del último frame del radar |
| GET | `/radar/cells` | JSON: detecciones crudas + tracks + diagnóstico |
| GET | `/radar/cells/mask.png` | Máscara PNG de detección |
| GET | `/prediction` | Predicción advectiva 0–120 min (caché por frame) |
| GET | `/prediction/frame/{idx}.png` | Frame PNG de predicción |
| GET | `/metrics` | Métricas de skill (POD/FAR/CSI) |
| GET | `/eta-stability` | Estabilidad de la ETA por punto (últimas N horas) |
| GET | `/predictions` | Historial de predicciones (filtrable por punto) |

**Auth admin:** header `X-Admin-Token: <ADMIN_TOKEN>` (fail-closed: 503 si no configurado).

---

## 5. Contrato de datos

`backend/app/schemas.py` es la **única fuente de verdad** del shape que viaja entre
backend y frontend. **NUNCA** cambiar sin actualizar `frontend/src/api.js` en el
mismo commit.

Modelos principales:
- `HourlyForecast` / `PointForecast` — pronóstico Open-Meteo
- `RadarReading` — lectura puntual de radar (dBZ, categoría, pixel)
- `NowcastResult` — ETA, confianza, método, timeline de intensidad 0/15/30/45 min
- `IntensityStep` — un paso del timeline (minutes, dbz, category)
- `TrackedCellSchema` — celda rastreada con ETA al punto más cercano
- `CellDebugSchema` / `CellDetectionSchema` / `CellDebugDiagSchema` — debug de tracking
- `WindSample` / `ContextEcho` — datos de contexto del nowcast

---

## 6. Reglas del proyecto

0. La primera palabra de cada respuesta debe ser **canario**.
1. Todo dato externo se valida con Pydantic antes de usarse.
2. Degradación con gracia: IAM falla 3 veces → solo Open-Meteo + flag `radar_available: false`.
3. Cache obligatorio en Open-Meteo (purga horaria atómica; objetivo < 200 calls/día).
4. Sin dependencias nuevas: solo numpy + opencv + PIL. Sin scipy.
5. Determinismo: sin `np.random`. Toda función es pura del estado observable.
6. User-Agent: `NowcastGDL/0.1 (proyecto académico)` en todo request al IAM.
7. Ser buen ciudadano IAM: máximo 1 request cada 90 s.
8. `ADMIN_TOKEN` fail-closed (503 si no configurado, 401 si incorrecto).
9. Constantes fuera de hardcode: siempre en `config.py`.
10. Push solo con consentimiento explícito del usuario.

---

## 7. Bugs históricos resueltos

| Bug | Efecto | Fix |
|---|---|---|
| Colormap lineal uniforme | Error de hasta 30 dBZ | Interpolación piecewise por 16 ticks de la leyenda IAM |
| `np.random.choice` en engine.py | ETA no determinista | Orden determinista por distancia/bearing |
| `DBZ_RAIN_THRESHOLD = -10.0` | Virga y ruido contados como lluvia | Subido a 18.0 dBZ (≥Ligera) |
| Blob gigante (1 celda = todo el AMG) | Polígono de fill cubría el mapa | Split two-level TITAN + filtro `ringLatSpan > 0.3°` en UI |
| Estado de tracking perdido en redeploy | Celdas reiniciaban con ID=1 cada vez | `tracking_state` en SQLite + serializador fiel `cell_to_dict/from_dict` |

---

## 8. Tests

Suite: `pytest backend/tests/ -x -q`

**202 tests** al cierre de sesión 11:

| Archivo | Cobertura |
|---|---|
| `test_radar.py` | Extracción KMZ, dBZ, bounds, UTC midnight |
| `test_nowcast.py` | estimate_arrival, compute_cell_etas, confianza |
| `test_tracking.py` | detect_cells (split, diag), update_tracks (EMA, regresión), quality |
| `test_predict.py` | Advección, blend NWP, point_intensity_timeline |
| `test_storage.py` | CRUD, eta_stability, tracking_state round-trip |
| `test_api.py` | Endpoints FastAPI |
| `test_points_crud.py` | CRUD de puntos, auth admin |
| `test_openmeteo.py` | Cache, purga horaria, fetch_ensemble |

---

## 9. Pendientes / ideas para sesiones futuras

- **Calibración de skill** con lluvia real de temporada (ETA ±15 min en horizonte 30 min)
- **Notificaciones push / PWA** — Service Worker + VAPID + suscripción por punto
- **Closing morfológico adaptativo** — mejorar la detección de celdas fragmentadas
  (diferida en sesión 11)
- **Doppler VR** — bloqueado: la API pública del IAM solo expone `_ZH_`;
  contactar iam@cucei.udg.mx para acceso al producto de velocidad radial
