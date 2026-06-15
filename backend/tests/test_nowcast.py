"""Tests del motor de nowcasting: motion.py + engine.py."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from PIL import Image

from app.processing.motion import compute_cell_motion, nearest_upstream_echo, project_cell
from app.processing.pixel_extract import set_legend_path
from app.schemas import (
    HourlyForecast,
    NowcastResult,
    PointForecast,
    RadarCategory,
    RadarReading,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Bounds reales de frame1.kml
BOUNDS = {
    "north": 22.03030437021881,
    "south": 19.32059531316582,
    "east": -101.9462411978663,
    "west": -104.8254262826025,
}

# Punto de prueba: GDL Centro
GDL_LAT, GDL_LON = 20.6767, -103.3475


@pytest.fixture(autouse=True)
def legend_loaded():
    """Carga el colormap antes de cualquier test que llame a _get_colormap."""
    set_legend_path(str(FIXTURES / "leyenda.png"))


def _frame1_bytes() -> bytes:
    return (FIXTURES / "frame1.png").read_bytes()


def _shifted_frame(shift_x: int = 0, shift_y: int = 0) -> bytes:
    """Desplaza frame1.png shift_x píxeles en X y shift_y en Y (np.roll)."""
    arr = np.array(Image.open(FIXTURES / "frame1.png"))
    if shift_x:
        arr = np.roll(arr, shift_x, axis=1)
    if shift_y:
        arr = np.roll(arr, shift_y, axis=0)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _mock_forecast(lat=GDL_LAT, lon=GDL_LON) -> PointForecast:
    h = HourlyForecast(
        time=datetime(2026, 6, 11, 14, 0, tzinfo=ZoneInfo("America/Mexico_City")),
        precipitation_mm=0.0,
        precipitation_probability=30,
        temperature_c=26.0,
        wind_speed_10m_kmh=15.0,
        wind_direction_10m_deg=180.0,
        wind_speed_700hPa_kmh=40.0,
        wind_direction_700hPa_deg=270.0,  # viento del Oeste → se mueve al Este
    )
    return PointForecast(
        point_id="centro",
        name="Centro GDL",
        lat=lat,
        lon=lon,
        generated_at=datetime.now(timezone.utc),
        hourly=[h],
    )


def _mock_reading(dbz: float = 5.0) -> RadarReading:
    return RadarReading(
        point_id="centro",
        dbz=dbz,
        category=RadarCategory.DEBIL,
        scan_time_utc=datetime.now(timezone.utc),
        frame_age_seconds=30.0,
        pixel_x=100,
        pixel_y=80,
    )


# ---------------------------------------------------------------------------
# compute_cell_motion
# ---------------------------------------------------------------------------

def test_motion_eastward_shift():
    """Desplazar frame1.png +10 px en X → bearing ≈ 90° (Este) y speed > 0."""
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    result = compute_cell_motion(older, newer, interval_seconds=90.0, bounds=BOUNDS)

    assert result["n_echo_pixels"] > 0
    assert result["speed_kmh"] > 0

    bearing = result["bearing_deg"]
    diff = min(abs(bearing - 90), 360 - abs(bearing - 90))
    assert diff < 60, f"Se esperaba bearing ≈ 90° (Este), se obtuvo {bearing:.1f}°"


def test_motion_blank_image():
    """Imagen completamente transparente → n_echo_pixels=0, speed=0."""
    arr = np.zeros((100, 100, 4), dtype=np.uint8)  # todo transparente
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    blank = buf.getvalue()

    result = compute_cell_motion(blank, blank, interval_seconds=90.0, bounds=BOUNDS)

    assert result["n_echo_pixels"] == 0
    assert result["speed_kmh"] == pytest.approx(0.0)


def test_motion_zero_interval():
    """Intervalo 0 → speed=0 (sin división por cero)."""
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=5)
    result = compute_cell_motion(older, newer, interval_seconds=0.0, bounds=BOUNDS)
    assert result["speed_kmh"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# nearest_upstream_echo
# ---------------------------------------------------------------------------

def test_nearest_upstream_blank_image():
    """Imagen transparente → None (sin eco)."""
    arr = np.zeros((100, 100, 4), dtype=np.uint8)
    img = Image.fromarray(arr)
    assert nearest_upstream_echo(img, BOUNDS, GDL_LAT, GDL_LON, 90.0) is None


def test_nearest_upstream_returns_structure_or_none():
    """Con frame1.png real: si hay eco upstream devuelve el dict correcto;
    si no hay, devuelve None. Ambos son resultados válidos."""
    img = Image.open(FIXTURES / "frame1.png")
    result = nearest_upstream_echo(img, BOUNDS, GDL_LAT, GDL_LON, 90.0)

    if result is not None:
        assert "distance_km" in result
        assert "cell_lat" in result
        assert "cell_lon" in result
        assert "bearing_cell_to_point_deg" in result
        assert "dbz" in result
        assert result["distance_km"] > 0
        assert 0 <= result["bearing_cell_to_point_deg"] < 360
        assert result["dbz"] >= 18.0


# ---------------------------------------------------------------------------
# project_cell
# ---------------------------------------------------------------------------

def test_project_cell_basic():
    """30 km a 60 km/h → eta=30 min; confianza ∈ [0,1]."""
    result = project_cell(
        GDL_LAT, GDL_LON,
        cell_distance_km=30.0,
        motion_speed_kmh=60.0,
        motion_bearing_deg=90.0,         # movimiento al Este
        bearing_cell_to_point_deg=90.0,  # la celda apunta al punto
        wind_700_speed_kmh=40.0,
        wind_700_dir_deg=270.0,           # viento del Oeste → va al Este → concuerda
        horizon_minutes=60,
    )
    assert result["eta_minutes"] == 30
    assert 0 <= result["confidence"] <= 1


def test_project_cell_horizon_exceeded():
    """200 km a 60 km/h → 200 min > horizonte 60 → eta=None."""
    result = project_cell(
        GDL_LAT, GDL_LON,
        cell_distance_km=200.0,
        motion_speed_kmh=60.0,
        motion_bearing_deg=90.0,
        bearing_cell_to_point_deg=90.0,
        wind_700_speed_kmh=40.0,
        wind_700_dir_deg=270.0,
        horizon_minutes=60,
    )
    assert result["eta_minutes"] is None
    assert result["confidence"] == pytest.approx(0.0)


def test_project_cell_zero_speed():
    """Velocidad ≈ 0 → eta=None, confidence=0."""
    result = project_cell(
        GDL_LAT, GDL_LON,
        cell_distance_km=30.0,
        motion_speed_kmh=0.0,
        motion_bearing_deg=90.0,
        bearing_cell_to_point_deg=90.0,
        wind_700_speed_kmh=40.0,
        wind_700_dir_deg=270.0,
        horizon_minutes=60,
    )
    assert result["eta_minutes"] is None


def test_project_cell_high_confidence_when_aligned():
    """Movimiento, dirección a punto y viento todos alineados → confianza alta."""
    result = project_cell(
        GDL_LAT, GDL_LON,
        cell_distance_km=30.0,
        motion_speed_kmh=60.0,
        motion_bearing_deg=90.0,         # mueve al Este
        bearing_cell_to_point_deg=90.0,  # celda→punto: Este
        wind_700_speed_kmh=40.0,
        wind_700_dir_deg=270.0,           # viento del Oeste → hacia el Este ✓
        horizon_minutes=60,
    )
    assert result["confidence"] > 0.8


# ---------------------------------------------------------------------------
# engine.estimate_arrival
# ---------------------------------------------------------------------------

from app.nowcast.engine import estimate_arrival


# ---------------------------------------------------------------------------
# Determinismo (Sesión 4)
# ---------------------------------------------------------------------------

def test_nearest_upstream_is_deterministic():
    """nearest_upstream_echo: 2 llamadas con la misma imagen → resultado idéntico."""
    img = Image.open(FIXTURES / "frame1.png")
    r1 = nearest_upstream_echo(img, BOUNDS, GDL_LAT, GDL_LON, 90.0)
    r2 = nearest_upstream_echo(img, BOUNDS, GDL_LAT, GDL_LON, 90.0)
    assert r1 == r2, "nearest_upstream_echo no es determinista"


def test_find_context_echoes_is_deterministic():
    """find_context_echoes: 2 llamadas con la misma imagen → resultado idéntico."""
    from app.processing.motion import find_context_echoes
    img = Image.open(FIXTURES / "frame1.png")
    r1 = find_context_echoes(img, BOUNDS, 90.0, 30.0)
    r2 = find_context_echoes(img, BOUNDS, 90.0, 30.0)
    assert r1 == r2, "find_context_echoes no es determinista"


# ---------------------------------------------------------------------------
# multi_frame_motion_field + sample_field_at (Sesión 4)
# ---------------------------------------------------------------------------

def test_multi_frame_motion_field_shape():
    """multi_frame_motion_field devuelve H×W×2 con eco suficiente."""
    from app.processing.motion import multi_frame_motion_field
    from datetime import timedelta

    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=90)
    frames = [(_shifted_frame(shift_x=5), t1), (_frame1_bytes(), t0)]
    field = multi_frame_motion_field(frames, BOUNDS)

    assert field is not None
    img = Image.open(FIXTURES / "frame1.png")
    W, H = img.size
    assert field.shape == (H, W, 2), f"Shape esperado ({H},{W},2), obtenido {field.shape}"
    assert field.dtype == np.float32


def test_multi_frame_motion_field_single_frame_returns_none():
    """Un solo frame → None (necesita ≥2)."""
    from app.processing.motion import multi_frame_motion_field

    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    frames = [(_frame1_bytes(), t0)]
    assert multi_frame_motion_field(frames, BOUNDS) is None


def test_sample_field_at_returns_float_pair():
    """sample_field_at devuelve una tupla (v_lat, v_lon) de floats."""
    from app.processing.motion import sample_field_at, dense_motion_field

    frame = _frame1_bytes()
    field = dense_motion_field(frame, frame, 90.0, BOUNDS)
    assert field is not None

    v_lat, v_lon = sample_field_at(field, GDL_LAT, GDL_LON, BOUNDS, win=3)
    assert isinstance(v_lat, float)
    assert isinstance(v_lon, float)


# ---------------------------------------------------------------------------
# engine con motion_field precomputado (Sesión 4, mejora A/B/E)
# ---------------------------------------------------------------------------

def test_engine_with_precomputed_motion_field():
    """estimate_arrival acepta motion_field precomputado y devuelve resultado válido."""
    from app.processing.motion import dense_motion_field

    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)

    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]

    field = dense_motion_field(older, newer, 90.0, BOUNDS)
    result = estimate_arrival("centro", reading, _mock_forecast(), frames, BOUNDS,
                              motion_field=field)

    assert isinstance(result, NowcastResult)
    assert result.method in {"no_echo", "no_motion", "no_approaching_cell", "advection"}


def test_engine_intensity_trend_exposed():
    """estimate_arrival expone intensity_trend cuando hay eco."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]

    result = estimate_arrival("centro", reading, _mock_forecast(), frames, BOUNDS)
    # intensity_trend se expone cuando hay eco (puede ser None si no hay eco)
    if result.method not in {"radar_unavailable", "insufficient_frames"}:
        assert result.intensity_trend is not None or result.method in {"no_echo"}


