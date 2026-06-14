/** Tarjeta de un punto: estado ahora (radar dBZ, nowcast) + pronóstico próximas horas.
 *  Props: { point, forecast, radar, nowcast, rainviewerUrl, loading? }
 */

import RadarStatus from "./RadarStatus.jsx"
import WindCompass from "./WindCompass.jsx"
import HourlyChart from "./HourlyChart.jsx"
import CellMap from "./CellMap.jsx"
import SourceTag from "./SourceTag.jsx"
import { SunIcon, CloudRainIcon, ClockIcon, DropletIcon, CloudIcon } from "./Icons.jsx"
import { theme } from "../theme.js"

/** Borde superior de color según estado de lluvia — comunica estado, no decoración */
function cardAccent(nowcast) {
  if (nowcast?.raining_now) return theme.green
  if (nowcast?.eta_minutes != null) return theme.accent
  return "transparent"
}

const s = {
  card: {
    background: theme.surface,
    borderRadius: "16px",
    border: `1px solid ${theme.border}`,
    boxShadow: theme.shadow,
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
  },
  header: {
    padding: "16px 18px 12px",
    borderBottom: `1px solid ${theme.border}`,
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: "8px",
  },
  headerLeft: {
    display: "flex",
    flexDirection: "column",
    gap: "3px",
  },
  pointName: {
    fontSize: "17px",
    fontWeight: 700,
    color: theme.text,
    letterSpacing: "-0.01em",
  },
  coords: {
    fontSize: "11px",
    color: theme.textFaint,
    fontFamily: theme.fontMono,
    fontVariantNumeric: "tabular-nums",
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
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: "8px",
  },
  section: {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
  },
  // Sin uppercase — jerarquía por peso y color, no por mayúsculas
  label: {
    fontSize: "11px",
    color: theme.textFaint,
    letterSpacing: "0.02em",
    fontWeight: 600,
  },
  rainNowBadge: (raining) => ({
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "5px 12px",
    borderRadius: "8px",
    background: raining ? theme.greenLight : theme.surfaceMuted,
    border: `1px solid ${raining ? theme.green + "55" : theme.borderMid}`,
    color: raining ? "#166534" : theme.textMuted,
    fontSize: "13px",
    fontWeight: 600,
  }),
  weakEchoBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "5px 12px",
    borderRadius: "8px",
    background: theme.yellowLight,
    border: `1px solid ${theme.yellow}55`,
    color: "#854D0E",
    fontSize: "13px",
    fontWeight: 600,
  },
  etaBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "5px 12px",
    borderRadius: "8px",
    background: theme.accentLight,
    border: `1px solid ${theme.accent}55`,
    color: "#92400E",
    fontSize: "13px",
    fontWeight: 600,
  },
  tempRow: {
    display: "flex",
    gap: "12px",
    alignItems: "baseline",
  },
  tempValue: {
    fontSize: "28px",
    fontWeight: 700,
    color: theme.text,
    lineHeight: 1,
    fontFamily: theme.fontMono,
    fontVariantNumeric: "tabular-nums",
  },
  tempUnit: {
    fontSize: "14px",
    color: theme.textMuted,
  },
  probBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "5px 12px",
    borderRadius: "8px",
    background: theme.primaryLight,
    border: `1px solid ${theme.primary}33`,
    color: theme.primary,
    fontSize: "13px",
    fontWeight: 600,
    fontFamily: theme.fontMono,
    fontVariantNumeric: "tabular-nums",
  },
  windRow: {
    display: "flex",
    gap: "20px",
    alignItems: "flex-start",
  },
  divider: {
    height: "1px",
    background: theme.border,
    margin: "2px 0",
  },
  chartSection: {
    padding: "0 18px 16px",
  },
  chartLabel: {
    fontSize: "11px",
    color: theme.textFaint,
    letterSpacing: "0.02em",
    fontWeight: 600,
    marginBottom: "8px",
  },
  confidenceBar: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "11px",
    color: theme.textFaint,
  },
  confidenceTrack: {
    flex: 1,
    height: "4px",
    background: theme.surfaceMuted,
    borderRadius: "999px",
    overflow: "hidden",
  },
  confidenceFill: (confidence) => ({
    height: "100%",
    width: `${Math.round((confidence ?? 0) * 100)}%`,
    background: confidence > 0.8 ? theme.green : confidence > 0.5 ? theme.accent : theme.borderMid,
    borderRadius: "999px",
    transition: "width 0.4s",
  }),
}

