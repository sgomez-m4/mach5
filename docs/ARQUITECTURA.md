# Arquitectura mach5

De los reportes regulatorios de 24 aerolíneas a una lista priorizada de prospectos para
una consultora de asset management aeronáutico.

Existe una versión navegable de este documento publicada como artifact, con el diagrama
de flujo end-to-end.

---

## 0. Qué resuelve

Una consultora especializada en **gestión de arrendamientos** y **gestión técnica de
activos** necesita saber qué aerolíneas atraviesan una transición de flota, porque esas
transiciones son el momento en que se contrata su servicio: devoluciones, condiciones de
retorno, renegociación de leases, phase-out técnico.

El sistema responde tres preguntas por aerolínea —qué flota opera, en qué condición
financiera está, y qué señal de transición muestra— y produce un ranking segmentado por
la línea de servicio que corresponde a cada prospecto.

| | |
|---|---|
| Cobertura | 24 aerolíneas · 12,271 aeronaves · 150 registros de pedidos |
| Ejercicio | FY2025 únicamente; el pipeline descarta los anteriores antes de acumular |
| Infraestructura | 22 servicios de Cloud Run, orquestados por Cloud Workflows |

---

## 1. Recorrido de un dato

```
FUENTE          SEC EDGAR              CSRC / HKEX            akshare
                (10-K, 20-F)           (PDF anual)            (Sina, Eastmoney)
                    |                       |                      |
                edgartools           descarga + PDF→MD            API
                    v                       v                      v
EXTRACCION      Item 2 / Item 4        seccion MD&A          3 estados financieros
                hechos XBRL            fallback Gemini        depreciacion Eastmoney
                    |                       |                      |
                    +-----------+-----------+----------+-----------+
                                v                      v
NORMALIZACION   ejecutar-pipeline-anuales     normalizacion-financiera
                resuelve identidad            crosswalk con precedencia
                marca filas agregadas         convierte a USD, deriva formulas
                                v                      v
ALMACEN         current_fleet_fact            financial_fact
                order_book_fact               financial_fact_raw
                                \                      /
                                 \   dim_airline      /
                                  \  dim_aircraft_type
                                           v
VISTAS                  v_fleet_normalized · v_prospect_score
                                           v
ENTREGA                   Power BI  ·  Radar publicado
```

Las dimensiones canónicas son las que hacen posible cruzar flota con finanzas: sin ellas
los dos lados usan identificadores distintos y no se pueden unir.

---

## 2. De dónde salen los datos

| Fuente | Cubre | Formato | Cómo se obtiene |
|---|---|---|---|
| SEC EDGAR 10-K | 11 aerolíneas US | XBRL `us-gaap` + texto | `edgartools` por CIK. Flota del Item 2, financieros de los hechos XBRL |
| SEC EDGAR 20-F | 5 emisoras LATAM | XBRL `ifrs-full` | Mismo mecanismo. Flota del Item 4 |
| CSRC / HKEX | 8 aerolíneas China y HK | PDF, chino simplificado y tradicional | Descarga, conversión a Markdown, localización de MD&A, extracción con Gemini |
| akshare | 7 A-Share chinas | Tabular | Estados financieros ya estructurados, sin IA generativa |

**Por qué akshare.** Los financieros chinos salían de Gemini leyendo PDFs. La API XBRL
directa de la Bolsa de Shanghái (`listxbrl.sse.com.cn`) ya no existe —redirige a la web
estática—, pero `akshare` lee de Sina y Eastmoney, que a su vez toman de los filings.
Siete de las ocho aerolíneas chinas pasaron a datos estructurados; solo Cathay Pacific,
que cotiza únicamente en HKEX, conserva la ruta Gemini.

---

## 3. Extracción

Cada etapa escribe a un prefijo de `gs://bucket-edgar` y la siguiente lo lee. Los archivos
intermedios quedan como evidencia auditable y permiten reprocesar una etapa sin rehacer
las anteriores.

### Cadena de flota

