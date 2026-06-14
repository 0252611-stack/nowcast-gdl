"""Tests del logging de aciertos: save_prediction, verify_predictions,
get_skill_metrics, purge_old_predictions y el endpoint /metrics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas import NowcastResult, RadarCategory, RadarReading
from app.storage import (
    get_skill_metrics,
    init_db,
    purge_old_predictions,
    save_prediction,
    save_reading,
    verify_predictions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)   # prediction time
T_RAIN = T0 + timedelta(minutes=30)                          # observed rain
T_VERIFY = T0 + timedelta(minutes=65)                        # now (horizon expired)
T_FUTURE = T0 + timedelta(minutes=10)                        # not yet expired


def _result(
    point_id: str = "centro",
    raining_now: bool = False,
    eta_minutes: int | None = None,
    method: str = "advection",
    horizon: int = 60,
    generated_at: datetime = T0,
) -> NowcastResult:
    return NowcastResult(
        point_id=point_id,
        raining_now=raining_now,
        eta_minutes=eta_minutes,
        confidence=0.7 if eta_minutes is not None else None,
        horizon_minutes=horizon,
        method=method,
        generated_at=generated_at,
    )


def _rain_reading(point_id: str = "centro", t: datetime = T_RAIN) -> RadarReading:
    return RadarReading(
        point_id=point_id,
        dbz=25.0,
        category=RadarCategory.LIGERA,
        scan_time_utc=t,
        frame_age_seconds=30.0,
        pixel_x=100,
        pixel_y=80,
    )


@pytest.fixture
def db(tmp_path):
    conn = init_db(tmp_path / "test_metrics.db")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# save_prediction
# ---------------------------------------------------------------------------

def test_save_prediction_persists_row(db):
    r = _result(eta_minutes=30)
    save_prediction(db, r)
    row = db.execute("SELECT * FROM nowcast_predictions").fetchone()
    assert row is not None
    assert row[1] == "centro"          # point_id
    assert row[4] == 1                 # predicted_rain


def test_save_prediction_no_rain(db):
    r = _result(eta_minutes=None)
    save_prediction(db, r)
    row = db.execute("SELECT predicted_rain FROM nowcast_predictions").fetchone()
    assert row[0] == 0


def test_save_prediction_raining_now_is_predicted(db):
    r = _result(raining_now=True, eta_minutes=0, method="radar_current")
    save_prediction(db, r)
    row = db.execute("SELECT raining_now, predicted_rain FROM nowcast_predictions").fetchone()
    assert row[0] == 1
    assert row[1] == 1


def test_save_prediction_derives_target_time(db):
    r = _result(eta_minutes=30, horizon=60, generated_at=T0)
    save_prediction(db, r)
    row = db.execute("SELECT target_time_utc, predicted_arrival_utc FROM nowcast_predictions").fetchone()
    target = datetime.fromisoformat(row[0])
    arrival = datetime.fromisoformat(row[1])
    # target = T0 + 60 min
    assert target.replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=60)
    # predicted_arrival = T0 + 30 min
    assert arrival.replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=30)


def test_save_prediction_no_arrival_when_no_eta(db):
    r = _result(eta_minutes=None)
    save_prediction(db, r)
    row = db.execute("SELECT predicted_arrival_utc FROM nowcast_predictions").fetchone()
    assert row[0] is None


# ---------------------------------------------------------------------------
# verify_predictions — cuatro outcomes
# ---------------------------------------------------------------------------

def test_verify_hit(db):
    save_prediction(db, _result(eta_minutes=30))   # predicted_rain=True
    save_reading(db, _rain_reading())              # observed rain at T_RAIN (in window)
    v = verify_predictions(db, T_VERIFY)
    assert v["count"] == 1
    assert v["hit"] == 1
    row = db.execute("SELECT outcome FROM nowcast_predictions").fetchone()
    assert row[0] == "hit"


def test_verify_false_alarm(db):
    save_prediction(db, _result(eta_minutes=30))   # predicted rain
    # no reading → no observed rain
    v = verify_predictions(db, T_VERIFY)
    assert v["count"] == 1
    assert v["false_alarm"] == 1
    assert db.execute("SELECT outcome FROM nowcast_predictions").fetchone()[0] == "false_alarm"


def test_verify_miss(db):
    save_prediction(db, _result(eta_minutes=None, method="no_approaching_cell"))  # predicted_rain=False
    save_reading(db, _rain_reading())              # but it rained
    v = verify_predictions(db, T_VERIFY)
    assert v["count"] == 1
    assert v["miss"] == 1
    assert db.execute("SELECT outcome FROM nowcast_predictions").fetchone()[0] == "miss"


def test_verify_correct_negative(db):
    save_prediction(db, _result(eta_minutes=None))  # predicted_rain=False
    # no rain observed
    v = verify_predictions(db, T_VERIFY)
    assert v["count"] == 1
    assert v["correct_negative"] == 1
    assert db.execute("SELECT outcome FROM nowcast_predictions").fetchone()[0] == "correct_negative"


def test_verify_skips_future_predictions(db):
    t_future_pred = T_VERIFY + timedelta(minutes=10)  # generated AFTER verify time
    save_prediction(db, _result(eta_minutes=30, generated_at=t_future_pred))
    v = verify_predictions(db, T_VERIFY)
    assert v["count"] == 0


def test_verify_ignores_rain_outside_window(db):
    """Lluvia registrada ANTES de la predicción no cuenta como 'observada'."""
    reading_before = _rain_reading(t=T0 - timedelta(minutes=5))
    save_prediction(db, _result(eta_minutes=30))
    save_reading(db, reading_before)
    verify_predictions(db, T_VERIFY)
    assert db.execute("SELECT outcome FROM nowcast_predictions").fetchone()[0] == "false_alarm"


def test_verify_lead_time_error(db):
    """Cuando hay eta y hay lluvia, registra el error de ETA."""
    save_prediction(db, _result(eta_minutes=30))   # predicción: lluvia en 30 min
    save_reading(db, _rain_reading(t=T0 + timedelta(minutes=40)))  # llegó en 40 min
    verify_predictions(db, T_VERIFY)
    row = db.execute("SELECT lead_time_error_min FROM nowcast_predictions").fetchone()
    # pred_arrival = T0+30, obs_arrival = T0+40 → error = (T0+30 - T0+40) = -10 min
    assert row[0] == pytest.approx(-10.0, abs=0.2)


def test_verify_does_not_reverify(db):
    """Llamar a verify_predictions dos veces no duplica el trabajo."""
    save_prediction(db, _result(eta_minutes=30))
    save_reading(db, _rain_reading())
    v1 = verify_predictions(db, T_VERIFY)
    v2 = verify_predictions(db, T_VERIFY + timedelta(minutes=5))
    assert v1["count"] == 1
    assert v2["count"] == 0


# ---------------------------------------------------------------------------
# get_skill_metrics
# ---------------------------------------------------------------------------

def _insert_verified(db, outcome: str, raining_now: bool = False, method: str = "advection"):
    """Inserta una predicción ya verificada directamente."""
    predicted_rain = 1 if outcome in ("hit", "false_alarm") else 0
    observed_raining = 1 if outcome in ("hit", "miss") else 0
    db.execute(
        """INSERT INTO nowcast_predictions
           (point_id, generated_at_utc, raining_now, predicted_rain,
            method, horizon_minutes, target_time_utc,
            verified_at_utc, observed_raining, outcome)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "centro",
            T0.isoformat(),
            int(raining_now),
            predicted_rain,
            method,
            60,
            (T0 + timedelta(minutes=60)).isoformat(),
            T_VERIFY.isoformat(),
            observed_raining,
            outcome,
        ),
    )
    db.commit()


