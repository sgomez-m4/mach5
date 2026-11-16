"""Reglas de identidad, desgloses alternativos y control de totales de la flota.

Funciones puras (sin GCS ni BigQuery) para poder probarlas sobre copias locales
de las extracciones. main.py las aplica antes de cargar a BigQuery.

Contexto: la corrida del 13-nov-2026 reemplazo un estado conciliado por otro con
seis grupos mal contados, y nada en el pipeline lo detecto:
  - Gemini etiqueto las filas de HK Express (dentro del informe de Cathay) como
    "Juneyao Airlines" y las de 9 Air (dentro del informe de Juneyao) como
    "China Express Airlines". El prompt de extraccion china solo enumera las ocho
    aerolineas cotizadas, y el modelo forzo las filiales a uno de esos nombres.
    El pipeline confiaba en el campo `airline` de cada fila sin contrastarlo con
    el documento del que salia.
  - Spring publica su flota dos veces (por propiedad y por modelo) y ambas
    lecturas se sumaban.
"""

import re
import unicodedata


# ---------------------------------------------------------------------------
# Identidad
# ---------------------------------------------------------------------------

def clave_aerolinea(nombre):
    """Normaliza un nombre de aerolinea para comparar: sin acentos, sin puntuacion,
    en minusculas. 'Aeroméxico Connect' y 'aeromexico connect' dan la misma clave."""
    if not nombre:
        return ""
    txt = unicodedata.normalize("NFKD", str(nombre))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return "".join(c for c in txt.lower() if c.isalnum())


# Tokens del nombre de archivo que nunca identifican a una aerolinea.
_TOKEN_NO_AEROLINEA = re.compile(
    r"^(\d{4}|\d{8}|10k|20f|item\d|flota|filtrado|filtered|annual|report|hk|json)$", re.I)


def grupo_documento(nombre_blob, indice):
    """group_id de la aerolinea duena del filing, deducido del nombre del archivo.

    '00293_HK_CathayPacificAirways_20260409_annual_report.json' -> CPACIFIC (por el
    codigo bursatil o por el nombre); 'aal-2026-10k_Item2_filtrado.json' -> AAL.
    Devuelve None si ningun token resuelve contra el catalogo; en ese caso no se
    puede contrastar la identidad de las filas y se informa como advertencia.
    """
    base = nombre_blob.rsplit("/", 1)[-1]
    base = re.sub(r"\.json$", "", base, flags=re.I)
    for token in re.split(r"[_\-\s.]+", base):
        if not token or _TOKEN_NO_AEROLINEA.match(token):
            continue
        entidad = indice.get(clave_aerolinea(token))
        if entidad is not None:
            return entidad["group_id"]
    return None


def entidad_por_fleet_id(fleet_id, indice):
    """Entidad que nombra el prefijo del fleet_id ('hong_kong_express_2025_...').

    Gemini arma el fleet_id leyendo el encabezado de la tabla, y en la corrida del
    13-nov ese prefijo seguia siendo correcto aunque el campo `airline` no lo era.
    """
    if not fleet_id:
        return None
    prefijo = re.split(r"_(?:19|20)\d{2}(?:_|$)", str(fleet_id), maxsplit=1)[0]
    return indice.get(clave_aerolinea(prefijo))


def _es_filial(entidad):
    return entidad["airline_id"] != entidad["group_id"]


