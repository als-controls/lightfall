#!/bin/bash
# Build LUCID native application using Briefcase
# This script orchestrates the full build: wheels -> create -> build
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NCS_DIR="$(dirname "$SCRIPT_DIR")"

cd "$NCS_DIR"

echo "=========================================="
echo "LUCID Briefcase Build"
echo "=========================================="
echo ""

# Step 1: Build sub-project wheels
echo "Step 1: Building sub-project wheels..."
echo "------------------------------------------"
./scripts/build-wheels.sh

# Step 2: Set up pip to find local wheels
export PIP_FIND_LINKS="$NCS_DIR/dist"
echo ""
echo "Step 2: Configured PIP_FIND_LINKS=$PIP_FIND_LINKS"

# Step 3: Create Briefcase scaffold
echo ""
echo "Step 3: Creating Briefcase scaffold..."
echo "------------------------------------------"
briefcase create --no-input

# Step 4: Build the application
echo ""
echo "Step 4: Building application..."
echo "------------------------------------------"
briefcase build --no-input

echo ""
echo "=========================================="
echo "Build complete!"
echo "=========================================="
echo ""
echo "To run the application:"
echo "  briefcase run"
echo ""
echo "To package for distribution:"
echo "  briefcase package"
echo ""
