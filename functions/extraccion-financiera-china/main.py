import functions_framework
import json
import math
import re
import time
from collections import defaultdict
from datetime import datetime

import akshare as ak
from google.cloud import storage

# =========================================================
# CONFIGURACION
# =========================================================
PROJECT_ID = "mach5-gemini-project"
LOCATION = "global"
MODEL_NAME = "gemini-3-flash-preview"
BUCKET_NAME = "bucket-edgar"
SOURCE_PREFIX_MD = "anual-china-md/"
TARGET_PREFIX = "financial_raw_china/"
CONFIG_BLOB = "config/xbrl_crosswalk.json"
TAXONOMY = "cas-china"
DATA_SOURCE = "akshare"

# =========================================================
# AEROLINEAS A-SHARE (datos estructurados via akshare)
# =========================================================
AEROLINEAS = [
    {"company": "Air China", "code": "601111", "market": "A-Share", "exchange": "SH"},
    {"company": "China Eastern Airlines", "code": "600115", "market": "A-Share", "exchange": "SH"},
    {"company": "China Southern Airlines", "code": "600029", "market": "A-Share", "exchange": "SH"},
    {"company": "Hainan Airlines", "code": "600221", "market": "A-Share", "exchange": "SH"},
    {"company": "Spring Airlines", "code": "601021", "market": "A-Share", "exchange": "SH"},
    {"company": "Juneyao Airlines", "code": "603885", "market": "A-Share", "exchange": "SH"},
    {"company": "China Express Airlines", "code": "002928", "market": "A-Share", "exchange": "SZ"},
]

# =========================================================
# AEROLINEAS SOLO H-SHARE (sin cobertura akshare -> fallback Gemini)
# =========================================================
AEROLINEAS_HSHARE_FALLBACK = [
    {"company": "Cathay Pacific Airways", "code": "00293", "market": "H-Share"},
]

# =========================================================
# MAPEO COLUMNAS CHINAS -> source_tag canonico
# Verificado empiricamente contra stock_financial_report_sina (2026-08).
# El source_tag emitido es el canonico en ingles porque el crosswalk
# cas-china ya lo contempla y evita el doble conteo de sinonimos chinos
# (p.ej. 营业总收入 y 营业收入 mapean ambos a revenue).
# =========================================================
MAPEO_INCOME = [
    # (columnas candidatas en orden de preferencia, source_tag, period)
    (["营业总收入", "营业收入"], "revenue", "FY"),
    (["营业成本"], "cost_of_revenue", "FY"),
    (["营业利润"], "operating_income", "FY"),
    (["利润总额"], "pretax_income", "FY"),
    # net_income debe ser el atribuible a la matriz para ser comparable con el
    # NetIncomeLoss de us-gaap. 净利润 incluye los intereses minoritarios y en
    # varias emisoras difiere de forma material (China Southern FY2025:
    # 857M atribuible vs 2,685M total). Se conserva como fallback.
    (["归属于母公司所有者的净利润", "归属于母公司股东的净利润", "净利润"], "net_income", "FY"),
    # 利息费用 es el gasto bruto por intereses; 财务费用 es el resultado
    # financiero neto (intereses - ingresos financieros +/- tipo de cambio).
    (["利息费用", "利息支出"], "interest_expense", "FY"),
    (["财务费用"], "interest_expense_net", "FY"),
]

MAPEO_BALANCE = [
    (["货币资金"], "cash_equivalents", "instant"),
    (["短期借款"], "short_term_debt", "instant"),
    (["长期借款"], "long_term_debt", "instant"),
    (["应付债券"], "bonds_payable", "instant"),
    (["租赁负债"], "lease_liabilities", "instant"),
    (["资产总计"], "total_assets", "instant"),
    (["负债合计"], "total_liabilities", "instant"),
    # Sina usa parentesis de ancho medio en esta etiqueta
    (["所有者权益(或股东权益)合计", "所有者权益（或股东权益）合计",
      "所有者权益合计", "股东权益合计"], "equity", "instant"),
]

MAPEO_CASHFLOW = [
    (["购建固定资产、无形资产和其他长期资产所支付的现金",
      "购建固定资产无形资产和其他长期资产支付的现金"], "capex", "FY"),
]

# source_tags cuya convencion de signo en CAS es opuesta a la de us-gaap.
# CAS reporta las salidas de caja de inversion como positivas; el XBRL de
# us-gaap (PaymentsToAcquirePropertyPlantAndEquipment) las trae negativas.
# Sin esta inversion capex queda con signos mezclados entre regiones y
# capex_depreciation_ratio cambia de signo segun la aerolinea.
SOURCE_TAGS_INVERTIR_SIGNO = {"capex"}

