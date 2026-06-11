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

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null

  const data = payload[0]?.payload ?? {}
  return (
    <div style={{
      background: "#1e293b",
      border: "1px solid #334155",
      borderRadius: "8px",
      padding: "10px 14px",
      fontSize: "13px",
      color: "#e2e8f0",
      minWidth: "170px",
    }}>
      <p style={{ fontWeight: 700, marginBottom: "6px", color: "#38bdf8" }}>{label}</p>
      <p>🌧 Precip: <strong>{data.precipitation_mm} mm</strong></p>
      <p>☁️ Prob lluvia: <strong>{data.precipitation_probability}%</strong></p>
      <p>🌡 Temp: <strong>{data.temperature_c} °C</strong></p>
      <p>💨 Viento 10m: <strong>{data.wind_speed_10m_kmh} km/h</strong></p>
    </div>
  )
}

export default function HourlyChart({ hourly }) {
  if (!hourly || hourly.length === 0) {
    return (
      <div style={{ color: "#64748b", fontSize: "13px", textAlign: "center", padding: "20px 0" }}>
        Sin datos de pronóstico
      </div>
    )
  }

  const chartData = hourly.map((h) => ({
    ...h,
    hour: fmtHour(h.time),
  }))

  return (
    <div style={{ width: "100%", height: 180 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData} margin={{ top: 4, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" vertical={false} />
          <XAxis
            dataKey="hour"
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            interval={1}
          />
          <YAxis
            yAxisId="left"
            orientation="left"
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={28}
            unit=" mm"
            domain={[0, 'auto']}
          />
          <YAxis
            yAxisId="right"
            orientation="right"
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={34}
            unit="%"
            domain={[0, 100]}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: "12px", color: "#94a3b8", paddingTop: "4px" }}
          />
          <Bar
            yAxisId="left"
            dataKey="precipitation_mm"
            name="Precip (mm)"
            fill="#38bdf8"
            opacity={0.8}
            radius={[3, 3, 0, 0]}
            maxBarSize={18}
          />
          <Line
            yAxisId="right"
            type="monotone"
            dataKey="precipitation_probability"
            name="Prob lluvia (%)"
            stroke="#f97316"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: "#f97316" }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
