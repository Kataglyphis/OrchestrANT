#requires -Version 7.0
# Exports one unverified ZIP for a release notice inventory.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageDirectory,
    [Parameter(Mandatory)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Initialize-HubScript.ps1')
$zip = Get-SingleFile -Directory $PackageDirectory -Filter 'orchestrant-directml-*-windows-x64.zip' -Description 'x64 DirectML inventory ZIP'
Publish-PendingDirectory -Destination $OutputDirectory -RefusalMessage "Refusing to reuse a DirectML inventory output: $OutputDirectory" -Fill {
    param($pending)
    Copy-Item -LiteralPath $zip.FullName -Destination $pending
    @'
INVENTORY INPUT ONLY. This package has not passed its complete notice check.
Do not install, publish or distribute it. Generate the notice from a matching
release round, then rebuild and verify the final packages.
'@ | Set-Content -LiteralPath (Join-Path $pending 'INVENTORY-ONLY.txt') -Encoding utf8NoBOM
}
