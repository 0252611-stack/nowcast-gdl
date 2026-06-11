"""Fallback visual de radar via RainViewer API pública."""

from __future__ import annotations

import logging
import math

import httpx

log = logging.getLogger(__name__)

_WEATHER_MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"


def _latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convierte lat/lon a índice de tile Web Mercator (OSM standard)."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


async def fetch_tile_url(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    zoom: int = 7,
    color: int = 4,
) -> str | None:
    """Devuelve la URL de un tile PNG de radar RainViewer centrado en (lat, lon).

    color=4 = esquema meteorológico estándar (azul→verde→amarillo→rojo).
    zoom=7 cubre ~156 km alrededor del punto (bueno para ver sistemas que se acercan).
    Devuelve None si la API no está disponible o no hay frames.
    """
    try:
        resp = await client.get(_WEATHER_MAPS_URL, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        past = data.get("radar", {}).get("past", [])
        if not past:
            return None
        latest = past[-1]
        host = data["host"]
        path = latest["path"]
        x, y = _latlon_to_tile(lat, lon, zoom)
        return f"{host}{path}/256/{zoom}/{x}/{y}/{color}/1_1.png"
    except Exception as exc:
        log.warning("RainViewer API no disponible: %s", exc)
        return None
