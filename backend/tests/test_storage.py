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
    load_tracking_state,
    purge_old_frames,
    purge_old_readings,
    save_frame,
    save_reading,
    save_tracking_state,
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


def test_purge_old_readings(db):
    """point_readings no tenía purga propia — crecía sin límite. Mismo patrón
    que purge_old_frames: solo se eliminan filas con created_at fuera de la
    ventana de retención."""
    db.execute(
        """INSERT INTO point_readings
           (point_id, dbz, category, scan_time_utc, frame_age_seconds,
            pixel_x, pixel_y, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("centro", 20.0, "Ligera", "2020-01-01T00:00:00Z", 30.0, 100, 80,
         "2020-01-01T00:00:00Z"),
    )
    db.commit()
    save_reading(db, _reading())  # created_at = ahora (default de la tabla)

    deleted = purge_old_readings(db, retention_hours=1)
    assert deleted >= 1
    remaining = db.execute("SELECT COUNT(*) FROM point_readings").fetchone()[0]
    assert remaining == 1


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


# ---------------------------------------------------------------------------
# Persistencia del estado de tracking (Etapa 3)
# ---------------------------------------------------------------------------

class TestTrackingState:
    """Round-trip save → load preserva todos los campos; guard de antigüedad funciona."""

    def _make_cell(self, cell_id: int = 1) -> "TrackedCell":
        from app.processing.tracking import TrackedCell
        t0 = datetime(2026, 6, 15, 20, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 6, 15, 20, 1, 30, tzinfo=timezone.utc)
        return TrackedCell(
            id=cell_id,
            lat=20.683,
            lon=-103.442,
            area_px=500,
            mean_dbz=32.5,
            max_dbz=45.0,
            ring=[[20.683, -103.442], [20.684, -103.441]],
            velocity_kmh=35.2,
            bearing_deg=225.0,
            centroid_history=[(20.68, -103.44, t0), (20.683, -103.442, t1)],
            area_history=[480, 500],
            age_frames=3,
            first_seen=t0,
            last_seen=t1,
            accel_kmh_per_min=1.5,
            split_from=None,
            merged_ids=[],
            missed_frames=0,
            quality=0.75,
        )

    def test_round_trip_preserves_all_fields(self, db):
        """save → load devuelve celdas con todos los campos intactos."""
        cell = self._make_cell(1)
        frame_time = datetime(2026, 6, 15, 20, 1, 30, tzinfo=timezone.utc)
        save_tracking_state(db, [cell], next_cell_id=2, frame_time=frame_time)

        cells, next_id, ft = load_tracking_state(db)
        assert next_id == 2
        assert ft is not None
        assert ft.tzinfo is not None
        assert len(cells) == 1
        c = cells[0]
        assert c.id == cell.id
        assert c.lat == pytest.approx(cell.lat)
        assert c.lon == pytest.approx(cell.lon)
        assert c.area_px == cell.area_px
        assert c.mean_dbz == pytest.approx(cell.mean_dbz)
        assert c.velocity_kmh == pytest.approx(cell.velocity_kmh)
        assert c.bearing_deg == pytest.approx(cell.bearing_deg)
        assert c.age_frames == cell.age_frames
        assert c.quality == pytest.approx(cell.quality)
        assert len(c.centroid_history) == len(cell.centroid_history)
        assert len(c.area_history) == len(cell.area_history)
        # Datetimes deben ser tz-aware y coincidir
        assert c.first_seen is not None and c.first_seen.tzinfo is not None
        assert c.last_seen is not None and c.last_seen.tzinfo is not None
        assert abs((c.first_seen - cell.first_seen).total_seconds()) < 1
        # centroid_history: el 3er elemento debe ser datetime tz-aware
        for entry in c.centroid_history:
            assert isinstance(entry[2], datetime), f"centroid_history entry no tiene datetime: {entry}"
            assert entry[2].tzinfo is not None

    def test_load_returns_empty_when_no_state(self, db):
        """Sin estado guardado → ([], 1, None)."""
        cells, next_id, ft = load_tracking_state(db)
        assert cells == []
        assert next_id == 1
        assert ft is None

    def test_multiple_cells_round_trip(self, db):
        """Múltiples celdas se guardan y cargan correctamente."""
        cells = [self._make_cell(i) for i in range(1, 4)]
        frame_time = datetime(2026, 6, 15, 20, 0, 0, tzinfo=timezone.utc)
        save_tracking_state(db, cells, next_cell_id=4, frame_time=frame_time)
        loaded, next_id, _ = load_tracking_state(db)
        assert len(loaded) == 3
        assert next_id == 4
        assert {c.id for c in loaded} == {1, 2, 3}

    def test_overwrite_replaces_previous_state(self, db):
        """Segunda llamada a save reemplaza la primera (fila única)."""
        c1 = self._make_cell(1)
        c2 = self._make_cell(2)
        frame_time = datetime(2026, 6, 15, 20, 0, 0, tzinfo=timezone.utc)
        save_tracking_state(db, [c1], next_cell_id=2, frame_time=frame_time)
        save_tracking_state(db, [c1, c2], next_cell_id=3, frame_time=frame_time)
        loaded, next_id, _ = load_tracking_state(db)
        assert len(loaded) == 2
        assert next_id == 3

    def test_corrupt_state_returns_clean(self, db):
        """Estado corrupto (JSON inválido) → arranque limpio sin excepción."""
        db.execute(
            """INSERT OR REPLACE INTO tracking_state (id, cells_json, next_cell_id, frame_time_utc, updated_at)
               VALUES (1, 'NOT_VALID_JSON', 5, '2026-06-15T20:00:00+00:00', '2026-06-15T20:00:00+00:00')"""
        )
        db.commit()
        cells, next_id, ft = load_tracking_state(db)
        assert cells == []
        assert next_id == 1
