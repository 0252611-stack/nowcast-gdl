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


def extract_pixel_color(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    """Devuelve el color RGB del pixel (x, y) en la imagen del radar."""
    pixel = image.getpixel((x, y))
    # Return only RGB, ignoring alpha channel if present
    return (pixel[0], pixel[1], pixel[2])


def reading_for_point(
    point_id: str,
    lat: float,
    lon: float,
    bounds: dict[str, float],
    image: Image.Image,
    scan_time_utc: datetime,
    frame_age_seconds: float,
) -> RadarReading:
    """Pipeline completo lat/lon → pixel → color → dBZ → RadarReading.

    Llama a latlon_to_pixel, extract_pixel_color, y colormap.color_to_dbz.
    Devuelve un RadarReading validado. Lanza ValueError si el pixel cae
    fuera del bounds de la imagen.
    """
    img_width, img_height = image.size

    x, y = latlon_to_pixel(lat, lon, bounds, img_width, img_height)

    # Validate pixel is within image bounds
    if not (0 <= x < img_width and 0 <= y < img_height):
        raise ValueError(
            f"Pixel ({x}, {y}) out of image bounds "
            f"({img_width}x{img_height}) for lat={lat}, lon={lon}"
        )

    color = extract_pixel_color(image, x, y)

    # Load colormap from the legend (lazy import to avoid circular deps)
    # We use a module-level cached colormap if available
    cmap = _get_colormap()

    dbz = colormap_module.color_to_dbz(color, cmap)
    # Clamp dBZ to valid Pydantic range
    dbz = max(-31.5, min(78.0, dbz))

    category = colormap_module.dbz_to_category(dbz)

    return RadarReading(
        point_id=point_id,
        dbz=dbz,
        category=category,
        scan_time_utc=scan_time_utc,
        frame_age_seconds=frame_age_seconds,
        pixel_x=x,
        pixel_y=y,
    )


# Module-level cache for the colormap so we don't reload leyenda.png every call
_colormap_cache: dict[tuple[int, int, int], float] | None = None
_legend_path: str | None = None


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
