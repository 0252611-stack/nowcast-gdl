/**
 * Raíz de la app — layout principal con router.
 * / → dashboard de PointCards
 * /mapa → mapa interactivo Leaflet
 * /admin → panel de administración
 */

import { useState, useEffect, useCallback } from "react"
import { Routes, Route, NavLink } from "react-router-dom"
import PointCard from "./components/PointCard.jsx"
import MapView from "./views/MapView.jsx"
import AdminView from "./views/AdminView.jsx"
import PredictionView from "./views/PredictionView.jsx"
import { getPoints, getForecast, getRadar, getMetrics } from "./api.js"
import { theme } from "./theme.js"
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
  const [rainviewerUrls, setRainviewerUrls] = useState({})

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
    const newRainviewerUrls = {}
    for (const { pt, forecast, radarResp } of results) {
      newForecasts[pt.id] = forecast
      newRadars[pt.id] = radarResp.radar
      newNowcasts[pt.id] = radarResp.nowcast ?? null
      newRainviewerUrls[pt.id] = radarResp.rainviewer_url ?? null
    }
    setPoints(pts)
    setForecasts(newForecasts)
    setRadars(newRadars)
    setNowcasts(newNowcasts)
    setRainviewerUrls(newRainviewerUrls)
    setGeneratedAt(new Date().toISOString())
    setUseMock(false)
    try {
      const m = await getMetrics()
      setSkillMetrics(m)
    } catch {
      // métricas opcionales
    }
  }, [])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      await loadRealData()
    } catch {
      setPoints(MOCK_POINTS)
      setForecasts(MOCK_FORECASTS)
      setRadars(Object.fromEntries(Object.entries(MOCK_RADAR).map(([k, v]) => [k, v])))
      setNowcasts(MOCK_NOWCAST)
      setGeneratedAt("2026-06-10T20:05:00Z")
      setUseMock(true)
    } finally {
      setLoading(false)
    }
  }, [loadRealData])

  useEffect(() => { loadData() }, [loadData])

  useEffect(() => {
    if (useMock) return
    const id = setInterval(() => { loadRealData().catch(() => {}) }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [useMock, loadRealData])

  const displayPoints = points.length > 0 ? points : []

  return (
    <div style={st.root}>
      {/* ── Header ── */}
      <header style={st.header}>
        <div style={st.headerInner}>
          <div style={st.titleGroup}>
            <h1 style={st.title}>
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"
                style={{ color: theme.primary }}>
                <path d="M19 16.9A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 5 16m7-3v6m-3-3 3 3 3-3"
                  stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              Nowcast GDL
            </h1>
            <p style={st.subtitle}>Pronóstico por puntos — Área Metropolitana de Guadalajara</p>
          </div>
          <div style={st.metaGroup}>
            <nav style={st.navLinks} aria-label="Navegación principal">
              <NavLink to="/" end style={({ isActive }) => isActive ? st.navLinkActive : st.navLink}>
                Inicio
              </NavLink>
              <NavLink to="/mapa" style={({ isActive }) => isActive ? st.navLinkActive : st.navLink}>
                Mapa
              </NavLink>
              <NavLink to="/prediccion" style={({ isActive }) => isActive ? st.navLinkActive : st.navLink}>
                Predicción
              </NavLink>
              <NavLink to="/admin" style={({ isActive }) => isActive ? st.navLinkActive : st.navLink}>
                Admin
              </NavLink>
            </nav>
            {useMock ? (
              <div style={st.badgeMock}>
                <span style={st.dot} aria-hidden="true" /> Modo offline — datos mock
              </div>
            ) : (
              <div style={st.badgeLive}>
                <span style={{ ...st.dot, background: theme.green }} aria-hidden="true" /> En línea — datos reales
              </div>
            )}
            <div style={st.timestamp}>Actualizado: {fmtDatetime(generatedAt)}</div>
          </div>
        </div>
      </header>

      {/* ── Rutas ── */}
      <Routes>
        {/* Dashboard home */}
        <Route path="/" element={
          <>
            {/* Filtro de puntos */}
            <nav style={st.chipNav} aria-label="Filtro de puntos">
              <div style={st.chipNavInner}>
                {displayPoints.map((p) => {
                  const nowcast = nowcasts[p.id]
                  const isRaining = nowcast?.raining_now
                  return (
                    <button
                      key={p.id}
                      style={st.chip(selectedPoint === p.id, isRaining)}
                      onClick={() => setSelectedPoint(selectedPoint === p.id ? null : p.id)}
                      aria-pressed={selectedPoint === p.id}
                    >
                      {/* Indicador de estado — punto de color en vez de emoji */}
                      <span style={st.chipDot(isRaining)} aria-hidden="true" />
                      {p.name}
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

            {/* Grid de tarjetas */}
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
                        rainviewerUrl={rainviewerUrls[point.id]}
                        loading={loading && !forecasts[point.id]}
                      />
                    ))}
                </div>
              )}
            </main>

            <footer style={st.footer}>
              <p style={st.footerText}>Nowcast GDL · Radar IAM-UdeG + Open-Meteo</p>
              {!useMock && <SkillBar metrics={skillMetrics} />}
            </footer>
          </>
        } />

        <Route path="/mapa" element={<MapView />} />
        <Route path="/prediccion" element={<PredictionView />} />
        <Route path="/admin" element={<AdminView />} />
      </Routes>
    </div>
  )
}