def test_skill_metrics_empty(db):
    m = get_skill_metrics(db)
    assert m["verified"] == 0
    assert m["overall"]["total"] == 0
    assert m["overall"]["pod"] is None


def test_skill_metrics_known_set(db):
    _insert_verified(db, "hit")
    _insert_verified(db, "miss")
    _insert_verified(db, "false_alarm")
    _insert_verified(db, "correct_negative")

    m = get_skill_metrics(db)
    o = m["overall"]
    assert o["hits"] == 1
    assert o["misses"] == 1
    assert o["false_alarms"] == 1
    assert o["correct_negatives"] == 1
    assert o["total"] == 4
    assert o["pod"]      == pytest.approx(0.5,   abs=0.001)
    assert o["far"]      == pytest.approx(0.5,   abs=0.001)
    assert o["csi"]      == pytest.approx(1/3,   abs=0.001)
    assert o["accuracy"] == pytest.approx(0.5,   abs=0.001)


def test_skill_metrics_forecast_only_excludes_raining_now(db):
    """Una predicción emitida mientras llovía aparece en overall pero no en forecast_only."""
    _insert_verified(db, "hit", raining_now=True)   # raining_now → excluir de forecast_only
    _insert_verified(db, "hit", raining_now=False)

    m = get_skill_metrics(db)
    assert m["overall"]["total"] == 2
    assert m["forecast_only"]["total"] == 1


