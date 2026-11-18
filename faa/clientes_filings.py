"""Proveedores de mantenimiento que cada aerolinea nombra en su ultimo 10-K o
20-F, para saber a quien le trabaja cada taller de FAA.

Baja el informe anual de EDGAR, se queda con los parrafos que hablan de
mantenimiento (MRO, overhaul, chequeos pesados, motores, componentes) y le pide
a Gemini los proveedores nombrados, con el servicio y la cita textual. El cruce
con los talleres se hace en SQL (sql/v_talleres_clientes.sql).

Las aerolineas chinas no presentan en EDGAR y quedan fuera.

Uso:
  python -I clientes_filings.py <carpeta de trabajo> [--cargar]
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eventos"))
import cargar_eventos as ce  # noqa: E402  (pedir, html_a_texto, token, UA_SEC)

PROYECTO = "mach5-gemini-project"
TABLA = "dataset_prospeccion.talleres_clientes_filing"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLAVES = re.compile(
    r"maintenance,? repair|\bMRO\b|overhaul|heavy (?:maintenance|check|airframe)|engine (?:maintenance|services?|"
    r"shop|repair)|repair station|third[- ]party maintenance|maintenance (?:provider|agreement|contract|services?|"
    r"vendor|facilit)|power[- ]by[- ]the[- ]hour|flight hour agreement|component (?:support|services|repair)|"
    r"TechOps|Technik|mantenimiento", re.I)
VENTANA = 700
MAX_TEXTO = 30000

PROMPT = """Del siguiente extracto del informe anual ({forma}) de {aerolinea}, lista las empresas que el
texto nombra como proveedoras de mantenimiento, reparacion u overhaul (MRO) para la aerolinea: talleres,
MRO independientes, fabricantes de motores o componentes con contratos de servicio (por ejemplo
power-by-the-hour), y filiales tecnicas de otras aerolineas. No incluyas arrendadores, fabricantes que solo
venden aeronaves, aseguradoras ni la propia aerolinea o sus filiales.

Para cada proveedor:
- proveedor: nombre como aparece.
- nombre_corto: el nombre con que se le conoce, sin sufijos legales (por ejemplo "StandardAero", "AAR",
  "Lufthansa Technik", "GE Aerospace", "Pratt & Whitney").
- tipo: "Fabricante de motores o componentes", "MRO independiente", "Filial tecnica de aerolinea" u "Otro".
- servicio: "Motores", "Estructura y chequeos pesados", "Componentes", "Mantenimiento de linea" u "Otro".
- ubicacion: ciudad o pais si el texto la da; vacio si no.
- cita: frase textual del extracto (maximo 220 caracteres) que lo nombra.

Si el extracto no nombra ninguno, devuelve una lista vacia.