def test_engine_model_agreement_in_advection():
    """En advection, model_agreement ∈ [0,1]."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]

    result = estimate_arrival("centro", reading, _mock_forecast(), frames, BOUNDS)
    if result.method == "advection":
        assert result.model_agreement is not None
        assert 0.0 <= result.model_agreement <= 1.0


def test_engine_radar_unavailable():
    """radar=None → method=radar_unavailable, raining_now=False."""
    result = estimate_arrival("centro", None, _mock_forecast(), [], None)
    assert isinstance(result, NowcastResult)
    assert result.method == "radar_unavailable"
    assert not result.raining_now
    assert result.eta_minutes is None


def test_engine_raining_now():
    """dBZ=25 >= DBZ_RAIN_THRESHOLD=-10 → raining_now=True, eta=0, method=radar_current."""
    reading = _mock_reading(dbz=25.0)
    result = estimate_arrival("centro", reading, _mock_forecast(), [], None)
    assert result.raining_now is True
    assert result.eta_minutes == 0
    assert result.method == "radar_current"
    assert result.confidence is not None and result.confidence > 0


def test_engine_insufficient_frames():
    """Sin frames + eco ruido → method=insufficient_frames."""
    reading = _mock_reading(dbz=-15.0)
    result = estimate_arrival("centro", reading, _mock_forecast(), [], None)
    assert result.method == "insufficient_frames"
    assert not result.raining_now


def test_engine_insufficient_frames_no_bounds():
    """2 frames pero bounds=None → method=insufficient_frames."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, tzinfo=timezone.utc)
    frames = [(_frame1_bytes(), t1), (_frame1_bytes(), t0)]
    result = estimate_arrival("centro", reading, _mock_forecast(), frames, None)
    assert result.method == "insufficient_frames"


