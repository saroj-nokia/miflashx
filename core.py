import os
import subprocess
import sys
import time
from utils import log_message, get_os # Import necessary functions from utils

class FlashingCore:
    def __init__(self, rom_path, adb_path, fastboot_path, flash_mode, log_callback=None):
        """
        Initializes the FlashingCore with ROM path, ADB/Fastboot paths,
        the selected flash mode, and an optional callback for logging progress.
        """
        self.rom_path = rom_path
        self.adb_path = adb_path
        self.fastboot_path = fastboot_path
        self.flash_mode = flash_mode # Store the selected flash mode
        self.log_callback = log_callback if log_callback else self._default_log_callback
        self.current_os = get_os()

        log_message('info', f"FlashingCore initialized with ROM: {self.rom_path}, Mode: {self.flash_mode}")
        log_message('info', f"ADB Path: {self.adb_path}, Fastboot Path: {self.fastboot_path}")
        log_message('info', f"Operating System: {self.current_os}")

    def _default_log_callback(self, message):
        """Default log callback if none is provided."""
        print(message) # Fallback to print if no GUI callback is set

    def _execute_command(self, command, cwd=None, shell=False):
        """
        Executes a shell command and logs its output.
        Returns True on success, False on failure.
        """
        command_str = ' '.join(command) if isinstance(command, list) else command
        self.log_callback(f"Executing command: {command_str}")
        log_message('debug', f"Executing: {command_str} in CWD: {cwd}")

        try:
            # Use subprocess.Popen for real-time output
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, # Redirect stderr to stdout
                text=True, # Decode stdout/stderr as text
                shell=shell # Use shell if command needs shell features (like wildcards)
            )

            for line in iter(process.stdout.readline, ''):
                self.log_callback(line.strip()) # Emit each line of output
                log_message('debug', f"CMD Output: {line.strip()}")

            process.stdout.close()
            return_code = process.wait()

            if return_code != 0:
                self.log_callback(f"Command failed with exit code {return_code}")
                log_message('error', f"Command '{command_str}' failed with exit code {return_code}")
                return False
            else:
                self.log_callback(f"Command completed successfully.")
                log_message('info', f"Command '{command_str}' completed successfully.")
                return True

        except FileNotFoundError:
            self.log_callback(f"Error: Command '{command[0]}' not found. Is it in PATH or correctly specified?")
            log_message('error', f"Command '{command[0]}' not found.")
            return False
        except Exception as e:
            self.log_callback(f"An unexpected error occurred: {e}")
            log_message('error', f"Error executing command '{command_str}': {e}")
            return False

    def flash_device(self):
        """
        Orchestrates the device flashing process.
        Finds the appropriate flash script (flash_all.sh or flash_all.bat)
        based on the selected flash_mode and executes it.
        """
        self.log_callback(f"Starting device flashing process with mode: {self.flash_mode}...")

        # Determine the base script name based on the selected flash mode
        script_base_name = ""
        if self.flash_mode == "flash_all":
            script_base_name = "flash_all"
        elif self.flash_mode == "flash_all_except_data_storage":
            script_base_name = "flash_all_except_data_storage"
        elif self.flash_mode == "flash_all_lock":
            script_base_name = "flash_all_lock"
        else:
            self.log_callback(f"Error: Unknown flash mode selected: {self.flash_mode}")
            log_message('error', f"Unknown flash mode: {self.flash_mode}")
            return False

        # Append appropriate extension based on OS
        if self.current_os == "win32":
            script_full_name = f"{script_base_name}.bat"
        elif self.current_os == "linux" or self.current_os == "darwin":
            script_full_name = f"{script_base_name}.sh"
        else:
            self.log_callback(f"Error: Unsupported operating system: {self.current_os}")
            log_message('error', f"Unsupported OS for flashing: {self.current_os}")
            return False

        # Construct path to the 'images' directory within the ROM folder
        images_dir = os.path.join(self.rom_path, "images")
        flash_script_path = None
        
        # Prioritize script in 'images' directory
        candidate_script_in_images = os.path.join(images_dir, script_full_name)
        if os.path.exists(candidate_script_in_images):
            flash_script_path = candidate_script_in_images
            self.log_callback(f"Found flash script in images directory: {flash_script_path}")
            log_message('info', f"Using script: {flash_script_path}")
        else:
            # Fallback: Check if the script is directly in the ROM root (less common for modern ROMs)
            candidate_script_in_root = os.path.join(self.rom_path, script_full_name)
            if os.path.exists(candidate_script_in_root):
                flash_script_path = candidate_script_in_root
                self.log_callback(f"Found flash script in ROM root: {flash_script_path}")
                log_message('info', f"Using script: {flash_script_path}")
            else:
                self.log_callback(f"Error: Flashing script '{script_full_name}' not found in '{images_dir}' or '{self.rom_path}'.")
                log_message('error', f"Flashing script '{script_full_name}' not found.")
                return False

        # Ensure the script is executable on Linux/macOS
        if self.current_os in ["linux", "darwin"]:
            try:
                os.chmod(flash_script_path, 0o755) # rwx for owner, rx for group/others
                self.log_callback(f"Set executable permissions for {flash_script_path}")
                log_message('info', f"Set executable permissions for {flash_script_path}")
            except Exception as e:
                self.log_callback(f"Warning: Could not set executable permissions for {flash_script_path}: {e}")
                log_message('warning', f"Failed to chmod {flash_script_path}: {e}")

        # Determine the working directory for the script execution
        # It's usually the directory containing the script, so it can find image files
        script_cwd = os.path.dirname(flash_script_path)

        # Execute the flashing script
        if self.current_os == "win32":
            # For .bat files on Windows, running with shell=True is generally the most reliable
            # as it correctly handles batch file syntax and environment variables like %~dp0
            command = [flash_script_path] # Just the script path
            return self._execute_command(command, cwd=script_cwd, shell=True)
        else: # Linux or macOS
            # For .sh scripts on Linux/macOS, run directly
            command = [flash_script_path] # Just the script path
            return self._execute_command(command, cwd=script_cwd, shell=False)

