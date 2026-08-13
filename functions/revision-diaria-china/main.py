import os
import re
import json
import time
import requests
import tempfile
import functions_framework
import html2text
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from google.cloud import storage
from google import genai
from google.genai import types
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
CORREO_ORIGEN = os.environ.get("CORREO_ORIGEN", "tu_correo@gmail.com")
PASSWORD_APP = os.environ.get("PASSWORD_APP", "tu_password_app")
CORREO_DESTINO = os.environ.get("CORREO_DESTINO", "destino@email.com")

BUCKET_NAME = os.environ.get("BUCKET_NAME", "bucket-edgar")
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "mach5-gemini-project")
LOCATION = "us-central1"

# ÚNICO archivo que se escribe en el bucket: la guía de ya-procesados.
HISTORIAL_BLOB = "historial_anuncios_china.txt"

AEROLINEAS = [
    {"codigo": "601111", "nombre": "Air China", "mercado": "A-Share", "column": "sse"},
    {"codigo": "600115", "nombre": "China Eastern Airlines", "mercado": "A-Share", "column": "sse"},
    {"codigo": "600029", "nombre": "China Southern Airlines", "mercado": "A-Share", "column": "sse"},
    {"codigo": "600221", "nombre": "Hainan Airlines", "mercado": "A-Share", "column": "sse"},
    {"codigo": "601021", "nombre": "Spring Airlines", "mercado": "A-Share", "column": "sse"},
    {"codigo": "603885", "nombre": "Juneyao Airlines", "mercado": "A-Share", "column": "szse"},
    {"codigo": "002928", "nombre": "China Express Airlines", "mercado": "A-Share", "column": "szse"},
    {"codigo": "00293", "nombre": "Cathay Pacific Airways", "mercado": "A-Share", "column": "hke"},
]

HEADERS_CNINFO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest"
}
HEADERS_GENERICOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

LIMITE_TEXTO = 45000

_html2md = html2text.HTML2Text()
_html2md.body_width = 0
_html2md.ignore_links = True
_html2md.ignore_images = True
_html2md.single_line_break = True
_html2md.unicode_snob = True

# El cliente de Storage se usa EXCLUSIVAMENTE para leer/escribir el historial.
storage_client = storage.Client()

ai_client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)


# =====================================================================
# HISTORIAL (única persistencia de la función)
# =====================================================================
def obtener_historial():
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(HISTORIAL_BLOB)
    if blob.exists():
        return set(blob.download_as_text().splitlines())
    return set()


def guardar_historial(historial, nuevo_id):
    historial.add(nuevo_id)
    bucket = storage_client.bucket(BUCKET_NAME)
    bucket.blob(HISTORIAL_BLOB).upload_from_string("\n".join(historial))


# =====================================================================
# OBTENCIÓN DE ANUNCIOS (solo lectura de APIs externas)
# =====================================================================
def obtener_anuncios_ashare(aerolinea):
    try:
        ts_res = requests.post(
            "https://www.cninfo.com.cn/new/information/topSearch/query",
            headers=HEADERS_CNINFO,
            data={"keyWord": aerolinea["codigo"], "maxNum": 10},
            timeout=15
        )
        org_id = next(
            (m.get("orgId", "") for m in ts_res.json() if m.get("code") == aerolinea["codigo"]),
            ""
        )
        if not org_id:
            return []
    except Exception as e:
        print(f"Error orgId {aerolinea['codigo']}: {e}")
        return []

    payload = {
        "pageNum": 1, "pageSize": 30,
        "column": aerolinea["column"],
        "tabName": "fulltext",
        "stock": f"{aerolinea['codigo']},{org_id}",
        "searchkey": "", "category": "", "isHLtitle": "true"
    }
    try:
        res = requests.post(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            headers=HEADERS_CNINFO, data=payload, timeout=15
        )
        anuncios = res.json().get("announcements") or []

        resultados = []
        for ann in anuncios:
            adj_url = ann.get("adjunctUrl", "")
            if not adj_url:
                continue
            resultados.append({
                "id": str(ann.get("announcementId", "")),
                "titulo": ann.get("announcementTitle", ""),
                "url": "http://static.cninfo.com.cn/" + adj_url,
                "fuente": "CNINFO"
            })
        return resultados
    except Exception as e:
        print(f"Error anuncios CNINFO {aerolinea['codigo']}: {e}")
        return []


