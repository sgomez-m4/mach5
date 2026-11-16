# -*- coding: utf-8 -*-
"""Sensibilidad del ranking a los parámetros internos de los componentes.

sensibilidad.py perturba solo los cinco pesos de la puntuación. Aquí se perturban
los parámetros que viven dentro de cada componente: los repartos internos (por
ejemplo 0,50 / 0,20 / 0,30 en leasing) y las constantes de saturación (12 modelos,
6 veces deuda/EBITDA, 10 años de vida, etc.). Mismo criterio que con los pesos:
cada parámetro se multiplica por un factor independiente U(0,5; 1,5); los repartos
internos se renormalizan para seguir sumando 1.

Dos pruebas, con N simulaciones cada una y semilla fija:
  A. solo parámetros internos (pesos en la línea base);
  B. parámetros internos y pesos a la vez.

Antes de simular, se comprueba que con los parámetros base se reconstruyen los cinco
componentes publicados por la vista (diferencia <= 0,06, por el redondeo a un decimal).

Uso:
    python -I parametros.py <insumos.csv> <score_full.csv> <salida.json>
"""
import csv
import io
import json
import random
import sys

N = 10000
SEMILLA = 20261109
PESOS = {"leasing": 0.35, "reemplazo": 0.30, "complejidad": 0.15,
         "transicion": 0.12, "apalancamiento": 0.08}

BASE = {
    # repartos internos (se renormalizan)
    "leasing_reparto": [0.50, 0.20, 0.30],     # operativo, financiero, concentración
    "leasing_sin_calendario": [0.71, 0.29],    # operativo, financiero
    "reemplazo_reparto": [0.65, 0.35],         # fracción en ventana, vida remanente
    "complejidad_reparto": [0.45, 0.35, 0.20], # modelos, fabricantes, generaciones
    "transicion_reparto": [0.60, 0.40],        # pedidos/flota, entregas a 2 años
    # constantes (escalares)
    "factor_12_meses": 2.5,
    "horizonte_vida": 10.0,
    "saturacion_modelos": 12.0,
    "saturacion_fabricantes": 3.0,
    "saturacion_pedidos": 0.5,
    "saturacion_entregas": 0.2,
    "saturacion_deuda": 6.0,
    "deuda_por_defecto": 2.5,
}


def num(x):
    return None if x in (None, "", "NULL") else float(x)


def componentes(f, p):
    op = min(f["op"], 1.0)
    fi = min(f["fin"], 1.0)
    a, b, c = p["leasing_reparto"]
    if f["c3"] is not None:
        leasing = 100 * (a * op + b * fi + c * min(f["c3"], 1.0))
    elif f["c1"] is not None:
        leasing = 100 * (a * op + b * fi + c * min(f["c1"] * p["factor_12_meses"], 1.0))
    else:
        x, y = p["leasing_sin_calendario"]
        leasing = 100 * (x * op + y * fi)

    h = p["horizonte_vida"]
    vida = f["vida"] if f["vida"] is not None else h
    v, w = p["reemplazo_reparto"]
    reemplazo = 100 * (v * min(f["ventana"], 1.0) + w * (1 - min(vida / h, 1.0)))

    m, k, g = p["complejidad_reparto"]
    complejidad = 100 * (m * min(f["modelos"] / p["saturacion_modelos"], 1.0)
                         + k * min((f["fabricantes"] - 1) / p["saturacion_fabricantes"], 1.0)
                         + g * (1.0 if f["mezcla"] else 0.0))

    r, s = p["transicion_reparto"]
    transicion = 100 * (r * min(f["ratio"] / p["saturacion_pedidos"], 1.0)
                        + s * min(f["o2"] / p["saturacion_entregas"], 1.0))

    deuda = f["deuda"] if f["deuda"] is not None else p["deuda_por_defecto"]
    apalancamiento = 100 * min(max(deuda, 0) / p["saturacion_deuda"], 1.0)
    return {"leasing": leasing, "reemplazo": reemplazo, "complejidad": complejidad,
            "transicion": transicion, "apalancamiento": apalancamiento}


def puntuar(grupos, p, pesos):
    total = sum(pesos.values())
    return {g: sum(pesos[k] * v for k, v in componentes(f, p).items()) / total
            for g, f in grupos.items()}


def rangos(puntos):
    orden = sorted(puntos, key=lambda g: -puntos[g])
    return {g: i + 1 for i, g in enumerate(orden)}


def tau_b(x, y):
    claves = list(x)
    conc = disc = tx = ty = 0
    for i in range(len(claves)):
        for j in range(i + 1, len(claves)):
            dx = x[claves[i]] - x[claves[j]]
            dy = y[claves[i]] - y[claves[j]]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif dx * dy > 0:
                conc += 1
            else:
                disc += 1
    return (conc - disc) / (((conc + disc + tx) * (conc + disc + ty)) ** 0.5)


