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
_UPSTREAM_CONE_DEG = 120   # semángulo del cono corriente arriba (era 90°)


def _km_per_deg_lon(bounds: dict[str, float]) -> float:
    lat_mid = (bounds["north"] + bounds["south"]) / 2
    return _KM_PER_DEG_LAT * math.cos(math.radians(lat_mid))


def dense_motion_field(
    frame_older: bytes,
    frame_newer: bytes,
    interval_seconds: float,
    bounds: dict[str, float],
) -> np.ndarray | None:
    """Campo denso de movimiento del eco entre dos frames PNG del radar.

    Usa cv2.calcOpticalFlowFarneback y convierte el flujo píxel→píxel a
    velocidad geográfica en **grados/minuto** por píxel.

    Devuelve un array H×W×2 con (v_lat, v_lon) en grados/min, o None si
    no hay eco suficiente o el intervalo es ≤0.
    """
    if interval_seconds <= 0:
        return None

    arr_older = np.array(Image.open(io.BytesIO(frame_older)).convert("RGBA"))
    arr_newer = np.array(Image.open(io.BytesIO(frame_newer)).convert("RGBA"))

    alpha_older = arr_older[:, :, 3]
    if int((alpha_older > 0).sum()) < _MIN_ECHO_PIXELS:
        return None

    H, W = arr_older.shape[:2]
    gray_older = cv2.cvtColor(arr_older[:, :, :3], cv2.COLOR_RGB2GRAY)
    gray_newer = cv2.cvtColor(arr_newer[:, :, :3], cv2.COLOR_RGB2GRAY)

    # Fondo a 0 para que el flow se ancle en el eco
    prev = np.where(alpha_older > 0, gray_older, 0).astype(np.uint8)
    nxt  = np.where(arr_newer[:, :, 3] > 0, gray_newer, 0).astype(np.uint8)

    flow = cv2.calcOpticalFlowFarneback(
        prev, nxt, None,
        pyr_scale=config.FLOW_PYR_SCALE,
        levels=config.FLOW_LEVELS,
        winsize=config.FLOW_WINSIZE,
        iterations=config.FLOW_ITERATIONS,
        poly_n=config.FLOW_POLY_N,
        poly_sigma=config.FLOW_POLY_SIGMA,
        flags=0,
    )  # H×W×2: flow[y,x] = (dx,dy) en píxeles/frame

    if config.FLOW_SMOOTH_KSIZE > 0:
        k = config.FLOW_SMOOTH_KSIZE
        flow[:, :, 0] = cv2.GaussianBlur(flow[:, :, 0], (k, k), 0)
        flow[:, :, 1] = cv2.GaussianBlur(flow[:, :, 1], (k, k), 0)

    # Convertir a grados/minuto por píxel
    minutes_per_frame = interval_seconds / 60.0
    deg_lon_per_px = (bounds["east"] - bounds["west"]) / W
    deg_lat_per_px = (bounds["north"] - bounds["south"]) / H

    v_lon = (flow[:, :, 0] * deg_lon_per_px / minutes_per_frame).astype(np.float32)
    v_lat = (-flow[:, :, 1] * deg_lat_per_px / minutes_per_frame).astype(np.float32)

    return np.stack([v_lat, v_lon], axis=2)  # H×W×2


def vector_to_speed_bearing(
    v_lat: float, v_lon: float, bounds: dict[str, float]
) -> tuple[float, float]:
    """Convierte un vector (v_lat, v_lon) en grados/min a (speed_kmh, bearing_deg).

    bearing_deg es el rumbo meteorológico (0=N, 90=E) hacia donde apunta el vector.
    speed_kmh se acota a config.CELL_MAX_SPEED_KMH: este es el único punto de
    conversión vector→velocidad usado tanto por el vector global de flujo óptico
    (field_to_global_vector, compute_cell_motion) como por el override de campo
    local en cell_tracking y advection (engine.py) — sin este tope, un pico de
    ruido del optical flow puede inyectar velocidades de cientos de km/h en
    cualquiera de esos caminos, igual que ocurría en el tracker antes de su fix.
    """
    dlon_km_per_min = v_lon * _km_per_deg_lon(bounds)
    dlat_km_per_min = v_lat * _KM_PER_DEG_LAT
    speed_kmh = math.sqrt(dlon_km_per_min**2 + dlat_km_per_min**2) * 60
    speed_kmh = min(speed_kmh, config.CELL_MAX_SPEED_KMH)
    bearing_deg = math.degrees(math.atan2(dlon_km_per_min, dlat_km_per_min)) % 360
    return speed_kmh, bearing_deg


