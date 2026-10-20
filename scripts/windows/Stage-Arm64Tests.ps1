#requires -Version 7.0
# Stages the pytest suite for windows-11-arm, where the arm64 bundle's own python.exe runs it against the shipped package.

Param(
	[string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
	[string]$OutDir = "dist/windows-arm64-tests",
	# Empty: the bundle interpreter's X.Y, the image's PYTHON_VERSION pin.
	[string]$PythonVersion = ""
)

$ErrorActionPreference = "Stop"
foreach ($module in 'WindowsPythonWheel.Common', 'WindowsPythonApp.Common') {
	Import-Module (Join-Path $RepoRoot "third_party/ANTfrastructure/windows/scripts/modules/$module.psm1") -DisableNameChecking
}
if (-not $PythonVersion) {
	$PythonVersion = (Get-PinnedPythonVersion).MajorMinor
}

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
# test_viewer_job.py checks the viewer job's flet pin against uv.lock, both read from the repo root.
$workflows = Join-Path $out ".github/workflows"
$null = New-Item -ItemType Directory -Force -Path $workflows
Copy-Item -LiteralPath (Join-Path $RepoRoot ".github/workflows/benchmarks.yml") -Destination $workflows
Copy-Item -LiteralPath (Join-Path $RepoRoot "uv.lock") -Destination $out
# test_benchmark_data.py loads these two by file path; with no __init__.py beside them they cannot shadow the installed package.
$bench = Join-Path $out "orchestrant/benchmark"
$null = New-Item -ItemType Directory -Force -Path $bench
foreach ($module in "speed_summary.py", "answers.py") {
	Copy-Item -LiteralPath (Join-Path $RepoRoot "orchestrant/benchmark/$module") -Destination $bench
}

# x64's pytest set as arm64 wheels (the tree stays arch-clean at the floor-0 gate); +gil because a bare X.Y can pick a free-threaded build, whose cpXYt .pyd wheels the plain bundle runtime cannot load.
if ($PythonVersion -match '^[0-9.]+$') { $pyRequest = "$PythonVersion+gil" } else { $pyRequest = $PythonVersion }
$site = Join-Path $out "site"
# The image's wheel store carries this set SHA-pinned (hub CON67, :winarm64 since 2026-10-10); PyPI is never asked.
if (-not $env:PYTHON_WHEELS -or @(Get-ChildItem -LiteralPath $env:PYTHON_WHEELS -Filter 'pytest-*-py3-none-any.whl' -File -ErrorAction SilentlyContinue).Count -eq 0) {
	throw "no pytest stack in the image's wheel store '$env:PYTHON_WHEELS': pull a :winarm64 published on or after 2026-10-10 (hub CON67)"
}
Write-Host "Test dependencies: offline from the image's wheel store $env:PYTHON_WHEELS"
& uv pip install --target $site --python "$pyRequest" --python-platform aarch64-pc-windows-msvc --python-version $PythonVersion --only-binary ":all:" --no-index --find-links $env:PYTHON_WHEELS pytest requests pytest-cov pytest-benchmark pytest-md pytest-md-report pytest-html
if ($LASTEXITCODE -ne 0) {
	throw "uv pip install of the test dependencies failed (exit $LASTEXITCODE)"
}
# uv's script trampolines are host-arch executables, which the lane's arch gate refuses.
$scripts = Join-Path $site "bin"
if (Test-Path -LiteralPath $scripts) {
	Remove-Item -LiteralPath $scripts -Recurse -Force
}
# The free-threaded leg: the cross lane's cp3XYt wheel and the lock's core and test set, as x64's free-threaded leg syncs them.
$ftWheel = @(Get-ChildItem -LiteralPath (Join-Path $RepoRoot "dist/windows-arm64/free-threaded") -Filter "*-cp*t-win_arm64.whl" -File)
if ($ftWheel.Count -ne 1) {
	throw "expected one cp3XYt win_arm64 orchestrant wheel in dist/windows-arm64/free-threaded, found $($ftWheel.Count)"
}
$ftSite = Join-Path $out "site-ft"
$ftWork = [IO.Directory]::CreateTempSubdirectory('orchestrant-ft-').FullName
# line-profiler declares no free-threading and py-spy is a Rust binary from an sdist: neither has a cp3XYt win_arm64 wheel, and no test imports them.
Install-PythonCrossSite -HostPython (Find-UvPython -Request "${PythonVersion}t") -SitePackages (Join-Path $ftWork "site") -RepoRoot $RepoRoot -Wheel $ftWheel[0].FullName `
	-Extras test -Exclude line-profiler, py-spy -WorkDir $ftWork -Platform aarch64-pc-windows-msvc -PythonVersion $PythonVersion `
	-Store (Join-Path $env:PYTHON_WHEELS_FREE_THREADED "deps") -StoreTag "cp$($PythonVersion -replace '\.', '')t-win_arm64"
# The wheel ships no .py, but tool_fingerprint and the source-text tests read each module's source; the .pyd still outranks it on import.
$ftPackage = Join-Path $ftWork "site/orchestrant"
$compiled = @(Get-ChildItem -LiteralPath $ftPackage -Filter "*.pyd" -Recurse -File)
foreach ($pyd in $compiled) {
	$source = [IO.Path]::Combine($RepoRoot, "orchestrant", [IO.Path]::GetRelativePath($ftPackage, $pyd.DirectoryName), $pyd.Name.Split('.')[0] + ".py")
	if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
		throw "the compiled $($pyd.FullName) has no source at $source"
	}
	Copy-Item -LiteralPath $source -Destination $pyd.DirectoryName
}
Write-Host "site-ft: $($compiled.Count) compiled orchestrant module(s), each with its source beside it"
# Installed container-local, then copied: on the workspace's bind mount uv cannot rename its temporary files into place.
& robocopy.exe (Join-Path $ftWork "site") $ftSite /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
	throw "copying the free-threaded site to $ftSite failed (robocopy exit $LASTEXITCODE)"
}
Remove-Item -LiteralPath $ftWork -Recurse -Force -ErrorAction SilentlyContinue

