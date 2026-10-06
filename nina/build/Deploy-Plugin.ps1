# Put the staged plugin where N.I.N.A. loads plugins from, for development.
#
#     dotnet build src/Starfront.Collab/Starfront.Collab.csproj -t:DeployPlugin
#
# N.I.N.A. keeps its plugins under %LOCALAPPDATA%\NINA\Plugins\3.0.0\<name>,
# one folder per plugin named after its title. Close N.I.N.A. first: a file it
# still has open cannot be replaced, and this checks before copying anything so
# that a half-replaced plugin never happens.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$Title,
    [string]$Destination
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$marker = '.starfront-stage'
if (-not (Test-Path -LiteralPath (Join-Path $Stage $marker))) { throw 'Use the DeployPlugin target to stage files first.' }
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $folder = $Title.Replace('\', '-').Replace('/', '-')
    foreach ($character in [IO.Path]::GetInvalidFileNameChars()) { $folder = $folder.Replace([string]$character, '_') }
    if ([string]::IsNullOrWhiteSpace($folder) -or $folder -in @('.', '..')) { throw 'The plugin title does not form a valid folder name.' }
    $Destination = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "NINA\Plugins\3.0.0\$folder"
}
$stageRoot = [IO.Path]::GetFullPath($Stage)
$files = @(Get-ChildItem -LiteralPath $stageRoot -File -Recurse | Where-Object Name -ne $marker)
foreach ($file in $files) {
    $relative = $file.FullName.Substring($stageRoot.Length).TrimStart('\', '/')
    $target = Join-Path $Destination $relative
    if (Test-Path -LiteralPath $target) {
        try { $handle = [IO.File]::Open($target, 'Open', 'ReadWrite', 'None'); $handle.Dispose() }
        catch { throw "Cannot replace '$target'. Close N.I.N.A. and retry. $($_.Exception.Message)" }
    }
}
foreach ($file in $files) {
    $relative = $file.FullName.Substring($stageRoot.Length).TrimStart('\', '/')
    $target = Join-Path $Destination $relative
    New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
}
Write-Host "Deployed the plugin to $Destination. Start N.I.N.A. to load it."
