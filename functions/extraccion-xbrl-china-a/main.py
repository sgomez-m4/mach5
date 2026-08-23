import functions_framework
import json
import re
import time
from datetime import datetime

from google.cloud import storage
from google import genai
from google.genai import types

# =========================================================
# CONFIGURACION
# =========================================================
PROJECT_ID = "mach5-gemini-project"
LOCATION = "global"
MODEL_NAME = "gemini-3-flash-preview"
BUCKET_NAME = "bucket-edgar"
SOURCE_PREFIX = "anual-china-md/"
TARGET_PREFIX = "financial_raw_china_a/"
CONFIG_BLOB = "config/xbrl_crosswalk.json"
TAXONOMY = "cas-china"

# =========================================================
# AEROLINEAS A-SHARE (catalogo de descarga-anual-china)
# =========================================================
AEROLINEAS = [
    {"company": "Air China", "code": "601111", "market": "A-Share"},
    {"company": "China Eastern Airlines", "code": "600115", "market": "A-Share"},
    {"company": "China Southern Airlines", "code": "600029", "market": "A-Share"},
    {"company": "Hainan Airlines", "code": "600221", "market": "A-Share"},
    {"company": "Spring Airlines", "code": "601021", "market": "A-Share"},
    {"company": "Juneyao Airlines", "code": "603885", "market": "A-Share"},
    {"company": "China Express Airlines", "code": "002928", "market": "A-Share"},
]

SYSTEM_INSTRUCTION = """
Actúa como experto en contabilidad CAS (Chinese Accounting Standards) y datos financieros de aerolíneas.
Tu tarea es extraer las métricas financieras de reportes anuales de aerolíneas chinas A-Share
y devolver estrictamente un JSON válido.

El texto proviene de un reporte anual (年度报告) que incluye estados financieros: balance (资产负债表),
estado de resultados (利润表) y flujo de efectivo (现金流量表). Los valores pueden estar en
yuanes (CNY), con escalas de 元, 万元 (10k), 亿元 (100M).

### Esquema de salida
Devuelve un objeto JSON con claves = tags financieros CAS (sin prefijo), valores = objeto con:
- value: número (siempre en CNY yuanes, expandiendo la escala indicada)
- scale: "yuan", "wan" (万元), "yi" (亿元) según el reporte
- period: "FY" si es del año completo, "instant" si es saldo al cierre
- context_ref: "consolidated" o "parent"

Solo incluye tags que encuentres con su valor numérico. NO inventes valores.

Tags relevantes (busca por estos conceptos chinos en el reporte):
- 营业收入 / 营业总收入  -> revenue
- 营业成本                 -> cost_of_revenue
- 营业利润                 -> operating_income
- 利润总额                 -> pretax_income
- 净利润                   -> net_income
- 购建固定资产无形资产和其他长期资产支付的现金  -> capex
- 货币资金                 -> cash_equivalents
- 短期借款                 -> short_term_debt
- 长期借款                 -> long_term_debt
- 应付债券                 -> bonds_payable
- 租赁负债                 -> lease_liabilities
- 资产总计                 -> total_assets
- 负债合计                 -> total_liabilities
- 所有者权益合计           -> equity

Usa ÚNICAMENTE la clave "financial_metrics" como raíz del JSON.
"""


def cargar_crosswalk():
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(CONFIG_BLOB)
    raw = blob.download_as_text(encoding="utf-8").strip().lstrip('\ufeff')
    data = json.loads(raw)
    return {
        item["source_tag"]: item
        for item in data
        if item.get("taxonomy") == TAXONOMY
    }


def extraer_anio_desde_nombre(nombre_blob):
    # Patrones comunes: {code}_{company}_{fecha}_annual_report.md
    # Si la fecha es de enero-junio, el reporte cubre el ejercicio fiscal previo
    m = re.search(r'(20\d{2})(\d{2})\d{2}', nombre_blob)
    if m:
        anio = int(m.group(1))
        mes = int(m.group(2))
        return anio - 1 if mes <= 6 else anio
    m = re.search(r'(20\d{2})', nombre_blob)
    if m:
        return int(m.group(1))
    # Si no hay año, usar año actual - 1 (reporte anual del año fiscal previo)
    return datetime.now().year - 1



