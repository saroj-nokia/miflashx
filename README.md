# MiFlashX: Xiaomi Fastboot Flashing Tool for Linux

MiFlashX is a graphical utility for flashing official Xiaomi Fastboot ROMs
from Linux. It wraps `adb`/`fastboot` in a PyQt6 GUI: pick a ROM, pick a
flashing mode, watch the log, done.

**Linux only**, by design — it manages `udev` rules and the `adbusers`
group, neither of which mean anything on Windows or macOS.

## Features ✨

* **Fastboot ROM flashing** from official `.tgz`/`.tar.gz` archives.
* **Import an already-extracted ROM folder** instead of re-extracting the
  archive every time — useful if you're working off an HDD, where repeated
  extraction of a multi-gigabyte archive is slow and unnecessary once
  you've already done it once.
* **Multiple flashing modes**: clean install (wipes everything), keep user
  data, keep data *and* storage (safest for routine updates), or flash-and-lock.
* **Event-driven device detection** — reacts to USB plug/unplug via `udev`
  instead of polling, so it doesn't stall waiting on `fastboot`.
* **Bootloader status check** before it'll let you flash anything.
* **Udev rules / `adbusers` group setup** from inside the app, via a proper
  graphical authentication prompt (`pkexec`) — works whether you launched
  MiFlashX from a terminal or an app menu icon.
