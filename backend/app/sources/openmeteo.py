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
_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
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

# Cache para viento en coordenadas arbitrarias: key = (lat_1dec, lon_1dec, level, hour_bucket)
_wind_cache: dict[tuple, dict] = {}

# Cache para precipitación en coordenadas arbitrarias: key = (lat_1dec, lon_1dec, hour_bucket)
_precip_cache: dict[tuple, float] = {}

# Cache para ensemble por punto: key = (lat_1dec, lon_1dec, hour_bucket)
_ensemble_cache: dict[tuple, float] = {}

# ── Gestión de ciclo de vida del cache ───────────────────────────────────────
# La última parte de cada clave es siempre el hour_bucket. Cuando la hora cambia
# todas las entradas de horas anteriores son stale: las purgamos de una vez.
_last_purge_bucket: str = ""

# Contador de cache misses (requests reales a Open-Meteo) por hora — L5.
_miss_count: int = 0
_miss_hour: str = ""


def _purge_old_entries(cache: dict, current_bucket: str) -> int:
    """Elimina entradas cuya hora (último elemento de la clave) no coincide con current_bucket.
    Devuelve el número de entradas eliminadas."""
    stale = [k for k in cache if k[-1] != current_bucket]
    for k in stale:
        del cache[k]
    return len(stale)


def _maybe_purge_all(current_bucket: str) -> None:
    """Purga todos los caches cuando cambia la hora — evita crecimiento ilimitado."""
    global _last_purge_bucket
    if current_bucket == _last_purge_bucket:
        return
    total = (
        _purge_old_entries(_cache, current_bucket)
        + _purge_old_entries(_wind_cache, current_bucket)
        + _purge_old_entries(_precip_cache, current_bucket)
        + _purge_old_entries(_ensemble_cache, current_bucket)
    )
    if total:
        logger.debug("Cache Open-Meteo: purgadas %d entradas de hora anterior.", total)
    _last_purge_bucket = current_bucket


def _record_miss() -> None:
    """Registra un cache miss (request real a Open-Meteo) — para el contador L5."""
    global _miss_count, _miss_hour
    bucket = _hour_bucket()
    if bucket != _miss_hour:
        _miss_count = 0
        _miss_hour = bucket
    _miss_count += 1


def get_cache_stats() -> dict:
    """Devuelve el tamaño actual de cada cache y los requests reales de la hora actual.
    Útil para el log de observabilidad (L1 y L5)."""
    return {
        "forecast": len(_cache),
        "wind": len(_wind_cache),
        "precip": len(_precip_cache),
        "ensemble": len(_ensemble_cache),
        "total": len(_cache) + len(_wind_cache) + len(_precip_cache) + len(_ensemble_cache),
        "misses_this_hour": _miss_count,
    }


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
    _maybe_purge_all(bucket)  # A1: purgar entradas de horas anteriores

    async def _fetch_or_cache(point: dict) -> PointForecast:
        pid = point["id"]
        cache_key = (pid, bucket)
        if cache_key in _cache:
            logger.debug("Cache hit for point %s bucket %s", pid, bucket)
            return _cache[cache_key]

        _record_miss()  # L5: contabilizar request real
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


_VALID_LEVELS = {850, 700, 500}


async def fetch_wind_at(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    level: int = 700,
) -> dict:
    """Viento de capa `level` hPa (850/700/500) en coordenadas arbitrarias.

    Caché por (lat 0.1°, lon 0.1°, level, hora UTC).
    Devuelve {"toward_deg": float, "speed_kmh": float} donde toward_deg
    es la dirección HACIA la que sopla (convención "hacia", 0=N, 90=E).
    """
    if level not in _VALID_LEVELS:
        level = 700
    bucket = _hour_bucket()
    _maybe_purge_all(bucket)  # A1: purgar entradas de horas anteriores
    key = (round(lat, 1), round(lon, 1), level, bucket)
    if key in _wind_cache:
        return _wind_cache[key]
    _record_miss()  # L5: contabilizar request real

    speed_var = f"wind_speed_{level}hPa"
    dir_var   = f"wind_direction_{level}hPa"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": f"{speed_var},{dir_var}",
        "timezone": "America/Mexico_City",
        "forecast_hours": 1,
    }
    data = await _get_with_retry(client, _BASE_URL, params)
    speed = float(data["hourly"][speed_var][0])
    direction = float(data["hourly"][dir_var][0])
    result = {"toward_deg": (direction + 180) % 360, "speed_kmh": speed}
    _wind_cache[key] = result
    logger.debug("Wind %d hPa at (%.1f, %.1f): %.0f° %.1f km/h", level, lat, lon, result["toward_deg"], speed)
    return result


