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
SOURCE_PREFIX = "flota-aerolineas-md/"
TARGET_PREFIX = "flota-aerolineas-json/"

# System Instruction adaptado para reportes anuales de aerolíneas chinas
SYSTEM_INSTRUCTION = """
Actúa como experto en ingeniería de datos aéreos y Business Intelligence, especializado en reportes anuales de aerolíneas chinas (A-Shares y H-Shares).

Tu tarea es extraer datos de tablas de flota de reportes anuales chinos y devolver estrictamente un JSON válido.

### CONTEXTO LINGÜÍSTICO
El texto de entrada puede estar en:
- Chino simplificado (A-Shares: Air China, China Eastern, China Southern, Hainan, Spring, Juneyao, China Express)
- Chino tradicional (H-Shares: Cathay Pacific)
- Inglés (reportes bilingües de Cathay Pacific)

### TRADUCCIONES CLAVE PARA MAPEO
| Término chino | Equivalente JSON |
|---|---|
| 自置 / 自有 / 自購 | "Owned" |
| 经营租赁 / 經營租賃 | "Operating Lease" |
| 融资租赁 / 融資租賃 | "Finance Lease" |
| 飞机 / 飛機 / Aircraft | aircraft_type (extraer el modelo) |
| 机龄 / 平均机龄 | average_age_years |
| 座位 / 座位数 / 载客量 | seating_capacity_avg |
| 引进 / 交付 / 接收 | delivery (pedidos futuros) |
| 订单 / 订购 / 待交付 | order_book |
| 架 / 架飞机 | quantity (contador de aeronaves) |
| 年 | year |
| 机型 / 型号 | aircraft_type |

### ESQUEMA 1: current_fleet_fact
Campos obligatorios:
- fleet_id: concatenación única (airline_year_model_ownership). Ejemplo: "air_china_2025_b737_800_owned"
- airline: nombre de la aerolínea en inglés (ej. "Air China", "China Eastern Airlines", "China Southern Airlines", "Cathay Pacific Airways", "Hainan Airlines", "Spring Airlines", "Juneyao Airlines", "China Express Airlines")
- report_year: año fiscal del reporte (entero, ej. 2025)
- aircraft_type: modelo exacto del avión normalizado (ej. "B737-800", "A320neo", "A350-900", "C919", "ARJ21"). Usa nomenclatura estándar de fabricante.
- ownership_type: "Owned", "Operating Lease", o "Finance Lease"
- quantity: cantidad de aeronaves (entero)
- seating_capacity_avg: capacidad promedio de asientos (entero o null si no se especifica)
- average_age_years: edad promedio de la flota en años (float o null)
- remaining_lease_years: años restantes del arrendamiento (float o null; usar null si es "Owned")

### ESQUEMA 2: order_book_fact
Campos obligatorios:
- order_id: concatenación única (airline_reportyear_model_deliveryyear). Ejemplo: "china_southern_2025_a320neo_2026"
- airline: nombre de la aerolínea en inglés
- report_year: año fiscal del reporte (entero)
- aircraft_type: modelo exacto del avión normalizado
- delivery_period: año o período de entrega esperado (string, ej. "2026", "2026-2028", "After 2028")
- quantity_committed: cantidad de aeronaves comprometidas/pedidas (entero)

### REGLAS CRÍTICAS
1. **Desnormalización**: Si una tabla tiene columnas por tipo de propiedad (自置, 经营租赁, 融资租赁), crea una fila separada por cada combinación modelo-propiedad.
2. **Normalización de modelos**: Convierte "波音737-800" → "B737-800", "空客A320neo" → "A320neo", "中国商飞C919" → "C919".
3. **Subtotales**: Si hay filas de "合计" (Total) o "小计" (Subtotal), NO las incluyas como filas individuales.
4. **Detección de aerolínea**: Identifica la aerolínea por el nombre en el texto (中国国际航空 → Air China, 中国东方航空 → China Eastern Airlines, 中国南方航空 → China Southern Airlines, 国泰航空 → Cathay Pacific Airways, 海南航空 → Hainan Airlines, 春秋航空 → Spring Airlines, 吉祥航空 → Juneyao Airlines, 华夏航空 → China Express Airlines).
5. **Año fiscal**: Extrae el año del título del reporte o del contexto (ej. "2025年年度报告" → 2025).
6. **Sin datos**: Si un campo no existe en el texto, usa null. No inventes datos.
7. **Formato de salida**: Devuelve ÚNICAMENTE un objeto JSON con dos claves raíz: "current_fleet" y "future_commitments". No uses bloques de código markdown. No agregues texto explicativo.
8. **Pedidos futuros**: Busca secciones sobre "飞机引进计划", "订单", "待交付", "未来交付", "资本性支出计划" o tablas con años futuros.
"""


