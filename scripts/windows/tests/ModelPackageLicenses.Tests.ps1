#requires -Version 7.0
# Checks project asset configuration and package wiring, not actual Windows Installer extraction.
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).ProviderPath
$hub = if ($env:ANTFRASTRUCTURE_DIR) { $env:ANTFRASTRUCTURE_DIR } else { Join-Path $repo 'third_party/ANTfrastructure' }
Import-Module (Join-Path $hub 'windows/scripts/tests/TestHarness.psm1') -DisableNameChecking -ErrorAction Stop
$fixture = New-TestDir -Prefix 'model-package-license-'
$root = Join-Path $fixture 'repo'
$fixtureHub = Join-Path $fixture 'hub'
$packages = Join-Path $fixture 'packages[assets]'
$null = New-Item -ItemType Directory -Force -Path (Join-Path $root 'resources/models'), (Join-Path $fixtureHub 'windows/scripts/modules'), (Join-Path $fixtureHub 'linux/scripts/lib'), $packages
Copy-Item -LiteralPath (Join-Path $hub 'linux/scripts/lib/fetch-data-lock.py') -Destination (Join-Path $fixtureHub 'linux/scripts/lib/fetch-data-lock.py')
Copy-Item -LiteralPath (Join-Path $hub 'linux/scripts/verify_package_license.py') -Destination (Join-Path $fixtureHub 'linux/scripts/verify_package_license.py')
$modules = @('WindowsScripts.Shared.psm1', 'WindowsSourceBuild.Common.psm1', 'WindowsTargetArch.Common.psm1') |
    ForEach-Object { Join-Path $hub "windows/scripts/modules/$_" }
Copy-Item -LiteralPath $modules -Destination (Join-Path $fixtureHub 'windows/scripts/modules')
@'
function Assert-PackageLicense {
    param($Path, $Pattern, $LicensePath, $VerifierPath)
    & python $VerifierPath $Path --require "$Pattern=$LicensePath"
    if ($LASTEXITCODE -ne 0) { throw 'fixture package check failed' }
}
function Assert-MsiPackageLicense {
    param($Path, $FileName, $LicensePath, $VerifierPath)
    Assert-PackageLicense -Path $Path -Pattern "*/share/orchestrant/models/$FileName" -LicensePath $LicensePath -VerifierPath $VerifierPath
}
Export-ModuleMember -Function Assert-PackageLicense, Assert-MsiPackageLicense
'@ | Set-Content -LiteralPath (Join-Path $fixtureHub 'windows/scripts/modules/WindowsMsiLicense.Common.psm1') -Encoding utf8
$names = @('deim_dfine_n_coco.LICENSE', 'deim_dfine_n_coco.NOTICE')
$rows = foreach ($name in $names) {
    $source = Join-Path $fixture $name
    [IO.File]::WriteAllText($source, "original $name`n", [Text.UTF8Encoding]::new($false))
    $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $name  $([Uri]::new($source).AbsoluteUri)"
}
[IO.File]::WriteAllLines((Join-Path $root 'resources/models/models.lock'), [string[]]$rows, [Text.UTF8Encoding]::new($false))

function New-ModelFixtureArchive([string]$Format, [string]$Missing = '') {
    $target = Join-Path $packages "test-windows-x64.$Format"
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    $members = [ordered]@{}
    $prefix = if ($Format -eq 'msix') { '' } else { 'fixture/' }
    foreach ($name in $names) {
        if ($name -eq $Missing) { continue }
        $members["${prefix}share/orchestrant/models/$name"] = Join-Path $fixture $name
    }
    New-TestZipFile -Path $target -Member $members -FileValues
}
function Invoke-ModelFixture {
    & pwsh -NoProfile -File (Join-Path $repo 'scripts/windows/Test-ModelPackageLicenses.ps1') -PackageDirectory $packages -Arch x64 -RepoRoot $root -HubRoot $fixtureHub 2>&1 | Out-Null
    return $LASTEXITCODE
}
foreach ($format in 'zip', 'msix', 'msi') { New-ModelFixtureArchive -Format $format }
$config = Get-Content -LiteralPath (Join-Path $repo 'packaging/app.json') -Raw | ConvertFrom-Json

try {
    Describe 'Model licence checks for Windows package payloads' {
        It 'uses the project PNG as the configured launcher icon' {
            Assert-Equal 'images/logo.png' $config.icon
            $png = [IO.File]::ReadAllBytes((Join-Path $repo $config.icon))
            Assert-True ($png.Length -gt 8)
            Assert-Equal '89504E470D0A1A0A' ([Convert]::ToHexString([byte[]]$png[0..7]))
        }
        It 'uses the project license as the configured package license' {
            Assert-Equal 'LICENSE' $config.license_file
            Assert-True ((Get-Item -LiteralPath (Join-Path $repo $config.license_file)).Length -gt 0)
        }
        It 'accepts both original locked texts in all three artifact types' { Assert-Equal 0 (Invoke-ModelFixture) }
        foreach ($format in 'zip', 'msix', 'msi') {
            It "rejects a missing $format notice" {
                New-ModelFixtureArchive -Format $format -Missing 'deim_dfine_n_coco.NOTICE'
                Assert-True ((Invoke-ModelFixture) -ne 0)
                New-ModelFixtureArchive -Format $format
            }
        }
        It 'rejects duplicate packages' {
            $second = Join-Path $packages 'duplicate'
            $null = New-Item -ItemType Directory -Path $second
            Copy-Item -LiteralPath (Join-Path $packages 'test-windows-x64.msi') -Destination $second
            try { Assert-True ((Invoke-ModelFixture) -ne 0) }
            finally { Remove-Item -LiteralPath $second -Recurse -Force }
        }
        It 'rejects a missing package' {
            Remove-Item -LiteralPath (Join-Path $packages 'test-windows-x64.msi')
            Assert-True ((Invoke-ModelFixture) -ne 0)
        }
    }
    $results = @(Get-TestResult)
    if ($results.Count -ne 8 -or @($results | Where-Object { -not $_.Ok }).Count) { exit 1 }
} finally {
    Remove-TestDir -Path $fixture
}
