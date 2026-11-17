# -*- coding: utf-8 -*-
"""Convierte un catalogo de config/ a NDJSON para cargar en BigQuery.

Las tablas de dimension no son una copia literal del JSON: dim_airline lleva dos
columnas derivadas que las vistas usan como llave. Cuando esta derivacion vivia
fuera del repo, cargar el JSON crudo dejaba la tabla sin group_id y
v_prospect_score dejaba de compilar.

Uso:
    python scripts/generar_dim_ndjson.py config/dim_airline.json airlines salida.ndjson
"""
import io
import json
import sys
import unicodedata


def clave_normalizada(texto):
    """Misma normalizacion que usa ejecutar-pipeline-anuales para resolver nombres:
    sin acentos, sin puntuacion, en minusculas."""
    txt = unicodedata.normalize("NFKD", str(texto))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return "".join(c for c in txt.lower() if c.isalnum())


def derivar_airline(fila):
    """group_id es la entidad de reporte del grupo: la matriz si la fila es una
    subsidiaria, ella misma si no. match_keys son las variantes de nombre ya
    normalizadas, que es contra lo que casa la resolucion de identidad."""
    fila = dict(fila)
    fila["group_id"] = fila.get("parent_id") or fila["airline_id"]
    fila["match_keys"] = sorted({clave_normalizada(v)
                                 for v in (fila.get("name_variants") or [])})
    return fila


def aplanar_pedidos(entrada):
    """config/pedidos_complementarios.json agrupa las filas por aerolinea; la
    tabla lleva una fila por pedido con la identidad y la fuente repetidas."""
    comunes = {k: v for k, v in entrada.items() if k != "filas"}
    return [dict(comunes, **f) for f in entrada["filas"]]


DERIVACIONES = {
    "airlines": derivar_airline,
}

# Derivaciones que convierten una entrada del JSON en varias filas
APLANADOS = {
    "aerolineas": aplanar_pedidos,
    # config/talleres_duenos.json guarda los dominios personales como lista simple
    "correo_personal": lambda dominio: [{"dominio": dominio}],
}

# Columnas que las vistas necesitan. bq load --autodetect omite del esquema
# cualquier campo que sea null en todas las filas, asi que un catalogo donde
# nadie tenga parent_id dejaria la tabla sin esa columna sin avisar.
REQUERIDAS = {
    "airlines": ["airline_id", "group_id", "parent_id", "display_name",
                 "financial_company", "region", "country", "business_model",
                 "name_variants", "match_keys"],
    "models": ["model", "manufacturer", "category", "generation",
               "design_life_years", "replacement_target"],
    # Pedidos publicados fuera del Item 2 (config/pedidos_complementarios.json)
    "aerolineas": ["airline_id", "group_id", "display_name", "report_year", "fuente",
                   "aircraft_type", "quantity_committed", "delivery_period"],
    # Dueno de los talleres Part 145 (config/talleres_duenos.json)
    "reglas": ["prioridad", "tipo_dueno", "tipo_regla", "valor"],
    "correo_personal": ["dominio"],
    # Puente con FAA y DOT (config/operadores_certificados.json)
    "operadores": ["airline_id", "group_id", "operador", "faa_dsgn_code",
                   "faa_certificate_number", "cfr_part", "dot_unique_carrier",
                   "dot_airline_id", "en_flota_reportada"],
}


def main(ruta_json, clave, ruta_salida):
    datos = json.load(io.open(ruta_json, encoding="utf-8"))
    filas = datos[clave] if isinstance(datos, dict) else datos

    derivar = DERIVACIONES.get(clave)
    if derivar:
        filas = [derivar(f) for f in filas]
    aplanar = APLANADOS.get(clave)
    if aplanar:
        filas = [fila for entrada in filas for fila in aplanar(entrada)]

    faltantes = [c for c in REQUERIDAS.get(clave, []) if c not in (filas[0] if filas else {})]
    if faltantes:
        sys.exit("faltan columnas requeridas en {}: {}".format(ruta_json, faltantes))

    # Un campo nulo en todas las filas desaparece del esquema autodetectado.
    for columna in REQUERIDAS.get(clave, []):
        if all(f.get(columna) is None for f in filas):
            sys.exit("la columna '{}' es nula en las {} filas de {}: el esquema "
                     "autodetectado la omitiria".format(columna, len(filas), ruta_json))

    with io.open(ruta_salida, "w", encoding="utf-8", newline="\n") as salida:
        for fila in filas:
            salida.write(json.dumps(fila, ensure_ascii=False) + "\n")

    print("{} filas preparadas desde {}".format(len(filas), ruta_json))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