def perturbar(rng):
    p = {}
    for k, v in BASE.items():
        if isinstance(v, list):
            vals = [x * rng.uniform(0.5, 1.5) for x in v]
            s = sum(vals)
            p[k] = [x / s for x in vals]
        else:
            p[k] = v * rng.uniform(0.5, 1.5)
    return p


def percentil(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def simular(grupos, base_rangos, nombres, rng, tambien_pesos):
    taus, top5 = [], []
    en_top5 = {g: 0 for g in grupos}
    lideres = {}
    rango_g = {g: [] for g in grupos}
    for _ in range(N):
        p = perturbar(rng)
        pesos = ({k: v * rng.uniform(0.5, 1.5) for k, v in PESOS.items()}
                 if tambien_pesos else PESOS)
        r = rangos(puntuar(grupos, p, pesos))
        taus.append(tau_b(base_rangos, r))
        arriba = {g for g in r if r[g] <= 5}
        top5.append(len(arriba & {g for g in base_rangos if base_rangos[g] <= 5}))
        for g in arriba:
            en_top5[g] += 1
        lider = min(r, key=r.get)
        lideres[nombres[lider]] = lideres.get(nombres[lider], 0) + 1
        for g in r:
            rango_g[g].append(r[g])
    orden = sorted(grupos, key=lambda g: base_rangos[g])
    return {
        "tau_mediana": round(percentil(taus, 0.5), 3),
        "tau_p5": round(percentil(taus, 0.05), 3),
        "top5_compartido_medio": round(sum(top5) / N, 2),
        "lider": {k: round(v / N, 3) for k, v in sorted(lideres.items(), key=lambda kv: -kv[1])},
        "grupos": [{"grupo": nombres[g], "rango_base": base_rangos[g],
                    "p_top5": round(en_top5[g] / N, 2),
                    "rango_p5": percentil(rango_g[g], 0.05),
                    "rango_p95": percentil(rango_g[g], 0.95)} for g in orden],
    }


def main(ruta_insumos, ruta_score, ruta_salida):
    grupos, nombres = {}, {}
    for f in csv.DictReader(io.open(ruta_insumos, encoding="utf-8")):
        flota = num(f["flota_total"])
        grupos[f["group_id"]] = {
            "op": num(f["pct_operating_lease"]) or 0.0, "fin": num(f["pct_finance_lease"]) or 0.0,
            "c3": num(f["lease_conc_3y"]), "c1": num(f["lease_conc_1y"]),
            "ventana": num(f["pct_ventana_reemplazo"]) or 0.0,
            "vida": num(f["vida_remanente_ponderada"]),
            "modelos": num(f["n_modelos"]), "fabricantes": num(f["n_fabricantes"]),
            "mezcla": f["mezcla_generaciones"].lower() == "true",
            "ratio": num(f["ratio_pedidos_flota"]) or 0.0,
            "o2": (num(f["pedidos_proximos_2a"]) or 0.0) / flota,
            "deuda": num(f["net_debt_ebitda"]),
        }
        nombres[f["group_id"]] = f["grupo"]

    # 1) reconstrucción con los parámetros base
    publicados = {f["group_id"]: f for f in csv.DictReader(io.open(ruta_score, encoding="utf-8"))}
    peor = 0.0
    for g, f in grupos.items():
        c = componentes(f, BASE)
        for k in PESOS:
            d = abs(c[k] - float(publicados[g]["score_" + k]))
            peor = max(peor, d)
            if d > 0.06:
                sys.exit("{} {}: reconstruido {:.2f}, publicado {}".format(
                    g, k, c[k], publicados[g]["score_" + k]))
    print("reconstrucción de los componentes: diferencia máxima {:.3f}".format(peor))

    base = rangos(puntuar(grupos, BASE, PESOS))
    rng = random.Random(SEMILLA)
    resultado = {
        "n": N, "semilla": SEMILLA, "reconstruccion_max_dif": round(peor, 3),
        "A_solo_parametros": simular(grupos, base, nombres, rng, False),
        "B_parametros_y_pesos": simular(grupos, base, nombres, rng, True),
    }
    io.open(ruta_salida, "w", encoding="utf-8").write(json.dumps(resultado, ensure_ascii=False, indent=2))
    for k in ("A_solo_parametros", "B_parametros_y_pesos"):
        r = resultado[k]
        print("\n{}: tau mediana {} (p5 {}), top5 compartido medio {}".format(
            k, r["tau_mediana"], r["tau_p5"], r["top5_compartido_medio"]))
        print("   líder:", r["lider"])
        for x in r["grupos"][:8]:
            print("   {:>2}. {:24} P(top5)={:.2f}  rango p5-p95 {}-{}".format(
                x["rango_base"], x["grupo"], x["p_top5"], x["rango_p5"], x["rango_p95"]))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
