CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_eventos_aerolinea` AS
-- Eventos corporativos (8-K, 6-K y anuncios de Cninfo) de los grupos del score
-- v2, clasificados por categoria y relevancia para mas4. Los de relevancia Alta
-- son disparadores: abren una conversacion comercial concreta.
--
-- La tabla la llena eventos/cargar_eventos.py (y, despues de la sustentacion,
-- los monitores rss-8k, rss-6k y revision-diaria-china).

SELECT
  e.id_evento,
  e.group_id,
  e.fecha,
  DATE_DIFF(CURRENT_DATE(), e.fecha, DAY) AS dias,
  e.fuente,
  e.formulario,
  e.items,
  e.titulo,
  e.url,
  IFNULL(e.categoria, 'Sin clasificar') AS categoria,
  IFNULL(e.relevancia, 'Sin clasificar') AS relevancia,
  CASE e.relevancia WHEN 'Alta' THEN 1 WHEN 'Media' THEN 2 WHEN 'Baja' THEN 3 ELSE 4 END AS orden_relevancia,
  e.resumen,
  e.relevancia = 'Alta' AS es_disparador
FROM `mach5-gemini-project.dataset_prospeccion.eventos_aerolinea` e
