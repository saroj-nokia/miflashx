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
pip install pyinstaller PyQt6 Pillow # Pillow is needed for generate_icon.py

# Remove previous build artifacts
echo "Cleaning up previous build artifacts..."
rm -rf build dist MiFlashX.spec  # Remove build directory, dist directory, and the .spec file

# --- NEW STEP: Generate Application Icon ---
# This ensures that 'assets/icon.png' exists before PyInstaller tries to bundle it.
echo "Generating application icon..."
python generate_icon.py

# --- Download and prepare Android Platform Tools ---
echo "Downloading and preparing Android Platform Tools..."
PLATFORM_TOOLS_URL="https://dl.google.com/android/repository/platform-tools-latest-linux.zip"

# Create the directory for platform-tools if it doesn't exist
mkdir -p platform-tools

# Download the zip file
echo "Downloading Android Platform Tools from ${PLATFORM_TOOLS_URL}..."
curl -L ${PLATFORM_TOOLS_URL} -o platform-tools.zip

# Unzip the contents into the platform-tools directory
echo "Extracting platform-tools.zip..."
unzip -q platform-tools.zip -d platform-tools/

# The zip usually extracts to a nested 'platform-tools/platform-tools/' directory.
# Move contents up one level and remove the nested directory.
if [ -d "platform-tools/platform-tools" ]; then
    mv platform-tools/platform-tools/* platform-tools/
    rmdir platform-tools/platform-tools
fi

# Ensure adb and fastboot binaries are executable
chmod +x platform-tools/adb platform-tools/fastboot
echo "Android Platform Tools prepared."

echo "Starting PyInstaller build..."
# Build with PyInstaller
# --noconfirm: Don't ask for confirmation to overwrite dist/build
# --onefile: Creates a single executable file
# --windowed: Hides the console window (important for GUI apps)
# --name MiFlashX: Sets the executable name in the dist/ directory
# --icon assets/icon.png: Specifies the application icon
# --add-data "source:destination_in_bundle": Adds data files to the bundle.
#   - "assets:assets": Copies the 'assets' directory to 'assets' inside the bundle.
#   - "platform-tools:platform-tools": Copies our platform-tools directory into the bundle.
# --hidden-import: Explicitly tells PyInstaller to include these modules.
#                  This is crucial for cases where auto-analysis might miss them.
pyinstaller --noconfirm \
            --onefile \
            --windowed \
            --name MiFlashX \
            --icon assets/icon.png \
            --add-data "assets:assets" \
            --add-data "platform-tools:platform-tools" \
            --hidden-import=utils \
            --hidden-import=core \
            --hidden-import=gui \
            main.py

# Clean up temporary platform-tools directory (optional, as they are now bundled)
# rm -rf platform-tools-temp # This line was for a temp dir, but we're now using the main platform-tools dir.
# If you want to delete the *original* platform-tools folder after bundling, uncomment and adjust:
# rm -rf platform-tools

echo "Build complete. Your executable is located in: dist/MiFlashX"
echo "To run: ./dist/MiFlashX"
echo "To make executable: chmod +x dist/MiFlashX"
