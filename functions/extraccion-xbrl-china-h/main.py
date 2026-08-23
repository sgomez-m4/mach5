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
TARGET_PREFIX = "financial_raw_china_h/"
CONFIG_BLOB = "config/xbrl_crosswalk.json"
TAXONOMY = "hkfrs"

# =========================================================
# AEROLINEAS H-SHARE (catalogo de descarga-anual-china)
# =========================================================
AEROLINEAS = [
    {"company": "Cathay Pacific Airways", "code": "00293", "market": "H-Share"},
    {"company": "Air China", "code": "00753", "market": "H-Share"},
    {"company": "China Eastern Airlines", "code": "00670", "market": "H-Share"},
    {"company": "China Southern Airlines", "code": "01055", "market": "H-Share"},
]

SYSTEM_INSTRUCTION = """
Actúa como experto en normas HKFRS (Hong Kong Financial Reporting Standards) y datos financieros de aerolíneas.
Tu tarea es extraer métricas financieras de reportes anuales de aerolíneas listadas en la Bolsa de
Hong Kong (H-Shares / HKE) y devolver estrictamente un JSON válido.

El texto puede estar en chino tradicional o inglés e incluye estados financieros:
balance (资产负债表/Statement of Financial Position), estado de resultados
(损益表/Statement of Profit or Loss) y flujo de efectivo (现金流量表/Statement of Cash Flows).
La moneda puede ser HKD o USD.

### Esquema de salida
Devuelve un objeto JSON con claves = tags HKFRS (sin prefijo), valores = objeto con:
- value: número en la moneda original del reporte (HKD o USD)
- currency: "HKD" o "USD"
- scale: "yuan" | "wan" | "yi" | "raw" (si aplica escala de miles/millones)
- period: "FY" si es del año completo, "instant" si es saldo al cierre
- context_ref: "consolidated" o "parent"

Solo incluye tags que encuentres con su valor numérico. NO inventes valores.

Tags relevantes (busca estos conceptos en el reporte, en inglés o chino tradicional):
- Revenue 收入            -> revenue
- Cost of sales 营业成本     -> cost_of_revenue
- Profit from operations 营业利润 -> operating_income
- Profit before tax 除税前溢利  -> pretax_income
- Profit for the year 年内溢利  -> net_income
- Purchase of property, plant and equipment 购置物业厂房及设备 -> capex
- Cash and cash equivalents 现金及现金等价物 -> cash_equivalents
- Loans and borrowings 借贷 -> total_debt
- Lease liabilities 租赁负债  -> lease_liabilities
- Interest expense 利息开支   -> interest_expense
- Total assets 资产总额      -> total_assets
- Total liabilities 负债总额  -> total_liabilities
- Equity 权益             -> equity

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
    # Si la fecha es de enero-junio, el reporte cubre el ejercicio fiscal previo
    m = re.search(r'(20\d{2})(\d{2})\d{2}', nombre_blob)
    if m:
        anio = int(m.group(1))
        mes = int(m.group(2))
        return anio - 1 if mes <= 6 else anio
    m = re.search(r'(20\d{2})', nombre_blob)
    if m:
        return int(m.group(1))
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
        "Procesa el siguiente extracto de estados financieros de un reporte anual "
        "(aerolínea listada en Hong Kong) y extrae las métricas al formato JSON requerido:\n\n" + texto
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
    for kw in ("Consolidated statement of financial position",
               "Consolidated statement of profit or loss",
               "Consolidated income statement",
               "Consolidated statement of cash flows"):
        idx = contenido.lower().find(kw.lower(), inicio_min)
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
                continue
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

            scale = str(entrada.get("scale", "raw"))
            # Gemini a veces ya expande el valor a unidades base y a veces no.
            if "yi" in scale and abs(valor) < 1e7:
                valor = valor * 1e8
            elif "wan" in scale and abs(valor) < 1e5:
                valor = valor * 1e4
            # Normalizar a unidades base (moneda local): si el valor es menor a 1e6,
            # Gemini lo entrego en millones de moneda local
            if abs(valor) < 1e6:
                valor = valor * 1e6

            currency = str(entrada.get("currency", "HKD"))

            registros.append({
                "company": None,
                "fiscal_year": anio,
                "taxonomy": TAXONOMY,
                "source_tag": tag,
                "raw_value": valor,
                "unit": currency,
                "currency": currency,
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
    print(f"[START] Extraccion financiera China H-Share: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    request_args = request.args or {}
    dry_run = (request_args.get("dry_run", "false").lower() == "true")
    code_filtro = request_args.get("code", "")

    crosswalk = cargar_crosswalk()
    print(f"[CROSSWALK] {len(crosswalk)} tags hkfrs en config")

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)

    blobs = list(bucket.list_blobs(prefix=SOURCE_PREFIX))
    blobs = [b for b in blobs if b.name.endswith(".md")]
    # Solo archivos de empresas del catalogo H-Share (formato {code}_...)
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