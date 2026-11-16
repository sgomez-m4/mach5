# -*- coding: utf-8 -*-
"""Exporta los insumos sin redondear del prospect score (la CTE `base` de la vista).

La vista publica sus insumos redondeados a porcentajes enteros, que no bastan para
reproducir los componentes. Este script toma el SQL versionado de la vista, lo corta
antes de la CTE `componentes` y selecciona la CTE `base`, de modo que los insumos
salen de la misma lógica que el puntaje publicado.

Uso:
    python exportar_insumos.py <sql/v_prospect_score.sql> <salida.csv>
"""
import csv
import io
import sys

from google.cloud import bigquery

COLUMNAS = ["group_id", "grupo", "flota_total", "pct_operating_lease", "pct_finance_lease",
            "lease_conc_3y", "lease_conc_1y", "pct_ventana_reemplazo", "vida_remanente_ponderada",
            "n_modelos", "n_fabricantes", "mezcla_generaciones", "ratio_pedidos_flota",
            "pedidos_proximos_2a", "net_debt_ebitda"]


def main(ruta_sql, ruta_salida):
    sql = io.open(ruta_sql, encoding="utf-8").read()
    inicio = sql.index("WITH flota AS (")
    fin = sql.index("componentes AS (")
    cuerpo = sql[inicio:fin].rstrip().rstrip(",")
    consulta = cuerpo + "\nSELECT " + ", ".join(COLUMNAS) + " FROM base ORDER BY group_id"
    filas = bigquery.Client(project="mach5-gemini-project").query(consulta).result()
    with io.open(ruta_salida, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNAS)
        n = 0
        for r in filas:
            w.writerow(["" if r[c] is None else r[c] for c in COLUMNAS])
            n += 1
    print("{} grupos exportados a {}".format(n, ruta_salida))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
