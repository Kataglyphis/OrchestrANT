#requires -Version 7.0
# Checks project asset configuration and package wiring with fixture archives, not actual Windows Installer extraction.
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).ProviderPath
$hub = if ($env:ANTFRASTRUCTURE_DIR) { $env:ANTFRASTRUCTURE_DIR } else { Join-Path $repo 'third_party/ANTfrastructure' }
Import-Module (Join-Path $hub 'windows/scripts/tests/TestHarness.psm1') -DisableNameChecking -ErrorAction Stop
$fixture = New-TestDir -Prefix 'package-license-'
$root = Join-Path $fixture 'repo'
$fixtureHub = Join-Path $fixture 'hub'
$packages = Join-Path $fixture 'packages[assets]'
$null = New-Item -ItemType Directory -Force -Path (Join-Path $root 'resources/models'), (Join-Path $root 'scripts'), (Join-Path $root 'packaging/licenses'), (Join-Path $root 'images'), (Join-Path $fixtureHub 'windows/scripts/modules'), (Join-Path $fixtureHub 'linux/scripts/lib'), $packages
Copy-Item -LiteralPath (Join-Path $hub 'linux/scripts/lib/fetch-data-lock.py') -Destination (Join-Path $fixtureHub 'linux/scripts/lib/fetch-data-lock.py')
Copy-Item -LiteralPath (Join-Path $hub 'linux/scripts/verify_package_license.py') -Destination (Join-Path $fixtureHub 'linux/scripts/verify_package_license.py')
foreach ($script in 'third_party_notices.py', 'check_launcher_icon.py') {
    Copy-Item -LiteralPath (Join-Path $repo "scripts/$script") -Destination (Join-Path $root "scripts/$script")
}
Copy-Item -LiteralPath (Join-Path $repo 'images/logo.png') -Destination (Join-Path $root 'images/logo.png')
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
    Assert-PackageLicense -Path $Path -Pattern "*/$FileName" -LicensePath $LicensePath -VerifierPath $VerifierPath
}
Export-ModuleMember -Function Assert-PackageLicense, Assert-MsiPackageLicense
'@ | Set-Content -LiteralPath (Join-Path $fixtureHub 'windows/scripts/modules/WindowsMsiLicense.Common.psm1') -Encoding utf8
$utf8 = [Text.UTF8Encoding]::new($false)
$rows = foreach ($name in 'deim_dfine_n_coco.LICENSE', 'deim_dfine_n_coco.NOTICE') {
    $source = Join-Path $fixture $name
    [IO.File]::WriteAllText($source, "original $name`n", $utf8)
    $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $name  $([Uri]::new($source).AbsoluteUri)"
}
[IO.File]::WriteAllLines((Join-Path $root 'resources/models/models.lock'), [string[]]$rows, $utf8)
$notice = Join-Path $root 'packaging/THIRD-PARTY-NOTICES.txt'
[IO.File]::WriteAllText($notice, "Python distributions`n====`n`nNative components outside site-packages`n====`n- launcher | MIT | windows-x64`n    files: *.exe`n", $utf8)
$supplied = Join-Path $root 'packaging/licenses/example-LICENSE'
[IO.File]::WriteAllText($supplied, "supplied wheel licence`n", $utf8)
$launcher = Join-Path $fixture 'app.exe'
$placeholder = Join-Path $fixture 'placeholder.exe'
$unlisted = Join-Path $fixture 'unlisted.dll'
$pe = Join-Path $repo 'scripts/windows/tests/new_icon_pe.py'
& python $pe (Join-Path $root 'images/logo.png') $launcher
& python $pe $notice $placeholder
& python $pe $notice $unlisted
$baseMembers = [ordered]@{
    'share/orchestrant/models/deim_dfine_n_coco.LICENSE' = Join-Path $fixture 'deim_dfine_n_coco.LICENSE'
    'share/orchestrant/models/deim_dfine_n_coco.NOTICE'  = Join-Path $fixture 'deim_dfine_n_coco.NOTICE'
    'THIRD-PARTY-NOTICES.txt'                            = $notice
    'licenses/example-LICENSE'                           = $supplied
    'yolo-monitor.exe'                                   = $launcher
}

