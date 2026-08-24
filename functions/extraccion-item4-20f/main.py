import functions_framework
from google.cloud import storage
import re

# =====================================================================
# CONFIGURACIÓN DE FILTRADO EXCLUSIVO (SOLO LAS SOLICITADAS)
# =====================================================================
CONFIG_AEROLINEAS = {
    'aeromex': {
        'start': r'\bITEM\s+4\b(?!\.[A-Za-z])',  # Permite "Item 4." pero bloquea "Item 4.A"
        'end': r'^(?:\||#|\*)*\s*ITEM\s+5\b', 
        'label': 'Item 4 Completo'
    },
    'ltm': {
        'start': r'\bITEM\s+4\b(?!\.[A-Za-z])', 
        'end': r'^(?:\||#|\*)*\s*ITEM\s+5\b', 
        'label': 'Item 4 Completo'
    },
    'vlrs': {
        'start': r'\bITEM\s+4\b(?!\.[A-Za-z])', 
        'end': r'^(?:\||#|\*)*\s*ITEM\s+5\b', 
        'label': 'Item 4 Completo'
    },
    'cpa': {
        'start': r'\bITEM\s+4\b(?!\.[A-Za-z])', 
        'end': r'^(?:\||#|\*)*\s*ITEM\s+5\b', 
        'label': 'Item 4 Completo'
    },
    'azul': {
        'start': r'\bITEM\s+4\b(?!\.[A-Za-z])', 
        'end': r'^(?:\||#|\*)*\s*ITEM\s+5\b', 
        'label': 'Item 4 Completo'
    }
}

# =====================================================================
# NÚCLEO DE EXTRACCIÓN DINÁMICA
# =====================================================================
# Terminos que debe contener una seccion de flota real. Sirven para distinguir
# la seccion de verdad del indice, que menciona "ITEM 4" pero no habla de aviones.
SENALES_FLOTA = [
    "a320", "a321", "a319", "a350", "a330", "737", "787", "777", "767",
    "e190", "e195", "e175", "boeing", "airbus", "embraer", "atr",
    "average age", "operating lease", "finance lease", "owned",
]


def cuenta_senales_flota(texto):
    bajo = texto.lower()
    return sum(1 for s in SENALES_FLOTA if s in bajo)


def extraer_por_posicion(content, regex_start, regex_end_text):
    """Aisla la seccion buscando offsets en el texto crudo, sin depender de paginas.

    Se usa cuando la segmentacion por marcadores {N}------ no sirve, sea porque el
    markdown no los trae o porque inicio y fin caen dentro del indice. Se recorren
    las apariciones de ITEM 4 de la ultima a la primera -el indice siempre es la
    primera- y se conserva el primer bloque que hable de aeronaves.
    """
    pi = re.compile(regex_start, re.IGNORECASE)
    pf = re.compile(regex_end_text, re.IGNORECASE | re.MULTILINE)

    inicios = [m.start() for m in pi.finditer(content)]
    if not inicios:
        return None

    mejor = None
    for ini in reversed(inicios):
        fin_match = pf.search(content, ini + 1)
        fin = fin_match.start() if fin_match else len(content)
        bloque = content[ini:fin].strip()
        if len(bloque) < 2000:
            continue
        senales = cuenta_senales_flota(bloque)
        if senales >= 4:
            if mejor is None or senales > mejor[0]:
                mejor = (senales, bloque)

    return mejor[1] if mejor else None


