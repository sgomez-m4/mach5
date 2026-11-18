"""Crea el libro de Excel del seguimiento comercial, precargado con las cuentas
del score v2: las aerolineas y los talleres del sur en nivel A o B de venta
directa, todos en "Sin contactar".

El libro vive en el OneDrive o SharePoint de mas4 y el equipo lo edita en Excel
en linea. Power BI lo lee desde ahi (parametro RutaSeguimiento del modelo).

Usa solo la libreria estandar: el .xlsx se arma a mano (Office Open XML), con
una tabla de Excel (tblSeguimiento) y una lista desplegable de estados.

Uso:
  python -I -X utf8 crear_seguimiento.py <archivo.xlsx>

No sobrescribe un archivo existente: el seguimiento lo edita el equipo y un
archivo nuevo borraria su trabajo. Para agregar cuentas nuevas, se agregan
filas a la tabla en Excel.
"""
import datetime
import json
import os
import subprocess
import sys
import zipfile
from xml.sax.saxutils import escape

ESTADOS = ["Sin contactar", "Contactado", "Reunión agendada", "Propuesta enviada", "Negociación",
           "Ganado", "Perdido", "En pausa"]
COLUMNAS = [  # (encabezado, ancho)
    ("Tipo", 11), ("ID", 18), ("Cuenta", 42), ("Detalle", 46), ("Estado", 18), ("Responsable", 18),
    ("Próxima acción", 40), ("Fecha próxima acción", 14), ("Último contacto", 14), ("Notas", 50)]
COL_FECHAS = {"Fecha próxima acción", "Último contacto"}


def bq(sql):
    r = subprocess.run(["cmd", "/c", "bq", "query", "--use_legacy_sql=false", "--format=json",
                        "--max_rows=5000"] if os.name == "nt" else
                       ["bq", "query", "--use_legacy_sql=false", "--format=json", "--max_rows=5000"],
                       input=sql, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise SystemExit(r.stdout + r.stderr)
    return json.loads(r.stdout)


def cuentas():
    aer = bq("""
      SELECT 'Aerolínea' AS tipo, group_id AS id, grupo AS cuenta,
             CONCAT('Nivel ', nivel, ' · ', linea_principal, ' · ', country) AS detalle
      FROM `mach5-gemini-project.dataset_prospeccion.v_prospect_score_v2`
      ORDER BY orden""")
    tal = bq("""
      SELECT 'Taller' AS tipo, clave_taller AS id, agency_name AS cuenta,
             CONCAT('Nivel ', nivel, ' · ', oferta_sugerida, ' · ', city, ', ', state) AS detalle
      FROM `mach5-gemini-project.dataset_prospeccion.v_talleres_informe`
      WHERE en_zona_objetivo AND canal = 'Venta directa' AND nivel IN ('A', 'B')
      ORDER BY puntaje_mayor DESC, agency_name""")
    return aer + tal


def col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def celda(ref, valor, estilo=0):
    if valor is None or valor == "":
        return f'<c r="{ref}" s="{estilo}"/>' if estilo else ""
    return f'<c r="{ref}" t="inlineStr" s="{estilo}"><is><t xml:space="preserve">{escape(str(valor))}</t></is></c>'


def hoja_seguimiento(filas):
    n = len(filas) + 1
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"{" hidden=\"1\"" if c == "ID" else ""}/>'
                   for i, (c, w) in enumerate(COLUMNAS))
    datos = ['<row r="1">' + "".join(celda(f"{col(i)}1", c, 1) for i, (c, _) in enumerate(COLUMNAS)) + "</row>"]
    for r, f in enumerate(filas, start=2):
        valores = [f["tipo"], f["id"], f["cuenta"], f["detalle"], "Sin contactar", "", "", None, None, ""]
        celdas = []
        for i, v in enumerate(valores):
            estilo = 2 if COLUMNAS[i][0] in COL_FECHAS else 0
            celdas.append(celda(f"{col(i)}{r}", v, estilo))
        datos.append(f'<row r="{r}">' + "".join(celdas) + "</row>")
    ultima = col(len(COLUMNAS) - 1)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:{ultima}{n}"/>'
        '<sheetViews><sheetView workbookViewId="0" tabSelected="1"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols}</cols><sheetData>{''.join(datos)}</sheetData>"
        '<dataValidations count="1"><dataValidation type="list" allowBlank="1" showErrorMessage="1" '
        'errorTitle="Estado" error="Elige un estado de la lista." sqref="E2:E20000">'
        f'<formula1>Listas!$A$2:$A${len(ESTADOS) + 1}</formula1></dataValidation></dataValidations>'
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        '<tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>')


