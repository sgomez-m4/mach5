import functions_framework

from google.cloud import storage

import re



def extraer_item2_por_paginas(content):
    partes = re.split(r'(\{\d+\}\s*-+)', content)
    
    paginas = [partes[0]]
    for i in range(1, len(partes), 2):
        marcador = partes[i]
        contenido_pag = partes[i+1] if i+1 < len(partes) else ""
        paginas.append(marcador + contenido_pag)

    patron_item2 = re.compile(r'\bITEM\s+2\b', re.IGNORECASE)
    
    paginas_con_item2 = []
    for i, pag in enumerate(paginas):
        if patron_item2.search(pag):
            paginas_con_item2.append(i)
            
    if not paginas_con_item2:
        return None

    idx_inicio = paginas_con_item2[0]
    
    if len(paginas_con_item2) > 1:
        max_gap = 0
        idx_after_gap = paginas_con_item2[0]
        
        for j in range(1, len(paginas_con_item2)):
            gap = paginas_con_item2[j] - paginas_con_item2[j-1]
            if gap > max_gap:
                max_gap = gap
                idx_after_gap = paginas_con_item2[j]
                
        if max_gap > 3:
            idx_inicio = idx_after_gap

    patron_item3_estricto = re.compile(r'^(?:\||#|\*)*\s*ITEM\s+3\b', re.IGNORECASE | re.MULTILINE)
    
    idx_fin = len(paginas) - 1
    for i in range(idx_inicio, len(paginas)):
        if patron_item3_estricto.search(paginas[i]):
            idx_fin = i
            break

    bloque_aislado = "".join(paginas[idx_inicio:idx_fin+1])
    
    match_fin = patron_item3_estricto.search(bloque_aislado)
    if match_fin:
        bloque_aislado = bloque_aislado[:match_fin.start()]

    texto_final = bloque_aislado.strip()

    if len(texto_final) < 50:
        return None

    return texto_final



@functions_framework.http

def procesar_bucket_edgar(request):
    storage_client = storage.Client()
    nombre_bucket = 'bucket-edgar'
    
    try:
        bucket = storage_client.bucket(nombre_bucket)
        blobs = bucket.list_blobs()
    except Exception as e:
        return f"Error al conectar con el bucket: {e}", 500
    
    archivos_procesados = 0
    archivos_omitidos = 0
    
    for blob in blobs:
        if blob.name.endswith('.md') and not blob.name.endswith('_filtrado.md'):
            
            contenido = blob.download_as_text()
            seccion_extraida = extraer_item2_por_paginas(contenido)
            
            if seccion_extraida:
                # ✅ CAMBIO: se extrae solo el nombre base del archivo y se guarda en la carpeta destino
                nombre_base = blob.name.split('/')[-1].replace('.md', '_Item2_filtrado.md')
                nuevo_nombre = f"10k-item2-md/{nombre_base}"

                nuevo_blob = bucket.blob(nuevo_nombre)
                nuevo_blob.upload_from_string(seccion_extraida, content_type='text/markdown')
                archivos_procesados += 1
            else:
                archivos_omitidos += 1
                
    mensaje_final = f"¡Proceso finalizado! Archivos extraídos: {archivos_procesados}. Omitidos: {archivos_omitidos}."
    print(mensaje_final)
    
    return mensaje_final, 200