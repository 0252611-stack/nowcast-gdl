"""Advección semi-Lagrangiana del campo de eco para nowcasting temporal.

Algoritmo: persistencia Lagrangiana mejorada con corrección de viento + blend NWP.
- Motor primario: flujo óptico denso multi-frame (Farneback, EMA temporal).
- Corrección: malla 6×6 de viento de capa interpolada con IDW.
- Advección: backward mapping con cv2.remap (semi-Lagrangiano).
- Blend NWP: malla de precipitación Open-Meteo → dBZ (Marshall-Palmer) mezclada
  con el campo advectado según horizonte (radar domina 0-60 min, NWP 90-120 min).

Referencia: Lagrangian persistence / Pulkkinen et al. (pysteps, GMD 2019).
           INCA blend seamless (Haiden et al. 2011).
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
    multi_frame_motion_field,
)

_KM_PER_DEG_LAT = 111.32
_DEFAULT_STEPS_MIN = list(range(5, 121, 5))   # 24 pasos: +5…+120 min
_MAX_TRAJECTORIES = 10


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


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

# ---------------------------------------------------------------------------
# Blend NWP (Fase 2): Marshall-Palmer + compositing seamless por horizonte
# ---------------------------------------------------------------------------

def precip_to_dbz(precip_mmh: float) -> float:
    """Convierte precipitación (mm/h) a reflectividad (dBZ) via Marshall-Palmer.

    Z = 200 * R^1.6  →  dBZ = 10 * log10(Z)
    Devuelve -31.5 (ruido) para R≤0.
    """
    if precip_mmh <= 0.0:
        return -31.5
    z = 200.0 * (precip_mmh ** 1.6)
    return max(-31.5, min(78.0, 10.0 * math.log10(max(z, 1e-6))))


def _precip_grid_to_dbz_field(
    precip_grid: list[dict],
    H: int,
    W: int,
    bounds: dict[str, float],
) -> np.ndarray:
    """Interpola la malla de precipitación a resolución H×W (IDW) en dBZ.

    Cada celda de la malla tiene {"lat", "lon", "precip_mm"}. Devuelve H×W
    con el campo dBZ NWP interpolado (float32).
    """
    if not precip_grid:
        return np.full((H, W), -31.5, dtype=np.float32)

    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]

    dbzs_g = np.array([precip_to_dbz(p["precip_mm"]) for p in precip_grid], dtype=np.float32)
    lats_g = np.array([p["lat"] for p in precip_grid], dtype=np.float32)
    lons_g = np.array([p["lon"] for p in precip_grid], dtype=np.float32)
    xs_g = (lons_g - west) / (east - west) * W
    ys_g = (north - lats_g) / (north - south) * H

    Y_px, X_px = np.mgrid[0:H, 0:W]
    dx = X_px[:, :, np.newaxis] - xs_g[np.newaxis, np.newaxis, :]
    dy = Y_px[:, :, np.newaxis] - ys_g[np.newaxis, np.newaxis, :]
    dist_sq = dx**2 + dy**2 + 1.0
    w = 1.0 / dist_sq
    w = w / w.sum(axis=2, keepdims=True)
    return (w * dbzs_g[np.newaxis, np.newaxis, :]).sum(axis=2).astype(np.float32)


def _dbz_to_rgba(dbz_field: np.ndarray, ref_image: np.ndarray) -> np.ndarray:
    """Convierte un campo dBZ H×W a RGBA usando el rango de la imagen de referencia.

    Usa la imagen de referencia para derivar el rango de color (percentil 5-95
    de píxeles con eco), luego mapea dBZ → luminancia gris con alpha proporcional
    a la intensidad. Solo pixeles con dBZ > umbral de lluvia tienen alpha > 0.
    """
    from app import config
    H, W = dbz_field.shape

    # Normalizar dBZ al rango [0, 255]
    dbz_min, dbz_max = -31.5, 78.0
    norm = np.clip((dbz_field - dbz_min) / (dbz_max - dbz_min), 0.0, 1.0)

    # Umbral: alpha=0 si dBZ < DBZ_RAIN_THRESHOLD; alpha proporcional a intensidad
    alpha = np.where(
        dbz_field >= config.DBZ_RAIN_THRESHOLD,
        (norm * 200).astype(np.uint8),
        0,
    ).astype(np.uint8)

    # Color: degradé amarillo-naranja-rojo para lluvia
    r = np.clip(norm * 255, 0, 255).astype(np.uint8)
    g = np.clip((1 - norm) * 200, 0, 255).astype(np.uint8)
    b = np.zeros((H, W), dtype=np.uint8)

    return np.stack([r, g, b, alpha], axis=2)


def blend_radar_nwp(
    advected_rgba: np.ndarray,
    nwp_dbz: np.ndarray,
    minutes: float,
    max_minutes: float = 120.0,
) -> np.ndarray:
    """Mezcla seamless del frame advectado (radar) con el campo NWP (dBZ).

    Peso radar α decrece con el horizonte (INCA-like):
      α = clamp(1 - minutes/max_minutes * 0.7 + 0.3, 0.3, 1.0)
    Esto hace que radar domine en 0-60 min y NWP aporte en 90-120 min.

    advected_rgba: H×W×4 uint8 (canal alpha = presencia de eco)
    nwp_dbz: H×W float32 (dBZ del modelo NWP interpolado)
    Devuelve: H×W×4 uint8 mezclado.
    """
    alpha_radar = 1.0 - min(0.7, (minutes / max(max_minutes, 1.0)) * 0.7)
    alpha_radar = _clamp(alpha_radar + 0.3, 0.3, 1.0)
    alpha_nwp = 1.0 - alpha_radar

    H, W = nwp_dbz.shape
    nwp_rgba = _dbz_to_rgba(nwp_dbz, advected_rgba)

    # Blend lineal en el canal alpha (presencia de eco)
    radar_alpha = advected_rgba[:, :, 3].astype(np.float32)
    nwp_alpha   = nwp_rgba[:, :, 3].astype(np.float32)
    blended_alpha = np.clip(alpha_radar * radar_alpha + alpha_nwp * nwp_alpha, 0, 255).astype(np.uint8)

    # Para los canales RGB: usar radar donde hay eco de radar, NWP donde no
    has_radar = radar_alpha > 0
    r = np.where(has_radar, advected_rgba[:, :, 0], nwp_rgba[:, :, 0]).astype(np.uint8)
    g = np.where(has_radar, advected_rgba[:, :, 1], nwp_rgba[:, :, 1]).astype(np.uint8)
    b = np.where(has_radar, advected_rgba[:, :, 2], nwp_rgba[:, :, 2]).astype(np.uint8)

    return np.stack([r, g, b, blended_alpha], axis=2)


def build_prediction(
    frame_older: bytes,
    frame_newer: bytes,
    interval_seconds: float,
    bounds: dict[str, float],
    wind_grid: list[dict],
    steps_min: list[int] | None = None,
    frames_recent: list[tuple[bytes, "datetime"]] | None = None,
    intensity_trend: float = 0.0,
    precip_grid: list[dict] | None = None,
) -> dict:
    """Construye la predicción advectiva del campo de eco.

    `frames_recent` (opcional): lista (png_bytes, datetime) más reciente primero.
    Si se provee, usa multi_frame_motion_field para mayor estabilidad temporal en
    vez del único par frame_older/frame_newer. Si no, cae al par clásico.

    `intensity_trend` (opcional, [-1,1]): tendencia de área del eco. Se usa para
    atenuar (decay) gradualmente los ecos que se disipan en los pasos largos.

    `precip_grid` (opcional): lista de {"lat", "lon", "precip_mm"} de Open-Meteo.
    Si se provee, mezcla el campo advectado con el campo NWP (Marshall-Palmer→dBZ)
    usando un blend seamless por horizonte (INCA-like).

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

    # Campo denso de optical flow (grados/min): multi-frame si se pasan frames_recent,
    # si no, par clásico (retrocompatibilidad).
    radar_field: np.ndarray | None = None
    if frames_recent is not None and len(frames_recent) >= 2:
        radar_field = multi_frame_motion_field(frames_recent, bounds)
    if radar_field is None:
        radar_field = dense_motion_field(frame_older, frame_newer, interval_seconds, bounds)

    if radar_field is None:
        method = "static_persistence"
        radar_field = np.zeros((H, W, 2), dtype=np.float32)
    else:
        method = "semi_lagrangian"

    # Campo combinado radar + viento
    motion = blend_motion_field(radar_field, alpha_newer, wind_grid, bounds)

    # Campo dBZ del modelo NWP (pre-interpolado, reutilizado en todos los pasos)
    nwp_dbz: np.ndarray | None = None
    if precip_grid:
        nwp_dbz = _precip_grid_to_dbz_field(precip_grid, H, W, bounds)

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
    # Advectar el frame y calcular contornos por paso.
    # D: aplicar factor de decay al canal alpha para atenuar ecos que se
    # disipan (trend < 0). Nunca amplifica artificialmente (min_decay=0.6).
    # ------------------------------------------------------------------
    frames_png: list[bytes] = []
    steps_data: list[dict] = []
    max_minutes = max(steps_min) if steps_min else 120

    for minutes in steps_min:
        adv_img = advect_image(arr_newer, motion, minutes, bounds)

        adv_arr = np.array(adv_img)

        # D: decay del alpha cuando el eco se disipa (trend < 0)
        if intensity_trend < 0:
            decay = _clamp(1 + 0.5 * intensity_trend * (minutes / max_minutes), 0.6, 1.0)
            adv_arr[:, :, 3] = np.clip(
                adv_arr[:, :, 3].astype(np.float32) * decay, 0, 255
            ).astype(np.uint8)

        # Fase 2: blend seamless radar + NWP si hay campo de precipitación
        if nwp_dbz is not None:
            adv_arr = blend_radar_nwp(adv_arr, nwp_dbz, float(minutes), float(max_minutes))

        adv_img = Image.fromarray(adv_arr, "RGBA")
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
