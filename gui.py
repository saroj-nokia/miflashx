import sys
import os
import time # For time.sleep or delays if needed
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QLabel,
                             QTextEdit, QComboBox, QFileDialog, QGroupBox,
                             QMessageBox, QProgressBar, QSizePolicy, QSpacerItem)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QIcon, QFont

# Ensure your core.py and utils.py are in the same directory or accessible via Python path
from core import FlashingCore
from utils import log_message, get_os, check_udev_rules, install_udev_rules, add_to_adbusers_group, find_adb_fastboot, generate_udev_rule_content

# Worker Thread for long-running operations (e.g., ROM extraction, flashing, udev tasks)
class Worker(QThread):
    finished = pyqtSignal(bool, str) # Emits (success, message) when done
    progress = pyqtSignal(str)       # For log messages from core operations

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            # If the function accepts an 'output_callback', we pass our progress signal to it.
            # This allows functions in core.py and utils.py to send real-time logs back to the GUI.
            if 'output_callback' in self.kwargs:
                # Store the original callback if it was passed, so we can chain it
                original_callback = self.kwargs['output_callback']
                # Replace the callback with one that emits to the GUI and also calls original if it exists
                self.kwargs['output_callback'] = lambda level, msg: (
                    self.progress.emit(f"[{level.upper()}] {msg}"),
                    original_callback(level, msg) if original_callback else None
                )
            elif 'output_callback' not in self.kwargs:
                # If the function doesn't expect an output_callback, just provide one for our progress signal
                self.kwargs['output_callback'] = lambda level, msg: self.progress.emit(f"[{level.upper()}] {msg}")

            # Call the target function
            # We expect functions in core.py and utils.py to return (success_bool, result_message)
            result, message = self.func(*self.args, **self.kwargs)
            self.finished.emit(result, message)
        except Exception as e:
            error_msg = f"An unexpected error occurred in worker thread: {e}"
            self.progress.emit(f"[ERROR] {error_msg}")
            self.finished.emit(False, error_msg)

