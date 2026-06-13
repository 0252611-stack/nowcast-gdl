/**
 * Mapa Leaflet: radar como ImageOverlay + puntos monitoreados + eco causante.
 * Props:
 *   points         — array de {id, name, lat, lon}
 *   nowcasts       — dict point_id → NowcastResult|null
 *   focusPoint     — {id, lat, lon} del punto principal (mini-mapa)
 *   rainviewerUrl  — URL de tile RainViewer (fallback cuando radar IAM no disponible)
 *   compact        — true para mini-mapa sin controles
 *   height         — CSS height del mapa (default "300px")
 *   contextEchoes  — array de ContextEcho; se usan para posicionar flechas de dirección
 *   radarImageUrl  — URL del PNG del radar IAM (/radar/image)
 *   radarBounds    — {north, south, east, west} del frame actual
 */

import { useEffect } from "react"
import {
  MapContainer, TileLayer, ImageOverlay,
  Marker, Polyline, CircleMarker, Tooltip, useMap,
} from "react-leaflet"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { theme } from "../theme.js"

delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconUrl:       new URL("leaflet/dist/images/marker-icon.png",    import.meta.url).href,
  iconRetinaUrl: new URL("leaflet/dist/images/marker-icon-2x.png", import.meta.url).href,
  shadowUrl:     new URL("leaflet/dist/images/marker-shadow.png",  import.meta.url).href,
})

/** Deriva plantilla de tiles RainViewer desde una URL de tile específica. */
function rainviewerTemplate(url) {
  if (!url) return null
  const parts = url.split("/")
  const idx = parts.indexOf("256")
  if (idx < 0) return null
  const color  = parts[idx + 4]
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
    <g transform="rotate(${bearing},14,14)">
      <polygon points="14,3 24,24 14,19 4,24" fill="${theme.orange}" stroke="#FFFFFF" stroke-width="1.5"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [28, 28], iconAnchor: [14, 14] })
}

/** Flecha azul hueca — viento 700 hPa en el eco causante */
function windArrowIcon(bearing) {
  const svg = `<svg width="22" height="22" viewBox="0 0 22 22" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing},11,11)">
      <polygon points="11,2 19,19 11,15 3,19" fill="none" stroke="${theme.primary}" stroke-width="2" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [22, 22], iconAnchor: [11, 11] })
}

/** Flecha pequeña azul — viento 700 hPa en punto intermedio de trayectoria */
function sampleArrowIcon(bearing) {
  const svg = `<svg width="14" height="14" viewBox="0 0 14 14" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing},7,7)">
      <polygon points="7,1 12,12 7,9 2,12" fill="none" stroke="${theme.primary}" stroke-width="1.5" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [14, 14], iconAnchor: [7, 7] })
}

/**
 * Flecha de dirección de campo — se muestra sobre los ecos de contexto.
 * Más grande y visible, con sombra blanca para legibilidad sobre la imagen.
 */
function fieldArrowIcon(bearing) {
  const svg = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing},16,16)">
      <polygon points="16,3 27,28 16,22 5,28" fill="${theme.orange}" stroke="#FFFFFF" stroke-width="2.5" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [32, 32], iconAnchor: [16, 16] })
}

