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

- `backend/` — Python 3.11, FastAPI, scheduler cada 90 s, SQLite
- `frontend/` — React + Vite + Recharts
- **Contrato de datos: `backend/app/schemas.py`** — NUNCA cambiar sin
  actualizar `frontend/src/api.js` en el mismo commit

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
