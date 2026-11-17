# Datos de FAA y DOT Form 41

Dos fuentes públicas de EE. UU. que no tienen API y se cargan a mano.

Todo lo de la prospección v2 vive en el dataset `dataset_prospeccion`, separado de `dataset_integrado`, que conserva el estado del trabajo de la especialización (etiqueta `especializacion-2026-11`).

| Fuente | Script | Tablas en `dataset_prospeccion` | Originales |
|---|---|---|---|
| FAA AVInfo: talleres Part 145 y operadores (partes 121, 125, 129) | [cargar_faa.py](cargar_faa.py) | `faa_talleres`, `faa_operadores`, `faa_operador_modelos` | `gs://bucket-edgar/faa/<fecha>/` |
| DOT Form 41 en BTS TranStats: B-43, P-5.2, P-10, B-1, P-1.2 | [../form41/descargar_form41.py](../form41/descargar_form41.py) y [../form41/cargar_form41.py](../form41/cargar_form41.py) | `form41_b43`, `form41_p52`, `form41_p10`, `form41_b1`, `form41_p12` | `gs://bucket-edgar/form41/<fecha>/` |

`config/operadores_certificados.json` une ambas fuentes con `dim_airline` (tabla `dim_operador_certificado`): cada grupo con sus certificados FAA (designador) y sus códigos DOT (`unique_carrier`). `sql/v_flota_b43.sql` normaliza el inventario B-43 y lo une a los grupos. `sql/v_pedidos_vigentes.sql` es el order book con las correcciones de American y Alaska, que la v2 usa en lugar de `order_book_fact` mientras esa tabla quede en el estado de la especialización.

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

Pendiente: la descarga de la parte 129 del 13 de noviembre trae 117 operadores y le faltan Volaris, Copa, LATAM, Cathay y casi todas las chinas. Hay que volver a bajarla completa.

## Form 41

`descargar_form41.py` reproduce el formulario de descarga de TranStats y baja un año completo por tabla. Sin argumentos baja B-43 y P-10 de 2023 a 2025, y P-5.2, B-1 y P-1.2 de 2024 a 2026. `cargar_form41.py` reemplaza las tablas completas, porque BTS revisa trimestres ya publicados.

```bash
python -I form41/descargar_form41.py <carpeta zips>
python -I form41/cargar_form41.py <carpeta zips> 2026-11-17 <carpeta temporal> --cargar
```

Los montos de P-5.2, B-1 y P-1.2 están en miles de USD. En B-43, `aircraft_status` es O (propio), B (leasing operativo) o A (leasing financiero).