def field_to_global_vector(
    field: np.ndarray,
    echo_mask: np.ndarray,
    bounds: dict[str, float],
) -> dict:
    """Promedia el campo denso H×W×2 sobre la máscara de eco → vector global.

    Devuelve {"speed_kmh", "bearing_deg", "n_echo_pixels"}. speed_kmh=0 si el
    promedio es ~0 o no hay píxeles de eco. Factoriza la lógica que antes vivía
    embebida en compute_cell_motion para poder reutilizarla con un campo ya
    calculado (p.ej. el EMA multi-frame del scheduler).
    """
    n_echo_pixels = int(echo_mask.sum())
    _empty = {"speed_kmh": 0.0, "bearing_deg": 0.0, "n_echo_pixels": n_echo_pixels}
    if n_echo_pixels == 0:
        return _empty

    v_lat_mean = float(field[echo_mask, 0].mean())
    v_lon_mean = float(field[echo_mask, 1].mean())

    if abs(v_lat_mean) < 1e-9 and abs(v_lon_mean) < 1e-9:
        return _empty

    speed_kmh, bearing_deg = vector_to_speed_bearing(v_lat_mean, v_lon_mean, bounds)
    if speed_kmh < 1e-6:
        return _empty

    return {"speed_kmh": speed_kmh, "bearing_deg": bearing_deg, "n_echo_pixels": n_echo_pixels}


