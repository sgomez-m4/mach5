import functions_framework
import time
import os
from google.cloud import storage
from google import genai
from google.genai import types

# =====================================================================
# CONFIGURACIONES DE ENTORNO Y PROYECTO
# =====================================================================
PROJECT_ID = "mach5-gemini-project"
LOCATION = "global" 
MODEL_NAME = "gemini-3-flash-preview"

# Ajuste de Prefijos para las carpetas dedicadas a los 20-F
BUCKET_NAME = "bucket-edgar"
SOURCE_PREFIX = "20f-flota-md/"
TARGET_PREFIX = "20f-flota-json/"

# =====================================================================
# SYSTEM INSTRUCTION (Optimizado para 20-F e Item 4 Internacional)
# =====================================================================
SYSTEM_INSTRUCTION = """
Actúa como experto en ingeniería de datos aéreos, consultoría de flota y Business Intelligence.
Tu tarea es extraer datos de las tablas e informes de flota a partir de los reportes anuales SEC Form 20-F (Item 4 / 4.B / 4.D) de aerolíneas internacionales y devolver estrictamente un JSON válido.

### ESQUEMA 1: current_fleet_fact
Campos obligatorios:
- fleet_id: concatenación única (airline_year_model_ownership)
- airline: nombre de la aerolínea
- report_year: año del reporte financiero (entero)
- aircraft_type: modelo exacto del avión (ej. A320neo, Boeing 737-800)
- ownership_type: "Owned", "Operating Lease", o "Finance Lease"
- quantity: cantidad de aeronaves activas/en flota (entero)
- seating_capacity_avg: capacidad promedio de asientos (entero o null)
- average_age_years: edad promedio de la sub-flota (float o null)
- remaining_lease_years: años restantes promedio del arrendamiento (float o null; usar null si es "Owned")

### ESQUEMA 2: order_book_fact
Campos obligatorios:
- order_id: concatenación única (airline_reportyear_model_deliveryyear)
- airline: nombre de la aerolínea
- report_year: año del reporte financiero (entero)
- aircraft_type: modelo exacto del avión comprometido
- delivery_period: año de entrega esperado (string, ej. "2026", "2027-2029", "After 2028")
- quantity_committed: cantidad de aeronaves comprometidas en el order book (entero)

Reglas Críticas de Datos:
1. Desnormaliza las columnas dinámicas de propiedad, tipos de arrendamiento y años de entrega directamente en filas individuales.
2. Devuelve ÚNICAMENTE un objeto JSON estructurado con dos claves raíz: "current_fleet" y "future_commitments". No incluyas bloques de código ni formatos markdown (como ```json) en tu respuesta.
"""

def process_with_gemini(text_content):
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION
    )

    contents = f"Procesa el siguiente texto Markdown de la sección de flota (20-F) y extrae los datos al formato JSON estructurado requerido:\n\n{text_content}"
    
    # -----------------------------------------------------------------
    # CONFIGURACIÓN PRINCIPAL: Gemini 3 con Razonamiento Avanzado
    # -----------------------------------------------------------------
    config_principal = types.GenerateContentConfig(
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

    try:
        print(f"   🤖 Intentando extracción principal con {MODEL_NAME}...")
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=config_principal,
        )
        
        # Si el modelo principal responde con éxito, devolvemos el JSON
        if response.text and len(response.text.strip()) > 10:
            return response.text
            
        print("   ⚠️ El modelo principal retornó una estructura vacía (None).")
        
    except Exception as e:
        print(f"   ⚠️ Error en el modelo principal: {e}")

    # -----------------------------------------------------------------
    # 🚀 CONFIGURACIÓN DE CONTINGENCIA (FALLBACK): Gemini 2.5 Flash
    # -----------------------------------------------------------------
    # Usamos un modelo de producción con alta estabilidad para estructuras masivas
    print("   ♻️ Activando Fallback: Procesando con gemini-2.5-flash...")
    config_fallback = types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        max_output_tokens=65535,
        response_mime_type="application/json",
        system_instruction=SYSTEM_INSTRUCTION,
        safety_settings=config_principal.safety_settings
        # Omitimos thinking_config para maximizar la velocidad y estabilidad del output
    )
    
    try:
        response_fallback = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=config_fallback,
        )
        print("   ✅ Extracción exitosa completada mediante el modelo de Fallback.")
        return response_fallback.text
        
    except Exception as e_fallback:
        print(f"   ❌ Error crítico en el modelo de contingencia: {e_fallback}")
        return None

