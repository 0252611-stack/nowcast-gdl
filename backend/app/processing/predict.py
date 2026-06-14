"""Advección semi-Lagrangiana del campo de eco para nowcasting temporal.

Algoritmo: persistencia Lagrangiana mejorada con corrección de viento 700 hPa.
- Motor primario: flujo óptico denso (Farneback) entre los dos frames más recientes.
- Corrección: malla 4×4 de viento 700 hPa interpolada con IDW; pesa más donde
  no hay eco (zona hacia donde avanzará la tormenta).
- Advección: backward mapping con cv2.remap (semi-Lagrangiano).

Referencia: Lagrangian persistence / Pulkkinen et al. (pysteps, GMD 2019).
"""

from __future__ import annotations

import io
import math

import cv2
import numpy as np
from PIL import Image

from app.processing.motion import (
    dense_motion_field,
    find_context_echoes,
    find_echo_contours,
)

_KM_PER_DEG_LAT = 111.32
_DEFAULT_STEPS_MIN = [15, 30, 45, 60, 75, 90, 105, 120]
_MAX_TRAJECTORIES = 10


# ---------------------------------------------------------------------------
# Interpolación de viento (IDW — sin scipy)
# ---------------------------------------------------------------------------

def _wind_grid_to_field(
    wind_grid: list[dict],
    H: int,
    W: int,
    bounds: dict[str, float],
) -> np.ndarray:
    """Interpola la malla de viento a resolución completa H×W usando IDW.

    Devuelve campo H×W×2 (v_lat, v_lon) en grados/minuto.
    """
    if not wind_grid:
        return np.zeros((H, W, 2), dtype=np.float32)

    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    lat_mid = (north + south) / 2
    km_per_deg_lon = _KM_PER_DEG_LAT * math.cos(math.radians(lat_mid))

    toward_rads = np.array([math.radians(p["toward_deg"]) for p in wind_grid], dtype=np.float32)
    speeds = np.array([p["speed_kmh"] for p in wind_grid], dtype=np.float32)
    # Convertir velocidad km/h y rumbo "hacia" a deg/min en cada componente
    vlat_g = speeds * np.cos(toward_rads) / (_KM_PER_DEG_LAT * 60.0)
    vlon_g = speeds * np.sin(toward_rads) / (km_per_deg_lon * 60.0)

    # Posición de cada punto de malla en coordenadas de imagen (float px)
    lats_g = np.array([p["lat"] for p in wind_grid], dtype=np.float32)
    lons_g = np.array([p["lon"] for p in wind_grid], dtype=np.float32)
    xs_g = (lons_g - west) / (east - west) * W
    ys_g = (north - lats_g) / (north - south) * H

    # Malla de píxeles H×W
    Y_px, X_px = np.mgrid[0:H, 0:W]

    # Distancia al cuadrado H×W×N (N = nº de puntos de malla)
    dx = X_px[:, :, np.newaxis] - xs_g[np.newaxis, np.newaxis, :]
    dy = Y_px[:, :, np.newaxis] - ys_g[np.newaxis, np.newaxis, :]
    dist_sq = dx**2 + dy**2 + 1.0  # +1 para evitar div/0

    # IDW (potencia 2): normalizar pesos
    w = 1.0 / dist_sq                           # H×W×N
    w_sum = w.sum(axis=2, keepdims=True)         # H×W×1
    w = w / w_sum                                # H×W×N

    vlat_f = (w * vlat_g[np.newaxis, np.newaxis, :]).sum(axis=2)
    vlon_f = (w * vlon_g[np.newaxis, np.newaxis, :]).sum(axis=2)

    return np.stack([vlat_f, vlon_f], axis=2).astype(np.float32)


# ---------------------------------------------------------------------------
# Mezcla de campo radar + viento
# ---------------------------------------------------------------------------

def blend_motion_field(
    radar_field: np.ndarray,
    echo_alpha: np.ndarray,
    wind_grid: list[dict],
    bounds: dict[str, float],
) -> np.ndarray:
    """Combina el campo de flujo óptico con el viento en malla.

    Donde hay eco (alpha>0), confía en el radar; fuera del eco, usa el viento.
    La transición es suave (Gaussian blur sobre la máscara de eco).

    radar_field : H×W×2 (v_lat, v_lon) en grados/min
    echo_alpha  : H×W uint8 (canal alpha del frame actual)
    Devuelve    : H×W×2 (v_lat, v_lon) en grados/min
    """
    H, W = radar_field.shape[:2]
    wind_field = _wind_grid_to_field(wind_grid, H, W, bounds)

    # Peso: 1 donde hay eco, 0 donde no; suavizado para transición gradual
    echo_weight = (echo_alpha > 0).astype(np.float32)
    echo_weight = cv2.GaussianBlur(echo_weight, (31, 31), sigmaX=10, sigmaY=10)
    echo_weight = np.clip(echo_weight, 0.0, 1.0)
    w = echo_weight[:, :, np.newaxis]  # H×W×1

    blended = w * radar_field + (1.0 - w) * wind_field

    # Suavizado final para eliminar discontinuidades
    blended[:, :, 0] = cv2.GaussianBlur(blended[:, :, 0], (15, 15), sigmaX=5, sigmaY=5)
    blended[:, :, 1] = cv2.GaussianBlur(blended[:, :, 1], (15, 15), sigmaX=5, sigmaY=5)

    return blended.astype(np.float32)


