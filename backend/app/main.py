"""FastAPI application — endpoints REST del Nowcast GDL."""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field

from app import config
from app.nowcast.engine import compute_cell_etas, estimate_arrival
from app.processing.motion import compute_cell_motion, find_context_echoes, find_echo_contours, sample_ring_vectors
from app.processing.tracking import TrackedCell, detect_cells, detection_mask, update_tracks
from app.scheduler import RadarState, run_forecast_loop, run_radar_loop
from app.schemas import (
    CellDebugDiagSchema,
    CellDebugSchema,
    CellDetectionSchema,
    ContextEcho,
    TrackedCellSchema,
    WindSample,
)
from app.sources.openmeteo import (
    fetch_ensemble,
    fetch_forecast,
    fetch_wind_700_at,
    sample_precip_grid,
    sample_trajectory_wind,
    sample_wind_grid,
)
from app.sources.rainviewer import fetch_tile_url as fetch_rainviewer_url
from app.storage import (
    add_point,
    delete_point,
    get_eta_stability,
    get_latest_reading,
    get_predictions,
    get_recent_frames,
    get_skill_metrics,
    init_db,
    list_points,
    load_tracking_state,
    seed_points,
    update_point,
)

_RAINVIEWER_TTL = 300.0  # segundos entre llamadas a RainViewer

log = logging.getLogger(__name__)


def _serialize_tracked_cells(
    cells: list,
    interval_seconds: float = 90.0,
    cell_etas: dict | None = None,
) -> list[dict]:
    """Convierte list[TrackedCell] al shape de TrackedCellSchema para el endpoint.

    cell_etas (opcional): dict {cell_id: {eta_minutes, eta_point_id, eta_confidence}}
    generado por compute_cell_etas. Si se pasa, los 3 campos se fusionan en cada
    celda (TrackedCellSchema tiene extra="forbid" con esos campos desde Etapa 4).
    """
    out = []
    for c in cells:
        age_min = round((c.age_frames - 1) * interval_seconds / 60.0, 1)
        track = [[pt[0], pt[1]] for pt in c.centroid_history]
        eta_info = cell_etas.get(c.id, {}) if cell_etas else {}
        out.append({
            "id": c.id,
            "lat": c.lat,
            "lon": c.lon,
            "mean_dbz": c.mean_dbz,
            "area_px": c.area_px,
            "velocity_kmh": c.velocity_kmh,
            "bearing_deg": c.bearing_deg,
            "age_minutes": age_min,
            "ring": c.ring,
            "track": track,
            "quality": c.quality,
            "eta_minutes": eta_info.get("eta_minutes"),
            "eta_point_id": eta_info.get("eta_point_id"),
            "eta_confidence": eta_info.get("eta_confidence"),
        })
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn NO añade handler al root logger, solo a sus propios loggers.
    # Sin handler en root, Python usa lastResort que descarta INFO.
    # Solución: configurar el logger "app" con su propio StreamHandler y nivel.
    _log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    _app_logger = logging.getLogger("app")
    _app_logger.setLevel(_log_level)
    if not _app_logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        _app_logger.addHandler(_h)
    _app_logger.propagate = False

    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(config.DB_PATH)
    seed_points(conn, config.POINTS)
    state = RadarState()

    # Cargar estado de tracking persistido (sobrevive reinicios/redeploys).
    # Solo se usa si el estado es reciente (< TRACKING_STATE_MAX_AGE_MIN).
    try:
        from datetime import datetime as _dt, timezone as _tz
        saved_cells, saved_next_id, saved_frame_time = load_tracking_state(conn)
        if saved_frame_time is not None:
            age_min = (_dt.now(_tz.utc) - saved_frame_time).total_seconds() / 60.0
            if age_min <= config.TRACKING_STATE_MAX_AGE_MIN and saved_cells:
                state.tracked_cells = saved_cells
                state.next_cell_id = saved_next_id
                state.last_frame_time = saved_frame_time
                log.info(
                    "Estado de tracking restaurado: %d celdas, next_id=%d (age=%.1f min)",
                    len(saved_cells), saved_next_id, age_min,
                )
            else:
                log.info(
                    "Estado de tracking descartado: age=%.1f min > %d min (o sin celdas).",
                    age_min if saved_frame_time else 9999.0,
                    config.TRACKING_STATE_MAX_AGE_MIN,
                )
    except Exception as exc_load:
        log.warning("Error cargando estado de tracking: %s — empezando limpio.", exc_load)

    radar_task = asyncio.create_task(run_radar_loop(conn, state))
    forecast_task = asyncio.create_task(run_forecast_loop(conn))
    app.state.db = conn
    app.state.radar_state = state
    app.state.rainviewer_url: str | None = None
    app.state.rainviewer_url_ts: float = 0.0
    # Cache de contornos: (frame_time, contours) — global, reutilizable entre puntos
    app.state.echo_contours_cache: tuple | None = None
    # Cache de predicción: (frame_time, result_dict) — advección semi-Lagrangiana
    app.state.prediction_cache: tuple | None = None
    log.info("Nowcast GDL iniciado. Scheduler activo.")
    try:
        yield
    finally:
        radar_task.cancel()
        forecast_task.cancel()
        conn.close()
        log.info("Nowcast GDL detenido.")


