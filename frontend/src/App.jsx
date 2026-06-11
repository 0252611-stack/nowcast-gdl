/**
 * Raíz de la app — grilla de PointCards para cada punto monitoreado.
 * Sprint 2: intenta conectarse al backend real; fallback a datos mock si no está disponible.
 */

import { useState, useEffect, useCallback } from "react"
import PointCard from "./components/PointCard.jsx"
import { getPoints, getForecast, getRadar, getMetrics } from "./api.js"
import {
  MOCK_POINTS,
  MOCK_FORECASTS,
  MOCK_RADAR,
  MOCK_NOWCAST,
} from "./mockData.js"

const REFRESH_INTERVAL_MS = 90_000

function fmtDatetime(isoStr) {
  if (!isoStr) return "—"
  return new Date(isoStr).toLocaleString("es-MX", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "America/Mexico_City",
  })
}

export default function App() {
  const [selectedPoint, setSelectedPoint] = useState(null)
  const [loading, setLoading] = useState(true)
  const [useMock, setUseMock] = useState(false)
  const [points, setPoints] = useState([])
  const [forecasts, setForecasts] = useState({})
  const [radars, setRadars] = useState({})
  const [nowcasts, setNowcasts] = useState({})
  const [generatedAt, setGeneratedAt] = useState(null)
  const [skillMetrics, setSkillMetrics] = useState(null)

  const loadRealData = useCallback(async () => {
    const pts = await getPoints()
    const results = await Promise.all(
      pts.map(async (pt) => {
        const [forecast, radarResp] = await Promise.all([
          getForecast(pt.id),
          getRadar(pt.id),
        ])
        return { pt, forecast, radarResp }
      })
    )
    const newForecasts = {}
    const newRadars = {}
    const newNowcasts = {}
    for (const { pt, forecast, radarResp } of results) {
      newForecasts[pt.id] = forecast
      newRadars[pt.id] = radarResp.radar
      newNowcasts[pt.id] = radarResp.nowcast ?? null
    }
    setPoints(pts)
    setForecasts(newForecasts)
    setRadars(newRadars)
    setNowcasts(newNowcasts)
    setGeneratedAt(new Date().toISOString())
    setUseMock(false)
    try {
      const m = await getMetrics()
      setSkillMetrics(m)
    } catch {
      // métricas opcionales — no afectan el resto de la app
    }
  }, [])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      await loadRealData()
    } catch {
      setPoints(MOCK_POINTS)
      setForecasts(MOCK_FORECASTS)
      setRadars(Object.fromEntries(
        Object.entries(MOCK_RADAR).map(([k, v]) => [k, v])
      ))
      setNowcasts(MOCK_NOWCAST)
      setGeneratedAt("2026-06-10T20:05:00Z")
      setUseMock(true)
    } finally {
      setLoading(false)
    }
  }, [loadRealData])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Auto-refresh cada 90 s cuando está en modo real
  useEffect(() => {
    if (useMock) return
    const id = setInterval(() => {
      loadRealData().catch(() => {})
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [useMock, loadRealData])

  const displayPoints = points.length > 0 ? points : []

  return (
    <div style={st.root}>
      {/* Header */}
      <header style={st.header}>
        <div style={st.headerInner}>
          <div style={st.titleGroup}>
            <h1 style={st.title}>
              <span>🌩</span> Nowcast GDL
            </h1>
            <p style={st.subtitle}>Pronóstico por puntos — Área Metropolitana de Guadalajara</p>
          </div>
          <div style={st.metaGroup}>
            {useMock ? (
              <div style={st.badgeMock}>
                <span style={st.dot} /> Modo offline — datos mock
              </div>
            ) : (
              <div style={st.badgeLive}>
                <span style={{ ...st.dot, background: "#22c55e" }} /> En línea — datos reales
              </div>
            )}
            <div style={st.timestamp}>Actualizado: {fmtDatetime(generatedAt)}</div>
          </div>
        </div>
      </header>

      {/* Nav filtro */}
      <nav style={st.nav}>
        <div style={st.navInner}>
          {displayPoints.map((p) => {
            const nowcast = nowcasts[p.id]
            const isRaining = nowcast?.raining_now
            return (
              <button
                key={p.id}
                style={st.chip(selectedPoint === p.id, isRaining)}
                onClick={() => setSelectedPoint(selectedPoint === p.id ? null : p.id)}
              >
                {isRaining ? "🌧 " : "☀️ "}{p.name}
              </button>
            )
          })}
          {selectedPoint && (
            <button style={st.clearBtn} onClick={() => setSelectedPoint(null)}>
              Mostrar todos
            </button>
          )}
          {useMock && (
            <button style={st.retryBtn} onClick={loadData}>
              ↺ Reintentar
            </button>
          )}
        </div>
      </nav>

      {/* Grid */}
      <main style={st.main}>
        {loading && displayPoints.length === 0 ? (
          <div style={st.spinner}>Cargando datos…</div>
        ) : (
          <div style={st.grid}>
            {displayPoints
              .filter((p) => selectedPoint === null || p.id === selectedPoint)
              .map((point) => (
                <PointCard
                  key={point.id}
                  point={point}
                  forecast={forecasts[point.id]}
                  radar={radars[point.id]}
                  nowcast={nowcasts[point.id]}
                  loading={loading && !forecasts[point.id]}
                />
              ))}
          </div>
        )}
      </main>

      <footer style={st.footer}>
        <p style={st.footerText}>
          Nowcast GDL · Radar IAM-UdeG + Open-Meteo
        </p>
        {!useMock && (
          <SkillBar metrics={skillMetrics} />
        )}
      </footer>
    </div>
  )
}

function SkillBar({ metrics }) {
  const fo = metrics?.forecast_only
  const v  = metrics?.verified ?? 0

  if (!metrics || v === 0) {
    return (
      <p style={{ fontSize: "11px", color: "#334155", marginTop: "4px" }}>
        Skill: acumulando datos… ({metrics?.pending ?? 0} predicciones pendientes)
      </p>
    )
  }

  const fmt = (x) => x != null ? (x * 100).toFixed(0) + "%" : "—"
  const fmtN = (x) => x != null ? x.toFixed(1) : "—"

  return (
    <p style={{ fontSize: "11px", color: "#475569", marginTop: "4px" }}>
      <span style={{ color: "#64748b", fontWeight: 600 }}>Skill</span>
      {" · "}Acc {fmt(fo?.accuracy)}
      {" · "}POD {fmt(fo?.pod)}
      {" · "}FAR {fmt(fo?.far)}
      {" · "}CSI {fmt(fo?.csi)}
      {" · "}n={v}
      {fo?.mean_lead_error_min != null && (
        <> · err {fmtN(fo.mean_lead_error_min)} min</>
      )}
    </p>
  )
}

const st = {
  root: { minHeight: "100vh", display: "flex", flexDirection: "column", background: "#0f172a" },
  header: { background: "#0f172a", borderBottom: "1px solid #1e293b", position: "sticky", top: 0, zIndex: 10 },
  headerInner: { maxWidth: "1280px", margin: "0 auto", padding: "16px 24px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "16px", flexWrap: "wrap" },
  titleGroup: { display: "flex", flexDirection: "column", gap: "2px" },
  title: { fontSize: "22px", fontWeight: 800, color: "#e2e8f0", letterSpacing: "-0.02em", display: "flex", alignItems: "center", gap: "8px" },
  subtitle: { fontSize: "13px", color: "#475569" },
  metaGroup: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" },
  badgeMock: { display: "inline-flex", alignItems: "center", gap: "6px", padding: "4px 12px", borderRadius: "999px", background: "#422006", border: "1px solid #f9731655", color: "#f97316", fontSize: "12px", fontWeight: 600 },
  badgeLive: { display: "inline-flex", alignItems: "center", gap: "6px", padding: "4px 12px", borderRadius: "999px", background: "#052e16", border: "1px solid #22c55e55", color: "#22c55e", fontSize: "12px", fontWeight: 600 },
  dot: { display: "inline-block", width: "6px", height: "6px", borderRadius: "50%", background: "#f97316", animation: "pulse 2s infinite" },
  timestamp: { fontSize: "11px", color: "#475569" },
  nav: { background: "#0f172a", borderBottom: "1px solid #1e293b" },
  navInner: { maxWidth: "1280px", margin: "0 auto", padding: "10px 24px", display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center" },
  chip: (active, raining) => ({ padding: "5px 14px", borderRadius: "999px", border: `1px solid ${active ? "#38bdf8" : raining ? "#22c55e55" : "#334155"}`, background: active ? "#0c2a4a" : raining ? "#052e16" : "#1e293b", color: active ? "#38bdf8" : raining ? "#22c55e" : "#94a3b8", fontSize: "13px", fontWeight: 600, cursor: "pointer" }),
  clearBtn: { padding: "5px 14px", borderRadius: "999px", border: "1px solid #334155", background: "transparent", color: "#64748b", fontSize: "12px", cursor: "pointer" },
  retryBtn: { padding: "5px 14px", borderRadius: "999px", border: "1px solid #38bdf8", background: "#0c2a4a", color: "#38bdf8", fontSize: "12px", fontWeight: 600, cursor: "pointer" },
  main: { flex: 1, maxWidth: "1280px", width: "100%", margin: "0 auto", padding: "24px" },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: "20px" },
  spinner: { textAlign: "center", color: "#475569", padding: "80px 0", fontSize: "15px" },
  footer: { borderTop: "1px solid #1e293b", padding: "16px 24px", textAlign: "center" },
  footerText: { fontSize: "12px", color: "#334155" },
}
