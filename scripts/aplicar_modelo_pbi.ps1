# Aplica powerbi/modelo.json al modelo tabular abierto en Power BI Desktop.
#
# El modelo vivia solo dentro del .pbix: no era revisable ni reproducible, y la
# unica copia era el archivo binario de una maquina. Esta definicion vive en el
# repo y este script la aplica, asi que reconstruirlo deja de depender de acordarse
# de que se hizo.
#
# Es idempotente: crea lo que falta, actualiza lo que difiere y deja igual lo que
# ya coincide. Por defecto no escribe nada; hay que pasar -Aplicar.
#
#   .\scripts\aplicar_modelo_pbi.ps1                 # informa que cambiaria
#   .\scripts\aplicar_modelo_pbi.ps1 -Aplicar        # escribe
#   .\scripts\aplicar_modelo_pbi.ps1 -Aplicar -Puerto 57088

[CmdletBinding()]
param(
  [switch]$Aplicar,
  [int]$Puerto = 0,
  [string]$Definicion = ""
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------- ensamblados
# AMO viene con DAX Studio y apunta a .NET Framework 4.7.2, que PowerShell 5.1
# puede cargar. Hay que cargar Tabular.dll directamente y dejar que sus
# dependencias se resuelvan desde la misma carpeta: cargar Core.dll primero
# falla con ReflectionTypeLoadException.
$script:binAmo = "C:\Program Files\DAX Studio\bin"
$principal = Join-Path $script:binAmo "Microsoft.AnalysisServices.Tabular.dll"
if (-not (Test-Path $principal)) {
  throw "no se encontro AMO en $script:binAmo. Lo instala DAX Studio (daxstudio.org)."
}

$resolver = [System.ResolveEventHandler] {
  param($remitente, $evento)
  $corto = ($evento.Name -split ',')[0]
  $candidata = Join-Path $script:binAmo "$corto.dll"
  if (Test-Path $candidata) { return [System.Reflection.Assembly]::LoadFrom($candidata) }
  return $null
}
[System.AppDomain]::CurrentDomain.add_AssemblyResolve($resolver)
Add-Type -Path $principal

# ------------------------------------------------------------------- el puerto
if ($Puerto -eq 0) {
  $procesos = (Get-Process -Name msmdsrv -ErrorAction SilentlyContinue).Id
  if (-not $procesos) { throw "Power BI Desktop no esta abierto" }
  $candidatos = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
                Where-Object { $procesos -contains $_.OwningProcess } |
                Select-Object -ExpandProperty LocalPort -Unique |
                Where-Object { $_ -ne 2383 -and $_ -gt 1024 }
  if (-not $candidatos) { throw "no se encontro el puerto de Power BI Desktop" }
  if ($candidatos.Count -gt 1) {
    throw "hay varias instancias ($($candidatos -join ', ')); indica cual con -Puerto"
  }
  $Puerto = $candidatos
}
Write-Host "instancia: localhost:$Puerto"

# --------------------------------------------------------------- la definicion
if (-not $Definicion) {
  $Definicion = Join-Path (Split-Path $PSScriptRoot -Parent) "powerbi\modelo.json"
}
if (-not (Test-Path $Definicion)) { throw "no existe $Definicion" }
$def = Get-Content $Definicion -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "definicion: $($def.tablas.Count) tablas, $($def.medidas.Count) medidas"

# ------------------------------------------------------------------- conexion
$srv = New-Object Microsoft.AnalysisServices.Tabular.Server
# Timeout explicito: si Power BI Desktop esta ocupado -un refresco en curso,
# un dialogo abierto- la conexion se queda colgada indefinidamente.
$srv.Connect("Data Source=localhost:$Puerto;Connect Timeout=30")
$db = $srv.Databases[0]
$modelo = $db.Model
Write-Host "modelo: $($db.Name)  (tablas actuales: $($modelo.Tables.Count))"
Write-Host ""

$cambios = New-Object System.Collections.ArrayList
function Anotar($texto) { [void]$cambios.Add($texto); Write-Host "  $texto" }

$tipos = @{
  "string"   = [Microsoft.AnalysisServices.Tabular.DataType]::String
  "int64"    = [Microsoft.AnalysisServices.Tabular.DataType]::Int64
  "double"   = [Microsoft.AnalysisServices.Tabular.DataType]::Double
  "decimal"  = [Microsoft.AnalysisServices.Tabular.DataType]::Decimal
  "boolean"  = [Microsoft.AnalysisServices.Tabular.DataType]::Boolean
  "dateTime" = [Microsoft.AnalysisServices.Tabular.DataType]::DateTime
}

# ------------------------------------------------------------------- tablas
Write-Host "== tablas =="
foreach ($t in $def.tablas) {
  $existente = $modelo.Tables.Find($t.nombre)

  if ($null -eq $existente) {
    Anotar "crear tabla $($t.nombre) ($($t.columnas.Count) columnas)"
    if ($Aplicar) {
      $tabla = New-Object Microsoft.AnalysisServices.Tabular.Table
      $tabla.Name = $t.nombre
      $tabla.Description = $t.descripcion
      foreach ($c in $t.columnas) {
        $col = New-Object Microsoft.AnalysisServices.Tabular.DataColumn
        $col.Name = $c.nombre
        $col.SourceColumn = $c.nombre
        $col.DataType = $tipos[$c.tipo]
        $col.IsHidden = [bool]$c.oculta
        $tabla.Columns.Add($col)
      }
      $part = New-Object Microsoft.AnalysisServices.Tabular.Partition
      $part.Name = $t.nombre
      $src = New-Object Microsoft.AnalysisServices.Tabular.MPartitionSource
      $src.Expression = $t.expresion_m
      $part.Source = $src
      $tabla.Partitions.Add($part)
      $modelo.Tables.Add($tabla)
    }
    continue
  }

  # Existe: solo se agregan las columnas que falten. No se borra ninguna, porque
  # un visual puede estar usandola y aqui no hay forma de saberlo.
  $faltan = @()
  foreach ($c in $t.columnas) {
    if ($null -eq $existente.Columns.Find($c.nombre)) { $faltan += $c }
  }
  if ($faltan.Count -gt 0) {
    Anotar "tabla $($t.nombre): agregar $($faltan.Count) columnas -> $(($faltan.nombre) -join ', ')"
    if ($Aplicar) {
      foreach ($c in $faltan) {
        $col = New-Object Microsoft.AnalysisServices.Tabular.DataColumn
        $col.Name = $c.nombre
        $col.SourceColumn = $c.nombre
        $col.DataType = $tipos[$c.tipo]
        $col.IsHidden = [bool]$c.oculta
        $existente.Columns.Add($col)
      }
    }
  }
}

# ------------------------------------------------------- tabla de medidas
Write-Host ""
Write-Host "== tabla de medidas =="
$nombreMedidas = $def.tabla_medidas
$tablaMedidas = $modelo.Tables.Find($nombreMedidas)
if ($null -eq $tablaMedidas) {
  Anotar "crear tabla $nombreMedidas (contenedor, sin datos)"
  if ($Aplicar) {
    $tm = New-Object Microsoft.AnalysisServices.Tabular.Table
    $tm.Name = $nombreMedidas
    $tm.Description = "Contenedor de medidas. No tiene datos propios."
    $col = New-Object Microsoft.AnalysisServices.Tabular.DataColumn
    $col.Name = "_"
    $col.SourceColumn = "_"
    $col.DataType = [Microsoft.AnalysisServices.Tabular.DataType]::Int64
    $col.IsHidden = $true
    $tm.Columns.Add($col)
    $part = New-Object Microsoft.AnalysisServices.Tabular.Partition
    $part.Name = $nombreMedidas
    $src = New-Object Microsoft.AnalysisServices.Tabular.CalculatedPartitionSource
    $src.Expression = "ROW(""_"", 1)"
    $part.Source = $src
    $tm.Partitions.Add($part)
    $modelo.Tables.Add($tm)
    $tablaMedidas = $tm
  }
} else {
  Write-Host "  $nombreMedidas ya existe"
}

# --------------------------------------------------------------- relaciones
Write-Host ""
Write-Host "== relaciones =="
foreach ($r in $def.relaciones) {
  $ya = $false
  foreach ($rel in $modelo.Relationships) {
    if ($rel.FromTable.Name -eq $r.desde_tabla -and $rel.FromColumn.Name -eq $r.desde_columna -and
        $rel.ToTable.Name   -eq $r.hacia_tabla -and $rel.ToColumn.Name   -eq $r.hacia_columna) { $ya = $true; break }
  }
  if ($ya) { continue }

  Anotar "crear relacion $($r.desde_tabla)[$($r.desde_columna)] -> $($r.hacia_tabla)[$($r.hacia_columna)]"
  if ($Aplicar) {
    $ft = $modelo.Tables.Find($r.desde_tabla); $tt = $modelo.Tables.Find($r.hacia_tabla)
    if ($null -eq $ft -or $null -eq $tt) { Write-Warning "    faltan tablas; se omite"; continue }
    $rel = New-Object Microsoft.AnalysisServices.Tabular.SingleColumnRelationship
    $rel.FromColumn = $ft.Columns.Find($r.desde_columna)
    $rel.ToColumn   = $tt.Columns.Find($r.hacia_columna)
    $rel.FromCardinality = [Microsoft.AnalysisServices.Tabular.RelationshipEndCardinality]::Many
    $rel.ToCardinality   = [Microsoft.AnalysisServices.Tabular.RelationshipEndCardinality]::One
    $rel.CrossFilteringBehavior = [Microsoft.AnalysisServices.Tabular.CrossFilteringBehavior]::OneDirection
    $modelo.Relationships.Add($rel)
  }
}

# ------------------------------------------------------------------ medidas
Write-Host ""
Write-Host "== medidas =="
if ($null -ne $tablaMedidas) {
  foreach ($m in $def.medidas) {
    # Puede existir en otra tabla de una version anterior del modelo
    $previa = $null
    foreach ($tb in $modelo.Tables) {
      $cand = $tb.Measures.Find($m.nombre)
      if ($null -ne $cand) { $previa = $cand; break }
    }

    if ($null -eq $previa) {
      Anotar "crear medida [$($m.nombre)]"
      if ($Aplicar) {
        $med = New-Object Microsoft.AnalysisServices.Tabular.Measure
        $med.Name = $m.nombre
        $med.Expression = $m.expresion
        $med.FormatString = $m.formato
        if ($m.carpeta) { $med.DisplayFolder = $m.carpeta }
        if ($m.descripcion) { $med.Description = $m.descripcion }
        $tablaMedidas.Measures.Add($med)
      }
    }
    elseif ($previa.Expression.Trim() -ne $m.expresion.Trim() -or
            $previa.FormatString -ne $m.formato) {
      Anotar "actualizar medida [$($m.nombre)] en [$($previa.Table.Name)]"
      if ($Aplicar) {
        $previa.Expression = $m.expresion
        $previa.FormatString = $m.formato
        if ($m.carpeta) { $previa.DisplayFolder = $m.carpeta }
        if ($m.descripcion) { $previa.Description = $m.descripcion }
      }
    }
  }
} else {
  Write-Host "  (la tabla de medidas se crea en esta misma corrida; vuelve a ejecutar para las medidas)"
}

# ------------------------------------------------------------------- guardar
Write-Host ""
if ($cambios.Count -eq 0) {
  Write-Host "el modelo ya coincide con la definicion; nada que hacer"
} elseif ($Aplicar) {
  $modelo.SaveChanges() | Out-Null
  Write-Host "aplicados $($cambios.Count) cambios. Guarda el .pbix en Power BI Desktop para conservarlos."
} else {
  Write-Host "$($cambios.Count) cambios pendientes. Vuelve a ejecutar con -Aplicar para escribirlos."
}

$srv.Disconnect()
