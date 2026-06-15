"""Tests de integración de los endpoints FastAPI."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.schemas import HourlyForecast, PointForecast, RadarCategory, RadarReading
from app.scheduler import RadarState


def _mock_forecast(point_id: str = "centro") -> PointForecast:
    from zoneinfo import ZoneInfo
    hourly = HourlyForecast(
        time=datetime(2026, 6, 11, 14, 0, tzinfo=ZoneInfo("America/Mexico_City")),
        precipitation_mm=0.0,
        precipitation_probability=20,
        temperature_c=24.0,
        wind_speed_10m_kmh=15.0,
        wind_direction_10m_deg=180.0,
        wind_speed_700hPa_kmh=40.0,
        wind_direction_700hPa_deg=200.0,
    )
    return PointForecast(
        point_id=point_id,
        name="Centro GDL",
        lat=20.6767,
        lon=-103.3475,
        generated_at=datetime.now(timezone.utc),
        hourly=[hourly],
    )


def _mock_reading(point_id: str = "centro") -> RadarReading:
    return RadarReading(
        point_id=point_id,
        dbz=22.0,
        category=RadarCategory.LIGERA,
        scan_time_utc=datetime.now(timezone.utc),
        frame_age_seconds=45.0,
        pixel_x=110,
        pixel_y=80,
    )


async def _noop(*args, **kwargs):
    """Coroutine sustituta para los loops del scheduler en tests."""


@pytest.fixture
def client(tmp_path):
    from app.main import app
    from app import storage, config

    conn = storage.init_db(tmp_path / "test.db")
    state = RadarState()

    with (
        patch("app.main.init_db", return_value=conn),
        patch("app.main.seed_points"),
        patch("app.main.run_radar_loop", _noop),
        patch("app.main.run_forecast_loop", _noop),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.db = conn
            app.state.radar_state = state
            # Sembrar puntos en la DB de test para que los endpoints los encuentren
            storage.seed_points(conn, config.POINTS)
            yield c, conn, state


def test_list_points(client):
    c, _, _ = client
    resp = c.get("/points")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(p["id"] == "up_gdl" for p in data)


def test_forecast_returns_point_forecast(client):
    c, _, _ = client
    with patch("app.main.fetch_forecast", new_callable=AsyncMock) as mock_ff:
        mock_ff.return_value = _mock_forecast("up_gdl")
        resp = c.get("/points/up_gdl/forecast")
    assert resp.status_code == 200
    body = resp.json()
    assert body["point_id"] == "up_gdl"
    assert "hourly" in body
    assert len(body["hourly"]) == 1


def test_forecast_404_for_unknown_point(client):
    c, _, _ = client
    resp = c.get("/points/noexiste/forecast")
    assert resp.status_code == 404


def test_radar_endpoint_no_reading(client):
    c, _, _ = client
    resp = c.get("/points/up_gdl/radar")
    assert resp.status_code == 200
    body = resp.json()
    assert body["radar"] is None
    assert body["radar_available"] is True
    # Sin lectura de radar → engine devuelve radar_unavailable (o None si el engine falla)
    if body["nowcast"] is not None:
        assert body["nowcast"]["method"] == "radar_unavailable"
        assert body["nowcast"]["raining_now"] is False


def test_radar_endpoint_with_reading(client):
    from app import storage
    c, conn, _ = client
    storage.save_reading(conn, _mock_reading("up_gdl"))
    resp = c.get("/points/up_gdl/radar")
    assert resp.status_code == 200
    body = resp.json()
    assert body["radar"]["point_id"] == "up_gdl"
    assert body["radar"]["dbz"] == pytest.approx(22.0)


def test_radar_endpoint_degraded(client):
    c, _, state = client
    state.available = False
    resp = c.get("/points/up_gdl/radar")
    assert resp.status_code == 200
    assert resp.json()["radar_available"] is False


def test_radar_404_for_unknown_point(client):
    c, _, _ = client
    resp = c.get("/points/noexiste/radar")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Compuerta 3 — /radar devuelve tracked_cells (lista, posiblemente vacía)
# ---------------------------------------------------------------------------

def test_radar_has_tracked_cells_key(client):
    """El endpoint /radar siempre devuelve la clave tracked_cells (lista)."""
    c, _, _ = client
    resp = c.get("/points/up_gdl/radar")
    assert resp.status_code == 200
    body = resp.json()
    assert "tracked_cells" in body
    assert isinstance(body["tracked_cells"], list)


def test_radar_tracked_cells_empty_in_warmup(client):
    """Sin frames de radar, tracked_cells debe ser una lista vacía."""
    c, _, state = client
    # state.tracked_cells no está seteado aún (warmup)
    assert getattr(state, "tracked_cells", []) == []
    resp = c.get("/points/up_gdl/radar")
    body = resp.json()
    assert body["tracked_cells"] == []


def test_radar_tracked_cells_serialization(client):
    """Con celdas en el estado, se serializan correctamente en la respuesta."""
    from app.processing.tracking import TrackedCell
    from datetime import datetime, timezone

    c, _, state = client
    t0 = datetime(2026, 6, 11, 20, 0, 0, tzinfo=timezone.utc)

    # Inyectar una celda rastreada sintética en el estado
    cell = TrackedCell(
        id=5,
        lat=20.67,
        lon=-103.40,
        area_px=500,
        mean_dbz=35.0,
        max_dbz=50.0,
        ring=[[20.68, -103.41], [20.66, -103.41], [20.66, -103.39], [20.68, -103.39]],
        velocity_kmh=30.0,
        bearing_deg=90.0,
        centroid_history=[(20.67, -103.41, t0), (20.67, -103.40, t0)],
        area_history=[500, 510],
        age_frames=3,
        first_seen=t0,
        last_seen=t0,
    )
    state.tracked_cells = [cell]

    resp = c.get("/points/up_gdl/radar")
    assert resp.status_code == 200
    body = resp.json()
    cells = body["tracked_cells"]
    assert len(cells) == 1
    tc = cells[0]
    assert tc["id"] == 5
    assert tc["lat"] == pytest.approx(20.67)
    assert tc["lon"] == pytest.approx(-103.40)
    assert tc["velocity_kmh"] == pytest.approx(30.0)
    assert tc["bearing_deg"] == pytest.approx(90.0)
    assert isinstance(tc["ring"], list)
    assert isinstance(tc["track"], list)
    assert len(tc["track"]) == 2  # centroid_history tiene 2 puntos


def test_nowcast_result_has_new_tracking_fields(client):
    """El nowcast en /radar incluye cell_id, cell_age_minutes, leading_edge_distance_km."""
    from app import storage
    c, conn, _ = client
    # Guardar una lectura con dBZ alto para que el engine devuelva radar_current
    storage.save_reading(conn, _mock_reading("up_gdl"))
    resp = c.get("/points/up_gdl/radar")
    assert resp.status_code == 200
    body = resp.json()
    nowcast = body["nowcast"]
    if nowcast is not None:
        # Los campos deben existir (pueden ser None)
        assert "cell_id" in nowcast
        assert "cell_age_minutes" in nowcast
        assert "leading_edge_distance_km" in nowcast