def elegir_entidad(entidad_nombre, entidad_fleet, grupo_doc):
    """Decide la identidad de una fila a partir del nombre y del fleet_id.

    Devuelve (entidad, motivo); motivo es None si se usa el nombre sin cambios.
      1. Sin nombre resoluble, se usa el fleet_id.
      2. Si el nombre cae fuera del grupo del documento y el fleet_id cae dentro,
         manda el fleet_id (HK Express etiquetada como Juneyao en el informe de
         Cathay).
      3. Si ambos son del mismo grupo y el fleet_id nombra una filial de la
         aerolinea que dice el nombre, manda la filial (Air Hong Kong etiquetada
         como Cathay Pacific).
    """
    if entidad_nombre is None:
        if entidad_fleet is not None:
            return entidad_fleet, "nombre_no_resuelto_se_usa_fleet_id"
        return None, None
    if entidad_fleet is None or entidad_fleet["airline_id"] == entidad_nombre["airline_id"]:
        return entidad_nombre, None

    if (grupo_doc is not None
            and entidad_nombre["group_id"] != grupo_doc
            and entidad_fleet["group_id"] == grupo_doc):
        return entidad_fleet, "nombre_fuera_del_grupo_del_documento"

    if (entidad_fleet["group_id"] == entidad_nombre["group_id"]
            and _es_filial(entidad_fleet) and not _es_filial(entidad_nombre)):
        return entidad_fleet, "fleet_id_nombra_una_filial"

    return entidad_nombre, None


# ---------------------------------------------------------------------------
# Desgloses alternativos de la misma flota
# ---------------------------------------------------------------------------

# Tipos que nombran una familia sin precisar el modelo: "A320 Series",
# "B737系列", "A320 family".
PATRON_TIPO_GENERICO = re.compile(r"(series|family|familia|系列)", re.I)


def familia_aeronave(aircraft_type):
    """Clave de familia para comparar desgloses: A319/A320/A321 (ceo/neo) -> a320,
    B737-800 / 737 MAX 8 -> b737, A350-1000 -> a350, ARJ21 -> arj21."""
    if not aircraft_type:
        return None
    t = str(aircraft_type).lower().replace("boeing", "b").replace("airbus", "a")
    t = re.sub(r"\s+", "", t)
    m = re.search(r"(a|b)?-?(\d{3})", t)
    if m:
        letra, num = m.group(1), m.group(2)
        if letra is None:
            letra = "a" if num.startswith("3") else "b" if num.startswith("7") else ""
        if letra == "a" and num in ("319", "320", "321"):
            return "a320"
        if letra in ("a", "b"):
            return letra + num
    m = re.match(r"[a-z]+\d*", t)
    return m.group(0) if m else t


def marcar_desgloses_alternativos(filas, cantidad="quantity"):
    """Marca como agregadas las filas por modelo que repiten una flota ya
    desglosada por familia.

    Algunos informes publican la misma flota dos veces: una tabla por familia y
    tipo de propiedad ("A320 Series": 98 propios, 34 en arrendamiento operativo,
    2 financiero) y otra por modelo (A320ceo 75, A320neo 47, A321neo 12). Ambas
    suman lo mismo. La regla: dentro de una misma aerolinea, ejercicio y familia,
    si las filas genericas y las especificas suman exactamente lo mismo, son dos
    lecturas de las mismas aeronaves y se marcan las especificas, que es la
    eleccion de la conciliacion del 16-nov para Spring (las filas por modelo
    venian todas como Owned, sin respaldo en el informe).

    No depende del fleet_id, que cambia entre corridas (Spring: '_total' el
    11-nov, '_owned' el 13-nov). Modifica las filas en sitio y devuelve una lista
    con lo marcado.
    """
    grupos = {}
    for f in filas:
        if f.get("es_fila_agregada"):
            continue
        clave = (f.get("airline_id") or f.get("airline_canonical"),
                 f.get("report_year"),
                 familia_aeronave(f.get("aircraft_type")))
        if None in clave:
            continue
        grupos.setdefault(clave, []).append(f)

    marcadas = []
    for (aerolinea, anio, familia), miembros in sorted(grupos.items(), key=lambda kv: str(kv[0])):
        es_generica = [bool(PATRON_TIPO_GENERICO.search(str(f.get("aircraft_type"))))
                       for f in miembros]
        genericas = [f for f, g in zip(miembros, es_generica) if g]
        especificas = [f for f, g in zip(miembros, es_generica) if not g]
        if not genericas or not especificas:
            continue
        suma_gen = sum(int(f.get(cantidad) or 0) for f in genericas)
        suma_esp = sum(int(f.get(cantidad) or 0) for f in especificas)
        if suma_gen > 0 and suma_gen == suma_esp:
            for f in especificas:
                f["es_fila_agregada"] = True
            marcadas.append({
                "aerolinea": aerolinea, "report_year": anio, "familia": familia,
                "aeronaves": suma_esp,
                "filas_marcadas": sorted(str(f.get("aircraft_type")) for f in especificas),
            })
    return marcadas


