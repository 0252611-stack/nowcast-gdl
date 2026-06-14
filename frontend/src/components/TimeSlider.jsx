/**
 * Control deslizante temporal para la vista de Predicción.
 * Props:
 *   step        — índice actual (0 = "Ahora", 1..N = pasos de predicción)
 *   steps       — array de {minutes, image_url, contours} del backend
 *   baseTime    — ISO string del timestamp del frame base
 *   onStepChange — callback(nuevoStep: number)
 */

import { useState, useEffect } from "react"
import { theme } from "../theme.js"

const PLAY_INTERVAL_MS = 350  // ~350 ms: 24 frames se reproducen fluido (~8 s)

export default function TimeSlider({ step, steps, baseTime, onStepChange }) {
  const [playing, setPlaying] = useState(false)

  // No auto-play si el usuario prefiere reducir movimiento
  const prefersReducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches

  useEffect(() => {
    if (!playing || prefersReducedMotion) return
    const id = setInterval(() => {
      onStepChange(prev => {
        const next = prev + 1
        if (next > steps.length) {
          setPlaying(false)
          return 0            // vuelve al inicio al terminar
        }
        return next
      })
    }, PLAY_INTERVAL_MS)
    return () => clearInterval(id)
  }, [playing, steps.length, onStepChange, prefersReducedMotion])

  // Etiqueta del paso actual
  const label = (() => {
    if (step === 0) return "Ahora"
    const s = steps[step - 1]
    if (!s) return ""
    const baseMs = baseTime ? new Date(baseTime).getTime() : Date.now()
    const targetMs = baseMs + s.minutes * 60_000
    const timeStr = new Date(targetMs).toLocaleString("es-MX", {
      timeStyle: "short",
      timeZone: "America/Mexico_City",
    })
    return `+${s.minutes} min · ${timeStr}`
  })()

  // Opacidad de la barra: reduce conforme avanza (incertidumbre creciente)
  const confidence = step === 0 ? 1.0 : Math.max(0.3, 1 - ((step - 1) / Math.max(1, steps.length)) * 0.6)

  return (
    <div style={st.wrapper}>
      {/* Botón Play / Pausa */}
      <button
        style={st.playBtn}
        onClick={() => setPlaying(p => !p)}
        aria-label={playing ? "Pausar animación" : "Reproducir animación"}
        disabled={steps.length === 0}
      >
        {playing ? "⏸" : "▶"}
      </button>

      {/* Slider */}
      <div style={st.sliderWrapper}>
        <input
          type="range"
          min={0}
          max={steps.length}
          value={step}
          onChange={e => {
            // NO detenemos la reproducción: el usuario puede arrastrar mientras
            // el slider avanza solo (auto-play + scrubbing simultáneos).
            onStepChange(Number(e.target.value))
          }}
          style={st.slider}
          aria-label="Seleccionar paso temporal"
        />
        {/* Marcas cada 30 min (no todos los 24 ticks — saturarían la UI) */}
        <div style={st.ticks}>
          <span style={st.tick}>Ahora</span>
          {steps.filter(s => s.minutes % 30 === 0).map(s => (
            <span key={s.minutes} style={st.tick}>+{s.minutes}&apos;</span>
          ))}
        </div>
      </div>

      {/* Etiqueta del paso */}
      <div style={{ ...st.label, opacity: confidence }}>
        {label}
      </div>
    </div>
  )
}

const st = {
  wrapper: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "12px 16px",
    background: theme.surface,
    borderTop: `1px solid ${theme.border}`,
    borderRadius: "0 0 14px 14px",
  },
  playBtn: {
    flexShrink: 0,
    width: "36px",
    height: "36px",
    borderRadius: "50%",
    border: `1px solid ${theme.border}`,
    background: theme.primaryLight,
    color: theme.primary,
    cursor: "pointer",
    fontSize: "14px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  sliderWrapper: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    gap: "2px",
  },
  slider: {
    width: "100%",
    accentColor: theme.primary,
    cursor: "pointer",
  },
  ticks: {
    display: "flex",
    justifyContent: "space-between",
  },
  tick: {
    fontSize: "10px",
    color: theme.textFaint,
    userSelect: "none",
  },
  label: {
    flexShrink: 0,
    minWidth: "110px",
    textAlign: "right",
    fontSize: "13px",
    fontWeight: 600,
    color: theme.text,
    transition: "opacity 0.3s ease",
  },
}
