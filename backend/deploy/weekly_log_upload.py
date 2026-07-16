#!/usr/bin/env python3
"""Sube el JSONL de diagnostico a Google Cloud Storage (respaldo crudo) y lo
carga a BigQuery (consulta directa por SQL, sin descargar nada). Pensado
para correr por cron como usuario `nowcast`, una vez por semana.

No toca el JSONL original en ningun momento -- `_rotate_diag_log` en
scheduler.py sigue siendo la unica fuente de verdad para la retencion en el
disco de la VM (180 dias + tope de 2GB). Este script solo LEE una copia
comprimida y la sube; es seguro correrlo las veces que sea (GCS sube un
archivo con nombre distinto por fecha, BigQuery acumula filas por corrida
via WRITE_APPEND).

Credenciales: cuenta de servicio dedicada `nowcast-log-uploader`, con
permisos minimos (solo storage.objectCreator en el bucket + bigquery
dataEditor/jobUser en el proyecto) -- ver backend/deploy/README-gcp.md.
"""

from __future__ import annotations

import gzip
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery, storage
from google.oauth2 import service_account

KEY_FILE = Path("/etc/nowcast-gdl/gcs-key.json")
DIAG_LOG = Path("/opt/nowcast-gdl/data/logs/nowcast_diag.jsonl")
BUCKET_NAME = "nowcast-gdl-diag-logs-genial-post-502521-m2"
PROJECT_ID = "genial-post-502521-m2"
DATASET = "nowcast_diag"
TABLE = "cycles"


def _log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} weekly_log_upload: {msg}", flush=True)


def main() -> int:
    if not DIAG_LOG.exists() or DIAG_LOG.stat().st_size == 0:
        _log("log vacio o no existe, nada que subir.")
        return 0

    credentials = service_account.Credentials.from_service_account_file(str(KEY_FILE))

    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    gz_path = Path(f"/tmp/nowcast_diag_{date_tag}.jsonl.gz")
    with DIAG_LOG.open("rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    try:
        # --- Respaldo crudo en GCS ---
        storage_client = storage.Client(project=PROJECT_ID, credentials=credentials)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob_name = f"nowcast_diag_{date_tag}.jsonl.gz"
        bucket.blob(blob_name).upload_from_filename(str(gz_path))
        _log(f"subido a gs://{BUCKET_NAME}/{blob_name}")

        # --- Carga a BigQuery (append, con evolucion de esquema) ---
        bq_client = bigquery.Client(project=PROJECT_ID, credentials=credentials)
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            autodetect=True,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            schema_update_options=[
                bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION,
                bigquery.SchemaUpdateOption.ALLOW_FIELD_RELAXATION,
            ],
        )
        table_ref = f"{PROJECT_ID}.{DATASET}.{TABLE}"
        with gz_path.open("rb") as f:
            load_job = bq_client.load_table_from_file(f, table_ref, job_config=job_config)
        load_job.result()  # espera a que termine; lanza excepcion si falla
        _log(f"cargado a BigQuery {table_ref}: {load_job.output_rows} filas.")
    finally:
        gz_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
