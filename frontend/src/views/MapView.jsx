/**
 * Vista /mapa — Mapa interactivo con radar IAM como ImageOverlay y puntos monitoreados.
 */

import { useState, useEffect } from "react"
import CellMap from "../components/CellMap.jsx"
import { getPoints, getRadar } from "../api.js"
import { theme } from "../theme.js"
import { API_BASE } from "../config.js"

const LAYERS = [
  { key: "showRadar",    label: "Radar",     title: "Mostrar/ocultar la imagen del radar IAM como fondo" },
  { key: "showContours", label: "Contornos", title: "Mostrar/ocultar los contornos de los ecos de lluvia" },
  { key: "showArrows",   label: "Flechas",   title: "Mostrar/ocultar las flechas de dirección del campo óptico" },
  { key: "showPoints",   label: "Puntos",    title: "Mostrar/ocultar los puntos monitoreados y sus ecos causantes" },
]

export default function MapView() {
  const [points, setPoints] = useState([])
  const [nowcasts, setNowcasts] = useState({})
  const [rainviewerUrl, setRainviewerUrl] = useState(null)
  const [contextEchoes, setContextEchoes] = useState([])
  const [echoContours, setEchoContours] = useState([])
  const [radarBounds, setRadarBounds] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [layers, setLayers] = useState({ showRadar: true, showContours: true, showArrows: true, showPoints: true })

  function toggleLayer(key) {
    setLayers(prev => ({ ...prev, [key]: !prev[key] }))
  }

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
        let bounds = null
        pts.forEach((pt, i) => {
          nw[pt.id] = results[i].nowcast ?? null
          if (!rvUrl && results[i].rainviewer_url) rvUrl = results[i].rainviewer_url
          if (!bounds && results[i].radar_bounds)  bounds = results[i].radar_bounds
        })
        setNowcasts(nw)
        setRainviewerUrl(rvUrl)
        setRadarBounds(bounds)

        // Deduplicar ecos de contexto (mismos datos en todas las respuestas)
        const allEchoes = results.flatMap(r => r.context_echoes ?? [])
        const seen = new Set()
        const deduped = allEchoes.filter(ce => {
          const key = `${Math.round(ce.lat * 10)}_${Math.round(ce.lon * 10)}`
          if (seen.has(key)) return false
          seen.add(key)
          return true
        })
        setContextEchoes(deduped)

        // Contornos — son globales (misma imagen), tomar el primer resultado no vacío
        setEchoContours(results.find(r => r.echo_contours?.length)?.echo_contours ?? [])
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
  const radarImageUrl = radarBounds ? `${API_BASE}/radar/image` : null

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
          <div style={st.toggleBar} role="group" aria-label="Capas del mapa">
            {LAYERS.map(({ key, label, title }) => (
              <button
                key={key}
                style={layers[key] ? st.toggleOn : st.toggleOff}
                onClick={() => toggleLayer(key)}
                aria-pressed={layers[key]}
                title={title}
              >
                {label}
              </button>
            ))}
          </div>

          <div style={st.mapWrapper}>
            <CellMap
              points={points}
              nowcasts={nowcasts}
              rainviewerUrl={rainviewerUrl}
              height="calc(100vh - 260px)"
              contextEchoes={contextEchoes}
              echoContours={echoContours}
              radarImageUrl={radarImageUrl}
              radarBounds={radarBounds}
              showRadar={layers.showRadar}
              showContours={layers.showContours}
              showArrows={layers.showArrows}
              showPoints={layers.showPoints}
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
              <span style={{ display: "inline-block", width: "20px", height: "3px", background: theme.orange, verticalAlign: "middle", marginRight: "2px" }} />
              Contorno del eco causante
            </span>
            <span style={st.legendItem}>
              <svg width="14" height="14" viewBox="0 0 14 14" style={{ flexShrink: 0 }}>
                <polygon points="7,1 12,12 7,9 2,12" fill={theme.orange} stroke="#fff" strokeWidth="0.8"/>
              </svg>
              Dirección del campo (radar)
            </span>
            <span style={st.legendItem}>
              <svg width="14" height="14" viewBox="0 0 14 14" style={{ flexShrink: 0 }}>
                <polygon points="7,1 12,12 7,9 2,12" fill="none" stroke={theme.primary} strokeWidth="1.5"/>
              </svg>
              Viento 700 hPa
            </span>
            <span style={st.legendItem}>
              <span style={{ display: "inline-block", width: "20px", height: "3px", background: theme.text, verticalAlign: "middle", marginRight: "2px", opacity: 0.75 }} />
              Contorno del eco
            </span>
            <span style={st.legendItem}>
              <span style={{ display: "inline-block", width: "20px", height: "3px", background: theme.green, verticalAlign: "middle", marginRight: "2px" }} />
              Trayectoria consistente
            </span>
            <span style={st.legendItem}>
              <span style={{ display: "inline-block", width: "20px", height: "3px", background: theme.orange, verticalAlign: "middle", marginRight: "2px" }} />
              Trayectoria incierta
            </span>
          </div>
        </>
      )}
    </div>
  )
}

const toggleBase = {
  padding: "5px 14px",
  borderRadius: "999px",
  fontSize: "12px",
  fontWeight: 600,
  cursor: "pointer",
  border: "1.5px solid",
  lineHeight: 1.4,
  minHeight: "30px",
  transition: "background 0.15s, color 0.15s",
}

const st = {
  container:  { padding: "24px", maxWidth: "1280px", margin: "0 auto", width: "100%" },
  header:     { display: "flex", alignItems: "center", gap: "16px", marginBottom: "12px", flexWrap: "wrap" },
  title:      { fontSize: "18px", fontWeight: 700, color: theme.text, margin: 0 },
  alert: {
    padding: "4px 14px", borderRadius: "999px",
    background: theme.accentLight, border: `1px solid ${theme.accent}55`,
    color: "#92400E", fontSize: "12px", fontWeight: 600,
  },
  toggleBar:  { display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "10px" },
  toggleOn: {
    ...toggleBase,
    background: theme.primary,
    borderColor: theme.primary,
    color: "#FFFFFF",
  },
  toggleOff: {
    ...toggleBase,
    background: theme.surface,
    borderColor: theme.border,
    color: theme.textMuted,
  },
  mapWrapper: { borderRadius: "14px", overflow: "hidden", border: `1px solid ${theme.border}`, boxShadow: theme.shadow },
  status:     { color: theme.textFaint, textAlign: "center", padding: "40px 0" },
  error:      { color: theme.red,       textAlign: "center", padding: "40px 0" },
  legend:     { display: "flex", gap: "20px", flexWrap: "wrap", marginTop: "12px" },
  legendItem: { display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: theme.textMuted },
  dot:        { display: "inline-block", width: "10px", height: "10px", borderRadius: "50%" },
}
