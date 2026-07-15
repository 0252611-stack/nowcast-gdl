# Spec FINAL del módulo radar_iam — API completa verificada (10-jun-2026)

> Spec 100% verificada por inspección del sitio real. El módulo radar_iam.py
> debe seguirla exactamente — cero adivinanza.

> **Actualización 15-jul-2026:** el IAM empezó a redirigir `http://` →
> `https://` (301) en algún momento entre el 10-jun y el 15-jul — no es un
> cambio nuestro, es un endurecimiento del lado del servidor. Con
> `httpx.AsyncClient` sin `follow_redirects=True`, `response.raise_for_status()`
> lanza sobre CUALQUIER 3xx (no solo 4xx/5xx), así que cada ciclo fallaba en
> el primer request, antes de guardar cualquier lectura/predicción/diagnóstico
> — código correcto, pero apuntando a una URL que el servidor ya no acepta
> directamente. Fix: `API_URL`/`BASE_URL` en `radar_iam.py` ahora usan
> `https://` directamente (evita el redirect por completo, sin depender de
> `follow_redirects`). Las URLs de este documento se dejan como estaban
> (referencia histórica de la spec original) pero el código usa `https://`.

---

## API del IAM (descubierta por inspección, NO documentada — tratar como frágil)

### Endpoint de descubrimiento del KMZ vigente

```
POST http://iam.cucei.udg.mx/radar/iam/api/api_radar.php?tipo_solicitud=kmz_act
Content-Type: application/x-www-form-urlencoded
Body: radar=_ZH_&fecha=20260610
```

- `fecha`: **YYYYMMDD en UTC** (crítico — ver "trampa de medianoche" abajo)
- `radar`: `_ZH_` (reflectividad, la que usamos) o `_VR_` (velocidad radial)
- Respuesta exitosa (texto plano): `../kmz/20260610/MEXI_ZH_20260610_192501.kmz`
- Respuesta de error: contiene la cadena `error` (así lo detecta su propio JS)
- La ruta es relativa a `/radar/iam/api/` → URL absoluta del KMZ:
  `http://iam.cucei.udg.mx/radar/iam/kmz/20260610/MEXI_ZH_20260610_192501.kmz`

### Contenido del KMZ (es un ZIP)

- `MEX_ZH_{epoch_inicio}_{epoch_fin}.png` — imagen del radar (~15 KB);
  los números son epochs Unix del inicio/fin del escaneo
- `doc.kml` — KML GroundOverlay con bounds en `<LatLonBox>`

### Endpoint del boletín meteorológico (opcional, nice-to-have)

```
POST http://iam.cucei.udg.mx/radar/iam/api/api_radar.php?tipo_solicitud=actualiza_descrip
Body: actualiza=KEY_24T579236K2
```

Devuelve el texto del boletín escrito manualmente por los meteorólogos del
IAM. Mostrarlo en el dashboard da contexto humano gratis. (La key está
hardcodeada en el JS público del propio sitio; usarla solo para este fin.)

---

## ⚠️ La trampa de medianoche UTC

GDL es UTC-6 → **el día del API cambia a las 18:00 hora local**, en plena
hora de tormentas vespertinas de temporada. El cliente DEBE calcular la
fecha con `datetime.now(timezone.utc)`, NUNCA con hora local. Un bug aquí
deja ciego al sistema justo cuando más se necesita.
**Test obligatorio:** simular las 18:00–18:05 local y verificar que la
fecha enviada es la del día siguiente UTC.

---

## Bounds geográficos

Auto-extraer de `doc.kml` en CADA frame (el sistema se auto-calibra si el
IAM cambia la cobertura). Valores actuales de referencia:

```
Norte:  22.03030437021881   Sur:   19.32059531316582
Este:  -101.9462411978663   Oeste: -104.8254262826025
```

GDL centro (20.67, -103.35) cae cómodamente dentro del box. ✓

## Mapeo lat/lon → pixel

KML GroundOverlay mapea la imagen **linealmente en lat/lon (EPSG:4326)**.
NO usar Mercator, NO usar pyproj:

```python
x = (lon - west) / (east - west) * img_width
y = (north - lat) / (north - south) * img_height   # y crece hacia abajo
```

Tolerancia: error < 2 pixels (≈300 m). Validar contra fixtures.

## Flujo completo del cliente (radar_iam.py)

```python
import io, zipfile, httpx
from datetime import datetime, timezone

API = "http://iam.cucei.udg.mx/radar/iam/api/api_radar.php"
BASE = "http://iam.cucei.udg.mx/radar/iam/"

async def fetch_current_frame(client: httpx.AsyncClient):
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    r = await client.post(f"{API}?tipo_solicitud=kmz_act",
                          data={"radar": "_ZH_", "fecha": fecha})
    path = r.text.strip()
    if "error" in path.lower():
        raise RadarUnavailable(path)
    kmz_url = BASE + path.removeprefix("../")
    # Idempotencia: si kmz_url == último procesado → skip
    kmz = await client.get(kmz_url)
    z = zipfile.ZipFile(io.BytesIO(kmz.content))
    kml = z.read("doc.kml")          # parsear LatLonBox → bounds
    png_name = next(n for n in z.namelist() if n.endswith(".png"))
    png = z.read(png_name)
    return bounds_from_kml(kml), png, kmz_url
```

## Escala de color dBZ

Calibrar `colormap.py` contra el screenshot de la leyenda en
`backend/tests/fixtures/leyenda.png`. Rango: -31.5 (ruido) a 78.0 (granizo).
Categorías de la leyenda oficial: Ruido | Débil | Ligera | Moderada a
fuerte | Granizo. Umbral operativo para nowcasting: dBZ > 18.

## Parámetros operativos

- Radar actualiza cada **~90 segundos** → polling cada 90 s
- PNG ~15 KB → guardar blob en SQLite; retener 24 h (~960 frames)
- 3 fallos consecutivos del API → marcar capa radar "no disponible",
  degradar a solo Open-Meteo (+ fallback RainViewer)
- User-Agent identificable y polite (infraestructura universitaria):
  `NowcastGDL/0.1 (proyecto académico; contacto@email)`
- Carga sobre el servidor: 1 req/90s ≈ 960 req/día — trivial, ser buen ciudadano

## 🎁 Producto _VR_ (velocidad radial) — post-MVP

`radar=_VR_` devuelve velocidad radial Doppler: componente del viento
hacia/desde el radar por pixel. Tercera validación independiente del
movimiento de celdas (junto con optical flow y viento 700 hPa de
Open-Meteo). Implementar post-MVP: la velocidad radial solo mide la
componente radial y puede tener aliasing.

## Nota institucional

Si el proyecto madura, contactar al IAM (33-1378-5900 ext. 27810 y 27812,
Av. Vallarta 2602, Col. Arcos Vallarta). El IAM hizo públicos los datos
deliberadamente desde 2011 ("no tiene caso guardar los datos"). Una
colaboración formal UP–UdeG es plausible y deseable.