def advertir_si_truncado(response):
    """Avisa si la respuesta se corto por agotar max_output_tokens.

    El presupuesto de max_output_tokens lo comparten el razonamiento y la salida.
    Con thinking_level alto el modelo puede gastar casi todo pensando y devolver
    un JSON cortado a medias, que aguas abajo aparece como un generico "no es
    JSON valido" sin pista de la causa. Paso en extraccion-json-china: 62,911
    tokens de razonamiento contra 2,610 de salida.

    Si esto se dispara, la correccion es bajar thinking_level; extraccion-json-china
    tiene el reintento automatico como referencia.
    """
    try:
        candidato = response.candidates[0] if response.candidates else None
        finish = getattr(candidato, "finish_reason", None)
        if finish is None or "MAX_TOKENS" not in str(finish):
            return
        uso = response.usage_metadata
        print(
            "      ⚠️  Respuesta truncada por limite de tokens: "
            f"razonamiento={getattr(uso, 'thoughts_token_count', '?')}, "
            f"salida={getattr(uso, 'candidates_token_count', '?')}. "
            "Bajar thinking_level en este servicio."
        )
    except Exception:
        # La advertencia nunca debe romper la extraccion
        pass


def extraer_con_gemini(texto, reintentos=3):
    client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
    contents = (
        "Procesa el siguiente extracto de estados financieros de un reporte anual chino "
        "y extrae las métricas financieras al formato JSON requerido:\n\n" + texto
    )
    config = types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        max_output_tokens=65535,
        response_mime_type="application/json",
        system_instruction=SYSTEM_INSTRUCTION,
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
        ],
        thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
    )
    for intento in range(reintentos):
        try:
            response = client.models.generate_content(model=MODEL_NAME, contents=contents, config=config)
            advertir_si_truncado(response)
            return response.text
        except Exception as e:
            if intento < reintentos - 1:
                espera = 15 * (intento + 1)
                print(f"    ↳ Error Gemini (intento {intento + 1}): {e} — reintento en {espera}s")
                time.sleep(espera)
            else:
                raise


