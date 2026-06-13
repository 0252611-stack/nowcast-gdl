"""FastAPI application — endpoints REST del Nowcast GDL."""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field

from app import config
from app.nowcast.engine import estimate_arrival
from app.processing.motion import compute_cell_motion, find_context_echoes, find_echo_contours
from app.scheduler import RadarState, run_forecast_loop, run_radar_loop
from app.schemas import ContextEcho, WindSample
from app.sources.openmeteo import fetch_forecast, fetch_wind_700_at, sample_trajectory_wind
from app.sources.rainviewer import fetch_tile_url as fetch_rainviewer_url
from app.storage import (
    add_point,
    delete_point,
    get_latest_reading,
    get_predictions,
    get_recent_frames,
    get_skill_metrics,
    init_db,
    list_points,
    seed_points,
    update_point,
)

_RAINVIEWER_TTL = 300.0  # segundos entre llamadas a RainViewer

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = init_db(config.DB_PATH)
    seed_points(conn, config.POINTS)
    state = RadarState()
    radar_task = asyncio.create_task(run_radar_loop(conn, state))
    forecast_task = asyncio.create_task(run_forecast_loop(conn))
    app.state.db = conn
    app.state.radar_state = state
    app.state.rainviewer_url: str | None = None
    app.state.rainviewer_url_ts: float = 0.0
    # Cache de contornos: (frame_time, contours) — global, reutilizable entre puntos
    app.state.echo_contours_cache: tuple | None = None
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


@app.get("/metrics")
async def metrics():
    """Métricas de verificación del motor de nowcasting (POD, FAR, CSI, accuracy)."""
    return get_skill_metrics(app.state.db)


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
    echo_contours: list[list[list[float]]] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT}, timeout=10
    ) as client:
        try:
            frames = get_recent_frames(app.state.db, 2)
            forecast = await fetch_forecast(client, pt["id"], pt["name"], pt["lat"], pt["lon"])
            nowcast = estimate_arrival(point_id, reading, forecast, frames, state.last_bounds)

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

                    # Contornos: reusar si el frame no cambió (globalespor imagen)
                    cached = app.state.echo_contours_cache
                    if cached is not None and cached[0] == newer_time:
                        echo_contours = cached[1]
                    else:
                        echo_contours = find_echo_contours(img, state.last_bounds)
                        app.state.echo_contours_cache = (newer_time, echo_contours)
            except Exception as exc_ce:
                log.debug("Context echoes no disponibles: %s", exc_ce)

        except Exception as exc:
            log.warning("Nowcast engine falló para %s: %s", point_id, exc)

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
        "radar_bounds": state.last_bounds,
    }


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
