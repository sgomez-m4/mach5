# Datos de FAA y DOT Form 41

Dos fuentes públicas de EE. UU. que no tienen API y se cargan a mano.

Todo lo de la prospección v2 vive en el dataset `dataset_prospeccion`, separado de `dataset_integrado`, que conserva el estado del trabajo de la especialización (etiqueta `especializacion-2026-11`).

| Fuente | Script | Tablas en `dataset_prospeccion` | Originales |
|---|---|---|---|
| FAA AVInfo: talleres Part 145 y operadores (partes 121, 125, 129) | [cargar_faa.py](cargar_faa.py) | `faa_talleres`, `faa_operadores`, `faa_operador_modelos` | `gs://bucket-edgar/faa/<fecha>/` |
| DOT Form 41 en BTS TranStats: B-43, P-5.2, P-10, B-1, P-1.2 | [../form41/descargar_form41.py](../form41/descargar_form41.py) y [../form41/cargar_form41.py](../form41/cargar_form41.py) | `form41_b43`, `form41_p52`, `form41_p10`, `form41_b1`, `form41_p12` | `gs://bucket-edgar/form41/<fecha>/` |

`config/operadores_certificados.json` une ambas fuentes con `dim_airline` (tabla `dim_operador_certificado`): cada grupo con sus certificados FAA (designador) y sus códigos DOT (`unique_carrier`). `sql/v_flota_b43.sql` normaliza el inventario B-43 y lo une a los grupos. `sql/v_pedidos_vigentes.sql` es el order book con las correcciones de American y Alaska, que la v2 usa en lugar de `order_book_fact` mientras esa tabla quede en el estado de la especialización.

## Calificación de talleres

`sql/v_talleres_calificacion.sql` califica cada taller de EE. UU. como prospecto de personal tercerizado (presencial y remoto) y de backoffice. La bandera `en_zona_objetivo` marca el sur: TX, OK, AR, LA, MS, AL, TN, GA, FL, SC, NC y AZ.

- **Tipo de taller**, por sus ratings: aeronave completa de aviones grandes (estructura clase 2 o 4), aeronave completa, motores, componentes y aviónica, o servicios especializados.
- **Dueño**, por el dominio del correo o el nombre, según las reglas editables de [config/talleres_duenos.json](../config/talleres_duenos.json): independiente, grupo MRO, fabricante, aerolínea, o gobierno y educación (excluidos). Fabricantes y aerolíneas van como cuenta corporativa.
- **Puntaje de personal (0-100):** estructura de aviones grandes, motores de turbina, componentes, NDT, aprobación EASA o del Reino Unido y tamaño del grupo. Resta si el correo es personal (gmail y similares), indicio de un taller muy pequeño.
- **Puntaje de backoffice (0-100):** acuerdos bilaterales (cada uno trae su suplemento de manual y sus auditorías), número de ratings, estructura, que no tenga un corporativo que absorba el trabajo y varios certificados en el grupo.
- **Nivel:** A con 60 o más en cualquiera de los dos; B con personal de 35 o más, o backoffice de 50 o más; C el resto.
- **Contacto:** Accountable Manager (quien decide) y Liaison (suele tener el correo), con coordenadas por ZIP desde `bigquery-public-data.geo_us_boundaries.zip_codes` (tabla `geo_zip`).

Corte del 17 de noviembre de 2026, sur, venta directa: 163 en A, 270 en B y 1.211 en C. De los 433 en A o B, 424 tienen correo de contacto.

Los puntajes son indicios de capacidad y de carga regulatoria, no una medición de la necesidad: FAA no publica plantilla, clientes ni finanzas de los talleres. Un rating "Limited" tampoco dice tamaño: muchos talleres de línea de aviones grandes solo tienen estructura Limited.

`sql/v_talleres_senales.sql` lista los cambios entre las dos últimas descargas: certificado nuevo o retirado, rating nuevo o reducido, acuerdo bilateral nuevo y cambio de Accountable Manager. Con una sola descarga no devuelve filas. [prueba_senales.py](prueba_senales.py) la prueba con una descarga anterior sintética sin tocar la tabla real.

`sql/v_mercado_mantenimiento.sql` es el contexto de mercado: cuánto mantenimiento compra a terceros cada aerolínea de EE. UU. (Form 41 P-5.2, últimos cuatro trimestres). Las cuentas 52431 y 52432 son la cuenta 43 de 14 CFR 241.12, servicios comprados a compañías externas o asociadas.

## FAA

Las descargas se hacen desde los tableros de AVInfo con "Download Crosstab":

- Talleres: https://www.faa.gov/av-info/facility-dashboard, vista "Repair Station Contacts with Ratings", sin filtros.
- Operadores: https://www.faa.gov/av-info/air-operator-far-search, una descarga por parte, y otra con "Show Aircraft" para los modelos.

Se guardan en una carpeta y se cargan con la fecha de descarga. Las descargas anteriores se conservan en las tablas: cada carga reemplaza solo las filas de su fecha.

```bash
python -I faa/cargar_faa.py "<carpeta de descargas>" 2026-11-13 <carpeta temporal> --cargar
```

Sin `--cargar` solo genera los NDJSON y un resumen de conteos.

Qué corrige el script: CSV en UTF-16 con tabuladores, fechas día/mes/año, miles con punto ("2.158"), la última columna sin nombre que repite el correo, el certificado de prueba "TEST CERTIFICATE FOR 121" y los ratings separados en clases (`airframe_clases = [3, 4]`) y "Limited".

La parte 129 se volvió a bajar completa el 17 de noviembre, solo con "Show Aircraft" (`Aircraft (2).csv`): 512 certificados, contra 117 de la primera descarga. Se carga con la fecha 2026-11-13 junto al resto. Los operadores que solo aparecen en la vista de aeronaves entran con nombre, designador, certificado y fecha, sin directivos ni conteos; FAA no publica directivos de operadores extranjeros.

## Form 41

`descargar_form41.py` reproduce el formulario de descarga de TranStats y baja un año completo por tabla. Sin argumentos baja B-43 y P-10 de 2023 a 2025, y P-5.2, B-1 y P-1.2 de 2024 a 2026. `cargar_form41.py` reemplaza las tablas completas, porque BTS revisa trimestres ya publicados.

```bash
python -I form41/descargar_form41.py <carpeta zips>
python -I form41/cargar_form41.py <carpeta zips> 2026-11-17 <carpeta temporal> --cargar
```

Los montos de P-5.2, B-1 y P-1.2 están en miles de USD. En B-43, `aircraft_status` es O (propio), B (leasing operativo) o A (leasing financiero).
