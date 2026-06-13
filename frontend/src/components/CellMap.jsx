/**
 * Mapa Leaflet reutilizable: puntos monitoreados + eco causante + flechas + ecos de contexto.
 * Props:
 *   points        — array de {id, name, lat, lon}
 *   nowcasts      — dict point_id → NowcastResult|null
 *   focusPoint    — {id, lat, lon} del punto principal (mini-mapa)
 *   rainviewerUrl — URL de tile RainViewer (para derivar la plantilla)
 *   compact       — true para mini-mapa sin controles
 *   height        — CSS height del mapa (default "300px")
 *   contextEchoes — array de ContextEcho (ecos no causantes, visualización)
 */

import { useEffect } from "react"
import { MapContainer, TileLayer, Marker, Polyline, CircleMarker, Tooltip, useMap } from "react-leaflet"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { theme } from "../theme.js"

delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconUrl: new URL("leaflet/dist/images/marker-icon.png", import.meta.url).href,
  iconRetinaUrl: new URL("leaflet/dist/images/marker-icon-2x.png", import.meta.url).href,
  shadowUrl: new URL("leaflet/dist/images/marker-shadow.png", import.meta.url).href,
})

const CONTEXT_COLOR = "#94A3B8"  // slate-400 — ecos de contexto

/** Deriva plantilla de tiles RainViewer desde una URL de tile específica. */
function rainviewerTemplate(url) {
  if (!url) return null
  const parts = url.split("/")
  const idx = parts.indexOf("256")
  if (idx < 0) return null
  const color = parts[idx + 4]
  const suffix = parts.slice(idx + 5).join("/")
  const prefix = parts.slice(0, idx + 1).join("/")
  return `${prefix}/{z}/{x}/{y}/${color}/${suffix}`
}

// ---------------------------------------------------------------------------
// Iconos SVG
// ---------------------------------------------------------------------------

/** Flecha naranja sólida — dirección del eco causante (optical flow) */
function flowArrowIcon(bearing) {
  const svg = `<svg width="28" height="28" viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing}, 14, 14)">
      <polygon points="14,3 24,24 14,19 4,24" fill="${theme.orange}" stroke="#FFFFFF" stroke-width="1.5"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [28, 28], iconAnchor: [14, 14] })
}

/** Flecha azul hueca — viento 700 hPa en el eco causante */
function windArrowIcon(bearing) {
  const svg = `<svg width="22" height="22" viewBox="0 0 22 22" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing}, 11, 11)">
      <polygon points="11,2 19,19 11,15 3,19" fill="none" stroke="${theme.primary}" stroke-width="2" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [22, 22], iconAnchor: [11, 11] })
}

/** Flecha pequeña azul — viento 700 hPa en punto intermedio de la trayectoria */
function sampleArrowIcon(bearing) {
  const svg = `<svg width="14" height="14" viewBox="0 0 14 14" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing}, 7, 7)">
      <polygon points="7,1 12,12 7,9 2,12" fill="none" stroke="${theme.primary}" stroke-width="1.5" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [14, 14], iconAnchor: [7, 7] })
}

/** Flecha gris pequeña — eco de contexto (no causante) */
function contextArrowIcon(bearing) {
  const svg = `<svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing}, 8, 8)">
      <polygon points="8,2 13,13 8,10 3,13" fill="${CONTEXT_COLOR}" stroke="#FFFFFF" stroke-width="1" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [16, 16], iconAnchor: [8, 8] })
}