export default function PointCard({ point, forecast, radar, nowcast, rainviewerUrl, loading = false }) {
  if (loading) {
    return (
      <div style={{ ...s.card, padding: "18px" }}>
        <div style={{
          height: "16px", width: "60%", borderRadius: "6px", marginBottom: "12px",
          background: `linear-gradient(90deg,${theme.surfaceMuted} 25%,${theme.border} 50%,${theme.surfaceMuted} 75%)`,
          backgroundSize: "200% 100%",
          animation: "skeleton-shimmer 1.4s infinite",
        }} />
        <div style={{
          height: "120px", borderRadius: "8px",
          background: `linear-gradient(90deg,${theme.surfaceMuted} 25%,${theme.border} 50%,${theme.surfaceMuted} 75%)`,
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
  const accentColor = cardAccent(nowcast)
  // Eco presente pero por debajo del umbral de lluvia operacional (18 dBZ)
  const hasWeakEcho = !nowcast?.raining_now && radar?.category === "Débil"

  return (
    <article style={{
      ...s.card,
      // Borde superior de estado — propósito funcional (lluvia activa / ETA próxima)
      borderTop: accentColor !== "transparent"
        ? `3px solid ${accentColor}`
        : `1px solid ${theme.border}`,
      // Sombra más pronunciada cuando hay alerta activa
      boxShadow: accentColor !== "transparent"
        ? `0 2px 8px rgba(0,0,0,0.10), 0 8px 24px rgba(0,0,0,0.08)`
        : theme.shadow,
    }}>
      {/* ---- Header ---- */}
      <div style={s.header}>
        <div style={s.headerLeft}>
          <span style={s.pointName}>{point.name}</span>
          <span style={s.coords}>
            {point.lat.toFixed(4)}° N, {Math.abs(point.lon).toFixed(4)}° O
          </span>
        </div>
        <RadarStatus reading={radar} available={radarAvailable} rainviewerUrl={rainviewerUrl} />
      </div>

      {/* ---- Body ---- */}
      <div style={s.body}>

        {/* Estado de lluvia ahora + ETA */}
        <div style={s.row}>
          <div style={s.section}>
            <span style={s.label}>Ahora</span>
            <span
              style={hasWeakEcho ? s.weakEchoBadge : s.rainNowBadge(nowcast?.raining_now)}
              title={
                nowcast?.raining_now
                  ? "El radar detecta lluvia activa sobre este punto (dBZ ≥ 18)."
                  : hasWeakEcho
                    ? "Eco de radar débil (<18 dBZ). Puede ser virga (lluvia que se evapora antes de llegar al suelo) o precipitación muy ligera."
                    : "Sin eco significativo en el radar sobre este punto."
              }
            >
              {nowcast?.raining_now
                ? <><CloudRainIcon size={14} color="#16A34A" /> Lloviendo</>
                : hasWeakEcho
                  ? <><CloudIcon size={14} color="#854D0E" /> Eco débil</>
                  : <><SunIcon size={14} color={theme.textMuted} /> Sin lluvia</>
              }
            </span>
            <SourceTag source="iam" />
          </div>

          {nowcast && !nowcast.raining_now && nowcast.eta_minutes !== null && (
            <div style={s.section}>
              <span style={s.label}>Lluvia en</span>
              <span
                style={s.etaBadge}
                title={`Tiempo Estimado de Llegada (ETA): el motor de nowcast calculó que la nube de lluvia más cercana tardará ~${nowcast.eta_minutes} minutos en llegar a este punto, usando el flujo óptico del radar y el viento a 700 hPa.`}
              >
                <ClockIcon size={14} color="#92400E" />
                <span style={{ fontFamily: theme.fontMono, fontVariantNumeric: "tabular-nums" }}>
                  {nowcast.eta_minutes} min
                </span>
              </span>
              <SourceTag source="nowcast" />
            </div>
          )}
        </div>

        {/* Mini-mapa del eco causante cuando hay ETA y posición del eco */}
        {nowcast && nowcast.eta_minutes !== null && nowcast.cell_lat != null && (
          <div style={s.section}>
            <span style={s.label}>Nube causante</span>
            <CellMap
              points={[{ id: point.id, name: point.name, lat: point.lat, lon: point.lon }]}
              nowcasts={{ [point.id]: nowcast }}
              focusPoint={{ id: point.id, lat: point.lat, lon: point.lon }}
              compact
              height="160px"
            />
            <SourceTag source="iam" />
          </div>
        )}

        {/* Confianza del nowcast */}
        {nowcast?.confidence !== null && nowcast?.confidence !== undefined && (
          <div style={s.section}>
            <div
              style={s.confidenceBar}
              title={[
              "Confianza del nowcast:",
              nowcast.conf_radar != null ? `radar ${Math.round(nowcast.conf_radar * 100)}%` : null,
              nowcast.model_agreement != null ? `modelo ${Math.round(nowcast.model_agreement * 100)}%` : null,
              nowcast.mult_trend != null ? `tendencia ×${nowcast.mult_trend.toFixed(2)}` : null,
              nowcast.weight_radar != null ? `(peso radar ${Math.round(nowcast.weight_radar * 100)}%)` : null,
              "· Verde >80%, ámbar >50%, gris = baja confianza.",
            ].filter(Boolean).join(" · ")}
            >
              <span>Confianza</span>
              <div style={s.confidenceTrack}>
                <div style={s.confidenceFill(nowcast.confidence)} />
              </div>
              <span style={{ fontFamily: theme.fontMono }}>
                {Math.round(nowcast.confidence * 100)}%
              </span>
            </div>
            <SourceTag source="nowcast" />
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
              <SourceTag source="openmeteo" />
            </div>
            <div style={s.section}>
              <span style={s.label}>Prob. lluvia</span>
              <span style={s.probBadge}>
                <DropletIcon size={13} color={theme.primary} />
                {nearest.precipitation_probability}%
              </span>
              <SourceTag source="openmeteo" />
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
            <SourceTag source="openmeteo" />
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
