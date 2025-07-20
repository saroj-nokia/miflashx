import os
import tarfile
import subprocess
import re
import time # For potential delays if needed

# Import utility functions for logging and path finding
from utils import log_message, get_os, find_adb_fastboot

class FlashingCore:
    def __init__(self, output_callback=None):
        """
        Initializes the FlashingCore.
        :param output_callback: A function (level, message) to send log messages to the GUI.
        """
        self.output_callback = output_callback if output_callback else log_message
        self.adb_path, self.fastboot_path = find_adb_fastboot()
        
        if not self.adb_path or not self.fastboot_path:
            # This should ideally be caught by GUI's initial checks, but good to have here too
            self.output_callback('error', "ADB or Fastboot executables not found. Please ensure they are in your PATH or bundled correctly.")
            raise FileNotFoundError("ADB/Fastboot not found. Cannot initialize FlashingCore.")

    def _run_command(self, cmd_list, cwd=None, sudo_required=False):
        """
        Helper to run shell commands and stream output to the callback.
        Handles sudo prefix if required.
        :param cmd_list: List of command arguments.
        :param cwd: Current working directory for the command.
        :param sudo_required: Boolean, whether to prepend 'sudo' to the command.
        """
        if sudo_required:
            if get_os() == "linux": # Sudo is primarily a Linux concept here
                # Check if already running as root (e.g., if app launched with sudo)
                if os.geteuid() == 0:
                    log_message('info', f"Running command as root (already elevated): {' '.join(cmd_list)}")
                else:
                    cmd_list = ["sudo"] + cmd_list
                    log_message('info', f"Executing with sudo: {' '.join(cmd_list)}")
            else:
                log_message('warning', f"Sudo requested but not on Linux: {' '.join(cmd_list)}")
                
        else:
            log_message('info', f"Executing: {' '.join(cmd_list)}")

        try:
            process = subprocess.Popen(
                cmd_list,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, # Decode output as text (UTF-8 by default)
                bufsize=1, # Line-buffered output
                # IMPORTANT: Close FDs in child process, crucial for PyInstaller --onefile on Linux
                # Especially if parent process has many open FDs due to PyQt.
                close_fds=True 
            )

            # Stream stdout and stderr to the callback
            for line in iter(process.stdout.readline, ''):
                self.output_callback('info', line.strip())
            for line in iter(process.stderr.readline, ''):
                # Classify some stderr as warnings rather than errors if they are typical non-fatal fastboot messages
                if "invalid sparse file header" in line.lower() or "erasing" in line.lower() or "sending" in line.lower():
                    self.output_callback('warning', line.strip())
                else:
                    self.output_callback('error', line.strip())

            process.stdout.close()
            process.stderr.close()
            process.wait() # Wait for the process to terminate

            if process.returncode != 0:
                full_error_output = process.stdout.read() + process.stderr.read() # Capture any remaining output
                raise subprocess.CalledProcessError(process.returncode, cmd_list, output=full_error_output)
            return process.returncode

        except FileNotFoundError:
            raise FileNotFoundError(f"Command not found: '{cmd_list[0]}'. Ensure adb/fastboot are correctly installed and in PATH.")
        except subprocess.CalledProcessError as e:
            error_message = f"Command failed with exit code {e.returncode}.\nCommand: {' '.join(cmd_list)}\nOutput: {e.output.strip()}"
            self.output_callback('error', error_message)
            raise
        except Exception as e:
            error_message = f"An unexpected error occurred while running command: {e}"
            self.output_callback('error', error_message)
            raise

    def detect_device(self):
        """
        Detects if a device is in Fastboot mode.
        Returns the device serial number if found, None otherwise.
        """
        try:
            # Use a short timeout to prevent UI freeze during detection
            result = subprocess.run([self.fastboot_path, "devices"], capture_output=True, text=True, check=True, timeout=5)
            output = result.stdout.strip()
            
            # Fastboot output for connected device is typically "SERIAL\tfastboot"
            if output and "fastboot" in output:
                serial = output.split('\t')[0].strip()
                # self.output_callback('info', f"Device detected in Fastboot mode: {serial}") # Log only on change
                return serial
            # self.output_callback('info', "No device found in Fastboot mode.") # Log only on change
            return None
        except subprocess.TimeoutExpired:
            # self.output_callback('warning', "Fastboot command timed out during device detection.")
            return None
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            # This can happen if permissions are wrong or fastboot isn't found/executable
            # self.output_callback('error', f"Error detecting device (permissions/path issue?): {e}")
            if isinstance(e, subprocess.CalledProcessError):
                self.output_callback('error', f"Fastboot 'devices' command failed. Stderr: {e.stderr.strip()}")
            elif isinstance(e, FileNotFoundError):
                self.output_callback('error', "Fastboot executable not found or not callable during device detection.")
            return None
        except Exception as e:
            self.output_callback('error', f"An unexpected error during device detection: {e}")
            return None

    def get_device_info(self, serial):
        """
        Attempts to get device information (codename, bootloader status, etc.) using fastboot.
        :param serial: The device serial number.
        Returns a dictionary with device info.
        """
        info = {"codename": "Unknown", "bootloader_locked": "Unknown", "product": "Unknown"}
        if not serial:
            return info

        try:
            # Get product/codename
            # 'fastboot getvar product' gives the device codename
            product_cmd = [self.fastboot_path, "-s", serial, "getvar", "product"]
            result = subprocess.run(product_cmd, capture_output=True, text=True, check=False, timeout=5)
            match = re.search(r"product:\s*(\w+)", result.stdout + result.stderr)
            if match:
                info["codename"] = match.group(1).strip()
                info["product"] = info["codename"] # Often product and codename are the same

            # Check bootloader status
            # 'fastboot oem device-info' or 'fastboot getvar unlocked'
            oem_cmd = [self.fastboot_path, "-s", serial, "oem", "device-info"]
            result = subprocess.run(oem_cmd, capture_output=True, text=True, check=False, timeout=5)
            output = result.stdout + result.stderr
            if "Device unlocked: true" in output or "unlocked: yes" in output:
                info["bootloader_locked"] = "Unlocked"
            elif "Device unlocked: false" in output or "unlocked: no" in output:
                info["bootloader_locked"] = "Locked"

            # Fallback for getvar unlocked (some devices might respond to this directly)
            if info["bootloader_locked"] == "Unknown":
                unlocked_cmd = [self.fastboot_path, "-s", serial, "getvar", "unlocked"]
                result_unlocked = subprocess.run(unlocked_cmd, capture_output=True, text=True, check=False, timeout=5)
                output_unlocked = result_unlocked.stdout + result_unlocked.stderr
                if "unlocked: yes" in output_unlocked:
                    info["bootloader_locked"] = "Unlocked"
                elif "unlocked: no" in output_unlocked:
                    info["bootloader_locked"] = "Locked"


        except subprocess.TimeoutExpired:
            self.output_callback('warning', f"Fastboot command timed out while getting device info for {serial}.")
        except Exception as e:
            self.output_callback('warning', f"Could not get full device info for {serial}: {e}")
        return info


    def extract_rom(self, tar_path, extract_base_dir):
        """
        Extracts a Fastboot ROM (TGZ archive) to a specified directory.
        :param tar_path: Path to the .tgz ROM file.
        :param extract_base_dir: The base directory where the ROM will be extracted into a new subfolder.
        Returns the path to the extracted ROM directory on success, False otherwise.
        """
        if not os.path.exists(tar_path):
            self.output_callback('error', f"ROM file not found: {tar_path}")
            return False
        
        self.output_callback('info', f"Preparing to extract ROM from {tar_path}...")
        os.makedirs(extract_base_dir, exist_ok=True) # Ensure base extraction directory exists

        try:
            with tarfile.open(tar_path, 'r:gz') as tar:
                # Determine the final extraction path.
                # ROMs are usually inside a single top-level directory within the .tgz.
                # Example: 'toco_global_images_V12.0.1.0.QJOMIXM_20200813.0000.00_10.0_global/'
                
                # Get the name of the top-level directory inside the tarball
                # This works for most Xiaomi Fastboot ROMs which have a single root folder.
                root_dir_name = ""
                for member in tar.getmembers():
                    if member.isdir() and "/" not in member.name.strip('/'): # Find top-level directory
                        root_dir_name = member.name.strip('/')
                        break
                
                if not root_dir_name:
                    # Fallback if no obvious root dir, just use cleaned tar name
                    root_dir_name = os.path.basename(tar_path).replace(".tgz", "").replace(".tar.gz", "")
                    self.output_callback('warning', f"Could not determine root directory in tar, using '{root_dir_name}' as extraction folder name.")


                final_extract_path = os.path.join(extract_base_dir, root_dir_name)
                os.makedirs(final_extract_path, exist_ok=True) # Create the specific ROM folder

                self.output_callback('info', f"Extracting to: {final_extract_path}")
                tar.extractall(path=extract_base_dir) # Extract all members to the base dir, which will create the root_dir_name folder

            self.output_callback('info', f"ROM extracted successfully to {final_extract_path}")
            return final_extract_path
        except tarfile.ReadError as e:
            self.output_callback('error', f"Failed to read ROM file (corrupt or not a valid TGZ): {e}")
            return False
        except Exception as e:
            self.output_callback('error', f"Error extracting ROM: {e}")
            return False

    def flash_rom(self, rom_path, flash_mode="clean_all"):
        """
        Flashes the extracted Fastboot ROM using its internal scripts.
        :param rom_path: Path to the extracted ROM directory.
        :param flash_mode: One of "clean_all", "except_storage", "except_data_storage".
        Returns True on success, False otherwise.
        """
        if not os.path.exists(rom_path):
            self.output_callback('error', f"Extracted ROM directory not found: {rom_path}")
            return False

        # Map desired mode to the corresponding script name
        script_map = {
            "clean_all": "flash_all.sh",
            "except_storage": "flash_all_except_storage.sh",
            "except_data_storage": "flash_all_except_data_storage.sh"
        }
        
        script_name = script_map.get(flash_mode, "flash_all.sh") # Default to clean_all

        script_path = os.path.join(rom_path, script_name)

        if not os.path.exists(script_path):
            self.output_callback('error', f"Flashing script '{script_name}' not found in {rom_path}.")
            self.output_callback('info', f"Available scripts in {rom_path}: {', '.join([f for f in os.listdir(rom_path) if f.endswith('.sh') or f.endswith('.bat')])}")
            return False

        self.output_callback('info', f"Starting flashing process using {script_name}...")
        
        try:
            # Ensure the script is executable on Linux
            os.chmod(script_path, 0o755) # rwxr-xr-x

            # Determine how to execute the script: with sudo or directly
            # The scripts usually call fastboot directly.
            # If fastboot is in PATH or bundled, it should be found.
            # sudo is often needed for fastboot commands if udev rules are not perfectly set up,
            # or if the script itself performs operations that require root.
            
            # The 'flash_all.sh' scripts in Xiaomi ROMs usually don't need 'sudo bash -c'
            # themselves if fastboot has permissions. However, if fastboot fails with
            # permission issues, adding 'sudo' here could resolve it.
            # For robustness, we'll try running with sudo as these scripts often need it.
            
            cmd = [script_path]
            # Check if we are already running as root (e.g., if user launched MiFlashX with sudo)
            if os.geteuid() != 0: # If not root
                cmd = ["sudo"] + cmd
                self.output_callback('warning', "Running flash script with 'sudo'. You may be prompted for your password in the terminal.")
            else:
                self.output_callback('info', "Running flash script as root (application is already elevated).")


            # Execute the script from its directory (cwd=rom_path)
            self._run_command(cmd, cwd=rom_path, sudo_required=True) # _run_command will handle the 'sudo' prefix itself
            
            self.output_callback('info', "Flashing completed successfully! Device should reboot automatically.")
            return True
        except subprocess.CalledProcessError as e:
            self.output_callback('error', f"Flashing failed. Check logs for details. Error: {e}")
            return False
        except Exception as e:
            self.output_callback('error', f"An unexpected error occurred during flashing: {e}")
            return False
