#requires -Version 7.0
using module ./WindowsSuite.Fixture.psm1
# The GPU candidate must prove that its bundled model can also run on CPU.
$repo, $fixture = Initialize-TestSuite -FixturePrefix 'directml-cpu-report-'
$bundle = Join-Path $fixture 'bundle[fixture]'
$module = New-FixtureDirectory -RelativePath 'bundle[fixture]/runtime/onnxruntime'
$model = Join-Path $bundle 'model.onnx'
[IO.File]::WriteAllText($model, 'model')
$checker = Join-Path $repo 'scripts/windows/Test-DirectMLCpuReport.ps1'
function Write-Fixture([object[]]$Providers = @('CPUExecutionProvider'), [string]$RuntimePath = $module, [string]$ModelPath = $model, [bool]$Ok = $true) {
    @{ self_test = @{ ok = $Ok; providers = $Providers; onnxruntime_module = $RuntimePath; model = $ModelPath } } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $bundle 'bundle.json') -Encoding utf8
}
Invoke-TestSuite -ExpectedCount 5 -Body {
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
}
