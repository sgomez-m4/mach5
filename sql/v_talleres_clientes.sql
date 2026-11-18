CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_talleres_clientes` AS
-- Talleres de FAA (EE. UU.) que pertenecen a un proveedor de mantenimiento que
-- alguna aerolinea del score nombra en su ultimo 10-K o 20-F. Dice "esta
-- empresa le trabaja a esta aerolinea", no que ese certificado en particular
-- lo haga: GE o Pratt & Whitney tienen muchos talleres y el informe no dice cual.
--
-- La tabla talleres_clientes_filing la llena faa/clientes_filings.py. El cruce
-- es por alias: cada nombre corto que da Gemini se lleva a la expresion con que
-- esa empresa aparece en el listado de FAA.

-- taller_excluir quita homonimos: Airbus Helicopters no es el soporte de
-- componentes de Airbus. "MRO Holdings" (Aeromexico) no tiene alias: en FAA
-- solo aparece un homonimo sin relacion (LNK NE MRO Holdings).
WITH alias AS (
  SELECT * FROM UNNEST([
    STRUCT(r'(?i)pratt|p\s*&\s*w\b|\biae\b|international aero' AS proveedor_re,
           r'PRATT\s*&\s*WHITNEY|\bP\s*&\s*W\b|INTERNATIONAL AERO ENGINES' AS taller_re,
           CAST(NULL AS STRING) AS taller_excluir),
    (r'(?i)^ge\b|general electric|ge aerospace|ge engine|ge celma', r'\bGE (AVIATION|AEROSPACE|ENGINE)|GENERAL ELECTRIC|\bUNISON\b', NULL),
    (r'(?i)rolls', r'ROLLS[- ]ROYCE', NULL),
    (r'(?i)\bcfm\b', r'\bCFM\b', NULL),
    (r'(?i)lufthansa technik', r'LUFTHANSA TECHNIK', NULL),
    (r'(?i)\bmtu\b', r'\bMTU\b', NULL),
    (r'(?i)aeroman', r'AEROMAN', NULL),
    (r'(?i)\bklm\b', r'\bKLM\b', NULL),
    (r'(?i)embraer', r'EMBRAER', NULL),
    (r'(?i)airbus', r'AIRBUS', r'HELICOPTER'),
    (r'(?i)^atr$', r'\bATR\b', NULL),
    (r'(?i)coopesa', r'COOPESA', NULL),
    (r'(?i)nayak', r'NAYAK', NULL)
  ])
),

menciones AS (
  SELECT f.*, a.taller_re, a.taller_excluir
  FROM `mach5-gemini-project.dataset_prospeccion.talleres_clientes_filing` f
  JOIN alias a ON REGEXP_CONTAINS(f.nombre_corto, a.proveedor_re)
),

-- Un renglon por taller, aerolinea y servicio, con una sola cita
cruce AS (
  SELECT
    t.clave_taller, t.cert_no, t.agency_name, t.city, t.state, t.en_zona_objetivo, t.nivel, t.tipo_dueno,
    m.group_id, m.nombre_corto AS proveedor, m.servicio, m.tipo AS tipo_proveedor,
    m.documento, m.fecha_documento, m.url, m.cita,
    ROW_NUMBER() OVER (PARTITION BY t.clave_taller, m.group_id, m.servicio ORDER BY LENGTH(m.cita) DESC) AS rn
  FROM menciones m
  JOIN `mach5-gemini-project.dataset_prospeccion.v_talleres_informe` t
    ON REGEXP_CONTAINS(UPPER(CONCAT(t.agency_name, ' ', IFNULL(t.dba, ''), ' ', IFNULL(t.grupo_empresa, ''))),
                       m.taller_re)
   AND NOT IFNULL(REGEXP_CONTAINS(UPPER(t.agency_name), m.taller_excluir), FALSE)
)

SELECT c.* EXCEPT (rn), p.grupo AS aerolinea
FROM cruce c
LEFT JOIN `mach5-gemini-project.dataset_prospeccion.v_prospect_score_v2` p USING (group_id)
WHERE rn = 1
