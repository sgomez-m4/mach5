import io
import functions_framework
import json
import re
import unicodedata
from google.cloud import storage
from google.cloud import bigquery

# Configuraciones fijas
BUCKET_NAME = "bucket-edgar"
DATASET_ID = "dataset_integrado"

CONFIG_DIM_AIRLINE = "config/dim_airline.json"

# Patrones de aircraft_type que representan un agregado del propio reporte
# (p.ej. "Total Fleet (Includes B737-800NG and E190)") o una cubeta sin detalle
# de modelo ("Commercial Aircraft"). Se conservan para el conteo de flota pero
# se marcan para poder excluirlas del analisis por tipo.
PATRONES_FILA_AGREGADA = [
    r"total\s+fleet",
    r"commercial\s+aircraft",
    r"mixed\s+models",
    r"^aircraft$",
    r"^fleet$",
]

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


def clave_aerolinea(nombre):
    """Normaliza un nombre de aerolinea para comparar: sin acentos, sin puntuacion,
    en minusculas. 'Aeroméxico Connect' y 'aeromexico connect' dan la misma clave."""
    if not nombre:
        return ""
    txt = unicodedata.normalize("NFKD", str(nombre))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return "".join(c for c in txt.lower() if c.isalnum())


# Sufijos que los extractores anexan al nombre del documento fuente. Un mismo
# filing puede terminar extraido dos veces bajo prefijos distintos -el 20-F de
# Aeromexico existe como "aeromex-2026-20f_Item2_filtrado" en 10k-item2-json/ y
# como "aeromex-2026-20f_Flota_filtrado" en 20f-flota-json/- y al recorrer los
# tres prefijos ambas lecturas se sumaban.
SUFIJOS_EXTRACCION = re.compile(r"(_item2|_item4|_flota|_filtrado|_filtered)+$", re.I)


def id_documento(nombre_blob):
    """Filing del que proviene una extraccion, sin prefijo, extension ni sufijos."""
    base = nombre_blob.rsplit("/", 1)[-1]
    base = re.sub(r"\.json$", "", base, flags=re.I)
    anterior = None
    while anterior != base:
        anterior = base
        base = SUFIJOS_EXTRACCION.sub("", base)
    return base.lower()


def riqueza_extraccion(data):
    """Criterio de desempate entre dos lecturas del mismo filing, de mayor a menor
    prioridad: que traiga filas de flota, que reporte edad, y que este mas
    desagregada. La edad pesa antes que el numero de filas porque es lo que
    habilita el analisis de reemplazo; de las dos lecturas del 20-F de Aeromexico
    solo una trae average_age_years."""
    flota = [r for r in (data.get("current_fleet") or []) if isinstance(r, dict)]
    pedidos = data.get("future_commitments") or []
    con_edad = sum(1 for r in flota if r.get("average_age_years") is not None)
    cobertura = (con_edad / float(len(flota))) if flota else 0.0
    return (1 if flota else 0, cobertura, len(flota), len(pedidos))


def cargar_indice_aerolineas(bucket):
    """Construye el indice clave_normalizada -> entidad desde config/dim_airline.json.

    Sin este indice el pipeline escribia el nombre literal que devolvia Gemini, que
    varia entre corridas ('United' vs 'United Airlines', 'Copa Holdings' vs
    'Copa Airlines'). Eso rompia el join con la dimension aguas abajo y fragmentaba
    grupos como American o Aeromexico en varias entidades.
    """
    blob = bucket.blob(CONFIG_DIM_AIRLINE)
    raw = blob.download_as_text(encoding="utf-8").strip().lstrip('\ufeff')
    cfg = json.loads(raw)

    indice = {}
    for a in cfg.get("airlines", []):
        entidad = {
            "airline_id": a["airline_id"],
            "group_id": a.get("parent_id") or a["airline_id"],
            "display_name": a["display_name"],
        }
        for variante in a.get("name_variants", []):
            indice[clave_aerolinea(variante)] = entidad
        indice[clave_aerolinea(a["display_name"])] = entidad
        indice[clave_aerolinea(a["airline_id"])] = entidad
    return indice


def es_fila_agregada(aircraft_type):
    """True si la fila es un agregado del reporte y no un tipo de aeronave real."""
    if not aircraft_type:
        return False
    txt = str(aircraft_type).lower()
    return any(re.search(p, txt) for p in PATRONES_FILA_AGREGADA)