Extracto:
{texto}"""

ESQUEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "proveedor": {"type": "STRING"}, "nombre_corto": {"type": "STRING"},
    "tipo": {"type": "STRING", "enum": ["Fabricante de motores o componentes", "MRO independiente",
                                       "Filial tecnica de aerolinea", "Otro"]},
    "servicio": {"type": "STRING", "enum": ["Motores", "Estructura y chequeos pesados", "Componentes",
                                           "Mantenimiento de linea", "Otro"]},
    "ubicacion": {"type": "STRING"}, "cita": {"type": "STRING"}},
    "required": ["proveedor", "nombre_corto", "tipo", "servicio", "cita"]}}


def ultimo_anual(cik):
    d = json.loads(ce.pedir(f"https://data.sec.gov/submissions/CIK{cik}.json", ce.UA_SEC))
    rec = d["filings"]["recent"]
    for i, f in enumerate(rec["form"]):
        if f in ("10-K", "20-F"):
            acc = rec["accessionNumber"][i]
            url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
                   f"{rec['primaryDocument'][i]}")
            return f, rec["filingDate"][i], url
    return None, None, None


def extracto(texto):
    """Ventanas alrededor de cada mencion de mantenimiento, unidas si se solapan."""
    tramos = []
    for m in CLAVES.finditer(texto):
        a, b = max(0, m.start() - VENTANA), min(len(texto), m.end() + VENTANA)
        if tramos and a <= tramos[-1][1]:
            tramos[-1][1] = b
        else:
            tramos.append([a, b])
    partes, total = [], 0
    for a, b in tramos:
        partes.append(texto[a:b])
        total += b - a
        if total >= MAX_TEXTO:
            break
    return " [...] ".join(partes)[:MAX_TEXTO]


def extraer(forma, aerolinea, texto, tk):
    cuerpo = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(
                  forma=forma, aerolinea=aerolinea, texto=texto)}]}],
              "generationConfig": {"responseMimeType": "application/json", "responseSchema": ESQUEMA,
                                   "temperature": 0}}
    url = (f"https://us-central1-aiplatform.googleapis.com/v1/projects/{PROYECTO}/locations/us-central1/"
           f"publishers/google/models/gemini-2.5-flash:generateContent")
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {tk}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.load(r)
    return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])


def main():
    trabajo = sys.argv[1]
    os.makedirs(trabajo, exist_ok=True)
    with open(os.path.join(RAIZ, "config", "eventos_fuentes.json"), encoding="utf-8") as f:
        fuentes = [x for x in json.load(f)["fuentes"] if x["tipo"] == "sec"]
    ruta_cache = os.path.join(trabajo, "clientes.json")
    cache = json.load(open(ruta_cache, encoding="utf-8")) if os.path.exists(ruta_cache) else {}
    tk = ce.token()
    filas = []
    for fu in fuentes:
        g = fu["group_id"]
        if g not in cache:
            forma, fecha, url = ultimo_anual(fu["cik"])
            if not url:
                print(f"{g}: sin 10-K ni 20-F")
                continue
            crudo = ce.pedir(url, ce.UA_SEC)
            texto = ce.html_a_texto(crudo) if crudo else ""
            ext = extracto(texto)
            try:
                provs = extraer(forma, g, ext, tk) if ext else []
            except Exception as e:
                print(f"{g}: fallo Gemini ({e})")
                continue
            cache[g] = {"forma": forma, "fecha": fecha, "url": url, "caracteres": len(texto),
                        "extracto": len(ext), "proveedores": provs}
            with open(ruta_cache, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
            time.sleep(0.3)
        c = cache[g]
        print(f"{g:8} {c['forma']} {c['fecha']} texto {c['caracteres']:>8} extracto {c['extracto']:>6} "
              f"proveedores {len(c['proveedores'])}")
        for p in c["proveedores"]:
            filas.append({"group_id": g, "documento": c["forma"], "fecha_documento": c["fecha"], "url": c["url"],
                          "proveedor": p["proveedor"], "nombre_corto": p["nombre_corto"], "tipo": p["tipo"],
                          "servicio": p["servicio"], "ubicacion": p.get("ubicacion") or None, "cita": p["cita"],
                          "cargado_en": datetime.datetime.now(datetime.timezone.utc).isoformat()})

    nd = os.path.join(trabajo, "talleres_clientes_filing.ndjson")
    with open(nd, "w", encoding="utf-8") as f:
        for x in filas:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    esquema = os.path.join(trabajo, "talleres_clientes_filing.schema.json")
    with open(esquema, "w", encoding="utf-8") as f:
        json.dump([{"name": n, "type": t} for n, t in [
            ("group_id", "STRING"), ("documento", "STRING"), ("fecha_documento", "DATE"), ("url", "STRING"),
            ("proveedor", "STRING"), ("nombre_corto", "STRING"), ("tipo", "STRING"), ("servicio", "STRING"),
            ("ubicacion", "STRING"), ("cita", "STRING"), ("cargado_en", "TIMESTAMP")]], f)
    print(f"{len(filas)} menciones de proveedores")
    if "--cargar" in sys.argv:
        bq = ["cmd", "/c", "bq"] if os.name == "nt" else ["bq"]
        r = subprocess.run(bq + ["load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON",
                                 f"{PROYECTO}:{TABLA}", nd, esquema], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stdout + r.stderr)
        print(f"cargada {TABLA}")


if __name__ == "__main__":
    main()
