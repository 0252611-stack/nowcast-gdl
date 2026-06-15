/**
 * Cliente HTTP del frontend para la API Nowcast GDL.
 * El shape de cada respuesta coincide exactamente con backend/app/schemas.py.
 * NUNCA cambiar schemas.py sin actualizar este archivo en el mismo commit.
 */

import { API_BASE } from "./config.js";
const BASE_URL = API_BASE;

async function fetchJson(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    const res = await fetch(`${BASE_URL}${path}`, { signal: controller.signal, ...options });
    if (!res.ok) throw new Error(`${path} no disponible: ${res.status}`);
    if (res.status === 204) return null;
    return await res.json();
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`${path} timeout (>25 s)`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/** @returns {Promise<Array<{id: string, name: string, lat: number, lon: number}>>} */
export async function getPoints() {
  return fetchJson("/points");
}

/**
 * @param {string} pointId
 * @returns {Promise<import('./types').PointForecast>}
 */
export async function getForecast(pointId) {
  return fetchJson(`/points/${pointId}/forecast`);
}

/**
 * @typedef {{ minutes: number, dbz: number, category: string }} IntensityStep
 *
 * @typedef {{ point_id: string, raining_now: boolean, eta_minutes: number|null,
 *   confidence: number|null, horizon_minutes: number, cell_speed_kmh: number|null,
 *   cell_bearing_deg: number|null, cell_lat: number|null, cell_lon: number|null,
 *   bearing_cell_to_point_deg: number|null,
 *   wind_echo_bearing_deg: number|null, wind_echo_speed_kmh: number|null,
 *   trajectory_wind: Array<{lat: number, lon: number, toward_deg: number, speed_kmh: number}>|null,
 *   intensity_trend: number|null, model_agreement: number|null,
 *   conf_radar: number|null, weight_radar: number|null, mult_trend: number|null,
 *   cell_id: number|null, cell_age_minutes: number|null,
 *   leading_edge_distance_km: number|null,
 *   intensity_timeline: IntensityStep[]|null, intensity_verdict: string|null,
 *   generated_at: string, method: string }} NowcastResult
 *
 * @typedef {{ id: number, lat: number, lon: number, mean_dbz: number, area_px: number,
 *   velocity_kmh: number, bearing_deg: number, age_minutes: number,
 *   ring: number[][], track: number[][], quality: number,
 *   eta_minutes: number|null, eta_point_id: string|null, eta_confidence: number|null }} TrackedCell
 *
 * @typedef {{ lat: number, lon: number, area_px: number, mean_dbz: number, max_dbz: number,
 *   solidity: number, extent: number, ring: number[][] }} CellDetection
 *
 * @typedef {{ n_det: number, n_alive: number, n_new: number, n_continued: number,
 *   n_purged: number, n_split: number, n_merge: number, gate_rejects: number,
 *   match_cost_mean: number|null, cell_min_px: number, dbz_threshold: number,
 *   match_max_km: number,
 *   det_n_components: number, det_n_oversized: number, det_n_blob_split: number,
 *   det_n_split_subcells: number, det_n_kept_whole: number }} CellDebugDiag
 *
 * @typedef {{ frame_time: string|null, detections: CellDetection[],
 *   tracks: TrackedCell[], diagnostics: CellDebugDiag }} CellDebug
 *
 * @typedef {{ lat: number, lon: number, dbz: number, bearing_deg: number, speed_kmh: number }} ContextEcho
 *
 * @param {string} pointId
 * @typedef {{ lat: number, lon: number, bearing_deg: number, speed_kmh: number }} MotionVector
 * @typedef {{ ring: number[][], vectors: MotionVector[] }} EnrichedContour
 *
 * @returns {Promise<{radar: object|null, radar_available: boolean, nowcast: NowcastResult|null,
 *   rainviewer_url: string|null, context_echoes: ContextEcho[],
 *   echo_contours: EnrichedContour[], tracked_cells: TrackedCell[],
 *   radar_bounds: {north: number, south: number, east: number, west: number}|null}>}
 */
export async function getRadar(pointId) {
  return fetchJson(`/points/${pointId}/radar`);
}

/**
 * @typedef {{ lat: number, lon: number }} TrajectoryPoint
 * @typedef {{ minutes: number, image_url: string, contours: number[][][] }} PredictionStep
 * @typedef {{
 *   available: boolean,
 *   base_time: string|null,
 *   bounds: {north: number, south: number, east: number, west: number}|null,
 *   method: string,
 *   steps: PredictionStep[],
 *   trajectories: TrajectoryPoint[][],
 * }} PredictionResult
 *
 * @returns {Promise<PredictionResult>}
 */
export async function getPrediction() {
  return fetchJson("/prediction");
}

/**
 * Diagnóstico de detecciones crudas + tracks + métricas del último ciclo de tracking.
 * Útil para inspeccionar la calidad de la detección de celdas.
 * @returns {Promise<CellDebug>}
 */
export async function getCellDebug() {
  return fetchJson("/radar/cells");
}

/**
 * @returns {Promise<{overall: object, forecast_only: object, by_method: object, pending: number, verified: number}>}
 */
export async function getMetrics() {
  return fetchJson("/metrics");
}

/**
 * Estabilidad de la ETA por punto en las últimas `hours` horas.
 * @param {number} hours
 * @returns {Promise<Array<{point_id: string, n: number, eta_mean: number|null,
 *   eta_std: number|null, jitter: number|null, method_changes: number,
 *   pct_with_eta: number, current_method: string|null, last_eta: number|null,
 *   series: Array<number|null>}>>}
 */
export async function getEtaStability(hours = 6) {
  return fetchJson(`/eta-stability?hours=${hours}`);
}

/**
 * @param {{ limit?: number, pointId?: string }} opts
 * @returns {Promise<Array>}
 */
export async function getPredictions({ limit = 100, pointId } = {}) {
  const qs = new URLSearchParams({ limit });
  if (pointId) qs.set("point_id", pointId);
  return fetchJson(`/predictions?${qs}`);
}

function adminHeaders(token) {
  return { "Content-Type": "application/json", "X-Admin-Token": token };
}

/**
 * @param {{ id: string, name: string, lat: number, lon: number }} point
 * @param {string} token
 */
export async function createPoint(point, token) {
  return fetchJson("/points", {
    method: "POST",
    headers: adminHeaders(token),
    body: JSON.stringify(point),
  });
}

/**
 * @param {string} id
 * @param {{ name?: string, lat?: number, lon?: number }} fields
 * @param {string} token
 */
export async function updatePoint(id, fields, token) {
  return fetchJson(`/points/${id}`, {
    method: "PUT",
    headers: adminHeaders(token),
    body: JSON.stringify(fields),
  });
}

/**
 * @param {string} id
 * @param {string} token
 */
export async function deletePoint(id, token) {
  return fetchJson(`/points/${id}`, {
    method: "DELETE",
    headers: adminHeaders(token),
  });
}
