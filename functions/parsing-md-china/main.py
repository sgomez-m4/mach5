import os
import tempfile
import traceback
import re
import requests
import fitz  # PyMuPDF
import pymupdf4llm  # <-- NUEVA LIBRERÍA
from google.cloud import storage
import functions_framework

# Inicializar cliente de Google Cloud Storage
storage_client = storage.Client()

# Usar una sesión global para reutilizar conexiones y cookies
http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*"
})

BUCKET_NAME = "bucket-edgar"
PDF_PREFIX = "anual-china/"
MD_PREFIX = "anual-china-md/"

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

def clean_company_name(name: str) -> str:
    """Limpia el nombre de la empresa para usarlo en nombres de archivo."""
    return re.sub(r'[^a-zA-Z0-9]', '', name)

# ------------------------------------------------------------------------------
# PROCEDIMIENTO 1: A-SHARES (API Oficial CNINFO)
# ------------------------------------------------------------------------------
def get_a_share_report_url(item: dict) -> tuple[str, str]:
    code = item["code"]
    company_slug = clean_company_name(item["company"])
    column = item.get("column", "sse")
    
    # 1. Obtener el orgId requerido por la API de CNINFO
    top_search_url = "https://www.cninfo.com.cn/new/information/topSearch/query"
    ts_headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest"
    }
    ts_payload = {"keyWord": code, "maxNum": 10}
    
    ts_res = http_session.post(top_search_url, headers=ts_headers, data=ts_payload, timeout=20)
    ts_res.raise_for_status()
    ts_data = ts_res.json()
    
    org_id = ""
    for match in ts_data:
        if match.get("code") == code:
            org_id = match.get("orgId", "")
            break
            
    if not org_id:
        raise ValueError(f"[A-Share] No se pudo encontrar el orgId interno para {code}")

    # 2. Buscar anuncios
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "pageNum": 1,
        "pageSize": 30,
        "column": column,
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": "category_ndbg_szsh;",
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true"
    }
    
    res = http_session.post(url, headers=ts_headers, data=payload, timeout=20)
    res.raise_for_status()
    data = res.json()
    
    announcements = data.get("announcements") or []
    
    if not announcements:
        # Reintento sin filtro estricto de categoría
        payload.pop("category", None)
        res = http_session.post(url, headers=ts_headers, data=payload, timeout=20)
        announcements = res.json().get("announcements") or []

    # Filtrar descartando resúmenes (摘要) y cancelados
    valid_reports = []
    for ann in announcements:
        title = ann.get("announcementTitle", "")
        if "年度报告" in title and "摘要" not in title and "取消" not in title:
            valid_reports.append(ann)

    if not valid_reports:
        raise ValueError(f"[A-Share] No se encontró reporte anual completo en CNINFO para {item['company']} ({code})")

    latest = valid_reports[0]
    pdf_url = "http://static.cninfo.com.cn/" + latest["adjunctUrl"]
    
    # Formatear fecha
    timestamp_ms = latest.get("announcementTime", 0)
    if timestamp_ms:
        from datetime import datetime
        date_str = datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y%m%d")
    else:
        date_str = "latest"

    file_base_name = f"{code}_{company_slug}_{date_str}_annual_report"
    return pdf_url, file_base_name

# ------------------------------------------------------------------------------
# PROCEDIMIENTO 2: H-SHARES (API EastMoney)
# ------------------------------------------------------------------------------
def get_h_share_report_url(item: dict) -> tuple[str, str]:
    code = item["code"].zfill(5)
    company_slug = clean_company_name(item["company"])

    base_url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    
    page_index = 1
    while page_index <= 5:
        params = {
            "page_size": 30,
            "page_index": page_index,
            "ann_type": "H",
            "client_source": "web",
            "stock_list": code,
            "f_node": "0"
        }

        res = http_session.get(base_url, params=params, timeout=20)
        res.raise_for_status()
        
        try:
            data = res.json()
        except ValueError:
            raise ValueError(f"[H-Share] La API de EastMoney no devolvió JSON válido para {item['company']} ({code})")

        ann_list = data.get("data", {}).get("list", []) or []
        if not ann_list:
            break

        keywords = ["年度報告", "年度报告", "Annual Report", "报告书", "報告書"]
        exclude_keywords = ["中期", "Interim", "摘要", "Summary", "業績公布", "业绩公布", "通函", "Circular"]

        valid_reports = []
        for ann in ann_list:
            title = ann.get("title", "")
            if any(k in title for k in keywords) and not any(e in title for e in exclude_keywords):
                valid_reports.append(ann)

        if valid_reports:
            latest = valid_reports[0]
            art_code = latest.get("art_code")
            if not art_code:
                raise ValueError(f"[H-Share] No se pudo extraer art_code para {item['company']} ({code})")
                
            pdf_url = f"http://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"
            notice_date = str(latest.get("notice_date", "latest")).split(" ")[0].replace("-", "")
            file_base_name = f"{code}_HK_{company_slug}_{notice_date}_annual_report"
            return pdf_url, file_base_name

        page_index += 1

    raise ValueError(f"[H-Share] No se encontró reporte anual para {item['company']} ({code})")

