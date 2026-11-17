CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_directivos_operador` AS
-- Directivos que cada certificado de operador declara ante FAA (ultima
-- descarga), unidos a los grupos del score. Un renglon por certificado y cargo,
-- para la ficha de aerolinea. FAA no publica correo ni telefono de las personas;
-- el telefono es el del operador.

WITH ultima AS (
  SELECT * FROM `mach5-gemini-project.dataset_prospeccion.faa_operadores`
  WHERE fecha_descarga = (SELECT MAX(fecha_descarga)
                          FROM `mach5-gemini-project.dataset_prospeccion.faa_operadores`)
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
  o.fecha_descarga
FROM `mach5-gemini-project.dataset_prospeccion.dim_operador_certificado` d
JOIN ultima o ON o.certificate_number = d.faa_certificate_number
CROSS JOIN UNNEST([
  STRUCT(1 AS orden, 'CEO' AS cargo, o.ceo_name AS nombre),
  (2, 'Director de mantenimiento', o.director_maintenance),
  (3, 'Director de operaciones',   o.director_operations),
  (4, 'Jefe de pilotos',           o.chief_pilot)]) c
WHERE c.nombre IS NOT NULL AND TRIM(c.nombre) != ''
