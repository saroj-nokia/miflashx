import sys
import os
import logging
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel, QFileDialog, QTextEdit, QProgressBar, QMessageBox, QComboBox, QHBoxLayout
from PyQt6.QtCore import Qt, QThread, pyqtSignal
import time # Import time for sleep in DeviceDetectionThread

# Import the log_message function from utils.py
from utils import log_message, get_os, find_adb_fastboot, check_udev_rules, install_udev_rules, add_to_adbusers_group

# Import FlashingCore from core.py
from core import FlashingCore

class MiFlashX(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiFlashX - Xiaomi Device Flasher")
        self.setGeometry(100, 100, 800, 600) # Initial window size

        self.adb_path = None
        self.fastboot_path = None
        self.current_os = get_os()
        self.rom_tgz_path = None # Stores path to the .tgz file
        self.extracted_rom_path = None # Stores path to the extracted ROM directory
        self.flash_mode_selected = "flash_all" # Default flash mode (Clean All)

        self.init_ui()
        self.check_dependencies() # This will populate self.adb_path and self.fastboot_path

        # Start device detection in a separate thread AFTER dependencies are checked
        self.device_detection_thread = DeviceDetectionThread(self.adb_path, self.fastboot_path)
        self.device_detection_thread.device_detected.connect(self.update_device_status)
        # Re-connected the log_signal from DeviceDetectionThread to update_status for thread-safe logging
        self.device_detection_thread.log_signal.connect(self.update_status)
        self.device_detection_thread.start()

        # Log that the application has started
        log_message('info', 'MiFlashX Application Initialized.')

    def init_ui(self):
        """Initializes the user interface components."""
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout()
        self.central_widget.setLayout(self.layout)

        # --- Device Status ---
        self.device_status_label = QLabel("Device Status: Not Connected")
        self.layout.addWidget(self.device_status_label)

        # --- ADB/Fastboot Path Display (for debugging/info) ---
        self.adb_fastboot_path_label = QLabel("ADB/Fastboot: Checking...")
        self.layout.addWidget(self.adb_fastboot_path_label)

        # --- ROM Selection & Extraction Section ---
        rom_selection_layout = QHBoxLayout()
        self.browse_rom_button = QPushButton("Browse ROM File")
        self.browse_rom_button.clicked.connect(self.browse_rom_file)
        rom_selection_layout.addWidget(self.browse_rom_button)

        self.rom_path_label = QLabel("ROM File: None Selected")
        rom_selection_layout.addWidget(self.rom_path_label)
        self.layout.addLayout(rom_selection_layout)

        self.extract_rom_button = QPushButton("Extract ROM")
        self.extract_rom_button.clicked.connect(self.start_rom_extraction)
        self.extract_rom_button.setEnabled(False) # Disable until ROM file is selected
        self.layout.addWidget(self.extract_rom_button)

        self.extracted_rom_label = QLabel("Extracted To: N/A")
        self.layout.addWidget(self.extracted_rom_label)

        # --- Flash Mode Selection ---
        self.flash_mode_label = QLabel("Select Flash Mode:")
        self.layout.addWidget(self.flash_mode_label)

        self.flash_mode_combo = QComboBox()
        self.flash_mode_combo.addItem("Flash All (Clean Install)", "flash_all")
        self.flash_mode_combo.addItem("Flash All Except Data Storage", "flash_all_except_data_storage")
        self.flash_mode_combo.addItem("Flash All and Lock Bootloader", "flash_all_lock")
        self.flash_mode_combo.currentIndexChanged.connect(self.update_flash_mode)
        self.layout.addWidget(self.flash_mode_combo)

        # --- Flash Button ---
        self.flash_button = QPushButton("Flash Device")
        self.flash_button.clicked.connect(self.start_flashing)
        self.flash_button.setEnabled(False) # Disable until ROM is extracted and dependencies met
        self.layout.addWidget(self.flash_button)

        # --- Progress Bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.layout.addWidget(self.progress_bar)

        # --- Console Output ---
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.layout.addWidget(self.console_output)

        # Redirect logging output to the console_output QTextEdit
        self.log_handler = QTextEditLogHandler(self.console_output)
        logging.getLogger().addHandler(self.log_handler)


    def update_flash_mode(self, index):
        """Updates the selected flash mode based on QComboBox selection."""
        self.flash_mode_selected = self.flash_mode_combo.itemData(index)
        log_message('info', f"Flash mode selected: {self.flash_mode_selected}")
        # Re-evaluate flash button state
        self._update_flash_button_state()

    def _update_flash_button_state(self):
        """Helper to enable/disable flash button based on all prerequisites."""
        # Flash button enabled if ADB/Fastboot found, ROM extracted, and device connected
        # Also check if device is unlocked if a specific mode requires it (e.g., flash_all_lock)
        device_status_text = self.device_status_label.text()
        is_device_connected = "Connected" in device_status_text
        is_bootloader_unlocked = "Unlocked" in device_status_text # Assumes 'Unlocked' is in the status string

        if self.adb_path and self.fastboot_path and self.extracted_rom_path and is_device_connected:
            # If "Flash All and Lock Bootloader" is selected, require unlocked bootloader for safety
            if self.flash_mode_selected == "flash_all_lock" and not is_bootloader_unlocked:
                self.flash_button.setEnabled(False)
            else:
                self.flash_button.setEnabled(True)
        else:
            self.flash_button.setEnabled(False)

    def check_dependencies(self):
        """Checks for ADB/Fastboot tools and Linux-specific dependencies."""
        log_message('info', "Checking ADB and Fastboot dependencies...")
        self.adb_path, self.fastboot_path = find_adb_fastboot()

        if self.adb_path and self.fastboot_path:
            self.adb_fastboot_path_label.setText(f"ADB: {self.adb_path}\nFastboot: {self.fastboot_path}")
            log_message('info', "ADB and Fastboot tools found.")
        else:
            self.adb_fastboot_path_label.setText("ADB/Fastboot: NOT FOUND. Please ensure platform-tools are correctly bundled or in your system PATH.")
            log_message('error', "ADB or Fastboot tools not found. Flashing will not be possible.")

        # Always update button state after dependency check
        self._update_flash_button_state()


        if self.current_os == "linux":
            log_message('info', "Checking Linux-specific udev rules and adbusers group...")
            rules_ok, rules_msg = check_udev_rules()
            if rules_ok:
                log_message('info', f"Udev rules: {rules_msg}")
            else:
                log_message('warning', f"Udev rules: {rules_msg}. You may need to install/update them.")
                # Offer to install rules
                reply = QMessageBox.question(self, 'Udev Rules Missing/Incomplete',
                                             f"{rules_msg}\nDo you want to attempt to install/update udev rules now? This requires sudo password.",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    install_success, install_msg = install_udev_rules()
                    log_message('info', f"Udev rules installation attempt: {install_msg}")
                    QMessageBox.information(self, "Udev Rules Installation", install_msg)
                    if install_success:
                        # After rules, check adbusers group
                        group_success, group_msg = add_to_adbusers_group()
                        log_message('info', f"Adbusers group addition attempt: {group_msg}")
                        QMessageBox.information(self, "Adbusers Group", group_msg)
                        if not group_success:
                            log_message('error', "Failed to add user to adbusers group. Please add manually.")
                    else:
                        log_message('error', "Udev rules installation failed. Please install manually.")

    def browse_rom_file(self):
        """Opens a file dialog to select the ROM .tgz file."""
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter("Fastboot ROMs (*.tgz *.tar.gz)")

        if dialog.exec():
            selected_file = dialog.selectedFiles()[0]
            self.rom_tgz_path = selected_file
            self.rom_path_label.setText(f"ROM File: {os.path.basename(self.rom_tgz_path)}")
            self.extracted_rom_path = None # Reset extracted path
            self.extracted_rom_label.setText("Extracted To: N/A")
            self.extract_rom_button.setEnabled(True) # Enable extract button
            self._update_flash_button_state() # Update flash button state
            log_message('info', f"ROM file selected: {self.rom_tgz_path}")
        else:
            log_message('info', "ROM file selection cancelled.")

    def start_rom_extraction(self):
        """Initiates the ROM extraction process in a separate thread."""
        if not self.rom_tgz_path:
            QMessageBox.warning(self, "No ROM File Selected", "Please select a Fastboot ROM (.tgz) file first.")
            log_message('warning', "Extraction attempt failed: No ROM file selected.")
            return

        self.extract_rom_button.setEnabled(False) # Disable extract button during extraction
        self.flash_button.setEnabled(False) # Disable flash button
        self.progress_bar.setValue(0)
        log_message('info', f"Starting ROM extraction for: {self.rom_tgz_path}")

        # Pass adb_path and fastboot_path to ExtractionThread for FlashingCore initialization
        self.extraction_thread = ExtractionThread(self.rom_tgz_path, self.adb_path, self.fastboot_path)
        self.extraction_thread.update_progress.connect(self.update_progress)
        self.extraction_thread.update_status.connect(self.update_status)
        self.extraction_thread.extraction_finished.connect(self.rom_extraction_finished)
        self.extraction_thread.start()

    def rom_extraction_finished(self, extracted_path, success):
        """Handles the completion of the ROM extraction process."""
        self.extract_rom_button.setEnabled(True) # Re-enable extract button
        if success:
            self.extracted_rom_path = extracted_path
            self.extracted_rom_label.setText(f"Extracted To: {os.path.basename(extracted_path)}")
            log_message('info', f"ROM extraction completed to: {extracted_path}")
            QMessageBox.information(self, "Extraction Complete", f"ROM extracted successfully to:\n{extracted_path}")
        else:
            self.extracted_rom_path = None
            self.extracted_rom_label.setText("Extracted To: FAILED")
            log_message('error', "ROM extraction failed.")
            QMessageBox.critical(self, "Extraction Failed", "Failed to extract ROM. Check logs for details.")
        self._update_flash_button_state() # Update flash button state

    def start_flashing(self):
        """Initiates the flashing process in a separate thread."""
        if not self.extracted_rom_path:
            QMessageBox.warning(self, "No ROM Extracted", "Please extract a ROM first before flashing.")
            log_message('warning', "Flash attempt failed: No ROM extracted.")
            return

        if not self.adb_path or not self.fastboot_path:
            QMessageBox.warning(self, "Tools Missing", "ADB or Fastboot tools not found. Cannot proceed with flashing.")
            log_message('warning', "Flash attempt failed: ADB/Fastboot tools missing.")
            return

        # Confirmation dialog before flashing
        reply = QMessageBox.question(self, 'Confirm Flashing',
                                     f"You are about to flash the ROM from:\n{self.extracted_rom_path}\n\nUsing mode: {self.flash_mode_combo.currentText()}\n\n"
                                     "**WARNINGS:**\n"
                                     "- Ensure your device's bootloader is UNLOCKED.\n"
                                     "- Device MUST be in Fastboot mode.\n"
                                     "- Flashing can lead to data loss or device damage if done incorrectly.\n"
                                     "- DO NOT DISCONNECT THE DEVICE during flashing!\n\n"
                                     "Are you sure you want to proceed?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.No:
            log_message('info', "Flashing cancelled by user.")
            return

        log_message('info', f"Starting flashing process for ROM: {self.extracted_rom_path} with mode: {self.flash_mode_selected}")
        self.flash_button.setEnabled(False) # Disable button during flashing
        self.progress_bar.setValue(0)

        # Pass the extracted ROM path and selected flash mode to the FlashingThread
        self.flashing_thread = FlashingThread(self.extracted_rom_path, self.adb_path, self.fastboot_path, self.flash_mode_selected)
        self.flashing_thread.update_progress.connect(self.update_progress)
        self.flashing_thread.update_status.connect(self.update_status)
        self.flashing_thread.finished.connect(self.flashing_finished)
        self.flashing_thread.start()

    def update_progress(self, value):
        """Updates the progress bar."""
        self.progress_bar.setValue(value)

    def update_status(self, message):
        """Updates the console output with status messages."""
        log_message('info', message) # Log status messages to file and console

    def flashing_finished(self):
        """Handles the completion of the flashing process."""
        self.flash_button.setEnabled(True) # Re-enable button
        log_message('info', "Flashing process completed.")
        QMessageBox.information(self, "Flashing Complete", "The flashing process has finished.")

    def update_device_status(self, serial, codename, bootloader_locked):
        """Updates the device status label in the GUI."""
        try: # Added try-except for defensive programming
            if serial:
                self.device_status_label.setText(f"Device Status: Connected ({serial})\nCodename: {codename}, Bootloader: {bootloader_locked}")
            else:
                self.device_status_label.setText("Device Status: Not Connected")
            self._update_flash_button_state() # Update flash button state based on new device status
        except Exception as e:
            log_message('error', f"Error updating device status label: {e}")


    def closeEvent(self, event):
        """Handles application close event to gracefully stop threads."""
        log_message('info', "MiFlashX application closing. Stopping device detection thread...")
        if self.device_detection_thread.isRunning():
            self.device_detection_thread.stop()
            self.device_detection_thread.wait() # Wait for the thread to finish
            log_message('info', "Device detection thread stopped.")
        event.accept() # Accept the close event


class DeviceDetectionThread(QThread):
    device_detected = pyqtSignal(str, str, str) # serial, codename, bootloader_locked
    log_signal = pyqtSignal(str) # Re-introduced signal for logging messages

    def __init__(self, adb_path, fastboot_path):
        super().__init__()
        self.adb_path = adb_path
        self.fastboot_path = fastboot_path
        # Initialize FlashingCore for device detection, passing the new log_signal's emit method as callback
        self.flashing_core = FlashingCore(
            adb_path=self.adb_path,
            fastboot_path=self.fastboot_path,
            output_callback=self.log_signal.emit # Directly emit the signal
        )
        self.last_detected_serial = None # Track the last detected serial
        self.last_device_info = {"codename": "Unknown", "bootloader_locked": "Unknown"} # Store last known info
        self._running = True # Control flag for the thread's run loop

    def stop(self):
        """Sets the internal flag to stop the thread's execution."""
        self._running = False

    def run(self):
        """Continuously attempts to detect a Fastboot device, optimizing info retrieval."""
        while self._running: # Use the _running flag to control the loop
            if self.fastboot_path: # Only attempt if fastboot path is known
                current_serial = self.flashing_core.detect_device()

                if current_serial:
                    if current_serial != self.last_detected_serial:
                        # Device newly connected or a different device connected
                        # Use self.log_signal.emit instead of direct log_message
                        self.log_signal.emit(f"New device detected: {current_serial}. Fetching detailed info...")
                        device_info = self.flashing_core.get_device_info(current_serial)
                        self.device_detected.emit(current_serial, device_info["codename"], device_info["bootloader_locked"])
                        self.last_detected_serial = current_serial
                        self.last_device_info = device_info
                    else:
                        # Same device still connected, emit last known info
                        self.device_detected.emit(current_serial, self.last_device_info["codename"], self.last_device_info["bootloader_locked"])
                else:
                    # No device detected
                    if self.last_detected_serial:
                        # Device was previously connected, now disconnected
                        self.log_signal.emit("Device disconnected.") # Use self.log_signal.emit
                        self.device_detected.emit("", "Unknown", "Unknown") # Emit empty if no device
                        self.last_detected_serial = None
                        self.last_device_info = {"codename": "Unknown", "bootloader_locked": "Unknown"}
                    else:
                        # Still no device connected, keep emitting "Not Connected"
                        self.device_detected.emit("", "Unknown", "Unknown")
            else:
                # Fastboot path not set, emit "Not Connected"
                if self.last_detected_serial or self.last_device_info["codename"] != "Unknown": # Check if it was previously connected
                    self.log_signal.emit("Fastboot path lost or not found. Resetting device status.") # Use self.log_signal.emit
                self.device_detected.emit("", "Unknown", "Unknown")
                self.last_detected_serial = None # Ensure serial is reset if fastboot path is lost
                self.last_device_info = {"codename": "Unknown", "bootloader_locked": "Unknown"}
            time.sleep(2) # Check every 2 seconds
        self.log_signal.emit("DeviceDetectionThread exiting run loop.") # Log when the thread's loop actually exits

class ExtractionThread(QThread):
    update_progress = pyqtSignal(int)
    update_status = pyqtSignal(str)
    extraction_finished = pyqtSignal(str, bool) # Emits extracted_path and success status

    def __init__(self, rom_tgz_path, adb_path, fastboot_path): # Added adb_path, fastboot_path
        super().__init__()
        self.rom_tgz_path = rom_tgz_path
        # For extraction, we only need a core instance to use its extract_rom method
        self.core_instance = FlashingCore(adb_path=adb_path, fastboot_path=fastboot_path, output_callback=self.update_status)

    def run(self):
        self.update_status.emit(f"Extracting {os.path.basename(self.rom_tgz_path)}...")
        extract_dir_base = os.path.join(os.getcwd(), "roms") # roms/ directory
        os.makedirs(extract_dir_base, exist_ok=True)

        # Call the extract_rom method from FlashingCore
        success, message = self.core_instance.extract_rom(self.rom_tgz_path, extract_dir_base)

        if success:
            self.update_status.emit(f"ROM extraction successful! {message}")
            self.update_progress.emit(100)
            # The actual ROM root might be deeper, extract_rom returns the correct path
            self.extraction_finished.emit(message, True) # message contains the final extracted_path
        else:
            self.update_status.emit(f"ROM extraction failed: {message}")
            self.update_progress.emit(0)
            self.extraction_finished.emit("", False) # No valid path on failure

class FlashingThread(QThread):
    update_progress = pyqtSignal(int)
    update_status = pyqtSignal(str)

    def __init__(self, extracted_rom_path, adb_path, fastboot_path, flash_mode):
        super().__init__()
        self.extracted_rom_path = extracted_rom_path
        self.adb_path = adb_path
        self.fastboot_path = fastboot_path
        self.flash_mode = flash_mode # Store the flash mode
        # Initialize FlashingCore correctly
        self.flashing_core = FlashingCore(
            adb_path=self.adb_path,
            fastboot_path=self.fastboot_path,
            output_callback=self.update_status # Pass the signal as a callback
        )

    def run(self):
        """Executes the flashing logic."""
        self.update_status.emit("Flashing process started...")
        # Pass rom_path and flash_mode to flash_device method
        success, message = self.flashing_core.flash_rom(self.extracted_rom_path, self.flash_mode)

        if success:
            self.update_status.emit(f"Flashing completed successfully! {message}")
            self.update_progress.emit(100)
        else:
            self.update_status.emit(f"Flashing failed: {message}")
            self.progress_bar.setValue(0) # Reset or indicate failure

class QTextEditLogHandler(logging.Handler):
    """Custom logging handler to redirect logs to a QTextEdit widget."""
    def __init__(self, text_edit_widget):
        super().__init__()
        self.text_edit = text_edit_widget
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    def emit(self, record):
        msg = self.format(record)
        # Ensure QApplication exists and is not shutting down before appending
        app = QApplication.instance()
        if app and not app.closingDown():
            self.text_edit.append(msg)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MiFlashX()
    window.show()
    sys.exit(app.exec())
