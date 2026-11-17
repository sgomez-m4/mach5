"""Prueba v_talleres_senales sobre una copia con una descarga anterior sintetica.

Casos sinteticos en la descarga 2026-10-01, a partir de tres certificados reales:
  - 1OVR169C: sin rating de radio y sin acuerdo TCCA  -> rating nuevo + acuerdo nuevo
  - 4V4R473D: otro Accountable Manager                -> cambio de AM
  - LH6Y959J: no existia                              -> certificado nuevo
  - ZZZZ999Z: solo existia antes                      -> certificado retirado
"""
import os, subprocess, sys

P = "mach5-gemini-project.dataset_prospeccion"
vista = open(sys.argv[1], encoding="utf-8").read()
cuerpo = vista.split(" AS\n", 1)[1]
cuerpo = cuerpo.replace(f"`{P}.faa_talleres`", f"`{P}._prueba_talleres`")

preparar = f"""
CREATE OR REPLACE TABLE `{P}._prueba_talleres` AS
SELECT * FROM `{P}.faa_talleres`;

INSERT INTO `{P}._prueba_talleres`
SELECT * REPLACE (DATE '2026-10-01' AS fecha_descarga,
                  IF(cert_no = '1OVR169C', NULL, rating_radio) AS rating_radio,
                  IF(cert_no = '1OVR169C', ARRAY<STRING>[], bilateral_agreements) AS bilateral_agreements,
                  IF(cert_no = '4V4R473D', 'ANTERIOR, PERSONA', am_name) AS am_name)
FROM `{P}.faa_talleres`
WHERE cert_no != 'LH6Y959J';

INSERT INTO `{P}._prueba_talleres` (fecha_descarga, cert_no, state, agency_name, country)
VALUES (DATE '2026-10-01', 'ZZZZ999Z', 'TX', 'TALLER QUE CERRO', 'UNITED STATES');
"""
consulta = f"SELECT senal, cert_no, state, detalle FROM ({cuerpo}) ORDER BY cert_no, senal"
limpiar = f"DROP TABLE `{P}._prueba_talleres`"

def bq(sql, fmt="none"):
    cmd = ["cmd", "/c", "bq", "query", "--use_legacy_sql=false", f"--format={fmt}", "--max_rows=50"]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(r.stdout + r.stderr)
    return r.stdout

try:
    bq(preparar)
    print(bq(consulta, "pretty"))
finally:
    bq(limpiar)
    print("tabla de prueba borrada")
