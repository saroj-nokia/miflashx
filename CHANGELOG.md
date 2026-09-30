# Changelog

All notable changes to MiFlashX are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). Nothing has been tagged as
a formal release yet — everything below is grouped as one in-progress
overhaul, in the order the work actually happened.

## [Unreleased] — Wayland compatibility investigation

### Investigated, no bug found
- **The app was already effectively Wayland-capable at the packaging level.**
  Confirmed empirically, not just by reading docs: `build_linux.sh` had a
  `--collect-submodules PyQt6.QtXcbQpa` flag that looked like it was forcing
  X11 platform-plugin inclusion, but `PyQt6.QtXcbQpa` isn't a real importable
  module — the flag did nothing. PyInstaller's own built-in `PyQt6.QtGui` hook
  already collects the entire `platforms/` plugin directory automatically
  (both `libqxcb.so` and `libqwayland.so`), plus the three
  `wayland-*-integration-client` plugin folders, every time `QtGui` is
  imported. Verified by building the real binary from the actual
  `build_linux.sh` and running it with `QT_QPA_PLATFORM=wayland`: Qt located
  and attempted to load the plugin ("even though it was found" — it only
  fails here for the expected reason, no compositor socket in a sandbox),
  and confirmed removing the inert flag changes the bundled file set not at
  all.
- **No X11-specific API usage found in the source** (`winId`, `grabMouse`,
  absolute window positioning, `QSystemTrayIcon`, etc.) — checked directly
  via search across every module.

### Changed
- Removed the inert `--collect-submodules PyQt6.QtXcbQpa` line from
  `build_linux.sh` and replaced it with an accurate comment, since it was
  actively misleading (implying it was responsible for X11 support it never
  provided) and left no clear explanation of what actually handles platform
  plugin bundling (nothing needs to — see above).
- **`main.py`**: added `app.setDesktopFileName("miflashx")`. This was the one
  genuine gap: native Wayland compositors (GNOME Shell, KDE Plasma) match a
  running window to its `.desktop` entry via the Wayland `app_id` property,
  which Qt sets from this call. Under X11/XWayland, the more lenient
  WM_CLASS-based matching often worked without it; native Wayland is
  stricter, so without this, taskbar icon/alt-tab grouping/dock pinning
  could end up wrong. Must match `packaging/miflashx.desktop`'s installed
  base name, which it does.

## [Unreleased] — Fixes found by testing against real Xiaomi ROM scripts

The user provided the actual, unmodified `flash_all.sh`, `flash_all_except_data_storage.sh`, and `flash_all_lock.sh` from a real sapphire ROM. Running them through the app's actual code (not synthetic test scripts) surfaced one blocking bug and two correctness issues.

### Fixed
- **Blocking: the app could not flash any real Xiaomi ROM.** Xiaomi ships
  every flash script with no shebang line (confirmed on all three real
  files). `flash_rom()` executed the script path directly via `subprocess`,
  which has no shell-fallback for a missing shebang the way a login shell
  does — this raised `OSError: [Errno 8] Exec format error` immediately,
  before a single `fastboot` command ran, on every real ROM. Reproduced
  against the actual unmodified script, then fixed by invoking `sh
  <script>` explicitly instead of the script path alone. Re-verified against
  the real script end to end: it now runs to completion.
