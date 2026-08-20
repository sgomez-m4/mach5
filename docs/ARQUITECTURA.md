# Arquitectura del Sistema — Monitoreo y Análisis de Flotas Aéreas

> **Proyecto GCP:** `mach5-gemini-project` (número de proyecto `846229407230`)
> **Repo:** `mach5` (monorepo en GitHub) · **Última actualización:** 2026-08-20

Este documento describe **qué hace cada uno de los 21 servicios de Cloud Run**, cómo se
relacionan entre sí y qué infraestructura comparten. Está pensado para dos tipos de
lectores:

- **Personas sin conocimientos técnicos** de Python o Cloud: la sección
  [Entendiendo el sistema sin ser experto](#entendiendo-el-sistema-sin-ser-experto)
  explica los conceptos con una analogía.
- **Personas con conocimientos técnicos**: cada servicio tiene una ficha con región,
  CPU/memoria, entrypoint, variables de entorno, URLs, modelos de IA usados y el flujo
  de datos exacto entre carpetas de GCS y tablas de BigQuery.

---

## Índice

1. [Resumen ejecutivo](#resumen-ejecutivo)
2. [Entendiendo el sistema sin ser experto](#entendiendo-el-sistema-sin-ser-experto)
3. [Glosario técnico](#glosario-técnico)
4. [Arquitectura general](#arquitectura-general)
5. [Infraestructura compartida](#infraestructura-compartida)
6. [Servicios — Visión de conjunto](#servicios--visión-de-conjunto)
7. [Fichas técnicas por servicio](#fichas-técnicas-por-servicio)
8. [Automatización del pipeline anual (Cloud Workflows)](#automatización-del-pipeline-anual-cloud-workflows)
9. [Flujo de datos en detalle](#flujo-de-datos-en-detalle)
10. [Base de datos BigQuery](#base-de-datos-bigquery)
11. [Automatización (Cloud Scheduler)](#automatización-cloud-scheduler)
12. [Despliegue e integración continua](#despliegue-e-integración-continua)
13. [Seguridad y acceso](#seguridad-y-acceso)
14. [Preguntas frecuentes](#preguntas-frecuentes)

---

## Resumen ejecutivo

El sistema **monitorea y analiza la flota de aviones de las aerolíneas** a partir de dos
grandes fuentes de información pública:

1. **Reportes anuales y eventos de aerolíneas chinas** (bolsas de Shanghái/Shenzhen y
   listados en Hong Kong), publicados en el portal **CNINFO**.
2. **Documentos que las aerolíneas entregan a la SEC de Estados Unidos**: formularios
   **10-K** (informe anual de aerolíneas estadounidenses), **20-F** (informe anual de
   aerolíneas extranjeras listadas en EE. UU.), y los formularios de eventos **8-K** y
   **6-K** (avisos importantes: cambios de flota, financiamientos, etc.).

El objetivo es **convertir esa información no estructurada** (PDF, HTML, documentos de
bolsa) en **datos estructurados** (JSON y tablas en BigQuery) que alimentan el análisis
de negocio de la flota: cantidad de aviones por aerolínea, tipos de aeronave, edad
promedio, aviones arrendados vs. propios, pedidos futuros (order book), etc.

Para lograrlo, los documentos pasan por una **cadena de procesamiento por etapas**:
primero se descargan, luego se transforman a texto plano (Markdown), luego se extraen
las secciones relevantes, después se convierten a JSON con la ayuda de **Gemini
(IA de Google)**, y finalmente se cargan en **BigQuery** para su análisis. Además,
tres servicios corren **todos los días a las 8:00 a. m.** para revisar si hay
**anuncios nuevos** en las bolsas y, si detectan algo relevante para la flota, **envían
un correo electrónico** automáticamente.

Desde agosto de 2026 el sistema también extrae **métricas financieras** (ingresos,
EBITDA, deuda, capex, caja, márgenes, etc.) de los mismos reportes: los 10-K y 20-F se
leen directamente del **XBRL** que publica la SEC (datos exactos, sin IA), mientras que
las aerolíneas chinas (A/H) se extraen con Gemini por no publicar XBRL. Todos los datos
crudos se **normalizan a un esquema único en USD millones** mediante un crosswalk de
etiquetas y se guardan en BigQuery (`financial_fact_raw` y `financial_fact`).

Todo el código vive en un repositorio de GitHub, y **cada cambio en el código se
despliega automáticamente** a Google Cloud sin intervención manual (CI/CD).

---

## Entendiendo el sistema sin ser experto

Imagina que quieres mantener actualizado el inventario de todos los aviones de varias
aerolíneas del mundo, leyendo únicamente sus informes públicos anuales y sus avisos de
prensa. Esa tarea es imposible a mano: los informes son PDFs de cientos de páginas, en
distintos idiomas (inglés, chino simplificado, chino tradicional) y con formatos
diferentes.

El sistema funciona como una **fábrica con una línea de ensamblaje**:

```
   (1) Descargar    → (2) Convertir    → (3) Filtrar      → (4) Extraer datos  → (5) Unificar y guardar
   el documento        el PDF a texto     la sección que      con ayuda de IA      en la base de datos
                       legible            nos interesa        (estructura JSON)    (BigQuery)
```

Cada paso lo hace un servicio distinto (una función en la nube), y el producto de cada
paso queda guardado en una **carpeta de almacenamiento compartida** (Google Cloud
Storage), de modo que el siguiente paso lo recoge de ahí.

**Ejemplo concreto con una aerolínea china (Air China):**

1. Un servicio **descarga** el informe anual (PDF) de la bolsa y lo guarda en la carpeta
   `anual-china/`.
2. Otro servicio **convierte** ese PDF a texto legible (Markdown) y lo guarda en
   `anual-china-md/`.
3. Otro servicio **filtra** y deja únicamente la sección donde la aerolínea habla de su
   flota, guardándola en `flota-aerolineas-md/`.
4. Otro servicio **le pasa esa sección a Gemini** (IA de Google) con una instrucción muy
   precisa ("extrae de este texto: cuántos aviones, de qué modelo, cuántos en leasing,
   qué edad promedio…") y guarda el resultado como **JSON** (datos organizados) en
   `flota-aerolineas-json/`.
5. Por último, un servicio **lee los JSON de todas las aerolíneas y todos los años**, los
   limpia y los **carga en BigQuery** (la base de datos de análisis).

A la vez, hay tres **vigilantes** que cada mañana revisan si hay anuncios nuevos en las
bolsas (formularios 8-K, 6-K y avisos chinos). Si aparece algo que pueda afectar la
flota, **te llega un correo** con un resumen y un análisis hecho con IA.

Los conceptos técnicos clave (qué es Cloud Run, Gemini, BigQuery, etc.) están
explicados en el [glosario](#glosario-técnico).

---

## Glosario técnico

| Concepto | Qué es (en palabras simples) |
|---|---|
| **Cloud Run** | Servicio de Google Cloud que ejecuta tu código (en este caso Python) cuando recibe una petición HTTP. No gestionas servidores: Google los administra. Cada servicio tiene una URL pública. |
| **Cloud Functions / functions-framework** | Forma ligera de escribir código que responde a una URL. Aquí, cada función es un servicio de Cloud Run que expone una URL y ejecuta una función Python (el "entrypoint"). |
| **GCS (Google Cloud Storage)** | Almacenamiento de archivos en la nube (como un disco duro remoto). Se usa como "carpeta de paso" entre etapas del flujo. |
| **BigQuery** | Base de datos analítica de Google para hacer consultas sobre millones de filas de forma rápida. Aquí es donde termina toda la información de la flota. |
| **Vertex AI / Gemini** | Plataforma de IA de Google. **Gemini** es el modelo de lenguaje que lee texto y extrae/estructura la información de la flota. Se accede vía Vertex AI. |
| **Cloud Scheduler** | Despertador / cron programable: ejecuta una URL a la hora programada (aquí todos los días 08:00, hora de Bogotá). |
| **EDGAR** | Base de datos pública de la SEC (la entidad reguladora del mercado de EE. UU.) con todos los documentos que las empresas entregan. |
| **CNINFO** | Portal público de las bolsas de valores chinas (Shanghái SSE, Shenzhen SZSE) donde las aerolíneas publican sus reportes anuales. |
| **Formulario 10-K** | Informe anual detallado de una aerolínea **estadounidense** (American, Delta, United, Southwest…). |
| **Formulario 20-F** | Informe anual de una aerolínea **extranjera** listada en EE. UU. (LATAM, Copa, Volaris, Aeroméxico, Azul…). |
| **Formulario 8-K** | Aviso de eventos importantes de una aerolínea estadounidense (no se espera un año para publicarlo; se reporta cuando pasa algo). |
| **Formulario 6-K** | Equivalente del 8-K pero para aerolíneas extranjeras listadas en EE. UU. |
| **A-Share / H-Share** | Acciones de empresas chinas. **A-Share**: se negocian en China (SSE/SZSE). **H-Share**: empresas chinas listadas en la bolsa de Hong Kong. |
| **CIK / Ticker** | El **CIK** es el identificador numérico único de una empresa en la SEC. El **ticker** es su símbolo bursátil (ej. `DAL` para Delta). |
| **Markdown (MD)** | Formato de texto plano con estructura ligera (encabezados, tablas), fácil de procesar por IA y por código. |
| **JSON** | Formato de datos organizado (clave-valor) que las máquinas entienden fácilmente. Es el "producto final" de la extracción con IA. |
| **SA (Service Account)** | Identidad de máquina en Google Cloud: credenciales que un servicio usa para actuar con permisos. |
| **OIDC** | Protocolo de autenticación. Aquí se usa para que Cloud Scheduler se autentique frente a Cloud Run con identidad (SA). |
| **WIF (Workload Identity Federation)** | Mecanismo para que GitHub se autentique en Google Cloud **sin guardar llaves de largo plazo**. |
| **SMTP / App Password** | Protocolo para enviar correos. El sistema usa una cuenta Gmail con una "contraseña de aplicación" para enviar los correos. |

---

## Arquitectura general

Los 21 servicios de Cloud Run se organizan en **4 ramas de datos (pipelines)** que
alimentan un **pipeline final de unificación**, más **3 servicios de monitoreo diario**
que son independientes. Una **tarea trimestral programada** (Cloud Workflows +
Cloud Scheduler) respalda los datos y re-ejecuta todo el pipeline anual
automáticamente.

```
                          ┌────────────────────────────────────────────────────┐
                          │              FUENTES DE INFORMACIÓN               │
                          └────────────────────────────────────────────────────┘
                                  │                       │                     │
                    ┌─────────────▼───────┐   ┌───────────▼──────────┐  ┌───────▼─────────┐
                    │  CNINFO (bolsas     │   │  EDGAR (SEC)         │  │ EDGAR (SEC)     │
                    │  de China A/H)      │   │  formularios 10-K    │  │ formularios     │
                    │  + anuncios diarios │   │  (aerolíneas US)     │  │ 20-F/8-K/6-K    │
                    └─────────────┬───────┘   └───────────┬──────────┘  └───────┬─────────┘
                                  │                       │                     │
              ┌───────────────────▼──────┐   ┌────────────▼──────────┐  ┌───────▼──────────────┐
              │   RAMA AEROLÍNEAS        │   │  RAMA 10-K (item 2)   │  │  RAMA 20-F (item 4) │
              │   CHINAS                 │   │                      │  │                     │
              └───────────────────┬──────┘   └────────────┬──────────┘  └───────┬──────────────┘
                                  │                       │                     │
        anual-china/    ┌─────────▼──────┐    10k-md/      ▼                     ▼
        anual-china-md/ │               │    10k-item2-md/ ┌───────────────┐ 20f-md/
        flota-aerolineas-md/            │    10k-item2-json/│ extraccion-  │ 20f-flota-md/
        flota-aerolineas-json/          │                   │ ...           │ 20f-flota-json/
                                  │     │                   └───────────────┘
                                  └─────┼──────────────────────────┬──────────────────────┘
                                        │                          │
                                        └───────────┐     ┌─────────┘
                                                    ▼     ▼
                                      ┌─────────────────────────────┐
                                      │   ejecutar-pipeline-anuales │
                                      │   (unifica y carga en       │
                                      │    BigQuery)                │
                                      └─────────────┬───────────────┘
                                                    ▼
                                      ┌─────────────────────────────┐
                                      │   BigQuery:                 │
                                      │   current_fleet_fact        │
                                      │   order_book_fact           │
                                      └─────────────────────────────┘

  RAMA FINANCIERA (4ta rama, desde 2026-08):
    extraccion-xbrl-10k       10-K → XBRL (us-gaap)         → financial_raw_10k/
    extraccion-xbrl-20f       20-F → XBRL (ifrs-full)       → financial_raw_20f/
    extraccion-xbrl-china-a   China A-Share → Gemini        → financial_raw_china_a/
    extraccion-xbrl-china-h   China H-Share → Gemini        → financial_raw_china_h/
        │
        ▼
    normalizacion-financiera  crosswalk + fx_rates → USD_m  → financiera-normalizada-json/
        │                                                  + BigQuery (financial_fact_raw, financial_fact)

  MONITOREO DIARIO (independiente del pipeline anual):
    rss-8k  ──► revisa Form 8-K  ──► correo si hay novedad
    rss-6k  ──► revisa Form 6-K  ──► correo si hay novedad
    revision-diaria-china ──► revisa anuncios de bolsa china ──► correo si hay novedad

  TAREA TRIMESTRAL ORQUESTADA (Cloud Workflows + Cloud Scheduler):
    revision-anual-sec (cron 0 6 1 1,4,7,10 *)
      └─► workflow pipeline-anual:
          fase1_respaldo      respaldo-anual  (snapshots BQ + archivo GCS)
          fase2_ingesta       descarga-anual-china / extraccion-10k-md / extraccion-20f-md  (paralelo)
          fase3_china         parsing-md-china → extraer-seccion-china → extraccion-json-china
          fase4_10k           extraccion-item2-10k → extraccion-json-item2
          fase5_20f           extraccion-item4-20f → extraccion-json-item4
          fase6_unificar      ejecutar-pipeline-anuales  (→ BigQuery)
          fase7_financiera    extraccion-xbrl-10k ∥ extraccion-xbrl-20f ∥ extraccion-xbrl-china-a ∥ extraccion-xbrl-china-h  (paralelo)
          fase8_normalizar    normalizacion-financiera  (→ BigQuery)
```

> Nota: cada carpeta (`anual-china/`, `10k-md/`, etc.) es un **prefijo dentro del mismo
> bucket `gs://bucket-edgar`**. Cada etapa lee de un prefijo y escribe en otro.

---

## Infraestructura compartida

Todos los servicios comparten la siguiente infraestructura en el proyecto
`mach5-gemini-project`:

### Proyecto y regiones

| Recurso | Valor |
|---|---|
| Proyecto GCP | `mach5-gemini-project` (`846229407230`) |
| Región principal (us-east1) | 18 de 21 servicios |
| Región secundaria (europe-west1) | 3 de 21 servicios (`rss-6k`, `ejecutar-pipeline-anuales`, `extraccion-20f-md`) |

### Storage (bucket de datos)

- **Bucket único de datos:** `gs://bucket-edgar`
  - Carpeta raíz con los archivos de control y resultados finales:
    - `historial_reportes.txt` — IDs de 8-K ya procesados (deduplicación de `rss-8k`).
    - `historial_reportes_6k.txt` — IDs de 6-K ya procesados (deduplicación de `rss-6k`).
    - `historial_anuncios_china.txt` — IDs de anuncios chinos ya procesados (deduplicación de `revision-diaria-china`).
    - `Aerolineas_Leases_Refinado_Final.csv` — salida de tablas (dataset de leases).
  - `archivo/backup/YYYYMMDD/` — copia de respaldo trimestral de todos los prefijos del
    pipeline (la crea `respaldo-anual` antes de cada corrida anual).
  - Carpetas intermedias por rama (detalle en [Flujo de datos](#flujo-de-datos-en-detalle)).
- **Buckets de "source"** (`gs://run-sources-mach5-gemini-project-us-east1` y
  `...-europe-west1`): los usa Cloud Run internamente para guardar el código de cada
  deploy. No son de uso directo.

### IA (Vertex AI — Gemini)

| Uso | Detalle |
|---|---|
| Modelo principal | `gemini-3-flash-preview` (en us-east1) |
| Modelos de contingencia | `gemini-2.5-flash` (fallback en `extraccion-json-item4`) y `gemini-2.0-flash` (fallback en `extraccion-tablas-item2`) |

> **Por qué cambió el modelo:** se migró de `gemini-2.5-flash` a `gemini-3-flash-preview`
> por errores de cuota `429 RESOURCE_EXHAUSTED` en la región us-east1.

### Base de datos (BigQuery)

- **Dataset:** `dataset_integrado`
- **Tablas de flota (2):**
  - `current_fleet_fact` — inventario actual de flota (294 filas).
  - `order_book_fact` — pedidos futuros de aviones (148 filas).
- **Tablas financieras (rama financiera, desde 2026-08):**
  - `financial_fact_raw` — un registro por fact crudo extraído (trazabilidad completa:
    taxonomía, fuente, tag, valor, moneda, periodo, `raw_json`).
  - `financial_fact` — métricas canónicas por empresa/año/métrica en **USD millones**
    (`value_usd_m`, `data_source` = `xbrl_tag`/`calculated`/`gemini_fallback`,
    `formula_used`, `source_tags`). WRITE_TRUNCATE en cada corrida.
  - `xbrl_crosswalk` — catálogo de mapeo `(taxonomy, source_tag) → canonical_metric`
    (146 filas; espejo de `config/xbrl_crosswalk.json`).
  - `fx_rates` — tasas de cambio anuales para conversión a USD.
- Detalle de esquemas en la sección [Base de datos BigQuery](#base-de-datos-bigquery).
- **Dataset histórico:** `dataset_historico` — guarda los **snapshots inmutables** que
  crea `respaldo-anual` antes de cada corrida anual (tablas
  `current_fleet_fact_YYYYMMDD`, `order_book_fact_YYYYMMDD`), más las tablas
  históricas `flota_historica_current_fleet_fact` / `flota_historica_order_book_fact`.

### Orquestación (Cloud Scheduler + Cloud Workflows)

- Tres tareas diarias `0 8 * * *` (hora de Bogotá), todas en us-east1, que invocan los
  servicios de monitoreo usando la SA `cloud-scheduler-invoker` con OIDC. Detalle en la
  sección [Automatización](#automatización-cloud-scheduler).
- Una **tarea trimestral** `revision-anual-sec` (`0 6 1 1,4,7,10 *`) que ejecuta el
  workflow de Cloud Workflows `pipeline-anual` usando la SA `workflows-invoker`.
  El workflow orquesta el respaldo y todo el pipeline anual con la SA `workflows-runner`.

### Identidades (Service Accounts)

| SA | Rol | Uso |
|---|---|---|
| `846229407230-compute@developer.gserviceaccount.com` | SA de cómputo por defecto | Es la **SA de runtime** de todos los servicios de Cloud Run (la que usan en tiempo de ejecución). |
| `github-deployer@mach5-gemini-project.iam.gserviceaccount.com` | `roles/run.developer` + permisos de deploy | La usa GitHub Actions vía WIF para desplegar. |
| `cloud-scheduler-invoker@mach5-gemini-project.iam.gserviceaccount.com` | `roles/run.invoker` sobre los 3 servicios de monitoreo | La usa Cloud Scheduler para invocar los servicios con autenticación. |
| `workflows-runner@mach5-gemini-project.iam.gserviceaccount.com` | `roles/run.invoker` (proyecto) + `roles/logging.logWriter` | SA de ejecución del workflow `pipeline-anual`: invoca cada servicio de Cloud Run y escribe logs del workflow. |
| `workflows-invoker@mach5-gemini-project.iam.gserviceaccount.com` | `roles/workflows.invoker` | La usa Cloud Scheduler (`revision-anual-sec`) para lanzar ejecuciones del workflow. |

> **Permisos BigQuery de la SA de cómputo:** para el borrado/recreación de snapshots,
> la SA de cómputo necesita `roles/bigquery.dataOwner` (el permiso
> `bigquery.tables.deleteSnapshot` **no** lo otorga `dataEditor`).

---

## Servicios — Visión de conjunto

| # | Servicio | Región | CPU / Mem | Entrypoint (función Python) | Rama | Rol |
|---|---|---|---|---|---|---|
| 1 | `descarga-anual-china` | us-east1 | 1 / 512Mi | `extraer_reportes_anuales` | China | Descarga PDFs de reportes anuales |
| 2 | `parsing-md-china` | us-east1 | 2 / 2Gi | `process_china_pdfs_http` | China | Convierte PDF → Markdown |
| 3 | `extraer-seccion-china` | us-east1 | 1 / 512Mi | `procesar_flota_aerolineas` | China | Filtra sección de flota |
| 4 | `extraccion-json-china` | us-east1 | 1 / 1Gi | `extract_fleet_china_http` | China | Sección flota → JSON (Gemini) |
| 5 | `extraccion-10k-md` | us-east1 | 1 / 1Gi | `run_edgar_pipeline` | 10-K | Descarga 10-K → Markdown |
| 6 | `extraccion-item2-10k` | us-east1 | 1 / 512Mi | `procesar_bucket_edgar` | 10-K | Extrae Item 2 de 10-K |
| 7 | `extraccion-json-item2` | us-east1 | 2 / 2Gi | `extract_fleet_http` | 10-K | Item 2 → JSON (Gemini) |
| 8 | `extraccion-tablas-item2` | us-east1 | 2 / 2Gi | `main` | 10-K | Extrae tablas de flota de 10-K |
| 9 | `extraccion-20f-md` | europe-west1 | 2 / 1Gi | `run_edgar_pipeline_20f` | 20-F | Descarga 20-F → Markdown |
| 10 | `extraccion-item4-20f` | us-east1 | 1 / 1Gi | `procesar_bucket_edgar_20f` | 20-F | Extrae Item 4 de 20-F |
| 11 | `extraccion-json-item4` | us-east1 | 2 / 1Gi | `extract_fleet_20f_http` | 20-F | Item 4 → JSON (Gemini) |
| 12 | `ejecutar-pipeline-anuales` | europe-west1 | 1 / 512Mi | `ejecutar_pipeline_flota` | Consolidación | Unifica y carga a BigQuery |
| 13 | `revision-diaria-china` | us-east1 | 1 / 1Gi | `monitorear_aerolineas_chinas` | Monitoreo | Anuncios chinos → correo |
| 14 | `rss-8k` | us-east1 | 1 / 1Gi | `monitorear_sec_event` | Monitoreo | Form 8-K → correo |
| 15 | `rss-6k` | europe-west1 | 1 / 1Gi | `monitorear_sec_event` | Monitoreo | Form 6-K → correo |
| 16 | `respaldo-anual` | us-east1 | 1 / 512Mi | `ejecutar_respaldo_anual` | Respaldo | Snapshots BQ + archivo GCS |
| 17 | `extraccion-xbrl-10k` | us-east1 | 2 / 2Gi | `ejecutar_extraccion_financiera` | Financiera | 10-K → XBRL → `financial_raw_10k/` |
| 18 | `extraccion-xbrl-20f` | us-east1 | 2 / 2Gi | `ejecutar_extraccion_financiera` | Financiera | 20-F → XBRL → `financial_raw_20f/` |
| 19 | `extraccion-xbrl-china-a` | us-east1 | 2 / 2Gi | `ejecutar_extraccion_financiera` | Financiera | China A-Share → Gemini → `financial_raw_china_a/` |
| 20 | `extraccion-xbrl-china-h` | us-east1 | 2 / 2Gi | `ejecutar_extraccion_financiera` | Financiera | China H-Share → Gemini → `financial_raw_china_h/` |
| 21 | `normalizacion-financiera` | us-east1 | 1 / 1Gi | `normalizar_financiera` | Financiera | Crosswalk + fx → USD_m → BigQuery |

---

## Fichas técnicas por servicio

A continuación, cada servicio con su ficha completa: qué hace, con qué entradas, qué
salidas genera, qué modelos de IA usa y qué variables de entorno necesita.

---

### 1. `descarga-anual-china`

**Rol:** Descargar los reportes anuales (PDF) de las aerolíneas chinas desde el portal
CNINFO y guardarlos en GCS.

- **Región / recursos:** us-east1 · 1 CPU / 512 Mi · entrypoint `extraer_reportes_anuales`
- **URL:** `https://descarga-anual-china-upaleadnxq-ue.a.run.app`
- **Entrada:** APIs públicas de CNINFO
  - `http://www.cninfo.com.cn/new/data/szse_stock.json` (acciones SZSE)
  - `http://www.cninfo.com.cn/new/data/hke_stock.json` (acciones HKE / Hong Kong)
- **Aerolíneas monitoreadas (código de bolsa, mercado):**
  | Aerolínea | Código | Mercado |
  |---|---|---|
  | Air China | `601111` | A-Share (SSE) |
  | China Eastern Airlines | `600115` | A-Share (SSE) |
  | China Southern Airlines | `600029` | A-Share (SSE) |
  | Cathay Pacific Airways | `00293` | H-Share (HKE) |
  | Hainan Airlines | `600221` | A-Share (SSE) |
  | Spring Airlines | `601021` | A-Share (SSE) |
  | Juneyao Airlines | `603885` | A-Share (SSE) |
  | China Express Airlines | `002928` | A-Share (SZSE) |
- **Salida:** PDFs en `gs://bucket-edgar/anual-china/` (prefijo destino `anual-china`).
- **Detalles técnicos:** descarga el reporte anual de cada aerolínea, borra antes
  cualquier archivo previo en el bucket con el prefijo `{codigo}_`, y sube el PDF con
  nombre saneado (sin caracteres inválidos).
- **Env vars:** `FUNCTION_TARGET=extraer_reportes_anuales` (solo).

---

### 2. `parsing-md-china`

**Rol:** Convertir los PDFs descargados de las aerolíneas chinas a Markdown (texto
estructurado legible).

- **Región / recursos:** us-east1 · 2 CPU / 2 Gi · entrypoint `process_china_pdfs_http`
- **URL:** `https://parsing-md-china-upaleadnxq-ue.a.run.app`
- **Entrada:** `gs://bucket-edgar/anual-china/` (PDFs, prefijo `PDF_PREFIX`)
- **Salida:** `gs://bucket-edgar/anual-china-md/` (Markdown, prefijo `MD_PREFIX`)
- **Detalles técnicos:** usa la librería de parseo de PDFs (PyMuPDF / `pymupdf4llm`)
  para convertir los PDFs a Markdown. Contiene procedimientos separados para A-Shares
  (API oficial CNINFO: `https://www.cninfo.com.cn/new/information/topSearch/query`) y
  H-Shares. Maneja nombres de aerolínea y limpieza de texto.
- **Env vars:** `FUNCTION_TARGET=process_china_pdfs_http` (solo).

---

### 3. `extraer-seccion-china`

**Rol:** De cada Markdown de reporte anual chino, filtrar únicamente la sección donde la
aerolínea describe su **flota**.

- **Región / recursos:** us-east1 · 1 CPU / 512 Mi · entrypoint `procesar_flota_aerolineas`
- **URL:** `https://extraer-seccion-china-upaleadnxq-ue.a.run.app`
- **Entrada:** `gs://bucket-edgar/anual-china-md/` (prefijo `SOURCE_PREFIX`)
- **Salida:** `gs://bucket-edgar/flota-aerolineas-md/` (prefijo `TARGET_PREFIX`)
- **Detalles técnicos:** recorre los blobs del prefijo origen, divide el contenido en
  páginas y extrae la sección de flota mediante heurísticas de texto.
- **Env vars:** `FUNCTION_TARGET=procesar_flota_aerolineas` (solo).

---

### 4. `extraccion-json-china`

**Rol:** Convertir la sección de flota de las aerolíneas chinas en **JSON estructurado**
usando Gemini.

- **Región / recursos:** us-east1 · 1 CPU / 1 Gi · entrypoint `extract_fleet_china_http`
- **URL:** `https://extraccion-json-china-upaleadnxq-ue.a.run.app`
- **Entrada:** `gs://bucket-edgar/flota-aerolineas-md/` (prefijo `SOURCE_PREFIX`)
- **Salida:** `gs://bucket-edgar/flota-aerolineas-json/` (prefijo `TARGET_PREFIX`)
- **Modelo de IA:** `gemini-3-flash-preview` (Vertex AI)
- **Prompt en español:** instruye a Gemini como "experto en ingeniería de datos aéreos y
  BI, especializado en reportes anuales de aerolíneas chinas (A-Shares y H-Shares)",
  indicando que el texto puede estar en chino simplificado (A-Shares) o tradicional
  (H-Shares), y define el esquema de salida (por ejemplo `fleet_id` con formato
  `airline_year_model_ownership`, `airline` en inglés, etc.).
- **Env vars:** `FUNCTION_TARGET=extract_fleet_china_http` (solo).

---

### 5. `extraccion-10k-md`

**Rol:** Descargar los formularios 10-K de aerolíneas estadounidenses desde EDGAR (SEC)
y convertirlos a Markdown.

- **Región / recursos:** us-east1 · 1 CPU / 1 Gi · entrypoint `run_edgar_pipeline`
- **URL:** `https://extraccion-10k-md-upaleadnxq-ue.a.run.app`
- **Entrada:** API de EDGAR (librería `edgar` de Python, con identidad SEC).
- **Aerolíneas (ticker / CIK):**
  | Aerolínea | Ticker | CIK |
  |---|---|---|
  | American Airlines Group | `AAL` | `0000006201` |
  | Delta Air Lines | `DAL` | `0000027904` |
  | United Airlines Holdings | `UAL` | `0000100517` |
  | Alaska Air Group (incl. Hawaiian) | `ALK` | `0000766421` |
  | Southwest Airlines | `LUV` | `0000092380` |
  | JetBlue Airways | `JBLU` | `0001158463` |
- **Salida:** Markdown en `gs://bucket-edgar/10k-md/`.
- **Detalles técnicos:** usa la librería oficial `edgar` de Python. Incluye
  `SEC_API_IDENTITY` para identificarse ante la SEC (buena práctica de uso del API).
- **Env vars:** `FUNCTION_TARGET=run_edgar_pipeline`, `GEMINI_API_KEY`,
  `SEC_API_IDENTITY` (`Samuel samuel.gomez@mas4aviation.com`), `GCP_PROJECT_ID`.

---

### 6. `extraccion-item2-10k`

**Rol:** Del Markdown de cada 10-K, extraer la sección **Item 2** (Properties —
propiedades / descripción de la flota).

- **Región / recursos:** us-east1 · 1 CPU / 512 Mi · entrypoint `procesar_bucket_edgar`
- **URL:** `https://extraccion-item2-10k-upaleadnxq-ue.a.run.app`
- **Entrada:** todos los blobs `.md` del bucket (`bucket.list_blobs()`) que no terminan
  en `_filtrado.md`.
- **Salida:** `gs://bucket-edgar/10k-item2-md/` con nombre `<base>_Item2_filtrado.md`.
- **Detalles técnicos:** usa la función `extraer_item2_por_paginas(contenido)` para
  extraer la sección Item 2 por páginas. No usa IA.
- **Env vars:** `FUNCTION_TARGET=procesar_bucket_edgar` (solo).

---

### 7. `extraccion-json-item2`

**Rol:** Convertir el Item 2 (descripción de flota) de cada 10-K en **JSON estructurado**
usando Gemini.

- **Región / recursos:** us-east1 · 2 CPU / 2 Gi · entrypoint `extract_fleet_http`
- **URL:** `https://extraccion-json-item2-upaleadnxq-ue.a.run.app`
- **Entrada:** `gs://bucket-edgar/10k-item2-md/` (prefijo `SOURCE_PREFIX`)
- **Salida:** `gs://bucket-edgar/10k-item2-json/` (prefijo `TARGET_PREFIX`)
- **Modelo de IA:** `gemini-3-flash-preview` (Vertex AI)
- **Env vars:** `FUNCTION_TARGET=extract_fleet_http`, `GEMINI_API_KEY`,
  `GCP_PROJECT_ID`.

---

### 8. `extraccion-tablas-item2`

**Rol:** Extraer las **tablas de flota** directamente de los 10-K vía la librería
`edgar`, estructurarlas con Gemini y generar CSV.

- **Región / recursos:** us-east1 · 2 CPU / 2 Gi · entrypoint `main`
- **URL:** `https://extraccion-tablas-item2-upaleadnxq-ue.a.run.app`
- **Entrada:** API de EDGAR (librería `edgar`).
- **Empresas procesadas:** `AAL`, `DAL`, `ALK`, `FDX`, `JBLU`, `ULCC` (con sus CIKs).
- **Salida:** CSV en `gs://bucket-edgar/Edgar/Tablas_Item2_10k/{ticker}_{cik}/` (también
  consolida en `Aerolineas_Leases_Refinado_Final.csv`).
- **Modelos de IA (con contingencia):**
  - Primario: `GEMINI_MODEL_PRIMARY = gemini-3-flash-preview`
  - Fallback: `GEMINI_MODEL_FALLBACK = gemini-2.0-flash`
  - La función `llamar_gemini_con_fallback` prueba primero el modelo primario y, si
    falla, el de contingencia.
- **Detalles técnicos:** requiere las env vars `SEC_API_IDENTITY` (identidad ante la SEC)
  y `GCP_PROJECT_ID`. Autentica contra Vertex AI con la SA de cómputo.
- **Env vars:** `FUNCTION_TARGET=main`, `GEMINI_API_KEY`,
  `SEC_API_IDENTITY` (`Samuel samuel.gomez@mas4aviation.com`), `GCP_PROJECT_ID`.

---

### 9. `extraccion-20f-md`

**Rol:** Descargar los formularios 20-F de aerolíneas extranjeras listadas en EE. UU.
desde EDGAR y extraer la sección de flota a Markdown.

- **Región / recursos:** europe-west1 · 2 CPU / 1 Gi · entrypoint `run_edgar_pipeline_20f`
- **URL:** `https://extraccion-20f-md-upaleadnxq-ew.a.run.app`
- **Entrada:** API de EDGAR (librería `edgar`).
- **Aerolíneas (ticker / CIK / sección de flota):**
  | Aerolínea | Ticker | CIK | Sección |
  |---|---|---|---|
  | Grupo Aeromexico | `AEROMEX` | `0001561861` | Item 4.D |
  | LATAM Airlines Group | `LTM` | `0001047716` | Item 4.B |
  | Volaris | `VLRS` | `0001520504` | Item 4 |
  | Copa Holdings | `CPA` | `0001345105` | Item 4 |
  | Azul SA | `AZUL` | `0001432364` | Item 4.B |
- **Salida:** Markdown en `gs://bucket-edgar/20f-md/` (incluye metadata `folder=20f-md`).
- **Env vars:** `FUNCTION_TARGET=run_edgar_pipeline_20f`,
  `SEC_API_IDENTITY` (`Samuel Gomez samuelgomezl@hotmail.com`).

---

### 10. `extraccion-item4-20f`

**Rol:** Del Markdown de cada 20-F, extraer la sección del **Item 4** (flota).

- **Región / recursos:** us-east1 · 1 CPU / 1 Gi · entrypoint `procesar_bucket_edgar_20f`
- **URL:** `https://extraccion-item4-20f-upaleadnxq-ue.a.run.app`
- **Entrada:** `gs://bucket-edgar/20f-md/` (apunta directo a la carpeta de origen 20-F).
- **Salida:** `gs://bucket-edgar/20f-flota-md/`.
- **Detalles técnicos:** usa `extraer_seccion_20f(content, regex_start, regex_end_text)`
  con expresiones regulares para localizar y recortar la sección de flota.
- **Env vars:** `FUNCTION_TARGET=procesar_bucket_edgar_20f` (solo).

---

### 11. `extraccion-json-item4`

**Rol:** Convertir la sección de flota del 20-F en **JSON estructurado** usando Gemini,
con modelo de contingencia.

- **Región / recursos:** us-east1 · 2 CPU / 1 Gi · entrypoint `extract_fleet_20f_http`
- **URL:** `https://extraccion-json-item4-upaleadnxq-ue.a.run.app`
- **Entrada:** `gs://bucket-edgar/20f-flota-md/` (prefijo `SOURCE_PREFIX`)
- **Salida:** `gs://bucket-edgar/20f-flota-json/` (prefijo `TARGET_PREFIX`)
- **Modelos de IA (con contingencia):**
  - Primario: `MODEL_NAME = gemini-3-flash-preview`
  - Fallback: `gemini-2.5-flash` (configuración de contingencia en `except`)
- **Env vars:** `FUNCTION_TARGET=extract_fleet_20f_http` (solo).

---

### 12. `ejecutar-pipeline-anuales` ⭐ (función final)

**Rol:** Leer los JSON de las **tres ramas** (10-K, 20-F y aerolíneas chinas), unificar y
limpiar las filas, y **cargarlas en BigQuery**.

- **Región / recursos:** europe-west1 · 1 CPU / 512 Mi · entrypoint `ejecutar_pipeline_flota`
- **URL:** `https://ejecutar-pipeline-anuales-upaleadnxq-ew.a.run.app`
- **Entradas (prefijos `SOURCE_PREFIXES`):**
  - `10k-item2-json/` — flota de aerolíneas US (vía 10-K)
  - `20f-flota-json/` — flota de aerolíneas extranjeras (vía 20-F)
  - `flota-aerolineas-json/` — aerolíneas chinas (A-Shares + H-Shares)
- **Salida:** filas insertadas en BigQuery (`WRITE_TRUNCATE`) en:
  - `dataset_integrado.current_fleet_fact`
  - `dataset_integrado.order_book_fact`
- **Parámetros opcionales vía query string:**
  - `?source=` — filtrar por fuente (`10k`, `20f`, `china`, `all`).
  - `?dry_run=true` — solo cuenta filas y valida sin cargar en BigQuery.
- **Detalles técnicos:** incluye una lista `CHINESE_AIRLINES` de 8 aerolíneas esperadas
  para validación, función `limpiar_fila(row)` para estandarizar/descartar filas, y
  `cargar_a_bigquery(bq_client, filas, tabla_completa_id)` para el insert.
- **Env vars:** `FUNCTION_TARGET=ejecutar_pipeline_flota` (solo).

---

### 13. `revision-diaria-china` (monitoreo diario)

**Rol:** Todos los días revisa si las aerolíneas chinas publicaron **anuncios nuevos**
en la bolsa; si hay algo relevante para la flota, envía un **correo** con análisis de
Gemini.

- **Región / recursos:** us-east1 · 1 CPU / 1 Gi · entrypoint `monitorear_aerolineas_chinas`
- **URL:** `https://revision-diaria-china-upaleadnxq-ue.a.run.app`
- **Disparador:** Cloud Scheduler `revision-diaria` (08:00 Bogotá).
- **Aerolíneas monitoreadas:** las mismas 8 (Air China, China Eastern, China Southern,
  Cathay Pacific, Hainan, Spring, Juneyao, China Express) con códigos de bolsa y
  mercado.
- **Modelo de IA:** `gemini-3-flash-preview`.
- **Deduplicación:** guarda en `historial_anuncios_china.txt` los anuncios ya procesados
  (es el **único archivo que escribe** en el bucket además de las lecturas).
- **Salida:** correo a `CORREO_DESTINO` si hay novedad.
- **Env vars:**
  - `FUNCTION_TARGET=monitorear_aerolineas_chinas`
  - `GCP_PROJECT_ID`, `GCP_LOCATION` (us-east1), `BUCKET_NAME` (bucket-edgar)
  - `CORREO_ORIGEN` (sgomezlopez123@gmail.com), `CORREO_DESTINO`
    (samuelgomezl@hotmail.com), `PASSWORD_APP` (contraseña de aplicación Gmail).

---

### 14. `rss-8k` (monitoreo diario)

**Rol:** Revisa en EDGAR si las aerolíneas estadounidenses publicaron **Form 8-K**
nuevos; si alguno es relevante para la flota, envía un **correo** con análisis de Gemini.

- **Región / recursos:** us-east1 · 1 CPU / 1 Gi · entrypoint `monitorear_sec_event`
- **URL:** `https://rss-8k-upaleadnxq-ue.a.run.app`
- **Disparador:** Cloud Scheduler `revision-8k` (08:00 Bogotá).
- **Entrada:** EDGAR (Form 8-K) para los CIKs configurados en `CIKS_A_MONITOREAR`.
- **Modelo de IA:** `gemini-3-flash-preview`.
- **Deduplicación:** `historial_reportes.txt` (IDs ya procesados).
- **Salida:** correo si hay novedad. Usa `extraer_markdown_pagina(url_html)` para limpiar
  el HTML de EDGAR (colapsa saltos de línea y ruido de tablas) y
  `obtener_urls_documentos(url_index)` para recorrer la página del filing.
- **Env vars:** `FUNCTION_TARGET=monitorear_sec_event`, `GCP_PROJECT_ID`,
  `GCP_LOCATION`, `BUCKET_NAME`, `CORREO_ORIGEN`, `CORREO_DESTINO`, `PASSWORD_APP`.

---

### 15. `rss-6k` (monitoreo diario)

**Rol:** Igual que `rss-8k` pero para **Form 6-K** (aerolíneas extranjeras listadas en
EE. UU.: Gol, LATAM, etc.).

- **Región / recursos:** europe-west1 · 1 CPU / 1 Gi · entrypoint `monitorear_sec_event`
- **URL:** `https://rss-6k-upaleadnxq-ew.a.run.app`
- **Disparador:** Cloud Scheduler `revision-6k` (08:00 Bogotá).
- **Entrada:** EDGAR (Form 6-K) para `CIKS_A_MONITOREAR` de aerolíneas extranjeras
  activas (ej. `0001291733` Gol, `0001047716` LATAM…).
- **Modelo de IA:** `gemini-3-flash-preview`.
- **Deduplicación:** `historial_reportes_6k.txt`.
- **Env vars:** mismas que `rss-8k` (con `FUNCTION_TARGET=monitorear_sec_event`).

---

### 16. `respaldo-anual` (tarea trimestral)

**Rol:** Antes de cada corrida del pipeline anual, **respalda el estado actual**: crea
snapshots inmutables de las tablas de BigQuery y archiva los archivos del bucket en una
carpeta por fecha.

- **Región / recursos:** us-east1 · 1 CPU / 512 Mi · entrypoint `ejecutar_respaldo_anual`
- **URL:** `https://respaldo-anual-upaleadnxq-ue.a.run.app`
- **Disparador:** Fase 1 (bloqueante) del workflow `pipeline-anual` (vía Cloud Workflows).
- **BigQuery:** crea con `CREATE SNAPSHOT TABLE` las tablas
  `dataset_historico.current_fleet_fact_YYYYMMDD` y
  `dataset_historico.order_book_fact_YYYYMMDD` a partir de `dataset_integrado`. Es
  idempotente por fecha: si el snapshot ya existe, lo borra y lo recrea (requiere
  `roles/bigquery.dataOwner` en la SA de cómputo).
- **GCS:** mueve (copia + borra) todos los archivos de los prefijos del pipeline
  (`10k-md/`, `10k-item2-md/`, `10k-item2-json/`, `20f-md/`, `20f-flota-md/`,
  `20f-flota-json/`, `anual-china/`, `anual-china-md/`, `flota-aerolineas-md/`,
  `flota-aerolineas-json/`, `financial_raw_10k/`, `financial_raw_20f/`,
  `financial_raw_china_a/`, `financial_raw_china_h/`, `financiera-normalizada-json/`)
  a `gs://bucket-edgar/archivo/backup/YYYYMMDD/<prefijo>/`.
  Un prefijo ya vacío (por ejemplo `10k-md/` tras haber sido movido en una corrida
  anterior) simplemente reporta 0 archivos.
- **Parámetro opcional:** `?dry_run=true` — cuenta archivos y valida sin crear snapshots
  ni mover nada.
- **Respuesta:** JSON con `snapshots[]`, `archivo_gcs{}` y `errores_bq` / `errores_gcs`.
- **Env vars:** `FUNCTION_TARGET=ejecutar_respaldo_anual` (solo).

---

### 17. `extraccion-xbrl-10k` (rama financiera)

**Rol:** Extraer las métricas financieras de los **10-K** (aerolíneas de EE. UU.)
directamente del **XBRL** de EDGAR (sin IA), usando los estados financieros consolidados.

- **Región / recursos:** us-east1 · 2 CPU / 2 Gi · entrypoint `ejecutar_extraccion_financiera`
- **URL:** `https://extraccion-xbrl-10k-846229407230.us-east1.run.app`
- **Entrada:** API de EDGAR (librería `edgar`/`edgartools`, identidad SEC vía
  `SEC_API_IDENTITY`).
- **Aerolíneas (ticker):** `AAL, ALGT, ALK, DAL, JBLU, JETMF, LUV, RJET, SKYW, UAL, ULCC`.
- **Método:** `xbrl.statements.{income_statement,balance_sheet,cash_flow_statement}()
  .to_dataframe()` → filtra `dimension==False` y `is_breakdown==False` (consolidados).
  Usa solo el filing 10-K más reciente (sus columnas de fecha traen N años de historia,
  `ANIOS_HISTORIA=3`); las partidas de balance usan `period="instant"` y las de resultados
  `period="FY"`.
- **Salida:** NDJSON en `gs://bucket-edgar/financial_raw_10k/{ticker}_financial.json`
  (una fila por fact crudo, `data_source=xbrl_tag`).
- **Env vars:** `SEC_API_IDENTITY` (`Samuel samuel.gomez@mas4aviation.com`).
- **Parámetro opcional:** `?dry_run=true` (no sube nada).

---

### 18. `extraccion-xbrl-20f` (rama financiera)

**Rol:** Igual que `extraccion-xbrl-10k` pero para **20-F** (aerolíneas extranjeras
listadas en EE. UU.), que publican bajo la taxonomía **ifrs-full**.

- **Región / recursos:** us-east1 · 2 CPU / 2 Gi · entrypoint `ejecutar_extraccion_financiera`
- **URL:** `https://extraccion-xbrl-20f-846229407230.us-east1.run.app`
- **Aerolíneas (ticker):** `AEROMEX, AZUL, CPA, LTM, VLRS`.
- **Salida:** NDJSON en `gs://bucket-edgar/financial_raw_20f/{ticker}_financial.json`.
- **Moneda:** se detecta automáticamente de la unidad (`unit_ref`) del fact de Revenue
  (fallback `xbrl.units`, default USD) — ej. Copa en USD, Volaris/LATAM/Aeroméxico/Azul
  en MXN/BRL/COP.
- **Env vars:** `SEC_API_IDENTITY` (`Samuel Gomez samuelgomezl@hotmail.com`).

---

### 19. `extraccion-xbrl-china-a` (rama financiera)

**Rol:** Extraer las métricas financieras de las aerolíneas **A-Share** chinas
(bolsas SSE/SZSE). No publican XBRL, por lo que se usa **Gemini** sobre el Markdown del
reporte anual (`anual-china-md/`).

- **Región / recursos:** us-east1 · 2 CPU / 2 Gi · entrypoint `ejecutar_extraccion_financiera`
- **URL:** `https://extraccion-xbrl-china-a-846229407230.us-east1.run.app`
- **Aerolíneas (código):** `601111` Air China, `600115` China Eastern, `600029` China
  Southern, `600221` Hainan, `601021` Spring, `603885` Juneyao, `002928` China Express.
- **Método:** se selecciona una **ventana de texto** (60 000 caracteres) desde la primera
  sección de estados financieros consolidados (`合并资产负债表`/`合并利润表`/`合并现金流量表`,
  tras el 15% del documento para evitar el índice) y se pide a Gemini el JSON de métricas.
  Los tags resultantes deben estar en el crosswalk `cas-china` (incluye nombres chinos y
  alias en inglés, ya que Gemini a veces responde en inglés). Se soporta que `financial_metrics`
  sea un objeto o una **lista** de objetos, y que el `value` venga en unidades base o en
  millones (`万`/`亿`), normalizando siempre a yuanes base. Los valores se llevan a
  **unidades base** (×1e6 si |valor|<1e6).
- **Modelo de IA:** `gemini-3-flash-preview` (Vertex AI, location `global`), con reintentos
  ante errores `429 RESOURCE_EXHAUSTED`.
- **Salida:** NDJSON en `gs://bucket-edgar/financial_raw_china_a/{company}_2025.json`
  (`data_source=gemini_fallback`, `currency=CNY`).
- **Filtro de empresa:** `?code=600115` procesa solo esa aerolínea (la corrida completa
  excede el timeout por request, por eso el workflow la invoca sin filtro con
  `--timeout=1200` en Cloud Run).

---

### 20. `extraccion-xbrl-china-h` (rama financiera)

**Rol:** Igual que `extraccion-xbrl-china-a` pero para aerolíneas listadas en **Hong
Kong** (H-Share), que reportan bajo **HKFRS**.

- **Región / recursos:** us-east1 · 2 CPU / 2 Gi · entrypoint `ejecutar_extraccion_financiera`
- **URL:** `https://extraccion-xbrl-china-h-846229407230.us-east1.run.app`
- **Aerolíneas (código):** `00293` Cathay Pacific (también H-Shares chinas `00753` Air
  China, `00670` China Eastern, `01055` China Southern).
- **Método:** ventana de texto desde las secciones en inglés (`Consolidated statement of
  financial position` / `profit or loss` / `cash flows`). Mismo post-procesado que china-a
  (listas, escalas, alias en inglés del crosswalk `hkfrs`).
- **Salida:** NDJSON en `gs://bucket-edgar/financial_raw_china_h/{company}_2025.json`
  (`currency=HKD`).
- **Filtro de empresa:** `?code=00293`.

---

### 21. `normalizacion-financiera` ⭐ (rama financiera — fuente agnóstica)

**Rol:** Leer los datos crudos de las **cuatro** extracciones financieras
(`financial_raw_10k/`, `financial_raw_20f/`, `financial_raw_china_a/`,
`financial_raw_china_h/`) y convertirlos a un **esquema canónico único en USD millones**,
aplicando un **crosswalk** de etiquetas por taxonomía y las tasas de cambio de `fx_rates`.

- **Región / recursos:** us-east1 · 1 CPU / 1 Gi · entrypoint `normalizar_financiera`
- **URL:** `https://normalizacion-financiera-846229407230.us-east1.run.app`
- **Configuración versionada en GCS/BQ:**
  - `config/xbrl_crosswalk.json` (+`.ndjson`) → tabla `dataset_integrado.xbrl_crosswalk`
    (146 filas: `us-gaap` 50, `ifrs-full` 25, `cas-china` 38, `hkfrs` 33). Un `source_tag`
    puede mapear a **varias** métricas canónicas (ej. `LongTermDebtAndCapitalLeaseObligations`
    → `total_debt` y `long_term_debt`); las contribuciones se suman.
  - `dataset_integrado.fx_rates` → conversión de moneda local a USD por año fiscal.
- **Conversión:** `raw_value × escala → moneda local → USD ÷ 1e6`; para monedas sin tasa
  se usa la tasa más cercana disponible. Las fórmulas de métricas derivadas
  (`operating_income + depreciation_amortization` → EBITDA, `total_debt +
  operating_lease_liability - cash_equivalents` → net_debt, márgenes, etc.) están fijas en
  `config/financial_metrics.json` (nunca se delegan a IA).
- **Salidas:**
  - GCS: `gs://bucket-edgar/financiera-normalizada-json/resumen_normalizado_*.json`.
  - BigQuery (`WRITE_TRUNCATE`): `dataset_integrado.financial_fact_raw` (trazabilidad por
    fact, incluye `raw_json`) y `dataset_integrado.financial_fact` (canónica por
    empresa/año/métrica con `value_usd_m`, `data_source`, `formula_used`, `source_tags`).
- **Parámetro opcional:** `?dry_run=true` (cuenta y valida sin cargar).
- **Env vars:** ninguna (PROJECT_ID fijo; usa la SA de cómputo).

---

## Automatización del pipeline anual (Cloud Workflows)

El workflow **`pipeline-anual`** (región us-east1, archivo `workflows/pipeline-anual.yaml`)
orquesta todo el pipeline anual. Lo dispara Cloud Scheduler trimestralmente o a mano con
`gcloud workflows execute pipeline-anual --location=us-east1`.

```
fase1_respaldo     respaldo-anual                    [bloqueante — respalda antes de recargar]
fase2_ingesta      descarga-anual-china ∥ extraccion-10k-md ∥ extraccion-20f-md   [paralelo]
fase3_china        parsing-md-china → extraer-seccion-china → extraccion-json-china
fase4_10k          extraccion-item2-10k → extraccion-json-item2
fase5_20f          extraccion-item4-20f → extraccion-json-item4
fase6_unificar     ejecutar-pipeline-anuales          [WRITE_TRUNCATE → BigQuery]
fase7_financiera   extraccion-xbrl-10k ∥ extraccion-xbrl-20f ∥ extraccion-xbrl-china-a ∥ extraccion-xbrl-china-h   [paralelo]
fase8_normalizar   normalizacion-financiera           [WRITE_TRUNCATE → BigQuery]
```

- **SA de ejecución:** `workflows-runner` (invoca cada servicio con OIDC; requiere
  `roles/run.invoker` a nivel proyecto y `roles/logging.logWriter` para registrar los
  pasos).
- **Retries y timeouts:** los pasos de descarga usan retry `http.default_retry_predicate`
  (3 intentos); los pasos de Gemini 5 intentos; timeout máximo por paso 1800 s.
- **Paso a paso:** `fase2_ingesta` lanza las tres descargas en paralelo y espera a que
  terminen antes de continuar (dependencias de datos).

## Flujo de datos en detalle

Todas las ramas convergen en el mismo bucket `gs://bucket-edgar`. Este es el mapa
exacto de carpetas (prefijos) y de quién lee/escribe en cada una:

```
gs://bucket-edgar/
│
├── anual-china/                    ← escritura: descarga-anual-china
│                                     lectura:  parsing-md-china
├── anual-china-md/                 ← escritura: parsing-md-china
│                                     lectura:  extraer-seccion-china
├── flota-aerolineas-md/            ← escritura: extraer-seccion-china
│                                     lectura:  extraccion-json-china
├── flota-aerolineas-json/          ← escritura: extraccion-json-china
│                                     lectura:  ejecutar-pipeline-anuales
│
├── 10k-md/                         ← escritura: extraccion-10k-md
│                                     lectura:  extraccion-item2-10k
├── 10k-item2-md/                   ← escritura: extraccion-item2-10k
│                                     lectura:  extraccion-json-item2
├── 10k-item2-json/                 ← escritura: extraccion-json-item2
│                                     lectura:  ejecutar-pipeline-anuales
│
├── 20f-md/                         ← escritura: extraccion-20f-md
│                                     lectura:  extraccion-item4-20f
├── 20f-flota-md/                   ← escritura: extraccion-item4-20f
│                                     lectura:  extraccion-json-item4
├── 20f-flota-json/                 ← escritura: extraccion-json-item4
│                                     lectura:  ejecutar-pipeline-anuales
│
├── financial_raw_10k/              ← escritura: extraccion-xbrl-10k (XBRL, us-gaap)
│                                     lectura:  normalizacion-financiera
├── financial_raw_20f/              ← escritura: extraccion-xbrl-20f (XBRL, ifrs-full)
│                                     lectura:  normalizacion-financiera
├── financial_raw_china_a/          ← escritura: extraccion-xbrl-china-a (Gemini, CNY)
│                                     lectura:  normalizacion-financiera
├── financial_raw_china_h/          ← escritura: extraccion-xbrl-china-h (Gemini, HKD)
│                                     lectura:  normalizacion-financiera
├── financiera-normalizada-json/    ← escritura: normalizacion-financiera (resúmenes)
│
├── Edgar/Tablas_Item2_10k/…        ← escritura: extraccion-tablas-item2 (CSV)
├── Aerolineas_Leases_Refinado_Final.csv ← salida consolidada de tablas
│
├── archivo/backup/YYYYMMDD/…       ← escritura: respaldo-anual (copia del pipeline)
│
├── historial_reportes.txt          ← escritura/lectura: rss-8k (deduplicación)
├── historial_reportes_6k.txt       ← escritura/lectura: rss-6k (deduplicación)
├── historial_anuncios_china.txt    ← escritura/lectura: revision-diaria-china (dedup.)
│
└── (otras carpetas históricas: 10k-notes-md/, 10k-leases-json/, processed/) — legado
```

**Orden de ejecución recomendado del pipeline anual (orquestado por el workflow
`pipeline-anual`):**

```
0. respaldo-anual                  (snapshots BQ + archivo GCS en archivo/backup/YYYYMMDD/)
1. descarga-anual-china            (China: PDFs → anual-china/)
2. parsing-md-china                (China: → anual-china-md/)
3. extraer-seccion-china           (China: → flota-aerolineas-md/)
4. extraccion-json-china           (China: → flota-aerolineas-json/)      [Gemini]
5. extraccion-10k-md               (10-K: → 10k-md/)
6. extraccion-item2-10k            (10-K: → 10k-item2-md/)
7. extraccion-json-item2           (10-K: → 10k-item2-json/)             [Gemini]
   (alternativa: extraccion-tablas-item2 → CSVs de tablas)
8. extraccion-20f-md               (20-F: → 20f-md/)
9. extraccion-item4-20f            (20-F: → 20f-flota-md/)
10. extraccion-json-item4          (20-F: → 20f-flota-json/)             [Gemini]
11. ejecutar-pipeline-anuales      (unifica 3 ramas → BigQuery)          [escritura BQ]
12. extraccion-xbrl-10k            (10-K: XBRL → financial_raw_10k/)     (indep. de EDGAR)
13. extraccion-xbrl-20f            (20-F: XBRL → financial_raw_20f/)     (indep. de EDGAR)
14. extraccion-xbrl-china-a        (China A: Gemini → financial_raw_china_a/)
15. extraccion-xbrl-china-h        (China H: Gemini → financial_raw_china_h/)
16. normalizacion-financiera       (unifica 4 prefijos → financial_fact) [escritura BQ]
```

> **Por qué el respaldo va primero:** el último paso (`ejecutar-pipeline-anuales`) hace
> `WRITE_TRUNCATE` sobre las tablas de `dataset_integrado`. El snapshot debe capturar el
> estado **anterior** a esa recarga, por eso `respaldo-anual` corre como fase bloqueante
> inicial. Nota: los prefijos de GCS quedan **vacíos** tras el respaldo (se movieron a
> `archivo/backup/`); por eso en cada corrida trimestral la descarga se vuelve a ejecutar.

> Los servicios de **monitoreo diario** (`rss-8k`, `rss-6k`, `revision-diaria-china`)
> NO participan en este pipeline: corren de forma independiente vía Cloud Scheduler y
> solo generan correos.

---

## Base de datos BigQuery

**Dataset:** `dataset_integrado` · **Ubicación de datos:** us-east1

### Tabla `current_fleet_fact` — inventario actual de flota

| Campo | Tipo | Significado |
|---|---|---|
| `fleet_id` | STRING | Identificador único (ej. `air_china_2025_b737_800_owned`) |
| `airline` | STRING | Aerolínea (en inglés) |
| `report_year` | INTEGER | Año del reporte |
| `aircraft_type` | STRING | Tipo de aeronave (modelo) |
| `quantity` | INTEGER | Cantidad de aviones |
| `ownership_type` | STRING | `owned` (propio) / `leased` (arrendado) |
| `average_age_years` | FLOAT | Edad promedio de la flota |
| `remaining_lease_years` | FLOAT | Años restantes de leasing (si aplica) |
| `seating_capacity_avg` | INTEGER | Capacidad promedio de asientos |

> ~294 filas · modo de carga `WRITE_TRUNCATE` (se reemplaza en cada ejecución).

### Tabla `order_book_fact` — pedidos futuros (backlog)

| Campo | Tipo | Significado |
|---|---|---|
| `order_id` | STRING | Identificador único del pedido |
| `airline` | STRING | Aerolínea |
| `report_year` | INTEGER | Año del reporte |
| `aircraft_type` | STRING | Tipo de aeronave pedida |
| `quantity_committed` | INTEGER | Cantidad comprometida |
| `delivery_period` | STRING | Periodo de entrega |

> ~148 filas · modo de carga `WRITE_TRUNCATE`.

### Tablas `financial_fact_raw` / `financial_fact` — métricas financieras

**`financial_fact_raw`** (trazabilidad; ~747 filas · WRITE_TRUNCATE):

| Campo | Tipo | Significado |
|---|---|---|
| `company` | STRING | Empresa (ticker para XBRL, nombre para China) |
| `fiscal_year` | INTEGER | Año fiscal |
| `taxonomy` | STRING | `us-gaap` / `ifrs-full` / `cas-china` / `hkfrs` |
| `source_tag` | STRING | Etiqueta original (concepto XBRL o tag de Gemini) |
| `raw_value` | FLOAT | Valor en moneda local (unidades base) |
| `currency` / `unit` | STRING | Moneda local |
| `period` | STRING | `FY` (resultados) / `instant` (balance) |
| `context_ref` | STRING | `consolidated` / `parent` |
| `data_source` | STRING | `xbrl_tag` / `gemini_fallback` |
| `raw_json` | STRING | JSON original del fact (auditoría) |

**`financial_fact`** (canónica, por empresa/año/métrica; ~854 filas · WRITE_TRUNCATE):

| Campo | Tipo | Significado |
|---|---|---|
| `company` | STRING | Empresa |
| `fiscal_year` | INTEGER | Año fiscal |
| `canonical_metric` | STRING | Métrica canónica (ingresos, EBITDA, deuda…) |
| `value_usd_m` | FLOAT | Valor en **millones de USD** |
| `data_source` | STRING | `xbrl_tag` / `calculated` / `gemini_fallback` |
| `formula_used` | STRING | Fórmula si es métrica derivada |
| `source_tags` | STRING[] | Etiquetas fuente que contribuyen |
| `extraction_ts` | TIMESTAMP | Momento de la corrida |

**Métricas canónicas cubiertas:** ingresos, costos de operación, margen operativo,
utilidad antes de impuestos, utilidad neta, EBITDA, depreciación/amortización, capex,
caja, deuda total / corto y largo plazo / bonos, leases, activos, pasivos, patrimonio,
gastos de personal, gastos de combustible, interés, impuestos, márgenes y deuda neta.

> El **XBRL de la SEC** (10-K/20-F) es la fuente de mayor calidad: `data_source=xbrl_tag`.
> Las aerolíneas chinas se extraen con **Gemini** (`gemini_fallback`) porque no publican
> XBRL; su cobertura depende de lo que reporta el estado financiero del PDF.

### Dataset histórico (`dataset_historico`)

Cada corrida trimestral, `respaldo-anual` crea **snapshots inmutables** del estado
previo de las tablas de `dataset_integrado`, con nombre `{tabla}_{YYYYMMDD}`:

| Tabla snapshot | Origen |
|---|---|
| `current_fleet_fact_YYYYMMDD` | `dataset_integrado.current_fleet_fact` |
| `order_book_fact_YYYYMMDD` | `dataset_integrado.order_book_fact` |

Además conserva las tablas históricas de respaldos anteriores (patrón append):
`flota_historica_current_fleet_fact` y `flota_historica_order_book_fact` (con columna
`fecha_respaldo`). Los snapshots permiten recuperar el estado exacto de cada corte
trimestral para comparativas o reconstrucción.

---

## Automatización (Cloud Scheduler + Cloud Workflows)

### Tareas diarias (monitoreo)

Tres tareas programadas, todas en `us-east1`, zona horaria `America/Bogota`, a las
**08:00 diarias** (`0 8 * * *`), método HTTP **GET**, con timeout de **900s** y
autenticación **OIDC** con la SA `cloud-scheduler-invoker`:

| Job | Programación | URL objetivo | Función disparada |
|---|---|---|---|
| `revision-8k` | `0 8 * * *` | `https://rss-8k-846229407230.us-east1.run.app/` | `rss-8k` |
| `revision-6k` | `0 8 * * *` | `https://rss-6k-846229407230.europe-west1.run.app/` | `rss-6k` |
| `revision-diaria` | `0 8 * * *` | `https://revision-diaria-china-846229407230.us-east1.run.app/` | `revision-diaria-china` |

### Tarea trimestral (pipeline anual)

Un job programado ejecuta el **workflow de Cloud Workflows** que orquesta el respaldo y
todo el pipeline anual:

| Job | Programación | Objetivo | Método |
|---|---|---|---|
| `revision-anual-sec` | `0 6 1 1,4,7,10 *` (cada 1.º de ene/abr/jul/oct, 06:00 Bogotá) | `POST` a la API de ejecuciones del workflow `pipeline-anual` (us-east1) | OIDC con SA `workflows-invoker` |

> El workflow `pipeline-anual` es la forma **recomendada** de correr el pipeline anual
> (respaldo + descargas + Gemini + unificación), y su ejecución dura ~1 hora. También
> puede lanzarse manualmente con
> `gcloud workflows execute pipeline-anual --location=us-east1`. El respaldo queda
> registrado en `dataset_historico` y `gs://bucket-edgar/archivo/backup/`. Un fallo no
> genera correo: se revisa en los logs de la ejecución y se re-dispara manualmente.

---

## Despliegue e integración continua

- **Repo:** `sgomez-m4/mach5` (monorepo). El código de cada función está en
  `functions/<nombre>/` con su `main.py` y un `Dockerfile`.
- **Workflows:** hay **un workflow de GitHub Actions por función** en
  `.github/workflows/deploy-<nombre>.yml`.
- **Disparo:** cada workflow se ejecuta cuando se modifican los archivos de su carpeta
  (`paths:`) o el propio workflow, en push a `main`.
- **Autenticación de GitHub → GCP:** **Workload Identity Federation (WIF)** — GitHub
  se autentica con la SA `github-deployer` sin necesidad de llaves de larga duración.
- **Build y deploy:** se construye la imagen y se ejecuta un `gcloud run deploy` que
  mantiene el **mismo nombre de servicio** (así la URL y los Cloud Scheduler no
  cambian). Las flags típicas incluyen `--memory`, `--cpu`, `--clear-base-image`
  (evita un error de entrypoint al usar el runtime estándar) y
  `--service-account=846229407230-compute@developer.gserviceaccount.com`.

### Git — convenciones

- Trabajar siempre sobre ramas y revisar antes de hacer push. El push a `main` dispara
  despliegues automáticos a producción.
- El proyecto GCP, la región, el bucket, los correos y las claves de API se inyectan
  como variables de entorno / secrets de GitHub (no se hardcodean en el código).

---

## Seguridad y acceso

- **Todos los servicios son privados** (requieren autenticación para invocarse):
  - Sin credenciales → HTTP **403**.
  - Con token/identidad válida → HTTP **200**.
- **¿Cómo se logra?** Se eliminó la anotación
  `run.googleapis.com/invoker-iam-disabled: 'true'` (que permitía invocaciones sin
  auth) de todos los servicios. Ahora Cloud Run exige IAM.
- **Cloud Scheduler** invoca los 3 servicios de monitoreo con la SA
  `cloud-scheduler-invoker` (rol `roles/run.invoker`), usando **OIDC** (`--oidc-service-account-email`
  + `--oidc-token-audience`).
- **Cloud Workflows** (`pipeline-anual`) usa dos SA: `workflows-invoker` (la usa el job
  `revision-anual-sec` para lanzar ejecuciones) y `workflows-runner` (ejecuta el workflow,
  invoca cada Cloud Run con OIDC y escribe logs del workflow).
- **En tiempo de ejecución**, cada servicio actúa con la SA de cómputo
  `846229407230-compute@...` (tiene permisos de GCS y BigQuery vía el rol por defecto
  de la SA de cómputo; para el borrado de snapshots se añadió `roles/bigquery.dataOwner`).
- **Secrets sensibles** (correos SMTP, contraseñas de aplicación, API keys) viajan como
  variables de entorno del servicio, no en el código.

---

## Preguntas frecuentes

**¿Por qué hay dos regiones?** Se distribuyeron servicios en us-east1 y europe-west1
para repartir la cuota de Vertex AI y evitar errores `429 RESOURCE_EXHAUSTED` (límite
de peticiones por región).

**¿Qué pasa si dos funciones se ejecutan en paralelo y compiten por la cuota de Gemini?**
Vertex AI en us-east1 tiene una cuota compartida; por eso los modelos de extracción
pesada usan `gemini-3-flash-preview` y se añadieron modelos de **fallback** en
`extraccion-tablas-item2` y `extraccion-json-item4`.

**¿El monitoreo diario alimenta BigQuery?** No. Los tres servicios de monitoreo
(`rss-8k`, `rss-6k`, `revision-diaria-china`) solo revisan novedades y **envían
correos**. Las funciones que escriben en BigQuery son `ejecutar-pipeline-anuales`
(flota), `normalizacion-financiera` (métricas financieras → `financial_fact_raw`/
`financial_fact`) y `respaldo-anual` (snapshots en `dataset_historico`).

**¿Qué pasa si falla la corrida trimestral?** El workflow `pipeline-anual` hace retries
en cada paso (3 intentos en descargas, 5 en los pasos de Gemini). Si aún así falla, la
ejecución queda `FAILED` en Cloud Workflows y no se envía correo: se revisan los logs de
la ejecución y se re-dispara manualmente con
`gcloud workflows execute pipeline-anual --location=us-east1`.

**¿El respaldo trimestral borra datos?** No: mueve los archivos del bucket a
`archivo/backup/YYYYMMDD/` y crea snapshots en `dataset_historico`. Ambos son
**reversibles/recuperables**. Sí deja vacíos los prefijos de trabajo, por lo que la
descarga anual se vuelve a ejecutar en cada corrida.

**¿Cómo sé que una función funcionó?** Cada función devuelve un resumen en su respuesta
HTTP (por ejemplo `{"status": "success", "filings_8k_procesados": 0, ...}`) y escribe
logs en Cloud Logging. Un HTTP 200 con el JSON de resumen indica éxito.

**¿Cómo probar una función privada manualmente?** Con la identidad correcta:
`gcloud auth print-access-token` y enviar la petición con el header
`Authorization: Bearer <token>`.

**¿Qué son los archivos `historial_*.txt` del bucket?** Son "memorias" de los servicios
de monitoreo para **no re-enviar el mismo anuncio dos veces** (deduplicación por ID).

---

## Apéndice — Resumen rápido por persona técnica

- 21 servicios Cloud Run (Python + `functions-framework`, entrypoint HTTP).
- 4 ramas de pipeline (3 de flota + 1 financiera) → 2 pipelines de unificación
  (`ejecutar-pipeline-anuales` para flota, `normalizacion-financiera` para métricas
  financieras) → BigQuery `dataset_integrado` (`current_fleet_fact`, `order_book_fact`,
  `financial_fact_raw`, `financial_fact`; `WRITE_TRUNCATE`).
- Rama financiera: 10-K/20-F desde XBRL (SEC, sin IA) + China A/H con Gemini;
  normalización fuente-agnóstica con crosswalk (`xbrl_crosswalk`) y `fx_rates` → USD_m.
- `respaldo-anual`: snapshots BQ (`dataset_historico`) + archivo GCS (`archivo/backup/`),
  incluye los 5 prefijos financieros.
- Pipeline anual orquestado por el workflow `pipeline-anual` (Cloud Workflows), disparado
  trimestralmente por el job `revision-anual-sec` (Cloud Scheduler, `0 6 1 1,4,7,10 *`);
  ahora con `fase7_financiera` (4 extracciones en paralelo) y `fase8_normalizar`.
- Bucket único de intercambio: `gs://bucket-edgar` (prefijos por etapa).
- Gemini `gemini-3-flash-preview` vía Vertex AI (us-east1), con fallbacks
  `gemini-2.5-flash` / `gemini-2.0-flash`.
- 3 jobs Cloud Scheduler diarios 08:00 Bogotá con OIDC + SA `cloud-scheduler-invoker`;
  el job trimestral usa las SA `workflows-invoker` / `workflows-runner`.
- CI/CD por función con GitHub Actions + WIF (`github-deployer`).
- Todos los servicios privados (403 sin auth).
