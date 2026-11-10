# -*- coding: utf-8 -*-
"""Figuras del articulo IEEE. Ancho de una columna IEEE: 3.5 in; doble: 7.16 in.

Uso: python -I figuras.py <score_full.csv> <sensibilidad.json> <dir_salida>
"""
import csv
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ruta_csv, ruta_json, salida = sys.argv[1], sys.argv[2], sys.argv[3]

plt.rcParams.update({
    "font.family": "Times New Roman", "font.size": 8, "axes.titlesize": 8,
    "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "axes.edgecolor": "#8A8A85", "axes.linewidth": 0.6,
    "xtick.color": "#4A4A46", "ytick.color": "#22221F", "text.color": "#22221F",
    "axes.labelcolor": "#22221F", "savefig.dpi": 300,
})
TINTA, TINTA2, REJILLA = "#22221F", "#5A5A55", "#E4E3DE"
# Paleta categorica de referencia, validada: slots 1, 2, 3, 4 y 7.
COL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#6250d6"]
NOM = ["Leasing (35 %)", "Reemplazo (30 %)", "Complejidad (15 %)", "Transición (12 %)", "Apalancamiento (8 %)"]
COMP = ["score_leasing", "score_reemplazo", "score_complejidad", "score_transicion", "score_apalancamiento"]
PESO = [0.35, 0.30, 0.15, 0.12, 0.08]

filas = list(csv.DictReader(open(ruta_csv, encoding="utf-8")))
sens = json.load(open(ruta_json, encoding="utf-8"))


# ------------------------------------------------------------ Fig. 1 arquitectura
def fig_arquitectura():
    fig, ax = plt.subplots(figsize=(7.16, 2.35))
    ax.set_xlim(0, 100); ax.set_ylim(0, 32); ax.axis("off")

    def caja(x, y, w, h, titulo, cuerpo, acento=False):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=0.8",
                                    fc="#EAF1FB" if acento else "#F6F6F3",
                                    ec="#2a78d6" if acento else "#9A9A94", lw=0.8))
        ax.text(x + w / 2, y + h - 2.2, titulo, ha="center", va="top", fontsize=7.5, weight="bold", color=TINTA)
        ax.text(x + w / 2, y + h - 6.0, cuerpo, ha="center", va="top", fontsize=6.3, color=TINTA2, linespacing=1.25)

    def flecha(x1, y1, x2, y2, txt=None):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", lw=0.8, color="#5A5A55", mutation_scale=7))
        if txt:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 1.0, txt, ha="center", va="bottom", fontsize=5.8, color=TINTA2)

    y, h = 8, 20
    caja(0.5, y, 14.5, h, "Fuentes", "SEC EDGAR\n10-K · 20-F\n8-K · 6-K\n\nBolsas chinas\nCSRC · HKEX\n\nakshare")
    caja(18.5, y, 16.5, h, "Adquisición", "Cloud Run\nedgartools\n\nDescarga anual\ny monitoreo\ndiario de eventos")
    caja(38.5, y, 17.5, h, "Extracción", "Aislamiento de\nItem 2 / Item 4\n\nGemini vía\nVertex AI → JSON\n\nHechos XBRL", acento=True)
    caja(59.5, y, 17, h, "Almacén", "BigQuery\n\nNormalización e\nidentidad canónica\n\nEsquema estrella\n+ 7 vistas")
    caja(80, y, 19.5, h, "Consumo", "Power BI\n9 páginas\n57 medidas DAX\n\nPuntuación\nexplicable de\nprospectos")
    flecha(15.3, y + h / 2, 18.2, y + h / 2)
    flecha(35.3, y + h / 2, 38.2, y + h / 2)
    flecha(56.3, y + h / 2, 59.2, y + h / 2)
    flecha(76.8, y + h / 2, 79.7, y + h / 2)

    ax.add_patch(FancyBboxPatch((0.5, 1.0), 99, 4.2, boxstyle="round,pad=0.2,rounding_size=0.6",
                                fc="white", ec="#9A9A94", lw=0.6, ls=(0, (3, 2))))
    ax.text(50, 3.1, "Cloud Workflows · corte trimestral · ocho fases con reintentos · Cloud Storage como evidencia intermedia",
            ha="center", va="center", fontsize=6.3, color=TINTA2)
    fig.savefig(os.path.join(salida, "fig1_arquitectura.png"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


# --------------------------------------------------- Fig. 2 composicion del score
def fig_composicion():
    datos = sorted(filas, key=lambda f: float(f["prospect_score"]))
    fig, ax = plt.subplots(figsize=(3.5, 4.6))
    ys = range(len(datos))
    izq = [0.0] * len(datos)
    for k, (c, col, nom) in enumerate(zip(COMP, COL, NOM)):
        v = [float(f[c]) * PESO[k] for f in datos]
        ax.barh(ys, v, left=izq, color=col, height=0.72, edgecolor="white", linewidth=0.7, label=nom, zorder=3)
        izq = [a + b for a, b in zip(izq, v)]
    for y, f in zip(ys, datos):
        ax.text(float(f["prospect_score"]) + 0.6, y, f"{float(f['prospect_score']):.1f}",
                va="center", ha="left", fontsize=6.3, color=TINTA2)
    ax.set_yticks(list(ys)); ax.set_yticklabels([f["grupo"].replace(" Airlines Group", "").replace(" Airlines", "").replace(" Air Lines", "").replace(" Airways", "") for f in datos])
    ax.set_xlim(0, 55); ax.set_xlabel("Contribución ponderada al prospect score (puntos)")
    ax.xaxis.grid(True, color=REJILLA, lw=0.5, zorder=0); ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="lower center", bbox_to_anchor=(0.42, 1.0), ncol=2, frameon=False, handlelength=1.1,
              columnspacing=1.0, handletextpad=0.4)
    fig.savefig(os.path.join(salida, "fig2_composicion.png"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ------------------------------------------------------ Fig. 3 intervalos de rango
def fig_montecarlo():
    pa = sens["montecarlo"]["por_aerolinea"]
    fig, ax = plt.subplots(figsize=(3.5, 4.3))
    n = len(pa)
    for i, r in enumerate(pa):
        y = n - 1 - i
        ax.plot([r["p5"], r["p95"]], [y, y], color="#86b6ef", lw=3.2, solid_capstyle="round", zorder=2)
        ax.plot([r["rango_base"]], [y], "o", ms=4.2, color="#2a78d6", mec="white", mew=0.8, zorder=3)
        if r["prob_top5"] >= 0.1:
            ax.text(26.6, y, f"{r['prob_top5']:.2f}", va="center", ha="left", fontsize=6.3, color=TINTA2)
    ax.text(26.6, n - 0.2, "P(top 5)", va="bottom", ha="left", fontsize=6.3, color=TINTA2, style="italic")
    ax.axvspan(0.5, 5.5, color="#F1F0EC", zorder=0)
    ax.set_yticks(range(n))
    ax.set_yticklabels([r["grupo"].replace(" Airlines Group", "").replace(" Airlines", "").replace(" Air Lines", "").replace(" Airways", "") for r in reversed(pa)])
    ax.set_xlim(0.5, 25.5); ax.set_xticks([1, 5, 10, 15, 20, 25])
    ax.set_xlabel("Posición en el ranking (1 = mejor prospecto)")
    ax.xaxis.grid(True, color=REJILLA, lw=0.5, zorder=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.savefig(os.path.join(salida, "fig3_montecarlo.png"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


fig_arquitectura()
fig_composicion()
fig_montecarlo()
print("figuras escritas en", salida)
