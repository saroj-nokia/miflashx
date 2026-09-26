import os
import re
import tarfile
import shutil
import zipfile

from utils import log_message, get_os
from command_runner import run_command


class FlashModes:
    CLEAN_ALL = "clean_all"
    SAVE_USER_DATA = "except_storage"
    LOCK_BOOTLOADER = "flash_all_lock"
    SAVE_DATA_AND_STORAGE = "except_data_storage"


def _assert_within(base_dir: str, member_path: str):
    """
    Raises ValueError if `member_path` (a path from inside an archive) would
    resolve outside `base_dir` once joined and normalized.

    This guards against "Zip Slip" / "Tar Slip" (the same vulnerability class
    as CVE-2007-4559): an archive entry named e.g. '../../.bashrc' or an
    absolute path like '/etc/cron.d/x' extracts to wherever it names, not
    inside the intended folder — tarfile/zipfile's extractall() does not
    prevent this on its own in older Python versions. Since MiFlashX's whole
    point is extracting ROM archives downloaded from third-party sources
    (XDA, random mirrors — see README), a malicious file offered as a "ROM"
    is a realistic threat model here, not a theoretical one.
    """
    base_real = os.path.realpath(base_dir)
    target_real = os.path.realpath(os.path.join(base_dir, member_path))
    if target_real != base_real and not target_real.startswith(base_real + os.sep):
        raise ValueError(
            f"Refusing to extract '{member_path}': it resolves outside the "
            f"target directory. This archive entry is either malicious or "
            f"corrupt."
        )


def _safe_extract_tar(tar: tarfile.TarFile, path: str):
    """
    Validates every member's path (and, for symlinks/hardlinks, their link
    target) stays within `path` before extracting anything, then extracts.
    Also passes filter='data' where available (Python 3.12+, PEP 706) as a
    second, independent layer of protection — the manual check above is what
    makes this safe on 3.10/3.11 too, where that parameter doesn't exist.
    """
    for member in tar.getmembers():
        _assert_within(path, member.name)
        if member.issym() or member.islnk():
            _assert_within(path, os.path.join(os.path.dirname(member.name), member.linkname))

    try:
        tar.extractall(path=path, filter='data')
    except TypeError:
        # Python < 3.12 has no `filter` parameter — the manual validation
        # above already vetted every member, so plain extractall() is safe.
        tar.extractall(path=path)


def _safe_extract_zip(zip_ref: zipfile.ZipFile, path: str):
    """Same guard as _safe_extract_tar, for ZIP archives."""
    for name in zip_ref.namelist():
        _assert_within(path, name)
    zip_ref.extractall(path)


# The three script basenames flash_rom() ever looks for (see below) — kept
# here as the single source of truth so validation and actual flashing can't
# drift out of sync with each other.
_KNOWN_FLASH_SCRIPT_NAMES = ["flash_all.sh", "flash_all_except_data_storage.sh", "flash_all_lock.sh"]


def validate_rom_directory(path: str):
    """
    Checks whether `path` looks like a usable extracted Fastboot ROM
    directory — i.e. it (or its 'images' subfolder, or one level of nested
    subfolder) contains at least one of the flash_all*.sh scripts flash_rom()
    knows how to run. Pure filesystem checks, cheap enough to call directly
    from the GUI thread (no subprocess, no large I/O) — this exists so
    someone who already extracted a ROM once doesn't have to re-extract a
    multi-gigabyte archive (rough on an HDD, and slow) just to flash again.

    Returns (True, resolved_path) if valid — resolved_path is the actual ROM
    root to hand to flash_rom(), which may be one level deeper than `path` if
    the user picked the outer folder. Returns (False, error_message) if not.
    """
    def _has_scripts(p: str) -> bool:
        if not os.path.isdir(p):
            return False
        for candidate_dir in (p, os.path.join(p, "images")):
            if os.path.isdir(candidate_dir):
                if any(os.path.exists(os.path.join(candidate_dir, name)) for name in _KNOWN_FLASH_SCRIPT_NAMES):
                    return True
        return False

    if not os.path.isdir(path):
        return False, f"'{path}' is not a directory."

    if _has_scripts(path):
        return True, path

    # The user may have picked the outer folder (e.g. one still containing a
    # single ROM-codename subfolder) rather than the ROM root itself — check
    # one level down before giving up, same fallback extract_rom() already
    # does for nested archives.
    try:
        for entry in sorted(os.listdir(path)):
            candidate = os.path.join(path, entry)
            if _has_scripts(candidate):
                return True, candidate
    except OSError as e:
        return False, f"Could not read directory '{path}': {e}"

    script_list = ", ".join(_KNOWN_FLASH_SCRIPT_NAMES)
    return False, (
        f"No flashing scripts ({script_list}) found in '{path}', its 'images' "
        f"subfolder, or one level of nested subfolders. Make sure this is the "
        f"extracted Fastboot ROM directory, not the original archive or an "
        f"unrelated folder."
    )


