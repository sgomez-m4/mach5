import os
import re
import json
import time
import requests
import feedparser
import functions_framework
import html2text
from bs4 import BeautifulSoup
from google.cloud import storage
from google import genai
from google.genai import types
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

CORREO_ORIGEN = os.environ.get("CORREO_ORIGEN", "tu_correo@gmail.com")
PASSWORD_APP = os.environ.get("PASSWORD_APP", "tu_contraseña_de_aplicacion")
CORREO_DESTINO = os.environ.get("CORREO_DESTINO", "tu-correo-destino@email.com")

# =====================================================================
# CONFIGURACIÓN DEL ENTORNO
# =====================================================================
# CIKs de Aerolíneas Extranjeras Activas (Formularios 6-K)
CIKS_A_MONITOREAR = [
    '0001432364', # AZUL SA (Brasil)
    '0002009640', # Cambodia Airways Co., Ltd. (Camboya)
    '0001520504', # Volaris (México)
    '0001345105', # Copa Holdings, S.A. (Panamá)
    '0002013602', # Flybondi Holdings plc (Argentina / Reino Unido)
    '0002053229', # Flybondi Ltd (Argentina / Reino Unido)
    '0001291733', # Gol Intelligent Airlines Inc. (Brasil)
    '0001410706', # Gol Finance (Brasil)
    '0002060486', # GOL Equity Finance (Brasil / Luxemburgo)
    '0001561861', # Grupo Aeromexico, S.A.B. de C.V. (México)
    '0001047716', # LATAM AIRLINES GROUP S.A. (Chile)
    '0001038683'  # RYANAIR HOLDINGS PLC (Irlanda)
]

BUCKET_NAME = os.environ.get("BUCKET_NAME")
PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
LOCATION = os.environ.get("GCP_LOCATION", "us-east1")

HEADERS_SEC = {"User-Agent": "MiEmpresaAnalisis santiago@tuemail.com"}

# Límite de caracteres de texto combinado (filing + adjuntos) que se envía a Gemini
LIMITE_TEXTO_TOTAL = 45000

# Extensiones de archivo que se ignoran al recorrer los documentos del filing
# (no aportan texto útil para el análisis: XBRL, imágenes, hojas de estilo, etc.)
EXTENSIONES_IGNORADAS = (".xml", ".xsd", ".jpg", ".jpeg", ".png", ".gif", ".css", ".js")

# Conversor HTML -> Markdown. Se configura para minimizar tokens:
# sin envolver líneas, sin links/imágenes (no aportan al análisis de flota)
# y colapsando el ruido típico de las tablas HTML de EDGAR.
_html2md = html2text.HTML2Text()
_html2md.body_width = 0
_html2md.ignore_links = True
_html2md.ignore_images = True
_html2md.ignore_emphasis = False
_html2md.single_line_break = True
_html2md.unicode_snob = True

# Inicialización de clientes
storage_client = storage.Client()

# Cliente Gemini vía Vertex AI — usa la cuenta de servicio del contenedor,
# sin necesidad de API key ni variables de entorno adicionales.
ai_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION
)

# =====================================================================
# FUNCIONES AUXILIARES
# =====================================================================

def obtener_historial_ids():
    """Descarga el historial de IDs de reportes 6-K ya procesados desde Cloud Storage."""
    bucket = storage_client.bucket(BUCKET_NAME)
    # Archivo independiente para no chocar con el de los 8-K
    blob = bucket.blob("historial_reportes_6k.txt")
    if blob.exists():
        contenido = blob.download_as_text()
        return set(contenido.splitlines())
    return set()


def guardar_nuevo_id(historial_ids, nuevo_id):
    """Sincroniza y guarda el nuevo ID procesado en el archivo de Cloud Storage."""
    historial_ids.add(nuevo_id)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob("historial_reportes_6k.txt")
    blob.upload_from_string("\n".join(historial_ids))


def extraer_markdown_pagina(url_html):
    """
    Descarga una URL y la convierte a Markdown compacto (en vez de texto plano),
    lo que reduce significativamente los tokens enviados a Gemini frente a un
    soup.get_text() plano: colapsa tablas, omite links/imágenes y normaliza
    espacios/saltos de línea, preservando la estructura (títulos, listas, negritas)
    que ayuda al modelo a ubicar rápido secciones relevantes (ej. exhibits de flota).
    """
    try:
        res = requests.get(url_html, headers=HEADERS_SEC, timeout=15)
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.content, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()

        markdown = _html2md.handle(str(soup))

        # Colapsa 3+ saltos de línea seguidos en máximo 2 (ruido típico de EDGAR)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
        return markdown

    except Exception as e:
        print(f"Error extrayendo/convirtiendo a Markdown {url_html}: {e}")
    return None


