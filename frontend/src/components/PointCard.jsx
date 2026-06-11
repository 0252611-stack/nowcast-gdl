/** Tarjeta de un punto: estado ahora (radar dBZ, nowcast) + pronóstico próximas horas.
 *  Props: { point, forecast, radar, nowcast, loading? }
 */

import RadarStatus from "./RadarStatus.jsx"
import WindCompass from "./WindCompass.jsx"
import HourlyChart from "./HourlyChart.jsx"

const s = {
  card: {
    background: "#1e293b",
    borderRadius: "14px",
    border: "1px solid #273549",
    boxShadow: "0 4px 24px rgba(0,0,0,0.35)",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
  },
  header: {
    padding: "16px 18px 12px",
    borderBottom: "1px solid #273549",
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: "8px",
  },
  headerLeft: {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
  },
  pointName: {
    fontSize: "17px",
    fontWeight: 700,
    color: "#e2e8f0",
    letterSpacing: "-0.01em",
  },
  coords: {
    fontSize: "11px",
    color: "#475569",
  },
  body: {
    padding: "14px 18px",
    display: "flex",
    flexDirection: "column",
    gap: "14px",
    flex: 1,
  },
  row: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "8px",
  },
  section: {
    display: "flex",
    flexDirection: "column",
    gap: "6px",
  },
  label: {
    fontSize: "11px",
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    fontWeight: 600,
  },
  rainNowBadge: (raining) => ({
    display: "inline-flex",
    alignItems: "center",
    gap: "5px",
    padding: "4px 10px",
    borderRadius: "999px",
    background: raining ? "#052e16" : "#1e293b",
    border: `1px solid ${raining ? "#22c55e66" : "#334155"}`,
    color: raining ? "#22c55e" : "#64748b",
    fontSize: "12px",
    fontWeight: 600,
  }),
  etaBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "5px",
    padding: "4px 10px",
    borderRadius: "999px",
    background: "#431407",
    border: "1px solid #f9731655",
    color: "#f97316",
    fontSize: "12px",
    fontWeight: 600,
  },
  tempRow: {
    display: "flex",
    gap: "16px",
    alignItems: "center",
  },
  tempValue: {
    fontSize: "28px",
    fontWeight: 700,
    color: "#e2e8f0",
    lineHeight: 1,
  },
  tempUnit: {
    fontSize: "14px",
    color: "#94a3b8",
  },
  probBadge: {
    display: "inline-flex",
    alignItems: "center",
    padding: "3px 10px",
    borderRadius: "999px",
    background: "#0c2a4a",
    border: "1px solid #38bdf855",
    color: "#38bdf8",
    fontSize: "13px",
    fontWeight: 600,
  },
  windRow: {
    display: "flex",
    gap: "20px",
    alignItems: "flex-start",
  },
  divider: {
    height: "1px",
    background: "#273549",
    margin: "2px 0",
  },
  chartSection: {
    padding: "0 18px 16px",
  },
  chartLabel: {
    fontSize: "11px",
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    fontWeight: 600,
    marginBottom: "8px",
  },
  confidenceBar: (confidence) => ({
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "11px",
    color: "#64748b",
  }),
  confidenceTrack: {
    flex: 1,
    height: "4px",
    background: "#273549",
    borderRadius: "999px",
    overflow: "hidden",
  },
  confidenceFill: (confidence) => ({
    height: "100%",
    width: `${Math.round((confidence ?? 0) * 100)}%`,
    background: confidence > 0.8 ? "#22c55e" : confidence > 0.5 ? "#f97316" : "#64748b",
    borderRadius: "999px",
    transition: "width 0.4s",
  }),
}

