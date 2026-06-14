"""Tests de la capa SQLite."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas import RadarCategory, RadarReading
from app.storage import (
    get_eta_stability,
    get_latest_reading,
    get_recent_frames,
    init_db,
    purge_old_frames,
    save_frame,
    save_reading,
)


@pytest.fixture
def db(tmp_path):
    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()


def _reading(point_id: str = "centro") -> RadarReading:
    return RadarReading(
        point_id=point_id,
        dbz=28.5,
        category=RadarCategory.LIGERA,
        scan_time_utc=datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc),
        frame_age_seconds=30.0,
        pixel_x=120,
        pixel_y=85,
    )


def test_init_creates_tables(db):
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "radar_frames" in tables
    assert "point_readings" in tables


def test_save_and_get_latest_reading(db):
    rdg = _reading()
    save_reading(db, rdg)
    result = get_latest_reading(db, "centro")
    assert result is not None
    assert result.point_id == "centro"
    assert result.dbz == pytest.approx(28.5)
    assert result.category == RadarCategory.LIGERA
    assert result.scan_time_utc.tzinfo is not None


def test_get_latest_reading_returns_none_when_empty(db):
    assert get_latest_reading(db, "noexiste") is None


def test_save_frame_idempotent(db):
    save_frame(db, "http://ejemplo/frame1.kmz", datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc), b"PNG1")
    save_frame(db, "http://ejemplo/frame1.kmz", datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc), b"PNG1")
    count = db.execute("SELECT COUNT(*) FROM radar_frames").fetchone()[0]
    assert count == 1


def test_get_recent_frames(db):
    for i in range(3):
        save_frame(
            db,
            f"http://ejemplo/frame{i}.kmz",
            datetime(2026, 6, 11, 4, i, tzinfo=timezone.utc),
            f"PNG{i}".encode(),
        )
    frames = get_recent_frames(db, n=2)
    assert len(frames) == 2
    png_bytes, scan_time = frames[0]
    assert isinstance(png_bytes, bytes)
    assert scan_time.tzinfo is not None
    # frames[0] debe ser el más reciente (minuto=2)
    assert frames[0][1] > frames[1][1]


def test_purge_old_frames(db):
    # Inserta directamente un frame con created_at antiguo
    db.execute(
        "INSERT INTO radar_frames (kmz_url, scan_time_utc, png_blob, created_at) VALUES (?, ?, ?, ?)",
        ("http://old.kmz", "2020-01-01T00:00:00Z", b"OLD", "2020-01-01T00:00:00Z"),
    )
    db.commit()
    save_frame(db, "http://new.kmz", datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc), b"NEW")
    deleted = purge_old_frames(db, retention_hours=1)
    assert deleted >= 1
    frames = get_recent_frames(db, n=10)
    assert len(frames) == 1


# ---------------------------------------------------------------------------
# get_eta_stability (Sesión 4)
# ---------------------------------------------------------------------------

def test_get_eta_stability_empty(db):
    """Sin predicciones → lista vacía."""
    result = get_eta_stability(db, hours=6)
    assert result == []


def test_get_eta_stability_calculates_jitter_and_method_changes(db):
    """Inserta predicciones sintéticas y verifica jitter + method_changes."""
    from datetime import timedelta
    from app.storage import get_eta_stability

    # Usar timestamps relativos para que siempre caigan dentro de la ventana de 24 h
    def _ts(offset_minutes: int) -> str:
        t = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _target(offset_minutes: int) -> str:
        t = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes - 60)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Insertar 4 predicciones para un punto con ETAs variables
    rows = [
        ("pt1", _ts(8), 0, 1, 30, 0.8, "advection",           60, _target(8), None),
        ("pt1", _ts(6), 0, 1, 45, 0.7, "advection",           60, _target(6), None),
        ("pt1", _ts(4), 0, 0, None, None, "no_approaching_cell", 60, _target(4), None),
        ("pt1", _ts(2), 0, 1, 20, 0.6, "advection",           60, _target(2), None),
    ]
    db.executemany(
        """INSERT INTO nowcast_predictions
           (point_id, generated_at_utc, raining_now, predicted_rain,
            eta_minutes, confidence, method, horizon_minutes,
            target_time_utc, predicted_arrival_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    db.commit()

    result = get_eta_stability(db, hours=24)
    assert len(result) == 1
    row = result[0]

    assert row["point_id"] == "pt1"
    assert row["n"] == 4
    assert row["pct_with_eta"] == pytest.approx(0.75)   # 3 de 4 tienen eta
    assert row["eta_mean"] is not None
    assert row["jitter"] is not None
    assert row["jitter"] >= 0
    # Hay un cambio de método (advection → no_approaching_cell → advection) = 2 cambios
    assert row["method_changes"] == 2
    assert len(row["series"]) == 4   # todos en la serie
    assert row["current_method"] == "advection"
    assert row["last_eta"] == 20
