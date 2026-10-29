import os
import re
import sys
import logging
import shutil
import subprocess
import tempfile
from collections import namedtuple

# adb_path/fastboot_path: resolved absolute paths (or None if neither system
# nor bundled has the tool). *_source: "system" or "bundled", telling the
# caller/UI which copy actually got used. warnings: human-readable strings
# (e.g. "system adb is older than the bundled copy") for cases worth
# surfacing to the user but not worth failing over. adb_choice/fastboot_choice:
# a ToolChoice when both a system AND bundled copy exist with DIFFERENT,
# determinable versions -- lets the caller offer the user a pick instead of
# the auto-decision silently winning; None when there's nothing to choose
# between (only one copy present, or versions are equal/unknown).
ToolInfo = namedtuple("ToolInfo", ["adb_path", "fastboot_path", "adb_source", "fastboot_source",
                                    "warnings", "adb_choice", "fastboot_choice"])
ToolChoice = namedtuple("ToolChoice", ["system_path", "bundled_path", "system_version", "bundled_version"])

# pkexec resolves the program it's told to run via its OWN restricted secure
# PATH, not the invoking user's PATH -- and that secure PATH varies by distro
# (e.g. openSUSE's pkexec build doesn't always include /usr/sbin, where
# usermod/groupadd live). Resolving to an absolute path ourselves -- using our
# own process's full PATH -- sidesteps pkexec's PATH lookup entirely.
_PRIVILEGED_BIN_FALLBACK_DIRS = ["/usr/sbin", "/sbin", "/usr/bin", "/bin"]


def _resolve_privileged_bin(name: str) -> str:
    """Resolves `name` to an absolute path, falling back to common sbin/bin
    dirs if it's not on our own PATH. Returns `name` unchanged if not found
    anywhere, so the original (clearer) pkexec error still surfaces."""
    found = shutil.which(name)
    if found:
        return found
    for d in _PRIVILEGED_BIN_FALLBACK_DIRS:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return name

# --- Logging Setup ---
# Previously this wrote to a folder next to __file__, which under PyInstaller's
# --onefile mode resolves inside the temp extraction dir: logs vanished every
# run, and on read-only install locations os.makedirs() could throw before any
# window even opened. Use the standard XDG data location instead.
LOG_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "miflashx", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "miflashx.log")

# DEBUG level + echoing every line to stdout is dev-only noise for a
# user-facing release (and a --windowed PyInstaller build has no terminal to
# show it on anyway). Set MIFLASHX_DEBUG=1 to get the old verbose behavior
# back; the log file always keeps full detail either way.
_DEBUG_ENV = os.getenv("MIFLASHX_DEBUG") == "1"
_log_handlers = [logging.FileHandler(LOG_FILE)]
if _DEBUG_ENV:
    _log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.DEBUG if _DEBUG_ENV else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
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
_VERSION_ARGS = {"adb": ["version"], "fastboot": ["--version"]}


def _tool_version(path: str, tool_name: str):
    """Runs `path version`/`--version` (falling back to a bare, no-args
    invocation if that gives no usable output -- some non-Google adb/
    fastboot builds, e.g. the "android-tools" project package shipped by
    openSUSE/Arch/Debian, don't answer "version" the same way Google's does
    but still print their version banner on a bare call) and parses the
    first X.Y.Z it finds. Returns a tuple of ints, or None if neither
    invocation produced a recognizable version number."""
    for args in (_VERSION_ARGS[tool_name], []):
        try:
            result = subprocess.run([path] + args, capture_output=True,
                                     text=True, timeout=5, check=False)
            output = (result.stdout or "") + (result.stderr or "")
            # `adb version` prints a constant "Android Debug Bridge version
            # 1.0.41" protocol line FIRST, then the real build number on its
            # own "Version X.Y.Z-..." line (capital V) -- matching
            # generically would always read 1.0.41 and never notice a real
            # version difference. Try the capital-V build-version line
            # first; fastboot has no such line, so it falls through to the
            # generic match below. The android-tools build's version string
            # ("Version 37.0.0p1-android-tools") still matches fine -- the
            # regex just stops at the first non-digit after the patch number.
            match = re.search(r"Version\s+(\d+)\.(\d+)\.(\d+)", output) or \
                    re.search(r"(\d+)\.(\d+)\.(\d+)", output)
            if match:
                return tuple(int(g) for g in match.groups())
        except Exception as e:
            log_message('debug', f"Could not determine {tool_name} version at {path} with args {args}: {e}")
    return None