| Servicio | Lee | Escribe |
|---|---|---|
| `extraccion-10k-md` | SEC EDGAR | `10k-md/` |
| `extraccion-item2-10k` | `10k-md/` | `10k-item2-md/` |
| `extraccion-json-item2` | `10k-item2-md/` | `10k-item2-json/` |
| `extraccion-20f-md` | SEC EDGAR | `20f-md/` |
| `extraccion-item4-20f` | `20f-md/` | `20f-flota-md/` |
| `extraccion-json-item4` | `20f-flota-md/` | `20f-flota-json/` |
| `descarga-anual-china` | CSRC / HKEX | `anual-china/` |
| `parsing-md-china` | `anual-china/` | `anual-china-md/` |
| `extraer-seccion-china` | `anual-china-md/` | `flota-aerolineas-md/` |
| `extraccion-json-china` | `flota-aerolineas-md/` | `flota-aerolineas-json/` |
| `ejecutar-pipeline-anuales` | los tres `*-json/` | `current_fleet_fact`, `order_book_fact` |

**Deduplicación.** `ejecutar-pipeline-anuales` recorre los tres prefijos de GCS y antes
concatenaba todo lo que encontrara. Un mismo filing puede quedar extraído dos veces bajo
prefijos distintos —el 20-F de Aeroméxico existe como `_Item2_filtrado` en `10k-item2-json/`
y como `_Flota_filtrado` en `20f-flota-json/`— y sus dos lecturas se sumaban. Ahora se
agrupa por documento fuente, quitando prefijo, extensión y sufijos del extractor, y se
conserva una sola lectura: primero la que traiga filas de flota, luego la de mayor cobertura
de `average_age_years`, luego la más desagregada. Las descartadas se reportan en la
respuesta. De 25 documentos, 6 estaban extraídos dos veces; cinco de esas segundas lecturas
venían vacías y solo Aeroméxico inflaba el conteo.

### Cadena financiera

| Servicio | Taxonomía | Escribe |
|---|---|---|
| `extraccion-xbrl-10k` | `us-gaap` | `financial_raw_10k/` |
| `extraccion-xbrl-20f` | `ifrs-full` | `financial_raw_20f/` |
| `extraccion-financiera-china` | `cas-china`, `hkfrs` | `financial_raw_china/` |
| `normalizacion-financiera` | — | `financial_fact`, `financial_fact_raw` |

**Detalle que importa.** El extractor XBRL consultaba solo los tres estados principales, y
el footnote de arrendamientos bajo ASC 842 no está ahí. En el 10-K de American los tres
estados exponen 2 tags de leasing; el universo completo de hechos expone 22, incluida la
escalera de vencimientos. Ese fallback habilitó la señal de acantilado de vencimientos,
central para la línea de gestión de arrendamientos.

---

## 4. Normalización

### Identidad de aerolínea

El pipeline escribía el nombre literal que devolvía Gemini leyendo el filing. Como el
modelo lee el nombre del propio documento, cada corrida producía variantes: convivían
`United` y `United Airlines`, `Copa Holdings` y `Copa Airlines`, `Federal Express` y
`FedEx`. En las cuatro tablas de flota llegaron a coexistir **36 variantes para 30
entidades reales**.

`config/dim_airline.json` resuelve el nombre a un `airline_id` canónico y lo liga al
emisor que reporta finanzas. `parent_id` pliega subsidiarias —American Eagle en AAL,
HK Express en Cathay— sin perder el detalle por entidad.

- 30 entidades con 103 variantes indexadas, sin ambigüedades
- 100% de resolución en producción; los nombres no reconocidos se reportan como
  advertencia, no se descartan en silencio
- Recuperó 1,870 aeronaves que la fragmentación había perdido (de 10,401 a 12,271)

### Taxonomía de aeronaves

La lógica que clasifica la flota vivía solo como columnas calculadas dentro de Power BI,
así que ningún consumidor SQL podía usarla: `737-800`, `Boeing 737-800`, `B737 Series` y
`B737` eran cosas distintas.

Se portó a una UDF de BigQuery más una tabla de atributos, verificada contra los **133
strings crudos distintos** del modelo: los 133 producen el mismo modelo canónico que el
DAX original.

```
dataset_integrado.normalizar_modelo_aeronave(aircraft_type) -> modelo canonico
dim_aircraft_type: 48 modelos x {fabricante, categoria, generacion,
                                 vida de diseño, objetivo de reemplazo}
```

Al portarlo se corrigieron tres defectos del original: filas muertas que `Aircraft_Model`
nunca emitía, 17 modelos que caían a un fallback genérico subestimando la vida de widebody
y cargueros, y dos implementaciones distintas de `Manufacturer` que podían clasificar el
mismo tipo de forma diferente según la tabla.

### Métricas financieras

El crosswalk mapea tags de tres taxonomías a métricas canónicas. La normalización convierte
a millones de USD usando `fx_rates`, filtra al último ejercicio de cada empresa y resuelve
las fórmulas derivadas de forma iterativa.

