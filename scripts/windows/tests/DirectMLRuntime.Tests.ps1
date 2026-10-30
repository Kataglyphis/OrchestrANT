#requires -Version 7.0
# A candidate's runtime bytes must survive wheel selection and ZIP packaging.
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).ProviderPath
$hub = if ($env:ANTFRASTRUCTURE_DIR) { $env:ANTFRASTRUCTURE_DIR } else { Join-Path $repo 'third_party/ANTfrastructure' }
Import-Module (Join-Path $hub 'windows/scripts/tests/TestHarness.psm1') -DisableNameChecking -ErrorAction Stop
$fixture = New-TestDir -Prefix 'directml-runtime-'
$root = Join-Path $fixture 'repo'
$moduleDir = Join-Path $root 'third_party/ANTfrastructure/windows/scripts/modules'
$linuxDir = Join-Path $root 'third_party/ANTfrastructure/linux/scripts'
$scriptDir = Join-Path $root 'scripts/windows'
$bundle = Join-Path $fixture 'bundle'
$ort = Join-Path $bundle 'runtime/Lib/site-packages/onnxruntime'
$capi = Join-Path $ort 'capi'
$monitorDir = Join-Path $bundle 'runtime/Lib/site-packages/orchestrant/yolo'
$monitorName = 'monitor.cp315-win_amd64.pyd'
$monitor = Join-Path $monitorDir $monitorName
$wheels = Join-Path $fixture 'wheels'
$packages = Join-Path $fixture 'packages'
$null = New-Item -ItemType Directory -Force -Path $moduleDir, $linuxDir, $scriptDir, $capi, $monitorDir, $wheels, $packages
foreach ($name in 'WindowsOrtLicense.Common', 'WindowsOrtProvenance.Common', 'WindowsMsiLicense.Common', 'WindowsPackageBinary.Common', 'WindowsPythonWheel.Common', 'WindowsScripts.Shared', 'WindowsTargetArch.Common') {
    Copy-Item -LiteralPath (Join-Path $hub "windows/scripts/modules/$name.psm1") -Destination $moduleDir
}
foreach ($name in 'verify_package_binary.py', 'verify_package_license.py') {
    Copy-Item -LiteralPath (Join-Path $hub "linux/scripts/$name") -Destination $linuxDir
}
$checker = Join-Path $scriptDir 'Test-DirectMLRuntime.ps1'
Copy-Item -LiteralPath (Join-Path $repo 'scripts/windows/Test-DirectMLRuntime.ps1') -Destination $checker
$names = @('onnxruntime.dll', 'onnxruntime_pybind11_state.pyd', 'DirectML.dll', 'onnxruntime_providers_shared.dll')
foreach ($name in $names) { [IO.File]::WriteAllText((Join-Path $capi $name), "original $name") }
foreach ($name in 'LICENSE', 'ThirdPartyNotices.txt') { [IO.File]::WriteAllText((Join-Path $ort $name), "original $name") }
$wheelMembers = [ordered]@{}
foreach ($name in $names) { $wheelMembers["onnxruntime/capi/$name"] = Join-Path $capi $name }
foreach ($name in 'LICENSE', 'ThirdPartyNotices.txt') { $wheelMembers["onnxruntime/$name"] = Join-Path $ort $name }
New-TestZipFile -Path (Join-Path $wheels 'onnxruntime-1.31.0-cp315-cp315-win_amd64.whl') -Member $wheelMembers -FileValues
[IO.File]::WriteAllText($monitor, 'original compiled monitor')
$appWheel = Join-Path $fixture 'orchestrant-1.0-cp315-cp315-win_amd64.whl'
New-TestZipFile -Path $appWheel -Member @{ "orchestrant/yolo/$monitorName" = $monitor } -FileValues
$stem = 'orchestrant-directml-nvidia-1.0-windows-x64'
$zip = Join-Path $packages "$stem.zip"
function Write-Package([string]$Wrong = '', [switch]$Duplicate, [switch]$ExtraProvider, [switch]$WrongMonitor) {
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    $members = [ordered]@{}
    foreach ($name in $names) {
        $path = Join-Path $capi $name
        if ($name -eq $Wrong) { $path = Join-Path $ort 'LICENSE' }
        $members["$stem/runtime/Lib/site-packages/onnxruntime/capi/$name"] = $path
    }
    if ($ExtraProvider) { $members["$stem/runtime/Lib/site-packages/onnxruntime/capi/onnxruntime_providers_cuda.dll"] = Join-Path $ort 'LICENSE' }
    $members["$stem/runtime/Lib/site-packages/orchestrant/yolo/$monitorName"] = if ($WrongMonitor) { Join-Path $ort 'LICENSE' } else { $monitor }
    $duplicates = if ($Duplicate) { @("$stem/runtime/Lib/site-packages/onnxruntime/capi/onnxruntime.dll") } else { @() }
    New-TestZipFile -Path $zip -Member $members -FileValues -DuplicateName $duplicates
}
function Invoke-Checker { & $checker -Bundle $bundle -WheelDir $wheels -AppWheel $appWheel -PackageDirectory $packages -InventoryOnly }
function Write-Msix([switch]$WrongMonitor) {
    $members = [ordered]@{}
    foreach ($name in $names) { $members["runtime/Lib/site-packages/onnxruntime/capi/$name"] = Join-Path $capi $name }
    $members["runtime/Lib/site-packages/orchestrant/yolo/$monitorName"] = if ($WrongMonitor) { Join-Path $ort 'LICENSE' } else { $monitor }
    $path = Join-Path $packages "$stem.msix"
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    New-TestZipFile -Path $path -Member $members -FileValues
}
function Write-Msi([switch]$WrongMonitor) {
    $root = Join-Path $fixture 'msi-work'
    $null = New-Item -ItemType Directory -Force -Path $root
    $items = @(); $index = 0
    foreach ($name in $names) { $items += @{ Id = "f$index"; Name = $name; Dir = 'capi'; Source = (Join-Path $capi $name) }; $index++ }
    $monitorSource = if ($WrongMonitor) { Join-Path $ort 'LICENSE' } else { $monitor }
    $items += @{ Id = "f$index"; Name = $monitorName; Dir = 'yolo'; Source = $monitorSource }
    $ddf = Join-Path $root 'payload.ddf'
    $cab = Join-Path $root 'payload.cab'
    if (Test-Path -LiteralPath $cab) { Remove-Item -LiteralPath $cab -Force }
    $lines = @('.OPTION EXPLICIT', '.Set CabinetNameTemplate=payload.cab', ".Set DiskDirectoryTemplate=$root", '.Set MaxDiskSize=0', '.Set Compress=ON')
    foreach ($item in $items) { $lines += "`"$($item.Source)`" $($item.Id)" }
    [IO.File]::WriteAllLines($ddf, $lines)
    Push-Location $root
    try { & makecab.exe /F $ddf | Out-Null; if ($LASTEXITCODE -ne 0) { throw 'Cannot create fixture cabinet' } }
    finally { Pop-Location }
    $path = Join-Path $packages "$stem.msi"
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $installer.OpenDatabase($path, 3)
    try {
        $sql = @(
            'CREATE TABLE `Directory` (`Directory` CHAR(72) NOT NULL, `Directory_Parent` CHAR(72), `DefaultDir` CHAR(255) NOT NULL PRIMARY KEY `Directory`)',
            'CREATE TABLE `Component` (`Component` CHAR(72) NOT NULL, `Directory_` CHAR(72) NOT NULL PRIMARY KEY `Component`)',
            'CREATE TABLE `File` (`File` CHAR(72) NOT NULL, `FileName` CHAR(255) NOT NULL, `Component_` CHAR(72) NOT NULL, `Sequence` SHORT NOT NULL PRIMARY KEY `File`)',
            'CREATE TABLE `Media` (`DiskId` SHORT NOT NULL, `LastSequence` SHORT NOT NULL, `Cabinet` CHAR(255) PRIMARY KEY `DiskId`)',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''TARGETDIR'', NULL, ''SourceDir'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''INSTALLFOLDER'', ''TARGETDIR'', ''OrchestrANT DirectML'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''runtime'', ''INSTALLFOLDER'', ''runtime'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''lib'', ''runtime'', ''Lib'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''site'', ''lib'', ''site-packages'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''ort'', ''site'', ''onnxruntime'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''capi'', ''ort'', ''capi'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''orch'', ''site'', ''orchestrant'')',
            'INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES (''yolo'', ''orch'', ''yolo'')',
            ('INSERT INTO `Media` (`DiskId`, `LastSequence`, `Cabinet`) VALUES (1, {0}, ''#payload.cab'')' -f $items.Count)
        )
        $sequence = 0
        foreach ($item in $items) {
            $sequence++
            $sql += 'INSERT INTO `Component` (`Component`, `Directory_`) VALUES (''c{0}'', ''{1}'')' -f $sequence, $item.Dir
            $sql += 'INSERT INTO `File` (`File`, `FileName`, `Component_`, `Sequence`) VALUES (''{0}'', ''{1}'', ''c{2}'', {2})' -f $item.Id, $item.Name, $sequence
        }
        foreach ($statement in $sql) {
            $view = $db.OpenView($statement)
            try { $null = $view.Execute(); $null = $view.Close() }
            finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view) }
        }
        $view = $db.OpenView('INSERT INTO `_Streams` (`Name`, `Data`) VALUES (?, ?)')
        $record = $installer.CreateRecord(2)
        try { $record.StringData(1) = 'payload.cab'; $record.SetStream(2, $cab); $null = $view.Execute($record); $null = $view.Close() }
        finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view) }
        $null = $db.Commit()
    } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer) }
}
function Invoke-FullChecker { & $checker -Bundle $bundle -WheelDir $wheels -AppWheel $appWheel -PackageDirectory $packages }
try {
    Describe 'DirectML finished runtime provenance' {
        It 'accepts a ZIP whose core, DirectML, bridge and Python extension match one wheel' {
            Write-Package
            Invoke-Checker
        }
        It 'rejects a modified runtime in the finished ZIP' {
            Write-Package -Wrong 'onnxruntime_providers_shared.dll'
            Assert-Throws { Invoke-Checker }
        }
        It 'rejects a modified compiled monitor in the finished ZIP' {
            Write-Package -WrongMonitor
            Assert-Throws { Invoke-Checker } -MessagePattern 'package binary verification failed'
        }
        It 'rejects a compiled monitor that differs from the application wheel' {
            Write-Package
            [IO.File]::WriteAllText($monitor, 'tampered monitor')
            try { Assert-Throws { Invoke-Checker } -MessagePattern 'package binary verification failed' }
            finally { [IO.File]::WriteAllText($monitor, 'original compiled monitor') }
        }
        It 'rejects an uncompiled or ambiguous monitor in the bundle' {
            Write-Package
            $extra = Join-Path $monitorDir 'monitor.py'
            [IO.File]::WriteAllText($extra, 'unexpected source')
            try { Assert-Throws { Invoke-Checker } -MessagePattern 'exactly one nonempty compiled' }
            finally { Remove-Item -LiteralPath $extra -Force }
        }
        It 'rejects a duplicate archive member' {
            Write-Package -Duplicate
            Assert-Throws { Invoke-Checker }
        }
        It 'rejects an extra provider added only to the finished ZIP' {
            Write-Package -ExtraProvider
            Assert-Throws { Invoke-Checker } -MessagePattern 'providers differ'
        }
        It 'rejects a changed DirectML runtime in the bundle' {
            Write-Package
            $file = Join-Path $capi 'DirectML.dll'
            [IO.File]::WriteAllText($file, 'changed directml')
            Assert-Throws { Invoke-Checker } -MessagePattern 'DirectML runtime differs'
            [IO.File]::WriteAllText($file, 'original DirectML.dll')
        }
        It 'rejects a provider absent from the matching wheel' {
            $file = Join-Path $capi 'onnxruntime_providers_cuda.dll'
            [IO.File]::WriteAllText($file, 'unproven cuda')
            Assert-Throws { Invoke-Checker } -MessagePattern 'matched 0 entries'
            Remove-Item -LiteralPath $file
        }
    }
    Describe 'DirectML compiled monitor in finished installers' {
        It 'accepts the monitor from the same wheel in ZIP, MSI and MSIX' {
            if (-not (Get-Command 7z -ErrorAction SilentlyContinue)) { $env:PATH = "$env:ProgramFiles\7-Zip;$env:PATH" }
            $null = Get-Command 7z, makecab.exe -ErrorAction Stop
            Write-Package; Write-Msix; Write-Msi
            Invoke-FullChecker
        }
        It 'rejects a changed compiled monitor in MSI' {
            Write-Msi -WrongMonitor
            Assert-Throws { Invoke-FullChecker } -MessagePattern 'package binary verification failed'
            Write-Msi
        }
        It 'rejects a changed compiled monitor in MSIX' {
            Write-Msix -WrongMonitor
            Assert-Throws { Invoke-FullChecker } -MessagePattern 'package binary verification failed'
        }
    }
    $results = @(Get-TestResult)
    if ($results.Count -ne 12 -or @($results | Where-Object { -not $_.Ok }).Count) { exit 1 }
} finally {
    Remove-TestDir -Path $fixture
}