def obtener_anuncios_hshare(aerolinea):
    params = {
        "page_size": 30, "page_index": 1,
        "ann_type": "H", "client_source": "web",
        "stock_list": aerolinea["codigo"].zfill(5), "f_node": "0"
    }
    try:
        res = requests.get(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            headers=HEADERS_GENERICOS, params=params, timeout=15
        )
        ann_list = res.json().get("data", {}).get("list", []) or []

        resultados = []
        for ann in ann_list:
            art_code = ann.get("art_code", "")
            if not art_code:
                continue
            resultados.append({
                "id": art_code,
                "titulo": ann.get("title", ""),
                "url": f"http://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
                "fuente": "EastMoney"
            })
        return resultados
    except Exception as e:
        print(f"Error anuncios EastMoney: {e}")
        return []


# =====================================================================
# EXTRACCIÓN DE TEXTO (100% en memoria; el temp file se borra al terminar)
# =====================================================================
def extraer_texto_anuncio(anuncio):
    try:
        res = requests.get(anuncio["url"], headers=HEADERS_GENERICOS, timeout=30)
        if res.status_code != 200:
            return None
        if res.content.startswith(b"%PDF-"):
            return extraer_texto_pdf(res.content)
        return extraer_texto_html(res.content)
    except Exception as e:
        print(f"Error extrayendo {anuncio['url']}: {e}")
        return None


def extraer_texto_pdf(contenido_bytes):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(contenido_bytes)
            tmp_path = f.name

        doc = fitz.open(tmp_path)
        texto = []
        for i, page in enumerate(doc):
            if i >= 30:
                break
            texto.append(page.get_text("text"))
        doc.close()
        return "\n\n".join(texto)[:LIMITE_TEXTO]
    except Exception as e:
        print(f"Error parseando PDF: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def extraer_texto_html(contenido_bytes):
    try:
        soup = BeautifulSoup(contenido_bytes, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        md = _html2md.handle(str(soup))
        return re.sub(r"\n{3,}", "\n\n", md).strip()[:LIMITE_TEXTO]
    except Exception as e:
        print(f"Error parseando HTML: {e}")
        return None


# =====================================================================
# ANÁLISIS GEMINI
# =====================================================================
def analizar_impacto_flota(texto, titulo, aerolinea):
    if not texto:
        return {
            "resumen_evento": "No se pudo extraer el texto del anuncio.",
            "impacto_flota": True,
            "detalle_impacto": "Revisión manual requerida: no se pudo extraer el contenido.",
            "es_error": True
        }

    prompt = f"""
Eres un analista aeronáutico experto en flotas de aerolíneas chinas.
Analiza el siguiente anuncio corporativo de {aerolinea['nombre']} y determina si implica cambios en su flota.

Título del anuncio: {titulo}

Responde en español con:
- resumen_evento: máximo 2 párrafos explicando el hecho material notificado
  (texto plano o HTML simple con <b>, <ul>, <li>, <p>).
- impacto_flota: true SOLO si el anuncio implica cambios en la flota:
  • Compras/órdenes de nuevas aeronaves (Boeing, Airbus, COMAC C919/C909/C929)
  • Entregas de aviones
  • Retiros de aeronaves
  • Nuevos contratos de leasing o venta de aviones
  • Cambios en composición o capacidad instalada de flota
  false si NO tiene relación con flota (resultados financieros, dividendos,
  cambios de directivos, boletines mensuales de tráfico, reuniones de accionistas).
- detalle_impacto: si impacto_flota es true, especifica modelos, cantidades y fechas.
  Si es false, explica brevemente de qué trata el anuncio.

Anuncio (chino simplificado, tradicional o inglés):
{texto}
"""
    try:
        response = ai_client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "resumen_evento": {"type": "STRING"},
                        "impacto_flota": {"type": "BOOLEAN"},
                        "detalle_impacto": {"type": "STRING"}
                    },
                    "required": ["resumen_evento", "impacto_flota", "detalle_impacto"]
                }
                # ❌ ELIMINADO: thinking_config=types.ThinkingConfig(thinking_level="HIGH")
            )
        )
        resultado = json.loads(response.text)
        resultado.setdefault("resumen_evento", "")
        resultado.setdefault("impacto_flota", False)
        resultado.setdefault("detalle_impacto", "")
        resultado["es_error"] = False
        return resultado
    except Exception as e:
        print(f"Error en Gemini: {e}")
        return {
            "resumen_evento": "Error al analizar con IA.",
            "impacto_flota": True,
            "detalle_impacto": f"Error: {e}. Revisión manual recomendada.",
            "es_error": True
        }


