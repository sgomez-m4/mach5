CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_mercado_mantenimiento` AS
-- Cuanto mantenimiento compra a terceros cada aerolinea de EE. UU., desde DOT
-- Form 41 Schedule P-5.2 (ultimos cuatro trimestres publicados).
--
-- Es el contexto de mercado para los talleres: quien gasta en reparaciones
-- hechas fuera y cuanto pesa eso frente a su mano de obra propia.
--
-- Cuentas (14 CFR 241.12, verificado el 17-nov-2026):
--   52251 / 52252  mano de obra propia de estructura / motores
--   52431 / 52432  reparaciones de estructura / motores. Son la cuenta 43,
--                  "General Services Purchased": servicios prestados por
--                  companias externas o asociadas (las asociadas son filiales,
--                  asi que no todo es mercado abierto)
--   52461 / 52462  materiales
--   52780          total de mantenimiento directo
-- Montos de Form 41 en miles de USD; aqui se pasan a millones.

WITH trimestres AS (
  SELECT DISTINCT year, quarter
  FROM `mach5-gemini-project.dataset_prospeccion.form41_p52`
  QUALIFY DENSE_RANK() OVER (ORDER BY year DESC, quarter DESC) <= 4
),

p52 AS (
  SELECT
    p.unique_carrier,
    ANY_VALUE(p.unique_carrier_name)                          AS aerolinea,
    ANY_VALUE(p.carrier_group_new)                            AS carrier_group,
    MIN(FORMAT('%d-T%d', p.year, p.quarter))                  AS desde,
    MAX(FORMAT('%d-T%d', p.year, p.quarter))                  AS hasta,
    SUM(IFNULL(p.airframe_labor, 0) + IFNULL(p.engine_labor, 0)) / 1000      AS mano_obra_propia_usdm,
    SUM(IFNULL(p.airframe_repair, 0)) / 1000                                 AS reparaciones_estructura_usdm,
    SUM(IFNULL(p.engine_repairs, 0)) / 1000                                  AS reparaciones_motores_usdm,
    SUM(IFNULL(p.airframe_materials, 0) + IFNULL(p.engine_materials, 0)) / 1000 AS materiales_usdm,
    SUM(IFNULL(p.tot_dir_maint, 0)) / 1000                                   AS mantenimiento_directo_usdm,
    SUM(IFNULL(p.total_air_hours, 0))                                        AS horas_vuelo_miles
  FROM `mach5-gemini-project.dataset_prospeccion.form41_p52` p
  JOIN trimestres USING (year, quarter)
  GROUP BY p.unique_carrier
),

empleados AS (
  SELECT unique_carrier, SUM(maintenance) AS empleados_mantenimiento
  FROM `mach5-gemini-project.dataset_prospeccion.form41_p10`
  WHERE year = (SELECT MAX(year) FROM `mach5-gemini-project.dataset_prospeccion.form41_p10`)
  GROUP BY unique_carrier
)

SELECT
  p.unique_carrier,
  p.aerolinea,
  c.group_id,
  p.carrier_group,
  p.desde,
  p.hasta,
  ROUND(p.mano_obra_propia_usdm, 1)        AS mano_obra_propia_usdm,
  ROUND(p.reparaciones_estructura_usdm, 1) AS reparaciones_estructura_usdm,
  ROUND(p.reparaciones_motores_usdm, 1)    AS reparaciones_motores_usdm,
  ROUND(p.reparaciones_estructura_usdm + p.reparaciones_motores_usdm, 1) AS reparaciones_compradas_usdm,
  ROUND(p.materiales_usdm, 1)              AS materiales_usdm,
  ROUND(p.mantenimiento_directo_usdm, 1)   AS mantenimiento_directo_usdm,
  -- Que parte del trabajo (mano de obra + reparaciones) se compra fuera
  ROUND(100 * SAFE_DIVIDE(p.reparaciones_estructura_usdm + p.reparaciones_motores_usdm,
                          p.reparaciones_estructura_usdm + p.reparaciones_motores_usdm
                          + p.mano_obra_propia_usdm), 0) AS pct_trabajo_comprado,
  e.empleados_mantenimiento,
  ROUND(SAFE_DIVIDE(p.mantenimiento_directo_usdm * 1e6, p.horas_vuelo_miles * 1000), 0) AS usd_mantenimiento_por_hora
FROM p52 p
LEFT JOIN empleados e USING (unique_carrier)
LEFT JOIN `mach5-gemini-project.dataset_prospeccion.dim_operador_certificado` c
  ON c.dot_unique_carrier = p.unique_carrier
WHERE p.mantenimiento_directo_usdm > 0
