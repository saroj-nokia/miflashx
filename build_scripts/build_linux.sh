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
# Removed --noconfirm as it's not supported by pyi-makespec
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

# --- STEP 2: Modify the .spec file using a temporary Python script ---
echo "Modifying MiFlashX.spec to explicitly add project root to pathex..."
SPEC_FILE="MiFlashX.spec"
TEMP_PYTHON_SCRIPT="modify_spec_temp.py"

# Write the Python script content to a temporary file
cat > "${TEMP_PYTHON_SCRIPT}" <<EOF
import re
import os
import sys

spec_file = "${SPEC_FILE}"
project_root = sys.argv[1] # Get project_root from command line argument

with open(spec_file, 'r') as f:
    content = f.read()

# Pattern to find the Analysis call and its arguments
# This pattern is more robust as it doesn't rely on specific whitespace around 'pathex=['
# It captures the part before 'pathex=[' and the content inside 'pathex=[]'
pattern = re.compile(r"(a = Analysis\(\s*\[.*?\](?:,\s*.*?)*,\s*pathex=\[)(.*?\])")

def replace_pathex(match):
    # Get the existing pathex content (everything inside the brackets)
    existing_pathex_str = match.group(2)
    
    # Safely parse the existing pathex list
    existing_paths = [p.strip().strip(\"'\") for p in existing_pathex_str.strip('[]').split(',') if p.strip()]

    # Ensure the project_root is not already in the list
    if project_root not in existing_paths:
        existing_paths.insert(0, project_root) # Add project_root at the beginning

    # Reconstruct the pathex string
    new_pathex_content = ', '.join([f\"'{p}'\" for p in existing_paths])
    
    return f\"{match.group(1)}{new_pathex_content}]\"

# Perform the replacement
new_content = pattern.sub(replace_pathex, content, 1) # Only replace the first occurrence

# Fallback if pattern matching fails (e.g., pathex not found at all)
if new_content == content:
    print(\"Warning: Could not find or modify 'pathex' in .spec file. Attempting to append.\")
    # Find the Analysis call and insert pathex before the closing parenthesis
    analysis_pattern = re.compile(r\"(a = Analysis\(\s*\[.*?\](?:,\s*\S+?)*)(\))\", re.DOTALL)
    def append_pathex_if_missing(match):
        # Only append if pathex wasn't found in the original content
        if \"pathex=\" not in match.group(0):
            return f\"{match.group(1)}, pathex=['{project_root}']{match.group(2)}\"
        return match.group(0) # Return original if pathex was already there
    new_content = analysis_pattern.sub(append_pathex_if_missing, content, 1)
    if new_content == content:
        print(\"Error: Failed to modify .spec file. 'pathex' could not be found or appended.\")
        sys.exit(1) # Exit with error if modification failed

with open(spec_file, 'w') as f:
    f.write(new_content)

print(f\"Successfully modified {spec_file}.\")
EOF

# Execute the temporary Python script
python "${TEMP_PYTHON_SCRIPT}" "${PROJECT_ROOT}"

# Clean up the temporary Python script
rm "${TEMP_PYTHON_SCRIPT}"

# --- STEP 3: Build using the MODIFIED .spec file ---
echo "Starting PyInstaller build using the modified .spec file..."
# Now, run PyInstaller using the generated and modified .spec file.
# All options are now contained within the .spec file.
pyinstaller "${SPEC_FILE}"

echo "Build complete. Your executable is located in: dist/MiFlashX"
echo "To run: ./dist/MiFlashX"
echo "To make executable: chmod +x dist/MiFlashX"
