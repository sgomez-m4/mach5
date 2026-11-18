"""Carga a BigQuery los eventos corporativos de los ultimos 12 meses de cada
grupo del score v2: 8-K y 6-K de EDGAR y anuncios de Cninfo de las aerolineas
chinas. Cada evento se clasifica con Gemini (Vertex AI) por categoria y por
relevancia para la oferta de mas4 (gestion de leasing, servicios tecnicos,
personal y backoffice de mantenimiento).

Los monitores desplegados (rss-8k, rss-6k, revision-diaria-china) solo envian
correos; este script es la carga de la tabla mientras esos servicios no la
escriban. Usa solo la libreria estandar y la sesion de gcloud del equipo.

Uso:
  python -I cargar_eventos.py <carpeta de trabajo> [--desde AAAA-MM-DD] [--cargar]

La carpeta de trabajo guarda los textos y las clasificaciones ya hechas: volver
a correr el script no repite descargas ni llamadas a Gemini. Con --cargar
reemplaza la tabla dataset_prospeccion.eventos_aerolinea.
"""

import datetime
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

PROYECTO = "mach5-gemini-project"
DATASET = "dataset_prospeccion"
TABLA = "eventos_aerolinea"
UBICACION_IA = "us-central1"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(RAIZ, "config", "eventos_fuentes.json")

UA_SEC = {"User-Agent": "mas4-prospeccion research bot"}
UA_WEB = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
H_CNINFO = {**UA_WEB, "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest"}

LOTE = 8               # eventos por llamada a Gemini

# Prompt, esquema y formato de fila: fuente unica, compartida con los monitores
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registro_eventos as reg  # noqa: E402

TEXTO_MAX = reg.TEXTO_MAX


# ---------------------------------------------------------------- utilidades
def pedir(url, cabeceras, datos=None, intentos=3):
    cuerpo = urllib.parse.urlencode(datos).encode() if datos else None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, data=cuerpo, headers=cabeceras)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:
            if i == intentos - 1:
                print(f"  fallo {url[:90]}: {e}")
                return None
            time.sleep(2 * (i + 1))


class _Texto(HTMLParser):
    def __init__(self):
        super().__init__()
        self.partes, self._omitir = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._omitir += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._omitir:
            self._omitir -= 1

    def handle_data(self, d):
        if not self._omitir:
            self.partes.append(d)


def html_a_texto(contenido):
    p = _Texto()
    try:
        p.feed(contenido.decode("utf-8", "ignore"))
    except Exception:
        return ""
    return re.sub(r"\s+", " ", html.unescape(" ".join(p.partes))).strip()


def leer_json(ruta, defecto):
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    return defecto


