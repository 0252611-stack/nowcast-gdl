/**
 * Datos mock con el shape exacto de backend/app/schemas.py.
 * Sprint 1 — se reemplaza en Sprint 2 con llamadas reales a api.js.
 * Temporada de lluvias de Guadalajara, junio 2026.
 */

// ---------------------------------------------------------------------------
// Puntos monitoreados (AMG)
// ---------------------------------------------------------------------------
export const MOCK_POINTS = [
  { id: "centro",      name: "Centro GDL",   lat: 20.6767, lon: -103.3475 },
  { id: "zapopan",     name: "Zapopan",       lat: 20.7214, lon: -103.3914 },
  { id: "tlaquepaque", name: "Tlaquepaque",   lat: 20.6420, lon: -103.3098 },
  { id: "tonala",      name: "Tonalá",        lat: 20.6236, lon: -103.2347 },
  { id: "tlajomulco",  name: "Tlajomulco",   lat: 20.4734, lon: -103.4432 },
]

// ---------------------------------------------------------------------------
// Helper: genera 12 horas de pronóstico a partir de una hora base
// ---------------------------------------------------------------------------
function makeHourly(baseIso, opts = {}) {
  const {
    rainNow = false,
    peakHour = 3,          // hora (0-11) con máxima lluvia
    maxPrecip = 2.5,
    maxProb = 75,
    baseTemp = 22,
    windBase = 18,
  } = opts

  const base = new Date(baseIso)
  return Array.from({ length: 12 }, (_, i) => {
    const t = new Date(base.getTime() + i * 3600 * 1000)
    const iso = t.toISOString().replace('Z', '-06:00')

    // curva gaussiana de lluvia centrada en peakHour
    const dist = Math.abs(i - peakHour)
    const factor = Math.exp(-(dist * dist) / 6)
    const precip = rainNow && i < 5
      ? +(maxPrecip * factor).toFixed(1)
      : +(maxPrecip * factor * 0.4).toFixed(1)
    const prob = Math.round(maxProb * factor + (Math.random() * 10 - 5))

    // viento en superficie con variación horaria
    const windSpeed = +(windBase + Math.sin(i * 0.7) * 8 + Math.random() * 5).toFixed(1)
    const windDir = Math.round((180 + i * 15 + Math.random() * 20) % 360)

    // viento en 700 hPa (más fuerte, diferente dirección)
    const wind700Speed = +(windSpeed * 2.2 + Math.random() * 10).toFixed(1)
    const wind700Dir = Math.round((windDir + 40 + Math.random() * 30) % 360)

    return {
      time: iso,
      precipitation_mm: precip,
      precipitation_probability: Math.max(0, Math.min(100, prob)),
      temperature_c: +(baseTemp - i * 0.3 + Math.sin(i * 0.5) * 1.5).toFixed(1),
      wind_speed_10m_kmh: windSpeed,
      wind_direction_10m_deg: windDir,
      wind_speed_700hPa_kmh: wind700Speed,
      wind_direction_700hPa_deg: wind700Dir,
    }
  })
}

const BASE_TIME = "2026-06-10T14:00:00-06:00"

// ---------------------------------------------------------------------------
// PointForecast — uno por punto
// ---------------------------------------------------------------------------
export const MOCK_FORECASTS = {
  centro: {
    point_id: "centro",
    name: "Centro GDL",
    lat: 20.6767,
    lon: -103.3475,
    generated_at: "2026-06-10T20:05:00Z",
    timezone: "America/Mexico_City",
    hourly: makeHourly(BASE_TIME, { rainNow: true, peakHour: 1, maxPrecip: 5.2, maxProb: 85, baseTemp: 23 }),
  },
  zapopan: {
    point_id: "zapopan",
    name: "Zapopan",
    lat: 20.7214,
    lon: -103.3914,
    generated_at: "2026-06-10T20:05:00Z",
    timezone: "America/Mexico_City",
    hourly: makeHourly(BASE_TIME, { rainNow: false, peakHour: 4, maxPrecip: 3.1, maxProb: 60, baseTemp: 22 }),
  },
  tlaquepaque: {
    point_id: "tlaquepaque",
    name: "Tlaquepaque",
    lat: 20.6420,
    lon: -103.3098,
    generated_at: "2026-06-10T20:05:00Z",
    timezone: "America/Mexico_City",
    hourly: makeHourly(BASE_TIME, { rainNow: true, peakHour: 2, maxPrecip: 7.8, maxProb: 90, baseTemp: 24 }),
  },
  tonala: {
    point_id: "tonala",
    name: "Tonalá",
    lat: 20.6236,
    lon: -103.2347,
    generated_at: "2026-06-10T20:05:00Z",
    timezone: "America/Mexico_City",
    hourly: makeHourly(BASE_TIME, { rainNow: false, peakHour: 6, maxPrecip: 1.2, maxProb: 35, baseTemp: 25 }),
  },
  tlajomulco: {
    point_id: "tlajomulco",
    name: "Tlajomulco",
    lat: 20.4734,
    lon: -103.4432,
    generated_at: "2026-06-10T20:05:00Z",
    timezone: "America/Mexico_City",
    hourly: makeHourly(BASE_TIME, { rainNow: false, peakHour: 8, maxPrecip: 2.0, maxProb: 45, baseTemp: 21 }),
  },
}

