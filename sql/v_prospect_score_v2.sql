CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_prospect_score_v2` AS
-- Prospeccion v2: niveles A/B/C explicados en aeronaves, en paralelo a
-- v_prospect_score para poder compararlas antes de cambiar el informe.
--
-- Que cambia frente a v1 y por que:
--
-- 1. La unidad es la aeronave, no un indice de 0 a 100. v1 sumaba cinco
--    componentes ponderados y el rango quedaba entre 14 y 47, con los tres
--    primeros separados por 0,3 puntos: una diferencia que el dato no sostiene.
--    Aqui cada grupo dice cuantas aeronaves estan en juego en cada linea de
--    servicio y en que plazo, y el nivel sale de ese conteo.
--
-- 2. El vencimiento de leasing multiplica a la exposicion en vez de sumarse.
--    En v1 Allegiant, sin un solo avion arrendado, sacaba 13 puntos de leasing
--    porque sus arrendamientos de otros activos vencian pronto. Ahora lo que
--    vence se aplica solo sobre aeronaves en leasing operativo.
--
-- 3. La escalera de pagos se convierte en contratos que vencen. Que el 45% de
--    los pagos caiga en tres anos no significa que el 45% de los contratos
--    venza: un contrato a diez anos tambien paga sus tres primeros. Ver el
--    bloque `leasing` para la conversion.
--
-- 4. Las entregas se cuentan en una ventana movil de 24 meses desde hoy, no
--    contra un anio fijo (v1 usaba <= 2027), y los rangos ("2026-2034",
--    "After 2027") se reparten entre sus anios en vez de caer enteros en el
--    primero. United pasaba con 634 de 634 pedidos "proximos".
--
-- 5. Solo cuenta el order book del ultimo reporte de cada grupo. American trae
--    filas de 2024 y 2025 y v1 sumaba las dos: 104 aviones de mas.
--
-- 6. La ventana de reemplazo se proyecta sobre la flota sin dato de edad y se
--    marca la cobertura. En v1 un grupo sin edades salia con 0 aviones a
--    reemplazar, que se leia igual que una flota joven.
--
-- 7. El apalancamiento deja de sumar puntos y pasa a bandera de riesgo de
--    cobro: un balance tensionado puede ser necesidad (sale-leaseback,
--    renegociacion) pero tambien riesgo de no cobrar, y eso lo decide una
--    persona, no un peso. La complejidad pasa a descriptor por la misma razon:
--    con 23 de 24 grupos mezclando generaciones ya no distinguia a nadie.
--
-- La vista depende de CURRENT_DATE(): la ventana avanza sola y el resultado de
-- hoy no es el de dentro de seis meses. Es intencional.

WITH

parametros AS (
  SELECT
    CURRENT_DATE()                       AS hoy,
    EXTRACT(YEAR  FROM CURRENT_DATE())   AS anio_hoy,
    EXTRACT(MONTH FROM CURRENT_DATE())   AS mes_hoy,
    -- Horizonte asumido para "2029 and Thereafter" y similares: las entregas se
    -- reparten en cinco anios desde el primero citado. Sin este supuesto los 421
    -- aviones "After 2027" de United caen todos en 2028.
    5                                    AS anios_horizonte_abierto,
    -- Umbrales de nivel. En aeronaves: 15 en tres anios son cinco por anio, una
    -- carga continua para un equipo; 50 ya es un programa. En proporcion de
    -- flota: sin ella, cualquier aerolinea grande llega a 50 con un 5% de su
    -- flota en movimiento, y el nivel A deja de distinguir (paso: 15 de 24).
    15                                   AS umbral_material,
    50                                   AS umbral_programa,
    0.25                                 AS pct_nivel_a,
    0.40                                 AS pct_nivel_a_chico,
    0.15                                 AS pct_nivel_b,
    -- Diferencia maxima entre los aviones de B-43 y los del informe para usar
    -- la edad por avion de B-43 en lugar del promedio del informe
    0.20                                 AS tolerancia_alcance_b43
),

-- ---------------------------------------------------------------------------
-- Flota
-- ---------------------------------------------------------------------------
-- Las filas sin edad no son una muestra al azar: son sobre todo flotas
-- regionales afiliadas (E175 nuevos junto a CRJ y ERJ viejos) y la flota de
-- FedEx. Proyectar con la proporcion de la linea principal infla o desinfla
-- segun el caso, asi que se imputa por generacion del modelo: a cada fila sin
-- edad se le aplica la tasa de ventana que esa generacion tiene en las filas
-- con edad de todo el universo.
tasa_ventana_generacion AS (
  SELECT
    generation,
    SAFE_DIVIDE(
      SUM(IF(fleet_replacement_status IN ('Replace Now', 'Critical'), quantity, 0)),
      SUM(quantity)) AS tasa
  FROM `mach5-gemini-project.dataset_integrado.v_fleet_normalized`
  WHERE NOT es_fila_agregada AND average_age_years IS NOT NULL
  GROUP BY generation
),

flota AS (
  SELECT
    group_id,
    ANY_VALUE(grupo)             AS grupo,
    ANY_VALUE(region)            AS region,
    ANY_VALUE(country)           AS country,
    ANY_VALUE(business_model)    AS business_model,
    ANY_VALUE(financial_company) AS financial_company,

    SUM(quantity) AS flota_total,
    SUM(IF(tipo_propiedad = 'Operating Lease', quantity, 0)) AS aeronaves_leasing_operativo,
    SUM(IF(tipo_propiedad = 'Finance Lease',   quantity, 0)) AS aeronaves_leasing_financiero,

    -- Vencimiento por avion, donde el reporte lo trae. remaining_lease_years es
    -- el promedio de la fila: si una fila de 40 aviones promedia 4 anios, no es
    -- que ninguno venza en tres. Se asume vencimiento repartido de forma pareja
    -- entre 0 y el doble del promedio, y la fraccion que cae en tres anios es
    -- 3 / (2 * promedio).
    SUM(IF(tipo_propiedad = 'Operating Lease' AND remaining_lease_years IS NOT NULL,
           quantity, 0)) AS op_lease_con_vencimiento,
    SUM(IF(tipo_propiedad = 'Operating Lease' AND remaining_lease_years IS NOT NULL,
           quantity * IF(remaining_lease_years <= 0, 1.0,
                         LEAST(3 / (2 * remaining_lease_years), 1.0)),
           0)) AS op_lease_vence_3a_por_avion,

    -- Reemplazo tecnico. Solo sobre filas con edad: lo demas es desconocido.
    SUM(IF(average_age_years IS NOT NULL, quantity, 0)) AS aeronaves_con_edad,
    SUM(IF(fleet_replacement_status IN ('Replace Now', 'Critical'), quantity, 0)) AS ventana_observada,
    SUM(IF(fleet_replacement_status = 'Monitor', quantity, 0)) AS en_monitoreo,
    SAFE_DIVIDE(SUM(quantity * average_age_years),
                SUM(IF(average_age_years IS NULL, 0, quantity))) AS edad_ponderada,

    -- Descriptores de complejidad (ya no puntuan)
    COUNT(DISTINCT aircraft_model) AS n_modelos,
    COUNT(DISTINCT manufacturer)   AS n_fabricantes,
    COUNTIF(v.generation = 'previous') > 0 AND COUNTIF(v.generation = 'current') > 0 AS mezcla_generaciones
  FROM `mach5-gemini-project.dataset_integrado.v_fleet_normalized` v
  WHERE NOT es_fila_agregada
  GROUP BY group_id
),

-- Edad por avion de Form 41 (B-43), donde corresponde al mismo alcance.
--
-- Los informes dan una edad promedio por fila, y el promedio esconde las colas
-- viejas: los E175 de Republic promedian 11,8 anios, asi que la fila queda
-- fuera de ventana, pero 82 de sus 189 aviones tienen 17 o mas. B-43 da el anio
-- de fabricacion de cada avion de las aerolineas de EE. UU.
--
-- Se aplica por entidad (no por modelo: los nombres de B-43 y de los informes
-- no siempre coinciden) y solo si el alcance coincide:
--   a) entidad con edad en el informe: B-43 la reemplaza si su numero de
--      aviones esta a +-20% de las filas con edad (tolerancia_alcance_b43).
--      Asi United usa B-43 para su flota principal (1.058 contra 1.066) y no
--      para sus regionales, que operan SkyWest o Republic bajo otro
--      certificado. Republic (251 contra 311) y SkyWest (581 contra 487)
--      difieren 19%: con 15% quedarian fuera y Republic pasaria de 178 aviones
--      en ventana a 0. La proporcion se escala al numero del informe.
--   b) entidad sin ninguna edad en el informe: B-43 cubre si tiene al menos la
--      mitad de sus aviones. Son las regionales de American y FedEx.
-- Fuera de esos casos queda el informe, y los aviones sin edad se imputan por
-- generacion.
b43_entidad AS (
  SELECT airline_id, COUNT(*) AS aviones, COUNTIF(en_ventana) AS en_ventana
  FROM `mach5-gemini-project.dataset_prospeccion.v_flota_b43`
  WHERE en_flota_reportada
    AND year = (SELECT MAX(year) FROM `mach5-gemini-project.dataset_prospeccion.form41_b43`)
  GROUP BY airline_id
),

informe_entidad AS (
  SELECT
    v.group_id,
    v.airline_id,
    SUM(IF(v.average_age_years IS NOT NULL, v.quantity, 0)) AS con_edad,
    SUM(IF(v.fleet_replacement_status IN ('Replace Now', 'Critical'), v.quantity, 0)) AS ventana_informe,
    SUM(IF(v.average_age_years IS NULL, v.quantity, 0)) AS sin_edad,
    SUM(IF(v.average_age_years IS NULL, v.quantity * IFNULL(t.tasa, 0), 0)) AS ventana_por_generacion
  FROM `mach5-gemini-project.dataset_integrado.v_fleet_normalized` v
  LEFT JOIN tasa_ventana_generacion t USING (generation)
  WHERE NOT v.es_fila_agregada
  GROUP BY 1, 2
),

regla_entidad AS (
  SELECT
    e.*,
    b.aviones AS aviones_b43,
    b.en_ventana AS en_ventana_b43,
    (e.con_edad > 0 AND b.aviones IS NOT NULL
     AND ABS(b.aviones - e.con_edad) <= par.tolerancia_alcance_b43 * e.con_edad) AS b43_con_edad,
    (e.con_edad = 0 AND e.sin_edad > 0 AND b.aviones >= 0.5 * e.sin_edad) AS b43_sin_edad
  FROM informe_entidad e
  LEFT JOIN b43_entidad b USING (airline_id)
  CROSS JOIN parametros par
),

imputacion AS (
  SELECT
    group_id,
    SUM(
      IF(b43_con_edad, con_edad * en_ventana_b43 / aviones_b43, ventana_informe)
      + IF(b43_sin_edad, sin_edad * en_ventana_b43 / aviones_b43, ventana_por_generacion)
    ) AS ventana_total,
    SUM(ventana_informe) AS ventana_segun_informe,
    SUM(IF(b43_con_edad, con_edad, 0)) AS con_edad_con_b43,
    SUM(IF(b43_sin_edad, sin_edad, 0)) AS sin_edad_con_b43,
    SUM(IF(b43_sin_edad, 0, sin_edad)) AS sin_edad_por_generacion
  FROM regla_entidad
  GROUP BY group_id
),

-- ---------------------------------------------------------------------------
-- Entregas en ventana movil de 24 meses
-- ---------------------------------------------------------------------------
pedidos_ultimo_reporte AS (
  SELECT * EXCEPT (ultimo_reporte)
  FROM (
    SELECT o.*, MAX(o.report_year) OVER (PARTITION BY o.group_id) AS ultimo_reporte
    FROM `mach5-gemini-project.dataset_prospeccion.v_pedidos_vigentes` o
  )
  WHERE report_year = ultimo_reporte
),

pedidos_con_rango AS (
  SELECT
    p.group_id,
    p.quantity_committed AS q,
    p.anio_primera_entrega AS anio_inicio,
    CASE
      WHEN p.anio_primera_entrega IS NULL THEN NULL
      -- "2026-2034": el segundo anio cierra el rango
      WHEN REGEXP_CONTAINS(p.delivery_period, r'20[2-4][0-9]\s*-\s*20[2-4][0-9]')
        THEN SAFE_CAST(REGEXP_EXTRACT(p.delivery_period, r'20[2-4][0-9]\s*-\s*(20[2-4][0-9])') AS INT64)
      -- "Up to 2029", "Through 2028": el anio citado es el final
      WHEN REGEXP_CONTAINS(p.delivery_period, r'(?i)up to|through')
        THEN SAFE_CAST(REGEXP_EXTRACT(p.delivery_period, r'(20[2-4][0-9])') AS INT64)
      -- "After 2027", "2028 and beyond", "2029 and Thereafter": sin cierre
      WHEN REGEXP_CONTAINS(p.delivery_period, r'(?i)after|beyond|thereafter')
        THEN p.anio_primera_entrega + par.anios_horizonte_abierto - 1
      ELSE p.anio_primera_entrega
    END AS anio_fin
  FROM pedidos_ultimo_reporte p
  CROSS JOIN parametros par
),

pedidos AS (
  SELECT
    r.group_id,
    SUM(r.q) AS pedidos_total,
    SUM(IF(r.anio_inicio IS NULL, r.q, 0)) AS pedidos_sin_fecha,
    -- Cada anio del rango recibe q / n_anios, y de cada anio cuenta la
    -- fraccion de sus meses que cae entre hoy y dentro de 24 meses.
    SUM(
      IF(r.anio_inicio IS NULL, 0,
        (SELECT SUM(
                  r.q / (r.anio_fin - r.anio_inicio + 1)
                  * CASE
                      WHEN y = par.anio_hoy     THEN (12 - par.mes_hoy + 1) / 12
                      WHEN y = par.anio_hoy + 1 THEN 1.0
                      WHEN y = par.anio_hoy + 2 THEN (par.mes_hoy - 1) / 12
                      ELSE 0.0
                    END)
         FROM UNNEST(GENERATE_ARRAY(r.anio_inicio, GREATEST(r.anio_fin, r.anio_inicio))) AS y))
    ) AS entregas_24m
  FROM pedidos_con_rango r
  CROSS JOIN parametros par
  GROUP BY r.group_id
),

-- ---------------------------------------------------------------------------
-- Finanzas: escalera de leasing y banderas de riesgo
-- ---------------------------------------------------------------------------
fin AS (
  SELECT
    company,
    MAX(IF(canonical_metric = 'lease_maturity_concentracion_3y', value_usd_m, NULL)) AS conc_pagos_3a,
    MAX(IF(canonical_metric = 'lease_maturity_concentracion_1y', value_usd_m, NULL)) AS conc_pasivo_1a,
    MAX(IF(canonical_metric = 'net_debt_ratio',  value_usd_m, NULL)) AS net_debt_ebitda,
    MAX(IF(canonical_metric = 'ebitda',          value_usd_m, NULL)) AS ebitda,
    MAX(IF(canonical_metric = 'equity',          value_usd_m, NULL)) AS patrimonio,
    MAX(IF(canonical_metric = 'revenue',         value_usd_m, NULL)) AS revenue,
    MAX(IF(canonical_metric = 'operating_margin', value_usd_m, NULL)) AS operating_margin
  FROM `mach5-gemini-project.dataset_integrado.financial_fact`
  GROUP BY company
),

base AS (
  SELECT
    f.*,
    IFNULL(im.ventana_total, 0)           AS ventana_total,
    IFNULL(im.ventana_segun_informe, 0)   AS ventana_segun_informe,
    IFNULL(im.con_edad_con_b43, 0)        AS con_edad_con_b43,
    IFNULL(im.sin_edad_con_b43, 0)        AS sin_edad_con_b43,
    IFNULL(im.sin_edad_por_generacion, 0) AS sin_edad_por_generacion,
    IFNULL(p.pedidos_total, 0)     AS pedidos_total,
    IFNULL(p.pedidos_sin_fecha, 0) AS pedidos_sin_fecha,
    IFNULL(p.entregas_24m, 0)      AS entregas_24m,
    fn.conc_pagos_3a,
    fn.conc_pasivo_1a,
    fn.net_debt_ebitda,
    fn.ebitda,
    fn.patrimonio,
    fn.revenue,
    fn.operating_margin
  FROM flota f
  LEFT JOIN imputacion im ON im.group_id = f.group_id
  LEFT JOIN pedidos p ON p.group_id = f.group_id
  LEFT JOIN fin fn    ON fn.company = f.financial_company
),

-- ---------------------------------------------------------------------------
-- Leasing: de la escalera de pagos a contratos que vencen
-- ---------------------------------------------------------------------------
-- Modelo: plazos remanentes repartidos de forma pareja entre 0 y 2m (m es el
-- plazo medio), cada contrato paga lo mismo cada anio. La parte de los pagos
-- que cae en los primeros h anios es s = (2mh - h^2/2) / (2m^2). Despejando m
-- y tomando la fraccion de contratos que vence en 3 anios, 3 / (2m):
--
--   con la escalera a 3 anios (h = 3):  vence_3a = s3 / (1 + sqrt(1 - s3))
--   con el pasivo corriente  (h = 1):  vence_3a = 3 * s1 / (1 + sqrt(1 - s1))
--
-- Con los datos actuales ambas dan cifras coherentes: American 26% por la
-- escalera y 24% por el pasivo corriente. La escalera incluye arrendamientos que
-- no son aviones (hangares, terminales), normalmente mas largos, asi que la
-- cifra es mas bien conservadora.
leasing_medido AS (
  SELECT
    b.*,
    CASE
      WHEN b.op_lease_con_vencimiento >= 0.5 * b.aeronaves_leasing_operativo
       AND b.aeronaves_leasing_operativo > 0                THEN 'Por avion (reporte de flota)'
      WHEN b.conc_pagos_3a  IS NOT NULL                     THEN 'Escalera de pagos a 3 anios'
      WHEN b.conc_pasivo_1a IS NOT NULL                     THEN 'Pasivo corriente de leasing'
      ELSE 'Sin dato'
    END AS fuente_vencimiento,
    CASE
      WHEN b.op_lease_con_vencimiento >= 0.5 * b.aeronaves_leasing_operativo
       AND b.aeronaves_leasing_operativo > 0
        -- Se extrapola a las filas sin vencimiento con la misma proporcion
        THEN b.op_lease_vence_3a_por_avion / b.op_lease_con_vencimiento
      WHEN b.conc_pagos_3a IS NOT NULL
        THEN LEAST(b.conc_pagos_3a / (1 + SQRT(1 - LEAST(b.conc_pagos_3a, 1.0))), 1.0)
      WHEN b.conc_pasivo_1a IS NOT NULL
        THEN LEAST(3 * b.conc_pasivo_1a / (1 + SQRT(1 - LEAST(b.conc_pasivo_1a, 1.0))), 1.0)
      ELSE NULL
    END AS frac_vence_3a_medida
  FROM base b
),

-- Sin ningun dato de vencimiento (las aerolineas chinas, que no lo desglosan)
-- dejar cero hacia pasar a Hainan, con 249 aviones en leasing operativo, por
-- "sin senal". Se usa la mediana de los grupos que si lo reportan, y la
-- confianza baja lo deja a la vista para verificar a mano.
mediana_vencimiento AS (
  SELECT APPROX_QUANTILES(frac_vence_3a_medida, 2)[OFFSET(1)] AS mediana
  FROM leasing_medido
  WHERE frac_vence_3a_medida IS NOT NULL AND aeronaves_leasing_operativo > 0
),

leasing AS (
  SELECT
    lm.* EXCEPT (fuente_vencimiento),
    CASE
      WHEN lm.aeronaves_leasing_operativo = 0 THEN 'No aplica: sin leasing operativo'
      WHEN lm.fuente_vencimiento = 'Sin dato' THEN 'Supuesto: mediana del universo'
      ELSE lm.fuente_vencimiento
    END AS fuente_vencimiento,
    IF(lm.aeronaves_leasing_operativo = 0, NULL,
       IFNULL(lm.frac_vence_3a_medida, mv.mediana)) AS frac_vence_3a
  FROM leasing_medido lm
  CROSS JOIN mediana_vencimiento mv
),

-- ---------------------------------------------------------------------------
-- Aeronaves en juego por linea de servicio
-- ---------------------------------------------------------------------------
lineas AS (
  SELECT
    l.*,
    SAFE_DIVIDE(l.aeronaves_con_edad, l.flota_total) AS cobertura_edad,
    -- Edad conocida por el informe o por el inventario B-43
    SAFE_DIVIDE(l.aeronaves_con_edad + l.sin_edad_con_b43, l.flota_total) AS cobertura_edad_efectiva,

    -- Leasing: aviones en leasing operativo cuyo contrato venceria en 3 anios
    ROUND(l.aeronaves_leasing_operativo * l.frac_vence_3a) AS leasing_vence_3a,

    -- Tecnico: ventana con edad por avion (B-43) o del informe, mas la
    -- imputada por generacion donde no hay ninguna edad
    ROUND(l.ventana_total) AS ventana_reemplazo_est,

    ROUND(l.entregas_24m) AS entregas_24m_redondeo
  FROM leasing l
),

total AS (
  SELECT
    x.*,
    -- Las entregas suelen reemplazar justo a los aviones en ventana: sumar las
    -- dos contaria dos veces la misma transicion. Se toma la mayor.
    GREATEST(IFNULL(x.ventana_reemplazo_est, 0), x.entregas_24m_redondeo) AS tecnico_en_juego,
    IFNULL(x.leasing_vence_3a, 0)
      + GREATEST(IFNULL(x.ventana_reemplazo_est, 0), x.entregas_24m_redondeo) AS aeronaves_en_juego
  FROM lineas x
),

clasificado AS (
  SELECT
    t.*,
    SAFE_DIVIDE(t.aeronaves_en_juego, t.flota_total) AS pct_flota_en_juego,

    -- A: un programa que ademas mueve una parte relevante de la flota, o una
    --    aerolinea chica con casi la mitad de su flota en juego.
    -- B: un programa aunque sea pequeno frente a la flota, o carga continua que
    --    pesa en la flota.
    -- C: el resto.
    CASE
      WHEN (t.aeronaves_en_juego >= par.umbral_programa
            AND SAFE_DIVIDE(t.aeronaves_en_juego, t.flota_total) >= par.pct_nivel_a)
        OR (t.aeronaves_en_juego >= par.umbral_material
            AND SAFE_DIVIDE(t.aeronaves_en_juego, t.flota_total) >= par.pct_nivel_a_chico) THEN 'A'
      WHEN t.aeronaves_en_juego >= par.umbral_programa
        OR (t.aeronaves_en_juego >= par.umbral_material
            AND SAFE_DIVIDE(t.aeronaves_en_juego, t.flota_total) >= par.pct_nivel_b)       THEN 'B'
      ELSE 'C'
    END AS nivel,

    CASE
      WHEN IFNULL(t.leasing_vence_3a, 0) >= par.umbral_material
       AND t.tecnico_en_juego            >= par.umbral_material THEN 'Leasing + Tecnica'
      WHEN IFNULL(t.leasing_vence_3a, 0) >= t.tecnico_en_juego
       AND IFNULL(t.leasing_vence_3a, 0) > 0                    THEN 'Leasing'
      WHEN t.tecnico_en_juego > 0                               THEN 'Tecnica'
      ELSE 'Sin senal'
    END AS linea_principal,

    -- Confianza del dato detras del nivel, no de la oportunidad
    CASE
      -- El supuesto de vencimiento solo baja la confianza si mueve el nivel: con
      -- 3 aviones en leasing (FedEx) no cambia nada
      WHEN (t.fuente_vencimiento = 'Supuesto: mediana del universo'
            AND t.aeronaves_leasing_operativo >= par.umbral_material)
        OR IFNULL(t.cobertura_edad_efectiva, 0) < 0.5 THEN 'Baja'
      WHEN t.fuente_vencimiento = 'Pasivo corriente de leasing'
        OR t.cobertura_edad_efectiva < 0.9
        OR SAFE_DIVIDE(t.pedidos_sin_fecha, NULLIF(t.pedidos_total, 0)) > 0.25 THEN 'Media'
      ELSE 'Alta'
    END AS confianza_dato,

    ARRAY_TO_STRING(ARRAY(
      SELECT b FROM UNNEST([
        IF(t.ebitda < 0,             'EBITDA negativo', NULL),
        IF(t.patrimonio < 0,         'Patrimonio negativo', NULL),
        IF(t.net_debt_ebitda > 5,    'Deuda neta / EBITDA > 5x', NULL),
        IF(t.financial_company IS NULL, 'Sin datos financieros', NULL)
      ]) AS b WHERE b IS NOT NULL
    ), '; ') AS alertas_riesgo
  FROM total t
  CROSS JOIN parametros par
)

SELECT
  group_id,
  grupo,
  region,
  country,
  business_model,
  flota_total,
  CASE
    WHEN flota_total >= 500 THEN 'L'
    WHEN flota_total >= 150 THEN 'M'
    ELSE 'S'
  END AS tier_tamano,

  nivel,
  linea_principal,
  confianza_dato,
  -- Orden dentro del nivel: por aeronaves en juego. No es un puntaje; dos
  -- grupos con 60 y 58 aviones en juego son equivalentes a efectos comerciales.
  RANK() OVER (ORDER BY nivel, aeronaves_en_juego DESC) AS orden,

  -- El porque, en una linea que se puede leer en voz alta en una llamada
  ARRAY_TO_STRING(ARRAY(
    SELECT m FROM UNNEST([
      IF(leasing_vence_3a > 0,
         FORMAT('%d de %d aviones en leasing operativo vencerian en 3 anios%s',
                CAST(leasing_vence_3a AS INT64), aeronaves_leasing_operativo,
                IF(fuente_vencimiento = 'Supuesto: mediana del universo',
                   ' (supuesto: no reporta vencimientos)', '')), NULL),
      IF(entregas_24m_redondeo > 0,
         FORMAT('entregas en 24 meses: %d', CAST(entregas_24m_redondeo AS INT64)), NULL),
      IF(ventana_reemplazo_est > 0,
         FORMAT('%d aviones en ventana de reemplazo%s', CAST(ventana_reemplazo_est AS INT64),
                CASE
                  WHEN con_edad_con_b43 + sin_edad_con_b43 > 0 AND sin_edad_por_generacion > 0
                    THEN FORMAT(' (edad por avion de Form 41 para %d; %d sin edad, imputados por generacion)',
                                con_edad_con_b43 + sin_edad_con_b43, CAST(sin_edad_por_generacion AS INT64))
                  WHEN con_edad_con_b43 + sin_edad_con_b43 > 0
                    THEN FORMAT(' (edad por avion de Form 41 para %d)', con_edad_con_b43 + sin_edad_con_b43)
                  WHEN sin_edad_por_generacion > 0
                    THEN FORMAT(' (%d sin edad, imputados por generacion)',
                                CAST(sin_edad_por_generacion AS INT64))
                  ELSE ''
                END), NULL)
    ]) AS m WHERE m IS NOT NULL
  ), '; ') AS motivo,

  -- Aeronaves en juego, por linea
  CAST(aeronaves_en_juego AS INT64)          AS aeronaves_en_juego,
  ROUND(100 * pct_flota_en_juego, 0)         AS pct_flota_en_juego,
  CAST(leasing_vence_3a AS INT64)            AS leasing_vence_3a,
  CAST(tecnico_en_juego AS INT64)            AS tecnico_en_juego,
  CAST(ventana_reemplazo_est AS INT64)       AS ventana_reemplazo_est,
  ventana_segun_informe,
  en_monitoreo,
  CAST(entregas_24m_redondeo AS INT64)       AS entregas_24m,

  -- Exposicion y fuentes
  aeronaves_leasing_operativo,
  aeronaves_leasing_financiero,
  ROUND(100 * SAFE_DIVIDE(aeronaves_leasing_operativo, flota_total), 0) AS pct_leasing_operativo,
  fuente_vencimiento,
  ROUND(100 * frac_vence_3a, 0)              AS pct_leasing_vence_3a,
  ROUND(100 * cobertura_edad, 0)             AS pct_cobertura_edad,
  ROUND(100 * cobertura_edad_efectiva, 0)    AS pct_cobertura_edad_con_form41,
  con_edad_con_b43 + sin_edad_con_b43        AS aviones_edad_form41,
  ROUND(edad_ponderada, 1)                   AS edad_ponderada,
  pedidos_total,
  pedidos_sin_fecha,

  -- Descriptores (no puntuan)
  n_modelos,
  n_fabricantes,
  mezcla_generaciones,

  -- Riesgo de cobro (no puntua)
  NULLIF(alertas_riesgo, '')                 AS alertas_riesgo,
  ROUND(net_debt_ebitda, 2)                  AS net_debt_ebitda,
  ROUND(ebitda, 0)                           AS ebitda_usdm,
  ROUND(revenue, 0)                          AS revenue_usdm,
  ROUND(100 * operating_margin, 1)           AS margen_operativo_pct,

  (SELECT hoy FROM parametros)               AS fecha_calculo
FROM clasificado
