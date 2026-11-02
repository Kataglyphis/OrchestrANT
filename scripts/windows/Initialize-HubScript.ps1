#requires -Version 7.0
# Dot-sourced prologue; callers set ErrorActionPreference to Stop first so a missing companion cannot continue.
param([string]$HubRoot, [string[]]$HubModule = @())
Set-StrictMode -Version Latest
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).ProviderPath
$hub = if ($HubRoot) { $HubRoot } else { Join-Path $repo 'third_party/ANTfrastructure' }
foreach ($moduleName in $HubModule) {
    Import-Module (Join-Path $hub "windows/scripts/modules/$moduleName.psm1") -DisableNameChecking -ErrorAction Stop
}

function Get-SingleFile([string]$Directory, [string]$Filter, [string]$Description) {
    $found = @(Get-ChildItem -LiteralPath $Directory -File -Filter $Filter)
    if ($found.Count -ne 1) { throw "Expected one $Description, found $($found.Count)" }
    return $found[0]
}

function Publish-PendingDirectory([string]$Destination, [string]$RefusalMessage, [scriptblock]$Fill) {
    $pending = "$Destination-pending"
    if ((Test-Path -LiteralPath $Destination) -or (Test-Path -LiteralPath $pending)) { throw $RefusalMessage }
    $null = New-Item -ItemType Directory -Path $pending
    & $Fill $pending
    Move-Item -LiteralPath $pending -Destination $Destination
}
