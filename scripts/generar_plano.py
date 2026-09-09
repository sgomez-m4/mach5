# -*- coding: utf-8 -*-
"""Regenera las tablas de tamaños del Plano leyendo el .pbix.

El Plano documenta el panel: qué hay en cada página, dónde y por qué. La parte
del "por qué" se escribe a mano y no la toca nadie. La del "dónde" son seis
tablas de coordenadas, y esas se desactualizan en cuanto alguien mueve un
visual, así que se sacan del archivo en vez de mantenerlas a mano.

    python scripts/generar_plano.py                       # informa, no escribe
    python scripts/generar_plano.py --escribir            # actualiza el Plano

Sustituye solo el bloque marcado con MEDIDO-POR-EL-ARCHIVO. Todo lo demás del
documento queda intacto, así que se puede correr las veces que haga falta.
"""
import argparse
import io
import json
import os
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MARCA = "MEDIDO-POR-EL-ARCHIVO"
FIN_SECCION = "    </section>"

# Power BI guarda el `nativeQueryRef` con el que se creó la proyeccion, aunque
# la columna se renombre despues en el modelo. La pantalla muestra el nombre
# nuevo y el archivo el viejo, asi que hay que traducirlo o el Plano vuelve a
# citar los nombres de BigQuery.
NOMBRES = {
    "display_name": "Aerolínea", "business_model": "Modelo de negocio",
    "country": "País", "region": "Región",
    "segmento_prospecto": "Segmento", "flota_total": "Flota total",
    "pct_operating_lease": "% arrendamiento operativo",
    "edad_ponderada": "Edad ponderada",
    "sin_dato_edad": "Sin dato de edad",
    "sin_dato_vencimientos": "Sin dato de vencimientos",
    "sin_datos_financieros": "Sin datos financieros",
    "aeronaves_en_filas_agregadas": "Aeronaves en filas agregadas",
    "pct_venc_leasing_1a": "% vence en 1 año",
    "pct_venc_leasing_3a": "% vence en 3 años",
    "leasing_venc_total_usdm": "Vencimientos (M USD)",
    "aircraft_model": "Modelo", "manufacturer": "Fabricante",
    "category": "Categoría", "generation": "Generación",
    "quantity_committed": "Unidades comprometidas", "quantity": "Aeronaves",
    "tipo_propiedad": "Tipo de propiedad",
    "average_age_years": "Edad media (años)",
    "remaining_replacement_life": "Vida remanente (años)",
    "fleet_replacement_status": "Estado de reemplazo",
    "ventana_entrega": "Ventana de entrega",
    "anio_primera_entrega": "Primera entrega",
    "fecha_aproximada": "Fecha aproximada",
    "aerolinea": "Aerolínea", "anios_con_datos": "Años con datos",
    "anio_inicial": "Año inicial", "anio_final": "Año final",
    "senal_transicion": "Señal de transición",
    "aeronaves_inicial": "Aeronaves inicial",
    "aeronaves_final": "Aeronaves final",
    "variacion": "Variación", "movimiento": "Movimiento",
    "ejercicios": "Ejercicios",
}

TIPOS = {
    "card": "Tarjeta", "multiRowCard": "Tarjeta de varias filas",
    "tableEx": "Tabla", "pivotTable": "Matriz", "slicer": "Segmentación",
    "textbox": "Cuadro de texto", "barChart": "Barras",
    "clusteredBarChart": "Barras agrupadas",
    "hundredPercentStackedBarChart": "Barras 100%",
    "columnChart": "Columnas", "donutChart": "Anillo",
    "scatterChart": "Dispersión", "waterfallChart": "Cascada",
}


def cargar(z, nombre):
    crudo = z.read(nombre)
    for cod in ("utf-8-sig", "utf-8", "utf-16-le"):
        try:
            return json.loads(crudo.decode(cod))
        except Exception:
            continue
    return None


def titulo_de(visual):
    for entrada in (visual.get("visualContainerObjects") or {}).get("title") or []:
        try:
            return entrada["properties"]["text"]["expr"]["Literal"]["Value"].strip("'")
        except Exception:
            continue
    return None


def primer_campo(visual):
    estado = (visual.get("query") or {}).get("queryState") or {}
    crudo = ""
    for rol in ("Values", "Category", "Y", "Rows"):
        proys = (estado.get(rol) or {}).get("projections") or []
        if proys:
            crudo = proys[0].get("nativeQueryRef") or ""
            break
    else:
        for cont in estado.values():
            proys = (cont or {}).get("projections") or []
            if proys:
                crudo = proys[0].get("nativeQueryRef") or ""
                break
    return NOMBRES.get(crudo, crudo)


def etiqueta(visual):
    """Cómo se llama el visual en la tabla: su título si lo tiene, si no su campo."""
    nombre_tipo = TIPOS.get(visual.get("visualType", "?"), visual.get("visualType", "?"))
    titulo = titulo_de(visual)
    if titulo:
        return titulo, nombre_tipo
    if visual.get("visualType") == "textbox":
        return "Título de la página", nombre_tipo
    return (primer_campo(visual) or nombre_tipo), nombre_tipo


class FuentePbix(object):
    """El informe dentro de un .pbix: un ZIP con todo bajo Report/."""

    def __init__(self, ruta):
        self.z = zipfile.ZipFile(ruta)
        self.nombres = self.z.namelist()
        self.raiz = "Report/definition"

    def leer(self, rel):
        return cargar(self.z, "{}/{}".format(self.raiz, rel))

    def visuales(self, pid):
        prefijo = "{}/pages/{}/visuals/".format(self.raiz, pid)
        return [n for n in self.nombres
                if n.startswith(prefijo) and n.endswith("visual.json")]

    def leer_absoluto(self, ruta):
        return cargar(self.z, ruta)


