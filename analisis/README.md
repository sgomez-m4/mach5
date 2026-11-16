# Evaluación posterior del prospect score

Este análisis se hizo **después de entregar el sistema**, para el artículo del proyecto. No forma parte del pipeline, de las vistas de BigQuery ni del tablero de Power BI, y no modifica ninguno de ellos. Lee una exportación de la vista `v_prospect_score` tomada en la fecha de corte y produce métricas y figuras.

## Qué responde

| Prueba | Pregunta |
|---|---|
| Reproducibilidad | ¿El score publicado es exactamente la suma ponderada de sus componentes? |
| Esquemas alternativos | ¿Cuánto cambia el ranking con otras tesis comerciales (pesos iguales, solo leasing, etc.)? |
| Monte Carlo | Si los pesos fueran algo distintos, ¿qué tan estable es el top 5 y entre qué posiciones se mueve cada aerolínea? |
| Corrección por cobertura | ¿Qué pasa si la fracción en ventana se calcula sobre las aeronaves con edad declarada y no sobre la flota total? |

## Parámetros de la simulación de Monte Carlo

- **N = 10 000 repeticiones.** Con ese número, el error estándar de cada probabilidad estimada es de 0,005 como máximo, así que los valores como «P(top 5) = 0,97» son estables en el segundo decimal.
- **Cada peso se multiplica por un factor U(0,5; 1,5) independiente.** La distribución es uniforme porque los pesos son criterio experto y no hay base para suponer otra. La perturbación es multiplicativa para que cada peso varíe en proporción a su tamaño; una perturbación aditiva de ±10 puntos haría negativo el peso de 8.
- **El margen de ±50 % es amplio pero conserva la prioridad de la empresa.** Leasing (35) y reemplazo (30) pueden intercambiarse entre sí y con complejidad (15), pero nunca quedan por debajo de apalancamiento (8): en el peor caso, 17,5 y 15 frente a 12.
- **Después de perturbar, los pesos se renormalizan para sumar 1**, igual que hace la medida `Score Ponderado` del tablero, de modo que la puntuación sigue en la escala de 0 a 100.
- **La semilla es fija (20261109)** para que el resultado sea idéntico en cada ejecución.

## Cómo reproducirlo

1. Exporta los datos con las consultas de [exportar.sql](exportar.sql):

   ```bash
   export CLOUDSDK_PYTHON=/c/Python312/python.exe
   ```

   ```bash
   bq --project_id=mach5-gemini-project query --use_legacy_sql=false --format=csv --max_rows=100 'SELECT * FROM `mach5-gemini-project.dataset_integrado.v_prospect_score` ORDER BY prospect_score DESC' > datos/score_full.csv
   ```

   La segunda consulta de `exportar.sql` produce `datos/ventana.csv` de la misma forma.

2. Ejecuta el análisis. Solo usa la biblioteca estándar de Python:

   ```bash
   py -3.12 -I sensibilidad.py datos/score_full.csv datos/sensibilidad.json datos/ventana.csv
   ```

3. Genera las figuras del artículo. Requiere matplotlib:

   ```bash
   py -3.12 figuras.py datos/score_full.csv datos/sensibilidad.json figuras
   ```

La carpeta `datos/` conserva la exportación usada en el artículo. Si se vuelve a exportar después de una nueva carga trimestral, los resultados cambiarán.

## Conciliación de totales

`datos/conciliacion_totales.csv` compara, para cada grupo, el total de flota extraído con el total que declara su informe, con la fuente y la línea del documento donde aparece. Se hizo a mano sobre las secciones de flota en `gs://bucket-edgar/` y se contrastó con dos estados del almacén: el del 11 de noviembre de 2026 (el que usa el artículo) y el que dejó la reejecución del pipeline del 13 de noviembre.

- 11 de noviembre: 23 de 24 grupos coinciden. Spring Airlines contaba dos veces sus 134 aeronaves (por propiedad y por modelo). Se corrigió marcando como agregadas las tres filas por modelo, con el mismo criterio que en Aeroméxico: conservar propiedad y edad a costa del tipo.
- 13 de noviembre: 18 de 24. La reejecución asignó HK Express a Juneyao, perdió a 9 Air, contó dos veces la flota regional de American y añadió aeronaves sin respaldo en United y China Express. Se restauró el estado del 11 de noviembre; las tablas de esa ejecución se conservan con el sufijo `_bak_20261116_ejec13nov`.

Los informes no declaran el mismo tipo de total: American y United incluyen su flota regional, Delta no; SkyWest cuenta las aeronaves en servicio o bajo contrato; Azul publica su flota contractual (227) y la operativa (178).

## Cotas de cobertura

`cotas.py` responde a la objeción de que la corrección de cobertura (prueba 4 de `sensibilidad.py`) supone que las aeronaves sin edad se parecen a las que la declaran. En lugar de suponer, calcula para cada grupo con edad incompleta el intervalo posible de la fracción en ventana:

- **Cota inferior:** ninguna aeronave sin edad está en ventana (el denominador original).
- **Cota inferior acotada:** las de modelos cuya producción terminó hace más años que el inicio de su ventana están en ventana con certeza. Hoy solo aplica a los CRJ200 de United y los MD-11 de FedEx. Los años de fin de producción están en `FIN_PRODUCCION` y deben citarse con fuente.
- **Cota superior:** todas están en ventana.

Antes de calcular, el script comprueba que reconstruye el componente de reemplazo de la vista; si no lo hace, se detiene.

```bash
py -3.12 -I cotas.py datos/score_full.csv datos/ventana.csv datos/sin_edad.csv datos/cotas.json
```

`datos/sin_edad.csv` sale de la consulta 3 de `exportar.sql`.

## Parámetros internos de los componentes

`parametros.py` responde a la objeción de que el Monte Carlo solo perturba los cinco pesos. Perturba con el mismo criterio, U(0,5; 1,5) multiplicativa y semilla 20261109, los repartos internos de cada componente (por ejemplo 0,50 / 0,20 / 0,30 en leasing, renormalizados) y sus constantes: el factor 2,5 de las emisoras IFRS, el horizonte de 10 años de vida, las saturaciones de 12 modelos, 3 fabricantes adicionales, 0,5 pedidos por avión, 0,2 entregas a dos años y 6 veces deuda/EBITDA, y la deuda por defecto de 2,5. Corre dos pruebas: solo parámetros, y parámetros y pesos a la vez.

Los insumos sin redondear salen de la CTE `base` de la propia vista con `exportar_insumos.py`. Antes de simular, el script comprueba que reconstruye los cinco componentes publicados.

```bash
python exportar_insumos.py ../sql/v_prospect_score.sql datos/insumos.csv
py -3.12 -I parametros.py datos/insumos.csv datos/score_full.csv datos/parametros.json
```
