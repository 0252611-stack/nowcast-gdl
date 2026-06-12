/**
 * Vista /mapa — Mapa interactivo con radar RainViewer y todos los puntos monitoreados.
 * Muestra ecos causantes y flechas de dirección cuando hay ETA activa.
 */

import { useState, useEffect } from "react"
import CellMap from "../components/CellMap.jsx"
import { getPoints, getRadar } from "../api.js"
import { theme } from "../theme.js"

export default function MapView() {
  const [points, setPoints] = useState([])
  const [nowcasts, setNowcasts] = useState({})
  const [rainviewerUrl, setRainviewerUrl] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const pts = await getPoints()
        if (cancelled) return
        setPoints(pts)
        const results = await Promise.all(pts.map(pt => getRadar(pt.id)))
        if (cancelled) return
        const nw = {}
        let rvUrl = null
        pts.forEach((pt, i) => {
          nw[pt.id] = results[i].nowcast ?? null
          if (!rvUrl && results[i].rainviewer_url) rvUrl = results[i].rainviewer_url
        })
        setNowcasts(nw)
        setRainviewerUrl(rvUrl)
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  const rainInfo = Object.values(nowcasts).filter(n => n?.eta_minutes != null && !n.raining_now)

  return (
    <div style={st.container}>
      <div style={st.header}>
        <h2 style={st.title}>Mapa de radar — AMG</h2>
        {rainInfo.length > 0 && (
          <div style={st.alert}>
            {rainInfo.length} punto{rainInfo.length > 1 ? "s" : ""} con lluvia próxima detectada
          </div>
        )}
      </div>

      {loading && <p style={st.status}>Cargando datos…</p>}
      {error && <p style={st.error}>Error: {error}</p>}

      {!loading && !error && (
        <>
          <div style={st.mapWrapper}>
            <CellMap
              points={points}
              nowcasts={nowcasts}
              rainviewerUrl={rainviewerUrl}
              height="calc(100vh - 220px)"
            />
          </div>

          {/* Leyenda */}
          <div style={st.legend}>
            <span style={st.legendItem}>
              <span style={{ ...st.dot, background: theme.primary }} /> Punto sin lluvia
            </span>
            <span style={st.legendItem}>
              <span style={{ ...st.dot, background: theme.green }} /> Lloviendo ahora
            </span>
            <span style={st.legendItem}>
              <span style={{ ...st.dot, background: theme.orange }} /> Eco causante
            </span>
            <span style={st.legendItem}>
              <span style={{ color: theme.textFaint, fontSize: "11px" }}>— Trayectoria</span>
            </span>
          </div>
        </>
      )}
    </div>
  )
}

const st = {
  container: { padding: "24px", maxWidth: "1280px", margin: "0 auto", width: "100%" },
  header: { display: "flex", alignItems: "center", gap: "16px", marginBottom: "16px", flexWrap: "wrap" },
  title: { fontSize: "18px", fontWeight: 700, color: theme.text, margin: 0 },
  alert: {
    padding: "4px 14px",
    borderRadius: "999px",
    background: theme.accentLight,
    border: `1px solid ${theme.accent}55`,
    color: "#92400E",
    fontSize: "12px",
    fontWeight: 600,
  },
  mapWrapper: { borderRadius: "14px", overflow: "hidden", border: `1px solid ${theme.border}`, boxShadow: theme.shadow },
  status: { color: theme.textFaint, textAlign: "center", padding: "40px 0" },
  error: { color: theme.red, textAlign: "center", padding: "40px 0" },
  legend: { display: "flex", gap: "20px", flexWrap: "wrap", marginTop: "12px" },
  legendItem: { display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: theme.textMuted },
  dot: { display: "inline-block", width: "10px", height: "10px", borderRadius: "50%" },
}
