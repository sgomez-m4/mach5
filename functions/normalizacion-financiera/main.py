import functions_framework
import io
import json
import re
import time
from datetime import datetime, date

from google.cloud import storage
from google.cloud import bigquery

# =========================================================
# CONFIGURACION
# =========================================================
PROJECT_ID = "mach5-gemini-project"
BUCKET_NAME = "bucket-edgar"
DATASET_ID = "dataset_integrado"
CONFIG_METRICS_BLOB = "config/financial_metrics.json"

SOURCE_PREFIXES = [
    "financial_raw_10k/",
    "financial_raw_20f/",
    "financial_raw_china_a/",
    "financial_raw_china_h/",
]

TARGET_PREFIX = "financiera-normalizada-json/"


# =========================================================
# CARGAR CONFIGURACIONES
# =========================================================
def cargar_config_metrics():
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(CONFIG_METRICS_BLOB)
    raw = blob.download_as_text(encoding="utf-8").strip().lstrip('\ufeff')
    return json.loads(raw)


def cargar_crosswalk():
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob("config/xbrl_crosswalk.json")
    raw = blob.download_as_text(encoding="utf-8").strip().lstrip('\ufeff')
    data = json.loads(raw)
    # Lista completa; un (taxonomy, source_tag) puede mapear a varias metricas
    return data


def crosswalk_lookup(crosswalk, taxonomy, source_tag):
    """Devuelve las entradas del crosswalk que coinciden con el fact."""
    return [
        item for item in crosswalk
        if item.get("taxonomy") == taxonomy and item.get("source_tag") == source_tag
    ]


def cargar_fx_rates():
    """Carga la tabla fx_rates de BigQuery y devuelve dict (currency, anio) -> rate."""
    bq_client = bigquery.Client(project=PROJECT_ID)
    query = f"""
        SELECT currency, rate_date, rate_to_usd
        FROM `{PROJECT_ID}.{DATASET_ID}.fx_rates`
    """
    result = bq_client.query(query).result()
    rates = {}
    for row in result:
        anio = row.rate_date.year
        rates[(row.currency, anio)] = row.rate_to_usd
    return rates


# =========================================================
# LECTURA DE JSON RAW
# =========================================================
def leer_todos_raw():
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    registros = []

    for prefix in SOURCE_PREFIXES:
        blobs = list(bucket.list_blobs(prefix=prefix))
        for blob in blobs:
            if not blob.name.endswith(".json"):
                continue
            try:
                contenido = blob.download_as_text(encoding="utf-8").strip().lstrip('\ufeff')
                # Puede ser un array JSON o NDJSON
                if contenido.startswith("["):
                    data = json.loads(contenido)
                    registros.extend(data)
                else:
                    for linea in contenido.splitlines():
                        linea = linea.strip()
                        if linea:
                            registros.append(json.loads(linea))
            except Exception as e:
                print(f"  ✗ Error leyendo {blob.name}: {e}")
        print(f"  → {prefix}: {len([r for r in registros if True])} registros totales acumulados")

    return registros