* **Light / Dark / Follow System theme switcher**, because on distros
  without Qt-GTK theme integration installed (stock Fedora Workstation
  among them), Qt has no way to detect your desktop's dark mode or accent
  color on its own — see [Theming](#theming-) below.
* **Real-time log output**, and a proper desktop integration (menu entry +
  icon) via the included installer script.

## Requirements 📋

* **OS:** A modern Linux distribution. Fedora and Ubuntu are both tested;
  anything with a recent Python 3 and Qt6 runtime libraries should work.
* **Python:** 3.10 or newer.
* **System Qt/XCB libraries** — PyQt6 needs these to even import, not just
  to run the built binary. Most desktop-flavored installs already have
  them; a minimal/server install or a CI runner usually won't.

  Fedora:
  ```bash
  sudo dnf install -y mesa-libGL mesa-libEGL libxkbcommon-x11 \
      libxcb libX11 xcb-util-cursor xcb-util-image xcb-util-keysyms \
      xcb-util-renderutil xcb-util-wm dbus-libs
  ```
  Ubuntu/Debian:
  ```bash
  sudo apt-get install -y libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 \
      libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
      libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 \
      libdbus-1-3
  ```
* **`pkexec`** (polkit) — for the in-app udev/adbusers setup buttons.
  Preinstalled on GNOME and KDE; install `polkit` manually on minimal
  window-manager setups if it's missing.
* **ADB and Fastboot** — MiFlashX looks for a bundled `platform-tools/`
  folder first, then falls back to your system `PATH`.

## Project Structure 📁

```
miflashx/
├── main.py                  # Thin launcher: QApplication setup only
├── gui.py                   # The actual application (MiFlashX QMainWindow)
├── core.py                  # Flashing/extraction logic, ROM folder validation
├── utils.py                 # Logging, OS detection, udev/adbusers via pkexec
├── device_monitor.py        # udev-based event-driven USB device detection
├── command_runner.py        # Deadlock-safe subprocess wrapper with timeouts
├── generate_icon.py         # Generates assets/icon.png
├── requirements.txt         # Runtime deps: PyQt6, Pillow, pyudev
├── assets/
│   └── icon.png
├── platform-tools/          # adb/fastboot binaries (not included — see below)
├── build_scripts/
│   └── build_linux.sh       # PyInstaller build script
├── packaging/
│   ├── install.sh           # User-level installer: binary + icons + .desktop
│   └── miflashx.desktop     # App-launcher entry
├── udev_rules/
│   └── 51-android.rules     # Reference copy; utils.py generates this content
└── .github/workflows/
    └── build_linux.yml      # CI build (requires system libs — see workflow)
```

At runtime, logs go to `~/.local/share/miflashx/logs/miflashx.log` and
extracted ROMs default to `~/.local/share/miflashx/roms/` — both XDG
locations, not folders inside the project directory.

## Building 🛠️

```bash
git clone https://github.com/saroj-nokia/miflashx.git
cd miflashx
```

**1. Get ADB/Fastboot.** Download `platform-tools-latest-linux.zip` from
[Google's site](https://developer.android.com/tools/releases/platform-tools),
extract it, and copy its contents into `platform-tools/` at the project
root:
```bash
mkdir -p platform-tools
# unzip the download, then:
cp /path/to/extracted/platform-tools/* platform-tools/
chmod +x platform-tools/adb platform-tools/fastboot
```

**2. Install Python dependencies:**
```bash
pip install -r requirements.txt
```

**3. Generate the icon:**
```bash
python3 generate_icon.py
```

**4. Build:**
```bash
chmod +x build_scripts/build_linux.sh
./build_scripts/build_linux.sh
```
This installs `pyinstaller` alongside the runtime deps and produces
`dist/MiFlashX`, a single-file executable.

**5. Install it properly (recommended):**
```bash
chmod +x packaging/install.sh
./packaging/install.sh
```
This copies the binary to `~/.local/bin/miflashx`, generates icon sizes for
the `hicolor` theme, and registers a `.desktop` entry — so MiFlashX shows
up in your actual application launcher with a real icon, not just as a
binary you run from a terminal. No `sudo` needed; it's a per-user install.

Alternatively, just run the built binary directly without installing:
```bash
./dist/MiFlashX
```

## Theming 🎨

**View → Theme** in the menu bar offers **Follow System / Light / Dark**,
saved across restarts.

Why this exists: Qt only knows your desktop's dark-mode state or accent
color if something bridges Qt to your desktop's actual theming system —
`qt6ct` (most distros), `adwaita-qt6` (GNOME specifically), or KDE's native
Breeze integration. **Stock Fedora Workstation doesn't ship any of these by
default**, so without installing one yourself, `Follow System` will look
identical to plain Fusion regardless of what your GNOME dark-mode toggle
says. `Light` and `Dark` sidestep this entirely with hand-built palettes
that work the same on every distro, with or without theme integration
installed.

If you *do* have `qt6ct` or `adwaita-qt6` set up and want `Follow System`
to actually follow it, that should already work — it restores whatever
palette Qt reported at startup.

## Usage 📝

### First-time Linux setup

On first launch, MiFlashX checks for `udev` rules and `adbusers` group
membership and shows their status. If either is missing:

1. Click **"Fix Udev Rules"** / **"Add User to 'adbusers' group"**. Each
   pops up your desktop's normal graphical authentication prompt
   (via `pkexec`) — no terminal `sudo` password needed.
2. **Log out and back in** after the `adbusers` group change — group
   membership only takes effect on your next login, not immediately.
3. Replug your device.

### Flashing a ROM

1. **Unlock your bootloader officially first** (Xiaomi's Mi Unlock Tool —
   Windows only, unrelated to this app). MiFlashX cannot do this for you,
   and it will refuse to flash a locked device. This wipes your data.
2. **Download the correct Fastboot ROM** for your *exact* device model —
   `.tgz`/`.tar.gz` only, never a Recovery `.zip`, and never another
   device's ROM. Wrong ROM = bricked device.
3. **Boot your device into Fastboot mode** (Volume Down + Power while
   powered off) and connect it via USB. MiFlashX should detect it within a
   couple of seconds — no restart or manual refresh needed.
4. **Get the ROM into a usable folder**, either:
   - **Browse → Extract ROM**: point at the archive, extract it once, or
   - **Use Already-Extracted ROM Folder…**: if you've extracted this ROM
     before and it's still on disk, skip straight to this — no need to
     re-extract.
5. **Pick a flashing mode** from the dropdown and click **Start Flashing**.
   Read the confirmation dialog — it calls out exactly what each mode does
   and doesn't wipe.
6. **Don't disconnect the device.** Flashing can take several minutes; the
   device reboots on its own when done. First boot afterward is slow —
   that's normal.

## Troubleshooting ⚠️

* **"ADB/Fastboot: Not Found"** — populate `platform-tools/` as described
  above, or install `android-tools` (or your distro's equivalent package)
  system-wide and ensure it's on `PATH`.
* **Udev/permissions errors, or "Fix Udev Rules" does nothing visible** —
  make sure `pkexec` is installed and a polkit authentication agent is
  running (standard on GNOME/KDE sessions; may be missing on a bare window
  manager). Check `~/.local/share/miflashx/logs/miflashx.log` for the
  specific failure if the graphical prompt never appears.
* **Device not detected** — confirm Fastboot mode, try a different cable
  and port, and check `sudo fastboot devices` from a terminal: if that
  detects it but MiFlashX doesn't, it's a udev/permissions problem, not a
  MiFlashX bug — revisit the setup step above.
* **Theme doesn't match my desktop** — see [Theming](#theming-); pick
  Light or Dark manually rather than relying on Follow System unless you've
  specifically set up `qt6ct`/`adwaita-qt6`.
* **Flashing fails** — check the bootloader is actually unlocked (most
  common cause), confirm the ROM matches your exact device model, and read
  the Log Output panel for the actual error rather than just the failure
  dialog.
* **Building fails with a PyQt6 import error** — you're likely missing one
  of the system Qt/XCB libraries listed under Requirements above; this is
  the most common build failure on minimal installs and CI runners.

## Contributing 🤝

Issues and pull requests welcome — see [CHANGELOG.md](CHANGELOG.md) for
what's changed recently and why, which is useful context before touching
`gui.py` or `core.py` in particular.

## License 📄

Open-source — see the `LICENSE` file in the project root.