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
# Los patrones se agrupan por especificidad. Los del primer nivel llevan numero
# de seccion o estan en chino tradicional, y son inequivocos. Los del segundo son
# solo el titulo: alcanzan reportes que no numeran sus secciones -Air China no
# lleva numeracion alguna- pero tambien coinciden con el indice y con menciones
# sueltas, asi que solo se usan si el primer nivel no dio una seccion valida.

SEPARADOR_BLOQUES = "\n\n"

PATRONES_INICIO_ESPECIFICOS = [
    re.compile(r'第三节\s*[|\s:：]*\s*管理层讨论与分析', re.IGNORECASE),
    re.compile(r'第\s*3\s*节\s*[|\s:：]*\s*管理层讨论与分析', re.IGNORECASE),
    re.compile(r'管理層討論及分析', re.IGNORECASE),
    re.compile(r'業務回顧', re.IGNORECASE),
    re.compile(r'(?:Management\s+Discussion\s*(?:&|and)\s*Analysis|Business\s+Review)', re.IGNORECASE),
]

# Air China numera sus divisiones con 章 (capitulo) en vez de 节 (seccion)
PATRONES_INICIO_CAPITULO = [
    re.compile(r'\u7b2c\u4e09\u7ae0\s*[|\s:\uff1a]*\s*\u7ba1\u7406\u5c42\u8ba8\u8bba\u4e0e\u5206\u6790', re.IGNORECASE),
    re.compile(r'\u7b2c\s*3\s*\u7ae0\s*[|\s:\uff1a]*\s*\u7ba1\u7406\u5c42\u8ba8\u8bba\u4e0e\u5206\u6790', re.IGNORECASE),
]

PATRONES_FIN_CAPITULO = [
    re.compile(r'\u7b2c\u56db\u7ae0\s*[|\s:\uff1a]*\s*\u516c\u53f8\u6cbb\u7406', re.IGNORECASE),
    re.compile(r'\u7b2c\s*4\s*\u7ae0\s*[|\s:\uff1a]*\s*\u516c\u53f8\u6cbb\u7406', re.IGNORECASE),
]

PATRONES_INICIO_AMPLIOS = [
    re.compile(r'\u7ecf\u8425\u60c5\u51b5\u8ba8\u8bba\u4e0e\u5206\u6790', re.IGNORECASE),
    re.compile(r'\u7ba1\u7406\u5c42\u8ba8\u8bba\u4e0e\u5206\u6790', re.IGNORECASE),
    re.compile(r'\u4e1a\u52a1\u56de\u987e', re.IGNORECASE),
    re.compile(r'\u8463\u4e8b\u4f1a\u62a5\u544a', re.IGNORECASE),
]

PATRONES_FIN_ESPECIFICOS = [
    re.compile(r'第四节\s*[|\s:：]*\s*公司治理', re.IGNORECASE),
    re.compile(r'第\s*4\s*节\s*[|\s:：]*\s*公司治理', re.IGNORECASE),
    re.compile(r'企業管治', re.IGNORECASE),
    re.compile(r'財務報表', re.IGNORECASE),
    re.compile(r'(?:Corporate\s+Governance|Financial\s+Statements)', re.IGNORECASE),
]

PATRONES_FIN_AMPLIOS = [
    re.compile(r'\u516c\u53f8\u6cbb\u7406', re.IGNORECASE),
    re.compile(r'\u8d22\u52a1\u62a5\u8868', re.IGNORECASE),
    re.compile(r'\u8d22\u52a1\u62a5\u544a', re.IGNORECASE),
]

# Compatibilidad con cualquier consumidor externo de estas listas
PATRONES_INICIO = (PATRONES_INICIO_ESPECIFICOS + PATRONES_INICIO_CAPITULO
                   + PATRONES_INICIO_AMPLIOS)
