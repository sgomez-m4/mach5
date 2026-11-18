"""Pruebas sin red de registro_eventos y de sus copias en los monitores.

  python -I eventos/prueba_registro.py
"""
import filecmp
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
import registro_eventos as reg  # noqa: E402

MONITORES = ["rss-8k", "rss-6k", "revision-diaria-china"]
fallos = 0


def comprobar(cond, texto):
    global fallos
    print(("ok    " if cond else "FALLA ") + texto)
    fallos += not cond


# 1. Las copias de los monitores son identicas a la fuente
for m in MONITORES:
    copia = os.path.join(RAIZ, "functions", m, "registro_eventos.py")
    comprobar(os.path.exists(copia) and filecmp.cmp(copia, os.path.join(AQUI, "registro_eventos.py"), shallow=False),
              f"copia identica en functions/{m}")
    with open(os.path.join(RAIZ, "functions", m, "requirements.txt"), encoding="utf-8") as f:
        comprobar("google-cloud-bigquery" in f.read(), f"google-cloud-bigquery en requirements de {m}")

# 2. El id de un filing sale igual del feed Atom, del enlace y de la API
comprobar(reg.id_sec("urn:tag:sec.gov,2008:accession-number=0001193125-26-123456") == "sec:0001193125-26-123456",
          "id desde el feed Atom")
comprobar(reg.id_sec("https://www.sec.gov/Archives/edgar/data/6201/000119312526123456/0001193125-26-123456-index.htm")
          == "sec:0001193125-26-123456", "id desde el enlace del indice")
comprobar(reg.id_sec("sin numero") is None, "sin accession no hay id")

# 3. El prompt lleva los items del 8-K traducidos y recorta el texto
ev = {"id_evento": "sec:1", "fecha": "2026-11-01", "formulario": "8-K", "titulo": "t", "items": "1.01,5.02"}
p = reg.armar_prompt([ev], {"sec:1": "x" * 10000})
comprobar("1.01 (acuerdo material), 5.02 (cambio de directivos)" in p, "items del 8-K en el prompt")
comprobar("x" * reg.TEXTO_MAX in p and "x" * (reg.TEXTO_MAX + 1) not in p, "texto recortado a TEXTO_MAX")

# 4. Una fila sin clasificacion queda con relevancia vacia, no se cae
f = reg.fila({**ev, "group_id": "AAL", "fuente": "EDGAR", "url": "u"}, None)
comprobar(f["relevancia"] is None and f["group_id"] == "AAL" and f["cargado_en"], "fila sin clasificacion")

# 5. Cada CIK y codigo de la configuracion es unico y apunta a un solo grupo
with open(os.path.join(RAIZ, "config", "eventos_fuentes.json"), encoding="utf-8") as fh:
    fuentes = json.load(fh)["fuentes"]
claves = [x.get("cik") or x.get("codigo") for x in fuentes]
comprobar(len(claves) == len(set(claves)), "claves de fuentes sin repetir")

print("\n" + ("todo bien" if not fallos else f"{fallos} fallas"))
sys.exit(1 if fallos else 0)
