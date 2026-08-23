# Migración del modelo de Power BI

## Por qué

El modelo `SEC China Dashboard_v3` corre sobre **datos obsoletos**. Contiene 294 filas
y 11,255 aeronaves, que corresponden a un estado anterior de `current_fleet_fact`
—el que conserva `dataset_historico.flota_historica_current_fleet_fact`—, no al de
producción.

Además concentra lógica que ya vive en el almacén:

| En el modelo (DAX) | Ahora en BigQuery |
|---|---|
| `Aircraft_Model` (SWITCH de ~100 líneas) | UDF `normalizar_modelo_aeronave` |
| `Aircraft_Category`, `Manufacturer` | `dim_aircraft_type` |
| `Aircraft_Design_Life` (DATATABLE) | `dim_aircraft_type` |
| `Remaining_Replacement_Life`, `Fleet_Replacement_Status` | `v_fleet_normalized` |
| `Dim_Aerolineas` (DATATABLE) | `dim_airline` |

Mantener dos copias de la misma lógica garantiza que se separen. La migración apunta
el modelo a las vistas y deja el DAX solo para lo que es propio del BI.

## Riesgo antes de empezar

**No refresques el modelo actual sin migrarlo.** Los nombres de aerolínea que produce
hoy el pipeline son canónicos (`Aeromexico`, `United Airlines`) y ya no coinciden con
los del `DATATABLE` `Dim_Aerolineas` (`Aeroméxico`, `United`). Un refresh dejaría todas
las filas fuera de la dimensión.

Los tickers del `DATATABLE` tampoco reconcilian con `financial_fact.company`: usa `RPA`
donde las finanzas usan `RJET`, `AMX` donde usan `AEROMEX`, `AZUL4` donde usan `AZUL`,
`VOLAR` donde usan `VLRS`. Solo `LTM` y `CPA` coinciden. Por eso nunca se pudo cruzar
flota con finanzas dentro del modelo.

## Pasos

### 1. Reemplazar las tablas de origen

Sustituir las consultas de `current_fleet_fact` y `order_book_fact` por las vistas.
Las expresiones M están en `m/`.

- `v_fleet_normalized` reemplaza a `current_fleet_fact`: trae ya resueltos
  `airline_id`, `group_id`, `aircraft_model`, `manufacturer`, `category`,
  `remaining_replacement_life` y `fleet_replacement_status`.
- `v_prospect_score` es nueva: una fila por grupo operador con el score y sus drivers.
- `dim_airline` reemplaza al `DATATABLE` `Dim_Aerolineas`.
- `dim_aircraft_type` reemplaza al `DATATABLE` `Aircraft_Design_Life`.

### 2. Eliminar las columnas calculadas ya materializadas

En `current_fleet_fact`: `Aircraft_Model`, `Aircraft_Category`, `Manufacturer`,
`Remaining_Replacement_Life`, `Fleet_Replacement_Status`.

En `order_book_fact`: `Aircraft_Model` y `Manufacturer`. Esta última usaba coincidencia
de substrings sobre el texto crudo, con patrones frágiles como `"0-"` para Airbus y
`"7-"` para Boeing, distintos de los que usaba la tabla de flota. Ahora ambas comparten
una sola definición.

### 3. Rehacer las relaciones

- `v_fleet_normalized[group_id]` → `dim_airline[airline_id]`
- `v_fleet_normalized[aircraft_model]` → `dim_aircraft_type[model]`
- `v_prospect_score[grupo]` → `dim_airline[display_name]`

### 4. Actualizar las medidas

`dax/measures.dax` trae las corregidas. La más importante:

```
Edad Promedio Flota =
DIVIDE(
    SUMX(v_fleet_normalized, v_fleet_normalized[quantity] * v_fleet_normalized[average_age_years]),
    SUMX(v_fleet_normalized, IF(ISBLANK(v_fleet_normalized[average_age_years]), 0, v_fleet_normalized[quantity]))
)
```

La versión anterior era `AVERAGE(current_fleet_fact[average_age_years])`, un promedio
simple: una fila de 3 aeronaves pesaba igual que una de 300.

### 5. Filtrar las filas agregadas

Todas las medidas de flota por tipo, modelo o generación deben excluir
`es_fila_agregada = TRUE`. Son filas donde el propio reporte declara un agregado
—`Total Fleet (Includes B737-800NG and E190)`, `Commercial Aircraft`— y no un tipo real.
Se conservan para no perder aeronaves del conteo total, pero contarlas como si fueran
un modelo distorsiona cualquier desglose.

## Lo que no se puede automatizar

La capa de reporte —las páginas y sus visuales— vive dentro del `.pbix` y no es
accesible desde las herramientas externas de modelo. Los pasos 1 a 5 se pueden aplicar
con Tabular Editor o a mano; rehacer las páginas para orientarlas a prospección es
trabajo manual en Power BI Desktop.

El diseño de destino está publicado como artifact: ranking por línea de servicio,
drivers visibles por aerolínea y banderas de calidad de dato.