def hoja_listas():
    filas = ['<row r="1">' + celda("A1", "Estados", 1) + celda("C1", "Cómo usar este libro", 1) + "</row>"]
    notas = [
        "Una fila por cuenta. Cambia el Estado con la lista desplegable.",
        "La columna ID (oculta) une la fila con el informe de Power BI: no la cambies.",
        "Para una cuenta nueva, agrega una fila al final de la tabla y deja el ID vacío.",
        "Fechas en formato fecha de Excel; la próxima acción vencida se marca en el informe.",
        "Ganado y Perdido cierran la cuenta: no cuentan como acciones pendientes.",
    ]
    for i in range(max(len(ESTADOS), len(notas))):
        r = i + 2
        filas.append(f'<row r="{r}">' + celda(f"A{r}", ESTADOS[i] if i < len(ESTADOS) else None)
                     + celda(f"C{r}", notas[i] if i < len(notas) else None) + "</row>")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<cols><col min="1" max="1" width="22" customWidth="1"/><col min="3" max="3" width="90" customWidth="1"/></cols>'
            f"<sheetData>{''.join(filas)}</sheetData></worksheet>")


def tabla(n):
    ultima = col(len(COLUMNAS) - 1)
    columnas = "".join(f'<tableColumn id="{i + 1}" name="{escape(c)}"/>' for i, (c, _) in enumerate(COLUMNAS))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="1" '
            f'name="tblSeguimiento" displayName="tblSeguimiento" ref="A1:{ultima}{n}" totalsRowShown="0">'
            f'<autoFilter ref="A1:{ultima}{n}"/><tableColumns count="{len(COLUMNAS)}">{columnas}</tableColumns>'
            '<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" '
            'showRowStripes="1" showColumnStripes="0"/></table>')


ESTILOS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
           '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
           '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
           '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
           '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
           '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
           '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def escribir(ruta, filas):
    n = len(filas) + 1
    partes = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '</Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>',
        "docProps/core.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>Seguimiento comercial mas4</dc:title>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ}</dcterms:created>'
            '</cp:coreProperties>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Seguimiento" sheetId="1" r:id="rId1"/><sheet name="Listas" sheetId="2" r:id="rId2"/></sheets>'
            '</workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        "xl/worksheets/_rels/sheet1.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table1.xml"/>'
            '</Relationships>',
        "xl/worksheets/sheet1.xml": hoja_seguimiento(filas),
        "xl/worksheets/sheet2.xml": hoja_listas(),
        "xl/tables/table1.xml": tabla(n),
        "xl/styles.xml": ESTILOS,
    }
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, contenido in partes.items():
            z.writestr(nombre, contenido)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    ruta = sys.argv[1]
    if os.path.exists(ruta):
        raise SystemExit(f"{ruta} ya existe; no se sobrescribe el seguimiento del equipo")
    filas = cuentas()
    escribir(ruta, filas)
    print(f"{ruta}: {sum(f['tipo'] == 'Aerolínea' for f in filas)} aerolineas y "
          f"{sum(f['tipo'] == 'Taller' for f in filas)} talleres")


if __name__ == "__main__":
    main()
