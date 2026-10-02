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

# No orchestrant sources (pyproject's pythonpath "." would shadow the installed package); bench is staged for bench-pytest.cmd.
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

# x64's pytest set as arm64 wheels (the tree stays arch-clean at the floor-0 gate); +gil because a bare 3.14 can pick a free-threaded build, whose cp314t .pyd wheels the plain bundle runtime cannot load.
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
# x64's reports, into results/ for the hub's test-results-path upload (the -tests artifact predates the device run); --cov-report=html dropped, its file is 4 MB self-contained.
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
# addopts' -q would drop the "=====" summary frame Invoke-StagedTests reads the counts from.
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
