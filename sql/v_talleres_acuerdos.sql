CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_talleres_acuerdos` AS
-- Un renglon por taller y acuerdo bilateral, para filtrar en Power BI. Los
-- talleres sin acuerdo llevan un renglon "Ninguno" para que tambien se puedan
-- elegir. Se une a v_talleres_informe por clave_taller.

SELECT
  CONCAT(cert_no, '|', IFNULL(state, '')) AS clave_taller,
  CASE a
    WHEN 'United Kingdom' THEN 'Reino Unido (UK CAA)'
    WHEN 'TCCA (Canada)'  THEN 'Canada (TCCA)'
    WHEN 'Brazil'         THEN 'Brasil (ANAC)'
    WHEN 'Singapore'      THEN 'Singapur (CAAS)'
    ELSE a
  END AS acuerdo
FROM `mach5-gemini-project.dataset_prospeccion.v_talleres_calificacion`,
     UNNEST(IF(ARRAY_LENGTH(bilateral_agreements) > 0, bilateral_agreements, ['Ninguno'])) a