/** Icono de marcador de punto monitoreado */
function pointIcon(raining) {
  const color = raining ? theme.green : theme.primary
  const svg = `<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
    <circle cx="10" cy="10" r="7" fill="${color}" stroke="#FFFFFF" stroke-width="2"/>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [20, 20], iconAnchor: [10, 10] })
}

// ---------------------------------------------------------------------------
// Utilidades de trayectoria
// ---------------------------------------------------------------------------

/**
 * Calcula el color de la línea de trayectoria según la consistencia del viento.
 * Verde si todos los puntos intermedios están dentro de ±30° del optical flow,
 * amarillo si la divergencia máxima está entre 30-60°, naranja si >60°.
 */
function trajectoryColor(nw) {
  if (!nw.trajectory_wind?.length) return theme.orange
  const bearing = nw.cell_bearing_deg ?? 0
  const maxDiff = Math.max(...nw.trajectory_wind.map(s => {
    let d = Math.abs(s.toward_deg - bearing)
    if (d > 180) d = 360 - d
    return d
  }))
  if (maxDiff < 30) return theme.green
  if (maxDiff < 60) return theme.yellow
  return theme.orange
}

/** Filtra ecos de contexto que estén demasiado cerca de ecos causantes */
function filterContextEchoes(contextEchoes, nowcasts, thresholdKm = 20) {
  const causing = Object.values(nowcasts)
    .filter(nw => nw?.cell_lat != null)
    .map(nw => [nw.cell_lat, nw.cell_lon])

  return contextEchoes.filter(ce => {
    return !causing.some(([lat, lon]) => {
      const dlat = (ce.lat - lat) * 111.32
      const dlon = (ce.lon - lon) * 111.32 * Math.cos(lat * Math.PI / 180)
      return Math.sqrt(dlat * dlat + dlon * dlon) < thresholdKm
    })
  })
}

// ---------------------------------------------------------------------------
// Componente de ajuste de bounds
// ---------------------------------------------------------------------------

function BoundsFitter({ points }) {
  const map = useMap()
  useEffect(() => {
    if (!points || points.length === 0) return
    const lats = points.map(p => p.lat)
    const lons = points.map(p => p.lon)
    const bounds = [
      [Math.min(...lats) - 0.05, Math.min(...lons) - 0.05],
      [Math.max(...lats) + 0.05, Math.max(...lons) + 0.05],
    ]
    map.fitBounds(bounds, { padding: [20, 20] })
  }, [map, points])
  return null
}

// ---------------------------------------------------------------------------
// Componente principal
// ---------------------------------------------------------------------------

export default function CellMap({
  points = [],
  nowcasts = {},
  focusPoint = null,
  rainviewerUrl = null,
  compact = false,
  height = "300px",
  contextEchoes = [],
}) {
  const center = focusPoint
    ? [focusPoint.lat, focusPoint.lon]
    : points.length > 0
    ? [points[0].lat, points[0].lon]
    : [20.68, -103.44]

  const zoom = compact ? 10 : 10
  const rvTemplate = rainviewerTemplate(rainviewerUrl)

  const mapStyle = {
    height,
    width: "100%",
    borderRadius: compact ? "8px" : "12px",
    border: `1px solid ${theme.border}`,
    zIndex: 0,
  }

  const displayPoints = focusPoint
    ? points.filter(p => p.id === focusPoint.id)
    : points

  const visibleContextEchoes = compact
    ? []
    : filterContextEchoes(contextEchoes, nowcasts)

  return (
    <MapContainer
      center={center}
      zoom={zoom}
      style={mapStyle}
      dragging={!compact}
      zoomControl={!compact}
      scrollWheelZoom={!compact}
      doubleClickZoom={!compact}
      touchZoom={!compact}
      attributionControl={!compact}
    >
      {/* Base OSM */}
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='© <a href="https://www.openstreetmap.org/copyright">OSM</a>'
        opacity={0.7}
      />

      {/* Capa radar RainViewer (cuando hay URL disponible) */}
      {rvTemplate && (
        <TileLayer url={rvTemplate} opacity={0.6} attribution="RainViewer" />
      )}

      {/* Ajustar bounds automáticamente */}
      {!compact && !focusPoint && points.length > 1 && <BoundsFitter points={points} />}

      {/* Ecos de contexto (no causantes) — círculo gris + flecha corta */}
      {visibleContextEchoes.map((ce, i) => (
        <g key={`ctx-${i}`}>
          <CircleMarker
            center={[ce.lat, ce.lon]}
            radius={5}
            pathOptions={{ color: CONTEXT_COLOR, fillColor: CONTEXT_COLOR, fillOpacity: 0.3, weight: 1.5 }}
          >
            <Tooltip>
              Eco · {ce.dbz.toFixed(0)} dBZ · {ce.speed_kmh.toFixed(0)} km/h
            </Tooltip>
          </CircleMarker>
          <Marker position={[ce.lat, ce.lon]} icon={contextArrowIcon(ce.bearing_deg)} />
        </g>
      ))}

      {/* Marcadores de puntos monitoreados */}
      {displayPoints.map(pt => {
        const nw = nowcasts[pt.id]
        return (
          <Marker key={pt.id} position={[pt.lat, pt.lon]} icon={pointIcon(nw?.raining_now)}>
            {!compact && <Tooltip>{pt.name}</Tooltip>}
          </Marker>
        )
      })}

      {/* Eco causante + flechas + trayectoria */}
      {(focusPoint ? [focusPoint] : points).map(pt => {
        const nw = nowcasts[pt.id]
        if (!nw || nw.cell_lat == null || nw.cell_lon == null) return null
        const echoPos = [nw.cell_lat, nw.cell_lon]
        const ptPos = [pt.lat, pt.lon]
        const flowBearing = nw.cell_bearing_deg ?? 0
        const lineColor = trajectoryColor(nw)

        return (
          <g key={`echo-${pt.id}`}>
            {/* Línea de trayectoria — color según consistencia del viento */}
            <Polyline
              positions={[echoPos, ptPos]}
              pathOptions={{ color: lineColor, weight: 2, dashArray: "6 4", opacity: 0.85 }}
            />

            {/* Flechas de viento en puntos intermedios de la trayectoria */}
            {nw.trajectory_wind?.map((s, i) => (
              <Marker key={`tw-${pt.id}-${i}`} position={[s.lat, s.lon]} icon={sampleArrowIcon(s.toward_deg)}>
                {!compact && (
                  <Tooltip>
                    Viento 700 hPa · {Math.round(s.toward_deg)}° · {s.speed_kmh.toFixed(0)} km/h
                  </Tooltip>
                )}
              </Marker>
            ))}

            {/* Círculo del eco causante */}
            <CircleMarker
              center={echoPos}
              radius={8}
              pathOptions={{ color: theme.orange, fillColor: theme.orange, fillOpacity: 0.5, weight: 2 }}
            >
              {!compact && (
                <Tooltip>
                  <div style={{ fontSize: "12px", lineHeight: "1.6" }}>
                    <strong>Eco causante</strong><br />
                    ETA: {nw.eta_minutes} min · {nw.cell_speed_kmh} km/h<br />
                    <span style={{ color: theme.orange }}>▶ Flujo radar: {Math.round(flowBearing)}°</span>
                    {nw.wind_echo_bearing_deg != null && (
                      <><br /><span style={{ color: theme.primary }}>▷ Viento 700 hPa: {Math.round(nw.wind_echo_bearing_deg)}° · {nw.wind_echo_speed_kmh?.toFixed(0)} km/h</span></>
                    )}
                  </div>
                </Tooltip>
              )}
            </CircleMarker>

            {/* Flecha naranja sólida — optical flow */}
            <Marker position={echoPos} icon={flowArrowIcon(flowBearing)} />

            {/* Flecha azul hueca — viento 700 hPa en el eco */}
            {nw.wind_echo_bearing_deg != null && (
              <Marker position={echoPos} icon={windArrowIcon(nw.wind_echo_bearing_deg)}>
                {!compact && (
                  <Tooltip>Viento 700 hPa en eco: {Math.round(nw.wind_echo_bearing_deg)}° · {nw.wind_echo_speed_kmh?.toFixed(0)} km/h</Tooltip>
                )}
              </Marker>
            )}
          </g>
        )
      })}
    </MapContainer>
  )
}
