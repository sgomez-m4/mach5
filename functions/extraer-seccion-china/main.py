import functions_framework
from google.cloud import storage
import re

# Configuración
SOURCE_PREFIX = "anual-china-md/"
TARGET_PREFIX = "flota-aerolineas-md/"

# Marcador de página insertado por pymupdf4llm
PATRON_PAGINA = re.compile(r'(<!-- Página \d+ -->)')

# ------------------------------------------------------------------------------
# PATRONES DE SECCIÓN
# ------------------------------------------------------------------------------

# Inicio de la sección de flota (MD&A / Business Review)
PATRONES_INICIO = [
    re.compile(r'第三节\s*[|\s:：]*\s*管理层讨论与分析', re.IGNORECASE),
    re.compile(r'第\s*3\s*节\s*[|\s:：]*\s*管理层讨论与分析', re.IGNORECASE),
    re.compile(r'管理層討論及分析', re.IGNORECASE),
    re.compile(r'業務回顧', re.IGNORECASE),
    re.compile(r'(?:Management\s+Discussion\s*(?:&|and)\s*Analysis|Business\s+Review)', re.IGNORECASE),
]

# Fin de la sección (inicio de la siguiente sección principal)
PATRONES_FIN = [
    re.compile(r'第四节\s*[|\s:：]*\s*公司治理', re.IGNORECASE),
    re.compile(r'第\s*4\s*节\s*[|\s:：]*\s*公司治理', re.IGNORECASE),
    re.compile(r'企業管治', re.IGNORECASE),
    re.compile(r'財務報表', re.IGNORECASE),
    re.compile(r'(?:Corporate\s+Governance|Financial\s+Statements)', re.IGNORECASE),
]


# ------------------------------------------------------------------------------
# LÓGICA DE EXTRACCIÓN POR PÁGINAS
# ------------------------------------------------------------------------------

