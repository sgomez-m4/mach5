# Informe de prospección v2: pasos en Power BI Desktop

> **Estado al 17-nov-2026:** los siete bloques están hechos. Las páginas se escribieron en PBIR con Power BI cerrado, se revisaron en Desktop y se guardaron desde ahí. Lo que quedó distinto de los pasos de abajo:
> - Las tres páginas de prospección se llaman `Aerolíneas`, `Talleres` y `Mapa de talleres`, para que quepan en el navegador. El navegador, arriba a la derecha de cada una, muestra solo esas tres.
> - Las dos fichas tienen el botón de volver arriba a la izquierda. En Desktop se usa con Ctrl + clic.
> - El mapa usa Azure Maps.
>
> Lo que sigue queda como referencia de qué contiene cada página.

`Prospeccion_v2_PBIP/` es una copia del informe de la especialización con el modelo ampliado. El original (`Prospeccion_Flota_PBIP/`) no se toca: es el que se muestra en la sustentación.

El modelo ya trae, desde `dataset_prospeccion`:

| Tabla | Vista | Filas al 17-nov-2026 |
|---|---|---|
| Prospectos aerolineas | v_prospect_score_v2 | 24 (11 en A, 12 en B) |
| Talleres | v_talleres_informe | 4.004 (1.799 en el sur) |
| Talleres ratings | v_talleres_ratings | 13.393 |
| Talleres acuerdos | v_talleres_acuerdos | 4.610 |
| Talleres senales | v_talleres_senales | 0 hasta la segunda descarga de FAA |
| Mercado mantenimiento | v_mercado_mantenimiento | 44 |
| Directivos | v_directivos_operador | 68 (13 grupos) |

Las medidas nuevas están en `_Medidas v2`. Las tablas de ratings y de acuerdos filtran a Talleres en ambos sentidos, así que sirven de segmentación directa. Prospectos aerolineas también filtra a `dim_airline` en ambos sentidos: elegir un grupo en la lista filtra su flota, sus pedidos y sus directivos.

Medidas de lienzo: 1280 × 720. Las posiciones van como x, y, ancho, alto, y se ponen en Formato → General → Propiedades.

## Bloque 1. Abrir y refrescar

1. Cierra cualquier Power BI abierto. Abre `mach5\powerbi\Prospeccion_v2_PBIP\Prospeccion_v2.pbip`.
2. Inicio → Actualizar. Si pide credenciales de Google BigQuery, usa la misma cuenta del informe original.
3. Comprueba en la vista de tabla (icono de cuadrícula a la izquierda), abajo a la izquierda, el número de filas de cada tabla. Debe coincidir con la tabla de arriba.
4. Archivo → Guardar.

## Bloque 2. Portada: aerolíneas

1. Botón **+** de pestañas → página nueva. Doble clic en la pestaña → nombre `Prospección: aerolíneas`. Arrástrala al primer lugar.
2. **Título:** Insertar → Cuadro de texto, en 16, 8, 820, 44. Texto: `Prospección de aerolíneas: a quién llamar y qué ofrecer`, 20 pt, negrita.
3. **Segmentaciones:** cuatro, de 196 × 56 en y = 64, con x = 16, 224, 432 y 640. Campos de `Prospectos aerolineas`:
   - Nivel
   - Servicio sugerido
   - Confianza del dato
   - Región

   En cada una: Formato → Configuración de la segmentación → Estilo → Lista desplegable.
4. **Lista**, en 16, 132, 820, 572: visual Tabla, con estas columnas en orden:
   - De `Prospectos aerolineas`: Orden, Grupo, Nivel, Servicio sugerido, Aeronaves en juego, % flota en juego, Flota total, Tamaño y Confianza del dato.

   Clic en el encabezado Orden para ordenar de menor a mayor. Renombra Orden a `#` (doble clic sobre el campo en el panel de visualizaciones).
