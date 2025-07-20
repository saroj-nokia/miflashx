# miflashx/modify_spec.py
import re
import os
import sys

def modify_spec_file(spec_file_path, project_root_path):
    """
    Modifies the PyInstaller .spec file to explicitly add the project root
    to the Analysis pathex, ensuring local modules are found.
    """
    print(f"Modifying {spec_file_path} to explicitly add project root ({project_root_path}) to pathex...")

    try:
        with open(spec_file_path, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: .spec file not found at {spec_file_path}")
        sys.exit(1)

    # Pattern to find the Analysis call and its arguments
    # This pattern is robust to whitespace and captures the content inside pathex=[]
    pattern = re.compile(r"(a = Analysis\(\s*\[.*?\](?:,\s*.*?)*,\s*pathex=\[)(.*?\])")

    def replace_pathex(match):
        existing_pathex_str = match.group(2)
        
        # Safely parse the existing pathex list
        # We strip both single and double quotes separately, which is robust
        existing_paths = [
            p.strip().strip("'").strip('"') # Strip single quotes, then double quotes
            for p in existing_pathex_str.strip('[]').split(',')
            if p.strip() # Ensure no empty strings from split
        ]

        # Ensure the project_root is not already in the list to avoid duplicates
        if project_root_path not in existing_paths:
            existing_paths.insert(0, project_root_path) # Add project_root at the beginning

        # Reconstruct the pathex string with single quotes around each path
        new_pathex_content = ', '.join([f"'{p}'" for p in existing_paths])
        
        return f"{match.group(1)}{new_pathex_content}]"

    # Perform the replacement on the content
    new_content = pattern.sub(replace_pathex, content, 1) # Only replace the first occurrence

    # Fallback if the primary pattern matching fails (e.g., 'pathex' argument is missing entirely)
    if new_content == content:
        print("Warning: Could not find or modify 'pathex' in .spec file using primary pattern. Attempting to append.")
        # Pattern to find the Analysis call and insert 'pathex' before its closing parenthesis
        analysis_pattern = re.compile(r"(a = Analysis\(\s*\[.*?\](?:,\s*\S+?)*)(\))", re.DOTALL)
        def append_pathex_if_missing(match):
            # Only append if 'pathex=' was genuinely not found in the original match group
            if "pathex=" not in match.group(0):
                return f"{match.group(1)}, pathex=['{project_root_path}']{match.group(2)}"
            return match.group(0) # Return original if pathex was already there (shouldn't happen if primary failed)
        new_content = analysis_pattern.sub(append_pathex_if_missing, content, 1)
        if new_content == content:
            print("Error: Failed to modify .spec file. 'pathex' could not be found or appended. Manual inspection needed.")
            sys.exit(1) # Exit with error if modification failed

    # Write the modified content back to the .spec file
    with open(spec_file_path, 'w') as f:
        f.write(new_content)

    print(f"Successfully modified {spec_file_path}.")

if __name__ == "__main__":
    # The script expects two command-line arguments:
    # 1. The path to the .spec file
    # 2. The absolute path to the project root directory
    if len(sys.argv) != 3:
        print("Usage: python modify_spec.py <spec_file_path> <project_root_path>")
        sys.exit(1)
    
    spec_file = sys.argv[1]
    project_root = sys.argv[2]
    modify_spec_file(spec_file, project_root)
