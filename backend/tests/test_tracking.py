"""Tests para tracking.py — Compuerta 1 de la Capa 2.

Tests de: detect_cells, update_tracks (continuidad, purga, determinismo),
detección básica de split/merge, y diag dict (Compuerta 0 — campos clave).
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app import config
from app.processing.pixel_extract import set_legend_path
from app.processing.tracking import TrackedCell, detect_cells, update_tracks

FIXTURES = Path(__file__).parent / "fixtures"

BOUNDS = {
    "north": 22.03030437021881,
    "south": 19.32059531316582,
    "east": -101.9462411978663,
    "west": -104.8254262826025,
}

T0 = datetime(2026, 6, 11, 20, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 11, 20, 1, 30, tzinfo=timezone.utc)
T2 = datetime(2026, 6, 11, 20, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def legend_loaded():
    set_legend_path(str(FIXTURES / "leyenda.png"))


def _frame1_bytes() -> bytes:
    return (FIXTURES / "frame1.png").read_bytes()


def _blank_frame() -> bytes:
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _shifted_frame(shift_x: int = 0, shift_y: int = 0) -> bytes:
    arr = np.array(Image.open(FIXTURES / "frame1.png"))
    if shift_x:
        arr = np.roll(arr, shift_x, axis=1)
    if shift_y:
        arr = np.roll(arr, shift_y, axis=0)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _make_tracked_cell(
    cell_id: int,
    lat: float,
    lon: float,
    area: int = 500,
    velocity_kmh: float = 20.0,
    bearing_deg: float = 90.0,
) -> TrackedCell:
    """Crea un TrackedCell sintético para tests de matching."""
    return TrackedCell(
        id=cell_id,
        lat=lat,
        lon=lon,
        area_px=area,
        mean_dbz=30.0,
        max_dbz=40.0,
        ring=[[lat, lon]],
        velocity_kmh=velocity_kmh,
        bearing_deg=bearing_deg,
        centroid_history=[(lat, lon, T0)],
        area_history=[area],
        age_frames=3,
        first_seen=T0,
        last_seen=T0,
    )


# ── detect_cells ──────────────────────────────────────────────────────────────

class TestDetectCells:
    def test_detects_at_least_one_cell_in_real_frame(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        cells = detect_cells(img, BOUNDS)
        assert len(cells) >= 1

    def test_cell_has_required_fields(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        cells = detect_cells(img, BOUNDS)
        assert cells, "No se detectaron celdas en frame1.png"
        c = cells[0]
        for field in ("lat", "lon", "area_px", "mean_dbz", "max_dbz", "ring"):
            assert field in c, f"Campo faltante: {field}"

    def test_cell_lat_lon_within_bounds(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        for c in detect_cells(img, BOUNDS):
            assert BOUNDS["south"] <= c["lat"] <= BOUNDS["north"]
            assert BOUNDS["west"] <= c["lon"] <= BOUNDS["east"]

    def test_cell_area_at_least_min_px(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        for c in detect_cells(img, BOUNDS):
            assert c["area_px"] >= config.CELL_MIN_PX

    def test_blank_frame_returns_empty_list(self):
        img = Image.open(io.BytesIO(_blank_frame()))
        assert detect_cells(img, BOUNDS) == []

    def test_large_min_px_returns_empty(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        assert detect_cells(img, BOUNDS, min_px=9_999_999) == []

    def test_ring_is_list_of_lat_lon_pairs(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        cells = detect_cells(img, BOUNDS)
        assert cells
        for c in cells:
            assert isinstance(c["ring"], list)
            for pt in c["ring"]:
                assert len(pt) == 2
                lat, lon = pt
                assert BOUNDS["south"] <= lat <= BOUNDS["north"]
                assert BOUNDS["west"] <= lon <= BOUNDS["east"]


# ── update_tracks — retorno y diag dict (Compuerta 0) ─────────────────────────

class TestDiagDict:
    """Compuerta 0: update_tracks devuelve 3-tupla; el diag dict tiene los campos clave."""

    def test_returns_three_tuple(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        result = update_tracks([], dets, T0, BOUNDS)
        assert len(result) == 3, "update_tracks debe devolver (tracks, next_id, diag)"

    def test_diag_has_required_keys(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        _, _, diag = update_tracks([], dets, T0, BOUNDS)
        required = {
            "n_alive", "n_new", "n_continued", "n_purged",
            "n_split", "n_merge", "gate_rejects", "match_cost_mean",
        }
        assert required <= diag.keys(), f"Faltan claves: {required - diag.keys()}"

    def test_diag_initial_frame_all_new(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("frame1.png sin celdas")
        tracks, _, diag = update_tracks([], dets, T0, BOUNDS)
        assert diag["n_new"] == len(tracks)
        assert diag["n_continued"] == 0
        assert diag["n_purged"] == 0
        assert diag["match_cost_mean"] is None  # sin tracks previos → sin matching

    def test_diag_continued_on_second_frame(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        tracks, nid, _ = update_tracks([], dets, T0, BOUNDS)
        _, _, diag2 = update_tracks(tracks, dets, T1, BOUNDS, next_id=nid)
        assert diag2["n_continued"] == len(tracks)
        assert diag2["n_new"] == 0
        assert diag2["match_cost_mean"] is not None  # hay matches → cost no None

    def test_diag_purged_after_disappearance(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        tracks, nid, _ = update_tracks([], dets, T0, BOUNDS)
        # Frame vacío CELL_MAX_MISSED+1 veces → purge
        current = tracks
        for i in range(config.CELL_MAX_MISSED + 1):
            t = datetime(T1.year, T1.month, T1.day, T1.hour, T1.minute + i, T1.second, tzinfo=timezone.utc)
            current, nid, diag = update_tracks(current, [], t, BOUNDS, next_id=nid)
        assert diag["n_purged"] >= 1

    def test_diag_is_jsonl_serializable(self):
        """El diag dict debe ser serializable a JSON (Compuerta 0 — JSONL)."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        _, _, diag = update_tracks([], dets, T0, BOUNDS)
        record = {"frame_time": T0.isoformat(), **diag}
        line = json.dumps(record)
        parsed = json.loads(line)
        assert parsed["frame_time"] == T0.isoformat()
        for key in ("n_alive", "n_new", "n_continued", "n_purged"):
            assert key in parsed