5. **Panel de detalle**, a la derecha:
   - Insertar → Formas → Rectángulo, en 852, 64, 412, 640, con relleno gris muy claro y sin borde.
   - Tarjeta (la clásica) con `_Medidas v2` → Aerolíneas → **Aerolínea elegida**, en 868, 72, 380, 64. Quita la etiqueta de categoría y deja el texto a 18 pt.
   - Tarjeta de varias filas con **Servicio aerolínea**, **Motivo aerolínea** y **Alertas aerolínea**, en 868, 144, 380, 220. En Formato → Valores, activa el ajuste de texto.
   - Gráfico de barras agrupadas en 868, 372, 380, 150:
     - Valores de `Prospectos aerolineas`: Leasing que vence en 3 años, En ventana de reemplazo y Entregas en 24 meses.
     - Título: `Aeronaves en juego por línea`.
   - Tabla en 868, 530, 380, 166:
     - Columnas de `Directivos`: Cargo, Nombre y Operador.
     - Título: `Directivos (FAA)`.
6. Con nada elegido, el panel dice "Elige una aerolínea en la lista". Clic en una fila de la tabla: el panel muestra esa aerolínea y sus directivos. Si una aerolínea extranjera no tiene directivos, es lo esperado: FAA solo los publica para operadores de EE. UU.
7. Guardar.

## Bloque 3. Portada: talleres

1. Clic derecho en la pestaña `Prospección: aerolíneas` → Duplicar página. Renombra la copia `Prospección: talleres` y déjala segunda.
2. Borra el gráfico de barras y la tabla de directivos. Cambia el título a `Talleres del sur: personal técnico y backoffice`.
3. **Segmentaciones:** cambia sus campos por:
   - Nivel (`Talleres`)
   - Oferta sugerida (`Talleres`)
   - Tipo de taller (`Talleres`)
   - Estado (`Talleres`)
4. **Filtro de página:** en el panel Filtros → Filtros en esta página, agrega:
   - `Talleres` → Zona sur = Verdadero
   - `Talleres` → Canal = Venta directa
5. **Lista:** cambia las columnas de la tabla por:
   - De `Talleres`: Taller, Ciudad, Estado, Nivel, Oferta sugerida, Puntaje personal, Puntaje backoffice y Acuerdos bilaterales.

   Ordena por Puntaje mayor: agrégalo, ordena de mayor a menor y quítalo de la vista con el ancho de columna en cero. También puedes ordenar por Puntaje personal si lo prefieres.
6. **Panel de detalle:**
   - Tarjeta con **Taller elegido**.
   - Tarjeta de varias filas con **Ubicación taller**, **Oferta taller**, **Motivo taller**, **Contacto taller**, **Ratings taller** y **Acuerdos taller**, en 868, 144, 380, 552.
7. **Comprobación:** con el filtro de página y sin segmentaciones, una tarjeta con **Talleres nivel A** da 163, y **Talleres nivel B** da 270. Puedes ponerla un momento y borrarla.
8. Guardar.

## Bloque 4. Pestañas

En las dos portadas:

- Insertar → Botones → Navegador → **Navegador de páginas**, en 860, 12, 404, 40.
- En Formato → Páginas, deja visibles solo `Prospección: aerolíneas` y `Prospección: talleres`.

Así las dos páginas se comportan como dos pestañas de una misma portada. En Desktop se navega con Ctrl + clic; en el servicio, con clic.

## Bloque 5. Ficha de aerolínea por drill-through

1. Duplica la página `Ficha de aerolínea` y renombra la copia `Ficha de aerolínea v2`.
2. Borra la segmentación de aerolínea (arriba a la izquierda).
3. Con la página seleccionada (clic en el lienzo vacío), en el panel de Visualizaciones → Obtener detalles (drill-through), arrastra `Prospectos aerolineas` → **Grupo**. Deja «Mantener todos los filtros» activado. Aparece un botón de volver arriba a la izquierda; ponlo en 16, 64, 32, 32.
4. En el hueco que dejó la segmentación (56, 64, 368, 88), pon una tarjeta de varias filas con:
   - **Aerolínea elegida**, **Servicio aerolínea** y **Motivo aerolínea**.
