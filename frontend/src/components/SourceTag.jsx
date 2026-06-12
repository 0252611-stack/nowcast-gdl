/**
 * Caption de fuente de datos.
 * Muestra un punto de color + texto: "● Open-Meteo"
 * Props: { source: "openmeteo"|"iam"|"rainviewer"|"nowcast", note?: string }
 */

import { SOURCES } from "../theme.js"

export default function SourceTag({ source, note }) {
  const src = SOURCES[source]
  if (!src) return null
  const label = note ? `${src.label} — ${note}` : src.label
  return (
    <span
      aria-label={`Fuente: ${label}`}
      style={{
        display:    "inline-flex",
        alignItems: "center",
        gap:        "5px",
        fontSize:   "11px",
        color:      "#475569",
        marginTop:  "3px",
      }}
    >
      {/* Punto de color — decorativo, la etiqueta de texto siempre está presente */}
      <span
        aria-hidden="true"
        style={{
          display:      "inline-block",
          width:        "6px",
          height:       "6px",
          borderRadius: "50%",
          background:   src.color,
          flexShrink:   0,
        }}
      />
      {label}
    </span>
  )
}
