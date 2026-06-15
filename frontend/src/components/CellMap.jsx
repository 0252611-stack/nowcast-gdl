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

import { useEffect, Fragment } from "react"
import {
  MapContainer, TileLayer, ImageOverlay,
  Marker, Polygon, Polyline, Rectangle, Tooltip, useMap,
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

/** Ray-casting: devuelve true si [lat, lon] está dentro del polígono `ring`. */
function pointInPolygon([lat, lon], ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [yi, xi] = ring[i]
    const [yj, xj] = ring[j]
    if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)
      inside = !inside
  }
  return inside
}

/** Centroide promedio de un anillo de polígono. */
function polygonCentroid(ring) {
  const n = ring.length
  return [ring.reduce((s, p) => s + p[0], 0) / n, ring.reduce((s, p) => s + p[1], 0) / n]
}

/**
 * Eco de contexto más cercano al centroide del anillo.
 * Fallback cuando no hay vectores locales del campo (ecos sin motion_field).
 */
function nearestContextEcho(ring, contextEchoes) {
  if (!contextEchoes.length) return null
  const [cLat, cLon] = polygonCentroid(ring)
  let best = null
  let bestDist = Infinity
  for (const ce of contextEchoes) {
    const d = Math.hypot(ce.lat - cLat, ce.lon - cLon)
    if (d < bestDist) { bestDist = d; best = ce }
  }
  return best
}

/**
 * Vector del campo más cercano a la posición (lat, lon) dentro del ring.
 * Devuelve null si no hay vectores disponibles o ninguno pasa el filtro.
 */
function nearestRingVector(lat, lon, vectors) {
  if (!vectors.length) return null
  let best = null
  let bestDist = Infinity
  for (const v of vectors) {
    const d = Math.hypot(v.lat - lat, v.lon - lon)
    if (d < bestDist) { bestDist = d; best = v }
  }
  return best
}

/**
 * Número de flechas interior proporcional al tamaño del eco:
 * ~1 flecha cada 0.08° de span, mín 1, máx 40.
 */
function echoArrowCount(ring) {
  const lats = ring.map(p => p[0])
  const lons = ring.map(p => p[1])
  const span = Math.max(Math.max(...lats) - Math.min(...lats), Math.max(...lons) - Math.min(...lons))
  return Math.max(1, Math.min(40, Math.round(span / 0.08) * 4))
}

/**
 * Devuelve hasta `maxArrows` posiciones [lat, lon] interiores al polígono `ring`
 * usando una grilla adaptativa al tamaño del eco. Siempre incluye el centroide.
 */
function echoArrowPositions(ring, maxArrows = 15) {
  const centroid = polygonCentroid(ring)
  const lats = ring.map(p => p[0])
  const lons = ring.map(p => p[1])
  const minLat = Math.min(...lats), maxLat = Math.max(...lats)
  const minLon = Math.min(...lons), maxLon = Math.max(...lons)
  const span = Math.max(maxLat - minLat, maxLon - minLon)

  // Ecos muy pequeños: solo el centroide
  if (span < 0.05) return [centroid]

  // Espaciado proporcional al span para distribuir flechas uniformemente
  const spacing = Math.max(0.04, span / Math.ceil(Math.sqrt(maxArrows)))
  const pts = [centroid]

  outer:
  for (let lat = minLat + spacing / 2; lat <= maxLat; lat += spacing) {
    for (let lon = minLon + spacing / 2; lon <= maxLon; lon += spacing) {
      if (pts.length >= maxArrows) break outer
      const pt = [lat, lon]
      const distToCentroid = Math.hypot(lat - centroid[0], lon - centroid[1])
      if (distToCentroid > spacing * 0.3 && pointInPolygon(pt, ring)) pts.push(pt)
    }
  }
  return pts
}

/**
 * Flecha de celda de malla — misma paleta de velocidad que el relleno de la celda.
 * Verde ≥30 km/h, ámbar ≥10, gris < 10 o sin datos.
 */
