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
PLATFORM_TOOLS_ZIP="platform-tools.zip"
TEMP_EXTRACT_DIR="temp_platform_tools_extract" # A new temporary directory for extraction

# Clean up previous 'platform-tools' directory if it exists to ensure a fresh start
if [ -d "platform-tools" ]; then
    echo "Cleaning up existing 'platform-tools' directory..."
    rm -rf platform-tools
fi
mkdir -p platform-tools # Recreate the clean target directory

# Create a temporary directory for extraction
mkdir -p "${TEMP_EXTRACT_DIR}"

# Download the zip file
echo "Downloading Android Platform Tools from ${PLATFORM_TOOLS_URL}..."
curl -L ${PLATFORM_TOOLS_URL} -o "${PLATFORM_TOOLS_ZIP}"

# Unzip the contents into the temporary directory
echo "Extracting ${PLATFORM_TOOLS_ZIP} to ${TEMP_EXTRACT_DIR}..."
unzip -q "${PLATFORM_TOOLS_ZIP}" -d "${TEMP_EXTRACT_DIR}"

# The zip usually extracts to a nested 'temp_platform_tools_extract/platform-tools/' directory.
# Move contents from the nested directory to the final 'platform-tools/' directory.
# Check if the nested directory exists before moving
if [ -d "${TEMP_EXTRACT_DIR}/platform-tools" ]; then
    echo "Moving extracted tools from nested directory to final 'platform-tools' directory..."
    mv "${TEMP_EXTRACT_DIR}/platform-tools"/* platform-tools/
else
    echo "Warning: Nested 'platform-tools' directory not found in temporary extraction. Assuming flat structure."
    mv "${TEMP_EXTRACT_DIR}"/* platform-tools/ # Fallback for unexpected zip structure
fi

# Clean up temporary extraction directory and zip file
echo "Cleaning up temporary files..."
rm -rf "${TEMP_EXTRACT_DIR}"
rm -f "${PLATFORM_TOOLS_ZIP}"

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

echo "Build complete. Your executable is located in: dist/MiFlashX"
echo "To run: ./dist/MiFlashX"
echo "To make executable: chmod +x dist/MiFlashX"
