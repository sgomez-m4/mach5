import re
import time
import json
import requests
import functions_framework
from google.cloud import storage

# ==========================================
# Configuración de destino en Google Cloud Storage
# ==========================================
BUCKET_NAME = "bucket-edgar"
DEST_PREFIX = "anual-china"  # Todos los archivos van a la raíz de esta "carpeta", sin subcarpetas

# Endpoints de Cninfo
STATIC_PDF_BASE = "http://static.cninfo.com.cn/"
CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CATALOGS = [
    "http://www.cninfo.com.cn/new/data/szse_stock.json",  # A-Shares
    "http://www.cninfo.com.cn/new/data/hke_stock.json"    # H-Shares
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "http://www.cninfo.com.cn",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
}

AIRLINES = [
    {"company": "Air China", "code": "601111", "market": "A-Share", "column": "sse"},
    {"company": "China Eastern Airlines", "code": "600115", "market": "A-Share", "column": "sse"},
    {"company": "China Southern Airlines", "code": "600029", "market": "A-Share", "column": "sse"},
    {"company": "Cathay Pacific Airways", "code": "00293", "market": "H-Share", "column": "hke"},
    {"company": "Hainan Airlines", "code": "600221", "market": "A-Share", "column": "sse"},
    {"company": "Spring Airlines", "code": "601021", "market": "A-Share", "column": "sse"},
    {"company": "Juneyao Airlines", "code": "603885", "market": "A-Share", "column": "sse"},
    {"company": "China Express Airlines", "code": "002928", "market": "A-Share", "column": "szse"}
]

_storage_client = None


def get_storage_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def build_org_id_dictionary():
    """Descarga los catálogos y se adapta a cualquier estructura JSON."""
    print("📥 Construyendo diccionario de orgId (Modo JSON Robusto)...")
    org_map = {}

    for url in CATALOGS:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code == 200:
                data = res.json()

                # Caso 1: El servidor devolvió un String (JSON doblemente serializado)
                if isinstance(data, str):
                    data = json.loads(data)

                # Caso 2: El servidor devolvió un Diccionario (Busca la lista adentro)
                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, list):
                            data = value
                            break

                # Caso 3: Iterar sobre la lista validada
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            code = item.get("code")
                            org_id = item.get("orgId")
                            if code and org_id:
                                org_map[code] = org_id

        except Exception as e:
            print(f"  [!] Fallo al descargar catálogo {url}: {e}")

    print(f"✅ Diccionario construido exitosamente ({len(org_map)} empresas registradas).\n")
    return org_map


def get_annual_report_announcements(stock_code: str, org_id: str, column: str, search_key: str):
    payload = {
        "pageNum": 1,
        "pageSize": 30,
        "column": column,
        "tabName": "fulltext",
        "stock": f"{stock_code},{org_id}",
        "searchkey": search_key,
        "category": "category_ndbg_szsh;" if column in ["sse", "szse"] else "",
        "isSet": "true"
    }

    try:
        res = requests.post(CNINFO_QUERY_URL, data=payload, headers=HEADERS, timeout=15)
        res.raise_for_status()
        return res.json().get("announcements", []) or []
    except Exception as e:
        print(f"  [!] Error al consultar anuncios: {e}")
        return []


def delete_existing_blobs(code: str):
    """Borra cualquier archivo previo en el bucket que empiece con '{code}_',
    para evitar duplicados cuando el título del reporte cambia entre corridas
    (ej. de un año fiscal a otro) o la función se ejecuta más de una vez."""
    client = get_storage_client()
    bucket = client.bucket(BUCKET_NAME)
    prefix = f"{DEST_PREFIX}/{code}_"

    borrados = []
    for blob in client.list_blobs(bucket, prefix=prefix):
        blob.delete()
        borrados.append(blob.name)

    if borrados:
        print(f"  🗑️  Eliminados {len(borrados)} archivo(s) previo(s) para el código {code}: {borrados}")


def upload_pdf_to_gcs(url: str, blob_name: str):
    """Descarga el PDF en memoria y lo sube directamente a GCS (sin tocar disco)."""
    client = get_storage_client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)

    res = requests.get(url, headers=HEADERS, stream=True, timeout=60)
    res.raise_for_status()

    blob.upload_from_string(res.content, content_type="application/pdf")
    return f"gs://{BUCKET_NAME}/{blob_name}"


