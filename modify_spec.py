# miflashx/modify_spec.py
import re
import os
import sys

def modify_spec_file(spec_file_path, project_root_path_arg):
    """
    Modifies the PyInstaller .spec file to explicitly add the project root
    to the Analysis pathex, ensuring local modules are found,
    and enables debug imports in the EXE block.
    """
    # Convert the passed project_root_path_arg to an absolute path immediately
    project_root_abs_path = os.path.abspath(project_root_path_arg)
    print(f"Modifying {spec_file_path} to explicitly add project root ({project_root_abs_path}) to pathex and enable debug imports...")

    try:
        with open(spec_file_path, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: .spec file not found at {spec_file_path}")
        sys.exit(1)

    # Pattern to find the Analysis call and its arguments for pathex
    pattern_pathex = re.compile(r"(a = Analysis\(\s*\[.*?\](?:,\s*.*?)*,\s*pathex=\[)(.*?\])")

    def replace_pathex(match):
        existing_pathex_str = match.group(2)
        
        existing_paths = [
            p.strip().strip("'").strip('"')
            for p in existing_pathex_str.strip('[]').split(',')
            if p.strip()
        ]

        if project_root_abs_path not in existing_paths:
            existing_paths.insert(0, project_root_abs_path)

        new_pathex_content = ', '.join([f"'{p}'" for p in existing_paths])
        
        return f"{match.group(1)}{new_pathex_content}]"

    new_content = pattern_pathex.sub(replace_pathex, content, 1)

    if new_content == content:
        print("Warning: Could not find or modify 'pathex' in .spec file using primary pattern. Attempting to append.")
        analysis_pattern = re.compile(r"(a = Analysis\(\s*\[.*?\](?:,\s*\S+?)*)(\))", re.DOTALL)
        def append_pathex_if_missing(match):
            if "pathex=" not in match.group(0):
                return f"{match.group(1)}, pathex=['{project_root_abs_path}']{match.group(2)}"
            return match.group(0)
        new_content = analysis_pattern.sub(append_pathex_if_missing, content, 1)
        if new_content == content:
            print("Error: Failed to modify .spec file. 'pathex' could not be found or appended. Manual inspection needed.")
            sys.exit(1)

    # --- NEW: Inject debug=True into the EXE block ---
    # Find the EXE call and insert 'debug=True'
    # This regex looks for 'exe = EXE(' and then tries to insert debug=True
    # before 'debug=False' or if 'debug' is missing.
    # It's safer to target the 'debug=False' line and replace it.
    exe_debug_pattern = re.compile(r"(exe = EXE\(\s*.*?,\s*debug=)False(,\s*.*?\))", re.DOTALL)
    new_content = exe_debug_pattern.sub(r"\1True\2", new_content, 1)

    # If debug=False wasn't found (e.g., debug line is missing or different),
    # try to insert it after 'name='
    if not exe_debug_pattern.search(content): # Check original content for debug=False
        exe_name_pattern = re.compile(r"(name='MiFlashX',)(\s*.*?bootloader_ignore_signals=False,)", re.DOTALL)
        new_content = exe_name_pattern.sub(r"\1\n    debug=True,\2", new_content, 1)
        print("Injected 'debug=True' after 'name=' in EXE block.")
    else:
        print("Replaced 'debug=False' with 'debug=True' in EXE block.")


    with open(spec_file_path, 'w') as f:
        f.write(new_content)

    print(f"Successfully modified {spec_file_path}.")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python modify_spec.py <spec_file_path> <project_root_path>")
        sys.exit(1)
    
    spec_file = sys.argv[1]
    project_root = sys.argv[2]
    modify_spec_file(spec_file, project_root)
