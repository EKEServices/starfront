# Make the distributable: a zip of the staged files and the manifest N.I.N.A.'s
# plugin manager reads.
#
#     dotnet build src/Starfront.Collab/Starfront.Collab.csproj -c Release -t:PackagePlugin
#
# The manifest is written from Plugin.props - the same file the assembly's
# attributes are generated from - so the DLL and the manifest can never
# disagree about the plugin's name, GUID or version. The checksum is of the
# final zip bytes: rebuild the zip and the manifest must be remade with it.
#
# Without -InstallerUrl the manifest carries a placeholder URL, good for
# checking the build and nothing else; a real release names where the zip
# will be downloaded from.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$AssemblyName,
    [Parameter(Mandatory)][string]$Props,
    [Parameter(Mandatory)][string]$Version,
    [string]$Repository,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$InstallerUrl,
    [ValidateSet('Release', 'Beta')][string]$Channel = 'Release'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw "A plugin version has four numeric parts; got '$Version'." }
$marker = '.starfront-stage'
$Stage = (Resolve-Path -LiteralPath $Stage).Path
$assembly = Join-Path $Stage $AssemblyName
if (-not (Test-Path -LiteralPath $assembly)) { throw "Staged assembly not found: $assembly" }
$fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($assembly).FileVersion
if ($fileVersion -ne $Version) { throw "The staged DLL says $fileVersion but the build asked for $Version. Rebuild before packaging." }

# -- the metadata, from Plugin.props -------------------------------------------
[xml]$xml = Get-Content -LiteralPath $Props -Raw
$group = $xml.Project.PropertyGroup
function Prop([string]$name) {
    $node = $group.SelectSingleNode($name)
    if ($null -eq $node) { return '' }
    return [string]$node.InnerText
}
function Parts([string]$version) {
    $p = $version.Split('.')
    return [ordered]@{ Major = $p[0]; Minor = $p[1]; Patch = $p[2]; Build = $p[3] }
}
$name = Prop 'Title'
$id = Prop 'PluginId'
$minimum = Prop 'MinimumNinaVersion'
if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($id)) { throw 'Plugin.props needs a Title and a PluginId.' }
if ($minimum -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw "MinimumNinaVersion must have four parts; got '$minimum'." }
if ([string]::IsNullOrWhiteSpace($Repository)) { $Repository = Prop 'RepositoryUrl' }
$tags = @((Prop 'PluginTags').Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$baseName = [IO.Path]::GetFileNameWithoutExtension($AssemblyName)
$archiveName = "$baseName.$Version"
if ([string]::IsNullOrWhiteSpace($InstallerUrl)) {
    $InstallerUrl = "https://example.invalid/releases/$archiveName.zip"
}
$uri = [uri]$InstallerUrl
if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https') { throw 'InstallerUrl must be an absolute HTTPS URL.' }

# -- the archive ---------------------------------------------------------------
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
$zip = Join-Path $OutputDirectory "$archiveName.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip }
$files = @(Get-ChildItem -LiteralPath $Stage -Force | Where-Object Name -ne $marker)
Compress-Archive -LiteralPath $files.FullName -DestinationPath $zip
$checksum = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash

# -- the manifest --------------------------------------------------------------
$manifest = [ordered]@{
    Name = $name
    Identifier = $id
    Version = Parts $Version
    Author = Prop 'Company'
    Homepage = Prop 'PluginHomepage'
    Repository = $Repository
    License = Prop 'PluginLicense'
    LicenseURL = Prop 'PluginLicenseUrl'
    ChangelogURL = Prop 'PluginChangelogUrl'
    Tags = $tags
    MinimumApplicationVersion = Parts $minimum
    Descriptions = [ordered]@{
        ShortDescription = Prop 'Description'
        LongDescription = Prop 'PluginLongDescription'
        FeaturedImageURL = Prop 'PluginFeaturedImageUrl'
        ScreenshotURL = Prop 'PluginScreenshotUrl'
        AltScreenshotURL = Prop 'PluginAltScreenshotUrl'
    }
    Installer = [ordered]@{
        URL = $InstallerUrl
        Type = 'ARCHIVE'
        Checksum = $checksum
        ChecksumType = 'SHA256'
    }
}
if ($Channel -eq 'Beta') { $manifest['Channel'] = 'Beta' }

# The required fields of the central repository's schema, checked here because
# Test-Json does not exist on Windows PowerShell and a manifest that fails
# validation is silently dropped by N.I.N.A.
foreach ($required in 'Name', 'Identifier', 'Author', 'Repository', 'License', 'LicenseURL') {
    if ([string]::IsNullOrWhiteSpace([string]$manifest[$required])) { throw "The manifest needs a $required; set it in Plugin.props." }
}
if ([string]::IsNullOrWhiteSpace($manifest.Descriptions.ShortDescription)) { throw 'The manifest needs a Description in Plugin.props.' }
if ($manifest.Descriptions.ShortDescription.Length -gt 256) { throw 'Description must be 256 characters or fewer.' }
if ($manifest.Descriptions.LongDescription.Length -gt 10000) { throw 'PluginLongDescription must be 10000 characters or fewer.' }
if ($name.Length -gt 50) { throw 'Title must be 50 characters or fewer.' }
if ($tags.Count -gt 20 -or ($tags | Where-Object { $_.Length -gt 30 })) { throw 'At most 20 tags of at most 30 characters.' }
try { [void][guid]::Parse($id) } catch { throw "PluginId is not a GUID: $id" }

$json = $manifest | ConvertTo-Json -Depth 10
$manifestPath = Join-Path $OutputDirectory "$archiveName.manifest.json"
[IO.File]::WriteAllText($manifestPath, $json, (New-Object Text.UTF8Encoding $false))
Write-Host "Created $zip"
Write-Host "Created $manifestPath"