| Rol del tag | Comportamiento | Por qué |
|---|---|---|
| `total` | Gana solo | El tag ya es el agregado. Sumarlo a sus partes duplicaba: American reportaba 14,031 de pasivo por leasing cuando el valor era 7,068 |
| `componente` | Se suman entre sí | Solo si no hay un total declarado |
| *(sin rol)* | Se toma uno solo | Alternativas que miden lo mismo por vías distintas. Sumar efectivo con el tag que ya incluye el restringido no tiene sentido |

**Convenciones alineadas con us-gaap.** Tres criterios de CAS no eran comparables:
`net_income` pasa al atribuible a la matriz —China Southern reportaba 2,685M cuando el
comparable son 857M—; `capex` se emite negativo como salida de caja, invirtiendo la
convención positiva de CAS —y se unifica en la normalización, porque el tag de origen
decide si viene firmado o como importe de pago: sin unificarlo quedaban 17 empresas
negativas y 3 positivas, y `capex_depreciation_ratio` no era comparable—; e `interest_expense` se separa del resultado financiero neto.

**Fallbacks.** Cuando una métrica no viene etiquetada pero es derivable de otras, se
sintetiza marcándola como `calculated_fallback`: `total_debt` de sus componentes,
`operating_lease_liability` de `lease_liabilities` bajo CAS/IFRS 16, y `total_liabilities`
de `total_assets - equity` cuando el emisor no la etiqueta o solo trae la porción corriente.

---

## 5. Modelo analítico

`v_fleet_normalized` resuelve la flota contra las dos dimensiones y materializa la vida
útil remanente y el estado de reemplazo. `v_prospect_score` agrega por grupo operador.

| Componente | Peso | Señal |
|---|---|---|
| Exposición a leasing | 35% | % operating y finance lease, concentración de vencimientos a 3 años |
| Presión de reemplazo | 30% | % de flota en ventana de reemplazo, vida remanente ponderada |
| Complejidad de portafolio | 15% | nº de modelos y fabricantes, convivencia de generaciones |
| Actividad de transición | 12% | order book sobre flota, ponderado por proximidad |
| Apalancamiento | 8% | deuda neta sobre EBITDA, como modificador |

`v_financial_trend` es la lectura complementaria: en vez de inferir la transición de una
foto, la **mide**. `extraccion-xbrl-10k` lee tres ejercicios de cada filing —los
comparativos vienen dentro del propio XBRL— y esa serie vive en `financial_fact_history`.
La vista compara los extremos y clasifica el movimiento:

| Señal | Cuándo | Qué significa comercialmente |
|---|---|---|
| Entrada de flota arrendada | pasivo por arrendamiento +15% anual y creciendo más rápido que los ingresos | Rotación hacia leasing en curso |
| Devolución de flota arrendada | pasivo por arrendamiento −10% anual | Condiciones de devolución y transiciones entre operadores |
| Renovación con capital propio | capex sobre depreciación ≥ 1.2 con arrendamiento estable | Gestión técnica de activos nuevos |
| Sin renovación | capex sobre depreciación ≤ 0.7 | Flota envejeciendo sin plan visible |

No es un componente del score, y a propósito: solo las emisoras SEC tienen más de un
ejercicio, porque las aerolíneas chinas se extraen de un reporte por año y hoy solo existe
2025. Meterla dentro penalizaría a once aerolíneas por falta de datos, no por falta de
señal. El consultor lee las dos.

**Anclas absolutas, no percentiles.** Con percentiles el score de una aerolínea cambiaría
al agregar o quitar otras del universo, lo que impide comparar entre corridas. Con anclas,
un 70 significa lo mismo este trimestre y el siguiente.

**Tamaño separado del ajuste.** La primera versión los mezclaba y la lista quedó dominada
por las grandes, mientras Frontier —100% arrendada, el prospecto de leasing más limpio del
universo— caía al puesto 15. Un solo ranking no puede responder *cuánto encaja* y *cuánto
vale* a la vez, así que se separan en `segmento_prospecto` y `tier_tamano`.

---

## 6. Visualización

El modelo `SEC China Dashboard_v3` consume las vistas por conexión directa a BigQuery.
Tiene 9 tablas, 33 medidas y 6 relaciones.

La migración fue **estrictamente aditiva**: se agregaron las cuatro tablas nuevas, sus
relaciones y 15 medidas, sin tocar las tablas ni las medidas anteriores. Las páginas
existentes siguen funcionando sobre los datos que ya usaban.

