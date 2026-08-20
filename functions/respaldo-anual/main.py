import json
from datetime import datetime

import functions_framework
from google.cloud import bigquery
from google.cloud import storage
from google.cloud.exceptions import NotFound

PROJECT_ID = "mach5-gemini-project"
BUCKET_NAME = "bucket-edgar"
DATASET_ORIGEN = "dataset_integrado"
DATASET_HISTORICO = "dataset_historico"

# Tablas de BigQuery a respaldar como SNAPSHOT (inmutables, cobran solo delta)
TABLAS_RESPALDO = ["current_fleet_fact", "order_book_fact"]

# Prefijos de aterrizaje que se re-crean en cada corrida del pipeline anual.
# Se archivan a archivo/backup/{fecha}/ y se limpia el origen para:
#  1) conservar los artefactos raw del año anterior, y
#  2) garantizar idempotencia (JSONs de años viejos no contaminan el WRITE_TRUNCATE).
PREFIJOS_ARCHIVO = [
    "10k-md/",
    "10k-item2-md/",
    "10k-item2-json/",
    "20f-md/",
    "20f-flota-md/",
    "20f-flota-json/",
    "anual-china/",
    "anual-china-md/",
    "flota-aerolineas-md/",
    "flota-aerolineas-json/",
    "financial_raw_10k/",
    "financial_raw_20f/",
    "financial_raw_china_a/",
    "financial_raw_china_h/",
    "financiera-normalizada-json/",
]


def crear_snapshots_bq(bq_client: bigquery.Client, fecha: str, dry_run: bool) -> list:
    """Crea SNAPSHOT TABLE inmutables por corrida (idempotente: borra y recrea)."""
    resultados = []
    for tabla in TABLAS_RESPALDO:
        nombre_snapshot = f"{tabla}_{fecha}"
        snapshot_id = f"{DATASET_HISTORICO}.{nombre_snapshot}"
        origen_id = f"{DATASET_ORIGEN}.{tabla}"

        # Idempotencia: si ya existe (re-ejecución del mismo día), se borra y recrea
        try:
            bq_client.get_table(snapshot_id)
            if dry_run:
                print(f"  [BQ][DRY] Snapshot existente {snapshot_id} (se borraría y recrearía)")
            else:
                bq_client.delete_table(snapshot_id)
                print(f"  [BQ] Snapshot existente {snapshot_id} eliminado (re-ejecución)")
        except NotFound:
            pass

        ddl = (
            f"CREATE SNAPSHOT TABLE `{PROJECT_ID}.{snapshot_id}` "
            f"CLONE `{PROJECT_ID}.{origen_id}`"
        )

        if dry_run:
            print(f"  [BQ][DRY] DDL: {ddl}")
            resultados.append({"tabla": snapshot_id, "estado": "dry_run"})
            continue

        try:
            job = bq_client.query(ddl)
            job.result()
            n = bq_client.get_table(snapshot_id).num_rows
            print(f"  [BQ] ✓ Snapshot {snapshot_id} creado ({n} filas)")
            resultados.append({"tabla": snapshot_id, "estado": "ok", "filas": n})
        except Exception as e:
            print(f"  [BQ] ✗ Error creando snapshot {snapshot_id}: {e}")
            resultados.append({"tabla": snapshot_id, "estado": "error", "error": str(e)})

    return resultados


def archivar_prefijos(bucket, fecha: str, dry_run: bool) -> dict:
    """Mueve los prefijos de aterrizaje a archivo/backup/{fecha}/ y limpia el origen."""
    resumen = {}
    for prefijo in PREFIJOS_ARCHIVO:
        blobs = list(bucket.list_blobs(prefix=prefijo))
        # Filtrar marcadores de "carpeta" (objeto vacío que termina en '/')
        archivos = [b for b in blobs if not b.name.endswith("/")]

        movidos = 0
        errores = 0
        for blob in archivos:
            destino = f"archivo/backup/{fecha}/{blob.name}"
            if dry_run:
                print(f"  [GCS][DRY] {blob.name} -> {destino}")
                movidos += 1
                continue
            try:
                bucket.copy_blob(blob, bucket, destino)
                blob.delete()
                movidos += 1
            except Exception as e:
                errores += 1
                print(f"  [GCS] ✗ Error moviendo {blob.name}: {e}")

        if dry_run:
            print(f"  [GCS][DRY] Prefijo {prefijo}: {movidos} archivos (simulación)")
        else:
            print(f"  [GCS] ✓ Prefijo {prefijo}: {movidos} archivos movidos, {errores} errores")

        resumen[prefijo] = {"archivos": movidos, "errores": errores}

    return resumen


@functions_framework.http
def ejecutar_respaldo_anual(request):
    """
    Fase 1 del pipeline anual: respaldo + archivo antes de re-procesar.

    Query params opcionales:
      - fecha:    YYYYMMDD a usar como sufijo (por defecto: fecha de hoy)
      - dry_run:  "true" para simular sin escribir (valida qué se movería)
    """
    print("[START] Respaldo anual: snapshots BigQuery + archivo de prefijos GCS...")

    request_args = request.args or {}
    fecha = request_args.get("fecha") or datetime.now().strftime("%Y%m%d")
    dry_run = request_args.get("dry_run", "false").lower() == "true"

    bq_client = bigquery.Client(project=PROJECT_ID)
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)

    # 1) Snapshots en dataset_historico
    snapshots = crear_snapshots_bq(bq_client, fecha, dry_run)

    # 2) Archivado de prefijos de aterrizaje
    archivo = archivar_prefijos(bucket, fecha, dry_run)

    # 3) Resumen
    errores_bq = [s for s in snapshots if s["estado"] == "error"]
    errores_gcs = sum(v["errores"] for v in archivo.values())
    status = "error" if (errores_bq or errores_gcs) else "ok"

    return (
        json.dumps(
            {
                "status": status,
                "fecha": fecha,
                "dry_run": dry_run,
                "snapshots": snapshots,
                "archivo_gcs": archivo,
                "errores_bq": len(errores_bq),
                "errores_gcs": errores_gcs,
            },
            ensure_ascii=False,
        ),
        200 if status == "ok" else 500,
        {"Content-Type": "application/json"},
    )