# =====================================================================
# DISPARADOR HTTP PRINCIPAL
# =====================================================================
@functions_framework.http
def extract_fleet_20f_http(request):
    t_inicio_total = time.time()
    print(f"[START] Inicio de procesamiento AI para 20-F: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    
    archivos_procesados = []
    archivos_omitidos = []
    errores = []

    # Escaneo inicial del directorio 20f-flota-md/
    t_list = time.time()
    blobs = list(bucket.list_blobs(prefix=SOURCE_PREFIX))
    t_list = round(time.time() - t_list, 2)
    
    total_md = sum(1 for b in blobs if b.name.endswith(".md"))
    print(f"[LISTADO] {total_md} fragmentos de flota .md encontrados en {t_list}s")

    procesados_count = 0
    omitidos_count = 0
    errores_count = 0

    for idx, blob in enumerate(blobs, 1):
        if not blob.name.endswith(".md"):
            continue

        t_archivo = time.time()
        file_name_only = blob.name.split("/")[-1]
        
        # Mapeo de nombre: aeromex-2025-20f_Flota_filtrado.md -> aeromex-2025-20f_Flota_filtrado.json
        output_file_name = file_name_only.replace(".md", ".json")
        target_path = f"{TARGET_PREFIX}{output_file_name}"

        # --- Verificación de Idempotencia (Evitar reprocesar gastos en la API) ---
        t_check = time.time()
        ya_existe = bucket.blob(target_path).exists()
        t_check = round(time.time() - t_check, 3)
        
        if ya_existe:
            omitidos_count += 1
            archivos_omitidos.append(target_path)
            print(f"[{idx}] OMITIDO VIA CHECK ({t_check}s) -> {target_path}")
            continue

        print(f"[{idx}] ENVIANDO A AI -> {blob.name}")
        
        # --- Descarga del fragmento ---
        t_download = time.time()
        content = blob.download_as_text()
        t_download = round(time.time() - t_download, 2)
        print(f"      ↳ GCS Download: {t_download}s | Peso: {len(content):,} caracteres")

        try:
            # --- Inferencia Estricta con Gemini 3 Flash Preview ---
            t_gemini = time.time()
            json_output = process_with_gemini(content)
            
            # 🚨 AGREGA ESTA VALIDACIÓN AQUÍ:
            if not json_output:
                print(f"  ⚠️ Alerta: Gemini no pudo estructurar el JSON para {blob.name} (Respuesta vacía). Intentando fallback...")
                # Levantamos un error controlado para que lo capture el except de abajo sin tumbar el loop
                raise ValueError("La API de Gemini devolvió None. El documento puede ser demasiado complejo para el modo strict JSON.")

            t_gemini = round(time.time() - t_gemini, 2)
            print(f"      ↳ Gemini Analytics: {t_gemini}s | Output JSON: {len(json_output):,} caracteres")

            # --- Almacenamiento en Data Lake (Carpeta JSON) ---
            t_upload = time.time()
            output_blob = bucket.blob(target_path)
            # ... el resto del código de subida se queda exactamente igual ...
            
            # Sincronizamos metadatos para auditoría del JSON
            output_blob.upload_from_string(
                json_output,
                content_type='application/json'
            )
            t_upload = round(time.time() - t_upload, 2)
            
            archivos_procesados.append(target_path)
            procesados_count += 1
            t_total_archivo = round(time.time() - t_archivo, 2)
            
            print(f"      ↳ GCS Upload: {t_upload}s")
            print(f"      ✓ PROCESADO EXITOSAMENTE en {t_total_archivo}s -> {target_path}")

        except Exception as e:
            errores_count += 1
            t_error = round(time.time() - t_archivo, 2)
            error_msg = f"Error en procesamiento del documento {blob.name}: {str(e)}"
            print(f"      ✗ ERROR CRÍTICO en {t_error}s -> {error_msg}")
            errores.append(error_msg)

        # --- Métrica de Control de Progreso ---
        if procesados_count > 0 and procesados_count % 5 == 0:
            t_transcurrido = round(time.time() - t_inicio_total, 1)
            print(f"[CONTROL] {procesados_count} extraídos | {omitidos_count} en caché | {errores_count} fallidos | Tiempo transcurrido: {t_transcurrido}s")

    # --- Resumen de Ejecución de Pipeline ---
    t_total = round(time.time() - t_inicio_total, 2)
    print(f"[END] Pipeline finalizado en {t_total}s")
    print(f"      Resumen definitivo -> Procesados: {procesados_count} | Omitidos: {omitidos_count} | Errores: {errores_count}")

    return {
        "status": "completado",
        "pipeline_type": "20F_Fleet_Intelligence",
        "tiempo_total_segundos": t_total,
        "total_encontrados": total_md,
        "total_procesados": procesados_count,
        "total_omitidos": omitidos_count,
        "total_errores": errores_count,
        "archivos_creados": archivos_procesados,
        "archivos_omitidos": archivos_omitidos,
        "log_errores": errores
    }, 200