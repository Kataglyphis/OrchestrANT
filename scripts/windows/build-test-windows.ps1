Param(
	[string[]]$PythonVersions = @("3.10", "3.11", "3.12", "3.13", "3.14", "3.14t"),
	[string]$PackageName = "orchestr_ant_ion",
	[string]$LogDir = "logs",
	[switch]$StopOnError  # Neuer Parameter: bei Fehler stoppen statt fortfahren
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDirPath = Join-Path $repoRoot $LogDir
New-Item -ItemType Directory -Force $logDirPath | Out-Null
$logPath = Join-Path $logDirPath "build-test-windows-$timestamp.log"
$script:CreatedUvEnvs = New-Object System.Collections.Generic.List[string]

# Tracking fÃ¼r Erfolg/Fehler

$script:Results = @{
	Succeeded = New-Object System.Collections.Generic.List[string]
	Failed    = New-Object System.Collections.Generic.List[string]
	SoftFailed = New-Object System.Collections.Generic.List[string]
	Errors    = @{}
	SoftErrors = @{}
}

$script:LogWriter = $null

function Open-Log {
	param(
		[Parameter(Mandatory)]
		[string]$Path
	)

	$fileStream = New-Object System.IO.FileStream(
		$Path,
		[System.IO.FileMode]::Append,
		[System.IO.FileAccess]::Write,
		[System.IO.FileShare]::ReadWrite
	)
	$script:LogWriter = New-Object System.IO.StreamWriter($fileStream, [System.Text.Encoding]::UTF8)
	$script:LogWriter.AutoFlush = $true
}

function Close-Log {
	if ($script:LogWriter) {
		try {
			$script:LogWriter.Flush()
			$script:LogWriter.Dispose()
		} catch {
			# ignore
		} finally {
			$script:LogWriter = $null
		}
	}
}

function Write-Log {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]  # <-- Diese Zeile hinzufÃ¼gen
		[string]$Message
	)

	Write-Host $Message
	if ($script:LogWriter) {
		$script:LogWriter.WriteLine($Message)
	}
}

function Write-LogWarning {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]  # <-- Diese Zeile hinzufÃ¼gen
		[string]$Message
	)

	if ($Message) {
		Write-Warning $Message
		if ($script:LogWriter) {
			$script:LogWriter.WriteLine("WARNING: $Message")
		}
	} else {
		Write-Host ""
		if ($script:LogWriter) {
			$script:LogWriter.WriteLine("")
		}
	}
}

function Write-LogError {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]  # <-- Diese Zeile hinzufÃ¼gen
		[string]$Message
	)

	if ($Message) {
		Write-Host $Message -ForegroundColor Red
		if ($script:LogWriter) {
			$script:LogWriter.WriteLine("ERROR: $Message")
		}
	} else {
		Write-Host ""
		if ($script:LogWriter) {
			$script:LogWriter.WriteLine("")
		}
	}
}

function Write-LogSuccess {
	param(
		[Parameter(Mandatory)]
		[AllowEmptyString()]  # <-- Diese Zeile hinzufÃ¼gen
		[string]$Message
	)

	if ($Message) {
		Write-Host $Message -ForegroundColor Green
		if ($script:LogWriter) {
			$script:LogWriter.WriteLine("SUCCESS: $Message")
		}
	} else {
		Write-Host ""
		if ($script:LogWriter) {
			$script:LogWriter.WriteLine("")
		}
	}
}

Open-Log -Path $logPath

Write-Log "=== Windows build/test pipeline (PowerShell) ==="
Write-Log "Repo root: $repoRoot"
Write-Log "Logging all output to: $logPath"
Write-Log "Stop on error: $StopOnError"

function Invoke-Optional {
	param(
		[scriptblock]$Script,
		[string]$Name
	)

	try {
		& $Script
	} catch {
		Write-LogWarning "$Name failed, continuing. Details: $($_.Exception.Message)"
	}
}

function Invoke-External {
	param(
		[Parameter(Mandatory)]
		[string]$File,
		[string[]]$Args = @()
	)

	$cmdLine = if ($Args -and $Args.Count) { "$File $($Args -join ' ')" } else { $File }
	Write-Log "CMD: $cmdLine"

	$previousErrorActionPreference = $ErrorActionPreference
	$ErrorActionPreference = "Continue"
	$global:LASTEXITCODE = 0
	try {
		& $File @Args 2>&1 | ForEach-Object {
			$line = $_
			if ($null -eq $line) {
				return
			}
			Write-Log ([string]$line)
		}
		$exitCode = $LASTEXITCODE
		if ($exitCode -ne 0) {
			throw "Command failed with exit code ${exitCode}: $cmdLine"
		}
	} finally {
		$ErrorActionPreference = $previousErrorActionPreference
	}
}

function New-UvEnvironment {
	param(
		[string]$PythonVersion,
		[string]$EnvName
	)

	$envPath = Join-Path $repoRoot $EnvName
	Write-Log "Creating uv environment: $envPath (Python $PythonVersion)"
	if (Test-Path -Path $envPath) {
		Remove-UvEnvironment -EnvPath $envPath
	}
	Invoke-External -File "uv" -Args @("venv", "--python", $PythonVersion, "--clear", $envPath)
	$env:UV_PROJECT_ENVIRONMENT = $envPath
	$script:CreatedUvEnvs.Add($envPath) | Out-Null

	return $envPath
}

