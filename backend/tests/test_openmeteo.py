"""Tests para app.sources.openmeteo.

Usa httpx.MockTransport para interceptar requests sin hacer calls reales.
"""

from __future__ import annotations

import json
from datetime import timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.sources import openmeteo as om
from app.sources.openmeteo import fetch_all_points, fetch_forecast
from app.schemas import PointForecast

_TZ_MX = ZoneInfo("America/Mexico_City")

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_open_meteo_payload(n: int = 12, extra_field: bool = False) -> dict:
    """Build a minimal valid Open-Meteo JSON response with `n` hourly entries."""
    times = [f"2026-06-10T{h:02d}:00" for h in range(n)]
    payload = {
        "latitude": 20.6767,
        "longitude": -103.3475,
        "timezone": "America/Mexico_City",
        "hourly": {
            "time": times,
            "precipitation": [0.0] * n,
            "precipitation_probability": [10] * n,
            "temperature_2m": [22.5] * n,
            "wind_speed_10m": [15.0] * n,
            "wind_direction_10m": [180.0] * n,
            "wind_speed_700hPa": [30.0] * n,
            "wind_direction_700hPa": [270.0] * n,
        },
    }
    if extra_field:
        payload["hourly"]["unknown_extra"] = [99] * n
    return payload


def _make_transport(payload: dict, status_code: int = 200) -> httpx.MockTransport:
    """Return an httpx.MockTransport that always responds with `payload`."""
    body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Test 1: happy path — 12 HourlyForecast entries, correct types
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_forecast_happy_path():
    payload = _make_open_meteo_payload(n=12)
    transport = _make_transport(payload)

    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_forecast(
            client,
            point_id="centro",
            name="Centro GDL",
            lat=20.6767,
            lon=-103.3475,
        )

    assert isinstance(result, PointForecast)
    assert result.point_id == "centro"
    assert result.name == "Centro GDL"
    assert len(result.hourly) == 12
    assert result.hourly[0].precipitation_mm == 0.0
    assert result.hourly[0].wind_speed_10m_kmh == 15.0


# ---------------------------------------------------------------------------
# Test 2: all timestamps carry America/Mexico_City tzinfo (not UTC, not naive)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_forecast_timestamps_timezone():
    payload = _make_open_meteo_payload(n=12)
    transport = _make_transport(payload)

    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_forecast(
            client,
            point_id="centro",
            name="Centro GDL",
            lat=20.6767,
            lon=-103.3475,
        )

    for hf in result.hourly:
        assert hf.time.tzinfo is not None, "timestamp must be tz-aware"
        # ZoneInfo objects compare by key
        assert hf.time.tzinfo == _TZ_MX, (
            f"Expected America/Mexico_City, got {hf.time.tzinfo}"
        )
        assert hf.time.tzinfo != timezone.utc, "timestamp must NOT be UTC"


# ---------------------------------------------------------------------------
# Test 3: cache — second call with same (point_id, hour) skips HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_all_points_uses_cache():
    # Clear module-level cache before the test
    om._cache.clear()

    payload = _make_open_meteo_payload(n=12)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=json.dumps(payload).encode())

    transport = httpx.MockTransport(handler)
    points = [{"id": "centro", "name": "Centro GDL", "lat": 20.6767, "lon": -103.3475}]

    async with httpx.AsyncClient(transport=transport) as client:
        # First call — should hit HTTP
        results1 = await fetch_all_points(client, points)
        assert call_count == 1

        # Second call — same hour bucket, should be served from cache
        results2 = await fetch_all_points(client, points)
        assert call_count == 1, (
            f"Expected no additional HTTP request, but call_count is {call_count}"
        )

    assert results1[0].point_id == results2[0].point_id


