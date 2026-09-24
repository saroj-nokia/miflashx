"""
main.py — the application entry point.

Previously this file contained a second, complete, independent copy of the
whole app (its own MiFlashX(QMainWindow), its own DeviceDetectionThread),
separate from the one in gui.py. PyInstaller was pointed at THIS file, so
that duplicate — the older, less-styled one — was what actually shipped;
gui.py's more complete version was only ever imported as a PyInstaller
hidden-import and never actually run. See build_scripts/build_linux.sh.

Now there's exactly one implementation, in gui.py. This file is just the
launcher: it owns QApplication setup (org/app name for QSettings, native
styling) and nothing else.
"""

import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from utils import get_os, log_message
from gui import MiFlashX


def main():
    if get_os() != "linux":
        # Only shown if PyQt6 itself is available on a non-Linux OS; the app
        # is Linux-only by design (udev rules, adbusers group, fastboot paths).
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "OS Not Supported", "MiFlashX is designed for Linux only. Exiting.")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("MiFlashX")
    app.setOrganizationName("miflashx")
    # No app.setStyle(...) call: leaving this unset lets Qt6's platform theme
    # plugin (qt6ct, QT_QPA_PLATFORMTHEME=gtk3, native Breeze on KDE, etc.)
    # pick the style that matches the user's actual desktop environment.

    log_message('info', 'MiFlashX application starting.')

    window = MiFlashX()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
