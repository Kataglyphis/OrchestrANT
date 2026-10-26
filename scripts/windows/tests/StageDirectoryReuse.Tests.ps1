#requires -Version 7.0

# This checks staging-root cleanup; it does not build wheels or run the arm64 test tree.
Describe 'Arm64 Python test stage directory reuse' {
    $module = Join-Path $PSScriptRoot '..\..\..\third_party\ANTfrastructure\windows\scripts\modules\WindowsPythonApp.Common.psm1'
    Import-Module $module -DisableNameChecking

    It 'uses the shared empty-directory operation before copying payloads' {
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '..\Stage-Arm64Tests.ps1'), [ref]$tokens, [ref]$errors)
        @($errors | Where-Object { $_ }).Count | Should Be 0
        $calls = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
        $reset = @($calls | Where-Object { $_.GetCommandName() -eq 'Initialize-PythonAppDirectory' })
        $reset.Count | Should Be 1
        ($reset[0].Extent.Text -match '-Path \$out -Empty') | Should Be $true
        $firstCopy = @($calls | Where-Object { $_.GetCommandName() -eq 'Copy-Item' } | Sort-Object { $_.Extent.StartOffset })[0]
        ($reset[0].Extent.StartOffset -lt $firstCopy.Extent.StartOffset) | Should Be $true
    }

    It 'removes stale stage bytes and leaves a usable directory' {
        $root = Join-Path $TestDrive 'stage'
        $null = New-Item -ItemType Directory -Path $root
        [IO.File]::WriteAllText((Join-Path $root 'stale.txt'), 'old')
        Initialize-PythonAppDirectory -Path $root -Empty
        (Test-Path -LiteralPath $root -PathType Container) | Should Be $true
        (Test-Path -LiteralPath (Join-Path $root 'stale.txt')) | Should Be $false
    }

    It 'propagates a failed stage-root allocation' {
        $target = Join-Path $TestDrive 'invalid<stage>'
        $message = try { Initialize-PythonAppDirectory -Path $target -Empty; '' } catch { $_.Exception.Message }
        [string]::IsNullOrWhiteSpace($message) | Should Be $false
        (Test-Path -LiteralPath $target) | Should Be $false
    }
}
