/** dBZ actual + badge de categoría con color.
 *  Props: { reading: RadarReading|null, available: boolean, rainviewerUrl?: string }
 */

import SourceTag from "./SourceTag.jsx"
import { theme } from "../theme.js"

const CATEGORY_STYLES = {
  "Ruido":              { bg: theme.surfaceMuted, color: theme.textMuted,  border: theme.borderMid,    label: "Ruido" },
  "Débil":             { bg: theme.greenLight,   color: "#166534",         border: theme.green + "55", label: "Débil" },
  "Ligera":             { bg: theme.yellowLight,  color: "#854D0E",         border: theme.yellow + "55",label: "Ligera" },
  "Moderada a fuerte":  { bg: theme.orangeLight,  color: "#9A3412",         border: theme.orange + "55",label: "Moderada" },
  "Granizo":            { bg: theme.redLight,     color: "#991B1B",         border: theme.red + "55",   label: "Granizo" },
}

const styles = {
  wrapper: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-end",
    gap: "4px",
  },
  readingRow: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
  },
  unavailable: {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "4px 10px",
    borderRadius: "999px",
    background: theme.surfaceMuted,
    border: `1px solid ${theme.borderMid}`,
    color: theme.textMuted,
    fontSize: "12px",
    fontWeight: 500,
  },
  dbz: {
    fontSize: "22px",
    fontWeight: 700,
    color: theme.text,
    lineHeight: 1,
    fontFamily: theme.fontMono,
    fontVariantNumeric: "tabular-nums",
  },
  dbzUnit: {
    fontSize: "12px",
    color: theme.textMuted,
    marginLeft: "2px",
  },
  badge: (cat) => {
    const cs = CATEGORY_STYLES[cat] || CATEGORY_STYLES["Ruido"]
    return {
      display: "inline-flex",
      alignItems: "center",
      padding: "3px 10px",
      borderRadius: "999px",
      background: cs.bg,
      color: cs.color,
      fontSize: "12px",
      fontWeight: 600,
      border: `1px solid ${cs.border}`,
    }
  },
}

export default function RadarStatus({ reading, available, rainviewerUrl }) {
  if (!available || reading === null) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "6px", alignItems: "flex-end" }}>
        <span style={styles.unavailable}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
            <circle cx="6" cy="6" r="5" stroke={theme.textMuted} strokeWidth="1.5"/>
            <line x1="4" y1="4" x2="8" y2="8" stroke={theme.textMuted} strokeWidth="1.5" strokeLinecap="round"/>
            <line x1="8" y1="4" x2="4" y2="8" stroke={theme.textMuted} strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          Radar no disponible
        </span>
        {rainviewerUrl && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "3px" }}>
            <a
              href={rainviewerUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="Radar regional vía RainViewer"
              aria-label="Ver radar regional en RainViewer"
            >
              <img
                src={rainviewerUrl}
                alt="Radar regional (RainViewer)"
                style={{
                  width: "80px",
                  height: "80px",
                  borderRadius: "8px",
                  border: `1px solid ${theme.border}`,
                  opacity: 0.92,
                  display: "block",
                }}
                onError={(e) => { e.target.parentElement.parentElement.style.display = "none" }}
              />
            </a>
            <SourceTag source="rainviewer" />
          </div>
        )}
      </div>
    )
  }

  const cat = reading.category
  const catStyle = CATEGORY_STYLES[cat] || CATEGORY_STYLES["Ruido"]

  return (
    <div style={styles.wrapper}>
      <div style={styles.readingRow}>
        <span
          style={styles.dbz}
          title="dBZ (decibelios respecto al milímetro cúbico): mide la intensidad del eco de radar. Más alto = gotas más grandes o más densas. ≥18 dBZ indica precipitación real; <18 puede ser virga (lluvia que no llega al suelo)."
        >
          {reading.dbz.toFixed(1)}
          <span style={styles.dbzUnit}>dBZ</span>
        </span>
        <span style={styles.badge(cat)} title={`Categoría de intensidad basada en el valor dBZ del radar IAM`}>
          {catStyle.label}
        </span>
      </div>
      <SourceTag source="iam" />
    </div>
  )
}
