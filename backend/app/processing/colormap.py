"""Calibración de la escala de color dBZ del radar IAM."""

from __future__ import annotations

import math

from PIL import Image

from app.schemas import RadarCategory

# dBZ range from the official IAM legend
DBZ_MIN = -31.5
DBZ_MAX = 78.0

# The legend image (leyenda.png) is a horizontal color bar spanning the full
# width of the image. We sample row y=70 (middle of the color bar) to get
# the color→dBZ mapping.
_LEGEND_SAMPLE_ROW = 70
_LEGEND_SAMPLE_COUNT = 200  # Number of equidistant samples across the bar


def load_colormap(legend_path: str) -> dict[tuple[int, int, int], float]:
    """Lee backend/tests/fixtures/leyenda.png y construye un dict
    {color_rgb: dbz_value} calibrado contra la escala oficial.

    Rango dBZ: -31.5 (ruido) a 78.0 (granizo).

    Estrategia:
    1. Abre la imagen de la leyenda.
    2. Muestrea _LEGEND_SAMPLE_COUNT colores equidistantes a lo largo de la
       fila central de la barra de color.
    3. Asigna dBZ linealmente desde DBZ_MIN hasta DBZ_MAX.
    4. Devuelve el dict {(r,g,b): dbz}.
    """
    img = Image.open(legend_path).convert("RGBA")
    width, height = img.size

    # Use the sampling row (clamped to image height)
    y = min(_LEGEND_SAMPLE_ROW, height - 1)

    colormap: dict[tuple[int, int, int], float] = {}

    dbz_range = DBZ_MAX - DBZ_MIN

    for i in range(_LEGEND_SAMPLE_COUNT):
        # x goes from 0 to width-1 linearly
        x = int(round(i / (_LEGEND_SAMPLE_COUNT - 1) * (width - 1)))
        r, g, b, a = img.getpixel((x, y))

        if a < 128:
            # Transparent pixel — skip
            continue

        dbz = DBZ_MIN + (x / (width - 1)) * dbz_range
        # Clamp to valid range
        dbz = max(DBZ_MIN, min(DBZ_MAX, dbz))

        colormap[(r, g, b)] = dbz

    return colormap


def color_to_dbz(
    color: tuple[int, int, int],
    colormap: dict[tuple[int, int, int], float],
) -> float:
    """Devuelve el valor dBZ más cercano al color RGB dado, buscando el
    vecino más próximo en el espacio de color del colormap calibrado.

    Distancia euclidiana: sqrt((r1-r2)^2 + (g1-g2)^2 + (b1-b2)^2).
    """
    r, g, b = color

    best_dbz = DBZ_MIN
    best_dist_sq = float("inf")

    for (cr, cg, cb), dbz in colormap.items():
        dist_sq = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_dbz = dbz

    return best_dbz


def dbz_to_category(dbz: float) -> RadarCategory:
    """Clasifica un valor dBZ en la categoría de la leyenda oficial IAM:
    Ruido | Débil | Ligera | Moderada a fuerte | Granizo.

    Umbrales basados en la leyenda oficial:
      < -10       → Ruido
      -10 .. 18   → Débil
       18 .. 35   → Ligera
       35 .. 55   → Moderada a fuerte
      > 55        → Granizo
    """
    if dbz < -10.0:
        return RadarCategory.RUIDO
    elif dbz < 18.0:
        return RadarCategory.DEBIL
    elif dbz < 35.0:
        return RadarCategory.LIGERA
    elif dbz < 55.0:
        return RadarCategory.MODERADA_FUERTE
    else:
        return RadarCategory.GRANIZO
