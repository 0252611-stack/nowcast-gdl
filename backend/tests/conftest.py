"""Fixtures globales de pytest para Nowcast GDL."""

from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Ruta al directorio de fixtures (PNGs reales del radar + leyenda.png).
    Los archivos los provee el desarrollador antes del Sprint 1."""
    return Path(__file__).parent / "fixtures"