# =========================================================
# NORMALIZACION
# =========================================================
def convertir_a_usd_millions(registro, fx_rates):
    """
    Convierte raw_value a USD millions.
    - Para XBRL US (unit USD, taxonomies us-gaap): el valor suele estar en USD reales.
      Se divide por 1e6 para millones. Si la unidad trae 'M' o el valor es enorme, se
      normaliza.
    - Para otras monedas: multiplica por fx_rate y divide por 1e6.
    """
    currency = registro.get("currency", "USD")
    raw_value = registro.get("raw_value")
    unit = registro.get("unit", currency)

    if raw_value is None:
        return None

    try:
        valor = float(raw_value)
    except (ValueError, TypeError):
        return None

    # Escala implicita en la unidad (edgartools a veces reporta en millones)
    escala = 1.0
    unit_lower = str(unit).lower()
    if any(s in unit_lower for s in ["millions", "m ", "mm", "million", "1000000"]):
        escala = 1e6
    elif any(s in unit_lower for s in ["thousands", "k "]):
        escala = 1e3
    elif "billion" in unit_lower:
        escala = 1e9

    # Si el valor crudo ya parece estar en millones (magnitud grande para estos tags),
    # no re-escalar. Heuristica: si unit dice "USD" puro y valor > 1e9, dividir por 1e6.
    # De lo contrario asumimos que el valor ya viene en la unidad base.
    valor_en_moneda = valor * escala if escala != 1.0 else valor

    if currency == "USD":
        valor_usd = valor_en_moneda
    else:
        anio = registro.get("fiscal_year")
        rate = fx_rates.get((currency, anio))
        if rate is None:
            # Fallback: la tasa mas cercana disponible para esa moneda
            candidatos = {k[1]: v for k, v in fx_rates.items() if k[0] == currency}
            if candidatos:
                anio_cercano = min(candidatos, key=lambda a: abs(a - anio))
                rate = candidatos[anio_cercano]
            else:
                print(f"    ⚠ Sin fx_rate para {currency} {anio}")
                return None
        valor_usd = valor_en_moneda * rate

    # Normalizar a millones de USD
    # Si unit ya era millones -> valor_en_moneda esta en millones; else es en unidades, /1e6
    if escala == 1.0:
        valor_usd_m = valor_usd / 1e6
    else:
        valor_usd_m = valor_usd

    return round(valor_usd_m, 4)


def resolver_formula(expr, valores):
    """
    Resuelve una formula simple tipo "op + dep" sobre dict de metricas canonicas.
    """
    expr_limpia = expr.replace(" ", "")
    # Sustituir nombres de metricas por su valor
    for nombre in sorted(valores.keys(), key=len, reverse=True):
        if nombre in expr_limpia:
            expr_limpia = expr_limpia.replace(nombre, f"({valores[nombre]})")
    expr_limpia = re.sub(r'[^0-9+\-*/().]', '', expr_limpia)
    if not expr_limpia or expr_limpia in ("()", ""):
        return None
    try:
        return eval(expr_limpia)  # solo aritmetica sobre numeros saneados
    except Exception:
        return None


