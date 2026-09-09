import os
import time

from datetime import datetime

from google.cloud import storage

from edgar import Company, set_identity

# =========================================================
# CONFIGURACION
# =========================================================
PROJECT_ID = "mach5-gemini-project"
BUCKET_NAME = "bucket-edgar"

SEC_IDENTITY = os.environ.get("SEC_API_IDENTITY")

if not SEC_IDENTITY:
    raise ValueError("SEC_API_IDENTITY no configurado")

set_identity(SEC_IDENTITY)

# =========================================================
# LISTA DE AEROLINEAS
# =========================================================
LISTA_EMPRESAS = [
 
    # ── LEGACY / RED COMPLETA ─────────────────────────────────────────────────
    {'ticker': 'AAL',  'cik': '0000006201'},   # American Airlines Group Inc.
    {'ticker': 'DAL',  'cik': '0000027904'},   # Delta Air Lines Inc.
    {'ticker': 'UAL',  'cik': '0000100517'},   # United Airlines Holdings Inc.
    {'ticker': 'ALK',  'cik': '0000766421'},   # Alaska Air Group Inc. (incl. Hawaiian Airlines)
 
    # ── LOW-COST ──────────────────────────────────────────────────────────────
    {'ticker': 'LUV',  'cik': '0000092380'},   # Southwest Airlines Co.
    {'ticker': 'JBLU', 'cik': '0001158463'},   # JetBlue Airways Corporation
 
    # ── ULTRA-LOW-COST (ULCC) ─────────────────────────────────────────────────
    {'ticker': 'ULCC', 'cik': '0001670076'},   # Frontier Group Holdings Inc. (Frontier Airlines)
    {'ticker': 'ALGT', 'cik': '0001362468'},   # Allegiant Travel Company
                                               #   ⚠ adquiriendo Sun Country (pendiente cierre)
 
    # ── REGIONALES ────────────────────────────────────────────────────────────
    {'ticker': 'SKYW', 'cik': '0000793733'},   # SkyWest Inc.
    {'ticker': 'RJET', 'cik': '0000810332'},   # Republic Airways Holdings Inc.
                                               #   (entidad SEC heredada de Mesa Air Group;
                                               #    fusión Republic + Mesa completada nov 2025)
 
    # ── SUPLEMENTARIA Part 121 (ACMI / chárter comercial) ────────────────────
    {'ticker': 'JETMF','cik': '0001846084'},   # Global Crossing Airlines Group Inc. (GlobalX)
                                               #   también cotiza en Cboe Canadá: JET
                                               #   ⚠ going concern reportado

]

# =========================================================
# GCS CLIENT
# =========================================================
storage_client = storage.Client(project=PROJECT_ID)

bucket = storage_client.bucket(BUCKET_NAME)

# =========================================================
# SUBIR REPORTE A GCS
# =========================================================
def subir_reporte_markdown(filing, ticker):

    try:

        # =====================================================
        # CONVERSION NATIVA A MARKDOWN
        # =====================================================
        markdown_content = filing.markdown(
            include_page_breaks=True,
            start_page_number=1
        )

        if not markdown_content:

            print(f"⚠ No markdown encontrado para {ticker}")
            return

        filing_date = str(filing.filing_date)

        year = filing_date[:4]

        # =====================================================
        # NOMBRE ARCHIVO
        # =====================================================
        filename = f"{ticker.lower()}-{year}-10k.md"

        blob = bucket.blob("/10k-md/" + filename)

        # =====================================================
        # EVITAR DUPLICADOS
        # =====================================================
        if blob.exists():

            print(f"⏩ Ya existe: {filename}")
            return

        # =====================================================
        # METADATA
        # =====================================================
        blob.metadata = {
            "ticker": ticker,
            "form": "10-K",
            "year": year,
            "filing_date": filing_date
        }

        # =====================================================
        # UPLOAD GCS
        # =====================================================
        blob.upload_from_string(
            markdown_content,
            content_type="text/markdown"
        )

        print(
            f"✔ Uploaded gs://{BUCKET_NAME}/10k-md/{filename}"
        )

    except Exception as e:

        print(
            f"✖ Error subiendo reporte {ticker}: {e}"
        )

# =========================================================
# PROCESAR EMPRESA
# =========================================================
def procesar_empresa(empresa, ejercicios=1):
    """Baja los `ejercicios` 10-K mas recientes de la empresa.

    Con ejercicios=1 se comporta como antes. Con mas, baja tambien los
    anteriores: la tabla de flota de un 10-K es una foto de un solo cierre
    —columnas Propias / Arrendadas / Total / Edad media, sin comparativo—, asi
    que la unica forma de medir la transicion de flota es tener varias fotos.
    """

    ticker = empresa["ticker"]
    cik = empresa["cik"]

    print(
        f"\n================================================="
    )

    print(f"Procesando {ticker} ({cik})")

    print(
        f"================================================="
    )

    try:

        company = Company(cik)

        # =====================================================
        # ULTIMOS 10 AÑOS
        # =====================================================
        año_actual = datetime.now().year
        año_inicio = año_actual - 10

        fecha_inicio = f"{año_inicio}-01-01"

        # =====================================================
        # FILINGS 10-K
        # =====================================================
        filings = company.get_filings(
            form="10-K",
            amendments=False
        )

        filings = filings.filter(
            date=f"{fecha_inicio}:"
        )

        if len(filings) == 0:

            print(f"⚠ No se encontraron 10-K para {ticker}")
            return

        # =====================================================
        # LOS N MAS RECIENTES
        # =====================================================
        # latest(1) devuelve el objeto suelto y latest(n>1) una coleccion:
        # se normaliza a lista para no tener dos caminos.
        recientes = filings.latest(ejercicios)

        if recientes is None:

            print(f"⚠ No se pudo obtener último 10-K para {ticker}")
            return

        if not isinstance(recientes, (list, tuple)):

            recientes = [recientes] if ejercicios == 1 else list(recientes)

        print(f"   {len(recientes)} filing(s) para {ticker}")

        for filing in recientes:

            subir_reporte_markdown(
                filing=filing,
                ticker=ticker
            )

            # Rate limiting SEC
            time.sleep(0.25)

    except Exception as e:

        print(
            f"✖ Error procesando {ticker}: {e}"
        )

# =========================================================
# CLOUD FUNCTION ENTRYPOINT
# =========================================================
#@functions_framework.http
def run_edgar_pipeline(request):

    # ?ejercicios=3 baja tambien los dos anteriores. Por defecto 1, que es el
    # comportamiento de la corrida diaria: sin esto, cada ejecucion rutinaria
    # volveria a recorrer diez anios de historia para no subir nada nuevo.
    ejercicios = 1

    try:
        crudo = (request.args.get("ejercicios")
                 or (request.get_json(silent=True) or {}).get("ejercicios"))
        if crudo:
            ejercicios = max(1, min(10, int(crudo)))
    except Exception:
        pass

    print(f"🚀 Iniciando pipeline SEC EDGAR (ejercicios={ejercicios})")

    inicio = datetime.now()

    for empresa in LISTA_EMPRESAS:

        procesar_empresa(empresa, ejercicios=ejercicios)

    fin = datetime.now()

    duracion = str(fin - inicio)

    return {
        "status": "success",
        "bucket": BUCKET_NAME,
        "duration": duracion,
        "ejercicios": ejercicios,
        "companies_processed": len(LISTA_EMPRESAS)
    }