# El estado de flujo de efectivo de Sina no trae la linea de depreciacion.
# Se obtiene del flujo de efectivo de Eastmoney, que si expone los componentes.
CAMPOS_DA_EM = [
    "FA_IR_DEPR",              # 固定资产折旧、油气资产折耗、生产性生物资产折旧
    "USERIGHT_ASSET_AMORTIZE",  # 使用权资产摊销
    "IA_AMORTIZE",             # 无形资产摊销
    "LPE_AMORTIZE",            # 长期待摊费用摊销
]


# =========================================================
# UTILIDADES
# =========================================================
def cargar_crosswalk():
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(CONFIG_BLOB)
    raw = blob.download_as_text(encoding="utf-8").strip().lstrip('﻿')
    data = json.loads(raw)
    return {
        item["source_tag"]: item
        for item in data
        if item.get("taxonomy") == TAXONOMY
    }


def a_float(valor):
    """Convierte un valor de akshare a float, descartando nulos y NaN."""
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (ValueError, TypeError):
        return None
    if math.isnan(numero) or math.isinf(numero):
        return None
    return numero


def fila_anual_mas_reciente(df, columna_fecha="报告日"):
    """Filtra a reportes anuales (cierre 1231) y devuelve la fila del ultimo anio."""
    if df is None or df.empty or columna_fecha not in df.columns:
        return None, None
    fechas = df[columna_fecha].astype(str).str.replace("-", "", regex=False).str[:8]
    anuales = df[fechas.str.endswith("1231")]
    if anuales.empty:
        return None, None
    orden = anuales[columna_fecha].astype(str).str.replace("-", "", regex=False).str[:8]
    idx = orden.astype(int).idxmax()
    fila = anuales.loc[idx]
    anio = int(str(fila[columna_fecha]).replace("-", "")[:4])
    return fila, anio


def primera_columna_disponible(fila, candidatas):
    """Devuelve (nombre_columna, valor) de la primera candidata con dato numerico."""
    for col in candidatas:
        if col in fila.index:
            valor = a_float(fila[col])
            if valor is not None:
                return col, valor
    return None, None


def construir_registro(company, anio, source_tag, valor, period, columna_origen,
                       data_source=DATA_SOURCE, currency="CNY"):
    if source_tag in SOURCE_TAGS_INVERTIR_SIGNO:
        valor = -valor
    return {
        "company": company,
        "fiscal_year": anio,
        "taxonomy": TAXONOMY,
        "source_tag": source_tag,
        "raw_value": valor,
        "unit": currency,
        "currency": currency,
        "period": period,
        "context_ref": "consolidated",
        "data_source": data_source,
        "period_end_date": f"{anio}-12-31",
        "extraction_ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "raw_json": json.dumps(
            {"source_column": columna_origen, "value": valor, "provider": data_source},
            ensure_ascii=False,
        ),
    }


# =========================================================
# EXTRACCION AKSHARE (A-SHARE)
# =========================================================
def extraer_depreciacion_em(aerolinea):
    """Suma los componentes de depreciacion y amortizacion del flujo de caja de Eastmoney."""
    simbolo = f"{aerolinea['exchange']}{aerolinea['code']}"
    try:
        df = ak.stock_cash_flow_sheet_by_report_em(symbol=simbolo)
    except Exception as e:
        print(f"    ⚠ Sin flujo Eastmoney para {simbolo}: {e}")
        return None, None, []

    fila, anio = fila_anual_mas_reciente(df, columna_fecha="REPORT_DATE")
    if fila is None:
        return None, None, []

    total = 0.0
    componentes = []
    for campo in CAMPOS_DA_EM:
        if campo not in fila.index:
            continue
        valor = a_float(fila[campo])
        if valor is None:
            continue
        total += valor
        componentes.append(campo)

    if not componentes:
        return None, anio, []
    return total, anio, componentes


