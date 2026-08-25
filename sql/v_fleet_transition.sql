-- fleet_history_fact la puebla ejecutar-pipeline-anuales, pero este workflow puede
-- aplicarse antes de que corra y CREATE VIEW falla si la tabla no existe. Se
-- garantiza con el mismo esquema que current_fleet_fact, que es lo que escribe la
-- funcion. Si ya existe, no se toca.
CREATE TABLE IF NOT EXISTS `mach5-gemini-project.dataset_integrado.fleet_history_fact`
AS SELECT * FROM `mach5-gemini-project.dataset_integrado.current_fleet_fact` WHERE FALSE;

CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_integrado.v_fleet_transition` AS
-- Movimiento de flota observado, modelo por modelo.
--
-- v_financial_trend mide la transicion por el lado del balance: cuanto se movio el
-- pasivo por arrendamiento, la deuda, el capex. Esta la mide por el lado del
-- activo, que es lo que la consultora va a discutir en la reunion: que entro, que
-- salio y que se esta reemplazando por que.
--
-- La fuente son las tablas de flota que publican varios cierres anuales en columnas
-- contiguas. No todas las aerolineas lo hacen -las que reportan un solo ejercicio
-- no aparecen aqui- asi que igual que v_financial_trend esta es una vista
-- COMPLEMENTARIA al score y no un componente suyo: penalizaria por falta de datos
-- a quien no publica la comparativa, no por falta de señal.

WITH base AS (
  SELECT
    h.group_id,
    h.report_year,
    -- El modelo canonico y no el texto crudo: "B737-8 MAX" y "737 MAX 8" son el
    -- mismo activo y sin normalizar apareceria uno entrando y otro saliendo.
    `mach5-gemini-project.dataset_integrado.normalizar_modelo_aeronave`(h.aircraft_type) AS modelo,
    h.quantity
  FROM `mach5-gemini-project.dataset_integrado.fleet_history_fact` h
  WHERE h.airline_id IS NOT NULL
    AND NOT h.es_fila_agregada
    AND h.quantity IS NOT NULL
),

por_anio AS (
  SELECT group_id, modelo, report_year, SUM(quantity) AS aeronaves
  FROM base
  GROUP BY 1, 2, 3
),

-- Solo tiene sentido comparar donde hay mas de un cierre
grupos_con_serie AS (
  SELECT group_id,
         COUNT(DISTINCT report_year) AS ejercicios,
         MIN(report_year) AS anio_inicial,
         MAX(report_year) AS anio_final
  FROM por_anio
  GROUP BY 1
  HAVING COUNT(DISTINCT report_year) >= 2
),

-- Un modelo puede no aparecer en un extremo: si no esta, la cantidad es cero, no
-- un hueco. Esa es justo la señal de entrada o salida de flota.
extremos AS (
  SELECT
    g.group_id,
    g.ejercicios,
    g.anio_inicial,
    g.anio_final,
    p.modelo,
    IFNULL(SUM(IF(p.report_year = g.anio_inicial, p.aeronaves, 0)), 0) AS inicial,
    IFNULL(SUM(IF(p.report_year = g.anio_final,   p.aeronaves, 0)), 0) AS final
  FROM grupos_con_serie g
  JOIN por_anio p USING (group_id)
  GROUP BY 1, 2, 3, 4, 5
)

SELECT
  d.airline_id,
  e.group_id,
  d.display_name AS aerolinea,
  d.region,
  d.business_model,

  e.ejercicios,
  e.anio_inicial,
  e.anio_final,

  e.modelo               AS aircraft_model,
  m.manufacturer,
  m.category,
  m.generation,

  e.inicial              AS aeronaves_inicial,
  e.final                AS aeronaves_final,
  e.final - e.inicial    AS variacion,
  SAFE_DIVIDE(e.final - e.inicial, NULLIF(e.inicial, 0)) AS variacion_pct,

  -- Lo que el consultor lee de un vistazo. Entrada y salida se separan de
  -- crecimiento y contraccion porque son conversaciones distintas: un tipo que
  -- aparece de cero implica introduccion de flota, transicion de records y
  -- entrenamiento; uno que crece 20% es una ampliacion del mismo contrato.
  CASE
    WHEN e.inicial = 0 AND e.final > 0 THEN 'Entra a la flota'
    WHEN e.inicial > 0 AND e.final = 0 THEN 'Sale de la flota'
    WHEN e.final > e.inicial           THEN 'Crece'
    WHEN e.final < e.inicial           THEN 'Se reduce'
    ELSE 'Estable'
  END AS movimiento

FROM extremos e
JOIN `mach5-gemini-project.dataset_integrado.dim_airline` d
  ON d.group_id = e.group_id
 AND d.parent_id IS NULL
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_aircraft_type` m
  ON m.model = e.modelo
WHERE e.inicial != e.final OR e.final > 0
ORDER BY e.group_id, ABS(e.final - e.inicial) DESC
