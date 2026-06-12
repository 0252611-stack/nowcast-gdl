"""Contrato de datos de Nowcast GDL.

Este archivo es la única fuente de verdad del shape que viaja entre el backend
y el frontend. NUNCA cambiar sin actualizar frontend/src/api.js en el mismo
commit (ver CLAUDE.md). Todo dato externo (Open-Meteo, radar IAM) se valida
contra estos modelos antes de usarse.

Convención de tiempos:
- Pronóstico Open-Meteo: timestamps tz-aware en America/Mexico_City.
- Radar IAM: timestamp del escaneo tz-aware en UTC (la trampa de medianoche
  UTC se maneja en radar_iam.py, no aquí).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Open-Meteo: pronóstico por punto
# --------------------------------------------------------------------------- #

class HourlyForecast(BaseModel):
    """Pronóstico de una hora para un punto. Direcciones de viento en grados
    meteorológicos (de dónde viene, 0=N, 90=E). Viento a 700 hPa se usa para
    el cross-check del movimiento de celdas en el nowcasting (Sprint 3)."""

    model_config = ConfigDict(extra="forbid")

    time: datetime = Field(..., description="Hora local America/Mexico_City, tz-aware")
    precipitation_mm: float = Field(..., ge=0)
    precipitation_probability: int = Field(..., ge=0, le=100)
    temperature_c: float
    wind_speed_10m_kmh: float = Field(..., ge=0)
    wind_direction_10m_deg: float = Field(..., ge=0, le=360)
    wind_speed_700hPa_kmh: float = Field(..., ge=0)
    wind_direction_700hPa_deg: float = Field(..., ge=0, le=360)


class PointForecast(BaseModel):
    """Pronóstico Open-Meteo completo de un punto: 12 horas hacia adelante."""

    model_config = ConfigDict(extra="forbid")

    point_id: str
    name: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    generated_at: datetime = Field(..., description="Cuándo se obtuvo, tz-aware")
    timezone: str = "America/Mexico_City"
    hourly: list[HourlyForecast] = Field(..., min_length=1)


# --------------------------------------------------------------------------- #
# Radar IAM: lectura por punto
# --------------------------------------------------------------------------- #

class RadarCategory(str, Enum):
    """Categorías de la leyenda oficial del radar IAM (leyenda.png)."""

    RUIDO = "Ruido"
    DEBIL = "Débil"
    LIGERA = "Ligera"
    MODERADA_FUERTE = "Moderada a fuerte"
    GRANIZO = "Granizo"


class RadarReading(BaseModel):
    """Lectura de reflectividad del radar en un punto. Solo representa lecturas
    VÁLIDAS; la indisponibilidad del radar se expresa con RadarReading | None y
    el flag radar_available del endpoint (degradación a solo Open-Meteo)."""

    model_config = ConfigDict(extra="forbid")

    point_id: str
    dbz: float = Field(..., ge=-31.5, le=78.0, description="Reflectividad calibrada")
    category: RadarCategory
    scan_time_utc: datetime = Field(..., description="Timestamp del escaneo, tz-aware UTC")
    frame_age_seconds: float = Field(..., ge=0, description="Edad del frame al leerse")
    pixel_x: int = Field(..., ge=0, description="Columna en la imagen del radar")
    pixel_y: int = Field(..., ge=0, description="Fila en la imagen del radar")


# --------------------------------------------------------------------------- #
# Nowcasting: ETA de lluvia (PRELIMINAR — se refina en Sprint 3)
# --------------------------------------------------------------------------- #

class NowcastResult(BaseModel):
    """Estimación de llegada de lluvia a un punto. Estructura preliminar; el
    motor real (optical flow + viento 700 hPa) se implementa en Sprint 3."""

    model_config = ConfigDict(extra="forbid")

    point_id: str
    raining_now: bool = Field(..., description="dBZ > umbral operativo (18) en el punto")
    eta_minutes: int | None = Field(None, ge=0, description="Minutos hasta llegada; None si no se espera lluvia")
    confidence: float | None = Field(None, ge=0, le=1)
    horizon_minutes: int = Field(60, description="Ventana de proyección (15/30/60)")
    cell_speed_kmh: float | None = Field(None, ge=0, description="Velocidad de la celda")
    cell_bearing_deg: float | None = Field(None, ge=0, le=360, description="Rumbo de la celda")
    # Posición del eco causante + dirección al punto (solo método advection)
    cell_lat: float | None = Field(None, ge=-90, le=90, description="Latitud del eco causante")
    cell_lon: float | None = Field(None, ge=-180, le=180, description="Longitud del eco causante")
    bearing_cell_to_point_deg: float | None = Field(
        None, ge=0, le=360, description="Rumbo del eco hacia el punto monitoreado (0-360°)"
    )
    # Viento 700 hPa medido EN el eco (hacia donde va, no de donde viene)
    wind_echo_bearing_deg: float | None = Field(
        None, ge=0, le=360, description="Rumbo hacia donde va el viento 700 hPa en el eco"
    )
    wind_echo_speed_kmh: float | None = Field(
        None, ge=0, description="Velocidad del viento 700 hPa en el eco (km/h)"
    )
    generated_at: datetime
    method: str = Field(
        "unknown",
        description=(
            "Método empleado: radar_unavailable | radar_current | insufficient_frames | "
            "no_echo | no_motion | no_approaching_cell | advection"
        ),
    )
