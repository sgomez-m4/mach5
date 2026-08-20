import functions_framework
import json
import os
import re
import time
from datetime import datetime

import pandas as pd
from google.cloud import storage

from edgar import Company, set_identity

# =========================================================
# CONFIGURACION
# =========================================================
PROJECT_ID = "mach5-gemini-project"
BUCKET_NAME = "bucket-edgar"
TARGET_PREFIX = "financial_raw_10k/"
CONFIG_BLOB = "config/xbrl_crosswalk.json"
TAXONOMY = "us-gaap"

SEC_IDENTITY = os.environ.get("SEC_API_IDENTITY")
if not SEC_IDENTITY:
    raise ValueError("SEC_API_IDENTITY no configurado")
set_identity(SEC_IDENTITY)

# =========================================================
# EMPRESAS (reutiliza catalogo de extraccion-10k-md)
# =========================================================
LISTA_EMPRESAS = [
    {'ticker': 'AAL',  'cik': '0000006201'},   # American Airlines Group Inc.
    {'ticker': 'DAL',  'cik': '0000027904'},   # Delta Air Lines Inc.
    {'ticker': 'UAL',  'cik': '0000100517'},   # United Airlines Holdings Inc.
    {'ticker': 'ALK',  'cik': '0000766421'},   # Alaska Air Group Inc. (incl. Hawaiian)
    {'ticker': 'LUV',  'cik': '0000092380'},   # Southwest Airlines Co.
    {'ticker': 'JBLU', 'cik': '0001158463'},   # JetBlue Airways Corporation
    {'ticker': 'ULCC', 'cik': '0001670076'},   # Frontier Group Holdings Inc.
    {'ticker': 'ALGT', 'cik': '0001362468'},   # Allegiant Travel Company
    {'ticker': 'SKYW', 'cik': '0000793733'},   # SkyWest Inc.
    {'ticker': 'RJET', 'cik': '0000810332'},   # Republic Airways Holdings Inc.
    {'ticker': 'JETMF', 'cik': '0001846084'},  # Global Crossing Airlines Group Inc.
]

# Años de historia a extraer (el 10-K mas reciente trae N años en columnas)
ANIOS_HISTORIA = 3


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


def obtener_statements_consolidados(xbrl):
    """
    Devuelve dict nombre -> DataFrame consolidado (sin dimensiones ni breakdowns)
    para income, balance y cash flow. Cada df tiene columnas de fecha con valores.
    """
    st = {}
    for name, obj in [
        ('income', xbrl.statements.income_statement()),
        ('balance', xbrl.statements.balance_sheet()),
        ('cf', xbrl.statements.cash_flow_statement()),
    ]:
        try:
            d = obj.to_dataframe()
        except Exception as e:
            print(f"    ↳ statement {name} no disponible: {e}")
            continue
        if len(d) == 0:
            continue
        if 'dimension' in d.columns:
            d = d[d['dimension'] == False]
        if 'is_breakdown' in d.columns:
            d = d[d['is_breakdown'] == False]
        st[name] = d
    return st


def anios_disponibles(st):
    anios = set()
    for d in st.values():
        for col in d.columns:
            m = re.match(r'^(\d{4})-', str(col))
            if m:
                anios.add(int(m.group(1)))
    return sorted(anios)


def buscar_tag_en_statements(st, tag, anio):
    """
    Busca el concept (normalizado us-gaap_Tag) en los statements consolidados.
    Devuelve (valor, columna_fecha, statement_name) o (None, None, None).
    """
    norm = f'{TAXONOMY}_{tag}'
    for name, d in st.items():
        fila = d[d['concept'] == norm]
        if len(fila) == 0:
            fila = d[d['concept'].astype(str).str.endswith('_' + tag, na=False)]
        if len(fila) == 0:
            continue
        for col in d.columns:
            if col in ('concept', 'label', 'standard_concept', 'level', 'abstract',
                       'dimension', 'is_breakdown', 'dimension_axis', 'dimension_member',
                       'dimension_member_label', 'dimension_label', 'balance', 'weight',
                       'preferred_sign', 'parent_concept', 'parent_abstract_concept'):
                continue
            if str(col).startswith(str(anio) + '-'):
                v = fila.iloc[0][col]
                if isinstance(v, (int, float)) and not pd.isna(v):
                    return v, col, name
    return None, None, None


