"""Tests de utilidades de scheduler.py que no requieren el loop async completo."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app import config
from app.scheduler import _rotate_diag_log


def test_rotate_diag_log_drops_old_lines_keeps_recent(tmp_path, monkeypatch):
    """_rotate_diag_log recorta líneas más viejas que retention_days, conserva
    las recientes. Sin esto el JSONL crece sin límite (append-only)."""
    diag_path = tmp_path / "diag.jsonl"
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    recent = now - timedelta(hours=1)

    lines = [
        json.dumps({"frame_time": old.isoformat(), "n_det": 1}),
        json.dumps({"frame_time": recent.isoformat(), "n_det": 2}),
    ]
    diag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(config, "DIAG_LOG_PATH", str(diag_path))
    _rotate_diag_log(retention_days=14)

    kept = [json.loads(l) for l in diag_path.read_text(encoding="utf-8").splitlines()]
    assert len(kept) == 1
    assert kept[0]["n_det"] == 2


def test_rotate_diag_log_noop_when_all_recent(tmp_path, monkeypatch):
    """Si todas las líneas están dentro de la ventana, el archivo no se toca."""
    diag_path = tmp_path / "diag.jsonl"
    now = datetime.now(timezone.utc)
    lines = [json.dumps({"frame_time": now.isoformat(), "n_det": 1})]
    diag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(config, "DIAG_LOG_PATH", str(diag_path))
    _rotate_diag_log(retention_days=14)

    kept = diag_path.read_text(encoding="utf-8").splitlines()
    assert len(kept) == 1


def test_rotate_diag_log_missing_file_is_noop(tmp_path, monkeypatch):
    """Si el archivo aún no existe (warmup), no debe fallar."""
    monkeypatch.setattr(config, "DIAG_LOG_PATH", str(tmp_path / "no_existe.jsonl"))
    _rotate_diag_log(retention_days=14)  # no debe lanzar excepción


def test_rotate_diag_log_enforces_size_cap_as_safety_net(tmp_path, monkeypatch):
    """Aunque todas las líneas estén dentro de retention_days, si el archivo
    supera max_bytes se descartan las más viejas hasta bajar del tope —
    la red de seguridad es independiente de la ventana de días."""
    diag_path = tmp_path / "diag.jsonl"
    now = datetime.now(timezone.utc)
    lines = [
        json.dumps({
            "frame_time": (now - timedelta(minutes=10 - i)).isoformat(),
            "n_det": i,
            "pad": "x" * 200,
        })
        for i in range(10)
    ]
    diag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    original_size = diag_path.stat().st_size

    monkeypatch.setattr(config, "DIAG_LOG_PATH", str(diag_path))
    small_cap = original_size // 2
    _rotate_diag_log(retention_days=14, max_bytes=small_cap)

    kept = [json.loads(l) for l in diag_path.read_text(encoding="utf-8").splitlines()]
    assert 0 < len(kept) < 10
    # Debe conservar las líneas MÁS RECIENTES (n_det más alto), no las viejas
    assert kept[-1]["n_det"] == 9
    assert diag_path.stat().st_size <= small_cap


def test_rotate_diag_log_size_cap_noop_when_under_limit(tmp_path, monkeypatch):
    """Si el archivo ya está por debajo de max_bytes, no se toca de más."""
    diag_path = tmp_path / "diag.jsonl"
    now = datetime.now(timezone.utc)
    lines = [json.dumps({"frame_time": now.isoformat(), "n_det": 1})]
    diag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(config, "DIAG_LOG_PATH", str(diag_path))
    _rotate_diag_log(retention_days=14, max_bytes=1024 * 1024 * 1024)  # 1 GB, muy holgado

    kept = diag_path.read_text(encoding="utf-8").splitlines()
    assert len(kept) == 1