def run_extraction():
    """Flujo principal de extracción. Devuelve un resumen del proceso."""
    print("=" * 70)
    print("  Iniciando Extracción de Reportes Anuales")
    print("=" * 70)

    resultados = []

    # 1. Cargar diccionario en RAM
    org_dict = build_org_id_dictionary()

    for item in AIRLINES:
        code = item["code"]
        company = item["company"]
        market = item["market"]
        column = item["column"]

        print(f"🔍 Procesando: {company} ({code}) [{market}]...")

        entry = {"company": company, "code": code, "market": market, "status": None}

        org_id = org_dict.get(code)
        if not org_id:
            msg = f"Código {code} no encontrado."
            print(f"  ❌ {msg} Omitiendo...\n")
            entry["status"] = "error"
            entry["detail"] = msg
            resultados.append(entry)
            continue

        # Consultar
        announcements = get_annual_report_announcements(code, org_id, column, "年度报告")
        if not announcements and market == "H-Share":
            announcements = get_annual_report_announcements(code, org_id, column, "Annual Report")

        if not announcements:
            msg = f"No se encontraron reportes anuales recientes para {code}"
            print(f"  ❌ {msg}\n")
            entry["status"] = "not_found"
            entry["detail"] = msg
            resultados.append(entry)
            continue

        # Filtrar
        # Excluye resúmenes, correcciones, avisos y anuncios "envoltorio" (海外监管公告)
        # que solo hacen referencia al reporte anual pero no son el documento en sí.
        EXCLUDE_WORDS = [
            "摘要", "取消", "提示", "第一", "第三",
            "海外监管公告", "海外監管公告",  # Overseas Regulatory Announcement (simplificado/tradicional)
            "Overseas Regulatory Announcement",
        ]
        target_report = None
        for ann in announcements:
            title = ann.get("announcementTitle", "")
            if ("年度报告" in title or "Annual Report" in title) and not any(
                word in title for word in EXCLUDE_WORDS
            ):
                target_report = ann
                break

        if not target_report:
            # Fallback: toma el primer anuncio que NO sea un envoltorio/resumen,
            # en vez de asumir ciegamente que announcements[0] es válido.
            non_excluded = [
                ann for ann in announcements
                if not any(word in ann.get("announcementTitle", "") for word in EXCLUDE_WORDS)
            ]
            target_report = non_excluded[0] if non_excluded else announcements[0]

        title = target_report.get("announcementTitle", "Annual_Report")
        adjunct_url = target_report.get("adjunctUrl", "")

        if not adjunct_url:
            msg = "Sin enlace PDF disponible."
            print(f"  ❌ {msg}\n")
            entry["status"] = "no_pdf_link"
            entry["detail"] = msg
            resultados.append(entry)
            continue

        full_pdf_url = STATIC_PDF_BASE + adjunct_url if not adjunct_url.startswith("http") else adjunct_url

        # Nombre de archivo: todos van a la raíz de DEST_PREFIX, sin subcarpetas por mercado
        filename = sanitize_filename(f"{code}_{company}_{title}.pdf")
        blob_name = f"{DEST_PREFIX}/{filename}"

        print(f"  📄 Documento: {title}")
        try:
            delete_existing_blobs(code)
            gcs_path = upload_pdf_to_gcs(full_pdf_url, blob_name)
            print(f"  ✅ Subido a: {gcs_path}\n")
            entry["status"] = "ok"
            entry["detail"] = gcs_path
        except Exception as e:
            msg = f"Fallo en la descarga/subida: {e}"
            print(f"  ❌ {msg}\n")
            entry["status"] = "error"
            entry["detail"] = msg

        resultados.append(entry)
        time.sleep(1.5)

    print("=" * 70)
    print("✨ Proceso completado.")
    print("=" * 70)

    return resultados


@functions_framework.http
def extraer_reportes_anuales(request):
    """Entry point de la Cloud Function (2nd gen, HTTP trigger)."""
    try:
        resultados = run_extraction()
        ok_count = sum(1 for r in resultados if r["status"] == "ok")
        response_body = {
            "status": "completed",
            "total": len(resultados),
            "exitosos": ok_count,
            "detalle": resultados,
        }
        return (json.dumps(response_body, ensure_ascii=False), 200, {"Content-Type": "application/json"})
    except Exception as e:
        error_body = {"status": "error", "message": str(e)}
        return (json.dumps(error_body, ensure_ascii=False), 500, {"Content-Type": "application/json"})