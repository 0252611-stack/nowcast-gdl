"""Smoke tests del contrato Pydantic — confirma que la suite arranca."""

from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from app.schemas import (
    CellDebugDiagSchema,
    CellDebugSchema,
    CellDetectionSchema,
    HourlyForecast,
    NowcastResult,
    PointForecast,
    RadarCategory,
    RadarReading,
    TrackedCellSchema,
)

TZ_MX = ZoneInfo("America/Mexico_City")


def _hourly() -> HourlyForecast:
    return HourlyForecast(
        time=datetime(2026, 6, 10, 14, 0, tzinfo=TZ_MX),
        precipitation_mm=2.5,
        precipitation_probability=70,
        temperature_c=24.3,
        wind_speed_10m_kmh=18.0,
        wind_direction_10m_deg=200.0,
        wind_speed_700hPa_kmh=45.0,
        wind_direction_700hPa_deg=230.0,
    )


def test_point_forecast_valid():
    pf = PointForecast(
        point_id="centro",
        name="Centro GDL",
        lat=20.6767,
        lon=-103.3475,
        generated_at=datetime.now(timezone.utc),
        hourly=[_hourly()],
    )
    assert pf.point_id == "centro"
    assert len(pf.hourly) == 1
    assert pf.timezone == "America/Mexico_City"


def test_radar_reading_valid():
    rr = RadarReading(
        point_id="centro",
        dbz=35.5,
        category=RadarCategory.MODERADA_FUERTE,
        scan_time_utc=datetime.now(timezone.utc),
        frame_age_seconds=45.0,
        pixel_x=120,
        pixel_y=85,
    )
    assert rr.dbz == 35.5


def test_nowcast_result_valid():
    nr = NowcastResult(
        point_id="centro",
        raining_now=False,
        generated_at=datetime.now(timezone.utc),
    )
    assert nr.eta_minutes is None
    assert nr.method == "unknown"


def test_hourly_forecast_rejects_extra_fields():
    with pytest.raises(Exception):
        HourlyForecast(
            time=datetime(2026, 6, 10, 14, 0, tzinfo=TZ_MX),
            precipitation_mm=0.0,
            precipitation_probability=0,
            temperature_c=22.0,
            wind_speed_10m_kmh=10.0,
            wind_direction_10m_deg=90.0,
            wind_speed_700hPa_kmh=30.0,
            wind_direction_700hPa_deg=90.0,
            campo_desconocido="esto_debe_fallar",
        )


# ---------------------------------------------------------------------------
# Compuerta 3 — Capa 2+3: campos nuevos de NowcastResult y TrackedCellSchema
# ---------------------------------------------------------------------------

def test_nowcast_result_accepts_new_fields():
    """NowcastResult acepta cell_id, cell_age_minutes, leading_edge_distance_km."""
    nr = NowcastResult(
        point_id="centro",
        raining_now=False,
        generated_at=datetime.now(timezone.utc),
        method="cell_tracking",
        cell_id=7,
        cell_age_minutes=4.5,
        leading_edge_distance_km=12.3,
    )
    assert nr.cell_id == 7
    assert nr.cell_age_minutes == 4.5
    assert nr.leading_edge_distance_km == 12.3
    assert nr.method == "cell_tracking"


def test_nowcast_result_new_fields_nullable():
    """Los nuevos campos de tracking son None por defecto."""
    nr = NowcastResult(
        point_id="centro",
        raining_now=False,
        generated_at=datetime.now(timezone.utc),
    )
    assert nr.cell_id is None
    assert nr.cell_age_minutes is None
    assert nr.leading_edge_distance_km is None


def test_nowcast_result_extra_forbid_still_works():
    """extra='forbid' sigue activo: campo desconocido debe fallar."""
    with pytest.raises(Exception):
        NowcastResult(
            point_id="centro",
            raining_now=False,
            generated_at=datetime.now(timezone.utc),
            campo_desconocido="fallo_esperado",
        )


def test_tracked_cell_schema_valid():
    """TrackedCellSchema acepta un dict bien formado."""
    tc = TrackedCellSchema(
        id=3,
        lat=20.67,
        lon=-103.40,
        mean_dbz=35.0,
        area_px=500,
        velocity_kmh=45.0,
        bearing_deg=90.0,
        age_minutes=3.0,
        ring=[[20.68, -103.41], [20.68, -103.39], [20.66, -103.39], [20.66, -103.41]],
        track=[[20.67, -103.41], [20.67, -103.40]],
    )
    assert tc.id == 3
    assert tc.velocity_kmh == 45.0
    assert len(tc.ring) == 4


