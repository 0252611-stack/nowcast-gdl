"""Cliente async para la API Open-Meteo (pronóstico hora a hora)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.schemas import HourlyForecast, PointForecast

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.open-meteo.com/v1/forecast"
_HOURLY_VARS = (
    "precipitation,"
    "precipitation_probability,"
    "wind_speed_10m,"
    "wind_direction_10m,"
    "temperature_2m,"
    "wind_speed_700hPa,"
    "wind_direction_700hPa"
)
_TZ_LOCAL = ZoneInfo("America/Mexico_City")
_MAX_HOURS = 12

# Cache module-level: key = (point_id, hour_bucket) → PointForecast
# hour_bucket is an ISO string truncated to the hour, e.g. "2026-06-10T14"
_cache: dict[tuple[str, str], PointForecast] = {}


def _hour_bucket() -> str:
    """Return current UTC time truncated to the hour as a string key."""
    now = datetime.now(tz=timezone.utc)
    return now.strftime("%Y-%m-%dT%H")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _get_with_retry(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """GET request with up to 3 retries and exponential backoff."""
    response = await client.get(url, params=params, timeout=10.0)
    response.raise_for_status()
    return response.json()


async def fetch_forecast(
    client: httpx.AsyncClient,
    point_id: str,
    name: str,
    lat: float,
    lon: float,
) -> PointForecast:
    """Consulta Open-Meteo y devuelve un PointForecast con las próximas 12 horas.

    Variables solicitadas: precipitation, precipitation_probability,
    wind_speed_10m, wind_direction_10m, temperature_2m,
    wind_speed_700hPa, wind_direction_700hPa.
    timezone=America/Mexico_City — los timestamps de respuesta son hora local.
    Toda la respuesta se valida contra HourlyForecast antes de devolverse.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": _HOURLY_VARS,
        "timezone": "America/Mexico_City",
        "forecast_hours": _MAX_HOURS,
    }

    data = await _get_with_retry(client, _BASE_URL, params)

    hourly = data["hourly"]
    times = hourly["time"]
    precipitations = hourly["precipitation"]
    precip_probs = hourly["precipitation_probability"]
    temperatures = hourly["temperature_2m"]
    wind_speed_10m = hourly["wind_speed_10m"]
    wind_dir_10m = hourly["wind_direction_10m"]
    wind_speed_700 = hourly["wind_speed_700hPa"]
    wind_dir_700 = hourly["wind_direction_700hPa"]

    n = min(len(times), _MAX_HOURS)
    if n < 1:
        raise ValueError(f"Open-Meteo returned 0 hourly entries for point {point_id}")

    hourly_forecasts: list[HourlyForecast] = []
    for i in range(n):
        # Parse the naive ISO string and attach Mexico City tzinfo
        naive_dt = datetime.fromisoformat(times[i])
        aware_dt = naive_dt.replace(tzinfo=_TZ_LOCAL)

        hf = HourlyForecast(
            time=aware_dt,
            precipitation_mm=precipitations[i],
            precipitation_probability=precip_probs[i],
            temperature_c=temperatures[i],
            wind_speed_10m_kmh=wind_speed_10m[i],
            wind_direction_10m_deg=wind_dir_10m[i],
            wind_speed_700hPa_kmh=wind_speed_700[i],
            wind_direction_700hPa_deg=wind_dir_700[i],
        )
        hourly_forecasts.append(hf)

    return PointForecast(
        point_id=point_id,
        name=name,
        lat=lat,
        lon=lon,
        generated_at=datetime.now(tz=timezone.utc),
        timezone="America/Mexico_City",
        hourly=hourly_forecasts,
    )


async def fetch_all_points(
    client: httpx.AsyncClient,
    points: list[dict],
) -> list[PointForecast]:
    """Ejecuta fetch_forecast en paralelo para todos los puntos de `points`.

    Respeta el límite de Open-Meteo: máximo 1 request por punto por hora
    (cache obligatorio por clave (point_id, hora_truncada)).
    Objetivo: < 200 calls/día para 7 puntos.
    """
    bucket = _hour_bucket()

    async def _fetch_or_cache(point: dict) -> PointForecast:
        pid = point["id"]
        cache_key = (pid, bucket)
        if cache_key in _cache:
            logger.debug("Cache hit for point %s bucket %s", pid, bucket)
            return _cache[cache_key]

        result = await fetch_forecast(
            client,
            point_id=pid,
            name=point["name"],
            lat=point["lat"],
            lon=point["lon"],
        )
        _cache[cache_key] = result
        return result

    return list(await asyncio.gather(*(_fetch_or_cache(p) for p in points)))
