#requires -Version 7.0

Param(
	# The Linux lane's matrix; no 3.13 since the image's ONNX Runtime wheels are cp314 only (third_party/ANTfrastructure/docs/python-ci.md, Trap 3).
	[string[]]$PythonVersions = @("3.14", "3.14t"),
	[string]$PackageName = "orchestrant",
	[string]$LogDir = "logs",
	[switch]$StopOnError,  # stop at the first failing step instead of carrying on
	[switch]$EnablePySpy
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

# Resolve through the synced bootstrap, not a hard-coded path, so a module moved upstream needs no edit here.
. (Join-Path $PSScriptRoot 'Resolve-BuildModule.ps1')

# Dependency order: a nested import inside a .psm1 is module-private and never reaches this session.
Import-BuildModule @(
	'WindowsScripts.Shared'
	'WindowsBuild.Common'
	'WindowsUv.Common'
)

# Static analysis and packaging run the hub's drivers as child processes, given -RepoRoot so their paths stay out of the hub.

$script:BuildContext = New-BuildContext -Workspace $repoRoot -LogDir $LogDir -StopOnError:$StopOnError
$script:BuildContext.SuppressConsoleOutput = $false
$logPath = $script:BuildContext.LogPath
$script:CreatedUvEnvs = New-Object System.Collections.Generic.List[string]

# Success/failure tracking

$script:Results = $script:BuildContext.Results

function Close-Log {
	Close-BuildLog -Context $script:BuildContext
}

# Not Write-Log: PSAvoidOverwritingBuiltInCmdlets reports that name as shadowing a built-in cmdlet.
function Write-LogInfo {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]
		[string]$Message
	)

	Write-BuildLog -Context $script:BuildContext -Message $Message
}

function Write-LogWarning {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]
		[string]$Message
	)

	Write-BuildLogWarning -Context $script:BuildContext -Message $Message
}

function Write-LogError {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]
		[string]$Message
	)

	Write-BuildLogError -Context $script:BuildContext -Message $Message
}

function Write-LogSuccess {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]
		[string]$Message
	)

	Write-BuildLogSuccess -Context $script:BuildContext -Message $Message
}

Open-BuildLog -Context $script:BuildContext

Write-LogInfo "=== Windows build/test pipeline (PowerShell) ==="
Write-LogInfo "Repo root: $repoRoot"
Write-LogInfo "Logging all output to: $logPath"
Write-LogInfo "Stop on error: $StopOnError"

# Gates aggregate inside the hub's Invoke-CiStaticAnalysis.ps1, whose own summary JSON names the six tools.

function Invoke-External {
	param(
		[Parameter(Mandatory)]
		[string]$File,
		[Alias('Args')]
		[string[]]$CommandArgs = @()
	)

	Invoke-BuildExternal -Context $script:BuildContext -File $File -Parameters $CommandArgs | Out-Null
}

function Invoke-BenchDemo {
	# Soft on purpose: a demo that cannot run here logs "<label> skipped", as on the Linux lane.
	param(
		[Parameter(Mandatory)]
		[string]$Label,
		[Parameter(Mandatory)]
		[string]$File,
		[Alias('Args')]
		[string[]]$CommandArgs = @()
	)

	$exitCode = Invoke-BuildExternal -Context $script:BuildContext -File $File -Parameters $CommandArgs -IgnoreExitCode
	if ($exitCode -ne 0) {
		Write-LogWarning "$Label skipped (exit $exitCode)"
	}
}

$script:UvCommandRunner = {
	param([string]$File, [string[]]$CommandArgs)
	Invoke-BuildExternal -Context $script:BuildContext -File $File -Parameters $CommandArgs | Out-Null
}

$script:UvLogInfo = {
	param([string]$Message)
	Write-BuildLog -Context $script:BuildContext -Message $Message
}

$script:UvLogWarning = {
	param([string]$Message)
	Write-BuildLogWarning -Context $script:BuildContext -Message $Message
}

function New-UvEnvironment {
	# Binds the repo root, tracker and delegates so the call sites need not repeat them.
	param(
		[string]$PythonVersion,
		[string]$EnvName
	)

	return New-TrackedUvEnvironment -Workspace $repoRoot -PythonVersion $PythonVersion -EnvName $EnvName -Tracker $script:CreatedUvEnvs -CommandRunner $script:UvCommandRunner -LogInfo $script:UvLogInfo -LogWarning $script:UvLogWarning
}

function Remove-UvEnvironment {
	param(
		[string]$EnvPath
	)

	Remove-UvProjectEnvironment -EnvPath $EnvPath -LogInfo $script:UvLogInfo -LogWarning $script:UvLogWarning
}

function Sync-ProjectDependencies {
	param(
		[switch]$NoBuildIsolationPackageWxPython,
		[switch]$UseLocked
	)

	# Opts into -RetryWithoutLocked, which upstream leaves off so --locked can fail on a stale lockfile.
	Sync-UvProjectDependencies `
		-NoBuildIsolationPackageWxPython:$NoBuildIsolationPackageWxPython `
		-UseLocked:$UseLocked `
		-RetryWithoutLocked `
		-CommandRunner $script:UvCommandRunner `
		-LogInfo $script:UvLogInfo `
		-LogWarning $script:UvLogWarning
}

function Initialize-TestResultsDir {
	New-Item -ItemType Directory -Force "docs/test_results" | Out-Null
}

# Runs one step and records success/failure.