class FuentePbip(object):
    """El informe de un proyecto PBIP: los mismos JSON, ya en carpetas.

    Es el formato que Microsoft soporta para editar el informe por script, y
    desde que el panel se guardo asi es la fuente buena. Se conserva el lector
    del .pbix porque el archivo antiguo sigue existiendo.
    """

    def __init__(self, carpeta):
        candidatos = [d for d in os.listdir(carpeta) if d.endswith(".Report")]
        if not candidatos:
            raise SystemExit("{} no parece un proyecto PBIP".format(carpeta))
        self.raiz = os.path.join(carpeta, candidatos[0], "definition")

    def leer(self, rel):
        ruta = os.path.join(self.raiz, rel.replace("/", os.sep))
        if not os.path.exists(ruta):
            return None
        return json.loads(io.open(ruta, encoding="utf-8-sig").read())

    def visuales(self, pid):
        base = os.path.join(self.raiz, "pages", pid, "visuals")
        if not os.path.isdir(base):
            return []
        return [os.path.join(base, d, "visual.json") for d in sorted(os.listdir(base))
                if os.path.exists(os.path.join(base, d, "visual.json"))]

    def leer_absoluto(self, ruta):
        return json.loads(io.open(ruta, encoding="utf-8-sig").read())


def abrir(origen):
    return FuentePbip(origen) if os.path.isdir(origen) else FuentePbix(origen)


def leer_paginas(origen):
    fuente = abrir(origen)
    orden = fuente.leer("pages/pages.json") or {}
    paginas = []
    for pid in orden.get("pageOrder") or []:
        pj = fuente.leer("pages/{}/page.json".format(pid)) or {}
        filas = []
        for r in fuente.visuales(pid):
            v = fuente.leer_absoluto(r) or {}
            pos = v.get("position") or {}
            nombre, tipo = etiqueta(v.get("visual") or {})
            filas.append({"y": int(round(pos.get("y", 0))),
                          "x": int(round(pos.get("x", 0))),
                          "w": int(round(pos.get("width", 0))),
                          "h": int(round(pos.get("height", 0))),
                          "nombre": nombre, "tipo": tipo})
        filas.sort(key=lambda f: (f["y"], f["x"]))
        paginas.append({"nombre": pj.get("displayName", "?"), "filas": filas})
    return paginas


def escapar(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bloque(paginas):
    partes = ["      <!-- {}: estas seis tablas las escribe".format(MARCA),
              "           scripts/generar_plano.py leyendo Prospeccion_Flota.pbix.",
              "           No las edites a mano: se sobrescriben. -->"]
    for p in paginas:
        filas = ['            <tr><td>{} · {}</td><td class="num">{}</td>'
                 '<td class="num">{}</td><td class="num">{}</td>'
                 '<td class="num">{}</td></tr>'.format(
                     escapar(f["tipo"]), escapar(f["nombre"]),
                     f["x"], f["y"], f["w"], f["h"])
                 for f in p["filas"]]
        partes.append(
            '      <h3>{}</h3>\n'
            '      <div class="env-tabla">\n'
            '        <table>\n'
            '          <thead><tr><th>Visual</th><th class="num">X</th>'
            '<th class="num">Y</th><th class="num">Ancho</th>'
            '<th class="num">Alto</th></tr></thead>\n'
            '          <tbody>\n{}\n          </tbody>\n'
            '        </table>\n'
            '      </div>\n'.format(escapar(p["nombre"]), "\n".join(filas)))
    return "\n".join(partes)


def main():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    # Por defecto el proyecto PBIP, que es la fuente viva desde que el informe
    # se guarda asi. Admite tambien un .pbix suelto para leer el archivo antiguo.
    ap.add_argument("--pbix", dest="origen", default=os.path.join(
        raiz, "powerbi", "Prospeccion_Flota_PBIP"),
        help="carpeta del proyecto PBIP o ruta a un .pbix")
    ap.add_argument("--plano", default=os.path.join(
        os.path.expanduser("~"), "Desktop", "Plano-del-Panel.html"))
    ap.add_argument("--escribir", action="store_true",
                    help="sin esto solo informa de lo que cambiaria")
    args = ap.parse_args()

    paginas = leer_paginas(args.origen)
    nuevo = bloque(paginas)

    html = io.open(args.plano, encoding="utf-8").read()
    if MARCA not in html:
        sys.exit("el Plano no tiene el bloque {}; parchealo una vez a mano".format(MARCA))
    inicio = html.index("      <!-- {}".format(MARCA))
    fin = html.index(FIN_SECCION, inicio)

    print("paginas leidas de {}".format(os.path.basename(args.origen.rstrip("\/"))))
    for p in paginas:
        print("   {:<26} {} visuales".format(p["nombre"], len(p["filas"])))

    if html[inicio:fin].rstrip() == nuevo.rstrip():
        print("\nel Plano ya coincide con el archivo; no hay nada que escribir")
        return
    if not args.escribir:
        print("\nel Plano NO coincide con el archivo. Vuelve a correr con --escribir")
        return

    io.open(args.plano, "w", encoding="utf-8", newline="\n").write(
        html[:inicio] + nuevo + html[fin:])
    print("\nPlano actualizado: {}".format(args.plano))
    print("Sube el HTML al artefacto para que la version compartida cambie tambien.")


if __name__ == "__main__":
    main()