# ---------------------------------------------------------------------------
# Control de totales declarados
# ---------------------------------------------------------------------------

def verificar_totales(filas_vigentes, cfg_totales):
    """Compara la flota extraida por grupo con el total que declara cada informe.

    cfg_totales: {"grupos": {group_id: {"report_year": 2025,
                                        "totales_aceptados": [134], ...}}}

    filas_vigentes son las que van a current_fleet_fact. Se suman todas las del
    grupo, sea cual sea su ejercicio, porque eso es lo que ve cualquier consumidor
    que agregue por grupo: el 13-nov el 10-K 2025 de American no traia filas de
    American Eagle, el ultimo ejercicio de AAL_EAGLE paso a ser 2024 y sus 585
    aeronaves se sumaron a las 1580 que ya incluian la regional.

    Devuelve (errores, advertencias, detalle). Es error:
      - un grupo con referencia para su ejercicio cuyo total no coincide;
      - un grupo cuyas filas vigentes mezclan ejercicios (una filial que se
        quedo en un cierre anterior al del grupo);
      - un grupo con referencia que desaparece de la extraccion.
    Si ninguna fila vigente es del ejercicio de la referencia (llego un filing
    nuevo), no se puede comparar: queda como advertencia para conciliar y
    actualizar la referencia, y no bloquea.
    """
    sumas = {}
    anios = {}
    for f in filas_vigentes:
        if f.get("es_fila_agregada"):
            continue
        g = f.get("group_id") or f.get("airline_canonical")
        anio = f.get("report_year")
        if g is None or anio is None:
            continue
        sumas[g] = sumas.get(g, 0) + int(f.get("quantity") or 0)
        por_anio = anios.setdefault(g, {})
        aerolinea = f.get("airline_id") or f.get("airline_canonical")
        por_anio.setdefault(anio, set()).add(aerolinea)

    referencias = (cfg_totales or {}).get("grupos", {})
    errores, advertencias, detalle = [], [], {}

    for g, ref in sorted(referencias.items()):
        anio_ref = ref["report_year"]
        aceptados = ref["totales_aceptados"]
        if g not in anios:
            errores.append(f"{g}: grupo con total declarado ({aceptados}) ausente de la extraccion")
            detalle[g] = {"esperado": aceptados, "extraido": None, "estado": "ausente"}
            continue
        if anio_ref not in anios[g]:
            advertencias.append(
                f"{g}: ejercicio extraido {sorted(anios[g])} sin referencia (la hay para "
                f"{anio_ref}); conciliar y actualizar config/totales_declarados.json")
            detalle[g] = {"esperado": aceptados, "extraido": None, "estado": "sin_referencia_vigente"}
            continue
        extraido = sumas[g]
        ok = extraido in aceptados
        detalle[g] = {"report_year": anio_ref, "esperado": aceptados, "extraido": extraido,
                      "estado": "coincide" if ok else "difiere"}
        if not ok:
            errores.append(f"{g} {anio_ref}: extraido {extraido}, declarado {aceptados}")

    for g, por_anio in sorted(anios.items(), key=lambda kv: str(kv[0])):
        if len(por_anio) > 1:
            mezcla = {a: sorted(map(str, ids)) for a, ids in sorted(por_anio.items())}
            errores.append(f"{g}: las filas vigentes mezclan ejercicios {mezcla}")
            detalle.setdefault(g, {})["ejercicios_mezclados"] = mezcla

    for g in sorted(set(anios) - set(referencias)):
        advertencias.append(f"{g}: sin total declarado de referencia")

    return errores, advertencias, detalle
