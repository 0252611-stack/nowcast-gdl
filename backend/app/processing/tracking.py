"""Seguimiento de celdas de eco entre frames del radar (Capa 2 del motor híbrido).

Pipeline: detect_cells → update_tracks (greedy con gating, determinista).
El estado persistente (tracked_cells, next_cell_id) vive en RadarState del scheduler.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np
from PIL import Image

from app import config
from app.processing.pixel_extract import _get_colormap

log = logging.getLogger(__name__)

_KM_PER_DEG_LAT = 111.32


def _km_per_deg_lon(bounds: dict[str, float]) -> float:
    lat_mid = (bounds["north"] + bounds["south"]) / 2
    return _KM_PER_DEG_LAT * math.cos(math.radians(lat_mid))


def _geo_dist_km(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    bounds: dict[str, float],
) -> float:
    dlat_km = (lat1 - lat2) * _KM_PER_DEG_LAT
    dlon_km = (lon1 - lon2) * _km_per_deg_lon(bounds)
    return math.sqrt(dlat_km**2 + dlon_km**2)


@dataclass
class TrackedCell:
    """Celda de eco con identidad persistente entre ciclos del radar."""

    id: int
    lat: float
    lon: float
    area_px: int
    mean_dbz: float
    max_dbz: float
    ring: list[list[float]]
    velocity_kmh: float = 0.0
    bearing_deg: float = 0.0
    # Historial de centroides [(lat, lon, scan_time)] para trazar la trayectoria
    centroid_history: list = field(default_factory=list)
    # Historial de área para tendencia crecimiento/decaimiento por celda
    area_history: list = field(default_factory=list)
    age_frames: int = 1
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    # Aceleración (km/h/min) — solo diagnóstico; no se aplica a la ETA
    accel_kmh_per_min: float = 0.0
    # Linaje ligero (split/merge)
    split_from: int | None = None
    merged_ids: list = field(default_factory=list)
    # Contador interno de ciclos sin match (para purga)
    missed_frames: int = 0
    # Quality score 0–1: diagnóstico de calidad de detección y tracking (no altera ETA)
    quality: float = 0.0


def _component_to_cell(
    comp_mask: np.ndarray,
    dbz_grid: np.ndarray,
    bounds: dict[str, float],
    H: int,
    W: int,
) -> dict | None:
    """Convierte una máscara binaria de componente a dict de celda, o None si vacía."""
    comp_ys, comp_xs = np.where(comp_mask > 0)
    area = int(comp_ys.size)
    if area == 0:
        return None

    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]

    cx = float(comp_xs.mean())
    cy = float(comp_ys.mean())
    lat = float(north - (cy / H) * (north - south))
    lon = float(west + (cx / W) * (east - west))

    pixel_dbzs = dbz_grid[comp_ys, comp_xs]
    mean_dbz = float(pixel_dbzs.mean())
    max_dbz = float(pixel_dbzs.max())

    ring: list[list[float]] = []
    solidity: float = 1.0
    extent: float = 1.0
    contours_c, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours_c:
        c = max(contours_c, key=cv2.contourArea)
        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        if hull_area > 0:
            solidity = min(1.0, float(area) / hull_area)
        _, _, bw, bh = cv2.boundingRect(c)
        bbox_area = bw * bh
        if bbox_area > 0:
            extent = min(1.0, float(area) / bbox_area)
        simplified = cv2.approxPolyDP(c, 0.3, True)
        if len(simplified) >= 3:
            for pt in simplified:
                x, y = float(pt[0][0]), float(pt[0][1])
                ring.append([
                    round(float(north - (y / H) * (north - south)), 5),
                    round(float(west + (x / W) * (east - west)), 5),
                ])

    return {
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "area_px": area,
        "mean_dbz": round(mean_dbz, 1),
        "max_dbz": round(max_dbz, 1),
        "ring": ring,
        "solidity": round(solidity, 3),
        "extent": round(extent, 3),
    }


def detect_cells(
    image: Image.Image,
    bounds: dict[str, float],
    min_px: int | None = None,
) -> list[dict]:
    """Detecta celdas de eco significativo en la imagen del radar.

    Reutiliza el mismo pipeline morfológico de find_echo_contours (motion.py):
    colormap LUT → máscara dBZ ≥ DBZ_THRESHOLD → MORPH_CLOSE → dilate →
    connectedComponents. Umbral de área = CELL_MIN_PX (mayor que los contornos
    de visualización, para filtrar ruido y ecos muy pequeños).

    Componentes con área > CELL_MAX_PX se re-segmentan con el umbral convectivo
    CELL_SPLIT_DBZ (two-level threshold). Si no hay núcleos válidos el componente
    original se conserva íntegro (degradación con gracia).

    Devuelve lista de dicts: {lat, lon, area_px, mean_dbz, max_dbz, ring}.
    """
    from app.processing.colormap import DBZ_MIN

    if min_px is None:
        min_px = config.CELL_MIN_PX

    arr = np.array(image.convert("RGBA"))
    H, W = arr.shape[:2]
    alpha = arr[:, :, 3]

    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return []

    # Colormap LUT vectorizado: mismo patrón que _upstream_candidates y find_echo_contours
    cmap = _get_colormap()
    cmap_colors = np.array(list(cmap.keys()), dtype=np.float32)
    cmap_dbzs = np.array(list(cmap.values()), dtype=np.float32)

    rgb = arr[ys, xs, :3].astype(np.float32)
    diffs = rgb[:, np.newaxis, :] - cmap_colors[np.newaxis, :, :]
    dists_sq = (diffs ** 2).sum(axis=2)
    best_idx = dists_sq.argmin(axis=1)
    dbzs = cmap_dbzs[best_idx]

    # Construir malla dBZ H×W y máscara fuerte
    dbz_grid = np.full((H, W), DBZ_MIN, dtype=np.float32)
    dbz_grid[ys, xs] = dbzs
    mask = ((dbz_grid >= config.DBZ_THRESHOLD) & (alpha > 0)).astype(np.uint8) * 255

    # Suavizado morfológico (igual que find_echo_contours)
    k = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.dilate(mask, k, iterations=1)

    n_labels, labels = cv2.connectedComponents(mask)
    cells: list[dict] = []

    for lbl in range(1, n_labels):
        comp_mask = (labels == lbl).astype(np.uint8) * 255
        comp_ys, _ = np.where(comp_mask > 0)
        area = int(comp_ys.size)
        if area < min_px:
            continue

        # Two-level split: blobs grandes se re-segmentan con umbral convectivo
        if area > config.CELL_MAX_PX:
            core_mask = (
                (comp_mask > 0) & (dbz_grid >= config.CELL_SPLIT_DBZ)
            ).astype(np.uint8) * 255
            n_core, core_labels = cv2.connectedComponents(core_mask)
            sub_cells: list[dict] = []
            for core_lbl in range(1, n_core):
                sub_mask = (core_labels == core_lbl).astype(np.uint8) * 255
                sub_ys, _ = np.where(sub_mask > 0)
                if sub_ys.size < min_px:
                    continue
                cell = _component_to_cell(sub_mask, dbz_grid, bounds, H, W)
                if cell is not None:
                    sub_cells.append(cell)
            if sub_cells:
                cells.extend(sub_cells)
                continue  # componente grande partida con éxito; omitir original

        cell = _component_to_cell(comp_mask, dbz_grid, bounds, H, W)
        if cell is not None:
            cells.append(cell)

    return cells


def detection_mask(image: Image.Image, bounds: dict[str, float]) -> np.ndarray:
    """Genera la máscara binaria (uint8 0/255) que produce detect_cells.

    Aplica el mismo pipeline: colormap LUT → dBZ ≥ DBZ_THRESHOLD → MORPH_CLOSE
    → dilate. Devuelve array H×W uint8 con 255 en los píxeles que se convierten
    en celdas y 0 en el resto.  Útil para el endpoint /radar/cells/mask.png.
    """
    from app.processing.colormap import DBZ_MIN

    arr = np.array(image.convert("RGBA"))
    H, W = arr.shape[:2]
    alpha = arr[:, :, 3]

    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return np.zeros((H, W), dtype=np.uint8)

    cmap = _get_colormap()
    cmap_colors = np.array(list(cmap.keys()), dtype=np.float32)
    cmap_dbzs = np.array(list(cmap.values()), dtype=np.float32)

    rgb = arr[ys, xs, :3].astype(np.float32)
    diffs = rgb[:, np.newaxis, :] - cmap_colors[np.newaxis, :, :]
    dists_sq = (diffs ** 2).sum(axis=2)
    best_idx = dists_sq.argmin(axis=1)
    dbzs = cmap_dbzs[best_idx]

    dbz_grid = np.full((H, W), DBZ_MIN, dtype=np.float32)
    dbz_grid[ys, xs] = dbzs
    mask = ((dbz_grid >= config.DBZ_THRESHOLD) & (alpha > 0)).astype(np.uint8) * 255

    k = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.dilate(mask, k, iterations=1)
    return mask


def _predict_position(
    lat: float, lon: float,
    velocity_kmh: float, bearing_deg: float,
    interval_seconds: float,
    bounds: dict[str, float],
) -> tuple[float, float]:
    """Posición predicha del centroide dt segundos adelante."""
    if velocity_kmh < 0.1:
        return lat, lon
    dt_h = interval_seconds / 3600.0
    dist_km = velocity_kmh * dt_h
    brad = math.radians(bearing_deg)
    dlat = dist_km * math.cos(brad) / _KM_PER_DEG_LAT
    dlon = dist_km * math.sin(brad) / _km_per_deg_lon(bounds)
    return lat + dlat, lon + dlon


def _velocity_stability(centroid_history: list) -> float:
    """Estabilidad de velocidad y rumbo derivada del historial de centroides.

    Necesita ≥3 puntos; devuelve 0.0 (neutro) para historiales más cortos.
    No depende de bounds: usa deltas lat/lon como proxy de magnitud (ratio → cancela
    la escala). Circular variance de rumbos + CV de rapideces → score 0–1.
    """
    if len(centroid_history) < 3:
        return 0.0

    bearings_rad: list[float] = []
    speeds: list[float] = []
    for i in range(1, len(centroid_history)):
        dlat = centroid_history[i][0] - centroid_history[i - 1][0]
        dlon = centroid_history[i][1] - centroid_history[i - 1][1]
        mag = math.sqrt(dlat ** 2 + dlon ** 2)
        speeds.append(mag)
        bearings_rad.append(math.atan2(dlon, dlat))

    n = len(bearings_rad)
    sin_m = sum(math.sin(b) for b in bearings_rad) / n
    cos_m = sum(math.cos(b) for b in bearings_rad) / n
    bearing_score = math.sqrt(sin_m ** 2 + cos_m ** 2)  # resultant length [0,1]

    mean_s = sum(speeds) / n
    if mean_s < 1e-9:
        speed_score = 0.0  # celda estacionaria → sin señal de velocidad
    else:
        std_s = math.sqrt(sum((s - mean_s) ** 2 for s in speeds) / n)
        speed_score = max(0.0, 1.0 - std_s / mean_s)

    return 0.5 * bearing_score + 0.5 * speed_score


def _cell_quality(
    area_px: int,
    solidity: float,
    age_frames: int,
    area_history: list[int],
    missed_frames: int,
    centroid_history: list | None = None,
) -> float:
    """Quality score determinista 0–1 para una celda rastreada.

    Función de: tamaño normalizado, compacidad de forma, persistencia,
    estabilidad del área histórica y estabilidad de velocidad/rumbo.
    Penalización por ciclos sin match. Solo diagnóstico — no altera la ETA.
    """
    area_ref = max(1, config.CELL_QUALITY_AREA_REF)
    age_ref = max(1, config.CELL_QUALITY_AGE_REF)

    area_score = min(1.0, area_px / area_ref)
    solidity_score = max(0.0, min(1.0, solidity))
    age_score = min(1.0, (age_frames - 1) / age_ref)

    # Estabilidad de área: CV bajo → score alto
    if len(area_history) >= 2:
        mean_a = sum(area_history) / len(area_history)
        std_a = math.sqrt(sum((x - mean_a) ** 2 for x in area_history) / len(area_history))
        cv = std_a / max(1, mean_a)
        stability_score = max(0.0, 1.0 - cv)
    else:
        stability_score = 0.0

    velocity_score = _velocity_stability(centroid_history or [])

    raw = (
        config.CELL_QUALITY_W_AREA * area_score
        + config.CELL_QUALITY_W_SOLIDITY * solidity_score
        + config.CELL_QUALITY_W_AGE * age_score
        + config.CELL_QUALITY_W_STABILITY * stability_score
        + config.CELL_QUALITY_W_VELOCITY * velocity_score
    )
    penalty = config.CELL_QUALITY_MISSED_PENALTY * missed_frames
    return round(max(0.0, min(1.0, raw - penalty)), 3)


def update_tracks(
    prev_tracks: list[TrackedCell],
    detections: list[dict],
    scan_time: datetime,
    bounds: dict[str, float],
    interval_seconds: float = 90.0,
    next_id: int = 1,
) -> tuple[list[TrackedCell], int, dict]:
    """Matching greedy con gating entre tracks previos y nuevas detecciones.

    Determinista: ordena los pares válidos por costo (distancia + penalización
    de área); sin aleatoriedad. Devuelve (new_tracks, updated_next_id, diag)
    donde diag es un dict con métricas del ciclo de tracking para observabilidad.
    """
    max_km = config.CELL_MATCH_MAX_KM
    max_missed = config.CELL_MAX_MISSED
    history_len = config.CELL_HISTORY_LEN

    # Posiciones predichas para cada track previo
    predictions: dict[int, tuple[float, float]] = {
        t.id: _predict_position(t.lat, t.lon, t.velocity_kmh, t.bearing_deg, interval_seconds, bounds)
        for t in prev_tracks
    }

    # Construir lista de pares válidos (dentro del gate) con su costo; contar rechazos
    valid_pairs: list[tuple[float, int, int]] = []  # (cost, track_id, det_idx)
    gate_rejects = 0
    for t in prev_tracks:
        p_lat, p_lon = predictions[t.id]
        for d_idx, det in enumerate(detections):
            dist = _geo_dist_km(p_lat, p_lon, det["lat"], det["lon"], bounds)
            if dist > max_km:
                gate_rejects += 1
                continue
            area_pen = min(2.0, abs(math.log(max(1, det["area_px"]) / max(1, t.area_px))))
            cost = dist + 0.5 * area_pen
            valid_pairs.append((cost, t.id, d_idx))

    # Orden determinista: primero por costo, luego por track_id, luego por det_idx
    valid_pairs.sort(key=lambda x: (x[0], x[1], x[2]))

    assigned_tracks: set[int] = set()
    assigned_dets: set[int] = set()
    assignments: dict[int, int] = {}  # track_id → det_idx

    # Registrar todos los tracks que apuntaron a cada detección (para detectar merge)
    det_claimants: dict[int, list[int]] = {}
    for _, t_id, d_idx in valid_pairs:
        det_claimants.setdefault(d_idx, []).append(t_id)

    # Asignación greedy
    for _cost, t_id, d_idx in valid_pairs:
        if t_id in assigned_tracks or d_idx in assigned_dets:
            continue
        assignments[t_id] = d_idx
        assigned_tracks.add(t_id)
        assigned_dets.add(d_idx)

    # Costo medio de los pares efectivamente asignados (diagnóstico de calidad del matching)
    cost_by_pair = {(t_id, d_idx): cost for cost, t_id, d_idx in valid_pairs}
    matched_costs = [
        cost_by_pair[(t_id, d_idx)]
        for t_id, d_idx in assignments.items()
        if (t_id, d_idx) in cost_by_pair
    ]
    match_cost_mean: float | None = round(sum(matched_costs) / len(matched_costs), 2) if matched_costs else None

    track_by_id = {t.id: t for t in prev_tracks}
    new_tracks: list[TrackedCell] = []
    n_purged = 0
    n_merge = 0
    n_split = 0

    # Actualizar tracks con match
    for t in prev_tracks:
        if t.id in assignments:
            d_idx = assignments[t.id]
            det = detections[d_idx]

            # Velocidad a partir del desplazamiento real
            if interval_seconds > 0:
                dlat_km = (det["lat"] - t.lat) * _KM_PER_DEG_LAT
                dlon_km = (det["lon"] - t.lon) * _km_per_deg_lon(bounds)
                raw_speed = math.sqrt(dlat_km**2 + dlon_km**2) / (interval_seconds / 3600.0)
                raw_bearing = math.degrees(math.atan2(dlon_km, dlat_km)) % 360
            else:
                raw_speed = t.velocity_kmh
                raw_bearing = t.bearing_deg

            # EMA α=0.5 en componentes (sin/cos para el ángulo → circular correcto)
            new_speed = 0.5 * raw_speed + 0.5 * t.velocity_kmh
            prev_rad = math.radians(t.bearing_deg)
            raw_rad = math.radians(raw_bearing)
            sin_avg = 0.5 * math.sin(raw_rad) + 0.5 * math.sin(prev_rad)
            cos_avg = 0.5 * math.cos(raw_rad) + 0.5 * math.cos(prev_rad)
            new_bearing = math.degrees(math.atan2(sin_avg, cos_avg)) % 360

            accel = (raw_speed - t.velocity_kmh) / max(0.1, interval_seconds / 60.0)

            # Merge: otros tracks también apuntaban a esta detección
            all_claimants = det_claimants.get(d_idx, [])
            merged_ids = [oid for oid in all_claimants if oid != t.id]
            if merged_ids:
                n_merge += 1
                log.info(
                    "Merge: celda %d absorbe candidatos %s en %s", t.id, merged_ids, scan_time
                )

            _new_area_hist = (t.area_history + [det["area_px"]])[-history_len:]
            _new_age = t.age_frames + 1
            new_tracks.append(TrackedCell(
                id=t.id,
                lat=det["lat"],
                lon=det["lon"],
                area_px=det["area_px"],
                mean_dbz=det["mean_dbz"],
                max_dbz=det["max_dbz"],
                ring=det["ring"],
                velocity_kmh=round(new_speed, 1),
                bearing_deg=round(new_bearing, 1),
                centroid_history=(t.centroid_history + [(det["lat"], det["lon"], scan_time)])[-history_len:],
                area_history=_new_area_hist,
                age_frames=_new_age,
                first_seen=t.first_seen,
                last_seen=scan_time,
                accel_kmh_per_min=round(accel, 2),
                split_from=t.split_from,
                merged_ids=merged_ids,
                missed_frames=0,
                quality=_cell_quality(
                    det["area_px"],
                    det.get("solidity", 1.0),
                    _new_age,
                    _new_area_hist,
                    0,
                    centroid_history=(t.centroid_history + [(det["lat"], det["lon"], scan_time)])[-history_len:],
                ),
            ))
        else:
            # Sin match: incrementar contador de ausencias
            t.missed_frames += 1
            if t.missed_frames <= max_missed:
                t.last_seen = scan_time
                new_tracks.append(t)
            else:
                n_purged += 1
                log.debug("Celda %d purgada tras %d ciclos sin match.", t.id, t.missed_frames)

    # Nuevas detecciones sin track previo → nuevos tracks
    for d_idx, det in enumerate(detections):
        if d_idx in assigned_dets:
            continue

        # Detección de split: ¿algún track ya asignado está cerca?
        split_from: int | None = None
        for t in prev_tracks:
            if t.id not in assignments:
                continue
            p_lat, p_lon = predictions[t.id]
            dist = _geo_dist_km(p_lat, p_lon, det["lat"], det["lon"], bounds)
            if dist <= max_km:
                split_from = t.id
                n_split += 1
                log.info(
                    "Split: nueva celda %d cerca de celda %d en %s", next_id, t.id, scan_time
                )
                break

        new_tracks.append(TrackedCell(
            id=next_id,
            lat=det["lat"],
            lon=det["lon"],
            area_px=det["area_px"],
            mean_dbz=det["mean_dbz"],
            max_dbz=det["max_dbz"],
            ring=det["ring"],
            velocity_kmh=0.0,
            bearing_deg=0.0,
            centroid_history=[(det["lat"], det["lon"], scan_time)],
            area_history=[det["area_px"]],
            age_frames=1,
            first_seen=scan_time,
            last_seen=scan_time,
            split_from=split_from,
            merged_ids=[],
            missed_frames=0,
            quality=_cell_quality(
                det["area_px"],
                det.get("solidity", 1.0),
                1,
                [det["area_px"]],
                0,
                centroid_history=[(det["lat"], det["lon"], scan_time)],
            ),
        ))
        next_id += 1

    # Log de resumen por ciclo
    n_alive = len(new_tracks)
    n_new = sum(1 for t in new_tracks if t.age_frames == 1)
    n_continued = n_alive - n_new
    log.info(
        "Tracking: %d celdas vivas (%d continuadas, %d nuevas) en %s",
        n_alive, n_continued, n_new, scan_time,
    )

    diag: dict = {
        "n_alive": n_alive,
        "n_new": n_new,
        "n_continued": n_continued,
        "n_purged": n_purged,
        "n_split": n_split,
        "n_merge": n_merge,
        "gate_rejects": gate_rejects,
        "match_cost_mean": match_cost_mean,
    }
    return new_tracks, next_id, diag
