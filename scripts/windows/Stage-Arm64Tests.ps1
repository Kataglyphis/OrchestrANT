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
# bench is staged for bench-pytest.cmd (the demo benchmark x64 also runs); its __init__.py makes it a package.
foreach ($item in "pyproject.toml", "tests", "benchmarks", "frontend", "bench") {
	Copy-Item -LiteralPath (Join-Path $RepoRoot $item) -Destination $out -Recurse
}
Copy-Item -LiteralPath (Join-Path $RepoRoot "third_party/ANTfrastructure/linux/llm-stack/backends.json") -Destination $out
# test_benchmark_data.py loads these two by file path; with no __init__.py beside them they cannot shadow the installed package.
$bench = Join-Path $out "orchestrant/benchmark"
$null = New-Item -ItemType Directory -Force -Path $bench
foreach ($module in "speed_summary.py", "answers.py") {
	Copy-Item -LiteralPath (Join-Path $RepoRoot "orchestrant/benchmark/$module") -Destination $bench
}

# What the suite needs beyond the bundle, as arm64 wheels. The set matches x64's pytest
# invocation in Build-Windows.ps1: coverage (a cp314 win_arm64 wheel exists), the benchmark
# plugin and the three report plugins. Everything here is a wheel: the test tree stays
# binary-free-arch-clean at the floor-0 gate.
# The resolver's interpreter decides the wheel ABI tag, and a bare `3.14` can pick a
# free-threaded build, which produces cp314t .pyd wheels that refuse to load in the bundle's
# plain-3.14 python.exe (measured on this host). `+gil` is the hub's uv_python_request rule.
if ($PythonVersion -match '^[0-9.]+$') { $pyRequest = "$PythonVersion+gil" } else { $pyRequest = $PythonVersion }
$site = Join-Path $out "site"
& uv pip install --target $site --python "$pyRequest" --python-platform aarch64-pc-windows-msvc --python-version $PythonVersion --only-binary ":all:" pytest requests pytest-cov pytest-benchmark pytest-md pytest-md-report pytest-html
if ($LASTEXITCODE -ne 0) {
	throw "uv pip install of the test dependencies failed (exit $LASTEXITCODE)"
}
# uv's script trampolines are host-arch executables, which the lane's arch gate refuses.
$scripts = Join-Path $site "bin"
if (Test-Path -LiteralPath $scripts) {
	Remove-Item -LiteralPath $scripts -Recurse -Force
}

Copy-Item -LiteralPath (Join-Path $RepoRoot "third_party/ANTfrastructure/windows/scripts/build/Invoke-StagedTests.ps1") -Destination $out
$null = New-Item -ItemType Directory -Force -Path (Join-Path $out "results")
# The same reports x64's Build-Windows.ps1 produces, written into results/ beside the run.
# addopts' -q would drop the "=====" summary frame Invoke-StagedTests reads the counts from;
# pytest-cov's term-missing, junit, html and md-report mirror x64's invocation (also
# --cov-report=html, whose file is 4 MB self-contained - dropped until a device upload exists:
# the -tests artifact is uploaded from the build job, BEFORE the device generates these).
@"
@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0site"
set "LLM_BACKENDS=%~dp0backends.json"
"%~dp0..\product\bundle\runtime\python.exe" -m pytest -p no:cacheprovider -o "addopts=-ra" ^
  --cov=orchestrant --cov-report=term-missing ^
  --cov-report=xml:%~dp0results\coverage-arm64.xml ^
  --junitxml=%~dp0results\report-arm64.xml ^
  --html=%~dp0results\pytest-report-arm64.html --self-contained-html ^
  --md-report --md-report-output=%~dp0results\pytest-report-arm64.md
"@ | Set-Content -LiteralPath (Join-Path $out "run-pytest.cmd") -Encoding ascii
@"
@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0site"
set "LLM_BACKENDS=%~dp0backends.json"
"%~dp0..\product\bundle\runtime\python.exe" -m pytest bench/demo_pytest_benchmark.py -p no:cacheprovider -o "addopts=-ra"
"@ | Set-Content -LiteralPath (Join-Path $out "bench-pytest.cmd") -Encoding ascii
# Same bench file x64's Build-Windows.ps1 runs after the suite; counted from its own pytest summary.
ConvertTo-Json -InputObject @(
	@{ exe = "run-pytest.cmd"; args = @(); kind = "pytest" },
	@{ exe = "bench-pytest.cmd"; args = @(); kind = "pytest" }
) |	Set-Content -LiteralPath (Join-Path $out "tests.json") -Encoding utf8
Write-Host "Staged the pytest suite for windows-11-arm in $out"
