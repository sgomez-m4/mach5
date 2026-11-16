import io
import functions_framework
import json
import re
from google.cloud import storage
from google.cloud import bigquery

from calidad_flota import (
    clave_aerolinea,
    elegir_entidad,
    entidad_por_fleet_id,
    grupo_documento,
    marcar_desgloses_alternativos,
    verificar_totales,
)

# Configuraciones fijas
BUCKET_NAME = "bucket-edgar"
DATASET_ID = "dataset_integrado"

CONFIG_DIM_AIRLINE = "config/dim_airline.json"
# Totales de flota que declara cada informe, ya conciliados. Si la extraccion no
# los reproduce, la carga se detiene antes de tocar BigQuery.
CONFIG_TOTALES = "config/totales_declarados.json"

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


def separar_ultimo_ejercicio(filas):
    """Devuelve (filas del ultimo ejercicio de cada aerolinea, ultimo anio por aerolinea).

    Varias tablas de flota publican dos o tres cierres anuales en columnas
    contiguas y la extraccion ahora emite una fila por ejercicio. Mezclarlos en
    current_fleet_fact contaria la misma aeronave varias veces en cualquier
    consumidor que no filtre por anio, asi que la tabla conserva solo el ultimo
    y la serie completa va a fleet_history_fact.

    La clave es la aerolinea y no el corpus entero: no todas reportan el mismo
    cierre, y tomar un maximo global dejaria fuera a las que van un anio atras.
    """
    ultimo = {}
    for fila in filas:
        clave = fila.get("airline_id") or fila.get("airline_canonical")
        anio = fila.get("report_year")
        if clave is None or anio is None:
            continue
        try:
            anio = int(anio)
        except (ValueError, TypeError):
            continue
        fila["report_year"] = anio
        if anio > ultimo.get(clave, 0):
            ultimo[clave] = anio

    vigentes = [
        f for f in filas
        if (f.get("airline_id") or f.get("airline_canonical")) is not None
        and f.get("report_year") == ultimo.get(f.get("airline_id") or f.get("airline_canonical"))
    ]
    return vigentes, ultimo


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


def cargar_totales_declarados(bucket):
    """Lee config/totales_declarados.json; None si no existe o no se puede leer."""
    try:
        raw = bucket.blob(CONFIG_TOTALES).download_as_text(encoding="utf-8")
        return json.loads(raw.strip().lstrip('﻿'))
    except Exception as e:
        print(f"[TOTALES] ✗ No se pudo leer {CONFIG_TOTALES}: {type(e).__name__}: {e}")
        return None


def es_fila_agregada(aircraft_type):
    """True si la fila es un agregado del reporte y no un tipo de aeronave real."""
    if not aircraft_type:
        return False
    txt = str(aircraft_type).lower()
    return any(re.search(p, txt) for p in PATRONES_FILA_AGREGADA)