# ── update_tracks — primer frame (sin tracks previos) ─────────────────────────

class TestUpdateTracksInitial:
    def test_initial_tracks_created_from_detections(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("frame1.png sin celdas detectables")
        tracks, next_id, _ = update_tracks([], dets, T0, BOUNDS)
        assert len(tracks) == len(dets)

    def test_initial_ids_unique_and_incremental(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("frame1.png sin celdas detectables")
        tracks, next_id, _ = update_tracks([], dets, T0, BOUNDS, next_id=1)
        ids = [t.id for t in tracks]
        assert ids == list(range(1, len(dets) + 1))
        assert next_id == len(dets) + 1

    def test_initial_age_is_one(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("frame1.png sin celdas detectables")
        tracks, _, _ = update_tracks([], dets, T0, BOUNDS)
        assert all(t.age_frames == 1 for t in tracks)

    def test_initial_velocity_is_zero(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("frame1.png sin celdas detectables")
        tracks, _, _ = update_tracks([], dets, T0, BOUNDS)
        assert all(t.velocity_kmh == 0.0 for t in tracks)


# ── update_tracks — continuidad ───────────────────────────────────────────────

class TestContinuity:
    def test_same_frame_preserves_ids(self):
        """Dos frames idénticos: las celdas mantienen sus IDs."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("frame1.png sin celdas detectables")
        tracks0, nid, _ = update_tracks([], dets, T0, BOUNDS)

        dets1 = detect_cells(img, BOUNDS)
        tracks1, _, _ = update_tracks(tracks0, dets1, T1, BOUNDS, next_id=nid)

        ids0 = {t.id for t in tracks0}
        ids1 = {t.id for t in tracks1}
        assert ids0 == ids1, "Todos los IDs deben conservarse con el mismo frame"

    def test_shifted_frame_preserves_at_least_one_id(self):
        """Frame desplazado: al menos una celda conserva su ID."""
        img0 = Image.open(io.BytesIO(_frame1_bytes()))
        dets0 = detect_cells(img0, BOUNDS)
        if not dets0:
            pytest.skip("frame1.png sin celdas detectables")
        tracks0, nid, _ = update_tracks([], dets0, T0, BOUNDS)

        img1 = Image.open(io.BytesIO(_shifted_frame(shift_x=5)))
        dets1 = detect_cells(img1, BOUNDS)
        if not dets1:
            pytest.skip("frame desplazado sin celdas detectables")
        tracks1, _, _ = update_tracks(tracks0, dets1, T1, BOUNDS, interval_seconds=90.0, next_id=nid)

        ids0 = {t.id for t in tracks0}
        continued = {t.id for t in tracks1 if t.id in ids0}
        assert len(continued) >= 1

    def test_age_increments_on_match(self):
        """age_frames se incrementa con cada ciclo de tracking."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("frame1.png sin celdas detectables")
        tracks, nid, _ = update_tracks([], dets, T0, BOUNDS)
        tracks2, _, _ = update_tracks(tracks, dets, T1, BOUNDS, next_id=nid)
        continued = [t for t in tracks2 if t.id in {tr.id for tr in tracks}]
        assert all(t.age_frames == 2 for t in continued)

    def test_velocity_nonzero_after_shift(self):
        """Celda desplazada tiene velocidad > 0 después del emparejamiento."""
        img0 = Image.open(io.BytesIO(_frame1_bytes()))
        dets0 = detect_cells(img0, BOUNDS)
        if not dets0:
            pytest.skip("no cells")
        tracks0, nid, _ = update_tracks([], dets0, T0, BOUNDS)

        img1 = Image.open(io.BytesIO(_shifted_frame(shift_x=10)))
        dets1 = detect_cells(img1, BOUNDS)
        if not dets1:
            pytest.skip("no cells in shifted frame")
        tracks1, _, _ = update_tracks(tracks0, dets1, T1, BOUNDS, interval_seconds=90.0, next_id=nid)

        ids0 = {t.id for t in tracks0}
        continued = [t for t in tracks1 if t.id in ids0]
        if continued:
            assert max(t.velocity_kmh for t in continued) > 0.0


# ── update_tracks — purga ─────────────────────────────────────────────────────

class TestPurge:
    def test_cell_survives_exactly_max_missed_cycles(self):
        """La celda sobrevive CELL_MAX_MISSED ciclos sin match."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        tracks, nid, _ = update_tracks([], dets, T0, BOUNDS)
        n = len(tracks)

        t = T1
        current = tracks
        for _ in range(config.CELL_MAX_MISSED):
            current, nid, _ = update_tracks(current, [], t, BOUNDS, next_id=nid)
            t = datetime(t.year, t.month, t.day, t.hour, t.minute + 1, t.second, tzinfo=timezone.utc)

        assert len(current) == n, "Celdas deben sobrevivir exactamente CELL_MAX_MISSED ciclos"

    def test_cell_purged_after_max_missed_plus_one(self):
        """La celda es eliminada tras CELL_MAX_MISSED + 1 ciclos sin match."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        tracks, nid, _ = update_tracks([], dets, T0, BOUNDS)

        t = T1
        current = tracks
        for _ in range(config.CELL_MAX_MISSED + 1):
            current, nid, _ = update_tracks(current, [], t, BOUNDS, next_id=nid)
            t = datetime(t.year, t.month, t.day, t.hour, t.minute + 1, t.second, tzinfo=timezone.utc)

        assert len(current) == 0, "Todas las celdas deben purgarse tras CELL_MAX_MISSED+1 ciclos"


# ── update_tracks — determinismo ──────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_ids(self):
        """Mismo par de frames → IDs y velocidades idénticos en dos ejecuciones."""
        img0 = Image.open(io.BytesIO(_frame1_bytes()))
        dets0 = detect_cells(img0, BOUNDS)
        if not dets0:
            pytest.skip("no cells")

        img1 = Image.open(io.BytesIO(_shifted_frame(shift_x=5)))
        dets1 = detect_cells(img1, BOUNDS)

        # Primera ejecución
        ta, nid_a, _ = update_tracks([], dets0, T0, BOUNDS, next_id=1)
        tb, nid_b, _ = update_tracks(ta, dets1, T1, BOUNDS, interval_seconds=90.0, next_id=nid_a)

        # Segunda ejecución con el mismo input
        ta2, nid_a2, _ = update_tracks([], dets0, T0, BOUNDS, next_id=1)
        tb2, nid_b2, _ = update_tracks(ta2, dets1, T1, BOUNDS, interval_seconds=90.0, next_id=nid_a2)

        assert sorted(t.id for t in tb) == sorted(t.id for t in tb2)
        assert nid_b == nid_b2

    def test_diag_deterministic(self):
        """Mismo input → mismo diag dict en dos ejecuciones."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        _, _, diag1 = update_tracks([], dets, T0, BOUNDS, next_id=1)
        _, _, diag2 = update_tracks([], dets, T0, BOUNDS, next_id=1)
        assert diag1 == diag2


# ── split / merge ─────────────────────────────────────────────────────────────

class TestSplitMerge:
    def test_merge_recorded_when_two_tracks_target_same_detection(self):
        """Dos tracks muy juntos, una sola detección → el ganador registra merged_ids."""
        t1 = _make_tracked_cell(1, 20.670, -103.400, velocity_kmh=0.0)
        t2 = _make_tracked_cell(2, 20.671, -103.400, velocity_kmh=0.0)

        det_lat, det_lon = 20.6705, -103.400
        dets = [{
            "lat": det_lat, "lon": det_lon, "area_px": 1000,
            "mean_dbz": 35.0, "max_dbz": 45.0,
            "ring": [[det_lat, det_lon]],
        }]

        tracks, _, diag = update_tracks([t1, t2], dets, T1, BOUNDS, interval_seconds=90.0, next_id=3)

        winners = [t for t in tracks if t.id in {1, 2} and t.missed_frames == 0]
        assert len(winners) == 1, "Solo un track debe ganar la detección"
        winner = winners[0]
        assert len(winner.merged_ids) >= 1
        assert diag["n_merge"] >= 1

    def test_new_cell_near_existing_track_gets_split_from(self):
        """Nueva detección cerca de un track asignado a otro → split_from poblado."""
        t1 = _make_tracked_cell(1, 20.670, -103.400, velocity_kmh=20.0, bearing_deg=90.0)

        det1 = {
            "lat": 20.670, "lon": -103.3952, "area_px": 800,
            "mean_dbz": 30.0, "max_dbz": 40.0,
            "ring": [[20.670, -103.3952]],
        }
        det2 = {
            "lat": 20.665, "lon": -103.3955, "area_px": 400,
            "mean_dbz": 25.0, "max_dbz": 35.0,
            "ring": [[20.665, -103.3955]],
        }

        tracks, next_id, _ = update_tracks(
            [t1], [det1, det2], T1, BOUNDS, interval_seconds=90.0, next_id=2
        )

        assert next_id >= 2


# ── gate dinámico y clamp de velocidad (fix cell_spd inverosímil) ────────────

class TestSpeedClampAndDynamicGate:
    """CELL_MAX_SPEED_KMH=80: el gate de matching debe tensarse con intervalos
    cortos (evita emparejar con un blob distinto a distancias físicamente
    imposibles), y raw_speed debe quedar acotado como respaldo defensivo."""

    def test_gate_rejects_far_match_at_normal_interval(self):
        """A 90s de intervalo, una detección a ~10km debe rechazarse: implicaría
        ~400 km/h, muy por encima de CELL_MAX_SPEED_KMH=80. Con el gate estático
        anterior (15km fijo) esta detección habría pasado sin problema."""
        t1 = _make_tracked_cell(1, 20.670, -103.400, velocity_kmh=0.0)
        far_det = {
            "lat": 20.670 + 10.0 / 111.32,  # ≈ +10 km en latitud
            "lon": -103.400,
            "area_px": 500, "mean_dbz": 30.0, "max_dbz": 40.0,
            "ring": [[20.670 + 10.0 / 111.32, -103.400]],
        }

        tracks, _, diag = update_tracks(
            [t1], [far_det], T1, BOUNDS, interval_seconds=90.0, next_id=2
        )

        assert diag["gate_rejects"] >= 1
        # El track sobrevive como "sin match" (missed_frames=1, no purgado aún),
        # no se le asigna la detección lejana ni hereda su posición/velocidad.
        survivors = [t for t in tracks if t.id == 1]
        assert len(survivors) == 1
        assert survivors[0].missed_frames == 1
        assert survivors[0].lat == t1.lat and survivors[0].lon == t1.lon

    def test_raw_speed_clamp_dampens_stale_high_velocity(self):
        """Rama interval_seconds<=0 (raw_speed = t.velocity_kmh): una celda con
        velocidad vieja >80 km/h (ej. restaurada de tracking_state de antes de
        este fix) debe activar el clamp, no propagarse intacta."""
        t1 = _make_tracked_cell(1, 20.670, -103.400, velocity_kmh=170.0, bearing_deg=90.0)
        same_spot_det = {
            "lat": 20.670, "lon": -103.400,
            "area_px": 500, "mean_dbz": 30.0, "max_dbz": 40.0,
            "ring": [[20.670, -103.400]],
        }

        tracks, _, diag = update_tracks(
            [t1], [same_spot_det], T1, BOUNDS, interval_seconds=0.0, next_id=2
        )

        assert diag["n_speed_clamped"] == 1
        winner = next(t for t in tracks if t.id == 1)
        # El EMA mezcla raw_speed clampeado (80) con la velocidad vieja (170);
        # el resultado debe ser estrictamente menor a la velocidad vieja sin
        # clamp (que habría dado 170 sin cambios).
        assert winner.velocity_kmh < t1.velocity_kmh

    def test_speed_clamped_counter_zero_in_normal_operation(self):
        """Un desplazamiento razonable (dentro del gate normal) no debe activar
        el clamp — confirma que no hay falsos positivos."""
        t1 = _make_tracked_cell(1, 20.670, -103.400, velocity_kmh=0.0)
        near_det = {
            "lat": 20.670 + 0.5 / 111.32,  # ≈ +0.5 km, bien dentro del gate a 90s (~2km)
            "lon": -103.400,
            "area_px": 500, "mean_dbz": 30.0, "max_dbz": 40.0,
            "ring": [[20.670 + 0.5 / 111.32, -103.400]],
        }

        _, _, diag = update_tracks(
            [t1], [near_det], T1, BOUNDS, interval_seconds=90.0, next_id=2
        )

        assert diag["n_continued"] == 1
        assert diag["n_speed_clamped"] == 0


# ── quality score (Compuerta 1) ───────────────────────────────────────────────

class TestQualityScore:
    def test_detect_cells_returns_solidity_and_extent(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        cells = detect_cells(img, BOUNDS)
        assert cells, "frame1.png sin celdas"
        for c in cells:
            assert "solidity" in c, "Campo 'solidity' faltante en detect_cells"
            assert "extent" in c, "Campo 'extent' faltante en detect_cells"
            assert 0.0 < c["solidity"] <= 1.0, f"solidity fuera de rango: {c['solidity']}"
            assert 0.0 < c["extent"] <= 1.0, f"extent fuera de rango: {c['extent']}"

    def test_tracked_cell_has_quality_field(self):
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        tracks, _, _ = update_tracks([], dets, T0, BOUNDS)
        for t in tracks:
            assert hasattr(t, "quality"), "TrackedCell sin campo 'quality'"
            assert 0.0 <= t.quality <= 1.0, f"quality fuera de [0,1]: {t.quality}"

    def test_quality_increases_with_age(self):
        """Celda que sobrevive más ciclos debe tener quality mayor o igual."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        tracks, nid, _ = update_tracks([], dets, T0, BOUNDS)
        q_age1 = [t.quality for t in tracks]

        tracks2, nid, _ = update_tracks(tracks, dets, T1, BOUNDS, next_id=nid)
        continued = [t for t in tracks2 if t.id in {tr.id for tr in tracks}]
        q_age2 = [t.quality for t in continued]

        # Al menos la media debe aumentar o mantenerse (age_score sube)
        assert sum(q_age2) / max(1, len(q_age2)) >= sum(q_age1) / max(1, len(q_age1)) - 0.05

    def test_quality_deterministic(self):
        """Mismo input → mismo quality en dos ejecuciones."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        dets = detect_cells(img, BOUNDS)
        if not dets:
            pytest.skip("no cells")
        t1, _, _ = update_tracks([], dets, T0, BOUNDS, next_id=1)
        t2, _, _ = update_tracks([], dets, T0, BOUNDS, next_id=1)
        for a, b in zip(t1, t2):
            assert a.quality == b.quality, f"quality no determinista: {a.quality} vs {b.quality}"

    def test_large_cell_has_higher_quality_than_small(self):
        """Celda grande (area > AREA_REF) vs celda pequeña (area = CELL_MIN_PX)."""
        from app.processing.tracking import _cell_quality
        q_large = _cell_quality(
            area_px=config.CELL_QUALITY_AREA_REF * 2,
            solidity=0.9,
            age_frames=5,
            area_history=[config.CELL_QUALITY_AREA_REF * 2] * 4,
            missed_frames=0,
        )
        q_small = _cell_quality(
            area_px=config.CELL_MIN_PX,
            solidity=0.5,
            age_frames=1,
            area_history=[config.CELL_MIN_PX],
            missed_frames=0,
        )
        assert q_large > q_small, f"Celda grande ({q_large}) debe superar a celda pequeña ({q_small})"

    def test_missed_frames_penalty(self):
        """Celda con missed_frames=1 tiene quality < la misma sin missed."""
        from app.processing.tracking import _cell_quality
        q_ok = _cell_quality(500, 0.8, 3, [500, 480, 510], missed_frames=0)
        q_missed = _cell_quality(500, 0.8, 3, [500, 480, 510], missed_frames=1)
        assert q_missed < q_ok

    def test_velocity_stability_neutral_for_short_history(self):
        """_velocity_stability devuelve 0.0 con <3 centroides."""
        from app.processing.tracking import _velocity_stability
        assert _velocity_stability([]) == 0.0
        assert _velocity_stability([(20.0, -103.0, T0)]) == 0.0
        assert _velocity_stability([(20.0, -103.0, T0), (20.1, -103.1, T1)]) == 0.0

    def test_velocity_stability_high_for_consistent_motion(self):
        """_velocity_stability es > 0.5 para movimiento constante en dirección fija."""
        from app.processing.tracking import _velocity_stability
        history = [
            (20.0, -103.0, T0),
            (20.1, -102.9, T1),
            (20.2, -102.8, T2),
            (20.3, -102.7, T2),
        ]
        score = _velocity_stability(history)
        assert score > 0.5, f"Movimiento constante debe dar score > 0.5; obtenido {score}"


# ── two-level blob split ───────────────────────────────────────────────────────

class TestBlobSplit:
    """Verifica que componentes con area > CELL_MAX_PX se re-segmentan
    usando el umbral convectivo CELL_SPLIT_DBZ (two-level threshold)."""

    def test_two_nuclei_blob_splits_into_multiple_cells(self):
        """Dos núcleos convectivos conectados por eco débil → ≥2 celdas tras split."""
        from app.processing.pixel_extract import _get_colormap

        cmap = _get_colormap()
        # Buscar color de "puente" (DBZ_THRESHOLD ≤ dbz < CELL_SPLIT_DBZ)
        # y color de "núcleo" (dbz ≥ CELL_SPLIT_DBZ)
        sorted_entries = sorted(cmap.items(), key=lambda x: x[1])
        bridge_color = next(
            (rgb for rgb, dbz in sorted_entries
             if config.DBZ_THRESHOLD <= dbz < config.CELL_SPLIT_DBZ),
            None,
        )
        core_color = next(
            (rgb for rgb, dbz in sorted_entries if dbz >= config.CELL_SPLIT_DBZ),
            None,
        )
        if bridge_color is None or core_color is None:
            pytest.skip("Colormap sin colores en los rangos de umbral requeridos")

        # Imagen 300×200: dos núcleos (30×60 px cada uno) unidos por puente (140×20 px)
        # Área total ≈ 2*1800 + 2800 = 6400 px >> CELL_MAX_PX=2000
        H, W = 200, 300
        img_arr = np.zeros((H, W, 4), dtype=np.uint8)

        # Núcleo izquierdo: columnas 10:70, filas 70:130
        img_arr[70:130, 10:70, :3] = core_color
        img_arr[70:130, 10:70, 3] = 255
        # Puente: columnas 70:230, filas 90:110
        img_arr[90:110, 70:230, :3] = bridge_color
        img_arr[90:110, 70:230, 3] = 255
        # Núcleo derecho: columnas 230:290, filas 70:130
        img_arr[70:130, 230:290, :3] = core_color
        img_arr[70:130, 230:290, 3] = 255

        img = Image.fromarray(img_arr, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img = Image.open(io.BytesIO(buf.getvalue()))

        local_bounds = {
            "north": 21.5, "south": 19.5,
            "east": -102.0, "west": -104.5,
        }
        cells = detect_cells(img, local_bounds, min_px=config.CELL_MIN_PX)

        assert len(cells) >= 2, (
            f"Blob de dos núcleos debe partirse en ≥2 celdas; "
            f"obtenido {len(cells)} con CELL_MAX_PX={config.CELL_MAX_PX}"
        )
        for c in cells:
            assert c["area_px"] >= config.CELL_MIN_PX
            for pt in c["ring"]:
                assert len(pt) == 2

    def test_small_blob_not_split(self):
        """Componente con área ≤ CELL_MAX_PX no pasa por la lógica de split."""
        from app.processing.pixel_extract import _get_colormap

        cmap = _get_colormap()
        core_color = next(
            (rgb for rgb, dbz in sorted(cmap.items(), key=lambda x: x[1])
             if dbz >= config.CELL_SPLIT_DBZ),
            None,
        )
        if core_color is None:
            pytest.skip("Colormap sin color de núcleo convectivo")

        # Blob pequeño: 20×20 = 400 px << CELL_MAX_PX=2000
        H, W = 100, 100
        img_arr = np.zeros((H, W, 4), dtype=np.uint8)
        img_arr[40:60, 40:60, :3] = core_color
        img_arr[40:60, 40:60, 3] = 255

        img = Image.fromarray(img_arr, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img = Image.open(io.BytesIO(buf.getvalue()))

        local_bounds = {
            "north": 21.5, "south": 19.5,
            "east": -102.0, "west": -104.0,
        }
        cells = detect_cells(img, local_bounds, min_px=config.CELL_MIN_PX)
        # Blob pequeño → exactamente 1 celda (sin split)
        assert len(cells) == 1, f"Blob pequeño no debe partirse; obtenido {len(cells)}"


# ── Diagnóstico del split (Etapa 1) ──────────────────────────────────────────

class TestDetectCellsDiag:
    """detect_cells con return_diag=True: coherencia de los contadores."""

    def test_return_diag_backward_compatible(self):
        """Sin return_diag el resultado sigue siendo list[dict]."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        result = detect_cells(img, BOUNDS)
        assert isinstance(result, list)

    def test_return_diag_gives_tuple(self):
        """Con return_diag=True devuelve (list, dict) con todos los campos."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        result = detect_cells(img, BOUNDS, return_diag=True)
        assert isinstance(result, tuple) and len(result) == 2
        cells, diag = result
        assert isinstance(cells, list)
        for key in ("det_n_components", "det_n_oversized", "det_n_blob_split",
                    "det_n_split_subcells", "det_n_kept_whole"):
            assert key in diag, f"Clave '{key}' ausente en det_diag"

    def test_diag_conserved_equals_components_minus_splits(self):
        """n_kept_whole + n_blob_split == n_components (sin contar sub-celdas extra)."""
        img = Image.open(io.BytesIO(_frame1_bytes()))
        _, diag = detect_cells(img, BOUNDS, return_diag=True)
        assert diag["det_n_kept_whole"] + diag["det_n_blob_split"] == diag["det_n_components"]

    def test_diag_empty_image_all_zeros(self):
        """Imagen transparente → todos los contadores a cero."""
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        cells, diag = detect_cells(img, BOUNDS, return_diag=True)
        assert cells == []
        assert diag["det_n_components"] == 0
        assert diag["det_n_oversized"] == 0


# ── Predicción por regresión (Etapa 2) ────────────────────────────────────────

class TestRegressionPrediction:
    """_predict_position_regression: celda en movimiento lineal → predicción precisa."""

    def test_regression_prediction_matches_linear_motion(self):
        """Celda que se mueve a velocidad constante (N → NE) → la regresión cae
        cerca de la posición extrapolada (+1 intervalo = 90 s más adelante)."""
        from datetime import timedelta
        from app.processing.tracking import _predict_position_regression

        # 5 puntos en línea recta: 0.01° lat/lon cada 90 s
        step_lat = 0.01
        step_lon = 0.01
        base_t = datetime(2026, 6, 11, 20, 0, 0, tzinfo=timezone.utc)
        times = [base_t + timedelta(seconds=i * 90) for i in range(5)]
        history = [(20.0 + i * step_lat, -103.0 + i * step_lon, times[i]) for i in range(5)]

        pred_lat, pred_lon = _predict_position_regression(history, 90.0, BOUNDS)
        expected_lat = 20.0 + 5 * step_lat
        expected_lon = -103.0 + 5 * step_lon

        assert abs(pred_lat - expected_lat) < 0.002, (
            f"lat predicha {pred_lat:.5f} difiere de {expected_lat:.5f}")
        assert abs(pred_lon - expected_lon) < 0.002, (
            f"lon predicha {pred_lon:.5f} difiere de {expected_lon:.5f}")

    def test_regression_deterministic(self):
        """Dos llamadas idénticas → mismo resultado."""
        from datetime import timedelta
        from app.processing.tracking import _predict_position_regression

        base_t = datetime(2026, 6, 11, 20, 0, 0, tzinfo=timezone.utc)
        times = [base_t + timedelta(seconds=i * 90) for i in range(4)]
        history = [(20.0 + i * 0.01, -103.0 + i * 0.01, times[i]) for i in range(4)]

        r1 = _predict_position_regression(history, 90.0, BOUNDS)
        r2 = _predict_position_regression(history, 90.0, BOUNDS)
        assert r1 == r2, f"No determinista: {r1} vs {r2}"

    def test_regression_falls_back_for_short_history(self):
        """Con historial < 2 entradas válidas → devuelve el último centroide."""
        from app.processing.tracking import _predict_position_regression

        # Historial con 1 punto → fallback al último centroide
        history = [(20.5, -103.5, datetime(2026, 6, 11, 20, 0, 0, tzinfo=timezone.utc))]
        lat, lon = _predict_position_regression(history, 90.0, BOUNDS)
        assert lat == 20.5 and lon == -103.5


class TestProjectPosition:
    """project_position: proyección lineal rumbo/velocidad constante (diag)."""

    def test_zero_speed_stays_in_place(self):
        from app.processing.tracking import project_position

        lat, lon = project_position(20.5, -103.5, 90.0, 0.0, 30.0, BOUNDS)
        assert lat == pytest.approx(20.5)
        assert lon == pytest.approx(-103.5)

    def test_north_bearing_increases_lat_only(self):
        """rumbo 0° (norte) — debe mover solo en latitud, no en longitud."""
        from app.processing.tracking import project_position

        lat, lon = project_position(20.5, -103.5, 0.0, 30.0, 30.0, BOUNDS)
        assert lat > 20.5
        assert lon == pytest.approx(-103.5, abs=1e-9)

    def test_east_bearing_increases_lon_only(self):
        """rumbo 90° (este) — debe mover solo en longitud, no en latitud."""
        from app.processing.tracking import project_position

        lat, lon = project_position(20.5, -103.5, 90.0, 30.0, 30.0, BOUNDS)
        assert lon > -103.5
        assert lat == pytest.approx(20.5, abs=1e-9)

    def test_distance_scales_with_speed_and_minutes(self):
        """Proyectar a 30 km/h por 60 min debe mover el doble que 30 min."""
        from app.processing.tracking import project_position, _geo_dist_km

        lat30, lon30 = project_position(20.5, -103.5, 45.0, 30.0, 30.0, BOUNDS)
        lat60, lon60 = project_position(20.5, -103.5, 45.0, 30.0, 60.0, BOUNDS)
        d30 = _geo_dist_km(20.5, -103.5, lat30, lon30, BOUNDS)
        d60 = _geo_dist_km(20.5, -103.5, lat60, lon60, BOUNDS)
        assert d60 == pytest.approx(2 * d30, rel=1e-6)
        assert d30 == pytest.approx(30.0 * 0.5, rel=1e-6)  # 30 km/h * 0.5h
