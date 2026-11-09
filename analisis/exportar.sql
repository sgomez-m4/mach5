-- Exportaciones usadas por la evaluacion posterior (analisis/sensibilidad.py).
-- Se ejecutan con bq en formato CSV; ver analisis/README.md.

-- 1. datos/score_full.csv: la vista de puntuacion completa
SELECT *
FROM `mach5-gemini-project.dataset_integrado.v_prospect_score`
ORDER BY prospect_score DESC;

-- 2. datos/ventana.csv: aeronaves en ventana y aeronaves con vida remanente
--    conocida, por grupo (para la correccion del denominador)
SELECT
  grupo,
  SUM(quantity) AS total,
  SUM(IF(fleet_replacement_status IN ("Replace Now", "Critical"), quantity, 0)) AS ventana,
  SUM(IF(remaining_replacement_life IS NULL, 0, quantity)) AS con_vida
FROM `mach5-gemini-project.dataset_integrado.v_fleet_normalized`
WHERE NOT es_fila_agregada
GROUP BY grupo
ORDER BY grupo;
