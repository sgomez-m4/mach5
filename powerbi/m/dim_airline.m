// Origen: mach5-gemini-project.dataset_integrado.dim_airline
let
    Origen = GoogleBigQuery.Database([Implementation="2.0"]),
    #"mach5-gemini-project_Database" = Origen{[Name="mach5-gemini-project",Kind="Database"]}[Data],
    dataset_integrado_Schema = #"mach5-gemini-project_Database"{[Name="dataset_integrado",Kind="Schema"]}[Data],
    Tabla = dataset_integrado_Schema{[Name="dim_airline",Kind="View"]}[Data]
in
    Tabla