// ---------------------------------------------------------------------------
// RadarReading — null para tlajomulco (simular radar no disponible)
// ---------------------------------------------------------------------------
export const MOCK_RADAR = {
  centro: {
    point_id: "centro",
    dbz: 42.5,
    category: "Moderada a fuerte",
    scan_time_utc: "2026-06-10T20:01:30Z",
    frame_age_seconds: 210,
    pixel_x: 412,
    pixel_y: 288,
  },
  zapopan: {
    point_id: "zapopan",
    dbz: 8.0,
    category: "Ruido",
    scan_time_utc: "2026-06-10T20:01:30Z",
    frame_age_seconds: 210,
    pixel_x: 398,
    pixel_y: 271,
  },
  tlaquepaque: {
    point_id: "tlaquepaque",
    dbz: 51.0,
    category: "Granizo",
    scan_time_utc: "2026-06-10T20:01:30Z",
    frame_age_seconds: 210,
    pixel_x: 428,
    pixel_y: 301,
  },
  tonala: {
    point_id: "tonala",
    dbz: 18.5,
    category: "Débil",
    scan_time_utc: "2026-06-10T20:01:30Z",
    frame_age_seconds: 210,
    pixel_x: 445,
    pixel_y: 305,
  },
  tlajomulco: null,  // radar no disponible para este punto
}

// ---------------------------------------------------------------------------
// NowcastResult
// ---------------------------------------------------------------------------
export const MOCK_NOWCAST = {
  centro: {
    point_id: "centro",
    raining_now: true,
    eta_minutes: null,       // ya está lloviendo
    confidence: 0.91,
    horizon_minutes: 60,
    cell_speed_kmh: 28.4,
    cell_bearing_deg: 245,
    generated_at: "2026-06-10T20:03:00Z",
    method: "radar_extrapolation",
  },
  zapopan: {
    point_id: "zapopan",
    raining_now: false,
    eta_minutes: 38,
    confidence: 0.62,
    horizon_minutes: 60,
    cell_speed_kmh: 22.1,
    cell_bearing_deg: 230,
    generated_at: "2026-06-10T20:03:00Z",
    method: "radar_extrapolation",
  },
  tlaquepaque: {
    point_id: "tlaquepaque",
    raining_now: true,
    eta_minutes: null,       // ya está lloviendo
    confidence: 0.95,
    horizon_minutes: 60,
    cell_speed_kmh: 31.0,
    cell_bearing_deg: 255,
    generated_at: "2026-06-10T20:03:00Z",
    method: "radar_extrapolation",
  },
  tonala: {
    point_id: "tonala",
    raining_now: false,
    eta_minutes: 15,
    confidence: 0.74,
    horizon_minutes: 60,
    cell_speed_kmh: 19.7,
    cell_bearing_deg: 240,
    generated_at: "2026-06-10T20:03:00Z",
    method: "radar_extrapolation",
  },
  tlajomulco: {
    point_id: "tlajomulco",
    raining_now: false,
    eta_minutes: null,       // sin estimación (radar no disponible)
    confidence: null,
    horizon_minutes: 60,
    cell_speed_kmh: null,
    cell_bearing_deg: null,
    generated_at: "2026-06-10T20:03:00Z",
    method: "open_meteo_only",
  },
}