export default function PointCard({ point, forecast, radar, nowcast, loading = false }) {
  if (loading) {
    return (
      <div style={{ ...s.card, padding: "18px" }}>
        <div style={{
          height: "16px", width: "60%", borderRadius: "6px", marginBottom: "12px",
          background: "linear-gradient(90deg,#1e293b 25%,#273549 50%,#1e293b 75%)",
          backgroundSize: "200% 100%",
          animation: "skeleton-shimmer 1.4s infinite",
        }} />
        <div style={{
          height: "120px", borderRadius: "8px",
          background: "linear-gradient(90deg,#1e293b 25%,#273549 50%,#1e293b 75%)",
          backgroundSize: "200% 100%",
          animation: "skeleton-shimmer 1.4s infinite",
        }} />
      </div>
    )
  }
  // Entrada horaria más cercana a ahora (última cuyo time <= now)
  const nearest = (() => {
    const hourly = forecast?.hourly
    if (!hourly?.length) return null
    const now = Date.now()
    let best = hourly[0]
    for (const h of hourly) {
      if (new Date(h.time).getTime() <= now) best = h
    }
    return best
  })()
  const radarAvailable = radar !== null && radar !== undefined

  return (
    <article style={s.card}>
      {/* ---- Header ---- */}
      <div style={s.header}>
        <div style={s.headerLeft}>
          <span style={s.pointName}>{point.name}</span>
          <span style={s.coords}>{point.lat.toFixed(4)}° N, {Math.abs(point.lon).toFixed(4)}° O</span>
        </div>
        <RadarStatus reading={radar} available={radarAvailable} />
      </div>

      {/* ---- Body ---- */}
      <div style={s.body}>

        {/* Estado de lluvia ahora + ETA */}
        <div style={s.row}>
          <div style={s.section}>
            <span style={s.label}>Ahora</span>
            <span style={s.rainNowBadge(nowcast?.raining_now)}>
              {nowcast?.raining_now
                ? <span>🌧 Lloviendo</span>
                : <span>☀️ Sin lluvia</span>
              }
            </span>
          </div>

          {nowcast && !nowcast.raining_now && nowcast.eta_minutes !== null && (
            <div style={s.section}>
              <span style={s.label}>Lluvia en</span>
              <span style={s.etaBadge}>
                ⏱ {nowcast.eta_minutes} min
              </span>
            </div>
          )}
        </div>

        {/* Confianza del nowcast */}
        {nowcast?.confidence !== null && nowcast?.confidence !== undefined && (
          <div style={s.confidenceBar(nowcast.confidence)}>
            <span>Confianza</span>
            <div style={s.confidenceTrack}>
              <div style={s.confidenceFill(nowcast.confidence)} />
            </div>
            <span>{Math.round(nowcast.confidence * 100)}%</span>
          </div>
        )}

        <div style={s.divider} />

        {/* Temperatura + Probabilidad de lluvia */}
        {nearest && (
          <div style={s.row}>
            <div style={s.section}>
              <span style={s.label}>Temperatura</span>
              <div style={s.tempRow}>
                <span style={s.tempValue}>
                  {nearest.temperature_c.toFixed(1)}
                  <span style={s.tempUnit}> °C</span>
                </span>
              </div>
            </div>
            <div style={s.section}>
              <span style={s.label}>Prob. lluvia</span>
              <span style={s.probBadge}>
                💧 {nearest.precipitation_probability}%
              </span>
            </div>
          </div>
        )}

        {/* Viento 10m + 700 hPa */}
        {nearest && (
          <div style={s.section}>
            <span style={s.label}>Viento</span>
            <div style={s.windRow}>
              <WindCompass
                speedKmh={nearest.wind_speed_10m_kmh}
                directionDeg={nearest.wind_direction_10m_deg}
                label="10 m"
              />
              <WindCompass
                speedKmh={nearest.wind_speed_700hPa_kmh}
                directionDeg={nearest.wind_direction_700hPa_deg}
                label="700 hPa"
              />
            </div>
          </div>
        )}
      </div>

      {/* ---- Gráfico horario ---- */}
      {forecast?.hourly && (
        <div style={s.chartSection}>
          <p style={s.chartLabel}>Próximas 12 horas</p>
          <HourlyChart hourly={forecast.hourly} />
        </div>
      )}
    </article>
  )
}
