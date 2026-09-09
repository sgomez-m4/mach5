CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_integrado.v_fleet_series` AS
-- Flota por ejercicio, una fila por grupo y año.
--
-- v_fleet_transition responde "que entro y que salio" comparando los dos
-- extremos de la serie. Esta responde otra cosa: como fue la trayectoria. Con
-- dos cierres son lo mismo; con tres o mas, no —una flota que sube y luego baja
-- y otra que sube dos veces tienen el mismo delta y significan cosas distintas—.
--
-- SOBRE LA FIABILIDAD. La serie se construye extrayendo la tabla de flota de
-- cada informe anual por separado, y los informes viejos se extraen peor que el
-- ultimo: aparecen epigrafes que no son aeronaves ("Aircraft and Engines",
-- "Contractual Fleet") y, en algun caso, la misma tabla leida dos veces. Se
-- excluye lo primero, que es identificable por el texto; lo segundo no se puede
-- separar de una filial regional que opera el mismo modelo, asi que NO se
-- corrige: se marca. Un año cuyo total se aparta mas de un 35% de la mediana de
-- su propia serie sale con anio_fiable = FALSE, y el panel lo enseña como tal.
-- Preferible a un limpiador que no se puede validar y borra flota real.

WITH base AS (
  SELECT
    h.group_id,
    h.report_year,
    h.quantity,
    h.average_age_years,
    h.ownership_type
  FROM `mach5-gemini-project.dataset_integrado.fleet_history_fact` h
  WHERE h.group_id IS NOT NULL
    AND NOT h.es_fila_agregada
    AND h.quantity IS NOT NULL
    -- No son tipos de aeronave: son epigrafes del calendario de arrendamiento o
    -- totales que el propio reporte ya suma aparte. Sumarlos duplica la flota.
    AND NOT REGEXP_CONTAINS(
          LOWER(h.aircraft_type),
          r'(engine|contractual|total fleet|commercial aircraft|^\s*fleet\s*$|^\s*aircraft\s*$)')
),

por_anio AS (
  SELECT
    group_id,
    report_year,
    SUM(quantity) AS aeronaves,
    SAFE_DIVIDE(
      SUM(quantity * average_age_years),
      SUM(IF(average_age_years IS NULL, 0, quantity))
    ) AS edad_ponderada,
    SUM(IF(ownership_type = 'Operating Lease', quantity, 0)) AS aeronaves_arrendadas
  FROM base
  GROUP BY 1, 2
),

-- La mediana de la propia serie es la referencia: no depende de que la flota
-- sea grande o pequeña, solo de que el año se parezca a los demas del grupo.
referencia AS (
  SELECT
    group_id,
    COUNT(DISTINCT report_year) AS ejercicios,
    APPROX_QUANTILES(aeronaves, 2)[OFFSET(1)] AS mediana
  FROM por_anio
  GROUP BY 1
)

SELECT
  p.group_id,
  d.airline_id,
  d.display_name AS aerolinea,
  d.region,
  d.business_model,
  p.report_year,
  p.aeronaves,
  ROUND(p.edad_ponderada, 1) AS edad_ponderada,
  p.aeronaves_arrendadas,
  SAFE_DIVIDE(p.aeronaves_arrendadas, p.aeronaves) AS pct_arrendada,
  r.ejercicios,
  r.mediana AS mediana_serie,
  -- Con un solo ejercicio no hay nada contra que contrastar: no se marca dudoso,
  -- simplemente no hay serie.
  CASE
    WHEN r.ejercicios < 2 THEN TRUE
    WHEN r.mediana IS NULL OR r.mediana = 0 THEN TRUE
    ELSE ABS(p.aeronaves - r.mediana) / r.mediana <= 0.35
  END AS anio_fiable,
  p.aeronaves - LAG(p.aeronaves) OVER (
    PARTITION BY p.group_id ORDER BY p.report_year) AS variacion_anual
FROM por_anio p
JOIN referencia r USING (group_id)
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_airline` d
  ON d.airline_id = p.group_id
ORDER BY aerolinea, report_year;
