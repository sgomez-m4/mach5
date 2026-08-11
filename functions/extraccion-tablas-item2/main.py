import os
import time
import logging
import pandas as pd
import functions_framework
from flask import jsonify
from edgar import Company, set_identity
from google import genai
from google.cloud import storage

# --- CONFIGURACIÓN DE LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# --- CONFIGURACIÓN MANUAL ---
LISTA_EMPRESAS = [
    {'ticker': 'AAL', 'cik': '0000006201'},
    {'ticker': 'DAL', 'cik': '0000027904'},
    {'ticker': 'ALK', 'cik': '0000766421'},
    {'ticker': 'FDX', 'cik': '0001048911'},
    {'ticker': 'JBLU', 'cik': '0001158463'},
    {'ticker': 'ULCC', 'cik': '0001670076'}
]

BUCKET_NAME = "bucket-edgar"

# --- CONFIGURACIÓN VERTEX AI ---
# Lee desde variables de entorno o usa valores por defecto
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")

# Modelos disponibles en Vertex AI
GEMINI_MODEL_PRIMARY = "gemini-3-flash-preview"
GEMINI_MODEL_FALLBACK = "gemini-2.0-flash"
MAX_CSV_CHARS = 600_000
# ----------------------------

CANDIDATOS_ITEM2 = [
    "2", "Item 2", "ITEM 2", "item 2",
    "Item\xa02", "ITEM\xa02",
    "Item2", "ITEM2",
    "2.", "Item 2.", "ITEM 2."
]


def get_item2(doc):
    for clave in CANDIDATOS_ITEM2:
        try:
            section = doc.get_section(clave)
            if section:
                return section
        except Exception:
            continue
    return None


def preprocesar_tabla_para_gemini(table_node, tablas_vistas):
    df = table_node.to_dataframe()
    if df is None or df.empty:
        return None
    df = df.reset_index()
    df = df.astype(object)
    df.replace(["None", "NaN", "nan", "", None], pd.NA, inplace=True)
    df.dropna(how="all", axis=1, inplace=True)
    df.dropna(how="all", axis=0, inplace=True)
    if df.empty:
        return None
    df.columns = [str(col).replace('\n', ' ').strip() for col in df.columns]
    df.fillna("", inplace=True)
    csv_str = df.to_csv(index=False)

    if csv_str in tablas_vistas:
        return None
    tablas_vistas.add(csv_str)
    return csv_str


def limpiar_respuesta_csv(texto):
    texto = texto.strip()
    if texto.startswith("```"):
        lineas = texto.split("\n")
        if len(lineas) > 1:
            texto = "\n".join(lineas[1:])
        if texto.endswith("```"):
            texto = texto[:-3].strip()
    return texto


def llamar_gemini_con_fallback(client_gemini, prompt, ticker_fmt, tabla_num):
    modelos_a_probar = [
        (GEMINI_MODEL_PRIMARY, "primario"),
        (GEMINI_MODEL_FALLBACK, "fallback")
    ]

    for modelo, tipo in modelos_a_probar:
        for intento in range(3):
            try:
                log.info(
                    f"  [{ticker_fmt}] Gemini tabla {tabla_num} "
                    f"modelo '{modelo}' ({tipo}) — intento {intento + 1}/3"
                )

                response = client_gemini.models.generate_content(
                    model=modelo,
                    contents=prompt
                )

                if not response or not response.text:
                    log.warning(
                        f"  [{ticker_fmt}] Respuesta vacía de Gemini "
                        f"para tabla {tabla_num}"
                    )
                    continue

                csv_final = limpiar_respuesta_csv(response.text)

                if not csv_final or len(csv_final) < 10:
                    log.warning(
                        f"  [{ticker_fmt}] Respuesta Gemini demasiado corta "
                        f"para tabla {tabla_num}"
                    )
                    continue

                return csv_final, modelo

            except Exception as e:
                error_tipo = type(e).__name__
                error_str = str(e)
                error_completo = f"{error_tipo}: {error_str}"

                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    log.warning(
                        f"  [{ticker_fmt}] Rate limit Gemini tabla {tabla_num} "
                        f"(modelo {modelo}), intento {intento + 1}. "
                        f"Esperando 25s... [{error_completo}]"
                    )
                    time.sleep(25)
                    continue

                if any(x in error_str for x in [
                    "404", "not found", "NOT_FOUND", "model", "unsupported"
                ]):
                    log.warning(
                        f"  [{ticker_fmt}] Modelo '{modelo}' no disponible. "
                        f"Error: {error_completo}"
                    )
                    break

                if any(x in error_str for x in [
                    "403", "PERMISSION_DENIED", "disabled"
                ]):
                    log.error(
                        f"  [{ticker_fmt}] Error de permisos/cuota con "
                        f"modelo '{modelo}': {error_completo}"
                    )
                    break

                log.error(
                    f"  [{ticker_fmt}] Error Gemini tabla {tabla_num} "
                    f"(modelo {modelo}), intento {intento + 1}: {error_completo}"
                )
                break

    return None, None


