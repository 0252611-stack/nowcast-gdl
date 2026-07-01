"""Loop de polling cada 90 s: descarga radar IAM, extrae dBZ, persiste."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image

from app import config
import numpy as np

from app.nowcast.engine import estimate_arrival
from app.processing.motion import field_to_global_vector, multi_frame_motion_field
from app.processing.tracking import TrackedCell, detect_cells, detection_mask, update_tracks
from app.processing.pixel_extract import reading_for_point
from app.schemas import WindSample
from app.sources.openmeteo import (
    fetch_all_points, fetch_ensemble, fetch_forecast, fetch_wind_700_at,
    get_cache_stats, sample_trajectory_wind,
)
from app.sources.radar_iam import RadarUnavailable, fetch_current_frame
from app.storage import (
    get_latest_reading,
    get_recent_frames,
    get_skill_metrics,
    list_points,
    purge_old_frames,
    purge_old_predictions,
    save_frame,
    save_prediction,
    save_reading,
    save_tracking_state,
    verify_predictions,
)

log = logging.getLogger(__name__)


@dataclass
class RadarState:
    available: bool = True
    consecutive_failures: int = 0
    last_kmz_url: str | None = None
    last_bounds: dict | None = None
    # EMA del campo de movimiento (multi-frame, suavizado temporal)
    motion_field_ema: object | None = None   # np.ndarray H×W×2 | None
    # Última ETA por punto para log de variabilidad
    last_eta: dict = field(default_factory=dict)  # point_id → (eta_min|None, method)
    # EMA de la tendencia de área del eco por punto (suaviza ruido de fotograma a fotograma)
    trend_ema: dict = field(default_factory=dict)  # point_id → float
    # Capa 2: celdas rastreadas con identidad persistente
    tracked_cells: list = field(default_factory=list)   # list[TrackedCell]
    next_cell_id: int = 1
    # Intervalo del último ciclo de radar (para calcular age_minutes en el endpoint)
    _cell_interval_s: float = 90.0
    # Detecciones crudas (pre-tracking) del último ciclo — para el endpoint /radar/cells
    last_detections: list = field(default_factory=list)   # list[dict] de detect_cells
    last_track_diag: dict = field(default_factory=dict)   # dict de update_tracks
    last_frame_time: object = None                         # datetime | None


def _scan_time_from_kmz_url(kmz_url: str) -> datetime:
    """Extrae fecha/hora del nombre del KMZ → datetime UTC.
    Formato real: MEXI_ZH_{YYYYMMDD}_{HHMMSS}.kmz
    """
    try:
        stem = kmz_url.rsplit("/", 1)[-1].removesuffix(".kmz")  # MEXI_ZH_20260611_192501
        parts = stem.split("_")
        dt_str = parts[-2] + parts[-1]             # "20260611192501"
        return datetime.strptime(dt_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


async def run_radar_loop(conn: sqlite3.Connection, state: RadarState) -> None:
    """Loop infinito: cada POLL_INTERVAL_SECONDS descarga un frame del IAM,
    extrae dBZ para cada punto de config.POINTS y persiste en SQLite."""
    while True:
        try:
            cycle_start = time.monotonic()
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as client:
                bounds, png_bytes, kmz_url = await fetch_current_frame(
                    client, state.last_kmz_url
                )

            scan_time = _scan_time_from_kmz_url(kmz_url)
            now_utc = datetime.now(timezone.utc)
            frame_age = (now_utc - scan_time).total_seconds()

            # L2: Alerta si los bounds del radar cambian entre ciclos (no debería ocurrir).
            # Un cambio indica reencuadre del IAM y posible error en la georreferencia.
            if state.last_bounds is not None:
                drift = max(
                    abs(bounds.get(k, 0) - state.last_bounds.get(k, 0))
                    for k in ("north", "south", "east", "west")
                )
                if drift > 0.01:
                    log.warning(
                        "Bounds del radar IAM cambiaron (Δ=%.4f°) — verificar georreferencia.", drift
                    )

            image = Image.open(io.BytesIO(png_bytes))

            save_frame(conn, kmz_url, scan_time, png_bytes)

            for pt in list_points(conn):
                try:
                    rdg = reading_for_point(
                        pt["id"], pt["lat"], pt["lon"],
                        bounds, image, scan_time, frame_age,
                    )
                    save_reading(conn, rdg)
                    log.debug("punto=%s dBZ=%.1f cat=%s", pt["id"], rdg.dbz, rdg.category.value)
                except Exception as exc:
                    log.warning("Error extrayendo punto %s: %s", pt["id"], exc)

            purge_old_frames(conn, config.RADAR_RETENTION_HOURS)

            # Calcular el campo de movimiento multi-frame UNA VEZ por ciclo (no por punto).
            # Mantener EMA temporal para suavizar ciclo a ciclo.
            frames_for_motion = get_recent_frames(conn, 4)
            _flow_stats: dict = {}
            if state.last_bounds and len(frames_for_motion) >= 2:
                new_field = multi_frame_motion_field(frames_for_motion, state.last_bounds)
                if new_field is not None:
                    prev = state.motion_field_ema
                    if isinstance(prev, np.ndarray) and prev.shape == new_field.shape:
                        state.motion_field_ema = (0.5 * new_field + 0.5 * prev).astype(np.float32)
                    else:
                        state.motion_field_ema = new_field

            # Estadísticas del campo de flujo óptico — para evaluar los vectores (flechas).
            # Se usa solo la máscara de eco (dbz >= DBZ_THRESHOLD) del frame actual para
            # no diluir el vector con píxeles de cielo despejado (la mayoría del área).
            if state.motion_field_ema is not None and state.last_bounds:
                _raw_mask = detection_mask(image, bounds)   # uint8 H×W (0 o 255)
                _echo_mask = _raw_mask > 0                  # bool H×W
                _n_echo_px = int(_echo_mask.sum())
                if _n_echo_px > 0:
                    _gv = field_to_global_vector(
                        state.motion_field_ema, _echo_mask, state.last_bounds
                    )
                    # Coherencia sobre píxeles de eco: resultante / magnitud_media.
                    # Alta (→1) = campo uniforme; baja (→0) = flujo caótico.
                    _f_eco = state.motion_field_ema[_echo_mask]   # N×2
                    _mag_eco = np.sqrt(_f_eco[:, 0] ** 2 + _f_eco[:, 1] ** 2)
                    _mean_mag = float(_mag_eco.mean())
                    _resultant = float(np.sqrt(
                        float(_f_eco[:, 0].mean()) ** 2 + float(_f_eco[:, 1].mean()) ** 2
                    ))
                    _coherence = round(_resultant / _mean_mag, 3) if _mean_mag > 1e-9 else 0.0
                    _flow_stats = {
                        "flow_spd_kmh": round(_gv["speed_kmh"], 1),
                        "flow_brg_deg": round(_gv["bearing_deg"], 0),
                        "flow_coherence": _coherence,
                        "flow_n_echo_px": _n_echo_px,
                    }

            # Capa 2: seguimiento de celdas UNA VEZ por ciclo, usando el frame más nuevo.
            dets: list[dict] = []          # detecciones crudas; inicializar para el log final
            track_diag: dict = {}          # diagnóstico del matching; inicializar por si falla
            if state.last_bounds and len(frames_for_motion) >= 1:
                try:
                    import io as _io
                    from PIL import Image as _Image
                    newer_bytes_track, newer_time_track = frames_for_motion[0]
                    img_track = _Image.open(_io.BytesIO(newer_bytes_track))
                    dets, det_diag = detect_cells(img_track, state.last_bounds, return_diag=True)
                    interval_track = 90.0
                    if len(frames_for_motion) >= 2:
                        _, older_time_track = frames_for_motion[1]
                        interval_track = max(
                            1.0, (newer_time_track - older_time_track).total_seconds()
                        )
                    state._cell_interval_s = interval_track
                    state.tracked_cells, state.next_cell_id, track_diag = update_tracks(
                        state.tracked_cells, dets, newer_time_track,
                        state.last_bounds, interval_track, state.next_cell_id,
                    )
                    # Fusionar diagnóstico del split en el dict de tracking
                    track_diag = {**track_diag, **det_diag}
                    state.last_detections = dets
                    state.last_track_diag = track_diag
                    state.last_frame_time = newer_time_track
                    # Persistir estado de tracking (1 upsert/ciclo ≈ despreciable)
                    try:
                        save_tracking_state(
                            conn, state.tracked_cells, state.next_cell_id, state.last_frame_time
                        )
                    except Exception as exc_sv:
                        log.debug("Error guardando estado de tracking: %s", exc_sv)
                except Exception as exc_tr:
                    log.warning("Error en tracking de celdas: %s", exc_tr)

            # Emitir una predicción por punto y registrarla para verificación posterior
            _point_diag: list[dict] = []
            frames = get_recent_frames(conn, 2)
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as fc:
                for pt in list_points(conn):
                    try:
                        # A4: cap de tiempo por punto para no desincronizar el ciclo de 90 s.
                        forecast = await asyncio.wait_for(
                            fetch_forecast(fc, pt["id"], pt["name"], pt["lat"], pt["lon"]),
                            timeout=12.0,
                        )
                        reading = get_latest_reading(conn, pt["id"])
                        # Ensemble prob (Fase 2) — degradación silenciosa si falla
                        ens_prob: float | None = None
                        try:
                            ens_prob = await fetch_ensemble(fc, pt["lat"], pt["lon"])
                        except Exception:
                            pass
                        result = estimate_arrival(
                            pt["id"], reading, forecast, frames, state.last_bounds,
                            motion_field=state.motion_field_ema,
                            ensemble_prob=ens_prob,
                            prev_trend_ema=state.trend_ema.get(pt["id"]),
                            tracked_cells=state.tracked_cells if state.tracked_cells else None,
                        )
                        if result.intensity_trend is not None:
                            state.trend_ema[pt["id"]] = result.intensity_trend
                        if result.cell_lat is not None:
                            try:
                                ew = await fetch_wind_700_at(fc, result.cell_lat, result.cell_lon)
                                result.wind_echo_bearing_deg = ew["toward_deg"]
                                result.wind_echo_speed_kmh = ew["speed_kmh"]
                            except Exception as exc:
                                log.debug("Viento en eco no disponible: %s", exc)
                            try:
                                traj = await sample_trajectory_wind(
                                    fc, result.cell_lat, result.cell_lon, pt["lat"], pt["lon"]
                                )
                                result.trajectory_wind = [WindSample(**s) for s in traj]
                            except Exception as exc:
                                log.debug("Trajectory wind no disponible: %s", exc)
                        save_prediction(conn, result)
                        _point_diag.append({
                            "id": pt["id"],
                            "method": result.method,
                            "eta_min": result.eta_minutes,
                            "conf": round(result.confidence, 3) if result.confidence is not None else None,
                            "led_km": round(result.leading_edge_distance_km, 1) if result.leading_edge_distance_km is not None else None,
                            "cell_spd": round(result.cell_speed_kmh, 1) if result.cell_speed_kmh is not None else None,
                            "cell_brg": round(result.cell_bearing_deg, 0) if result.cell_bearing_deg is not None else None,
                            "trend": round(result.intensity_trend, 3) if result.intensity_trend is not None else None,
                            "w_radar": round(result.weight_radar, 3) if result.weight_radar is not None else None,
                            "model_agr": round(result.model_agreement, 3) if result.model_agreement is not None else None,
                        })

                        # Log de variabilidad: mostrar delta respecto al ciclo anterior
                        pid = pt["id"]
                        prev_eta, prev_method = state.last_eta.get(pid, (None, None))
                        curr_eta = result.eta_minutes
                        curr_method = result.method
                        # Advertir si el método cambió (señal de inestabilidad de fuente)
                        if prev_method is not None and curr_method != prev_method:
                            log.warning(
                                "ETA[%s] cambio de método: %s → %s",
                                pid, prev_method, curr_method,
                            )
                        if prev_eta is not None and curr_eta is not None:
                            delta = curr_eta - prev_eta
                            log.info(
                                "ETA[%s] %s→%s min (Δ%+d) método=%s vel=%s rumbo=%s "
                                "trend=%s conf=%s",
                                pid, prev_eta, curr_eta, delta, curr_method,
                                result.cell_speed_kmh, result.cell_bearing_deg,
                                result.intensity_trend, result.confidence,
                            )
                        elif curr_eta is not None or prev_eta is not None:
                            log.info(
                                "ETA[%s] %s→%s min método=%s vel=%s trend=%s conf=%s",
                                pid, prev_eta, curr_eta, curr_method,
                                result.cell_speed_kmh, result.intensity_trend, result.confidence,
                            )
                        state.last_eta[pid] = (curr_eta, curr_method)

                    except Exception as exc:
                        log.warning("Error emitiendo predicción para %s: %s", pt["id"], exc)

            # Verificar predicciones cuyo horizonte ya expiró
            v = verify_predictions(conn, now_utc)
            if v["count"]:
                log.info(
                    "Verificadas %d predicciones: %d aciertos, %d falsas alarmas, "
                    "%d pérdidas, %d neg. correctas.",
                    v["count"], v["hit"], v["false_alarm"], v["miss"], v["correct_negative"],
                )

            purge_old_predictions(conn)

            state.consecutive_failures = 0
            state.available = True
            state.last_kmz_url = kmz_url
            state.last_bounds = bounds

            # L1: tamaño del cache Open-Meteo y misses de la hora actual.
            cs = get_cache_stats()
            log.info(
                "Cache Open-Meteo: %d entradas (pronóst=%d viento=%d precip=%d ens=%d) "
                "— %d requests reales esta hora.",
                cs["total"], cs["forecast"], cs["wind"], cs["precip"], cs["ensemble"],
                cs["misses_this_hour"],
            )

            # L3: diagnóstico estructurado del ciclo — parseable por grep y JSONL.
            _areas = [d["area_px"] for d in dets]
            _dbzs = [d["mean_dbz"] for d in dets]
            _cycle_s = round(time.monotonic() - cycle_start, 1)
            log.info(
                "cycle_s=%.1f n_det=%d area_min=%s area_med=%s area_max=%s "
                "dbz_min=%s dbz_max=%s n_alive=%s n_new=%s n_continued=%s "
                "n_purged=%s n_split=%s n_merge=%s gate_rejects=%s match_cost_mean=%s",
                _cycle_s,
                len(dets),
                min(_areas) if _areas else None,
                int(np.median(_areas)) if _areas else None,
                max(_areas) if _areas else None,
                round(min(_dbzs), 1) if _dbzs else None,
                round(max(_dbzs), 1) if _dbzs else None,
                track_diag.get("n_alive", 0),
                track_diag.get("n_new", 0),
                track_diag.get("n_continued", 0),
                track_diag.get("n_purged", 0),
                track_diag.get("n_split", 0),
                track_diag.get("n_merge", 0),
                track_diag.get("gate_rejects", 0),
                track_diag.get("match_cost_mean"),
            )

            # Escribir registro JSONL (una línea por ciclo) para análisis posterior.
            try:
                _diag_path = Path(config.DIAG_LOG_PATH)
                _diag_path.parent.mkdir(parents=True, exist_ok=True)
                # Skill actual (lectura ligera de DB, sin bloquear el ciclo)
                _sm = get_skill_metrics(conn)
                _sk_o = _sm.get("overall", {}) if _sm.get("verified", 0) > 0 else {}
                _record = {
                    "frame_time": scan_time.isoformat(),
                    "cycle_s": _cycle_s,
                    # --- Detección y tracking ---
                    "n_det": len(dets),
                    "area_min": min(_areas) if _areas else None,
                    "area_med": int(np.median(_areas)) if _areas else None,
                    "area_max": max(_areas) if _areas else None,
                    "dbz_min": round(min(_dbzs), 1) if _dbzs else None,
                    "dbz_max": round(max(_dbzs), 1) if _dbzs else None,
                    **track_diag,
                    # --- Vectores (optical flow) ---
                    **_flow_stats,
                    # --- Motor: predicción por punto ---
                    "points": _point_diag,
                    # --- Skill verificado acumulado ---
                    "skill_n": _sm.get("overall", {}).get("total", 0),
                    "skill_pod": round(_sk_o["pod"], 3) if _sk_o.get("pod") is not None else None,
                    "skill_far": round(_sk_o["far"], 3) if _sk_o.get("far") is not None else None,
                    "skill_csi": round(_sk_o["csi"], 3) if _sk_o.get("csi") is not None else None,
                    "skill_acc": round(_sk_o["accuracy"], 3) if _sk_o.get("accuracy") is not None else None,
                }
                with _diag_path.open("a", encoding="utf-8") as _f:
                    _f.write(json.dumps(_record) + "\n")
            except Exception as _exc_diag:
                log.debug("Error escribiendo JSONL de diagnóstico: %s", _exc_diag)

            log.info(
                "Frame radar OK: %s (age %.0f s) — ciclo %.1f s",
                kmz_url.rsplit("/", 1)[-1], frame_age, _cycle_s,
            )

        except RadarUnavailable as exc:
            # "same frame" es skip normal, no un fallo
            if "same frame" in str(exc).lower():
                log.debug("Radar: mismo frame, skip.")
            else:
                state.consecutive_failures += 1
                log.warning("Radar no disponible (%d/%d): %s",
                            state.consecutive_failures, config.RADAR_FAIL_THRESHOLD, exc)
                if state.consecutive_failures >= config.RADAR_FAIL_THRESHOLD:
                    state.available = False
                    log.error("Radar degradado a solo Open-Meteo tras %d fallos.",
                              config.RADAR_FAIL_THRESHOLD)
        except Exception as exc:
            state.consecutive_failures += 1
            log.warning("Error en ciclo radar (%d/%d): %s",
                        state.consecutive_failures, config.RADAR_FAIL_THRESHOLD, exc)
            if state.consecutive_failures >= config.RADAR_FAIL_THRESHOLD:
                state.available = False

        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


async def run_forecast_loop(conn: sqlite3.Connection) -> None:
    """Precalienta el cache de Open-Meteo una vez por hora para todos los puntos activos.
    También emite un log periódico de skill (POD/FAR/CSI) sin necesidad de abrir /metrics."""
    while True:
        try:
            points = list_points(conn)
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT}, timeout=10
            ) as client:
                forecasts = await fetch_all_points(client, points)
                log.info("Pronóstico Open-Meteo actualizado para %d puntos.", len(forecasts))

            # L4: log de skill global cada hora — tendencia de calidad del motor.
            m = get_skill_metrics(conn)
            if m["verified"] > 0:
                o = m["overall"]
                log.info(
                    "Skill global (n=%d verificadas, %d pendientes): "
                    "POD=%.0f%% FAR=%.0f%% CSI=%.0f%% Acc=%.0f%%",
                    o["total"], m["pending"],
                    (o["pod"]      or 0) * 100,
                    (o["far"]      or 0) * 100,
                    (o["csi"]      or 0) * 100,
                    (o["accuracy"] or 0) * 100,
                )
            else:
                log.info(
                    "Skill global: sin predicciones verificadas aún (%d pendientes).",
                    m["pending"],
                )
        except Exception as exc:
            log.warning("Error actualizando pronóstico/skill: %s", exc)
        await asyncio.sleep(3600)
