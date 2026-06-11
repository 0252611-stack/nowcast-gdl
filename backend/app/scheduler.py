"""Loop de polling cada 90 s: descarga radar IAM, extrae dBZ, persiste."""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from PIL import Image

from app import config
from app.nowcast.engine import estimate_arrival
from app.processing.pixel_extract import reading_for_point
from app.sources.openmeteo import fetch_all_points, fetch_forecast
from app.sources.radar_iam import RadarUnavailable, fetch_current_frame
from app.storage import (
    get_latest_reading,
    get_recent_frames,
    purge_old_frames,
    purge_old_predictions,
    save_frame,
    save_prediction,
    save_reading,
    verify_predictions,
)

log = logging.getLogger(__name__)


@dataclass
class RadarState:
    available: bool = True
    consecutive_failures: int = 0
    last_kmz_url: str | None = None
    last_bounds: dict | None = None


def _scan_time_from_kmz_url(kmz_url: str) -> datetime:
    """Extrae fecha/hora del nombre del KMZ → datetime UTC.
    Formato real: MEXI_ZH_{YYYYMMDD}_{HHMMSS}.kmz
    """
    try:
        stem = kmz_url.rsplit("/", 1)[-1].removesuffix(".kmz")  # MEXI_ZH_20260611_192501
        parts = stem.split("_")
        dt_str = parts[-2] + parts[-1]             # "20260611192501"
        return datetime.strptime(dt_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


async def run_radar_loop(conn: sqlite3.Connection, state: RadarState) -> None:
    """Loop infinito: cada POLL_INTERVAL_SECONDS descarga un frame del IAM,
    extrae dBZ para cada punto de config.POINTS y persiste en SQLite."""
    while True:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as client:
                bounds, png_bytes, kmz_url = await fetch_current_frame(
                    client, state.last_kmz_url
                )

            scan_time = _scan_time_from_kmz_url(kmz_url)
            now_utc = datetime.now(timezone.utc)
            frame_age = (now_utc - scan_time).total_seconds()

            image = Image.open(io.BytesIO(png_bytes))

            save_frame(conn, kmz_url, scan_time, png_bytes)

            for pt in config.POINTS:
                try:
                    rdg = reading_for_point(
                        pt["id"], pt["lat"], pt["lon"],
                        bounds, image, scan_time, frame_age,
                    )
                    save_reading(conn, rdg)
                    log.debug("punto=%s dBZ=%.1f cat=%s", pt["id"], rdg.dbz, rdg.category.value)
                except Exception as exc:
                    log.warning("Error extrayendo punto %s: %s", pt["id"], exc)

            purge_old_frames(conn, config.RADAR_RETENTION_HOURS)

            # Emitir una predicción por punto y registrarla para verificación posterior
            frames = get_recent_frames(conn, 2)
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as fc:
                for pt in config.POINTS:
                    try:
                        forecast = await fetch_forecast(
                            fc, pt["id"], pt["name"], pt["lat"], pt["lon"]
                        )
                        reading = get_latest_reading(conn, pt["id"])
                        result = estimate_arrival(
                            pt["id"], reading, forecast, frames, state.last_bounds
                        )
                        save_prediction(conn, result)
                    except Exception as exc:
                        log.warning("Error emitiendo predicción para %s: %s", pt["id"], exc)

            # Verificar predicciones cuyo horizonte ya expiró
            verified = verify_predictions(conn, now_utc)
            if verified:
                log.info("Verificadas %d predicciones.", verified)

            purge_old_predictions(conn)

            state.consecutive_failures = 0
            state.available = True
            state.last_kmz_url = kmz_url
            state.last_bounds = bounds
            log.info("Frame radar OK: %s (age %.0f s)", kmz_url.rsplit("/", 1)[-1], frame_age)

        except RadarUnavailable as exc:
            # "same frame" es skip normal, no un fallo
            if "same frame" in str(exc).lower():
                log.debug("Radar: mismo frame, skip.")
            else:
                state.consecutive_failures += 1
                log.warning("Radar no disponible (%d/%d): %s",
                            state.consecutive_failures, config.RADAR_FAIL_THRESHOLD, exc)
                if state.consecutive_failures >= config.RADAR_FAIL_THRESHOLD:
                    state.available = False
                    log.error("Radar degradado a solo Open-Meteo tras %d fallos.",
                              config.RADAR_FAIL_THRESHOLD)
        except Exception as exc:
            state.consecutive_failures += 1
            log.warning("Error en ciclo radar (%d/%d): %s",
                        state.consecutive_failures, config.RADAR_FAIL_THRESHOLD, exc)
            if state.consecutive_failures >= config.RADAR_FAIL_THRESHOLD:
                state.available = False

        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


async def run_forecast_loop(points: list[dict]) -> None:
    """Precalienta el cache de Open-Meteo una vez por hora para todos los puntos."""
    while True:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as client:
                forecasts = await fetch_all_points(client, points)
                log.info("Pronóstico Open-Meteo actualizado para %d puntos.", len(forecasts))
        except Exception as exc:
            log.warning("Error actualizando pronóstico: %s", exc)
        await asyncio.sleep(3600)