function meshCellArrowIcon(bearing, speed) {
  const color = speed >= 30 ? "#16A34A" : speed >= 10 ? "#D97706" : "#6B7280"
  const svg = `<svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing},8,8)">
      <polygon points="8,1 12,13 8,10 4,13" fill="${color}" stroke="#ffffff" stroke-width="1" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [16, 16], iconAnchor: [8, 8] })
}

/**
 * Genera celdas de malla tipo CAD con tamaño uniforme (~2.2 km por celda en GDL).
 * Usa celda de tamaño fijo en grados en lugar de n_side adaptativo, para que el mesh
 * tenga resolución consistente independientemente del tamaño del eco.
 * Compensa la diferencia lat/lon: a 20.7°N, 1°lon ≈ 0.935 * 1°lat.
 * Cota de rendimiento: máximo 900 celdas por contorno.
 */
function computeMeshCells(ring, vectors) {
  const lats = ring.map(p => p[0])
  const lons = ring.map(p => p[1])
  const minLat = Math.min(...lats), maxLat = Math.max(...lats)
  const minLon = Math.min(...lons), maxLon = Math.max(...lons)
  const centerLat = (minLat + maxLat) / 2

  // Tamaño de celda fijo en km → grados
  const CELL_KM  = 1.2
  const stepLat  = CELL_KM / 111.32
  const stepLon  = CELL_KM / (111.32 * Math.cos(centerLat * Math.PI / 180))

  const nLat = Math.max(3, Math.ceil((maxLat - minLat) / stepLat))
  const nLon = Math.max(3, Math.ceil((maxLon - minLon) / stepLon))

  // Si la cota de celdas se supera, escalar el paso uniformemente
  const MAX_CELLS = 2000
  const scale = nLat * nLon > MAX_CELLS ? Math.sqrt((nLat * nLon) / MAX_CELLS) : 1
  const sLat = stepLat * scale
  const sLon = stepLon * scale
  const nL   = Math.ceil((maxLat - minLat) / sLat)
  const nC   = Math.ceil((maxLon - minLon) / sLon)

  const cells = []
  for (let i = 0; i < nL; i++) {
    for (let j = 0; j < nC; j++) {
      const lat = minLat + (i + 0.5) * sLat
      const lon = minLon + (j + 0.5) * sLon
      if (!pointInPolygon([lat, lon], ring)) continue
      cells.push({
        bounds: [
          [minLat + i * sLat,       minLon + j * sLon],
          [minLat + (i + 1) * sLat, minLon + (j + 1) * sLon],
        ],
        centerLat: lat,
        centerLon: lon,
        vec: nearestRingVector(lat, lon, vectors),
      })
    }
  }
  return cells
}

/**
 * Escala secuencial de calidad 0→1: rojo (baja) → ámbar → verde (alta).
 * Accesible: además del color se muestra el número en el tooltip.
 */
function qualityColor(q) {
  if (q >= 0.7) return "#16A34A"   // verde — calidad alta
  if (q >= 0.4) return "#D97706"   // ámbar — calidad media
  return "#DC2626"                  // rojo — calidad baja
}

/** Flecha cuyo color refleja la calidad (quality score) de la celda */
function trackedCellArrowIcon(bearing, quality) {
  const color = qualityColor(quality)
  const svg = `<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Dirección celda">
    <g transform="rotate(${bearing},10,10)">
      <polygon points="10,2 17,17 10,13 3,17" fill="${color}" stroke="#FFFFFF" stroke-width="1.5" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [20, 20], iconAnchor: [10, 10] })
}

