import os
import subprocess
import sys
import time
from utils import log_message, get_os # Import necessary functions from utils

class FlashingCore:
    def __init__(self, rom_path, adb_path, fastboot_path, log_callback=None):
        """
        Initializes the FlashingCore with ROM path, ADB/Fastboot paths,
        and an optional callback for logging progress.
        """
        self.rom_path = rom_path
        self.adb_path = adb_path
        self.fastboot_path = fastboot_path
        self.log_callback = log_callback if log_callback else self._default_log_callback
        self.current_os = get_os()

        log_message('info', f"FlashingCore initialized with ROM: {self.rom_path}")
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
        and executes it.
        """
        self.log_callback("Starting device flashing process...")

        # Construct path to the 'images' directory within the ROM folder
        # Fastboot ROMs usually have a structure like:
        # ROM_FOLDER/
        #   images/
        #     flash_all.sh (or .bat)
        #     ... various .img files
        
        # Check for the 'images' subdirectory first, as scripts are often there
        images_dir = os.path.join(self.rom_path, "images")
        flash_script_path = None
        script_name = ""
        
        if self.current_os == "win32":
            script_name = "flash_all.bat"
        elif self.current_os == "linux" or self.current_os == "darwin":
            script_name = "flash_all.sh"
        else:
            self.log_callback(f"Error: Unsupported operating system: {self.current_os}")
            log_message('error', f"Unsupported OS for flashing: {self.current_os}")
            return False

        # Prioritize script in 'images' directory
        candidate_script_in_images = os.path.join(images_dir, script_name)
        if os.path.exists(candidate_script_in_images):
            flash_script_path = candidate_script_in_images
            self.log_callback(f"Found flash script in images directory: {flash_script_path}")
            log_message('info', f"Using script: {flash_script_path}")
        else:
            # Fallback: Check if the script is directly in the ROM root (less common for modern ROMs)
            candidate_script_in_root = os.path.join(self.rom_path, script_name)
            if os.path.exists(candidate_script_in_root):
                flash_script_path = candidate_script_in_root
                self.log_callback(f"Found flash script in ROM root: {flash_script_path}")
                log_message('info', f"Using script: {flash_script_path}")
            else:
                self.log_callback(f"Error: Flashing script '{script_name}' not found in '{images_dir}' or '{self.rom_path}'.")
                log_message('error', f"Flashing script '{script_name}' not found.")
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
        # For .sh scripts, we run them directly. For .bat, we use 'cmd /c' on Windows.
        if self.current_os == "win32":
            # On Windows, we need to run batch files via cmd.exe
            command = ["cmd.exe", "/c", script_name]
            # When using shell=True or cmd /c, the script_name needs to be just the name,
            # and cwd handles the directory.
            # However, since we're using Popen with a list of commands, it's safer
            # to provide the full path to the script and let the shell handle it.
            # Let's try running it directly with its full path and shell=True for bat files
            # as they often rely on shell features.
            return self._execute_command([flash_script_path], cwd=script_cwd, shell=True)
        else: # Linux or macOS
            # On Linux/macOS, we run shell scripts directly
            return self._execute_command([flash_script_path], cwd=script_cwd, shell=False)
