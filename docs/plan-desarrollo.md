# Plan de desarrollo — Nowcast GDL
## Herramienta de pronóstico a corto plazo para puntos específicos de Guadalajara
### Desarrollado con Claude Code · Junio 2026 · v2 (con datos reales del recon)

---

## 0. Resumen ejecutivo

Construir en ~2 semanas una herramienta con dos capas:

- **Nivel 1 (días 1–4):** Dashboard funcional con pronóstico hora a hora por punto usando Open-Meteo (viento, lluvia, probabilidad). Útil desde el primer día.
- **Nivel 2 (días 5–14):** Capa de nowcasting real — descarga del KMZ del radar IAM vía su API, extracción de dBZ por pixel en cada punto, y estimación de movimiento de celdas combinando frames consecutivos + vector de viento.

**Stack:** Python 3.11 + FastAPI (backend) · React + Vite (frontend) · SQLite (historial de frames) · OpenCV + Pillow (procesamiento de imagen).

**Estado del recon:** COMPLETO. La API del IAM está 100% mapeada (ver docs/spec-radar-iam.md). El radar actualiza cada 90 segundos, no cada 5 minutos como se asumió originalmente — mejor resolución temporal para optical flow.

---

## 1. Estructura del repositorio

```
nowcast-gdl/
├── CLAUDE.md                    # Contexto del proyecto (ya incluido en este kit)
├── .claude/
│   └── agents/
│       ├── radar-engineer.md    # Subagente: procesamiento de imagen del radar
│       ├── api-engineer.md      # Subagente: clientes de APIs externas
│       └── test-runner.md       # Subagente: corre tests y reporta solo fallos
├── docs/
│   ├── plan-desarrollo.md       # Este archivo
│   └── spec-radar-iam.md        # Spec verificada de la API del radar
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI
│   │   ├── config.py            # Puntos de la ciudad (lat/lon), constantes
│   │   ├── schemas.py           # CONTRATO de datos (Pydantic) — se define PRIMERO
│   │   ├── sources/
│   │   │   ├── openmeteo.py     # Cliente Open-Meteo
│   │   │   └── radar_iam.py     # Cliente API IAM + extracción KMZ
│   │   ├── processing/
│   │   │   ├── pixel_extract.py # lat/lon → pixel → color → dBZ
│   │   │   ├── colormap.py      # Escala dBZ calibrada de la leyenda
│   │   │   └── motion.py        # Optical flow entre frames (Nivel 2b)
│   │   ├── nowcast/
│   │   │   └── engine.py        # Combina radar + viento → ETA de lluvia
│   │   ├── storage.py           # SQLite: frames, lecturas por punto
│   │   └── scheduler.py         # Loop cada 90 s
│   └── tests/
│       └── fixtures/            # PNGs reales del radar + leyenda dBZ
└── frontend/
    └── src/
        ├── App.jsx
        ├── components/
        │   ├── PointCard.jsx     # Tarjeta por punto: ahora + próximas horas
        │   ├── WindCompass.jsx   # Rosa de viento
        │   ├── RadarStatus.jsx   # dBZ actual + tendencia
        │   └── HourlyChart.jsx   # Recharts: precipitación próximas 12h
        └── api.js
```

---

## 2. Agentes y asignación de modelos

### Filosofía de modelos en Claude Code

Configuración recomendada de la sesión: **`/model opusplan`** — Opus en plan
mode para arquitectura, Sonnet automático en ejecución. Verifica disponibilidad
con `/model`.

| Rol | Modelo | Por qué |
|---|---|---|
| Sesión principal (arquitectura, integración) | opusplan | Opus piensa, Sonnet ejecuta, automático |
| Subagentes de implementación | sonnet | Excelente costo/velocidad con spec clara |
| Subagente test-runner | haiku | Trabajo mecánico |

Los tres subagentes ya están definidos en `.claude/agents/` de este kit, con
los datos reales de la API integrados.

### Hábito clave con opusplan
Shift+Tab (plan mode) antes de cada decisión grande: inicio de cada sprint,
diseño de cada módulo nuevo. Si nunca entras a plan mode, nunca usas Opus.

---

## 3. Plan de sprints con paralelización

### Sprint 0 — Setup (medio día, secuencial)

1. Claude Code lee CLAUDE.md + docs/
2. Propone `backend/app/schemas.py` (PointForecast, RadarReading) — **APROBACIÓN HUMANA OBLIGATORIA antes de continuar**
3. Scaffold completo del repo con stubs y firmas de función
4. Setup de pytest apuntando a backend/tests/fixtures/
5. Definir los puntos de la ciudad en config.py

### Sprint 1 — Tres tracks EN PARALELO (días 1–3)

