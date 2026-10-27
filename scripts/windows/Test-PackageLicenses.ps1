#requires -Version 7.0
# Checks Windows packages for the model texts, the third-party notice and its supplied texts, and the launchers' logo.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageDirectory,
    [Parameter(Mandatory)][ValidateSet('x64', 'arm64')][string]$Arch,
    [string]$RepoRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
    [string]$HubRoot = (Join-Path $RepoRoot 'third_party/ANTfrastructure')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$module = Join-Path $HubRoot 'windows/scripts/modules/WindowsMsiLicense.Common.psm1'
$fetcher = Join-Path $HubRoot 'linux/scripts/lib/fetch-data-lock.py'
$verifier = Join-Path $HubRoot 'linux/scripts/verify_package_license.py'
Import-Module (Join-Path $HubRoot 'windows/scripts/modules/WindowsScripts.Shared.psm1') -DisableNameChecking -ErrorAction Stop
Import-Module (Join-Path $HubRoot 'windows/scripts/modules/WindowsSourceBuild.Common.psm1') -DisableNameChecking -ErrorAction Stop
Import-Module $module -DisableNameChecking -ErrorAction Stop
function Get-PackageArtifacts([string]$Directory, [string]$TargetArch) {
    $root = [Management.Automation.WildcardPattern]::Escape((Resolve-Path -LiteralPath $Directory).ProviderPath)
    $artifacts = @{}
    foreach ($format in 'zip', 'msi', 'msix') {
        $file = Get-UniqueBuildArtifact -Root $root -Filter "*-windows-$TargetArch.$format" -Description "$TargetArch $format package"
        $artifacts[$format] = $file.FullName
    }
    return $artifacts
}
function Invoke-RepoPython([string]$Script, [string[]]$Arguments) {
    & python -I (Join-Path $RepoRoot $Script) @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Script failed (exit $LASTEXITCODE)" }
}
$work = New-TemporaryDirectory -Prefix 'orchestrant-package-license-'
try {
    & python -I $fetcher --lock (Join-Path $RepoRoot 'resources/models/models.lock') --dest $work `
        --only deim_dfine_n_coco.LICENSE --only deim_dfine_n_coco.NOTICE
    if ($LASTEXITCODE -ne 0) { throw "Fetching locked model texts failed (exit $LASTEXITCODE)" }
    $artifacts = Get-PackageArtifacts -Directory $PackageDirectory -TargetArch $Arch
    # Bundle-relative path -> source of every text the packages must carry byte for byte.
    $texts = [ordered]@{}
    foreach ($name in 'deim_dfine_n_coco.LICENSE', 'deim_dfine_n_coco.NOTICE') {
        $texts["share/orchestrant/models/$name"] = Join-Path $work $name
    }
    $texts['THIRD-PARTY-NOTICES.txt'] = Join-Path $RepoRoot 'packaging/THIRD-PARTY-NOTICES.txt'
    foreach ($supplied in @(Get-ChildItem -LiteralPath (Join-Path $RepoRoot 'packaging/licenses') -File)) {
        $texts["licenses/$($supplied.Name)"] = $supplied.FullName
    }
    foreach ($entry in $texts.GetEnumerator()) {
        Assert-PackageLicense -Path $artifacts.zip -Pattern "*/$($entry.Key)" -LicensePath $entry.Value -VerifierPath $verifier
        Assert-PackageLicense -Path $artifacts.msix -Pattern $entry.Key -LicensePath $entry.Value -VerifierPath $verifier
        Assert-MsiPackageLicense -Path $artifacts.msi -FileName (Split-Path $entry.Key -Leaf) -LicensePath $entry.Value -VerifierPath $verifier
    }
    Invoke-RepoPython -Script 'scripts/third_party_notices.py' -Arguments @('check', $artifacts.zip)
    Invoke-RepoPython -Script 'scripts/check_launcher_icon.py' -Arguments @($artifacts.zip, (Join-Path $RepoRoot 'images/logo.png'))
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Stop
}
