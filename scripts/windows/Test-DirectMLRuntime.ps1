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
. (Join-Path $PSScriptRoot 'Initialize-HubScript.ps1') -HubModule 'WindowsOrtLicense.Common', 'WindowsPackageBinary.Common', 'WindowsMsiLicense.Common'
$lock = Join-Path $repo 'resources/models/models.lock'
$modelNames = @(Get-Content -LiteralPath $lock | ForEach-Object { $cols = -split $_; if ($cols.Count -ge 3 -and $cols[0] -notlike '#*') { $cols[1] } })
if ($modelNames.Count -eq 0) { throw "$lock names no file" }
$modelDirectory = Join-Path $Bundle 'share/orchestrant/models'
function Assert-LockedDetectorModel {
    & python -I (Join-Path $hub 'shared/python/verify_locked_files.py') --root $Bundle `
        --directory $modelDirectory --lock $lock `
        --fetcher (Join-Path $hub 'linux/scripts/lib/fetch-data-lock.py') `
        @($modelNames | ForEach-Object { '--file'; $_ })
    if ($LASTEXITCODE -ne 0) { throw 'The bundled model files do not match models.lock.' }
}
Assert-LockedDetectorModel
$ort = Join-Path $Bundle 'runtime/Lib/site-packages/onnxruntime/capi'
function Get-DirectMlProviderNames([string]$Directory) {
    return @(Get-ChildItem -LiteralPath $Directory -Filter 'onnxruntime_providers_*.dll' -File | ForEach-Object {
        if ($_.Name -cnotmatch '^onnxruntime_providers_([a-z0-9_]+)\.dll$') { throw "Invalid ORT provider name: $($_.Name)" }
        $Matches[1]
    })
}
$providers = @(Get-DirectMlProviderNames -Directory $ort)
if ($providers -cnotcontains 'shared') { throw 'The shared ONNX Runtime provider bridge is missing.' }
function Assert-BundleOrtWheel {
    $null = Resolve-PythonOrtLicenses -Bundle $Bundle -WheelDir $WheelDir -RequiredProvider $providers -RequirePythonExtension -RequireDirectMl
}
Assert-BundleOrtWheel
$names = @('onnxruntime.dll', 'onnxruntime_pybind11_state.pyd', 'DirectML.dll') + @($providers | ForEach-Object { "onnxruntime_providers_$_.dll" })
$relative = 'runtime/Lib/site-packages/onnxruntime/capi'
$msiRoot = @{ RootDirectoryId = 'INSTALLFOLDER'; RootDirectoryName = 'OrchestrANT DirectML' }
$family = [string[]]@($names | Where-Object { $_ -clike '*.dll' })
Assert-PackageOrtFamily -Path $Bundle -Directory $relative -Name $family
$sources = [string[]]@($names | ForEach-Object {
        $file = Get-Item -LiteralPath (Join-Path $ort $_) -ErrorAction Stop
        if ($file.PSIsContainer -or $file.Length -le 0) { throw "Missing or empty runtime file: $_" }
        $file.FullName
    })
function Get-CandidatePackage([string]$Extension) { Get-ChildItem -LiteralPath $PackageDirectory -Filter "orchestrant-directml-*-windows-x64.$Extension" -File }
$zips = @(Get-CandidatePackage -Extension zip)
$msis = @(Get-CandidatePackage -Extension msi)
$msixes = @(Get-CandidatePackage -Extension msix)
if ($zips.Count -ne 1 -or ($InventoryOnly -and ($msis.Count -or $msixes.Count)) -or
    (-not $InventoryOnly -and ($msis.Count -ne 1 -or $msixes.Count -ne 1))) {
    throw 'Expected one DirectML ZIP and, outside inventory mode, one MSI and one MSIX.'
}
$prefix = [IO.Path]::GetFileNameWithoutExtension($zips[0].Name)
if ($prefix -cnotmatch '^orchestrant-directml-(amd|nvidia)-') { throw 'The candidate filename does not identify a supported GPU vendor.' }
$expectedVendor = $Matches[1]
$vendorMarker = Join-Path $Bundle 'share/orchestrant/directml-vendor.txt'
if (-not (Test-Path -LiteralPath $vendorMarker -PathType Leaf) -or
    (Get-Content -LiteralPath $vendorMarker -Raw).Trim() -cne $expectedVendor) {
    throw 'The DirectML bundle vendor marker is missing or disagrees with the candidate.'
}
$monitorDir = Join-Path $Bundle 'runtime/Lib/site-packages/orchestrant/yolo'
function Get-CompiledMonitor([string]$Directory) {
    $monitors = @(Get-ChildItem -LiteralPath $Directory -Filter 'monitor.*' -File)
    if ($monitors.Count -ne 1 -or $monitors[0].Name -cnotmatch '^monitor\.cp[0-9]+-win_amd64\.pyd$' -or
        ($monitors[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -or $monitors[0].Length -le 0) {
        throw 'The bundle must contain exactly one nonempty compiled x64 monitor module.'
    }
    return $monitors[0]
}
$monitor = Get-CompiledMonitor -Directory $monitorDir
Assert-PackageBinary -Path $AppWheel -Member "orchestrant/yolo/$($monitor.Name)" -SourcePath $monitor.FullName
$modelMembers = @($modelNames | ForEach-Object { "share/orchestrant/models/$_" })
$members = [string[]]@(@($names | ForEach-Object { "$relative/$_" }) + "runtime/Lib/site-packages/orchestrant/yolo/$($monitor.Name)" + $modelMembers + 'share/orchestrant/directml-vendor.txt')
$sources = [string[]]@($sources + $monitor.FullName + @($modelNames | ForEach-Object { Join-Path $modelDirectory $_ }) + $vendorMarker)
function Assert-ProviderSet([string]$Path, [string]$Directory, [switch]$Msi) {
    $parameters = @{ Path = $Path; Directory = $Directory }
    if ($Msi) { $parameters += $msiRoot }
    $actual = @(Get-PackageOrtProviderNames @parameters | Sort-Object -CaseSensitive)
    $expected = @($providers | ForEach-Object { "onnxruntime_providers_$_.dll" } | Sort-Object -CaseSensitive)
    if (($actual -join "`n") -cne ($expected -join "`n")) { throw "Finished package ORT providers differ from the proven bundle: $Path" }
}
Assert-ProviderSet -Path $zips[0].FullName -Directory "$prefix/$relative"
Assert-PackageOrtFamily -Path $zips[0].FullName -Directory "$prefix/$relative" -Name $family
Assert-PackageBinary -Path $zips[0].FullName -Member @($members | ForEach-Object { "$prefix/$_" }) -SourcePath $sources
if (-not $InventoryOnly) {
    Assert-ProviderSet -Path $msis[0].FullName -Directory $relative -Msi
    Assert-ProviderSet -Path $msixes[0].FullName -Directory $relative
    Assert-PackageOrtFamily -Path $msis[0].FullName -Directory $relative -Name $family @msiRoot
    Assert-PackageOrtFamily -Path $msixes[0].FullName -Directory $relative -Name $family
    $ids = [string[]]@($members | ForEach-Object {
            Get-MsiInstalledFileId -Path $msis[0].FullName -RelativePath $_ @msiRoot
        })
    Assert-PackageBinary -Path $msis[0].FullName -Member $ids -SourcePath $sources
    Assert-PackageBinary -Path $msixes[0].FullName -Member $members -SourcePath $sources
}
Assert-BundleOrtWheel
Assert-LockedDetectorModel
Write-Host 'Finished DirectML candidate runtime and compiled monitor match their wheels; packaged model files match the lock.'