class FlashingCore:
    def __init__(self, adb_path, fastboot_path, output_callback=None):
        self.adb_path = adb_path
        self.fastboot_path = fastboot_path

        if hasattr(output_callback, 'emit') and callable(output_callback.emit):
            self._emit_status = lambda msg: output_callback.emit(msg)
        else:
            self._emit_status = lambda msg: log_message('info', msg)

        self.current_os = get_os()

        log_message('info', "FlashingCore initialized.")
        log_message('info', f"ADB Path: {self.adb_path}, Fastboot Path: {self.fastboot_path}")
        log_message('info', f"Operating System: {self.current_os}")

    def _run(self, command, cwd=None, timeout=30.0):
        """
        Thin wrapper around command_runner.run_command that also streams
        output to the GUI log and the file log, matching the old
        _execute_command's side effects. Safe from any thread.
        """
        command_str = ' '.join(command)
        self._emit_status(f"Executing command: {command_str}")
        log_message('debug', f"Executing: {command_str} in CWD: {cwd}")

        result = run_command(command, cwd=cwd, timeout=timeout)

        for line in result.lines:
            self._emit_status(line)
            log_message('debug', f"CMD OUTPUT: {line}")

        if result.timed_out:
            self._emit_status(f"Command timed out after {timeout}s.")
            log_message('error', f"Command '{command_str}' timed out.")
        elif result.error:
            self._emit_status(result.error)
            log_message('error', result.error)
        elif result.ok:
            self._emit_status("Command completed successfully.")
            log_message('info', f"Command '{command_str}' completed successfully.")
        else:
            self._emit_status(f"Command failed with exit code {result.returncode}.")
            log_message('error', f"Command '{command_str}' failed with exit code {result.returncode}")

        return result

    def detect_device(self):
        """
        One-shot fastboot device check. For live GUI updates, prefer
        device_monitor.DeviceMonitor instead — this remains here for
        callers (like get_device_info) that need a direct, synchronous check.
        """
        if not self.fastboot_path:
            self._emit_status("Fastboot path not set. Cannot detect device.")
            return None

        result = self._run([self.fastboot_path, "devices"], timeout=5.0)

        if result.ok:
            for line in result.lines:
                match = re.match(r'(\S+)\s+fastboot', line)
                if match:
                    serial = match.group(1)
                    self._emit_status(f"Detected Fastboot device: {serial}")
                    return serial

        self._emit_status("No Fastboot device detected.")
        return None

    def get_device_info(self, serial):
        """
        Retrieves device information (codename, bootloader status) for a given serial.
        """
        info = {"codename": "Unknown", "bootloader_locked": "Unknown"}
        if not self.fastboot_path or not serial:
            self._emit_status("Fastboot path or device serial not set. Cannot get device info.")
            return info

        result = self._run([self.fastboot_path, "-s", serial, "getvar", "all"], timeout=10.0)

        if result.ok:
            for line in result.lines:
                product_match = re.search(r'\(bootloader\)\s+product:(\S+)', line)
                if product_match:
                    info["codename"] = product_match.group(1)

                unlocked_match = re.search(r'\(bootloader\)\s+unlocked:(yes|no)', line)
                if unlocked_match:
                    info["bootloader_locked"] = "Unlocked" if unlocked_match.group(1) == "yes" else "Locked"

        self._emit_status(f"Device info for {serial}: Codename={info['codename']}, Bootloader={info['bootloader_locked']}")
        return info

    def extract_rom(self, rom_file_path, extract_base_dir):
        """
        Extracts a .tgz Fastboot ROM archive. If it contains a nested .zip or
        .tar, it extracts that too. Uses Python's tarfile/zipfile directly
        (not subprocess), routed through _safe_extract_tar/_safe_extract_zip
        to guard against path-traversal ("Zip Slip") entries in a malicious
        archive.
        Returns (True, extracted_directory_path) on success, (False, error_message) on failure.
        """
        if not os.path.exists(rom_file_path):
            return False, f"ROM file not found: {rom_file_path}"

        if not tarfile.is_tarfile(rom_file_path):
            return False, f"Selected file is not a valid tar archive: {rom_file_path}"

        rom_filename = os.path.basename(rom_file_path)
        if rom_filename.lower().endswith('.tgz'):
            initial_extracted_dir_name = rom_filename[:-4]
        elif rom_filename.lower().endswith('.tar.gz'):
            initial_extracted_dir_name = rom_filename[:-7]
        else:
            initial_extracted_dir_name = rom_filename + "_extracted_tgz"

        initial_extracted_full_path = os.path.join(extract_base_dir, initial_extracted_dir_name)

        if os.path.exists(initial_extracted_full_path):
            self._emit_status(f"Removing existing initial extracted directory: {initial_extracted_full_path}")
            try:
                shutil.rmtree(initial_extracted_full_path)
            except Exception as e:
                return False, f"Failed to remove existing initial extracted directory: {e}"

        self._emit_status(f"Extracting primary archive '{rom_filename}' to '{initial_extracted_full_path}'...")
        os.makedirs(initial_extracted_full_path, exist_ok=True)

        try:
            with tarfile.open(rom_file_path, "r:gz") as tar:
                _safe_extract_tar(tar, initial_extracted_full_path)
            self._emit_status("Primary archive extraction complete.")

            nested_archive_file = None
            nested_archive_type = None

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

                nested_extracted_dir_name = os.path.basename(nested_archive_file)
                if nested_archive_type == 'zip':
                    nested_extracted_dir_name = nested_extracted_dir_name[:-4]
                elif nested_archive_type == 'tar':
                    if nested_extracted_dir_name.lower().endswith('.tgz'):
                        nested_extracted_dir_name = nested_extracted_dir_name[:-4]
                    elif nested_extracted_dir_name.lower().endswith('.tar.gz'):
                        nested_extracted_dir_name = nested_extracted_dir_name[:-7]
                    elif nested_extracted_dir_name.lower().endswith('.tar'):
                        nested_extracted_dir_name = nested_extracted_dir_name[:-4]

                final_extracted_full_path = os.path.join(initial_extracted_full_path, nested_extracted_dir_name)

                if os.path.exists(final_extracted_full_path):
                    self._emit_status(f"Removing existing nested extracted directory: {final_extracted_full_path}")
                    try:
                        shutil.rmtree(final_extracted_full_path)
                    except Exception as e:
                        return False, f"Failed to remove existing nested extracted directory: {e}"

                os.makedirs(final_extracted_full_path, exist_ok=True)

                if nested_archive_type == 'zip':
                    with zipfile.ZipFile(nested_archive_file, 'r') as zip_ref:
                        _safe_extract_zip(zip_ref, final_extracted_full_path)
                elif nested_archive_type == 'tar':
                    tar_mode = "r"
                    if nested_archive_file.lower().endswith('.gz') or nested_archive_file.lower().endswith('.tgz'):
                        tar_mode = "r:gz"
                    elif nested_archive_file.lower().endswith('.bz2'):
                        tar_mode = "r:bz2"

                    with tarfile.open(nested_archive_file, tar_mode) as tar_ref:
                        _safe_extract_tar(tar_ref, final_extracted_full_path)

                self._emit_status(f"Nested {nested_archive_type.upper()} extraction complete.")

                rom_root_found = False
                for item in os.listdir(final_extracted_full_path):
                    item_path = os.path.join(final_extracted_full_path, item)
                    if os.path.isdir(item_path):
                        if any(f.startswith('flash_all') and (f.endswith('.sh') or f.endswith('.bat')) for f in os.listdir(item_path)) or \
                           os.path.exists(os.path.join(item_path, 'images')):
                            final_extracted_full_path = item_path
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
        Orchestrates the device flashing process. Runs the flash script through
        command_runner instead of the old blocking Popen loop, with a generous
        timeout since flashing genuinely takes minutes.
        Returns (True, message) on success, (False, error_message) on failure.
        """
        self._emit_status(f"Starting device flashing process with mode: {flash_mode}...")

        # Resolve the real ROM root first, via the same validate_rom_directory()
        # used for the "already-extracted folder" import path in the GUI. This
        # makes flash_rom() work identically whether extracted_rom_path came
        # from extract_rom() (already resolved to the nested folder, when there
        # was one) or from a manually-selected folder that might be one level
        # above the actual ROM root.
        root_ok, root_result = validate_rom_directory(extracted_rom_path)
        if not root_ok:
            self._emit_status(root_result)
            log_message('error', root_result)
            return False, root_result
        extracted_rom_path = root_result

        script_base_name = ""
        if flash_mode == FlashModes.CLEAN_ALL:
            script_base_name = "flash_all"
        elif flash_mode == FlashModes.SAVE_USER_DATA:
            script_base_name = "flash_all_except_data_storage"
        elif flash_mode == FlashModes.LOCK_BOOTLOADER:
            script_base_name = "flash_all_lock"
        elif flash_mode == FlashModes.SAVE_DATA_AND_STORAGE:
            script_base_name = "flash_all_except_data_storage"
        else:
            error_msg = f"Error: Unknown flash mode selected: {flash_mode}"
            self._emit_status(error_msg)
            log_message('error', error_msg)
            return False, error_msg

        if self.current_os == "win32":
            script_full_name = f"{script_base_name}.bat"
        elif self.current_os in ("linux", "darwin"):
            script_full_name = f"{script_base_name}.sh"
        else:
            error_msg = f"Error: Unsupported operating system: {self.current_os}"
            self._emit_status(error_msg)
            log_message('error', error_msg)
            return False, error_msg

        images_dir = os.path.join(extracted_rom_path, "images")
        flash_script_path = None

        candidate_script_in_images = os.path.join(images_dir, script_full_name)
        if os.path.exists(candidate_script_in_images):
            flash_script_path = candidate_script_in_images
        else:
            candidate_script_in_root = os.path.join(extracted_rom_path, script_full_name)
            if os.path.exists(candidate_script_in_root):
                flash_script_path = candidate_script_in_root
            else:
                error_msg = f"Error: Flashing script '{script_full_name}' not found in '{images_dir}' or '{extracted_rom_path}'."
                self._emit_status(error_msg)
                log_message('error', error_msg)
                return False, error_msg

        if self.current_os in ("linux", "darwin"):
            try:
                os.chmod(flash_script_path, 0o755)
            except Exception as e:
                self._emit_status(f"Warning: Could not set executable permissions for {flash_script_path}: {e}")
                log_message('warning', f"Failed to chmod {flash_script_path}: {e}")

        script_cwd = os.path.dirname(flash_script_path)

        # Flashing genuinely takes several minutes — a long timeout here is
        # intentional, unlike the short ones used for detection/status checks.
        result = self._run([flash_script_path], cwd=script_cwd, timeout=900.0)

        if result.ok:
            return True, "Flashing process completed."
        elif result.timed_out:
            return False, "Flashing timed out after 15 minutes. Check the device and logs before retrying."
        else:
            return False, "Flashing process failed. Check logs for details."