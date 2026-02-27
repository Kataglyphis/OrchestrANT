Param(
	[string[]]$PythonVersions = @("3.14", "3.14t"),
	[string]$PackageName = "orchestr_ant_ion",
	[string]$LogDir = "logs",
	[switch]$StopOnError,  # Neuer Parameter: bei Fehler stoppen statt fortfahren
	[switch]$EnablePySpy
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$containerHubModulesPath = Join-Path $repoRoot "ExternalLib\Kataglyphis-ContainerHub\windows\scripts\modules"
$buildCommonModulePath = Join-Path $containerHubModulesPath "WindowsBuild.Common.psm1"
$uvCommonModulePath = Join-Path $containerHubModulesPath "WindowsUv.Common.psm1"

if (-not (Test-Path -Path $buildCommonModulePath)) {
	throw "Required reusable module not found: $buildCommonModulePath"
}

if (-not (Test-Path -Path $uvCommonModulePath)) {
	throw "Required reusable module not found: $uvCommonModulePath"
}

Import-Module $buildCommonModulePath -Force
Import-Module $uvCommonModulePath -Force

$script:BuildContext = New-BuildContext -Workspace $repoRoot -LogDir $LogDir -StopOnError:$StopOnError
$script:BuildContext.SuppressConsoleOutput = $false
$logPath = $script:BuildContext.LogPath
$script:CreatedUvEnvs = New-Object System.Collections.Generic.List[string]

# Tracking fÃ¼r Erfolg/Fehler

$script:BuildContext.Results.SoftFailed = New-Object System.Collections.Generic.List[string]
$script:BuildContext.Results.SoftErrors = @{}
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

function Invoke-Optional {
	param(
		[scriptblock]$Script,
		[string]$Name
	)

	Invoke-BuildOptional -Context $script:BuildContext -Script $Script -Name $Name
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
	param(
		[string]$PythonVersion,
		[string]$EnvName
	)

	$envPath = New-UvProjectEnvironment -Workspace $repoRoot -PythonVersion $PythonVersion -EnvName $EnvName -CommandRunner $script:UvCommandRunner -LogInfo $script:UvLogInfo -LogWarning $script:UvLogWarning
	$script:CreatedUvEnvs.Add($envPath) | Out-Null

	return $envPath
}

function Remove-UvEnvironment {
	param(
		[string]$EnvPath
	)

	Remove-UvProjectEnvironment -EnvPath $EnvPath -LogInfo $script:UvLogInfo -LogWarning $script:UvLogWarning
}

function Invoke-UvSync {
	param(
		[switch]$NoBuildIsolationPackageWxPython,
		[switch]$UseLocked
	)

	$syncArgs = @("sync", "--dev", "--all-extras")
	if ($UseLocked) {
		$syncArgs += "--locked"
	}
	if ($NoBuildIsolationPackageWxPython) {
		$syncArgs += @("--no-build-isolation-package", "wxpython")
	}

	try {
		Invoke-External -File "uv" -Args $syncArgs
	} catch {
		$errorMessage = $_.Exception.Message
		$lockOutdated = $errorMessage -match "lockfile.*needs to be updated" -or $errorMessage -match "--locked was provided"
		if ($UseLocked -and $lockOutdated) {
			Write-LogWarning "uv.lock is out of date; retrying dependency sync without --locked."
			$retryArgs = @("sync", "--dev", "--all-extras")
			if ($NoBuildIsolationPackageWxPython) {
				$retryArgs += @("--no-build-isolation-package", "wxpython")
			}
			Invoke-External -File "uv" -Args $retryArgs
			return
		}

		throw
	}
}

function Sync-ProjectDependencies {
	param(
		[switch]$NoBuildIsolationPackageWxPython,
		[switch]$UseLocked
	)

	Invoke-UvSync -NoBuildIsolationPackageWxPython:$NoBuildIsolationPackageWxPython -UseLocked:$UseLocked
}

function Ensure-TestResultsDir {
	New-Item -ItemType Directory -Force "docs/test_results" | Out-Null
}

# Neue Funktion: FÃ¼hrt einen Schritt aus und trackt Erfolg/Fehler

function Invoke-Step {
	param(
		[Parameter(Mandatory)]
		[string]$StepName,
		[Parameter(Mandatory)]
		[scriptblock]$Script,
		[switch]$Critical,  # Bei Critical + StopOnError wird das Skript beendet
		[switch]$AllowFailure  # Log as non-blocking failure
	)

	Write-Log ""
	Write-Log ">>> Starting: $StepName"
	Write-Log ("=" * 60)

	try {
		& $Script
		$script:Results.Succeeded.Add($StepName) | Out-Null
		Write-LogSuccess "<<< Completed: $StepName"
		return $true
	} catch {
		$errorMessage = $_.Exception.Message
		if ($AllowFailure) {
			$script:Results.SoftFailed.Add($StepName) | Out-Null
			$script:Results.SoftErrors[$StepName] = $errorMessage
			Write-LogWarning "<<< FAILED (allowed): $StepName"
			Write-LogWarning "    Error: $errorMessage"
		} else {
			$script:Results.Failed.Add($StepName) | Out-Null
			$script:Results.Errors[$StepName] = $errorMessage
			Write-LogError "<<< FAILED: $StepName"
			Write-LogError "    Error: $errorMessage"
		}

		if ($_.ScriptStackTrace) {
			Write-Log "    Stack: $($_.ScriptStackTrace)"
		}

		if (-not $AllowFailure -and $StopOnError -and $Critical) {
			throw "Critical step '$StepName' failed: $errorMessage"
		}

		return $false
	}
}

function Write-Summary {
	Write-Log ""
	Write-Log ("=" * 60)
	Write-Log "=== PIPELINE SUMMARY ==="
	Write-Log ("=" * 60)
	Write-Log ""

	if ($script:Results.Succeeded.Count -gt 0) {
		Write-LogSuccess "SUCCEEDED ($($script:Results.Succeeded.Count)):"
		foreach ($step in $script:Results.Succeeded) {
			Write-LogSuccess "  [OK] $step"
		}
	}

	Write-Log ""

	if ($script:Results.Failed.Count -gt 0) {
		Write-LogError "FAILED ($($script:Results.Failed.Count)):"
		foreach ($step in $script:Results.Failed) {
			Write-LogError "  [X] $step"
			Write-LogError "      Error: $($script:Results.Errors[$step])"
		}
	}

	Write-Log ""

	if ($script:Results.SoftFailed.Count -gt 0) {
		Write-LogWarning "FAILED (allowed) ($($script:Results.SoftFailed.Count)):"
		foreach ($step in $script:Results.SoftFailed) {
			Write-LogWarning "  [~] $step"
			Write-LogWarning "      Error: $($script:Results.SoftErrors[$step])"
		}
	}

	Write-Log ""
	$total = $script:Results.Succeeded.Count + $script:Results.Failed.Count + $script:Results.SoftFailed.Count
	$successRate = if ($total -gt 0) { [math]::Round(($script:Results.Succeeded.Count / $total) * 100, 1) } else { 0 }
	Write-Log "Total: $total steps, $($script:Results.Succeeded.Count) succeeded, $($script:Results.Failed.Count) failed ($($successRate)% success rate)"
	Write-Log ""

	if ($script:Results.Failed.Count -gt 0) {
		Write-LogWarning "Pipeline completed with errors!"
	} else {
		Write-LogSuccess "Pipeline completed successfully!"
	}
}

try {
	try {
		Ensure-TestResultsDir

		Write-Log "=== Pytest matrix (Windows) ==="

		foreach ($version in $PythonVersions) {
			$versionNumber = $null
			if ($version -match '^\d+(?:\.\d+)?') {
				try {
					$versionNumber = [version]$Matches[0]
				} catch {
					$versionNumber = $null
				}
			}
			$allowFailure = $false
			if ($versionNumber -and $versionNumber -ge [version]"3.14") {
				$allowFailure = $true
			}

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

		Invoke-Step -StepName "Static Analysis (Python 3.14)" -Script {
			Write-Log "=== Static analysis (Python 3.14) ==="
			$envPath = New-UvEnvironment -PythonVersion "3.14" -EnvName ".venv-static"
			try {
				Sync-ProjectDependencies -NoBuildIsolationPackageWxPython

				Invoke-Optional -Name "codespell" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "codespell",
						"orchestr_ant_ion", "tests", "docs/source/conf.py", "setup.py", "README.md"
					)
				}
				Invoke-Optional -Name "bandit" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "bandit", "-r", "orchestr_ant_ion",
						"-x", "tests,.venv,.venv_static_analysis,ExternalLib,archive,docs/test_results"
					)
				}
				Invoke-Optional -Name "vulture" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "vulture",
						"orchestr_ant_ion", "tests", "docs/source/conf.py", "setup.py"
					)
				}
				Invoke-Optional -Name "ruff" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "ruff", "check", "--fix",
						"orchestr_ant_ion", "tests", "docs/source/conf.py", "setup.py"
					)
				}
				Invoke-Optional -Name "ruff format" -Script {
					Invoke-External -File "uv" -Args @(
						"run", "--active", "ruff", "format",
						"orchestr_ant_ion", "tests", "docs/source/conf.py", "setup.py"
					)
				}
				Invoke-Optional -Name "ty" -Script { Invoke-External -File "uv" -Args @("run", "--active", "ty", "check") }
			} finally {
				Remove-UvEnvironment -EnvPath $envPath
			}
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
				Invoke-UvSync
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
	# Cleanup aller Environments
	foreach ($envPath in $script:CreatedUvEnvs) {
		Remove-UvEnvironment -EnvPath $envPath
	}

	# Summary ausgeben
	Write-Summary

	Close-Log

	# Exit-Code basierend auf Fehlern
	if ($script:Results.Failed.Count -gt 0) {
		exit 1
	}
}

