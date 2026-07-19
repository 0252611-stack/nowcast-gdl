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
    # Sesión 15 (15-jul-2026) — 15 puntos estratégicos + 4 direcciones específicas,
    # para acelerar la recolección de datos con mayor cobertura del AMG.
    {"id": "punto_1",  "name": "Punto 1",  "lat": 20.677034,  "lon": -103.346984},   # Catedral GDL, centro
    {"id": "punto_2",  "name": "Punto 2",  "lat": 20.6714793, "lon": -103.4410621},  # Parque Metropolitano, Zapopan
    {"id": "punto_3",  "name": "Punto 3",  "lat": 20.6395117, "lon": -103.3090156},  # Jardín Hidalgo, Tlaquepaque
    {"id": "punto_4",  "name": "Punto 4",  "lat": 20.6246256, "lon": -103.24242},    # Centro, Tonalá
    {"id": "punto_5",  "name": "Punto 5",  "lat": 20.475623,  "lon": -103.4456882},  # Centro, Tlajomulco de Zúñiga
    {"id": "punto_6",  "name": "Punto 6",  "lat": 20.7307838, "lon": -103.3879727},  # Zapopan Centro
    {"id": "punto_7",  "name": "Punto 7",  "lat": 20.6024902, "lon": -103.4469054},  # Bugambilias, SO Zapopan
    {"id": "punto_8",  "name": "Punto 8",  "lat": 20.6505195, "lon": -103.4013333},  # Plaza del Sol
    {"id": "punto_9",  "name": "Punto 9",  "lat": 20.5190865, "lon": -103.178122},   # Centro, El Salto
    {"id": "punto_10", "name": "Punto 10", "lat": 20.6270376, "lon": -103.4121672},  # Las Águilas, Zapopan
    {"id": "punto_11", "name": "Punto 11", "lat": 20.6171935, "lon": -103.3515091},  # Miravalle, Guadalajara
    {"id": "punto_12", "name": "Punto 12", "lat": 20.8016245, "lon": -103.4791946},  # Tesistán, norte Zapopan
    {"id": "punto_13", "name": "Punto 13", "lat": 20.6747187, "lon": -103.3255216},  # Oblatos, este Guadalajara
    {"id": "punto_14", "name": "Punto 14", "lat": 20.567017,  "lon": -103.4699635},  # Santa Anita, SO Tlajomulco
    {"id": "punto_15", "name": "Punto 15", "lat": 20.6782083, "lon": -103.4482868},  # Ciudad Granja, oeste Zapopan
    {"id": "catalunia_40",     "name": "Cataluña 40",       "lat": 20.7183089, "lon": -103.4171707},  # Puerta de Hierro, Zapopan
    {"id": "bahia_acapulco",   "name": "Bahía de Acapulco", "lat": 20.6017382, "lon": -103.4159593},  # San Pedro Tlaquepaque
    {"id": "oficina_ingredion","name": "Oficina Ingredion", "lat": 20.7105972, "lon": -103.4141243},  # Andares, Zapopan
    {"id": "hella",            "name": "HELLA",             "lat": 20.6085621, "lon": -103.4042517},  # Edificio Connect, Tlaquepaque
    # Sesión 17 (17-jul-2026) — anillo exterior fuera de la ZMG, 18 cabeceras
    # municipales reales dentro del rango útil del radar (~100 km). Prefijo
    # "ext_" → oculto del dashboard de inicio (ver HIDDEN_ON_HOME en App.jsx),
    # igual que punto_1..15: solo para análisis (skill/trayectoria), no son
    # direcciones de usuario. Coordenadas verificadas por búsqueda web
    # (cabecera municipal/plaza principal), espaciamiento ≥15 km entre sí y
    # respecto a los 23 puntos anteriores (ver análisis de sesión 17).
    {"id": "ext_ixtlahuacan_rio", "name": "Ixtlahuacán del Río", "lat": 20.8641,   "lon": -103.2383},   # 26 km NE
    {"id": "ext_san_cristobal",   "name": "San Cristóbal de la Barranca", "lat": 21.044444, "lon": -103.429167},  # 41 km N
    {"id": "ext_el_arenal",       "name": "El Arenal",         "lat": 20.775556, "lon": -103.693333},  # 34 km WNW
    {"id": "ext_zapotlanejo",     "name": "Zapotlanejo",       "lat": 20.6222,   "lon": -103.0683},    # 34 km E
    {"id": "ext_acatlan_juarez",  "name": "Acatlán de Juárez", "lat": 20.4205,   "lon": -103.5911},    # 36 km SW
    {"id": "ext_cuquio",          "name": "Cuquío",            "lat": 20.927586, "lon": -103.023046},  # 47 km NE
    {"id": "ext_chapala",         "name": "Chapala",           "lat": 20.29028,  "lon": -103.19194},   # 47 km SSE
    {"id": "ext_tequila",         "name": "Tequila",           "lat": 20.882778, "lon": -103.836667},  # 52 km WNW
    {"id": "ext_zapotlan_rey",    "name": "Zapotlán del Rey",  "lat": 20.46589,  "lon": -102.92148},   # 54 km ESE
    {"id": "ext_cocula",          "name": "Cocula",            "lat": 20.365389, "lon": -103.822775},  # 57 km SW
    {"id": "ext_tototlan",        "name": "Tototlán",          "lat": 20.542257, "lon": -102.793381},  # 63 km ESE
    {"id": "ext_ameca",           "name": "Ameca",             "lat": 20.547778, "lon": -104.047222},  # 70 km WSW
    {"id": "ext_etzatlan",       "name": "Etzatlán",          "lat": 20.764722, "lon": -104.080556},  # 73 km W
    {"id": "ext_ocotlan",         "name": "Ocotlán",           "lat": 20.3553,   "lon": -102.77358},   # 73 km ESE
    {"id": "ext_yahualica",       "name": "Yahualica de González Gallo", "lat": 21.181667, "lon": -102.890556},  # 76 km NE
    {"id": "ext_hostotipaquillo", "name": "Hostotipaquillo",   "lat": 21.058056, "lon": -104.051389},  # 81 km WNW
    {"id": "ext_atotonilco_alto", "name": "Atotonilco el Alto", "lat": 20.5502447, "lon": -102.5081224}, # 92 km E
    {"id": "ext_la_barca",        "name": "La Barca",          "lat": 20.276940, "lon": -102.548890},  # 98 km ESE
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
CELL_MATCH_MAX_KM: float = 15.0  # Techo espacial absoluto de matching greedy (usado tal cual
                                  # solo tras huecos largos del IAM; en cadencia normal el gate
                                  # real es más estricto, ver CELL_MAX_SPEED_KMH)
