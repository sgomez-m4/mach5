CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_talleres_ratings` AS
-- Un renglon por taller y rating, para filtrar en Power BI ("talleres con
-- motores de turbina", "con ensayos no destructivos"). Se une a
-- v_talleres_informe por clave_taller.
--
-- Clases segun 14 CFR 145.59. Las especialidades del rating Limited salen de
-- la columna de especialidades; "Limitado" a secas en una categoria significa
-- que el alcance esta en la lista de capacidades, que FAA no publica.

WITH t AS (
  SELECT CONCAT(cert_no, '|', IFNULL(state, '')) AS clave_taller, *
  FROM `mach5-gemini-project.dataset_prospeccion.v_talleres_calificacion`
),

tokens AS (
  -- Helices llega como "1, 2" en vez de "Class 1, Class 2"
  SELECT clave_taller, categoria,
         REGEXP_REPLACE(TRIM(tok), r'^(\d)$', r'Class \1') AS tok
  FROM t,
       UNNEST([
         STRUCT('Estructura' AS categoria, rating_airframe AS valor),
         ('Motores',      rating_powerplant),
         ('Helices',      rating_propeller),
         ('Radio',        rating_radio),
         ('Instrumentos', rating_instrument),
         ('Accesorios',   rating_accessory)]) r,
       UNNEST(SPLIT(r.valor, ',')) tok
  WHERE r.valor IS NOT NULL
)

SELECT
  clave_taller,
  categoria,
  CASE
    WHEN tok = 'Limited' THEN 'Limitado'
    WHEN categoria = 'Estructura' THEN CASE tok
      WHEN 'Class 1' THEN 'Clase 1: compuesta, aeronave pequena'
      WHEN 'Class 2' THEN 'Clase 2: compuesta, aeronave grande'
      WHEN 'Class 3' THEN 'Clase 3: metalica, aeronave pequena'
      WHEN 'Class 4' THEN 'Clase 4: metalica, aeronave grande' END
    WHEN categoria = 'Motores' THEN CASE tok
      WHEN 'Class 1' THEN 'Clase 1: piston de 400 hp o menos'
      WHEN 'Class 2' THEN 'Clase 2: piston de mas de 400 hp'
      WHEN 'Class 3' THEN 'Clase 3: turbina' END
    WHEN categoria = 'Helices' THEN CASE tok
      WHEN 'Class 1' THEN 'Clase 1: paso fijo o ajustable en tierra'
      WHEN 'Class 2' THEN 'Clase 2: las demas' END
    WHEN categoria = 'Radio' THEN CASE tok
      WHEN 'Class 1' THEN 'Clase 1: comunicaciones'
      WHEN 'Class 2' THEN 'Clase 2: navegacion'
      WHEN 'Class 3' THEN 'Clase 3: radar' END
    WHEN categoria = 'Instrumentos' THEN CASE tok
      WHEN 'Class 1' THEN 'Clase 1: mecanicos'
      WHEN 'Class 2' THEN 'Clase 2: electricos'
      WHEN 'Class 3' THEN 'Clase 3: giroscopicos'
      WHEN 'Class 4' THEN 'Clase 4: electronicos' END
    WHEN categoria = 'Accesorios' THEN CASE tok
      WHEN 'Class 1' THEN 'Clase 1: mecanicos'
      WHEN 'Class 2' THEN 'Clase 2: electricos'
      WHEN 'Class 3' THEN 'Clase 3: electronicos' END
  END AS rating,
  CASE WHEN tok = 'Limited' THEN 9 ELSE SAFE_CAST(REGEXP_EXTRACT(tok, r'(\d)') AS INT64) END AS orden
FROM tokens

UNION ALL

SELECT
  clave_taller,
  'Especialidad limitada',
  CASE e
    WHEN 'NonDestructiveTst'  THEN 'Ensayos no destructivos'
    WHEN 'SpecializedService' THEN 'Servicio especializado'
    WHEN 'Landing Gear'       THEN 'Tren de aterrizaje'
    WHEN 'Equipment'          THEN 'Equipos'
    WHEN 'Rotor Blades'       THEN 'Palas de rotor'
    WHEN 'Floats'             THEN 'Flotadores'
    WHEN 'Aircraft Fabric'    THEN 'Entelado'
    ELSE e
  END,
  NULL
FROM t, UNNEST(limited_especialidades) e
