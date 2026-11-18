"""Carga a BigQuery las descargas manuales de FAA (talleres Part 145 y operadores).

FAA publica estos listados en tableros de Tableau (AVInfo) sin API, asi que se
descargan a mano con "Download Crosstab" y este script los normaliza y los carga.
Cada descarga se conserva con su fecha (`fecha_descarga`) para poder detectar
cambios entre una y otra: un rating nuevo, un acuerdo bilateral nuevo o un
cambio de Accountable Manager son senales comerciales.

Formato de las descargas (verificado el 2026-11-13):
  - CSV en UTF-16 separado por tabuladores.
  - Fechas dia/mes/anio ("9/11/2026" es 9 de noviembre).
  - Miles con punto en los conteos de personal ("2.158" son 2158 empleados).
  - La parte 125 se bajo como .xlsx.
  - La parte 129 completa se bajo el 2026-11-17 solo con "Show Aircraft"
    (Aircraft (2).csv, 512 certificados); se carga con la fecha 2026-11-13
    junto al resto. Los operadores que solo estan ahi se toman de esa vista.
  - La vista de talleres trae una ultima columna sin nombre que repite Agency Email.

Uso:
  python -I cargar_faa.py <carpeta_descargas> <fecha_descarga AAAA-MM-DD> <carpeta_salida> [--cargar]

Sin --cargar solo escribe los NDJSON y un resumen; con --cargar ademas sube los
originales a gs://bucket-edgar/faa/<fecha>/ y reemplaza en BigQuery las filas
de esa fecha (las de otras fechas no se tocan).
"""

import csv
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import date

PROYECTO = "mach5-gemini-project"
DATASET = "dataset_prospeccion"
BUCKET = "bucket-edgar"

ARCHIVO_TALLERES = "Repair Station Contacts with Ratings (Download) (1).csv"
# Operadores y sus aeronaves: un archivo por parte, con nombres que pone el
# navegador. Se reconocen por contenido (columna "14 CFR Part"), no por nombre.
PREFIJO_OPERADORES = "Air Operators"
PREFIJO_AERONAVES = "Aircraft"

RATINGS = ["Accessory", "Airframe", "Instrument", "Limited", "Powerplant", "Propeller", "Radio"]

# Certificados de prueba que FAA deja en el listado publico
NOMBRES_DE_PRUEBA = re.compile(r"\bTEST CERTIFICATE\b", re.I)


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def leer_tsv_utf16(ruta):
    with open(ruta, "rb") as f:
        texto = f.read().decode("utf-16")
    filas = list(csv.reader(io.StringIO(texto), delimiter="\t"))
    cab = [c.strip() for c in filas[0]]
    return [dict(zip(cab, f)) for f in filas[1:] if any(c.strip() for c in f)]


