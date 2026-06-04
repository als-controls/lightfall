# Build wheels for sub-projects (epics-pyside, pyside-claude)
# These wheels are used by Briefcase via --find-links
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LightfallDir = Split-Path -Parent $ScriptDir
$DistDir = Join-Path $LightfallDir "dist"

# Find the parent directory containing sub-projects
# Works from both main checkout and worktrees
$ParentDir = $null

$NormalPath = Join-Path (Split-Path -Parent $LightfallDir) "epics-pyside"
$WorktreePath = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $LightfallDir))) "epics-pyside"

if (Test-Path $NormalPath) {
    # Normal checkout: ncs/ncs/../epics-pyside -> ncs/epics-pyside
    $ParentDir = Split-Path -Parent $LightfallDir
} elseif (Test-Path $WorktreePath) {
    # Worktree: ncs/ncs/.worktrees/branch/../../../epics-pyside -> ncs/epics-pyside
    $ParentDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $LightfallDir))
} else {
    Write-Error "Cannot find sub-projects directory"
    Write-Host "Expected epics-pyside at: $NormalPath"
    Write-Host "  or: $WorktreePath (for worktrees)"
    exit 1
}

Write-Host "Building wheels for sub-projects..."
Write-Host "  LIGHTFALL_DIR: $LightfallDir"
Write-Host "  PARENT_DIR: $ParentDir"
Write-Host "  DIST_DIR: $DistDir"

# Create dist directory if it doesn't exist
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir | Out-Null
}

# Build epics-pyside wheel
$EpicsPysidePath = Join-Path $ParentDir "epics-pyside"
if (Test-Path $EpicsPysidePath) {
    Write-Host ""
    Write-Host "Building epics-pyside..."
    pip wheel $EpicsPysidePath -w $DistDir --no-deps
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Warning "epics-pyside not found at $EpicsPysidePath"
}

# Build pyside-claude wheel
$PysideClaudePath = Join-Path $ParentDir "pyside-claude"
if (Test-Path $PysideClaudePath) {
    Write-Host ""
    Write-Host "Building pyside-claude..."
    pip wheel $PysideClaudePath -w $DistDir --no-deps
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Warning "pyside-claude not found at $PysideClaudePath"
}

Write-Host ""
Write-Host "Wheels built in ${DistDir}:"
$wheels = Get-ChildItem -Path $DistDir -Filter "*.whl" -ErrorAction SilentlyContinue
if ($wheels) {
    $wheels | ForEach-Object { Write-Host "  $($_.Name)" }
} else {
    Write-Host "  (no wheels found)"
}