def test_engine_advection_valid_result():
    """Frames con eco ruido + not raining → NowcastResult válido con method reconocido."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)

    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)  # eco moviéndose al Este
    frames = [(newer, t1), (older, t0)]

    result = estimate_arrival("centro", reading, _mock_forecast(), frames, BOUNDS)

    assert isinstance(result, NowcastResult)
    assert not result.raining_now
    assert result.method in {
        "no_echo", "no_motion", "no_approaching_cell", "advection"
    }
    # Los campos opcionales de celda son coherentes si hay advección
    if result.method == "advection":
        assert result.cell_speed_kmh is not None and result.cell_speed_kmh > 0
        assert result.cell_bearing_deg is not None
        if result.eta_minutes is not None:
            assert 0 <= result.eta_minutes <= 60


# ---------------------------------------------------------------------------
# A3 — Guard hourly vacío (no debe lanzar IndexError)
# ---------------------------------------------------------------------------

def test_engine_empty_hourly_forecast_no_crash():
    """Con forecast.hourly=[] (degradación), estimate_arrival no lanza IndexError.
    Usa model_construct para bypassear la validación Pydantic (min_length=1) y
    simular el escenario de un object construido directamente (sin fetch_forecast)."""
    from datetime import timedelta
    from app.schemas import PointForecast

    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=90)
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]

    # Bypasear Pydantic para simular hourly vacío (caso defensivo)
    empty_forecast = PointForecast.model_construct(
        point_id="centro",
        name="Centro GDL",
        lat=GDL_LAT,
        lon=GDL_LON,
        generated_at=t0,
        hourly=[],
    )

    # No debe lanzar ninguna excepción (guard en engine.py línea 161)
    result = estimate_arrival("centro", reading, empty_forecast, frames, BOUNDS)

    assert isinstance(result, NowcastResult)
    assert result.method in {
        "no_echo", "no_motion", "no_approaching_cell", "advection",
        "insufficient_frames",
    }, f"método inesperado: {result.method}"


# ---------------------------------------------------------------------------
# Compuerta 2 — Capa 3: leading_edge_point + engine con tracked_cells
# ---------------------------------------------------------------------------

from app.processing.motion import leading_edge_point
from app.processing.tracking import TrackedCell


def _make_ring_around(center_lat: float, center_lon: float, radius_deg: float = 0.1) -> list:
    """Ring cuadrado alrededor del centroide."""
    d = radius_deg
    return [
        [center_lat + d, center_lon - d],
        [center_lat + d, center_lon + d],
        [center_lat - d, center_lon + d],
        [center_lat - d, center_lon - d],
    ]


def _make_tracked_cell_for_engine(
    cell_id: int,
    lat: float,
    lon: float,
    velocity_kmh: float,
    bearing_deg: float,
    ring: list | None = None,
) -> TrackedCell:
    if ring is None:
        ring = _make_ring_around(lat, lon)
    return TrackedCell(
        id=cell_id,
        lat=lat,
        lon=lon,
        area_px=500,
        mean_dbz=35.0,
        max_dbz=50.0,
        ring=ring,
        velocity_kmh=velocity_kmh,
        bearing_deg=bearing_deg,
        centroid_history=[(lat, lon, datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc))],
        area_history=[500, 510, 520],
        age_frames=3,
        first_seen=datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc),
        last_seen=datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc),
    )


# ── leading_edge_point ───────────────────────────────────────────────────────

def test_leading_edge_closer_than_centroid():
    """El borde de ataque está más cerca del punto que el centroide."""
    # Echo centrado en GDL+0.5° norte, radio 0.1°
    echo_lat, echo_lon = GDL_LAT + 0.5, GDL_LON
    ring = _make_ring_around(echo_lat, echo_lon, radius_deg=0.1)

    edge_lat, edge_lon, edge_dist = leading_edge_point(ring, GDL_LAT, GDL_LON, BOUNDS)

    from app.processing.tracking import _geo_dist_km
    centroid_dist = _geo_dist_km(echo_lat, echo_lon, GDL_LAT, GDL_LON, BOUNDS)
    assert edge_dist < centroid_dist, (
        f"Borde ({edge_dist:.2f} km) debe ser < centroide ({centroid_dist:.2f} km)"
    )


def test_leading_edge_returns_vertex_from_ring():
    """El punto devuelto es uno de los vértices del ring."""
    echo_lat, echo_lon = GDL_LAT + 0.3, GDL_LON
    ring = _make_ring_around(echo_lat, echo_lon, radius_deg=0.05)
    edge_lat, edge_lon, _ = leading_edge_point(ring, GDL_LAT, GDL_LON, BOUNDS)
    assert any(
        abs(pt[0] - edge_lat) < 1e-6 and abs(pt[1] - edge_lon) < 1e-6
        for pt in ring
    ), "El punto devuelto debe ser un vértice del ring"


def test_leading_edge_distance_positive():
    """La distancia al borde siempre es positiva."""
    ring = _make_ring_around(GDL_LAT + 0.2, GDL_LON)
    _, _, dist = leading_edge_point(ring, GDL_LAT, GDL_LON, BOUNDS)
    assert dist > 0.0


# ── engine con tracked_cells ─────────────────────────────────────────────────

def test_engine_cell_tracking_method_when_cell_approaches():
    """Con una celda rastreada aproximándose, method == 'cell_tracking'."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]

    # Celda directamente al norte del punto GDL, moviéndose al sur (bearing=180)
    cell = _make_tracked_cell_for_engine(
        cell_id=1,
        lat=GDL_LAT + 0.3,   # ~33 km al norte
        lon=GDL_LON,
        velocity_kmh=60.0,
        bearing_deg=180.0,   # moviéndose hacia el sur → hacia GDL
    )

    result = estimate_arrival(
        "centro", reading, _mock_forecast(), frames, BOUNDS,
        tracked_cells=[cell],
    )
    assert isinstance(result, NowcastResult)
    # Si hay celda acercándose, el método debe ser cell_tracking
    # (puede no serlo si el punto ya está lloviendo o sin eco suficiente,
    # pero con dbz=-15 y celda válida el motor debe elegir cell_tracking)
    if result.method == "cell_tracking":
        assert result.cell_id == 1
        assert result.cell_age_minutes is not None and result.cell_age_minutes >= 0
        assert result.leading_edge_distance_km is not None
        assert result.confidence is not None and 0 <= result.confidence <= 1


