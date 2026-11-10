# -*- coding: utf-8 -*-
"""Analisis de sensibilidad del prospect score (evaluacion posterior a la entrega).

No forma parte del pipeline ni del tablero: lee una exportacion de la vista
v_prospect_score y no escribe nada en BigQuery ni en Power BI.

Cuatro preguntas:
  1. Reproducibilidad: el score publicado es exactamente la suma ponderada de sus
     componentes?
  2. Esquemas alternativos: cuanto cambia el ranking con otras tesis comerciales?
  3. Monte Carlo: con pesos perturbados al azar alrededor de la linea base, que
     tan estable es el top-5 y en que rango se mueve cada aerolinea?
  4. Sesgo por cobertura: la fraccion en ventana usa la flota TOTAL como
     denominador, asi que las aeronaves sin edad cuentan como "fuera de
     ventana". Que pasa si se calcula sobre la flota con edad y, cuando un
     grupo no tiene ninguna, se excluye el componente y se renormaliza?

Uso: python -I sensibilidad.py <score_full.csv> <salida.json> <ventana.csv>
"""
import csv
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ruta, salida = sys.argv[1], sys.argv[2]
filas = list(csv.DictReader(open(ruta, encoding="utf-8")))
COMP = ["score_leasing", "score_reemplazo", "score_complejidad",
        "score_transicion", "score_apalancamiento"]
BASE = [35, 30, 15, 12, 8]

datos = []
for f in filas:
    datos.append({
        "grupo": f["grupo"],
        "region": f["region"],
        "segmento": f["segmento_prospecto"],
        "publicado": float(f["prospect_score"]),
        "c": [float(f[k]) for k in COMP],
        "sin_edad": f["sin_dato_edad"].strip().lower() == "true",
        "flota": int(float(f["flota_total"])),
    })
n = len(datos)


def puntuar(pesos, excluir_reemplazo_si_falta=False):
    out = []
    for d in datos:
        w = list(pesos)
        if excluir_reemplazo_si_falta and d["sin_edad"]:
            w[1] = 0
        tot = sum(w)
        out.append(sum(c * x for c, x in zip(d["c"], w)) / tot if tot else 0.0)
    return out


