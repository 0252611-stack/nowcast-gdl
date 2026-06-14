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
_LEGEND_SAMPLE_COUNT = 200

# The 16 tick-mark dBZ values from the IAM legend, left-to-right.
# The tick marks are EVENLY spaced in pixel coordinates (~26.5 px apart in a
# 399 px wide image), but the dBZ steps are NOT uniform: the first two
# intervals span 21.5 and 23 dBZ respectively, then 5 dBZ steps for the rest.
# Using a flat linear mapping produces errors up to 30 dBZ — piecewise
# interpolation between these ticks gives the correct calibration.
_TICK_DBZ = [
    -31.5, -10.0,  # Ruido
     13.0,  18.0,  23.0,  28.0,  33.0,  # Débil → Ligera
     38.0,  43.0,  48.0,  53.0,  # Moderada a fuerte
     58.0,  63.0,  68.0,  73.0,  78.0,  # Granizo
]  # Number of equidistant samples across the bar


def load_colormap(legend_path: str) -> dict[tuple[int, int, int], float]:
    """Lee backend/tests/fixtures/leyenda.png y construye un dict
    {color_rgb: dbz_value} calibrado contra la escala oficial.

    Rango dBZ: -31.5 (ruido) a 78.0 (granizo).

    Estrategia:
    1. Abre la imagen de la leyenda (399×93 px).
    2. Muestrea _LEGEND_SAMPLE_COUNT colores equidistantes a lo largo de la
       fila y=70 (centro de la barra de color).
    3. Asigna dBZ mediante interpolación PIECEWISE entre los 16 ticks de la
       leyenda IAM. Los ticks están igualmente espaciados en píxeles (~26.5 px),
       pero los valores dBZ NO son uniformes (primeros dos saltos: 21.5 y 23 dBZ;
       el resto: 5 dBZ cada uno). Una asignación lineal simple introduce errores
       de hasta 30 dBZ — este método es correcto.
    4. Devuelve el dict {(r,g,b): dbz}.
    """
    img = Image.open(legend_path).convert("RGBA")
    width, height = img.size

    y = min(_LEGEND_SAMPLE_ROW, height - 1)

    colormap: dict[tuple[int, int, int], float] = {}
    n_ticks = len(_TICK_DBZ)

    for i in range(_LEGEND_SAMPLE_COUNT):
        x = int(round(i / (_LEGEND_SAMPLE_COUNT - 1) * (width - 1)))
        r, g, b, a = img.getpixel((x, y))

        if a < 128:
            continue

        # Posición fraccional en el espacio de ticks [0, n_ticks-1]
        t = (x / (width - 1)) * (n_ticks - 1)
        idx = min(int(t), n_ticks - 2)
        frac = t - idx
        dbz = _TICK_DBZ[idx] + frac * (_TICK_DBZ[idx + 1] - _TICK_DBZ[idx])
        dbz = max(DBZ_MIN, min(DBZ_MAX, dbz))

        colormap[(r, g, b)] = dbz

    return colormap


def color_to_dbz(
    color: tuple[int, int, int],
    colormap: dict[tuple[int, int, int], float],
    lut: dict[tuple[int, int, int], float] | None = None,
) -> float:
    """Devuelve el valor dBZ más cercano al color RGB dado.

    Búsqueda en tres pasos para minimizar trabajo:
    1. Coincidencia exacta en el colormap (O(1)).
    2. Coincidencia en el LUT de llamadas anteriores (O(1)).
    3. Vecino más próximo en el colormap (O(N)), resultado cacheado en `lut`.

    `lut` es un dict externo (estado del llamador) que acumula colores ya resueltos.
    """
    if color in colormap:
        return colormap[color]
    if lut is not None and color in lut:
        return lut[color]

    r, g, b = color
    best_dbz = DBZ_MIN
    best_dist_sq = float("inf")

    for (cr, cg, cb), dbz in colormap.items():
        dist_sq = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_dbz = dbz

    if lut is not None:
        lut[color] = best_dbz
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
