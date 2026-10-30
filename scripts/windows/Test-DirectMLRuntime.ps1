#requires -Version 7.0
# Proves wheel-selected ORT files and the compiled monitor survive packaging byte for byte.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Bundle,
    [Parameter(Mandatory)][string]$WheelDir,
    [Parameter(Mandatory)][string]$AppWheel,
    [Parameter(Mandatory)][string]$PackageDirectory,
    [switch]$InventoryOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).ProviderPath
$hub = Join-Path $repo 'third_party/ANTfrastructure'
Import-Module (Join-Path $hub 'windows/scripts/modules/WindowsOrtLicense.Common.psm1') -DisableNameChecking
Import-Module (Join-Path $hub 'windows/scripts/modules/WindowsPackageBinary.Common.psm1') -DisableNameChecking
Import-Module (Join-Path $hub 'windows/scripts/modules/WindowsMsiLicense.Common.psm1') -DisableNameChecking
$ort = Join-Path $Bundle 'runtime/Lib/site-packages/onnxruntime/capi'
$providers = @(Get-ChildItem -LiteralPath $ort -Filter 'onnxruntime_providers_*.dll' -File | ForEach-Object {
        if ($_.Name -cnotmatch '^onnxruntime_providers_([a-z0-9_]+)\.dll$') { throw "Invalid ORT provider name: $($_.Name)" }
        $Matches[1]
    })
if ($providers -cnotcontains 'shared') { throw 'The shared ONNX Runtime provider bridge is missing.' }
$null = Resolve-PythonOrtLicenses -Bundle $Bundle -WheelDir $WheelDir -RequiredProvider $providers -RequirePythonExtension -RequireDirectMl
$names = @('onnxruntime.dll', 'onnxruntime_pybind11_state.pyd', 'DirectML.dll') + @($providers | ForEach-Object { "onnxruntime_providers_$_.dll" })
$relative = 'runtime/Lib/site-packages/onnxruntime/capi'
$sources = [string[]]@($names | ForEach-Object {
        $file = Get-Item -LiteralPath (Join-Path $ort $_) -ErrorAction Stop
        if ($file.PSIsContainer -or $file.Length -le 0) { throw "Missing or empty runtime file: $_" }
        $file.FullName
    })
$zips = @(Get-ChildItem -LiteralPath $PackageDirectory -Filter 'orchestrant-directml-*-windows-x64.zip' -File)
$msis = @(Get-ChildItem -LiteralPath $PackageDirectory -Filter 'orchestrant-directml-*-windows-x64.msi' -File)
$msixes = @(Get-ChildItem -LiteralPath $PackageDirectory -Filter 'orchestrant-directml-*-windows-x64.msix' -File)
if ($zips.Count -ne 1 -or ($InventoryOnly -and ($msis.Count -or $msixes.Count)) -or
    (-not $InventoryOnly -and ($msis.Count -ne 1 -or $msixes.Count -ne 1))) {
    throw 'Expected one DirectML ZIP and, outside inventory mode, one MSI and one MSIX.'
}
$prefix = [IO.Path]::GetFileNameWithoutExtension($zips[0].Name)
$monitorDir = Join-Path $Bundle 'runtime/Lib/site-packages/orchestrant/yolo'
$monitors = @(Get-ChildItem -LiteralPath $monitorDir -Filter 'monitor.*' -File)
if ($monitors.Count -ne 1 -or $monitors[0].Name -cnotmatch '^monitor\.cp[0-9]+-win_amd64\.pyd$' -or
    ($monitors[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -or $monitors[0].Length -le 0) {
    throw 'The bundle must contain exactly one nonempty compiled x64 monitor module.'
}
$monitor = $monitors[0]
Assert-PackageBinary -Path $AppWheel -Member "orchestrant/yolo/$($monitor.Name)" -SourcePath $monitor.FullName
$members = [string[]]@(@($names | ForEach-Object { "$relative/$_" }) + "runtime/Lib/site-packages/orchestrant/yolo/$($monitor.Name)")
$sources = [string[]]@($sources + $monitor.FullName)
function Assert-ProviderSet([string]$Path, [string]$Directory, [switch]$Msi) {
    $parameters = @{ Path = $Path; Directory = $Directory }
    if ($Msi) { $parameters.RootDirectoryId = 'INSTALLFOLDER'; $parameters.RootDirectoryName = 'OrchestrANT DirectML' }
    $actual = @(Get-PackageOrtProviderNames @parameters | Sort-Object -CaseSensitive)
    $expected = @($providers | ForEach-Object { "onnxruntime_providers_$_.dll" } | Sort-Object -CaseSensitive)
    if (($actual -join "`n") -cne ($expected -join "`n")) { throw "Finished package ORT providers differ from the proven bundle: $Path" }
}
Assert-ProviderSet -Path $zips[0].FullName -Directory "$prefix/$relative"
Assert-PackageBinary -Path $zips[0].FullName -Member @($members | ForEach-Object { "$prefix/$_" }) -SourcePath $sources
if (-not $InventoryOnly) {
    Assert-ProviderSet -Path $msis[0].FullName -Directory $relative -Msi
    Assert-ProviderSet -Path $msixes[0].FullName -Directory $relative
    $ids = [string[]]@($members | ForEach-Object {
            Get-MsiInstalledFileId -Path $msis[0].FullName -RelativePath $_ -RootDirectoryId 'INSTALLFOLDER' -RootDirectoryName 'OrchestrANT DirectML'
        })
    Assert-PackageBinary -Path $msis[0].FullName -Member $ids -SourcePath $sources
    Assert-PackageBinary -Path $msixes[0].FullName -Member $members -SourcePath $sources
}
$null = Resolve-PythonOrtLicenses -Bundle $Bundle -WheelDir $WheelDir -RequiredProvider $providers -RequirePythonExtension -RequireDirectMl
Write-Host 'Finished DirectML candidate runtime and compiled monitor match their selected wheels and bundle.'
