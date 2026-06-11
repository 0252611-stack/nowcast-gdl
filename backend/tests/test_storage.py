"""Tests de la capa SQLite."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas import RadarCategory, RadarReading
from app.storage import (
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
