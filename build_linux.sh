#!/bin/bash
# This script is used to build the MiFlashX executable for Linux using PyInstaller.

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Starting MiFlashX Linux build process..."

# Navigate to the root of your project (one level up from build_scripts)
cd "$(dirname "$0")/.."

# Ensure Python virtual environment is activated if you use one
# source venv/bin/activate # Uncomment if you're using a virtual environment

# Install PyInstaller and PyQt6 if they are not already installed
echo "Installing/updating Python dependencies..."
pip install pyinstaller PyQt6

# Remove previous build artifacts to ensure a clean build
echo "Cleaning up previous build artifacts..."
rm -rf build dist MiFlashX.spec  # Remove build directory, dist directory, and the .spec file

# Create a temporary directory to store the platform-tools binaries for bundling
echo "Preparing platform-tools for bundling..."
mkdir -p platform-tools-temp

# Copy only the necessary Linux platform-tools binaries
# Adjust these paths if your platform-tools structure is different
if [ -f "platform-tools/adb" ]; then
    cp -f platform-tools/adb platform-tools-temp/
    echo "Copied platform-tools/adb"
else
    echo "Warning: platform-tools/adb not found. Build may fail or function incorrectly."
fi

if [ -f "platform-tools/fastboot" ]; then
    cp -f platform-tools/fastboot platform-tools-temp/
    echo "Copied platform-tools/fastboot"
else
    echo "Warning: platform-tools/fastboot not found. Build may fail or function incorrectly."
fi

# You might need to copy other supporting libraries depending on your specific adb/fastboot versions.
# For most basic operations, adb and fastboot themselves are sufficient.

echo "Starting PyInstaller build..."
# Build with PyInstaller
# --noconfirm: Don't ask for confirmation to overwrite dist/build
# --onefile: Creates a single executable file
# --windowed: Hides the console window (important for GUI apps)
# --name MiFlashX: Sets the executable name in the dist/ directory
# --icon assets/icon.png: Specifies the application icon
# --add-data "source:destination_in_bundle": Adds data files to the bundle.
#   - "assets:assets": Copies the 'assets' directory to 'assets' inside the bundle.
#   - "platform-tools-temp:platform-tools": Copies our temporary 'platform-tools-temp'
#     directory into a 'platform-tools' directory inside the bundle's temporary execution environment (_MEIPASS).
#     This path is then used by `main.py` to add to the system's PATH for the app.
pyinstaller --noconfirm \
            --onefile \
            --windowed \
            --name MiFlashX \
            --icon assets/icon.png \
            --add-data "assets:assets" \
            --add-data "platform-tools-temp:platform-tools" \
            main.py

# Clean up temporary platform-tools directory
echo "Cleaning up temporary platform-tools directory..."
rm -rf platform-tools-temp

echo "Build complete. Your executable is located in: dist/MiFlashX"
echo "To run: ./dist/MiFlashX"
echo "To make executable: chmod +x dist/MiFlashX"
