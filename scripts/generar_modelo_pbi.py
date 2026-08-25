# -*- coding: utf-8 -*-
"""Genera powerbi/modelo.json a partir de los esquemas reales de BigQuery.

El modelo tabular no infiere el esquema de una tabla agregada por script: hay que
declararle cada columna con su tipo. Escribirlas a mano se desincroniza en cuanto
una vista cambia, asi que se leen del almacen y se emiten aqui.

El resultado, junto con powerbi/medidas.json, es la definicion completa del
modelo. scripts/aplicar_modelo_pbi.ps1 la aplica.

Uso:
    python scripts/generar_modelo_pbi.py
"""
import io
import json
import os
import sys

from google.cloud import bigquery

PROYECTO = "mach5-gemini-project"
DATASET = "dataset_integrado"

# Tabla del modelo -> vista del almacen. El orden importa solo para la lectura.
TABLAS = [
    ("dim_airline", "dim_airline", "dimension",
     "Catalogo canonico de aerolineas. group_id pliega las subsidiarias en su matriz."),
    ("dim_aircraft_type", "dim_aircraft_type", "dimension",
     "Taxonomia de modelos: fabricante, categoria, generacion y vida util de diseño."),
    ("v_fleet_normalized", "v_fleet_normalized", "hecho",
     "Flota vigente resuelta contra ambas dimensiones, con vida remanente y estado de reemplazo."),
    ("v_order_book_normalized", "v_order_book_normalized", "hecho",
     "Pedidos con el modelo normalizado y la fecha de entrega parseada."),
    ("v_prospect_score", "v_prospect_score", "hecho",
     "Ranking de prospectos por grupo, con los componentes del score expuestos."),
    ("v_fleet_transition", "v_fleet_transition", "hecho",
     "Movimiento de flota observado, modelo por modelo. Solo cubre a quien publica la comparativa."),
    ("v_financial_trend", "v_financial_trend", "hecho",
     "Tendencia de arrendamiento, deuda y capex sobre la serie financiera."),
]

# Relaciones, siempre por identificador y nunca por nombre: un cambio de
# display_name no debe romper el modelo.
RELACIONES = [
    ("v_fleet_normalized", "group_id", "dim_airline", "airline_id"),
    ("v_fleet_normalized", "aircraft_model", "dim_aircraft_type", "model"),
    ("v_order_book_normalized", "group_id", "dim_airline", "airline_id"),
    ("v_order_book_normalized", "aircraft_model", "dim_aircraft_type", "model"),
    ("v_prospect_score", "group_id", "dim_airline", "airline_id"),
    ("v_fleet_transition", "group_id", "dim_airline", "airline_id"),
    ("v_fleet_transition", "aircraft_model", "dim_aircraft_type", "model"),
    ("v_financial_trend", "group_id", "dim_airline", "airline_id"),
]

TIPOS = {
    "STRING": "string", "BYTES": "string",
    "INT64": "int64", "INTEGER": "int64",
    "FLOAT64": "double", "FLOAT": "double",
    "NUMERIC": "decimal", "BIGNUMERIC": "decimal",
    "BOOL": "boolean", "BOOLEAN": "boolean",
    "DATE": "dateTime", "DATETIME": "dateTime", "TIMESTAMP": "dateTime",
}

# Columnas que solo sirven de llave: se ocultan para que el panel de campos no
# se llene de identificadores que nadie arrastra a un visual.
OCULTAS = {"group_id", "airline_id", "fleet_id", "order_id", "model",
           "financial_company", "parent_id"}


def expresion_m(vista):
    """Consulta M contra BigQuery. Se listan las columnas explicitamente porque
    Table.SelectColumns tambien excluye las repetidas, que el modelo no admite."""
    return (
        'let\n'
        '    Origen = GoogleBigQuery.Database([BillingProject="{p}"]),\n'
        '    Proyecto = Origen{{[Name="{p}"]}}[Data],\n'
        '    Dataset = Proyecto{{[Name="{d}",Kind="Schema"]}}[Data],\n'
        '    Tabla = Dataset{{[Name="{v}",Kind="View"]}}[Data]\n'
        'in\n'
        '    Tabla'
    ).format(p=PROYECTO, d=DATASET, v=vista)


def main():
    cliente = bigquery.Client(project=PROYECTO)
    tablas = []
    omitidas = []

    for nombre, vista, clase, descripcion in TABLAS:
        tb = cliente.get_table("{}.{}.{}".format(PROYECTO, DATASET, vista))
        columnas = []
        for campo in tb.schema:
            if campo.mode == "REPEATED":
                # El modelo tabular no admite columnas de lista
                omitidas.append("{}.{}".format(nombre, campo.name))
                continue
            tipo = TIPOS.get(campo.field_type)
            if tipo is None:
                omitidas.append("{}.{} ({})".format(nombre, campo.name, campo.field_type))
                continue
            columnas.append({
                "nombre": campo.name,
                "tipo": tipo,
                "oculta": campo.name in OCULTAS,
            })

        tablas.append({
            "nombre": nombre,
            "vista_origen": vista,
            "clase": clase,
            "descripcion": descripcion,
            "expresion_m": expresion_m(vista),
            "columnas": columnas,
        })

    relaciones = [
        {"desde_tabla": a, "desde_columna": b, "hacia_tabla": c, "hacia_columna": d}
        for a, b, c, d in RELACIONES
    ]

    # Comprobacion: una relacion contra una columna que no existe se aplicaria
    # sin error y dejaria el modelo mudo.
    porTabla = {t["nombre"]: {c["nombre"] for c in t["columnas"]} for t in tablas}
    for r in relaciones:
        for lado in ("desde", "hacia"):
            t, c = r[lado + "_tabla"], r[lado + "_columna"]
            if t not in porTabla:
                sys.exit("relacion contra tabla desconocida: {}".format(t))
            if c not in porTabla[t]:
                sys.exit("relacion contra columna inexistente: {}[{}]".format(t, c))

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    medidas = json.load(io.open(os.path.join(raiz, "powerbi", "medidas.json"), encoding="utf-8"))

    modelo = {
        "generado_por": "scripts/generar_modelo_pbi.py",
        "advertencia": "Archivo generado. Editar powerbi/medidas.json o el generador, no este archivo.",
        "proyecto": PROYECTO,
        "dataset": DATASET,
        "tabla_medidas": medidas["tabla_medidas"],
        "tablas": tablas,
        "relaciones": relaciones,
        "medidas": medidas["medidas"],
    }

    destino = os.path.join(raiz, "powerbi", "modelo.json")
    io.open(destino, "w", encoding="utf-8", newline="\n").write(
        json.dumps(modelo, ensure_ascii=False, indent=2) + "\n")

    print("{} tablas, {} columnas, {} relaciones, {} medidas".format(
        len(tablas), sum(len(t["columnas"]) for t in tablas),
        len(relaciones), len(modelo["medidas"])))
    if omitidas:
        print("columnas omitidas (repetidas o de tipo no soportado): {}".format(omitidas))
    print("escrito: powerbi/modelo.json")


if __name__ == "__main__":
    main()