app = FastAPI(title="Nowcast GDL", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_origin_regex=r"http://localhost:\d+",  # cualquier puerto local en dev
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth admin
# ---------------------------------------------------------------------------

async def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    if not config.ADMIN_TOKEN:
        raise HTTPException(503, "Admin token not configured on server")
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(401, "Invalid or missing admin token")


# ---------------------------------------------------------------------------
# Modelos de escritura de puntos
# ---------------------------------------------------------------------------

class PointCreate(BaseModel):
    id: str
    name: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class PointUpdate(BaseModel):
    name: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_point(db: sqlite3.Connection, point_id: str) -> dict:
    pts = {p["id"]: p for p in list_points(db)}
    pt = pts.get(point_id)
    if pt is None:
        raise HTTPException(status_code=404, detail=f"Punto '{point_id}' no encontrado")
    return pt


# ---------------------------------------------------------------------------
# Endpoints de lectura
# ---------------------------------------------------------------------------

@app.get("/radar/image")
async def get_radar_image():
    """Último frame del radar IAM como PNG con fondo transparente."""
    frames = get_recent_frames(app.state.db, 1)
    if not frames:
        raise HTTPException(404, "No hay frames de radar disponibles")
    return Response(
        content=frames[0][0],
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/radar/cells", response_model=CellDebugSchema)
async def get_radar_cells():
    """Detecciones crudas + tracks rastreados + diagnóstico del último ciclo de tracking.

    Endpoint de observabilidad read-only (sin autenticación): permite inspeccionar
    la calidad de la detección de celdas y el matching entre ciclos.
    """
    state: RadarState = app.state.radar_state
    frame_time = (
        state.last_frame_time.isoformat() if state.last_frame_time else None
    )
    _cell_etas = compute_cell_etas(
        state.tracked_cells, list_points(app.state.db),
        state.last_bounds, state.motion_field_ema,
    )
    tracks_out = _serialize_tracked_cells(state.tracked_cells, state._cell_interval_s, _cell_etas)
    diag = state.last_track_diag
    return CellDebugSchema(
        frame_time=frame_time,
        detections=[CellDetectionSchema(**d) for d in state.last_detections],
        tracks=[TrackedCellSchema(**t) for t in tracks_out],
        diagnostics=CellDebugDiagSchema(
            n_det=len(state.last_detections),
            n_alive=diag.get("n_alive", 0),
            n_new=diag.get("n_new", 0),
            n_continued=diag.get("n_continued", 0),
            n_purged=diag.get("n_purged", 0),
            n_split=diag.get("n_split", 0),
            n_merge=diag.get("n_merge", 0),
            gate_rejects=diag.get("gate_rejects", 0),
            match_cost_mean=diag.get("match_cost_mean"),
            cell_min_px=config.CELL_MIN_PX,
            dbz_threshold=config.DBZ_THRESHOLD,
            match_max_km=config.CELL_MATCH_MAX_KM,
            det_n_components=diag.get("det_n_components", 0),
            det_n_oversized=diag.get("det_n_oversized", 0),
            det_n_blob_split=diag.get("det_n_blob_split", 0),
            det_n_split_subcells=diag.get("det_n_split_subcells", 0),
            det_n_kept_whole=diag.get("det_n_kept_whole", 0),
        ),
    )


@app.get("/radar/cells/mask.png")
async def get_radar_cells_mask():
    """Máscara binaria de detección del último frame como PNG blanco/negro.

    Muestra exactamente qué píxeles pasan el umbral dBZ y la morfología que
    produce detect_cells. Útil para calibrar CELL_MIN_PX y DBZ_THRESHOLD.
    """
    frames = get_recent_frames(app.state.db, 1)
    if not frames:
        raise HTTPException(404, "No hay frames de radar disponibles")
    state: RadarState = app.state.radar_state
    if not state.last_bounds:
        raise HTTPException(404, "Bounds del radar aún no disponibles (warmup)")
    img = Image.open(io.BytesIO(frames[0][0]))
    mask_arr = detection_mask(img, state.last_bounds)
    # Convertir a imagen RGB: máscara en blanco, fondo transparente
    rgba = Image.new("RGBA", (mask_arr.shape[1], mask_arr.shape[0]), (0, 0, 0, 0))
    white_layer = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    mask_pil = Image.fromarray(mask_arr, mode="L")
    rgba.paste(white_layer, mask=mask_pil)
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/diag/log")
async def get_diag_log(tail: int = 0):
    """Log JSONL de diagnóstico por ciclo (detección, tracking, flujo óptico, motor, skill).

    Sin `tail` devuelve el archivo completo. `?tail=N` devuelve solo las últimas N líneas
    (útil para muestreos rápidos sin descargar el histórico entero). Read-only, sin auth,
    mismo criterio que /radar/cells y /metrics (no expone nada sensible).
    """
    diag_path = Path(config.DIAG_LOG_PATH)
    if not diag_path.exists():
        raise HTTPException(404, "Aún no hay registros de diagnóstico")
    lines = diag_path.read_text(encoding="utf-8").splitlines()
    if tail and tail > 0:
        lines = lines[-tail:]
    return Response(
        content="\n".join(lines) + ("\n" if lines else ""),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/metrics")
async def metrics():
    """Métricas de verificación del motor de nowcasting (POD, FAR, CSI, accuracy)."""
    return get_skill_metrics(app.state.db)


@app.get("/eta-stability")
async def eta_stability(hours: int = 6):
    """Variabilidad de la ETA por punto en las últimas `hours` horas.

    Devuelve lista de {point_id, n, eta_mean, eta_std, jitter, method_changes,
    pct_with_eta, current_method, last_eta, series}. Útil para monitorear
    cuánto salta la predicción de ciclo a ciclo y diagnosticar la causa
    (cambios de método vs. ruido de velocidad).
    """
    return get_eta_stability(app.state.db, hours)


@app.get("/predictions")
async def list_predictions(limit: int = 100, point_id: str | None = None):
    """Historial de predicciones individuales (recientes primero)."""
    return get_predictions(app.state.db, limit=limit, point_id=point_id)


@app.get("/points")
async def list_points_endpoint():
    """Lista de todos los puntos monitoreados."""
    return list_points(app.state.db)


@app.get("/points/{point_id}/forecast")
async def get_forecast(point_id: str):
    """Pronóstico Open-Meteo de las próximas 12 h para el punto."""
    pt = _get_point(app.state.db, point_id)
    async with httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT}, timeout=10
    ) as client:
        forecast = await fetch_forecast(client, pt["id"], pt["name"], pt["lat"], pt["lon"])
    return forecast


@app.get("/points/{point_id}/radar")
async def get_radar(point_id: str):
    """Última lectura de radar + disponibilidad + nowcast para el punto."""
    pt = _get_point(app.state.db, point_id)
    state: RadarState = app.state.radar_state
    reading = get_latest_reading(app.state.db, point_id)
    nowcast = None
    rainviewer_url = None
    context_echoes: list[ContextEcho] = []
    echo_contours: list[dict] = []
    tracked_cells_out: list[dict] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT}, timeout=10
    ) as client:
        try:
            frames = get_recent_frames(app.state.db, 2)
            forecast = await fetch_forecast(client, pt["id"], pt["name"], pt["lat"], pt["lon"])
            # Ensemble (Fase 2): probabilidad de precipitación del spread NWP
            ensemble_prob: float | None = None
            try:
                ensemble_prob = await fetch_ensemble(client, pt["lat"], pt["lon"])
            except Exception:
                pass
            nowcast = estimate_arrival(
                point_id, reading, forecast, frames, state.last_bounds,
                motion_field=state.motion_field_ema,
                ensemble_prob=ensemble_prob,
                tracked_cells=state.tracked_cells if hasattr(state, "tracked_cells") else None,
            )

            if nowcast is not None and nowcast.cell_lat is not None:
                # Viento 700 hPa en el eco
                try:
                    ew = await fetch_wind_700_at(client, nowcast.cell_lat, nowcast.cell_lon)
                    nowcast.wind_echo_bearing_deg = ew["toward_deg"]
                    nowcast.wind_echo_speed_kmh = ew["speed_kmh"]
                except Exception as exc_w:
                    log.debug("Viento en eco no disponible: %s", exc_w)

                # Viento a lo largo de la trayectoria eco → punto
                try:
                    traj = await sample_trajectory_wind(
                        client, nowcast.cell_lat, nowcast.cell_lon, pt["lat"], pt["lon"]
                    )
                    nowcast.trajectory_wind = [WindSample(**s) for s in traj]
                except Exception as exc_t:
                    log.debug("Trajectory wind no disponible: %s", exc_t)

            # Ecos de contexto + contornos — solo necesita 1 frame
            try:
                if len(frames) >= 1 and state.last_bounds:
                    newer_bytes, newer_time = frames[0]
                    bearing, speed = 0.0, 0.0
                    if len(frames) >= 2:
                        older_bytes, older_time = frames[1]
                        interval_s = max(1.0, (newer_time - older_time).total_seconds())
                        m = compute_cell_motion(
                            older_bytes, newer_bytes, interval_s, state.last_bounds
                        )
                        bearing = m["bearing_deg"]
                        speed = m["speed_kmh"]
                    img = Image.open(io.BytesIO(newer_bytes))
                    raw = find_context_echoes(
                        img, state.last_bounds, bearing, speed
                    )
                    context_echoes = [ContextEcho(**e) for e in raw]

                    # Contornos enriquecidos con vectores locales del campo de movimiento.
                    # Se reusan si el frame no cambió (globales por imagen).
                    cached = app.state.echo_contours_cache
                    if cached is not None and cached[0] == newer_time:
                        echo_contours = cached[1]
                    else:
                        plain_rings = find_echo_contours(img, state.last_bounds)
                        mf = state.motion_field_ema
                        echo_contours = [
                            {
                                "ring": ring,
                                "vectors": sample_ring_vectors(ring, mf, state.last_bounds)
                                if mf is not None else [],
                            }
                            for ring in plain_rings
                        ]
                        app.state.echo_contours_cache = (newer_time, echo_contours)

            except Exception as exc_ce:
                log.debug("Context echoes no disponibles: %s", exc_ce)

        except Exception as exc:
            log.warning("Nowcast engine falló para %s: %s", point_id, exc)

        # Celdas rastreadas — siempre disponibles desde el estado del scheduler,
        # independientemente de si hay frames nuevos (warmup = lista vacía).
        _tc = getattr(state, "tracked_cells", [])
        _bounds_eta = state.last_bounds
        _mf_eta = state.motion_field_ema
        _pts_eta = list_points(app.state.db)
        _cell_etas_radar = compute_cell_etas(_tc, _pts_eta, _bounds_eta, _mf_eta)
        tracked_cells_out = _serialize_tracked_cells(
            _tc,
            getattr(state, "_cell_interval_s", 90.0),
            _cell_etas_radar,
        )

        if not state.available:
            now = time.monotonic()
            if app.state.rainviewer_url is None or now - app.state.rainviewer_url_ts > _RAINVIEWER_TTL:
                app.state.rainviewer_url = await fetch_rainviewer_url(client, pt["lat"], pt["lon"])
                app.state.rainviewer_url_ts = now
            rainviewer_url = app.state.rainviewer_url

    return {
        "radar": reading,
        "radar_available": state.available,
        "nowcast": nowcast,
        "rainviewer_url": rainviewer_url,
        "context_echoes": context_echoes,
        "echo_contours": echo_contours,
        "tracked_cells": tracked_cells_out,
        "radar_bounds": state.last_bounds,
    }


