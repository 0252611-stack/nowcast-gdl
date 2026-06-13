"""Optical flow entre frames del radar y búsqueda de celda corriente arriba."""

from __future__ import annotations

import io
import math

import cv2
import numpy as np
from PIL import Image

from app import config
from app.processing.pixel_extract import _get_colormap

_MIN_ECHO_PIXELS = 10
_MAX_ECHO_SAMPLE = 5_000   # subsample for colormap lookup in nearest_upstream
_KM_PER_DEG_LAT = 111.32


def _km_per_deg_lon(bounds: dict[str, float]) -> float:
    lat_mid = (bounds["north"] + bounds["south"]) / 2
    return _KM_PER_DEG_LAT * math.cos(math.radians(lat_mid))


def compute_cell_motion(
    frame_older: bytes,
    frame_newer: bytes,
    interval_seconds: float,
    bounds: dict[str, float],
) -> dict:
    """Vector de movimiento del campo de eco entre dos frames PNG del radar.

    Usa OpenCV calcOpticalFlowFarneback sobre los píxeles con alpha>0.
    Devuelve {"speed_kmh": float, "bearing_deg": float, "n_echo_pixels": int}.
    Si no hay eco suficiente o el intervalo es ≤0 → speed_kmh=0.
    """
    img_older = Image.open(io.BytesIO(frame_older)).convert("RGBA")
    img_newer = Image.open(io.BytesIO(frame_newer)).convert("RGBA")

    arr_older = np.array(img_older)
    arr_newer = np.array(img_newer)

    alpha_older = arr_older[:, :, 3]
    alpha_newer = arr_newer[:, :, 3]

    echo_mask = alpha_older > 0
    n_echo_pixels = int(echo_mask.sum())

    _empty = {"speed_kmh": 0.0, "bearing_deg": 0.0, "n_echo_pixels": n_echo_pixels}

    if n_echo_pixels < _MIN_ECHO_PIXELS:
        return _empty

    gray_older = cv2.cvtColor(arr_older[:, :, :3], cv2.COLOR_RGB2GRAY)
    gray_newer = cv2.cvtColor(arr_newer[:, :, :3], cv2.COLOR_RGB2GRAY)

    # Fondo a 0 para que el flow se ancle en el eco
    prev = np.where(alpha_older > 0, gray_older, 0).astype(np.uint8)
    nxt = np.where(alpha_newer > 0, gray_newer, 0).astype(np.uint8)

    flow = cv2.calcOpticalFlowFarneback(
        prev, nxt, None,
        pyr_scale=0.5, levels=3, winsize=15,
        iterations=3, poly_n=5, poly_sigma=1.2,
        flags=0,
    )  # (H, W, 2): flow[y,x] = (dx,dy) en píxeles

    dx_mean = float(flow[echo_mask, 0].mean())
    dy_mean = float(flow[echo_mask, 1].mean())

    if abs(dx_mean) < 1e-6 and abs(dy_mean) < 1e-6:
        return _empty

    H, W = arr_older.shape[:2]
    deg_lon_per_px = (bounds["east"] - bounds["west"]) / W
    deg_lat_per_px = (bounds["north"] - bounds["south"]) / H

    dlon = dx_mean * deg_lon_per_px
    dlat = -dy_mean * deg_lat_per_px  # +dy en imagen = sur = -lat

    dlon_km = dlon * _km_per_deg_lon(bounds)
    dlat_km = dlat * _KM_PER_DEG_LAT

    dist_km = math.sqrt(dlon_km**2 + dlat_km**2)

    if interval_seconds <= 0 or dist_km < 1e-6:
        return _empty

    speed_kmh = dist_km / (interval_seconds / 3600)
    # Rumbo meteorológico HACIA donde se mueve (0=N, 90=E)
    bearing_deg = math.degrees(math.atan2(dlon_km, dlat_km)) % 360

    return {"speed_kmh": speed_kmh, "bearing_deg": bearing_deg, "n_echo_pixels": n_echo_pixels}