def rangos(valores):
    # rango 1 = mejor; empates con rango medio
    orden = sorted(range(len(valores)), key=lambda i: -valores[i])
    r = [0.0] * len(valores)
    i = 0
    while i < len(orden):
        j = i
        while j + 1 < len(orden) and valores[orden[j + 1]] == valores[orden[i]]:
            j += 1
        medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[orden[k]] = medio
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = rangos(a), rangos(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den


def kendall(a, b):
    # tau-b
    conc = disc = ta = tb = 0
    for i in range(n):
        for j in range(i + 1, n):
            da, db = a[i] - a[j], b[i] - b[j]
            if da == 0 and db == 0:
                continue
            if da == 0:
                ta += 1
            elif db == 0:
                tb += 1
            elif da * db > 0:
                conc += 1
            else:
                disc += 1
    return (conc - disc) / (((conc + disc + ta) * (conc + disc + tb)) ** 0.5)


def top(valores, k):
    return set(sorted(range(n), key=lambda i: -valores[i])[:k])


res = {}

# ------------------------------------------------------------ 1. reproducibilidad
base = puntuar(BASE)
dif = [abs(round(b, 1) - d["publicado"]) for b, d in zip(base, datos)]
res["reproducibilidad"] = {"max_dif": max(dif), "n": n}
print("1. reproducibilidad: diferencia maxima {:.2f} sobre {} aerolineas".format(max(dif), n))

# ------------------------------------------------------- 2. esquemas alternativos
ESQ = {
    "Línea base (35/30/15/12/8)": BASE,
    "Pesos iguales (20 c/u)": [20, 20, 20, 20, 20],
    "Tesis leasing (60/15/10/10/5)": [60, 15, 10, 10, 5],
    "Tesis técnica (15/60/10/10/5)": [15, 60, 10, 10, 5],
    "Sin contexto financiero (35/30/15/12/0)": [35, 30, 15, 12, 0],
    "Solo leasing y reemplazo (50/50/0/0/0)": [50, 50, 0, 0, 0],
}
top5b, top10b = top(base, 5), top(base, 10)
res["esquemas"] = []
print("\n2. esquemas alternativos")
for nom, w in ESQ.items():
    s = puntuar(w)
    fila = {
        "esquema": nom,
        "spearman": round(spearman(base, s), 3),
        "kendall": round(kendall(base, s), 3),
        "top5": len(top5b & top(s, 5)),
        "top10": len(top10b & top(s, 10)),
        "lider": datos[max(range(n), key=lambda i: s[i])]["grupo"],
    }
    res["esquemas"].append(fila)
    print("  {:<42} rho={:.3f} tau={:.3f} top5={}/5 top10={}/10 lider={}".format(
        nom, fila["spearman"], fila["kendall"], fila["top5"], fila["top10"], fila["lider"]))

# ------------------------------------------------------------- 3. Monte Carlo
random.seed(20261109)
N = 10000
taus, rhos, t5 = [], [], []
pos = [[] for _ in range(n)]
en_top5 = [0] * n
rb = rangos(base)
for _ in range(N):
    # perturbacion multiplicativa uniforme de +-50% sobre cada peso, renormalizada
    w = [b * random.uniform(0.5, 1.5) for b in BASE]
    s = puntuar(w)
    r = rangos(s)
    for i in range(n):
        pos[i].append(r[i])
    for i in top(s, 5):
        en_top5[i] += 1
    rhos.append(spearman(base, s))
    taus.append(kendall(base, s))
    t5.append(len(top5b & top(s, 5)))


def pct(v, p):
    v = sorted(v)
    return v[int(p / 100 * (len(v) - 1))]


res["montecarlo"] = {
    "N": N,
    "perturbacion": "x U(0.5, 1.5) por peso, renormalizado",
    "kendall_mediana": round(pct(taus, 50), 3),
    "kendall_p5": round(pct(taus, 5), 3),
    "spearman_mediana": round(pct(rhos, 50), 3),
    "spearman_p5": round(pct(rhos, 5), 3),
    "top5_media": round(sum(t5) / N, 2),
    "top5_p5": pct(t5, 5),
    "por_aerolinea": [],
}
print("\n3. Monte Carlo ({} muestras): tau mediana {:.3f} (p5 {:.3f}), rho mediana {:.3f} (p5 {:.3f}), top5 compartido medio {:.2f}".format(
    N, pct(taus, 50), pct(taus, 5), pct(rhos, 50), pct(rhos, 5), sum(t5) / N))
for i in sorted(range(n), key=lambda i: rb[i]):
    fila = {"grupo": datos[i]["grupo"], "rango_base": int(rb[i]),
            "p5": int(pct(pos[i], 5)), "p95": int(pct(pos[i], 95)),
            "prob_top5": round(en_top5[i] / N, 3)}
    res["montecarlo"]["por_aerolinea"].append(fila)
    print("  {:>2}. {:<26} rango p5-p95 {:>2}-{:<2}  P(top5)={:.2f}".format(
        fila["rango_base"], fila["grupo"], fila["p5"], fila["p95"], fila["prob_top5"]))

# --------------------------------------------------- 4. sesgo por dato faltante
# El componente de reemplazo usa pct_ventana = aeronaves en ventana / flota TOTAL,
# asi que las aeronaves sin edad cuentan como "fuera de ventana". Se recalcula
# sobre la flota con edad, y si un grupo no tiene ninguna, el componente se
# excluye y se renormalizan los demas pesos en vez de imputar 0.
vent = {r["grupo"]: r for r in csv.DictReader(open(sys.argv[3], encoding="utf-8"))}
vida = {f["grupo"]: (float(f["vida_remanente"]) if f["vida_remanente"] else None) for f in filas}


def reemplazo(pct, v):
    return round(100 * (0.65 * min(pct, 1.0) + 0.35 * (1 - min((10 if v is None else v) / 10, 1.0))), 1)


dif_rep = 0.0
for d in datos:
    r = vent[d["grupo"]]
    tot, ven, con = int(r["total"]), int(r["ventana"]), int(r["con_vida"])
    original = reemplazo(ven / tot, vida[d["grupo"]])
    dif_rep = max(dif_rep, abs(original - d["c"][1]))
    d["cobertura_edad"] = con / tot
    if con == 0:
        d["c_corr"] = None
    else:
        d["c_corr"] = reemplazo(ven / con, vida[d["grupo"]])
print("\n4. el componente original se reproduce con diferencia maxima {:.2f}".format(dif_rep))

ex = []
for d in datos:
    w = list(BASE)
    c = list(d["c"])
    if d["c_corr"] is None:
        w[1] = 0
    else:
        c[1] = d["c_corr"]
    ex.append(sum(x * y for x, y in zip(c, w)) / sum(w))
for d in datos:
    d["sin_edad"] = d["cobertura_edad"] < 1.0
rx = rangos(ex)
sin = [i for i in range(n) if datos[i]["sin_edad"]]
mov = []
for i in range(n):
    mov.append({"grupo": datos[i]["grupo"], "sin_edad": datos[i]["sin_edad"],
                "cobertura_edad": round(datos[i]["cobertura_edad"], 2),
                "reemplazo_base": datos[i]["c"][1], "reemplazo_corr": datos[i]["c_corr"],
                "score_base": round(base[i], 1), "score_excl": round(ex[i], 1),
                "rango_base": int(rb[i]), "rango_excl": int(rx[i]),
                "delta": int(rb[i] - rx[i])})
res["faltantes"] = {
    "n_sin_edad": len(sin),
    "flota_sin_edad_grupos": sum(datos[i]["flota"] for i in sin),
    "kendall": round(kendall(base, ex), 3),
    "spearman": round(spearman(base, ex), 3),
    "top5_compartido": len(top5b & top(ex, 5)),
    "suben_sin_edad": sum(1 for i in sin if rb[i] > rx[i]),
    "subida_media_sin_edad": round(sum(rb[i] - rx[i] for i in sin) / len(sin), 2),
    "movimientos": sorted(mov, key=lambda m: m["rango_excl"]),
}
print("   corregido: {} grupos con edad incompleta; tau={:.3f}, rho={:.3f}, top5 compartido {}/5; suben {} de {}, subida media {:.2f} puestos".format(
    len(sin), res["faltantes"]["kendall"], res["faltantes"]["spearman"], res["faltantes"]["top5_compartido"],
    res["faltantes"]["suben_sin_edad"], len(sin), res["faltantes"]["subida_media_sin_edad"]))
for m in res["faltantes"]["movimientos"]:
    if m["sin_edad"] or m["delta"]:
        print("  {:>2} <- {:>2}  {:<24} cob={:<4} reempl {:>5} -> {:<5}  score {:>5} -> {:>5}".format(
            m["rango_excl"], m["rango_base"], m["grupo"], m["cobertura_edad"],
            m["reemplazo_base"], str(m["reemplazo_corr"]), m["score_base"], m["score_excl"]))
# efecto techo/suelo del componente de reemplazo
ceros = [d["grupo"] for d in datos if d["c"][1] == 0]
res["suelo_reemplazo"] = {"n": len(ceros), "grupos": ceros}
print("\n   componente de reemplazo exactamente 0 en {} de {} grupos".format(len(ceros), n))

# segmentos
seg = {}
for d in datos:
    seg[d["segmento"]] = seg.get(d["segmento"], 0) + 1
res["segmentos"] = seg
print("\nsegmentos:", seg)

json.dump(res, open(salida, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\nescrito", salida)
