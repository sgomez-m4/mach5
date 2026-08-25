CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_integrado.v_order_book_normalized` AS
-- El order book resuelto contra las dos dimensiones y con la fecha de entrega
-- parseada, para que ningun consumidor tenga que interpretar texto libre.
--
-- Hasta ahora el modelo de Power BI clasificaba el fabricante del order book con
-- coincidencias de substring sobre el texto crudo -"0-" para Airbus, "7-" para
-- Boeing- mientras que en la flota usaba el modelo ya normalizado. Los mismos
-- datos podian clasificarse distinto segun la tabla. Esta vista deja una sola
-- respuesta, la misma que usa v_fleet_normalized.
--
-- delivery_period es texto libre y llega en diecinueve formas distintas: "2026",
-- "After 2028", "2029 and Thereafter", "2026-2034", "Up to 2029", "Thereafter".
-- Sin parsearlo no se puede ponderar un pedido por su proximidad, que es lo que
-- distingue una entrega el proximo trimestre de una comprometida para 2032.

WITH base AS (
  SELECT
    o.order_id,
    o.airline_id,
    o.group_id,
    o.airline_canonical,
    o.report_year,
    o.aircraft_type AS aircraft_type_reportado,
    o.quantity_committed,
    o.delivery_period,
    o.es_fila_agregada,
    `mach5-gemini-project.dataset_integrado.normalizar_modelo_aeronave`(o.aircraft_type) AS aircraft_model,

    -- Primer año que aparece en el texto. Cubre tanto "2026" como "2026-2034"
    -- y "2029 and Thereafter"; el rango se ancla en su inicio.
    SAFE_CAST(REGEXP_EXTRACT(o.delivery_period, r'(20[2-4][0-9])') AS INT64) AS anio_citado,

    -- "After 2028" excluye el año que nombra; "2028 and beyond" y "2029 and
    -- Thereafter" lo incluyen. Solo el primero desplaza la fecha un año.
    REGEXP_CONTAINS(IFNULL(o.delivery_period, ''), r'(?i)after\s*20[2-4][0-9]') AS es_posterior_a,
    -- "Up to 2029" y "Through 2028" acotan el final, no el principio.
    REGEXP_CONTAINS(IFNULL(o.delivery_period, ''), r'(?i)up to|through')         AS es_hasta,
    -- Horizonte sin cierre: la primera entrega esta fechada pero la ultima no.
    REGEXP_CONTAINS(IFNULL(o.delivery_period, ''), r'(?i)beyond|thereafter|after') AS es_horizonte_abierto
  FROM `mach5-gemini-project.dataset_integrado.order_book_fact` o
),

fechada AS (
  SELECT
    b.*,
    CASE
      -- "After 2028" significa 2029 en adelante. "2028 and beyond" no: incluye
      -- el 2028, asi que cae en la rama general de abajo.
      WHEN b.es_posterior_a AND b.anio_citado IS NOT NULL THEN b.anio_citado + 1
      -- "Up to 2029" acota el final, no el principio: la primera entrega es la
      -- del ejercicio siguiente al del reporte
      WHEN b.es_hasta AND b.report_year IS NOT NULL THEN b.report_year + 1
      WHEN b.anio_citado IS NOT NULL THEN b.anio_citado
      -- "Thereafter" a secas no da ningun anclaje
      ELSE NULL
    END AS anio_primera_entrega
  FROM base b
)

SELECT
  f.order_id,
  f.airline_id,
  f.group_id,
  d.display_name        AS grupo,
  d.region,
  d.country,
  d.business_model,

  f.report_year,
  f.aircraft_type_reportado,
  f.aircraft_model,
  m.manufacturer,
  m.category,
  m.generation,

  f.quantity_committed,
  f.delivery_period,
  f.anio_primera_entrega,
  -- Años desde el cierre del reporte hasta la primera entrega
  f.anio_primera_entrega - f.report_year AS anios_hasta_entrega,
  (f.es_horizonte_abierto OR f.es_hasta OR f.anio_primera_entrega IS NULL) AS fecha_aproximada,

  CASE
    WHEN f.anio_primera_entrega IS NULL                       THEN 'Sin fecha'
    WHEN f.anio_primera_entrega - f.report_year <= 1          THEN 'Inminente (12 meses)'
    WHEN f.anio_primera_entrega - f.report_year <= 3          THEN 'Corto plazo (2-3 anios)'
    ELSE 'Largo plazo (4+ anios)'
  END AS ventana_entrega,

  f.es_fila_agregada

FROM fechada f
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_airline` d
  ON d.airline_id = f.group_id
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_aircraft_type` m
  ON m.model = f.aircraft_model