# ---------------------------------------------------------------------------
# Advección
# ---------------------------------------------------------------------------

def advect_image(
    rgba: np.ndarray,
    motion_field: np.ndarray,
    minutes: float,
    bounds: dict[str, float],
) -> Image.Image:
    """Advección semi-Lagrangiana hacia adelante `minutes` minutos.

    Usa backward mapping (cv2.remap): para cada píxel de destino calcula
    de dónde venía el eco hace `minutes` minutos y muestrea ahí.

    rgba         : H×W×4 uint8
    motion_field : H×W×2 (v_lat, v_lon) en grados/min
    Devuelve     : PIL.Image RGBA
    """
    H, W = rgba.shape[:2]
    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]

    deg_lon_per_px = (east - west) / W
    deg_lat_per_px = (north - south) / H

    # Desplazamiento en píxeles para `minutes` minutos
    d_lon = motion_field[:, :, 1] * minutes           # deg
    d_lat = motion_field[:, :, 0] * minutes           # deg
    d_x = (d_lon / deg_lon_per_px).astype(np.float32)
    d_y = (-d_lat / deg_lat_per_px).astype(np.float32)

    Y, X = np.mgrid[0:H, 0:W]
    map_x = (X.astype(np.float32) - d_x)
    map_y = (Y.astype(np.float32) - d_y)

    channels = []
    for c in range(4):
        remapped = cv2.remap(
            rgba[:, :, c].astype(np.float32),
            map_x, map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        channels.append(np.clip(remapped, 0, 255).astype(np.uint8))

    return Image.fromarray(np.stack(channels, axis=2), "RGBA")


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def build_prediction(
    frame_older: bytes,
    frame_newer: bytes,
    interval_seconds: float,
    bounds: dict[str, float],
    wind_grid: list[dict],
    steps_min: list[int] | None = None,
) -> dict:
    """Construye la predicción advectiva del campo de eco.

    Devuelve::
        {
            "frames_png": [bytes, ...],       # PNG RGBA por paso temporal
            "steps": [
                {"minutes": int, "contours": [[[lat, lon], ...], ...]}
            ],
            "trajectories": [                 # polilíneas [t0, t1, ..., tN]
                [[lat, lon], ...], ...
            ],
            "bounds": {...},
            "method": str,
        }
    """
    if steps_min is None:
        steps_min = _DEFAULT_STEPS_MIN

    img_newer = Image.open(io.BytesIO(frame_newer)).convert("RGBA")
    arr_newer = np.array(img_newer)
    alpha_newer = arr_newer[:, :, 3]
    H, W = arr_newer.shape[:2]
    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]

    # Campo denso de optical flow (grados/min) — None si sin eco suficiente
    radar_field = dense_motion_field(frame_older, frame_newer, interval_seconds, bounds)

    if radar_field is None:
        method = "static_persistence"
        radar_field = np.zeros((H, W, 2), dtype=np.float32)
    else:
        method = "semi_lagrangian"

    # Campo combinado radar + viento
    motion = blend_motion_field(radar_field, alpha_newer, wind_grid, bounds)

    # ------------------------------------------------------------------
    # Trayectorias: proyectar centroide de cada eco fuerte en el tiempo
    # ------------------------------------------------------------------
    context_echoes = find_context_echoes(img_newer, bounds, 0.0, 0.0)
    trajectories: list[list[list[float]]] = []

    for echo in context_echoes[:_MAX_TRAJECTORIES]:
        lat0, lon0 = echo["lat"], echo["lon"]
        px_x = int((lon0 - west) / (east - west) * W)
        px_y = int((north - lat0) / (north - south) * H)
        px_x = max(0, min(W - 1, px_x))
        px_y = max(0, min(H - 1, px_y))

        vlat = float(motion[px_y, px_x, 0])   # deg/min
        vlon = float(motion[px_y, px_x, 1])   # deg/min

        traj: list[list[float]] = [[round(lat0, 5), round(lon0, 5)]]
        for t in steps_min:
            traj.append([round(lat0 + vlat * t, 5), round(lon0 + vlon * t, 5)])
        trajectories.append(traj)

    # ------------------------------------------------------------------
    # Advectar el frame y calcular contornos por paso
    # ------------------------------------------------------------------
    frames_png: list[bytes] = []
    steps_data: list[dict] = []

    for minutes in steps_min:
        adv_img = advect_image(arr_newer, motion, minutes, bounds)
        contours = find_echo_contours(adv_img, bounds)

        buf = io.BytesIO()
        adv_img.save(buf, format="PNG")
        frames_png.append(buf.getvalue())
        steps_data.append({"minutes": minutes, "contours": contours})

    return {
        "frames_png": frames_png,
        "steps": steps_data,
        "trajectories": trajectories,
        "bounds": bounds,
        "method": method,
    }