def sample_field_at(
    field: np.ndarray,
    lat: float,
    lon: float,
    bounds: dict[str, float],
    win: int = 3,
) -> tuple[float, float]:
    """Muestrea el campo denso en (lat, lon) promediando una ventana win×win.

    Devuelve (v_lat, v_lon) en grados/min. La ventana promedia para dar robustez
    ante el ruido local del optical flow (mejora B: vector local de la celda, en
    vez del promedio global del campo).
    """
    H, W = field.shape[:2]
    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    px = int((lon - west) / (east - west) * W)
    py = int((north - lat) / (north - south) * H)
    half = max(0, win // 2)
    y0, y1 = max(0, py - half), min(H, py + half + 1)
    x0, x1 = max(0, px - half), min(W, px + half + 1)
    if y0 >= y1 or x0 >= x1:
        return (0.0, 0.0)
    patch = field[y0:y1, x0:x1]
    return (float(patch[:, :, 0].mean()), float(patch[:, :, 1].mean()))


def _point_in_ring(lat: float, lon: float, ring: list[list[float]]) -> bool:
    """Ray-casting: True si (lat, lon) está dentro del polígono cerrado `ring`."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def sample_ring_vectors(
    ring: list[list[float]],
    motion_field: np.ndarray,
    bounds: dict[str, float],
) -> list[dict]:
    """Muestrea el campo de movimiento en puntos interiores del anillo.

    Genera una grilla adaptativa al tamaño del eco (densidad proporcional al
    span en grados) y muestrea `motion_field` en los puntos dentro del polígono.
    Devuelve [{lat, lon, bearing_deg, speed_kmh}]; omite posiciones con
    speed_kmh < 0.5 (flujo prácticamente nulo).
    """
    if len(ring) < 3:
        return []

    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    span = max(max_lat - min_lat, max_lon - min_lon)

    # Densidad proporcional al span: ~1 celda cada 0.1°, mín 3, máx 8 por lado
    n_side = max(3, min(8, int(math.ceil(span / 0.1)) + 2))
    step_lat = (max_lat - min_lat) / n_side if max_lat > min_lat else span / n_side
    step_lon = (max_lon - min_lon) / n_side if max_lon > min_lon else span / n_side

    results = []
    for i in range(n_side):
        for j in range(n_side):
            lat = min_lat + (i + 0.5) * step_lat
            lon = min_lon + (j + 0.5) * step_lon
            if not _point_in_ring(lat, lon, ring):
                continue
            try:
                v_lat, v_lon = sample_field_at(motion_field, lat, lon, bounds)
                speed, bearing = vector_to_speed_bearing(v_lat, v_lon, bounds)
            except Exception:
                continue
            if speed >= 0.5:
                results.append({
                    "lat": round(lat, 5),
                    "lon": round(lon, 5),
                    "bearing_deg": round(bearing, 1),
                    "speed_kmh": round(speed, 1),
                })

    return results


def multi_frame_motion_field(
    frames: list[tuple[bytes, "datetime"]],
    bounds: dict[str, float],
    max_pairs: int = 3,
) -> np.ndarray | None:
    """Campo de movimiento promediado sobre varios pares consecutivos de frames.

    `frames` = lista (png_bytes, datetime) del más reciente al más viejo (igual
    que get_recent_frames). Calcula dense_motion_field para cada par consecutivo
    (older, newer) y los promedia con peso decreciente (0.6**i: el par más reciente
    pesa más). Devuelve H×W×2 (grados/min) o None si hay <2 frames o ningún par
    produce flujo.

    Reduce el ruido temporal del optical flow de un solo par (mejora C: estabilidad).
    """
    if len(frames) < 2:
        return None

    n_pairs = min(max_pairs, len(frames) - 1)
    acc: np.ndarray | None = None
    weight_sum = 0.0

    for i in range(n_pairs):
        newer_bytes, newer_time = frames[i]
        older_bytes, older_time = frames[i + 1]
        interval_s = max(1.0, (newer_time - older_time).total_seconds())
        field = dense_motion_field(older_bytes, newer_bytes, interval_s, bounds)
        if field is None:
            continue
        w = 0.6 ** i
        if acc is None:
            acc = np.zeros_like(field)
        acc += w * field
        weight_sum += w

    if acc is None or weight_sum == 0:
        return None
    return (acc / weight_sum).astype(np.float32)


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
    arr_older = np.array(Image.open(io.BytesIO(frame_older)).convert("RGBA"))
    alpha_older = arr_older[:, :, 3]
    echo_mask = alpha_older > 0
    n_echo_pixels = int(echo_mask.sum())

    if n_echo_pixels < _MIN_ECHO_PIXELS:
        return {"speed_kmh": 0.0, "bearing_deg": 0.0, "n_echo_pixels": n_echo_pixels}

    field = dense_motion_field(frame_older, frame_newer, interval_seconds, bounds)
    if field is None:
        return {"speed_kmh": 0.0, "bearing_deg": 0.0, "n_echo_pixels": n_echo_pixels}

    return field_to_global_vector(field, echo_mask, bounds)


def _upstream_candidates(
    image: Image.Image,
    bounds: dict[str, float],
    point_lat: float,
    point_lon: float,
    motion_bearing_deg: float,
    max_range_km: float = 100.0,
    cone_deg: float = _UPSTREAM_CONE_DEG,
) -> list[dict]:
    """Núcleo compartido: devuelve TODOS los ecos dentro del cono upstream, ordenados por distancia.

    Cada candidato: {"distance_km", "cell_lat", "cell_lon", "bearing_cell_to_point_deg", "dbz"}.
    Lista vacía si no hay eco upstream dentro de max_range_km.
    """
    arr = np.array(image.convert("RGBA"))
    H, W = arr.shape[:2]
    alpha = arr[:, :, 3]

    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return []

    # Submuestreo DETERMINISTA por stride: misma imagen → mismo resultado.
    # (antes np.random.choice hacía que la ETA saltara de ciclo a ciclo)
    if len(xs) > _MAX_ECHO_SAMPLE:
        idx = np.linspace(0, len(xs) - 1, _MAX_ECHO_SAMPLE).astype(int)
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
        return []

    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    lons = west + (xs / W) * (east - west)
    lats = north - (ys / H) * (north - south)

    km_lon = _km_per_deg_lon(bounds)
    dlat_km = (lats - point_lat) * _KM_PER_DEG_LAT
    dlon_km = (lons - point_lon) * km_lon
    dist_km = np.sqrt(dlat_km**2 + dlon_km**2)

    in_range = dist_km <= max_range_km
    if not np.any(in_range):
        return []

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
    upstream_mask = np.abs(angle_diffs) <= cone_deg

    if not np.any(upstream_mask):
        return []

    dist_up = dist_km[upstream_mask]
    lats_up = lats[upstream_mask]
    lons_up = lons[upstream_mask]
    bearings_up = bearings[upstream_mask]
    dbzs_up = dbzs[upstream_mask]

    order = np.argsort(dist_up)
    return [
        {
            "distance_km": float(dist_up[i]),
            "cell_lat": float(lats_up[i]),
            "cell_lon": float(lons_up[i]),
            "bearing_cell_to_point_deg": float((bearings_up[i] + 180) % 360),
            "dbz": float(dbzs_up[i]),
        }
        for i in order
    ]


def nearest_upstream_echo(
    image: Image.Image,
    bounds: dict[str, float],
    point_lat: float,
    point_lon: float,
    motion_bearing_deg: float,
    max_range_km: float = 100.0,
) -> dict | None:
    """Eco más cercano corriente arriba del punto dado el rumbo del campo.

    API pública intacta para retrocompatibilidad. Internamente usa _upstream_candidates
    con cono ampliado a ±120°. Para buscar múltiples candidatos (multicelular), usar
    find_upstream_echoes.
    """
    candidates = _upstream_candidates(
        image, bounds, point_lat, point_lon, motion_bearing_deg, max_range_km
    )
    return candidates[0] if candidates else None


def find_upstream_echoes(
    image: Image.Image,
    bounds: dict[str, float],
    point_lat: float,
    point_lon: float,
    motion_bearing_deg: float,
    max_range_km: float = 100.0,
    max_candidates: int = 5,
) -> list[dict]:
    """Hasta max_candidates ecos corriente arriba, ordenados por distancia (más cercano primero).

    Permite que el motor de nowcasting evalúe múltiples celdas y elija la que
    llega antes al punto monitoreado (B1: búsqueda multicelular).
    """
    return _upstream_candidates(
        image, bounds, point_lat, point_lon, motion_bearing_deg, max_range_km
    )[:max_candidates]


def find_context_echoes(
    image: Image.Image,
    bounds: dict[str, float],
    motion_bearing_deg: float,
    motion_speed_kmh: float,
    grid_deg: float = 0.3,
    min_pixels: int = 10,
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
        # Submuestreo DETERMINISTA por stride: misma imagen → mismo resultado.
        # (antes np.random.choice hacía que la ETA saltara de ciclo a ciclo)
        idx = np.linspace(0, len(xs) - 1, _MAX_ECHO_SAMPLE).astype(int)
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

    El ETA se calcula con la velocidad de ACERCAMIENTO (closing speed): la
    componente radial de la velocidad de la celda hacia el punto,
    speed * cos(Δ(rumbo, dirección_al_punto)). Una celda alejándose tiene
    closing speed negativa y una lateral ~0 — ninguna produce ETA, aunque su
    velocidad absoluta sea alta. Antes se usaba distancia/velocidad absoluta y
    la dirección solo bajaba la confianza, lo que generaba "lloverá en X min"
    con nubes que nunca iban a llegar (FAR ~50% medido en producción).

    Devuelve {"eta_minutes": int|None, "confidence": float}.
    eta=None si velocidad≈0, closing speed < MIN_CLOSING_SPEED_KMH, o si el
    ETA supera el horizonte.
    """
    if motion_speed_kmh < 0.1:
        return {"eta_minutes": None, "confidence": 0.0}

    def _angle_diff_deg(a_deg: float, b_deg: float) -> float:
        d = (a_deg - b_deg + 360) % 360
        return 360 - d if d > 180 else d

    def _cos_diff(a_deg: float, b_deg: float) -> float:
        return max(0.0, math.cos(math.radians(_angle_diff_deg(a_deg, b_deg))))

    # wind_700 da DE DÓNDE viene → se mueve HACIA (dir+180)
    wind_toward = (wind_700_dir_deg + 180) % 360
    # La alineación angular se pondera por la magnitud del viento: viento < 5 km/h
    # es prácticamente en calma y no aporta señal de dirección.
    wind_magnitude_factor = min(1.0, wind_700_speed_kmh / 20.0)
    conf_wind = _cos_diff(motion_bearing_deg, wind_toward) * wind_magnitude_factor

    # Coseno CRUDO (sin clamp) rumbo vs dirección al punto: negativo = se aleja.
    raw_cos_dir = math.cos(
        math.radians(_angle_diff_deg(motion_bearing_deg, bearing_cell_to_point_deg))
    )
    closing_speed_kmh = motion_speed_kmh * raw_cos_dir

    # ¿El campo se dirige hacia el punto? (versión clampeada para la confianza)
    conf_dir = max(0.0, raw_cos_dir)
    confidence = round(0.5 * conf_wind + 0.5 * conf_dir, 3)

    if closing_speed_kmh < config.MIN_CLOSING_SPEED_KMH:
        return {"eta_minutes": None, "confidence": confidence}

    eta_min = round(cell_distance_km / closing_speed_kmh * 60)

    if eta_min > horizon_minutes:
        return {"eta_minutes": None, "confidence": confidence}

    return {"eta_minutes": int(eta_min), "confidence": confidence}


def leading_edge_point(
    ring: list[list[float]],
    point_lat: float,
    point_lon: float,
    bounds: dict[str, float],
) -> tuple[float, float, float]:
    """Vértice del ring más cercano al punto monitoreado (borde de ataque).

    Devuelve (lat, lon, distance_km). Usado por el motor de nowcasting para
    obtener la distancia real desde el borde de la celda al punto (en lugar del
    centroide), reduciendo el error de ETA ~35-40 % según papers modernos.
    """
    best_lat, best_lon, best_dist = 0.0, 0.0, float("inf")
    for pt in ring:
        plat, plon = pt[0], pt[1]
        dlat_km = (plat - point_lat) * _KM_PER_DEG_LAT
        dlon_km = (plon - point_lon) * _km_per_deg_lon(bounds)
        dist = math.sqrt(dlat_km**2 + dlon_km**2)
        if dist < best_dist:
            best_dist = dist
            best_lat, best_lon = plat, plon
    return best_lat, best_lon, best_dist


def find_echo_contours(
    image: Image.Image,
    bounds: dict[str, float],
    min_dbz: float = 0.0,
    min_area_px: int = 4,
    smooth_kernel: int = 3,
    epsilon_px: float = 0.3,
    max_contours: int = 60,
) -> list[list[list[float]]]:
    """Traza el contorno EXTERNO de cada eco (dBZ >= min_dbz) en la imagen del
    radar y devuelve una lista de polígonos en coordenadas geográficas.

    Cada polígono es una lista de puntos [lat, lon] lista para pasar a Leaflet
    Polygon.

    Pipeline morfológico:
    1. MORPH_CLOSE (k=smooth_kernel): rellena huecos pequeños dentro del eco.
    2. MORPH_DILATE (k=3, 1 iter): engrosa todos los ecos 1 px para que incluso
       píxeles aislados formen un área de polígono detectable (sin dilate, los
       componentes de 1-3 px tienen contourArea=0 y se filtran).

    min_dbz: umbral mínimo de dBZ (0.0 = todo eco detectable).
    min_area_px: descarta contornos con área < N px² tras la dilatación.
    epsilon_px: tolerancia Douglas-Peucker (0.3 = alta precisión, pocos vértices eliminados).
    max_contours: límite de contornos devueltos (los N con mayor área).
    """
    from app.processing.colormap import DBZ_MIN

    arr = np.array(image.convert("RGBA"))
    H, W = arr.shape[:2]
    alpha = arr[:, :, 3]

    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return []

    # Colormap lookup vectorizado: asignar dBZ a cada píxel con eco
    cmap = _get_colormap()
    cmap_colors = np.array(list(cmap.keys()), dtype=np.float32)
    cmap_dbzs = np.array(list(cmap.values()), dtype=np.float32)

    rgb = arr[ys, xs, :3].astype(np.float32)
    diffs = rgb[:, np.newaxis, :] - cmap_colors[np.newaxis, :, :]
    dists_sq = (diffs ** 2).sum(axis=2)
    best_idx = dists_sq.argmin(axis=1)
    dbzs = cmap_dbzs[best_idx]

    # Construir máscara H×W
    dbz_grid = np.full((H, W), DBZ_MIN, dtype=np.float32)
    dbz_grid[ys, xs] = dbzs
    mask = ((dbz_grid >= min_dbz) & (alpha > 0)).astype(np.uint8) * 255

    # 1. MORPH_CLOSE: rellena huecos menores al kernel dentro de cada eco
    k = np.ones((smooth_kernel, smooth_kernel), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

    # 2. MORPH_DILATE: engrosa 1 px para que ecos de 1-3 px sean detectables.
    #    Ecos aislados pasan de área=0 (invisible) a ≥9 px² (contornable).
    k3 = np.ones((3, 3), np.uint8)
    mask = cv2.dilate(mask, k3, iterations=1)

    # Procesar cada componente conectado por separado: findContours sobre toda
    # la máscara devuelve un único contorno envolvente cuando los componentes
    # son dispersos (el background rodea todos los huecos).
    n_labels, labels = cv2.connectedComponents(mask)

    north = bounds["north"]
    south = bounds["south"]
    east = bounds["east"]
    west = bounds["west"]

    result: list[tuple[float, list[list[float]]]] = []
    for lbl in range(1, n_labels):
        comp_mask = (labels == lbl).astype(np.uint8) * 255
        contours_c, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours_c:
            continue
        c = max(contours_c, key=cv2.contourArea)
        area = cv2.contourArea(c)
        if area < min_area_px:
            continue
        simplified = cv2.approxPolyDP(c, epsilon_px, True)
        if len(simplified) < 3:
            continue
        ring: list[list[float]] = []
        for pt in simplified:
            x, y = float(pt[0][0]), float(pt[0][1])
            lat = round(north - (y / H) * (north - south), 5)
            lon = round(west + (x / W) * (east - west), 5)
            ring.append([lat, lon])
        result.append((area, ring))

    result.sort(key=lambda t: t[0], reverse=True)
    return [ring for _, ring in result[:max_contours]]
