/**
 * Mapa Leaflet reutilizable: puntos monitoreados + eco causante + flecha de dirección.
 * Props:
 *   points       — array de {id, name, lat, lon}
 *   nowcasts     — dict point_id → NowcastResult|null
 *   focusPoint   — {id, lat, lon} del punto principal (mini-mapa)
 *   rainviewerUrl — URL de tile RainViewer (para derivar la plantilla)
 *   compact      — true para mini-mapa sin controles
 *   height       — CSS height del mapa (default "300px")
 */

import { useEffect } from "react"
import { MapContainer, TileLayer, Marker, Polyline, CircleMarker, Tooltip, useMap } from "react-leaflet"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { theme } from "../theme.js"

// Corrige el icono por defecto de Leaflet (problema conocido con bundlers)
delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconUrl: new URL("leaflet/dist/images/marker-icon.png", import.meta.url).href,
  iconRetinaUrl: new URL("leaflet/dist/images/marker-icon-2x.png", import.meta.url).href,
  shadowUrl: new URL("leaflet/dist/images/marker-shadow.png", import.meta.url).href,
})

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

/** Icono SVG de flecha que apunta según el bearing dado (0=Norte, 90=Este...) */
function arrowIcon(bearing) {
  const svg = `<svg width="28" height="28" viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${bearing}, 14, 14)">
      <polygon points="14,3 24,24 14,19 4,24" fill="${theme.orange}" stroke="#FFFFFF" stroke-width="1.5"/>
    </g>
  </svg>`
  return L.divIcon({
    className: "",
    html: svg,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  })
}

/** Icono de marcador de punto monitoreado */
function pointIcon(raining) {
  const color = raining ? theme.green : theme.primary
  const svg = `<svg width="20" height="20" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
    <circle cx="10" cy="10" r="7" fill="${color}" stroke="#FFFFFF" stroke-width="2"/>
  </svg>`
  return L.divIcon({
    className: "",
    html: svg,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  })
}

/** Ajusta el mapa al bounds de los puntos cuando cambian */
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

export default function CellMap({
  points = [],
  nowcasts = {},
  focusPoint = null,
  rainviewerUrl = null,
  compact = false,
  height = "300px",
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
        <TileLayer
          url={rvTemplate}
          opacity={0.6}
          attribution='RainViewer'
        />
      )}

      {/* Ajustar bounds automáticamente en modo no compacto con varios puntos */}
      {!compact && !focusPoint && points.length > 1 && <BoundsFitter points={points} />}

      {/* Marcadores de puntos monitoreados */}
      {displayPoints.map(pt => {
        const nw = nowcasts[pt.id]
        return (
          <Marker
            key={pt.id}
            position={[pt.lat, pt.lon]}
            icon={pointIcon(nw?.raining_now)}
          >
            {!compact && <Tooltip>{pt.name}</Tooltip>}
          </Marker>
        )
      })}

      {/* Eco causante + flecha de dirección */}
      {(focusPoint ? [focusPoint] : points).map(pt => {
        const nw = nowcasts[pt.id]
        if (!nw || nw.cell_lat == null || nw.cell_lon == null) return null
        const echoPos = [nw.cell_lat, nw.cell_lon]
        const ptPos = [pt.lat, pt.lon]
        const bearing = nw.bearing_cell_to_point_deg ?? 0
        return (
          <g key={`echo-${pt.id}`}>
            {/* Círculo del eco */}
            <CircleMarker
              center={echoPos}
              radius={8}
              pathOptions={{ color: theme.orange, fillColor: theme.orange, fillOpacity: 0.5, weight: 2 }}
            >
              {!compact && (
                <Tooltip>
                  Eco causante · {nw.eta_minutes} min · {nw.cell_speed_kmh} km/h
                </Tooltip>
              )}
            </CircleMarker>

            {/* Flecha de dirección en el eco */}
            <Marker position={echoPos} icon={arrowIcon(bearing)} />

            {/* Línea del eco al punto */}
            <Polyline
              positions={[echoPos, ptPos]}
              pathOptions={{ color: theme.orange, weight: 2, dashArray: "6 4", opacity: 0.8 }}
            />
          </g>
        )
      })}
    </MapContainer>
  )
}
