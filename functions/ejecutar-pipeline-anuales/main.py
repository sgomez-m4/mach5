import io
import functions_framework
import json
from google.cloud import storage
from google.cloud import bigquery

# Configuraciones fijas
BUCKET_NAME = "bucket-edgar"
DATASET_ID = "dataset_integrado"

# Lista de prefijos a procesar (10-K, 20-F y Aerolíneas Chinas)
SOURCE_PREFIXES = [
    "10k-item2-json/",
    "20f-flota-json/",
    "flota-aerolineas-json/"   # ← NUEVO: Aerolíneas chinas (A-Shares + H-Shares)
]

# Aerolíneas chinas esperadas (para validación)
CHINESE_AIRLINES = [
    "Air China",
    "China Eastern Airlines",
    "China Southern Airlines",
    "Cathay Pacific Airways",
    "Hainan Airlines",
    "Spring Airlines",
    "Juneyao Airlines",
    "China Express Airlines"
]


@functions_framework.http
def ejecutar_pipeline_flota(request):
    """
    Pipeline de unificación y carga a BigQuery para todas las fuentes de flota:
    - SEC 10-K (aerolíneas US)
    - SEC 20-F (aerolíneas extranjeras listadas en US)
    - Reportes anuales chinos (A-Shares y H-Shares)
    
    Query params opcionales:
      - source: Filtrar por fuente específica ("10k", "20f", "china", "all")
      - dry_run: "true" para solo contar filas sin cargar a BigQuery
    """
    print("[START] Iniciando pipeline de unificación y carga a BigQuery (10-K + 20-F + China)...")
    
    # Parámetros opcionales
    request_args = request.args or {}
    source_filter = request_args.get("source", "all").lower()
    dry_run = (request_args.get("dry_run", "false").lower() == "true")
    
    # Filtrar prefijos según el parámetro source
    prefixes_a_procesar = SOURCE_PREFIXES
    if source_filter == "10k":
        prefixes_a_procesar = ["10k-item2-json/"]
    elif source_filter == "20f":
        prefixes_a_procesar = ["20f-flota-json/"]
    elif source_filter == "china":
        prefixes_a_procesar = ["flota-aerolineas-json/"]
    
    storage_client = storage.Client()
    bq_client = bigquery.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    
    current_rows = []
    commitment_rows = []
    
    # Estadísticas por fuente
    stats = {
        "archivos_leidos": 0,
        "archivos_con_error": 0,
        "filas_current_fleet": 0,
        "filas_order_book": 0,
        "aerolineas_detectadas": set()
    }
    
    # Iterar por cada carpeta de origen
    for prefix in prefixes_a_procesar:
        print(f"[PROCESANDO CARPETA] Listando archivos en: {prefix}")
        blobs = bucket.list_blobs(prefix=prefix)
        
        archivos_en_prefijo = 0
        
        for blob in blobs:
            if not blob.name.endswith('.json'):
                continue
            
            archivos_en_prefijo += 1
                
            try:
                raw = blob.download_as_text(encoding="utf-8").strip().lstrip('\ufeff')
                data = json.loads(raw)
                
                # Extracción y limpieza de Flota Actual (current_fleet)
                if "current_fleet" in data and isinstance(data["current_fleet"], list):
                    for r in data["current_fleet"]:
                        cleaned = limpiar_fila(r)
                        if cleaned:
                            current_rows.append(cleaned)
                            # Trackear aerolínea
                            airline = cleaned.get("airline", "").strip()
                            if airline:
                                stats["aerolineas_detectadas"].add(airline)
                        
                # Extracción y limpieza de Compromisos Futuros (future_commitments)
                if "future_commitments" in data and isinstance(data["future_commitments"], list):
                    for r in data["future_commitments"]:
                        cleaned = limpiar_fila(r)
                        if cleaned:
                            commitment_rows.append(cleaned)
                
                stats["archivos_leidos"] += 1
                print(f"  ✓ Procesado: {blob.name}")
                
            except json.JSONDecodeError as e:
                stats["archivos_con_error"] += 1
                print(f"  ✗ JSON inválido en {blob.name}: {e}")
            except Exception as e:
                stats["archivos_con_error"] += 1
                print(f"  ✗ Error procesando {blob.name}: {type(e).__name__}: {e}")
        
        print(f"  → {archivos_en_prefijo} archivos JSON encontrados en '{prefix}'")

    stats["filas_current_fleet"] = len(current_rows)
    stats["filas_order_book"] = len(commitment_rows)
    
    print(f"\n[RESUMEN EXTRACCIÓN]")
    print(f"  Archivos leídos: {stats['archivos_leidos']}")
    print(f"  Archivos con error: {stats['archivos_con_error']}")
    print(f"  Filas current_fleet: {len(current_rows)}")
    print(f"  Filas order_book: {len(commitment_rows)}")
    print(f"  Aerolíneas detectadas: {sorted(stats['aerolineas_detectadas'])}")

    # Modo dry_run: solo reportar sin cargar
    if dry_run:
        return {
            "status": "dry_run",
            "mensaje": "Ejecución de prueba. No se cargó a BigQuery.",
            "filas_current_fleet": len(current_rows),
            "filas_order_book": len(commitment_rows),
            "aerolineas_detectadas": sorted(stats["aerolineas_detectadas"]),
            "archivos_leidos": stats["archivos_leidos"],
            "archivos_con_error": stats["archivos_con_error"]
        }, 200

    # Cargar a BigQuery
    resultados = {}
    
    if current_rows:
        resultados["current_fleet_fact"] = cargar_a_bigquery(
            bq_client, current_rows, f"{DATASET_ID}.current_fleet_fact"
        )
    else:
        resultados["current_fleet_fact"] = "Sin filas para cargar."
        
    if commitment_rows:
        resultados["order_book_fact"] = cargar_a_bigquery(
            bq_client, commitment_rows, f"{DATASET_ID}.order_book_fact"
        )
    else:
        resultados["order_book_fact"] = "Sin filas para cargar."

    return {
        "status": "Proceso completado (Fuentes: 10K + 20F + Aerolíneas Chinas)",
        "dry_run": False,
        "estadisticas": {
            "archivos_leidos": stats["archivos_leidos"],
            "archivos_con_error": stats["archivos_con_error"],
            "aerolineas_detectadas": sorted(stats["aerolineas_detectadas"])
        },
        "detalles_carga": resultados
    }, 200


