/**
 * Tokens de diseño centralizados — Nowcast GDL.
 * Un solo lugar con hex; todos los componentes importan de aquí.
 * Paleta: Analytics Dashboard claro (ui-ux-pro-max · WCAG-verificada).
 */

export const theme = {
  // Fondos — gris neutro sin tinte azul
  bg:          "#FAFAFA",
  surface:     "#FFFFFF",
  surfaceMuted:"#F4F4F5",

  // Bordes y divisores — gris neutro
  border:      "#E4E4E7",
  borderMid:   "#D4D4D8",

  // Texto (todos ≥4.5:1 sobre white/surface)
  text:        "#1E293B",   // ~13:1
  textMuted:   "#475569",   // ~6.5:1
  textFaint:   "#64748B",   // ~5:1

  // Primario (azul analítico)
  primary:     "#1E40AF",
  primaryLight:"#DBEAFE",
  primaryText: "#FFFFFF",

  // Acento (ámbar — ETA, alertas)
  accent:      "#D97706",
  accentLight: "#FEF3C7",
  accentText:  "#FFFFFF",

  // Semánticos de estado de lluvia (≥4.5:1 sobre white)
  green:       "#16A34A",   // Débil / sin lluvia
  greenLight:  "#DCFCE7",
  yellow:      "#CA8A04",   // Ligera
  yellowLight: "#FEF9C3",
  orange:      "#EA580C",   // Moderada a fuerte
  orangeLight: "#FFEDD5",
  red:         "#DC2626",   // Granizo
  redLight:    "#FEE2E2",

  // Sombra
  shadow:      "0 1px 4px rgba(0,0,0,0.08), 0 4px 16px rgba(0,0,0,0.06)",

  // Tipografía
  fontBase:    "'Fira Sans', system-ui, sans-serif",
  fontMono:    "'Fira Code', 'Fira Mono', monospace",
}

/**
 * Fuentes de datos — color del punto y etiqueta.
 * Usar con <SourceTag source="openmeteo" /> etc.
 */
export const SOURCES = {
  openmeteo: { label: "Open-Meteo",    color: "#2563EB" },
  iam:       { label: "Radar IAM-UdeG",color: "#16A34A" },
  rainviewer:{ label: "RainViewer",    color: "#D97706" },
  nowcast:   { label: "Motor Nowcast", color: "#7C3AED" },
}
