"""Cliente async para la API del radar IAM-UdeG.

Sigue docs/spec-radar-iam.md exactamente. No inventar endpoints ni formatos.
"""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from app import config

API_URL = "https://iam.cucei.udg.mx/radar/iam/api/api_radar.php"
BASE_URL = "https://iam.cucei.udg.mx/radar/iam/"


class RadarUnavailable(Exception):
    """La API del IAM devolvió un error o falló 3 veces consecutivas."""


def bounds_from_kml(kml_bytes: bytes) -> dict[str, float]:
    """Parsea el LatLonBox del doc.kml y devuelve
    {"north": float, "south": float, "east": float, "west": float}.

    Se llama en CADA frame para auto-calibración (no hardcodear bounds).
    """
    root = ET.fromstring(kml_bytes)

    # Handle KML namespace (the file uses http://earth.google.com/kml/2.2)
    ns_map = {
        "kml22": "http://earth.google.com/kml/2.2",
        "kml21": "http://www.opengis.net/kml/2.2",
    }

    box = None
    # Try with each known namespace
    for prefix, ns in ns_map.items():
        box = root.find(f".//{{{ns}}}LatLonBox")
        if box is not None:
            break

    # Fallback: no-namespace search
    if box is None:
        box = root.find(".//LatLonBox")

    if box is None:
        raise ValueError("LatLonBox not found in KML")

    def _text(tag: str) -> float:
        # Try namespaced first, then bare
        for prefix, ns in ns_map.items():
            el = box.find(f"{{{ns}}}{tag}")
            if el is not None and el.text:
                return float(el.text.strip())
        el = box.find(tag)
        if el is not None and el.text:
            return float(el.text.strip())
        raise ValueError(f"Tag <{tag}> not found in LatLonBox")

    return {
        "north": _text("north"),
        "south": _text("south"),
        "east": _text("east"),
        "west": _text("west"),
    }


async def fetch_current_frame(
    client: httpx.AsyncClient,
    last_kmz_url: str | None = None,
) -> tuple[dict[str, float], bytes, str]:
    """Descarga el KMZ vigente del IAM y devuelve (bounds, png_bytes, kmz_url).

    Usa datetime.now(timezone.utc) para la fecha — NUNCA hora local.
    Si la respuesta contiene "error" → lanza RadarUnavailable.
    Si kmz_url == last_kmz_url → lanza RadarUnavailable (idempotencia, skip).
    User-Agent: config.USER_AGENT.
    Timeout: 10 s, sin retries agresivos.
    """
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")

    headers = {"User-Agent": config.USER_AGENT}

    response = await client.post(
        API_URL,
        params={"tipo_solicitud": "kmz_act"},
        data={"radar": "_ZH_", "fecha": fecha},
        headers=headers,
        timeout=10.0,
    )
    response.raise_for_status()

    body = response.text.strip()

    if "error" in body.lower():
        raise RadarUnavailable(f"API returned error: {body}")

    # Convert relative path to absolute URL
    # Response looks like: ../kmz/20260610/MEXI_ZH_20260610_192501.kmz
    # Absolute URL: BASE_URL + path without leading ../
    relative_path = body.lstrip("./").lstrip("/")
    # body might be "../kmz/..." → strip leading "../"
    if body.startswith("../"):
        relative_path = body[3:]
    kmz_url = BASE_URL + relative_path

    if kmz_url == last_kmz_url:
        raise RadarUnavailable("same frame, skip")

    # Download the KMZ file
    kmz_response = await client.get(
        kmz_url,
        headers=headers,
        timeout=10.0,
    )
    kmz_response.raise_for_status()

    kmz_bytes = kmz_response.content

    # KMZ is a ZIP file containing a PNG and doc.kml
    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as zf:
        names = zf.namelist()

        # Find the PNG file
        png_name = next((n for n in names if n.lower().endswith(".png")), None)
        if png_name is None:
            raise RadarUnavailable("No PNG found in KMZ")

        # Find the KML file
        kml_name = next(
            (n for n in names if n.lower().endswith(".kml")), None
        )
        if kml_name is None:
            raise RadarUnavailable("No KML found in KMZ")

        png_bytes = zf.read(png_name)
        kml_bytes = zf.read(kml_name)

    bounds = bounds_from_kml(kml_bytes)

    return bounds, png_bytes, kmz_url
