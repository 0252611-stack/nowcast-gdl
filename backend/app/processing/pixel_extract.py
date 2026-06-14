"""Extracción de dBZ en un punto a partir de la imagen del radar."""

from __future__ import annotations

from datetime import datetime

from PIL import Image

from app.schemas import RadarCategory, RadarReading
from app.processing import colormap as colormap_module


def latlon_to_pixel(
    lat: float,
    lon: float,
    bounds: dict[str, float],
    img_width: int,
    img_height: int,
) -> tuple[int, int]:
    """Convierte coordenadas geográficas a pixel (x, y) usando interpolación
    LINEAL en EPSG:4326 (sin Mercator, sin pyproj).

    Fórmula: x = (lon - west) / (east - west) * width
             y = (north - lat) / (north - south) * height
    Error esperado < 2 px (≈300 m). Validar contra fixtures.
    """
    north = bounds["north"]
    south = bounds["south"]
    east = bounds["east"]
    west = bounds["west"]

    x = (lon - west) / (east - west) * img_width
    y = (north - lat) / (north - south) * img_height

    return int(round(x)), int(round(y))


def reading_for_point(
    point_id: str,
    lat: float,
    lon: float,
    bounds: dict[str, float],
    image: Image.Image,
    scan_time_utc: datetime,
    frame_age_seconds: float,
    neighborhood: int = 2,
) -> RadarReading:
    """Pipeline completo lat/lon → vecindad de píxeles → max dBZ → RadarReading.

    Lee una ventana de (2*neighborhood+1)² píxeles centrada en el punto y
    devuelve el máximo dBZ encontrado entre los píxeles con alpha > 0.
    Con neighborhood=2 la ventana es 5×5 (≈ 1-2 km según resolución del radar),
    lo que absorbe el error de cuantización lat/lon→px (< 2 px ≈ 300-600 m) y
    detecta lluvia que empieza en el borde del punto.

    Píxeles con alpha=0 se ignoran (son fondo sin eco); si toda la ventana es
    transparente devuelve dBZ=-31.5 (Ruido).

    Lanza ValueError si el centro del punto cae fuera del bounds de la imagen.
    """
    img_width, img_height = image.size
    cx, cy = latlon_to_pixel(lat, lon, bounds, img_width, img_height)

    if not (0 <= cx < img_width and 0 <= cy < img_height):
        raise ValueError(
            f"Pixel ({cx}, {cy}) fuera de los bounds "
            f"({img_width}×{img_height}) para lat={lat}, lon={lon}"
        )

    cmap = _get_colormap()
    rgba = image.convert("RGBA")

    best_dbz = colormap_module.DBZ_MIN
    best_x, best_y = cx, cy

    for dy in range(-neighborhood, neighborhood + 1):
        for dx in range(-neighborhood, neighborhood + 1):
            px, py = cx + dx, cy + dy
            if not (0 <= px < img_width and 0 <= py < img_height):
                continue
            r, g, b, a = rgba.getpixel((px, py))
            if a == 0:
                continue  # fondo transparente = sin eco
            dbz = colormap_module.color_to_dbz((r, g, b), cmap, _color_lut)
            if dbz > best_dbz:
                best_dbz = dbz
                best_x, best_y = px, py

    best_dbz = max(colormap_module.DBZ_MIN, min(colormap_module.DBZ_MAX, best_dbz))
    return RadarReading(
        point_id=point_id,
        dbz=best_dbz,
        category=colormap_module.dbz_to_category(best_dbz),
        scan_time_utc=scan_time_utc,
        frame_age_seconds=frame_age_seconds,
        pixel_x=best_x,
        pixel_y=best_y,
    )


# Module-level cache for the colormap so we don't reload leyenda.png every call
_colormap_cache: dict[tuple[int, int, int], float] | None = None
_legend_path: str | None = None
# LUT de colores ya resueltos por NN: evita repetir la búsqueda O(N) para colores
# no presentes en el colormap (antialiasing, compresión PNG). Se acumula entre frames.
_color_lut: dict[tuple[int, int, int], float] = {}


def _get_colormap() -> dict[tuple[int, int, int], float]:
    """Returns the cached colormap, loading it from the default legend path
    if not already loaded."""
    global _colormap_cache, _legend_path

    if _colormap_cache is None:
        import os

        # Try to find the legend relative to this file's location
        this_dir = os.path.dirname(os.path.abspath(__file__))
        # backend/app/processing/ → backend/tests/fixtures/leyenda.png
        candidate = os.path.join(
            this_dir, "..", "..", "tests", "fixtures", "leyenda.png"
        )
        candidate = os.path.normpath(candidate)

        if not os.path.exists(candidate):
            raise FileNotFoundError(
                f"leyenda.png not found at {candidate}. "
                "Call set_legend_path() before reading_for_point()."
            )

        _colormap_cache = colormap_module.load_colormap(candidate)
        _legend_path = candidate

    return _colormap_cache


def set_legend_path(path: str) -> None:
    """Override the legend path and clear the colormap cache.
    Useful for testing or custom deployments.
    """
    global _colormap_cache, _legend_path
    _colormap_cache = None
    _legend_path = path
    # Pre-load it
    _colormap_cache = colormap_module.load_colormap(path)