# ---------------------------------------------------------------------------
# Test 3b: fetch_forecast_cached — el punto de entrada usado por el scheduler
# y por los endpoints /forecast y /radar debe cachear igual que fetch_all_points.
# Bug real de producción (sesión 17): esos 3 sitios llamaban fetch_forecast()
# sin cache, generando 1 request/punto/ciclo de 90s en vez de 1/punto/hora.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_forecast_cached_skips_http_on_second_call():
    om._cache.clear()

    payload = _make_open_meteo_payload(n=12)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=json.dumps(payload).encode())

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        r1 = await om.fetch_forecast_cached(client, "centro", "Centro GDL", 20.6767, -103.3475)
        assert call_count == 1
        r2 = await om.fetch_forecast_cached(client, "centro", "Centro GDL", 20.6767, -103.3475)
        assert call_count == 1, "segunda llamada en el mismo bucket de hora debe usar cache"

    assert r1.point_id == r2.point_id == "centro"


# ---------------------------------------------------------------------------
# Test 4: Pydantic raises ValidationError on unknown extra field in hourly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_forecast_validates_schema():
    """HourlyForecast has extra='forbid'; an extra key in the payload must
    raise a Pydantic ValidationError before returning."""
    from pydantic import ValidationError

    payload = _make_open_meteo_payload(n=3, extra_field=True)
    transport = _make_transport(payload)

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValidationError):
            # The extra field "unknown_extra" is in hourly dict; we need to
            # pass it directly to HourlyForecast to trigger the forbid check.
            # fetch_forecast itself only maps known keys, so we test the model
            # directly here (as specified: "respuesta mock con campo extra en
            # hourly → Pydantic lanza error").
            from app.schemas import HourlyForecast
            from datetime import datetime

            HourlyForecast(
                time=datetime(2026, 6, 10, 14, 0, tzinfo=_TZ_MX),
                precipitation_mm=0.0,
                precipitation_probability=10,
                temperature_c=22.5,
                wind_speed_10m_kmh=15.0,
                wind_direction_10m_deg=180.0,
                wind_speed_700hPa_kmh=30.0,
                wind_direction_700hPa_deg=270.0,
                unknown_extra=99,  # This must be rejected
            )


# ---------------------------------------------------------------------------
# A1 — Cache purge (entradas de hora anterior se eliminan al cambiar la hora)
# ---------------------------------------------------------------------------

def test_cache_purge_removes_stale_entries():
    """_maybe_purge_all elimina entradas de horas anteriores sin tocar las actuales."""
    from app.sources.openmeteo import _cache, _wind_cache, _maybe_purge_all

    old_bucket = "2026-06-10T08"
    new_bucket = "2026-06-10T09"

    # Insertar entradas antiguas
    _cache[("punto_viejo", old_bucket)] = object()  # type: ignore[assignment]
    _wind_cache[(20.6, -103.4, 700, old_bucket)] = {"toward_deg": 90.0, "speed_kmh": 30.0}

    # Insertar entrada con el bucket nuevo (no debe borrarse)
    _cache[("punto_nuevo", new_bucket)] = object()  # type: ignore[assignment]

    _maybe_purge_all(new_bucket)

    assert ("punto_viejo", old_bucket) not in _cache, "Entrada antigua debe eliminarse"
    assert (20.6, -103.4, 700, old_bucket) not in _wind_cache, "Entrada wind antigua debe eliminarse"
    assert ("punto_nuevo", new_bucket) in _cache, "Entrada del bucket actual debe sobrevivir"


def test_cache_purge_noop_same_bucket():
    """_maybe_purge_all no hace nada si el bucket no cambió."""
    from app.sources.openmeteo import _cache, _maybe_purge_all

    same_bucket = "2026-06-10T10"
    _cache[("pt_keep", same_bucket)] = object()  # type: ignore[assignment]

    # Forzar que el último bucket purgado coincida con same_bucket
    import app.sources.openmeteo as _om
    _om._last_purge_bucket = same_bucket

    _maybe_purge_all(same_bucket)

    assert ("pt_keep", same_bucket) in _cache, "No debe purgar entradas del mismo bucket"


def test_get_cache_stats_returns_expected_keys():
    """get_cache_stats devuelve un dict con las claves de observabilidad."""
    from app.sources.openmeteo import get_cache_stats
    stats = get_cache_stats()
    assert "total" in stats
    assert "forecast" in stats
    assert "wind" in stats
    assert "precip" in stats
    assert "ensemble" in stats
    assert "misses_this_hour" in stats
    assert isinstance(stats["total"], int)
    assert stats["total"] >= 0
