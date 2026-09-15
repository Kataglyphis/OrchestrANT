#requires -Version 7.0

Param(
	# Same matrix as the Linux lane (test-python-versions in
	# .github/workflows/ubuntu-26.04-amd64-arm64.yml). 3.13 was missing here while
	# ruff and ty both target it and Linux CI runs it, so Windows never exercised
	# the version the lint gates are configured for.
	[string[]]$PythonVersions = @("3.13", "3.14", "3.14t"),
	[string]$PackageName = "orchestrant",
	[string]$LogDir = "logs",
	[switch]$StopOnError,  # stop at the first failing step instead of carrying on
	[switch]$EnablePySpy
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

# Modules resolve through the shared bootstrap (a verbatim copy of
# ANTfrastructure's shared/windows/templates/Resolve-BuildModule.ps1) instead of a
# hard-coded submodule path: a module that moves upstream is picked up without
# editing this script, and a missing submodule reports the exact
# `git submodule update` command rather than a bare path.
. (Join-Path $PSScriptRoot 'Resolve-BuildModule.ps1')

# Dependency order: Shared, then Build, then what builds on them.
# (Import-BuildModule pulls WindowsScripts.Shared in regardless — a nested
# import inside a .psm1 is module-private and never reaches this session.)
Import-BuildModule @(
	'WindowsScripts.Shared'
	'WindowsBuild.Common'
	'WindowsUv.Common'
)

# ADOPTED 2026-09-15 (owner decision: adopt the hub's Windows Python drivers).
# The static-analysis step and the two packaging steps below are no longer
# re-inlined here. They are ANTfrastructure's own drivers at the pin recorded in
# .gitmodules - windows/scripts/python/Invoke-CiStaticAnalysis.ps1 and
# Invoke-CiPackaging.ps1 - launched as CHILD PROCESSES by path, because
# Resolve-BuildModule probes only the modules/ directories; their exit code is
# what fails the step. Both declare `[string]$RepoRoot = ''` and forward it to
# Initialize-CiEnvironment, so passing THIS repo's root is what keeps every path
# they derive (pyproject.toml, the venvs, the log dir) out of the hub checkout.
#
# The pytest matrix stays local: the hub's Invoke-CiTests.ps1 was removed
# upstream (ANTfrastructure 2eaed40e), so there is no counterpart to call.
#
# What adoption changed, recorded rather than left to be found in a diff:
#   * -RetryWithoutLocked is gone for those three steps. Sync-ProjectDependencies
#     below still opts the pytest matrix into the `uv sync` retry without
#     --locked; the hub drivers do not, so a stale lockfile is a hard failure
#     there. Stricter, not weaker.
#   * Static analysis runs in the driver's `.venv-static-analysis` (which it
#     creates and removes itself) instead of the local `.venv-static`, so
#     $script:CreatedUvEnvs no longer tracks that environment.
#   * Each driver writes its own log and build-summary JSON beside this script's,
#     so a lane collecting artifacts collects three sets, not one.
#   * -PackageName 'orchestrant' is load-bearing: Get-PyprojectPackageName falls
#     back to the DISTRIBUTION name, "OrchestrANT", which is not an importable
#     module - the trap AGENTS.md section 4 opens with.

$script:BuildContext = New-BuildContext -Workspace $repoRoot -LogDir $LogDir -StopOnError:$StopOnError
$script:BuildContext.SuppressConsoleOutput = $false
$logPath = $script:BuildContext.LogPath
$script:CreatedUvEnvs = New-Object System.Collections.Generic.List[string]

# Success/failure tracking

# NOTE: Results.SoftFailed / Results.SoftErrors used to be hand-added here for
# the local Invoke-Step fork. New-BuildContext already creates AllowedFailures,
# Errors and Durations, which the upstream Invoke-BuildStep populates instead.
$script:Results = $script:BuildContext.Results

function Close-Log {
	Close-BuildLog -Context $script:BuildContext
}

# Write-LogInfo, not Write-Log: PSScriptAnalyzer's
# PSAvoidOverwritingBuiltInCmdlets reports the shorter name as shadowing a
# cmdlet PowerShell ships, and the -Info suffix also matches its three
# siblings below.
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

# GATE AGGREGATION IS NOT LOCAL ANY MORE. A local Invoke-Gate wrapper (and
# before it an Invoke-Optional that could not fail: it recorded findings under
# AllowedFailures, which never reaches $Results.Failed and so never reaches the
# exit code) used to stand here for the static-analysis step's six tools. That
# step now runs ANTfrastructure's Invoke-CiStaticAnalysis.ps1, which does its own
# Invoke-BuildGate / Assert-BuildGates - the twin of 01-core/gates.sh on the
# Linux lane - so both lanes aggregate the same way, every tool still runs when
# an earlier one fails, and a batch in which NO gate ran cannot report green.
# The consequence for reading a summary: the driver's own build-summary JSON
# names the six tools, while this script records the step.

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
	# The soft half of Invoke-External, for bench/demo_*.py only. Invoke-BuildExternal
	# -IgnoreExitCode returns the code instead of throwing, so a missing profiler or a
	# demo that cannot run on this host logs "<label> skipped" and the step carries on
	# - the same verdict as `|| info "... skipped"` on the Linux lane.
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
	# The create-and-remember pair this file carried SCRIPT-LOCAL is now
	# ANTfrastructure's New-TrackedUvEnvironment / Remove-TrackedUvEnvironment
	# (WindowsUv.Common). Three drivers had each written the same body against
	# their own $CreatedUvEnvs list, and script-local meant none of them could
	# call another's. Kept as a wrapper rather than editing five call sites:
	# binding $repoRoot, the tracker and the three delegates is the only thing
	# those call sites would otherwise have to repeat.
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

	# Was a local re-implementation of the whole uv sync, written only to get the
	# retry-without---locked fallback. That fallback is now upstream as
	# Sync-UvProjectDependencies -RetryWithoutLocked (ANTfrastructure 2026-08-11),
	# so this is a two-line adapter that binds the build context's runner and
	# log sinks. It is opt-in upstream on purpose: --locked exists so CI fails on
	# an un-regenerated lockfile, and defaulting the fallback on would make that
	# gate a no-op. This repo opts in, matching its previous behaviour.
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
	# Delegates to ANTfrastructure's Invoke-BuildStep (WindowsBuild.Common), which
	# this script already imports. The local body replaced here was an older fork
	# of exactly that function - same parameters, same log format, same
	# StopOnError-and-Critical rethrow - but it tracked allowed failures in
	# hand-added Results.SoftFailed/SoftErrors instead of the AllowedFailures and
	# Errors that New-BuildContext already creates, and it had no timing.
	#
	# Delegating gains per-step durations and the machine-readable JSON summary
	# for free. Kept as a wrapper rather than editing every call site: the -Context
	# binding is the only thing those call sites would otherwise have to repeat.
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
	# Delegates to ANTfrastructure's Write-BuildSummary. The 39-line local body this
	# replaced printed the same three sections from the same Results object; the
	# upstream one additionally reports per-step durations and writes the
	# machine-readable build-summary JSON to $Context.SummaryPath.
	Write-BuildSummary -Context $script:BuildContext
}

