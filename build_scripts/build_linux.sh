#!/bin/bash
# This script is used to build the MiFlashX executable for Linux using PyInstaller.

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Starting MiFlashX Linux build process..."

# Navigate to the root of your project (one level up from build_scripts)
PROJECT_ROOT="$(dirname "$0")/.."
cd "${PROJECT_ROOT}"

# Ensure Python virtual environment is activated if you use one
# source venv/bin/activate # Uncomment if you're using a virtual environment

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
             --specpath . \
             main.py

# --- STEP 2: Modify the .spec file using the standalone Python script ---
echo "Modifying MiFlashX.spec to explicitly add project root to pathex..."
SPEC_FILE="MiFlashX.spec"
# Call the new Python script to modify the spec file, passing the spec file path and project root
python modify_spec.py "${SPEC_FILE}" "${PROJECT_ROOT}"

# --- STEP 3: Build using the MODIFIED .spec file ---
echo "Starting PyInstaller build using the modified .spec file..."
# Now, run PyInstaller using the generated and modified .spec file.
# All options are now contained within the .spec file.
pyinstaller "${SPEC_FILE}"

echo "Build complete. Your executable is located in: dist/MiFlashX"
echo "To run: ./dist/MiFlashX"
echo "To make executable: chmod +x dist/MiFlashX"

# --- NEW STEP: Test Run the Compiled Executable ---
echo "Attempting to run the compiled executable for a quick test..."
EXECUTABLE_PATH="./dist/MiFlashX"
TEST_LOG_FILE="test_run_output.log"

if [ -f "${EXECUTABLE_PATH}" ]; then
    chmod +x "${EXECUTABLE_PATH}" # Ensure it's executable
    echo "Running ${EXECUTABLE_PATH} and redirecting output to ${TEST_LOG_FILE}..."
    # Run in background with a timeout to prevent hanging, and capture output
    # `timeout` command is useful in CI environments
    timeout 10s "${EXECUTABLE_PATH}" > "${TEST_LOG_FILE}" 2>&1 &
    PID=$! # Get the process ID of the background process

    echo "Executable started with PID ${PID}. Waiting 5 seconds for it to initialize..."
    sleep 5 # Give it a few seconds to start up and potentially crash

    # Check if the process is still running
    if ps -p $PID > /dev/null; then
        echo "Executable appears to be running. Sending SIGTERM to gracefully stop it."
        kill $PID
        wait $PID || true # Wait for it to terminate, `|| true` prevents script from exiting if kill fails
        echo "Executable stopped."
        echo "Test run successful (application launched and terminated)."
        echo "Check '${TEST_LOG_FILE}' for any captured output."
    else
        echo "Executable terminated unexpectedly or failed to start."
        echo "Test run FAILED. Please check '${TEST_LOG_FILE}' for details."
        cat "${TEST_LOG_FILE}" # Print the log content to console for immediate debugging
        exit 1 # Fail the build script if the test run fails
    fi
else
    echo "Error: Compiled executable not found at ${EXECUTABLE_PATH}. Build likely failed."
    exit 1
fi

echo "Build and test process completed."
