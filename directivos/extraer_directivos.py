"""Directivos de las aerolineas extranjeras del score v2, desde sus informes
anuales: el 20-F (Item 6) de las latinoamericanas y el informe anual de las
chinas. FAA no publica directivos de operadores de la parte 129, asi que esta es
la fuente para esas fichas.

Lee los markdown que el pipeline ya dejo en gs://bucket-edgar (20f-md/ y
anual-china-md/), toma la seccion de direccion y le pide a Gemini los cargos que
importan para vender: CEO, director tecnico o de mantenimiento, operaciones,
flota o compras, finanzas y presidente del consejo.

Uso:
  python -I -X utf8 extraer_directivos.py <carpeta de trabajo> [--cargar]
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eventos"))
import cargar_eventos as ce  # noqa: E402  (token)

PROYECTO = "mach5-gemini-project"
TABLA = "dataset_prospeccion.directivos_filings"
BUCKET = "gs://bucket-edgar"

# grupo -> (archivo en el bucket, documento)
DOCUMENTOS = {
    "AEROMEX": ("20f-md/aeromex-2026-20f.md", "20-F 2025"),
    "AZUL": ("20f-md/azul-2026-20f.md", "20-F 2025"),
    "CPA": ("20f-md/cpa-2026-20f.md", "20-F 2025"),
    "LTM": ("20f-md/ltm-2026-20f.md", "20-F 2025"),
    "VLRS": ("20f-md/vlrs-2026-20f.md", "20-F 2025"),
    "CHEXP": ("anual-china-md/002928_ChinaExpressAirlines_20260426_annual_report.md", "Informe anual 2025"),
    "CPACIFIC": ("anual-china-md/00293_HK_CathayPacificAirways_20260409_annual_report.md", "Informe anual 2025"),
    "CSN": ("anual-china-md/600029_ChinaSouthernAirlines_20260330_annual_report.md", "Informe anual 2025"),
    "CEA": ("anual-china-md/600115_ChinaEasternAirlines_20260330_annual_report.md", "Informe anual 2025"),
    "HNA": ("anual-china-md/600221_HainanAirlines_20260417_annual_report.md", "Informe anual 2025"),
    "SPRING": ("anual-china-md/601021_SpringAirlines_20260410_annual_report.md", "Informe anual 2025"),
    "CAO": ("anual-china-md/601111_AirChina_20260326_annual_report.md", "Informe anual 2025"),
    "JUNEYAO": ("anual-china-md/603885_JuneyaoAirlines_20260422_annual_report.md", "Informe anual 2025"),
}

# Marcas de la seccion de direccion: solo titulos de cargo. Palabras como
# "maintenance" o "fleet" llevaban la ventana a la descripcion del negocio.
# Se toma la ventana con mas marcas.
MARCAS = re.compile(
    r"chief executive|chief operat|chief financial|chief technical|chief commercial|vice[- ]president|"
    r"executive officer|senior management|总经理|总裁|副总|总工程师|首席|董事长|总会计师|高级管理人员", re.I)
VENTANA = 60000

CARGOS = ["CEO", "Director técnico o de mantenimiento", "Director de operaciones", "Director de flota o compras",
          "CFO", "Presidente del consejo", "Otro directivo"]

PROMPT = """Del siguiente extracto del {documento} de {aerolinea}, lista los directivos ACTUALES (al cierre del
informe o despues) con estos cargos, que son los que importan para vender servicios de leasing, tecnicos y de
mantenimiento a la aerolinea:
- CEO o presidente ejecutivo / director general (en chino: 总经理, 总裁).
- Director tecnico, de ingenieria o de mantenimiento (总工程师, 机务, 维修), incluido el vicepresidente a cargo.
- Director de operaciones (COO, 运行).
- Director de flota, de compras o de planeacion de flota.
- Director financiero (CFO, 总会计师, 财务总监).
- Presidente del consejo (董事长).
No incluyas consejeros independientes ni supervisores. Si alguien ya dejo el cargo, no lo incluyas.

Para cada uno:
- nombre: en caracteres latinos; si el texto esta en chino, en pinyin seguido del original entre parentesis.
- cargo: una de {cargos}.
- cargo_original: el titulo como aparece, traducido al espanol si esta en chino.
- cita: frase textual corta del extracto (maximo 160 caracteres) que lo respalda.

