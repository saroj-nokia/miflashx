# utils.py
import os
import sys
import logging
import subprocess
import time

# --- Logging Setup ---
# Ensure the logs directory exists
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "miflashx.log")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout) # Also print to console for development/debugging
    ]
)

def log_message(level, message):
    """
    Logs a message with the given level to the configured log file and console.
    Levels: 'info', 'warning', 'error', 'debug'.
    """
    if level == 'info':
        logging.info(message)
    elif level == 'warning':
        logging.warning(message)
    elif level == 'error':
        logging.error(message)
    elif level == 'debug':
        logging.debug(message)
    else:
        logging.info(message) # Default to info if unknown level

# --- OS Detection ---
def get_os():
    """Returns the operating system name (e.g., 'linux', 'win32', 'darwin')."""
    return sys.platform

# --- ADB/Fastboot Path Discovery ---
def find_adb_fastboot():
    """
    Attempts to find adb and fastboot executables.
    Prioritizes bundled tools, then system PATH.
    Returns a tuple (adb_path, fastboot_path) or (None, None) if not found.
    """
    adb_path = None
    fastboot_path = None

    # Determine the base path for bundled tools.
    # sys._MEIPASS is used by PyInstaller for the temporary directory of bundled files.
    # Otherwise, it's the directory of the current script.
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    bundled_dir = os.path.join(base_path, "platform-tools")

    # 1. Check bundled platform-tools directory first
    if os.path.isdir(bundled_dir):
        adb_candidate = os.path.join(bundled_dir, "adb")
        fastboot_candidate = os.path.join(bundled_dir, "fastboot")
        
        # Check if files exist and are executable
        if os.path.exists(adb_candidate) and os.access(adb_candidate, os.X_OK):
            adb_path = adb_candidate
        if os.path.exists(fastboot_candidate) and os.access(fastboot_candidate, os.X_OK):
            fastboot_path = fastboot_candidate
            
        if adb_path and fastboot_path:
            log_message('info', f"Found bundled platform-tools: {bundled_dir}")
            return adb_path, fastboot_path

    # 2. Fallback to system PATH if bundled tools are not found or incomplete
    log_message('info', "Bundled platform-tools not found or incomplete, checking system PATH.")
    try:
        # 'which' command is standard on Linux/macOS to find executable in PATH
        adb_result = subprocess.run(["which", "adb"], capture_output=True, text=True, check=False)
        if adb_result.returncode == 0:
            adb_path = adb_result.stdout.strip()
            log_message('debug', f"Found adb in PATH: {adb_path}")
        else:
            log_message('warning', "adb not found in system PATH.")

        fastboot_result = subprocess.run(["which", "fastboot"], capture_output=True, text=True, check=False)
        if fastboot_result.returncode == 0:
            fastboot_path = fastboot_result.stdout.strip()
            log_message('debug', f"Found fastboot in PATH: {fastboot_path}")
        else:
            log_message('warning', "fastboot not found in system PATH.")

    except Exception as e:
        log_message('error', f"Error checking system PATH for adb/fastboot: {e}")

    return adb_path, fastboot_path

# --- Udev Rule Management (Linux Only) ---
def generate_udev_rule_content():
    """Generates the content for the 51-android.rules file."""
    # Common Xiaomi vendor ID (2717) and generic Android vendor ID (18d1)
    # MODE="0666": Grants read/write access to all users.
    # GROUP="adbusers": Assigns devices to 'adbusers' group for fine-grained permissions.
    # TAG+="uaccess": Grants access to the user logged into the console.
    return """
# Google Android devices
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0666", GROUP="adbusers", TAG+="uaccess"
# Xiaomi devices (common vendor ID for various models)
SUBSYSTEM=="usb", ATTR{idVendor}=="2717", MODE="0666", GROUP="adbusers", TAG+="uaccess"
"""

def check_udev_rules():
    """
    Checks if basic udev rules for Android devices exist and contain the necessary entries.
    Returns (True/False, message).
    """
    udev_rules_path = "/etc/udev/rules.d/51-android.rules"
    
    if not os.path.exists(udev_rules_path):
        return False, f"Udev rules file '{udev_rules_path}' not found."

    try:
        with open(udev_rules_path, 'r') as f:
            current_content = f.read()
        
        # Simple check for presence of key vendor IDs. More robust checks are possible.
        # FIX: Escaped the second double quote correctly.
        if "ATTR{idVendor}==\"18d1\"" in current_content and \
           "ATTR{idVendor}==\"2717\"" in current_content:
            return True, "Rules file found and appears correct."
        else:
            return False, "Rules file exists but may be incomplete or incorrect for Xiaomi/Android devices."
    except Exception as e:
        log_message('error', f"Error reading udev rules file {udev_rules_path}: {e}")
        return False, f"Error reading udev rules file: {e}"