@functions_framework.http
def normalizar_financiera(request):
    t0 = time.time()
    print(f"[START] Normalizacion financiera: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    request_args = request.args or {}
    dry_run = (request_args.get("dry_run", "false").lower() == "true")

    config = cargar_config_metrics()
    crosswalk = cargar_crosswalk()
    fx_rates = cargar_fx_rates()
    metrics_def = config["metrics"]

    print(f"[CONFIG] {len(crosswalk)} mappings crosswalk | {len(metrics_def)} metricas definidas")

    # 1. Leer todos los raw
    raw_registros = leer_todos_raw()
    print(f"[RAW] {len(raw_registros)} registros crudos leidos")

    # 2. Normalizar cada registro -> fila financial_fact_raw
    filas_raw = []
    # Acumulador para financial_fact: (company, fiscal_year, canonical_metric) -> valor_usd_m
    acumulado = {}
    source_tags_por_metrica = {}
    data_source_por_metrica = {}
    unmapped = []

    for r in raw_registros:
        mappings = crosswalk_lookup(crosswalk, r.get("taxonomy"), r.get("source_tag"))
        if not mappings:
            unmapped.append(f"{r.get('taxonomy')}:{r.get('source_tag')}")
            continue

        valor_usd_m = convertir_a_usd_millions(r, fx_rates)
        if valor_usd_m is None:
            continue

        filas_raw.append({
            "company": r.get("company"),
            "fiscal_year": r.get("fiscal_year"),
            "taxonomy": r.get("taxonomy"),
            "source_tag": r.get("source_tag"),
            "raw_value": r.get("raw_value"),
            "unit": r.get("unit"),
            "currency": r.get("currency"),
            "period": r.get("period"),
            "context_ref": r.get("context_ref"),
            "data_source": r.get("data_source", "xbrl_tag"),
            "period_end_date": r.get("period_end_date"),
            "extraction_ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "raw_json": r.get("raw_json"),
        })

        src = r.get("data_source", "xbrl_tag")
        for m in mappings:
            canonical = m["canonical_metric"]
            clave_acum = (r.get("company"), r.get("fiscal_year"), canonical)
            acumulado.setdefault(clave_acum, 0.0)
            acumulado[clave_acum] += valor_usd_m
            source_tags_por_metrica.setdefault(clave_acum, [])
            if src not in data_source_por_metrica or data_source_por_metrica[clave_acum] == "xbrl_tag":
                data_source_por_metrica[clave_acum] = src
            if r.get("source_tag") not in source_tags_por_metrica[clave_acum]:
                source_tags_por_metrica[clave_acum].append(r.get("source_tag"))

    print(f"[MAPEO] {len(filas_raw)} filas raw mapeadas | {len(unmapped)} tags sin mapping")

    # 3. Calcular metricas derivadas y construir financial_fact
    filas_fact = []
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Primero las directas (xbrl/gemini)
    for (company, anio, metric), valor in sorted(acumulado.items()):
        filas_fact.append({
            "company": company,
            "fiscal_year": anio,
            "canonical_metric": metric,
            "value_usd_m": round(valor, 4),
            "data_source": data_source_por_metrica.get((company, anio, metric), "xbrl_tag"),
            "formula_used": None,
            "source_tags": source_tags_por_metrica.get((company, anio, metric), []),
            "extraction_ts": ts,
        })

    # Ahora las derivadas (calculated)
    claves_con_valor = {}
    for (company, anio, metric), valor in acumulado.items():
        claves_con_valor.setdefault((company, anio), {})[metric] = valor

    for (company, anio), valores in claves_con_valor.items():
        for metric, def_metrica in metrics_def.items():
            formula = def_metrica.get("formula")
            if not formula:
                continue
            resultado = resolver_formula(formula, valores)
            if resultado is None:
                print(f"    ⚠ No se pudo calcular {metric} para {company} {anio} (dependencias incompletas)")
                continue
            filas_fact.append({
                "company": company,
                "fiscal_year": anio,
                "canonical_metric": metric,
                "value_usd_m": round(resultado, 4),
                "data_source": "calculated",
                "formula_used": formula,
                "source_tags": [],
                "extraction_ts": ts,
            })

    print(f"[RESULTADO] {len(filas_fact)} filas canonicales | {len(filas_raw)} filas raw")

    # 4. Subir JSON normalizado a GCS y cargar a BQ
    if not dry_run:
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)

        # JSON de resumen por empresa
        resumen_blob = bucket.blob(f"{TARGET_PREFIX}resumen_normalizado_{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
        resumen_blob.upload_from_string(
            json.dumps({"filas_fact": filas_fact, "filas_raw": filas_raw, "unmapped": unmapped}, ensure_ascii=False),
            content_type="application/json",
        )

        bq_client = bigquery.Client(project=PROJECT_ID)

        def cargar(tabla, filas):
            if not filas:
                return "Sin filas"
            ndjson = "\n".join(json.dumps(r, ensure_ascii=False) for r in filas) + "\n"
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                autodetect=True,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            job = bq_client.load_table_from_file(io.BytesIO(ndjson.encode("utf-8")), tabla, job_config=job_config)
            job.result()
            return f"Cargadas {len(filas)} filas"

        r1 = cargar(f"{DATASET_ID}.financial_fact_raw", filas_raw)
        r2 = cargar(f"{DATASET_ID}.financial_fact", filas_fact)
        print(f"[BQ] raw: {r1} | fact: {r2}")
    else:
        r1 = "dry_run"
        r2 = "dry_run"

    t_total = round(time.time() - t0, 2)
    print(f"[END] Finalizado en {t_total}s")

    return {
        "status": "ok",
        "dry_run": dry_run,
        "total_registros_raw": len(filas_raw),
        "total_filas_fact": len(filas_fact),
        "tags_sin_mapping": len(unmapped),
        "unmapped_sample": sorted(set(unmapped))[:20],
        "carga_raw": r1,
        "carga_fact": r2,
        "tiempo_segundos": t_total,
    }, 200