"""Capa de persistencia SQLite: frames del radar y lecturas por punto."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.schemas import NowcastResult, RadarCategory, RadarReading

_DDL = """
CREATE TABLE IF NOT EXISTS nowcast_predictions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id              TEXT    NOT NULL,
    generated_at_utc      TEXT    NOT NULL,
    raining_now           INTEGER NOT NULL,
    predicted_rain        INTEGER NOT NULL,
    eta_minutes           INTEGER,
    confidence            REAL,
    method                TEXT    NOT NULL,
    horizon_minutes       INTEGER NOT NULL,
    target_time_utc       TEXT    NOT NULL,
    predicted_arrival_utc TEXT,
    verified_at_utc       TEXT,
    observed_raining      INTEGER,
    observed_arrival_utc  TEXT,
    lead_time_error_min   REAL,
    outcome               TEXT,
    created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS radar_frames (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kmz_url       TEXT    NOT NULL UNIQUE,
    scan_time_utc TEXT    NOT NULL,
    png_blob      BLOB    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS point_readings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id          TEXT    NOT NULL,
    dbz               REAL    NOT NULL,
    category          TEXT    NOT NULL,
    scan_time_utc     TEXT    NOT NULL,
    frame_age_seconds REAL    NOT NULL,
    pixel_x           INTEGER NOT NULL,
    pixel_y           INTEGER NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Crea las tablas si no existen y devuelve la conexión."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.executescript(_DDL)
    conn.commit()
    return conn


def save_frame(
    conn: sqlite3.Connection,
    kmz_url: str,
    scan_time_utc: datetime,
    png_bytes: bytes,
) -> None:
    """Inserta un frame PNG en radar_frames. Idempotente por kmz_url."""
    conn.execute(
        "INSERT OR IGNORE INTO radar_frames (kmz_url, scan_time_utc, png_blob) VALUES (?, ?, ?)",
        (kmz_url, scan_time_utc.isoformat(), png_bytes),
    )
    conn.commit()


def save_reading(conn: sqlite3.Connection, reading: RadarReading) -> None:
    """Guarda la lectura dBZ de un punto."""
    conn.execute(
        """INSERT INTO point_readings
           (point_id, dbz, category, scan_time_utc, frame_age_seconds, pixel_x, pixel_y)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            reading.point_id,
            reading.dbz,
            reading.category.value,
            reading.scan_time_utc.isoformat(),
            reading.frame_age_seconds,
            reading.pixel_x,
            reading.pixel_y,
        ),
    )
    conn.commit()


def get_recent_frames(
    conn: sqlite3.Connection, n: int = 3
) -> list[tuple[bytes, datetime]]:
    """Devuelve los n frames más recientes como (PNG bytes, scan_time_utc) DESC."""
    rows = conn.execute(
        "SELECT png_blob, scan_time_utc FROM radar_frames ORDER BY scan_time_utc DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [
        (row[0], datetime.fromisoformat(row[1]).replace(tzinfo=timezone.utc))
        for row in rows
    ]


def get_latest_reading(
    conn: sqlite3.Connection, point_id: str
) -> RadarReading | None:
    """Última lectura del punto, reconstruida como RadarReading. None si no hay datos."""
    row = conn.execute(
        """SELECT point_id, dbz, category, scan_time_utc, frame_age_seconds, pixel_x, pixel_y
           FROM point_readings
           WHERE point_id = ?
           ORDER BY scan_time_utc DESC
           LIMIT 1""",
        (point_id,),
    ).fetchone()
    if row is None:
        return None
    point_id, dbz, category, scan_time_utc, frame_age_seconds, pixel_x, pixel_y = row
    return RadarReading(
        point_id=point_id,
        dbz=dbz,
        category=RadarCategory(category),
        scan_time_utc=datetime.fromisoformat(scan_time_utc).replace(tzinfo=timezone.utc),
        frame_age_seconds=frame_age_seconds,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
    )


def purge_old_frames(conn: sqlite3.Connection, retention_hours: int = 24) -> int:
    """Elimina frames con más de retention_hours horas. Devuelve cantidad eliminada."""
    cursor = conn.execute(
        """DELETE FROM radar_frames
           WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? )""",
        (f"-{retention_hours} hours",),
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Predicciones y verificación de skill
# ---------------------------------------------------------------------------

def save_prediction(conn: sqlite3.Connection, result: NowcastResult) -> None:
    """Persiste una predicción emitida por el motor de nowcasting."""
    generated_utc = result.generated_at.astimezone(timezone.utc)
    target_utc = generated_utc + timedelta(minutes=result.horizon_minutes)
    predicted_arrival_utc = (
        generated_utc + timedelta(minutes=result.eta_minutes)
        if result.eta_minutes is not None else None
    )
    predicted_rain = bool(result.raining_now or result.eta_minutes is not None)

    conn.execute(
        """INSERT INTO nowcast_predictions
           (point_id, generated_at_utc, raining_now, predicted_rain,
            eta_minutes, confidence, method, horizon_minutes,
            target_time_utc, predicted_arrival_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.point_id,
            generated_utc.isoformat(),
            int(result.raining_now),
            int(predicted_rain),
            result.eta_minutes,
            result.confidence,
            result.method,
            result.horizon_minutes,
            target_utc.isoformat(),
            predicted_arrival_utc.isoformat() if predicted_arrival_utc else None,
        ),
    )
    conn.commit()


