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

# --- Optical flow (Farneback) ---
FLOW_PYR_SCALE: float  = 0.5   # escala entre niveles de pirámide
FLOW_LEVELS:    int    = 3      # niveles de pirámide
FLOW_WINSIZE:   int    = 25     # tamaño de ventana de integración (↑ vs 15 → más coherencia)
FLOW_ITERATIONS: int   = 3      # iteraciones por nivel
FLOW_POLY_N:    int    = 5      # tamaño de vecindad polinomial
FLOW_POLY_SIGMA: float = 1.2    # sigma del filtro gaussiano del polinomio
# Suavizado espacial del flujo después de calcOpticalFlowFarneback.
# 0 = desactivado; valor impar > 0 = kernel de GaussianBlur (p.ej. 9 ≈ ~2 km en GDL).
FLOW_SMOOTH_KSIZE: int = 9

# --- Tracking de celdas de eco ---
CELL_MIN_PX: int = 30          # Área mínima (px) para considerar una celda rastreable
CELL_MATCH_MAX_KM: float = 15.0  # Distancia máx de matching greedy entre ciclos
CELL_MAX_MISSED: int = 1       # Ciclos sin match antes de purgar una celda
CELL_HISTORY_LEN: int = 8      # Longitud del historial de centroides por celda
# Two-level split: componentes con area > CELL_MAX_PX se re-segmentan al umbral de núcleo.
# Calibrado para cubrir ~3 km² en la imagen del radar IAM (≈ 5 px/km → ~75 px).
CELL_MAX_PX: int = 2_000       # área (px) sobre la que se intenta partir el blob
CELL_SPLIT_DBZ: float = 30.0   # umbral de núcleo convectivo para el split (dBZ)

# --- Logging y observabilidad ---
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
# Ruta del log JSONL estructurado (una línea por ciclo, para análisis posterior).
# Default: <DATA_DIR>/logs/nowcast_diag.jsonl. Configurable vía env DIAG_LOG_PATH.
DIAG_LOG_PATH: str = os.getenv("DIAG_LOG_PATH", str(_data_dir / "logs" / "nowcast_diag.jsonl"))

# --- Quality score de celdas (Capa 2 — solo diagnóstico; no altera la ETA) ---
# Pesos del promedio ponderado; deben sumar 1.0.
CELL_QUALITY_W_AREA:      float = 0.30  # tamaño normalizado de la celda
CELL_QUALITY_W_SOLIDITY:  float = 0.25  # compacidad de la forma (solidity)
CELL_QUALITY_W_AGE:       float = 0.15  # persistencia (frames de vida)
CELL_QUALITY_W_STABILITY: float = 0.15  # estabilidad del área histórica (CV bajo → alto)
CELL_QUALITY_W_VELOCITY:  float = 0.15  # estabilidad del movimiento (velocidad + bearing)
# Referencias de normalización
CELL_QUALITY_AREA_REF: int = 300   # area_px en que area_score = 1.0
CELL_QUALITY_AGE_REF: int = 4      # age_frames en que age_score = 1.0
# Penalización por ciclos sin match (celda parpadeante/ausente)
CELL_QUALITY_MISSED_PENALTY: float = 0.15

# --- Zonas horarias ---
TZ_LOCAL = ZoneInfo("America/Mexico_City")
TZ_UTC = ZoneInfo("UTC")
