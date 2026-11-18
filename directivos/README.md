# Directivos de aerolíneas extranjeras

FAA solo publica directivos de operadores de EE. UU. (partes 121 y 125). Para las 13 aerolíneas extranjeras del score, [extraer_directivos.py](extraer_directivos.py) los toma de sus informes anuales:

- **Latinoamericanas** (Aeroméxico, Azul, Copa, LATAM y Volaris): del Item 6 del 20-F, en `gs://bucket-edgar/20f-md/`.
- **Chinas** (Air China, China Eastern, China Southern, Hainan, Spring, Juneyao, China Express y Cathay Pacific): de la sección de dirección del informe anual, en `gs://bucket-edgar/anual-china-md/`.

Gemini extrae los cargos que importan para vender: CEO, director técnico o de mantenimiento, operaciones, flota o compras, CFO y presidente del consejo. El resultado va a la tabla `dataset_prospeccion.directivos_filings`.

`sql/v_directivos_operador.sql` une estos directivos con los de FAA, y la ficha y la portada de aerolíneas los muestran en la misma tabla. En la columna Operador se lee de qué documento salen («Según 20-F 2025»). El nombre lleva el título original entre paréntesis, porque el cargo normalizado lo clasifica Gemini y a veces es discutible. Por ejemplo, el CEO de LATAM Cargo quedó como «Director de flota o compras».

Corte del 18-nov-2026: 60 directivos de 13 aerolíneas. Entre los técnicos están el CTO de Azul, el VP de Operaciones Técnicas de Copa y los ingenieros jefe (总工程师) de China Southern, Juneyao, Spring y China Eastern.

```bash
python -I -X utf8 directivos/extraer_directivos.py <carpeta de trabajo> --cargar
```

La sección se ubica así:

- **20-F:** el Item 6 se busca solo como encabezado al inicio de línea. El texto cita «Item 6. Directors…» en muchas partes, y las filas del índice empiezan con `|`.
- **Si no hay encabezado:** se toma la ventana de 60.000 caracteres con más títulos de cargo («Chief Executive», 总经理, etc.).
