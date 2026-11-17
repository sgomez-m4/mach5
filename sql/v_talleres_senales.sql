CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_talleres_senales` AS
-- Cambios de un taller entre las dos ultimas descargas de FAA.
--
-- Son senales comerciales: un rating nuevo es capacidad que hay que llenar con
-- gente; un acuerdo bilateral nuevo trae auditorias y un suplemento al manual;
-- un Accountable Manager nuevo es un tomador de decisiones nuevo; un
-- certificado nuevo es un taller que se esta armando.
--
-- Con una sola descarga cargada la vista no devuelve filas. Empieza a servir
-- desde la segunda (cargar_faa.py conserva cada fecha).

WITH fechas AS (
  SELECT fecha_descarga, DENSE_RANK() OVER (ORDER BY fecha_descarga DESC) AS orden
  FROM (SELECT DISTINCT fecha_descarga
        FROM `mach5-gemini-project.dataset_prospeccion.faa_talleres`)
),

actual AS (
  SELECT t.* FROM `mach5-gemini-project.dataset_prospeccion.faa_talleres` t
  JOIN fechas f USING (fecha_descarga) WHERE f.orden = 1
),

anterior AS (
  SELECT t.* FROM `mach5-gemini-project.dataset_prospeccion.faa_talleres` t
  JOIN fechas f USING (fecha_descarga) WHERE f.orden = 2
),

-- Cada rating como texto comparable ("Estructura Class 3, Class 4"). Los
-- talleres extranjeros no tienen estado: la clave usa '' para que el cruce no
-- los tome por nuevos y retirados a la vez.
firmas AS (
  SELECT
    'actual' AS cual, cert_no, IFNULL(state, '') AS estado_clave, state, agency_name, am_name, fecha_descarga,
    bilateral_agreements,
    [IF(rating_airframe   IS NOT NULL, 'Estructura '   || rating_airframe,   NULL),
     IF(rating_powerplant IS NOT NULL, 'Motores '      || rating_powerplant, NULL),
     IF(rating_accessory  IS NOT NULL, 'Accesorios '   || rating_accessory,  NULL),
     IF(rating_instrument IS NOT NULL, 'Instrumentos ' || rating_instrument, NULL),
     IF(rating_radio      IS NOT NULL, 'Radio '        || rating_radio,      NULL),
     IF(rating_propeller  IS NOT NULL, 'Helices '      || rating_propeller,  NULL),
     IF(rating_limited    IS NOT NULL, 'Limitado '     || rating_limited,    NULL)] AS ratings
  FROM actual
  UNION ALL
  SELECT
    'anterior', cert_no, IFNULL(state, ''), state, agency_name, am_name, fecha_descarga,
    bilateral_agreements,
    [IF(rating_airframe   IS NOT NULL, 'Estructura '   || rating_airframe,   NULL),
     IF(rating_powerplant IS NOT NULL, 'Motores '      || rating_powerplant, NULL),
     IF(rating_accessory  IS NOT NULL, 'Accesorios '   || rating_accessory,  NULL),
     IF(rating_instrument IS NOT NULL, 'Instrumentos ' || rating_instrument, NULL),
     IF(rating_radio      IS NOT NULL, 'Radio '        || rating_radio,      NULL),
     IF(rating_propeller  IS NOT NULL, 'Helices '      || rating_propeller,  NULL),
     IF(rating_limited    IS NOT NULL, 'Limitado '     || rating_limited,    NULL)]
  FROM anterior
),

pares AS (
  SELECT
    cert_no,
    COALESCE(a.state, b.state)            AS state,
    COALESCE(a.agency_name, b.agency_name) AS agency_name,
    b.fecha_descarga AS fecha_anterior,
    a.fecha_descarga AS fecha_actual,
    a.ratings AS ratings_actual, b.ratings AS ratings_anterior,
    a.bilateral_agreements AS acuerdos_actual, b.bilateral_agreements AS acuerdos_anterior,
    a.am_name AS am_actual, b.am_name AS am_anterior,
    a.cert_no IS NOT NULL AS esta_hoy,
    b.cert_no IS NOT NULL AS estaba
  FROM (SELECT * FROM firmas WHERE cual = 'actual') a
  FULL OUTER JOIN (SELECT * FROM firmas WHERE cual = 'anterior') b
    USING (cert_no, estado_clave)
  -- Sin descarga anterior no hay con que comparar
  WHERE EXISTS (SELECT 1 FROM fechas WHERE orden = 2)
),

senales AS (
  SELECT cert_no, state, agency_name, fecha_anterior, fecha_actual,
         'Certificado nuevo' AS senal, NULL AS detalle
  FROM pares WHERE esta_hoy AND NOT estaba
  UNION ALL
  SELECT cert_no, state, agency_name, fecha_anterior, fecha_actual,
         'Certificado retirado', NULL
  FROM pares WHERE estaba AND NOT esta_hoy
  UNION ALL
  SELECT cert_no, state, agency_name, fecha_anterior, fecha_actual,
         'Rating nuevo o ampliado', r
  FROM pares, UNNEST(ratings_actual) r
  WHERE esta_hoy AND estaba AND r IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM UNNEST(ratings_anterior) x WHERE x = r)
  UNION ALL
  SELECT cert_no, state, agency_name, fecha_anterior, fecha_actual,
         'Rating retirado o reducido', r
  FROM pares, UNNEST(ratings_anterior) r
  WHERE esta_hoy AND estaba AND r IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM UNNEST(ratings_actual) x WHERE x = r)
  UNION ALL
  SELECT cert_no, state, agency_name, fecha_anterior, fecha_actual,
         'Acuerdo bilateral nuevo', a
  FROM pares, UNNEST(acuerdos_actual) a
  WHERE esta_hoy AND estaba AND NOT EXISTS (SELECT 1 FROM UNNEST(acuerdos_anterior) x WHERE x = a)
  UNION ALL
  SELECT cert_no, state, agency_name, fecha_anterior, fecha_actual,
         'Cambio de Accountable Manager',
         FORMAT('%s -> %s', IFNULL(am_anterior, '(vacio)'), IFNULL(am_actual, '(vacio)'))
  FROM pares
  WHERE esta_hoy AND estaba AND IFNULL(am_actual, '') != IFNULL(am_anterior, '')
)

SELECT s.*, c.en_zona_objetivo, c.nivel, c.tipo_dueno
FROM senales s
LEFT JOIN `mach5-gemini-project.dataset_prospeccion.v_talleres_calificacion` c
  USING (cert_no, state)