function Invoke-Step {
	# Binds -Context so the call sites need not repeat it.
	param(
		[Parameter(Mandatory)]
		[string]$StepName,
		[Parameter(Mandatory)]
		[scriptblock]$Script,
		[switch]$Critical,
		[switch]$AllowFailure
	)

	return Invoke-BuildStep -Context $script:BuildContext -StepName $StepName -Script $Script -Critical:$Critical -AllowFailure:$AllowFailure
}

function Write-Summary {
	Write-BuildSummary -Context $script:BuildContext
}

try {
	try {
		Initialize-TestResultsDir

		Write-LogInfo "=== Pytest matrix (Windows) ==="

		# The fleet's EXPERIMENTAL_PYTHON_VERSIONS decides; never a version range, as allowed failures skip the exit code.
		foreach ($version in $PythonVersions) {
			$allowFailure = Test-ExperimentalPython -Version $version

			Invoke-Step -StepName "Python $version - Tests" -AllowFailure:$allowFailure -Script {
				Write-LogInfo "--- Python $version ---"
				$envPath = New-UvEnvironment -PythonVersion $version -EnvName (".venv-$version")

				try {
					Sync-ProjectDependencies -NoBuildIsolationPackageWxPython

					Invoke-External -File "uv" -Args @(
						"run", "pytest", "tests/unit", "-v",
						"--cov=$PackageName",
						"--cov-report=term-missing",
						"--cov-report=html:docs/test_results/coverage-html-$version",
						"--cov-report=xml:docs/test_results/coverage-$version.xml",
						"--junitxml=docs/test_results/report-$version.xml",
						"--html=docs/test_results/pytest-report-$version.html",
						"--self-contained-html",
						"--md-report",
						"--md-report-verbose=1",
						"--md-report-output",
						"docs/test_results/pytest-report-$version.md"
					)

					# The demos are a profiling showcase, not a gate: soft here as on the Linux lane.
					Invoke-BenchDemo -Label "demo_cprofile.py" -File "uv" -CommandArgs @("run", "python", "bench/demo_cprofile.py")
					Invoke-BenchDemo -Label "demo_line_profiler.py" -File "uv" -CommandArgs @("run", "python", "bench/demo_line_profiler.py")
					if ($EnablePySpy) {
						Invoke-BenchDemo -Label "py-spy profiling" -File "uv" -CommandArgs @("run", "py-spy", "record", "--rate", "200", "--duration", "45", "-o", "profile.svg", "--", "python", "bench/demo_py_spy.py")
					}
					Invoke-BenchDemo -Label "benchmark tests" -File "uv" -CommandArgs @("run", "pytest", "bench/demo_pytest_benchmark.py")
				} finally {
					Remove-UvEnvironment -EnvPath $envPath
				}
			} | Out-Null
		}

		# -ExtraPaths and -BanditExcludes must equal what scripts/linux/ci_static_analysis.sh exports.
		Invoke-Step -StepName "Static Analysis (Python 3.14)" -Script {
			Write-LogInfo "=== Static analysis (Python 3.14) ==="
			$driver = Join-Path $repoRoot 'third_party/ANTfrastructure/windows/scripts/python/Invoke-CiStaticAnalysis.ps1'
			if (-not (Test-Path $driver)) {
				throw "Missing $driver - run: git submodule update --init --recursive"
			}

			# -Command, not -File: `pwsh -File` silently binds only the first element of the -ExtraPaths array.
			$q = { param([string]$v) "'" + $v.Replace("'", "''") + "'" }
			$banditExcludes = 'tests,.venv,.venv_static_analysis,ExternalLib,third_party,archive,docs/test_results,benchmarks/tests'
			$command = "& {0} -RepoRoot {1} -PythonVersion '3.14' -PackageName {2} -ExtraPaths @('benchmarks','frontend','bench','examples') -BanditExcludes {3}" -f `
				(& $q $driver), (& $q $repoRoot), (& $q $PackageName), (& $q $banditExcludes)
			Invoke-External -File "pwsh" -Args @("-NoProfile", "-Command", $command)
		} | Out-Null

		# Invoke-CiPackaging.ps1 runs the wheel steps itself, then, for packaging/app.json, the app bundle with its zip and MSI.
		Invoke-Step -StepName "Packaging (source + Windows binaries)" -Script {
			Write-LogInfo "=== Packaging (source + Windows binaries) ==="
			$driver = Join-Path $repoRoot 'third_party/ANTfrastructure/windows/scripts/python/Invoke-CiPackaging.ps1'
			if (-not (Test-Path $driver)) {
				throw "Missing $driver - run: git submodule update --init --recursive"
			}

			Invoke-External -File "pwsh" -Args @(
				"-NoProfile", "-File", $driver,
				"-RepoRoot", $repoRoot,
				"-PythonVersion", "3.14"
			)
		} | Out-Null

		Write-LogInfo "=== Completed Windows build/test pipeline ==="

	} catch {
		Write-LogError "Unhandled critical error: $($_.Exception.Message)"
		if ($_.ScriptStackTrace) {
			Write-LogError "Stack trace: $($_.ScriptStackTrace)"
		}
		throw
	}
} finally {
	# Removes every tracked venv even when one fails, so no later run inherits a half-deleted one.
	Remove-TrackedUvEnvironment -Tracker $script:CreatedUvEnvs -LogInfo $script:UvLogInfo -LogWarning $script:UvLogWarning

	Write-Summary

	Close-Log

	# Exit code derived from the recorded failures
	if ($script:Results.Failed.Count -gt 0) {
		exit 1
	}
}

