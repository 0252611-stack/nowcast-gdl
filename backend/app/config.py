"""Configuración global: puntos monitoreados y constantes operativas."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Puntos monitoreados (lat/lon en WGS-84) ---
# Define aquí los puntos reales del AMG. Los IDs deben ser únicos y estables
# (se usan como clave en la BD y en los endpoints REST).
POINTS: list[dict] = [
    {"id": "up_gdl",       "name": "Universidad Panamericana", "lat": 20.68263,   "lon": -103.44197},
    {"id": "puerta_lomas", "name": "Puerta Las Lomas",         "lat": 20.7054792, "lon": -103.4363111},
    {"id": "club_atlas",   "name": "Club Atlas Colomos",       "lat": 20.7143976, "lon": -103.4025069},
]

# --- Constantes del radar IAM ---
USER_AGENT = "NowcastGDL/0.1 (proyecto academico)"
POLL_INTERVAL_SECONDS = 90
RADAR_FAIL_THRESHOLD = 3        # Fallos consecutivos → degradar a solo Open-Meteo
DBZ_THRESHOLD = 18.0            # Mínimo dBZ para tracking de movimiento (optical flow)
DBZ_RAIN_THRESHOLD = -10.0      # Mínimo dBZ para "lloviendo ahora" — frontera Ruido/Débil del IAM
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

# --- Zonas horarias ---
TZ_LOCAL = ZoneInfo("America/Mexico_City")
TZ_UTC = ZoneInfo("UTC")
