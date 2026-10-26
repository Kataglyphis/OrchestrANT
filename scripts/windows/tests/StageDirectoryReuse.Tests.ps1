#requires -Version 7.0

# This checks staging-root cleanup; it does not build wheels or run the arm64 test tree.
Import-Module (Join-Path $PSScriptRoot '..\..\..\third_party\ANTfrastructure\windows\scripts\tests\WindowsStageDirectory.Fixture.psm1') -ErrorAction Stop

Describe 'Arm64 Python test stage directory reuse' {
    $module = Join-Path $PSScriptRoot '..\..\..\third_party\ANTfrastructure\windows\scripts\modules\WindowsPythonApp.Common.psm1'
    Import-Module $module -DisableNameChecking

    It 'uses the shared empty-directory operation before copying payloads' {
        Assert-StageResetBeforeCopy -ScriptPath (Join-Path $PSScriptRoot '..\Stage-Arm64Tests.ps1') `
            -ResetCommand Initialize-PythonAppDirectory -PathArgument '$out'
    }

    It 'removes stale stage bytes and leaves a usable directory' {
        Assert-StageDirectoryCleared -TestRoot $TestDrive -RunReset {
            param($path) Initialize-PythonAppDirectory -Path $path -Empty
        }
    }

    It 'propagates a failed stage-root allocation' {
        Assert-StageDirectoryAllocationFailure -TestRoot $TestDrive -RunReset {
            param($path) Initialize-PythonAppDirectory -Path $path -Empty
        }
    }
}
