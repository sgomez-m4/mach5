CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_directivos_operador` AS
-- Directivos de cada grupo del score, para la ficha de aerolinea. Dos fuentes:
--
--  - FAA: lo que cada certificado de operador declara (ultima descarga). Solo
--    operadores de EE. UU.; FAA no publica correo ni telefono de las personas,
--    y el telefono es el del operador.
--  - Informes anuales: el 20-F de las latinoamericanas y el informe anual de
--    las chinas (tabla directivos_filings, de directivos/extraer_directivos.py).
--    El nombre lleva el titulo original entre parentesis, porque el cargo
--    normalizado es una clasificacion de Gemini.
--
-- El orden por cargo es el mismo en ambas fuentes: cada cargo tiene un solo
-- numero, como pide la columna de orden del informe.

WITH ultima AS (
  SELECT * FROM `mach5-gemini-project.dataset_prospeccion.faa_operadores`
  WHERE fecha_descarga = (SELECT MAX(fecha_descarga)
                          FROM `mach5-gemini-project.dataset_prospeccion.faa_operadores`)
),

orden_cargo AS (
  SELECT * FROM UNNEST([
    STRUCT('CEO' AS cargo, 1 AS orden),
    ('Director de mantenimiento', 2), ('Director técnico o de mantenimiento', 2),
    ('Director de operaciones', 3), ('Jefe de pilotos', 4), ('Director de flota o compras', 5),
    ('CFO', 6), ('Presidente del consejo', 7), ('Otro directivo', 8)])
)

SELECT
  d.group_id,
  d.airline_id,
  o.operator_name AS operador,
  o.dsgn_code,
  o.certificate_number,
  o.cfr_part,
  c.orden,
  c.cargo,
  c.nombre,
  o.phone AS telefono_operador,
  CONCAT(IFNULL(o.city, ''), IF(o.state IS NULL, '', ', ' || o.state)) AS sede,
  o.total_employees,
  o.certificated_mechanics,
  o.fecha_descarga,
  'FAA' AS fuente
FROM `mach5-gemini-project.dataset_prospeccion.dim_operador_certificado` d
JOIN ultima o ON o.certificate_number = d.faa_certificate_number
CROSS JOIN UNNEST([
  STRUCT(1 AS orden, 'CEO' AS cargo, o.ceo_name AS nombre),
  (2, 'Director de mantenimiento', o.director_maintenance),
  (3, 'Director de operaciones',   o.director_operations),
  (4, 'Jefe de pilotos',           o.chief_pilot)]) c
WHERE c.nombre IS NOT NULL AND TRIM(c.nombre) != ''

UNION ALL

SELECT
  f.group_id,
  f.group_id AS airline_id,
  CONCAT('Según ', f.documento) AS operador,
  CAST(NULL AS STRING) AS dsgn_code,
  CAST(NULL AS STRING) AS certificate_number,
  CAST(NULL AS STRING) AS cfr_part,
  IFNULL(oc.orden, 8) AS orden,
  IF(oc.cargo IS NULL, 'Otro directivo', f.cargo) AS cargo,
  CONCAT(f.nombre, ' (', f.cargo_original, ')') AS nombre,
  CAST(NULL AS STRING) AS telefono_operador,
  CAST(NULL AS STRING) AS sede,
  CAST(NULL AS INT64) AS total_employees,
  CAST(NULL AS INT64) AS certificated_mechanics,
  CAST(NULL AS DATE) AS fecha_descarga,
  f.documento AS fuente
FROM `mach5-gemini-project.dataset_prospeccion.directivos_filings` f
LEFT JOIN orden_cargo oc USING (cargo)
