CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_flota_b43` AS
-- Inventario por avion de DOT Form 41, Schedule B-43, unido a dim_airline por el
-- puente dim_operador_certificado.
--
-- Complementa a v_fleet_normalized, que sale de los informes anuales: estos
-- traen cantidades por modelo y una edad promedio que no siempre publican (las
-- regionales de American, toda la flota de FedEx). B-43 trae el anio de
-- fabricacion y la propiedad de cada avion de las aerolineas de EE. UU.
--
-- El alcance no coincide con el del informe: B-43 es por certificado y el
-- informe por grupo. United incluye en su flota regionales que operan SkyWest
-- o Republic; esos aviones estan en B-43 bajo el certificado del operador, no
-- bajo United. Por eso esta vista no reemplaza a v_fleet_normalized: se usa por
-- entidad donde el informe no trae edad.
--
-- Los codigos de modelo de B-43 ("737-823", "CL-600-2D24", "ERJ170-200LR") no
-- son los nombres comerciales de los informes y normalizar_modelo_aeronave los
-- clasifica mal (lee 737-823 como MAX y A321-253NX como ceo), asi que aqui hay
-- reglas propias.

WITH b AS (
  SELECT
    b.*,
    -- Sin espacios ni parentesis, sin prefijo de fabricante y con el
    -- "AA321" mal escrito corregido
    REGEXP_REPLACE(
      REGEXP_REPLACE(UPPER(REGEXP_REPLACE(b.model, r'[\s()]', '')), r'^(BOEING|AIRBUS|CESSNA)', ''),
      r'^AA3', 'A3') AS m
  FROM `mach5-gemini-project.dataset_prospeccion.form41_b43` b
),

normalizado AS (
  SELECT
    b.*,
    CASE
      WHEN REGEXP_CONTAINS(m, r'MD-?11')                       THEN 'MD-11F'
      WHEN REGEXP_CONTAINS(m, r'^747.*F')                      THEN '747F'
      WHEN REGEXP_CONTAINS(m, r'^747')                         THEN '747'
      WHEN REGEXP_CONTAINS(m, r'^767.*F')                      THEN '767F'
      WHEN REGEXP_CONTAINS(m, r'^757.*F')                      THEN '757F'
      WHEN REGEXP_CONTAINS(m, r'^777.*F')                      THEN '777F'
      WHEN REGEXP_CONTAINS(m, r'^717')                         THEN '717'
      -- 737-8, 737-9, MAX8, 737-8200: MAX. 737-823, 737-924ER: NG (las dos
      -- ultimas cifras son el codigo de cliente de Boeing)
      WHEN REGEXP_CONTAINS(m, r'MAX|^737-?(7|8|9|10|8200)$')   THEN '737MAX'
      WHEN REGEXP_CONTAINS(m, r'^737(NG|-?[6-9])')             THEN '737NG'
      WHEN REGEXP_CONTAINS(m, r'^757')                         THEN '757'
      WHEN REGEXP_CONTAINS(m, r'^767')                         THEN '767'
      WHEN REGEXP_CONTAINS(m, r'^777')                         THEN '777'
      WHEN REGEXP_CONTAINS(m, r'^787')                         THEN '787'
      -- Airbus: la N al final del codigo de motor (251N, 271NX) es neo
      WHEN REGEXP_CONTAINS(m, r'^A321-?\d{3}N')                THEN 'A321neo'
      WHEN REGEXP_CONTAINS(m, r'^A321')                        THEN 'A321ceo'
      WHEN REGEXP_CONTAINS(m, r'^A320-?\d{3}N')                THEN 'A320neo'
      WHEN REGEXP_CONTAINS(m, r'^A320')                        THEN 'A320ceo'
      WHEN REGEXP_CONTAINS(m, r'^A319')                        THEN 'A319'
      WHEN REGEXP_CONTAINS(m, r'^A330-?9')                     THEN 'A330neo'
      WHEN REGEXP_CONTAINS(m, r'^A330.*F')                     THEN 'A330F'
      WHEN REGEXP_CONTAINS(m, r'^A330')                        THEN 'A330ceo'
      WHEN REGEXP_CONTAINS(m, r'^A350')                        THEN 'A350'
      WHEN REGEXP_CONTAINS(m, r'^A300.*F')                     THEN 'A300F'
      WHEN REGEXP_CONTAINS(m, r'^A300')                        THEN 'A300'
      WHEN REGEXP_CONTAINS(m, r'^(BD-500|A220)')               THEN 'A220'
      -- Bombardier CRJ por numero de certificado de tipo
      WHEN REGEXP_CONTAINS(m, r'2B19|^CRJ(100|200)')           THEN 'CRJ200'
      WHEN REGEXP_CONTAINS(m, r'2C1[01]|^CRJ700')              THEN 'CRJ700'
      WHEN REGEXP_CONTAINS(m, r'2D(15|24)|^CRJ900')            THEN 'CRJ900'
      WHEN REGEXP_CONTAINS(m, r'2E25|^CRJ1000')                THEN 'CRJ1000'
      -- Embraer: ERJ170-200 es el E175 y ERJ170-100 el E170
      WHEN REGEXP_CONTAINS(m, r'^ERJ-?170-?200|E175')          THEN 'E175'
      WHEN REGEXP_CONTAINS(m, r'^ERJ-?170')                    THEN 'E170'
      WHEN REGEXP_CONTAINS(m, r'^ERJE?-?190-?200|E195')        THEN 'E195'
      WHEN REGEXP_CONTAINS(m, r'^ERJE?-?190|E190')             THEN 'E190'
      WHEN REGEXP_CONTAINS(m, r'^(EMB|ERJ)-?1(35|40|45)')      THEN 'ERJ145'
      WHEN REGEXP_CONTAINS(m, r'ATR-?72')                      THEN 'ATR-72'
      WHEN REGEXP_CONTAINS(m, r'ATR-?42')                      THEN 'ATR-42'
      WHEN REGEXP_CONTAINS(m, r'^(C-?)?208|^408')              THEN 'Cessna'
      WHEN REGEXP_CONTAINS(m, r'^BD-(100|700)|GULFSTREAM')     THEN 'Business Jet'
      ELSE 'Other'
    END AS aircraft_model
  FROM b
)

SELECT
  n.year,
  n.unique_carrier,
  n.unique_carrier_name,
  c.airline_id,
  c.group_id,
  c.en_flota_reportada,
  n.tail_number,
  n.serial_number,
  n.manufacturer,
  n.model                   AS model_b43,
  n.aircraft_model,
  t.generation,
  t.replacement_target,
  n.manufacture_year,
  n.year - n.manufacture_year AS edad,
  CASE n.aircraft_status
    WHEN 'O' THEN 'Owned'
    WHEN 'B' THEN 'Operating Lease'
    WHEN 'A' THEN 'Finance Lease'
    ELSE 'Unknown'
  END                       AS tipo_propiedad,
  n.operating_status,
  n.acquisition_date,
  n.number_of_seats,
  -- Misma regla que fleet_replacement_status en v_fleet_normalized:
  -- Replace Now o Critical si faltan 3 anios o menos para el objetivo
  (t.replacement_target - (n.year - n.manufacture_year)) <= 3 AS en_ventana
FROM normalizado n
LEFT JOIN `mach5-gemini-project.dataset_prospeccion.dim_operador_certificado` c
  ON c.dot_unique_carrier = n.unique_carrier
LEFT JOIN `mach5-gemini-project.dataset_integrado.dim_aircraft_type` t
  ON t.model = n.aircraft_model
