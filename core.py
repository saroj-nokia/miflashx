import os
import subprocess
import sys
import time
import re # Import regex for parsing output
import tarfile # For ROM extraction
import shutil # For rmtree in extract_rom
import zipfile # For nested ZIP extraction

from utils import log_message, get_os # Import necessary functions from utils

# Define FlashModes as a simple class for clarity, matching GUI's usage
class FlashModes:
    CLEAN_ALL = "clean_all"
    SAVE_USER_DATA = "except_storage" # Renamed to match GUI's "except_storage"
    LOCK_BOOTLOADER = "flash_all_lock" # Renamed to match GUI's "flash_all_lock"
    # Added for GUI's "except_data_storage"
    SAVE_DATA_AND_STORAGE = "except_data_storage"

class FlashingCore:
    def __init__(self, adb_path, fastboot_path, output_callback=None):
        """
        Initializes the FlashingCore with ADB/Fastboot paths,
        and an optional callback for logging progress to the GUI.
        """
        self.adb_path = adb_path
        self.fastboot_path = fastboot_path

        # Determine if output_callback is a Qt Signal or a regular function.
        # If it's a signal, we'll use .emit() on it.
        # Otherwise, fall back to log_message which is already handled by QTextEditLogHandler.
        if hasattr(output_callback, 'emit') and callable(output_callback.emit):
            self._emit_status = lambda msg: output_callback.emit(msg)
        else:
            # Fallback to log_message directly, which is already routed to GUI's QTextEdit
            self._emit_status = lambda msg: log_message('info', msg)

        self.current_os = get_os()

        log_message('info', f"FlashingCore initialized.")
        log_message('info', f"ADB Path: {self.adb_path}, Fastboot Path: {self.fastboot_path}")
        log_message('info', f"Operating System: {self.current_os}")

    def _execute_command(self, command, cwd=None, shell=False, capture_output=False):
        """
        Executes a shell command and logs its output.
        If capture_output is True, returns (True/False, list_of_output_lines)
        Else, returns (True/False, None)
        """
        command_str = ' '.join(command) if isinstance(command, list) else command
        self._emit_status(f"Executing command: {command_str}")
        log_message('debug', f"Executing: {command_str} in CWD: {cwd}")

        output_lines = [] # This will now collect both stdout and stderr if capture_output is True
        try:
            # IMPORTANT: Explicitly set env to inherit current environment
            # This helps replicate terminal behavior more closely.
            env = os.environ.copy()

            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, # Keep stderr separate for better debugging
                text=True, # Decode stdout/stderr as text
                shell=shell, # Use shell if command needs shell features (like wildcards)
                env=env # Pass the copied environment
            )

            # Read stdout and stderr concurrently
            # Use iter and functools.partial for non-blocking reads if needed for very large outputs
            # For fastboot getvar all, sequential read is generally fine.

            # Read stdout
            for line in iter(process.stdout.readline, ''):
                stripped_line = line.strip()
                if stripped_line:
                    if capture_output: # Only add to output_lines if we intend to capture for parsing
                        output_lines.append(stripped_line)
                    self._emit_status(stripped_line) # Emit to GUI log
                    log_message('debug', f"CMD STDOUT: {stripped_line}")

            # Read stderr
            for line in iter(process.stderr.readline, ''):
                stripped_line = line.strip()
                if stripped_line:
                    if capture_output: # Also add stderr to output_lines for parsing
                        output_lines.append(stripped_line)
                    self._emit_status(f"STDERR: {stripped_line}") # Emit stderr as warning
                    log_message('warning', f"CMD STDERR: {stripped_line}")

            process.stdout.close()
            process.stderr.close()
            return_code = process.wait()

            log_message('debug', f"Command '{command_str}' finished with exit code {return_code}")
            log_message('debug', f"Full Captured Output (STDOUT+STDERR): {output_lines}") # Log the combined output

            if return_code != 0:
                error_msg = f"Command failed with exit code {return_code}. Output: {' '.join(output_lines)}"
                self._emit_status(error_msg)
                log_message('error', f"Command '{command_str}' failed with exit code {return_code}")
                return False, output_lines if capture_output else None
            else:
                self._emit_status("Command completed successfully.")
                log_message('info', f"Command '{command_str}' completed successfully.")
                return True, output_lines if capture_output else None

        except FileNotFoundError:
            error_msg = f"Error: Command '{command[0]}' not found. Is it in PATH or correctly specified?"
            self._emit_status(error_msg)
            log_message('error', f"Command '{command[0]}' not found.")
            return False, output_lines if capture_output else None
        except Exception as e:
            error_msg = f"An unexpected error occurred: {e}"
            self._emit_status(error_msg)
            log_message('error', f"Error executing command '{command_str}': {e}")
            return False, output_lines if capture_output else None

    def detect_device(self):
        """
        Detects if a Fastboot device is connected.
        Returns the device serial number (string) if found, otherwise None.
        """
        if not self.fastboot_path:
            self._emit_status("Fastboot path not set. Cannot detect device.")
            return None

        # Ensure we're using the full path to fastboot
        command = [self.fastboot_path, "devices"]
        self._emit_status(f"Attempting to detect device with command: {' '.join(command)}")

        success, output = self._execute_command(command, capture_output=True)

        log_message('debug', f"Raw output from fastboot devices: {output}")

        if success and output:
            for line in output:
                # Fastboot output format: <serial_number>\tfastboot
                match = re.match(r'(\S+)\s+fastboot', line)
                if match:
                    serial = match.group(1)
                    self._emit_status(f"Detected Fastboot device: {serial}")
                    log_message('debug', f"Regex matched serial: {serial}")
                    return serial
        self._emit_status("No Fastboot device detected.")
        log_message('debug', "No Fastboot device detected by regex.")
        return None

    def get_device_info(self, serial):
        """
        Retrieves device information (codename, bootloader status) for a given serial.
        Returns a dictionary with 'codename' and 'bootloader_locked' status.
        """
        info = {"codename": "Unknown", "bootloader_locked": "Unknown"}
        if not self.fastboot_path or not serial:
            self._emit_status("Fastboot path or device serial not set. Cannot get device info.")
            return info

        # Get all variables from fastboot
        command = [self.fastboot_path, "-s", serial, "getvar", "all"]
        self._emit_status(f"Attempting to get device info with command: {' '.join(command)}")

        success, output = self._execute_command(command, capture_output=True)

        log_message('debug', f"Raw output from fastboot getvar all: {output}")

        if success and output:
            for line in output:
                log_message('debug', f"Parsing line: '{line}'") # Log each line being parsed

                # Corrected regex: removed \s+ after 'product:'
                product_match = re.search(r'\(bootloader\)\s+product:(\S+)', line)
                if product_match:
                    info["codename"] = product_match.group(1)
                    log_message('debug', f"Found product match. Codename: {info['codename']}")
                else:
                    log_message('debug', f"No product match for line: '{line}'") # Log if no match

                # Corrected regex: removed \s+ after 'unlocked:'
                unlocked_match = re.search(r'\(bootloader\)\s+unlocked:(yes|no)', line)
                if unlocked_match:
                    info["bootloader_locked"] = "Unlocked" if unlocked_match.group(1) == "yes" else "Locked"
                    log_message('debug', f"Found unlocked match. Bootloader status: {info['bootloader_locked']}")
                else:
                    log_message('debug', f"No unlocked match for line: '{line}'") # Log if no match

        self._emit_status(f"Device info for {serial}: Codename={info['codename']}, Bootloader={info['bootloader_locked']}")
        return info

    def extract_rom(self, rom_file_path, extract_base_dir):
        """
        Extracts a .tgz Fastboot ROM archive. If it contains a nested .zip or .tar,
        it extracts that too.
        Returns (True, extracted_directory_path) on success, (False, error_message) on failure.
        """
        if not os.path.exists(rom_file_path):
            return False, f"ROM file not found: {rom_file_path}"

        if not tarfile.is_tarfile(rom_file_path):
            return False, f"Selected file is not a valid tar archive: {rom_file_path}"

        # Create a unique extraction directory based on ROM file name
        rom_filename = os.path.basename(rom_file_path)
        # Remove .tgz or .tar.gz extension to get base name for directory
        if rom_filename.lower().endswith('.tgz'):
            initial_extracted_dir_name = rom_filename[:-4]
        elif rom_filename.lower().endswith('.tar.gz'):
            initial_extracted_dir_name = rom_filename[:-7]
        else:
            initial_extracted_dir_name = rom_filename + "_extracted_tgz" # Fallback

        initial_extracted_full_path = os.path.join(extract_base_dir, initial_extracted_dir_name)

        # Clean up previous initial extraction if it exists
        if os.path.exists(initial_extracted_full_path):
            self._emit_status(f"Removing existing initial extracted directory: {initial_extracted_full_path}")
            try:
                shutil.rmtree(initial_extracted_full_path)
            except Exception as e:
                return False, f"Failed to remove existing initial extracted directory: {e}"

        self._emit_status(f"Extracting primary archive '{rom_filename}' to '{initial_extracted_full_path}'...")
        os.makedirs(initial_extracted_full_path, exist_ok=True) # Ensure target dir exists

        try:
            with tarfile.open(rom_file_path, "r:gz") as tar:
                # Extract all contents of the .tgz
                tar.extractall(path=initial_extracted_full_path)
            self._emit_status("Primary archive extraction complete.")

            # --- Handle nested archives (ZIP or TAR) ---
            nested_archive_file = None
            nested_archive_type = None # 'zip' or 'tar'

            for root, _, files in os.walk(initial_extracted_full_path):
                for file in files:
                    file_lower = file.lower()
                    if file_lower.endswith('.zip'):
                        nested_archive_file = os.path.join(root, file)
                        nested_archive_type = 'zip'
                        break
                    elif file_lower.endswith('.tar') or file_lower.endswith('.tar.gz') or file_lower.endswith('.tgz'):
                        nested_archive_file = os.path.join(root, file)
                        nested_archive_type = 'tar'
                        break
                if nested_archive_file:
                    break

            if nested_archive_file:
                self._emit_status(f"Detected nested {nested_archive_type.upper()} archive: '{os.path.basename(nested_archive_file)}'. Extracting...")

                # Create a new directory for the nested archive's contents
                nested_extracted_dir_name = os.path.basename(nested_archive_file)
                if nested_archive_type == 'zip':
                    nested_extracted_dir_name = nested_extracted_dir_name[:-4] # Remove .zip
                elif nested_archive_type == 'tar':
                    if nested_extracted_dir_name.lower().endswith('.tgz'):
                        nested_extracted_dir_name = nested_extracted_dir_name[:-4]
                    elif nested_extracted_dir_name.lower().endswith('.tar.gz'):
                        nested_extracted_dir_name = nested_extracted_dir_name[:-7]
                    elif nested_extracted_dir_name.lower().endswith('.tar'):
                        nested_extracted_dir_name = nested_extracted_dir_name[:-4]

                final_extracted_full_path = os.path.join(initial_extracted_full_path, nested_extracted_dir_name)

                # Clean up previous nested extraction if it exists
                if os.path.exists(final_extracted_full_path):
                    self._emit_status(f"Removing existing nested extracted directory: {final_extracted_full_path}")
                    try:
                        shutil.rmtree(final_extracted_full_path)
                    except Exception as e:
                        return False, f"Failed to remove existing nested extracted directory: {e}"

                os.makedirs(final_extracted_full_path, exist_ok=True) # Ensure target dir exists

                if nested_archive_type == 'zip':
                    with zipfile.ZipFile(nested_archive_file, 'r') as zip_ref:
                        zip_ref.extractall(final_extracted_full_path)
                elif nested_archive_type == 'tar':
                    # Determine mode for tarfile.open
                    tar_mode = "r"
                    if nested_archive_file.lower().endswith('.gz') or nested_archive_file.lower().endswith('.tgz'):
                        tar_mode = "r:gz"
                    elif nested_archive_file.lower().endswith('.bz2'): # Just in case, though less common for ROMs
                        tar_mode = "r:bz2"

                    with tarfile.open(nested_archive_file, tar_mode) as tar_ref:
                        tar_ref.extractall(final_extracted_full_path)

                self._emit_status(f"Nested {nested_archive_type.upper()} extraction complete.")

                # The actual ROM contents are likely one level deeper, inside a folder named after the archive
                # E.g., if zip is "rom.zip" and extracts to "rom", then contents are in "rom"
                # We need to find the directory that contains flash_all.sh/bat or the 'images' folder.

                # Let's find the actual ROM root within the nested extraction
                rom_root_found = False
                for item in os.listdir(final_extracted_full_path):
                    item_path = os.path.join(final_extracted_full_path, item)
                    if os.path.isdir(item_path):
                        # Check for flash scripts or 'images' folder inside this subdirectory
                        if any(f.startswith('flash_all') and (f.endswith('.sh') or f.endswith('.bat')) for f in os.listdir(item_path)) or \
                           os.path.exists(os.path.join(item_path, 'images')):
                            final_extracted_full_path = item_path # This is the true ROM root
                            rom_root_found = True
                            self._emit_status(f"Found ROM root inside nested extraction: {final_extracted_full_path}")
                            break

                if not rom_root_found:
                    self._emit_status(f"Could not find a clear ROM root within the nested extraction at {final_extracted_full_path}. Assuming it's the current path.")

                return True, final_extracted_full_path

            else:
                self._emit_status("No nested archive (ZIP/TAR) found. Assuming primary archive contains ROM directly.")
                return True, initial_extracted_full_path

        except tarfile.ReadError as e:
            return False, f"Error reading tar file (corrupted or invalid format): {e}"
        except zipfile.BadZipFile as e:
            return False, f"Error reading nested zip file (corrupted or invalid format): {e}"
        except Exception as e:
            return False, f"An error occurred during ROM extraction: {e}"

    def flash_rom(self, extracted_rom_path, flash_mode):
        """
        Orchestrates the device flashing process.
        Finds the appropriate flash script (flash_all.sh or flash_all.bat)
        based on the selected flash_mode and executes it.
        Returns (True, message) on success, (False, error_message) on failure.
        """
        self._emit_status(f"Starting device flashing process with mode: {flash_mode}...")

        # Determine the base script name based on the selected flash mode
        script_base_name = ""
        if flash_mode == FlashModes.CLEAN_ALL:
            script_base_name = "flash_all"
        elif flash_mode == FlashModes.SAVE_USER_DATA:
            script_base_name = "flash_all_except_data_storage" # This script name is used for keeping user data
        elif flash_mode == FlashModes.LOCK_BOOTLOADER:
            script_base_name = "flash_all_lock"
        elif flash_mode == FlashModes.SAVE_DATA_AND_STORAGE:
            # Assuming 'flash_all_except_data_storage' is the script that keeps apps and data
            # You might need to verify which script exactly corresponds to this mode.
            script_base_name = "flash_all_except_data_storage"
        else:
            error_msg = f"Error: Unknown flash mode selected: {flash_mode}"
            self._emit_status(error_msg)
            log_message('error', error_msg)
            return False, error_msg

        # Append appropriate extension based on OS
        if self.current_os == "win32":
            script_full_name = f"{script_base_name}.bat"
        elif self.current_os == "linux" or self.current_os == "darwin":
            script_full_name = f"{script_base_name}.sh"
        else:
            error_msg = f"Error: Unsupported operating system: {self.current_os}"
            self._emit_status(error_msg)
            log_message('error', error_msg)
            return False, error_msg

        # Construct path to the 'images' directory within the ROM folder
        images_dir = os.path.join(extracted_rom_path, "images")
        flash_script_path = None

        # Prioritize script in 'images' directory
        candidate_script_in_images = os.path.join(images_dir, script_full_name)
        if os.path.exists(candidate_script_in_images):
            flash_script_path = candidate_script_in_images
            self._emit_status(f"Found flash script in images directory: {flash_script_path}")
            log_message('info', f"Using script: {flash_script_path}")
        else:
            # Fallback: Check if the script is directly in the ROM root (less common for modern ROMs)
            candidate_script_in_root = os.path.join(extracted_rom_path, script_full_name)
            if os.path.exists(candidate_script_in_root):
                flash_script_path = candidate_script_in_root
                self._emit_status(f"Found flash script in ROM root: {flash_script_path}")
                log_message('info', f"Using script: {flash_script_path}")
            else:
                error_msg = f"Error: Flashing script '{script_full_name}' not found in '{images_dir}' or '{extracted_rom_path}'."
                self._emit_status(error_msg)
                log_message('error', error_msg)
                return False, error_msg

        # Ensure the script is executable on Linux/macOS
        if self.current_os in ["linux", "darwin"]:
            try:
                os.chmod(flash_script_path, 0o755) # rwx for owner, rx for group/others
                self._emit_status(f"Set executable permissions for {flash_script_path}")
                log_message('info', f"Set executable permissions for {flash_script_path}")
            except Exception as e:
                self._emit_status(f"Warning: Could not set executable permissions for {flash_script_path}: {e}")
                log_message('warning', f"Failed to chmod {flash_script_path}: {e}")

        # Determine the working directory for the script execution
        # It's usually the directory containing the script, so it can find image files
        script_cwd = os.path.dirname(flash_script_path)

        # Execute the flashing script
        if self.current_os == "win32":
            command = [flash_script_path]
            success, _ = self._execute_command(command, cwd=script_cwd, shell=True)
        else: # Linux or macOS
            command = [flash_script_path]
            success, _ = self._execute_command(command, cwd=script_cwd, shell=False)

        if success:
            return True, "Flashing process completed."
        else:
            return False, "Flashing process failed. Check logs for details."