# ------------------------------------------------------------------------------
# DESCARGA, PARSEO Y SUBIDA A GCS
# ------------------------------------------------------------------------------
def download_and_parse_to_md(pdf_url: str, file_base_name: str) -> tuple[str, str]:
    bucket = storage_client.bucket(BUCKET_NAME)
    tmp_pdf_path = None

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/pdf,*/*"}
        response = http_session.get(pdf_url, headers=headers, timeout=300)
        response.raise_for_status()

        if not response.content.startswith(b"%PDF-"):
            raise ValueError(f"El archivo descargado no es un PDF válido (probablemente bloqueado por anti-bot). Inicio: {response.content[:100]}")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(response.content)
            tmp_pdf_path = tmp_file.name

        # 1. Guardar PDF en gs://bucket-edgar/anual-china/
        pdf_blob_name = f"{PDF_PREFIX}{file_base_name}.pdf"
        pdf_blob = bucket.blob(pdf_blob_name)
        pdf_blob.upload_from_filename(tmp_pdf_path, content_type="application/pdf")

        # 2. Convertir PDF a Markdown usando pymupdf4llm
        # Esta librería convierte tablas automáticamente a formato | col1 | col2 |
        # y elimina las coordenadas absolutas del HTML crudo.
        try:
            print(f"[{file_base_name}] Convirtiendo PDF a Markdown con pymupdf4llm...")
            markdown_content = pymupdf4llm.to_markdown(
                tmp_pdf_path,
                show_progress=False,
                write_images=False,  # No guardar imágenes para reducir tamaño
                page_chunks=True     # Mantener separación por páginas
            )
            
            # page_chunks=True devuelve una lista de dicts, los unimos
            if isinstance(markdown_content, list):
                full_markdown = "\n\n---\n\n".join([
                    f"<!-- Página {chunk.get('metadata', {}).get('page', i+1)} -->\n\n{chunk.get('text', '')}"
                    for i, chunk in enumerate(markdown_content)
                ])
            else:
                full_markdown = markdown_content
                
            print(f"[{file_base_name}] Conversión exitosa. Tamaño: {len(full_markdown)} caracteres")
            
        except Exception as conv_err:
            print(f"[{file_base_name}] pymupdf4llm falló, usando fallback de PyMuPDF: {conv_err}")
            # Fallback al método antiguo por si la librería tiene problemas con algún PDF específico
            doc = fitz.open(tmp_pdf_path)
            chunks = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text("html")
                chunks.append(f"<!-- Página {page_num + 1} -->\n\n{page_text}")
            doc.close()
            full_markdown = "\n\n".join(chunks)

        # 3. Guardar MD en gs://bucket-edgar/anual-china-md/
        md_blob_name = f"{MD_PREFIX}{file_base_name}.md"
        md_blob = bucket.blob(md_blob_name)
        md_blob.upload_from_string(
            full_markdown,
            content_type="text/markdown; charset=utf-8"
        )

        return pdf_blob_name, md_blob_name

    finally:
        if tmp_pdf_path and os.path.exists(tmp_pdf_path):
            os.remove(tmp_pdf_path)

# ------------------------------------------------------------------------------
# ENTRYPOINT HTTP
# ------------------------------------------------------------------------------
@functions_framework.http
def process_china_pdfs_http(request):
    if request.path == "/favicon.ico":
        return ("", 204)

    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    filter_code = request_json.get("code") or request_args.get("code")
    force = (request_args.get("force", "false").lower() == "true")

    airlines_to_process = AIRLINES
    if filter_code:
        airlines_to_process = [a for a in AIRLINES if a["code"] == str(filter_code)]

    bucket = storage_client.bucket(BUCKET_NAME)
    success_results = []
    skipped_results = []
    error_results = []

    for item in airlines_to_process:
        company = item["company"]
        code = item["code"]
        market = item["market"]

        try:
            if market == "A-Share":
                pdf_url, file_base_name = get_a_share_report_url(item)
            elif market == "H-Share":
                pdf_url, file_base_name = get_h_share_report_url(item)
            else:
                raise ValueError(f"Mercado no soportado: {market}")

            expected_md = f"{MD_PREFIX}{file_base_name}.md"
            if not force and bucket.blob(expected_md).exists():
                skipped_results.append({
                    "company": company,
                    "code": code,
                    "reason": "El archivo MD ya existe.",
                    "gcs_md_path": f"gs://{BUCKET_NAME}/{expected_md}"
                })
                continue

            pdf_blob_name, md_blob_name = download_and_parse_to_md(pdf_url, file_base_name)

            success_results.append({
                "company": company,
                "code": code,
                "market": market,
                "gcs_pdf_path": f"gs://{BUCKET_NAME}/{pdf_blob_name}",
                "gcs_md_path": f"gs://{BUCKET_NAME}/{md_blob_name}",
                "pdf_url": pdf_url
            })

        except Exception as e:
            print(f"Error procesando {company} ({code}): {traceback.format_exc()}")
            error_results.append({
                "company": company,
                "code": code,
                "error": f"{type(e).__name__}: {str(e)}"
            })

    return (
        {
            "status": "completed",
            "total_processed": len(success_results),
            "total_skipped": len(skipped_results),
            "total_failed": len(error_results),
            "successful": success_results,
            "skipped": skipped_results,
            "errors": error_results
        },
        200
    )