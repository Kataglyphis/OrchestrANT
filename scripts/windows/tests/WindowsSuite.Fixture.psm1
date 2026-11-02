#requires -Version 7.0
# Gives script-style suites their checkout, hub harness and fixture lifetime; it does not check the code under test.
Set-StrictMode -Version Latest
$script:Hub = ''
$script:Fixture = ''

function Initialize-TestSuite([string]$FixturePrefix) {
    . (Join-Path $PSScriptRoot '../Initialize-HubScript.ps1') -HubRoot $env:ANTFRASTRUCTURE_DIR
    Import-Module (Join-Path $hub 'windows/scripts/tests/TestHarness.psm1') -Global -DisableNameChecking -ErrorAction Stop
    $script:Hub = $hub
    $script:Fixture = New-TestDir -Prefix $FixturePrefix
    return $repo, $script:Fixture
}

function Get-TestSuiteHub { return $script:Hub }

function New-FixtureDirectory([string]$RelativePath) {
    return (New-Item -ItemType Directory -Force -Path (Join-Path $script:Fixture $RelativePath)).FullName
}

function Copy-TestFile([string]$From, [string]$To, [string[]]$RelativePath) {
    foreach ($path in $RelativePath) {
        $target = Join-Path $To $path
        $null = New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent)
        Copy-Item -LiteralPath (Join-Path $From $path) -Destination $target -ErrorAction Stop
    }
}

function Reset-TestFile([string]$Path) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
}

function Invoke-TestSuite([int]$ExpectedCount, [scriptblock]$Body) {
    try {
        & $Body
        $results = @(Get-TestResult)
        if ($results.Count -ne $ExpectedCount -or @($results | Where-Object { -not $_.Ok }).Count) { exit 1 }
    } finally {
        Remove-TestDir -Path $script:Fixture
    }
}

Export-ModuleMember -Function Initialize-TestSuite, Get-TestSuiteHub, New-FixtureDirectory, Copy-TestFile, Reset-TestFile, Invoke-TestSuite
