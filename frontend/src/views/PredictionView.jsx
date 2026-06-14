/**
 * Vista /prediccion — Nowcast advectivo del campo de eco con animación temporal.
 *
 * Paso 0 = frame actual del radar (imagen en vivo + trayectorias).
 * Pasos 1-8 = frames advectados +15…+120 min (sin trayectorias, con contornos).
 *
 * Motor: optical flow denso (Farneback) + corrección viento 700 hPa en malla 4×4.
 * Precisión honesta: ~20-30 min; se comunica visualmente con opacidad decreciente.
 */

import { useState, useEffect } from "react"
import CellMap from "../components/CellMap.jsx"
import TimeSlider from "../components/TimeSlider.jsx"
import { getPoints, getRadar, getPrediction } from "../api.js"
import { theme } from "../theme.js"

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

export default function PredictionView() {
  const [points, setPoints]           = useState([])
  const [nowcasts, setNowcasts]       = useState({})
  const [currentContours, setCurrentContours] = useState([])
  const [radarBounds, setRadarBounds] = useState(null)
  const [prediction, setPrediction]   = useState(null)
  const [step, setStep]               = useState(0)
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const pts = await getPoints()
        if (cancelled) return
        setPoints(pts)

        // Cargamos radar (1 punto basta; los contornos son globales) y predicción en paralelo
        const [radarResult, predResult] = await Promise.all([
          getRadar(pts[0]?.id ?? ""),
          getPrediction(),
        ])
        if (cancelled) return

        // Nowcasts de todos los puntos
        const nwResults = await Promise.all(pts.map(pt => getRadar(pt.id)))
        if (cancelled) return

        const nw = {}
        pts.forEach((pt, i) => { nw[pt.id] = nwResults[i].nowcast ?? null })
        setNowcasts(nw)

        if (radarResult.radar_bounds) setRadarBounds(radarResult.radar_bounds)
        setCurrentContours(radarResult.echo_contours ?? [])
        setPrediction(predResult)
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  // Datos del paso actual
  const currentStep = step === 0 ? null : prediction?.steps?.[step - 1] ?? null

  const radarImageUrl = step === 0
    ? `${API_BASE}/radar/image`
    : currentStep ? `${API_BASE}${currentStep.image_url}` : null

  const echoContours = step === 0
    ? currentContours
    : (currentStep?.contours ?? [])

  // Trayectorias solo en el paso 0 (frame actual)
  const trajectories = step === 0 ? (prediction?.trajectories ?? []) : []

  const steps = prediction?.steps ?? []
  const available = prediction?.available ?? false

  return (
    <div style={st.container}>
      <div style={st.header}>
        <h2 style={st.title}>Predicción de campo — próximas 2 horas</h2>
        {!loading && available && (
          <div style={st.badge}>
            {prediction.method === "semi_lagrangian" ? "Adv. semi-Lagrangiana" : "Persistencia"}
          </div>
        )}
      </div>

      {loading && <p style={st.status}>Calculando predicción…</p>}
      {error   && <p style={st.error}>Error: {error}</p>}

      {!loading && !error && !available && (
        <p style={st.status}>Radar no disponible — la predicción requiere datos del radar IAM.</p>
      )}

      {!loading && !error && available && (
        <>
          {/* Aviso de incertidumbre */}
          {step > 2 && (
            <div style={st.warning}>
              ⚠ La precisión disminuye después de ~30 min. Los ecos pueden formarse o
              disiparse sin aparecer en la predicción.
            </div>
          )}

          {/* Mapa */}
          <div style={st.mapWrapper}>
            <CellMap
              points={points}
              nowcasts={step === 0 ? nowcasts : {}}
              rainviewerUrl={null}
              height="calc(100vh - 300px)"
              echoContours={echoContours}
              trajectories={trajectories}
              radarImageUrl={radarImageUrl}
              radarBounds={radarBounds}
            />
            {/* TimeSlider superpuesto al pie del mapa */}
            <TimeSlider
              step={step}
              steps={steps}
              baseTime={prediction?.base_time}
              onStepChange={setStep}
            />
          </div>

          {/* Leyenda */}
          <div style={st.legend}>
            <span style={st.legendItem}>
              <span style={{ ...st.colorLine, background: theme.text }} />
              Contorno del eco
            </span>
            <span style={st.legendItem}>
              <span style={{ ...st.colorLine, background: theme.orange }} />
              Eco causante
            </span>
            <span style={st.legendItem}>
              <span style={{ ...st.colorLine, background: theme.primary, borderStyle: "dashed" }} />
              Trayectoria prevista (t=0)
            </span>
            <span style={st.legendItem}>
              <span style={{ ...st.dot, background: theme.primary }} /> Punto monitoreado
            </span>
          </div>
        </>
      )}
    </div>
  )
}

const st = {
  container:  { padding: "24px", maxWidth: "1280px", margin: "0 auto", width: "100%" },
  header:     { display: "flex", alignItems: "center", gap: "12px", marginBottom: "12px", flexWrap: "wrap" },
  title:      { fontSize: "18px", fontWeight: 700, color: theme.text, margin: 0 },
  badge: {
    padding: "3px 12px", borderRadius: "999px",
    background: theme.primaryLight, border: `1px solid ${theme.primary}55`,
    color: theme.primary, fontSize: "11px", fontWeight: 600,
  },
  warning: {
    marginBottom: "10px",
    padding: "8px 14px",
    borderRadius: "8px",
    background: theme.accentLight,
    border: `1px solid ${theme.accent}55`,
    color: "#92400E",
    fontSize: "12px",
  },
  mapWrapper: {
    borderRadius: "14px",
    overflow: "hidden",
    border: `1px solid ${theme.border}`,
    boxShadow: theme.shadow,
    display: "flex",
    flexDirection: "column",
  },
  status:  { color: theme.textFaint, textAlign: "center", padding: "40px 0" },
  error:   { color: theme.red,       textAlign: "center", padding: "40px 0" },
  legend:  { display: "flex", gap: "20px", flexWrap: "wrap", marginTop: "12px" },
  legendItem: { display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: theme.textMuted },
  colorLine: {
    display: "inline-block", width: "20px", height: "3px",
    verticalAlign: "middle", borderTop: "3px solid",
  },
  dot: { display: "inline-block", width: "10px", height: "10px", borderRadius: "50%" },
}
