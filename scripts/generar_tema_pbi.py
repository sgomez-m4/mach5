# -*- coding: utf-8 -*-
"""Genera powerbi/tema.json, el tema de Power BI de mas4 Aviation.

La paleta no esta elegida a ojo. Los tonos de marca salen de muestrear los pixeles
del logotipo y del CSS de mas4aviation.com; los colores de serie se validaron con
el comprobador de seis pruebas -banda de luminosidad, piso de croma, separacion
bajo deuteranopia/protanopia/tritanopia, piso de vision normal y contraste contra
la superficie- iterando hasta que pasaran.

Dos hallazgos de esa validacion condicionan el diseno:

  - Una paleta solo con tonos frios es imposible bajo CVD. Azul y violeta se
    separan por su contenido de rojo, que es justo lo que la deuteranopia elimina:
    quedan en delta-E 3.8, indistinguibles. La identidad necesita el eje calido-frio.

  - Ocho colores es el techo. Con el criterio de pares adyacentes pasan ocho; a
    partir de ahi no hay sitio en el espacio de color. La novena serie no se
    inventa: se pliega en "Otros" o se factoriza en multiplos pequenos.

El estado no comparte espacio con la identidad. Rojo, ambar y verde de estado
llevan siempre icono y etiqueta, nunca color solo, y sus valores concretos no
reaparecen como color de serie.

Uso:
    python scripts/generar_tema_pbi.py                  # con Nunito, la de marca
    python scripts/generar_tema_pbi.py --fuente "Segoe UI"
"""
import argparse
import io
import json
import os

# --------------------------------------------------------------- marca
# Muestreados del logotipo (logo-mas4-aviation.png) y del CSS del sitio.
AZUL_MARCA = "#1D70B5"   # el que declara el CSS; el logo da #2070B0, el mismo tono
AZUL_MEDIO = "#2E9BD9"
AZUL_CIELO = "#5BC0EF"
AZUL_PALIDO = "#90D0F0"
AZUL_PROFUNDO = "#0E4C7A"

TINTA = "#333333"        # texto del sitio
GRIS_MARCA = "#6F6F6E"   # gris secundario del sitio
GRIS_FRIO = "#69727D"

# ------------------------------------------------- identidad (validada)
# Orden fijo. Power BI asigna dataColors por posicion, asi que el azul de marca
# encabeza cualquier visual de una sola serie. El orden ademas es el que paso la
# comprobacion de pares adyacentes: cambiarlo puede romper la separacion CVD.
SERIES = [
    AZUL_MARCA,  # azul mas4
    "#E2761B",   # naranja
    "#00897B",   # teal
    "#7CB342",   # lima
    "#C2185B",   # carmin
    "#7B5FC4",   # violeta
    "#00A3C4",   # cian
    "#A1662F",   # ocre
]

# ------------------------------------------------------------- estado
# Reservados. No aparecen en SERIES y siempre se acompanan de icono y etiqueta.
ESTADO = {
    "replace_now": "#B3261E",   # rojo profundo
    "critical":    "#D2691E",   # naranja quemado
    "monitor":     "#C99700",   # ambar
    "healthy":     "#2E7D52",   # verde profundo
    "sin_dato":    "#9AA5B1",   # gris frio
}

# La distincion que mas importa del plan: lo medido frente a lo inferido.
# Comparte el verde de "healthy" a proposito -ambos significan "esto se sabe"- y
# el ambar de "monitor" para lo inferido: no es un error, es menos certeza.
COBERTURA = {
    "medido":   ESTADO["healthy"],
    "inferido": ESTADO["monitor"],
    "sin_dato": ESTADO["sin_dato"],
}

# ------------------------------------------- lo que admite el esquema
# Tomadas de reportThemeSchema-2.114.json. El esquema declara
# additionalProperties: false, asi que una sola clave de mas hace que Power BI
# rechace el archivo entero con un dialogo que enumera las cuatro causas posibles
# sin decir cual es. Van embebidas para poder comprobar sin red.
PROPIEDADES_VALIDAS = {
    "$schema", "accent", "background", "backgroundDark", "backgroundLight",
    "backgroundNeutral", "bad", "center", "dataColors", "disabledText",
    "firstLevelElements", "foreground", "foregroundButton", "foregroundDark",
    "foregroundLight", "foregroundNeutralDark", "foregroundNeutralLight",
    "foregroundNeutralSecondary", "foregroundNeutralSecondaryAlt",
    "foregroundNeutralSecondaryAlt2", "foregroundNeutralTertiary",
    "foregroundNeutralTertiaryAlt", "foregroundSelected", "fourthLevelElements",
    "good", "hyperlink", "icons", "mapPushpin", "maximum", "minimum", "name",
    "neutral", "null", "secondLevelElements", "secondaryBackground",
    "shapeStroke", "tableAccent", "textClasses", "thirdLevelElements",
    "visitedHyperlink", "visualStyles",
}

CLASES_TEXTO_VALIDAS = {
    "boldLabel", "callout", "dataTitle", "header", "label", "largeLabel",
    "largeLightLabel", "largeTitle", "lightLabel", "semiboldLabel",
    "smallDataLabel", "smallLabel", "smallLightLabel", "title",
}


def comprobar(tema):
    """Aborta si el tema lleva algo que Power BI no admite."""
    malas = sorted(k for k in tema if k not in PROPIEDADES_VALIDAS)
    if malas:
        raise SystemExit(
            "propiedades de nivel superior no admitidas: {}\n"
            "Power BI rechazaria el archivo entero sin decir cual es.".format(", ".join(malas)))

    clases = sorted(k for k in tema.get("textClasses", {}) if k not in CLASES_TEXTO_VALIDAS)
    if clases:
        raise SystemExit("clases de texto no admitidas: {}".format(", ".join(clases)))

    for nombre, valor in tema.items():
        if isinstance(valor, str) and valor.startswith("#") and len(valor) not in (4, 7):
            raise SystemExit("color mal formado en '{}': {}".format(nombre, valor))