def resolver_identidad(fila, indice, no_resueltos, grupo_doc=None, correcciones=None):
    """Añade airline_id / group_id / airline_canonical a la fila.

    Si el nombre no esta en el catalogo se conserva la fila y se registra el nombre
    en no_resueltos, para que aparezca como advertencia en la respuesta en vez de
    degradar en silencio.

    El nombre se contrasta con el prefijo del fleet_id y con el grupo dueño del
    documento (ver calidad_flota.elegir_entidad); si se corrige, se anota en
    `correcciones`.
    """
    nombre = (fila.get("airline") or "").strip()
    entidad_nombre = indice.get(clave_aerolinea(nombre))
    entidad_fleet = entidad_por_fleet_id(fila.get("fleet_id") or fila.get("order_id"), indice)
    entidad, motivo = elegir_entidad(entidad_nombre, entidad_fleet, grupo_doc)
    if motivo and correcciones is not None:
        correcciones.append({
            "fila": fila.get("fleet_id") or fila.get("order_id"),
            "airline_extraido": nombre,
            "airline_id_asignado": entidad["airline_id"],
            "motivo": motivo,
        })

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
      - forzar_carga: "true" para cargar aunque falle el control de calidad
        (solo a mano, despues de conciliar y actualizar la referencia)
    """
    print("[START] Iniciando pipeline de unificación y carga a BigQuery (10-K + 20-F + China)...")

    # Parámetros opcionales
    request_args = request.args or {}
    source_filter = request_args.get("source", "all").lower()
    dry_run = (request_args.get("dry_run", "false").lower() == "true")
    forzar_carga = (request_args.get("forzar_carga", "false").lower() == "true")
    
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
    correcciones_identidad = []
    filas_fuera_de_grupo = []
    documentos_sin_grupo = []
    desgloses_alternativos = []
    for nombre_blob, data in seleccionadas:
        grupo_doc = grupo_documento(nombre_blob, indice_aerolineas)
        if grupo_doc is None:
            documentos_sin_grupo.append(nombre_blob)
        try:
            # Extraccion y limpieza de Flota Actual (current_fleet)
            filas_doc = []
            if "current_fleet" in data and isinstance(data["current_fleet"], list):
                for r in data["current_fleet"]:
                    cleaned = limpiar_fila(r)
                    if cleaned:
                        cleaned = resolver_identidad(
                            cleaned, indice_aerolineas, aerolineas_no_resueltas,
                            grupo_doc, correcciones_identidad
                        )
                        filas_doc.append(cleaned)
                        # Trackear aerolinea ya canonizada
                        airline = cleaned.get("airline_canonical") or ""
                        if airline:
                            stats["aerolineas_detectadas"].add(airline)
            # Una misma flota publicada por familia y por modelo se cuenta una vez.
            desgloses_alternativos.extend(marcar_desgloses_alternativos(filas_doc))
            current_rows.extend(filas_doc)

            # Extraccion y limpieza de Compromisos Futuros (future_commitments)
            if "future_commitments" in data and isinstance(data["future_commitments"], list):
                for r in data["future_commitments"]:
                    cleaned = limpiar_fila(r)
                    if cleaned:
                        cleaned = resolver_identidad(
                            cleaned, indice_aerolineas, aerolineas_no_resueltas,
                            grupo_doc
                        )
                        commitment_rows.append(cleaned)

            # Filas que, tras la correccion, siguen atribuidas a otro grupo que
            # el dueño del filing: casi siempre una filial que el extractor
            # rotulo con el nombre de otra aerolinea (9 Air como China Express).
            if grupo_doc is not None:
                for f in filas_doc:
                    if f.get("group_id") and f["group_id"] != grupo_doc:
                        filas_fuera_de_grupo.append({
                            "documento": nombre_blob,
                            "grupo_documento": grupo_doc,
                            "fila": f.get("fleet_id"),
                            "airline_extraido": f.get("airline"),
                            "group_id_asignado": f["group_id"],
                            "report_year": f.get("report_year"),
                        })

            stats["archivos_leidos"] += 1
            print(f"  ✓ Procesado: {nombre_blob}")

        except Exception as e:
            stats["archivos_con_error"] += 1
            print(f"  ✗ Error procesando {nombre_blob}: {type(e).__name__}: {e}")

    # La serie completa se conserva aparte; las tablas de siempre se quedan con el
    # ultimo ejercicio de cada aerolinea, que es el contrato que consumen las
    # vistas y el modelo de Power BI.
    historia_rows = list(current_rows)
    current_rows, ultimo_ejercicio = separar_ultimo_ejercicio(current_rows)
    commitment_rows, _ = separar_ultimo_ejercicio(commitment_rows)
    anios_flota = sorted({f.get("report_year") for f in historia_rows
                          if f.get("report_year") is not None})

    stats["filas_current_fleet"] = len(current_rows)
    stats["filas_flota_historica"] = len(historia_rows)
    stats["anios_flota"] = anios_flota
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
    print(f"  Filas flota historica: {len(historia_rows)} (ejercicios {anios_flota})")
    print(f"  Filas order_book: {len(commitment_rows)}")
    print(f"  Aerolíneas detectadas: {sorted(stats['aerolineas_detectadas'])}")

    sin_id = sum(1 for r in current_rows + commitment_rows if not r.get("airline_id"))
    if aerolineas_no_resueltas:
        print(f"  ⚠ {len(aerolineas_no_resueltas)} nombres sin resolver contra el catalogo "
              f"({sin_id} filas afectadas): {sorted(aerolineas_no_resueltas)}")
        print("    → agregar la variante a config/dim_airline.json y resincronizar a GCS")
    else:
        print("  ✓ Todos los nombres resolvieron contra el catalogo")

    # --- Control de calidad antes de tocar BigQuery ---------------------------
    # WRITE_TRUNCATE reemplaza el estado conciliado entero, asi que una
    # extraccion que no reproduce los totales declarados no se carga.
    cfg_totales = cargar_totales_declarados(bucket)
    if cfg_totales is None:
        errores_totales = [f"No se pudo leer gs://{BUCKET_NAME}/{CONFIG_TOTALES}; "
                           "sin referencia no se reemplazan las tablas"]
        advertencias_totales, detalle_totales = [], {}
    else:
        errores_totales, advertencias_totales, detalle_totales = verificar_totales(
            current_rows, cfg_totales)

    # Una fila del ultimo ejercicio atribuida a un grupo ajeno al filing es un
    # error aunque ese grupo no tenga referencia de totales.
    vigentes_fuera = [
        c for c in filas_fuera_de_grupo
        if c["report_year"] is not None
        and int(c["report_year"]) == max(
            (int(f["report_year"]) for f in current_rows
             if f.get("group_id") == c["grupo_documento"]), default=-1)
    ]
    errores_calidad = list(errores_totales)
    for c in vigentes_fuera:
        errores_calidad.append(
            f"{c['documento']}: fila {c['fila']} ('{c['airline_extraido']}') atribuida a "
            f"{c['group_id_asignado']}, pero el filing es de {c['grupo_documento']}")

    calidad = {
        "errores": errores_calidad,
        "advertencias": advertencias_totales,
        "totales_por_grupo": detalle_totales,
        "correcciones_identidad": correcciones_identidad,
        "filas_fuera_del_grupo_del_documento": filas_fuera_de_grupo,
        "documentos_sin_grupo_identificado": sorted(documentos_sin_grupo),
        "desgloses_alternativos_marcados": desgloses_alternativos,
    }
    print(f"\n[CALIDAD] {len(errores_calidad)} errores, "
          f"{len(advertencias_totales)} advertencias, "
          f"{len(correcciones_identidad)} identidades corregidas, "
          f"{len(desgloses_alternativos)} desgloses alternativos marcados")
    for e in errores_calidad:
        print(f"  ✗ {e}")
    for a in advertencias_totales:
        print(f"  ⚠ {a}")

    # Modo dry_run: solo reportar sin cargar
    if dry_run:
        return {
            "status": "dry_run",
            "calidad": calidad,
            "mensaje": "Ejecución de prueba. No se cargó a BigQuery.",
            "filas_current_fleet": len(current_rows),
            "filas_flota_historica": len(historia_rows),
            "anios_flota": anios_flota,
            "filas_order_book": len(commitment_rows),
            "aerolineas_detectadas": sorted(stats["aerolineas_detectadas"]),
            "archivos_leidos": stats["archivos_leidos"],
            "archivos_con_error": stats["archivos_con_error"],
            "aerolineas_sin_resolver": sorted(aerolineas_no_resueltas),
            "filas_sin_airline_id": sin_id,
            "extracciones_duplicadas_descartadas": sorted(extracciones_descartadas),
            "anios_flota": anios_flota,
            "ultimo_ejercicio_por_aerolinea": {k: v for k, v in sorted(ultimo_ejercicio.items())}
        }, 200

    if errores_calidad and not forzar_carga:
        # 409 no entra en http.default_retry_predicate: el workflow falla en
        # fase6_unificar sin reintentar y las tablas quedan como estaban.
        print("[ABORTADO] La extraccion no reproduce el estado conciliado; "
              "no se carga a BigQuery (forzar_carga=true para cargar igual)")
        return {
            "status": "abortado_por_control_de_calidad",
            "mensaje": "No se cargó a BigQuery: la extracción no reproduce los totales "
                       "declarados o atribuye filas a un grupo ajeno al filing.",
            "calidad": calidad,
        }, 409

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

    if historia_rows:
        resultados["fleet_history_fact"] = cargar_a_bigquery(
            bq_client, historia_rows, f"{DATASET_ID}.fleet_history_fact"
        )
    else:
        resultados["fleet_history_fact"] = "Sin filas para cargar."

    return {
        "status": "Proceso completado (Fuentes: 10K + 20F + Aerolíneas Chinas)",
        "dry_run": False,
        "forzar_carga": forzar_carga,
        "calidad": calidad,
        "estadisticas": {
            "archivos_leidos": stats["archivos_leidos"],
            "archivos_con_error": stats["archivos_con_error"],
            "aerolineas_detectadas": sorted(stats["aerolineas_detectadas"]),
            "aerolineas_sin_resolver": sorted(aerolineas_no_resueltas),
            "filas_sin_airline_id": sin_id,
            "extracciones_duplicadas_descartadas": sorted(extracciones_descartadas),
            "anios_flota": anios_flota,
            "ultimo_ejercicio_por_aerolinea": {k: v for k, v in sorted(ultimo_ejercicio.items())}
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