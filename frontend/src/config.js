/** Configuración global del cliente frontend. */

/** URL base de la API Nowcast GDL (puede sobreescribirse con la variable de entorno VITE_API_URL). */
export const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