def obtener_urls_documentos(url_index):
    """
    Recorre la página índice del filing (Filing Detail) en EDGAR y devuelve
    las URLs de TODOS los documentos asociados (el 6-K principal + exhibits,
    p.ej. comunicados de prensa o presentaciones), que es donde suele estar
    la información real sobre flota (compras, leasing, retiros, entregas, etc.).
    """
    urls = []
    try:
        res = requests.get(url_index, headers=HEADERS_SEC, timeout=15)
        if res.status_code != 200:
            return [url_index]

        soup = BeautifulSoup(res.content, "html.parser")
        tabla = soup.find("table", class_="tableFile")
        if not tabla:
            return [url_index]

        for fila in tabla.find_all("tr")[1:]:
            celdas = fila.find_all("td")
            if len(celdas) < 3:
                continue
            link_tag = celdas[2].find("a")
            if not link_tag or not link_tag.get("href"):
                continue
            href = link_tag["href"]
            if href.lower().endswith(EXTENSIONES_IGNORADAS):
                continue
            url_completa = href if href.startswith("http") else f"https://www.sec.gov{href}"
            urls.append(url_completa)

    except Exception as e:
        print(f"Error obteniendo la lista de documentos del filing {url_index}: {e}")

    return urls if urls else [url_index]


def extraer_texto_completo_filing(url_index):
    """
    Combina el texto (en Markdown) del filing principal con el de todos sus
    documentos adjuntos (exhibits), respetando un límite total de caracteres
    para no exceder el contexto enviado a Gemini.
    """
    urls_documentos = obtener_urls_documentos(url_index)
    print(f"   → {len(urls_documentos)} documento(s) encontrados en el filing.")

    fragmentos = []
    total_chars = 0

    for url_doc in urls_documentos:
        texto_md = extraer_markdown_pagina(url_doc)
        if not texto_md:
            continue
        fragmento = f"\n\n--- DOCUMENTO: {url_doc} ---\n{texto_md}"
        fragmentos.append(fragmento)
        total_chars += len(fragmento)
        if total_chars >= LIMITE_TEXTO_TOTAL:
            break
        time.sleep(0.2)  # cortesía hacia los servidores de la SEC

    texto_final = "".join(fragmentos)
    return texto_final[:LIMITE_TEXTO_TOTAL] if texto_final else None


def generar_analisis_gemini(texto_reporte, titulo_reporte):
    """
    Envía el texto combinado (filing 6-K + adjuntos) a Gemini y obtiene una
    respuesta ESTRUCTURADA en JSON, incluyendo una decisión explícita
    (impacto_flota: true/false) que se usa para decidir si se envía el correo.
    """
    if not texto_reporte:
        return {
            "resumen_evento": "No se pudo extraer el texto del reporte ni de sus documentos adjuntos.",
            "impacto_flota": True,  # ante la duda, se prefiere notificar para revisión manual
            "detalle_impacto": "No fue posible analizar el contenido automáticamente; se recomienda revisión manual.",
            "es_error": True
        }

    prompt = f"""
    Eres un consultor aeronáutico experto en gestión de flota. Analiza la siguiente presentación 6-K ante la SEC
    (correspondiente a un emisor privado extranjero), INCLUYENDO el documento principal y todos sus documentos
    adjuntos (exhibits, comunicados de prensa, presentaciones a inversionistas, etc.), ya que la información
    relevante sobre flota suele estar en los adjuntos y no en el cuerpo principal del 6-K.

    Reporte: {titulo_reporte}

    Responde en español con:
    - resumen_evento: máximo dos párrafos explicando qué hecho material o financiero principal se notifica
      (texto plano o HTML simple con <b>, <ul>, <li>, <p>).
    - impacto_flota: true SOLO si el documento (incluyendo adjuntos) implica cambios en la flota de la aerolínea
      (ej. compras de nuevas aeronaves, pasivos por arrendamiento/leasing, retiros, retrasos en entregas,
      venta de activos, cambios en la composición de la flota). false si no hay relación con la flota.
    - detalle_impacto: si impacto_flota es true, explica específicamente qué cambio de flota se identifica y en
      qué documento/sección se encontró. Si es false, indícalo explícitamente
      (ej. "No se identifican impactos o cambios relacionados con la flota en este reporte ni en sus adjuntos").

    Texto del reporte SEC y sus documentos adjuntos:
    {texto_reporte}
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "resumen_evento": {"type": "STRING"},
                        "impacto_flota": {"type": "BOOLEAN"},
                        "detalle_impacto": {"type": "STRING"},
                    },
                    "required": ["resumen_evento", "impacto_flota", "detalle_impacto"]
                }
            )
        )
        resultado = json.loads(response.text)
        # Aseguramos que las claves esperadas existan siempre
        resultado.setdefault("resumen_evento", "")
        resultado.setdefault("impacto_flota", False)
        resultado.setdefault("detalle_impacto", "")
        resultado["es_error"] = False
        return resultado

    except Exception as e:
        print(f"Error en la API de Gemini: {e}")
        return {
            "resumen_evento": "Error al generar el análisis automatizado con Gemini.",
            "impacto_flota": True,  # ante un error de análisis, se prefiere notificar para revisión manual
            "detalle_impacto": f"Ocurrió un error al analizar el reporte automáticamente: {e}",
            "es_error": True
        }


def enviar_correo_alerta(titulo, link, cik, analisis):
    """Ejecuta el envío del correo directamente usando SMTP nativo de Gmail."""
    es_error = analisis.get("es_error", False)

    if es_error:
        banner = """
        <div style="background-color: #fdecea; padding: 14px 18px; border-left: 4px solid #d93025; font-family: Arial, sans-serif; margin-bottom: 16px;">
            <b>⚠️ No fue posible completar el análisis automático de este reporte.</b><br/>
            Se recomienda revisión manual: no se pudo confirmar ni descartar impacto en flota.
        </div>
        """
        encabezado = "⚠️ Monitoreo Automático SEC: Error de Análisis — Revisión Manual Requerida (6-K)"
        asunto = f"⚠️ REVISIÓN MANUAL (error de análisis, 6-K): {titulo[:40]}... (CIK: {cik})"
    else:
        banner = ""
        encabezado = "✈️ Monitoreo Automático SEC: Posible Impacto en Flota Detectado (6-K, Extranjero)"
        asunto = f"✈️ Impacto en Flota Detectado (6-K): {titulo[:40]}... (CIK: {cik})"

    cuerpo_html = f"""
    <h2>{encabezado}</h2>
    {banner}
    <p><b>Identificador de Empresa (CIK):</b> {cik}</p>
    <p><b>Enlace Original SEC:</b> <a href="{link}">{titulo}</a></p>
    <hr/>
    <h3>📋 Resumen del Evento (AI - Gemini 2.5 Flash):</h3>
    <div style="background-color: #f9f9f9; padding: 18px; border-left: 4px solid #1a73e8; font-family: Arial, sans-serif; line-height: 1.6;">
        {analisis.get('resumen_evento', '')}
    </div>
    <h3>🛩️ Impacto en Flota:</h3>
    <div style="background-color: #fff4e5; padding: 18px; border-left: 4px solid #e8871a; font-family: Arial, sans-serif; line-height: 1.6;">
        {analisis.get('detalle_impacto', '')}
    </div>
    <hr/>
    <p style="font-size: 11px; color: #777;">Alerta automatizada generada desde GCP Cloud Functions. Se analizaron el documento principal y sus adjuntos (formulario 6-K, emisores extranjeros).</p>
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
        print(f"✅ Correo enviado exitosamente vía Gmail para CIK {cik}")

    except Exception as e:
        print(f"❌ Error crítico al enviar el correo vía Gmail: {e}")


