#requires -Version 7.0
# The arm64 cross lane's build (windows-arm64-cross.yml): the pure wheel, then the arm64 bundle and its packages in dist/windows-arm64.

Param(
	# Empty: the image's pinned X.Y, which both scripts below resolve themselves.
	[string]$PythonVersion = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$driver = Join-Path $repoRoot 'third_party/ANTfrastructure/windows/scripts/python/Invoke-CiPackaging.ps1'
if (-not (Test-Path $driver)) {
	throw "Missing $driver - run: git submodule update --init --recursive"
}

# The packages ship the model, which no checkout tracks: fetched by checksum before the driver copies app.json's data.
Import-Module (Join-Path $repoRoot 'third_party/ANTfrastructure/windows/scripts/modules/WindowsPythonWheel.Common.psm1')
$fetchVersion = if ($PythonVersion) { $PythonVersion } else { (Get-PinnedPythonVersion).MajorMinor }
& (Find-UvPython -Request $fetchVersion) -I (Join-Path $repoRoot 'scripts/fetch_models.py')
if ($LASTEXITCODE -ne 0) {
	throw "fetch_models.py failed (exit $LASTEXITCODE)"
}

# Static analysis runs in windows-x64.yml; windows-11-arm starts this arm64 bundle and runs the pytest suite under it.
$versionArgs = @(if ($PythonVersion) { '-PythonVersion', $PythonVersion })
& pwsh -NoProfile -File $driver -RepoRoot $repoRoot -TargetArch arm64 @versionArgs
if ($LASTEXITCODE -ne 0) {
	throw "Invoke-CiPackaging.ps1 failed (exit $LASTEXITCODE)"
}

& pwsh -NoProfile -File (Join-Path $PSScriptRoot "Stage-Arm64Tests.ps1") -RepoRoot $repoRoot @versionArgs
if ($LASTEXITCODE -ne 0) {
	throw "Stage-Arm64Tests.ps1 failed (exit $LASTEXITCODE)"
}
