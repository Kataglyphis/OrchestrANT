Param(
	[string[]]$PythonVersions = @("3.10", "3.11", "3.12", "3.13", "3.14", "3.14t"),
	[string]$PackageName = "kataglyphispythoninference",
	[string]$ClangVersion = "21.1.8"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

Write-Host "=== Windows build/test pipeline (PowerShell) ==="
Write-Host "Repo root: $repoRoot"

function Invoke-Optional {
	param(
		[scriptblock]$Script,
		[string]$Name
	)

	try {
		& $Script
	} catch {
		Write-Warning "$Name failed, continuing. Details: $($_.Exception.Message)"
	}
}

function New-UvEnvironment {
	param(
		[string]$PythonVersion,
		[string]$EnvName
	)

	$envPath = Join-Path $repoRoot $EnvName
	Write-Host "Creating uv environment: $envPath (Python $PythonVersion)"
	uv venv --python $PythonVersion $envPath
	$env:UV_PROJECT_ENVIRONMENT = $envPath
	return $envPath
}

function Sync-ProjectDependencies {
	param(
		[switch]$NoBuildIsolationPackageWxPython,
		[switch]$UseLocked
	)

	$args = @("-v", "sync", "--dev", "--all-extras")
	if ($UseLocked) {
		$args += "--locked"
	}
	if ($NoBuildIsolationPackageWxPython) {
		$args += @("--no-build-isolation-package", "wxpython")
	}

	Write-Host "Syncing project dependencies: uv $($args -join ' ')"
	uv @args
}

function Ensure-TestResultsDir {
	New-Item -ItemType Directory -Force "docs/test_results" | Out-Null
}

Ensure-TestResultsDir

Write-Host "=== Pytest matrix (Windows) ==="
# $env:CMAKE_GENERATOR_TOOLSET = "v143"
foreach ($version in $PythonVersions) {
	Write-Host "--- Python $version ---"
	$null = New-UvEnvironment -PythonVersion $version -EnvName (".venv-$version")

	$useLocked = Test-Path -Path "uv.lock"
	if ($useLocked) {
		Sync-ProjectDependencies -NoBuildIsolationPackageWxPython -UseLocked
	} else {
		Sync-ProjectDependencies -NoBuildIsolationPackageWxPython
	}

	uv run pytest tests/unit -v `
		--cov=$PackageName `
		--cov-report=term-missing `
		--cov-report=html:docs/test_results/coverage-html-$version `
		--cov-report=xml:docs/test_results/coverage-$version.xml `
		--junitxml=docs/test_results/report-$version.xml `
		--html=docs/test_results/pytest-report-$version.html `
		--self-contained-html `
		--md-report `
		--md-report-verbose=1 `
		--md-report-output docs/test_results/pytest-report-$version.md

	uv run python bench/demo_cprofile.py
	uv run python bench/demo_line_profiler.py
	uv run -m memory_profiler bench/demo_memory_profiling.py
	uv run py-spy record --rate 200 --duration 45 -o profile.svg -- python bench/demo_py_spy.py
	uv run pytest bench/demo_pytest_benchmark.py
}

Write-Host "=== Static analysis (Python 3.13) ==="
$null = New-UvEnvironment -PythonVersion "3.13" -EnvName ".venv-static"
if (Test-Path -Path "uv.lock") {
	Sync-ProjectDependencies -NoBuildIsolationPackageWxPython -UseLocked
} else {
	Sync-ProjectDependencies -NoBuildIsolationPackageWxPython
}

Invoke-Optional -Name "codespell" -Script { uv run codespell }
Invoke-Optional -Name "mypy" -Script { uv run mypy . }
Invoke-Optional -Name "bandit" -Script { uv run bandit -r . }
Invoke-Optional -Name "vulture" -Script { uv run vulture . }
Invoke-Optional -Name "ruff" -Script { uv run ruff check }
Invoke-Optional -Name "ty" -Script { uv run ty check }

Write-Host "=== Packaging (source) ==="
$null = New-UvEnvironment -PythonVersion "3.13" -EnvName ".venv-packaging-sources"
if (Test-Path -Path "uv.lock") {
	Sync-ProjectDependencies -NoBuildIsolationPackageWxPython -UseLocked
} else {
	Sync-ProjectDependencies -NoBuildIsolationPackageWxPython
}
uv build

Write-Host "=== Packaging (Windows binaries) ==="
$env:CYTHONIZE = "True"

$null = New-UvEnvironment -PythonVersion "3.13" -EnvName ".venv-packaging-binaries"
if (Test-Path -Path "uv.lock") {
	uv sync --locked --dev --all-extras
} else {
	uv sync --dev --all-extras
}

uv build

Write-Host "=== Completed Windows build/test pipeline ==="