def procesar_empresa(empresa, crosswalk):
    ticker = empresa["ticker"]
    cik = empresa["cik"]
    print(f"\n=== Procesando {ticker} ({cik}) ===")

    try:
        company = Company(cik)
        filings = company.get_filings(form="10-K", amendments=False)
        if len(filings) == 0:
            print(f"  ⚠ Sin 10-K para {ticker}")
            return None

        # El filing mas reciente contiene los ultimos N anios en sus statements
        filing = filings[0]
        print(f"  ↳ 10-K mas reciente: {filing.filing_date}")

        try:
            xbrl = filing.xbrl()
        except Exception as e:
            print(f"  ✗ XBRL no disponible: {e}")
            return None

        st = obtener_statements_consolidados(xbrl)
        anios = anios_disponibles(st)
        if not anios:
            print(f"  ✗ Sin columnas de anio en los statements")
            return None

        anio_max = anios[-1]
        objetivos = [a for a in range(anio_max - ANIOS_HISTORIA + 1, anio_max + 1)]
        print(f"  ↳ anios disponibles: {anios} | objetivo: {objetivos}")

        registros = []
        for anio in objetivos:
            if anio not in anios:
                continue
            for tag, item in crosswalk.items():
                valor, col_fecha, st_name = buscar_tag_en_statements(st, tag, anio)
                if valor is None:
                    continue
                period = "instant" if st_name == "balance" else "FY"
                registros.append({
                    "company": ticker,
                    "fiscal_year": anio,
                    "taxonomy": TAXONOMY,
                    "source_tag": tag,
                    "raw_value": valor,
                    "unit": "USD",
                    "currency": "USD",
                    "period": period,
                    "context_ref": "consolidated",
                    "data_source": "xbrl_tag",
                    "period_end_date": col_fecha,
                    "extraction_ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "raw_json": json.dumps({"concept": f"{TAXONOMY}_{tag}", "statement": st_name}, ensure_ascii=False)
                })
            print(f"    ↳ anio {anio}: {len(registros)} registros acumulados")

        return registros
    except Exception as e:
        print(f"  ✗ Error procesando {ticker}: {e}")
        return None


def subir_resultados(ticker, registros):
    if not registros:
        return None, 0
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob_name = f"{TARGET_PREFIX}{ticker.lower()}_financial.json"
    ndjson = "\n".join(json.dumps(r, ensure_ascii=False) for r in registros) + "\n"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(ndjson, content_type="application/x-ndjson")
    return blob_name, len(registros)


@functions_framework.http
def ejecutar_extraccion_financiera(request):
    t0 = time.time()
    print(f"[START] Extraccion XBRL 10-K financiera: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    request_args = request.args or {}
    dry_run = (request_args.get("dry_run", "false").lower() == "true")
    ticker_filtro = request_args.get("ticker", "").upper()

    crosswalk = cargar_crosswalk()
    print(f"[CROSSWALK] {len(crosswalk)} tags us-gaap en config")

    empresas = LISTA_EMPRESAS
    if ticker_filtro:
        empresas = [e for e in empresas if e["ticker"] == ticker_filtro]
        if not empresas:
            return {"error": f"Ticker no encontrado: {ticker_filtro}"}, 400

    total_registros = 0
    total_tags = 0
    resumen_empresas = []
    errores = []

    for empresa in empresas:
        registros = procesar_empresa(empresa, crosswalk)
        if registros is None:
            errores.append(empresa["ticker"])
            resumen_empresas.append({"ticker": empresa["ticker"], "registros": 0, "tags": 0, "estado": "error"})
            continue

        tags = len(set(r["source_tag"] for r in registros))
        total_registros += len(registros)
        total_tags += tags

        if dry_run:
            resumen_empresas.append({"ticker": empresa["ticker"], "registros": len(registros), "tags": tags, "estado": "dry_run"})
            print(f"  [DRY-RUN] {empresa['ticker']}: {len(registros)} registros, {tags} tags")
        else:
            blob_name, n = subir_resultados(empresa["ticker"], registros)
            resumen_empresas.append({"ticker": empresa["ticker"], "registros": n, "tags": tags, "estado": "ok"})
            print(f"  ✓ {empresa['ticker']}: {n} registros ({tags} tags) -> {blob_name}")

        time.sleep(0.25)  # rate limit SEC

    t_total = round(time.time() - t0, 2)
    print(f"[END] Finalizado en {t_total}s | {total_registros} registros | {total_tags} tags unicos")

    return {
        "status": "ok" if not errores else "parcial",
        "taxonomy": TAXONOMY,
        "dry_run": dry_run,
        "total_registros": total_registros,
        "total_tags_unicos": total_tags,
        "empresas": resumen_empresas,
        "errores": errores,
        "tiempo_segundos": t_total
    }, 200