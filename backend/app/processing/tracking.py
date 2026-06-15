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

    north, south, east, west = bounds["north"], bounds["south"], bounds["east"], bounds["west"]

    n_labels, labels = cv2.connectedComponents(mask)
    cells: list[dict] = []

    for lbl in range(1, n_labels):
        comp_mask = (labels == lbl).astype(np.uint8) * 255
        comp_ys, comp_xs = np.where(comp_mask > 0)
        area = int(comp_ys.size)
        if area < min_px:
            continue

        # Centroide geográfico
        cx = float(comp_xs.mean())
        cy = float(comp_ys.mean())
        lat = float(north - (cy / H) * (north - south))
        lon = float(west + (cx / W) * (east - west))

        # Estadísticas dBZ
        pixel_dbzs = dbz_grid[comp_ys, comp_xs]
        mean_dbz = float(pixel_dbzs.mean())
        max_dbz = float(pixel_dbzs.max())

        # Contorno geográfico del componente
        ring: list[list[float]] = []
        contours_c, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours_c:
            c = max(contours_c, key=cv2.contourArea)
            simplified = cv2.approxPolyDP(c, 0.3, True)
            if len(simplified) >= 3:
                for pt in simplified:
                    x, y = float(pt[0][0]), float(pt[0][1])
                    ring.append([
                        round(float(north - (y / H) * (north - south)), 5),
                        round(float(west + (x / W) * (east - west)), 5),
                    ])

        cells.append({
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "area_px": area,
            "mean_dbz": round(mean_dbz, 1),
            "max_dbz": round(max_dbz, 1),
            "ring": ring,
        })

    return cells


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


def update_tracks(
    prev_tracks: list[TrackedCell],
    detections: list[dict],
    scan_time: datetime,
    bounds: dict[str, float],
    interval_seconds: float = 90.0,
    next_id: int = 1,
) -> tuple[list[TrackedCell], int]:
    """Matching greedy con gating entre tracks previos y nuevas detecciones.

    Determinista: ordena los pares válidos por costo (distancia + penalización
    de área); sin aleatoriedad. Devuelve (new_tracks, updated_next_id).
    """
    max_km = config.CELL_MATCH_MAX_KM
    max_missed = config.CELL_MAX_MISSED
    history_len = config.CELL_HISTORY_LEN

    # Posiciones predichas para cada track previo
    predictions: dict[int, tuple[float, float]] = {
        t.id: _predict_position(t.lat, t.lon, t.velocity_kmh, t.bearing_deg, interval_seconds, bounds)
        for t in prev_tracks
    }

    # Construir lista de pares válidos (dentro del gate) con su costo
    valid_pairs: list[tuple[float, int, int]] = []  # (cost, track_id, det_idx)
    for t in prev_tracks:
        p_lat, p_lon = predictions[t.id]
        for d_idx, det in enumerate(detections):
            dist = _geo_dist_km(p_lat, p_lon, det["lat"], det["lon"], bounds)
            if dist > max_km:
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

    track_by_id = {t.id: t for t in prev_tracks}
    new_tracks: list[TrackedCell] = []

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
                log.info(
                    "Merge: celda %d absorbe candidatos %s en %s", t.id, merged_ids, scan_time
                )

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
                area_history=(t.area_history + [det["area_px"]])[-history_len:],
                age_frames=t.age_frames + 1,
                first_seen=t.first_seen,
                last_seen=scan_time,
                accel_kmh_per_min=round(accel, 2),
                split_from=t.split_from,
                merged_ids=merged_ids,
                missed_frames=0,
            ))
        else:
            # Sin match: incrementar contador de ausencias
            t.missed_frames += 1
            if t.missed_frames <= max_missed:
                t.last_seen = scan_time
                new_tracks.append(t)
            else:
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

    return new_tracks, next_id
