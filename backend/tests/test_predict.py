"""Tests para el módulo de predicción advectiva (predict.py)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.processing.pixel_extract import set_legend_path

FIXTURES = Path(__file__).parent / "fixtures"

BOUNDS = {
    "north": 22.03030437021881,
    "south": 19.32059531316582,
    "east": -101.9462411978663,
    "west": -104.8254262826025,
}

WIND_GRID = [
    {"lat": 20.0, "lon": -103.0, "toward_deg": 90.0, "speed_kmh": 20.0},
    {"lat": 21.0, "lon": -103.0, "toward_deg": 90.0, "speed_kmh": 20.0},
    {"lat": 20.0, "lon": -104.0, "toward_deg": 90.0, "speed_kmh": 20.0},
    {"lat": 21.0, "lon": -104.0, "toward_deg": 90.0, "speed_kmh": 20.0},
]


@pytest.fixture(autouse=True)
def _legend():
    set_legend_path(str(FIXTURES / "leyenda.png"))


# ---------------------------------------------------------------------------
# dense_motion_field
# ---------------------------------------------------------------------------

def test_dense_motion_field_shape():
    """dense_motion_field devuelve array H×W×2 con shape correcto."""
    from app.processing.motion import dense_motion_field

    frame_bytes = (FIXTURES / "frame1.png").read_bytes()
    field = dense_motion_field(frame_bytes, frame_bytes, interval_seconds=90.0, bounds=BOUNDS)

    img = Image.open(FIXTURES / "frame1.png")
    W, H = img.size

    assert field is not None
    assert field.shape == (H, W, 2), f"Esperado ({H}, {W}, 2), obtenido {field.shape}"
    assert field.dtype == np.float32


def test_dense_motion_field_none_when_no_echo():
    """Imagen transparente → None."""
    from app.processing.motion import dense_motion_field

    empty_buf = __empty_png_bytes(100, 100)
    result = dense_motion_field(empty_buf, empty_buf, interval_seconds=90.0, bounds=BOUNDS)
    assert result is None


def test_dense_motion_field_same_frame_near_zero():
    """Mismo frame → velocidad casi cero."""
    from app.processing.motion import dense_motion_field

    frame_bytes = (FIXTURES / "frame1.png").read_bytes()
    field = dense_motion_field(frame_bytes, frame_bytes, interval_seconds=90.0, bounds=BOUNDS)

    assert field is not None
    rms = float(np.sqrt((field**2).mean()))
    # El flujo óptico entre el mismo frame debe ser esencialmente nulo
    assert rms < 0.01, f"RMS esperado < 0.01 deg/min, obtenido {rms:.4f}"


# ---------------------------------------------------------------------------
# advect_image
# ---------------------------------------------------------------------------

def test_advect_image_same_size():
    """advect_image devuelve imagen del mismo tamaño que la original."""
    from app.processing.predict import advect_image

    img = Image.open(FIXTURES / "frame1.png").convert("RGBA")
    arr = np.array(img)
    H, W = arr.shape[:2]

    # Campo cero → imagen idéntica
    zero_field = np.zeros((H, W, 2), dtype=np.float32)
    result = advect_image(arr, zero_field, minutes=30.0, bounds=BOUNDS)

    assert result.size == img.size


def test_advect_image_zero_field_near_identical():
    """Campo de movimiento cero → imagen advectada casi idéntica al original."""
    from app.processing.predict import advect_image

    img = Image.open(FIXTURES / "frame1.png").convert("RGBA")
    arr = np.array(img)
    H, W = arr.shape[:2]
    zero_field = np.zeros((H, W, 2), dtype=np.float32)

    result = advect_image(arr, zero_field, minutes=0.0, bounds=BOUNDS)
    arr_result = np.array(result)

    # Con minutes=0 y campo cero el resultado debe ser idéntico
    diff = np.abs(arr.astype(int) - arr_result.astype(int)).mean()
    assert diff < 1.0, f"Diferencia media esperada < 1, obtenida {diff:.2f}"


# ---------------------------------------------------------------------------
# blend_motion_field
# ---------------------------------------------------------------------------

def test_blend_with_empty_wind_returns_radar():
    """Sin viento, blend devuelve esencialmente el campo de radar."""
    from app.processing.predict import blend_motion_field

    img = Image.open(FIXTURES / "frame1.png").convert("RGBA")
    arr = np.array(img)
    H, W = arr.shape[:2]
    alpha = arr[:, :, 3]

    radar_field = np.ones((H, W, 2), dtype=np.float32) * 0.001

    blended = blend_motion_field(radar_field, alpha, wind_grid=[], bounds=BOUNDS)
    assert blended.shape == (H, W, 2)
    # Donde hay eco, el campo debe estar cerca del campo radar
    echo_mask = alpha > 0
    if echo_mask.any():
        mean_diff = float(np.abs(blended[echo_mask] - radar_field[echo_mask]).mean())
        # Puede diferir por el suavizado Gaussian, pero no por mucho
        assert mean_diff < 0.005, f"Diferencia media en ecos: {mean_diff:.6f}"


# ---------------------------------------------------------------------------
# build_prediction
# ---------------------------------------------------------------------------

def test_build_prediction_returns_expected_steps():
    """build_prediction devuelve el número correcto de pasos con contornos."""
    from app.processing.predict import build_prediction

    frame_bytes = (FIXTURES / "frame1.png").read_bytes()
    steps_min = [15, 30, 45]

    result = build_prediction(
        frame_older=frame_bytes,
        frame_newer=frame_bytes,
        interval_seconds=90.0,
        bounds=BOUNDS,
        wind_grid=WIND_GRID,
        steps_min=steps_min,
    )

    assert isinstance(result, dict)
    assert len(result["frames_png"]) == len(steps_min)
    assert len(result["steps"]) == len(steps_min)
    assert "trajectories" in result
    assert "method" in result
    assert result["bounds"] == BOUNDS

    for i, s in enumerate(result["steps"]):
        assert s["minutes"] == steps_min[i]
        assert isinstance(s["contours"], list)
        # Los frames PNG deben ser bytes válidos decodificables como imagen
        img = Image.open(__bytes_buf(result["frames_png"][i]))
        assert img.mode == "RGBA"


def test_build_prediction_empty_image_returns_frames():
    """Imagen vacía → method=static_persistence, frames vacíos pero lista no vacía."""
    from app.processing.predict import build_prediction

    empty = __empty_png_bytes(200, 200)
    result = build_prediction(
        frame_older=empty,
        frame_newer=empty,
        interval_seconds=90.0,
        bounds=BOUNDS,
        wind_grid=[],
        steps_min=[15, 30],
    )

    assert result["method"] == "static_persistence"
    assert len(result["frames_png"]) == 2
    assert len(result["trajectories"]) == 0


# ---------------------------------------------------------------------------
# Utilidades internas de test
# ---------------------------------------------------------------------------

def __empty_png_bytes(w: int, h: int) -> bytes:
    """PNG RGBA completamente transparente."""
    import io
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def __bytes_buf(b: bytes):
    import io
    return io.BytesIO(b)