/** Flecha pequeña naranja — dirección de movimiento dentro del perímetro de un eco */
function echoMotionArrowIcon(bearing) {
  const svg = `<svg width="14" height="14" viewBox="0 0 14 14" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing},7,7)">
      <polygon points="7,1 11,12 7,9.5 3,12" fill="${theme.orange}" stroke="#FFFFFF" stroke-width="1" stroke-linejoin="round"/>
    </g>
  </svg>`
  return L.divIcon({ className: "", html: svg, iconSize: [14, 14], iconAnchor: [7, 7] })
}

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
  echoContours  = [],
  radarImageUrl = null,
  radarBounds   = null,
  trajectories  = [],   // polilíneas de trayectoria de eco [[lat,lon],…]
  showRadar        = true,
  showContours     = true,
  showArrows       = true,
  showPoints       = true,
  showMesh         = false,  // cuadrantes CAD + flechas (modo diagnóstico /malla)
  showFieldVectors = false,  // solo flechas del campo interior, sin rectángulos (toggle en /mapa)
  trackedCells     = [],     // list[TrackedCellSchema] — celdas rastreadas con identidad
  showCells        = false,  // toggle de la capa de celdas rastreadas
  rawDetections    = [],     // list[CellDetection] — detecciones crudas pre-tracking (debug)
  showRawDetections = false, // toggle de la capa de detecciones crudas
}) {
  const center = focusPoint
    ? [focusPoint.lat, focusPoint.lon]
    : points.length > 0 ? [points[0].lat, points[0].lon]
    : [20.68, -103.44]

  const rvTemplate  = rainviewerTemplate(rainviewerUrl)
  const leafletBounds = radarBounds
    ? [[radarBounds.south, radarBounds.west], [radarBounds.north, radarBounds.east]]
    : null

  // Fallback de dirección: viento 700 hPa del primer nowcast con esa data
  const windFallbackBearing = Object.values(nowcasts)
    .find(n => n?.wind_echo_bearing_deg != null)?.wind_echo_bearing_deg ?? null

  // ¿Hay al menos una señal de dirección disponible?
  const hasDirection = contextEchoes.some(ce => ce.speed_kmh > 1) || windFallbackBearing != null

  // Flechas de campo sobre contextEchoes (grilla de flechas grandes)
  const arrowPositions = (!compact && contextEchoes.length > 0 && hasDirection)
    ? selectArrowPositions(contextEchoes, 10, 25).map(ce => {
        const useWind = ce.speed_kmh <= 1 && windFallbackBearing != null
        return { ...ce, bearing_deg: useWind ? windFallbackBearing : ce.bearing_deg, _src: useWind ? "wind" : "flow" }
      })
    : []

  // Normalizar echoContours: acepta tanto el formato enriquecido {ring, vectors}
  // (desde /radar) como el formato plano number[][] (desde pasos de predicción).
  const normalizedContours = echoContours.map(c =>
    Array.isArray(c) ? { ring: c, vectors: [] } : c
  )

  // Posiciones de los ecos causantes (para colorear su contorno en naranja)
  const causantePositions = Object.values(nowcasts)
    .filter(nw => nw?.cell_lat != null && nw?.cell_lon != null)
    .map(nw => [nw.cell_lat, nw.cell_lon])

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
      {showRadar && leafletBounds && radarImageUrl && (
        <ImageOverlay
          url={radarImageUrl}
          bounds={leafletBounds}
          opacity={0.75}
          zIndex={10}
        />
      )}

      {/* Fallback: capa RainViewer cuando el radar IAM no está disponible */}
      {showRadar && !radarImageUrl && rvTemplate && (
        <TileLayer url={rvTemplate} opacity={0.6} attribution="RainViewer" />
      )}

      {/* Contornos de eco — naranja+grueso para el causante, slate+fino para los demás.
          Flechas interiores con vector local del campo (Opción B): cada flecha usa el
          vector muestreado por el backend más cercano a su posición; si no hay vectores
          (pasos de predicción) cae al eco de contexto más próximo o al viento 700 hPa. */}
      {showContours && !compact && normalizedContours.map(({ ring, vectors }, i) => {
        const isCausante = causantePositions.some(pos => pointInPolygon(pos, ring))
        const hasVectors = vectors.length > 0
        const nearEcho = !hasVectors ? nearestContextEcho(ring, contextEchoes) : null
        const fallbackBearing = (nearEcho?.speed_kmh > 1 ? nearEcho.bearing_deg : null) ?? windFallbackBearing
        const maxArrows = echoArrowCount(ring)
        const arrowPts = (!showMesh && (hasVectors || fallbackBearing != null))
          ? echoArrowPositions(ring, maxArrows)
          : []
        return (
          <Fragment key={`ec-${i}`}>
            <Polygon
              positions={ring}
              pathOptions={{
                color:   isCausante ? theme.orange : theme.text,
                weight:  isCausante ? 3 : 1.5,
                opacity: 0.85,
                fill:    false,
              }}
            />

            {/* Modo malla tipo CAD: cuadrantes rectangulares interiores al contorno.
                Relleno coloreado por velocidad + flecha al centro de cada celda.
                Siempre visible — gris sin datos, coloreado cuando hay campo de movimiento. */}
            {showMesh && computeMeshCells(ring, vectors).map(({ bounds, centerLat, centerLon, vec }, j) => {
              const speed   = vec?.speed_kmh ?? -1
              const bearing = vec?.bearing_deg ?? 0
              const color   = speed >= 30 ? "#16A34A" : speed >= 10 ? "#D97706" : "#64748B"
              const hasVec  = vec !== null
              return (
                <Fragment key={`mc-${i}-${j}`}>
                  <Rectangle
                    bounds={bounds}
                    pathOptions={{
                      color,
                      weight:       0.8,
                      opacity:      0.7,
                      fillColor:    color,
                      fillOpacity:  hasVec ? 0.18 : 0.06,
                      dashArray:    hasVec ? null : "2 3",
                    }}
                  >
                    <Tooltip>
                      <span style={{ fontSize: "11px" }}>
                        {hasVec
                          ? `${Math.round(bearing)}° · ${speed.toFixed(0)} km/h`
                          : "Sin campo de movimiento aún"
                        }
                      </span>
                    </Tooltip>
                  </Rectangle>
                  {hasVec && (
                    <Marker
                      position={[centerLat, centerLon]}
                      icon={meshCellArrowIcon(bearing, speed)}
                    />
                  )}
                </Fragment>
              )
            })}

            {/* Modo normal: grilla del frontend con interpolación al vector más cercano */}
            {!showMesh && arrowPts.map((pt, j) => {
              const vec = hasVectors ? nearestRingVector(pt[0], pt[1], vectors) : null
              const bearing = vec?.bearing_deg ?? fallbackBearing
              if (bearing == null) return null
              return <Marker key={`ea-${i}-${j}`} position={pt} icon={echoMotionArrowIcon(bearing)} />
            })}
          </Fragment>
        )
      })}

      {/* Vectores del campo interior — independiente de showContours.
          Solo aparecen dentro de los polígonos de eco; computeMeshCells
          filtra con pointInPolygon, garantizando que ninguna flecha quede
          fuera de los contornos de los ecos. */}
      {showFieldVectors && !compact && !showMesh && normalizedContours.map(({ ring, vectors }, i) => (
        <Fragment key={`fv-${i}`}>
          {computeMeshCells(ring, vectors).map(({ centerLat, centerLon, vec }, j) => {
            if (!vec) return null
            return (
              <Marker
                key={`fv-${i}-${j}`}
                position={[centerLat, centerLon]}
                icon={meshCellArrowIcon(vec.bearing_deg, vec.speed_kmh)}
              >
                <Tooltip>
                  <span style={{ fontSize: "11px" }}>
                    {Math.round(vec.bearing_deg)}° · {vec.speed_kmh.toFixed(0)} km/h
                  </span>
                </Tooltip>
              </Marker>
            )
          })}
        </Fragment>
      ))}

      {/* Trayectorias de ecos — polilíneas punteadas de t=0 a t=120 min */}
      {showContours && !compact && trajectories.map((traj, i) => (
        <Polyline
          key={`tr-${i}`}
          positions={traj}
          pathOptions={{ color: theme.primary, weight: 1.5, dashArray: "4 6", opacity: 0.6 }}
        />
      ))}

      {/* Detecciones crudas (pre-tracking) — polígonos tenues para debug */}
      {showRawDetections && !compact && rawDetections.map((det, i) => (
        <Fragment key={`det-${i}`}>
          {det.ring.length >= 3 && (
            <Polygon
              positions={det.ring}
              pathOptions={{
                color: "#94A3B8", weight: 1, opacity: 0.7,
                fill: true, fillColor: "#94A3B8", fillOpacity: 0.05,
                dashArray: "4 4",
              }}
            >
              <Tooltip>
                <div style={{ fontSize: "11px", lineHeight: 1.5 }}>
                  <strong>Detección cruda</strong><br />
                  {det.mean_dbz.toFixed(0)} dBZ · {det.area_px} px<br />
                  Solidity: {(det.solidity * 100).toFixed(0)}% · Extent: {(det.extent * 100).toFixed(0)}%
                </div>
              </Tooltip>
            </Polygon>
          )}
        </Fragment>
      ))}

      {/* Celdas rastreadas (Capa 2) — coloreadas por quality score */}
      {showCells && !compact && trackedCells.map(cell => {
        const q = typeof cell.quality === "number" ? cell.quality : 0
        const ringColor = qualityColor(q)
        return (
          <Fragment key={`tc-${cell.id}`}>
            {/* Polígono del anillo coloreado por quality */}
            <Polygon
              positions={cell.ring}
              pathOptions={{
                color:       ringColor,
                weight:      2,
                opacity:     0.9,
                fill:        true,
                fillColor:   ringColor,
                fillOpacity: 0.10,
              }}
            />

            {/* Trayectoria histórica de centroides */}
            {cell.track.length >= 2 && (
              <Polyline
                positions={cell.track}
                pathOptions={{ color: ringColor, weight: 2, dashArray: "3 5", opacity: 0.65 }}
              />
            )}

            {/* Flecha de velocidad + tooltip con quality */}
            <Marker
              position={[cell.lat, cell.lon]}
              icon={trackedCellArrowIcon(cell.bearing_deg, q)}
            >
              <Tooltip>
                <div style={{ fontSize: "12px", lineHeight: 1.6 }}>
                  <strong>Celda #{cell.id}</strong><br />
                  Calidad: {(q * 100).toFixed(0)}%<br />
                  {cell.mean_dbz.toFixed(0)} dBZ · {cell.velocity_kmh.toFixed(0)} km/h · {Math.round(cell.bearing_deg)}°<br />
                  Edad: {cell.age_minutes} min · {cell.area_px} px
                </div>
              </Tooltip>
            </Marker>
          </Fragment>
        )
      })}

      {/* Flechas de dirección del campo — posicionadas sobre los ecos más fuertes */}
      {showArrows && arrowPositions.map((ce, i) => (
        <Marker key={`fa-${i}`} position={[ce.lat, ce.lon]} icon={fieldArrowIcon(ce.bearing_deg)}>
          <Tooltip>
            {ce._src === "wind"
              ? `Viento 700 hPa: ${Math.round(ce.bearing_deg)}°`
              : `Campo: ${Math.round(ce.bearing_deg)}° · ${ce.speed_kmh.toFixed(0)} km/h`
            }
          </Tooltip>
        </Marker>
      ))}

      {/* Ajuste automático de bounds */}
      {!compact && !focusPoint && points.length > 1 && <BoundsFitter points={points} />}

      {/* Marcadores de puntos monitoreados */}
      {showPoints && displayPoints.map(pt => {
        const nw = nowcasts[pt.id]
        return (
          <Marker key={pt.id} position={[pt.lat, pt.lon]} icon={pointIcon(nw?.raining_now)}>
            {!compact && <Tooltip>{pt.name}</Tooltip>}
          </Marker>
        )
      })}

      {/* Eco causante + flechas + trayectoria */}
      {showPoints && (focusPoint ? [focusPoint] : points).map(pt => {
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

            {/* Flecha naranja sólida — optical flow (tooltip de eco causante) */}
            <Marker position={echoPos} icon={flowArrowIcon(flowBearing)}>
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
            </Marker>

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