/** Marcador de punto monitoreado */
function pointIcon(raining) {
  const color = raining ? theme.green : theme.primary
  const svg = `<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
    <circle cx="10" cy="10" r="7" fill="${color}" stroke="#FFFFFF" stroke-width="2"/>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [20, 20], iconAnchor: [10, 10] })
}

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------

/** Color de trayectoria según consistencia del viento a lo largo del camino */
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

/**
 * Selecciona hasta N posiciones donde mostrar flechas de dirección del campo.
 * Usa los ecos más fuertes espaciados al menos minDistKm entre sí.
 */
function selectArrowPositions(contextEchoes, n = 4, minDistKm = 40) {
  const selected = []
  for (const ce of contextEchoes) {
    if (selected.length >= n) break
    const tooClose = selected.some(s => {
      const dlat = (ce.lat - s.lat) * 111.32
      const dlon = (ce.lon - s.lon) * 111.32 * Math.cos(s.lat * Math.PI / 180)
      return Math.sqrt(dlat * dlat + dlon * dlon) < minDistKm
    })
    if (!tooClose) selected.push(ce)
  }
  return selected
}

// ---------------------------------------------------------------------------
// Ajuste de bounds
// ---------------------------------------------------------------------------

function BoundsFitter({ points }) {
  const map = useMap()
  useEffect(() => {
    if (!points || points.length === 0) return
    const lats = points.map(p => p.lat)
    const lons = points.map(p => p.lon)
    map.fitBounds([
      [Math.min(...lats) - 0.05, Math.min(...lons) - 0.05],
      [Math.max(...lats) + 0.05, Math.max(...lons) + 0.05],
    ], { padding: [20, 20] })
  }, [map, points])
  return null
}

// ---------------------------------------------------------------------------
// Componente principal
// ---------------------------------------------------------------------------

export default function CellMap({
  points        = [],
  nowcasts      = {},
  focusPoint    = null,
  rainviewerUrl = null,
  compact       = false,
  height        = "300px",
  contextEchoes = [],
  radarImageUrl = null,
  radarBounds   = null,
}) {
  const center = focusPoint
    ? [focusPoint.lat, focusPoint.lon]
    : points.length > 0 ? [points[0].lat, points[0].lon]
    : [20.68, -103.44]

  const rvTemplate  = rainviewerTemplate(rainviewerUrl)
  const leafletBounds = radarBounds
    ? [[radarBounds.south, radarBounds.west], [radarBounds.north, radarBounds.east]]
    : null

  // Flechas de dirección: hasta 4 posiciones distribuidas en el campo
  const hasMotion = contextEchoes.some(ce => ce.speed_kmh > 0)
  const arrowPositions = (!compact && hasMotion)
    ? selectArrowPositions(contextEchoes)
    : []

  const displayPoints = focusPoint
    ? points.filter(p => p.id === focusPoint.id)
    : points

  return (
    <MapContainer
      center={center}
      zoom={compact ? 10 : 10}
      style={{ height, width: "100%", borderRadius: compact ? "8px" : "12px", border: `1px solid ${theme.border}`, zIndex: 0 }}
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

      {/* Radar IAM como imagen georreferenciada (fondo transparente) */}
      {leafletBounds && radarImageUrl && (
        <ImageOverlay
          url={radarImageUrl}
          bounds={leafletBounds}
          opacity={0.75}
          zIndex={10}
        />
      )}

      {/* Fallback: capa RainViewer cuando el radar IAM no está disponible */}
      {!radarImageUrl && rvTemplate && (
        <TileLayer url={rvTemplate} opacity={0.6} attribution="RainViewer" />
      )}

      {/* Flechas de dirección del campo — posicionadas sobre los ecos más fuertes */}
      {arrowPositions.map((ce, i) => (
        <Marker key={`fa-${i}`} position={[ce.lat, ce.lon]} icon={fieldArrowIcon(ce.bearing_deg)}>
          <Tooltip>
            Campo: {Math.round(ce.bearing_deg)}° · {ce.speed_kmh.toFixed(0)} km/h
          </Tooltip>
        </Marker>
      ))}

      {/* Ajuste automático de bounds */}
      {!compact && !focusPoint && points.length > 1 && <BoundsFitter points={points} />}

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
        const echoPos   = [nw.cell_lat, nw.cell_lon]
        const ptPos     = [pt.lat, pt.lon]
        const flowBearing = nw.cell_bearing_deg ?? 0
        const lineColor = trajectoryColor(nw)

        return (
          <g key={`echo-${pt.id}`}>
            {/* Línea de trayectoria — color según consistencia del viento */}
            <Polyline
              positions={[echoPos, ptPos]}
              pathOptions={{ color: lineColor, weight: 2, dashArray: "6 4", opacity: 0.85 }}
            />

            {/* Flechas de viento en puntos intermedios */}
            {nw.trajectory_wind?.map((s, i) => (
              <Marker key={`tw-${pt.id}-${i}`} position={[s.lat, s.lon]} icon={sampleArrowIcon(s.toward_deg)}>
                {!compact && (
                  <Tooltip>Viento 700 hPa · {Math.round(s.toward_deg)}° · {s.speed_kmh.toFixed(0)} km/h</Tooltip>
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