PATRONES_FIN = (PATRONES_FIN_ESPECIFICOS + PATRONES_FIN_CAPITULO
                + PATRONES_FIN_AMPLIOS)


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
                              buscar_desde: int = 0,
                              patrones_excluir: list[re.Pattern] | None = None) -> int | None:
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

        # El indice lista todas las divisiones a la vez, asi que una pagina
        # que contiene el titulo de la seccion siguiente no es el inicio de
        # esta sino la tabla de contenidos.
        if patrones_excluir and any(p.search(texto_pagina) for p in patrones_excluir):
            continue
        
        for patron in patrones:
            if patron.search(texto_pagina):
                return idx
    
    return None


def _construir_seccion(paginas, idx_inicio, idx_fin):
    """Une las paginas del rango y devuelve el texto, o None si es demasiado corto."""
    paginas_seccion = paginas[idx_inicio:idx_fin]
    if not paginas_seccion:
        return None

    bloques = []
    for pag in paginas_seccion:
        bloques.append(pag['marcador'])
        bloques.append(pag['contenido'].strip())
    texto_seccion = SEPARADOR_BLOQUES.join(bloques).strip()

    # Una seccion MD&A real tiene al menos ~2000 caracteres
    if len(texto_seccion) < 2000:
        return None

    header = (
        "# Seccion: Flota y Operaciones (MD&A)" + SEPARADOR_BLOQUES
        + "*Paginas {} a {} ({} paginas extraidas)*".format(
            paginas_seccion[0]['numero'],
            paginas_seccion[-1]['numero'],
            len(paginas_seccion))
        + SEPARADOR_BLOQUES + "---" + SEPARADOR_BLOQUES
    )
    return header + texto_seccion


def extraer_seccion_flota(content: str) -> str | None:
    """
    Extrae la seccion de flota/MD&A usando los marcadores de pagina como delimitadores.

    Se prueban las combinaciones de patrones de mas especifica a mas amplia y se
    devuelve la primera que produce una seccion valida. Buscar con todos los
    patrones a la vez no funciona: los amplios coinciden antes -en el indice o en
    una mencion suelta- y recortan la seccion por debajo del minimo, que es justo
    lo que dejaria fuera a China Express y Juneyao.
    """
    paginas = dividir_por_paginas(content)

    if len(paginas) < 5:
        # Documento demasiado corto, probablemente no es un reporte anual completo
        return None

    # El segundo elemento indica si hay que descartar las paginas que tambien
    # contienen el titulo de la seccion siguiente, que es lo que caracteriza
    # al indice. Solo aplica a los niveles menos especificos: en el primero
    # el patron ya es inequivoco, y filtrar ahi recortaba secciones que se
    # venian extrayendo bien.
    niveles = (
        (PATRONES_INICIO_ESPECIFICOS, None),
        (PATRONES_INICIO_CAPITULO, PATRONES_FIN_CAPITULO),
        (PATRONES_INICIO_AMPLIOS, PATRONES_FIN_AMPLIOS),
    )
    for patrones_ini, patrones_par in niveles:
        # Desde la pagina 3 para saltar portada e indice; si no, desde el inicio
        idx_inicio = encontrar_pagina_seccion(paginas, patrones_ini, buscar_desde=2,
                                              patrones_excluir=patrones_par)
        if idx_inicio is None:
            idx_inicio = encontrar_pagina_seccion(paginas, patrones_ini, buscar_desde=0,
                                                  patrones_excluir=patrones_par)
        if idx_inicio is None:
            continue

        candidatos_fin = []
        for patrones_fin in (PATRONES_FIN_ESPECIFICOS, PATRONES_FIN_CAPITULO,
                             PATRONES_FIN_AMPLIOS):
            idx_fin = encontrar_pagina_seccion(paginas, patrones_fin,
                                               buscar_desde=idx_inicio + 1)
            if idx_fin is not None:
                candidatos_fin.append(idx_fin)
        # Ultimo recurso: la mitad de lo que queda del documento
        candidatos_fin.append(
            min(idx_inicio + (len(paginas) - idx_inicio) // 2, len(paginas) - 1)
        )

        for idx_fin in candidatos_fin:
            seccion = _construir_seccion(paginas, idx_inicio, idx_fin)
            if seccion is not None:
                return seccion

    return None


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