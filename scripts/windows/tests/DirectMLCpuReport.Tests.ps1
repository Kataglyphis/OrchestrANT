#requires -Version 7.0
# The GPU candidate must prove that its bundled model can also run on CPU.
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).ProviderPath
$hub = if ($env:ANTFRASTRUCTURE_DIR) { $env:ANTFRASTRUCTURE_DIR } else { Join-Path $repo 'third_party/ANTfrastructure' }
Import-Module (Join-Path $hub 'windows/scripts/tests/TestHarness.psm1') -DisableNameChecking -ErrorAction Stop
$fixture = New-TestDir -Prefix 'directml-cpu-report-'
$bundle = Join-Path $fixture 'bundle[fixture]'
$module = Join-Path $bundle 'runtime/onnxruntime'
$model = Join-Path $bundle 'model.onnx'
$null = New-Item -ItemType Directory -Path $module -Force
[IO.File]::WriteAllText($model, 'model')
$checker = Join-Path $repo 'scripts/windows/Test-DirectMLCpuReport.ps1'
function Write-Fixture([object[]]$Providers = @('CPUExecutionProvider'), [string]$RuntimePath = $module, [string]$ModelPath = $model, [bool]$Ok = $true) {
    @{ self_test = @{ ok = $Ok; providers = $Providers; onnxruntime_module = $RuntimePath; model = $ModelPath } } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $bundle 'bundle.json') -Encoding utf8
}
try {
    Describe 'DirectML bundle CPU inference report' {
        It 'accepts CPU-only inference using the bundled runtime and model' {
            Write-Fixture
            & $checker -Bundle $bundle
        }
        It 'rejects an unsuccessful self-test' {
            Write-Fixture -Ok $false
            Assert-Throws { & $checker -Bundle $bundle }
        }
        It 'rejects GPU and mixed provider lists' {
            foreach ($providers in @(@('DmlExecutionProvider'), @('CPUExecutionProvider', 'DmlExecutionProvider'))) {
                Write-Fixture -Providers $providers
                Assert-Throws { & $checker -Bundle $bundle }
            }
        }
        It 'rejects missing runtime and model paths' {
            Write-Fixture -RuntimePath ''
            Assert-Throws { & $checker -Bundle $bundle }
            Write-Fixture -ModelPath ''
            Assert-Throws { & $checker -Bundle $bundle }
        }
        It 'rejects runtime and model paths outside the bundle' {
            Write-Fixture -RuntimePath $fixture
            Assert-Throws { & $checker -Bundle $bundle }
            Write-Fixture -ModelPath $fixture
            Assert-Throws { & $checker -Bundle $bundle }
        }
    }
    $results = @(Get-TestResult)
    if ($results.Count -ne 5 -or @($results | Where-Object { -not $_.Ok }).Count) { exit 1 }
} finally {
    Remove-TestDir -Path $fixture
}