def limpiar_json(texto):
    texto = texto.strip().lstrip('\ufeff')
    texto = re.sub(r'^```(?:json)?\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return json.loads(texto)


def procesar_archivo(blob, crosswalk):
    nombre = blob.name.split("/")[-1]
    contenido = blob.download_as_text()
    anio = extraer_anio_desde_nombre(nombre)
    print(f"  ↳ {nombre} | anio={anio} | {len(contenido):,} chars")

    # Seleccionar ventana con los estados financieros consolidados:
    # buscar la seccion real (despues del 15% del documento para evitar el indice)
    inicio_min = int(len(contenido) * 0.15)
    candidatos = []
    for kw in ("合并资产负债表", "合并利润表", "合并现金流量表"):
        idx = contenido.find(kw, inicio_min)
        if idx != -1:
            candidatos.append(idx)
    if candidatos:
        inicio = min(candidatos)
        texto = contenido[inicio:inicio + 60000]
    else:
        texto = contenido[-60000:] if len(contenido) > 60000 else contenido

    try:
        respuesta = extraer_con_gemini(texto)
        print(f"    ↳ Gemini resp: {respuesta[:400]}")
        data = limpiar_json(respuesta)
        metricas = data.get("financial_metrics", data)
        # Gemini a veces devuelve financial_metrics como lista de objetos
        if isinstance(metricas, list):
            merged = {}
            for elem in metricas:
                if not isinstance(elem, dict):
                    continue
                if any(k in crosswalk for k in elem.keys()):
                    merged.update({k: v for k, v in elem.items() if k in crosswalk})
                elif elem.get("name") or elem.get("tag") or elem.get("metric"):
                    tag = elem.get("name") or elem.get("tag") or elem.get("metric")
                    merged[tag] = elem
            metricas = merged

        registros = []
        for tag, info in metricas.items():
            if tag not in crosswalk:
                continue  # solo tags del crosswalk
            # Gemini a veces devuelve una lista de entradas para un tag
            entradas = info if isinstance(info, list) else [info]
            if not entradas:
                continue
            # Preferir la entrada consolidada; si no, la primera
            consolidados = [e for e in entradas
                            if isinstance(e, dict) and str(e.get("context_ref", "")).lower() == "consolidated"]
            entrada = consolidados[0] if consolidados else (entradas[0] if isinstance(entradas[0], dict) else None)
            if not isinstance(entrada, dict) or entrada.get("value") is None:
                continue
            try:
                valor = float(entrada["value"])
            except (ValueError, TypeError):
                continue

            scale = str(entrada.get("scale", "yuan"))
            # Gemini a veces ya expande el valor a yuanes base y a veces no.
            # Solo expandir si la magnitud sugiere que aun no esta expandido.
            if "yi" in scale and abs(valor) < 1e7:
                valor = valor * 1e8
            elif "wan" in scale and abs(valor) < 1e5:
                valor = valor * 1e4
            # Normalizar a unidades base (yuanes): si el valor es menor a 1e6,
            # Gemini lo entrego en millones de moneda local
            if abs(valor) < 1e6:
                valor = valor * 1e6

            registros.append({
                "company": None,  # se setea despues por codigo de archivo
                "fiscal_year": anio,
                "taxonomy": TAXONOMY,
                "source_tag": tag,
                "raw_value": valor,
                "unit": "CNY",
                "currency": "CNY",
                "period": entrada.get("period", "FY"),
                "context_ref": entrada.get("context_ref", "consolidated"),
                "data_source": "gemini_fallback",
                "period_end_date": f"{anio}-12-31",
                "extraction_ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "raw_json": json.dumps(entrada, ensure_ascii=False),
            })
        return registros, anio
    except Exception as e:
        print(f"    ✗ Error Gemini: {e}")
        return [], anio


def mapear_codigo_empresa(nombre_blob, crosswalk_empresas):
    for emp in crosswalk_empresas:
        if f"{emp['code']}_" in nombre_blob:
            return emp["company"]
    return "desconocida"


@functions_framework.http
def ejecutar_extraccion_financiera(request):
    t0 = time.time()
    print(f"[START] Extraccion financiera China A-Share: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    request_args = request.args or {}
    dry_run = (request_args.get("dry_run", "false").lower() == "true")
    code_filtro = request_args.get("code", "")

    crosswalk = cargar_crosswalk()
    print(f"[CROSSWALK] {len(crosswalk)} tags cas-china en config")

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)

    blobs = list(bucket.list_blobs(prefix=SOURCE_PREFIX))
    blobs = [b for b in blobs if b.name.endswith(".md")]
    # Solo procesar archivos de empresas del catalogo A-Share (formato {code}_...)
    blobs = [b for b in blobs if any(f"{e['code']}_" in b.name.split('/')[-1] for e in AEROLINEAS)]

    if code_filtro:
        blobs = [b for b in blobs if f"{code_filtro}_" in b.name]

    print(f"[LISTADO] {len(blobs)} archivos .md en {SOURCE_PREFIX}")

    registros_totales = []
    resumen = []
    errores = []

    for blob in blobs:
        nombre = blob.name.split("/")[-1]
        company = mapear_codigo_empresa(nombre, AEROLINEAS)
        registros, anio = procesar_archivo(blob, crosswalk)
        for r in registros:
            r["company"] = company
        registros_totales.extend(registros)
        resumen.append({"archivo": nombre, "company": company, "anio": anio, "registros": len(registros)})
        print(f"  {'DRY-RUN' if dry_run else 'OK'}: {company} {anio} -> {len(registros)} registros")

    if not dry_run and registros_totales:
        # Agrupar por company-anio y subir un NDJSON por empresa
        from collections import defaultdict
        grupos = defaultdict(list)
        for r in registros_totales:
            grupos[f"{r['company']}_{r['fiscal_year']}"].append(r)

        for clave, grupo in grupos.items():
            blob_name = f"{TARGET_PREFIX}{clave.replace(' ', '_')}.json"
            ndjson = "\n".join(json.dumps(r, ensure_ascii=False) for r in grupo) + "\n"
            bucket.blob(blob_name).upload_from_string(ndjson, content_type="application/x-ndjson")
            print(f"  ✓ Subido {blob_name} ({len(grupo)} registros)")

    t_total = round(time.time() - t0, 2)
    tags_unicos = len(set(r["source_tag"] for r in registros_totales))
    print(f"[END] Finalizado en {t_total}s | {len(registros_totales)} registros | {tags_unicos} tags")

    return {
        "status": "ok" if not errores else "parcial",
        "taxonomy": TAXONOMY,
        "dry_run": dry_run,
        "total_registros": len(registros_totales),
        "total_tags_unicos": tags_unicos,
        "archivos": resumen,
        "errores": errores,
        "tiempo_segundos": t_total,
    }, 200