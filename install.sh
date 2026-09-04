#!/usr/bin/env bash
# NIfTI Analyzer installer (Linux only).
#
# Checks system dependencies, creates the venv, installs the full package
# including the contrast-phase model stack, links the command into
# ~/.local/bin, and adds an application-menu entry.
# Idempotent: re-run after `git pull` to upgrade. `--uninstall` reverts it.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$APP_DIR/.venv"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="$HOME/.local/share/applications/nifti-analyzer.desktop"

UNINSTALL=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=1 ;;
        -y|--yes)    ASSUME_YES=1 ;;
        -h|--help)
            echo "Usage: ./install.sh [--uninstall] [-y]"
            echo "  --uninstall  remove the venv, the command link, and the menu entry"
            echo "  -y           answer yes to prompts (system package install)"
            exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 1 ;;
    esac
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

if [ "$UNINSTALL" -eq 1 ]; then
    say "Uninstalling"
    rm -rf "$VENV"
    rm -f "$BIN_DIR/nifti-analyzer" "$DESKTOP_FILE"
    say "Removed venv, command link, and menu entry. The cloned repo stays."
    exit 0
fi

[ "$(uname -s)" = "Linux" ] || fail "this installer supports Linux only"

# --- system dependencies ----------------------------------------------------
say "Checking system dependencies"
MISSING=()
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null \
    || fail "python3 >= 3.10 is required"
python3 -c 'import ensurepip' 2>/dev/null || MISSING+=(python3-venv)
python3 -c 'import gi' 2>/dev/null || MISSING+=(python3-gi)
python3 -c 'import gi; gi.require_version("WebKit2", "4.1"); from gi.repository import WebKit2' 2>/dev/null \
    || MISSING+=(gir1.2-webkit2-4.1)

if [ "${#MISSING[@]}" -gt 0 ]; then
    if command -v apt-get >/dev/null; then
        CMD="sudo apt-get install -y ${MISSING[*]}"
        echo "Missing system packages: ${MISSING[*]}"
        if [ "$ASSUME_YES" -eq 1 ]; then
            $CMD
        elif [ -t 0 ]; then
            read -r -p "Run '$CMD' now? [y/N] " answer
            [ "$answer" = "y" ] || fail "install the packages above, then re-run"
            $CMD
        else
            fail "run '$CMD', then re-run this script"
        fi
    else
        fail "missing system packages (Debian/Ubuntu names): ${MISSING[*]} — install their equivalents for your distribution, then re-run"
    fi
fi

# --- venv -------------------------------------------------------------------
say "Setting up the virtual environment"
[ -d "$VENV" ] || python3 -m venv "$VENV"
# pywebview needs the system PyGObject/WebKit2GTK, which pip cannot provide.
sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' "$VENV/pyvenv.cfg"

say "Installing NIfTI Analyzer"
"$VENV/bin/pip" install --quiet --upgrade pip
# Without an NVIDIA GPU, CPU-only torch saves ~1.5 GB.
if ! command -v nvidia-smi >/dev/null || ! nvidia-smi -L 2>/dev/null | grep -q GPU; then
    say "No NVIDIA GPU detected: installing CPU-only torch"
    "$VENV/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
fi
"$VENV/bin/pip" install --quiet "$APP_DIR[phase]"
if [ ! -d "$HOME/.totalsegmentator" ]; then
    echo "Note: the first contrast-phase run downloads model weights (~1.5 GB) to ~/.totalsegmentator."
fi

# --- integration ------------------------------------------------------------
say "Linking the command and menu entry"
mkdir -p "$BIN_DIR" "$(dirname "$DESKTOP_FILE")"
ln -sf "$VENV/bin/nifti-analyzer" "$BIN_DIR/nifti-analyzer"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Note: $BIN_DIR is not in your PATH; the menu entry works regardless." ;;
esac
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=NIfTI Analyzer
Comment=Geometry, HU statistics, and contrast phase for NIfTI CT volumes
Exec=$VENV/bin/nifti-analyzer
Terminal=false
Categories=Science;
EOF
command -v update-desktop-database >/dev/null \
    && update-desktop-database "$(dirname "$DESKTOP_FILE")" 2>/dev/null || true

# --- self-check -------------------------------------------------------------
say "Running the self-check"
"$VENV/bin/python" -c "from nifti_analyzer import desktop, runner, analysis" \
    || fail "self-check failed: the installed package does not import"

say "Done. Start with 'nifti-analyzer' or from the application menu."
