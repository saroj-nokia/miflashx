import os
import re
import inspect
import subprocess

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QLabel,
                             QTextEdit, QFileDialog, QGroupBox, QFrame,
                             QRadioButton, QButtonGroup, QScrollArea,
                             QMessageBox, QProgressBar,
                             QStatusBar, QGraphicsDropShadowEffect, QGraphicsOpacityEffect)
from PyQt6.QtCore import QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QByteArray, QSettings, Qt
from PyQt6.QtGui import QIcon, QFont, QColor, QPalette, QAction, QActionGroup, QPixmap

from core import FlashingCore, FlashModes, validate_rom_directory
from utils import log_message, get_os, check_udev_rules, install_udev_rules, add_to_adbusers_group, find_adb_fastboot
from device_monitor import DeviceMonitor, DeviceState


# Softer, slightly desaturated status colors — easier on the eye than pure
# CSS "red"/"green"/"orange" in both light and dark mode.
_STATUS_COLORS = {"ok": "#43a047", "bad": "#e53935", "warn": "#fb8c00"}
_STATUS_ICONS = {"ok": "✅", "bad": "❌", "warn": "⚠️"}

# Flashing mode metadata shared between the mode-card builder and the
# lookup used when reading back the current selection — single source of
# truth so the display title used in the confirmation dialog can never
# drift out of sync with what the cards actually show.
_FLASH_MODE_INFO = [
    (FlashModes.SAVE_DATA_AND_STORAGE, "Safest — keep apps & data",
     "Recommended for routine updates. Keeps your data and storage intact.", "ok"),
    (FlashModes.SAVE_USER_DATA, "Keep user data",
     "Wipes the system partition but preserves your user data.", "warn"),
    (FlashModes.CLEAN_ALL, "Clean install",
     "Wipes ALL data on the device. Make sure you have a backup.", "bad"),
    (FlashModes.LOCK_BOOTLOADER, "Flash & lock bootloader",
     "Extreme caution: locks the bootloader after flashing. Only use if you're certain of the ROM's integrity.", "bad"),
]


def _status_html(label: str, value: str, kind: str) -> str:
    """Consistent 'Bold Label: [icon] value' formatting for status rows."""
    color = _STATUS_COLORS.get(kind, "palette(mid)")
    icon = _STATUS_ICONS.get(kind, "")
    return f"<b>{label}:</b> {icon} <span style='color:{color}'>{value}</span>"


def _build_palette(dark: bool) -> QPalette:
    """
    Explicit, self-contained Light/Dark palettes.

    Why not rely on the system palette for this: on Fedora Workstation (and
    other GNOME setups) without qt6ct or adwaita-qt installed, Qt has no
    bridge to GTK's theme/dark-mode/accent settings at all. QApplication
    silently falls back to the plain "Fusion" style with ITS default light
    palette, no matter what the desktop's dark mode or accent color is set
    to — there's nothing to "detect" because the OS never told Qt anything.
    So Light/Dark here are built by hand and paired with an explicit
    `app.setStyle("Fusion")`, which guarantees the switch actually takes
    effect on every distro, not just ones with the right Qt glue installed.
    """
    pal = QPalette()

    if dark:
        window = QColor(45, 45, 48)
        base = QColor(35, 35, 38)
        text = QColor(230, 230, 230)
        button = QColor(55, 55, 59)
        accent = QColor(66, 133, 244)
        disabled_text = QColor(120, 120, 120)
    else:
        window = QColor(240, 240, 242)
        base = QColor(255, 255, 255)
        text = QColor(20, 20, 20)
        button = QColor(240, 240, 242)
        accent = QColor(25, 100, 210)
        disabled_text = QColor(160, 160, 160)

    pal.setColor(QPalette.ColorRole.Window, window)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, window)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, button)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.Mid, window.darker(115) if dark else window.darker(108))
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text)

    return pal


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


def _rich_question(parent, title: str, html_lines: list, default_no: bool = True) -> bool:
    """
    Yes/No confirmation dialog that actually renders embedded HTML. Plain
    QMessageBox.question() only treats the text as rich text if it starts
    with a tag; our messages open with a plain sentence and put <b
    style='color:...'> further in, which rendered as literal text instead of
    styled warnings. Building the box directly and forcing
    Qt.TextFormat.RichText fixes that regardless of where the tags appear.
    `html_lines` is joined with <br><br> -- each item is one paragraph, so
    callers don't have to hand-manage line breaks in HTML mode (plain "\n"
    is collapsed like any other whitespace once textFormat is RichText).
    """
    box = QMessageBox(QMessageBox.Icon.Question, title, "<br><br>".join(html_lines), parent=parent)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes)
    return box.exec() == QMessageBox.StandardButton.Yes