# setuptools's wheel ships I386/AMD64 launcher stubs (cli*.exe, gui*.exe) as package data; nothing in a test run invokes them, and the floor-0 gate refuses any non-arm64 PE.
foreach ($setuptools in (Join-Path $site "setuptools"), (Join-Path $ftSite "setuptools")) {
	if (Test-Path -LiteralPath $setuptools) {
		Get-ChildItem -LiteralPath $setuptools -Filter '*.exe' -Recurse -File | Remove-Item -Force
	}
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
# The same suite on the kit's free-threaded interpreter and site; ORCHESTRANT_REQUIRE_COMPILED fails a module imported from its .py, not its .pyd.
@"
@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0site-ft"
set "LLM_BACKENDS=%~dp0backends.json"
set "ORCHESTRANT_REQUIRE_COMPILED=1"
"%~dp0..\product\free-threaded\python\python${PythonVersion}t.exe" -m pytest -p no:cacheprovider -o "addopts=-ra" ^
  --junitxml=%~dp0results\report-arm64-ft.xml ^
  --md-report --md-report-output=%~dp0results\pytest-report-arm64-ft.md
"@ | Set-Content -LiteralPath (Join-Path $out "run-pytest-ft.cmd") -Encoding ascii
# addopts' -q would drop the "=====" summary frame Invoke-StagedTests reads the counts from.
@"
@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0site"
set "LLM_BACKENDS=%~dp0backends.json"
"%~dp0..\product\bundle\runtime\python.exe" -m pytest bench/demo_pytest_benchmark.py -p no:cacheprovider -o "addopts=-ra"
"@ | Set-Content -LiteralPath (Join-Path $out "bench-pytest.cmd") -Encoding ascii
# Same bench file the hub's x64 driver runs after the suite; counted from its own pytest summary.
ConvertTo-Json -InputObject @(
	@{ exe = "run-pytest.cmd"; args = @(); kind = "pytest" },
	@{ exe = "run-pytest-ft.cmd"; args = @(); kind = "pytest" },
	@{ exe = "bench-pytest.cmd"; args = @(); kind = "pytest" }
) |	Set-Content -LiteralPath (Join-Path $out "tests.json") -Encoding utf8
Write-Host "Staged the pytest suite for windows-11-arm in $out"