def extraer_aerolinea_akshare(aerolinea):
    """Extrae los tres estados financieros del ultimo ejercicio anual disponible."""
    company = aerolinea["company"]
    code = aerolinea["code"]
    registros = []
    faltantes = []

    estados = [
        ("利润表", MAPEO_INCOME),
        ("资产负债表", MAPEO_BALANCE),
        ("现金流量表", MAPEO_CASHFLOW),
    ]

    anio_reporte = None
    for symbol, mapeo in estados:
        try:
            df = ak.stock_financial_report_sina(stock=code, symbol=symbol)
        except Exception as e:
            print(f"    ✗ {company} {symbol}: {e}")
            faltantes.append(f"{symbol}:error")
            continue

        fila, anio = fila_anual_mas_reciente(df)
        if fila is None:
            print(f"    ✗ {company} {symbol}: sin reportes anuales")
            faltantes.append(f"{symbol}:sin_anual")
            continue

        anio_reporte = anio if anio_reporte is None else max(anio_reporte, anio)
        print(f"    ↳ {company} {symbol} | anio={anio} | {len(df)} filas")

        for candidatas, source_tag, period in mapeo:
            columna, valor = primera_columna_disponible(fila, candidatas)
            if valor is None:
                faltantes.append(source_tag)
                continue
            registros.append(
                construir_registro(company, anio, source_tag, valor, period, columna)
            )

    # Depreciacion y amortizacion desde Eastmoney (no disponible en Sina)
    da, anio_da, componentes = extraer_depreciacion_em(aerolinea)
    if da is not None and anio_da is not None:
        registros.append(
            construir_registro(
                company, anio_da, "depreciation_amortization", da, "FY",
                "+".join(componentes),
            )
        )
    else:
        faltantes.append("depreciation_amortization")

    return registros, anio_reporte, faltantes


# =========================================================
# FALLBACK GEMINI (H-SHARE puro, p.ej. Cathay Pacific)
# Logica portada desde functions/extraccion-xbrl-china-h/main.py
# =========================================================
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
- Depreciation and amortisation 折旧及摊销 -> depreciation_amortization
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


def extraer_con_gemini(texto, reintentos=3):
    from google import genai
    from google.genai import types

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
            return response.text
        except Exception as e:
            if intento < reintentos - 1:
                espera = 15 * (intento + 1)
                print(f"    ↳ Error Gemini (intento {intento + 1}): {e} — reintento en {espera}s")
                time.sleep(espera)
            else:
                raise


