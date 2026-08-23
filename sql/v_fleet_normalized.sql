CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_integrado.v_fleet_normalized` AS
-- Flota resuelta contra las dos dimensiones: identidad de aerolinea y taxonomia
-- de aeronave. Materializa las señales que antes solo existian como columnas
-- calculadas DAX dentro del modelo de Power BI (Aircraft_Model, Aircraft_Category,
-- Manufacturer, Remaining_Replacement_Life, Fleet_Replacement_Status), de modo que
-- cualquier consumidor SQL pueda usarlas.
--
-- es_fila_agregada marca las filas que son un agregado del propio reporte
-- ("Total Fleet (Includes...)", "Commercial Aircraft"). Se conservan para no
-- perder aeronaves del conteo, pero deben excluirse al analizar por tipo o
-- generacion, porque no corresponden a un modelo real.
SELECT
  f.report_year,
  f.fleet_id,

  -- Identidad
  f.airline               AS airline_reportada,
  f.airline_id,
  f.group_id,
  d.display_name          AS airline,
  g.display_name          AS grupo,
  g.region,
  g.country,
  g.business_model,
  g.financial_company,

  -- Taxonomia de aeronave
  f.aircraft_type         AS aircraft_type_reportado,
  m.model                 AS aircraft_model,
  m.manufacturer,
  m.category,
  m.generation,
  m.design_life_years,
  m.replacement_target,

  -- Metricas de flota
  f.quantity,
  f.ownership_type,
  f.average_age_years,
  f.remaining_lease_years,
  f.seating_capacity_avg,
  f.es_fila_agregada,

  -- Clasificacion de arrendamiento: ownership_type esta poblado al 100%, asi que
  -- es la señal mas confiable para exposicion a leasing.
  CASE
    WHEN f.ownership_type LIKE '%Operating%' THEN 'Operating Lease'
    WHEN f.ownership_type LIKE '%Finance%'   THEN 'Finance Lease'
    WHEN f.ownership_type LIKE '%Owned%'     THEN 'Owned'
    ELSE 'Unknown'
  END                     AS tipo_propiedad,
  (f.ownership_type LIKE '%Lease%') AS es_arrendada,

  -- Vida util remanente frente al objetivo de reemplazo del modelo.
  -- Replica MAX(0, Replacement_Target - average_age_years) del DAX.
  IF(f.average_age_years IS NULL, NULL,
     GREATEST(0, m.replacement_target - f.average_age_years)
  )                       AS remaining_replacement_life,

  -- Ventana de reemplazo. Replica el SWITCH de Fleet_Replacement_Status.
  -- Se distingue "Sin dato" de "Healthy": sin average_age_years no se puede
  -- afirmar que la flota este sana, y esa distincion se perdia en el DAX.
  CASE
    WHEN f.average_age_years IS NULL THEN 'Sin dato'
    WHEN m.replacement_target - f.average_age_years <= 0 THEN 'Replace Now'
    WHEN m.replacement_target - f.average_age_years <= 3 THEN 'Critical'
    WHEN m.replacement_target - f.average_age_years <= 5 THEN 'Monitor'
    ELSE 'Healthy'
  END                     AS fleet_replacement_status

FROM `mach5-gemini-project.dataset_integrado.current_fleet_fact` f
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_airline` d
  ON d.airline_id = f.airline_id
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_airline` g
  ON g.airline_id = f.group_id
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_aircraft_type` m
  ON m.model = `mach5-gemini-project.dataset_integrado.normalizar_modelo_aeronave`(f.aircraft_type);