def test_tracked_cell_schema_empty_track():
    """track puede ser vacío (warmup — solo un frame)."""
    tc = TrackedCellSchema(
        id=1,
        lat=20.67,
        lon=-103.40,
        mean_dbz=25.0,
        area_px=100,
        velocity_kmh=0.0,
        bearing_deg=0.0,
        age_minutes=0.0,
        ring=[[20.68, -103.41], [20.66, -103.41], [20.66, -103.39]],
        track=[],
    )
    assert tc.track == []


def test_tracked_cell_schema_rejects_extra_fields():
    """extra='forbid' activo en TrackedCellSchema."""
    with pytest.raises(Exception):
        TrackedCellSchema(
            id=1, lat=20.0, lon=-103.0, mean_dbz=30.0, area_px=200,
            velocity_kmh=10.0, bearing_deg=90.0, age_minutes=1.5,
            ring=[], track=[],
            campo_extra="fallo",
        )


# ── Compuerta 2 — CellDetectionSchema, CellDebugDiagSchema, CellDebugSchema ──

def test_cell_detection_schema_valid():
    det = CellDetectionSchema(
        lat=20.67, lon=-103.40, area_px=300,
        mean_dbz=30.0, max_dbz=45.0,
        solidity=0.85, extent=0.60,
        ring=[[20.68, -103.41], [20.66, -103.41], [20.66, -103.39]],
    )
    assert det.solidity == pytest.approx(0.85)
    assert det.extent == pytest.approx(0.60)


def test_cell_detection_schema_rejects_extra():
    with pytest.raises(Exception):
        CellDetectionSchema(
            lat=20.67, lon=-103.40, area_px=300,
            mean_dbz=30.0, max_dbz=45.0,
            solidity=0.85, extent=0.60, ring=[],
            campo_extra="fallo",
        )


def test_cell_debug_diag_schema_valid():
    diag = CellDebugDiagSchema(
        n_det=5, n_alive=4, n_new=2, n_continued=2,
        n_purged=1, n_split=0, n_merge=1,
        gate_rejects=3, match_cost_mean=1.25,
        cell_min_px=30, dbz_threshold=18.0, match_max_km=15.0,
    )
    assert diag.n_det == 5
    assert diag.match_cost_mean == pytest.approx(1.25)


def test_cell_debug_diag_schema_none_cost():
    """match_cost_mean puede ser None (sin tracks previos → sin matching)."""
    diag = CellDebugDiagSchema(
        n_det=3, n_alive=3, n_new=3, n_continued=0,
        n_purged=0, n_split=0, n_merge=0,
        gate_rejects=0, match_cost_mean=None,
        cell_min_px=30, dbz_threshold=18.0, match_max_km=15.0,
    )
    assert diag.match_cost_mean is None


def test_cell_debug_schema_valid():
    det = CellDetectionSchema(
        lat=20.67, lon=-103.40, area_px=300,
        mean_dbz=30.0, max_dbz=45.0, solidity=0.85, extent=0.60, ring=[],
    )
    track = TrackedCellSchema(
        id=1, lat=20.67, lon=-103.40, mean_dbz=30.0, area_px=300,
        velocity_kmh=20.0, bearing_deg=90.0, age_minutes=1.5,
        ring=[], track=[], quality=0.65,
    )
    diag = CellDebugDiagSchema(
        n_det=1, n_alive=1, n_new=1, n_continued=0,
        n_purged=0, n_split=0, n_merge=0,
        gate_rejects=0, match_cost_mean=None,
        cell_min_px=30, dbz_threshold=18.0, match_max_km=15.0,
    )
    debug = CellDebugSchema(
        frame_time="2026-06-15T02:00:00+00:00",
        detections=[det],
        tracks=[track],
        diagnostics=diag,
    )
    assert len(debug.detections) == 1
    assert debug.tracks[0].quality == pytest.approx(0.65)


def test_tracked_cell_schema_accepts_quality():
    tc = TrackedCellSchema(
        id=1, lat=20.67, lon=-103.40, mean_dbz=30.0, area_px=300,
        velocity_kmh=20.0, bearing_deg=90.0, age_minutes=1.5,
        ring=[], track=[], quality=0.72,
    )
    assert tc.quality == pytest.approx(0.72)


def test_tracked_cell_schema_quality_defaults_zero():
    """quality es opcional; default 0.0 para retrocompatibilidad."""
    tc = TrackedCellSchema(
        id=1, lat=20.67, lon=-103.40, mean_dbz=30.0, area_px=300,
        velocity_kmh=20.0, bearing_deg=90.0, age_minutes=1.5,
        ring=[], track=[],
    )
    assert tc.quality == 0.0
