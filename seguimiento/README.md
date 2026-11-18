# Seguimiento comercial

El equipo lleva el seguimiento de cada cuenta en un Excel en línea (OneDrive o SharePoint de mas4). El informe `Prospeccion_v2` lo lee al refrescar.

## El libro

[crear_seguimiento.py](crear_seguimiento.py) crea `Seguimiento_comercial_mas4.xlsx`, precargado con 457 cuentas en «Sin contactar»: las 24 aerolíneas del score v2 y los 433 talleres del sur en nivel A o B de venta directa. No sobrescribe un archivo existente.

| Columna | Uso |
|---|---|
| Tipo | Aerolínea o Taller |
| ID | Oculta. Une la fila con el informe (`group_id` o `clave_taller`); no se cambia |
| Cuenta, Detalle | Nombre y contexto (nivel, servicio, ubicación) |
| Estado | Lista: Sin contactar, Contactado, Reunión agendada, Propuesta enviada, Negociación, Ganado, Perdido, En pausa |
| Responsable, Próxima acción, Fecha próxima acción, Último contacto, Notas | Se llenan a mano |

Es una tabla de Excel (`tblSeguimiento`). Para agregar una cuenta, se agrega una fila al final de la tabla y se deja el ID vacío. Esa fila sale en la página Seguimiento, pero no en las listas ni en las fichas, porque sin ID no se puede unir. Las fechas se escriben como fechas, no como fórmulas: Excel copia una fórmula a toda la columna de la tabla.

## En el informe

- **Aerolíneas y Talleres:** columna «Estado comercial» en la lista.
- **Ficha de aerolínea:** estado y próxima acción en la tarjeta de arriba.
- **Ficha de taller:** el seguimiento junto al nombre.
- **Página Seguimiento:**
  - Cuentas con acción pendiente, ordenadas por fecha, con la situación de cada una: Vencida, Al día o Sin fecha.
  - Tarjetas de cuentas en conversación, acciones vencidas y cuentas ganadas.
  - Filtros por tipo, estado, responsable y situación.

## Conectar el archivo de SharePoint

El modelo lee la ruta del parámetro `RutaSeguimiento`. El repo es público, así que guarda una ruta de ejemplo (`https://<organizacion>-my.sharepoint.com/personal/<usuario>/Documents/...`). La ruta real se pone en cada equipo con los pasos de abajo. Si el archivo se mueve, se repiten esos pasos con la ruta nueva. El enlace de «Compartir» no sirve, porque devuelve la página de SharePoint y no el archivo.

Para que git no suba la ruta real después de ponerla, se marca el archivo del parámetro como cambio local:

```bash
git update-index --skip-worktree powerbi/Prospeccion_v2_PBIP/Prospeccion_v2.SemanticModel/definition/expressions.tmdl
```

Para volver a versionarlo hay que usar `--no-skip-worktree`. Ten en cuenta que el `git pull` no actualiza un archivo marcado así.

1. Sube el archivo al OneDrive o SharePoint de mas4 y compártelo con el equipo.
2. Abre el archivo en Excel de escritorio: Archivo → Información → Copiar ruta. Debe quedar un enlace que empiece por `https://` y termine en `.xlsx`. Si al final trae `?web=1`, quítalo.
3. En Power BI Desktop: Inicio → Transformar datos → Editar parámetros, y pega el enlace en `RutaSeguimiento`.
4. Inicio → Actualizar. La primera vez pide credenciales: elige «Cuenta de la organización» e inicia sesión con tu cuenta de mas4.
5. Guarda.