function Remove-UvEnvironment {
	param(
		[string]$EnvPath
	)

	if (-not $EnvPath) {
		return
	}

	if ($env:UV_PROJECT_ENVIRONMENT -eq $EnvPath) {
		$env:UV_PROJECT_ENVIRONMENT = $null
	}

	if (-not (Test-Path -Path $EnvPath)) {
		return
	}

	Write-Log "Removing uv environment: $EnvPath"
	$maxAttempts = 8
	for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
		$removeErrors = @()
		Remove-Item -Path $EnvPath -Recurse -Force -ErrorAction SilentlyContinue -ErrorVariable +removeErrors
		if (-not (Test-Path -Path $EnvPath)) {
			return
		}

		try {
			[GC]::Collect()
			[GC]::WaitForPendingFinalizers()
		} catch {
			# ignore
		}

		$lastError = $null
		if ($removeErrors -and $removeErrors.Count) {
			$lastError = $removeErrors[-1].Exception.Message
		}

		Write-LogWarning "Failed to remove environment '$EnvPath' (attempt $attempt/$maxAttempts). $lastError"
		Start-Sleep -Seconds 2
	}
}

function Sync-ProjectDependencies {
	param(
		[switch]$NoBuildIsolationPackageWxPython,
		[switch]$UseLocked
	)

	$syncArgs = @("-v", "sync", "--dev", "--all-extras")
	if ($UseLocked) {
		$syncArgs += "--locked"
	}
	if ($NoBuildIsolationPackageWxPython) {
		$syncArgs += @("--no-build-isolation-package", "wxpython")
	}

	Write-Log "Syncing project dependencies: uv $($syncArgs -join ' ')"
	Invoke-External -File "uv" -Args $syncArgs
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
					$useLocked = Test-Path -Path "uv.lock"
					if ($useLocked) {
						Sync-ProjectDependencies -NoBuildIsolationPackageWxPython -UseLocked
					} else {
						Sync-ProjectDependencies -NoBuildIsolationPackageWxPython
					}

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
					# Invoke-External -File "uv" -Args @("run", "py-spy", "record", "--rate", "200", "--duration", "45", "-o", "profile.svg", "--", "python", "bench/demo_py_spy.py")
					Invoke-External -File "uv" -Args @("run", "pytest", "bench/demo_pytest_benchmark.py")
				} finally {
					Remove-UvEnvironment -EnvPath $envPath
				}
			}
		}

		Invoke-Step -StepName "Static Analysis (Python 3.13)" -Script {
			Write-Log "=== Static analysis (Python 3.13) ==="
			$envPath = New-UvEnvironment -PythonVersion "3.13" -EnvName ".venv-static"
			try {
				if (Test-Path -Path "uv.lock") {
					Sync-ProjectDependencies -NoBuildIsolationPackageWxPython -UseLocked
				} else {
					Sync-ProjectDependencies -NoBuildIsolationPackageWxPython
				}

				Invoke-Optional -Name "codespell" -Script { Invoke-External -File "uv" -Args @("run", "codespell") }
				Invoke-Optional -Name "mypy" -Script { Invoke-External -File "uv" -Args @("run", "mypy", ".") }
				Invoke-Optional -Name "bandit" -Script { Invoke-External -File "uv" -Args @("run", "bandit", "-r", ".") }
				Invoke-Optional -Name "vulture" -Script { Invoke-External -File "uv" -Args @("run", "vulture", ".") }
				Invoke-Optional -Name "ruff" -Script { Invoke-External -File "uv" -Args @("run", "ruff", "check") }
				Invoke-Optional -Name "ty" -Script { Invoke-External -File "uv" -Args @("run", "ty", "check") }
			} finally {
				Remove-UvEnvironment -EnvPath $envPath
			}
		}

		Invoke-Step -StepName "Packaging (source)" -Script {
			Write-Log "=== Packaging (source) ==="
			$envPath = New-UvEnvironment -PythonVersion "3.13" -EnvName ".venv-packaging-sources"
			try {
				if (Test-Path -Path "uv.lock") {
					Sync-ProjectDependencies -NoBuildIsolationPackageWxPython -UseLocked
				} else {
					Sync-ProjectDependencies -NoBuildIsolationPackageWxPython
				}
				Invoke-External -File "uv" -Args @("build")
			} finally {
				Remove-UvEnvironment -EnvPath $envPath
			}
		}

		Invoke-Step -StepName "Packaging (Windows binaries)" -Script {
			Write-Log "=== Packaging (Windows binaries) ==="
			$env:CYTHONIZE = "True"

			$envPath = New-UvEnvironment -PythonVersion "3.13" -EnvName ".venv-packaging-binaries"
			try {
				if (Test-Path -Path "uv.lock") {
					Invoke-External -File "uv" -Args @("sync", "--locked", "--dev", "--all-extras")
				} else {
					Invoke-External -File "uv" -Args @("sync", "--dev", "--all-extras")
				}
				Invoke-External -File "uv" -Args @("build")
			} finally {
				Remove-UvEnvironment -EnvPath $envPath
			}
		}

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