async def fetch_wind_700_at(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> dict:
    """Compatibilidad: fetch_wind_at con level=700."""
    return await fetch_wind_at(client, lat, lon, level=700)


async def sample_wind_grid(
    client: httpx.AsyncClient,
    bounds: dict[str, float],
    nx: int = 6,
    ny: int = 6,
    level: int = 700,
) -> list[dict]:
    """Viento de capa `level` hPa en una malla nx×ny sobre el área del radar.

    Malla por defecto 6×6 (antes 4×4) para interpolación IDW más precisa.
    Reutiliza fetch_wind_at (cacheada por hora, 0.1° y nivel).
    Devuelve lista de {"lat", "lon", "toward_deg", "speed_kmh"}.
    """
    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    lat_step = (north - south) / ny
    lon_step = (east - west) / nx

    coords = [
        (south + (j + 0.5) * lat_step, west + (i + 0.5) * lon_step)
        for j in range(ny)
        for i in range(nx)
    ]

    tasks = [fetch_wind_at(client, lat, lon, level=level) for lat, lon in coords]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    grid = []
    for (lat, lon), result in zip(coords, results):
        if isinstance(result, Exception):
            logger.debug("Wind grid error at (%.2f, %.2f): %s", lat, lon, result)
            continue
        grid.append({"lat": round(lat, 4), "lon": round(lon, 4), **result})
    return grid


async def sample_precip_grid(
    client: httpx.AsyncClient,
    bounds: dict[str, float],
    nx: int = 6,
    ny: int = 6,
) -> list[dict]:
    """Precipitación (mm/h) en una malla nx×ny sobre el área del radar.

    Usa el endpoint horario de Open-Meteo para la hora actual.
    Caché por (lat 0.1°, lon 0.1°, hora UTC) — sin requests redundantes.
    Devuelve lista de {"lat", "lon", "precip_mm"}.
    """
    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    lat_step = (north - south) / ny
    lon_step = (east - west) / nx

    coords = [
        (south + (j + 0.5) * lat_step, west + (i + 0.5) * lon_step)
        for j in range(ny)
        for i in range(nx)
    ]

    bucket = _hour_bucket()

    async def _fetch_one(lat: float, lon: float) -> dict | None:
        key = (round(lat, 1), round(lon, 1), bucket)
        if key in _precip_cache:
            return {"lat": round(lat, 4), "lon": round(lon, 4), "precip_mm": _precip_cache[key]}
        _record_miss()  # L5: contabilizar request real
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "precipitation",
                "timezone": "America/Mexico_City",
                "forecast_hours": 1,
            }
            data = await _get_with_retry(client, _BASE_URL, params)
            precip = float(data["hourly"]["precipitation"][0])
            _precip_cache[key] = precip
            return {"lat": round(lat, 4), "lon": round(lon, 4), "precip_mm": precip}
        except Exception as e:
            logger.debug("Precip grid error at (%.2f, %.2f): %s", lat, lon, e)
            return None

    results = await asyncio.gather(*[_fetch_one(lat, lon) for lat, lon in coords])
    return [r for r in results if r is not None]


async def fetch_minutely_15(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    n_steps: int = 8,
) -> list[dict]:
    """Precipitación cada 15 min en los próximos `n_steps` pasos (hasta 2 h).

    Devuelve lista de {"minutes_ahead": int, "precip_mm": float}.
    Caché implícita: misma llamada por hora (Open-Meteo regenera minutely_15
    solo 4×/h; pedir más seguido devuelve los mismos datos).
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "minutely_15": "precipitation",
        "timezone": "America/Mexico_City",
        "forecast_minutely_15": n_steps,
    }
    try:
        data = await _get_with_retry(client, _BASE_URL, params)
        precips = data.get("minutely_15", {}).get("precipitation", [])
        return [
            {"minutes_ahead": (i + 1) * 15, "precip_mm": float(v)}
            for i, v in enumerate(precips[:n_steps])
            if v is not None
        ]
    except Exception as e:
        logger.debug("minutely_15 no disponible: %s", e)
        return []


async def fetch_ensemble(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> float | None:
    """Probabilidad de precipitación derivada del spread del ensemble.

    Usa el endpoint ensemble-api.open-meteo.com. Deriva la probabilidad como
    la fracción de miembros con precipitación > 0.1 mm en la hora actual.
    Caché por (lat 0.1°, lon 0.1°, hora UTC). Devuelve None si falla.
    """
    key = (round(lat, 1), round(lon, 1), _hour_bucket())
    if key in _ensemble_cache:
        return _ensemble_cache[key]
    _record_miss()  # L5: contabilizar request real

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation",
        "timezone": "America/Mexico_City",
        "forecast_hours": 1,
        "models": "icon_seamless",   # ensemble con múltiples miembros disponibles
    }
    try:
        data = await _get_with_retry(client, _ENSEMBLE_URL, params)
        hourly = data.get("hourly", {})
        # Los miembros se devuelven como "precipitation_member01", "precipitation_member02", etc.
        # También puede devolver "precipitation" si el modelo es determinista — fallback.
        member_keys = [k for k in hourly if k.startswith("precipitation")]
        if not member_keys:
            return None
        values = [hourly[k][0] for k in member_keys if hourly[k]]
        if not values:
            return None
        prob = sum(1 for v in values if v is not None and float(v) > 0.1) / len(values)
        result = round(prob, 3)
        _ensemble_cache[key] = result
        logger.debug("Ensemble prob at (%.1f, %.1f): %.2f (%d members)", lat, lon, result, len(values))
        return result
    except Exception as e:
        logger.debug("Ensemble no disponible: %s", e)
        return None


async def sample_trajectory_wind(
    client: httpx.AsyncClient,
    echo_lat: float,
    echo_lon: float,
    point_lat: float,
    point_lon: float,
    n: int = 3,
    level: int = 700,
) -> list[dict]:
    """Viento de capa `level` hPa en N puntos equidistantes eco→punto.

    Usa fetch_wind_at (caché por hora, 0.1° y nivel). Devuelve lista de
    {"lat", "lon", "toward_deg", "speed_kmh"}. Omite fallos silenciosamente.
    """
    samples = []
    for i in range(1, n + 1):
        t = i / (n + 1)
        lat = echo_lat + t * (point_lat - echo_lat)
        lon = echo_lon + t * (point_lon - echo_lon)
        try:
            wind = await fetch_wind_at(client, lat, lon, level=level)
            samples.append({"lat": round(lat, 4), "lon": round(lon, 4), **wind})
        except Exception:
            pass
    return samples