def nearest_upstream_echo(
    image: Image.Image,
    bounds: dict[str, float],
    point_lat: float,
    point_lon: float,
    motion_bearing_deg: float,
    max_range_km: float = 100.0,
) -> dict | None:
    """Eco más cercano corriente arriba del punto dado el rumbo del campo.

    'Corriente arriba' = dirección opuesta al rumbo (de donde viene el campo).
    Filtra por dBZ >= DBZ_THRESHOLD. Devuelve:
      {"distance_km", "cell_lat", "cell_lon", "bearing_cell_to_point_deg", "dbz"}
    o None si no hay eco upstream dentro de max_range_km.
    """
    arr = np.array(image.convert("RGBA"))
    H, W = arr.shape[:2]
    alpha = arr[:, :, 3]

    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return None

    # Subsample para eficiencia en la búsqueda del vecino más cercano en colormap
    if len(xs) > _MAX_ECHO_SAMPLE:
        idx = np.random.choice(len(xs), _MAX_ECHO_SAMPLE, replace=False)
        ys = ys[idx]
        xs = xs[idx]

    cmap = _get_colormap()
    cmap_colors = np.array(list(cmap.keys()), dtype=np.float32)   # (N_cmap, 3)
    cmap_dbzs = np.array(list(cmap.values()), dtype=np.float32)   # (N_cmap,)

    rgb = arr[ys, xs, :3].astype(np.float32)                      # (M, 3)
    diffs = rgb[:, np.newaxis, :] - cmap_colors[np.newaxis, :, :] # (M, N_cmap, 3)
    dists_sq = (diffs**2).sum(axis=2)                              # (M, N_cmap)
    best_idx = dists_sq.argmin(axis=1)                             # (M,)
    dbzs = cmap_dbzs[best_idx]

    strong = dbzs >= config.DBZ_THRESHOLD
    ys = ys[strong]
    xs = xs[strong]
    dbzs = dbzs[strong]

    if len(xs) == 0:
        return None

    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    lons = west + (xs / W) * (east - west)
    lats = north - (ys / H) * (north - south)

    km_lon = _km_per_deg_lon(bounds)
    dlat_km = (lats - point_lat) * _KM_PER_DEG_LAT
    dlon_km = (lons - point_lon) * km_lon
    dist_km = np.sqrt(dlat_km**2 + dlon_km**2)

    in_range = dist_km <= max_range_km
    if not np.any(in_range):
        return None

    dlat_km = dlat_km[in_range]
    dlon_km = dlon_km[in_range]
    dist_km = dist_km[in_range]
    lats = lats[in_range]
    lons = lons[in_range]
    dbzs = dbzs[in_range]

    # Rumbo DESDE el punto HACIA cada eco (meteorológico)
    bearings = np.degrees(np.arctan2(dlon_km, dlat_km)) % 360

    upstream_dir = (motion_bearing_deg + 180) % 360
    angle_diffs = (bearings - upstream_dir + 360) % 360
    angle_diffs = np.where(angle_diffs > 180, angle_diffs - 360, angle_diffs)
    upstream_mask = np.abs(angle_diffs) <= 90

    if not np.any(upstream_mask):
        return None

    dist_up = dist_km[upstream_mask]
    lats_up = lats[upstream_mask]
    lons_up = lons[upstream_mask]
    bearings_up = bearings[upstream_mask]
    dbzs_up = dbzs[upstream_mask]

    i = int(np.argmin(dist_up))

    return {
        "distance_km": float(dist_up[i]),
        "cell_lat": float(lats_up[i]),
        "cell_lon": float(lons_up[i]),
        "bearing_cell_to_point_deg": float((bearings_up[i] + 180) % 360),
        "dbz": float(dbzs_up[i]),
    }


