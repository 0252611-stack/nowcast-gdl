"""FastAPI application — endpoints REST del Nowcast GDL."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.nowcast.engine import estimate_arrival
from app.scheduler import RadarState, run_forecast_loop, run_radar_loop
from app.sources.openmeteo import fetch_forecast
from app.storage import get_latest_reading, get_recent_frames, get_skill_metrics, init_db

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = init_db(config.DB_PATH)
    state = RadarState()
    radar_task = asyncio.create_task(run_radar_loop(conn, state))
    forecast_task = asyncio.create_task(run_forecast_loop(config.POINTS))
    app.state.db = conn
    app.state.radar_state = state
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
    allow_methods=["GET"],
    allow_headers=["*"],
)

_POINTS_BY_ID: dict[str, dict] = {p["id"]: p for p in config.POINTS}


@app.get("/metrics")
async def metrics():
    """Métricas de verificación del motor de nowcasting (POD, FAR, CSI, accuracy)."""
    return get_skill_metrics(app.state.db)


@app.get("/points")
async def list_points():
    """Lista de todos los puntos monitoreados."""
    return config.POINTS


@app.get("/points/{point_id}/forecast")
async def get_forecast(point_id: str):
    """Pronóstico Open-Meteo de las próximas 12 h para el punto."""
    pt = _POINTS_BY_ID.get(point_id)
    if pt is None:
        raise HTTPException(status_code=404, detail=f"Punto '{point_id}' no encontrado")
    async with httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT}, timeout=10
    ) as client:
        forecast = await fetch_forecast(client, pt["id"], pt["name"], pt["lat"], pt["lon"])
    return forecast


@app.get("/points/{point_id}/radar")
async def get_radar(point_id: str):
    """Última lectura de radar + disponibilidad + nowcast para el punto."""
    if point_id not in _POINTS_BY_ID:
        raise HTTPException(status_code=404, detail=f"Punto '{point_id}' no encontrado")
    pt = _POINTS_BY_ID[point_id]
    state: RadarState = app.state.radar_state
    reading = get_latest_reading(app.state.db, point_id)
    nowcast = None
    try:
        frames = get_recent_frames(app.state.db, 2)
        async with httpx.AsyncClient(
            headers={"User-Agent": config.USER_AGENT}, timeout=10
        ) as client:
            forecast = await fetch_forecast(client, pt["id"], pt["name"], pt["lat"], pt["lon"])
        nowcast = estimate_arrival(point_id, reading, forecast, frames, state.last_bounds)
    except Exception as exc:
        log.warning("Nowcast engine falló para %s: %s", point_id, exc)
    return {
        "radar": reading,
        "radar_available": state.available,
        "nowcast": nowcast,
    }
