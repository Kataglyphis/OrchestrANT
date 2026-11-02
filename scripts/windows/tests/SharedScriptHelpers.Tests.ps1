#requires -Version 7.0
using module ./WindowsSuite.Fixture.psm1
# Exercises the script prologue and suite fixture directly; the DirectML suites prove their wiring.
$repo, $fixture = Initialize-TestSuite -FixturePrefix 'shared-script-helpers-'
$prologue = Join-Path $repo 'scripts/windows/Initialize-HubScript.ps1'
$suiteModule = Join-Path $repo 'scripts/windows/tests/WindowsSuite.Fixture.psm1'
. $prologue
function Invoke-Child([string]$Name, [string]$Body) {
    $path = Join-Path $fixture "$Name.ps1"
    [IO.File]::WriteAllText($path, $Body)
    & pwsh -NoProfile -File $path *>&1 | Out-Null
    return $LASTEXITCODE
}
function Get-SuiteChild([string]$Body) {
    return "using module '$suiteModule'`n`$null, `$childFixture = Initialize-TestSuite -FixturePrefix 'child-'`n[IO.File]::WriteAllText('$fixture/left-behind.txt', `$childFixture)`nInvoke-TestSuite -ExpectedCount 1 -Body { $Body }`n"
}
# This suite keeps its own verdict: a broken Invoke-TestSuite must not grade its own test.
try {
    Describe 'Script prologue' {
        It 'applies strict mode in the dot-sourcing script' {
            Assert-Equal 1 (Invoke-Child 'strict' "`$ErrorActionPreference = 'Stop'`n. '$prologue'`n`$missing.Name`n")
        }
        It 'stops on a hub module that is not there' {
            Assert-Equal 1 (Invoke-Child 'module' "`$ErrorActionPreference = 'Stop'`n. '$prologue' -HubRoot '$fixture' -HubModule 'WindowsAbsent.Common'`n")
        }
        It 'binds the checkout and honours a hub override' {
            $status = Invoke-Child 'roots' "`$ErrorActionPreference = 'Stop'`n. '$prologue' -HubRoot '$fixture'`nif (`$repo -cne '$repo' -or `$hub -cne '$fixture') { exit 3 }`n"
            Assert-Equal 0 $status
        }
        It 'returns the one match and names the count otherwise' {
            $directory = New-FixtureDirectory -RelativePath 'single[files]'
            Assert-Throws { Get-SingleFile -Directory $directory -Filter '*.zip' -Description 'test ZIP' } -MessagePattern 'Expected one test ZIP, found 0'
            Write-TestFileTree -Root $directory -Files @{ 'a.zip' = 'a' }
            Assert-Equal 'a.zip' (Get-SingleFile -Directory $directory -Filter '*.zip' -Description 'test ZIP').Name
            Write-TestFileTree -Root $directory -Files @{ 'b.zip' = 'b' }
            Assert-Throws { Get-SingleFile -Directory $directory -Filter '*.zip' -Description 'test ZIP' } -MessagePattern 'found 2'
        }
        It 'publishes a filled directory and consumes its pending name' {
            $destination = Join-Path $fixture 'published[output]'
            Publish-PendingDirectory -Destination $destination -RefusalMessage 'refused' -Fill { param($pending) Write-TestFileTree -Root $pending -Files @{ 'file.txt' = 'x' } }
            Assert-Equal 'x' (Get-Content -LiteralPath "$destination/file.txt" -Raw)
            Assert-False (Test-Path -LiteralPath "$destination-pending")
        }
        It 'refuses an existing destination or pending directory before filling' {
            $destination = New-FixtureDirectory -RelativePath 'taken'
            Assert-Throws { Publish-PendingDirectory -Destination $destination -RefusalMessage 'refused' -Fill { throw 'filled' } } -MessagePattern 'refused'
            $other = Join-Path $fixture 'free'
            $null = New-FixtureDirectory -RelativePath 'free-pending'
            Assert-Throws { Publish-PendingDirectory -Destination $other -RefusalMessage 'refused' -Fill { throw 'filled' } } -MessagePattern 'refused'
            Assert-False (Test-Path -LiteralPath $other)
        }
        It 'publishes nothing when the fill fails' {
            $destination = Join-Path $fixture 'failed'
            Assert-Throws { Publish-PendingDirectory -Destination $destination -RefusalMessage 'refused' -Fill { throw 'fill failed' } } -MessagePattern 'fill failed'
            Assert-False (Test-Path -LiteralPath $destination)
        }
    }
    Describe 'Suite fixture' {
        It 'copies nested files and fails on a missing source' {
            $from = New-FixtureDirectory -RelativePath 'from'
            Write-TestFileTree -Root $from -Files @{ 'a/b/file.txt' = 'x' }
            $to = Join-Path $fixture 'to[copy]'
            Copy-TestFile -From $from -To $to -RelativePath 'a/b/file.txt'
            Assert-Equal 'x' ([IO.File]::ReadAllText((Join-Path $to 'a/b/file.txt')))
            Assert-Throws { Copy-TestFile -From $from -To $to -RelativePath 'absent.txt' }
        }
        It 'resets a present or absent file' {
            Write-TestFileTree -Root $fixture -Files @{ 'reset[file].txt' = 'x' }
            $file = "$fixture/reset[file].txt"
            Reset-TestFile -Path $file
            Assert-False (Test-Path -LiteralPath $file)
            Reset-TestFile -Path $file
        }
        It 'passes only on the expected passing count and removes its fixture' {
            $pass = "Describe 'x' { It 'y' { Assert-True `$true } }"
            Assert-Equal 0 (Invoke-Child 'pass' (Get-SuiteChild $pass))
            Assert-False (Test-Path -LiteralPath ([IO.File]::ReadAllText("$fixture/left-behind.txt")))
            Assert-Equal 1 (Invoke-Child 'count' (Get-SuiteChild "$pass; $pass"))
            Assert-False (Test-Path -LiteralPath ([IO.File]::ReadAllText("$fixture/left-behind.txt")))
        }
        It 'fails on a failed test and removes its fixture' {
            Assert-Equal 1 (Invoke-Child 'fail' (Get-SuiteChild "Describe 'x' { It 'y' { Assert-True `$false } }"))
            Assert-False (Test-Path -LiteralPath ([IO.File]::ReadAllText("$fixture/left-behind.txt")))
        }
    }
    if (@(Get-TestResult | Where-Object { $_.Ok }).Count -ne 11) { exit 1 }
} finally {
    Remove-TestDir -Path $fixture
}
