#!/bin/bash
# install.sh — run this after build_linux.sh, from the project root:
#   ./packaging/install.sh
#
# Installs MiFlashX into the current user's XDG directories (no sudo needed):
#   ~/.local/bin/miflashx
#   ~/.local/share/icons/hicolor/<size>/apps/miflashx.png   (several sizes)
#   ~/.local/share/applications/miflashx.desktop
#
# This is what actually makes the app show up correctly in a GNOME/KDE/XFCE
# app launcher with a proper icon, instead of only being runnable as a
# standalone binary from a terminal.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

BIN_SRC="dist/MiFlashX"
ICON_SRC="assets/icon.png"
DESKTOP_SRC="packaging/miflashx.desktop"

BIN_DEST="$HOME/.local/bin"
ICON_BASE="$HOME/.local/share/icons/hicolor"
DESKTOP_DEST="$HOME/.local/share/applications"

if [ ! -f "$BIN_SRC" ]; then
    echo "Error: $BIN_SRC not found. Run build_scripts/build_linux.sh first." >&2
    exit 1
fi
if [ ! -f "$ICON_SRC" ]; then
    echo "Error: $ICON_SRC not found. Run generate_icon.py first." >&2
    exit 1
fi

# --- Install the binary ---
mkdir -p "$BIN_DEST"
cp "$BIN_SRC" "$BIN_DEST/miflashx"
chmod +x "$BIN_DEST/miflashx"
echo "Installed binary: $BIN_DEST/miflashx"

# --- Install icons at standard hicolor sizes ---
# The project only ships one 256x256 icon.png; resize it down for the sizes
# the icon theme actually looks for so it doesn't get blurry-scaled at
# small sizes in menus/docks.
python3 - "$ICON_SRC" "$ICON_BASE" <<'PYEOF'
import sys
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    print("Warning: Pillow not installed, skipping icon resize step.")
    print("Run: pip install Pillow")
    sys.exit(0)

icon_src, icon_base = sys.argv[1], Path(sys.argv[2])
img = Image.open(icon_src).convert("RGBA")

for size in (16, 22, 24, 32, 48, 64, 128, 256):
    out_dir = icon_base / f"{size}x{size}" / "apps"
    out_dir.mkdir(parents=True, exist_ok=True)
    resized = img.resize((size, size), Image.LANCZOS)
    resized.save(out_dir / "miflashx.png")
    print(f"Installed icon: {out_dir / 'miflashx.png'}")
PYEOF

# --- Install the .desktop file ---
mkdir -p "$DESKTOP_DEST"
cp "$DESKTOP_SRC" "$DESKTOP_DEST/miflashx.desktop"
echo "Installed desktop entry: $DESKTOP_DEST/miflashx.desktop"

# --- Refresh caches so the menu/icon show up without a re-login ---
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DEST" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICON_BASE" 2>/dev/null || true
fi

# --- PATH check ---
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
        echo ""
        echo "Note: $HOME/.local/bin is not on your PATH."
        echo "Add this to your shell's rc file to run 'miflashx' from a terminal:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo "(The app launcher entry will work regardless.)"
        ;;
esac

echo ""
echo "Done. MiFlashX should now appear in your application launcher."
