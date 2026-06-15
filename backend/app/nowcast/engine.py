"""Motor de nowcasting: combina radar + viento para estimar ETA de lluvia."""

from __future__ import annotations

import io
import logging
import math
from datetime import datetime, timedelta

import numpy as np
from PIL import Image

from app import config
from app.processing.motion import (
    compute_cell_motion,
    field_to_global_vector,
    find_upstream_echoes,
    leading_edge_point,
    multi_frame_motion_field,
    project_cell,
    sample_field_at,
    vector_to_speed_bearing,
)
from app.schemas import NowcastResult, PointForecast, RadarReading

log = logging.getLogger(__name__)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _model_prob_at(forecast: PointForecast, arrival_time: datetime) -> float:
    """Probabilidad de precipitación del modelo (0-1) en la hora más cercana a la
    llegada prevista. Usa el pronóstico horario de Open-Meteo ya cacheado."""
    if not forecast.hourly:
        return 0.0
    best = min(forecast.hourly, key=lambda h: abs((h.time - arrival_time).total_seconds()))
    return best.precipitation_probability / 100.0


def _project_cell_to_point(
    cell,
    point_lat: float,
    point_lon: float,
    bounds: dict[str, float],
    motion_field: "np.ndarray | None" = None,
    wind_speed: float = 0.0,
    wind_dir: float = 0.0,
    horizon_minutes: int = 240,
) -> dict | None:
    """Proyecta una celda rastreada hacia un punto y devuelve ETA + metadatos.

    Encapsula: speed-gate (≥1 km/h), cono de dirección (±120° hacia el punto),
    cálculo del borde de ataque (leading_edge_point), override de velocidad
    local por motion_field, y project_cell.

    Devuelve {eta_minutes, confidence, edge_dist_km, speed_kmh, bearing_deg}
    o None si la celda no apunta al punto o no llega en el horizonte.
    """
    if cell.velocity_kmh < 1.0:
        return None

    km_lon = 111.32 * math.cos(math.radians((bounds["north"] + bounds["south"]) / 2))
    dlat_km = (point_lat - cell.lat) * 111.32
    dlon_km = (point_lon - cell.lon) * km_lon
    bearing_to_pt = math.degrees(math.atan2(dlon_km, dlat_km)) % 360
    ang_diff = abs(((cell.bearing_deg - bearing_to_pt + 180) % 360) - 180)
    if ang_diff > 120:
        return None

    # Borde de ataque
    if cell.ring and len(cell.ring) >= 2:
        edge_lat, edge_lon, edge_dist_km = leading_edge_point(
            cell.ring, point_lat, point_lon, bounds
        )
    else:
        edge_lat, edge_lon = cell.lat, cell.lon
        dlat_km2 = (point_lat - cell.lat) * 111.32
        dlon_km2 = (point_lon - cell.lon) * km_lon
        edge_dist_km = math.sqrt(dlat_km2**2 + dlon_km2**2)

    # Velocidad local del campo de movimiento (o velocidad del track)
    cspeed = cell.velocity_kmh
    cbearing = cell.bearing_deg
    if motion_field is not None:
        v_lat, v_lon = sample_field_at(motion_field, edge_lat, edge_lon, bounds)
        local_speed, local_bearing = vector_to_speed_bearing(v_lat, v_lon, bounds)
        if local_speed >= 1.0:
            cspeed = local_speed
            cbearing = local_bearing

    proj = project_cell(
        point_lat, point_lon,
        edge_dist_km,
        cspeed,
        cbearing,
        bearing_to_pt,
        wind_speed,
        wind_dir,
        horizon_minutes,
    )
    if proj["eta_minutes"] is None:
        return None

    return {
        "eta_minutes": proj["eta_minutes"],
        "confidence": proj["confidence"],
        "edge_dist_km": edge_dist_km,
        "speed_kmh": cspeed,
        "bearing_deg": cbearing,
    }


