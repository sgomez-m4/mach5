import functions_framework
import time
from google.cloud import storage
from google import genai
from google.genai import types

# Configuraciones del proyecto
PROJECT_ID = "mach5-gemini-project"
LOCATION = "global"
MODEL_NAME = "gemini-3-flash-preview"

# Configuraciones de Storage
BUCKET_NAME = "bucket-edgar"
SOURCE_PREFIX = "10k-item2-md/"
TARGET_PREFIX = "10k-item2-json/"

# System Instruction con los esquemas JSON estrictos
SYSTEM_INSTRUCTION = """
Actúa como experto en ingeniería de datos aéreos y Business Intelligence.
Tu tarea es extraer datos de tablas de flota de reportes SEC y devolver estrictamente un JSON válido.

### ESQUEMA 1: current_fleet_fact
Campos obligatorios:
- fleet_id: concatenación única (airline_year_model_ownership)
- airline: nombre de la aerolínea
- report_year: año del reporte financiero (entero)
- aircraft_type: modelo exacto del avión (ej. A321neo)
- ownership_type: "Owned", "Operating Lease", o "Finance Lease"
- quantity: cantidad de aeronaves (entero)
- seating_capacity_avg: capacidad promedio (entero o null)
- average_age_years: edad promedio (float o null)
- remaining_lease_years: años restantes del arrendamiento (float o null; usar null si es "Owned")

### ESQUEMA 2: order_book_fact
Campos obligatorios:
- order_id: concatenación única (airline_reportyear_model_deliveryyear)
- airline: nombre de la aerolínea
- report_year: año del reporte financiero (entero)
- aircraft_type: modelo exacto del avión
- delivery_period: año de entrega esperado (string, ej. "2026", "After 2028")
- quantity_committed: cantidad de aeronaves comprometidas (entero)

Reglas Críticas:
1. Desnormaliza las columnas dinámicas de propiedad y años en filas.
2. Devuelve ÚNICAMENTE un objeto JSON con dos claves raíz: "current_fleet" y "future_commitments". No uses bloques de código markdown en la salida.
"""


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


def process_with_gemini(text_content):
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION
    )

    # Resolución del error: Pasar el string directamente 
    contents = f"Procesa el siguiente texto Markdown de la tabla de flota y extrae los datos al formato JSON requerido:\n\n{text_content}"
    
    generate_content_config = types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        max_output_tokens=65535,
        response_mime_type="application/json",
        system_instruction=SYSTEM_INSTRUCTION,
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
        thinking_config=types.ThinkingConfig(
            thinking_level="HIGH"
        )
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=generate_content_config,
    )
    
    advertir_si_truncado(response)
    return response.text

@functions_framework.http
def extract_fleet_http(request):
    t_inicio_total = time.time()
    print(f"[START] Inicio de ejecución: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    
    archivos_procesados = []
    archivos_omitidos = []
    errores = []

    # Listar blobs primero para saber el total
    t_list = time.time()
    blobs = list(bucket.list_blobs(prefix=SOURCE_PREFIX))
    t_list = round(time.time() - t_list, 2)
    
    total_md = sum(1 for b in blobs if b.name.endswith(".md"))
    print(f"[LISTADO] {total_md} archivos .md encontrados en {t_list}s")

    procesados_count = 0
    omitidos_count = 0
    errores_count = 0

    for idx, blob in enumerate(blobs, 1):
        if not blob.name.endswith(".md"):
            continue

        t_archivo = time.time()
        file_name_only = blob.name.split("/")[-1]
        output_file_name = file_name_only.replace(".md", ".json")
        target_path = f"{TARGET_PREFIX}{output_file_name}"

        # --- Verificación de existencia ---
        t_check = time.time()
        ya_existe = bucket.blob(target_path).exists()
        t_check = round(time.time() - t_check, 3)
        
        if ya_existe:
            omitidos_count += 1
            archivos_omitidos.append(target_path)
            print(f"[{idx}] OMITIDO ({t_check}s check) -> {target_path}")
            continue

        print(f"[{idx}] PROCESANDO -> {blob.name}")
        
        # --- Descarga ---
        t_download = time.time()
        content = blob.download_as_text()
        t_download = round(time.time() - t_download, 2)
        print(f"      ↳ Descarga: {t_download}s | Tamaño: {len(content):,} chars")

        try:
            # --- Gemini ---
            t_gemini = time.time()
            json_output = process_with_gemini(content)
            t_gemini = round(time.time() - t_gemini, 2)
            print(f"      ↳ Gemini: {t_gemini}s | Respuesta: {len(json_output):,} chars")

            # --- Subida ---
            t_upload = time.time()
            output_blob = bucket.blob(target_path)
            output_blob.upload_from_string(
                json_output,
                content_type='application/json'
            )
            t_upload = round(time.time() - t_upload, 2)
            
            archivos_procesados.append(target_path)
            procesados_count += 1
            t_total_archivo = round(time.time() - t_archivo, 2)
            
            print(f"      ↳ Subida GCS: {t_upload}s")
            print(f"      ✓ COMPLETADO en {t_total_archivo}s -> {target_path}")

        except Exception as e:
            errores_count += 1
            t_error = round(time.time() - t_archivo, 2)
            error_msg = f"Fallo en {blob.name}: {str(e)}"
            print(f"      ✗ ERROR en {t_error}s -> {error_msg}")
            errores.append(error_msg)

        # --- Progreso cada 5 archivos ---
        if procesados_count > 0 and procesados_count % 5 == 0:
            t_transcurrido = round(time.time() - t_inicio_total, 1)
            print(f"[PROGRESO] {procesados_count} procesados | {omitidos_count} omitidos | {errores_count} errores | Tiempo acumulado: {t_transcurrido}s")

    # --- Resumen final ---
    t_total = round(time.time() - t_inicio_total, 2)
    print(f"[END] Finalizado en {t_total}s")
    print(f"      Procesados: {procesados_count} | Omitidos: {omitidos_count} | Errores: {errores_count}")

    return {
        "status": "completado",
        "tiempo_total_segundos": t_total,
        "total_encontrados": total_md,
        "total_procesados": procesados_count,
        "total_omitidos": omitidos_count,
        "total_errores": errores_count,
        "archivos": archivos_procesados,
        "omitidos": archivos_omitidos,
        "errores": errores
    }, 200