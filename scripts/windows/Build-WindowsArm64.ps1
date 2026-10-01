#requires -Version 7.0
# The arm64 cross lane's build (windows-arm64-cross.yml): the pure wheel, then the arm64 bundle and its packages in dist/windows-arm64.

Param(
	[string]$PythonVersion = "3.14"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$driver = Join-Path $repoRoot 'third_party/ANTfrastructure/windows/scripts/python/Invoke-CiPackaging.ps1'
if (-not (Test-Path $driver)) {
	throw "Missing $driver - run: git submodule update --init --recursive"
}

# Static analysis runs in windows-x64.yml; windows-11-arm starts this arm64 bundle and runs the pytest suite under it.
& pwsh -NoProfile -File $driver -RepoRoot $repoRoot -PythonVersion $PythonVersion -TargetArch arm64
if ($LASTEXITCODE -ne 0) {
	throw "Invoke-CiPackaging.ps1 failed (exit $LASTEXITCODE)"
}

& pwsh -NoProfile -File (Join-Path $PSScriptRoot "Stage-Arm64Tests.ps1") -RepoRoot $repoRoot -PythonVersion $PythonVersion
if ($LASTEXITCODE -ne 0) {
	throw "Stage-Arm64Tests.ps1 failed (exit $LASTEXITCODE)"
}
