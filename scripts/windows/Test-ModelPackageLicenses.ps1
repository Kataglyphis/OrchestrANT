#requires -Version 7.0
# Checks the pinned model texts in Windows packages; it does not inventory other dependencies.
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
function Get-ModelPackageArtifacts([string]$Directory, [string]$TargetArch) {
    $root = [Management.Automation.WildcardPattern]::Escape((Resolve-Path -LiteralPath $Directory).ProviderPath)
    $artifacts = @{}
    foreach ($format in 'zip', 'msi', 'msix') {
        $file = Get-UniqueBuildArtifact -Root $root -Filter "*-windows-$TargetArch.$format" -Description "$TargetArch $format package"
        $artifacts[$format] = $file.FullName
    }
    return $artifacts
}
$work = New-TemporaryDirectory -Prefix 'orchestrant-model-license-'
try {
    & python -I $fetcher --lock (Join-Path $RepoRoot 'resources/models/models.lock') --dest $work `
        --only deim_dfine_n_coco.LICENSE --only deim_dfine_n_coco.NOTICE
    if ($LASTEXITCODE -ne 0) { throw "Fetching locked model texts failed (exit $LASTEXITCODE)" }
    $artifacts = Get-ModelPackageArtifacts -Directory $PackageDirectory -TargetArch $Arch
    foreach ($name in 'deim_dfine_n_coco.LICENSE', 'deim_dfine_n_coco.NOTICE') {
        $source = Join-Path $work $name
        Assert-PackageLicense -Path $artifacts.zip -Pattern "*/share/orchestrant/models/$name" -LicensePath $source -VerifierPath $verifier
        Assert-PackageLicense -Path $artifacts.msix -Pattern "share/orchestrant/models/$name" -LicensePath $source -VerifierPath $verifier
        Assert-MsiPackageLicense -Path $artifacts.msi -FileName $name -LicensePath $source -VerifierPath $verifier
    }
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Stop
}