def test_skill_metrics_by_method(db):
    _insert_verified(db, "hit",   method="advection")
    _insert_verified(db, "false_alarm", method="advection")
    _insert_verified(db, "correct_negative", method="no_approaching_cell")

    m = get_skill_metrics(db)
    assert "advection" in m["by_method"]
    assert "no_approaching_cell" in m["by_method"]
    assert m["by_method"]["advection"]["total"] == 2


def test_skill_metrics_pending_count(db):
    save_prediction(db, _result(eta_minutes=30))   # pending
    _insert_verified(db, "hit")                    # verified
    m = get_skill_metrics(db)
    assert m["pending"] == 1
    assert m["verified"] == 1


# ---------------------------------------------------------------------------
# purge_old_predictions
# ---------------------------------------------------------------------------

def test_purge_old_predictions(db):
    db.execute(
        """INSERT INTO nowcast_predictions
           (point_id, generated_at_utc, raining_now, predicted_rain,
            method, horizon_minutes, target_time_utc, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("centro", T0.isoformat(), 0, 0, "advection", 60,
         (T0 + timedelta(hours=1)).isoformat(), "2020-01-01T00:00:00Z"),
    )
    db.commit()
    save_prediction(db, _result())   # reciente
    deleted = purge_old_predictions(db, retention_hours=1)
    assert deleted >= 1
    remaining = db.execute("SELECT COUNT(*) FROM nowcast_predictions").fetchone()[0]
    assert remaining == 1


# ---------------------------------------------------------------------------
# API endpoint /metrics
# ---------------------------------------------------------------------------

async def _noop(*args, **kwargs):
    pass


@pytest.fixture
def api_client(tmp_path):
    from app.main import app
    from app import storage
    from fastapi.testclient import TestClient
    from app.scheduler import RadarState

    conn = storage.init_db(tmp_path / "test_api_metrics.db")
    state = RadarState()

    with (
        patch("app.main.init_db", return_value=conn),
        patch("app.main.run_radar_loop", _noop),
        patch("app.main.run_forecast_loop", _noop),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.db = conn
            app.state.radar_state = state
            yield c, conn


def test_metrics_endpoint_returns_200(api_client):
    c, _ = api_client
    resp = c.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert "overall" in body
    assert "forecast_only" in body
    assert "pending" in body
    assert "verified" in body


def test_metrics_endpoint_verified_zero_when_empty(api_client):
    c, _ = api_client
    resp = c.get("/metrics")
    assert resp.json()["verified"] == 0
    assert resp.json()["overall"]["pod"] is None
