#requires -Version 7.0
# Reject a GPU bundle whose packaged launcher did not complete strictly CPU inference.
[CmdletBinding()]
param([Parameter(Mandatory)][string]$Bundle)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path -LiteralPath $Bundle).ProviderPath.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
$manifest = Get-Content -LiteralPath (Join-Path $root 'bundle.json') -Raw | ConvertFrom-Json -AsHashtable
$report = $manifest['self_test']
if ($report -isnot [hashtable] -or -not $report['ok']) { throw 'The bundled CPU self-test did not succeed.' }
$providers = @($report['providers'])
if ($providers.Count -ne 1 -or $providers[0] -cne 'CPUExecutionProvider') {
    throw 'The bundled CPU self-test must use only CPUExecutionProvider.'
}
foreach ($key in 'onnxruntime_module', 'model') {
    $path = [string]$report[$key]
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { throw "The self-test $key is missing." }
    $resolved = (Resolve-Path -LiteralPath $path).ProviderPath
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The self-test $key is outside the bundle: $resolved"
    }
}
Write-Host 'DirectML bundle CPU inference used only the bundled runtime and model.'