class MiFlashX(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiFlashX (Xiaomi Fastboot Flashing Tool for Linux)")
        self.setGeometry(100, 100, 850, 750) # Set initial window size

        # Set application icon
        try:
            # Path to icon.png relative to the script/executable
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
            else:
                log_message('warning', f"Application icon not found at: {icon_path}")
        except Exception as e:
            log_message('error', f"Could not set window icon: {e}")

        self.flashing_core = None # Will be initialized after checking ADB/Fastboot presence
        self.current_serial = None
        self.current_device_codename = "Unknown"
        self.current_bootloader_status = "Unknown"
        self.extracted_rom_path = None
        
        self.init_ui()
        self.check_initial_setup()
        
        # Setup a QTimer for periodic device detection (more robust for GUI than a looping thread)
        self.device_detect_timer = QTimer(self)
        self.device_detect_timer.setInterval(2000) # Check every 2 seconds
        self.device_detect_timer.timeout.connect(self.detect_device_periodic)
        self.device_detect_timer.start()

    def init_ui(self):
        """Initializes the main graphical user interface elements."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Status Section ---
        status_group = QGroupBox("System Status & Device Info")
        status_layout = QVBoxLayout(status_group)
        
        # Labels to display status information
        self.adb_fastboot_status_label = QLabel("ADB/Fastboot: Checking...")
        self.udev_status_label = QLabel("Udev Rules (Linux): Checking...")
        self.device_status_label = QLabel("Device: Not connected")
        self.device_info_label = QLabel("Info: N/A")
        
        status_layout.addWidget(self.adb_fastboot_status_label)
        status_layout.addWidget(self.udev_status_label)
        status_layout.addWidget(self.device_status_label)
        status_layout.addWidget(self.device_info_label)

        # Linux Specific Buttons for fixing permissions
        linux_buttons_layout = QHBoxLayout()
        self.install_udev_button = QPushButton("Fix Udev Rules (Linux)")
        self.install_udev_button.clicked.connect(self.install_udev_rules_action)
        self.install_udev_button.setEnabled(False) # Enable only if needed
        linux_buttons_layout.addWidget(self.install_udev_button)
        
        self.add_adbusers_button = QPushButton("Add User to 'adbusers' group (Linux)")
        self.add_adbusers_button.clicked.connect(self.add_to_adbusers_group_action)
        self.add_adbusers_button.setEnabled(False) # Enable only if needed
        linux_buttons_layout.addWidget(self.add_adbusers_button)

        # Widget to group and control visibility of Linux-specific buttons
        self.linux_buttons_widget = QWidget()
        self.linux_buttons_widget.setLayout(linux_buttons_layout)
        status_layout.addWidget(self.linux_buttons_widget)
        
        main_layout.addWidget(status_group)

        # --- ROM Selection Section ---
        rom_selection_group = QGroupBox("ROM Selection & Extraction")
        rom_selection_layout = QVBoxLayout(rom_selection_group)

        rom_path_layout = QHBoxLayout()
        self.rom_path_input = QLineEdit()
        self.rom_path_input.setPlaceholderText("Select Fastboot ROM (.tgz)")
        self.rom_path_input.setReadOnly(True) # Make read-only as path is selected via browse button
        self.browse_rom_button = QPushButton("Browse")
        self.browse_rom_button.clicked.connect(self.browse_rom)
        rom_path_layout.addWidget(self.rom_path_input)
        rom_path_layout.addWidget(self.browse_rom_button)
        rom_selection_layout.addLayout(rom_path_layout)

        self.extract_rom_button = QPushButton("Extract ROM")
        self.extract_rom_button.clicked.connect(self.extract_rom)
        self.extract_rom_button.setEnabled(False) # Disabled until a ROM is selected
        rom_selection_layout.addWidget(self.extract_rom_button)
        
        self.extracted_path_label = QLabel("Extracted ROM: None")
        rom_selection_layout.addWidget(self.extracted_path_label)

        main_layout.addWidget(rom_selection_group)

        # --- Flashing Options Section ---
        flashing_group = QGroupBox("Flashing Options")
        flashing_layout = QVBoxLayout(flashing_group)

        flashing_layout.addWidget(QLabel("Select Flashing Mode:"))
        self.flash_mode_combo = QComboBox()
        # Add items with user-friendly text and corresponding data values for core.py
        self.flash_mode_combo.addItem("Flash all (clean install, wipe all data)", "clean_all")
        self.flash_mode_combo.addItem("Flash all except storage (keep user data)", "except_storage")
        self.flash_mode_combo.addItem("Flash all except data and storage (safest for updates, keeps apps and data)", "except_data_storage")
        flashing_layout.addWidget(self.flash_mode_combo)

        self.flash_button = QPushButton("Start Flashing")
        self.flash_button.clicked.connect(self.start_flashing_confirmation)
        self.flash_button.setEnabled(False) # Disabled until ROM is extracted and device detected/unlocked
        flashing_layout.addWidget(self.flash_button)

        main_layout.addWidget(flashing_group)

        # --- Progress & Log Section ---
        log_group = QGroupBox("Log Output")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True) # Make log output read-only
        self.log_output.setFont(QFont("monospace", 9)) # Use monospace font for logs
        self.log_output.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ccc;")
        log_layout.addWidget(self.log_output)
        main_layout.addWidget(log_group, 1) # Give it stretch so it expands with window

        # --- Progress Bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True) # Show percentage text
        self.progress_bar.setFormat("Operation Progress: %p%")
        main_layout.addWidget(self.progress_bar)
        
        # Add a flexible spacer to push elements to the top if window is very large
        main_layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))


    def append_log(self, message):
        """
        Appends a message to the log output QTextEdit, applying color based on content.
        Assumes messages from workers are already formatted with "[LEVEL]".
        """
        color = "black"
        # Determine color based on keywords in the message
        message_upper = message.upper()
        if "[ERROR]" in message_upper:
            color = "red"
        elif "[WARNING]" in message_upper:
            color = "darkorange" # Use darkorange for better contrast
        elif "[INFO]" in message_upper:
            color = "darkblue" # Use darkblue for better contrast
        elif "[DEBUG]" in message_upper:
            color = "gray"
        
        self.log_output.append(f"<font color='{color}'>{message}</font>")
        # Scroll to the bottom to show latest messages
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())
        
        # Also write to the file log via utils.log_message
        # Extract level from message if present, or default to info
        level_match = re.match(r"\[(\w+)\]", message)
        log_level = level_match.group(1).lower() if level_match else 'info'
        log_message(log_level, message)

    def check_initial_setup(self):
        """
        Performs initial checks on application startup:
        1. ADB/Fastboot executables presence.
        2. Linux udev rules status.
        Updates UI labels accordingly.
        """
        self.append_log("[INFO] Performing initial setup checks...")
        
        # 1. Check ADB/Fastboot executables
        self.adb_path, self.fastboot_path = find_adb_fastboot()
        if self.adb_path and self.fastboot_path:
            self.adb_fastboot_status_label.setText("ADB/Fastboot: <font color='green'>Found</font>")
            # Initialize FlashingCore only if binaries are found
            self.flashing_core = FlashingCore(output_callback=lambda level, msg: self.append_log(f"[{level.upper()}] {msg}"))
            self.append_log('[INFO]', f"ADB: {self.adb_path}, Fastboot: {self.fastboot_path}")
            self.extract_rom_button.setEnabled(True) # Enable ROM selection if tools are present
        else:
            self.adb_fastboot_status_label.setText("ADB/Fastboot: <font color='red'>Not Found</font>. Please ensure Android SDK Platform Tools are installed and accessible.")
            self.append_log('[ERROR]', "ADB/Fastboot not found. Cannot proceed without them. Ensure they are in your system PATH or correctly bundled.")
            self.flash_button.setEnabled(False)
            self.extract_rom_button.setEnabled(False)
            return

        # 2. Check udev rules (Linux specific)
        if get_os() == "linux":
            udev_ok, udev_msg = check_udev_rules()
            if udev_ok:
                self.udev_status_label.setText(f"Udev Rules (Linux): <font color='green'>{udev_msg}</font>")
                self.install_udev_button.setEnabled(False)
                self.add_adbusers_button.setEnabled(False)
            else:
                self.udev_status_label.setText(f"Udev Rules (Linux): <font color='red'>{udev_msg}</font>")
                self.install_udev_button.setEnabled(True)
                self.add_adbusers_button.setEnabled(True)
                self.append_log('[WARNING]', "Udev rules might be missing or incorrect for Xiaomi/Android devices. This can cause 'no permissions' errors with Fastboot.")
                self.append_log('[INFO]', "Click 'Fix Udev Rules' and 'Add User to adbusers group' if you encounter device detection/permission issues on Linux. Remember to log out and back in after adding user to group.")
        else: # Hide Linux-specific buttons on non-Linux OS
            self.udev_status_label.setText("Udev Rules (Linux): N/A (Not Linux)")
            self.linux_buttons_widget.setVisible(False)
        
        # Initial device detection will be handled by the QTimer
        self.append_log('[INFO]', "Initial setup checks complete. Waiting for device connection...")

    def detect_device_periodic(self):
        """
        Slot connected to QTimer timeout. Periodically checks for device connection
        and updates GUI status.
        """
        if not self.flashing_core: # Don't attempt if ADB/Fastboot are not found
            return

        # self.append_log('[DEBUG]', "Checking for device...")
        serial = self.flashing_core.detect_device() # This calls core.py's method

        if serial and serial != self.current_serial: # New device connected
            self.current_serial = serial
            device_info = self.flashing_core.get_device_info(serial)
            self.current_device_codename = device_info.get('codename', 'Unknown')
            self.current_bootloader_status = device_info.get('bootloader_locked', 'Unknown')

            self.device_status_label.setText(f"Device: <font color='green'>Connected ({serial})</font>")
            self.device_info_label.setText(f"Info: Codename: {self.current_device_codename}, Bootloader: {self.current_bootloader_status}")
            self.append_log('[INFO]', f"Device connected: {serial} (Codename: {self.current_device_codename}, Bootloader: {self.current_bootloader_status})")
            
            # Enable flash button if ROM is extracted AND bootloader is unlocked
            self.flash_button.setEnabled(self.extracted_rom_path is not None and self.current_bootloader_status == "Unlocked")
            if self.extracted_rom_path and self.current_bootloader_status != "Unlocked":
                 self.append_log('[WARNING]', "Bootloader is locked. Please unlock it officially before flashing a Fastboot ROM. Flashing button remains disabled.")

        elif not serial and self.current_serial: # Device disconnected
            self.current_serial = None
            self.current_device_codename = "Unknown"
            self.current_bootloader_status = "Unknown"
            self.device_status_label.setText("Device: <font color='red'>Disconnected</font>")
            self.device_info_label.setText("Info: N/A")
            self.flash_button.setEnabled(False) # Disable flash button
            self.append_log('[INFO]', "Device disconnected.")
        # else: No change in connection status, avoid logging noise

    def browse_rom(self):
        """Opens a file dialog for the user to select a Fastboot ROM (.tgz)."""
        file_dialog = QFileDialog(self)
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile) # Only allow existing files
        file_dialog.setNameFilter("Fastboot ROMs (*.tgz *.tar.gz)") # Filter for common ROM extensions
        
        if file_dialog.exec(): # If user selects a file and clicks OK
            selected_file = file_dialog.selectedFiles()[0]
            self.rom_path_input.setText(selected_file)
            self.extracted_rom_path = None # Reset extracted path on new ROM selection
            self.extracted_path_label.setText("Extracted ROM: None (Click 'Extract ROM')")
            self.extract_rom_button.setEnabled(True) # Enable extract button
            self.flash_button.setEnabled(False) # Disable flash until re-extracted and device re-checked

    def extract_rom(self):
        """Starts the ROM extraction process in a worker thread."""
        rom_file = self.rom_path_input.text()
        if not rom_file:
            QMessageBox.warning(self, "No ROM Selected", "Please select a Fastboot ROM (.tgz) file first.")
            return
        if not os.path.exists(rom_file):
            QMessageBox.critical(self, "File Not Found", f"The selected ROM file does not exist: {rom_file}")
            return

        # Determine the directory where ROMs will be extracted
        extract_base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roms")
        os.makedirs(extract_base_dir, exist_ok=True) # Ensure this directory exists
        
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Extracting: %p%")
        self.append_log('[INFO]', f"Starting ROM extraction from '{os.path.basename(rom_file)}'...")
        self.set_ui_enabled(False) # Disable main UI elements during extraction

        def on_extract_finished(success, message):
            """Callback for when the ROM extraction worker thread finishes."""
            self.set_ui_enabled(True) # Re-enable UI elements
            self.progress_bar.setValue(100 if success else 0)
            self.progress_bar.setFormat("Extraction: %p%") # Reset format
            if success:
                self.extracted_rom_path = message # Message from worker is the actual extracted path
                self.extracted_path_label.setText(f"Extracted ROM: <font color='green'>{os.path.basename(self.extracted_rom_path)}</font>")
                QMessageBox.information(self, "Extraction Complete", "ROM extracted successfully!")
                # Re-check flash button status based on device connection and bootloader
                self.flash_button.setEnabled(self.current_serial is not None and self.current_bootloader_status == "Unlocked")
                if not self.flash_button.isEnabled():
                    self.append_log('[WARNING]', "Flashing button remains disabled. Ensure a device is connected in Fastboot mode and its bootloader is unlocked.")
            else:
                self.extracted_rom_path = None
                self.extracted_path_label.setText("Extracted ROM: <font color='red'>Failed</font>")
                QMessageBox.critical(self, "Extraction Failed", message)
            
        # Create and start the worker thread for extraction
        self.worker = Worker(self.flashing_core.extract_rom, rom_file, extract_base_dir)
        self.worker.finished.connect(on_extract_finished)
        self.worker.progress.connect(self.append_log) # Connect for real-time log updates from worker
        self.worker.start()

    def start_flashing_confirmation(self):
        """Displays a confirmation dialog before starting the flashing process."""
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
        if flash_mode_data == "clean_all":
            confirmation_msg += "<b style='color: red;'>WARNING: This mode will wipe ALL data on your device! Ensure you have a backup.</b>\n"
        elif flash_mode_data == "except_storage":
             confirmation_msg += "<b style='color: orange;'>WARNING: This mode keeps user data but wipes the system partition. Proceed with caution.</b>\n"
        elif flash_mode_data == "except_data_storage":
             confirmation_msg += "<b style='color: green;'>This mode aims to keep your user data and apps. It's generally safer for updates.</b>\n"

        confirmation_msg += "\nEnsure your device battery is at least 50% charged and do NOT disconnect the device during flashing.\n"
        confirmation_msg += "\nAre you absolutely sure you want to proceed?"

        reply = QMessageBox.question(self, "Confirm Flashing Operation", confirmation_msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.start_flashing()
        else:
            self.append_log('[INFO]', "Flashing cancelled by user.")


    def start_flashing(self):
        """Starts the flashing process in a worker thread."""
        flash_mode = self.flash_mode_combo.currentData() # Get the data value (e.g., "clean_all")
        
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Flashing: %p%")
        self.append_log('[INFO]', f"Initiating flashing process for '{self.current_device_codename}' with mode: {flash_mode}...")
        self.set_ui_enabled(False) # Disable UI during flashing

        def on_flash_finished(success, message):
            """Callback for when the flashing worker thread finishes."""
            self.set_ui_enabled(True) # Re-enable UI elements
            self.progress_bar.setValue(100 if success else 0)
            self.progress_bar.setFormat("Flashing: %p%") # Reset format
            if success:
                QMessageBox.information(self, "Flashing Complete", "ROM flashed successfully! Your device should now reboot. First boot may take a while.")
                self.append_log('[INFO]', "Flashing process finished successfully.")
            else:
                QMessageBox.critical(self, "Flashing Failed", f"Flashing failed: {message}. Check logs for detailed error output.")
                self.append_log('[ERROR]', "Flashing process failed.")

        # Create and start the worker thread for flashing
        self.worker = Worker(self.flashing_core.flash_rom, self.extracted_rom_path, flash_mode)
        self.worker.finished.connect(on_flash_finished)
        self.worker.progress.connect(self.append_log) # Connect for real-time log updates from worker
        self.worker.start()

    def set_ui_enabled(self, enabled):
        """
        Helper function to enable/disable relevant UI elements during long operations.
        :param enabled: Boolean, True to enable, False to disable.
        """
        self.browse_rom_button.setEnabled(enabled)
        # Enable extract button only if enabled and a ROM path is selected
        self.extract_rom_button.setEnabled(enabled and self.rom_path_input.text() != "")
        self.flash_mode_combo.setEnabled(enabled)
        
        # Flash button enabled based on specific conditions (ROM, device, unlocked) AND global enabled state
        self.flash_button.setEnabled(enabled and self.extracted_rom_path is not None and 
                                     self.current_serial is not None and self.current_bootloader_status == "Unlocked")

        # Udev buttons are only enabled if they were needed (based on initial check) and UI is globally enabled
        if get_os() == "linux":
            udev_ok, _ = check_udev_rules()
            if not udev_ok: # Only enable if rules were initially found to be missing/incorrect
                self.install_udev_button.setEnabled(enabled)
                self.add_adbusers_button.setEnabled(enabled)
            else: # If rules are already good, keep them disabled
                 self.install_udev_button.setEnabled(False)
                 self.add_adbusers_button.setEnabled(False)


    def install_udev_rules_action(self):
        """Action to start the udev rules installation process."""
        reply = QMessageBox.question(self, "Install Udev Rules", 
                                     "This action requires administrator (sudo) privileges on Linux. "
                                     "It will write/update udev rules for Android devices and reload udev configuration. "
                                     "You may be prompted for your password in the terminal where MiFlashX was launched.\n\n"
                                     "Do you want to proceed?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.append_log('[INFO]', "Attempting to install udev rules...")
            self.set_ui_enabled(False) # Disable UI during this operation

            def on_install_finished(success, message):
                """Callback for udev rules installation worker."""
                self.set_ui_enabled(True) # Re-enable UI
                if success:
                    QMessageBox.information(self, "Udev Rules Installed", message)
                    self.append_log('[INFO]', message)
                    # Re-check initial setup to update labels and button states based on new udev status
                    self.check_initial_setup()
                else:
                    QMessageBox.critical(self, "Udev Rules Installation Failed", message)
                    self.append_log('[ERROR]', message)

            self.worker = Worker(install_udev_rules) # No args needed for install_udev_rules
            self.worker.finished.connect(on_install_finished)
            self.worker.progress.connect(self.append_log)
            self.worker.start()

    def add_to_adbusers_group_action(self):
        """Action to add the current user to the 'adbusers' group."""
        reply = QMessageBox.question(self, "Add User to 'adbusers' Group", 
                                     "This action requires administrator (sudo) privileges on Linux. "
                                     "It will add your current user to the 'adbusers' group, which can help with device permissions. "
                                     "You may be prompted for your password in the terminal where MiFlashX was launched.\n\n"
                                     "<b style='color: red;'>Important: You will need to log out and log back in for this change to take effect!</b>\n\n"
                                     "Do you want to proceed?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.append_log('[INFO]', "Attempting to add user to 'adbusers' group...")
            self.set_ui_enabled(False) # Disable UI during this operation

            def on_add_finished(success, message):
                """Callback for adding user to group worker."""
                self.set_ui_enabled(True) # Re-enable UI
                if success:
                    QMessageBox.information(self, "User Added to Group", message)
                    self.append_log('[INFO]', message)
                    # No need to re-check udev rules, but log the message.
                    # The effect only happens after re-login.
                    # self.check_initial_setup() # Could re-run to update labels, but message is clear.
                else:
                    QMessageBox.critical(self, "Add User to Group Failed", message)
                    self.append_log('[ERROR]', message)

            self.worker = Worker(add_to_adbusers_group) # No args needed
            self.worker.finished.connect(on_add_finished)
            self.worker.progress.connect(self.append_log)
            self.worker.start()

# --- Main Application Entry ---
if __name__ == "__main__":
    # This block runs when gui.py is executed directly.
    # For packaged app, main.py will be the entry.
    # This is useful for testing the GUI part in isolation during development.
    if get_os() != "linux":
        QMessageBox.critical(None, "OS Not Supported", "This GUI is designed for Linux only. Exiting.")
        sys.exit(1)

    app = QApplication(sys.argv)
    window = MiFlashX()
    window.show()
    sys.exit(app.exec())