def process_with_gemini(text_content: str, thinking_level: str = "LOW") -> str:
    """Envía el contenido markdown a Gemini para extracción estructurada."""
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION
    )

    contents = (
        "Procesa el siguiente extracto de reporte anual de una aerolínea china. "
        "Identifica la aerolínea, el año fiscal, y extrae todos los datos de flota "
        "(actual y pedidos futuros) al formato JSON requerido.\n\n"
        f"--- INICIO DEL EXTRACTO ---\n\n{text_content}\n\n--- FIN DEL EXTRACTO ---"
    )
    
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
            thinking_level=thinking_level
        )
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=generate_content_config,
    )

    # El presupuesto de max_output_tokens lo comparten el "thinking" y la salida.
    # Con thinking_level alto el modelo puede gastar casi todo pensando y truncar
    # el JSON a medias: China Eastern gastaba 62,911 tokens de razonamiento y solo
    # 2,610 de salida, cortando el JSON a medio string. Si eso pasa, se reintenta
    # con menos razonamiento, que para extraccion de tablas basta y sobra.
    candidato = response.candidates[0] if response.candidates else None
    finish = getattr(candidato, "finish_reason", None)
    if finish is not None and "MAX_TOKENS" in str(finish):
        uso = response.usage_metadata
        print(f"      ⚠️  Respuesta truncada (finish_reason=MAX_TOKENS): "
              f"thinking={getattr(uso, 'thoughts_token_count', '?')} tokens, "
              f"salida={getattr(uso, 'candidates_token_count', '?')} tokens")
        if thinking_level != "LOW":
            print("      ↻ Reintentando con thinking_level=LOW")
            return process_with_gemini(text_content, thinking_level="LOW")

    return response.text


@functions_framework.http
def extract_fleet_china_http(request):
    """
    Cloud Function HTTP que procesa los extractos de flota de aerolíneas chinas
    con Gemini y genera JSONs estructurados.
    
    Query params opcionales:
      - code: Filtrar por código de aerolínea (ej: ?code=601111)
      - force: "true" para reprocesar archivos existentes
    """
    t_inicio_total = time.time()
    print(f"[START] Inicio de ejecución: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Parámetros opcionales
    request_args = request.args or {}
    filter_code = request_args.get("code")
    force = (request_args.get("force", "false").lower() == "true")

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    
    archivos_procesados = []
    archivos_omitidos = []
    errores = []

    # Listar blobs
    t_list = time.time()
    blobs = list(bucket.list_blobs(prefix=SOURCE_PREFIX))
    t_list = round(time.time() - t_list, 2)
    
    # Filtrar solo .md
    blobs_md = [b for b in blobs if b.name.endswith(".md")]
    
    # Filtro opcional por código
    if filter_code:
        blobs_md = [b for b in blobs_md if filter_code in b.name]
    
    total_md = len(blobs_md)
    print(f"[LISTADO] {total_md} archivos .md encontrados en {t_list}s")

    if total_md == 0:
        return {"status": "sin_archivos", "mensaje": f"No se encontraron archivos en {SOURCE_PREFIX}"}, 404

    procesados_count = 0
    omitidos_count = 0
    errores_count = 0

    for idx, blob in enumerate(blobs_md, 1):
        t_archivo = time.time()
        file_name_only = blob.name.split("/")[-1]
        output_file_name = file_name_only.replace("_Flota.md", ".json").replace(".md", ".json")
        target_path = f"{TARGET_PREFIX}{output_file_name}"

        # --- Verificación de existencia ---
        if not force:
            t_check = time.time()
            ya_existe = bucket.blob(target_path).exists()
            t_check = round(time.time() - t_check, 3)
            
            if ya_existe:
                omitidos_count += 1
                archivos_omitidos.append(target_path)
                print(f"[{idx}/{total_md}] OMITIDO ({t_check}s) -> {target_path}")
                continue

        print(f"[{idx}/{total_md}] PROCESANDO -> {blob.name}")
        
        # --- Descarga ---
        t_download = time.time()
        try:
            content = blob.download_as_text()
        except Exception as e:
            errores_count += 1
            error_msg = f"Error descargando {blob.name}: {str(e)}"
            errores.append(error_msg)
            print(f"      ✗ ERROR descarga: {error_msg}")
            continue
            
        t_download = round(time.time() - t_download, 2)
        print(f"      ↳ Descarga: {t_download}s | Tamaño: {len(content):,} chars")

        # Validación de contenido mínimo
        if len(content) < 500:
            errores_count += 1
            error_msg = f"Contenido demasiado corto ({len(content)} chars) en {blob.name}"
            errores.append(error_msg)
            print(f"      ✗ {error_msg}")
            continue

        try:
            # --- Gemini ---
            t_gemini = time.time()
            json_output = process_with_gemini(content)
            t_gemini = round(time.time() - t_gemini, 2)
            print(f"      ↳ Gemini: {t_gemini}s | Respuesta: {len(json_output):,} chars")

            # Validar que la respuesta sea JSON válido
            import json
            try:
                json.loads(json_output)
            except json.JSONDecodeError as je:
                print(f"      ⚠️  Respuesta no es JSON válido, intentando limpiar...")
                # Intentar extraer JSON si viene con texto extra
                json_output = json_output.strip()
                if json_output.startswith("```"):
                    # Remover bloques de código markdown si los hay
                    lines = json_output.split('\n')
                    json_output = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
                try:
                    json.loads(json_output)
                except:
                    raise ValueError(f"Respuesta de Gemini no es JSON válido: {json_output[:200]}")

            # --- Subida ---
            t_upload = time.time()
            output_blob = bucket.blob(target_path)
            output_blob.upload_from_string(
                json_output,
                content_type='application/json; charset=utf-8'
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
            error_msg = f"Fallo en {blob.name}: {type(e).__name__}: {str(e)}"
            print(f"      ✗ ERROR en {t_error}s -> {error_msg}")
            errores.append(error_msg)

        # --- Progreso cada 3 archivos ---
        if procesados_count > 0 and procesados_count % 3 == 0:
            t_transcurrido = round(time.time() - t_inicio_total, 1)
            print(f"[PROGRESO] {procesados_count} procesados | {omitidos_count} omitidos | "
                  f"{errores_count} errores | Tiempo: {t_transcurrido}s")

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
        "archivos_procesados": archivos_procesados,
        "archivos_omitidos": archivos_omitidos,
        "errores": errores
    }, 200