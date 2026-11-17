"""Descarga tablas de DOT Form 41 desde BTS TranStats.

TranStats no tiene API: cada tabla se baja con un formulario ASP.NET
(DL_SelectFields.aspx) que devuelve un .zip con un CSV. Este script reproduce
ese formulario: lee la pagina, toma los campos ocultos (__VIEWSTATE y
compania) y los nombres de todas las columnas, y envia la descarga de un anio
con todos sus periodos.

Tablas (codigo gnoyr_VQ de TranStats, base "Air Carrier Financial Reports"):
  GEH  Schedule B-43 Inventory      una fila por avion; anual
  FMK  Schedule P-5.2               gasto de mantenimiento por tipo de avion; trimestral
  GDF  Schedule P-10                empleados por categoria; anual
  FKM  Schedule B-1                 balance, aerolineas >= 20 M USD; trimestral
  FMI  Schedule P-1.2               resultados, aerolineas >= 20 M USD; trimestral

Uso:
  python -I descargar_form41.py <carpeta_salida> [TABLA:ANIO ...]

Sin pares TABLA:ANIO baja el conjunto por defecto (DEFECTO). Un archivo ya
descargado no se vuelve a pedir.
"""

import html
import http.cookiejar
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://www.transtats.bts.gov/DL_SelectFields.aspx"
SUFIJO = "&QO_fu146_anzr=Nv4%20Pn44vr4%20Sv0n0pvny"

TABLAS = {
    "B43": "GEH",
    "P52": "FMK",
    "P10": "GDF",
    "B1": "FKM",
    "P12": "FMI",
}

DEFECTO = (
    [f"B43:{a}" for a in (2023, 2024, 2025)]
    + [f"P52:{a}" for a in (2024, 2025, 2026)]
    + [f"P10:{a}" for a in (2023, 2024, 2025)]
    + [f"B1:{a}" for a in (2024, 2025, 2026)]
    + [f"P12:{a}" for a in (2024, 2025, 2026)]
)

# Casillas del formulario que son opciones, no columnas
CONTROLES = {"chkAllVars", "chkAllGroups", "chkDownloadZip", "chkshowNull",
             "chkMergeSub", "chkDocument", "chkTermDef"}


def _abridor():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (form41-descarga)")]
    return op


def descargar(op, tabla, anio, destino):
    url = f"{BASE}?gnoyr_VQ={TABLAS[tabla]}{SUFIJO}"
    pagina = op.open(url, timeout=120).read().decode("utf-8", "replace")

    campos = {}
    for nombre in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        m = re.search(rf'name="{nombre}"[^>]*value="([^"]*)"', pagina) \
            or re.search(rf'value="([^"]*)"[^>]*name="{nombre}"', pagina)
        if not m:
            raise RuntimeError(f"{tabla}: no se encontro {nombre}; cambio la pagina?")
        campos[nombre] = html.unescape(m.group(1))

    columnas = [c for c in re.findall(r'<input[^>]*type="checkbox"[^>]*name="([^"]+)"', pagina)
                if c not in CONTROLES]
    columnas += [c for c in re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="checkbox"', pagina)
                 if c not in CONTROLES and c not in columnas]
    if not columnas:
        raise RuntimeError(f"{tabla}: la pagina no lista columnas")

    anios = re.findall(r'<option[^>]*value="(\d{4})"', pagina)
    if str(anio) not in anios:
        raise RuntimeError(f"{tabla}: el anio {anio} no esta disponible (hay {min(anios)}-{max(anios)})")

    campos.update({
        "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
        "txtSearch": "", "cboGeography": "All", "cboYear": str(anio), "cboPeriod": "All",
        "chkDownloadZip": "on", "btnDownload": "Download",
    })
    for c in columnas:
        campos[c] = "on"

    datos = urllib.parse.urlencode(campos).encode()
    r = op.open(urllib.request.Request(url, data=datos), timeout=600)
    contenido = r.read()
    if not contenido.startswith(b"PK"):
        raise RuntimeError(f"{tabla} {anio}: la respuesta no es un zip "
                           f"({r.headers.get('Content-Type')}, {len(contenido)} bytes)")
    with open(destino, "wb") as f:
        f.write(contenido)
    return len(columnas), len(contenido)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    salida = sys.argv[1]
    pedidos = sys.argv[2:] or DEFECTO
    os.makedirs(salida, exist_ok=True)
    op = _abridor()
    for p in pedidos:
        tabla, anio = p.split(":")
        destino = os.path.join(salida, f"form41_{tabla}_{anio}.zip")
        if os.path.exists(destino):
            print(f"{p}: ya existe, se omite")
            continue
        try:
            ncol, nbytes = descargar(op, tabla, int(anio), destino)
            print(f"{p}: {ncol} columnas, {nbytes / 1e6:.1f} MB")
        except Exception as e:
            print(f"{p}: ERROR {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()
