-- UDF: normaliza el aircraft_type de texto libre a un modelo canonico.
--
-- Portada desde la columna calculada DAX Aircraft_Model del modelo de Power BI.
-- Vivia solo dentro del BI, asi que ningun consumidor SQL podia clasificar la
-- flota: convivian "737-800", "Boeing 737-800", "B737 Series" y "B737" como si
-- fueran cosas distintas.
--
-- El orden de las ramas importa y replica el del DAX: las familias mas
-- especificas se evaluan antes que las genericas.

CREATE OR REPLACE FUNCTION `mach5-gemini-project.dataset_integrado.normalizar_modelo_aeronave`(
  aircraft_type STRING
) RETURNS STRING AS (
  (
    WITH v AS (
      SELECT
        UPPER(TRIM(IFNULL(aircraft_type, ''))) AS t
    ),
    f AS (
      SELECT
        t,
        -- Carguero: sufijos y palabras de conversion. El "termina en F" replica
        -- el RIGHT(t,1)="F" del DAX.
        (
          STRPOS(t, 'FREIGHTER') > 0 OR STRPOS(t, 'CARGO') > 0
          OR STRPOS(t, 'BCF') > 0 OR STRPOS(t, 'P2F') > 0
          OR STRPOS(t, 'ERF') > 0 OR STRPOS(t, 'CONVERTED') > 0
          OR ENDS_WITH(t, 'F')
        ) AS is_freighter,
        -- Filas que son agregados del reporte, no modelos reales
        (
          STRPOS(t, 'TOTAL FLEET') > 0 OR STRPOS(t, 'MIXED MODELS') > 0
          OR STRPOS(t, 'NEXT-GENERATION') > 0 OR STRPOS(t, 'PREVIOUS-GENERATION') > 0
        ) AS is_aggregate
      FROM v
    )
    SELECT
      CASE
        WHEN is_aggregate THEN 'Other'

        -- ---------- AIRBUS ----------
        WHEN STRPOS(t, 'A220') > 0 THEN 'A220'
        WHEN STRPOS(t, 'A319') > 0 THEN 'A319'
        WHEN STRPOS(t, 'A320') > 0 THEN
          IF(STRPOS(t, 'NEO') > 0, 'A320neo', 'A320ceo')
        -- A321: LR y XLR son variantes del neo
        WHEN STRPOS(t, 'A321') > 0 THEN
          CASE
            WHEN is_freighter THEN 'A321F'
            WHEN STRPOS(t, 'NEO') > 0 OR STRPOS(t, 'LR') > 0 OR STRPOS(t, 'XLR') > 0
              THEN 'A321neo'
            ELSE 'A321ceo'
          END
        WHEN STRPOS(t, 'A330') > 0 THEN
          CASE
            WHEN is_freighter THEN 'A330F'
            WHEN STRPOS(t, 'NEO') > 0 THEN 'A330neo'
            ELSE 'A330ceo'
          END
        WHEN STRPOS(t, 'A350') > 0 THEN 'A350'
        WHEN STRPOS(t, 'A340') > 0 THEN 'A340'
        WHEN STRPOS(t, 'A300') > 0 THEN IF(is_freighter, 'A300F', 'A300')

        -- ---------- BOEING ----------
        WHEN STRPOS(t, '717') > 0 THEN '717'
        -- 737: solo NG vs MAX; los genericos caen en NG
        WHEN STRPOS(t, '737') > 0 THEN
          CASE
            WHEN is_freighter THEN IF(STRPOS(t, 'MAX') > 0, '737MAXF', '737NGF')
            WHEN STRPOS(t, 'MAX') > 0
              OR STRPOS(t, '8200') > 0
              OR (STRPOS(t, '-8') > 0 AND STRPOS(t, '-800') = 0)
              OR (STRPOS(t, '-9') > 0 AND STRPOS(t, '-900') = 0)
              OR STRPOS(t, '-10') > 0
              THEN '737MAX'
            ELSE '737NG'
          END
        WHEN STRPOS(t, '747') > 0 THEN IF(is_freighter, '747F', '747')
        WHEN STRPOS(t, '757') > 0 THEN IF(is_freighter, '757F', '757')
        WHEN STRPOS(t, '767') > 0 THEN IF(is_freighter, '767F', '767')
        WHEN STRPOS(t, '777') > 0 THEN IF(is_freighter, '777F', '777')
        WHEN STRPOS(t, '787') > 0 THEN '787'
        WHEN STRPOS(t, 'MD-11') > 0 OR STRPOS(t, 'MD11') > 0 THEN
          IF(is_freighter, 'MD-11F', 'MD-11')

        -- ---------- COMAC ----------
        WHEN STRPOS(t, 'C919') > 0 THEN 'C919'
        WHEN STRPOS(t, 'ARJ21') > 0 OR STRPOS(t, 'ARJ-21') > 0 OR STRPOS(t, 'C909') > 0
          THEN 'C909/ARJ21'
        WHEN STRPOS(t, 'C929') > 0 OR STRPOS(t, 'CR929') > 0 THEN 'C929'

        -- ---------- EMBRAER ----------
        -- La generacion E2 va primero: 'E-195-E2' contiene 'E-195' y caeria en
        -- la rama de la generacion anterior.
        WHEN STRPOS(t, 'E195-E2') > 0 OR STRPOS(t, 'E-195-E2') > 0
          OR STRPOS(t, 'E195E2') > 0 THEN 'E195-E2'
        WHEN STRPOS(t, 'E190-E2') > 0 OR STRPOS(t, 'E-190-E2') > 0
          OR STRPOS(t, 'E190E2') > 0 THEN 'E190-E2'
        WHEN STRPOS(t, 'E170/175') > 0 OR STRPOS(t, 'E175') > 0 THEN 'E175'
        WHEN STRPOS(t, 'E170') > 0 THEN 'E170'
        -- Cubeta mixta: se toma el mayor del par, igual que E170/175 -> E175.
        WHEN STRPOS(t, 'E-190/195') > 0 OR STRPOS(t, 'E190/195') > 0 THEN 'E195'
        WHEN STRPOS(t, 'E190') > 0 OR STRPOS(t, 'E-190') > 0 THEN 'E190'
        WHEN STRPOS(t, 'E195') > 0 OR STRPOS(t, 'E-195') > 0 THEN 'E195'
        WHEN STRPOS(t, 'ERJ145') > 0 OR STRPOS(t, 'ERJ-145') > 0 THEN 'ERJ145'

        -- ---------- BOMBARDIER ----------
        WHEN STRPOS(t, 'CRJ550') > 0 OR STRPOS(t, 'CRJ700') > 0 THEN 'CRJ700'
        WHEN STRPOS(t, 'CRJ900') > 0 THEN 'CRJ900'
        WHEN STRPOS(t, 'CRJ1000') > 0 THEN 'CRJ1000'
        WHEN STRPOS(t, 'CRJ200') > 0 THEN 'CRJ200'

        -- ---------- ATR ----------
        WHEN STRPOS(t, 'ATR-72') > 0 OR STRPOS(t, 'ATR72') > 0
          OR STRPOS(t, 'ATR 72') > 0 THEN
          IF(is_freighter, 'ATR-72F', 'ATR-72')
        WHEN STRPOS(t, 'ATR-42') > 0 OR STRPOS(t, 'ATR42') > 0
          OR STRPOS(t, 'ATR 42') > 0 THEN
          IF(is_freighter, 'ATR-42F', 'ATR-42')

        -- ---------- CESSNA ----------
        WHEN STRPOS(t, 'CESSNA') > 0 OR STRPOS(t, '208') > 0 OR STRPOS(t, '408') > 0
          THEN 'Cessna'

        -- ---------- CUBETAS DE FAMILIA ----------
        -- Algunos reportes no desglosan por modelo: Azul declara "Airbus
        -- narrowbody" y "Airbus widebody". No hay modelo que asignar, pero si
        -- fabricante y categoria, que es lo que alimenta la complejidad de
        -- portafolio. Van al final para no ensombrecer ninguna rama especifica.
        WHEN STRPOS(t, 'AIRBUS NARROWBODY') > 0 OR STRPOS(t, 'AIRBUS NARROW-BODY') > 0
          THEN 'Airbus Narrowbody'
        WHEN STRPOS(t, 'AIRBUS WIDEBODY') > 0 OR STRPOS(t, 'AIRBUS WIDE-BODY') > 0
          THEN 'Airbus Widebody'
        WHEN STRPOS(t, 'BUSINESS JET') > 0 THEN 'Business Jet'

        ELSE 'Other'
      END
    FROM f
  )
);
