/**
 * Cliente HTTP del frontend para la API Nowcast GDL.
 * El shape de cada respuesta coincide exactamente con backend/app/schemas.py.
 * NUNCA cambiar schemas.py sin actualizar este archivo en el mismo commit.
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

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
 *   bearing_cell_to_point_deg: number|null, generated_at: string, method: string }} NowcastResult
 *
 * @param {string} pointId
 * @returns {Promise<{radar: object|null, radar_available: boolean, nowcast: NowcastResult|null, rainviewer_url: string|null}>}
 */
export async function getRadar(pointId) {
  return fetchJson(`/points/${pointId}/radar`);
}

/**
 * @returns {Promise<{overall: object, forecast_only: object, by_method: object, pending: number, verified: number}>}
 */
export async function getMetrics() {
  return fetchJson("/metrics");
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