def verify_predictions(
    conn: sqlite3.Connection,
    now_utc: datetime,
    dbz_threshold: float = 18.0,
) -> int:
    """Verifica predicciones cuyo horizonte ya expiró. Devuelve nº verificadas."""
    rows = conn.execute(
        """SELECT id, point_id, generated_at_utc, predicted_rain,
                  target_time_utc, predicted_arrival_utc
           FROM nowcast_predictions
           WHERE verified_at_utc IS NULL
             AND target_time_utc <= ?""",
        (now_utc.isoformat(),),
    ).fetchall()

    if not rows:
        return 0

    verified_at = now_utc.isoformat()
    count = 0

    for (row_id, point_id, gen_utc_str, predicted_rain,
         target_utc_str, arr_utc_str) in rows:

        obs = conn.execute(
            """SELECT MAX(dbz),
                      MIN(CASE WHEN dbz > ? THEN scan_time_utc ELSE NULL END)
               FROM point_readings
               WHERE point_id = ?
                 AND scan_time_utc > ?
                 AND scan_time_utc <= ?""",
            (dbz_threshold, point_id, gen_utc_str, target_utc_str),
        ).fetchone()

        max_dbz, first_rain_str = obs
        observed_raining = bool(max_dbz is not None and max_dbz > dbz_threshold)
        observed_arrival = first_rain_str if observed_raining else None

        if predicted_rain and observed_raining:
            outcome = "hit"
        elif predicted_rain and not observed_raining:
            outcome = "false_alarm"
        elif not predicted_rain and observed_raining:
            outcome = "miss"
        else:
            outcome = "correct_negative"

        lead_error = None
        if arr_utc_str and observed_arrival:
            pred_arr = datetime.fromisoformat(arr_utc_str)
            obs_arr = datetime.fromisoformat(observed_arrival).replace(tzinfo=timezone.utc)
            if pred_arr.tzinfo is None:
                pred_arr = pred_arr.replace(tzinfo=timezone.utc)
            lead_error = round((pred_arr - obs_arr).total_seconds() / 60, 1)

        conn.execute(
            """UPDATE nowcast_predictions
               SET verified_at_utc = ?, observed_raining = ?,
                   observed_arrival_utc = ?, lead_time_error_min = ?, outcome = ?
               WHERE id = ?""",
            (verified_at, int(observed_raining), observed_arrival, lead_error, outcome, row_id),
        )
        count += 1

    conn.commit()
    return count


def get_skill_metrics(conn: sqlite3.Connection) -> dict:
    """Métricas de verificación: POD, FAR, CSI, accuracy (overall y forecast_only)."""
    rows = conn.execute(
        """SELECT raining_now, predicted_rain, outcome, method, lead_time_error_min
           FROM nowcast_predictions
           WHERE verified_at_utc IS NOT NULL"""
    ).fetchall()

    pending = conn.execute(
        "SELECT COUNT(*) FROM nowcast_predictions WHERE verified_at_utc IS NULL"
    ).fetchone()[0]

    def _metrics(subset: list) -> dict:
        H  = sum(1 for r in subset if r[2] == "hit")
        M  = sum(1 for r in subset if r[2] == "miss")
        FA = sum(1 for r in subset if r[2] == "false_alarm")
        CN = sum(1 for r in subset if r[2] == "correct_negative")
        total = H + M + FA + CN
        errors = [r[4] for r in subset if r[4] is not None]
        return {
            "hits": H, "misses": M, "false_alarms": FA, "correct_negatives": CN,
            "total": total,
            "pod":      round(H / (H + M), 3)           if H + M > 0     else None,
            "far":      round(FA / (H + FA), 3)          if H + FA > 0    else None,
            "csi":      round(H / (H + M + FA), 3)       if H + M + FA > 0 else None,
            "accuracy": round((H + CN) / total, 3)       if total > 0     else None,
            "mean_lead_error_min": round(sum(errors) / len(errors), 1) if errors else None,
        }

    forecast_only = [r for r in rows if not r[0]]  # excluye raining_now=True
    methods = sorted({r[3] for r in rows})
    by_method = {m: _metrics([r for r in rows if r[3] == m]) for m in methods}

    overall = _metrics(rows)
    return {
        "overall": overall,
        "forecast_only": _metrics(forecast_only),
        "by_method": by_method,
        "pending": pending,
        "verified": len(rows),
        "mean_lead_time_error_min": overall["mean_lead_error_min"],
    }


def purge_old_predictions(conn: sqlite3.Connection, retention_hours: int = 168) -> int:
    """Elimina predicciones con más de retention_hours horas (default 7 días)."""
    cursor = conn.execute(
        """DELETE FROM nowcast_predictions
           WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)""",
        (f"-{retention_hours} hours",),
    )
    conn.commit()
    return cursor.rowcount
