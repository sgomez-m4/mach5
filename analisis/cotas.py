# -*- coding: utf-8 -*-
"""Cotas de la fracción en ventana de reemplazo para los grupos con edad incompleta.

La corrección de cobertura del artículo (sensibilidad.py, prueba 4) supone que las
aeronaves sin edad declarada se parecen a las que sí la declaran. Este script no
supone nada sobre ellas: calcula el intervalo de valores posibles y lo estrecha solo
con lo que se sabe con certeza por el modelo.

  - Cota inferior: ninguna aeronave sin edad está en ventana (denominador original).
  - Cota superior: todas lo están.
  - Cota inferior acotada: las de modelos cuya producción terminó hace tanto que su
    edad mínima ya supera el inicio de la ventana (meta de reemplazo - 3 años) están,
    con certeza, en ventana.

Para cada escenario recalcula el componente de reemplazo y la puntuación del grupo,
deja fijos a los demás y devuelve el ranking.

Uso:
    python -I cotas.py <score_full.csv> <ventana.csv> <sin_edad.csv> <salida.json>

sin_edad.csv: group_id, aircraft_model, replacement_target, sin_edad (exportar.sql, consulta 3).
"""
import csv
import io
import json
import sys

ANIO_REPORTE = 2025
VENTANA_ANIOS = 3  # estado crítico: vida remanente <= 3 años

# Último año de producción de modelos ya descontinuados (datos externos al almacén).
# CRJ200: Bombardier interrumpió la línea en enero de 2006 ("Bombardier to suspend
#   CRJ200 production line", Aviation Week, 31 oct. 2005); la reapertura posterior fue
#   sobre todo para el Challenger 850. El resultado no cambia mientras el CRJ200 se haya
#   fabricado en 2008 o antes (meta 20 - 3 años de ventana = 17 años a fines de 2025).
# MD-11: "Production of the MD-11 ended in 2001" ("Aircraft profile: MD-11",
#   Al Jazeera English, 23 mar. 2009).
FIN_PRODUCCION = {
    "CRJ200": 2006,
    "MD-11": 2001,
}

PESOS = {"leasing": 0.35, "reemplazo": 0.30, "complejidad": 0.15,
         "transicion": 0.12, "apalancamiento": 0.08}


def leer(ruta):
    return list(csv.DictReader(io.open(ruta, encoding="utf-8")))


def num(x):
    return None if x in (None, "", "NULL") else float(x)


def componente_reemplazo(v, vida):
    vida = 10.0 if vida is None else vida
    return 100 * (0.65 * min(v, 1) + 0.35 * (1 - min(vida / 10, 1)))


def puntuacion(f, reemplazo):
    return (PESOS["leasing"] * num(f["score_leasing"]) + PESOS["reemplazo"] * reemplazo
            + PESOS["complejidad"] * num(f["score_complejidad"])
            + PESOS["transicion"] * num(f["score_transicion"])
            + PESOS["apalancamiento"] * num(f["score_apalancamiento"]))


def ranking(puntos):
    orden = sorted(puntos, key=lambda g: -puntos[g])
    return {g: i + 1 for i, g in enumerate(orden)}


def main(ruta_score, ruta_ventana, ruta_sin_edad, ruta_salida):
    score = {f["group_id"]: f for f in leer(ruta_score)}
    ventana = {f["grupo"]: f for f in leer(ruta_ventana)}
    sin_edad = {}
    for f in leer(ruta_sin_edad):
        sin_edad.setdefault(f["group_id"], []).append(f)

    base = {g: puntuacion(f, num(f["score_reemplazo"])) for g, f in score.items()}

    escenarios = {}
    detalle = {}
    for g, filas in sin_edad.items():
        f = score[g]
        total = num(f["flota_total"])
        # conteos exactos: el porcentaje exportado viene redondeado a un decimal
        cuenta = ventana[f["grupo"]]
        en_ventana = num(cuenta["ventana"])
        total = num(cuenta["total"])
        v0 = en_ventana / total
        reconstruido = componente_reemplazo(v0, num(f["vida_remanente"]))
        if abs(reconstruido - num(f["score_reemplazo"])) > 0.25:
            sys.exit("{}: el componente reconstruido ({:.2f}) no reproduce el de la vista ({})".format(
                g, reconstruido, f["score_reemplazo"]))
        faltan = sum(num(x["sin_edad"]) for x in filas)
        seguras = 0.0
        for x in filas:
            fin = FIN_PRODUCCION.get(x["aircraft_model"])
            umbral = num(x["replacement_target"]) - VENTANA_ANIOS
            if fin is not None and ANIO_REPORTE - fin >= umbral:
                seguras += num(x["sin_edad"])
        vida = num(f["vida_remanente"])
        con_edad = total - faltan
        v = {
            "inferior": v0,
            "inferior_acotada": (en_ventana + seguras) / total,
            "supuesto_del_articulo": (en_ventana / con_edad) if con_edad > 0 else None,
            "superior": (en_ventana + faltan) / total,
        }
        detalle[g] = {"grupo": f["grupo"], "flota": total, "sin_edad": faltan,
                      "seguras_en_ventana": seguras,
                      "fraccion_en_ventana": {k: (round(x, 4) if x is not None else None) for k, x in v.items()}}
        for k, x in v.items():
            if x is None:
                continue
            escenarios.setdefault(k, {})[g] = puntuacion(f, componente_reemplazo(x, vida))

    resultado = {"detalle": detalle, "escenarios": {}}
    for k, cambios in escenarios.items():
        puntos = dict(base)
        puntos.update(cambios)
        r = ranking(puntos)
        top5 = sorted([g for g in r if r[g] <= 5], key=lambda g: r[g])
        resultado["escenarios"][k] = {
            "lider": score[top5[0]]["grupo"],
            "top5": [score[g]["grupo"] for g in top5],
            "grupos": {score[g]["grupo"]: {"puntuacion": round(puntos[g], 1), "rango": r[g]} for g in cambios},
        }
    r0 = ranking(base)
    resultado["base"] = {"top5": [score[g]["grupo"] for g in sorted(r0, key=lambda g: r0[g])[:5]]}
    io.open(ruta_salida, "w", encoding="utf-8").write(json.dumps(resultado, ensure_ascii=False, indent=2))
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
