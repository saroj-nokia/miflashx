import sys
import os
import logging
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel, QFileDialog, QTextEdit, QProgressBar, QMessageBox, QComboBox
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# Import the log_message function from utils.py
# The logging.basicConfig setup will run automatically when utils is imported.
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
        self.rom_path = None
        self.flash_mode_selected = "flash_all" # Default flash mode

        self.init_ui()
        self.check_dependencies()

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

        # --- Flash Mode Selection ---
        self.flash_mode_label = QLabel("Select Flash Mode:")
        self.layout.addWidget(self.flash_mode_label)

        self.flash_mode_combo = QComboBox()
        self.flash_mode_combo.addItem("Flash All (Clean Install)", "flash_all")
        self.flash_mode_combo.addItem("Flash All Except Data Storage", "flash_all_except_data_storage")
        self.flash_mode_combo.addItem("Flash All and Lock Bootloader", "flash_all_lock")
        self.flash_mode_combo.currentIndexChanged.connect(self.update_flash_mode)
        self.layout.addWidget(self.flash_mode_combo)

        # --- Browse Button for ROM ---
        self.browse_button = QPushButton("Browse ROM Folder")
        self.browse_button.clicked.connect(self.browse_rom_folder)
        self.layout.addWidget(self.browse_button)

        self.rom_path_label = QLabel("ROM Path: None Selected")
        self.layout.addWidget(self.rom_path_label)
        

        # --- Flash Button ---
        self.flash_button = QPushButton("Flash Device")
        self.flash_button.clicked.connect(self.start_flashing)
        self.flash_button.setEnabled(False) # Disable until ROM path is set and dependencies met
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

    def check_dependencies(self):
        """Checks for ADB/Fastboot tools and Linux-specific dependencies."""
        log_message('info', "Checking ADB and Fastboot dependencies...")
        self.adb_path, self.fastboot_path = find_adb_fastboot()

        if self.adb_path and self.fastboot_path:
            self.adb_fastboot_path_label.setText(f"ADB: {self.adb_path}\nFastboot: {self.fastboot_path}")
            log_message('info', "ADB and Fastboot tools found.")
            # Only enable flash button if ROM path is also set
            if self.rom_path:
                self.flash_button.setEnabled(True)
        else:
            self.adb_fastboot_path_label.setText("ADB/Fastboot: NOT FOUND. Please ensure platform-tools are correctly bundled or in your system PATH.")
            log_message('error', "ADB or Fastboot tools not found. Flashing will not be possible.")
            self.flash_button.setEnabled(False)

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

    def browse_rom_folder(self):
        """Opens a file dialog to select the ROM folder."""
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)

        if dialog.exec():
            selected_directory = dialog.selectedFiles()[0]
            self.rom_path = selected_directory
            self.rom_path_label.setText(f"ROM Path: {self.rom_path}")
            log_message('info', f"ROM folder selected: {self.rom_path}")
            # Enable flash button only if ADB/Fastboot are also found
            if self.adb_path and self.fastboot_path:
                self.flash_button.setEnabled(True) 
        else:
            log_message('info', "ROM folder selection cancelled.")

    def start_flashing(self):
        """Initiates the flashing process in a separate thread."""
        if not self.rom_path:
            QMessageBox.warning(self, "No ROM Selected", "Please select a ROM folder first.")
            log_message('warning', "Flash attempt failed: No ROM folder selected.")
            return
        
        if not self.adb_path or not self.fastboot_path:
            QMessageBox.warning(self, "Tools Missing", "ADB or Fastboot tools not found. Cannot proceed with flashing.")
            log_message('warning', "Flash attempt failed: ADB/Fastboot tools missing.")
            return

        log_message('info', f"Starting flashing process for ROM: {self.rom_path} with mode: {self.flash_mode_selected}")
        self.flash_button.setEnabled(False) # Disable button during flashing
        self.progress_bar.setValue(0)

        # Pass the selected flash mode to the FlashingThread
        self.flashing_thread = FlashingThread(self.rom_path, self.adb_path, self.fastboot_path, self.flash_mode_selected)
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

class FlashingThread(QThread):
    update_progress = pyqtSignal(int)
    update_status = pyqtSignal(str)

    def __init__(self, rom_path, adb_path, fastboot_path, flash_mode):
        super().__init__()
        self.rom_path = rom_path
        self.adb_path = adb_path
        self.fastboot_path = fastboot_path
        self.flash_mode = flash_mode # Store the flash mode
        self.flashing_core = FlashingCore(
            rom_path=self.rom_path,
            adb_path=self.adb_path,
            fastboot_path=self.fastboot_path,
            flash_mode=self.flash_mode, # Pass flash mode to FlashingCore
            log_callback=self.update_status # Pass the signal as a callback
        )

    def run(self):
        """Executes the flashing logic."""
        self.update_status.emit("Flashing process started...")
        success = self.flashing_core.flash_device()
        
        if success:
            self.update_status.emit("Flashing completed successfully!")
            self.update_progress.emit(100)
        else:
            self.update_status.emit("Flashing failed. Check logs for details.")
            self.update_progress.emit(0) # Reset or indicate failure

class QTextEditLogHandler(logging.Handler):
    """Custom logging handler to redirect logs to a QTextEdit widget."""
    def __init__(self, text_edit_widget):
        super().__init__()
        self.text_edit = text_edit_widget
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    def emit(self, record):
        msg = self.format(record)
        self.text_edit.append(msg) # Append message to QTextEdit

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MiFlashX()
    window.show()
    sys.exit(app.exec())