# ---------------------------------------------------------------------------
# Endpoints de predicción advectiva
# ---------------------------------------------------------------------------

@app.get("/prediction")
async def get_prediction():
    """Predicción advectiva del campo de eco para los próximos 120 minutos.

    Motor: optical flow denso multi-frame (Farneback, EMA temporal) + corrección
    de viento 700 hPa en malla 4×4. Devuelve 24 pasos de +5 a +120 min con
    frames PNG y contornos. Cacheado por timestamp de frame (~90 s TTL).
    """
    from app.processing.predict import build_prediction

    state: RadarState = app.state.radar_state

    if not state.available or state.last_bounds is None:
        return {
            "available": False, "method": "radar_unavailable",
            "base_time": None, "bounds": None,
            "steps": [], "trajectories": [],
        }

    frames = get_recent_frames(app.state.db, 2)
    if len(frames) < 2:
        return {
            "available": False, "method": "insufficient_frames",
            "base_time": None, "bounds": state.last_bounds,
            "steps": [], "trajectories": [],
        }

    newer_bytes, newer_time = frames[0]
    older_bytes, older_time = frames[1]

    # Reusar si el frame base no cambió
    cached = app.state.prediction_cache
    if cached is not None and cached[0] == newer_time:
        result = cached[1]
        log.debug("Predicción: cache hit para frame %s", newer_time)
    else:
        interval_s = max(1.0, (newer_time - older_time).total_seconds())
        wind_grid = []
        precip_grid = []
        async with httpx.AsyncClient(
            headers={"User-Agent": config.USER_AGENT}, timeout=15
        ) as client:
            try:
                wind_grid = await sample_wind_grid(client, state.last_bounds)
            except Exception as exc_w:
                log.warning("Viento en malla no disponible: %s — usando solo radar", exc_w)
            # Fase 2: malla de precipitación NWP para blend seamless
            try:
                precip_grid = await sample_precip_grid(client, state.last_bounds)
            except Exception as exc_p:
                log.debug("Malla de precipitación NWP no disponible: %s", exc_p)

        # Frames recientes para multi-frame motion (más estable que un solo par)
        frames_recent = get_recent_frames(app.state.db, 4)

        result = build_prediction(
            older_bytes, newer_bytes, interval_s, state.last_bounds, wind_grid,
            frames_recent=frames_recent,
            precip_grid=precip_grid or None,
        )
        app.state.prediction_cache = (newer_time, result)
        log.info("Predicción generada: %d pasos, método=%s", len(result["steps"]), result["method"])

    steps_response = [
        {
            "minutes": s["minutes"],
            "image_url": f"/prediction/frame/{i}.png",
            "contours": s["contours"],
        }
        for i, s in enumerate(result["steps"])
    ]

    return {
        "available": True,
        "base_time": newer_time.isoformat(),
        "bounds": result["bounds"],
        "method": result["method"],
        "steps": steps_response,
        "trajectories": result["trajectories"],
    }


