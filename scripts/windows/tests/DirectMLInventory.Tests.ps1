#requires -Version 7.0
using module ./WindowsSuite.Fixture.psm1
# An inventory input must never be confused with a verified release package.
$repo, $fixture = Initialize-TestSuite -FixturePrefix 'directml-inventory-'
$packages = New-FixtureDirectory -RelativePath 'packages[fixture]'
$exporter = Join-Path $repo 'scripts/windows/Export-DirectMLInventory.ps1'
$output = Join-Path $fixture 'inventory'
$zip = Join-Path $packages 'orchestrant-directml-amd-1.2.3-windows-x64.zip'
$other = Join-Path $packages 'orchestrant-directml-nvidia-1.2.3-windows-x64.zip'
function Export-Fixture([string]$Destination = $output) {
    & $exporter -PackageDirectory $packages -OutputDirectory $Destination
}
function Test-ExportFailure([string]$Destination = $output) {
    try { Export-Fixture $Destination; return $false } catch { return $true }
}
Invoke-TestSuite -ExpectedCount 5 -Body {
    Describe 'DirectML inventory export' {
        It 'rejects an empty package directory' { Assert-True (Test-ExportFailure) }
        It 'exports the one ZIP with an inventory-only warning, not other packages' {
            [IO.File]::WriteAllText($zip, 'inventory fixture')
            [IO.File]::WriteAllText((Join-Path $packages 'candidate.msi'), 'not for inventory')
            Export-Fixture
            Assert-True (Test-Path -LiteralPath (Join-Path $output (Split-Path $zip -Leaf)) -PathType Leaf)
            Assert-True (Test-Path -LiteralPath (Join-Path $output 'INVENTORY-ONLY.txt') -PathType Leaf)
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $output 'candidate.msi')))
        }
        It 'refuses to overwrite an existing export' { Assert-True (Test-ExportFailure) }
        It 'refuses an existing pending export' {
            $pendingOutput = Join-Path $fixture 'another'
            $null = New-Item -ItemType Directory -Path "$pendingOutput-pending"
            Assert-True (Test-ExportFailure $pendingOutput)
        }
        It 'rejects ambiguous matching ZIPs' {
            [IO.File]::WriteAllText($other, 'second fixture')
            Assert-True (Test-ExportFailure (Join-Path $fixture 'ambiguous'))
        }
    }
}
