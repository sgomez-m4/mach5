# -*- coding: utf-8 -*-
"""Empaqueta el panel en un solo fichero de texto para subirlo a una IA.

Un .pbix no sirve para esto: es un ZIP con un modelo binario dentro y ninguna IA
saca nada de el. Desde que el informe se guarda como proyecto PBIP, en cambio,
todo lo que importa es texto: el modelo en TMDL, las vistas en SQL y la
definicion de cada visual en JSON.

Lo que se incluye y lo que no:

  - El modelo va literal. Son las 49 medidas con su DAX, las columnas, las
    relaciones y las consultas M: es lo que hay que leer para razonar sobre el
    panel.
  - Las vistas SQL van literales. Ahi vive la logica de negocio -el score, la
    normalizacion de modelos, la transicion- y sin ellas las medidas no se
    entienden.
  - El informe va como inventario, no como JSON crudo. Los visual.json son 432 KB
    de los que el 90% es andamiaje del formato; un listado de pagina, visual,
    posicion y campos dice lo mismo en una centesima parte y se lee.
  - El codigo de las Cloud Functions no va: es el pipeline que llena BigQuery,
    esta aguas arriba del panel y multiplicaria el tamano sin aportar al analisis
    del modelo.

    python scripts/empaquetar_para_ia.py
"""
import argparse
import io
import json
import os
import re
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# El proyecto vive dentro del repositorio desde que se versiona: Power BI
# reescribe la carpeta entera al cerrar, y sin git no hay forma de ver que
# se llevo por delante.
PBIP_POR_DEFECTO = os.path.join(RAIZ, "powerbi", "Prospeccion_Flota_PBIP")

TIPOS = {
    "card": "Tarjeta", "multiRowCard": "Tarjeta de varias filas",
    "tableEx": "Tabla", "pivotTable": "Matriz", "slicer": "Segmentación",
    "textbox": "Cuadro de texto", "barChart": "Barras",
    "clusteredBarChart": "Barras agrupadas",
    "clusteredColumnChart": "Columnas agrupadas",
    "hundredPercentStackedBarChart": "Barras 100%",
    "columnChart": "Columnas", "donutChart": "Anillo",
    "scatterChart": "Dispersión", "waterfallChart": "Cascada",
}

# Nada de esto deberia aparecer en un fichero que sale de la maquina. Se
# comprueba sobre el resultado final, no sobre las fuentes: es mas barato
# equivocarse aqui que despues.
SOSPECHOSO = re.compile(
    r"(api[_-]?key|apikey|secret|password|contrase|token\s*[:=]|AQ\.[A-Za-z0-9]{10,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY)", re.I)


def leer(ruta):
    return io.open(ruta, encoding="utf-8-sig").read()


