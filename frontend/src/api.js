/**
 * Cliente HTTP del frontend para la API Nowcast GDL.
 * El shape de cada respuesta coincide exactamente con backend/app/schemas.py.
 * NUNCA cambiar schemas.py sin actualizar este archivo en el mismo commit.
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function fetchJson(path) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const res = await fetch(`${BASE_URL}${path}`, { signal: controller.signal });
    if (!res.ok) throw new Error(`${path} no disponible: ${res.status}`);
    return await res.json();
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`${path} timeout (>5 s)`);
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
 * @param {string} pointId
 * @returns {Promise<{radar: import('./types').RadarReading|null, radar_available: boolean, nowcast: import('./types').NowcastResult|null}>}
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