def find_context_echoes(
    image: Image.Image,
    bounds: dict[str, float],
    motion_bearing_deg: float,
    motion_speed_kmh: float,
    grid_deg: float = 0.3,
    min_pixels: int = 30,
    max_clusters: int = 20,
) -> list[dict]:
    """Clusters de eco significativo para visualización de contexto en el mapa.

    Agrupa todos los píxeles con dBZ >= DBZ_THRESHOLD en una grilla de grid_deg°
    y devuelve el centroide de cada celda con suficientes píxeles.
    No filtra por dirección relativa a ningún punto — muestra todo el campo.

    Devuelve: [{"lat", "lon", "dbz", "bearing_deg", "speed_kmh"}, ...]
    """
    arr = np.array(image.convert("RGBA"))
    H, W = arr.shape[:2]
    alpha = arr[:, :, 3]

    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return []

    if len(xs) > _MAX_ECHO_SAMPLE:
        idx = np.random.choice(len(xs), _MAX_ECHO_SAMPLE, replace=False)
        ys = ys[idx]
        xs = xs[idx]

    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    lons = west + (xs / W) * (east - west)
    lats = north - (ys / H) * (north - south)

    cmap = _get_colormap()
    cmap_colors = np.array(list(cmap.keys()), dtype=np.float32)
    cmap_dbzs = np.array(list(cmap.values()), dtype=np.float32)
    rgb = arr[ys, xs, :3].astype(np.float32)
    diffs = rgb[:, np.newaxis, :] - cmap_colors[np.newaxis, :, :]
    dists_sq = (diffs ** 2).sum(axis=2)
    best_idx = dists_sq.argmin(axis=1)
    dbzs = cmap_dbzs[best_idx]

    strong = dbzs >= config.DBZ_THRESHOLD
    lats = lats[strong]
    lons = lons[strong]
    dbzs = dbzs[strong]

    if len(lats) == 0:
        return []

    # Bin into regular grid
    lat_bins = (np.floor(lats / grid_deg) * grid_deg).round(6)
    lon_bins = (np.floor(lons / grid_deg) * grid_deg).round(6)
    keys = list(zip(lat_bins.tolist(), lon_bins.tolist()))

    clusters: dict[tuple, dict] = {}
    for i, key in enumerate(keys):
        if key not in clusters:
            clusters[key] = {"sum_lat": 0.0, "sum_lon": 0.0, "sum_dbz": 0.0, "n": 0}
        c = clusters[key]
        c["sum_lat"] += float(lats[i])
        c["sum_lon"] += float(lons[i])
        c["sum_dbz"] += float(dbzs[i])
        c["n"] += 1

    result = []
    for c in clusters.values():
        if c["n"] < min_pixels:
            continue
        result.append({
            "lat": round(c["sum_lat"] / c["n"], 5),
            "lon": round(c["sum_lon"] / c["n"], 5),
            "dbz": round(c["sum_dbz"] / c["n"], 1),
            "bearing_deg": motion_bearing_deg,
            "speed_kmh": motion_speed_kmh,
        })

    result.sort(key=lambda x: x["dbz"], reverse=True)
    return result[:max_clusters]


def project_cell(
    point_lat: float,
    point_lon: float,
    cell_distance_km: float,
    motion_speed_kmh: float,
    motion_bearing_deg: float,
    bearing_cell_to_point_deg: float,
    wind_700_speed_kmh: float,
    wind_700_dir_deg: float,
    horizon_minutes: int,
) -> dict:
    """Proyecta la ETA de la celda al punto con cross-check contra viento 700 hPa.

    Devuelve {"eta_minutes": int|None, "confidence": float}.
    eta=None si velocidad≈0 o si ETA supera el horizonte.
    """
    if motion_speed_kmh < 0.1:
        return {"eta_minutes": None, "confidence": 0.0}

    eta_min = round(cell_distance_km / motion_speed_kmh * 60)

    if eta_min > horizon_minutes:
        return {"eta_minutes": None, "confidence": 0.0}

    def _cos_diff(a_deg: float, b_deg: float) -> float:
        d = (a_deg - b_deg + 360) % 360
        if d > 180:
            d = 360 - d
        return max(0.0, math.cos(math.radians(d)))

    # wind_700 da DE DÓNDE viene → se mueve HACIA (dir+180)
    wind_toward = (wind_700_dir_deg + 180) % 360
    conf_wind = _cos_diff(motion_bearing_deg, wind_toward)

    # ¿El campo se dirige hacia el punto?
    conf_dir = _cos_diff(motion_bearing_deg, bearing_cell_to_point_deg)

    confidence = round(0.5 * conf_wind + 0.5 * conf_dir, 3)

    return {"eta_minutes": int(eta_min), "confidence": confidence}
