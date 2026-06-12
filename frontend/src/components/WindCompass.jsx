/** Rosa de viento con flecha SVG.
 *  Props: { speedKmh: number, directionDeg: number, label?: string }
 *
 *  directionDeg: dirección DE donde viene el viento (convención meteorológica).
 *  La flecha apunta hacia donde va el viento (directionDeg + 180°).
 */

import { theme } from "../theme.js"

const SIZE = 64
const CX = SIZE / 2
const CY = SIZE / 2
const R_OUTER = 28
const R_INNER = 10

export default function WindCompass({ speedKmh, directionDeg, label }) {
  // Rotación: el viento "viene de" directionDeg, la flecha apunta adonde "va"
  const arrowRotation = (directionDeg + 180) % 360

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "4px" }}>
      {label && (
        <span style={{
          fontSize: "10px",
          color: theme.textFaint,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
        }}>
          {label}
        </span>
      )}
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        aria-label={`Viento: ${speedKmh} km/h desde ${directionDeg}°`}
      >
        {/* Círculo base */}
        <circle
          cx={CX}
          cy={CY}
          r={R_OUTER}
          fill={theme.surfaceMuted}
          stroke={theme.border}
          strokeWidth="1.5"
        />

        {/* Marcas cardinales */}
        {[0, 90, 180, 270].map((deg) => {
          const rad = (deg - 90) * (Math.PI / 180)
          const x1 = CX + (R_OUTER - 4) * Math.cos(rad)
          const y1 = CY + (R_OUTER - 4) * Math.sin(rad)
          const x2 = CX + (R_OUTER - 9) * Math.cos(rad)
          const y2 = CY + (R_OUTER - 9) * Math.sin(rad)
          return (
            <line
              key={deg}
              x1={x1} y1={y1}
              x2={x2} y2={y2}
              stroke={theme.borderMid}
              strokeWidth="1"
              strokeLinecap="round"
            />
          )
        })}

        {/* Flecha de viento */}
        <g transform={`rotate(${arrowRotation}, ${CX}, ${CY})`}>
          {/* Punta de flecha (triángulo hacia arriba = norte) */}
          <polygon
            points={`${CX},${CY - R_INNER - 10} ${CX - 5},${CY - R_INNER + 2} ${CX + 5},${CY - R_INNER + 2}`}
            fill={theme.primary}
          />
          {/* Tallo */}
          <line
            x1={CX} y1={CY - R_INNER + 2}
            x2={CX} y2={CY + R_INNER + 6}
            stroke={theme.primary}
            strokeWidth="2.5"
            strokeLinecap="round"
          />
          {/* Cola */}
          <line
            x1={CX - 5} y1={CY + R_INNER + 2}
            x2={CX + 5} y2={CY + R_INNER + 2}
            stroke={theme.primary}
            strokeWidth="2"
            strokeLinecap="round"
          />
        </g>

        {/* Punto central */}
        <circle cx={CX} cy={CY} r="3" fill={theme.surface} stroke={theme.primary} strokeWidth="1.5" />
      </svg>

      <span style={{
        fontSize: "13px",
        fontWeight: 600,
        color: theme.text,
        fontFamily: theme.fontMono,
        fontVariantNumeric: "tabular-nums",
      }}>
        {speedKmh.toFixed(0)}{" "}
        <span style={{ fontSize: "10px", color: theme.textMuted }}>km/h</span>
      </span>
      <span style={{ fontSize: "10px", color: theme.textFaint, fontFamily: theme.fontMono }}>
        {directionDeg}°
      </span>
    </div>
  )
}
