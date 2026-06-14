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
 * @typedef {{ point_id: string, raining_now: boolean, eta_minutes: number|null,
 *   confidence: number|null, horizon_minutes: number, cell_speed_kmh: number|null,
 *   cell_bearing_deg: number|null, cell_lat: number|null, cell_lon: number|null,
 *   bearing_cell_to_point_deg: number|null,
 *   wind_echo_bearing_deg: number|null, wind_echo_speed_kmh: number|null,
 *   trajectory_wind: Array<{lat: number, lon: number, toward_deg: number, speed_kmh: number}>|null,
 *   intensity_trend: number|null, model_agreement: number|null,
 *   conf_radar: number|null, weight_radar: number|null, mult_trend: number|null,
 *   generated_at: string, method: string }} NowcastResult
 *
 * @typedef {{ lat: number, lon: number, dbz: number, bearing_deg: number, speed_kmh: number }} ContextEcho
 *
 * @param {string} pointId
 * @typedef {{ lat: number, lon: number, bearing_deg: number, speed_kmh: number }} MotionVector
 * @typedef {{ ring: number[][], vectors: MotionVector[] }} EnrichedContour
 *
 * @returns {Promise<{radar: object|null, radar_available: boolean, nowcast: NowcastResult|null,
 *   rainviewer_url: string|null, context_echoes: ContextEcho[],
 *   echo_contours: EnrichedContour[],
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
