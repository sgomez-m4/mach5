# mach5

Pipeline que convierte los reportes regulatorios de 24 aerolíneas en una lista priorizada
de prospectos para una consultora de asset management aeronáutico.

**[docs/ARQUITECTURA.md](docs/ARQUITECTURA.md)** documenta el sistema completo: de dónde
sale cada dato, cómo se normaliza, cómo se convierte en señal comercial y dónde se consume.
Empieza por ahí.

## Estructura

```
functions/    22 servicios de Cloud Run (Python + functions-framework)
config/       catalogos e identidad canonica; se leen desde GCS en ejecucion
sql/          UDF y vistas de dataset_integrado
powerbi/      artefactos de migracion del modelo (M y DAX)
workflows/    definicion del Cloud Workflow de orquestacion trimestral
docs/         documentacion
```

## Despliegue

Cuatro workflows escuchan `push` a `main`. Los 22 workflows de servicio son reutilizables
(`workflow_call`) y los invoca el orquestador.

| Workflow | Se dispara con | Qué hace |
|---|---|---|
| `deploy-cloud-run` | `functions/**` | Detecta qué servicios cambiaron y los despliega **de uno en uno** |
| `sync-config-gcs` | `config/**` | Respalda lo que hay en GCS y sube la versión del repo |
| `aplicar-sql-bigquery` | `sql/**` | Aplica UDF y vistas en orden de dependencia, y verifica que devuelvan filas |
| `deploy-pipeline-anual-workflow` | `workflows/**` | Despliega la definición del Cloud Workflow |

El despliegue es secuencial a propósito: cinco builds en paralelo agotan la cuota de
`Build and Operation Get requests per minute` de Cloud Build. El razonamiento completo está
en la documentación.

Para desplegar a mano, `deploy-cloud-run` acepta un input: vacío despliega lo que cambió,
un nombre de servicio despliega solo ese, `todos` fuerza el despliegue completo.

Autenticación por **Workload Identity Federation**; no hay claves JSON de larga duración
ni credenciales en el código.

## Dos cosas que sorprenden

**`config/` no se lee del repo.** Los servicios lo leen de `gs://bucket-edgar/config/` en
tiempo de ejecución. El workflow `sync-config-gcs` los mantiene sincronizados; sin él, un
cambio en el repo no surte ningún efecto.

**Las cargas a BigQuery usan `WRITE_TRUNCATE`.** Reemplazan la tabla completa. Conviene
correr con `?dry_run=true` y crear un snapshot antes de una carga real.

## GitHub Secrets

| Secret | Descripción |
|---|---|
| `WIF_PROVIDER` | Provider de Workload Identity |
| `WIF_SERVICE_ACCOUNT` | SA desplegadora (`github-deployer@...`) |
| `GCP_PROJECT_ID` | Proyecto GCP |
| `GCP_LOCATION` | Región por defecto (us-east1) |
| `BUCKET_NAME` | Bucket de GCS |
| `CORREO_ORIGEN` / `CORREO_DESTINO` / `PASSWORD_APP` | Credenciales SMTP Gmail |
| `GEMINI_API_KEY` | API key de Gemini |
| `SEC_API_IDENTITY_10K` / `_20F` / `_TABLAS` | Identidad SEC-API por función |

La SA desplegadora necesita además `bigquery.jobUser` y `bigquery.dataEditor` para que
`aplicar-sql-bigquery` pueda crear las vistas.