class MiFlashX(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiFlashX (Xiaomi Fastboot Flashing Tool for Linux)")
        # Size to the screen rather than a fixed number: the content is tall
        # enough that a fixed size (or the layout's own minimum) can exceed a
        # laptop screen, pushing the bottom of the window off-screen.
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.resize(min(1100, int(avail.width() * 0.9)),
                        min(800, int(avail.height() * 0.9)))
        else:
            self.resize(1100, 800)

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
        self._archive_selected = False

        self.settings = QSettings("miflashx", "MiFlashX")
        saved_theme = self.settings.value("theme", "system")

        self.init_menu_bar(saved_theme)
        self.apply_theme(saved_theme, persist=False)  # already the saved value; no need to re-save
        self.init_ui()
        self.apply_card_theme()
        self.check_initial_setup()

        if self.flashing_core:
            self.device_monitor = DeviceMonitor(self.fastboot_path, parent=self)
            self.device_monitor.device_changed.connect(self.on_device_state_changed)

    def init_menu_bar(self, current_theme: str):
        """View > Theme menu with a checkable Follow System / Light / Dark group."""
        menu_bar = self.menuBar()
        view_menu = menu_bar.addMenu("&View")
        theme_menu = view_menu.addMenu("Theme")

        self._theme_actions = {}
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)

        for key, label in (("system", "Follow System"), ("light", "Light"), ("dark", "Dark")):
            action = QAction(label, self, checkable=True)
            action.setChecked(key == current_theme)
            action.triggered.connect(lambda checked, k=key: self.apply_theme(k))
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[key] = action

    def apply_theme(self, theme: str, persist: bool = True):
        """
        Switches the whole application's style/palette, then rebuilds this
        window's card stylesheet to match. 'light' and 'dark' use the
        explicit palettes in _build_palette() paired with the Fusion style,
        which works regardless of whether the desktop has Qt theme
        integration installed. 'system' restores whatever QApplication
        started with (see main.py, which stashes it before anything
        overrides it).
        """
        app = QApplication.instance()

        if theme == "dark":
            app.setStyle("Fusion")
            app.setPalette(_build_palette(dark=True))
        elif theme == "light":
            app.setStyle("Fusion")
            app.setPalette(_build_palette(dark=False))
        else:  # "system"
            theme = "system"
            original_style = app.property("_original_style_name")
            original_palette = app.property("_original_palette")
            if original_style:
                app.setStyle(original_style)
            if original_palette is not None:
                app.setPalette(original_palette)

        if persist:
            self.settings.setValue("theme", theme)

        if hasattr(self, '_theme_actions') and theme in self._theme_actions:
            self._theme_actions[theme].setChecked(True)

        # The card stylesheet was baked from a snapshot of palette colors
        # (see apply_card_theme's docstring), so it has to be rebuilt
        # explicitly whenever the underlying palette changes — it doesn't
        # update itself just because QApplication.setPalette() was called.
        if hasattr(self, '_cards'):
            self.apply_card_theme()

    def closeEvent(self, event):
        if self.device_monitor:
            self.device_monitor.stop()
        super().closeEvent(event)

    def init_ui(self):
        """Initializes the main graphical user interface elements."""
        # The page lives inside a scroll area: if the window is ever shorter
        # than the content (small screen, tiled window), it scrolls instead of
        # forcing the window taller than the display and cutting off the bottom.
        central_widget = QWidget()
        central_widget.setObjectName("centralArea")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(central_widget)
        self.setCentralWidget(scroll)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(18)
        main_layout.setContentsMargins(18, 18, 18, 18)

        # Secondary/muted-text labels (subtitle, "— or —" divider, flash-mode
        # descriptions) register themselves here as (label, extra_css) pairs.
        # apply_card_theme() computes a properly readable muted color from
        # the live palette and applies it to all of them — see that method's
        # docstring for why this can't just use "color: palette(mid)".
        self._muted_labels = []

        # --- Header banner: gives the app a visual anchor instead of
        # dropping straight into the first card ---
        header_layout = QHBoxLayout()
        header_layout.setSpacing(14)

        icon_label = QLabel()
        icon_pixmap = QPixmap()
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
        if os.path.exists(icon_path):
            icon_pixmap = QPixmap(icon_path)
        if not icon_pixmap.isNull():
            icon_label.setPixmap(icon_pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation))
        else:
            icon_label.setText("📱")
            icon_label.setStyleSheet("font-size: 32px;")
        header_layout.addWidget(icon_label)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(0)
        title_label = QLabel("MiFlashX")
        title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        subtitle_label = QLabel("Xiaomi Fastboot Flashing Tool")
        subtitle_label.setStyleSheet("font-size: 12px;")
        self._muted_labels.append((subtitle_label, "font-size: 12px;"))
        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        header_layout.addLayout(title_layout)
        header_layout.addStretch(1)

        main_layout.addLayout(header_layout)

        # Two columns instead of one tall stack. Windows are wider than they
        # are tall, and a single column of four cards needs ~1250px of height.
        columns = QHBoxLayout()
        columns.setSpacing(18)
        left_col = QVBoxLayout()
        left_col.setSpacing(18)
        right_col = QVBoxLayout()
        right_col.setSpacing(18)
        columns.addLayout(left_col, 1)
        columns.addLayout(right_col, 1)
        main_layout.addLayout(columns, 1)

        # --- System Status & Device Info Group ---
        status_group = QGroupBox("🖥️  System Status && Device Info")
        status_group.setObjectName("card")
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(10)

        self.adb_fastboot_status_label = QLabel("🔄 ADB/Fastboot: Checking...")
        self.udev_status_label = QLabel("🔄 Udev Rules (Linux): Checking...")
        self.adbusers_status_label = QLabel("🔄 User in 'adbusers' group: Checking...")
        self.device_status_label = QLabel("🔌 Device: Not connected")
        self.device_info_label = QLabel("ℹ️ Info: N/A")

        status_layout.addWidget(self.adb_fastboot_status_label)
        status_layout.addWidget(self.udev_status_label)
        status_layout.addWidget(self.adbusers_status_label)
        status_layout.addWidget(self.device_status_label)
        status_layout.addWidget(self.device_info_label)
        for _lbl in (self.adb_fastboot_status_label, self.udev_status_label,
                     self.adbusers_status_label, self.device_status_label,
                     self.device_info_label):
            _lbl.setWordWrap(True)

        linux_buttons_layout = QHBoxLayout()
        self.install_udev_button = QPushButton("🔧 Fix Udev Rules (Linux)")
        self.install_udev_button.clicked.connect(self.install_udev_rules_action)
        self.install_udev_button.setEnabled(False)
        linux_buttons_layout.addWidget(self.install_udev_button)

        self.add_adbusers_button = QPushButton("👤 Add User to 'adbusers' group (Linux)")
        self.add_adbusers_button.clicked.connect(self.add_to_adbusers_group_action)
        self.add_adbusers_button.setEnabled(False)
        linux_buttons_layout.addWidget(self.add_adbusers_button)

        self.linux_buttons_widget = QWidget()
        self.linux_buttons_widget.setLayout(linux_buttons_layout)
        status_layout.addWidget(self.linux_buttons_widget)

        left_col.addWidget(status_group)

        # --- ROM Selection Section ---
        rom_selection_group = QGroupBox("📦  ROM Selection && Extraction")
        rom_selection_group.setObjectName("card")
        rom_selection_layout = QVBoxLayout(rom_selection_group)
        rom_selection_layout.setSpacing(10)

        rom_path_layout = QHBoxLayout()
        self.rom_path_input = QLineEdit()
        self.rom_path_input.setPlaceholderText("Select Fastboot ROM (.tgz)")
        self.rom_path_input.setReadOnly(True)
        self.browse_rom_button = QPushButton("📁 Browse")
        self.browse_rom_button.clicked.connect(self.browse_rom)
        rom_path_layout.addWidget(self.rom_path_input)
        rom_path_layout.addWidget(self.browse_rom_button)
        rom_selection_layout.addLayout(rom_path_layout)

        self.extract_rom_button = QPushButton("📦 Extract ROM")
        self.extract_rom_button.clicked.connect(self.extract_rom)
        self.extract_rom_button.setEnabled(False)
        rom_selection_layout.addWidget(self.extract_rom_button)

        or_label = QLabel("— or, if you've already extracted this ROM before —")
        self._muted_labels.append((or_label, ""))
        rom_selection_layout.addWidget(or_label)

        self.use_extracted_folder_button = QPushButton("📂 Use Already-Extracted ROM Folder…")
        self.use_extracted_folder_button.clicked.connect(self.browse_extracted_folder)
        rom_selection_layout.addWidget(self.use_extracted_folder_button)

        self.extracted_path_label = QLabel("📭 Extracted ROM: None")
        rom_selection_layout.addWidget(self.extracted_path_label)

        left_col.addWidget(rom_selection_group)
        left_col.addStretch(1)

        # --- Flashing Options Section ---
        flashing_group = QGroupBox("⚡  Flashing Options")
        flashing_group.setObjectName("card")
        flashing_layout = QVBoxLayout(flashing_group)
        flashing_layout.setSpacing(6)

        flashing_layout.addWidget(QLabel("Select Flashing Mode:"))

        # Mode picker as selectable cards rather than a plain dropdown — for
        # a choice this consequential (one option wipes the whole device),
        # seeing all the trade-offs at once beats hiding them behind a
        # collapsed combo box. Also fixes a real UX/safety issue in the old
        # combo: its first (and therefore pre-selected) item was the
        # destructive "wipe all data" option. The safest option is now both
        # first and the default selection.
        self._flash_mode_group = QButtonGroup(self)
        self._flash_mode_group.setExclusive(True)
        self._flash_mode_radios = {}

        for mode_value, title, description, risk in _FLASH_MODE_INFO:
            option_frame = QFrame()
            option_frame.setObjectName("modeOption")
            option_frame.setProperty("risk", risk)
            option_layout = QVBoxLayout(option_frame)
            option_layout.setSpacing(2)
            option_layout.setContentsMargins(10, 4, 10, 4)

            radio = QRadioButton(f"{_STATUS_ICONS.get(risk, '')} {title.replace('&', '&&')}")
            radio.setStyleSheet("font-weight: 600;")
            desc_label = QLabel(description)
            desc_label.setWordWrap(True)
            self._muted_labels.append((desc_label, "font-size: 11px; margin-left: 22px;"))

            option_layout.addWidget(radio)
            option_layout.addWidget(desc_label)

            self._flash_mode_group.addButton(radio)
            self._flash_mode_radios[mode_value] = radio
            flashing_layout.addWidget(option_frame)

        # Default to the safest option, not the first-added one blindly.
        self._flash_mode_radios[FlashModes.SAVE_DATA_AND_STORAGE].setChecked(True)

        self.flash_button = QPushButton("⚡ Start Flashing")
        self.flash_button.clicked.connect(self.start_flashing_confirmation)
        self.flash_button.setEnabled(False)
        flashing_layout.addWidget(self.flash_button)

        right_col.addWidget(flashing_group)

        # --- Progress & Log Section ---
        log_group = QGroupBox("📜  Log Output")
        log_group.setObjectName("card")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(100)
        self.log_output.setFont(QFont("monospace", 9))
        log_layout.addWidget(self.log_output)
        right_col.addWidget(log_group, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Operation Progress: %p%")
        main_layout.addWidget(self.progress_bar)


        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")

        # Give the primary action its own accent styling and mark the four
        # sections as "cards" so apply_card_theme() can give each a drop
        # shadow — the actual elevation effect a stylesheet alone can't do.
        self.flash_button.setObjectName("primaryButton")
        self._cards = [status_group, rom_selection_group, flashing_group, log_group]

        # Animate progress bar value changes instead of jumping instantly —
        # set_progress() below is what the rest of the app should call.
        self._progress_anim = QPropertyAnimation(self.progress_bar, QByteArray(b"value"), self)
        self._progress_anim.setDuration(300)
        self._progress_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_progress(self, value: int):
        """Animate the progress bar to `value` instead of jumping instantly."""
        self._progress_anim.stop()
        self._progress_anim.setStartValue(self.progress_bar.value())
        self._progress_anim.setEndValue(value)
        self._progress_anim.start()

    def get_selected_flash_mode(self):
        """Returns the FlashModes value of whichever mode card is checked."""
        for mode_value, radio in self._flash_mode_radios.items():
            if radio.isChecked():
                return mode_value
        return None  # shouldn't happen -- one is always checked by default

    def get_selected_flash_mode_title(self):
        """Display title for the currently selected mode, for dialog text."""
        selected = self.get_selected_flash_mode()
        for mode_value, title, _description, _risk in _FLASH_MODE_INFO:
            if mode_value == selected:
                return title
        return "Unknown mode"

    def apply_card_theme(self):
        """
        Builds a stylesheet from the CURRENT application palette (not
        hardcoded colors), so the "card" look — rounded corners, spacing,
        hover states — adapts to light/dark mode and whatever accent color
        is active, instead of fighting it the way the old QSS did.

        Reads QApplication.instance().palette() rather than self.palette():
        immediately after apply_theme() calls app.setPalette(), this widget's
        own cached palette hasn't necessarily been re-resolved yet (that
        propagation happens via an event Qt hasn't processed at this point in
        the same call stack), so self.palette() can return stale colors here.
        The application-level palette is always current.
        """
        pal = QApplication.instance().palette()
        base = pal.color(pal.ColorRole.Base)
        window = pal.color(pal.ColorRole.Window)
        text = pal.color(pal.ColorRole.Text)
        accent = pal.color(pal.ColorRole.Highlight)
        mid = pal.color(pal.ColorRole.Mid)

        # A card needs to read as a distinct surface from the window behind
        # it. If the theme's Base and Window colors are too close (some
        # themes set them nearly identical), nudge the card surface instead
        # of silently rendering a flat, undifferentiated page.
        def _luminance(c: QColor) -> float:
            return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()

        is_dark = _luminance(window) < 128
        card_bg = base.lighter(106) if is_dark else base
        border = mid.name()
        accent_hover = accent.lighter(115).name()
        accent_pressed = accent.darker(110).name()

        # Muted/secondary text (subtitle, "— or —" divider, flash-mode
        # descriptions) needs a color that reads as "subdued" while staying
        # legible against the card background in BOTH themes. Bug this
        # replaces: these labels used to set "color: palette(mid)" directly,
        # but Mid is meant as a border/separator tone — in the dark palette
        # it's deliberately darker than the window color for that purpose,
        # which made text using it nearly invisible against a dark card.
        # Blending 55% toward the full-contrast text color (rather than
        # reusing a role designed for a different job) gives a muted tone
        # that scales correctly whether the theme is light, dark, or a
        # future accent scheme neither of us has tested against.
        def _blend(c1: QColor, c2: QColor, t: float) -> QColor:
            return QColor(
                int(c1.red() * t + c2.red() * (1 - t)),
                int(c1.green() * t + c2.green() * (1 - t)),
                int(c1.blue() * t + c2.blue() * (1 - t)),
            )
        muted_text = _blend(text, window, 0.55)
        # Disabled controls should look dimmed but stay readable. They used
        # `mid` (a border tone, darker than the window in the dark palette),
        # which turned disabled button labels into unreadable dark bars.
        disabled_text = _blend(text, window, 0.45)
        for label, extra_css in self._muted_labels:
            label.setStyleSheet(f"color: {muted_text.name()}; {extra_css}")

        self.setStyleSheet(f"""
            QWidget#centralArea {{
                background-color: {window.name()};
            }}

            QGroupBox#card {{
                background-color: {card_bg.name()};
                border: 1px solid {border};
                border-radius: 10px;
                margin-top: 14px;
                padding: 14px 10px 10px 10px;
                font-weight: 600;
                font-size: 13px;
            }}
            QGroupBox#card::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {text.name()};
            }}

            QPushButton {{
                background-color: {base.name()};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 7px 14px;
            }}
            QPushButton:hover:!disabled {{
                border-color: {accent.name()};
            }}
            QPushButton:pressed:!disabled {{
                background-color: {mid.lighter(115).name()};
            }}
            QPushButton:disabled {{
                color: {disabled_text.name()};
            }}

            /* Native radio indicators are drawn from Window.darker(), which is
               near-black on a dark card, so unselected options were invisible.
               Draw them explicitly from the live palette instead. */
            QRadioButton::indicator {{
                width: 10px;
                height: 10px;
                border-radius: 7px;
                border: 2px solid {muted_text.name()};
                background-color: {card_bg.name()};
            }}
            QRadioButton::indicator:hover {{
                border-color: {accent.name()};
            }}
            QRadioButton::indicator:checked {{
                border-color: {accent.name()};
                background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 {accent.name()}, stop:0.5 {accent.name()},
                    stop:0.6 {card_bg.name()}, stop:1 {card_bg.name()});
            }}
            QRadioButton::indicator:disabled {{
                border-color: {disabled_text.name()};
            }}

            QPushButton#primaryButton:!disabled {{
                background-color: {accent.name()};
                color: {pal.color(pal.ColorRole.HighlightedText).name()};
                border: none;
                font-weight: 600;
                font-size: 14px;
                padding: 10px 14px;
            }}
            QPushButton#primaryButton:hover:!disabled {{
                background-color: {accent_hover};
            }}
            QPushButton#primaryButton:pressed:!disabled {{
                background-color: {accent_pressed};
            }}

            QLineEdit, QComboBox, QTextEdit {{
                background-color: {base.name()};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border-color: {accent.name()};
            }}

            QProgressBar {{
                border: 1px solid {border};
                border-radius: 6px;
                text-align: center;
                background-color: {base.name()};
            }}
            QProgressBar::chunk {{
                background-color: {accent.name()};
                border-radius: 5px;
            }}
        """)

        # Elevation: a real drop shadow per card, not just a border. This is
        # the part a stylesheet genuinely cannot do on its own.
        shadow_color = QColor(0, 0, 0, 90 if is_dark else 45)
        for card in self._cards:
            effect = QGraphicsDropShadowEffect(card)
            effect.setBlurRadius(18)
            effect.setOffset(0, 3)
            effect.setColor(shadow_color)
            card.setGraphicsEffect(effect)

    def showEvent(self, event):
        """Fade the window in on first show — a small touch, but it's the
        one animation every user sees, every time, on every desktop."""
        super().showEvent(event)
        if not getattr(self, '_fade_played', False):
            self._fade_played = True
            self.setWindowOpacity(0.0)
            self._fade_anim = QPropertyAnimation(self, QByteArray(b"windowOpacity"), self)
            self._fade_anim.setDuration(220)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._fade_anim.start()

    def _pulse_label(self, label: QLabel):
        """Brief opacity pulse to draw the eye to a status change (e.g. a
        device just connected) without anything as heavy-handed as a popup."""
        effect = QGraphicsOpacityEffect(label)
        label.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, QByteArray(b"opacity"), label)
        anim.setDuration(450)
        anim.setStartValue(0.25)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        # Keep a reference so it isn't garbage-collected mid-animation.
        label._pulse_anim = anim

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
            self.adb_fastboot_status_label.setText("✅ ADB/Fastboot: <font color='green'>Found</font>")
            self.flashing_core = FlashingCore(
                adb_path=self.adb_path,
                fastboot_path=self.fastboot_path,
                output_callback=lambda level, msg: self.append_log(f"[{level.upper()}] {msg}")
            )
            self.append_log(f"[INFO] ADB: {self.adb_path}, Fastboot: {self.fastboot_path}")
            self.extract_rom_button.setEnabled(True)
        else:
            self.adb_fastboot_status_label.setText("❌ ADB/Fastboot: <font color='red'>Not Found</font>. Please ensure Android SDK Platform Tools are installed and accessible.")
            self.append_log('[ERROR] ADB/Fastboot not found. Cannot proceed without them. Ensure they are in your system PATH or correctly bundled.')
            self.flash_button.setEnabled(False)
            self.extract_rom_button.setEnabled(False)
            self.use_extracted_folder_button.setEnabled(False)
            self.device_status_label.setText("⚪ Device: <font color='red'>N/A (Tools Missing)</font>")
            self.device_info_label.setText("ℹ️ Info: N/A")
            self.udev_status_label.setText("⚪ Udev Rules (Linux): N/A (Tools Missing)")
            self.adbusers_status_label.setText("⚪ User in 'adbusers' group: N/A (Tools Missing)")
            self.linux_buttons_widget.setVisible(False)
            return

        if get_os() == "linux":
            udev_ok, udev_msg = check_udev_rules()
            if udev_ok:
                self.udev_status_label.setText(f"✅ Udev Rules (Linux): <font color='green'>{udev_msg}</font>")
                self.install_udev_button.setEnabled(False)
            else:
                self.udev_status_label.setText(f"⚠️ Udev Rules (Linux): <font color='red'>{udev_msg}</font>")
                self.install_udev_button.setEnabled(True)
                self.append_log('[WARNING] Udev rules might be missing or incorrect for Xiaomi/Android devices. This can cause \'no permissions\' errors with Fastboot.')
                self.append_log('[INFO] Click \'Fix Udev Rules\' if you encounter device detection/permission issues.')

            adbusers_ok = self._adbusers_ok()
            self.adbusers_status_label.setText("✅ User in 'adbusers' group: <font color='green'>Yes</font>" if adbusers_ok else "⚠️ User in 'adbusers' group: <font color='red'>No</font>")
            self.add_adbusers_button.setEnabled(not adbusers_ok)
            if not adbusers_ok:
                self.append_log('[WARNING] Your user is not in the \'adbusers\' group. This can cause permission issues. Click \'Add User to adbusers group\'.')
                self.append_log('[INFO] Remember to log out and back in after adding user to group for changes to take effect.')
        else:
            self.udev_status_label.setText("⚪ Udev Rules (Linux): N/A (Not Linux)")
            self.adbusers_status_label.setText("⚪ User in 'adbusers' group: N/A (Not Linux)")
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
            self.device_status_label.setText("⚠️ Device: <font color='orange'>Fastboot not responding (timed out)</font>")
            self.append_log('[WARNING] fastboot devices timed out. The device or USB port may be in a bad state — try replugging.')
            return

        if state.connected and state.serial != self.current_serial:
            self.current_serial = state.serial
            self.device_status_label.setText(f"✅ Device: <font color='green'>Connected ({state.serial})</font>")
            self._pulse_label(self.device_status_label)
            self.append_log(f'[INFO] Device connected: {state.serial}')

            # Fetch getvar-all info on a background thread rather than inline.
            self._info_worker = DeviceInfoWorker(self.flashing_core, state.serial, parent=self)
            self._info_worker.info_ready.connect(self._on_device_info_ready)
            self._info_worker.start()

        elif not state.connected and self.current_serial:
            self.current_serial = None
            self.current_device_codename = "Unknown"
            self.current_bootloader_status = "Unknown"
            self.device_status_label.setText("🔌 Device: <font color='red'>Disconnected</font>")
            self._pulse_label(self.device_status_label)
            self.device_info_label.setText("ℹ️ Info: N/A")
            self.flash_button.setEnabled(False)
            self.append_log('[INFO] Device disconnected.')

    def _on_device_info_ready(self, info: dict):
        self.current_device_codename = info.get('codename', 'Unknown')
        self.current_bootloader_status = info.get('bootloader_locked', 'Unknown')
        self.device_info_label.setText(f"ℹ️ Info: Codename: {self.current_device_codename}, Bootloader: {self.current_bootloader_status}")
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
            self._archive_selected = True
            self.extracted_path_label.setText("📭 Extracted ROM: None (Click 'Extract ROM')")
            self.extract_rom_button.setEnabled(True)
            self.flash_button.setEnabled(False)

    def browse_extracted_folder(self):
        """
        Lets the user point directly at a ROM they already extracted, instead
        of re-extracting the archive every time — extracting a multi-gigabyte
        fastboot ROM tarball repeatedly is slow and hard on an HDD, and
        there's no reason to redo it if the extracted files are still there.
        Validation is a handful of os.path.exists() checks (see
        core.validate_rom_directory), so it's fine to run directly here on
        the GUI thread rather than through a Worker.
        """
        selected_dir = QFileDialog.getExistingDirectory(
            self, "Select Already-Extracted ROM Folder", os.path.expanduser("~")
        )
        if not selected_dir:
            return

        ok, result = validate_rom_directory(selected_dir)
        if not ok:
            QMessageBox.critical(self, "Not a Valid ROM Folder", result)
            self.append_log(f"[WARNING] Rejected folder as ROM source: {result}")
            return

        resolved_path = result
        self.extracted_rom_path = resolved_path
        self._archive_selected = False
        # This path bypasses rom_path_input/extract_rom entirely, so make
        # that visually clear rather than leaving a stale/empty archive field.
        self.rom_path_input.setText("(using pre-extracted folder — no archive selected)")
        self.extract_rom_button.setEnabled(False)
        self.extracted_path_label.setText(
            f"✅ Extracted ROM: <font color='green'>{os.path.basename(resolved_path)} (existing folder)</font>"
        )
        self.append_log(f"[INFO] Using already-extracted ROM folder: {resolved_path}")

        self.flash_button.setEnabled(self.current_serial is not None and self.current_bootloader_status == "Unlocked")
        if not self.flash_button.isEnabled():
            self.append_log('[WARNING] Flashing button remains disabled. Ensure a device is connected in Fastboot mode and its bootloader is unlocked.')

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
            self.set_progress(100 if success else 0)
            self.progress_bar.setFormat("Extraction: %p%")
            if success:
                self.extracted_rom_path = message
                self.extracted_path_label.setText(f"✅ Extracted ROM: <font color='green'>{os.path.basename(self.extracted_rom_path)}</font>")
                QMessageBox.information(self, "Extraction Complete", "ROM extracted successfully!")
                self.statusBar.showMessage("ROM extracted. Ready to flash.")
                self.flash_button.setEnabled(self.current_serial is not None and self.current_bootloader_status == "Unlocked")
                if not self.flash_button.isEnabled():
                    self.append_log('[WARNING] Flashing button remains disabled. Ensure a device is connected in Fastboot mode and its bootloader is unlocked.')
            else:
                self.extracted_rom_path = None
                self.extracted_path_label.setText("❌ Extracted ROM: <font color='red'>Failed</font>")
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

        selected_mode_text = self.get_selected_flash_mode_title()
        flash_mode_data = self.get_selected_flash_mode()

        paragraphs = [
            f"You are about to flash the ROM using '{selected_mode_text}' mode to "
            f"device '{self.current_serial}' (Codename: {self.current_device_codename})."
        ]
        if flash_mode_data == FlashModes.CLEAN_ALL:
            paragraphs.append("<b style='color: red;'>WARNING: This mode will wipe ALL data on your device! Ensure you have a backup.</b>")
        elif flash_mode_data == FlashModes.SAVE_USER_DATA:
            paragraphs.append("<b style='color: orange;'>WARNING: This mode keeps user data but wipes the system partition. Proceed with caution.</b>")
        elif flash_mode_data == FlashModes.SAVE_DATA_AND_STORAGE:
            paragraphs.append("<b style='color: green;'>This mode aims to keep your user data and apps. It's generally safer for updates.</b>")
        elif flash_mode_data == FlashModes.LOCK_BOOTLOADER:
            paragraphs.append("<b style='color: red;'>EXTREME CAUTION: This mode will lock your bootloader after flashing. If you flash an incompatible ROM or encounter errors, your device may be bricked! Only use this if you are absolutely sure of the ROM's compatibility and integrity.</b>")

        paragraphs.append("Ensure your device battery is at least 50% charged and do NOT disconnect the device during flashing.")
        paragraphs.append("Are you absolutely sure you want to proceed?")

        if _rich_question(self, "Confirm Flashing Operation", paragraphs):
            self.start_flashing()
        else:
            self.append_log('[INFO] Flashing cancelled by user.')

    def start_flashing(self):
        flash_mode = self.get_selected_flash_mode()

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Flashing: %p%")
        self.append_log(f'[INFO] Initiating flashing process for \'{self.current_device_codename}\' with mode: {flash_mode}...')
        self.set_ui_enabled(False)
        self.statusBar.showMessage("Flashing device...")

        def on_flash_finished(success, message):
            self.set_ui_enabled(True)
            self.set_progress(100 if success else 0)
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
        self.extract_rom_button.setEnabled(enabled and self._archive_selected)
        self.use_extracted_folder_button.setEnabled(enabled)
        for _radio in self._flash_mode_radios.values():
            _radio.setEnabled(enabled)

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
        proceed = _rich_question(self, "Add User to 'adbusers' Group", [
            "This action requires administrator privileges. It will add your current user "
            "to the 'adbusers' group, which can help with device permissions. You'll be "
            "prompted via your system's authentication dialog.",
            "<b style='color: red;'>Important: You will need to log out and log back in for this change to take effect!</b>",
            "Do you want to proceed?",
        ])
        if proceed:
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