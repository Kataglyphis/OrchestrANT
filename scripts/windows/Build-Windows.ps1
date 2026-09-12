#requires -Version 7.0

Param(
	# Same default matrix as ANTfrastructure's windows/scripts/python/Invoke-CiTests.ps1.
	# 3.13 was missing here while ruff and ty both target it and Linux CI runs it,
	# so Windows never exercised the version the lint gates are configured for.
	[string[]]$PythonVersions = @("3.13", "3.14", "3.14t"),
	[string]$PackageName = "orchestrant",
	[string]$LogDir = "logs",
	[switch]$StopOnError,  # Neuer Parameter: bei Fehler stoppen statt fortfahren
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

# NOT-YET-ADOPTED: three blocks below re-inline drivers that already exist in
# ANTfrastructure — the pytest matrix is diff-identical to
# windows/scripts/python/Invoke-CiTests.ps1, the static-analysis step is
# step-for-step Invoke-CiStaticAnalysis.ps1, and the two packaging steps are
# Invoke-CiPackaging.ps1. Roughly 90 lines of duplication.
#
# The -RepoRoot BLOCKER THIS COMMENT USED TO NAME IS GONE — do not act on the
# old wording. It said the drivers "CANNOT work against the currently pinned
# submodule" because they call
#   Initialize-CiEnvironment -ScriptRoot $PSScriptRoot -EnterRepoRoot
# without passing -RepoRoot through, so the repo root would resolve three levels
# above the driver, i.e. to third_party/ANTfrastructure itself, and pyproject.toml,
# the uv venvs, logs/ and docs/test_results/ would all be read from and written
# into the submodule. That was true once. At the pin recorded in .gitmodules
# today (f6cc09f7) all four drivers declare `[string]$RepoRoot = ''` and forward
# it: third_party/ANTfrastructure/windows/scripts/python/Invoke-CiTests.ps1 lines
# 49 and 58, Invoke-CiStaticAnalysis.ps1 41 and 50, Invoke-CiPackaging.ps1 37
# and 46, Invoke-CiBuildDocs.ps1 37 and 46. Both former preconditions — merged
# upstream, and bumped here — are MET.
#
# THE REMAINING BLOCKER IS A GATE DOWNGRADE, AND IT IS THE REASON THIS STILL
# STANDS: Invoke-CiTests.ps1 lines 137-145 wrap the cprofile demo, the
# line_profiler demo and pytest-benchmark in Invoke-BuildOptional. The
# corresponding calls below are plain Invoke-External, i.e. HARD failures.
# Swapping the hub driver in as it is pinned today would turn three failing
# gates green without a single line of this file changing, which is exactly the
# class of silent-green regression the 3.14 unit-test tolerance note further
# down was written about. Fix that upstream FIRST — make the three demos
# non-optional in Invoke-CiTests.ps1 — and only then delete the local copies.
#
# RE-VERIFIED 2026-09-08 against the ANTfrastructure working tree, while adopting
# the shared gate / uv / experimental-Python owners below: Invoke-CiTests.ps1 is
# UNCHANGED, and its cprofile demo, line_profiler demo and pytest-benchmark are
# still wrapped in Invoke-BuildOptional. The blocker therefore STANDS and the
# three driver bodies below stay local. Adopting the smaller owners does not
# move it either way: those are mechanics, these three are policy.
#
# Preconditions for adopting, restated: (1) the three bench demos are hard
# failures in ANTfrastructure's Invoke-CiTests.ps1, (2) third_party/ANTfrastructure is
# bumped to that commit. Then launch each driver as a CHILD PROCESS by path and
# propagate its exit code — Resolve-BuildModule cannot resolve them, it appends
# `.psm1` and probes only `modules/`.
#
# Remaining behavioural consequences that need a decision at that point, none of
# which should be discovered from a diff:
#   * -RetryWithoutLocked is lost. Sync-ProjectDependencies below opts into the
#     `uv sync` retry without --locked; the hub drivers do not. That makes a
#     stale lockfile a hard failure, which is stricter, not weaker.
#   * -EnablePySpy has no equivalent in Invoke-CiTests.ps1; the py-spy record
#     step would simply disappear.
#   * Three drivers means three logs, three build-summary JSONs and three exit
#     codes instead of one combined run, so the workflow's artifact paths and
#     this script's single `exit 1` both change shape.

$script:BuildContext = New-BuildContext -Workspace $repoRoot -LogDir $LogDir -StopOnError:$StopOnError
$script:BuildContext.SuppressConsoleOutput = $false
$logPath = $script:BuildContext.LogPath
$script:CreatedUvEnvs = New-Object System.Collections.Generic.List[string]

# Tracking fÃ¼r Erfolg/Fehler

# NOTE: Results.SoftFailed / Results.SoftErrors used to be hand-added here for
# the local Invoke-Step fork. New-BuildContext already creates AllowedFailures,
# Errors and Durations, which the upstream Invoke-BuildStep populates instead.
$script:Results = $script:BuildContext.Results

function Close-Log {
	Close-BuildLog -Context $script:BuildContext
}

function Write-Log {
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

Write-Log "=== Windows build/test pipeline (PowerShell) ==="
Write-Log "Repo root: $repoRoot"
Write-Log "Logging all output to: $logPath"
Write-Log "Stop on error: $StopOnError"

# Invoke-Optional (a pass-through to ANTfrastructure's Invoke-BuildOptional) used
# to stand here, and the static-analysis step was its only caller. That was the
# Windows half of a gate that could not fail: Invoke-BuildOptional catches the
# exception, records the tool under $Context.Results.AllowedFailures, logs
# "<name> failed, continuing" and returns. Nothing it touches is
# $Results.Failed, and $Results.Failed.Count is the ONLY input to this script's
# `exit 1`. codespell, bandit, vulture, ruff and ty were therefore advisory on
# this lane while .github/copilot-instructions.md called them merge blockers.
#
# The deliberate opposite of that is now upstream, as ANTfrastructure's
# Invoke-BuildGate / Assert-BuildGates (WindowsBuild.Common) -- the twin of
# 01-core/gates.sh on the Linux lane, so both lanes aggregate the same way. It
# still runs every tool (one push should surface every finding, not the first),
# records each failure on the build context, and lets Assert-BuildGates raise
# the verdict once at the end of the step -- including when NO gate ran, which
# an aggregator must never report green over.
#
# The local $script:GateFailures list went with it. Two consequences worth
# knowing before reading a summary: a failing GATE now lands in
# $Results.Failed under its own name as well as the step's, and a passing one
# lands in $Results.Succeeded, so the summary names the six tools individually
# instead of only "Static Analysis".
#
# Kept as a wrapper rather than editing the six call sites: binding -Context is
# the only thing they would otherwise have to repeat -- the same argument
# Invoke-Step below already makes.
function Invoke-Gate {
	param(
		[Parameter(Mandatory)]
		[string]$Name,
		[Parameter(Mandatory)]
		[scriptblock]$Script
	)

	Invoke-BuildGate -Context $script:BuildContext -Name $Name -Script $Script
}

function Invoke-External {
	param(
		[Parameter(Mandatory)]
		[string]$File,
		[Alias('Args')]
		[string[]]$CommandArgs = @()
	)

	Invoke-BuildExternal -Context $script:BuildContext -File $File -Parameters $CommandArgs | Out-Null
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

function Ensure-TestResultsDir {
	New-Item -ItemType Directory -Force "docs/test_results" | Out-Null
}

# Neue Funktion: FÃ¼hrt einen Schritt aus und trackt Erfolg/Fehler

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
		Ensure-TestResultsDir

		Write-Log "=== Pytest matrix (Windows) ==="

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
				Write-Log "--- Python $version ---"
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

					Invoke-External -File "uv" -Args @("run", "python", "bench/demo_cprofile.py")
					Invoke-External -File "uv" -Args @("run", "python", "bench/demo_line_profiler.py")
					# Invoke-External -File "uv" -Args @("run", "-m", "memory_profiler", "bench/demo_memory_profiling.py")
					if ($EnablePySpy) {
						Invoke-External -File "uv" -Args @("run", "py-spy", "record", "--rate", "200", "--duration", "45", "-o", "profile.svg", "--", "python", "bench/demo_py_spy.py")
					}
					Invoke-External -File "uv" -Args @("run", "pytest", "bench/demo_pytest_benchmark.py")
				} finally {
					Remove-UvEnvironment -EnvPath $envPath
				}
			} | Out-Null
		}

		# GATING. Keep the tool list and its arguments identical to
		# scripts/linux/ci_static_analysis.sh -- the two lanes grading the same
		# tree differently is the drift that produced this whole change.
		Invoke-Step -StepName "Static Analysis (Python 3.14)" -Script {
			Write-Log "=== Static analysis (Python 3.14) ==="
			$envPath = New-UvEnvironment -PythonVersion "3.14" -EnvName ".venv-static"
			try {
				Sync-ProjectDependencies -NoBuildIsolationPackageWxPython

				Invoke-Gate -Name "codespell" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "codespell",
						"orchestrant", "tests", "docs/source/conf.py", "setup.py", "README.md"
					)
				}
				Invoke-Gate -Name "bandit" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "bandit", "-r", "orchestrant",
						"-x", "tests,.venv,.venv_static_analysis,ExternalLib,third_party,archive,docs/test_results"
					)
				}
				Invoke-Gate -Name "vulture" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "vulture",
						"orchestrant", "tests", "docs/source/conf.py", "setup.py"
					)
				}
				# --no-fix, not --fix. `ruff check --fix` reports only what it
				# could NOT repair, and CI throws the checkout away, so every
				# auto-fixable finding was silently "handled" and never seen.
				Invoke-Gate -Name "ruff check" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "ruff", "check", "--no-fix",
						"orchestrant", "tests", "docs/source/conf.py", "setup.py"
					)
				}
				# --check --diff, not a bare `format`, for the same reason:
				# rewriting files in a discarded checkout always exits 0.
				Invoke-Gate -Name "ruff format" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "ruff", "format", "--check", "--diff",
						"orchestrant", "tests", "docs/source/conf.py", "setup.py"
					)
				}
				Invoke-Gate -Name "ty" -Script { Invoke-External -File "uv" -Args @("run", "--active", "ty", "check") }
			} finally {
				Remove-UvEnvironment -EnvPath $envPath
			}

			# Assert-BuildGates throws on any failed gate -- that throw is what
			# puts this STEP into $Results.Failed and so into the exit code -- and
			# throws again when no gate ran at all. Its success line ("Static
			# analysis OK (6 gate(s))") replaces the hand-written pass message
			# that used to name the six tools; the gates now name themselves as
			# they run.
			Assert-BuildGates -Context $script:BuildContext -Label "Static analysis"
		} | Out-Null

		Invoke-Step -StepName "Packaging (source)" -Script {
			Write-Log "=== Packaging (source) ==="
			$envPath = New-UvEnvironment -PythonVersion "3.14" -EnvName ".venv-packaging-sources"
			try {
				Sync-ProjectDependencies -NoBuildIsolationPackageWxPython
				Invoke-External -File "uv" -Args @("build")
			} finally {
				Remove-UvEnvironment -EnvPath $envPath
			}
		} | Out-Null

		Invoke-Step -StepName "Packaging (Windows binaries)" -Script {
			Write-Log "=== Packaging (Windows binaries) ==="
			$env:CYTHONIZE = "True"

			$envPath = New-UvEnvironment -PythonVersion "3.14" -EnvName ".venv-packaging-binaries"
			try {
				Sync-ProjectDependencies
				Invoke-External -File "uv" -Args @("build")
			} finally {
				Remove-UvEnvironment -EnvPath $envPath
			}
		} | Out-Null

		Write-Log "=== Completed Windows build/test pipeline ==="

	} catch {
		Write-LogError "Unhandled critical error: $($_.Exception.Message)"
		if ($_.ScriptStackTrace) {
			Write-LogError "Stack trace: $($_.ScriptStackTrace)"
		}
		throw
	}
} finally {
	# Cleanup aller Environments. Remove-TrackedUvEnvironment (ANTfrastructure
	# WindowsUv.Common) owns this loop now: it attempts removal for EVERY
	# tracked environment even when one fails -- leaving the rest behind on a
	# Windows runner is how a later run inherits a half-deleted venv -- and
	# then clears the tracker. Removing one that a step's own finally already
	# removed is free: Remove-UvProjectEnvironment returns early when the path
	# is gone.
	Remove-TrackedUvEnvironment -Tracker $script:CreatedUvEnvs -LogInfo $script:UvLogInfo -LogWarning $script:UvLogWarning

	# Summary ausgeben
	Write-Summary

	Close-Log

	# Exit-Code basierend auf Fehlern
	if ($script:Results.Failed.Count -gt 0) {
		exit 1
	}
}

