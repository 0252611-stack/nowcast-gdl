"""Configuración global: puntos monitoreados y constantes operativas."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Puntos monitoreados (lat/lon en WGS-84) ---
# Define aquí los puntos reales del AMG. Los IDs deben ser únicos y estables
# (se usan como clave en la BD y en los endpoints REST).
POINTS: list[dict] = [
    {"id": "up_gdl",         "name": "Universidad Panamericana", "lat": 20.68263,   "lon": -103.44197},
    {"id": "puerta_lomas",   "name": "Puerta Las Lomas",         "lat": 20.7054792, "lon": -103.4363111},
    {"id": "club_atlas",     "name": "Club Atlas Colomos",       "lat": 20.7143976, "lon": -103.4025069},
    {"id": "hogar_cabanas",  "name": "Hogar Cabañas",            "lat": 20.650890,  "lon": -103.39572},
]

# --- Sitio del radar IAM (fuente: <lookAt> del doc.kml — constante en todos los frames) ---
# Radar Doppler IAM-CUCEI, Av. Vallarta 2602, Guadalajara, Jalisco.
# Centro geométrico del área de cobertura = posición de la antena.
RADAR_SITE_LAT: float = 20.67555618286133
RADAR_SITE_LON: float = -103.3858337402344

# --- Constantes del radar IAM ---
USER_AGENT = "NowcastGDL/0.1 (proyecto academico)"
POLL_INTERVAL_SECONDS = 90
RADAR_FAIL_THRESHOLD = 3        # Fallos consecutivos → degradar a solo Open-Meteo
DBZ_THRESHOLD = 18.0            # Mínimo dBZ para tracking de movimiento (optical flow)
DBZ_RAIN_THRESHOLD = 18.0       # Mínimo dBZ para "lloviendo ahora" (≥18 = precipitación significativa,
                                # alineado con DBZ_THRESHOLD de tracking). Por debajo → eco débil/virga.
RADAR_RETENTION_HOURS = 24      # Frames SQLite a retener (~960 frames)

# --- Persistencia ---
# En Railway: monta un volume en /data y define DATA_DIR=/data
_data_dir = Path(os.getenv("DATA_DIR", str(Path(__file__).parent.parent)))
DB_PATH = _data_dir / "nowcast.db"

# --- CORS ---
# Separar URLs con coma: "https://app.vercel.app,http://localhost:5173"
ALLOWED_ORIGINS: list[str] = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:5173"
).split(",")

# --- Admin ---
# Definir ADMIN_TOKEN en la env var para habilitar endpoints de escritura.
# Sin token configurado, los endpoints de escritura devuelven 503 (fail-closed).
ADMIN_TOKEN: str | None = os.getenv("ADMIN_TOKEN")

# --- Tracking de celdas de eco ---
CELL_MIN_PX: int = 30          # Área mínima (px) para considerar una celda rastreable
CELL_MATCH_MAX_KM: float = 15.0  # Distancia máx de matching greedy entre ciclos
CELL_MAX_MISSED: int = 1       # Ciclos sin match antes de purgar una celda
CELL_HISTORY_LEN: int = 8      # Longitud del historial de centroides por celda

# --- Logging y observabilidad ---
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
# Ruta del log JSONL estructurado (una línea por ciclo, para análisis posterior).
# Default: <DATA_DIR>/logs/nowcast_diag.jsonl. Configurable vía env DIAG_LOG_PATH.
DIAG_LOG_PATH: str = os.getenv("DIAG_LOG_PATH", str(_data_dir / "logs" / "nowcast_diag.jsonl"))

# --- Quality score de celdas (Capa 2 — solo diagnóstico; no altera la ETA) ---
# Pesos del promedio ponderado; deben sumar 1.0.
CELL_QUALITY_W_AREA: float = 0.30      # contribución del tamaño de la celda
CELL_QUALITY_W_SOLIDITY: float = 0.30  # contribución de la compacidad de la forma
CELL_QUALITY_W_AGE: float = 0.20       # contribución de la persistencia (frames de vida)
CELL_QUALITY_W_STABILITY: float = 0.20 # contribución de la estabilidad del área histórica
# Referencias de normalización
CELL_QUALITY_AREA_REF: int = 300   # area_px en que area_score = 1.0
CELL_QUALITY_AGE_REF: int = 4      # age_frames en que age_score = 1.0

# --- Zonas horarias ---
TZ_LOCAL = ZoneInfo("America/Mexico_City")
TZ_UTC = ZoneInfo("UTC")
