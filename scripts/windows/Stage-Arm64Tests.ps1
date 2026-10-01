#requires -Version 7.0
# Stages the pytest suite for windows-11-arm, where the arm64 bundle's own python.exe runs it against the shipped package.

Param(
	[string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
	[string]$OutDir = "dist/windows-arm64-tests",
	[string]$PythonVersion = "3.14"
)

$ErrorActionPreference = "Stop"

$out = Join-Path $RepoRoot $OutDir
if (Test-Path -LiteralPath $out) {
	Remove-Item -LiteralPath $out -Recurse -Force
}
$null = New-Item -ItemType Directory -Force -Path $out

# No orchestrant sources: pyproject's pythonpath "." would shadow the bundle's installed package with them.
foreach ($item in "pyproject.toml", "tests", "benchmarks", "frontend") {
	Copy-Item -LiteralPath (Join-Path $RepoRoot $item) -Destination $out -Recurse
}
Copy-Item -LiteralPath (Join-Path $RepoRoot "third_party/ANTfrastructure/linux/llm-stack/backends.json") -Destination $out

# What the suite needs beyond the bundle, as arm64 wheels.
$site = Join-Path $out "site"
& uv pip install --target $site --python-platform aarch64-pc-windows-msvc --python-version $PythonVersion --only-binary ":all:" pytest requests
if ($LASTEXITCODE -ne 0) {
	throw "uv pip install of the test dependencies failed (exit $LASTEXITCODE)"
}
# uv's script trampolines are host-arch executables, which the lane's arch gate refuses.
$scripts = Join-Path $site "bin"
if (Test-Path -LiteralPath $scripts) {
	Remove-Item -LiteralPath $scripts -Recurse -Force
}

Copy-Item -LiteralPath (Join-Path $RepoRoot "third_party/ANTfrastructure/windows/scripts/build/Invoke-StagedTests.ps1") -Destination $out
# addopts' -q would drop the "=====" summary frame Invoke-StagedTests reads the counts from.
@"
@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0site"
set "LLM_BACKENDS=%~dp0backends.json"
"%~dp0..\product\bundle\runtime\python.exe" -m pytest -p no:cacheprovider -o "addopts=-ra"
"@ | Set-Content -LiteralPath (Join-Path $out "run-pytest.cmd") -Encoding ascii
ConvertTo-Json -InputObject @(@{ exe = "run-pytest.cmd"; args = @(); kind = "pytest" }) |
	Set-Content -LiteralPath (Join-Path $out "tests.json") -Encoding utf8
Write-Host "Staged the pytest suite for windows-11-arm in $out"
