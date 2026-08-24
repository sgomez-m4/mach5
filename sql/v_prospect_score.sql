CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_integrado.v_prospect_score` AS
-- Ranking de prospectos para una consultora de asset management aeronautico,
-- ponderado hacia sus dos lineas de servicio: gestion de leasing y gestion
-- tecnica de activos.
--
-- Los componentes se normalizan contra anclas absolutas, no contra percentiles
-- de la muestra. Con percentiles el score de una aerolinea cambiaria al agregar
-- o quitar otras, lo que hace imposible comparar entre corridas; con anclas, un
-- 70 significa lo mismo hoy que el trimestre que viene.
--
-- Cada componente queda expuesto junto a sus drivers: el consultor tiene que
-- poder ver POR QUE alguien es prospecto, no solo su posicion en la lista.
WITH flota AS (
  SELECT
    group_id,
    grupo,
    region,
    country,
    business_model,
    ANY_VALUE(financial_company) AS financial_company,

    SUM(quantity) AS flota_total,

    -- Exposicion a arrendamiento. ownership_type esta poblado al 100%, asi que
    -- es la señal mas confiable de todo el modelo.
    SAFE_DIVIDE(SUM(IF(tipo_propiedad = 'Operating Lease', quantity, 0)), SUM(quantity)) AS pct_operating_lease,
    SAFE_DIVIDE(SUM(IF(tipo_propiedad = 'Finance Lease',  quantity, 0)), SUM(quantity)) AS pct_finance_lease,
    SAFE_DIVIDE(SUM(IF(es_arrendada, quantity, 0)),        SUM(quantity)) AS pct_arrendada,

    -- Presion de reemplazo tecnico
    SAFE_DIVIDE(
      SUM(IF(fleet_replacement_status IN ('Replace Now', 'Critical'), quantity, 0)),
      SUM(quantity)
    ) AS pct_ventana_reemplazo,

    -- Edad y vida remanente ponderadas por cantidad. El modelo de Power BI usaba
    -- AVERAGE simple, con lo que una fila de 3 aeronaves pesaba igual que una de 300.
    SAFE_DIVIDE(
      SUM(quantity * average_age_years),
      SUM(IF(average_age_years IS NULL, 0, quantity))
    ) AS edad_ponderada,
    SAFE_DIVIDE(
      SUM(quantity * remaining_replacement_life),
      SUM(IF(remaining_replacement_life IS NULL, 0, quantity))
    ) AS vida_remanente_ponderada,

    -- Complejidad del portafolio: cuantas plataformas distintas hay que gestionar
    COUNT(DISTINCT aircraft_model)  AS n_modelos,
    COUNT(DISTINCT manufacturer)    AS n_fabricantes,
    COUNT(DISTINCT category)        AS n_categorias,
    -- Convivencia de generaciones dentro de una misma familia (ceo junto a neo,
    -- NG junto a MAX): duplica procesos tecnicos, repuestos y entrenamiento.
    COUNTIF(generation = 'previous') > 0 AND COUNTIF(generation = 'current') > 0 AS mezcla_generaciones,

    SUM(IF(es_fila_agregada, quantity, 0)) AS aeronaves_en_filas_agregadas
  FROM `mach5-gemini-project.dataset_integrado.v_fleet_normalized`
  WHERE report_year = 2025
    AND NOT es_fila_agregada
  GROUP BY group_id, grupo, region, country, business_model
),

pedidos AS (
  -- delivery_period es texto libre inconsistente ("2026", "After 2028",
  -- "2029 and Thereafter", "2026-2029+"). Se extrae el primer anio de cuatro
  -- digitos como inicio de entrega, que basta para ponderar por proximidad.
  SELECT
    d.group_id,
    SUM(o.quantity_committed) AS pedidos_total,
    SUM(IF(SAFE_CAST(REGEXP_EXTRACT(o.delivery_period, r'(20\d{2})') AS INT64) <= 2027,
           o.quantity_committed, 0)) AS pedidos_proximos_2a
  FROM `mach5-gemini-project.dataset_integrado.order_book_fact` o
  JOIN `mach5-gemini-project.dataset_integrado.dim_airline` d
    ON d.airline_id = o.group_id
  WHERE o.report_year = 2025
  GROUP BY d.group_id
),

fin AS (
  SELECT
    company,
    MAX(IF(canonical_metric = 'lease_maturity_concentracion_3y', value_usd_m, NULL)) AS lease_conc_3y,
    MAX(IF(canonical_metric = 'lease_maturity_concentracion_1y', value_usd_m, NULL)) AS lease_conc_1y,
    MAX(IF(canonical_metric = 'lease_maturity_total',            value_usd_m, NULL)) AS lease_venc_total,
    MAX(IF(canonical_metric = 'lease_maturity_y1',               value_usd_m, NULL)) AS lease_venc_y1,
    MAX(IF(canonical_metric = 'net_debt_ratio',                  value_usd_m, NULL)) AS net_debt_ebitda,
    MAX(IF(canonical_metric = 'revenue',                         value_usd_m, NULL)) AS revenue,
    MAX(IF(canonical_metric = 'operating_margin',                value_usd_m, NULL)) AS operating_margin
  FROM `mach5-gemini-project.dataset_integrado.financial_fact`
  GROUP BY company
),

base AS (
  SELECT
    f.*,
    IFNULL(p.pedidos_total, 0)       AS pedidos_total,
    IFNULL(p.pedidos_proximos_2a, 0) AS pedidos_proximos_2a,
    SAFE_DIVIDE(IFNULL(p.pedidos_total, 0), f.flota_total) AS ratio_pedidos_flota,
    fn.lease_conc_3y,
    fn.lease_conc_1y,
    fn.lease_venc_total,
    fn.lease_venc_y1,
    fn.net_debt_ebitda,
    fn.revenue,
    fn.operating_margin
  FROM flota f
  LEFT JOIN pedidos p ON p.group_id = f.group_id
  LEFT JOIN fin fn    ON fn.company = f.financial_company
),

componentes AS (
  SELECT
    *,

    -- (1) EXPOSICION A LEASING  — linea de servicio: gestion de arrendamientos.
    -- El arrendamiento operativo pesa mas que el financiero: genera devoluciones,
    -- condiciones de retorno y renegociaciones, que es donde entra la consultora.
    -- La urgencia de vencimiento se toma de la escalera a 3 anios cuando existe.
    -- Solo us-gaap la etiqueta por anio: bajo IFRS el analisis va dentro de un
    -- text block. Para esas emisoras se usa la proporcion que vence a 12 meses,
    -- que sale del split corriente / no corriente y si esta en ambas taxonomias.
    -- Se reescala por 2.5 porque un acantilado a un anio pesa mas que el mismo
    -- porcentaje repartido en tres. Sin ninguna de las dos, el peso se reparte
    -- entre la composicion de la flota en vez de asumir cero.
    ROUND(
      CASE
        WHEN lease_conc_3y IS NOT NULL THEN
          100 * (0.50 * LEAST(pct_operating_lease, 1.0)
               + 0.20 * LEAST(pct_finance_lease, 1.0)
               + 0.30 * LEAST(lease_conc_3y, 1.0))
        WHEN lease_conc_1y IS NOT NULL THEN
          100 * (0.50 * LEAST(pct_operating_lease, 1.0)
               + 0.20 * LEAST(pct_finance_lease, 1.0)
               + 0.30 * LEAST(lease_conc_1y * 2.5, 1.0))
        ELSE
          100 * (0.71 * LEAST(pct_operating_lease, 1.0)
               + 0.29 * LEAST(pct_finance_lease, 1.0))
      END, 1) AS score_leasing,

    -- (2) PRESION DE REEMPLAZO — linea de servicio: gestion tecnica de activos.
    -- Se ancla en la proporcion de flota dentro de la ventana de reemplazo y en
    -- la vida remanente, escalada contra 10 anios: por encima de eso la urgencia
    -- tecnica es baja y el score satura en 0.
    ROUND(
      100 * (0.65 * LEAST(pct_ventana_reemplazo, 1.0)
           + 0.35 * (1 - LEAST(IFNULL(vida_remanente_ponderada, 10) / 10, 1.0))
      ), 1) AS score_reemplazo,

    -- (3) COMPLEJIDAD DE PORTAFOLIO — cuanto trabajo tecnico implica la flota.
    -- 12 modelos y 4 fabricantes se toman como techo: por encima la complejidad
    -- ya es maxima en terminos operativos.
    ROUND(
      100 * (0.45 * LEAST(n_modelos / 12, 1.0)
           + 0.35 * LEAST((n_fabricantes - 1) / 3, 1.0)
           + 0.20 * IF(mezcla_generaciones, 1.0, 0.0)
      ), 1) AS score_complejidad,

    -- (4) ACTIVIDAD DE TRANSICION — un pedido entrante implica salida de flota
    -- vieja, y con ella devoluciones y transiciones tecnicas. Se pondera la
    -- proximidad: lo que entra en los proximos dos anios ya es trabajo en curso.
    ROUND(
      100 * (0.60 * LEAST(IFNULL(ratio_pedidos_flota, 0) / 0.5, 1.0)
           + 0.40 * LEAST(SAFE_DIVIDE(pedidos_proximos_2a, flota_total) / 0.2, 1.0)
      ), 1) AS score_transicion,

    -- (5) CONTEXTO FINANCIERO — modificador, no motor. Un balance apalancado
    -- empuja hacia sale-leaseback y reestructura; uno holgado, hacia compra.
    -- Ancla: net_debt/EBITDA de 6x se considera tension alta.
    ROUND(
      100 * LEAST(GREATEST(IFNULL(net_debt_ebitda, 2.5), 0) / 6, 1.0)
    , 1) AS score_apalancamiento

  FROM base
)

SELECT
  grupo,
  region,
  country,
  business_model,
  flota_total,

  -- Tamano de la oportunidad, separado del ajuste. Un score alto sobre 26
  -- aeronaves y uno alto sobre 1,500 describen encargos de escala muy distinta,
  -- y mezclarlos en un solo numero hace que las grandes tapen a las que mejor
  -- encajan con el servicio.
  CASE
    WHEN flota_total >= 500 THEN 'L'
    WHEN flota_total >= 150 THEN 'M'
    ELSE 'S'
  END AS tier_tamano,

  -- Que linea de servicio explica el prospecto. El score global responde "cuanto"
  -- encaja; esto responde "en que", que es lo que decide quien de la firma hace
  -- la llamada.
  CASE
    WHEN score_leasing >= 45 AND score_reemplazo >= 25 THEN 'Leasing + Tecnico'
    WHEN score_leasing >= 45                            THEN 'Leasing'
    WHEN score_reemplazo >= 25                          THEN 'Tecnico / Reemplazo'
    WHEN score_transicion >= 60                         THEN 'Transicion de flota'
    WHEN score_complejidad >= 70                        THEN 'Complejidad de portafolio'
    ELSE 'Sin señal dominante'
  END AS segmento_prospecto,

  -- Score global. Los pesos siguen las lineas de servicio priorizadas: leasing y
  -- reemplazo tecnico mandan, complejidad y transicion aportan, y el
  -- apalancamiento solo matiza.
  ROUND(
    0.35 * score_leasing
  + 0.30 * score_reemplazo
  + 0.15 * score_complejidad
  + 0.12 * score_transicion
  + 0.08 * score_apalancamiento
  , 1) AS prospect_score,

  score_leasing,
  score_reemplazo,
  score_complejidad,
  score_transicion,
  score_apalancamiento,

  -- Drivers, para poder justificar la conversacion comercial
  ROUND(100 * pct_operating_lease, 0)   AS pct_operating_lease,
  ROUND(100 * pct_finance_lease, 0)     AS pct_finance_lease,
  ROUND(100 * pct_ventana_reemplazo, 0) AS pct_ventana_reemplazo,
  ROUND(edad_ponderada, 1)              AS edad_ponderada,
  ROUND(vida_remanente_ponderada, 1)    AS vida_remanente,
  n_modelos,
  n_fabricantes,
  mezcla_generaciones,
  pedidos_total,
  pedidos_proximos_2a,
  ROUND(100 * ratio_pedidos_flota, 0)   AS pct_renovacion,
  ROUND(100 * lease_conc_3y, 0)         AS pct_venc_leasing_3a,
  ROUND(100 * lease_conc_1y, 0)         AS pct_venc_leasing_1a,
  ROUND(lease_venc_total, 0)            AS leasing_venc_total_usdm,
  ROUND(net_debt_ebitda, 2)             AS net_debt_ebitda,
  ROUND(revenue, 0)                     AS revenue_usdm,

  -- Banderas de calidad: sin esto un score bajo por falta de dato se confunde
  -- con un score bajo por ausencia real de oportunidad.
  (vida_remanente_ponderada IS NULL) AS sin_dato_edad,
  (lease_conc_3y IS NULL AND lease_conc_1y IS NULL) AS sin_dato_vencimientos,
  (financial_company IS NULL)        AS sin_datos_financieros,
  aeronaves_en_filas_agregadas

FROM componentes
ORDER BY prospect_score DESC;