def campos_de(visual):
    """Los campos por rol, en la forma corta 'tabla[campo]'."""
    salida = []
    estado = (visual.get("query") or {}).get("queryState") or {}
    for rol, cont in estado.items():
        nombres = []
        for p in cont.get("projections") or []:
            campo = p.get("field") or {}
            for clave in ("Column", "Measure", "Aggregation"):
                d = campo.get(clave)
                if not isinstance(d, dict):
                    continue
                if clave == "Aggregation":
                    d = (d.get("Expression") or {}).get("Column") or {}
                ent = ((d.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "?")
                nombres.append("{}[{}]".format(ent, d.get("Property", "?")))
                break
            else:
                nombres.append(p.get("nativeQueryRef") or "?")
        if nombres:
            salida.append("{}: {}".format(rol, ", ".join(nombres)))
    return " | ".join(salida)


def titulo_de(visual):
    for e in (visual.get("visualContainerObjects") or {}).get("title") or []:
        try:
            return e["properties"]["text"]["expr"]["Literal"]["Value"].strip("'")
        except Exception:
            pass
    return ""


def inventario_informe(pbip):
    carpeta = [d for d in os.listdir(pbip) if d.endswith(".Report")][0]
    base = os.path.join(pbip, carpeta, "definition", "pages")
    orden = json.loads(leer(os.path.join(base, "pages.json")))

    lineas = []
    for i, pid in enumerate(orden.get("pageOrder") or [], 1):
        pj = json.loads(leer(os.path.join(base, pid, "page.json")))
        lineas.append("\n### {}. {}  ({} × {})\n".format(
            i, pj.get("displayName"), pj.get("width"), pj.get("height")))
        vis_dir = os.path.join(base, pid, "visuals")
        filas = []
        for v in sorted(os.listdir(vis_dir)):
            f = os.path.join(vis_dir, v, "visual.json")
            if not os.path.exists(f):
                continue
            j = json.loads(leer(f))
            vis = j.get("visual") or {}
            pos = j.get("position") or {}
            filas.append((
                int(pos.get("y", 0)), int(pos.get("x", 0)),
                TIPOS.get(vis.get("visualType"), vis.get("visualType", "?")),
                titulo_de(vis),
                "{},{} {}×{}".format(int(pos.get("x", 0)), int(pos.get("y", 0)),
                                     int(pos.get("width", 0)), int(pos.get("height", 0))),
                campos_de(vis),
                bool((vis.get("objects") or {}).get("dataPoint")),
                # Power BI escribe una entrada por cada campo del visual para
                # poder listarlo en el panel de filtros. Solo cuenta como filtro
                # la que trae condicion: las demas son marcadores inertes.
                any((x.get("filter") or {}).get("Where")
                    for x in (j.get("filterConfig") or {}).get("filters") or []),
            ))
        for _, _, tipo, tit, geo, campos, color, filtro in sorted(filas):
            marcas = []
            if color:
                marcas.append("colores fijados")
            if filtro:
                marcas.append("con filtro")
            lineas.append("- **{}**{} — `{}`{}".format(
                tipo,
                " · " + tit if tit else "",
                geo,
                "  _({})_".format(", ".join(marcas)) if marcas else ""))
            if campos:
                lineas.append("  - {}".format(campos))
    return "\n".join(lineas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbip", default=PBIP_POR_DEFECTO)
    ap.add_argument("--salida", default=os.path.join(
        os.path.expanduser("~"), "Desktop", "Panel-Prospeccion-para-IA.md"))
    args = ap.parse_args()

    sm = [d for d in os.listdir(args.pbip) if d.endswith(".SemanticModel")][0]
    defs = os.path.join(args.pbip, sm, "definition")

    partes = []
    partes.append("""# Panel de prospección de flota — modelo, vistas e informe

Panel de Power BI de una consultora de *asset management* aeronáutico. Sirve para
encontrar clientes a partir de transiciones de flota: quién está rotando aviones,
quién tiene mucha superficie de contrato de arrendamiento y a quién le entra flota
en ventana de reemplazo.

Los datos salen de informes anuales presentados a la SEC (10-K y 20-F) y de las
bolsas chinas, extraídos con un pipeline propio y normalizados en BigQuery. El
panel consume vistas, no tablas.

**Cifras de contexto** (a {fecha}): 25 grupos aeronáuticos, 12.189 aeronaves,
3.253 pedidos en firme. 16 grupos con serie financiera comparable y 16 con serie
de flota; de estos últimos, 12 dan una serie limpia y 3 tienen algún ejercicio
marcado como no fiable.

**Cómo leer este documento.** El modelo semántico y las vistas SQL van literales:
son la sustancia. El informe va como inventario de páginas y visuales, porque su
JSON es en su mayor parte andamiaje del formato. El pipeline de extracción no
está aquí: queda aguas arriba y no hace falta para razonar sobre el panel.
""".format(fecha=date.today().isoformat()))

    # ---------------------------------------------------------------- modelo
    partes.append("\n---\n\n## 1. Modelo semántico (TMDL)\n")
    partes.append("Formato TMDL de Power BI. Una tabla por fichero; `_Medidas` "
                  "es una tabla sin datos que solo contiene medidas DAX.\n")
    for nombre in ("model.tmdl", "relationships.tmdl"):
        ruta = os.path.join(defs, nombre)
        if os.path.exists(ruta):
            partes.append("\n### `{}`\n\n```tmdl\n{}\n```\n".format(nombre, leer(ruta).rstrip()))
    tablas = os.path.join(defs, "tables")
    for f in sorted(os.listdir(tablas)):
        if f.endswith(".tmdl"):
            partes.append("\n### `tables/{}`\n\n```tmdl\n{}\n```\n".format(
                f, leer(os.path.join(tablas, f)).rstrip()))

    # ------------------------------------------------------------------ SQL
    partes.append("\n---\n\n## 2. Vistas de BigQuery\n")
    partes.append("Cada tabla del modelo se alimenta de una de estas vistas. "
                  "Aquí está la lógica de negocio: el score de prospecto, la "
                  "normalización de modelos de aeronave y la medición de "
                  "transición de flota.\n")
    sql = os.path.join(RAIZ, "sql")
    for f in sorted(os.listdir(sql)):
        if f.endswith(".sql"):
            partes.append("\n### `sql/{}`\n\n```sql\n{}\n```\n".format(
                f, leer(os.path.join(sql, f)).rstrip()))

    # -------------------------------------------------------------- informe
    partes.append("\n---\n\n## 3. Informe: páginas y visuales\n")
    partes.append("Lienzo de 1280 × 720 en todas las páginas. Retícula de 12 "
                  "columnas de 93 px con canal de 12 y margen de 16, así que los "
                  "anchos válidos son 198, 303, 408, 618, 828, 933 y 1248.\n")
    partes.append(inventario_informe(args.pbip))

    # ----------------------------------------------------------------- tema
    tema = os.path.join(RAIZ, "powerbi", "tema.json")
    if os.path.exists(tema):
        partes.append("\n---\n\n## 4. Tema\n\n```json\n{}\n```\n".format(leer(tema).rstrip()))

    texto = "\n".join(partes)

    # Revision antes de escribir: este fichero esta hecho para salir de aqui.
    hallazgos = [m.group(0) for m in SOSPECHOSO.finditer(texto)]
    if hallazgos:
        print("ATENCION: posibles credenciales en la salida, no se escribe:")
        for h in set(hallazgos):
            print("   " + h[:40])
        sys.exit(1)

    io.open(args.salida, "w", encoding="utf-8", newline="\n").write(texto)
    print("escrito: {}".format(args.salida))
    print("  {:,} lineas | {:,} KB".format(
        texto.count("\n") + 1, len(texto.encode("utf-8")) // 1024))
    print("  medidas DAX: {}".format(len(re.findall(r"^\tmeasure ", texto, re.M))))
    print("  vistas SQL:  {}".format(len(re.findall(r"^### `sql/", texto, re.M))))
    print("  paginas:     {}".format(len(re.findall(r"^### \d+\. ", texto, re.M))))


if __name__ == "__main__":
    main()
