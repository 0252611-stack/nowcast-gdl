/**
 * Vista /malla — Diagnóstico de la malla de vectores del campo de movimiento.
 *
 * Muestra los puntos exactos que el backend muestreó del campo denso (optical flow)
 * dentro de cada contorno de eco. Coloreados por velocidad:
 *   Verde  ≥30 km/h — señal fuerte, confiable
 *   Ámbar  ≥10 km/h — moderada
 *   Gris   < 10 km/h — débil (puede ser ruido del optical flow)
 *
 * Los pasos de predicción NO tienen vectores locales — usan la dirección del
 * eco de contexto más cercano (campo en t=0, asumido estacionario).
 */

import { useState, useEffect } from "react"
import CellMap from "../components/CellMap.jsx"
import { getPoints, getRadar, getCellDebug } from "../api.js"
import { theme } from "../theme.js"
import { API_BASE } from "../config.js"

export default function FieldGridView() {
  const [points, setPoints] = useState([])
  const [nowcasts, setNowcasts] = useState({})
  const [echoContours, setEchoContours] = useState([])
  const [contextEchoes, setContextEchoes] = useState([])
  const [trackedCells, setTrackedCells] = useState([])
  const [rawDetections, setRawDetections] = useState([])
  const [cellDiag, setCellDiag] = useState(null)
  const [skillMetrics, setSkillMetrics] = useState(null)
  const [radarBounds, setRadarBounds] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [showRadar, setShowRadar] = useState(true)
  const [showPoints, setShowPoints] = useState(true)
  const [showCells, setShowCells] = useState(false)
  const [showRawDetections, setShowRawDetections] = useState(false)

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
        let bounds = null
        pts.forEach((pt, i) => {
          nw[pt.id] = results[i].nowcast ?? null
          if (!bounds && results[i].radar_bounds) bounds = results[i].radar_bounds
        })
        setNowcasts(nw)
        setRadarBounds(bounds)

        const allEchoes = results.flatMap(r => r.context_echoes ?? [])
        const seen = new Set()
        setContextEchoes(allEchoes.filter(ce => {
          const key = `${Math.round(ce.lat * 10)}_${Math.round(ce.lon * 10)}`
          if (seen.has(key)) return false
          seen.add(key)
          return true
        }))

        setEchoContours(results.find(r => r.echo_contours?.length)?.echo_contours ?? [])
        setTrackedCells(results.find(r => r.tracked_cells?.length)?.tracked_cells ?? [])

        // Diagnóstico de celdas y skill — degradación silenciosa si falla
        try {
          const debug = await getCellDebug()
          if (!cancelled) {
            setRawDetections(debug.detections ?? [])
            setCellDiag(debug.diagnostics ?? null)
          }
        } catch (_) { /* sin datos de debug — no es error crítico */ }

        try {
          const metricsUrl = `${API_BASE}/metrics`
          const mRes = await fetch(metricsUrl)
          if (mRes.ok) {
            const m = await mRes.json()
            if (!cancelled) setSkillMetrics(m)
          }
        } catch (_) { /* sin skill — no bloquea */ }
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  const radarImageUrl = radarBounds ? `${API_BASE}/radar/image` : null

  // Estadísticas de la malla
  const stats = (() => {
    const withVecs = echoContours.filter(c => !Array.isArray(c) && c.vectors?.length > 0)
    const totalVecs = withVecs.reduce((s, c) => s + c.vectors.length, 0)
    if (!withVecs.length) return null
    const allSpeeds = withVecs.flatMap(c => c.vectors.map(v => v.speed_kmh))
    return {
      totalContours: echoContours.length,
      contoursWithVecs: withVecs.length,
      totalVectors: totalVecs,
      avgVectors: (totalVecs / withVecs.length).toFixed(1),
      fast: allSpeeds.filter(s => s >= 30).length,
      medium: allSpeeds.filter(s => s >= 10 && s < 30).length,
      slow: allSpeeds.filter(s => s < 10).length,
      maxSpeed: Math.max(...allSpeeds).toFixed(0),
      meanSpeed: (allSpeeds.reduce((a, b) => a + b, 0) / allSpeeds.length).toFixed(0),
    }
  })()

  return (
    <div style={st.container}>
      <div style={st.header}>
        <div>
          <h2 style={st.title}>Malla de vectores — campo de movimiento</h2>
          <p style={st.subtitle}>
            Puntos muestreados del optical flow denso dentro de cada contorno de eco.
            Cada punto muestra la dirección y velocidad local del campo en esa celda.
          </p>
        </div>
      </div>

      {loading && <p style={st.status}>Cargando datos…</p>}
      {error && <p style={st.error}>Error: {error}</p>}

      {!loading && !error && (
        <>
          {/* Toggles */}
          <div style={st.toggleBar} role="group" aria-label="Capas del mapa">
            {[
              { key: "radar", label: "Radar", val: showRadar, set: setShowRadar },
              { key: "cells", label: "Celdas", val: showCells, set: setShowCells,
                badge: trackedCells.length || null,
                title: "Mostrar/ocultar las celdas de tormenta rastreadas (TITAN) — coloreadas por calidad" },
              { key: "raw", label: "Debug celdas", val: showRawDetections, set: setShowRawDetections,
                badge: rawDetections.length || null,
                title: "Mostrar/ocultar las detecciones crudas pre-tracking (para calibración)" },
              { key: "points", label: "Puntos", val: showPoints, set: setShowPoints },
            ].map(({ key, label, val, set, title, badge }) => (
              <button
                key={key}
                style={val ? st.toggleOn : st.toggleOff}
                onClick={() => set(v => !v)}
                aria-pressed={val}
                title={title}
              >
                {label}
                {badge != null && (
                  <span style={{
                    marginLeft: "6px",
                    background: val ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.12)",
                    borderRadius: "10px",
                    padding: "1px 6px",
                    fontSize: "11px",
                    fontWeight: 700,
                    lineHeight: 1.4,
                  }}>{badge}</span>
                )}
              </button>
            ))}
          </div>

          {/* Aviso cuando la capa está activa pero sin datos (cielo despejado) */}
          {showCells && trackedCells.length === 0 && (
            <p style={{ fontSize: "12px", color: theme.textMuted, margin: "4px 0 0", paddingLeft: "2px" }}>
              Sin celdas rastreadas — no hay tormentas activas en el área o el radar no ha actualizado aún.
            </p>
          )}
          {showRawDetections && rawDetections.length === 0 && (
            <p style={{ fontSize: "12px", color: theme.textMuted, margin: "4px 0 0", paddingLeft: "2px" }}>
              Sin detecciones crudas — sin lluvia activa o sin frame de radar disponible.
            </p>
          )}

          {/* Mapa */}
          <div style={st.mapWrapper}>
            <CellMap
              points={points}
              nowcasts={nowcasts}
              height="calc(100vh - 340px)"
              contextEchoes={contextEchoes}
              echoContours={echoContours}
              radarImageUrl={radarImageUrl}
              radarBounds={radarBounds}
              showRadar={showRadar}
              showContours
              showArrows={false}
              showPoints={showPoints}
              showMesh
              showCells={showCells}
              trackedCells={trackedCells}
              rawDetections={rawDetections}
              showRawDetections={showRawDetections}
            />
          </div>

          {/* Leyenda de calidad de celdas (solo visible cuando celdas están activas) */}
          {showCells && (
            <div style={{ ...st.legend, marginBottom: "4px" }}>
              <span style={st.legendTitle}>Calidad de celda:</span>
              {[
                { color: "#16A34A", label: "≥ 70% — alta" },
                { color: "#D97706", label: "40–69% — media" },
                { color: "#DC2626", label: "< 40% — baja (celda nueva o ruidosa)" },
              ].map(({ color, label }) => (
                <span key={color} style={st.legendItem}>
                  <svg width="12" height="12" viewBox="0 0 12 12" style={{ flexShrink: 0 }}>
                    <rect x="1" y="1" width="10" height="10" fill={color} rx="2" />
                  </svg>
                  {label}
                </span>
              ))}
            </div>
          )}

          {/* Leyenda de velocidad */}
          <div style={st.legend}>
            <span style={st.legendTitle}>Velocidad del campo:</span>
            {[
              { color: "#16A34A", label: "≥ 30 km/h — confiable" },
              { color: "#D97706", label: "10–29 km/h — moderado" },
              { color: "#6B7280", label: "< 10 km/h — débil (posible ruido)" },
            ].map(({ color, label }) => (
              <span key={color} style={st.legendItem}>
                <svg width="14" height="14" viewBox="0 0 14 14" style={{ flexShrink: 0 }}>
                  <circle cx="7" cy="7" r="2" fill={color} />
                  <polygon points="7,1 10,7 7,5.5 4,7" fill={color} />
                </svg>
                {label}
              </span>
            ))}
          </div>

          {/* Panel de skill (calidad del motor de predicciones) */}
          {skillMetrics && skillMetrics.verified > 0 && (() => {
            const o = skillMetrics.overall
            const fmt = v => v != null ? `${(v * 100).toFixed(0)}%` : "—"
            return (
              <div style={{ ...st.statsPanel, marginBottom: "8px" }}>
                <span style={st.statsTitle}>
                  Calidad del motor (skill) — {skillMetrics.verified} predicciones verificadas
                </span>
                <div style={st.statsGrid}>
                  <StatCell label="POD" value={fmt(o.pod)} color={o.pod >= 0.7 ? "#16A34A" : o.pod >= 0.5 ? "#D97706" : "#DC2626"} />
                  <StatCell label="FAR" value={fmt(o.far)} color={o.far <= 0.3 ? "#16A34A" : o.far <= 0.5 ? "#D97706" : "#DC2626"} />
                  <StatCell label="CSI" value={fmt(o.csi)} color={o.csi >= 0.5 ? "#16A34A" : o.csi >= 0.3 ? "#D97706" : "#DC2626"} />
                  <StatCell label="Exactitud" value={fmt(o.accuracy)} />
                  <StatCell label="Pendientes" value={skillMetrics.pending} />
                </div>
                <p style={st.statsNote}>
                  POD = tasa de aciertos, FAR = falsa alarma, CSI = índice de éxito crítico.
                  Se actualiza automáticamente cada hora.
                </p>
              </div>
            )
          })()}

          {/* Panel de diagnóstico de celdas (tracking) */}
          {cellDiag && (
            <div style={{ ...st.statsPanel, marginBottom: "8px" }}>
              <span style={st.statsTitle}>Diagnóstico de celdas — último ciclo</span>
              <div style={st.statsGrid}>
                <StatCell label="Detectadas" value={cellDiag.n_det} />
                <StatCell label="Vivas" value={cellDiag.n_alive} />
                <StatCell label="Nuevas" value={cellDiag.n_new} />
                <StatCell label="Continuadas" value={cellDiag.n_continued} />
                <StatCell label="Purgadas" value={cellDiag.n_purged} color={cellDiag.n_purged > 0 ? "#D97706" : undefined} />
                <StatCell label="Split" value={cellDiag.n_split} />
                <StatCell label="Merge" value={cellDiag.n_merge} />
                <StatCell label="Rechazos gate" value={cellDiag.gate_rejects} />
                <StatCell label="Costo medio match" value={cellDiag.match_cost_mean != null ? cellDiag.match_cost_mean.toFixed(2) : "—"} />
                <StatCell label="CELL_MIN_PX" value={cellDiag.cell_min_px} />
                <StatCell label="DBZ umbral" value={`${cellDiag.dbz_threshold} dBZ`} />
                <StatCell label="Gate max" value={`${cellDiag.match_max_km} km`} />
              </div>
              <p style={st.statsNote}>
                Activa &quot;Celdas&quot; para ver la malla de calidad (verde = alta, rojo = baja).
                Activa &quot;Debug celdas&quot; para ver las detecciones crudas pre-tracking (gris).
              </p>
            </div>
          )}

          {/* Panel de estadísticas */}
          {stats ? (
            <div style={st.statsPanel}>
              <span style={st.statsTitle}>Estadísticas de la malla</span>
              <div style={st.statsGrid}>
                <StatCell label="Contornos" value={stats.totalContours} />
                <StatCell label="Con vectores" value={`${stats.contoursWithVecs} / ${stats.totalContours}`} />
                <StatCell label="Total vectores" value={stats.totalVectors} />
                <StatCell label="Prom. / contorno" value={stats.avgVectors} />
                <StatCell label="Vel. media" value={`${stats.meanSpeed} km/h`} />
                <StatCell label="Vel. máx." value={`${stats.maxSpeed} km/h`} />
                <StatCell label="Rápidos (≥30)" value={stats.fast} color="#16A34A" />
                <StatCell label="Moderados (10-29)" value={stats.medium} color="#D97706" />
                <StatCell label="Débiles (<10)" value={stats.slow} color="#6B7280" />
              </div>
              <p style={st.statsNote}>
                Los contornos sin vectores corresponden a ecos detectados antes de acumular
                2 frames (motor aún calentando), o a contornos con flujo nulo en toda su área.
                Para predicciones (+15 a +120 min) se usa la dirección del eco de contexto
                más cercano en t=0 (campo asumido estacionario).
              </p>
            </div>
          ) : (
            <div style={st.statsPanel}>
              <p style={{ color: theme.textMuted, margin: 0, fontSize: "13px" }}>
                Sin vectores de malla aún — el motor necesita ≥ 2 frames de radar (~3 min de uptime).
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function StatCell({ label, value, color }) {
  return (
    <div style={stc.cell}>
      <span style={stc.label}>{label}</span>
      <span style={{ ...stc.value, color: color ?? theme.text }}>{value}</span>
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
  container:   { padding: "24px", maxWidth: "1280px", margin: "0 auto", width: "100%" },
  header:      { marginBottom: "12px" },
  title:       { fontSize: "18px", fontWeight: 700, color: theme.text, margin: "0 0 4px" },
  subtitle:    { fontSize: "13px", color: theme.textMuted, margin: 0 },
  status:      { color: theme.textFaint, textAlign: "center", padding: "40px 0" },
  error:       { color: theme.red, textAlign: "center", padding: "40px 0" },
  toggleBar:   { display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "10px" },
  toggleOn:    { ...toggleBase, background: theme.primary, borderColor: theme.primary, color: "#FFFFFF" },
  toggleOff:   { ...toggleBase, background: theme.surface, borderColor: theme.border, color: theme.textMuted },
  mapWrapper:  { borderRadius: "14px", overflow: "hidden", border: `1px solid ${theme.border}`, boxShadow: theme.shadow },
  legend:      { display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center", marginTop: "10px" },
  legendTitle: { fontSize: "12px", fontWeight: 600, color: theme.textMuted },
  legendItem:  { display: "flex", alignItems: "center", gap: "5px", fontSize: "12px", color: theme.textMuted },
  statsPanel:  {
    marginTop: "12px",
    padding: "14px 18px",
    borderRadius: "12px",
    background: theme.surface,
    border: `1px solid ${theme.border}`,
    boxShadow: theme.shadow,
  },
  statsTitle:  { fontSize: "12px", fontWeight: 700, color: theme.textMuted, letterSpacing: "0.03em", display: "block", marginBottom: "10px" },
  statsGrid:   { display: "flex", gap: "0", flexWrap: "wrap", marginBottom: "10px" },
  statsNote:   { fontSize: "11px", color: theme.textFaint, margin: "10px 0 0", lineHeight: 1.5 },
}

const stc = {
  cell:  { minWidth: "120px", padding: "6px 16px 6px 0" },
  label: { display: "block", fontSize: "11px", color: theme.textFaint, fontWeight: 600, marginBottom: "2px" },
  value: { display: "block", fontSize: "16px", fontWeight: 700, fontFamily: theme.fontMono, fontVariantNumeric: "tabular-nums" },
}
