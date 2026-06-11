"""Tests para backend/app/sources/rainviewer.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.sources.rainviewer import _latlon_to_tile, fetch_tile_url

# ---------------------------------------------------------------------------
# _latlon_to_tile
# ---------------------------------------------------------------------------

def test_latlon_to_tile_gdl_zoom7():
    x, y = _latlon_to_tile(20.68, -103.44, zoom=7)
    assert x == 27
    assert y == 56


def test_latlon_to_tile_zoom0():
    x, y = _latlon_to_tile(0.0, 0.0, zoom=0)
    assert x == 0
    assert y == 0


def test_latlon_to_tile_antimeridian():
    x, _ = _latlon_to_tile(0.0, 179.99, zoom=1)
    assert x == 1


# ---------------------------------------------------------------------------
# fetch_tile_url
# ---------------------------------------------------------------------------

_FAKE_API_RESPONSE = {
    "host": "https://tilecache.rainviewer.com",
    "radar": {
        "past": [
            {"time": 1718000000, "path": "/v2/radar/1718000000"},
            {"time": 1718000600, "path": "/v2/radar/1718000600"},
        ]
    },
}


def _mock_client(json_data: dict) -> AsyncMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.mark.anyio
async def test_fetch_tile_url_success():
    client = _mock_client(_FAKE_API_RESPONSE)
    url = await fetch_tile_url(client, lat=20.68, lon=-103.44, zoom=7)

    assert url is not None
    # usa el frame más reciente
    assert "/v2/radar/1718000600/" in url
    # tile correcto para GDL zoom=7
    assert "/256/7/27/56/" in url
    # color 4 por defecto
    assert "/4/1_1.png" in url


@pytest.mark.anyio
async def test_fetch_tile_url_color_param():
    client = _mock_client(_FAKE_API_RESPONSE)
    url = await fetch_tile_url(client, lat=20.68, lon=-103.44, zoom=7, color=2)
    assert "/2/1_1.png" in url


@pytest.mark.anyio
async def test_fetch_tile_url_no_past_frames():
    data = {"host": "https://tilecache.rainviewer.com", "radar": {"past": []}}
    client = _mock_client(data)
    url = await fetch_tile_url(client, lat=20.68, lon=-103.44)
    assert url is None


@pytest.mark.anyio
async def test_fetch_tile_url_api_http_error():
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "503", request=MagicMock(), response=MagicMock()
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=resp)
    url = await fetch_tile_url(client, lat=20.68, lon=-103.44)
    assert url is None


@pytest.mark.anyio
async def test_fetch_tile_url_network_error():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
    url = await fetch_tile_url(client, lat=20.68, lon=-103.44)
    assert url is None


# ---------------------------------------------------------------------------
# Integración mínima: /points/{id}/radar incluye rainviewer_url=null
# cuando radar está disponible (caso normal)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.schemas import HourlyForecast, PointForecast, RadarReading, RadarCategory
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _mock_forecast(point_id: str) -> PointForecast:
    tz = ZoneInfo("America/Mexico_City")
    hourly = HourlyForecast(
        time=datetime.now(tz=tz),
        precipitation_mm=0.0,
        precipitation_probability=5,
        temperature_c=25.0,
        wind_speed_10m_kmh=10.0,
        wind_direction_10m_deg=180,
        wind_speed_700hPa_kmh=20.0,
        wind_direction_700hPa_deg=180,
    )
    return PointForecast(
        point_id=point_id, name="UP GDL", lat=20.68, lon=-103.44,
        generated_at=datetime.now(timezone.utc),
        timezone="America/Mexico_City", hourly=[hourly],
    )


def _mock_reading(point_id: str) -> RadarReading:
    return RadarReading(
        point_id=point_id,
        dbz=8.1,
        category=RadarCategory.RUIDO,
        scan_time_utc=datetime.now(timezone.utc),
        frame_age_seconds=30.0,
        pixel_x=100,
        pixel_y=100,
    )


def test_radar_endpoint_has_rainviewer_url_field():
    """rainviewer_url está presente en la respuesta (null cuando IAM disponible)."""
    with (
        patch("app.main.get_latest_reading", return_value=_mock_reading("up_gdl")),
        patch("app.main.get_recent_frames", return_value=[]),
        patch("app.main.fetch_forecast", new_callable=AsyncMock) as mock_ff,
        patch("app.main.estimate_arrival", return_value=None),
    ):
        mock_ff.return_value = _mock_forecast("up_gdl")
        with TestClient(app) as c:
            resp = c.get("/points/up_gdl/radar")
    assert resp.status_code == 200
    body = resp.json()
    assert "rainviewer_url" in body
    assert body["rainviewer_url"] is None  # IAM disponible → no se llama RainViewer
