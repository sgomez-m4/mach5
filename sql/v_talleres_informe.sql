CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_talleres_informe` AS
-- v_talleres_calificacion en la forma que necesita Power BI: sin columnas
-- REPEATED (el conector no las lee) y con una clave de una sola columna para
-- las relaciones. Los arreglos pasan a texto; para filtrar por rating o por
-- acuerdo estan v_talleres_ratings y v_talleres_acuerdos.

SELECT
  CONCAT(cert_no, '|', IFNULL(state, '')) AS clave_taller,
  * EXCEPT (airframe_clases, powerplant_clases, limited_especialidades, bilateral_agreements),
  IFNULL(NULLIF(ARRAY_TO_STRING(bilateral_agreements, ', '), ''), 'Ninguno') AS acuerdos,
  ARRAY_LENGTH(bilateral_agreements) AS n_acuerdos,
  ARRAY_TO_STRING(limited_especialidades, ', ') AS especialidades_limitadas,
  -- Una sola linea legible con todos los ratings, para la ficha
  ARRAY_TO_STRING(ARRAY(
    SELECT x FROM UNNEST([
      'Estructura: '   || rating_airframe,
      'Motores: '      || rating_powerplant,
      'Helices: '      || rating_propeller,
      'Radio: '        || rating_radio,
      'Instrumentos: ' || rating_instrument,
      'Accesorios: '   || rating_accessory]) x
    WHERE x IS NOT NULL), ' | ') AS ratings_texto,
  GREATEST(puntaje_personal, puntaje_backoffice) AS puntaje_mayor
FROM `mach5-gemini-project.dataset_prospeccion.v_talleres_calificacion`
