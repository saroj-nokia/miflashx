import sys
import os
from PyQt6.QtWidgets import QApplication, QMessageBox
from gui import MiFlashX
from utils import log_message, get_os, find_adb_fastboot # Import utility functions

def main():
    # Only run on Linux as this version is Linux-specific
    if get_os() != "linux":
        # Using QMessageBox here as it's the very first entry point
        # before the full GUI is initialized in MiFlashX constructor.
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "OS Not Supported", "MiFlashX (Linux Version) can only run on Linux. Please use the appropriate version for your operating system or build for your OS.")
        sys.exit(1)

    # --- Crucial for PyInstaller Bundles ---
    # When PyInstaller creates a --onefile executable, it extracts all bundled
    # files (including 'platform-tools') into a temporary directory accessible via sys._MEIPASS.
    # We need to ensure these bundled 'adb' and 'fastboot' executables are in the PATH
    # of the current process so `subprocess.run` calls can find them without absolute paths.

    # Determine the base path for bundled tools
    # If frozen (PyInstaller bundle), use _MEIPASS. Otherwise, use current script's directory.
    base_path = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS') and sys._MEIPASS or \
                os.path.dirname(os.path.abspath(__file__))
    
    bundled_platform_tools_dir = os.path.join(base_path, "platform-tools")

    if os.path.isdir(bundled_platform_tools_dir):
        # Add the bundled platform-tools directory to the current process's PATH
        # os.pathsep is ';' on Windows, ':' on Linux/macOS
        os.environ["PATH"] = bundled_platform_tools_dir + os.pathsep + os.environ.get("PATH", "")
        log_message('info', f"Added bundled platform-tools to PATH for this session: {bundled_platform_tools_dir}")
    else:
        log_message('warning', "Bundled 'platform-tools' directory not found. Relying on system PATH for ADB/Fastboot.")
    
    # Check if adb and fastboot are now discoverable (either bundled or from system PATH)
    adb_found, fastboot_found = find_adb_fastboot()
    if not adb_found or not fastboot_found:
        log_message('error', "ADB and/or Fastboot executables are not found. Please ensure they are installed and in your system PATH, or that the bundled 'platform-tools' are present and correctly configured.")
        # We don't exit here immediately; the GUI will display the error status.
        # This allows the app to launch and tell the user what's wrong.

    # Start the PyQt application
    app = QApplication(sys.argv)
    window = MiFlashX() # Instantiate the main window
    window.show() # Display the window
    sys.exit(app.exec()) # Start the Qt event loop

if __name__ == "__main__":
    main()