# Tope físico de velocidad de una celda de tormenta en el AMG/GDL. Se usa para (a) el gate
# dinámico de matching (max_km = min(CELL_MATCH_MAX_KM, CELL_MAX_SPEED_KMH * interval_s/3600))
# y (b) el clamp de raw_speed tras el emparejamiento — evita que un identity-swap del tracker
# (merge/split enlazando la celda con un blob distinto) produzca velocidades de cientos de
# km/h. 80 km/h cubre incluso líneas de tormenta severas sin recortar convección típica (10-40).
CELL_MAX_SPEED_KMH: float = 80.0
CELL_MAX_MISSED: int = 1       # Ciclos sin match antes de purgar una celda
CELL_HISTORY_LEN: int = 8      # Longitud del historial de centroides por celda
# Two-level split: componentes con area > CELL_MAX_PX se re-segmentan al umbral de núcleo.
# Calibrado para cubrir ~3 km² en la imagen del radar IAM (≈ 5 px/km → ~75 px).
CELL_MAX_PX: int = 2_000       # área (px) sobre la que se intenta partir el blob
CELL_SPLIT_DBZ: float = 30.0   # umbral de núcleo convectivo para el split (dBZ)

# --- Persistencia del estado de tracking entre reinicios (Etapa 3) ---
# Máximo de minutos que puede tener el estado guardado para ser considerado
# válido al arrancar. Si el estado es más viejo, se descarta y empieza limpio.
TRACKING_STATE_MAX_AGE_MIN: int = 30

# --- Predicción de posición por regresión lineal (Etapa 2) ---
# Cuando True, usa numpy.polyfit sobre los últimos CELL_PREDICT_MIN_HISTORY
# centroides para predecir la posición futura. Si el historial es más corto
# o la flag es False, cae al _predict_position original (EMA de velocidad).
CELL_PREDICT_REGRESSION: bool = True
CELL_PREDICT_MIN_HISTORY: int = 3