Los artefactos de migración están en `powerbi/`: expresiones M por tabla, medidas DAX
corregidas y los pasos.

**Trampa al agregar tablas desde fuera.** El motor local de Power BI Desktop no infiere el
esquema de una tabla M creada desde una herramienta externa: queda con la sola columna de
sistema `RowNumber` y ni siquiera se puede consultar. Hay que declarar cada columna
explícitamente. Además `dim_airline` expone dos columnas de tipo array sin equivalente
escalar en el modelo tabular, que hay que excluir en la expresión M.

---

## 7. Operación

### Ejecución

| Disparador | Frecuencia | Qué corre |
|---|---|---|
| `revision-anual-sec` | 1 de ene, abr, jul y oct | Cloud Workflow `pipeline-anual`: las 8 fases |
| `revision-8k` / `revision-6k` | Diaria, 08:00 | Monitoreo de eventos SEC por RSS |
| `revision-diaria` | Diaria, 08:00 | Monitoreo de anuncios de aerolíneas chinas |

### Despliegue

Cuatro workflows escuchan `push` a `main`; los 22 de servicio son reutilizables
(`workflow_call`) y los invoca el orquestador.

| Workflow | Se dispara con | Qué hace |
|---|---|---|
| `deploy-cloud-run` | `functions/**` | Detecta qué servicios cambiaron y los despliega de uno en uno |
| `sync-config-gcs` | `config/**` | Respalda lo que hay en GCS y sube la versión del repo |
| `aplicar-sql-bigquery` | `sql/**` | Aplica UDF y vistas en orden de dependencia, y verifica que devuelvan filas |
| `deploy-pipeline-anual-workflow` | `workflows/**` | Despliega la definición del Cloud Workflow |

**Por qué el despliegue es secuencial.** Cada servicio tenía su propio disparador y un
merge que tocara varios lanzaba todos en paralelo. Cinco a la vez agotaban la cuota de
`Build and Operation Get requests per minute` de Cloud Build. Un concurrency group
compartido no sirve: GitHub cancela el run pendiente anterior cuando llega otro al mismo
grupo, así que de cinco encolados sobrevivirían el primero y el último y tres quedarían
sin desplegar en silencio. De ahí el encadenado explícito con `needs`.

Esa misma cancelación afecta al propio orquestador, que también sostiene un solo run en
cola. Por eso la base de comparación es el `head_sha` del último run exitoso y no el push
anterior: si un run queda cancelado en cola, sus cambios entran en la siguiente corrida en
vez de perderse.

El orquestador acepta un input manual: vacío despliega lo que cambió, un nombre despliega
solo ese servicio, `todos` fuerza el despliegue completo.

### Puntos de retorno

- Snapshots de BigQuery antes de cada carga con `WRITE_TRUNCATE`, retención de 30 días
- Respaldos automáticos de `config/` en `gs://bucket-edgar/config/backup/` en cada sync
- Archivado anual de los prefijos de origen por `respaldo-anual`, que garantiza que los
  JSON de años viejos no contaminen la recarga
- TMSL completo del modelo de Power BI previo a la migración

---

## 8. Validaciones

El pipeline verifica su propia salida en cada corrida y devuelve las advertencias en la
respuesta HTTP, no solo en el log.

| Comprobación | Estado actual |
|---|---|
| Identidad de flota resuelta | 319 de 319 filas con `airline_id` |
| Identidad financiera resuelta | 24 de 24 empresas mapeadas |
| Identidad contable `activo = pasivo + patrimonio` | 24 de 24 cuadran |
| Cobertura mínima de métricas Tier 1 | Sin advertencias |
| Tags sin mapear en el crosswalk | 0 |
| Tipos de aeronave sin clasificar | 0, salvo filas agregadas del reporte |

---

## 9. Limitaciones conocidas

Nada de esto está roto; son fronteras del sistema tal como está hoy.

**Sin serie temporal de flota.** La composición de flota se declara en una sección
narrativa del reporte, no en XBRL, así que recuperar ejercicios anteriores exige descargar
los filings viejos y volver a pasarlos por la cadena de extracción completa. Hoy toda la
flota es del ejercicio 2025 y la transición se *infiere* de esa foto.

Lo que sí quedó medido es el lado financiero: los comparativos vienen dentro del propio
XBRL, y `v_financial_trend` mide el movimiento del arrendamiento, la deuda y el capex sobre
tres ejercicios para las 16 emisoras SEC. Las ocho aerolíneas asiáticas siguen con un solo
año, porque su fuente es un reporte anual por ejercicio.

