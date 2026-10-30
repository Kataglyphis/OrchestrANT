#requires -Version 7.0
# Builds a separately identified DirectML package with the shared Python bundle builders.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('amd', 'nvidia')][string]$Vendor,
    [switch]$InventoryOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).ProviderPath
$hub = Join-Path $repo 'third_party/ANTfrastructure'
Import-Module (Join-Path $hub 'windows/scripts/modules/WindowsPythonWheel.Common.psm1') -DisableNameChecking
$python = Find-UvPython -Request (Get-PinnedPythonVersion).MajorMinor
& $python -I (Join-Path $hub 'linux/scripts/lib/fetch-data-lock.py') --lock (Join-Path $repo 'resources/models/models.lock')
if ($LASTEXITCODE -ne 0) { throw "Fetching the pinned model failed: $LASTEXITCODE" }

$env:UV_SYNC_EXTRAS = 'app'
& pwsh -NoProfile -File (Join-Path $hub 'windows/scripts/python/Invoke-CiPackaging.ps1') -RepoRoot $repo -TargetArch amd64 -FreeThreadedWheel off
if ($LASTEXITCODE -ne 0) { throw "Building the project wheel failed: $LASTEXITCODE" }

$output = Join-Path $repo $(if ($InventoryOnly) { 'dist/directml-inventory' } else { 'dist/directml' })
$null = New-Item -ItemType Directory -Force -Path $output
$config = Get-Content -LiteralPath (Join-Path $repo 'packaging/app.json') -Raw | ConvertFrom-Json -AsHashtable
$config['name'] = 'OrchestrANT DirectML'
$config['id'] = "orchestrant-directml-$Vendor"
$config['msi_upgrade_code'] = '{6ED76451-2968-485C-8B15-229B5BDB944E}'
$config['self_test'] = @('yolo-monitor', '--self-test', '--device', 'cpu')
$configPath = Join-Path $output 'app-directml.json'
$config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $configPath -Encoding utf8
$wheel = @(Get-ChildItem -LiteralPath (Join-Path $repo 'dist') -File -Filter 'orchestrant-*-win_amd64.whl')
if ($wheel.Count -ne 1) { throw "Expected one x64 binary wheel, found $($wheel.Count)" }
$version = ($wheel[0].Name -split '-')[1]
$bundle = Join-Path $output "orchestrant-directml-$Vendor-$version-windows-x64"
$packages = Join-Path $output 'packages'
& pwsh -NoProfile -File (Join-Path $hub 'windows/scripts/python/New-PythonAppBundle.ps1') -RepoRoot $repo -Config $configPath -WheelDir dist -OutDir $bundle -TargetArch amd64
if ($LASTEXITCODE -ne 0) { throw "DirectML bundle build failed: $LASTEXITCODE" }
& (Join-Path $PSScriptRoot 'Test-DirectMLCpuReport.ps1') -Bundle $bundle
& (Join-Path $hub 'windows/scripts/diagnostics/Test-NvidiaOpenCvRuntime.ps1') `
    -Bundle $bundle -CudaVersion '13.4' -CudnnVersion '9.27.0.42'

$runtime = Join-Path $bundle 'runtime/python.exe'
& $runtime -I -c "import onnxruntime as ort; providers = ort.get_available_providers(); print(providers); assert 'DmlExecutionProvider' in providers, 'DirectML provider missing'"
if ($LASTEXITCODE -ne 0) { throw 'The bundled ONNX Runtime does not offer DirectML' }
$formats = if ($InventoryOnly) { 'zip' } else { 'zip,msi,msix' }
& pwsh -NoProfile -File (Join-Path $hub 'windows/scripts/python/New-PythonAppPackage.ps1') -RepoRoot $repo -Config $configPath -Bundle $bundle -OutDir $packages -Formats $formats
if ($LASTEXITCODE -ne 0) { throw "DirectML packaging failed: $LASTEXITCODE" }
Import-Module (Join-Path $hub 'windows/scripts/modules/WindowsOrtProvenance.Common.psm1') -DisableNameChecking
$wheelStore = if ($env:ORT_CHAIN_WHEEL_DIR) { $env:ORT_CHAIN_WHEEL_DIR } else { Get-OrtChainWheelStore }
& (Join-Path $PSScriptRoot 'Test-DirectMLRuntime.ps1') -Bundle $bundle -WheelDir $wheelStore -AppWheel $wheel[0].FullName -PackageDirectory $packages -InventoryOnly:$InventoryOnly

if ($InventoryOnly) {
    & (Join-Path $PSScriptRoot 'Export-DirectMLInventory.ps1') -PackageDirectory $packages -OutputDirectory (Join-Path $output 'inventory')
    return
}

& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'Test-PackageLicenses.ps1') -PackageDirectory $packages -Arch x64
if ($LASTEXITCODE -ne 0) { throw "DirectML package licence verification failed: $LASTEXITCODE" }
$verified = Join-Path $output 'verified'
$pending = Join-Path $output 'verified-pending'
if ((Test-Path -LiteralPath $verified) -or (Test-Path -LiteralPath $pending)) { throw "Refusing to reuse a previous DirectML verification directory under $output" }
$null = New-Item -ItemType Directory -Path $pending
Copy-Item -Path (Join-Path $packages '*') -Destination $pending -Recurse
Move-Item -LiteralPath $pending -Destination $verified