# =====================================================================
# DISPARADOR HTTP PRINCIPAL (Cloud Function)
# =====================================================================
@functions_framework.http
def monitorear_sec_event(request):
    """Punto de entrada HTTP de la Cloud Function para 6-K (aerolíneas extranjeras)."""
    print("Iniciando verificación periódica de reportes 6-K en la SEC (Aerolíneas Extranjeras)...")
    historial_ids = obtener_historial_ids()
    filings_procesados = 0
    alertas_impacto_confirmado = 0
    alertas_por_error_analisis = 0

    for cik in CIKS_A_MONITOREAR:
        url_feed = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={cik}&type=6-K"
            f"&dateb=&owner=exclude&start=0&count=10&output=atom"
        )

        try:
            res = requests.get(url_feed, headers=HEADERS_SEC, timeout=10)
            if res.status_code != 200:
                print(f"No se pudo acceder al feed del CIK {cik}. Código: {res.status_code}")
                continue

            feed = feedparser.parse(res.content)

            for entrada in feed.entries:
                reporte_id = entrada.id
                categoria = entrada.get("category", "").upper()
                titulo = entrada.title
                link_reporte = entrada.link

                if "6-K" not in categoria:
                    continue
                if reporte_id in historial_ids:
                    continue

                print(f"🔥 Nuevo reporte 6-K detectado para CIK {cik}. Procesando (incluyendo adjuntos)...")
                filings_procesados += 1

                texto_completo = extraer_texto_completo_filing(link_reporte)
                analisis = generar_analisis_gemini(texto_completo, titulo)

                if analisis.get("impacto_flota"):
                    enviar_correo_alerta(titulo, link_reporte, cik, analisis)
                    if analisis.get("es_error"):
                        alertas_por_error_analisis += 1
                    else:
                        alertas_impacto_confirmado += 1
                else:
                    print(f"   ℹ️ Sin impacto en flota. No se envía correo. CIK {cik} — {titulo[:60]}")

                # Se marca como procesado siempre, tenga o no impacto en flota,
                # para no volver a analizarlo en la siguiente ejecución.
                guardar_nuevo_id(historial_ids, reporte_id)

        except Exception as e:
            print(f"Error procesando eventos para el CIK {cik}: {e}")

    return {
        "status": "success",
        "filings_6k_procesados": filings_procesados,
        "alertas_con_impacto_flota_enviadas": alertas_impacto_confirmado,
        "alertas_por_error_analisis_enviadas": alertas_por_error_analisis
    }, 200