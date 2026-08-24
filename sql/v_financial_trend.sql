-- financial_fact_history la crea normalizacion-financiera en su primera corrida,
-- pero este workflow puede aplicarse antes de que eso ocurra y CREATE VIEW falla
-- si la tabla no existe. Se garantiza aqui con el mismo esquema que financial_fact,
-- que es exactamente lo que escribe la funcion. Si ya existe, no se toca.
CREATE TABLE IF NOT EXISTS `mach5-gemini-project.dataset_integrado.financial_fact_history`
AS SELECT * FROM `mach5-gemini-project.dataset_integrado.financial_fact` WHERE FALSE;

CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_integrado.v_financial_trend` AS
-- Transicion de flota MEDIDA, no inferida.
--
-- v_prospect_score deduce la transicion de una foto estatica: mucha flota
-- arrendada, flota vieja, pedidos pendientes. Es la mejor señal disponible con
-- un solo ejercicio, pero sigue siendo una inferencia sobre un estado.
--
-- Esta vista mide el cambio. extraccion-xbrl-10k lee tres ejercicios de cada
-- filing y esa historia ahora sobrevive en financial_fact_history, asi que se
-- puede ver como se movio el pasivo por arrendamiento, la deuda y el capex.
-- Un pasivo por arrendamiento que sube 20% anual mientras los ingresos crecen
-- 3% no es una flota que crece: es una flota que se esta rotando hacia leasing,
-- que es exactamente el momento en que una consultora de asset management tiene
-- algo que vender.
--
-- Cobertura: solo las emisoras SEC tienen mas de un ejercicio, porque los
-- comparativos vienen dentro del propio XBRL. Las aerolineas chinas se extraen
-- de un reporte por anio y hoy solo hay 2025. Por eso esta vista es
-- COMPLEMENTARIA al score y no un componente suyo: meterla dentro penalizaria a
-- las 11 aerolineas asiaticas por falta de datos, no por falta de señal.

WITH serie AS (
  SELECT
    company,
    fiscal_year,
    canonical_metric,
    -- capex se emite con signo negativo (salida de caja, convencion us-gaap).
    -- Para medir tendencia interesa la magnitud, no el signo.
    IF(canonical_metric = 'capex', ABS(value_usd_m), value_usd_m) AS valor
  FROM `mach5-gemini-project.dataset_integrado.financial_fact_history`
  WHERE value_usd_m IS NOT NULL
    AND canonical_metric IN (
      'operating_lease_liability', 'lease_liability_total', 'total_debt',
      'capex', 'depreciation_amortization', 'revenue', 'ebitda',
      'net_debt_ratio', 'capex_depreciation_ratio'
    )
),

extremos AS (
  SELECT
    company,
    canonical_metric,
    COUNT(DISTINCT fiscal_year)                                      AS anios,
    MIN(fiscal_year)                                                 AS anio_ini,
    MAX(fiscal_year)                                                 AS anio_fin,
    ARRAY_AGG(valor ORDER BY fiscal_year)[SAFE_OFFSET(0)]            AS valor_ini,
    ARRAY_AGG(valor ORDER BY fiscal_year DESC)[SAFE_OFFSET(0)]       AS valor_fin
  FROM serie
  GROUP BY company, canonical_metric
),

variacion AS (
  SELECT
    company,
    canonical_metric,
    anios,
    anio_ini,
    anio_fin,
    valor_ini,
    valor_fin,
    -- Tasa anualizada. Sin ella una serie de dos anios y una de tres no son
    -- comparables. Solo tiene sentido si ambos extremos son positivos: una
    -- metrica que cruza cero no admite lectura porcentual.
    IF(anio_fin > anio_ini AND valor_ini > 0 AND valor_fin > 0,
       POW(valor_fin / valor_ini, 1.0 / (anio_fin - anio_ini)) - 1,
       NULL) AS var_anual
  FROM extremos
),

tendencia AS (
  SELECT
    company,
    MAX(anios)    AS anios_con_datos,
    MIN(anio_ini) AS anio_inicial,
    MAX(anio_fin) AS anio_final,

    MAX(IF(canonical_metric = 'operating_lease_liability', valor_ini, NULL)) AS lease_inicial,
    MAX(IF(canonical_metric = 'operating_lease_liability', valor_fin, NULL)) AS lease_final,
    MAX(IF(canonical_metric = 'operating_lease_liability', var_anual, NULL)) AS lease_var_anual,

    MAX(IF(canonical_metric = 'total_debt', valor_ini, NULL))  AS deuda_inicial,
    MAX(IF(canonical_metric = 'total_debt', valor_fin, NULL))  AS deuda_final,
    MAX(IF(canonical_metric = 'total_debt', var_anual, NULL))  AS deuda_var_anual,

    MAX(IF(canonical_metric = 'capex', valor_ini, NULL))       AS capex_inicial,
    MAX(IF(canonical_metric = 'capex', valor_fin, NULL))       AS capex_final,
    MAX(IF(canonical_metric = 'capex', var_anual, NULL))       AS capex_var_anual,

    MAX(IF(canonical_metric = 'revenue', valor_fin, NULL))     AS ingresos_final,
    MAX(IF(canonical_metric = 'revenue', var_anual, NULL))     AS ingresos_var_anual,

    MAX(IF(canonical_metric = 'capex_depreciation_ratio', valor_ini, NULL)) AS capex_dep_inicial,
    MAX(IF(canonical_metric = 'capex_depreciation_ratio', valor_fin, NULL)) AS capex_dep_final,

    MAX(IF(canonical_metric = 'net_debt_ratio', valor_fin, NULL)) AS net_debt_ebitda_final
  FROM variacion
  GROUP BY company
),

clasificada AS (
  SELECT
    t.*,
    -- Cuanto mas rapido crece el arrendamiento que el negocio. Un lease que
    -- sube al mismo ritmo que los ingresos es crecimiento; uno que sube mucho
    -- mas rapido es rotacion de flota hacia arrendamiento.
    SAFE_SUBTRACT(t.lease_var_anual, t.ingresos_var_anual) AS lease_sobre_ingresos,
    (t.anios_con_datos >= 2) AS es_medida
  FROM tendencia t
)

SELECT
  d.airline_id,
  d.group_id,
  d.display_name          AS aerolinea,
  d.region,
  d.country,
  d.business_model,
  c.company               AS financial_company,

  c.es_medida,
  c.anios_con_datos,
  c.anio_inicial,
  c.anio_final,

  ROUND(c.lease_inicial, 1)        AS lease_inicial_usd_m,
  ROUND(c.lease_final, 1)          AS lease_final_usd_m,
  ROUND(c.lease_var_anual, 4)      AS lease_var_anual,
  ROUND(c.lease_sobre_ingresos, 4) AS lease_sobre_ingresos,

  ROUND(c.deuda_final, 1)          AS deuda_final_usd_m,
  ROUND(c.deuda_var_anual, 4)      AS deuda_var_anual,

  ROUND(c.capex_final, 1)          AS capex_final_usd_m,
  ROUND(c.capex_var_anual, 4)      AS capex_var_anual,
  ROUND(c.capex_dep_inicial, 3)    AS capex_dep_inicial,
  ROUND(c.capex_dep_final, 3)      AS capex_dep_final,

  ROUND(c.ingresos_var_anual, 4)   AS ingresos_var_anual,
  ROUND(c.net_debt_ebitda_final, 2) AS net_debt_ebitda,

  -- La lectura comercial. El orden importa: se evalua primero el movimiento del
  -- arrendamiento, que es la linea de servicio priorizada, y solo despues la
  -- renovacion via capital propio.
  CASE
    WHEN NOT c.es_medida THEN 'Sin serie: un solo ejercicio'
    WHEN c.lease_var_anual >= 0.15
         AND (c.lease_sobre_ingresos IS NULL OR c.lease_sobre_ingresos >= 0.05)
      THEN 'Entrada de flota arrendada'
    WHEN c.lease_var_anual <= -0.10
      THEN 'Devolucion de flota arrendada'
    WHEN c.capex_dep_final >= 1.20
         AND (c.lease_var_anual IS NULL OR ABS(c.lease_var_anual) < 0.10)
      THEN 'Renovacion con capital propio'
    WHEN c.capex_dep_final <= 0.70
      THEN 'Sin renovacion: flota envejeciendo'
    ELSE 'Estable'
  END AS senal_transicion,

  -- Prioridad de contacto, para ordenar la lista. No se mezcla con el score de
  -- v_prospect_score: son dos lecturas distintas del mismo prospecto y el
  -- consultor deberia ver ambas.
  CASE
    WHEN NOT c.es_medida THEN NULL
    WHEN c.lease_var_anual >= 0.15
         AND (c.lease_sobre_ingresos IS NULL OR c.lease_sobre_ingresos >= 0.05) THEN 1
    WHEN c.lease_var_anual <= -0.10 THEN 2
    WHEN c.capex_dep_final <= 0.70 THEN 3
    WHEN c.capex_dep_final >= 1.20 THEN 4
    ELSE 5
  END AS prioridad_transicion

FROM clasificada c
JOIN `mach5-gemini-project.dataset_integrado.dim_airline` d
  ON d.financial_company = c.company
 AND d.parent_id IS NULL
ORDER BY prioridad_transicion, ABS(IFNULL(lease_var_anual, 0)) DESC