def limpiar_fila(row: dict) -> dict | None:
    """
    Limpia una fila individual para asegurar compatibilidad con BigQuery.
    - Elimina saltos de línea en strings
    - Normaliza campos numéricos
    - Valida campos obligatorios
    """
    if not isinstance(row, dict):
        return None
    
    cleaned = {}
    
    for k, v in row.items():
        if v is None:
            cleaned[k] = None
        elif isinstance(v, str):
            # Limpiar saltos de línea, tabs y espacios extra
            cleaned[k] = v.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ').strip()
        elif isinstance(v, (int, float)):
            cleaned[k] = v
        else:
            # Convertir tipos inesperados a string
            cleaned[k] = str(v)
    
    # Validación mínima: debe tener al menos airline y aircraft_type
    if not cleaned.get("airline") or not cleaned.get("aircraft_type"):
        return None
    
    # Normalizar campos numéricos que podrían venir como string
    campos_numericos = ["quantity", "seating_capacity_avg", "quantity_committed"]
    for campo in campos_numericos:
        if campo in cleaned and isinstance(cleaned[campo], str):
            try:
                cleaned[campo] = int(cleaned[campo].replace(",", ""))
            except (ValueError, TypeError):
                cleaned[campo] = None
    
    campos_float = ["average_age_years", "remaining_lease_years"]
    for campo in campos_float:
        if campo in cleaned and isinstance(cleaned[campo], str):
            try:
                cleaned[campo] = float(cleaned[campo].replace(",", ""))
            except (ValueError, TypeError):
                cleaned[campo] = None
    
    return cleaned


def cargar_a_bigquery(bq_client, filas: list, tabla_completa_id: str) -> str:
    """
    Convierte las filas a formato NDJSON en bytes y carga a BigQuery.
    Usa WRITE_TRUNCATE para reconstruir la tabla completa con todas las fuentes.
    """
    
    # 1. Crear NDJSON con UTF-8 nativo (importante para caracteres chinos)
    ndjson_string = "\n".join(json.dumps(r, ensure_ascii=False) for r in filas) + "\n"
    
    # 2. Convertir a bytes UTF-8
    ndjson_bytes = ndjson_string.encode('utf-8')
    
    # 3. Envolver en BytesIO para streaming
    bytes_file = io.BytesIO(ndjson_bytes)
    
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
    )
    
    try:
        job = bq_client.load_table_from_file(
            bytes_file, 
            tabla_completa_id, 
            job_config=job_config
        )
        job.result()  # Esperar a que el Job de BigQuery finalice
        print(f"  [BQ] ✓ Cargadas {len(filas)} filas en {tabla_completa_id}")
        return f"Cargadas {len(filas)} filas exitosamente."
    except Exception as e:
        print(f"  [BQ] ✗ Error cargando en {tabla_completa_id}: {e}")
        return f"Error: {str(e)}"