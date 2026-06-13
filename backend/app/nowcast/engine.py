"""Motor de nowcasting: combina radar + viento para estimar ETA de lluvia."""

from __future__ import annotations

import io
import logging
from datetime import datetime

from PIL import Image

from app import config
from app.processing.motion import compute_cell_motion, nearest_upstream_echo, project_cell
from app.schemas import NowcastResult, PointForecast, RadarReading

log = logging.getLogger(__name__)


def estimate_arrival(
    point_id: str,
    radar: RadarReading | None,
    forecast: PointForecast,
    frames: list[tuple[bytes, datetime]],
    bounds: dict[str, float] | None,
    horizon_minutes: int = 240,
) -> NowcastResult:
    """Estima si lloverá en el punto dentro de horizon_minutes.

    Métodos posibles en NowcastResult.method:
      radar_unavailable   — sin datos de radar
      radar_current       — ya está lloviendo en el punto
      insufficient_frames — menos de 2 frames o bounds no disponibles
      no_echo             — no hay eco en los frames (imagen transparente)
      no_motion           — hay eco pero no se detecta movimiento
      no_approaching_cell — no hay celda acercándose desde upstream
      advection           — ETA calculada por optical flow + viento 700 hPa
    """
    generated_at = datetime.now(tz=config.TZ_LOCAL)

    def _result(**kw) -> NowcastResult:
        defaults = dict(
            point_id=point_id,
            raining_now=False,
            eta_minutes=None,
            confidence=None,
            horizon_minutes=horizon_minutes,
            cell_speed_kmh=None,
            cell_bearing_deg=None,
            generated_at=generated_at,
            method="unknown",
        )
        defaults.update(kw)
        return NowcastResult(**defaults)

    # 1. Sin radar
    if radar is None:
        return _result(method="radar_unavailable")

    # 2. ¿Lloviendo ahora? Cualquier eco no-ruido (≥ DBZ_RAIN_THRESHOLD) cuenta como lluvia.
    raining_now = radar.dbz >= config.DBZ_RAIN_THRESHOLD
    if raining_now:
        conf = min(1.0, (radar.dbz - config.DBZ_RAIN_THRESHOLD) / (55.0 - config.DBZ_RAIN_THRESHOLD))
        return _result(raining_now=True, eta_minutes=0, confidence=round(conf, 3),
                       method="radar_current")

    # 3. ¿Suficientes frames para optical flow?
    if len(frames) < 2 or bounds is None:
        return _result(method="insufficient_frames")

    # 4. Optical flow entre los 2 frames más recientes (frames[0]=nuevo, frames[1]=viejo)
    newer_bytes, newer_time = frames[0]
    older_bytes, older_time = frames[1]
    interval_s = max(1.0, (newer_time - older_time).total_seconds())

    motion = compute_cell_motion(older_bytes, newer_bytes, interval_s, bounds)

    if motion["n_echo_pixels"] == 0:
        return _result(method="no_echo")

    if motion["speed_kmh"] < 0.1:
        return _result(method="no_motion")

    # 5. Buscar eco corriente arriba
    newer_image = Image.open(io.BytesIO(newer_bytes))
    nearest = nearest_upstream_echo(
        newer_image, bounds,
        forecast.lat, forecast.lon,
        motion["bearing_deg"],
    )

    if nearest is None:
        return _result(
            cell_speed_kmh=round(motion["speed_kmh"], 1),
            cell_bearing_deg=round(motion["bearing_deg"], 1),
            method="no_approaching_cell",
        )

    # 6. Proyectar ETA usando viento 700 hPa de la hora más próxima del pronóstico
    nearest_hour = forecast.hourly[0]
    projection = project_cell(
        forecast.lat, forecast.lon,
        nearest["distance_km"],
        motion["speed_kmh"],
        motion["bearing_deg"],
        nearest["bearing_cell_to_point_deg"],
        nearest_hour.wind_speed_700hPa_kmh,
        nearest_hour.wind_direction_700hPa_deg,
        horizon_minutes,
    )

    # ETA beyond horizon: eco existe pero llega demasiado tarde para el horizonte
    if projection["eta_minutes"] is None:
        return _result(
            cell_speed_kmh=round(motion["speed_kmh"], 1),
            cell_bearing_deg=round(motion["bearing_deg"], 1),
            method="no_approaching_cell",
        )

    return _result(
        eta_minutes=projection["eta_minutes"],
        confidence=projection["confidence"],
        cell_speed_kmh=round(motion["speed_kmh"], 1),
        cell_bearing_deg=round(motion["bearing_deg"], 1),
        cell_lat=round(nearest["cell_lat"], 6),
        cell_lon=round(nearest["cell_lon"], 6),
        bearing_cell_to_point_deg=round(nearest["bearing_cell_to_point_deg"], 1),
        method="advection",
    )