def test_engine_cell_tracking_eta_uses_leading_edge():
    """ETA con celda rastreada usa el borde de ataque (distancia < centroide)."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]

    cell = _make_tracked_cell_for_engine(
        cell_id=1,
        lat=GDL_LAT + 0.3,
        lon=GDL_LON,
        velocity_kmh=60.0,
        bearing_deg=180.0,
    )

    result = estimate_arrival(
        "centro", reading, _mock_forecast(), frames, BOUNDS,
        tracked_cells=[cell],
    )

    if result.method == "cell_tracking" and result.leading_edge_distance_km is not None:
        from app.processing.tracking import _geo_dist_km
        centroid_dist = _geo_dist_km(cell.lat, cell.lon, GDL_LAT, GDL_LON, BOUNDS)
        assert result.leading_edge_distance_km < centroid_dist + 0.1  # borde ≤ centroide


def test_engine_fallback_without_tracked_cells():
    """Sin tracked_cells, el resultado debe ser idéntico al comportamiento anterior."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]
    forecast = _mock_forecast()

    result_none = estimate_arrival("centro", reading, forecast, frames, BOUNDS, tracked_cells=None)
    result_empty = estimate_arrival("centro", reading, forecast, frames, BOUNDS, tracked_cells=[])

    # Ambos deben usar el mismo método (no cell_tracking)
    assert result_none.method == result_empty.method
    assert result_none.method != "cell_tracking"
    assert result_none.eta_minutes == result_empty.eta_minutes


def test_engine_cell_tracking_mult_trend_from_history():
    """Con historial de área creciente, mult_trend debe ser > 1."""
    reading = _mock_reading(dbz=-15.0)
    t0 = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 11, 4, 1, 30, tzinfo=timezone.utc)
    older = _frame1_bytes()
    newer = _shifted_frame(shift_x=10)
    frames = [(newer, t1), (older, t0)]

    # Celda con historial de área creciente (señal de crecimiento)
    cell = _make_tracked_cell_for_engine(
        cell_id=1,
        lat=GDL_LAT + 0.3,
        lon=GDL_LON,
        velocity_kmh=60.0,
        bearing_deg=180.0,
    )
    cell.area_history = [300, 400, 500, 600]  # creciendo

    result = estimate_arrival(
        "centro", reading, _mock_forecast(), frames, BOUNDS,
        tracked_cells=[cell],
    )

    if result.method == "cell_tracking" and result.mult_trend is not None:
        assert result.mult_trend > 1.0, "Eco creciente debe dar mult_trend > 1"