# --- Verificación de skill: confianza mínima para contar como "predijo lluvia" ---
# Hallazgo (sesión de análisis diurno): sin este umbral, CUALQUIER eta_minutes cuenta
# para POD/FAR/CSI sin importar la confianza — ecos de madrugada en disipación con
# conf~0.005-0.05 se marcaban como falsa alarma al no materializarse, aunque el motor
# ya sabía que eran poco confiables (vs conf~0.5+ en tarde/noche, FAR~0%). Solo afecta
# la métrica de skill (storage.py); NO cambia lo que se muestra al usuario (NowcastResult
# sigue exponiendo eta_minutes/confidence tal cual, con su tooltip de incertidumbre).
PREDICTED_RAIN_MIN_CONFIDENCE: float = 0.30

# --- Logging y observabilidad ---
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
# Ruta del log JSONL estructurado (una línea por ciclo, para análisis posterior).
# Default: <DATA_DIR>/logs/nowcast_diag.jsonl. Configurable vía env DIAG_LOG_PATH.
DIAG_LOG_PATH: str = os.getenv("DIAG_LOG_PATH", str(_data_dir / "logs" / "nowcast_diag.jsonl"))
# Días de historial a conservar en el JSONL de diagnóstico. Sin esto el archivo
# crece sin límite (append-only, sin purga). Se recorta una vez por hora
# (run_forecast_loop), eliminando las líneas más viejas que el retention.
# 180 días ≈ una temporada de lluvias completa; al ritmo medido en producción
# (~6 KB/ciclo con cells[]+puntos actuales) esto son solo ~1 GB en disco.
DIAG_LOG_RETENTION_DAYS: int = int(os.getenv("DIAG_LOG_RETENTION_DAYS", "180"))
# Tope duro de tamaño (bytes) — red de seguridad independiente de los días de
# arriba. Si algo dispara el ritmo de crecimiento sin que nadie lo note (ej.
# un bug de tracking que multiplique las celdas detectadas por ciclo), esto
# evita que el disco se llene antes de que el recorte por días vuelva a bajarlo.
# No debería activarse en operación normal. Default 3 GB (subido de 2GB en
# sesión 17 al pasar de 23 a 41 puntos: el registro por punto en el JSONL
# ["points[]"] ya cruzaba el tope de 2GB/180d en temporada de lluvia activa
# con >10 celdas simultáneas — ver análisis de sesión 17. Hay ~23GB libres
# medidos en la VM, sobra margen).
DIAG_LOG_MAX_BYTES: int = int(os.getenv("DIAG_LOG_MAX_BYTES", str(3 * 1024 * 1024 * 1024)))
# Retención de radar_frames/point_readings en SQLite (horas). point_readings
# no tenía purga propia — sin esto crecía sin límite (una fila por punto/ciclo).
READINGS_RETENTION_HOURS: int = 24

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

# --- Closing speed del ETA (Capa: dirección bloquea ETA, no solo confianza) ---
# Velocidad mínima de acercamiento (componente radial hacia el punto) para
# emitir un ETA. Por debajo, la celda no se está acercando de verdad
# (movimiento lateral o alejándose) y el ETA distancia/velocidad es ficción.
MIN_CLOSING_SPEED_KMH: float = 2.0
# Horizonte máximo del ETA. Antes 240 min: un ETA de 3-4h por advección
# lineal no es información, es ruido (la UI ya avisa que >30 min degrada).
ETA_HORIZON_MINUTES: int = 120

# --- Timeline de intensidad por punto (Etapa 5) ---
# Pasos de tiempo en minutos para el backtrace semi-lagrangiano por punto.
INTENSITY_TIMELINE_STEPS_MIN: tuple = (0, 15, 30, 45)
# Umbral de diferencia dBZ(45min) − dBZ(0min) para el veredicto empeora/mejora.
INTENSITY_VERDICT_DBZ_DELTA: float = 3.0

# --- Zonas horarias ---
TZ_LOCAL = ZoneInfo("America/Mexico_City")
TZ_UTC = ZoneInfo("UTC")