try {
	try {
		Initialize-TestResultsDir

		Write-LogInfo "=== Pytest matrix (Windows) ==="

		# WHICH interpreter may fail without gating CI is a FLEET answer, not a
		# per-repo one. Test-ExperimentalPython (ANTfrastructure WindowsUv.Common)
		# reads the same EXPERIMENTAL_PYTHON_VERSIONS knob as the Linux half
		# (linux/scripts/01-core/python_uv.sh, same "3.14t" default), so one
		# export now sets the policy for both lanes of the matrix.
		#
		# What stood here was a THIRD literal of that list beside the two
		# upstream ones, with nothing holding the three equal. Before that it was
		# a range (`-ge [version]"3.14"`), which tolerated plain CPython 3.14
		# too: an allowed failure never reaches $Results.Failed and therefore
		# never reaches the exit code, so a real 3.14 unit-test failure FAILED
		# Linux CI and was silently green here. The upstream function is an exact
		# membership test for exactly that reason -- keep it one. With a
		# comparison, every future stable release (3.15, 3.16, ...) is
		# grandfathered into the tolerance the day it joins $PythonVersions.
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

					# SOFT, deliberately. The Linux twin
					# (third_party/ANTfrastructure/linux/scripts/02-toolchain/python/ci_tests.sh)
					# ends every bench/demo_*.py line with `|| info "... skipped"`.
					# These demos are a profiling showcase, not a gate, and the two
					# lanes grading the same tree differently is what this file keeps
					# having to unpick. The unit tests above stay hard.
					Invoke-BenchDemo -Label "demo_cprofile.py" -File "uv" -CommandArgs @("run", "python", "bench/demo_cprofile.py")
					Invoke-BenchDemo -Label "demo_line_profiler.py" -File "uv" -CommandArgs @("run", "python", "bench/demo_line_profiler.py")
					# Invoke-BenchDemo -Label "memory profiling" -File "uv" -CommandArgs @("run", "-m", "memory_profiler", "bench/demo_memory_profiling.py")
					if ($EnablePySpy) {
						Invoke-BenchDemo -Label "py-spy profiling" -File "uv" -CommandArgs @("run", "py-spy", "record", "--rate", "200", "--duration", "45", "-o", "profile.svg", "--", "python", "bench/demo_py_spy.py")
					}
					Invoke-BenchDemo -Label "benchmark tests" -File "uv" -CommandArgs @("run", "pytest", "bench/demo_pytest_benchmark.py")
				} finally {
					Remove-UvEnvironment -EnvPath $envPath
				}
			} | Out-Null
		}

		# GATING, and no longer re-inlined here: this IS the hub's
		# Invoke-CiStaticAnalysis.ps1, so the six tools, their arguments and
		# their aggregation (Invoke-BuildGate / Assert-BuildGates, which also
		# fails when NO gate ran) are the ones the Linux twin uses. Tool-list
		# parity between the lanes is structural now, not a rule this file has
		# to restate and keep true by hand.
		#
		# -ExtraPaths, since the 19286e9f pin, is the Windows twin of the Linux
		# lane's STATIC_ANALYSIS_EXTRA_PATHS (scripts/linux/ci_static_analysis.sh
		# sets the same three). benchmarks/, frontend/ and bench/ are first-party
		# Python that the driver's package/tests/conf.py/setup.py target list
		# could not reach, so the two lanes graded the same subset and both
		# missed it. The bandit half of the knob is broken upstream and is
		# documented where it bites, in the Linux wrapper's header.
		Invoke-Step -StepName "Static Analysis (Python 3.14)" -Script {
			Write-LogInfo "=== Static analysis (Python 3.14) ==="
			$driver = Join-Path $repoRoot 'third_party/ANTfrastructure/windows/scripts/python/Invoke-CiStaticAnalysis.ps1'
			if (-not (Test-Path $driver)) {
				throw "Missing $driver - run: git submodule update --init --recursive"
			}

			# -Command, NOT -File, and only because -ExtraPaths is an array.
			# `pwsh -File driver.ps1 -ExtraPaths benchmarks frontend bench` binds
			# ONE element and silently discards the rest (measured: Count = 1,
			# "benchmarks"), and the comma spelling binds the whole thing as a
			# single path string. Neither errors, so the gate would have gone on
			# reporting green over two of the three trees. -Command takes a real
			# array literal, and an unhandled terminating error inside it still
			# leaves the child at exit 1, which is what fails this step.
			#
			# -PackageName is not optional here: without it the driver derives
			# the DISTRIBUTION name "OrchestrANT" from pyproject.toml and points
			# bandit, ruff and vulture at a directory that does not exist.
			$q = { param([string]$v) "'" + $v.Replace("'", "''") + "'" }
			$command = "& {0} -RepoRoot {1} -PythonVersion '3.14' -PackageName {2} -ExtraPaths @('benchmarks','frontend','bench')" -f `
				(& $q $driver), (& $q $repoRoot), (& $q $PackageName)
			Invoke-External -File "pwsh" -Args @("-NoProfile", "-Command", $command)
		} | Out-Null

		# Both packaging steps in one call: Invoke-CiPackaging.ps1 runs
		# "Packaging (source)" and "Packaging (Windows binaries)" itself, with
		# the same CYTHONIZE=True second pass and the same per-step venvs.
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
	# Clean up every environment. Remove-TrackedUvEnvironment (ANTfrastructure
	# WindowsUv.Common) owns this loop now: it attempts removal for EVERY
	# tracked environment even when one fails -- leaving the rest behind on a
	# Windows runner is how a later run inherits a half-deleted venv -- and
	# then clears the tracker. Removing one that a step's own finally already
	# removed is free: Remove-UvProjectEnvironment returns early when the path
	# is gone.
	Remove-TrackedUvEnvironment -Tracker $script:CreatedUvEnvs -LogInfo $script:UvLogInfo -LogWarning $script:UvLogWarning

	# Print the summary
	Write-Summary

	Close-Log

	# Exit code derived from the recorded failures
	if ($script:Results.Failed.Count -gt 0) {
		exit 1
	}
}