def install_udev_rules():
    """
    Installs/updates udev rules for Android devices.
    Requires sudo.
    Returns (True/False, message).
    """
    if get_os() != "linux":
        return False, "Udev rule installation is only for Linux."

    udev_rules_path = "/etc/udev/rules.d/51-android.rules"
    rules_content = generate_udev_rule_content()

    log_message('info', f"Attempting to write udev rules to {udev_rules_path}...")
    try:
        # Use 'sudo tee' to write to a privileged location safely.
        # `input=rules_content.encode()` sends content to stdin of tee.
        process = subprocess.run(
            ["sudo", "tee", udev_rules_path],
            input=rules_content.encode(),
            capture_output=True,
            check=False, # Don't raise CalledProcessError immediately for sudo prompts
            text=True
        )

        if process.returncode != 0:
            error_msg = f"Failed to write udev rules. Sudo response: {process.stderr.strip()}"
            log_message('error', error_msg)
            if "incorrect password attempt" in process.stderr.lower():
                return False, "Failed to write udev rules: Incorrect sudo password or no password entered. Run the app from a terminal and provide password."
            return False, error_msg

        # Set appropriate permissions for the rules file
        subprocess.run(["sudo", "chmod", "644", udev_rules_path], check=True, capture_output=True)

        log_message('info', "Udev rules written. Reloading udev rules...")
        # Reload and trigger udev to apply new rules
        subprocess.run(["sudo", "udevadm", "control", "--reload-rules"], check=True, capture_output=True)
        subprocess.run(["sudo", "udevadm", "trigger"], check=True, capture_output=True)
        
        log_message('info', "Udev rules reloaded. You may need to replug your device.")
        return True, "Udev rules installed and reloaded successfully. Please replug your device."

    except subprocess.CalledProcessError as e:
        error_msg = f"Sudo command failed during udev rule installation: {e}\nStderr: {e.stderr.strip()}"
        log_message('error', error_msg)
        return False, error_msg
    except FileNotFoundError:
        return False, "sudo command not found. Please ensure sudo is installed and in your PATH."
    except Exception as e:
        error_msg = f"An unexpected error occurred during udev rule installation: {e}"
        log_message('error', error_msg)
        return False, error_msg

def add_to_adbusers_group():
    """
    Adds the current user to the 'adbusers' group.
    Requires sudo and a logout/login for changes to take effect.
    Returns (True/False, message).
    """
    if get_os() != "linux":
        return False, "Adding user to 'adbusers' group is only for Linux."

    current_user = os.getenv('USER')
    if not current_user:
        return False, "Could not determine current user (USER environment variable not set)."

    log_message('info', f"Attempting to add user '{current_user}' to 'adbusers' group...")
    try:
        # Check if 'adbusers' group exists, create it if not
        # Redirect stderr to /dev/null to suppress "groupadd: group 'adbusers' already exists"
        subprocess.run(["sudo", "groupadd", "adbusers"], stderr=subprocess.DEVNULL, check=False)

        # Add user to group
        process = subprocess.run(
            ["sudo", "usermod", "-aG", "adbusers", current_user],
            capture_output=True,
            check=False,
            text=True
        )

        if process.returncode != 0:
            error_msg = f"Failed to add user to 'adbusers' group. Sudo response: {process.stderr.strip()}"
            log_message('error', error_msg)
            if "incorrect password attempt" in process.stderr.lower():
                return False, "Failed to add user to group: Incorrect sudo password or no password entered. Run the app from a terminal and provide password."
            return False, error_msg
            
        log_message('info', f"User '{current_user}' successfully added to 'adbusers' group.")
        return True, f"User '{current_user}' successfully added to 'adbusers' group. You MUST log out and log back in for changes to take effect."

    except subprocess.CalledProcessError as e:
        error_msg = f"Sudo command failed during group addition: {e}\nStderr: {e.stderr.strip()}"
        log_message('error', error_msg)
        return False, error_msg
    except FileNotFoundError:
        return False, "sudo command not found. Please ensure sudo is installed and in your PATH."
    except Exception as e:
        error_msg = f"An unexpected error occurred during group addition: {e}"
        log_message('error', error_msg)
        return False, error_msg
