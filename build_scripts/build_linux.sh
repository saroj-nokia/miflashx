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

# Remove previous build artifacts
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

# --- Generate initial .spec file ---
echo "Generating initial PyInstaller .spec file..."
# Use pyi-makespec to generate the spec file without building immediately.
# We include all the flags here so they are written into the spec file.
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
            --specpath . \
            main.py

# --- NEW: Modify the .spec file using Python ---
echo "Modifying MiFlashX.spec to explicitly add project root to pathex..."
SPEC_FILE="MiFlashX.spec"
PYTHON_SCRIPT_TO_MODIFY_SPEC=$(cat <<EOF
import re
import os

spec_path = "${SPEC_FILE}"
project_root = os.path.abspath("${PROJECT_ROOT}") # Get absolute path for robustness

with open(spec_path, 'r') as f:
    content = f.read()

# Pattern to find the Analysis call and its arguments
# This pattern is more robust as it doesn't rely on specific whitespace around 'pathex=['
pattern = re.compile(r"(a = Analysis\(\s*\[.*?\]\s*,\s*.*?pathex=\[)(.*?\])")

def replace_pathex(match):
    # Get the existing pathex content
    existing_pathex = match.group(2)
    # Remove any existing project_root if it was somehow added before
    existing_pathex = existing_pathex.replace(f"'{project_root}', ", "").replace(f"'{project_root}'", "")
    existing_pathex = existing_pathex.strip('[]').strip() # Remove brackets and extra spaces

    # Construct the new pathex with project_root at the beginning
    if existing_pathex:
        new_pathex_content = f"'{project_root}', {existing_pathex}"
    else:
        new_pathex_content = f"'{project_root}'"

    return f"{match.group(1)}{new_pathex_content}]"

# Perform the replacement
new_content = pattern.sub(replace_pathex, content, 1) # Only replace the first occurrence

if new_content == content:
    print("Warning: Could not find or modify 'pathex' in .spec file. Manual inspection needed.")
    # Fallback if pattern matching fails, try appending if not found
    if "pathex=[" not in content:
        print("Attempting to append pathex if not found...")
        # Find the Analysis call and insert pathex before the closing parenthesis
        analysis_pattern = re.compile(r"(a = Analysis\(\s*\[.*?\](?:,\s*\S+?)*)(\))", re.DOTALL)
        def append_pathex(match):
            return f"{match.group(1)}, pathex=['{project_root}']{match.group(2)}"
        new_content = analysis_pattern.sub(append_pathex, content, 1)


with open(spec_path, 'w') as f:
    f.write(new_content)

print(f"Successfully modified {SPEC_FILE}.")
EOF
)
python -c "${PYTHON_SCRIPT_TO_MODIFY_SPEC}"

# --- Build using the modified .spec file ---
echo "Starting PyInstaller build using the modified .spec file..."
# Now, run PyInstaller using the generated and modified .spec file.
# All options are now contained within the .spec file.
pyinstaller "${SPEC_FILE}"

echo "Build complete. Your executable is located in: dist/MiFlashX"
echo "To run: ./dist/MiFlashX"
echo "To make executable: chmod +x dist/MiFlashX"
