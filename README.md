# mach5 — Cloud Run functions (CI/CD via GitHub Actions)

Repo monorepo con el código fuente de 7 Cloud Run services de GCP. Cada `push` a `main`
despliega automáticamente la función cuyo código cambió.

## Estructura

```
functions/
  rss-8k/                    # us-east1  | 1CPU/1Gi | Cloud Scheduler revision-8k (diario 08:00)
  rss-6k/                    # europe-west1 | 1CPU/1Gi | Cloud Scheduler revision-6k (diario 08:00)
  revision-diaria-china/     # us-east1  | 1CPU/1Gi
  extraccion-json-item2/     # us-east1  | 2CPU/2Gi
  extraccion-10k-md/         # us-east1  | 1CPU/1Gi
  extraccion-20f-md/         # europe-west1 | 2CPU/1Gi
  extraccion-tablas-item2/   # us-east1  | 2CPU/2Gi
.github/workflows/           # un workflow de deploy por función (filtro paths)
```

Cada función usa **Python + functions-framework** (entrypoint HTTP en `main.py`).

## Despliegue automático

- Cada workflow se dispara al modificar la carpeta de su función (`paths:`) o el propio workflow.
- Autenticación: **Workload Identity Federation** (sin claves JSON de larga duración).
- Secretos inyectados desde **GitHub Secrets** (no hay credenciales en el código).

## GitHub Secrets requeridos

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

## Flujo de trabajo

1. Editar el código en `functions/<funcion>/`.
2. `git commit` + `git push` a `main`.
3. El workflow correspondiente construye con buildpacks y despliega a Cloud Run
   manteniendo el mismo nombre de servicio (la URL y el Cloud Scheduler no cambian).