def dividir_por_paginas(content: str) -> list[dict]:
    """
    Divide el contenido en páginas usando el marcador <!-- Página X -->.
    Retorna lista de dicts: {'marcador': str, 'numero': int, 'contenido': str}
    """
    partes = PATRON_PAGINA.split(content)
    
    paginas = []
    
    # partes[0] es contenido antes del primer marcador (generalmente vacío o mínimo)
    # Luego viene: [marcador, contenido, marcador, contenido, ...]
    
    for i in range(1, len(partes), 2):
        marcador = partes[i]  # "<!-- Página 5 -->"
        contenido = partes[i + 1] if i + 1 < len(partes) else ""
        
        # Extraer número de página del marcador
        match_num = re.search(r'\d+', marcador)
        num_pagina = int(match_num.group()) if match_num else (i // 2)
        
        paginas.append({
            'marcador': marcador,
            'numero': num_pagina,
            'contenido': contenido
        })
    
    return paginas


def encontrar_pagina_seccion(paginas: list[dict], patrones: list[re.Pattern], 
                              buscar_desde: int = 0) -> int | None:
    """
    Busca en qué página aparece cualquiera de los patrones.
    
    Args:
        paginas: Lista de páginas divididas
        patrones: Lista de regex a buscar
        buscar_desde: Índice de página desde donde empezar a buscar (para saltar TOC)
    
    Returns:
        Índice de la página donde se encontró (0-based), o None
    """
    for idx in range(buscar_desde, len(paginas)):
        texto_pagina = paginas[idx]['contenido']
        
        for patron in patrones:
            if patron.search(texto_pagina):
                return idx
    
    return None


def extraer_seccion_flota(content: str) -> str | None:
    """
    Extrae la sección de flota/MD&A usando los marcadores de página como delimitadores.
    
    Estrategia:
    1. Dividir el documento en páginas por <!-- Página X -->
    2. Saltar las primeras páginas (TOC/portada) → buscar desde página 3+
    3. Encontrar la página donde inicia "第三节 管理层讨论与分析"
    4. Encontrar la página donde inicia "第四节 公司治理"
    5. Extraer todas las páginas entre ambas
    """
    paginas = dividir_por_paginas(content)
    
    if len(paginas) < 5:
        # Documento demasiado corto, probablemente no es un reporte anual completo
        return None
    
    # --- PASO 1: Encontrar inicio de sección ---
    # Buscar desde la página índice 2 (página 3) para saltar portada y TOC
    idx_inicio = encontrar_pagina_seccion(paginas, PATRONES_INICIO, buscar_desde=2)
    
    if idx_inicio is None:
        # Intentar desde el inicio por si no tiene TOC separado
        idx_inicio = encontrar_pagina_seccion(paginas, PATRONES_INICIO, buscar_desde=0)
    
    if idx_inicio is None:
        return None
    
    # --- PASO 2: Encontrar fin de sección ---
    # Buscar la siguiente sección principal DESPUÉS del inicio
    idx_fin = encontrar_pagina_seccion(paginas, PATRONES_FIN, buscar_desde=idx_inicio + 1)
    
    if idx_fin is None:
        # Si no encontramos el fin, tomar hasta el 50% restante del documento
        idx_fin = min(idx_inicio + (len(paginas) - idx_inicio) // 2, len(paginas) - 1)
    
    # --- PASO 3: Unir las páginas de la sección ---
    paginas_seccion = paginas[idx_inicio:idx_fin]
    
    # Construir el texto final preservando los marcadores de página
    bloques = []
    for pag in paginas_seccion:
        bloques.append(pag['marcador'])
        bloques.append(pag['contenido'].strip())
    
    texto_seccion = "\n\n".join(bloques).strip()
    
    # Validación mínima (una sección MD&A real tiene al menos ~2000 caracteres)
    if len(texto_seccion) < 2000:
        return None
    
    # Agregar encabezado
    header = (
        f"# Sección: Flota y Operaciones (MD&A)\n\n"
        f"*Páginas {paginas_seccion[0]['numero']} a {paginas_seccion[-1]['numero']} "
        f"({len(paginas_seccion)} páginas extraídas)*\n\n"
        f"---\n\n"
    )
    
    return header + texto_seccion


# ------------------------------------------------------------------------------
# ENTRYPOINT HTTP
# ------------------------------------------------------------------------------

@functions_framework.http
def procesar_flota_aerolineas(request):
    """
    Cloud Function HTTP que recorre los reportes anuales en .md
    y extrae la sección de flota/MD&A de cada aerolínea.
    
    Query params opcionales:
      - code: Filtrar por código (ej: ?code=601111)
      - force: "true" para reprocesar archivos existentes
    """
    storage_client = storage.Client()
    nombre_bucket = 'bucket-edgar'
    
    request_args = request.args or {}
    filter_code = request_args.get("code")
    force = (request_args.get("force", "false").lower() == "true")
    
    try:
        bucket = storage_client.bucket(nombre_bucket)
        blobs = list(bucket.list_blobs(prefix=SOURCE_PREFIX))
    except Exception as e:
        return {"error": f"Error al conectar con el bucket: {e}"}, 500
    
    if not blobs:
        return {"error": f"No se encontraron archivos en {SOURCE_PREFIX}"}, 404
    
    archivos_procesados = 0
    archivos_omitidos = 0
    archivos_sin_flota = 0
    errores = []
    
    for blob in blobs:
        if not blob.name.endswith('.md'):
            continue
        
        if filter_code and filter_code not in blob.name:
            continue
        
        # Nombre de salida
        nombre_base = blob.name.split('/')[-1].replace('.md', '_Flota.md')
        nuevo_nombre = f"{TARGET_PREFIX}{nombre_base}"
        nuevo_blob = bucket.blob(nuevo_nombre)
        
        # Omitir si ya existe
        if not force and nuevo_blob.exists():
            archivos_omitidos += 1
            continue
        
        try:
            contenido = blob.download_as_text()
            seccion_flota = extraer_seccion_flota(contenido)
            
            if seccion_flota:
                nuevo_blob.upload_from_string(
                    seccion_flota,
                    content_type='text/markdown; charset=utf-8'
                )
                archivos_procesados += 1
                
                # Log con info de páginas extraídas
                num_paginas = seccion_flota.count('<!-- Página')
                print(f"✅ [{blob.name}] → {num_paginas} páginas extraídas ({len(seccion_flota)} chars)")
            else:
                archivos_sin_flota += 1
                print(f"⚠️  [{blob.name}] No se encontró sección de flota")
                
        except Exception as e:
            errores.append({"archivo": blob.name, "error": str(e)})
            print(f"❌ [{blob.name}] Error: {e}")
    
    resumen = (
        f"Procesados: {archivos_procesados} | "
        f"Omitidos: {archivos_omitidos} | "
        f"Sin flota: {archivos_sin_flota} | "
        f"Errores: {len(errores)}"
    )
    
    return {
        "status": "completed",
        "resumen": resumen,
        "procesados": archivos_procesados,
        "omitidos": archivos_omitidos,
        "sin_flota": archivos_sin_flota,
        "errores": errores
    }, 200