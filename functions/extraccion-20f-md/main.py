import os
import time
from datetime import datetime
from google.cloud import storage
from edgar import Company, set_identity

# =========================================================
# CONFIGURACIÓN
# =========================================================
PROJECT_ID = "mach5-gemini-project"
BUCKET_NAME = "bucket-edgar"

SEC_IDENTITY = os.environ.get("SEC_API_IDENTITY")
if not SEC_IDENTITY:
    raise ValueError("SEC_API_IDENTITY no configurado en las variables de entorno")

set_identity(SEC_IDENTITY)

# =========================================================
# LISTA DE AEROLÍNEAS EXTRANJERAS ACTIVAS (20-F)
# =========================================================
# Se incluyen las notas sobre las secciones exactas del Item 4 para el análisis de flota
LISTA_EMPRESAS = [
    # ── LATINOAMÉRICA ─────────────────────────────────────────────────────────
    {'ticker': 'AEROMEX', 'cik': '0001561861', 'seccion_flota': 'Item 4.D'}, # Grupo Aeromexico
    {'ticker': 'LTM',     'cik': '0001047716', 'seccion_flota': 'Item 4.B'}, # LATAM Airlines Group
    {'ticker': 'VLRS',    'cik': '0001520504', 'seccion_flota': 'Item 4'},   # Volaris
    {'ticker': 'CPA',     'cik': '0001345105', 'seccion_flota': 'Item 4'},   # Copa Holdings
    {'ticker': 'AZUL',    'cik': '0001432364', 'seccion_flota': 'Item 4.B'}, # Azul SA
]

# =========================================================
# GCS CLIENT
# =========================================================
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)

# =========================================================
# SUBIR REPORTE A GCS
# =========================================================
def subir_reporte_markdown(filing, ticker, seccion_flota):
    try:
        # =====================================================
        # CONVERSIÓN NATIVA A MARKDOWN (Mantiene tablas estructuradas)
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
        # NOMBRE ARCHIVO Y RUTA (Organizado en la carpeta 20f-md)
        # =====================================================
        filename = f"{ticker.lower()}-{year}-20f.md"
        blob = bucket.blob(f"20f-md/{filename}")

        # =====================================================
        # EVITAR DUPLICADOS
        # =====================================================
        if blob.exists():
            print(f"⏩ Ya existe: {filename}")
            return

        # =====================================================
        # METADATA (Se inyecta la sección clave como metadato del objeto)
        # =====================================================
        blob.metadata = {
            "ticker": ticker,
            "form": "20-F",
            "year": year,
            "filing_date": filing_date,
            "target_fleet_section": seccion_flota
        }

        # =====================================================
        # UPLOAD GCS
        # =====================================================
        blob.upload_from_string(
            markdown_content,
            content_type="text/markdown"
        )

        print(f"✔ Uploaded gs://{BUCKET_NAME}/20f-md/{filename} (Target: {seccion_flota})")

    except Exception as e:
        print(f"✖ Error subiendo reporte 20-F de {ticker}: {e}")

# =========================================================
# PROCESAR EMPRESA
# =========================================================
def procesar_empresa(empresa):
    ticker = empresa["ticker"]
    cik = empresa["cik"]
    seccion_flota = empresa["seccion_flota"]

    print("\n=================================================")
    print(f"Procesando {ticker} ({cik}) - Target: {seccion_flota}")
    print("=================================================")

    try:
        company = Company(cik)

        # =====================================================
        # ÚLTIMOS 10 AÑOS
        # =====================================================
        año_actual = datetime.now().year
        año_inicio = año_actual - 10
        fecha_inicio = f"{año_inicio}-01-01"

        # =====================================================
        # FILINGS 20-F (Filtro específico para extranjeros)
        # =====================================================
        filings = company.get_filings(
            form="20-F",
            amendments=False
        )

        filings = filings.filter(
            date=f"{fecha_inicio}:"
        )

        if len(filings) == 0:
            print(f"⚠ No se encontraron 20-F para {ticker}")
            return

        # =====================================================
        # SOLO EL REPORTE ANUAL MÁS RECIENTE
        # =====================================================
        ultimo_filing = filings.latest()

        if not ultimo_filing:
            print(f"⚠ No se pudo obtener el último 20-F para {ticker}")
            return

        subir_reporte_markdown(
            filing=ultimo_filing,
            ticker=ticker,
            seccion_flota=seccion_flota
        )

        # Rate limiting preventivo exigido por la SEC
        time.sleep(0.25)

    except Exception as e:
        print(f"✖ Error procesando {ticker}: {e}")

# =========================================================
# CLOUD FUNCTION ENTRYPOINT
# =========================================================
# @functions_framework.http
def run_edgar_pipeline_20f(request):
    print("🚀 Iniciando pipeline SEC EDGAR para reportes 20-F (Internacionales)")
    inicio = datetime.now()

    for empresa in LISTA_EMPRESAS:
        procesar_empresa(empresa)

    fin = datetime.now()
    duracion = str(fin - inicio)

    return {
        "status": "success",
        "bucket": BUCKET_NAME,
        "folder": "20f-md",
        "duration": duracion,
        "companies_processed": len(LISTA_EMPRESAS)
    }