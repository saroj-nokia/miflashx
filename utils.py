import os
import sys
import logging
import subprocess
import tempfile

# --- Logging Setup ---
# Previously this wrote to a folder next to __file__, which under PyInstaller's
# --onefile mode resolves inside the temp extraction dir: logs vanished every
# run, and on read-only install locations os.makedirs() could throw before any
# window even opened. Use the standard XDG data location instead.
LOG_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "miflashx", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "miflashx.log")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)


def log_message(level, message):
    """
    Logs a message with the given level to the configured log file and console.
    Levels: 'info', 'warning', 'error', 'debug'.
    """
    {
        'info': logging.info,
        'warning': logging.warning,
        'error': logging.error,
        'debug': logging.debug,
    }.get(level, logging.info)(message)


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

    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    bundled_dir = os.path.join(base_path, "platform-tools")

    if os.path.isdir(bundled_dir):
        adb_candidate = os.path.join(bundled_dir, "adb")
        fastboot_candidate = os.path.join(bundled_dir, "fastboot")

        if os.path.exists(adb_candidate) and os.access(adb_candidate, os.X_OK):
            adb_path = adb_candidate
        if os.path.exists(fastboot_candidate) and os.access(fastboot_candidate, os.X_OK):
            fastboot_path = fastboot_candidate

        if adb_path and fastboot_path:
            log_message('info', f"Found bundled platform-tools: {bundled_dir}")
            return adb_path, fastboot_path

    log_message('info', "Bundled platform-tools not found or incomplete, checking system PATH.")
    try:
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

        if "ATTR{idVendor}==\"18d1\"" in current_content and \
           "ATTR{idVendor}==\"2717\"" in current_content:
            return True, "Rules file found and appears correct."
        else:
            return False, "Rules file exists but may be incomplete or incorrect for Xiaomi/Android devices."
    except Exception as e:
        log_message('error', f"Error reading udev rules file {udev_rules_path}: {e}")
        return False, f"Error reading udev rules file: {e}"


def _run_privileged(command: list[str]) -> tuple[bool, str]:
    """
    Runs a command with elevated privileges via pkexec.

    Why not sudo: sudo needs a controlling terminal (or an askpass helper) to
    prompt for a password. An app launched from a desktop icon or app menu has
    neither, so `sudo` fails immediately with "no tty present and no askpass
    program specified" — the old behavior silently broke for anyone not
    launching MiFlashX from a terminal. pkexec integrates with polkit and pops
    up a proper graphical authentication dialog regardless of how the app was
    launched.
    """
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return False, proc.stderr.strip() or f"Command failed with exit code {proc.returncode}."
        return True, proc.stdout.strip()
    except FileNotFoundError:
        return False, ("'pkexec' was not found. Install polkit (usually preinstalled on "
                        "GNOME/KDE distros) to allow MiFlashX to request permissions.")
    except Exception as e:
        return False, f"Unexpected error running privileged command: {e}"


def install_udev_rules():
    """
    Installs/updates udev rules for Android devices via pkexec.
    Returns (True/False, message).
    """
    if get_os() != "linux":
        return False, "Udev rule installation is only for Linux."

    udev_rules_path = "/etc/udev/rules.d/51-android.rules"
    rules_content = generate_udev_rule_content()

    log_message('info', f"Attempting to write udev rules to {udev_rules_path}...")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile('w', suffix='.rules', delete=False) as f:
            f.write(rules_content)
            tmp_path = f.name

        # `install` copies + sets ownership/permissions in one privileged step,
        # rather than piping through `tee`, which doesn't play well with pkexec.
        ok, msg = _run_privileged(["pkexec", "install", "-m", "644", tmp_path, udev_rules_path])
        if not ok:
            error_msg = f"Failed to write udev rules: {msg}"
            log_message('error', error_msg)
            return False, error_msg

        log_message('info', "Udev rules written. Reloading udev rules...")
        ok, msg = _run_privileged(["pkexec", "udevadm", "control", "--reload-rules"])
        if not ok:
            return False, f"Udev rules written but reload failed: {msg}"

        ok, msg = _run_privileged(["pkexec", "udevadm", "trigger"])
        if not ok:
            return False, f"Udev rules written but trigger failed: {msg}"

        log_message('info', "Udev rules reloaded. You may need to replug your device.")
        return True, "Udev rules installed and reloaded successfully. Please replug your device."

    except Exception as e:
        error_msg = f"An unexpected error occurred during udev rule installation: {e}"
        log_message('error', error_msg)
        return False, error_msg
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def add_to_adbusers_group():
    """
    Adds the current user to the 'adbusers' group via pkexec.
    Requires a logout/login for changes to take effect.
    Returns (True/False, message).
    """
    if get_os() != "linux":
        return False, "Adding user to 'adbusers' group is only for Linux."

    current_user = os.getenv('USER') or os.getenv('LOGNAME')
    if not current_user:
        try:
            import pwd
            current_user = pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            return False, "Could not determine the current username."

    log_message('info', f"Attempting to add user '{current_user}' to 'adbusers' group...")

    # Ignore failure here: group may already exist, which is fine.
    subprocess.run(["pkexec", "groupadd", "adbusers"], stderr=subprocess.DEVNULL, check=False)

    ok, msg = _run_privileged(["pkexec", "usermod", "-aG", "adbusers", current_user])
    if not ok:
        error_msg = f"Failed to add user to 'adbusers' group: {msg}"
        log_message('error', error_msg)
        return False, error_msg

    log_message('info', f"User '{current_user}' successfully added to 'adbusers' group.")
    return True, f"User '{current_user}' successfully added to 'adbusers' group. You MUST log out and log back in for changes to take effect."