def resolver_identidad(fila, indice, no_resueltos):
    """Añade airline_id / group_id / airline_canonical a la fila.

    Si el nombre no esta en el catalogo se conserva la fila y se registra el nombre
    en no_resueltos, para que aparezca como advertencia en la respuesta en vez de
    degradar en silencio.
    """
    nombre = (fila.get("airline") or "").strip()
    entidad = indice.get(clave_aerolinea(nombre))

    if entidad is None:
        if nombre:
            no_resueltos.add(nombre)
        fila["airline_id"] = None
        fila["group_id"] = None
        fila["airline_canonical"] = nombre or None
    else:
        fila["airline_id"] = entidad["airline_id"]
        fila["group_id"] = entidad["group_id"]
        fila["airline_canonical"] = entidad["display_name"]

    fila["es_fila_agregada"] = es_fila_agregada(fila.get("aircraft_type"))
    return fila


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

    indice_aerolineas = cargar_indice_aerolineas(bucket)
    print(f"[DIM] {len(indice_aerolineas)} claves de aerolinea cargadas del catalogo")
    
    current_rows = []
    commitment_rows = []
    aerolineas_no_resueltas = set()
    extracciones_descartadas = []
    
    # Estadísticas por fuente
    stats = {
        "archivos_leidos": 0,
        "extracciones_descartadas": 0,
        "archivos_con_error": 0,
        "filas_current_fleet": 0,
        "filas_order_book": 0,
        "aerolineas_detectadas": set()
    }
    
    # --- Fase 1: leer y parsear, agrupando por documento fuente ---------------
    extracciones = {}
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
            except json.JSONDecodeError as e:
                stats["archivos_con_error"] += 1
                print(f"  ✗ JSON inválido en {blob.name}: {e}")
                continue
            except Exception as e:
                stats["archivos_con_error"] += 1
                print(f"  ✗ Error leyendo {blob.name}: {type(e).__name__}: {e}")
                continue

            extracciones.setdefault(id_documento(blob.name), []).append((blob.name, data))

        print(f"  → {archivos_en_prefijo} archivos JSON encontrados en '{prefix}'")

    # --- Fase 2: un documento fuente, una sola extraccion ---------------------
    seleccionadas = []
    for doc, candidatas in sorted(extracciones.items()):
        if len(candidatas) > 1:
            candidatas = sorted(candidatas, key=lambda c: riqueza_extraccion(c[1]),
                                reverse=True)
            for descartada, _ in candidatas[1:]:
                extracciones_descartadas.append(descartada)
                print(f"  ⊘ {doc}: se descarta la extracción duplicada "
                      f"{descartada}; se conserva {candidatas[0][0]}")
        seleccionadas.append(candidatas[0])

    # --- Fase 3: normalizar las filas de las extracciones elegidas ------------
    for nombre_blob, data in seleccionadas:
        try:
            # Extraccion y limpieza de Flota Actual (current_fleet)
            if "current_fleet" in data and isinstance(data["current_fleet"], list):
                for r in data["current_fleet"]:
                    cleaned = limpiar_fila(r)
                    if cleaned:
                        cleaned = resolver_identidad(
                            cleaned, indice_aerolineas, aerolineas_no_resueltas
                        )
                        current_rows.append(cleaned)
                        # Trackear aerolinea ya canonizada
                        airline = cleaned.get("airline_canonical") or ""
                        if airline:
                            stats["aerolineas_detectadas"].add(airline)

            # Extraccion y limpieza de Compromisos Futuros (future_commitments)
            if "future_commitments" in data and isinstance(data["future_commitments"], list):
                for r in data["future_commitments"]:
                    cleaned = limpiar_fila(r)
                    if cleaned:
                        cleaned = resolver_identidad(
                            cleaned, indice_aerolineas, aerolineas_no_resueltas
                        )
                        commitment_rows.append(cleaned)

            stats["archivos_leidos"] += 1
            print(f"  ✓ Procesado: {nombre_blob}")

        except Exception as e:
            stats["archivos_con_error"] += 1
            print(f"  ✗ Error procesando {nombre_blob}: {type(e).__name__}: {e}")

    stats["filas_current_fleet"] = len(current_rows)
    stats["filas_order_book"] = len(commitment_rows)
    stats["extracciones_descartadas"] = len(extracciones_descartadas)
    
    print(f"\n[RESUMEN EXTRACCIÓN]")
    print(f"  Archivos leídos: {stats['archivos_leidos']}")
    print(f"  Documentos fuente: {len(extracciones)}")
    if extracciones_descartadas:
        print(f"  ⚠ {len(extracciones_descartadas)} extracciones duplicadas "
              f"descartadas: {sorted(extracciones_descartadas)}")
    print(f"  Archivos con error: {stats['archivos_con_error']}")
    print(f"  Filas current_fleet: {len(current_rows)}")
    print(f"  Filas order_book: {len(commitment_rows)}")
    print(f"  Aerolíneas detectadas: {sorted(stats['aerolineas_detectadas'])}")

    sin_id = sum(1 for r in current_rows + commitment_rows if not r.get("airline_id"))
    if aerolineas_no_resueltas:
        print(f"  ⚠ {len(aerolineas_no_resueltas)} nombres sin resolver contra el catalogo "
              f"({sin_id} filas afectadas): {sorted(aerolineas_no_resueltas)}")
        print("    → agregar la variante a config/dim_airline.json y resincronizar a GCS")
    else:
        print("  ✓ Todos los nombres resolvieron contra el catalogo")

    # Modo dry_run: solo reportar sin cargar
    if dry_run:
        return {
            "status": "dry_run",
            "mensaje": "Ejecución de prueba. No se cargó a BigQuery.",
            "filas_current_fleet": len(current_rows),
            "filas_order_book": len(commitment_rows),
            "aerolineas_detectadas": sorted(stats["aerolineas_detectadas"]),
            "archivos_leidos": stats["archivos_leidos"],
            "archivos_con_error": stats["archivos_con_error"],
            "aerolineas_sin_resolver": sorted(aerolineas_no_resueltas),
            "filas_sin_airline_id": sin_id,
            "extracciones_duplicadas_descartadas": sorted(extracciones_descartadas)
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
            "aerolineas_detectadas": sorted(stats["aerolineas_detectadas"]),
            "aerolineas_sin_resolver": sorted(aerolineas_no_resueltas),
            "filas_sin_airline_id": sin_id,
            "extracciones_duplicadas_descartadas": sorted(extracciones_descartadas)
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