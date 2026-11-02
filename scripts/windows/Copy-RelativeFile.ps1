#requires -Version 7.0
# Copies files to the same relative paths under another root, creating parents; it does not check their content.
param([string]$From, [string]$To, [string[]]$RelativePath)
foreach ($path in $RelativePath) {
    $target = Join-Path $To $path
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent)
    Copy-Item -LiteralPath (Join-Path $From $path) -Destination $target -ErrorAction Stop
}
