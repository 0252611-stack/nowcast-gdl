"""Smoke tests del contrato Pydantic — confirma que la suite arranca."""

from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from app.schemas import (
    HourlyForecast,
    NowcastResult,
    PointForecast,
    RadarCategory,
    RadarReading,
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
