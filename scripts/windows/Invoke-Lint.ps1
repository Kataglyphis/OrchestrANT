# Copyright (c) 2025 Kataglyphis
# SPDX-License-Identifier: MIT

#requires -Version 7.0

<#
.SYNOPSIS
    This repo's PowerShell lint gate: the hub's Invoke-Lint.ps1 over scripts/windows.

.DESCRIPTION
    A wrapper around ANTfrastructure's windows/scripts/Invoke-Lint.ps1, which
    owns the three passes (mandatory parse, AST traps, PSScriptAnalyzer) and the
    ruleset in windows/PSScriptAnalyzerSettings.psd1. The ruleset is consumed BY
    REFERENCE: the hub script anchors it to its own $PSScriptRoot, so nothing is
    copied here and nothing can drift.

    This wrapper exists for the reason scripts/linux/run-lint-gates.sh exists:
    the CI step and the dev-box command must be the same string, or the gate
    that blocks a merge cannot be reproduced locally.

    -Path is not optional. Omitted, the hub script lints the HUB's own trees
    (windows/ and shared/windows/) out of this repo's checkout and reports green
    over a tree this repo does not own -- the same wrong-root failure the Linux
    aggregator refuses to infer its way into.

    -FailOnAnalyzer is not optional either. Without it the analyzer pass prints
    its findings and the script still exits 0.

.EXAMPLE
    pwsh -File scripts/windows/Invoke-Lint.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$gate = Join-Path $repoRoot 'third_party\ANTfrastructure\windows\scripts\Invoke-Lint.ps1'
if (-not (Test-Path -LiteralPath $gate)) {
    throw "Missing $gate - run: git submodule update --init --recursive third_party/ANTfrastructure"
}

& $gate -Path (Join-Path $repoRoot 'scripts\windows') -FailOnAnalyzer
exit $LASTEXITCODE
