# Eventos corporativos: disparadores de prospección

Los 8-K (emisores de EE. UU.), 6-K (emisores extranjeros) y anuncios de Cninfo (aerolíneas chinas) de los grupos del score v2 se guardan en `dataset_prospeccion.eventos_aerolinea`. Gemini clasifica cada uno por categoría y por relevancia para la oferta de mas4:

- **Alta (disparador):** abre una conversación concreta. Entran pedidos, entregas o retiros de aeronaves, contratos o devoluciones de leasing, sale-leaseback, contratos de MRO, aviones en tierra por motores y un director técnico nuevo.
- **Media:** cambia el contexto del cliente, como cambios en la alta dirección, financiamiento, reestructuración o fusiones.
- **Baja:** rutina, como fechas de resultados, dividendos o juntas.

`sql/v_eventos_aerolinea.sql` es la vista que lee el informe. La portada de aerolíneas muestra el último disparador y cuántos hubo en 90 días; la ficha lista los eventos de relevancia alta y media del último año.

| Archivo | Qué hace |
|---|---|
| [registro_eventos.py](registro_eventos.py) | Fuente única del prompt, el esquema y el formato de fila. Los monitores llevan una copia idéntica. |
| [cargar_eventos.py](cargar_eventos.py) | Carga local de los últimos 12 meses desde EDGAR y Cninfo. Usa solo la librería estándar y la sesión de gcloud. |
| [prueba_registro.py](prueba_registro.py) | Pruebas sin red: copias idénticas, ids de EDGAR, armado del prompt. |
| [../config/eventos_fuentes.json](../config/eventos_fuentes.json) | CIK o código de Cninfo de cada grupo. |

```bash
python -I -X utf8 eventos/cargar_eventos.py <carpeta de trabajo> --cargar
```

La carpeta de trabajo guarda los textos y las clasificaciones: volver a correrlo no repite descargas ni llamadas a Gemini, y `--cargar` reemplaza la tabla completa.

## Monitores

`functions/rss-8k`, `rss-6k` y `revision-diaria-china` siguen enviando sus correos. Ahora además registran cada aviso nuevo de un grupo del score en la misma tabla, con la misma clasificación. Si el registro falla, el correo sale igual.

Estos cambios están en la rama `feat/score-v2` y **no están desplegados**: los servicios se despliegan al fusionar con `main`, que se hace después de la sustentación del 28-nov-2026. Para que funcionen hace falta:

1. Que `config/eventos_fuentes.json` esté en `gs://bucket-edgar/config/`. Lo sube `sync-config-gcs` al fusionar.
2. Que la cuenta de servicio de los tres monitores tenga `roles/bigquery.dataEditor` sobre `dataset_prospeccion` y `roles/bigquery.jobUser` en el proyecto.
3. Una ejecución de prueba de cada servicio después del despliegue, revisando `eventos_registrados` en la respuesta.

Mientras tanto, la tabla se actualiza corriendo `cargar_eventos.py` a mano.
