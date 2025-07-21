#!/bin/bash
# This script is used to build the MiFlashX executable for Fedora using PyInstaller.

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Starting MiFlashX Fedora build process..."

# Navigate to the root of your project (one level up from build_scripts)
PROJECT_ROOT="$(dirname "$0")/.."
cd "${PROJECT_ROOT}"

# Ensure Python virtual environment is activated if you use one
# source venv/bin/activate # Uncomment if you're using a virtual environment

# Install system-level dependencies for graphics (important for PyQt6 on Fedora)
# These packages provide libEGL.so.1, libGL.so.1, and other OpenGL libraries.
echo "Installing system-level graphics dependencies for the build environment (Fedora)..."
sudo dnf install -y \
    mesa-libGL-devel \
    mesa-libEGL-devel \
    libxkbcommon-x11 \
    libxcb-devel \
    libX11-devel \
    libXau-devel \
    libXdmcp-devel \
    # The following libxcb-*devel packages might be included in libxcb-devel
    # or have slightly different names. Let's try the most common ones first.
    # If errors persist, we might need to be more specific or find Fedora equivalents.
    # Removed specific libxcb-*-devel as they might be covered by libxcb-devel
    # or have different naming conventions on Fedora 42.
    # If you still get 'No match for argument' for other XCB libs, you might need
    # to find their exact Fedora package names (e.g., 'libxcb-util-devel' vs 'xcb-util-devel').
    mesa-dri-drivers \
    mesa-vulkan-drivers \
    # Adding some common Qt dependencies often needed on Fedora
    qt6-qtbase-devel \
    qt6-qtwayland-devel # If you use Wayland, otherwise not strictly necessary for XCB

# Install PyInstaller, PyQt6, Pillow if they are not already installed
echo "Installing/updating Python dependencies..."
pip install pyinstaller PyQt6 Pillow

# Remove previous build artifacts to ensure a clean slate
echo "Cleaning up previous build artifacts..."
rm -rf build dist MiFlashX.spec # Remove build directory, dist directory, and the .spec file

# --- Generate Application Icon ---
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

# --- STEP 1: Generate initial .spec file using pyi-makespec ---
# This command only generates the spec file, it does NOT build the executable.
echo "Generating initial PyInstaller .spec file..."
pyi-makespec \
             --onefile \
             --windowed \
             --name MiFlashX \
             --icon assets/icon.png \
             --add-data "assets:assets" \
             --add-data "platform-tools:platform-tools" \
             --hidden-import=utils \
             --hidden-import=core \
             --hidden-import=gui \
             --collect-submodules PyQt6.QtXcbQpa \
             --collect-data PyQt6.Qt \
             --specpath . \
             main.py

# --- STEP 2: Modify the .spec file using the standalone Python script ---
echo "Modifying MiFlashX.spec to explicitly add project root to pathex and enable debug imports..."
SPEC_FILE="MiFlashX.spec"
# Call the new Python script to modify the spec file, passing the spec file path and project root
python modify_spec.py "${SPEC_FILE}" "${PROJECT_ROOT}"

# --- INSPECTION STEP: Print the modified .spec file content ---
echo "--- Contents of modified ${SPEC_FILE} ---"
cat "${SPEC_FILE}"
echo "-----------------------------------------"

# --- STEP 3: Build using the MODIFIED .spec file ---
echo "Starting PyInstaller build using the modified .spec file..."
pyinstaller "${SPEC_FILE}"

echo "Build complete. Your executable is located in: dist/MiFlashX"
echo "To run: ./dist/MiFlashX"
echo "To make executable: chmod +x dist/MiFlashX"

# --- INSPECTION STEP: List contents of the build directory ---
echo "--- Contents of build/MiFlashX/ (where modules are unpacked) ---"
if [ -d "build/MiFlashX/" ]; then
    ls -R build/MiFlashX/
else
    echo "Build directory 'build/MiFlashX/' not found. Build might have failed earlier."
fi
echo "-------------------------------------------------------------"

echo "Build process completed. Please manually test the executable in dist/MiFlashX."