def limpiar_json(texto):
    texto = texto.strip().lstrip('﻿')
    texto = re.sub(r'^```(?:json)?\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return json.loads(texto)


def procesar_archivo_gemini(blob, crosswalk_hkfrs, company):
    """Extrae metricas de un .md anual con Gemini (solo H-Share sin akshare)."""
    nombre = blob.name.split("/")[-1]
    contenido = blob.download_as_text()
    anio = extraer_anio_desde_nombre(nombre)
    print(f"  ↳ [gemini] {nombre} | anio={anio} | {len(contenido):,} chars")

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
        texto = contenido[min(candidatos):min(candidatos) + 60000]
    else:
        texto = contenido[-60000:] if len(contenido) > 60000 else contenido

    try:
        respuesta = extraer_con_gemini(texto)
        data = limpiar_json(respuesta)
        metricas = data.get("financial_metrics", data)
        # Gemini a veces devuelve financial_metrics como lista de objetos
        if isinstance(metricas, list):
            merged = {}
            for elem in metricas:
                if not isinstance(elem, dict):
                    continue
                if any(k in crosswalk_hkfrs for k in elem.keys()):
                    merged.update({k: v for k, v in elem.items() if k in crosswalk_hkfrs})
                elif elem.get("name") or elem.get("tag") or elem.get("metric"):
                    tag = elem.get("name") or elem.get("tag") or elem.get("metric")
                    merged[tag] = elem
            metricas = merged

        registros = []
        for tag, info in metricas.items():
            if tag not in crosswalk_hkfrs:
                continue
            entradas = info if isinstance(info, list) else [info]
            if not entradas:
                continue
            consolidados = [e for e in entradas
                            if isinstance(e, dict) and str(e.get("context_ref", "")).lower() == "consolidated"]
            entrada = consolidados[0] if consolidados else (entradas[0] if isinstance(entradas[0], dict) else None)
            if not isinstance(entrada, dict) or entrada.get("value") is None:
                continue
            valor = a_float(entrada["value"])
            if valor is None:
                continue

            scale = str(entrada.get("scale", "raw"))
            # Gemini a veces ya expande el valor a unidades base y a veces no.
            if "yi" in scale and abs(valor) < 1e7:
                valor = valor * 1e8
            elif "wan" in scale and abs(valor) < 1e5:
                valor = valor * 1e4
            if abs(valor) < 1e6:
                valor = valor * 1e6

            currency = str(entrada.get("currency", "HKD"))
            # capex es siempre una salida de caja; Gemini devuelve el signo de
            # forma inconsistente segun como lo presente el reporte (parentesis
            # o no), asi que se normaliza a negativo como en us-gaap.
            if tag in SOURCE_TAGS_INVERTIR_SIGNO:
                valor = -abs(valor)
            registros.append({
                "company": company,
                "fiscal_year": anio,
                "taxonomy": "hkfrs",
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


def cargar_crosswalk_hkfrs():
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    raw = bucket.blob(CONFIG_BLOB).download_as_text(encoding="utf-8").strip().lstrip('﻿')
    data = json.loads(raw)
    return {item["source_tag"]: item for item in data if item.get("taxonomy") == "hkfrs"}


def ejecutar_fallback_gemini(bucket, code_filtro):
    """Procesa las aerolineas H-Share que no tienen cobertura en akshare."""
    aerolineas = AEROLINEAS_HSHARE_FALLBACK
    if code_filtro:
        aerolineas = [a for a in aerolineas if a["code"] == code_filtro]
    if not aerolineas:
        return [], []

    crosswalk_hkfrs = cargar_crosswalk_hkfrs()
    blobs = [b for b in bucket.list_blobs(prefix=SOURCE_PREFIX_MD) if b.name.endswith(".md")]

    registros = []
    resumen = []
    for aero in aerolineas:
        candidatos = [b for b in blobs if f"{aero['code']}_" in b.name.split("/")[-1]]
        if not candidatos:
            print(f"  ⚠ Sin .md para {aero['company']} ({aero['code']})")
            resumen.append({"company": aero["company"], "fuente": "gemini_fallback",
                            "anio": None, "registros": 0})
            continue
        # Solo el reporte mas reciente (ultimo ejercicio)
        blob = sorted(candidatos, key=lambda b: b.name)[-1]
        regs, anio = procesar_archivo_gemini(blob, crosswalk_hkfrs, aero["company"])
        registros.extend(regs)
        resumen.append({"company": aero["company"], "fuente": "gemini_fallback",
                        "anio": anio, "registros": len(regs)})
    return registros, resumen


# =========================================================
# ENTRYPOINT
# =========================================================
@functions_framework.http
def ejecutar_extraccion_financiera(request):
    t0 = time.time()
    print(f"[START] Extraccion financiera China (akshare): {time.strftime('%Y-%m-%d %H:%M:%S')}")

    request_args = request.args or {}
    dry_run = (request_args.get("dry_run", "false").lower() == "true")
    code_filtro = request_args.get("code", "")

    crosswalk = cargar_crosswalk()
    print(f"[CROSSWALK] {len(crosswalk)} tags {TAXONOMY} en config")

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)

    aerolineas = AEROLINEAS
    if code_filtro:
        aerolineas = [a for a in aerolineas if a["code"] == code_filtro]

    registros_totales = []
    resumen = []
    errores = []

    for aero in aerolineas:
        print(f"  → {aero['company']} ({aero['code']})")
        try:
            registros, anio, faltantes = extraer_aerolinea_akshare(aero)
        except Exception as e:
            print(f"    ✗ Error akshare {aero['company']}: {e}")
            errores.append({"company": aero["company"], "error": str(e)})
            continue

        # Solo conservar tags presentes en el crosswalk cas-china
        registros = [r for r in registros if r["source_tag"] in crosswalk]
        registros_totales.extend(registros)
        resumen.append({
            "company": aero["company"],
            "code": aero["code"],
            "fuente": DATA_SOURCE,
            "anio": anio,
            "registros": len(registros),
            "faltantes": faltantes,
        })
        print(f"  {'DRY-RUN' if dry_run else 'OK'}: {aero['company']} {anio} -> {len(registros)} registros")

    # Fallback Gemini para H-Share puro (Cathay Pacific)
    try:
        regs_gemini, resumen_gemini = ejecutar_fallback_gemini(bucket, code_filtro)
        registros_totales.extend(regs_gemini)
        resumen.extend(resumen_gemini)
    except Exception as e:
        print(f"  ✗ Error fallback Gemini: {e}")
        errores.append({"company": "H-Share fallback", "error": str(e)})

    if not dry_run and registros_totales:
        # Agrupar por company-anio y subir un NDJSON por empresa
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
        "data_source": DATA_SOURCE,
        "dry_run": dry_run,
        "total_registros": len(registros_totales),
        "total_tags_unicos": tags_unicos,
        "empresas": resumen,
        "errores": errores,
        "tiempo_segundos": t_total,
    }, 200
