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
import numpy as np

from app.nowcast.engine import estimate_arrival
from app.processing.motion import multi_frame_motion_field
from app.processing.pixel_extract import reading_for_point
from app.schemas import WindSample
from app.sources.openmeteo import fetch_all_points, fetch_ensemble, fetch_forecast, fetch_wind_700_at, sample_trajectory_wind
from app.sources.radar_iam import RadarUnavailable, fetch_current_frame
from app.storage import (
    get_latest_reading,
    get_recent_frames,
    list_points,
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
    # EMA del campo de movimiento (multi-frame, suavizado temporal)
    motion_field_ema: object | None = None   # np.ndarray H×W×2 | None
    # Última ETA por punto para log de variabilidad
    last_eta: dict = field(default_factory=dict)  # point_id → (eta_min|None, method)


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

            for pt in list_points(conn):
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

            # Calcular el campo de movimiento multi-frame UNA VEZ por ciclo (no por punto).
            # Mantener EMA temporal para suavizar ciclo a ciclo.
            frames_for_motion = get_recent_frames(conn, 4)
            if state.last_bounds and len(frames_for_motion) >= 2:
                new_field = multi_frame_motion_field(frames_for_motion, state.last_bounds)
                if new_field is not None:
                    prev = state.motion_field_ema
                    if isinstance(prev, np.ndarray) and prev.shape == new_field.shape:
                        state.motion_field_ema = (0.5 * new_field + 0.5 * prev).astype(np.float32)
                    else:
                        state.motion_field_ema = new_field

            # Emitir una predicción por punto y registrarla para verificación posterior
            frames = get_recent_frames(conn, 2)
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as fc:
                for pt in list_points(conn):
                    try:
                        forecast = await fetch_forecast(
                            fc, pt["id"], pt["name"], pt["lat"], pt["lon"]
                        )
                        reading = get_latest_reading(conn, pt["id"])
                        # Ensemble prob (Fase 2) — degradación silenciosa si falla
                        ens_prob: float | None = None
                        try:
                            ens_prob = await fetch_ensemble(fc, pt["lat"], pt["lon"])
                        except Exception:
                            pass
                        result = estimate_arrival(
                            pt["id"], reading, forecast, frames, state.last_bounds,
                            motion_field=state.motion_field_ema,
                            ensemble_prob=ens_prob,
                        )
                        if result.cell_lat is not None:
                            try:
                                ew = await fetch_wind_700_at(fc, result.cell_lat, result.cell_lon)
                                result.wind_echo_bearing_deg = ew["toward_deg"]
                                result.wind_echo_speed_kmh = ew["speed_kmh"]
                            except Exception as exc:
                                log.debug("Viento en eco no disponible: %s", exc)
                            try:
                                traj = await sample_trajectory_wind(
                                    fc, result.cell_lat, result.cell_lon, pt["lat"], pt["lon"]
                                )
                                result.trajectory_wind = [WindSample(**s) for s in traj]
                            except Exception as exc:
                                log.debug("Trajectory wind no disponible: %s", exc)
                        save_prediction(conn, result)

                        # Log de variabilidad: mostrar delta respecto al ciclo anterior
                        pid = pt["id"]
                        prev_eta, prev_method = state.last_eta.get(pid, (None, None))
                        curr_eta = result.eta_minutes
                        curr_method = result.method
                        if prev_eta is not None and curr_eta is not None:
                            delta = curr_eta - prev_eta
                            log.info(
                                "ETA[%s] %s→%s min (Δ%+d) método=%s vel=%s rumbo=%s "
                                "trend=%s conf=%s",
                                pid, prev_eta, curr_eta, delta, curr_method,
                                result.cell_speed_kmh, result.cell_bearing_deg,
                                result.intensity_trend, result.confidence,
                            )
                        elif curr_eta is not None or prev_eta is not None:
                            log.info(
                                "ETA[%s] %s→%s min método=%s vel=%s trend=%s conf=%s",
                                pid, prev_eta, curr_eta, curr_method,
                                result.cell_speed_kmh, result.intensity_trend, result.confidence,
                            )
                        state.last_eta[pid] = (curr_eta, curr_method)

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


async def run_forecast_loop(conn: sqlite3.Connection) -> None:
    """Precalienta el cache de Open-Meteo una vez por hora para todos los puntos activos."""
    while True:
        try:
            points = list_points(conn)
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as client:
                forecasts = await fetch_all_points(client, points)
                log.info("Pronóstico Open-Meteo actualizado para %d puntos.", len(forecasts))
        except Exception as exc:
            log.warning("Error actualizando pronóstico: %s", exc)
        await asyncio.sleep(3600)
