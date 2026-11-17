"""Carga a BigQuery los .zip de Form 41 bajados con descargar_form41.py.

Una tabla por schedule (form41_b43, form41_p52, form41_p10, form41_b1,
form41_p12) con todos los anios descargados. Cada carga reemplaza la tabla
entera: TranStats revisa trimestres ya publicados, asi que lo correcto es
volver a bajar los anios recientes y recargar, no acumular.

Tipos: las columnas cuyos valores son todos numericos se cargan como numero
(los montos de Form 41 vienen en miles de USD); las de identidad se fuerzan a
texto aunque parezcan numeros (un SERIAL_NUMBER "24339" no es una cantidad).
En B-43, AIRCRAFT_STATUS llega en mayusculas y minusculas ('b' y 'B'); se
normaliza a mayusculas: O propio, B leasing operativo, A leasing financiero.

Uso:
  python -I cargar_form41.py <carpeta_zips> <fecha_descarga AAAA-MM-DD> <carpeta_salida> [--cargar]
"""

import csv
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import date, datetime

PROYECTO = "mach5-gemini-project"
DATASET = "dataset_prospeccion"
BUCKET = "bucket-edgar"

TEXTO = {
    "CARRIER", "CARRIER_NAME", "UNIQUE_CARRIER", "UNIQUE_CARRIER_NAME",
    "UNIQUE_CARRIER_ENTITY", "ENTITY", "REGION", "SERIAL_NUMBER", "TAIL_NUMBER",
    "MODEL", "MANUFACTURER", "AIRCRAFT_STATUS", "OPERATING_STATUS",
}
FECHAS = {"ACQUISITION_DATE"}
NUMERO = re.compile(r"^-?\d+(\.\d+)?$")


def _fecha(v):
    v = v.strip()
    if not v:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def leer_zips(carpeta):
    """{schedule: (cabecera, filas)} juntando los anios de cada schedule."""
    tablas = {}
    for nombre in sorted(os.listdir(carpeta)):
        m = re.match(r"form41_([A-Z0-9]+)_(\d{4})\.zip$", nombre)
        if not m:
            continue
        sched = m.group(1).lower()
        z = zipfile.ZipFile(os.path.join(carpeta, nombre))
        csvs = [n for n in z.namelist() if n.startswith("T_") and n.endswith(".csv")]
        if len(csvs) != 1:
            raise RuntimeError(f"{nombre}: se esperaba un CSV de datos, hay {csvs}")
        filas = list(csv.reader(io.StringIO(z.read(csvs[0]).decode("utf-8-sig"))))
        cab, datos = filas[0], [f for f in filas[1:] if any(c.strip() for c in f)]
        if sched in tablas and tablas[sched][0] != cab:
            raise RuntimeError(f"{nombre}: columnas distintas a las de otros anios")
        tablas.setdefault(sched, (cab, []))[1].extend(datos)
    return tablas


def tipar(cab, filas):
    tipos = {}
    for i, c in enumerate(cab):
        if c in TEXTO:
            tipos[c] = "STRING"
        elif c in FECHAS:
            tipos[c] = "DATE"
        else:
            vals = [f[i].strip() for f in filas if i < len(f) and f[i].strip()]
            if vals and all(NUMERO.match(v) for v in vals):
                tipos[c] = "INT64" if all("." not in v for v in vals) else "FLOAT64"
            else:
                tipos[c] = "STRING"
    return tipos


def convertir(cab, fila, tipos, sched):
    out = {}
    for c, v in zip(cab, fila):
        v = v.strip()
        t = tipos[c]
        if v == "":
            out[c.lower()] = None
        elif t == "INT64":
            out[c.lower()] = int(v)
        elif t == "FLOAT64":
            out[c.lower()] = float(v)
        elif t == "DATE":
            out[c.lower()] = _fecha(v)
        else:
            out[c.lower()] = v
    if sched == "b43" and out.get("aircraft_status"):
        out["aircraft_status"] = out["aircraft_status"].upper()
    return out


def _cmd(prog, *args):
    cmd = (["cmd", "/c", prog] if os.name == "nt" else [prog]) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{prog} {' '.join(args[:2])} fallo:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    carpeta, fecha_descarga, salida = sys.argv[1], sys.argv[2], sys.argv[3]
    hacer_carga = "--cargar" in sys.argv[4:]
    date.fromisoformat(fecha_descarga)
    os.makedirs(salida, exist_ok=True)

    resumen = {}
    for sched, (cab, filas) in leer_zips(carpeta).items():
        tipos = tipar(cab, filas)
        esquema = [{"name": "fecha_descarga", "type": "DATE", "mode": "REQUIRED"}] + \
                  [{"name": c.lower(), "type": tipos[c], "mode": "NULLABLE"} for c in cab]
        ruta = os.path.join(salida, f"form41_{sched}.ndjson")
        with open(ruta, "w", encoding="utf-8") as f:
            for fila in filas:
                d = convertir(cab, fila, tipos, sched)
                d["fecha_descarga"] = fecha_descarga
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        with open(os.path.join(salida, f"form41_{sched}.schema.json"), "w", encoding="utf-8") as f:
            json.dump(esquema, f, indent=1)
        anios = sorted({fila[cab.index("YEAR")] for fila in filas})
        resumen[sched] = {"filas": len(filas), "anios": anios,
                          "columnas_texto_no_forzadas": [c for c in cab if tipos[c] == "STRING" and c not in TEXTO]}
    print(json.dumps(resumen, indent=1))

    if hacer_carga:
        for nombre in sorted(os.listdir(carpeta)):
            if nombre.endswith(".zip"):
                _cmd("gsutil", "cp", os.path.join(carpeta, nombre),
                     f"gs://{BUCKET}/form41/{fecha_descarga}/{nombre}")
        print(f"originales en gs://{BUCKET}/form41/{fecha_descarga}/")
        for sched in resumen:
            _cmd("bq", "load", "--source_format=NEWLINE_DELIMITED_JSON", "--replace",
                 f"{PROYECTO}:{DATASET}.form41_{sched}",
                 os.path.join(salida, f"form41_{sched}.ndjson"),
                 os.path.join(salida, f"form41_{sched}.schema.json"))
            print(f"cargada form41_{sched}")


if __name__ == "__main__":
    main()