Prompt para la sesión principal:

```
Lanza tres subagentes en paralelo:

1. api-engineer: implementa backend/app/sources/openmeteo.py — cliente
   async que recibe lista de (nombre, lat, lon) y devuelve para cada
   punto: precipitación horaria 12h, probabilidad, viento 10m y 700hPa
   (dirección y velocidad), temperatura. Con cache y tests.

2. radar-engineer: implementa backend/app/sources/radar_iam.py +
   backend/app/processing/pixel_extract.py siguiendo exactamente
   docs/spec-radar-iam.md. Valida contra los fixtures.

3. Tercer subagente: scaffold del frontend React con Vite — App.jsx
   con grid de PointCards usando DATOS MOCK con el shape exacto de
   backend/app/schemas.py. No esperes al backend.
```

**Clave:** el contrato (`schemas.py`) se congela en Sprint 0. El frontend se
construye contra mocks con ese shape; al integrar, solo se cambia la URL.

### Sprint 2 — Integración + scheduler (días 4–5, mayormente secuencial)

- FastAPI endpoints: `GET /points`, `GET /points/{id}/forecast`, `GET /points/{id}/radar`
- Scheduler: loop cada 90 s → API IAM → si hay KMZ nuevo, extraer dBZ de cada punto → SQLite
- Conectar frontend a la API real
- **En paralelo (Ctrl+B):** test-runner corriendo la suite en background

### Sprint 3 — Nowcasting (días 6–10)

Módulo `motion.py`:
1. Toma los 2–3 frames más recientes de SQLite (separados 90 s — desplazamientos pequeños, flow estable)
2. Optical flow (OpenCV `calcOpticalFlowFarneback`) sobre la zona con eco
3. Deriva vector de movimiento de la celda (km/h + rumbo)
4. Cross-check contra viento de Open-Meteo a **700 hPa** (las celdas se mueven con el viento de niveles medios, NO el de superficie)
5. Proyecta: ¿la celda intersecta el punto X en 15/30/60 min?

**En paralelo durante Sprint 3:**
- Subagente frontend: componente de alerta visual
- Subagente api-engineer: fallback a RainViewer API (radar global público) por si el IAM se cae

**Post-MVP:** producto `_VR_` (velocidad radial Doppler) como tercera
validación del movimiento — ver spec.

### Sprint 4 — Pulido (días 11–14)

- Deploy: Railway/Fly.io (backend + scheduler), Vercel (frontend)
- Calibración con lluvia real (temporada activa — timing perfecto)
- Logging de aciertos: predicción a t+30 vs realidad → datos para mejorar

---

## 4. Qué paralelizar y qué NO

### Paraleliza (independientes):
- ✅ Cliente Open-Meteo ⟂ Cliente radar IAM ⟂ Frontend con mocks (Sprint 1)
- ✅ Tests en background (Ctrl+B) mientras desarrollas
- ✅ Fallback RainViewer ⟂ motor de nowcasting (Sprint 3)

### NO paralelices (dependencias secuenciales):
- ❌ `motion.py` antes de validar `pixel_extract.py`
- ❌ Integración frontend-backend antes de congelar `schemas.py`
- ❌ El scheduler antes de que ambas fuentes funcionen individualmente
- ❌ Dos subagentes editando el mismo archivo

### Regla práctica:
> Comparten archivo o una depende del output de la otra → secuencial.
> Módulos separados con contrato definido → subagentes paralelos.

---

## 5. Riesgos y mitigaciones

| Riesgo | Prob. | Mitigación |
|---|---|---|
| IAM cambia su API (no documentada) | Media | Cliente con detección de errores ("error" en respuesta); fallback RainViewer |
| IAM se cae en temporada (ver reseñas de su app) | Media-alta | Degradación a solo Open-Meteo + capa "radar no disponible" |
| Bug de fecha UTC (cambia a las 18:00 local) | Alta si no se cuida | Test específico obligatorio; SIEMPRE datetime.now(timezone.utc) |
| Calibración dBZ imprecisa | Media | Validar contra tuits de @RadarDopplerUdG |
| Optical flow ruidoso con ecos débiles | Alta | Umbral mínimo dBZ > 18 ("ligera") antes de calcular movimiento |
| Open-Meteo rate limit | Baja | Cache por hora; 7 puntos × 24 h = 168 calls/día vs 10k límite |

---

## 6. Métricas de éxito

- **Nivel 1:** dashboard responde < 2 s con pronóstico de los 7 puntos
- **Nivel 2:** lectura dBZ por punto con latencia < 3 min vs radar real
- **Nowcast:** en eventos con celda en movimiento, ETA ±15 min en horizonte de 30 min (medir durante la temporada)
