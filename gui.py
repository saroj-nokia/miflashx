import sys
import os
import re
import inspect
import subprocess

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QLabel,
                             QTextEdit, QComboBox, QFileDialog, QGroupBox,
                             QMessageBox, QProgressBar, QSizePolicy, QSpacerItem,
                             QStatusBar)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QFont

from core import FlashingCore, FlashModes
from utils import log_message, get_os, check_udev_rules, install_udev_rules, add_to_adbusers_group, find_adb_fastboot
from device_monitor import DeviceMonitor, DeviceState


# Worker Thread for long-running operations (ROM extraction, flashing, udev tasks).
#
# Bug fixed here: the previous version unconditionally injected an
# output_callback kwarg into every function it called. That's only valid for
# functions written to accept it — extract_rom(), flash_rom(),
# install_udev_rules(), and add_to_adbusers_group() vary on this, and calling
# any of the ones that don't accept it raised "got an unexpected keyword
# argument 'output_callback'" and killed the worker before it did anything.
# Now Worker inspects the target function first and only passes the callback
# to functions that actually declare it.
class Worker(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            def worker_output_callback(level, msg):
                self.progress.emit(f"[{level.upper()}] {msg}")

            sig = inspect.signature(self.func)
            accepts_callback = (
                'output_callback' in sig.parameters
                or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            )
            if accepts_callback:
                self.kwargs['output_callback'] = worker_output_callback

            result, message = self.func(*self.args, **self.kwargs)
            self.finished.emit(result, message)
        except Exception as e:
            error_msg = f"An unexpected error occurred in worker thread: {e}"
            self.progress.emit(f"[ERROR] {error_msg}")
            self.finished.emit(False, error_msg)


class DeviceInfoWorker(QThread):
    """
    Fetches `fastboot getvar all` output off the GUI thread. Previously this
    ran inline inside the QTimer slot alongside detect_device() — same
    blocking-subprocess-on-GUI-thread problem, just less visible because it
    only fired once per new device instead of every 2 seconds.
    """
    info_ready = pyqtSignal(dict)

    def __init__(self, flashing_core: FlashingCore, serial: str, parent=None):
        super().__init__(parent)
        self.flashing_core = flashing_core
        self.serial = serial

    def run(self):
        info = self.flashing_core.get_device_info(self.serial)
        self.info_ready.emit(info)


class MiFlashX(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiFlashX (Xiaomi Fastboot Flashing Tool for Linux)")
        self.setGeometry(100, 100, 900, 700)

        # Prefer the user's icon theme; fall back to the bundled icon only if
        # the desktop environment has no "phone" icon of its own.
        icon = QIcon.fromTheme("phone", QIcon())
        if icon.isNull():
            try:
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
                if os.path.exists(icon_path):
                    icon = QIcon(icon_path)
            except Exception as e:
                log_message('error', f"Could not set window icon: {e}")
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.adb_path = None
        self.fastboot_path = None
        self.flashing_core = None
        self.current_serial = None
        self.current_device_codename = "Unknown"
        self.current_bootloader_status = "Unknown"
        self.extracted_rom_path = None
        self.device_monitor = None
        self._info_worker = None

        self.init_ui()
        # No apply_qss() call anymore — see module docstring below for why.
        self.check_initial_setup()

        if self.flashing_core:
            self.device_monitor = DeviceMonitor(self.fastboot_path, parent=self)
            self.device_monitor.device_changed.connect(self.on_device_state_changed)

    def closeEvent(self, event):
        if self.device_monitor:
            self.device_monitor.stop()
        super().closeEvent(event)

    def init_ui(self):
        """Initializes the main graphical user interface elements."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)

        # --- System Status & Device Info Group ---
        status_group = QGroupBox("System Status & Device Info")
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(8)

        self.adb_fastboot_status_label = QLabel("ADB/Fastboot: Checking...")
        self.udev_status_label = QLabel("Udev Rules (Linux): Checking...")
        self.adbusers_status_label = QLabel("User in 'adbusers' group: Checking...")
        self.device_status_label = QLabel("Device: Not connected")
        self.device_info_label = QLabel("Info: N/A")

        status_layout.addWidget(self.adb_fastboot_status_label)
        status_layout.addWidget(self.udev_status_label)
        status_layout.addWidget(self.adbusers_status_label)
        status_layout.addWidget(self.device_status_label)
        status_layout.addWidget(self.device_info_label)

        linux_buttons_layout = QHBoxLayout()
        self.install_udev_button = QPushButton("Fix Udev Rules (Linux)")
        self.install_udev_button.clicked.connect(self.install_udev_rules_action)
        self.install_udev_button.setEnabled(False)
        linux_buttons_layout.addWidget(self.install_udev_button)

        self.add_adbusers_button = QPushButton("Add User to 'adbusers' group (Linux)")
        self.add_adbusers_button.clicked.connect(self.add_to_adbusers_group_action)
        self.add_adbusers_button.setEnabled(False)
        linux_buttons_layout.addWidget(self.add_adbusers_button)

        self.linux_buttons_widget = QWidget()
        self.linux_buttons_widget.setLayout(linux_buttons_layout)
        status_layout.addWidget(self.linux_buttons_widget)

        main_layout.addWidget(status_group)

        # --- ROM Selection Section ---
        rom_selection_group = QGroupBox("ROM Selection & Extraction")
        rom_selection_layout = QVBoxLayout(rom_selection_group)
        rom_selection_layout.setSpacing(10)

        rom_path_layout = QHBoxLayout()
        self.rom_path_input = QLineEdit()
        self.rom_path_input.setPlaceholderText("Select Fastboot ROM (.tgz)")
        self.rom_path_input.setReadOnly(True)
        self.browse_rom_button = QPushButton("Browse")
        self.browse_rom_button.clicked.connect(self.browse_rom)
        rom_path_layout.addWidget(self.rom_path_input)
        rom_path_layout.addWidget(self.browse_rom_button)
        rom_selection_layout.addLayout(rom_path_layout)

        self.extract_rom_button = QPushButton("Extract ROM")
        self.extract_rom_button.clicked.connect(self.extract_rom)
        self.extract_rom_button.setEnabled(False)
        rom_selection_layout.addWidget(self.extract_rom_button)

        self.extracted_path_label = QLabel("Extracted ROM: None")
        rom_selection_layout.addWidget(self.extracted_path_label)

        main_layout.addWidget(rom_selection_group)

        # --- Flashing Options Section ---
        flashing_group = QGroupBox("Flashing Options")
        flashing_layout = QVBoxLayout(flashing_group)
        flashing_layout.setSpacing(10)

        flashing_layout.addWidget(QLabel("Select Flashing Mode:"))
        self.flash_mode_combo = QComboBox()
        self.flash_mode_combo.addItem("Flash all (clean install, wipe all data)", FlashModes.CLEAN_ALL)
        self.flash_mode_combo.addItem("Flash all except storage (keep user data)", FlashModes.SAVE_USER_DATA)
        self.flash_mode_combo.addItem("Flash all except data and storage (safest for updates, keeps apps and data)", FlashModes.SAVE_DATA_AND_STORAGE)
        self.flash_mode_combo.addItem("Flash all and lock bootloader (use with caution!)", FlashModes.LOCK_BOOTLOADER)
        flashing_layout.addWidget(self.flash_mode_combo)

        self.flash_button = QPushButton("Start Flashing")
        self.flash_button.clicked.connect(self.start_flashing_confirmation)
        self.flash_button.setEnabled(False)
        flashing_layout.addWidget(self.flash_button)

        main_layout.addWidget(flashing_group)

        # --- Progress & Log Section ---
        log_group = QGroupBox("Log Output")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("monospace", 9))
        log_layout.addWidget(self.log_output)
        main_layout.addWidget(log_group, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Operation Progress: %p%")
        main_layout.addWidget(self.progress_bar)

        main_layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")

    # apply_qss() has been removed entirely. The old version hardcoded a
    # light-theme-only stylesheet (#f0f2f5 backgrounds, #333333 text, etc.)
    # across every widget type, which fought whatever GTK/Breeze/etc. theme
    # the user's desktop was actually running and looked wrong in dark mode.
    # Qt6's platform theme integration (qt6ct, QT_QPA_PLATFORMTHEME=gtk3, or
    # native Breeze on KDE) already makes an unstyled QWidget app match the
    # system theme, light or dark, automatically. The status-color spans
    # below (green/red in labels) are semantic indicators, not theming, and
    # are left as-is — they read fine against both light and dark palettes.

    def append_log(self, message):
        """Appends a message to the log output QTextEdit and the file log."""
        color = "gray"
        message_upper = message.upper()
        if "[ERROR]" in message_upper:
            color = "#d9534f"
        elif "[WARNING]" in message_upper:
            color = "#e08e0b"
        elif "[INFO]" in message_upper:
            color = "#3b7dd8"

        self.log_output.append(f"<span style='color:{color}'>{message}</span>")
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

        level_match = re.match(r"\[(\w+)\]", message)
        log_level = level_match.group(1).lower() if level_match else 'info'
        log_message(log_level, message)

    def _adbusers_ok(self):
        current_user = os.getenv('USER')
        if not current_user:
            return False
        try:
            result = subprocess.run(["groups", current_user], capture_output=True, text=True, check=True)
            return "adbusers" in result.stdout
        except Exception:
            return False

    def check_initial_setup(self):
        """
        Performs initial checks on application startup: ADB/Fastboot presence,
        udev rules status, and 'adbusers' group membership.
        """
        self.append_log("[INFO] Performing initial setup checks...")

        self.adb_path, self.fastboot_path = find_adb_fastboot()
        if self.adb_path and self.fastboot_path:
            self.adb_fastboot_status_label.setText("ADB/Fastboot: <font color='green'>Found</font>")
            self.flashing_core = FlashingCore(
                adb_path=self.adb_path,
                fastboot_path=self.fastboot_path,
                output_callback=lambda level, msg: self.append_log(f"[{level.upper()}] {msg}")
            )
            self.append_log(f"[INFO] ADB: {self.adb_path}, Fastboot: {self.fastboot_path}")
            self.extract_rom_button.setEnabled(True)
        else:
            self.adb_fastboot_status_label.setText("ADB/Fastboot: <font color='red'>Not Found</font>. Please ensure Android SDK Platform Tools are installed and accessible.")
            self.append_log('[ERROR] ADB/Fastboot not found. Cannot proceed without them. Ensure they are in your system PATH or correctly bundled.')
            self.flash_button.setEnabled(False)
            self.extract_rom_button.setEnabled(False)
            self.device_status_label.setText("Device: <font color='red'>N/A (Tools Missing)</font>")
            self.device_info_label.setText("Info: N/A")
            self.udev_status_label.setText("Udev Rules (Linux): N/A (Tools Missing)")
            self.adbusers_status_label.setText("User in 'adbusers' group: N/A (Tools Missing)")
            self.linux_buttons_widget.setVisible(False)
            return

        if get_os() == "linux":
            udev_ok, udev_msg = check_udev_rules()
            if udev_ok:
                self.udev_status_label.setText(f"Udev Rules (Linux): <font color='green'>{udev_msg}</font>")
                self.install_udev_button.setEnabled(False)
            else:
                self.udev_status_label.setText(f"Udev Rules (Linux): <font color='red'>{udev_msg}</font>")
                self.install_udev_button.setEnabled(True)
                self.append_log('[WARNING] Udev rules might be missing or incorrect for Xiaomi/Android devices. This can cause \'no permissions\' errors with Fastboot.')
                self.append_log('[INFO] Click \'Fix Udev Rules\' if you encounter device detection/permission issues.')

            adbusers_ok = self._adbusers_ok()
            self.adbusers_status_label.setText("User in 'adbusers' group: <font color='green'>Yes</font>" if adbusers_ok else "User in 'adbusers' group: <font color='red'>No</font>")
            self.add_adbusers_button.setEnabled(not adbusers_ok)
            if not adbusers_ok:
                self.append_log('[WARNING] Your user is not in the \'adbusers\' group. This can cause permission issues. Click \'Add User to adbusers group\'.')
                self.append_log('[INFO] Remember to log out and back in after adding user to group for changes to take effect.')
        else:
            self.udev_status_label.setText("Udev Rules (Linux): N/A (Not Linux)")
            self.adbusers_status_label.setText("User in 'adbusers' group: N/A (Not Linux)")
            self.linux_buttons_widget.setVisible(False)

        self.append_log('[INFO] Initial setup checks complete. Waiting for device connection...')
        self.statusBar.showMessage("Initial checks complete. Ready for device.")

    def on_device_state_changed(self, state: DeviceState):
        """
        Slot connected to DeviceMonitor.device_changed. Replaces the old
        QTimer-driven detect_device_periodic(): this now fires only when the
        device state actually changes, driven by real udev events (with a
        5s safety-net poll), and the fastboot check itself always runs on a
        background thread — never here on the GUI thread.
        """
        if state.mode == "timeout":
            self.device_status_label.setText("Device: <font color='orange'>Fastboot not responding (timed out)</font>")
            self.append_log('[WARNING] fastboot devices timed out. The device or USB port may be in a bad state — try replugging.')
            return

        if state.connected and state.serial != self.current_serial:
            self.current_serial = state.serial
            self.device_status_label.setText(f"Device: <font color='green'>Connected ({state.serial})</font>")
            self.append_log(f'[INFO] Device connected: {state.serial}')

            # Fetch getvar-all info on a background thread rather than inline.
            self._info_worker = DeviceInfoWorker(self.flashing_core, state.serial, parent=self)
            self._info_worker.info_ready.connect(self._on_device_info_ready)
            self._info_worker.start()

        elif not state.connected and self.current_serial:
            self.current_serial = None
            self.current_device_codename = "Unknown"
            self.current_bootloader_status = "Unknown"
            self.device_status_label.setText("Device: <font color='red'>Disconnected</font>")
            self.device_info_label.setText("Info: N/A")
            self.flash_button.setEnabled(False)
            self.append_log('[INFO] Device disconnected.')

    def _on_device_info_ready(self, info: dict):
        self.current_device_codename = info.get('codename', 'Unknown')
        self.current_bootloader_status = info.get('bootloader_locked', 'Unknown')
        self.device_info_label.setText(f"Info: Codename: {self.current_device_codename}, Bootloader: {self.current_bootloader_status}")
        self.append_log(f'[INFO] Device info: Codename: {self.current_device_codename}, Bootloader: {self.current_bootloader_status}')

        self.flash_button.setEnabled(self.extracted_rom_path is not None and self.current_bootloader_status == "Unlocked")
        if self.extracted_rom_path and self.current_bootloader_status != "Unlocked":
            self.append_log('[WARNING] Bootloader is locked. Please unlock it officially before flashing a Fastboot ROM. Flashing button remains disabled.')

    def browse_rom(self):
        file_dialog = QFileDialog(self)
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        file_dialog.setNameFilter("Fastboot ROMs (*.tgz *.tar.gz)")

        if file_dialog.exec():
            selected_file = file_dialog.selectedFiles()[0]
            self.rom_path_input.setText(selected_file)
            self.extracted_rom_path = None
            self.extracted_path_label.setText("Extracted ROM: None (Click 'Extract ROM')")
            self.extract_rom_button.setEnabled(True)
            self.flash_button.setEnabled(False)

    def extract_rom(self):
        rom_file = self.rom_path_input.text()
        if not rom_file:
            QMessageBox.warning(self, "No ROM Selected", "Please select a Fastboot ROM (.tgz) file first.")
            return
        if not os.path.exists(rom_file):
            QMessageBox.critical(self, "File Not Found", f"The selected ROM file does not exist: {rom_file}")
            return

        extract_base_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "miflashx", "roms")
        os.makedirs(extract_base_dir, exist_ok=True)

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Extracting: %p%")
        self.append_log(f'[INFO] Starting ROM extraction from \'{os.path.basename(rom_file)}\'...')
        self.set_ui_enabled(False)
        self.statusBar.showMessage("Extracting ROM...")

        def on_extract_finished(success, message):
            self.set_ui_enabled(True)
            self.progress_bar.setValue(100 if success else 0)
            self.progress_bar.setFormat("Extraction: %p%")
            if success:
                self.extracted_rom_path = message
                self.extracted_path_label.setText(f"Extracted ROM: <font color='green'>{os.path.basename(self.extracted_rom_path)}</font>")
                QMessageBox.information(self, "Extraction Complete", "ROM extracted successfully!")
                self.statusBar.showMessage("ROM extracted. Ready to flash.")
                self.flash_button.setEnabled(self.current_serial is not None and self.current_bootloader_status == "Unlocked")
                if not self.flash_button.isEnabled():
                    self.append_log('[WARNING] Flashing button remains disabled. Ensure a device is connected in Fastboot mode and its bootloader is unlocked.')
            else:
                self.extracted_rom_path = None
                self.extracted_path_label.setText("Extracted ROM: <font color='red'>Failed</font>")
                QMessageBox.critical(self, "Extraction Failed", message)
                self.statusBar.showMessage("ROM extraction failed.")

        self.worker = Worker(self.flashing_core.extract_rom, rom_file_path=rom_file, extract_base_dir=extract_base_dir)
        self.worker.finished.connect(on_extract_finished)
        self.worker.progress.connect(self.append_log)
        self.worker.start()

    def start_flashing_confirmation(self):
        if not self.extracted_rom_path:
            QMessageBox.warning(self, "No ROM Extracted", "Please extract a Fastboot ROM first.")
            return
        if not self.current_serial:
            QMessageBox.warning(self, "No Device Connected", "Please connect your Xiaomi device in Fastboot mode.")
            return

        if self.current_bootloader_status != "Unlocked":
            QMessageBox.critical(self, "Bootloader Locked", "Your device's bootloader is locked. Flashing a Fastboot ROM requires an unlocked bootloader. Please unlock it officially first (using Xiaomi's Mi Unlock Tool).")
            return

        selected_mode_text = self.flash_mode_combo.currentText()
        flash_mode_data = self.flash_mode_combo.currentData()

        confirmation_msg = f"You are about to flash the ROM using '{selected_mode_text}' mode to device '{self.current_serial}' (Codename: {self.current_device_codename}).\n\n"
        if flash_mode_data == FlashModes.CLEAN_ALL:
            confirmation_msg += "<b style='color: red;'>WARNING: This mode will wipe ALL data on your device! Ensure you have a backup.</b>\n"
        elif flash_mode_data == FlashModes.SAVE_USER_DATA:
            confirmation_msg += "<b style='color: orange;'>WARNING: This mode keeps user data but wipes the system partition. Proceed with caution.</b>\n"
        elif flash_mode_data == FlashModes.SAVE_DATA_AND_STORAGE:
            confirmation_msg += "<b style='color: green;'>This mode aims to keep your user data and apps. It's generally safer for updates.</b>\n"
        elif flash_mode_data == FlashModes.LOCK_BOOTLOADER:
            confirmation_msg += "<b style='color: red;'>EXTREME CAUTION: This mode will lock your bootloader after flashing. If you flash an incompatible ROM or encounter errors, your device may be bricked! Only use this if you are absolutely sure of the ROM's compatibility and integrity.</b>\n"

        confirmation_msg += "\nEnsure your device battery is at least 50% charged and do NOT disconnect the device during flashing.\n"
        confirmation_msg += "\nAre you absolutely sure you want to proceed?"

        reply = QMessageBox.question(self, "Confirm Flashing Operation", confirmation_msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.start_flashing()
        else:
            self.append_log('[INFO] Flashing cancelled by user.')

    def start_flashing(self):
        flash_mode = self.flash_mode_combo.currentData()

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Flashing: %p%")
        self.append_log(f'[INFO] Initiating flashing process for \'{self.current_device_codename}\' with mode: {flash_mode}...')
        self.set_ui_enabled(False)
        self.statusBar.showMessage("Flashing device...")

        def on_flash_finished(success, message):
            self.set_ui_enabled(True)
            self.progress_bar.setValue(100 if success else 0)
            self.progress_bar.setFormat("Flashing: %p%")
            if success:
                QMessageBox.information(self, "Flashing Complete", "ROM flashed successfully! Your device should now reboot. First boot may take a while.")
                self.append_log('[INFO] Flashing process finished successfully.')
                self.statusBar.showMessage("Flashing complete. Device should reboot.")
            else:
                QMessageBox.critical(self, "Flashing Failed", f"Flashing failed: {message}. Check logs for detailed error output.")
                self.append_log('[ERROR] Flashing process failed.')
                self.statusBar.showMessage("Flashing failed.")

        self.worker = Worker(self.flashing_core.flash_rom, extracted_rom_path=self.extracted_rom_path, flash_mode=flash_mode)
        self.worker.finished.connect(on_flash_finished)
        self.worker.progress.connect(self.append_log)
        self.worker.start()

    def set_ui_enabled(self, enabled):
        self.browse_rom_button.setEnabled(enabled)
        self.extract_rom_button.setEnabled(enabled and self.rom_path_input.text() != "")
        self.flash_mode_combo.setEnabled(enabled)

        self.flash_button.setEnabled(enabled and self.extracted_rom_path is not None and
                                     self.current_serial is not None and self.current_bootloader_status == "Unlocked")

        if get_os() == "linux":
            udev_ok, _ = check_udev_rules()
            adbusers_ok = self._adbusers_ok()
            self.install_udev_button.setEnabled(enabled and not udev_ok)
            self.add_adbusers_button.setEnabled(enabled and not adbusers_ok)
        else:
            self.install_udev_button.setEnabled(False)
            self.add_adbusers_button.setEnabled(False)

    def install_udev_rules_action(self):
        reply = QMessageBox.question(self, "Install Udev Rules",
                                     "This action requires administrator privileges. "
                                     "It will write/update udev rules for Android devices and reload udev configuration. "
                                     "You'll be prompted via your system's authentication dialog.\n\n"
                                     "Do you want to proceed?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.append_log('[INFO] Attempting to install udev rules...')
            self.set_ui_enabled(False)
            self.statusBar.showMessage("Installing udev rules...")

            def on_install_finished(success, message):
                self.set_ui_enabled(True)
                if success:
                    QMessageBox.information(self, "Udev Rules Installed", message)
                    self.append_log(f'[INFO] {message}')
                    self.statusBar.showMessage("Udev rules installed.")
                    self.check_initial_setup()
                else:
                    QMessageBox.critical(self, "Udev Rules Installation Failed", message)
                    self.append_log(f'[ERROR] {message}')
                    self.statusBar.showMessage("Udev rules installation failed.")

            self.worker = Worker(install_udev_rules)
            self.worker.finished.connect(on_install_finished)
            self.worker.progress.connect(self.append_log)
            self.worker.start()

    def add_to_adbusers_group_action(self):
        reply = QMessageBox.question(self, "Add User to 'adbusers' Group",
                                     "This action requires administrator privileges. "
                                     "It will add your current user to the 'adbusers' group, which can help with device permissions. "
                                     "You'll be prompted via your system's authentication dialog.\n\n"
                                     "<b style='color: red;'>Important: You will need to log out and log back in for this change to take effect!</b>\n\n"
                                     "Do you want to proceed?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.append_log('[INFO] Attempting to add user to \'adbusers\' group...')
            self.set_ui_enabled(False)
            self.statusBar.showMessage("Adding user to 'adbusers' group...")

            def on_add_finished(success, message):
                self.set_ui_enabled(True)
                if success:
                    QMessageBox.information(self, "User Added to Group", message)
                    self.append_log(f'[INFO] {message}')
                    self.statusBar.showMessage("User added to 'adbusers' group. Please re-login.")
                    self.check_initial_setup()
                else:
                    QMessageBox.critical(self, "Add User to Group Failed", message)
                    self.append_log(f'[ERROR] {message}')
                    self.statusBar.showMessage("Failed to add user to 'adbusers' group.")

            self.worker = Worker(add_to_adbusers_group)
            self.worker.finished.connect(on_add_finished)
            self.worker.progress.connect(self.append_log)
            self.worker.start()