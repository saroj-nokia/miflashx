# Changelog

All notable changes to MiFlashX are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). Nothing has been tagged as
a formal release yet — everything below is grouped as one in-progress
overhaul, in the order the work actually happened.

## [Unreleased] — Architecture overhaul, native theming, ROM import

### Added
- **`command_runner.py`** — subprocess wrapper with merged stdout/stderr and
  a mandatory timeout on every call.
- **`device_monitor.py`** — event-driven USB device detection via `pyudev`,
  with a 5-second safety-net poll for setups where udev events don't fire
  reliably.
- **Light / Dark / Follow System theme switcher** (`View → Theme` menu),
  persisted via `QSettings`. Light and Dark use explicit, hand-built
  palettes paired with `app.setStyle("Fusion")`, so switching works
  regardless of whether the desktop has Qt theme integration (`qt6ct`,
  `adwaita-qt6`) installed — notably, stock Fedora Workstation doesn't ship
  either by default, which was the original motivation.
- **"Use Already-Extracted ROM Folder…"** button — imports a previously
  extracted ROM directly, skipping re-extraction. Backed by
  `core.validate_rom_directory()`, which confirms the folder (or its
  `images/` subfolder, or one level of nesting) actually contains a
  `flash_all*.sh`/`.bat` script before accepting it.
- **Card-based UI**: the four main sections now render as elevated cards
  (rounded corners, drop shadows via `QGraphicsDropShadowEffect`) with
  colors computed from the live palette rather than hardcoded, plus hover/
  focus/pressed states on inputs and buttons.
- **Animations**: progress bar value changes now ease smoothly
  (`set_progress()`) instead of jumping instantly; the window fades in on
  first show; the device-status label pulses briefly on connect/disconnect.
- **`packaging/install.sh`** and **`packaging/miflashx.desktop`** — a
  user-level installer that resizes the app icon into standard `hicolor`
  sizes and registers a proper application-launcher entry, so MiFlashX
  shows up in the desktop menu instead of only being runnable as a
  standalone binary.
- **`requirements.txt`** — single source of truth for runtime dependencies
  (`PyQt6`, `Pillow`, `pyudev`), also used by CI's pip cache key.

### Changed
- **Consolidated `main.py` and `gui.py`.** These previously contained two
  entirely separate, independent implementations of the app — `main.py`'s
  own `MiFlashX(QMainWindow)` with its own `DeviceDetectionThread`, and a
  more complete, styled version in `gui.py`. PyInstaller was pointed at
  `main.py`, so the older, less-complete implementation was what actually
  shipped; `gui.py`'s version was only ever pulled in as an unused
  PyInstaller hidden-import. `main.py` is now a ~40-line launcher; `gui.py`
  is the one and only implementation.
- **All subprocess calls now go through `command_runner.py`.** Closes a
  deadlock in the old manual `Popen` + sequential-`readline` approach:
  reading all of stdout before touching stderr would hang forever once a
  process (`fastboot` especially) wrote enough to stderr to fill its pipe
  buffer.
- **Device detection moved off the GUI thread entirely.** Previously a
  `QTimer` polled `fastboot devices` synchronously every 2 seconds *on the
  GUI thread*, freezing the whole app on every poll and hanging completely
  if `fastboot` wedged. Now handled by `DeviceMonitor`, with all fastboot
  confirmation and `getvar all` lookups running on background `QThread`s.
- **Privilege escalation switched from `sudo` to `pkexec`** for the udev
  rules and `adbusers` group actions in `utils.py`. `sudo` has no
  controlling terminal when launched from a desktop icon or app menu and
  fails immediately ("no tty present and no askpass program specified");
  `pkexec` shows a proper graphical prompt regardless of launch method.
- **Log directory moved to `~/.local/share/miflashx/logs/`** (XDG standard),
  away from a path next to `__file__` — which, under PyInstaller's
  `--onefile` mode, resolved inside the temporary extraction directory,
  so logs silently vanished after every run.
- **CI workflow (`build_linux.yml`) rewritten**: removed `continue-on-error:
  true` on the build step, which was masking build failures — the job could
  report green while silently producing no artifact. Added the actual
  Ubuntu system packages PyQt6 needs to even import (`libegl1`,
  `libxcb-cursor0`, etc.) — the previous system-dependency install step was
  present but commented out and written for Fedora's `dnf`, not Ubuntu's
  `apt`, so it never applied on the `ubuntu-latest` runner. Added an
  explicit check that `dist/MiFlashX` exists before upload, with
  `if-no-files-found: error`.

### Fixed
- **`Worker.run()` crash**: it unconditionally injected an `output_callback`
  keyword argument into every function it called, but `install_udev_rules()`
  and `add_to_adbusers_group()` never accepted that parameter — clicking
  either "Fix Udev Rules" or "Add User to adbusers group" always raised
  `got an unexpected keyword argument 'output_callback'` and died before
  reaching `pkexec`. `Worker` now inspects the target function's signature
  and only passes the callback to functions that actually declare it.
- **CI cache error** ("No file ... matched to `**/requirements.txt` or
  `**/pyproject.toml`"): `actions/setup-python`'s `cache: pip` needs a
  lockfile to key against, which the repo didn't have. Added
  `requirements.txt`.
- **Card stylesheet not updating on theme switch**: `apply_card_theme()`
  read `self.palette()` immediately after `app.setPalette()`, before Qt had
  propagated the change to this widget — native chrome (menu bar, status
  bar) would go dark correctly while the custom card stylesheet stayed on
  stale light colors. Fixed by reading `QApplication.instance().palette()`
  directly instead.