def extraer_seccion_20f(content, regex_start, regex_end_text):
    """Segmenta por páginas y aísla la sección de flota según los límites provistos."""
    partes = re.split(r'(\{\d+\}\s*-+)', content)
    
    paginas = [partes[0]]
    for i in range(1, len(partes), 2):
        marcador = partes[i]
        contenido_pag = partes[i+1] if i+1 < len(partes) else ""
        paginas.append(marcador + contenido_pag)

    patron_inicio = re.compile(regex_start, re.IGNORECASE)
    
    paginas_con_inicio = []
    for i, pag in enumerate(paginas):
        if patron_inicio.search(pag):
            paginas_con_inicio.append(i)
            
    if not paginas_con_inicio:
        return None

    idx_inicio = paginas_con_inicio[0]
    
    # Manejo de Brechas (Gaps) para saltarse el Índice (Table of Contents)
    if len(paginas_con_inicio) > 1:
        max_gap = 0
        idx_after_gap = paginas_con_inicio[0]
        
        for j in range(1, len(paginas_con_inicio)):
            gap = paginas_con_inicio[j] - paginas_con_inicio[j-1]
            if gap > max_gap:
                max_gap = gap
                idx_after_gap = paginas_con_inicio[j]
                
        if max_gap > 3:
            idx_inicio = idx_after_gap

    patron_fin_estricto = re.compile(regex_end_text, re.IGNORECASE | re.MULTILINE)
    
    idx_fin = len(paginas) - 1
    for i in range(idx_inicio, len(paginas)):
        if patron_fin_estricto.search(paginas[i]):
            idx_fin = i
            break

    bloque_aislado = "".join(paginas[idx_inicio:idx_fin+1])
    
    match_fin = patron_fin_estricto.search(bloque_aislado)
    if match_fin:
        bloque_aislado = bloque_aislado[:match_fin.start()]

    texto_final = bloque_aislado.strip()

    # La segmentacion por paginas falla cuando el markdown no trae marcadores
    # {N}------: el documento entero cae en una o dos paginas y tanto ITEM 4 como
    # ITEM 5 casan dentro del indice, dejando la portada como "seccion de flota".
    # Le paso al 20-F de Volaris. En ese caso se busca por posicion de texto.
    if len(texto_final) < 50 or cuenta_senales_flota(texto_final) < 4:
        alternativa = extraer_por_posicion(content, regex_start, regex_end_text)
        if alternativa:
            print(f"    ↻ Seccion por paginas sin contenido de flota "
                  f"({len(texto_final)} chars); se usa la busqueda por posicion "
                  f"({len(alternativa)} chars)")
            return alternativa

        # Sin seccion respaldada por el documento no se emite nada: el modelo
        # rellenaria el vacio inventando la flota.
        print(f"    ⚠ Sin seccion de flota identificable ({len(texto_final)} chars, "
              f"{cuenta_senales_flota(texto_final)} señales); se omite")
        return None

    return texto_final

# =====================================================================
# ENTRYPOINT DE LA CLOUD FUNCTION
# =====================================================================
@functions_framework.http
def procesar_bucket_edgar_20f(request):
    storage_client = storage.Client()
    nombre_bucket = 'bucket-edgar'
    
    try:
        bucket = storage_client.bucket(nombre_bucket)
        blobs = bucket.list_blobs(prefix="20f-md/") # Apunta directo a la carpeta de origen 20-F
    except Exception as e:
        return f"Error al conectar con el bucket: {e}", 500
    
    archivos_procesados = 0
    archivos_omitidos = 0
    
    for blob in blobs:
        # Validar que sea un archivo markdown válido y no un filtrado previo
        if blob.name.endswith('.md') and not '_filtrado.md' in blob.name:
            
            # Extraer el nombre del archivo plano (ej: aeromex-2025-20f.md)
            nombre_archivo = blob.name.split('/')[-1]
            # Extraer el token del ticker (primer elemento antes del guion)
            ticker = nombre_archivo.split('-')[0].lower()
            
            # 🚨 FILTRO ESTRICTO: Si no está en las 5 aprobadas, se ignora por completo
            if ticker not in CONFIG_AEROLINEAS:
                archivos_omitidos += 1
                continue
                
            config = CONFIG_AEROLINEAS[ticker]
            print(f"📦 Procesando Flota de {ticker.upper()} usando límites para {config['label']}...")
            
            contenido = blob.download_as_text()
            seccion_extraida = extraer_seccion_20f(contenido, config['start'], config['end'])
            
            if seccion_extraida:
                # Modificar el nombre para guardarlo en la carpeta destino dedicada
                nombre_base = nombre_archivo.replace('.md', '_Flota_filtrado.md')
                nuevo_nombre = f"20f-flota-md/{nombre_base}"

                nuevo_blob = bucket.blob(nuevo_nombre)
                
                # Sostener la metadata del target original para auditorías rápidas
                nuevo_blob.metadata = {
                    "extracted_section": config['label'],
                    "ticker": ticker.upper()
                }
                
                nuevo_blob.upload_from_string(seccion_extraida, content_type='text/markdown')
                archivos_procesados += 1
                print(f"✔ Guardado exitosamente: gs://{nombre_bucket}/{nuevo_nombre}")
            else:
                print(f"⚠ Límites encontrados pero texto insuficiente en {nombre_archivo}")
                archivos_omitidos += 1
                
    mensaje_final = f"¡Proceso 20-F finalizado! Archivos de Flota extraídos: {archivos_procesados}. Omitidos o no autorizados: {archivos_omitidos}."
    print(mensaje_final)
    
    return mensaje_final, 200