# ------------------------------------------------------ superficies
FONDO = "#FFFFFF"
FONDO_SUAVE = "#F4F6F8"
LINEA = "#DCE1E6"


def construir(fuente):
    texto_base = {"fontFace": fuente, "color": TINTA}

    return {
        "name": "mas4 Aviation - Prospeccion de flota",
        "$schema": "https://raw.githubusercontent.com/microsoft/powerbi-desktop-samples/main/Report%20Theme%20JSON%20Schema/reportThemeSchema-2.114.json",

        # Identidad. Orden fijo y validado; no reordenar sin volver a comprobar.
        "dataColors": SERIES,

        # Ranuras clasicas del esquema. Power BI rechaza el archivo entero -y no
        # dice cual- si encuentra una clave que no reconoce, asi que aqui solo van
        # nombres documentados.
        "background": FONDO,
        "backgroundLight": FONDO_SUAVE,
        "backgroundNeutral": LINEA,
        "foreground": TINTA,
        "foregroundNeutralSecondary": GRIS_MARCA,
        "foregroundNeutralTertiary": "#9AA5B1",
        "tableAccent": AZUL_MARCA,
        "hyperlink": AZUL_MARCA,
        "visitedHyperlink": AZUL_PROFUNDO,
        "disabledText": "#A9B2BB",
        "shapeStroke": GRIS_MARCA,

        # Sentimiento de los KPI
        "good": ESTADO["healthy"],
        "neutral": ESTADO["monitor"],
        "bad": ESTADO["replace_now"],

        # Divergente para variacion: calido cae, azul de marca sube, gris al centro.
        # El punto medio es gris y no un tono: un tono en el centro inventa una
        # tercera categoria donde solo hay ausencia de cambio.
        "minimum": "#C05621",
        "center": "#B9C2CC",
        "maximum": AZUL_MARCA,
        "null": ESTADO["sin_dato"],

        "textClasses": {
            "title":      dict(texto_base, fontSize=16),
            "header":     dict(texto_base, fontSize=13),
            "label":      dict(texto_base, fontSize=10),
            # 24 y no 32: a 32 el numero no cabe en una tarjeta de 88 px de alto
            # y Power BI lo recorta por abajo sin avisar.
            "callout":    {"fontFace": fuente, "fontSize": 24, "color": AZUL_PROFUNDO},
            "largeTitle": dict(texto_base, fontSize=20),
            "lightLabel": {"fontFace": fuente, "fontSize": 10, "color": GRIS_MARCA},
            "boldLabel":  dict(texto_base, fontSize=10),
        },

        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": FONDO}},
                                    "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": LINEA}}}],
                    "title": [{"show": True, "fontColor": {"solid": {"color": TINTA}},
                               "background": {"solid": {"color": FONDO}},
                               "alignment": "left", "fontSize": 12}],
                    # La leyenda siempre presente: con dos o mas series la identidad
                    # no puede depender solo del color.
                    "legend": [{"show": True, "position": "TopCenter",
                                "showTitle": False, "fontSize": 9,
                                "labelColor": {"solid": {"color": GRIS_MARCA}}}],
                    # Rejilla y ejes recesivos: son andamiaje, no dato.
                    "categoryAxis": [{"show": True, "fontSize": 9,
                                      "labelColor": {"solid": {"color": GRIS_MARCA}},
                                      "showAxisTitle": False}],
                    "valueAxis": [{"show": True, "fontSize": 9,
                                   "labelColor": {"solid": {"color": GRIS_MARCA}},
                                   "gridlineColor": {"solid": {"color": LINEA}},
                                   "showAxisTitle": False}],
                },
            },
            "page": {
                "*": {
                    "background": [{"color": {"solid": {"color": FONDO_SUAVE}},
                                    "transparency": 0}],
                    "outspace": [{"color": {"solid": {"color": FONDO_SUAVE}}}],
                },
            },
        },
    }


def main():
    ap = argparse.ArgumentParser()
    # Segoe UI por defecto y no Nunito: Nunito es la de la web de la empresa pero
    # hay que instalarla en cada maquina que abra el informe, y cuando falta Power
    # BI no avisa: cae a una serif y el panel entero pasa a parecer un documento.
    # Segoe UI viene con Windows y con el servicio, asi que se ve igual en todas
    # partes. Con --fuente Nunito se recupera la de marca donde este instalada.
    ap.add_argument("--fuente", default="Segoe UI",
                    help="Familia tipografica. Segoe UI esta garantizada; Nunito es "
                         "la de marca pero hay que instalarla en cada maquina.")
    args = ap.parse_args()

    tema = construir(args.fuente)
    comprobar(tema)

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    destino = os.path.join(raiz, "powerbi", "tema.json")
    io.open(destino, "w", encoding="utf-8", newline="\n").write(
        json.dumps(tema, ensure_ascii=False, indent=2) + "\n")

    print("tema escrito: powerbi/tema.json")
    print("  tipografia: {}".format(args.fuente))
    print("  series: {} colores validados".format(len(SERIES)))
    print("  estado: {}".format(", ".join("{}={}".format(k, v) for k, v in ESTADO.items())))
    print("  cobertura: medido={} inferido={}".format(COBERTURA["medido"], COBERTURA["inferido"]))


if __name__ == "__main__":
    main()
