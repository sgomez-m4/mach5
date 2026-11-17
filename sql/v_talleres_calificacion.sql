CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_talleres_calificacion` AS
-- Calificacion de talleres Part 145 como prospectos de personal tercerizado
-- (presencial y remoto) y de backoffice.
--
-- FAA no publica tamano, plantilla, clientes ni finanzas de los talleres. Los
-- puntajes son indicios de capacidad y de carga regulatoria, no una medicion
-- de la necesidad: un taller con estructura clase 4 y aprobacion EASA tiene mas
-- probabilidad de necesitar tecnicos certificados y gente de records que uno
-- con un rating de radio, pero no se sabe si hoy le faltan.
--
-- Clases de rating (14 CFR 145.59):
--   Estructura   1 compuesta pequena, 2 compuesta grande, 3 metalica pequena,
--                4 metalica grande. Aviones grandes = clase 2 o 4.
--   Motores      1 reciproco <= 400 hp, 2 reciproco > 400 hp, 3 turbina.
--   Helices      1 paso fijo, 2 paso variable.
--   Radio        1 comunicacion, 2 navegacion, 3 radar.
--   Instrumentos 1 mecanico, 2 electrico, 3 giroscopico, 4 electronico.
--   Accesorios   1 mecanico, 2 electrico, 3 electronico.
-- "Limited" es un rating limitado a marcas y modelos concretos. No dice tamano:
-- muchos talleres de linea de aviones grandes solo tienen estructura Limited.
--
-- Se publican todos los talleres de EE. UU. con la bandera en_zona_objetivo,
-- para que el informe filtre sin cambiar la vista.

WITH parametros AS (
  SELECT
    -- Sur de EE. UU. definido con el usuario el 2026-11-17 (Arizona incluido)
    ['TX','OK','AR','LA','MS','AL','TN','GA','FL','SC','NC','AZ'] AS estados_objetivo,
    -- Umbrales de nivel. Backoffice pide mas porque casi cualquier taller
    -- independiente con estructura llega a 35 (mediana del sur: 38); 50 es
    -- el percentil 75.
    60 AS umbral_a,
    35 AS umbral_personal_b,
    50 AS umbral_backoffice_b
),

ultima AS (
  SELECT t.*
  FROM `mach5-gemini-project.dataset_prospeccion.faa_talleres` t
  WHERE t.fecha_descarga = (SELECT MAX(fecha_descarga)
                            FROM `mach5-gemini-project.dataset_prospeccion.faa_talleres`)
    AND t.country = 'UNITED STATES'
),

base AS (
  SELECT
    u.*,
    LOWER(REGEXP_EXTRACT(COALESCE(u.am_email, u.liaison_email, u.agency_email), r'@([^@\s]+)$')) AS dominio
  FROM ultima u
),

con_dominio AS (
  SELECT
    b.*,
    (p.dominio IS NOT NULL) AS correo_personal
  FROM base b
  LEFT JOIN `mach5-gemini-project.dataset_prospeccion.talleres_correo_personal` p
    ON p.dominio = b.dominio
),

-- Tipo de dueno: la regla de menor prioridad que aplique
dueno AS (
  SELECT
    c.cert_no, c.state,
    ARRAY_AGG(r.tipo_dueno ORDER BY r.prioridad LIMIT 1)[OFFSET(0)] AS tipo_dueno
  FROM con_dominio c
  JOIN `mach5-gemini-project.dataset_prospeccion.talleres_reglas_dueno` r
    ON (r.tipo_regla = 'dominio'       AND NOT c.correo_personal AND c.dominio = r.valor)
    OR (r.tipo_regla = 'dominio_regex' AND NOT c.correo_personal AND REGEXP_CONTAINS(IFNULL(c.dominio, ''), r.valor))
    OR (r.tipo_regla = 'nombre_regex'  AND REGEXP_CONTAINS(UPPER(c.agency_name), r.valor))
  GROUP BY c.cert_no, c.state
),

-- Empresa a la que pertenece el certificado: el dominio corporativo si lo hay;
-- si no, las dos primeras palabras del nombre sin sufijos societarios. Sirve
-- para no contactar seis veces a la misma empresa y como indicio de tamano.
grupo AS (
  SELECT
    c.cert_no, c.state,
    IF(c.dominio IS NOT NULL AND NOT c.correo_personal, c.dominio,
       ARRAY_TO_STRING(ARRAY(
         SELECT w FROM UNNEST(SPLIT(REGEXP_REPLACE(UPPER(c.agency_name),
                 r'[^A-Z0-9 ]|\b(INC|LLC|CORP|CORPORATION|CO|COMPANY|LTD|LP|THE|DBA)\b', ' '), ' ')) AS w WITH OFFSET o
         WHERE w != '' ORDER BY o LIMIT 2), ' ')) AS grupo_empresa
  FROM con_dominio c
),

grupo_tamano AS (
  SELECT grupo_empresa, COUNT(*) AS certificados_grupo
  FROM grupo
  GROUP BY grupo_empresa
),

capacidades AS (
  SELECT
    c.*,
    IFNULL(d.tipo_dueno, 'Independiente') AS tipo_dueno,
    g.grupo_empresa,
    gt.certificados_grupo,

    (2 IN UNNEST(c.airframe_clases) OR 4 IN UNNEST(c.airframe_clases)) AS estructura_grandes,
    (ARRAY_LENGTH(c.airframe_clases) > 0 OR c.airframe_limited)        AS tiene_estructura,
    (3 IN UNNEST(c.powerplant_clases))                                 AS motores_turbina,
    (ARRAY_LENGTH(c.powerplant_clases) > 0 OR c.powerplant_limited)    AS tiene_motores,
    (IF(ARRAY_LENGTH(c.accessory_clases) > 0 OR c.accessory_limited, 1, 0)
     + IF(ARRAY_LENGTH(c.instrument_clases) > 0 OR c.instrument_limited, 1, 0)
     + IF(ARRAY_LENGTH(c.radio_clases) > 0 OR c.radio_limited, 1, 0)
     + IF(ARRAY_LENGTH(c.propeller_clases) > 0 OR c.propeller_limited, 1, 0)) AS n_componentes,
    ('NonDestructiveTst' IN UNNEST(c.limited_especialidades))          AS ndt,
    ('Landing Gear' IN UNNEST(c.limited_especialidades))               AS tren_aterrizaje,
    EXISTS (SELECT 1 FROM UNNEST(c.bilateral_agreements) a WHERE a IN ('EASA', 'United Kingdom')) AS aprobacion_europea,
    ARRAY_LENGTH(c.bilateral_agreements)                               AS n_acuerdos,
    (IF(c.rating_accessory IS NOT NULL, 1, 0) + IF(c.rating_airframe IS NOT NULL, 1, 0)
     + IF(c.rating_instrument IS NOT NULL, 1, 0) + IF(c.rating_limited IS NOT NULL, 1, 0)
     + IF(c.rating_powerplant IS NOT NULL, 1, 0) + IF(c.rating_propeller IS NOT NULL, 1, 0)
     + IF(c.rating_radio IS NOT NULL, 1, 0))                           AS n_categorias_rating
  FROM con_dominio c
  LEFT JOIN dueno d       USING (cert_no, state)
  LEFT JOIN grupo g       USING (cert_no, state)
  LEFT JOIN grupo_tamano gt ON gt.grupo_empresa = g.grupo_empresa
),

puntajes AS (
  SELECT
    k.*,
    CASE
      WHEN k.estructura_grandes                    THEN 'Aeronave completa: aviones grandes'
      WHEN k.tiene_estructura                      THEN 'Aeronave completa'
      WHEN k.tiene_motores                         THEN 'Motores'
      WHEN k.n_componentes > 0                     THEN 'Componentes y avionica'
      WHEN ARRAY_LENGTH(k.limited_especialidades) > 0 THEN 'Servicios especializados'
      ELSE 'Sin rating publicado'
    END AS tipo_taller,

    -- Personal tecnico (presencial y remoto): capacidad para trabajos que piden
    -- tecnicos certificados en cantidad
    GREATEST(0, LEAST(100,
        CASE WHEN k.estructura_grandes THEN 30
             WHEN ARRAY_LENGTH(k.airframe_clases) > 0 THEN 15
             WHEN k.airframe_limited THEN 12 ELSE 0 END
      + CASE WHEN k.motores_turbina THEN 20
             WHEN k.tiene_motores THEN 8 ELSE 0 END
      + LEAST(4 * k.n_componentes, 12)
      + IF(k.ndt, 6, 0) + IF(k.tren_aterrizaje, 4, 0)
      + IF(k.aprobacion_europea, 10, 0)
      + IF('TCCA (Canada)' IN UNNEST(k.bilateral_agreements), 4, 0)
      + CASE WHEN k.certificados_grupo >= 3 THEN 8
             WHEN k.certificados_grupo = 2 THEN 4 ELSE 0 END
      - IF(k.correo_personal, 10, 0)
    )) AS puntaje_personal,

    -- Backoffice (planeacion, records, calidad y cumplimiento, remoto): carga
    -- documental y regulatoria, y que no tenga un corporativo que la absorba
    GREATEST(0, LEAST(100,
        LEAST(12 * k.n_acuerdos, 36)
      + LEAST(4 * k.n_categorias_rating, 24)
      + IF(k.tiene_estructura, 15, 0)
      + IF(k.tipo_dueno IN ('Independiente', 'Grupo MRO'), 15, 0)
      + IF(k.certificados_grupo >= 3, 10, 0)
      - IF(k.correo_personal, 5, 0)
    )) AS puntaje_backoffice
  FROM capacidades k
)

SELECT
  p.cert_no,
  p.dsgn_code,
  p.agency_name,
  p.dba,
  p.city,
  p.state,
  p.zip5,
  z.lat,
  z.lon,
  p.state IN UNNEST(par.estados_objetivo) AS en_zona_objetivo,

  p.tipo_taller,
  p.tipo_dueno,
  p.grupo_empresa,
  p.certificados_grupo,
  p.correo_personal,

  p.puntaje_personal,
  p.puntaje_backoffice,
  CASE
    WHEN p.tipo_dueno = 'Gobierno, defensa o educacion' THEN 'Excluido'
    WHEN GREATEST(p.puntaje_personal, p.puntaje_backoffice) >= par.umbral_a THEN 'A'
    WHEN p.puntaje_personal >= par.umbral_personal_b
      OR p.puntaje_backoffice >= par.umbral_backoffice_b THEN 'B'
    ELSE 'C'
  END AS nivel,
  CASE
    WHEN p.puntaje_personal >= par.umbral_personal_b
     AND p.puntaje_backoffice >= par.umbral_backoffice_b THEN 'Personal + backoffice'
    WHEN p.puntaje_personal >= par.umbral_personal_b   THEN 'Personal tecnico'
    WHEN p.puntaje_backoffice >= par.umbral_backoffice_b THEN 'Backoffice'
    ELSE 'Sin oferta clara'
  END AS oferta_sugerida,
  -- OEM y aerolineas compran por areas de abastecimiento corporativas: es otra
  -- forma de venta que la de un taller independiente
  CASE
    WHEN p.tipo_dueno IN ('Fabricante (OEM)', 'Aerolinea u operador') THEN 'Cuenta corporativa'
    WHEN p.tipo_dueno = 'Gobierno, defensa o educacion' THEN 'No aplica'
    ELSE 'Venta directa'
  END AS canal,

  ARRAY_TO_STRING(ARRAY(
    SELECT m FROM UNNEST([
      IF(p.estructura_grandes, 'estructura de aviones grandes (clase 2 o 4)', NULL),
      IF(p.motores_turbina, 'motores de turbina', NULL),
      IF(p.aprobacion_europea, 'aprobado por EASA o Reino Unido', NULL),
      IF(p.n_acuerdos > 0 AND NOT p.aprobacion_europea, 'acuerdo bilateral', NULL),
      IF(p.ndt, 'ensayos no destructivos', NULL),
      IF(p.n_categorias_rating >= 4, FORMAT('%d categorias de rating', p.n_categorias_rating), NULL),
      IF(p.certificados_grupo >= 2, FORMAT('%d certificados del mismo grupo', p.certificados_grupo), NULL),
      IF(p.correo_personal, 'correo personal: taller muy pequeno', NULL)
    ]) AS m WHERE m IS NOT NULL
  ), '; ') AS motivo,

  -- Contacto: el Accountable Manager es quien decide; el Liaison es el enlace
  -- con FAA y suele tener el correo
  p.am_name,
  p.am_email,
  p.am_phone,
  p.liaison_name,
  p.liaison_email,
  p.liaison_phone,
  p.agency_phone,
  p.agency_email,
  COALESCE(p.am_email, p.liaison_email, p.agency_email) AS correo_contacto,
  COALESCE(p.am_phone, p.liaison_phone, p.agency_phone) AS telefono_contacto,

  -- Capacidades, para filtrar en el informe
  p.airframe_clases,
  p.powerplant_clases,
  p.limited_especialidades,
  p.bilateral_agreements,
  p.n_categorias_rating,
  p.rating_airframe,
  p.rating_powerplant,
  p.rating_accessory,
  p.rating_instrument,
  p.rating_radio,
  p.rating_propeller,
  p.rating_limited,
  p.fecha_descarga
FROM puntajes p
CROSS JOIN parametros par
LEFT JOIN `mach5-gemini-project.dataset_prospeccion.geo_zip` z
  ON z.zip_code = p.zip5
