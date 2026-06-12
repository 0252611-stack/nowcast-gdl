/**
 * Vista /admin — Panel de administración.
 * - Token admin (guardado en sessionStorage, no en código).
 * - Historial de predicciones verificadas.
 * - CRUD de puntos monitoreados.
 */

import { useState, useEffect, useCallback } from "react"
import { getPoints, getPredictions, createPoint, updatePoint, deletePoint } from "../api.js"

const OUTCOME_LABELS = {
  hit: { label: "Acierto", color: "#22c55e" },
  miss: { label: "Fallo", color: "#f97316" },
  false_alarm: { label: "Falsa alarma", color: "#eab308" },
  correct_negative: { label: "Negativo correcto", color: "#64748b" },
}

function fmtUtc(str) {
  if (!str) return "—"
  return new Date(str).toLocaleString("es-MX", {
    dateStyle: "short", timeStyle: "short", timeZone: "America/Mexico_City",
  })
}

// ---------------------------------------------------------------------------
// Sección de historial de predicciones
// ---------------------------------------------------------------------------

function PredictionsTable() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [filterPoint, setFilterPoint] = useState("")

  useEffect(() => {
    getPredictions({ limit: 100, pointId: filterPoint || undefined })
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [filterPoint])

  return (
    <div style={st.section}>
      <h3 style={st.sectionTitle}>Historial de predicciones</h3>
      <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
        <input
          style={st.input}
          placeholder="Filtrar por punto (id)…"
          value={filterPoint}
          onChange={e => { setFilterPoint(e.target.value); setLoading(true) }}
        />
      </div>
      {loading ? (
        <p style={st.muted}>Cargando…</p>
      ) : rows.length === 0 ? (
        <p style={st.muted}>Sin predicciones registradas.</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={st.table}>
            <thead>
              <tr>
                {["Punto", "Generada", "Método", "Predijo lluvia", "ETA (min)", "Resultado", "Error (min)"].map(h => (
                  <th key={h} style={st.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const oc = OUTCOME_LABELS[r.outcome]
                return (
                  <tr key={r.id} style={st.tr}>
                    <td style={st.td}>{r.point_id}</td>
                    <td style={st.td}>{fmtUtc(r.generated_at_utc)}</td>
                    <td style={st.td}><code style={st.code}>{r.method}</code></td>
                    <td style={st.td}>{r.predicted_rain ? "Sí" : "No"}</td>
                    <td style={st.td}>{r.eta_minutes ?? "—"}</td>
                    <td style={st.td}>
                      {oc ? (
                        <span style={{ color: oc.color, fontWeight: 600 }}>{oc.label}</span>
                      ) : r.verified_at_utc ? "—" : <span style={st.muted}>Pendiente</span>}
                    </td>
                    <td style={st.td}>{r.lead_time_error_min ?? "—"}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sección de CRUD de puntos
// ---------------------------------------------------------------------------

function PointsManager({ token }) {
  const [points, setPoints] = useState([])
  const [editing, setEditing] = useState(null)  // punto en edición o "new"
  const [form, setForm] = useState({ id: "", name: "", lat: "", lon: "" })
  const [msg, setMsg] = useState(null)

  const refresh = useCallback(() => {
    getPoints().then(setPoints).catch(() => {})
  }, [])

  useEffect(() => { refresh() }, [refresh])

  function startNew() {
    setEditing("new")
    setForm({ id: "", name: "", lat: "", lon: "" })
    setMsg(null)
  }

  function startEdit(pt) {
    setEditing(pt.id)
    setForm({ id: pt.id, name: pt.name, lat: String(pt.lat), lon: String(pt.lon) })
    setMsg(null)
  }

  function cancelEdit() {
    setEditing(null)
    setMsg(null)
  }

  async function handleSave() {
    if (!token) { setMsg({ ok: false, text: "Ingresa el token admin." }); return }
    const lat = parseFloat(form.lat)
    const lon = parseFloat(form.lon)
    if (!form.id || !form.name || isNaN(lat) || isNaN(lon)) {
      setMsg({ ok: false, text: "Completa todos los campos correctamente." })
      return
    }
    try {
      if (editing === "new") {
        await createPoint({ id: form.id, name: form.name, lat, lon }, token)
        setMsg({ ok: true, text: `Punto '${form.id}' creado.` })
      } else {
        await updatePoint(editing, { name: form.name, lat, lon }, token)
        setMsg({ ok: true, text: `Punto '${editing}' actualizado.` })
      }
      setEditing(null)
      refresh()
    } catch (e) {
      setMsg({ ok: false, text: e.message })
    }
  }

  async function handleDelete(id) {
    if (!token) { setMsg({ ok: false, text: "Ingresa el token admin." }); return }
    if (!window.confirm(`¿Eliminar el punto '${id}'?`)) return
    try {
      await deletePoint(id, token)
      setMsg({ ok: true, text: `Punto '${id}' eliminado.` })
      refresh()
    } catch (e) {
      setMsg({ ok: false, text: e.message })
    }
  }

  return (
    <div style={st.section}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
        <h3 style={st.sectionTitle}>Puntos monitoreados</h3>
        <button style={st.btnPrimary} onClick={startNew}>+ Nuevo punto</button>
      </div>

      {msg && (
        <div style={{ ...st.alert, color: msg.ok ? "#22c55e" : "#ef4444", borderColor: msg.ok ? "#22c55e44" : "#ef444444" }}>
          {msg.text}
        </div>
      )}

      {/* Formulario de alta/edición */}
      {editing && (
        <div style={st.form}>
          <div style={st.formRow}>
            <label style={st.label}>ID</label>
            <input
              style={st.input}
              value={form.id}
              onChange={e => setForm(f => ({ ...f, id: e.target.value }))}
              disabled={editing !== "new"}
              placeholder="slug_unico"
            />
          </div>
          <div style={st.formRow}>
            <label style={st.label}>Nombre</label>
            <input
              style={st.input}
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="Nombre del punto"
            />
          </div>
          <div style={st.formRow}>
            <label style={st.label}>Lat</label>
            <input
              style={st.input}
              value={form.lat}
              onChange={e => setForm(f => ({ ...f, lat: e.target.value }))}
              placeholder="20.6826"
              type="number"
              step="0.0001"
            />
          </div>
          <div style={st.formRow}>
            <label style={st.label}>Lon</label>
            <input
              style={st.input}
              value={form.lon}
              onChange={e => setForm(f => ({ ...f, lon: e.target.value }))}
              placeholder="-103.4420"
              type="number"
              step="0.0001"
            />
          </div>
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <button style={st.btnPrimary} onClick={handleSave}>Guardar</button>
            <button style={st.btnSecondary} onClick={cancelEdit}>Cancelar</button>
          </div>
        </div>
      )}

      {/* Tabla de puntos */}
      <div style={{ overflowX: "auto" }}>
        <table style={st.table}>
          <thead>
            <tr>
              {["ID", "Nombre", "Lat", "Lon", "Acciones"].map(h => (
                <th key={h} style={st.th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {points.map(pt => (
              <tr key={pt.id} style={st.tr}>
                <td style={st.td}><code style={st.code}>{pt.id}</code></td>
                <td style={st.td}>{pt.name}</td>
                <td style={st.td}>{pt.lat.toFixed(5)}</td>
                <td style={st.td}>{pt.lon.toFixed(5)}</td>
                <td style={st.td}>
                  <div style={{ display: "flex", gap: "6px" }}>
                    <button style={st.btnSmall} onClick={() => startEdit(pt)}>Editar</button>
                    <button style={{ ...st.btnSmall, color: "#ef4444", borderColor: "#ef444455" }} onClick={() => handleDelete(pt.id)}>
                      Eliminar
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Vista principal
// ---------------------------------------------------------------------------

export default function AdminView() {
  const [token, setToken] = useState(() => sessionStorage.getItem("admin_token") ?? "")
  const [showToken, setShowToken] = useState(false)

  function handleTokenChange(val) {
    setToken(val)
    sessionStorage.setItem("admin_token", val)
  }

  return (
    <div style={st.container}>
      <h2 style={st.pageTitle}>Panel de administración</h2>

      {/* Token */}
      <div style={st.tokenBox}>
        <label style={st.label}>Token admin</label>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <input
            style={{ ...st.input, maxWidth: "320px" }}
            type={showToken ? "text" : "password"}
            value={token}
            onChange={e => handleTokenChange(e.target.value)}
            placeholder="Pegar token aquí…"
          />
          <button style={st.btnSecondary} onClick={() => setShowToken(s => !s)}>
            {showToken ? "Ocultar" : "Mostrar"}
          </button>
        </div>
        <p style={{ ...st.muted, marginTop: "4px" }}>
          El token se guarda en sessionStorage (se borra al cerrar la pestaña).
        </p>
      </div>

      <PointsManager token={token} />
      <PredictionsTable />
    </div>
  )
}

const st = {
  container: { padding: "24px", maxWidth: "1100px", margin: "0 auto", width: "100%" },
  pageTitle: { fontSize: "18px", fontWeight: 700, color: "#e2e8f0", marginBottom: "24px" },
  section: { marginBottom: "32px" },
  sectionTitle: { fontSize: "15px", fontWeight: 700, color: "#e2e8f0", margin: "0 0 12px" },
  tokenBox: { background: "#1e293b", borderRadius: "10px", padding: "16px", marginBottom: "24px", border: "1px solid #273549" },
  form: { background: "#0f172a", border: "1px solid #273549", borderRadius: "8px", padding: "16px", marginBottom: "16px" },
  formRow: { display: "flex", alignItems: "center", gap: "12px", marginBottom: "10px" },
  label: { fontSize: "12px", color: "#64748b", minWidth: "52px", fontWeight: 600 },
  input: {
    background: "#0f172a", border: "1px solid #334155", borderRadius: "6px",
    color: "#e2e8f0", padding: "6px 10px", fontSize: "13px",
    outline: "none", width: "100%",
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: "13px" },
  th: { padding: "8px 12px", textAlign: "left", color: "#64748b", fontWeight: 600, fontSize: "11px", textTransform: "uppercase", borderBottom: "1px solid #273549" },
  tr: { borderBottom: "1px solid #1e293b" },
  td: { padding: "10px 12px", color: "#cbd5e1", verticalAlign: "middle" },
  code: { background: "#0f172a", borderRadius: "4px", padding: "1px 6px", color: "#38bdf8", fontSize: "12px" },
  muted: { color: "#475569", fontSize: "12px", margin: 0 },
  alert: { padding: "8px 12px", borderRadius: "6px", border: "1px solid", marginBottom: "12px", fontSize: "13px" },
  btnPrimary: {
    padding: "6px 14px", borderRadius: "6px", border: "1px solid #38bdf855",
    background: "#0c2a4a", color: "#38bdf8", fontSize: "12px", fontWeight: 600, cursor: "pointer",
  },
  btnSecondary: {
    padding: "6px 14px", borderRadius: "6px", border: "1px solid #334155",
    background: "transparent", color: "#94a3b8", fontSize: "12px", cursor: "pointer",
  },
  btnSmall: {
    padding: "4px 10px", borderRadius: "6px", border: "1px solid #334155",
    background: "transparent", color: "#94a3b8", fontSize: "11px", cursor: "pointer",
  },
}
