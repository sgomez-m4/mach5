CREATE OR REPLACE VIEW `mach5-gemini-project.dataset_prospeccion.v_modelos_operador` AS
-- Modelos de aeronave que cada certificado del grupo tiene registrados ante FAA
-- (ultima descarga, vista "Show Aircraft"). Un renglon por grupo, operador y
-- modelo, para la ficha de aerolinea. FAA no da cantidades: dice que tipos
-- opera cada certificado, no cuantos. Para los grupos extranjeros es lo unico
-- que publica FAA (parte 129).

WITH ultima AS (
  SELECT * FROM `mach5-gemini-project.dataset_prospeccion.faa_operador_modelos`
  WHERE fecha_descarga = (SELECT MAX(fecha_descarga)
                          FROM `mach5-gemini-project.dataset_prospeccion.faa_operador_modelos`)
)

SELECT DISTINCT
  d.group_id,
  d.operador,
  CAST(d.cfr_part AS STRING) AS cfr_part,
  m.aircraft_model_faa AS modelo_faa,
  m.fecha_descarga
FROM `mach5-gemini-project.dataset_prospeccion.dim_operador_certificado` d
JOIN ultima m ON m.certificate_number = d.faa_certificate_number
WHERE d.en_flota_reportada