function New-FixtureArchive([string]$Format, [string]$Missing = '', [hashtable]$Replace = @{}) {
    $target = Join-Path $packages "test-windows-x64.$Format"
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    $members = [ordered]@{}
    $prefix = if ($Format -eq 'msix') { '' } else { 'fixture/' }
    foreach ($entry in $baseMembers.GetEnumerator()) {
        if ($entry.Key -eq $Missing) { continue }
        $members["$prefix$($entry.Key)"] = $entry.Value
    }
    foreach ($entry in $Replace.GetEnumerator()) { $members["$prefix$($entry.Key)"] = $entry.Value }
    New-TestZipFile -Path $target -Member $members -FileValues
}
function Invoke-PackageFixture {
    & pwsh -NoProfile -File (Join-Path $repo 'scripts/windows/Test-PackageLicenses.ps1') -PackageDirectory $packages -Arch x64 -RepoRoot $root -HubRoot $fixtureHub 2>&1 | Out-Null
    return $LASTEXITCODE
}
foreach ($format in 'zip', 'msix', 'msi') { New-FixtureArchive -Format $format }
$config = Get-Content -LiteralPath (Join-Path $repo 'packaging/app.json') -Raw | ConvertFrom-Json

try {
    Describe 'Licence, notice and logo checks for Windows package payloads' {
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
        It 'ships the notice and every supplied licence text through app.json' {
            $shipped = @($config.data | ForEach-Object { "$($_.from)=$($_.to)" })
            Assert-True ($shipped -contains 'packaging/THIRD-PARTY-NOTICES.txt=THIRD-PARTY-NOTICES.txt')
            foreach ($text in @(Get-ChildItem -LiteralPath (Join-Path $repo 'packaging/licenses') -File)) {
                Assert-True ($shipped -contains "packaging/licenses/$($text.Name)=licenses/$($text.Name)")
            }
        }
        It 'accepts every text and the logo in all three artifact types' { Assert-Equal 0 (Invoke-PackageFixture) }
        foreach ($format in 'zip', 'msix', 'msi') {
            It "rejects a $format without the model notice" {
                New-FixtureArchive -Format $format -Missing 'share/orchestrant/models/deim_dfine_n_coco.NOTICE'
                Assert-True ((Invoke-PackageFixture) -ne 0)
                New-FixtureArchive -Format $format
            }
            It "rejects a $format without the third-party notice" {
                New-FixtureArchive -Format $format -Missing 'THIRD-PARTY-NOTICES.txt'
                Assert-True ((Invoke-PackageFixture) -ne 0)
                New-FixtureArchive -Format $format
            }
        }
        It 'rejects an MSI without a supplied licence text' {
            New-FixtureArchive -Format msi -Missing 'licenses/example-LICENSE'
            Assert-True ((Invoke-PackageFixture) -ne 0)
            New-FixtureArchive -Format msi
        }
        It 'rejects a launcher whose icon is not the logo' {
            New-FixtureArchive -Format zip -Replace @{ 'yolo-monitor.exe' = $placeholder }
            Assert-True ((Invoke-PackageFixture) -ne 0)
            New-FixtureArchive -Format zip
        }
        It 'rejects a native file the notice does not list' {
            New-FixtureArchive -Format zip -Replace @{ 'runtime/unlisted.dll' = $unlisted }
            Assert-True ((Invoke-PackageFixture) -ne 0)
            New-FixtureArchive -Format zip
        }
        It 'accepts the restored fixture again' { Assert-Equal 0 (Invoke-PackageFixture) }
        It 'rejects duplicate packages' {
            $second = Join-Path $packages 'duplicate'
            $null = New-Item -ItemType Directory -Path $second
            Copy-Item -LiteralPath (Join-Path $packages 'test-windows-x64.msi') -Destination $second
            try { Assert-True ((Invoke-PackageFixture) -ne 0) }
            finally { Remove-Item -LiteralPath $second -Recurse -Force }
        }
        It 'rejects a missing package' {
            Remove-Item -LiteralPath (Join-Path $packages 'test-windows-x64.msi')
            Assert-True ((Invoke-PackageFixture) -ne 0)
        }
    }
    $results = @(Get-TestResult)
    if ($results.Count -ne 16 -or @($results | Where-Object { -not $_.Ok }).Count) { exit 1 }
} finally {
    Remove-TestDir -Path $fixture
}