@app.get("/prediction/frame/{idx}.png")
async def get_prediction_frame(idx: int):
    """Frame i del nowcast advectivo como PNG (0 = +15 min, 7 = +120 min)."""
    cached = app.state.prediction_cache
    if cached is None:
        raise HTTPException(404, "No hay predicción en caché; llame primero a GET /prediction")
    _, result = cached
    frames_png: list[bytes] = result.get("frames_png", [])
    if idx < 0 or idx >= len(frames_png):
        raise HTTPException(404, f"Frame {idx} fuera de rango (0–{len(frames_png) - 1})")
    return Response(
        content=frames_png[idx],
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


# ---------------------------------------------------------------------------
# Endpoints de escritura (requieren token admin)
# ---------------------------------------------------------------------------

@app.post("/points", dependencies=[Depends(require_admin)], status_code=201)
async def create_point(body: PointCreate):
    """Crea un nuevo punto monitoreado."""
    try:
        return add_point(app.state.db, body.id, body.name, body.lat, body.lon)
    except Exception:
        raise HTTPException(409, f"El punto '{body.id}' ya existe")


@app.put("/points/{point_id}", dependencies=[Depends(require_admin)])
async def update_point_endpoint(point_id: str, body: PointUpdate):
    """Actualiza nombre y/o coordenadas de un punto existente."""
    updated = update_point(app.state.db, point_id, body.name, body.lat, body.lon)
    if updated is None:
        raise HTTPException(404, f"Punto '{point_id}' no encontrado")
    return updated


@app.delete("/points/{point_id}", dependencies=[Depends(require_admin)], status_code=204)
async def delete_point_endpoint(point_id: str):
    """Elimina un punto monitoreado."""
    if not delete_point(app.state.db, point_id):
        raise HTTPException(404, f"Punto '{point_id}' no encontrado")