5. Agrega abajo, o en lugar de la dona, una tabla con `Directivos` → Cargo, Nombre, Operador y Teléfono del operador.
6. Si la tarjeta de varias filas de arriba muestra Prospect Score (v1), cámbialo por `Prospectos aerolineas` → Nivel y Aeronaves en juego.
7. En la portada de aerolíneas, clic derecho en una fila → Obtener detalles → Ficha de aerolínea v2. Debe abrir la ficha de ese grupo.
8. Guardar.

## Bloque 6. Talleres: mapa y ficha

1. Página nueva `Talleres: mapa`, tercera.
2. **Mapa:** visual Mapa en 16, 64, 820, 640. Si no aparece o sale en gris, actívalo en Archivo → Opciones y configuración → Opciones → Seguridad → «Usar los objetos visuales Mapa y Mapa coroplético», y reinicia Power BI.
   - Latitud: `Talleres` → Latitud
   - Longitud: `Talleres` → Longitud
   - Leyenda: Nivel
   - Tamaño de burbuja: Puntaje mayor (promedio)
   - Información sobre herramientas: Taller y Oferta sugerida
3. **Segmentaciones** a la derecha, en x = 852, ancho 412:
   - `Talleres ratings` → **Categoría de rating** y **Rating** (las dos en la misma segmentación, como jerarquía). Por ejemplo, Motores → Clase 3: turbina.
   - `Talleres acuerdos` → **Acuerdo bilateral**.
   - `Talleres` → Estado.
   - `Talleres` → Tipo de dueño.
4. **Filtro de página:** Zona sur = Verdadero.
5. **Tabla de contactos** abajo a la derecha, con `Talleres` → Taller, Accountable Manager, Liaison, Correo de contacto y Teléfono de contacto.
6. **Comprobación:**
   - Acuerdo = EASA da 731 talleres en el sur.
   - Rating = Motores → Clase 3: turbina da 35.
7. **Ficha de taller:**
   - Página nueva `Ficha de taller`.
   - Drill-through con `Talleres` → **Certificado**.
   - La tarjeta de varias filas del panel de detalle (cópiala de la portada de talleres y pégala aquí).
   - Una tabla con `Talleres ratings` → Categoría de rating y Rating.
   - Otra con `Talleres senales` → Señal, Detalle y Descarga actual. Quedará vacía hasta la segunda descarga de FAA.
8. Guardar.

## Bloque 7. Laboratorio y evidencia

1. **Laboratorio:** renombra `Ranking de prospectos` a `Laboratorio: pesos del score v1`. Es la página que ya tiene los cinco deslizadores; queda como laboratorio.
2. **Evidencia:** renombra las demás páginas con el prefijo `Evidencia · ` y ponlas después de las fichas. Son:
   - Exposición a leasing
   - Presión de reemplazo
   - Ventana de reemplazo
   - Transición medida
   - Pipeline de entregas
   - Cobertura y calidad
3. **Ficha anterior:** la `Ficha de aerolínea` original puede quedarse como `Evidencia · Ficha de aerolínea (v1)` u ocultarse (clic derecho → Ocultar página).
4. **Página de apertura:** Power BI abre en la página activa al guardar. Termina en `Prospección: aerolíneas` y guarda. Así deja de abrir en «Exposición a leasing».

Orden final de las pestañas:

1. Prospección: aerolíneas
2. Prospección: talleres
3. Talleres: mapa
4. Ficha de aerolínea v2
5. Ficha de taller
6. Laboratorio
7. Las páginas de Evidencia

## Al terminar

Cierra Power BI. Desde la consola, el informe queda versionado en la rama `feat/score-v2`; Power BI reescribe la carpeta `.Report` al guardar, así que se hace commit de lo que quedó en disco después de cerrarlo.
