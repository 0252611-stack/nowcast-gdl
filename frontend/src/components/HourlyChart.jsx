/** Gráfico de precipitación + probabilidad de lluvia para las próximas 12 h.
 *  Props: { hourly: HourlyForecast[] }
 *  Usa Recharts: barras de precipitación_mm (eje izq.) + línea de probabilidad % (eje der.)
 */

import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts"
import SourceTag from "./SourceTag.jsx"
import { CloudRainIcon, DropletIcon, ThermometerIcon, WindIcon } from "./Icons.jsx"
import { theme } from "../theme.js"

/** Formatea ISO datetime → "HH:mm" en hora local */
function fmtHour(isoStr) {
  const date = new Date(isoStr)
  return date.toLocaleTimeString("es-MX", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "America/Mexico_City",
  })
}

function TooltipRow({ icon, label, value }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px" }}>
      {icon}
      <span style={{ color: theme.textMuted, minWidth: "70px" }}>{label}</span>
      <strong style={{ fontFamily: theme.fontMono, color: theme.text }}>{value}</strong>
    </div>
  )
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null

  const data = payload[0]?.payload ?? {}
  return (
    <div style={{
      background: theme.surface,
      border: `1px solid ${theme.border}`,
      borderRadius: "10px",
      padding: "10px 14px",
      fontSize: "13px",
      color: theme.text,
      minWidth: "180px",
      boxShadow: theme.shadow,
    }}>
      <p style={{ fontWeight: 700, marginBottom: "8px", color: theme.primary, fontFamily: theme.fontMono }}>{label}</p>
      <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
        <TooltipRow icon={<CloudRainIcon size={13} color={theme.primary} />} label="Precip" value={`${data.precipitation_mm} mm`} />
        <TooltipRow icon={<DropletIcon size={13} color={theme.accent} />} label="Prob. lluvia" value={`${data.precipitation_probability}%`} />
        <TooltipRow icon={<ThermometerIcon size={13} color={theme.red} />} label="Temp" value={`${data.temperature_c} °C`} />
        <TooltipRow icon={<WindIcon size={13} color={theme.textMuted} />} label="Viento 10 m" value={`${data.wind_speed_10m_kmh} km/h`} />
      </div>
    </div>
  )
}

export default function HourlyChart({ hourly }) {
  if (!hourly || hourly.length === 0) {
    return (
      <div style={{ color: theme.textFaint, fontSize: "13px", textAlign: "center", padding: "20px 0" }}>
        Sin datos de pronóstico
      </div>
    )
  }

  const chartData = hourly.map((h) => ({
    ...h,
    hour: fmtHour(h.time),
  }))

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <div style={{ width: "100%", height: 180 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 4, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
            <XAxis
              dataKey="hour"
              tick={{ fill: theme.textFaint, fontSize: 11, fontFamily: theme.fontMono }}
              tickLine={false}
              axisLine={false}
              interval={1}
            />
            <YAxis
              yAxisId="left"
              orientation="left"
              tick={{ fill: theme.textFaint, fontSize: 11, fontFamily: theme.fontMono }}
              tickLine={false}
              axisLine={false}
              width={28}
              unit=" mm"
              domain={[0, 'auto']}
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fill: theme.textFaint, fontSize: 11, fontFamily: theme.fontMono }}
              tickLine={false}
              axisLine={false}
              width={34}
              unit="%"
              domain={[0, 100]}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend
              wrapperStyle={{ fontSize: "12px", color: theme.textMuted, paddingTop: "4px" }}
            />
            <Bar
              yAxisId="left"
              dataKey="precipitation_mm"
              name="Precip (mm)"
              fill={theme.primary}
              opacity={0.75}
              radius={[3, 3, 0, 0]}
              maxBarSize={18}
            />
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="precipitation_probability"
              name="Prob lluvia (%)"
              stroke={theme.accent}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: theme.accent }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <SourceTag source="openmeteo" />
    </div>
  )
}