Extracto:
{texto}"""

ESQUEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "nombre": {"type": "STRING"}, "cargo": {"type": "STRING", "enum": CARGOS},
    "cargo_original": {"type": "STRING"}, "cita": {"type": "STRING"}},
    "required": ["nombre", "cargo", "cargo_original", "cita"]}}


def leer_gcs(ruta):
    r = subprocess.run(["cmd", "/c", "gsutil", "cat", f"{BUCKET}/{ruta}"] if os.name == "nt"
                       else ["gsutil", "cat", f"{BUCKET}/{ruta}"], capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore"))
    return r.stdout.decode("utf-8", "ignore")


def seccion(texto):
    """La ventana de VENTANA caracteres con mas marcas de direccion. En los
    20-F empieza en el Item 6 si se encuentra fuera del indice."""
    # Solo encabezados (al inicio de linea, con o sin '#'): el texto del 20-F
    # cita "Item 6. Directors..." en muchas partes, y esas referencias no son el
    # inicio de la seccion. Las filas del indice empiezan con '|' y no cuentan.
    m = [x.start() for x in re.finditer(r"^(?:#+\s*)?\**\s*ITEM\s*6\b", texto, re.I | re.M)]
    sig = [x.start() for x in re.finditer(r"^(?:#+\s*)?\**\s*ITEM\s*7\b", texto, re.I | re.M)]
    mejores = [(min([s for s in sig if s > a] or [len(texto)]) - a, a) for a in m]
    if mejores:
        largo, inicio = max(mejores)
        if largo > 5000:
            return texto[inicio:inicio + VENTANA]
    pos = [x.start() for x in MARCAS.finditer(texto)]
    if not pos:
        return texto[:VENTANA]
    j, mejor = 0, (0, 0)
    for i, p in enumerate(pos):
        while pos[j] < p - VENTANA:
            j += 1
        if i - j + 1 > mejor[0]:
            mejor = (i - j + 1, max(0, p - VENTANA))
    return texto[mejor[1]:mejor[1] + VENTANA]


def extraer(documento, aerolinea, texto, tk):
    cuerpo = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(
                  documento=documento, aerolinea=aerolinea, cargos=", ".join(CARGOS), texto=texto)}]}],
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
    ruta_cache = os.path.join(trabajo, "directivos.json")
    cache = json.load(open(ruta_cache, encoding="utf-8")) if os.path.exists(ruta_cache) else {}
    tk = ce.token()
    filas = []
    for g, (archivo, documento) in DOCUMENTOS.items():
        if g not in cache:
            try:
                texto = seccion(leer_gcs(archivo))
                cache[g] = {"documento": documento, "archivo": archivo,
                            "directivos": extraer(documento, g, texto, tk)}
            except Exception as e:
                print(f"{g}: fallo ({e})")
                continue
            with open(ruta_cache, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
        c = cache[g]
        print(f"{g:9} {c['documento']:20} {len(c['directivos'])} directivos")
        vistos = set()
        for d in c["directivos"]:
            clave = (d["nombre"].lower(), d["cargo"])
            if clave in vistos:
                continue
            vistos.add(clave)
            filas.append({"group_id": g, "documento": c["documento"], "archivo": c["archivo"], **d,
                          "cargado_en": datetime.datetime.now(datetime.timezone.utc).isoformat()})

    nd = os.path.join(trabajo, "directivos_filings.ndjson")
    with open(nd, "w", encoding="utf-8") as f:
        for x in filas:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    esquema = os.path.join(trabajo, "directivos_filings.schema.json")
    with open(esquema, "w", encoding="utf-8") as f:
        json.dump([{"name": n, "type": t} for n, t in [
            ("group_id", "STRING"), ("documento", "STRING"), ("archivo", "STRING"), ("nombre", "STRING"),
            ("cargo", "STRING"), ("cargo_original", "STRING"), ("cita", "STRING"), ("cargado_en", "TIMESTAMP")]], f)
    print(f"{len(filas)} directivos")
    if "--cargar" in sys.argv:
        bq = ["cmd", "/c", "bq"] if os.name == "nt" else ["bq"]
        r = subprocess.run(bq + ["load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON",
                                 f"{PROYECTO}:{TABLA}", nd, esquema], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stdout + r.stderr)
        print(f"cargada {TABLA}")


if __name__ == "__main__":
    main()
