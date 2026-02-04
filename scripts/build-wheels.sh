#!/bin/bash
# Build wheels for sub-projects (epics-pyside, pyside-claude)
# These wheels are used by Briefcase via --find-links
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NCS_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$NCS_DIR/dist"

# Find the parent directory containing sub-projects
# Works from both main checkout and worktrees
if [ -d "$NCS_DIR/../epics-pyside" ]; then
    # Normal checkout: ncs/ncs/../epics-pyside -> ncs/epics-pyside
    PARENT_DIR="$(cd "$NCS_DIR/.." && pwd)"
elif [ -d "$NCS_DIR/../../../epics-pyside" ]; then
    # Worktree: ncs/ncs/.worktrees/branch/../../../epics-pyside -> ncs/epics-pyside
    PARENT_DIR="$(cd "$NCS_DIR/../../.." && pwd)"
else
    echo "Error: Cannot find sub-projects directory"
    echo "Expected epics-pyside at $NCS_DIR/../epics-pyside"
    echo "  or $NCS_DIR/../../../epics-pyside (for worktrees)"
    exit 1
fi

echo "Building wheels for sub-projects..."
echo "  NCS_DIR: $NCS_DIR"
echo "  PARENT_DIR: $PARENT_DIR"
echo "  DIST_DIR: $DIST_DIR"

mkdir -p "$DIST_DIR"

# Build epics-pyside wheel
if [ -d "$PARENT_DIR/epics-pyside" ]; then
    echo ""
    echo "Building epics-pyside..."
    pip wheel "$PARENT_DIR/epics-pyside" -w "$DIST_DIR" --no-deps
else
    echo "Warning: epics-pyside not found at $PARENT_DIR/epics-pyside"
fi

# Build pyside-claude wheel
if [ -d "$PARENT_DIR/pyside-claude" ]; then
    echo ""
    echo "Building pyside-claude..."
    pip wheel "$PARENT_DIR/pyside-claude" -w "$DIST_DIR" --no-deps
else
    echo "Warning: pyside-claude not found at $PARENT_DIR/pyside-claude"
fi

echo ""
echo "Wheels built in $DIST_DIR:"
ls -la "$DIST_DIR"/*.whl 2>/dev/null || echo "  (no wheels found)"
