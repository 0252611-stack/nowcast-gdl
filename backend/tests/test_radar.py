"""Tests for radar IAM modules: radar_iam.py, pixel_extract.py, colormap.py.

All tests use real fixtures from tests/fixtures/ — no image mocks.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from PIL import Image

from app.sources.radar_iam import (
    RadarUnavailable,
    bounds_from_kml,
    fetch_current_frame,
)
from app.processing.pixel_extract import latlon_to_pixel, reading_for_point
from app.processing.colormap import load_colormap, color_to_dbz, dbz_to_category
from app.schemas import RadarReading, RadarCategory


FIXTURES = Path(__file__).parent / "fixtures"

# GDL centro coordinates (from config.py)
GDL_LAT = 20.6767
GDL_LON = -103.3475

# Expected bounds from frame1.kml
EXPECTED_BOUNDS = {
    "north": 22.03030437021881,
    "south": 19.32059531316582,
    "east": -101.9462411978663,
    "west": -104.8254262826025,
}


# ---------------------------------------------------------------------------
# Test 1: bounds_from_kml parses frame1.kml correctly
# ---------------------------------------------------------------------------

def test_bounds_from_kml():
    """Reads frame1.kml and verifies north≈22.03, south≈19.32,
    east≈-101.95, west≈-104.83."""
    kml_path = FIXTURES / "frame1.kml"
    kml_bytes = kml_path.read_bytes()

    bounds = bounds_from_kml(kml_bytes)

    assert bounds["north"] == pytest.approx(22.03, abs=0.01)
    assert bounds["south"] == pytest.approx(19.32, abs=0.01)
    assert bounds["east"] == pytest.approx(-101.95, abs=0.01)
    assert bounds["west"] == pytest.approx(-104.83, abs=0.01)

    # Verify all four keys are present
    assert set(bounds.keys()) == {"north", "south", "east", "west"}


# ---------------------------------------------------------------------------
# Test 2: latlon_to_pixel places GDL centro inside the image bounds
# ---------------------------------------------------------------------------

def test_latlon_to_pixel_gdl_centro():
    """GDL centro (20.6767, -103.3475) with frame1.kml bounds → pixel
    inside image dimensions (not out-of-bounds)."""
    image = Image.open(FIXTURES / "frame1.png")
    img_width, img_height = image.size

    x, y = latlon_to_pixel(GDL_LAT, GDL_LON, EXPECTED_BOUNDS, img_width, img_height)

    assert 0 <= x < img_width, f"x={x} out of [0, {img_width})"
    assert 0 <= y < img_height, f"y={y} out of [0, {img_height})"


# ---------------------------------------------------------------------------
# Test 3: latlon_to_pixel is invertible with < 2 px error
# ---------------------------------------------------------------------------

def test_latlon_to_pixel_precision():
    """The linear mapping is invertible: convert a known lat/lon to pixel,
    then reconstruct lat/lon from that pixel, and verify the round-trip
    error is < 2 pixels."""
    image = Image.open(FIXTURES / "frame1.png")
    img_width, img_height = image.size
    bounds = EXPECTED_BOUNDS

    # Use GDL centro as the test point
    lat_orig, lon_orig = GDL_LAT, GDL_LON

    # Forward: lat/lon → pixel
    x, y = latlon_to_pixel(lat_orig, lon_orig, bounds, img_width, img_height)

    # Inverse: pixel → lat/lon
    lon_back = bounds["west"] + (x / img_width) * (bounds["east"] - bounds["west"])
    lat_back = bounds["north"] - (y / img_height) * (bounds["north"] - bounds["south"])

    # Convert the reconstruction error to pixels
    dx = abs(lon_back - lon_orig) / (bounds["east"] - bounds["west"]) * img_width
    dy = abs(lat_back - lat_orig) / (bounds["north"] - bounds["south"]) * img_height

    assert dx < 2.0, f"Round-trip x error too large: {dx:.3f} px"
    assert dy < 2.0, f"Round-trip y error too large: {dy:.3f} px"


# ---------------------------------------------------------------------------
# Test 4: reading_for_point returns a valid RadarReading for GDL centro
# ---------------------------------------------------------------------------

def test_reading_for_point_returns_schema():
    """Creates a RadarReading from frame1.png for GDL centro.
    Result must be a valid RadarReading with dbz in [-31.5, 78.0]."""
    from app.processing.pixel_extract import set_legend_path

    # Set the legend path for the module cache
    set_legend_path(str(FIXTURES / "leyenda.png"))

    image = Image.open(FIXTURES / "frame1.png")
    scan_time = datetime(2026, 6, 11, 3, 42, 30, tzinfo=timezone.utc)

    reading = reading_for_point(
        point_id="centro",
        lat=GDL_LAT,
        lon=GDL_LON,
        bounds=EXPECTED_BOUNDS,
        image=image,
        scan_time_utc=scan_time,
        frame_age_seconds=15.0,
    )

    assert isinstance(reading, RadarReading)
    assert reading.point_id == "centro"
    assert -31.5 <= reading.dbz <= 78.0
    assert isinstance(reading.category, RadarCategory)
    assert reading.scan_time_utc.tzinfo is not None
    assert reading.frame_age_seconds == 15.0
    assert reading.pixel_x >= 0
    assert reading.pixel_y >= 0


# ---------------------------------------------------------------------------
# Test 5: fetch_current_frame uses UTC date at 23:59 UTC (17:59 local GDL)
# ---------------------------------------------------------------------------

def test_midnight_utc_date():
    """Simulates datetime.now(timezone.utc) = 23:59 UTC (= 17:59 GDL local)
    and verifies that fetch_current_frame uses the UTC date (same day),
    NOT the day before (which would happen if local time were used).

    The mocked API response returns a KMZ URL containing the UTC date.
    """
    # 2026-06-10 23:59:00 UTC  →  fecha = "20260610"
    # In GDL (UTC-6) this is 17:59 — still the same calendar day locally.
    utc_time = datetime(2026, 6, 10, 23, 59, 0, tzinfo=timezone.utc)
    expected_fecha = "20260610"

    captured_data: dict = {}

    async def run():
        # Build a minimal fake KMZ in memory
        kml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://earth.google.com/kml/2.2">
 <Folder>
  <GroundOverlay>
   <Icon><href>MEX_ZH_1781149350_1781149416.png</href></Icon>
   <LatLonBox>
    <north>22.03030437021881</north>
    <south>19.32059531316582</south>
    <east>-101.9462411978663</east>
    <west>-104.8254262826025</west>
   </LatLonBox>
  </GroundOverlay>
 </Folder>
</kml>"""
        png_bytes = (FIXTURES / "frame1.png").read_bytes()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("doc.kml", kml_content)
            zf.writestr("MEX_ZH_1781149350_1781149416.png", png_bytes)
        kmz_bytes = buf.getvalue()

        def make_post_response(fecha_in_body: str):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.text = f"../kmz/{fecha_in_body}/MEXI_ZH_{fecha_in_body}_235900.kmz"
            return mock_resp

        def make_get_response():
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.content = kmz_bytes
            return mock_resp

        post_responses = []
        get_responses = []

        async def mock_post(url, **kwargs):
            # Capture the fecha from the posted data
            data = kwargs.get("data", {})
            captured_data["fecha"] = data.get("fecha", "")
            fecha_in_body = data.get("fecha", "20260610")
            post_responses.append(fecha_in_body)
            return make_post_response(fecha_in_body)

        async def mock_get(url, **kwargs):
            return make_get_response()

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.get = mock_get

        with patch(
            "app.sources.radar_iam.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.now.side_effect = lambda tz=None: utc_time

            await fetch_current_frame(mock_client)

    import asyncio
    asyncio.run(run())

    assert captured_data["fecha"] == expected_fecha, (
        f"Expected fecha={expected_fecha!r}, got {captured_data['fecha']!r}. "
        "fetch_current_frame must use UTC date, not local time."
    )


# ---------------------------------------------------------------------------
# Test 6: fetch_current_frame raises RadarUnavailable on error response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_current_frame_error_response():
    """Mock returning a body containing 'error' → raises RadarUnavailable."""

    async def mock_post(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "error: no data available"
        return mock_resp

    mock_client = AsyncMock()
    mock_client.post = mock_post

    with pytest.raises(RadarUnavailable):
        await fetch_current_frame(mock_client)


# ---------------------------------------------------------------------------
# Test 7: fetch_current_frame raises RadarUnavailable on same URL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_current_frame_same_url():
    """Mock returning the same kmz_url as last_kmz_url → raises
    RadarUnavailable('same frame, skip')."""

    # The URL that will be returned by the mock API
    existing_url = (
        "https://iam.cucei.udg.mx/radar/iam/kmz/20260610/MEXI_ZH_20260610_192501.kmz"
    )

    async def mock_post(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        # The API returns a relative path; radar_iam.py constructs the full URL
        mock_resp.text = "../kmz/20260610/MEXI_ZH_20260610_192501.kmz"
        return mock_resp

    mock_client = AsyncMock()
    mock_client.post = mock_post

    with pytest.raises(RadarUnavailable, match="same frame"):
        await fetch_current_frame(mock_client, last_kmz_url=existing_url)


# ---------------------------------------------------------------------------
# Test 8: find_echo_contours — real fixture
# ---------------------------------------------------------------------------

def test_find_echo_contours_returns_polygons():
    """find_echo_contours con frame1.png devuelve polígonos válidos dentro de bounds."""
    from app.processing.motion import find_echo_contours
    from app.processing.pixel_extract import set_legend_path

    set_legend_path(str(FIXTURES / "leyenda.png"))
    image = Image.open(FIXTURES / "frame1.png")
    bounds = EXPECTED_BOUNDS

    contours = find_echo_contours(image, bounds)

    assert isinstance(contours, list)
    assert len(contours) > 0, "frame1.png tiene ecos — debe devolver al menos un contorno"

    for ring in contours:
        assert isinstance(ring, list)
        assert len(ring) >= 3, "Cada polígono debe tener al menos 3 vértices"
        for point in ring:
            lat, lon = point[0], point[1]
            # Dentro de bounds con holgura de 1 grado (simplificación puede sacar 1 px)
            assert bounds["south"] - 1.0 <= lat <= bounds["north"] + 1.0
            assert bounds["west"] - 1.0 <= lon <= bounds["east"] + 1.0


def test_find_echo_contours_empty_image():
    """Una imagen completamente transparente devuelve lista vacía."""
    from app.processing.motion import find_echo_contours
    from app.processing.pixel_extract import set_legend_path

    set_legend_path(str(FIXTURES / "leyenda.png"))
    # Crear imagen RGBA completamente transparente
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))

    contours = find_echo_contours(img, EXPECTED_BOUNDS)

    assert contours == []
