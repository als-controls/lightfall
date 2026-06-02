# Build Lightfall native application using Briefcase
# This script orchestrates the full build: wheels -> create -> build
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$NcsDir = Split-Path -Parent $ScriptDir

Push-Location $NcsDir
try {
    Write-Host "=========================================="
    Write-Host "Lightfall Briefcase Build"
    Write-Host "=========================================="
    Write-Host ""

    # Step 1: Build sub-project wheels
    Write-Host "Step 1: Building sub-project wheels..."
    Write-Host "------------------------------------------"
    & "$ScriptDir\build-wheels.ps1"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    # Step 2: Set up pip to find local wheels
    $DistDir = Join-Path $NcsDir "dist"
    $env:PIP_FIND_LINKS = $DistDir
    Write-Host ""
    Write-Host "Step 2: Configured PIP_FIND_LINKS=$env:PIP_FIND_LINKS"

    # Step 3: Create Briefcase scaffold
    Write-Host ""
    Write-Host "Step 3: Creating Briefcase scaffold..."
    Write-Host "------------------------------------------"
    briefcase create --no-input
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    # Step 4: Build the application
    Write-Host ""
    Write-Host "Step 4: Building application..."
    Write-Host "------------------------------------------"
    briefcase build --no-input
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host ""
    Write-Host "=========================================="
    Write-Host "Build complete!"
    Write-Host "=========================================="
    Write-Host ""
    Write-Host "To run the application:"
    Write-Host "  briefcase run"
    Write-Host ""
    Write-Host "To package for distribution:"
    Write-Host "  briefcase package"
    Write-Host ""
}
finally {
    Pop-Location
}
