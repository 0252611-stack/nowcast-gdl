"""Tests para CRUD de puntos, historial de predicciones y auth admin."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import config
from app.schemas import (
    HourlyForecast,
    NowcastResult,
    PointForecast,
    RadarCategory,
    RadarReading,
)
from app.scheduler import RadarState
from app.storage import (
    add_point,
    delete_point,
    get_predictions,
    init_db,
    list_points,
    save_prediction,
    seed_points,
    update_point,
)


# ---------------------------------------------------------------------------
# Fixture de DB en memoria
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()


_SEED = [
    {"id": "p1", "name": "Punto Uno", "lat": 20.68, "lon": -103.44},
    {"id": "p2", "name": "Punto Dos", "lat": 20.71, "lon": -103.40},
]


# ---------------------------------------------------------------------------
# seed_points
# ---------------------------------------------------------------------------

def test_seed_points_inserts_on_empty(db):
    seed_points(db, _SEED)
    pts = list_points(db)
    assert len(pts) == 2
    assert pts[0]["id"] == "p1"


def test_seed_points_idempotente(db):
    seed_points(db, _SEED)
    seed_points(db, _SEED)   # segunda llamada no duplica
    assert len(list_points(db)) == 2


# ---------------------------------------------------------------------------
# list_points / add_point / update_point / delete_point
# ---------------------------------------------------------------------------

def test_list_points_empty(db):
    assert list_points(db) == []


def test_add_and_list_point(db):
    add_point(db, "pto", "Nombre", 20.1, -103.5)
    pts = list_points(db)
    assert len(pts) == 1
    assert pts[0]["id"] == "pto"
    assert pts[0]["lat"] == pytest.approx(20.1)


def test_add_duplicate_raises(db):
    add_point(db, "dup", "A", 20.0, -103.0)
    with pytest.raises(sqlite3.IntegrityError):
        add_point(db, "dup", "B", 21.0, -104.0)


def test_update_point_full(db):
    add_point(db, "x", "Orig", 10.0, -100.0)
    updated = update_point(db, "x", "Nuevo", 11.0, -101.0)
    assert updated["name"] == "Nuevo"
    assert updated["lat"] == pytest.approx(11.0)


def test_update_point_partial(db):
    add_point(db, "y", "Orig", 10.0, -100.0)
    updated = update_point(db, "y", "Solo nombre", None, None)
    assert updated["name"] == "Solo nombre"
    assert updated["lat"] == pytest.approx(10.0)


def test_update_point_not_found(db):
    assert update_point(db, "nope", "X", None, None) is None


def test_delete_point_existing(db):
    add_point(db, "del", "D", 20.0, -103.0)
    assert delete_point(db, "del") is True
    assert list_points(db) == []


def test_delete_point_not_found(db):
    assert delete_point(db, "noexiste") is False


# ---------------------------------------------------------------------------
# get_predictions
# ---------------------------------------------------------------------------

def _make_nowcast(point_id: str = "p1") -> NowcastResult:
    from zoneinfo import ZoneInfo
    return NowcastResult(
        point_id=point_id,
        raining_now=False,
        eta_minutes=15,
        confidence=0.7,
        horizon_minutes=60,
        cell_speed_kmh=40.0,
        cell_bearing_deg=270.0,
        generated_at=datetime.now(ZoneInfo("America/Mexico_City")),
        method="advection",
    )


def test_get_predictions_empty(db):
    assert get_predictions(db) == []


def test_get_predictions_order_and_limit(db):
    seed_points(db, _SEED)
    for _ in range(5):
        save_prediction(db, _make_nowcast("p1"))
    rows = get_predictions(db, limit=3)
    assert len(rows) == 3


def test_get_predictions_filter_by_point(db):
    seed_points(db, _SEED)
    save_prediction(db, _make_nowcast("p1"))
    save_prediction(db, _make_nowcast("p2"))
    rows = get_predictions(db, point_id="p1")
    assert all(r["point_id"] == "p1" for r in rows)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# cell_lat / cell_lon / bearing en NowcastResult (contrato de schema)
# ---------------------------------------------------------------------------

def test_nowcast_result_cell_fields_default_none():
    from zoneinfo import ZoneInfo
    result = NowcastResult(
        point_id="x",
        raining_now=False,
        generated_at=datetime.now(ZoneInfo("America/Mexico_City")),
        method="no_echo",
    )
    assert result.cell_lat is None
    assert result.cell_lon is None
    assert result.bearing_cell_to_point_deg is None


def test_nowcast_result_cell_fields_set():
    from zoneinfo import ZoneInfo
    result = NowcastResult(
        point_id="x",
        raining_now=False,
        generated_at=datetime.now(ZoneInfo("America/Mexico_City")),
        method="advection",
        cell_lat=20.68,
        cell_lon=-103.44,
        bearing_cell_to_point_deg=135.0,
    )
    assert result.cell_lat == pytest.approx(20.68)
    assert result.bearing_cell_to_point_deg == pytest.approx(135.0)


# ---------------------------------------------------------------------------
# Fixture de API para tests de auth y endpoints nuevos
# ---------------------------------------------------------------------------

async def _noop(*args, **kwargs):
    pass


def _mock_forecast(point_id: str = "up_gdl") -> PointForecast:
    from zoneinfo import ZoneInfo
    hourly = HourlyForecast(
        time=datetime(2026, 6, 11, 14, 0, tzinfo=ZoneInfo("America/Mexico_City")),
        precipitation_mm=0.0,
        precipitation_probability=20,
        temperature_c=25.0,
        wind_speed_10m_kmh=15.0,
        wind_direction_10m_deg=180.0,
        wind_speed_700hPa_kmh=40.0,
        wind_direction_700hPa_deg=200.0,
    )
    return PointForecast(
        point_id=point_id,
        name="UP GDL",
        lat=20.68,
        lon=-103.44,
        generated_at=datetime.now(timezone.utc),
        hourly=[hourly],
    )


@pytest.fixture
def api(tmp_path):
    from app.main import app
    from app import storage

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
            # Sembrar puntos directamente
            storage.seed_points(conn, config.POINTS)
            yield c, conn, state


# ---------------------------------------------------------------------------
# Tests de auth admin
# ---------------------------------------------------------------------------

def test_create_point_without_token_returns_401_or_503(api, monkeypatch):
    c, _, _ = api
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret123")
    resp = c.post("/points", json={"id": "nuevo", "name": "Nuevo", "lat": 20.7, "lon": -103.5})
    assert resp.status_code == 401


def test_create_point_wrong_token_returns_401(api, monkeypatch):
    c, _, _ = api
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret123")
    resp = c.post(
        "/points",
        json={"id": "nuevo", "name": "Nuevo", "lat": 20.7, "lon": -103.5},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_create_point_correct_token_returns_201(api, monkeypatch):
    c, _, _ = api
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret123")
    resp = c.post(
        "/points",
        json={"id": "nuevo", "name": "Nuevo", "lat": 20.7, "lon": -103.5},
        headers={"X-Admin-Token": "secret123"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "nuevo"


def test_create_point_no_server_token_returns_503(api, monkeypatch):
    c, _, _ = api
    monkeypatch.setattr(config, "ADMIN_TOKEN", None)
    resp = c.post(
        "/points",
        json={"id": "nuevo", "name": "Nuevo", "lat": 20.7, "lon": -103.5},
        headers={"X-Admin-Token": "cualquiera"},
    )
    assert resp.status_code == 503


def test_delete_point_correct_token(api, monkeypatch):
    c, conn, _ = api
    monkeypatch.setattr(config, "ADMIN_TOKEN", "tok")
    add_point(conn, "tmp", "Temp", 20.0, -103.0)
    resp = c.delete("/points/tmp", headers={"X-Admin-Token": "tok"})
    assert resp.status_code == 204


def test_update_point_correct_token(api, monkeypatch):
    c, conn, _ = api
    monkeypatch.setattr(config, "ADMIN_TOKEN", "tok")
    add_point(conn, "upd", "Original", 20.0, -103.0)
    resp = c.put(
        "/points/upd",
        json={"name": "Actualizado"},
        headers={"X-Admin-Token": "tok"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Actualizado"


# ---------------------------------------------------------------------------
# Endpoint /predictions
# ---------------------------------------------------------------------------

def test_predictions_endpoint_returns_list(api):
    c, _, _ = api
    resp = c.get("/predictions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_predictions_endpoint_filter_by_point(api):
    c, conn, _ = api
    save_prediction(conn, _make_nowcast("up_gdl"))
    resp = c.get("/predictions?point_id=up_gdl")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert all(r["point_id"] == "up_gdl" for r in data)