def compute_cell_etas(
    cells: list,
    points: list[dict],
    bounds: dict[str, float] | None,
    motion_field: "np.ndarray | None" = None,
) -> dict[int, dict]:
    """Para cada celda rastreada, calcula la ETA al punto monitoreado más cercano.

    Devuelve {cell_id: {eta_minutes, eta_point_id, eta_confidence}}.
    Solo considera celdas que apunten (cono ±120°) hacia algún punto.
    Sin corrección de viento (ETA secundaria — para tooltip del mapa).
    """
    if bounds is None or not points:
        return {}

    result: dict[int, dict] = {}
    for cell in cells:
        best_eta: int | None = None
        best_point_id: str | None = None
        best_confidence: float | None = None

        for pt in points:
            proj = _project_cell_to_point(
                cell, pt["lat"], pt["lon"], bounds, motion_field,
                wind_speed=0.0, wind_dir=0.0, horizon_minutes=240,
            )
            if proj is None:
                continue
            if best_eta is None or proj["eta_minutes"] < best_eta:
                best_eta = proj["eta_minutes"]
                best_point_id = pt["id"]
                best_confidence = proj["confidence"]

        if best_eta is not None:
            result[cell.id] = {
                "eta_minutes": best_eta,
                "eta_point_id": best_point_id,
                "eta_confidence": round(best_confidence, 3) if best_confidence is not None else None,
            }
    return result


