Param(
    [string]$ClangVersion  = '21.1.1'
)

Write-Host "=== Installing build dependencies on Windows ==="

# Enable verbose logging
$ErrorActionPreference = 'Stop'

# Install LLVM/Clang via Chocolatey
Write-Host "Installing LLVM/Clang $ClangVersion..."
winget install --accept-source-agreements --accept-package-agreements --id=LLVM.LLVM -v $ClangVersion -e
# Get and install ninja
Write-Host "Installing Ninja via winget..."
winget install --accept-source-agreements --accept-package-agreements --id=Ninja-build.Ninja  -e
# Install Astral UV
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
Write-Host "=== Dependency installation completed ==="