**Tipos de cambio faltantes.** `fx_rates` se mantiene a mano y no tiene BRL para 2023 ni
2024. La conversión cae a la tasa más cercana, así que la serie de Azul convierte los tres
ejercicios con la tasa de 2025 y un movimiento cambiario podría leerse como movimiento del
negocio. La normalización ahora reporta qué conversiones usaron una tasa sustituta.

**Aeroméxico pierde detalle de modelo.** Su 20-F quedó extraído dos veces bajo prefijos
distintos, y el pipeline sumaba ambas lecturas: 355 aeronaves donde hay 165. Se resolvió
agrupando por documento fuente (ver *Deduplicación* abajo), pero la lectura que se conserva
—la única con edades— agrupa como `Boeing Fleet (B787/B737)` en vez de separar MAX de NG y
787-8 de 787-9. Se privilegió la edad porque presión de reemplazo y exposición a leasing
pesan 65% del score contra 15% de complejidad de portafolio, y la lectura descartada solo
detallaba 26 de 164 aeronaves. Recuperar ambas cosas exige re-extraer el filing.

**Páginas de Power BI.** La capa de reporte vive dentro del `.pbix` y no es accesible desde
herramientas de modelo. Las tablas y medidas están listas; construir las páginas es
trabajo manual.

### Resueltas

**Flota inventada en Volaris.** Su 20-F no trae los marcadores de página que usa el
extractor de sección: el documento entero cayó en dos páginas, `ITEM 4` e `ITEM 5` casaron
ambos dentro del índice, y la sección aislada fue la portada —9 KB sin una sola mención de
aeronaves—. Aguas abajo eso no falló: el modelo llenó el vacío con siete filas fechadas en
2023 que no están en el filing, y como el score filtra por `report_year = 2025`, Volaris
desaparecía del ranking sin ninguna señal. El extractor ahora cae a una búsqueda por
posición de texto cuando la segmentación por páginas no da contenido de flota, y **omite la
sección antes que emitir una vacía** que el modelo rellenaría. Volaris quedó en 155
aeronaves reales, 97% en operating lease, y entra al ranking en tercer lugar. Auditados los
31 documentos del corpus contra sus secciones de origen: era el único caso.

**Vencimientos de leasing bajo IFRS.** La escalera por año solo la etiqueta us-gaap. Se
agregó `lease_maturity_concentracion_1y`, derivada del split corriente / no corriente que
sí existe en ambas taxonomías: la señal pasó de 11 a 15 aerolíneas.

**Truncamiento por thinking alto.** Los cinco servicios en `thinking_level="HIGH"` detectaban
`MAX_TOKENS` pero devolvían igual el JSON cortado, que aguas abajo aparecía como un genérico
"no es JSON válido". Ahora repiten la llamada con `LOW` —el mismo reintento que ya tenía
`extraccion-json-china`—. No se bajó el nivel de entrada porque en 90 días ninguno de los
cinco se truncó: sería perder calidad sin ganar nada.

**Merges muy seguidos.** GitHub sostiene un solo run en cola por grupo de concurrency, así
que con tres merges seguidos el de en medio se cancelaba. Como la base de comparación era
`github.event.before` —el push anterior—, los servicios de ese merge no se desplegaban nunca:
quedaban en `main` con Cloud Run sirviendo la revisión vieja y sin ninguna señal. Ahora la
base es el `head_sha` del último run exitoso del propio orquestador, así que un run cancelado
deja sus cambios pendientes para la siguiente corrida.

---

## Referencia rápida

```
config/     dim_airline.json          identidad canonica de aerolineas
            dim_aircraft_type.json    taxonomia de modelos
            xbrl_crosswalk.json       tags -> metricas canonicas
            financial_metrics.json    definicion y formulas de metricas

sql/        normalizar_modelo_aeronave.sql
            v_fleet_normalized.sql
            v_prospect_score.sql

powerbi/    m/*.m                     expresiones Power Query
            dax/measures.dax          medidas corregidas
            README.md                 pasos de migracion

functions/  22 servicios de Cloud Run
workflows/  pipeline-anual.yaml       orquestacion trimestral
```

Los archivos de `config/` se leen desde `gs://bucket-edgar/config/` en tiempo de ejecución,
no del repo. El workflow `sync-config-gcs` los mantiene sincronizados; sin él, un cambio en
el repo no surte efecto.
