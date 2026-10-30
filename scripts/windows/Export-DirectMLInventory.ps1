#requires -Version 7.0
# Exports one unverified ZIP for a release notice inventory.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageDirectory,
    [Parameter(Mandatory)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$zip = @(Get-ChildItem -LiteralPath $PackageDirectory -File -Filter 'orchestrant-directml-*-windows-x64.zip')
if ($zip.Count -ne 1) { throw "Expected one x64 DirectML inventory ZIP, found $($zip.Count)" }
$pending = "$OutputDirectory-pending"
if ((Test-Path -LiteralPath $OutputDirectory) -or (Test-Path -LiteralPath $pending)) {
    throw "Refusing to reuse a DirectML inventory output: $OutputDirectory"
}
$null = New-Item -ItemType Directory -Path $pending
Copy-Item -LiteralPath $zip[0].FullName -Destination $pending
@'
INVENTORY INPUT ONLY. This package has not passed its complete notice check.
Do not install, publish or distribute it. Generate the notice from a matching
release round, then rebuild and verify the final packages.
'@ | Set-Content -LiteralPath (Join-Path $pending 'INVENTORY-ONLY.txt') -Encoding utf8NoBOM
Move-Item -LiteralPath $pending -Destination $OutputDirectory