def escribir_json(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- EDGAR
def eventos_sec(fuente, desde, cache):
    cik = fuente["cik"]
    crudo = pedir(f"https://data.sec.gov/submissions/CIK{cik}.json", UA_SEC)
    if not crudo:
        return []
    rec = json.loads(crudo)["filings"]["recent"]
    salida = []
    for i, forma in enumerate(rec["form"]):
        if forma != fuente["formulario"] or rec["filingDate"][i] < desde:
            continue
        acc = rec["accessionNumber"][i]
        clave = f"sec:{acc}"
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
        ev = {
            "id_evento": clave, "group_id": fuente["group_id"], "fecha": rec["filingDate"][i],
            "fuente": "EDGAR", "formulario": forma,
            "items": rec.get("items", [""] * len(rec["form"]))[i] or None,
            "titulo": rec.get("primaryDocDescription", [""] * len(rec["form"]))[i] or forma,
            "url": f"{base}/{rec['primaryDocument'][i]}",
        }
        if clave not in cache:
            texto = ""
            principal = pedir(ev["url"], UA_SEC)
            if principal:
                texto = html_a_texto(principal)
            # El comunicado de prensa (EX-99) suele tener el contenido real
            indice = pedir(f"{base}/index.json", UA_SEC)
            if indice:
                for item in json.loads(indice)["directory"]["item"]:
                    n = item["name"].lower()
                    if re.search(r"ex-?99|exhibit99|dex99", n) and n.endswith((".htm", ".html", ".txt")):
                        anexo = pedir(f"{base}/{item['name']}", UA_SEC)
                        if anexo:
                            texto += " || ANEXO: " + html_a_texto(anexo)
                        break
            cache[clave] = texto[:TEXTO_MAX]
            time.sleep(0.15)
        salida.append(ev)
    return salida


# ---------------------------------------------------------------- Cninfo
def eventos_cninfo(fuente, desde, hasta):
    crudo = pedir("https://www.cninfo.com.cn/new/information/topSearch/query", H_CNINFO,
                  {"keyWord": fuente["codigo"], "maxNum": 10})
    org = next((m.get("orgId") for m in json.loads(crudo or b"[]") if m.get("code") == fuente["codigo"]), None)
    if not org:
        print(f"  sin orgId para {fuente['codigo']}")
        return []
    salida, pagina = [], 1
    while True:
        crudo = pedir("https://www.cninfo.com.cn/new/hisAnnouncement/query", H_CNINFO, {
            "pageNum": pagina, "pageSize": 30, "column": fuente["column"], "tabName": "fulltext",
            "plate": "", "stock": f"{fuente['codigo']},{org}", "searchkey": "", "secid": "",
            "category": "", "trade": "", "seDate": f"{desde}~{hasta}", "sortName": "", "sortType": "",
            "isHLtitle": "true"})
        if not crudo:
            break
        d = json.loads(crudo)
        for a in d.get("announcements") or []:
            fecha = datetime.datetime.fromtimestamp(a["announcementTime"] / 1000, datetime.timezone.utc)
            fecha = (fecha + datetime.timedelta(hours=8)).date().isoformat()   # hora de Pekin
            titulo = re.sub(r"<[^>]+>", "", a.get("announcementTitle", "")).strip()
            salida.append({
                "id_evento": f"cninfo:{a['announcementId']}", "group_id": fuente["group_id"], "fecha": fecha,
                "fuente": "Cninfo", "formulario": "Anuncio", "items": None, "titulo": titulo,
                "url": "http://static.cninfo.com.cn/" + a.get("adjunctUrl", ""),
            })
        if not d.get("hasMore"):
            break
        pagina += 1
        time.sleep(0.5)
    return salida


# ---------------------------------------------------------------- Gemini
def token():
    r = subprocess.run(["cmd", "/c", "gcloud", "auth", "print-access-token"] if os.name == "nt"
                       else ["gcloud", "auth", "print-access-token"], capture_output=True, text=True)
    return r.stdout.strip()


def clasificar(lote, textos, tk):
    cuerpo = {"contents": [{"role": "user", "parts": [{"text": reg.armar_prompt(lote, textos)}]}],
              "generationConfig": {"responseMimeType": "application/json", "responseSchema": reg.ESQUEMA,
                                   "temperature": 0}}
    url = (f"https://{UBICACION_IA}-aiplatform.googleapis.com/v1/projects/{PROYECTO}/locations/"
           f"{UBICACION_IA}/publishers/google/models/{reg.MODELO}:generateContent")
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {tk}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.load(r)
    return reg.leer_respuesta(resp["candidates"][0]["content"]["parts"][0]["text"])


# ---------------------------------------------------------------- principal
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    trabajo = sys.argv[1]
    os.makedirs(trabajo, exist_ok=True)
    hoy = datetime.date.today()
    desde = sys.argv[sys.argv.index("--desde") + 1] if "--desde" in sys.argv else \
        (hoy - datetime.timedelta(days=365)).isoformat()

    with open(CONFIG, encoding="utf-8") as f:
        fuentes = json.load(f)["fuentes"]

    ruta_textos = os.path.join(trabajo, "textos.json")
    ruta_clas = os.path.join(trabajo, "clasificacion.json")
    textos, clasif = leer_json(ruta_textos, {}), leer_json(ruta_clas, {})

    eventos = []
    for fu in fuentes:
        if fu["tipo"] == "sec":
            ev = eventos_sec(fu, desde, textos)
        else:
            ev = eventos_cninfo(fu, desde, hoy.isoformat())
        print(f"{fu['group_id']:9} {fu['tipo']:6} {len(ev):4} eventos")
        eventos += ev
        escribir_json(ruta_textos, textos)

    pendientes = [e for e in eventos if e["id_evento"] not in clasif]
    print(f"{len(eventos)} eventos; {len(pendientes)} por clasificar")
    tk, t0 = token(), time.time()
    for i in range(0, len(pendientes), LOTE):
        if time.time() - t0 > 2400:          # el token dura una hora
            tk, t0 = token(), time.time()
        lote = pendientes[i:i + LOTE]
        try:
            clasif.update(clasificar(lote, textos, tk))
        except Exception as e:
            print(f"  lote {i // LOTE}: {e}")
            time.sleep(10)
            continue
        escribir_json(ruta_clas, clasif)
        if (i // LOTE) % 10 == 0:
            print(f"  clasificados {min(i + LOTE, len(pendientes))}/{len(pendientes)}")

    filas = [reg.fila(e, clasif.get(e["id_evento"])) for e in eventos]
    sin = sum(1 for f in filas if not f["relevancia"])
    print(f"sin clasificar: {sin}")
    resumen = {}
    for f in filas:
        resumen.setdefault(f["relevancia"], 0)
        resumen[f["relevancia"]] += 1
    print("por relevancia:", resumen)

    ndjson = os.path.join(trabajo, f"{TABLA}.ndjson")
    with open(ndjson, "w", encoding="utf-8") as f:
        for fila in filas:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")
    esquema = os.path.join(trabajo, f"{TABLA}.schema.json")
    escribir_json(esquema, [
        {"name": "id_evento", "type": "STRING", "mode": "REQUIRED"},
        {"name": "group_id", "type": "STRING"}, {"name": "fecha", "type": "DATE"},
        {"name": "fuente", "type": "STRING"}, {"name": "formulario", "type": "STRING"},
        {"name": "items", "type": "STRING"}, {"name": "titulo", "type": "STRING"},
        {"name": "url", "type": "STRING"}, {"name": "categoria", "type": "STRING"},
        {"name": "relevancia", "type": "STRING"}, {"name": "resumen", "type": "STRING"},
        {"name": "cargado_en", "type": "TIMESTAMP"}])

    if "--cargar" in sys.argv:
        bq = ["cmd", "/c", "bq"] if os.name == "nt" else ["bq"]
        r = subprocess.run(bq + ["load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON",
                                 f"{PROYECTO}:{DATASET}.{TABLA}", ndjson, esquema],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stdout + r.stderr)
        print(f"cargada {DATASET}.{TABLA}: {len(filas)} filas")


if __name__ == "__main__":
    main()