@functions_framework.http
def main(request):
    tiempo_inicio_total = time.time()
    log.info("=" * 60)
    log.info(f"INICIO DE EJECUCIÓN — {len(LISTA_EMPRESAS)} empresas a procesar")
    log.info("=" * 60)

    sec_identity = os.getenv("SEC_API_IDENTITY")

    if not sec_identity:
        log.error("Falta variable de entorno: SEC_API_IDENTITY")
        return jsonify({"error": "Falta variable de entorno SEC_API_IDENTITY"}), 500

    if not PROJECT_ID:
        log.error("Falta configuración: GCP_PROJECT_ID no está definido")
        return jsonify({"error": "Falta GCP_PROJECT_ID en variables de entorno"}), 500

    set_identity(sec_identity)

    try:
        # --- VERTEX AI: autenticación automática via cuenta de servicio ---
        client_gemini = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION
        )
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        log.info(
            f"Conexión exitosa a Vertex AI (proyecto: {PROJECT_ID}, "
            f"región: {LOCATION}) y GCS. Bucket: {BUCKET_NAME}"
        )
    except Exception as e:
        log.error(f"Error inicializando Vertex AI/GCS: {type(e).__name__}: {e}")
        return jsonify(
            {"error": f"Error inicializando Vertex AI/GCS: {str(e)}"}
        ), 500

    resultados = []
    errores_detectados = []

    for idx_empresa, empresa in enumerate(LISTA_EMPRESAS, 1):
        ticker_raw = empresa["ticker"].strip()
        cik_raw = empresa["cik"].strip()
        ticker_fmt = ticker_raw.capitalize()
        cik_fmt = cik_raw.upper()

        tiempo_inicio_empresa = time.time()
        log.info("-" * 60)
        log.info(
            f"[{idx_empresa}/{len(LISTA_EMPRESAS)}] "
            f"Procesando: {ticker_fmt} (CIK: {cik_fmt})"
        )

        conteo_tablas_nuevas = 0
        conteo_ya_existian = 0
        conteo_vacias = 0
        conteo_errores_gemini = 0
        conteo_truncadas = 0
        total_tablas_encontradas = 0

        try:
            log.info(f"  [{ticker_fmt}] Descargando filings 10-K desde EDGAR...")
            company = Company(cik_raw)
            filings = company.get_filings(form="10-K")

            if not filings:
                log.warning(f"  [{ticker_fmt}] No se encontraron filings 10-K")
                errores_detectados.append(f"No hay 10-K para {ticker_fmt}")
                continue

            log.info(
                f"  [{ticker_fmt}] Filing 10-K encontrado. "
                f"Extrayendo documento..."
            )
            tenk = filings[0]
            doc = tenk.obj().document
            item2 = get_item2(doc)

            if not item2:
                try:
                    secciones_disponibles = str(
                        getattr(doc, 'sections', 'N/A')
                    )
                    msg = (
                        f"No se encontró Item 2 para {ticker_fmt}. "
                        f"Secciones disponibles: {secciones_disponibles[:300]}"
                    )
                except Exception:
                    msg = f"No se encontró Item 2 para {ticker_fmt}"
                log.warning(f"  [{ticker_fmt}] {msg}")
                errores_detectados.append(msg)
                continue

            tables = item2.tables()
            total_tablas_encontradas = len(list(tables))
            tables = item2.tables()

            log.info(
                f"  [{ticker_fmt}] Item 2 encontrado — "
                f"{total_tablas_encontradas} tablas detectadas"
            )

            tablas_vistas = set()
            numero_tabla_real = 1

            for i, table in enumerate(tables, 1):
                folder_name = f"{ticker_fmt}_{cik_fmt}"
                file_name = (
                    f"{ticker_fmt}_10K_Item2_Table_{numero_tabla_real}.csv"
                )
                destination_blob_name = (
                    f"Edgar/Tablas_Item2_10k/{folder_name}/{file_name}"
                )
                blob = bucket.blob(destination_blob_name)

                if i % 5 == 0 or i == total_tablas_encontradas:
                    pct = round((i / total_tablas_encontradas) * 100) \
                        if total_tablas_encontradas > 0 else 0
                    elapsed = round(time.time() - tiempo_inicio_empresa, 1)
                    log.info(
                        f"  [{ticker_fmt}] Progreso: {i}/{total_tablas_encontradas} "
                        f"tablas ({pct}%) — {elapsed}s "
                        f"| Subidas: {conteo_tablas_nuevas} "
                        f"| Existían: {conteo_ya_existian} "
                        f"| Vacías: {conteo_vacias} "
                        f"| Truncadas: {conteo_truncadas} "
                        f"| Errores: {conteo_errores_gemini}"
                    )

                if blob.exists():
                    log.debug(
                        f"  [{ticker_fmt}] Tabla {numero_tabla_real} ya existe "
                        f"en bucket, se omite"
                    )
                    conteo_ya_existian += 1
                    numero_tabla_real += 1
                    continue

                datos_crudos_csv = preprocesar_tabla_para_gemini(
                    table, tablas_vistas
                )
                if not datos_crudos_csv:
                    log.debug(
                        f"  [{ticker_fmt}] Tabla {i} vacía o duplicada, "
                        f"se descarta"
                    )
                    conteo_vacias += 1
                    continue

                fue_truncada = False
                csv_len_original = len(datos_crudos_csv)
                if len(datos_crudos_csv) > MAX_CSV_CHARS:
                    log.warning(
                        f"  [{ticker_fmt}] Tabla {i} muy grande "
                        f"({csv_len_original:,} chars). Truncando a "
                        f"{MAX_CSV_CHARS:,} chars."
                    )
                    datos_crudos_csv = (
                        datos_crudos_csv[:MAX_CSV_CHARS] + "\n...[TRUNCADO]"
                    )
                    conteo_truncadas += 1
                    fue_truncada = True

                prompt = f"""You are an expert financial data analyst.
Clean this raw CSV from an SEC 10-K filing.
- Ensure headers are correct.
- Keep the first column (labels).
- RETURN ONLY THE RAW CSV TEXT.
- Do not add explanations, markdown formatting, or code blocks.

RAW DATA:
{datos_crudos_csv}
"""

                csv_final, modelo_usado = llamar_gemini_con_fallback(
                    client_gemini, prompt, ticker_fmt, numero_tabla_real
                )

                if csv_final:
                    try:
                        blob.upload_from_string(
                            csv_final, content_type="text/csv"
                        )
                        log.info(
                            f"  [{ticker_fmt}] Tabla {numero_tabla_real} "
                            f"subida a GCS (modelo: {modelo_usado}"
                            f"{', truncada' if fue_truncada else ''})"
                        )
                        numero_tabla_real += 1
                        conteo_tablas_nuevas += 1
                    except Exception as e:
                        log.error(
                            f"  [{ticker_fmt}] Error subiendo tabla "
                            f"{numero_tabla_real} a GCS: "
                            f"{type(e).__name__}: {e}"
                        )
                        errores_detectados.append(
                            f"Error GCS en {ticker_fmt} T{i}: {str(e)}"
                        )
                        conteo_errores_gemini += 1
                else:
                    log.error(
                        f"  [{ticker_fmt}] Falló Gemini para tabla "
                        f"{numero_tabla_real} (todos los modelos agotados)"
                    )
                    errores_detectados.append(
                        f"Fallo total Gemini en {ticker_fmt} T{i}"
                    )
                    conteo_errores_gemini += 1

            tiempo_empresa = round(time.time() - tiempo_inicio_empresa, 1)
            log.info(
                f"  [{ticker_fmt}] ✓ COMPLETADO en {tiempo_empresa}s — "
                f"Encontradas: {total_tablas_encontradas} | "
                f"Subidas: {conteo_tablas_nuevas} | "
                f"Ya existían: {conteo_ya_existian} | "
                f"Vacías: {conteo_vacias} | "
                f"Truncadas: {conteo_truncadas} | "
                f"Fallidas: {conteo_errores_gemini}"
            )

            resultados.append({
                "empresa": f"{ticker_fmt}_{cik_fmt}",
                "tablas_encontradas_en_item2": total_tablas_encontradas,
                "tablas_ya_existian_en_bucket": conteo_ya_existian,
                "tablas_vacias_o_duplicadas": conteo_vacias,
                "tablas_truncadas": conteo_truncadas,
                "tablas_procesadas_por_gemini": conteo_tablas_nuevas,
                "tablas_fallidas_en_gemini": conteo_errores_gemini,
                "tiempo_segundos": tiempo_empresa
            })

        except Exception as e:
            log.error(
                f"  [{ticker_fmt}] Error general inesperado: "
                f"{type(e).__name__}: {e}"
            )
            errores_detectados.append(
                f"Error procesando {ticker_fmt}: {str(e)}"
            )

    tiempo_total = round(time.time() - tiempo_inicio_total, 1)
    log.info("=" * 60)
    log.info(f"FIN DE EJECUCIÓN — Tiempo total: {tiempo_total}s")
    log.info(
        f"Empresas procesadas: {len(resultados)} | "
        f"Errores: {len(errores_detectados)}"
    )
    log.info("=" * 60)

    return jsonify({
        "status": "completado",
        "tiempo_total_segundos": tiempo_total,
        "resumen": resultados,
        "errores": errores_detectados
    }), 200