def estimate_arrival(
    point_id: str,
    radar: RadarReading | None,
    forecast: PointForecast,
    frames: list[tuple[bytes, datetime]],
    bounds: dict[str, float] | None,
    horizon_minutes: int = 240,
    motion_field: np.ndarray | None = None,
    ensemble_prob: float | None = None,
    prev_trend_ema: float | None = None,
    tracked_cells: list | None = None,
) -> NowcastResult:
    """Estima si lloverá en el punto dentro de horizon_minutes.

    `motion_field` (opcional): campo denso H×W×2 (grados/min) precomputado por el
    scheduler (EMA multi-frame). Si es None se calcula con multi_frame_motion_field
    sobre `frames`. Pasarlo evita recomputar el flujo una vez por punto y mantiene
    la ETA estable (mismo campo para todos los puntos en el ciclo).

    Métodos posibles en NowcastResult.method:
      radar_unavailable   — sin datos de radar
      radar_current       — ya está lloviendo en el punto
      insufficient_frames — menos de 2 frames o bounds no disponibles
      no_echo             — no hay eco en los frames (imagen transparente)
      no_motion           — hay eco pero no se detecta movimiento
      no_approaching_cell — no hay celda acercándose desde upstream
      cell_tracking       — ETA por borde de ataque de celda rastreada (Capa 2+3)
      advection           — ETA por optical flow + viento 700 hPa (fallback)
    """
    generated_at = datetime.now(tz=config.TZ_LOCAL)

    # Timeline de intensidad (computable en cualquier momento después de tener frames)
    _timeline_dict: dict | None = None

    def _get_timeline(img: Image.Image, mf) -> tuple[list | None, str | None]:
        """Calcula el timeline semi-lagrangiano para el punto. Lazy y una sola vez."""
        nonlocal _timeline_dict
        if _timeline_dict is None:
            try:
                from app.processing.predict import point_intensity_timeline
                from app.schemas import IntensityStep
                result_tl = point_intensity_timeline(
                    forecast.lat, forecast.lon, img, mf, bounds,
                    steps_min=config.INTENSITY_TIMELINE_STEPS_MIN,
                )
                _timeline_dict = result_tl
            except Exception as exc_tl:
                log.debug("Timeline de intensidad no disponible: %s", exc_tl)
                return None, None
        if _timeline_dict is None:
            return None, None
        from app.schemas import IntensityStep, RadarCategory
        steps = [
            IntensityStep(
                minutes=s["minutes"],
                dbz=s["dbz"],
                category=RadarCategory(s["category"]),
            )
            for s in _timeline_dict["steps"]
        ]
        return steps, _timeline_dict.get("verdict")

    def _result(**kw) -> NowcastResult:
        defaults = dict(
            point_id=point_id,
            raining_now=False,
            eta_minutes=None,
            confidence=None,
            horizon_minutes=horizon_minutes,
            cell_speed_kmh=None,
            cell_bearing_deg=None,
            generated_at=generated_at,
            method="unknown",
            intensity_trend=None,
            model_agreement=None,
            cell_id=None,
            cell_age_minutes=None,
            leading_edge_distance_km=None,
            intensity_timeline=None,
            intensity_verdict=None,
        )
        defaults.update(kw)
        return NowcastResult(**defaults)

    # 1. Sin radar
    if radar is None:
        return _result(method="radar_unavailable")

    # 2. ¿Lloviendo ahora? Cualquier eco no-ruido (≥ DBZ_RAIN_THRESHOLD) cuenta como lluvia.
    raining_now = radar.dbz >= config.DBZ_RAIN_THRESHOLD
    if raining_now:
        conf = min(1.0, (radar.dbz - config.DBZ_RAIN_THRESHOLD) / (55.0 - config.DBZ_RAIN_THRESHOLD))
        return _result(raining_now=True, eta_minutes=0, confidence=round(conf, 3),
                       method="radar_current")

    # 3. ¿Suficientes frames para optical flow?
    if len(frames) < 2 or bounds is None:
        return _result(method="insufficient_frames")

    # 4. Campo de movimiento (frames[0]=nuevo, frames[1]=viejo). Usa el campo
    #    precomputado (EMA del scheduler) o lo calcula multi-frame; cae a 2-frame.
    newer_bytes, newer_time = frames[0]
    older_bytes, older_time = frames[1]

    if motion_field is None:
        motion_field = multi_frame_motion_field(frames, bounds)

    arr_newer = np.array(Image.open(io.BytesIO(newer_bytes)).convert("RGBA"))
    arr_older = np.array(Image.open(io.BytesIO(older_bytes)).convert("RGBA"))
    echo_mask_newer = arr_newer[:, :, 3] > 0
    n_echo_newer = int(echo_mask_newer.sum())
    n_echo_older = int((arr_older[:, :, 3] > 0).sum())

    if motion_field is not None and motion_field.shape[:2] == echo_mask_newer.shape:
        motion = field_to_global_vector(motion_field, echo_mask_newer, bounds)
    else:
        interval_s = max(1.0, (newer_time - older_time).total_seconds())
        motion = compute_cell_motion(older_bytes, newer_bytes, interval_s, bounds)
        motion_field = None  # no se puede muestrear vector local

    if motion["n_echo_pixels"] == 0:
        return _result(method="no_echo")

    if motion["speed_kmh"] < 0.1:
        return _result(method="no_motion")

    # D: tendencia de área del eco (crecimiento/decaimiento) entre los 2 frames.
    # EMA α=0.5 suaviza el ruido de fotograma a fotograma sin eliminar la señal.
    raw_trend = _clamp((n_echo_newer - n_echo_older) / max(1, n_echo_older), -1.0, 1.0)
    trend = (0.5 * raw_trend + 0.5 * prev_trend_ema
             if prev_trend_ema is not None else raw_trend)
    trend = _clamp(trend, -1.0, 1.0)
    mult_trend = _clamp(1 + 0.5 * trend, 0.5, 1.2)

    # Timeline de intensidad: pre-computar una vez con la imagen más nueva.
    _newer_pil_img = Image.open(io.BytesIO(newer_bytes))
    _tl_steps, _tl_verdict = _get_timeline(_newer_pil_img, motion_field)

    # 5a. Ruta preferente: celdas rastreadas (Capa 2+3 — cell_tracking).
    #     Si hay celdas con identidad persistente, usar borde de ataque + velocidad
    #     por celda. Fallback al camino por píxeles si no hay celdas válidas.
    if tracked_cells:
        if forecast.hourly:
            wind_speed_700_ct = forecast.hourly[0].wind_speed_700hPa_kmh
            wind_dir_700_ct   = forecast.hourly[0].wind_direction_700hPa_deg
        else:
            wind_speed_700_ct, wind_dir_700_ct = 0.0, 0.0

        best_ct_proj: dict | None = None
        best_ct_cell = None
        best_ct_edge_dist: float | None = None
        best_ct_bearing_to_pt: float = 0.0

        for cell in tracked_cells:
            proj = _project_cell_to_point(
                cell, forecast.lat, forecast.lon, bounds, motion_field,
                wind_speed=wind_speed_700_ct, wind_dir=wind_dir_700_ct,
                horizon_minutes=horizon_minutes,
            )
            if proj is None:
                continue
            # Recalcular bearing_to_pt para exponerlo en el resultado
            km_lon_ct = 111.32 * math.cos(math.radians((bounds["north"] + bounds["south"]) / 2))
            dlat_km_ct = (forecast.lat - cell.lat) * 111.32
            dlon_km_ct = (forecast.lon - cell.lon) * km_lon_ct
            bearing_to_pt = math.degrees(math.atan2(dlon_km_ct, dlat_km_ct)) % 360
            if best_ct_proj is None or proj["eta_minutes"] < best_ct_proj["eta_minutes"]:
                best_ct_proj = proj
                best_ct_cell = cell
                best_ct_edge_dist = proj["edge_dist_km"]
                best_ct_bearing_to_pt = bearing_to_pt

        if best_ct_proj is not None and best_ct_cell is not None:
            cell = best_ct_cell
            eta_min = best_ct_proj["eta_minutes"]
            conf_radar = best_ct_proj["confidence"]

            # Tendencia por celda (area_history de esa celda — no global)
            if len(cell.area_history) >= 2:
                a_new = cell.area_history[-1]
                a_old = cell.area_history[-2]
                raw_trend_ct = _clamp((a_new - a_old) / max(1, a_old), -1.0, 1.0)
                trend = (0.5 * raw_trend_ct + 0.5 * prev_trend_ema
                         if prev_trend_ema is not None else raw_trend_ct)
                trend = _clamp(trend, -1.0, 1.0)
            else:
                trend = _clamp(
                    0.5 * _clamp((n_echo_newer - n_echo_older) / max(1, n_echo_older), -1.0, 1.0)
                    + (0.5 * prev_trend_ema if prev_trend_ema is not None else 0.0),
                    -1.0, 1.0,
                )
            mult_trend = _clamp(1 + 0.5 * trend, 0.5, 1.2)

            arrival_time = generated_at + timedelta(minutes=eta_min)
            if ensemble_prob is not None:
                model_prob = float(ensemble_prob)
            else:
                model_prob = _model_prob_at(forecast, arrival_time)
            w = _clamp(1 - eta_min / 120, 0.3, 1.0)
            if not 0.0 <= conf_radar <= 1.0:
                log.warning(
                    "conf_radar fuera de rango (cell_tracking) para %s: %.3f", point_id, conf_radar
                )
            confidence = _clamp(w * conf_radar * mult_trend + (1 - w) * model_prob, 0.0, 1.0)

            # age_minutes: intervalo de escaneo × ciclos de vida
            interval_s = max(
                1.0, (frames[0][1] - frames[1][1]).total_seconds() if len(frames) >= 2 else 90.0
            )
            age_minutes = round((cell.age_frames - 1) * interval_s / 60.0, 1)

            return _result(
                eta_minutes=eta_min,
                confidence=round(confidence, 3),
                cell_speed_kmh=round(cell.velocity_kmh, 1),
                cell_bearing_deg=round(cell.bearing_deg, 1),
                cell_lat=round(cell.lat, 6),
                cell_lon=round(cell.lon, 6),
                bearing_cell_to_point_deg=round(best_ct_bearing_to_pt, 1),
                intensity_trend=round(trend, 3),
                model_agreement=round(model_prob, 3),
                conf_radar=round(conf_radar, 3),
                weight_radar=round(w, 3),
                mult_trend=round(mult_trend, 3),
                cell_id=cell.id,
                cell_age_minutes=age_minutes,
                leading_edge_distance_km=round(best_ct_edge_dist, 2) if best_ct_edge_dist is not None else None,
                intensity_timeline=_tl_steps,
                intensity_verdict=_tl_verdict,
                method="cell_tracking",
            )
        # Sin celda válida → fallback al camino por píxeles (advection)

    # 5b. Fallback: buscar ecos corriente arriba por píxeles (B1: multicelular, cono ±120°).
    #     Se evalúan hasta 5 candidatos con project_cell y se elige el de menor ETA.
    newer_image = Image.open(io.BytesIO(newer_bytes))
    candidates = find_upstream_echoes(
        newer_image, bounds,
        forecast.lat, forecast.lon,
        motion["bearing_deg"],
    )

    if not candidates:
        return _result(
            cell_speed_kmh=round(motion["speed_kmh"], 1),
            cell_bearing_deg=round(motion["bearing_deg"], 1),
            intensity_trend=round(trend, 3),
            method="no_approaching_cell",
        )

    # 6. Proyectar ETA para cada candidato; elegir el de menor tiempo de llegada.
    if forecast.hourly:
        wind_speed_700 = forecast.hourly[0].wind_speed_700hPa_kmh
        wind_dir_700   = forecast.hourly[0].wind_direction_700hPa_deg
    else:
        log.debug("hourly vacío para %s — sin corrección de viento 700 hPa", point_id)
        wind_speed_700 = 0.0
        wind_dir_700   = 0.0

    best_nearest = None
    best_proj: dict | None = None
    best_speed = motion["speed_kmh"]
    best_bearing = motion["bearing_deg"]

    for cand in candidates:
        # B: vector LOCAL del campo en la posición de este eco candidato.
        cspeed = motion["speed_kmh"]
        cbearing = motion["bearing_deg"]
        if motion_field is not None:
            v_lat, v_lon = sample_field_at(
                motion_field, cand["cell_lat"], cand["cell_lon"], bounds
            )
            local_speed, local_bearing = vector_to_speed_bearing(v_lat, v_lon, bounds)
            if local_speed >= 1.0:
                cspeed = local_speed
                cbearing = local_bearing

        proj = project_cell(
            forecast.lat, forecast.lon,
            cand["distance_km"],
            cspeed,
            cbearing,
            cand["bearing_cell_to_point_deg"],
            wind_speed_700,
            wind_dir_700,
            horizon_minutes,
        )

        if proj["eta_minutes"] is None:
            continue

        if best_proj is None or proj["eta_minutes"] < best_proj["eta_minutes"]:
            best_proj = proj
            best_nearest = cand
            best_speed = cspeed
            best_bearing = cbearing

    # Ningún candidato llega dentro del horizonte
    if best_proj is None or best_nearest is None:
        return _result(
            cell_speed_kmh=round(motion["speed_kmh"], 1),
            cell_bearing_deg=round(motion["bearing_deg"], 1),
            intensity_trend=round(trend, 3),
            method="no_approaching_cell",
        )

    nearest = best_nearest
    projection = best_proj
    cell_speed_kmh = best_speed
    cell_bearing_deg = best_bearing

    eta_min = projection["eta_minutes"]
    conf_radar = projection["confidence"]

    # E: blend de confianza radar + probabilidad NWP, ponderado por horizonte.
    # Si se pasa ensemble_prob (Fase 2), se usa en lugar de precipitation_probability.
    arrival_time = generated_at + timedelta(minutes=eta_min)
    if ensemble_prob is not None:
        model_prob = float(ensemble_prob)
    else:
        model_prob = _model_prob_at(forecast, arrival_time)
    w = _clamp(1 - eta_min / 120, 0.3, 1.0)
    # L3: advertir si algún componente del blend sale de [0,1] antes del clamp final.
    if not 0.0 <= conf_radar <= 1.0:
        log.warning(
            "conf_radar fuera de rango para %s: conf_radar=%.3f (eta=%d w=%.2f) — se clampea",
            point_id, conf_radar, eta_min, w,
        )
    confidence = _clamp(w * conf_radar * mult_trend + (1 - w) * model_prob, 0.0, 1.0)

    return _result(
        eta_minutes=eta_min,
        confidence=round(confidence, 3),
        cell_speed_kmh=round(cell_speed_kmh, 1),
        cell_bearing_deg=round(cell_bearing_deg, 1),
        cell_lat=round(nearest["cell_lat"], 6),
        cell_lon=round(nearest["cell_lon"], 6),
        bearing_cell_to_point_deg=round(nearest["bearing_cell_to_point_deg"], 1),
        intensity_trend=round(trend, 3),
        model_agreement=round(model_prob, 3),
        # B2: componentes del blend — confianza interpretable
        conf_radar=round(conf_radar, 3),
        weight_radar=round(w, 3),
        mult_trend=round(mult_trend, 3),
        intensity_timeline=_tl_steps,
        intensity_verdict=_tl_verdict,
        method="advection",
    )