def leer_xlsx(ruta):
    """Lector minimo de la primera hoja; evita depender de openpyxl."""
    z = zipfile.ZipFile(ruta)
    compartidas = []
    if "xl/sharedStrings.xml" in z.namelist():
        xml = z.read("xl/sharedStrings.xml").decode("utf-8")
        for si in re.findall(r"<si>(.*?)</si>", xml, re.S):
            compartidas.append(_xml_texto("".join(re.findall(r"<t[^>]*>([^<]*)</t>", si))))
    xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    filas = []
    for fila in re.findall(r"<row [^>]*>(.*?)</row>", xml, re.S):
        celdas = {}
        for col, attrs, interior in re.findall(
                r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', fila, re.S):
            v = re.search(r"<v>([^<]*)</v>", interior or "")
            valor = v.group(1) if v else ""
            if 't="s"' in attrs and valor != "":
                valor = compartidas[int(valor)]
            celdas[col] = _xml_texto(valor)
        filas.append(celdas)
    cab = filas[0]
    columnas = sorted(set(k for f in filas for k in f), key=lambda c: (len(c), c))
    nombres = [cab.get(c, "").strip() for c in columnas]
    return [{n: f.get(c, "") for n, c in zip(nombres, columnas)} for f in filas[1:]]


def _xml_texto(s):
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'"))


def leer(ruta):
    return leer_xlsx(ruta) if ruta.lower().endswith(".xlsx") else leer_tsv_utf16(ruta)


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------

def texto(v):
    if v is None:
        return None
    v = " ".join(str(v).split())
    return None if v == "" or v.upper() in ("NULL", "NONE", "-") else v


def entero(v):
    """'2.158' -> 2158. Todos los conteos de FAA son enteros, asi que el punto
    solo puede ser separador de miles."""
    v = texto(v)
    if v is None:
        return None
    v = re.sub(r"[.,\s]", "", v)
    return int(v) if v.isdigit() else None


def fecha_dma(v):
    v = texto(v)
    if v is None:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", v)
    if not m:
        return None
    d, mes, a = map(int, m.groups())
    return date(a, mes, d).isoformat()


def telefono(v):
    v = texto(v)
    if v is None:
        return None
    d = re.sub(r"\D", "", v)
    return d or None


def correo(v):
    v = texto(v)
    return v.lower() if v and "@" in v else None


def rating(v):
    """'Class 1, Class 3, Limited' -> ([1, 3], True). Propeller viene sin la
    palabra Class ('1, 2, Limited')."""
    v = texto(v)
    if v is None:
        return [], False
    clases = sorted({int(n) for n in re.findall(r"(?:Class\s*)?\b(\d)\b", v)})
    return clases, bool(re.search(r"\bLimited\b", v, re.I))


def lista(v):
    v = texto(v)
    return sorted({p.strip() for p in v.split(",") if p.strip()}) if v else []


# ---------------------------------------------------------------------------
# Talleres Part 145
# ---------------------------------------------------------------------------

def normalizar_talleres(filas, fecha_descarga):
    salida = []
    for r in filas:
        dba = texto(r.get("DBA"))
        if dba:
            dba = re.sub(r"^DBA\s+", "", dba, flags=re.I)
        t = {
            "fecha_descarga": fecha_descarga,
            "cert_no": texto(r["Cert_No"]),
            "dsgn_code": texto(r["DSGN_CODE"]),
            "agency_name": texto(r["Agency Name"]),
            "dba": dba,
            "address_1": texto(r.get("Address Line 1")),
            "address_2": texto(r.get("Address Line 2")),
            "address_3": texto(r.get("Address Line 3")),
            "city": texto(r.get("City")),
            "state": texto(r.get("State/Province")),
            "country": texto(r.get("Country")),
            "postal_code": texto(r.get("Postal Code")),
            # Los cinco primeros digitos son el ZIP de EE. UU.; sirven para
            # geocodificar sin coordenadas
            "zip5": (re.match(r"^(\d{5})", texto(r.get("Postal Code")) or "") or [None, None])[1]
                    if texto(r.get("Country")) == "UNITED STATES" else None,
            "agency_phone": telefono(r.get("Agency Phone Number")),
            "agency_email": correo(r.get("Agency Email")),
            "am_name": texto(r.get("Accountable Manager")),
            "am_phone": telefono(r.get("Accountable Manager Phone Number")),
            "am_email": correo(r.get("Accountable Manager Email")),
            "liaison_name": texto(r.get("Liaison")),
            "liaison_phone": telefono(r.get("Liaison Phone Number")),
            "liaison_email": correo(r.get("Liaison Email")),
            "bilateral_agreements": lista(r.get("Bilateral Agreements")),
            "faa_update_date": fecha_dma(r.get("Update_Date")),
        }
        for nombre in RATINGS:
            crudo = r.get(f"Rating - {nombre}")
            clave = nombre.lower()
            t[f"rating_{clave}"] = texto(crudo)
            if nombre == "Limited":
                # El rating Limited no tiene clases: enumera especialidades
                t["limited_especialidades"] = lista(crudo)
            else:
                clases, limitado = rating(crudo)
                t[f"{clave}_clases"] = clases
                t[f"{clave}_limited"] = limitado
        salida.append(t)
    return salida


# ---------------------------------------------------------------------------
# Operadores
# ---------------------------------------------------------------------------

def normalizar_operadores(filas, fecha_descarga):
    salida = []
    for r in filas:
        nombre = texto(r.get("Air_Operator_Name"))
        if not nombre or NOMBRES_DE_PRUEBA.search(nombre):
            continue
        parte = texto(r.get("14 CFR Part"))
        salida.append({
            "fecha_descarga": fecha_descarga,
            "certificate_number": texto(r.get("Certificate_Number")),
            "dsgn_code": texto(r.get("DSGN_CODE")),
            "operator_name": nombre,
            "cfr_part": parte,
            "es_parte_121": bool(parte and "121" in parte),
            "cert_issue_date": fecha_dma(r.get("Cert_Issue_Date")),
            "ceo_name": texto(r.get("CEO Name")),
            "director_operations": texto(r.get("Director of Operations")),
            "director_maintenance": texto(r.get("Director of Maintenance")),
            "chief_pilot": texto(r.get("Chief Pilot")),
            "address_1": texto(r.get("Addr1")),
            "address_2": texto(r.get("Addr2")),
            "address_3": texto(r.get("Addr3")),
            "city": texto(r.get("City")),
            "state": texto(r.get("St")),
            "country": texto(r.get("Country")),
            "postal_code": texto(r.get("Zip/Postal Code")),
            "phone": telefono(r.get("Phone")) or telefono(r.get("Foreign_Phone")),
            "pic_captains": entero(r.get("PIC Captains")),
            "inspectors": entero(r.get("Inspectors")),
            "designated_inspectors": entero(r.get("Designated Inspectors")),
            "noncertificated_mechanics": entero(r.get("NonCertificated Mechanics")),
            "certificated_mechanics": entero(r.get("Certificated Mechanics")),
            "repairmen": entero(r.get("Repairmen")),
            "total_employees": entero(r.get("Total Number of Employees")),
        })
    return salida


def normalizar_modelos(filas, fecha_descarga):
    """Una fila por operador y modelo. FAA no da cantidades ni matriculas: esto
    dice que tipos opera cada uno, no cuantos."""
    salida, previo = [], {}
    for r in filas:
        # En el .xlsx las filas repetidas pueden venir con la identidad vacia
        for k in ("Air_Operator_Name", "14 CFR Part", "DSGN_CODE", "Certificate_Number"):
            if texto(r.get(k)):
                previo[k] = r[k]
            else:
                r[k] = previo.get(k, "")
        nombre = texto(r.get("Air_Operator_Name"))
        # La columna del modelo sale sin nombre en la descarga
        modelo = texto(r.get("") or r.get("Model") or "")
        if not nombre or NOMBRES_DE_PRUEBA.search(nombre) or not modelo:
            continue
        salida.append({
            "fecha_descarga": fecha_descarga,
            "certificate_number": texto(r.get("Certificate_Number")),
            "operator_name": nombre,
            "cfr_part": texto(r.get("14 CFR Part")),
            "aircraft_model_faa": modelo,
        })
    # Duplicados exactos (un modelo listado dos veces para el mismo certificado)
    vistos, unicos = set(), []
    for m in salida:
        k = (m["certificate_number"] or m["operator_name"], m["aircraft_model_faa"])
        if k not in vistos:
            vistos.add(k)
            unicos.append(m)
    return unicos


# ---------------------------------------------------------------------------
# Esquemas de BigQuery
# ---------------------------------------------------------------------------

def _c(nombre, tipo="STRING", modo="NULLABLE"):
    return {"name": nombre, "type": tipo, "mode": modo}


ESQUEMA_TALLERES = (
    [_c("fecha_descarga", "DATE", "REQUIRED"), _c("cert_no", modo="REQUIRED"), _c("dsgn_code"),
     _c("agency_name"), _c("dba"), _c("address_1"), _c("address_2"), _c("address_3"),
     _c("city"), _c("state"), _c("country"), _c("postal_code"), _c("zip5"),
     _c("agency_phone"), _c("agency_email"), _c("am_name"), _c("am_phone"), _c("am_email"),
     _c("liaison_name"), _c("liaison_phone"), _c("liaison_email"),
     _c("bilateral_agreements", modo="REPEATED"), _c("faa_update_date", "DATE")]
    + [c for n in RATINGS for c in (
        [_c(f"rating_{n.lower()}"), _c("limited_especialidades", modo="REPEATED")]
        if n == "Limited" else
        [_c(f"rating_{n.lower()}"), _c(f"{n.lower()}_clases", "INT64", "REPEATED"),
         _c(f"{n.lower()}_limited", "BOOL")])]
)

ESQUEMA_OPERADORES = [
    _c("fecha_descarga", "DATE", "REQUIRED"), _c("certificate_number"),
    _c("dsgn_code"), _c("operator_name"), _c("cfr_part"), _c("es_parte_121", "BOOL"),
    _c("cert_issue_date", "DATE"), _c("ceo_name"), _c("director_operations"),
    _c("director_maintenance"), _c("chief_pilot"), _c("address_1"), _c("address_2"),
    _c("address_3"), _c("city"), _c("state"), _c("country"), _c("postal_code"), _c("phone"),
    _c("pic_captains", "INT64"), _c("inspectors", "INT64"), _c("designated_inspectors", "INT64"),
    _c("noncertificated_mechanics", "INT64"), _c("certificated_mechanics", "INT64"),
    _c("repairmen", "INT64"), _c("total_employees", "INT64"),
]

ESQUEMA_MODELOS = [
    _c("fecha_descarga", "DATE", "REQUIRED"), _c("certificate_number"), _c("operator_name"),
    _c("cfr_part"), _c("aircraft_model_faa"),
]

TABLAS = {
    "faa_talleres": ESQUEMA_TALLERES,
    "faa_operadores": ESQUEMA_OPERADORES,
    "faa_operador_modelos": ESQUEMA_MODELOS,
}


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def _bq(*args, entrada=None):
    cmd = (["cmd", "/c", "bq"] if os.name == "nt" else ["bq"]) + list(args)
    r = subprocess.run(cmd, input=entrada, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"bq {' '.join(args[:2])} fallo:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def _gsutil(*args):
    cmd = (["cmd", "/c", "gsutil"] if os.name == "nt" else ["gsutil"]) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gsutil fallo:\n{r.stdout}\n{r.stderr}")


def cargar(tabla, ruta_ndjson, ruta_esquema, fecha_descarga):
    destino = f"{PROYECTO}:{DATASET}.{tabla}"
    existe = subprocess.run(
        (["cmd", "/c", "bq"] if os.name == "nt" else ["bq"]) + ["show", destino],
        capture_output=True).returncode == 0
    if existe:
        # Reemplaza solo la descarga de esta fecha: recargar el mismo dia no
        # duplica, y las descargas anteriores quedan para comparar
        _bq("query", "--use_legacy_sql=false", "--format=none",
            f"DELETE FROM `{PROYECTO}.{DATASET}.{tabla}` "
            f"WHERE fecha_descarga = DATE '{fecha_descarga}'")
    _bq("load", "--source_format=NEWLINE_DELIMITED_JSON", "--noreplace",
        destino, ruta_ndjson, ruta_esquema)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    carpeta, fecha_descarga, salida = sys.argv[1], sys.argv[2], sys.argv[3]
    hacer_carga = "--cargar" in sys.argv[4:]
    date.fromisoformat(fecha_descarga)
    os.makedirs(salida, exist_ok=True)

    archivos = sorted(os.listdir(carpeta))
    talleres = normalizar_talleres(leer(os.path.join(carpeta, ARCHIVO_TALLERES)), fecha_descarga)

    operadores_crudos, modelos_crudos = [], []
    for a in archivos:
        ruta = os.path.join(carpeta, a)
        if a.startswith(PREFIJO_OPERADORES) and a.lower().endswith((".csv", ".xlsx")):
            operadores_crudos.extend(leer(ruta))
        elif a.startswith(PREFIJO_AERONAVES) and a.lower().endswith((".csv", ".xlsx")):
            modelos_crudos.extend(leer(ruta))
    operadores = normalizar_operadores(operadores_crudos, fecha_descarga)
    # normalizar_modelos rellena la identidad del operador en las filas crudas
    modelos = normalizar_modelos(modelos_crudos, fecha_descarga)
    # La vista "Show Aircraft" trae nombre, parte, designador, certificado y
    # fecha del certificado. Los operadores que solo aparecen ahi (la parte 129
    # se bajo completa solo con aeronaves el 2026-11-17) entran sin directivos
    # ni conteos; FAA tampoco publica directivos de operadores extranjeros. Van
    # despues de la lista de operadores para que la deduplicacion de abajo
    # conserve la fila completa cuando existe.
    operadores += normalizar_operadores(modelos_crudos, fecha_descarga)

    # Un mismo certificado bajado dos veces (p. ej. .csv y .xlsx de la misma
    # parte) se cuenta una vez. Algunos operadores 125 vienen sin numero de
    # certificado: para ellos la clave es designador y nombre.
    vistos, unicos = set(), []
    for o in operadores:
        k = o["certificate_number"] or (o["dsgn_code"], o["operator_name"])
        if k not in vistos:
            vistos.add(k)
            unicos.append(o)
    operadores = unicos

    datos = {"faa_talleres": talleres, "faa_operadores": operadores,
             "faa_operador_modelos": modelos}
    for tabla, filas in datos.items():
        with open(os.path.join(salida, f"{tabla}.ndjson"), "w", encoding="utf-8") as f:
            for fila in filas:
                f.write(json.dumps(fila, ensure_ascii=False) + "\n")
        with open(os.path.join(salida, f"{tabla}.schema.json"), "w", encoding="utf-8") as f:
            json.dump(TABLAS[tabla], f, indent=1)

    resumen = {
        "fecha_descarga": fecha_descarga,
        "talleres": len(talleres),
        "talleres_eeuu": sum(1 for t in talleres if t["country"] == "UNITED STATES"),
        "cert_no_repetidos": sorted({t["cert_no"] for t in talleres
                                     if sum(1 for x in talleres if x["cert_no"] == t["cert_no"]) > 1}),
        "operadores": len(operadores),
        "operadores_por_parte": {p: sum(1 for o in operadores if o["cfr_part"] == p)
                                 for p in sorted({o["cfr_part"] for o in operadores})},
        "modelos_operador": len(modelos),
    }
    print(json.dumps(resumen, ensure_ascii=False, indent=1))

    if hacer_carga:
        for a in archivos:
            _gsutil("cp", os.path.join(carpeta, a), f"gs://{BUCKET}/faa/{fecha_descarga}/{a}")
        print(f"originales en gs://{BUCKET}/faa/{fecha_descarga}/")
        for tabla in datos:
            cargar(tabla, os.path.join(salida, f"{tabla}.ndjson"),
                   os.path.join(salida, f"{tabla}.schema.json"), fecha_descarga)
            print(f"cargada {tabla}")


if __name__ == "__main__":
    main()