- **The device serial was never passed to the flashing script.** Every
  `fastboot` call inside Xiaomi's scripts is written as `fastboot $*
  <command>` — `$*` expands to whatever arguments the script itself was
  called with. `flash_rom()` never passed any, so every call was unscoped:
  harmless with exactly one fastboot device connected, silently wrong or
  ambiguous with two. `flash_rom()` now takes a `serial` parameter and
  invokes the script as `sh <script> -s <serial>`; `gui.py` passes
  `self.current_serial`. Verified against the real script: all 36 fastboot
  calls it makes now correctly carry `-s <serial>`.
- **Duplicate flashing mode was a false choice.** "Safest — keep apps &
  data" and "Keep user data" mapped to the identical script candidate list,
  just tried in a different order — a ROM only ships one of
  `flash_all_except_data_storage.sh` / `flash_all_except_storage.sh`, so
  choosing either option always ran the exact same script. Removed
  `FlashModes.SAVE_USER_DATA`; three real modes remain (safest/keep-data,
  clean install, flash & lock), matching the three scripts Xiaomi actually
  ships.
- **Generic "Flashing process failed" message replaced with specific
  detection** of the two named failure conditions every real flash script
  checks for itself before touching the device: a device/ROM model mismatch
  and an anti-rollback version block. Both now surface a clear, specific
  explanation instead of "check logs for details."

## [Unreleased] — Follow-up audit after the layout rewrite

### Fixed
- **Confirmation dialogs showed raw HTML instead of rendering it.**
  `QMessageBox.question()` only treats its text as rich text if the string
  *starts* with a tag. Both the flash-mode confirmation and the "Add User to
  adbusers group" confirmation open with a plain sentence and put
  `<b style='color: red;'>` warnings further in, so the tags printed
  literally instead of rendering — confirmed by grabbing the actual dialog.
  Added `_rich_question()`, which builds the `QMessageBox` directly and
  forces `Qt.TextFormat.RichText`; both dialogs now use it. Re-rendered both
  after the fix to confirm the warnings display as styled bold/red text.
- **`command_runner.run_command()`**: rewrote to stream output line-by-line as
  the process runs instead of returning everything at once when it finishes,
  so a multi-minute flash shows progress live rather than dumping output only
  at the end. Also fixed: on timeout, the whole process group is now killed,
  not just the direct child — the old version could leave `fastboot` running
  against the device if a flash script spawned it as a child and only the
  wrapping script got killed. Verified both with direct timing/process
  checks, including a control test proving the leak-detection check actually
  works.
- **`core.py` flash-mode-to-script mapping was too rigid.** It only ever
  looked for `flash_all_except_data_storage.sh`, but current MIUI/HyperOS
  ROMs ship `flash_all_except_storage.sh` instead — a ROM only contains one
  of the two names, so the "keep data" modes failed outright against roughly
  half of ROMs in circulation. Added `resolve_flash_script()`, which tries
  both names per mode and is called before flashing starts, so a ROM missing
  the needed script is rejected immediately instead of after 15 minutes.
  Device-query output (`fastboot devices`, `getvar all`) no longer duplicates
  into the on-screen log now that command output streams live.
- Removed three unused imports (`sys`, `QSizePolicy`, `QSpacerItem`) left
  over from the layout rewrite; confirmed clean with `pyflakes` across every
  module.

## [Unreleased] — UI modernization

### Added
- **Header banner** at the top of the window (app icon, "MiFlashX" title,
  subtitle), giving the UI a visual anchor instead of dropping straight
  into the first card.
- **Status icons on every status row** (ADB/Fastboot, udev rules,
  `adbusers` group, device connection, extracted ROM): ✅ ok, ❌ missing,
  ⚠️ needs attention, ⚪ not applicable, 🔄 checking, 🔌 device. Previously
  status was conveyed by text color alone.
- **Icons on all buttons** (🔧 Fix Udev Rules, 👤 Add User to group,
  📁 Browse, 📦 Extract ROM, 📂 Use Already-Extracted ROM Folder,
  ⚡ Start Flashing) for quicker scanning.

### Changed
- **Flashing mode picker is now a set of selectable options with
  descriptions** (radio buttons with a risk icon and one-line explanation
  each) instead of a collapsed dropdown. For a choice where one option
  wipes the whole device, showing every trade-off at once beats hiding
  them behind a combo box. This also fixes a safety issue in the old
  dropdown: its first, and therefore pre-selected, item was the
  destructive "wipe all data" option. The safest option ("keep apps and
  data") is now both listed first and selected by default.
- **"Start Flashing" is visually the primary action**: larger font and
  extra padding so it stands out from secondary buttons.
- Slightly larger card titles and more spacing between status rows, for
  stronger visual hierarchy.
- **Two-column layout with a scrolling page.** The single stack of four cards
  needed ~1250px of height (minimum hint 1116px), taller than most laptop
  screens, so the bottom half of the window was cut off. Setup (status, ROM
  selection) now sits on the left and flashing options plus the log on the
  right, and the whole page lives in a scroll area so it can never force the
  window taller than the display. The initial window size is derived from the
  screen's available area instead of a fixed 900x700. Long status lines now
  wrap instead of stretching the column.

### Fixed
- **Near-invisible secondary text in dark mode.** The header subtitle, the
  "— or, if you've already extracted this ROM before —" divider, and the
  flashing-mode descriptions were styled with `color: palette(mid)`. `Mid`
  is a border/separator role, and the dark palette deliberately makes it
  darker than the window background, so text using it nearly vanished on
  dark cards. `apply_card_theme()` now computes a muted text color by
  blending the live text and window colors, and applies it to all such
  labels centrally, so it re-applies correctly on every theme switch.
  Verified by rendering both themes before and after.
- **Crash on Extract ROM and Start Flashing.** When the flashing-mode dropdown
  was replaced by selectable options, four call sites still referenced the
  removed `flash_mode_combo` (in `set_ui_enabled`, `start_flashing_confirmation`
  and `start_flashing`), raising `AttributeError` as soon as either button was
  used. They now use `get_selected_flash_mode()` /
  `get_selected_flash_mode_title()`, and `set_ui_enabled()` enables and
  disables the radio options. Verified end to end with a stubbed flasher:
  each of the four modes reaches `flash_rom()` unchanged.
- **Stray underscores in "keep apps _data" and "Flash _lock bootloader".** In a
  `QRadioButton`, `&` marks a keyboard mnemonic, so the `&` in those titles was
  swallowed and the next letter underlined. The ampersand is now escaped.
- **Invisible unselected radio circles and unreadable disabled buttons in dark
  mode.** Both drew from the `Mid` colour, which the dark palette makes darker
  than the card behind it. Radio indicators are now drawn explicitly (muted
  ring, accent-coloured dot when selected) and disabled button text uses a
  colour blended from the live text and window colours.

## [Unreleased] — Security audit

### Fixed (CWE-1333 / CWE-400 / CWE-730, flagged by GitHub code scanning)
- **Removed `modify_spec.py` entirely.** Its two regex patterns — used to
  hand-patch `pathex` and `debug=True` into PyInstaller's generated `.spec`
  file — both had catastrophic (exponential) backtracking:
  `(?:,\s*.*?)*` and `(?:,\s*\S+?)*`, nested unbounded quantifiers inside a
  repeated group. Confirmed by direct timing test, not just static
  analysis: `n=10` repeated comma-separated segments took 0.05s; `n=15`
  (five more, well under 100 bytes of input) already exceeded 5 seconds,
  worsening from there. CWE-400 and CWE-730 are the same underlying alert
  as CWE-1333, tagged with its broader parent categories (Uncontrolled
  Resource Consumption / Denial of Service) — not three separate findings.
  `build_scripts/build_linux.sh` now passes `--paths` and `--debug=imports`
  directly to `pyi-makespec`, PyInstaller's own built-in mechanism for
  exactly what those regexes were hand-patching — eliminating the vulnerable
  code path rather than just hardening it.
- **Decompression-bomb guard** (`_assert_reasonable_total_size` in
  `core.py`): found no reported finding for this, but it's the same CWE-400
  category and a natural gap to close while already auditing the extraction
  path. Rejects any archive whose declared total uncompressed size exceeds
  20 GB (generous headroom over a real ROM's 3-6 GB), checked against the
  archive's own metadata before extracting a single byte. Verified against
  an actual crafted bomb (a small compressed file declaring 25 GB
  uncompressed) — rejected instantly.
- **Audited, no issue found**: the four remaining regex patterns in the
  codebase (`core.py`'s device-serial/bootloader-status parsing, `gui.py`'s
  log-level parsing) were stress-tested with adversarial input matching
  their actual call sites (`re.match` vs `re.search`, worst-case strings
  for each) — all confirmed genuinely linear-time. An initial test using
  `.search()` where the real code uses `.match()` gave a misleading
  quadratic-looking result; re-tested against the actual method used and
  confirmed safe.

### Fixed (path traversal, found independently of the scanner)
- **Path traversal in ROM extraction ("Zip Slip" / "Tar Slip", same class as
  CVE-2007-4559)**: `extract_rom()`'s three `extractall()` calls (primary
  `.tgz`, nested `.zip`, nested `.tar`) performed no validation of archive
  member paths. A malicious file offered as a "ROM" — a realistic threat
  here, since the README itself points people at third-party sources like
  XDA — could contain an entry such as `../../../.bashrc` and write outside
  the intended extraction directory entirely independent of any fastboot
  interaction. Added `_assert_within()`, `_safe_extract_tar()`, and
  `_safe_extract_zip()` in `core.py`, which validate every member's path
  (and symlink/hardlink targets) before extracting anything, plus pass
  `filter='data'` on Python 3.12+ as a second layer. Verified with actual
  crafted-archive attacks against both the tar and nested-zip paths — both
  blocked, both with legitimate ROM extraction confirmed unaffected.

### Changed
- **Pinned minimum dependency versions** in `requirements.txt` and
  `build_scripts/build_linux.sh` to exclude known CVEs found during audit:
  `PyQt6>=6.9.3` (Qt CVE-2025-5455 QtCore DoS, CVE-2025-10729 Qt SVG
  use-after-free), `Pillow>=12.3.0` (multiple 2026 buffer-overflow/DoS CVEs
  plus CVE-2026-55798, a Windows-only command-injection flaw in
  `ImageShow`), `pyinstaller>=6.10.0` (CVE-2025-59042, a local
  privilege-escalation bug in the frozen-app bootstrap process). No CVE was
  found specifically against `pyudev`; its floor was raised to `>=0.24.1`
  for currency rather than a specific fix.

### Audited, no issue found
- All `subprocess` calls use list-form arguments with no `shell=True`
  anywhere in the codebase — no shell-injection surface.
- No use of `eval`, `exec` (Python's, as distinct from Qt's unrelated
  `QDialog.exec()`), or `pickle`.
- Temp file creation (`utils.install_udev_rules`) uses `tempfile`'s secure
  creation (mode 0600, non-predictable name) rather than a guessed path.

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