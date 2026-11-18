"""Clasificacion y registro de eventos corporativos en
dataset_prospeccion.eventos_aerolinea.

Es la fuente unica del prompt y del esquema de clasificacion. La usan:
  - eventos/cargar_eventos.py (carga local, solo libreria estandar), y
  - los monitores rss-8k, rss-6k y revision-diaria-china, que la llevan copiada
    en su carpeta porque cada servicio se despliega desde la suya. Las copias
    deben ser identicas a este archivo: eventos/prueba_registro.py lo verifica.

Las dependencias de Google se importan dentro de las funciones que las usan,
para que el cargador local funcione sin ellas.
"""
import datetime
import json
import re

PROYECTO = "mach5-gemini-project"
DATASET = "dataset_prospeccion"
TABLA = "eventos_aerolinea"
BLOB_FUENTES = "config/eventos_fuentes.json"
MODELO = "gemini-2.5-flash"

CATEGORIAS = ["Flota y pedidos", "Leasing y financiamiento", "Mantenimiento y MRO",
              "Cambio de directivos", "Resultados financieros", "Reestructuracion o riesgo",
              "Gobierno corporativo", "Otro"]

# 8-K: el codigo de item ya dice mucho del evento
ITEMS_8K = {"1.01": "acuerdo material", "1.02": "fin de acuerdo material", "2.01": "compra o venta de activos",
            "2.02": "resultados", "2.03": "obligacion financiera", "2.05": "costos de salida",
            "2.06": "deterioro", "3.02": "venta de acciones", "5.02": "cambio de directivos",
            "5.03": "estatutos", "5.07": "votacion de accionistas", "7.01": "regulacion FD",
            "8.01": "otros eventos", "9.01": "anexos"}

PROMPT = """Eres analista comercial de mas4 Aviation, que vende a aerolineas: gestion de contratos de
leasing (entregas y devoluciones de aeronaves, registros tecnicos), servicios tecnicos de flota, y
personal tercerizado y backoffice de mantenimiento. Clasifica cada evento corporativo.

Para cada evento devuelve:
- id_evento: el mismo que recibes.
- categoria: una de {categorias}.
- relevancia: "Alta" si abre una conversacion comercial concreta para mas4 (pedidos, entregas o
  retiros de aeronaves, contratos o devoluciones de leasing, sale-leaseback, contratos de
  mantenimiento o MRO, aeronaves en tierra por motores, nuevo director de mantenimiento, de
  flota o tecnico); "Media" si cambia el contexto del cliente (cambios de alta direccion,
  financiamiento, reestructuracion, fusiones, resultados con cambios de flota); "Baja" si es
  rutina (fechas de resultados, dividendos, juntas, recompras, gobierno corporativo).
- resumen: una frase en espanol de maximo 160 caracteres con el hecho concreto (cifras si las hay).

Los titulos de Cninfo estan en chino: traducelos al resumir. Eventos:
{eventos}"""

ESQUEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "id_evento": {"type": "STRING"}, "categoria": {"type": "STRING", "enum": CATEGORIAS},
    "relevancia": {"type": "STRING", "enum": ["Alta", "Media", "Baja"]}, "resumen": {"type": "STRING"}},
    "required": ["id_evento", "categoria", "relevancia", "resumen"]}}

TEXTO_MAX = 3500


def id_sec(texto):
    """El id de un 8-K o 6-K es su numero de accession, venga del feed Atom
    ('...accession-number=0001193125-26-123456') o de la API de EDGAR."""
    m = re.search(r"(\d{10}-\d{2}-\d{6})", texto or "")
    return f"sec:{m.group(1)}" if m else None


def armar_prompt(eventos, textos):
    """eventos: dicts con id_evento, fecha, formulario, titulo, items; textos:
    id_evento -> texto del documento (opcional)."""
    detalle = []
    for e in eventos:
        d = {"id_evento": e["id_evento"], "fecha": e["fecha"], "formulario": e["formulario"],
             "titulo": e["titulo"]}
        if e.get("items"):
            d["items_8k"] = ", ".join(f"{c} ({ITEMS_8K.get(c, '?')})" for c in e["items"].split(","))
        if textos.get(e["id_evento"]):
            d["texto"] = textos[e["id_evento"]][:TEXTO_MAX]
        detalle.append(d)
    return PROMPT.format(categorias=", ".join(CATEGORIAS), eventos=json.dumps(detalle, ensure_ascii=False))


def leer_respuesta(texto):
    return {c["id_evento"]: c for c in json.loads(texto)}


def fila(evento, clasificacion):
    c = clasificacion or {}
    return {**{k: evento.get(k) for k in ("id_evento", "group_id", "fecha", "fuente", "formulario",
                                          "items", "titulo", "url")},
            "categoria": c.get("categoria"), "relevancia": c.get("relevancia"), "resumen": c.get("resumen"),
            "cargado_en": datetime.datetime.now(datetime.timezone.utc).isoformat()}


# ---------------------------------------------------------------- monitores
def cargar_fuentes(storage_client, bucket):
    """Mapa cik o codigo de Cninfo -> group_id, desde gs://<bucket>/config/."""
    blob = storage_client.bucket(bucket).blob(BLOB_FUENTES)
    if not blob.exists():
        return {}
    mapa = {}
    for f in json.loads(blob.download_as_text())["fuentes"]:
        mapa[f.get("cik") or f.get("codigo")] = f["group_id"]
    return mapa


def clasificar_con_sdk(ai_client, evento, texto):
    from google.genai import types
    resp = ai_client.models.generate_content(
        model=MODELO, contents=armar_prompt([evento], {evento["id_evento"]: texto}),
        config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json",
                                           response_schema=ESQUEMA))
    return leer_respuesta(resp.text).get(evento["id_evento"])


def registrar(evento, texto, ai_client, bq_client=None):
    """Clasifica el evento y lo agrega a la tabla, salvo que ya este. Nunca
    lanza excepcion: un fallo aqui no debe impedir el correo de alerta."""
    try:
        from google.cloud import bigquery
        bq_client = bq_client or bigquery.Client(project=PROYECTO)
        destino = f"{PROYECTO}.{DATASET}.{TABLA}"
        existe = list(bq_client.query(
            f"SELECT 1 FROM `{destino}` WHERE id_evento = @id LIMIT 1",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", evento["id_evento"])])).result())
        if existe:
            return False
        try:
            clas = clasificar_con_sdk(ai_client, evento, texto)
        except Exception as e:
            print(f"registro_eventos: sin clasificacion ({e})")
            clas = None
        errores = bq_client.insert_rows_json(destino, [fila(evento, clas)])
        if errores:
            print(f"registro_eventos: error al insertar {evento['id_evento']}: {errores}")
            return False
        return True
    except Exception as e:
        print(f"registro_eventos: no se registro {evento.get('id_evento')}: {e}")
        return False
