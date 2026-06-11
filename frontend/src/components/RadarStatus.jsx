/** dBZ actual + badge de categoría con color.
 *  Props: { reading: RadarReading|null, available: boolean } */

const CATEGORY_STYLES = {
  "Ruido":              { bg: "#334155", color: "#94a3b8", label: "Ruido" },
  "Débil":             { bg: "#14532d", color: "#22c55e", label: "Débil" },
  "Ligera":             { bg: "#713f12", color: "#eab308", label: "Ligera" },
  "Moderada a fuerte":  { bg: "#7c2d12", color: "#f97316", label: "Moderada" },
  "Granizo":            { bg: "#7f1d1d", color: "#ef4444", label: "Granizo" },
}

const styles = {
  wrapper: {
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
    background: "#1e293b",
    border: "1px solid #334155",
    color: "#64748b",
    fontSize: "12px",
    fontWeight: 500,
  },
  dbz: {
    fontSize: "22px",
    fontWeight: 700,
    color: "#e2e8f0",
    lineHeight: 1,
  },
  dbzUnit: {
    fontSize: "12px",
    color: "#94a3b8",
    marginLeft: "2px",
  },
  badge: (cat) => ({
    display: "inline-flex",
    alignItems: "center",
    padding: "3px 10px",
    borderRadius: "999px",
    background: (CATEGORY_STYLES[cat] || CATEGORY_STYLES["Ruido"]).bg,
    color: (CATEGORY_STYLES[cat] || CATEGORY_STYLES["Ruido"]).color,
    fontSize: "12px",
    fontWeight: 600,
    border: `1px solid ${(CATEGORY_STYLES[cat] || CATEGORY_STYLES["Ruido"]).color}44`,
  }),
}

export default function RadarStatus({ reading, available, rainviewerUrl }) {
  if (!available || reading === null) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "6px", alignItems: "flex-end" }}>
        <span style={styles.unavailable}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <circle cx="6" cy="6" r="5" stroke="#64748b" strokeWidth="1.5"/>
            <line x1="4" y1="4" x2="8" y2="8" stroke="#64748b" strokeWidth="1.5" strokeLinecap="round"/>
            <line x1="8" y1="4" x2="4" y2="8" stroke="#64748b" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          Radar no disponible
        </span>
        {rainviewerUrl && (
          <a
            href={rainviewerUrl}
            target="_blank"
            rel="noopener noreferrer"
            title="Radar regional vía RainViewer"
          >
            <img
              src={rainviewerUrl}
              alt="Radar regional (RainViewer)"
              style={{
                width: "80px",
                height: "80px",
                borderRadius: "6px",
                border: "1px solid #334155",
                opacity: 0.9,
                display: "block",
              }}
              onError={(e) => { e.target.parentElement.style.display = "none" }}
            />
          </a>
        )}
      </div>
    )
  }

  const cat = reading.category
  const catStyle = CATEGORY_STYLES[cat] || CATEGORY_STYLES["Ruido"]

  return (
    <div style={styles.wrapper}>
      <span style={styles.dbz}>
        {reading.dbz.toFixed(1)}
        <span style={styles.dbzUnit}>dBZ</span>
      </span>
      <span style={styles.badge(cat)}>{catStyle.label}</span>
    </div>
  )
}