# =====================================================================
# ENVÍO DE CORREO (única salida visible de la función)
# =====================================================================
def enviar_correo_alerta(titulo, url, aerolinea, analisis, fuente):
    es_error = analisis.get("es_error", False)

    if es_error:
        asunto = f"⚠️ REVISIÓN MANUAL: {aerolinea['nombre']} — {titulo[:40]}..."
        encabezado = "⚠️ Monitoreo Aerolíneas China: Error de Análisis"
    else:
        asunto = f"✈️ Impacto Flota: {aerolinea['nombre']} — {titulo[:40]}..."
        encabezado = "✈️ Monitoreo Aerolíneas China: Impacto en Flota"

    cuerpo_html = f"""
<html><body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <h2 style="color: #1a73e8;">{encabezado}</h2>
    <p><b>Aerolínea:</b> {aerolinea['nombre']} ({aerolinea['codigo']} — {aerolinea['mercado']})</p>
    <p><b>Fuente:</b> {fuente}</p>
    <p><b>Enlace al anuncio:</b> <a href="{url}">{titulo}</a></p>
    <hr/>
    <h3>📋 Resumen del Anuncio (Gemini 2.5 Flash):</h3>
    <div style="background-color: #f9f9f9; padding: 18px; border-left: 4px solid #1a73e8;">
        {analisis.get('resumen_evento', '')}
    </div>
    <h3>🛩️ Impacto en Flota:</h3>
    <div style="background-color: #fff4e5; padding: 18px; border-left: 4px solid #e8871a;">
        {analisis.get('detalle_impacto', '')}
    </div>
    <hr/>
    <p style="font-size: 11px; color: #777;">
        Alerta automatizada de monitoreo en tiempo real. No modifica datos históricos de flota.
    </p>
</body></html>
"""
    msg = MIMEMultipart()
    msg['From'] = CORREO_ORIGEN
    msg['To'] = CORREO_DESTINO
    msg['Subject'] = asunto
    msg.attach(MIMEText(cuerpo_html, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(CORREO_ORIGEN, PASSWORD_APP)
        server.send_message(msg)
        server.quit()
        print(f"✅ Correo enviado: {aerolinea['nombre']}")
        return True
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")
        return False


# =====================================================================
# ENTRYPOINT
# =====================================================================
@functions_framework.http
def monitorear_aerolineas_chinas(request):
    print("🔍 Iniciando monitoreo de aerolíneas chinas...")
    t_inicio = time.time()

    historial = obtener_historial()

    stats = {"anuncios_nuevos": 0, "alertas_enviadas": 0, "sin_impacto": 0, "errores": 0}
    alertas_detalle = []

    for aerolinea in AEROLINEAS:
        if aerolinea["mercado"] == "A-Share":
            anuncios = obtener_anuncios_ashare(aerolinea)
        else:
            anuncios = obtener_anuncios_hshare(aerolinea)

        print(f"📡 {aerolinea['nombre']}: {len(anuncios)} anuncio(s) consultado(s)")

        for anuncio in anuncios:
            anuncio_id = f"{aerolinea['codigo']}_{anuncio['id']}"

            # Deduplicación por historial: ya procesado → skip
            if anuncio_id in historial:
                continue

            stats["anuncios_nuevos"] += 1
            print(f"   🔔 Nuevo: {anuncio['titulo'][:70]}")

            # Todo en memoria: nada se sube al bucket
            texto = extraer_texto_anuncio(anuncio)
            analisis = analizar_impacto_flota(texto, anuncio["titulo"], aerolinea)

            if analisis.get("es_error") or analisis.get("impacto_flota"):
                enviado = enviar_correo_alerta(
                    anuncio["titulo"], anuncio["url"], aerolinea, analisis, anuncio["fuente"]
                )
                if enviado:
                    stats["alertas_enviadas"] += 1
                    alertas_detalle.append({
                        "aerolinea": aerolinea["nombre"],
                        "titulo": anuncio["titulo"],
                        "url": anuncio["url"]
                    })
                if analisis.get("es_error"):
                    stats["errores"] += 1
            else:
                stats["sin_impacto"] += 1
                print(f"   ℹ️ Sin impacto en flota. Sin correo.")

            # Se marca como procesado SIEMPRE (con o sin alerta)
            guardar_historial(historial, anuncio_id)

            time.sleep(0.5)

    t_total = round(time.time() - t_inicio, 2)
    print(f"✅ Completado en {t_total}s | Alertas: {stats['alertas_enviadas']}")

    return {
        "status": "success",
        "persistencia": f"solo {HISTORIAL_BLOB} ({len(historial)} IDs acumulados)",
        "tiempo_ejecucion_segundos": t_total,
        "anuncios_nuevos": stats["anuncios_nuevos"],
        "alertas_enviadas": stats["alertas_enviadas"],
        "sin_impacto": stats["sin_impacto"],
        "errores_analisis": stats["errores"],
        "alertas": alertas_detalle
    }, 200