def _resolve_tool(tool_name: str, system_path: str, bundled_path: str, preferred_source: str = None):
    """Picks which copy of a tool (adb/fastboot) to use between a
    system-installed one and the one bundled with this app.

    If `preferred_source` ("system"/"bundled") is given and that copy
    actually exists, it wins outright -- this is how a user's remembered
    choice from the picker dialog gets applied without re-asking.

    Otherwise: prefers the system copy (it's what the system's own udev
    rules/package manager already track) UNLESS the bundled copy is a
    newer, determinably-different version, in which case the bundled copy
    is used by default and a warning is returned. When both versions ARE
    known and differ, a ToolChoice is also returned so the caller can offer
    the user a pick instead of just accepting the default.
    Returns (path, source, warning_or_None, choice_or_None).
    """
    if system_path and bundled_path:
        sys_ver = _tool_version(system_path, tool_name)
        bundled_ver = _tool_version(bundled_path, tool_name)
        choice = ToolChoice(system_path, bundled_path, sys_ver, bundled_ver) \
            if sys_ver and bundled_ver and sys_ver != bundled_ver else None

        if preferred_source == "system":
            return system_path, "system", None, choice
        if preferred_source == "bundled":
            return bundled_path, "bundled", None, choice

        if sys_ver and bundled_ver and sys_ver < bundled_ver:
            warning = (f"System {tool_name} ({'.'.join(map(str, sys_ver))}) is older than the "
                       f"version bundled with MiFlashX ({'.'.join(map(str, bundled_ver))}). "
                       f"Using the bundled copy — consider updating your system's {tool_name} tool.")
            log_message('warning', warning)
            return bundled_path, "bundled", warning, choice
        return system_path, "system", None, choice
    if system_path:
        return system_path, "system", None, None
    if bundled_path:
        return bundled_path, "bundled", None, None
    return None, None, None, None


def find_adb_fastboot(preferred_source: dict = None) -> ToolInfo:
    """
    Attempts to find adb and fastboot executables.
    Prioritizes the system's own install (e.g. `android-tools`/`platform-tools`
    from the distro repos or installed directly from Google) over the copy
    bundled with this app, per-binary — UNLESS the system copy is an older
    version than the bundled one, in which case the (newer) bundled copy is
    used and a warning is included so the caller can surface it. Either
    default can be overridden per-tool via `preferred_source`, e.g.
    {"adb": "system", "fastboot": "bundled"} — see _resolve_tool().
    """
    preferred_source = preferred_source or {}
    system_adb = shutil.which("adb")
    system_fastboot = shutil.which("fastboot")

    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    bundled_dir = os.path.join(base_path, "platform-tools")
    bundled_adb = bundled_fastboot = None
    if os.path.isdir(bundled_dir):
        candidate = os.path.join(bundled_dir, "adb")
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            bundled_adb = candidate
        candidate = os.path.join(bundled_dir, "fastboot")
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            bundled_fastboot = candidate

    adb_path, adb_source, adb_warning, adb_choice = _resolve_tool(
        "adb", system_adb, bundled_adb, preferred_source.get("adb"))
    fastboot_path, fastboot_source, fastboot_warning, fastboot_choice = _resolve_tool(
        "fastboot", system_fastboot, bundled_fastboot, preferred_source.get("fastboot"))

    for name, path, source in (("adb", adb_path, adb_source), ("fastboot", fastboot_path, fastboot_source)):
        if path:
            log_message('debug', f"Using {source} {name}: {path}")
        else:
            log_message('warning', f"{name} not found on system PATH or bundled with the app.")

    warnings = [w for w in (adb_warning, fastboot_warning) if w]
    return ToolInfo(adb_path, fastboot_path, adb_source, fastboot_source, warnings, adb_choice, fastboot_choice)


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
        install_bin = _resolve_privileged_bin("install")
        ok, msg = _run_privileged(["pkexec", install_bin, "-m", "644", tmp_path, udev_rules_path])
        if not ok:
            error_msg = f"Failed to write udev rules: {msg}"
            log_message('error', error_msg)
            return False, error_msg

        log_message('info', "Udev rules written. Reloading udev rules...")
        udevadm_bin = _resolve_privileged_bin("udevadm")
        ok, msg = _run_privileged(["pkexec", udevadm_bin, "control", "--reload-rules"])
        if not ok:
            return False, f"Udev rules written but reload failed: {msg}"

        ok, msg = _run_privileged(["pkexec", udevadm_bin, "trigger"])
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
    groupadd_bin = _resolve_privileged_bin("groupadd")
    subprocess.run(["pkexec", groupadd_bin, "adbusers"], stderr=subprocess.DEVNULL, check=False)

    usermod_bin = _resolve_privileged_bin("usermod")
    ok, msg = _run_privileged(["pkexec", usermod_bin, "-aG", "adbusers", current_user])
    if not ok:
        error_msg = f"Failed to add user to 'adbusers' group: {msg}"
        log_message('error', error_msg)
        return False, error_msg

    log_message('info', f"User '{current_user}' successfully added to 'adbusers' group.")
    return True, f"User '{current_user}' successfully added to 'adbusers' group. You MUST log out and log back in for changes to take effect."
