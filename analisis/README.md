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
