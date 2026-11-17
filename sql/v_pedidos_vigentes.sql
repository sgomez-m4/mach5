CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_pedidos_vigentes` AS
-- Order book vigente para la prospeccion v2, con las dos correcciones que
-- ejecutar-pipeline-anuales aplica desde el 17-nov-2026:
--
--   1. Se descartan pedidos de un ejercicio anterior al de la flota vigente de
--      la misma aerolinea (los de 2024 de American Eagle, ya superados).
--   2. Se agregan los pedidos que la aerolinea publica fuera del Item 2
--      (config/pedidos_complementarios.json; hoy Alaska), solo si la extraccion
--      no trajo pedidos suyos en ese ejercicio.
--
-- Por que una vista aparte y no la tabla corregida: dataset_integrado conserva
-- el estado del trabajo de la especializacion (sustentacion del 28-nov-2026), y
-- su order_book_fact no debe cambiar hasta despues. Cuando el pipeline
-- corregido vuelva a cargar esa tabla, esta vista no cambia de resultado: las
-- dos reglas quedan sin efecto porque los datos ya llegan corregidos.

WITH ultima_flota AS (
  SELECT airline_id, MAX(report_year) AS anio_flota
  FROM `mach5-gemini-project.dataset_integrado.current_fleet_fact`
  WHERE airline_id IS NOT NULL
  GROUP BY airline_id
),

extraidos AS (
  SELECT
    o.order_id, o.airline_id, o.group_id, o.report_year,
    o.aircraft_type_reportado, o.aircraft_model, o.manufacturer, o.generation,
    o.quantity_committed, o.delivery_period, o.anio_primera_entrega,
    'Extraccion del informe' AS origen
  FROM `mach5-gemini-project.dataset_integrado.v_order_book_normalized` o
  LEFT JOIN ultima_flota u USING (airline_id)
  WHERE u.anio_flota IS NULL OR o.report_year >= u.anio_flota
),

complementarios AS (
  SELECT
    CONCAT(c.display_name, '_', CAST(c.report_year AS STRING), '_',
           c.aircraft_type, '_', c.delivery_period)            AS order_id,
    c.airline_id,
    c.group_id,
    c.report_year,
    c.aircraft_type                                            AS aircraft_type_reportado,
    m.model                                                    AS aircraft_model,
    m.manufacturer,
    m.generation,
    c.quantity_committed,
    c.delivery_period,
    -- Las entradas del config usan anio o rango ("2026-2035"): el primer anio
    -- es el inicio de entregas
    SAFE_CAST(REGEXP_EXTRACT(c.delivery_period, r'(20[2-4][0-9])') AS INT64) AS anio_primera_entrega,
    CONCAT('Complementario: ', c.fuente)                       AS origen
  FROM `mach5-gemini-project.dataset_prospeccion.pedidos_complementarios` c
  JOIN ultima_flota u
    ON u.airline_id = c.airline_id AND u.anio_flota = c.report_year
  LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_aircraft_type` m
    ON m.model = `mach5-gemini-project.dataset_integrado.normalizar_modelo_aeronave`(c.aircraft_type)
  WHERE NOT EXISTS (
    SELECT 1 FROM extraidos e
    WHERE e.airline_id = c.airline_id AND e.report_year = c.report_year
  )
)

SELECT * FROM extraidos
UNION ALL
SELECT * FROM complementarios
