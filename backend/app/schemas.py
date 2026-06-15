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
# Viento a lo largo de la trayectoria eco → punto
# --------------------------------------------------------------------------- #

class WindSample(BaseModel):
    """Muestra de viento 700 hPa en un punto intermedio de la trayectoria."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    toward_deg: float = Field(..., ge=0, le=360, description="Hacia donde sopla (0=N, 90=E)")
    speed_kmh: float = Field(..., ge=0)


# --------------------------------------------------------------------------- #
# Ecos de contexto (no causantes)
# --------------------------------------------------------------------------- #

class ContextEcho(BaseModel):
    """Cluster de eco detectado en el radar pero no clasificado como causante."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    dbz: float
    bearing_deg: float = Field(..., ge=0, le=360, description="Hacia donde se mueve el eco")
    speed_kmh: float = Field(..., ge=0)


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
# Timeline de intensidad por punto (Etapa 5)
# --------------------------------------------------------------------------- #

class IntensityStep(BaseModel):
    """Un paso del timeline de intensidad de eco en un punto monitoreado."""

    model_config = ConfigDict(extra="forbid")

    minutes: int = Field(..., ge=0, description="Minutos hacia adelante (0/15/30/45)")
    dbz: float = Field(..., description="Reflectividad estimada en el punto (dBZ)")
    category: RadarCategory = Field(..., description="Categoría correspondiente al dBZ")


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
    # Muestras de viento a lo largo de la trayectoria eco → punto
    trajectory_wind: list[WindSample] | None = Field(
        None, description="Viento 700 hPa en puntos intermedios entre el eco y el punto monitoreado"
    )
    # Estabilidad / blend (Sesión 4): tendencia de área del eco y acuerdo con NWP
    intensity_trend: float | None = Field(
        None, ge=-1, le=1,
        description="Tendencia de área del eco entre frames: >0 crece, <0 se disipa",
    )
    model_agreement: float | None = Field(
        None, ge=0, le=1,
        description="Probabilidad de precipitación del modelo NWP (Open-Meteo) en la hora de llegada",
    )
    # Componentes del blend de confianza (B2: confianza interpretable)
    conf_radar: float | None = Field(
        None, ge=0, le=1,
        description="Confianza raw del radar (optical flow + alineación) antes del blend NWP",
    )
    weight_radar: float | None = Field(
        None, ge=0, le=1,
        description="Peso dado a la señal de radar en el blend final (w): 1=solo radar, 0.3=mínimo",
    )
    mult_trend: float | None = Field(
        None, ge=0,
        description="Multiplicador de tendencia de área del eco: >1 crece, <1 se disipa",
    )
    # Campos de la Capa 2+3: identidad persistente + ETA leading-edge
    cell_id: int | None = Field(
        None, description="ID persistente de la celda causante (tracking de celdas)"
    )
    cell_age_minutes: float | None = Field(
        None, ge=0, description="Madurez de la celda causante en minutos desde su detección"
    )
    leading_edge_distance_km: float | None = Field(
        None, ge=0, description="Distancia al borde de ataque de la celda (menor que al centroide)"
    )
    # Timeline de intensidad por punto 0/15/30/45 min (Etapa 5)
    intensity_timeline: list[IntensityStep] | None = Field(
        default=None,
        description="Intensidad estimada en el punto a 0/15/30/45 min (backtrace semi-lagrangiano)"
    )
    intensity_verdict: str | None = Field(
        default=None,
        description="Veredicto de tendencia: empeora | mejora | estable | sin_lluvia"
    )
    generated_at: datetime
    method: str = Field(
        "unknown",
        description=(
            "Método empleado: radar_unavailable | radar_current | insufficient_frames | "
            "no_echo | no_motion | no_approaching_cell | cell_tracking | advection"
        ),
    )


# --------------------------------------------------------------------------- #
# Tracking de celdas (Capa 2): estado por celda para el mapa
# --------------------------------------------------------------------------- #

class TrackedCellSchema(BaseModel):
    """Celda de eco rastreada con identidad persistente. Se devuelve desde /radar
    como `tracked_cells` para que la UI dibuje polígonos con ID y trayectoria."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="ID persistente de la celda")
    lat: float = Field(..., ge=-90, le=90, description="Latitud del centroide actual")
    lon: float = Field(..., ge=-180, le=180, description="Longitud del centroide actual")
    mean_dbz: float = Field(..., description="Reflectividad media de la celda (dBZ)")
    area_px: int = Field(..., ge=0, description="Área en píxeles del componente")
    velocity_kmh: float = Field(..., ge=0, description="Velocidad de la celda (km/h)")
    bearing_deg: float = Field(..., ge=0, le=360, description="Rumbo de la celda (0=N, 90=E)")
    age_minutes: float = Field(..., ge=0, description="Tiempo desde primera detección (minutos)")
    ring: list[list[float]] = Field(..., description="Contorno geográfico [[lat,lon],...]")
    track: list[list[float]] = Field(
        default_factory=list,
        description="Historial de centroides [[lat,lon],...] para trazar la trayectoria"
    )
    quality: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Quality score 0–1 (tamaño + forma + persistencia + estabilidad)"
    )
    # ETA al punto monitoreado más cercano (Etapa 4)
    eta_minutes: int | None = Field(
        default=None, ge=0, description="ETA en minutos al punto monitoreado más cercano"
    )
    eta_point_id: str | None = Field(
        default=None, description="ID del punto monitoreado de menor ETA"
    )
    eta_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Confianza de la ETA al punto más cercano"
    )


class CellDetectionSchema(BaseModel):
    """Detección cruda de celda (pre-tracking). Solo para el endpoint de debug /radar/cells."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    area_px: int = Field(..., ge=0)
    mean_dbz: float
    max_dbz: float
    solidity: float = Field(..., ge=0.0, le=1.0)
    extent: float = Field(..., ge=0.0, le=1.0)
    ring: list[list[float]]


class CellDebugDiagSchema(BaseModel):
    """Diagnóstico del ciclo de tracking. Solo para el endpoint /radar/cells."""

    model_config = ConfigDict(extra="forbid")

    n_det: int
    n_alive: int
    n_new: int
    n_continued: int
    n_purged: int
    n_split: int
    n_merge: int
    gate_rejects: int
    match_cost_mean: float | None
    cell_min_px: int
    dbz_threshold: float
    match_max_km: float
    # Diagnóstico del split de blobs (Etapa 1)
    det_n_components: int = 0
    det_n_oversized: int = 0
    det_n_blob_split: int = 0
    det_n_split_subcells: int = 0
    det_n_kept_whole: int = 0


class CellDebugSchema(BaseModel):
    """Respuesta completa del endpoint /radar/cells (detecciones + tracks + diagnóstico)."""

    model_config = ConfigDict(extra="forbid")

    frame_time: str | None
    detections: list[CellDetectionSchema]
    tracks: list[TrackedCellSchema]
    diagnostics: CellDebugDiagSchema