function SkillBar({ metrics }) {
  const fo = metrics?.forecast_only
  const v  = metrics?.verified ?? 0

  if (!metrics || v === 0) {
    return (
      <p style={{ fontSize: "11px", color: theme.textFaint, marginTop: "4px" }}>
        Skill: acumulando datos… ({metrics?.pending ?? 0} predicciones pendientes)
      </p>
    )
  }

  const fmt  = (x) => x != null ? (x * 100).toFixed(0) + "%" : "—"
  const fmtN = (x) => x != null ? x.toFixed(1) : "—"

  return (
    <p style={{ fontSize: "11px", color: theme.textMuted, marginTop: "4px" }}>
      <span style={{ color: theme.textFaint, fontWeight: 600 }}>Skill</span>
      {" · "}Acc {fmt(fo?.accuracy)}
      {" · "}POD {fmt(fo?.pod)}
      {" · "}FAR {fmt(fo?.far)}
      {" · "}CSI {fmt(fo?.csi)}
      {" · "}n={v}
      {fo?.mean_lead_error_min != null && <> · err {fmtN(fo.mean_lead_error_min)} min</>}
    </p>
  )
}

const st = {
  root: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    background: theme.bg,
  },
  header: {
    background: theme.surface,
    borderBottom: `1px solid ${theme.border}`,
    boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
    position: "sticky",
    top: 0,
    zIndex: 10,
  },
  headerInner: {
    maxWidth: "1280px",
    margin: "0 auto",
    padding: "14px 24px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "16px",
    flexWrap: "wrap",
  },
  titleGroup: { display: "flex", flexDirection: "column", gap: "2px" },
  title: {
    fontSize: "20px",
    fontWeight: 700,
    color: theme.text,
    letterSpacing: "-0.02em",
    display: "flex",
    alignItems: "center",
    gap: "8px",
  },
  subtitle: { fontSize: "13px", color: theme.textFaint },
  metaGroup: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" },
  navLinks: { display: "flex", gap: "4px" },
  navLink: {
    padding: "5px 14px",
    borderRadius: "8px",
    fontSize: "13px",
    fontWeight: 600,
    color: theme.textMuted,
    textDecoration: "none",
    border: "1px solid transparent",
    transition: "color 0.15s, background 0.15s",
  },
  navLinkActive: {
    padding: "5px 14px",
    borderRadius: "8px",
    fontSize: "13px",
    fontWeight: 600,
    color: theme.primary,
    textDecoration: "none",
    border: `1px solid ${theme.border}`,
    background: theme.primaryLight,
  },
  badgeMock: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "4px 12px",
    borderRadius: "999px",
    background: "#FEF3C7",
    border: "1px solid #FDE68A",
    color: "#92400E",
    fontSize: "12px",
    fontWeight: 600,
  },
  badgeLive: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "4px 12px",
    borderRadius: "999px",
    background: theme.greenLight,
    border: `1px solid ${theme.green}44`,
    color: "#166534",
    fontSize: "12px",
    fontWeight: 600,
  },
  dot: {
    display: "inline-block",
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: theme.accent,
    animation: "pulse 2s infinite",
  },
  timestamp: { fontSize: "11px", color: theme.textFaint },
  chipNav: {
    background: theme.surface,
    borderBottom: `1px solid ${theme.border}`,
  },
  chipNavInner: {
    maxWidth: "1280px",
    margin: "0 auto",
    padding: "10px 24px",
    display: "flex",
    flexWrap: "wrap",
    gap: "8px",
    alignItems: "center",
  },
  chip: (active, raining) => ({
    display: "inline-flex",
    alignItems: "center",
    gap: "7px",
    padding: "5px 14px",
    borderRadius: "999px",
    border: `1px solid ${active ? theme.primary : raining ? theme.green + "55" : theme.borderMid}`,
    background: active ? theme.primaryLight : raining ? theme.greenLight : theme.surfaceMuted,
    color: active ? theme.primary : raining ? "#166534" : theme.textMuted,
    fontSize: "13px",
    fontWeight: 600,
    cursor: "pointer",
    transition: "background 0.15s, border-color 0.15s",
  }),
  chipDot: (raining) => ({
    display: "inline-block",
    width: "7px",
    height: "7px",
    borderRadius: "50%",
    background: raining ? theme.green : theme.borderMid,
    flexShrink: 0,
  }),
  clearBtn: {
    padding: "5px 14px",
    borderRadius: "999px",
    border: `1px solid ${theme.borderMid}`,
    background: "transparent",
    color: theme.textFaint,
    fontSize: "12px",
    cursor: "pointer",
  },
  retryBtn: {
    padding: "5px 14px",
    borderRadius: "999px",
    border: `1px solid ${theme.primary}`,
    background: theme.primaryLight,
    color: theme.primary,
    fontSize: "12px",
    fontWeight: 600,
    cursor: "pointer",
  },
  main: {
    flex: 1,
    maxWidth: "1280px",
    width: "100%",
    margin: "0 auto",
    padding: "24px",
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
    gap: "20px",
  },
  spinner: {
    textAlign: "center",
    color: theme.textFaint,
    padding: "80px 0",
    fontSize: "15px",
  },
  footer: {
    borderTop: `1px solid ${theme.border}`,
    padding: "16px 24px",
    textAlign: "center",
    background: theme.surface,
  },
  footerText: { fontSize: "12px", color: theme.textFaint },
}
