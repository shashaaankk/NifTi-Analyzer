#!/usr/bin/env python3
"""OS-agnostic installer for NIfTI Analyzer (Linux, Windows, macOS).

Run with the system Python:  python3 install.py   (Windows: py install.py)

Checks platform dependencies, creates the venv, installs the full package
including the contrast-phase model stack, and adds a launcher (application
menu on Linux, Start Menu on Windows). Idempotent: re-run after `git pull`
to upgrade. `--uninstall` reverts it.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SYSTEM = platform.system()
VENV = APP_DIR / ".venv"

LINUX_BIN = Path.home() / ".local" / "bin" / "nifti-analyzer"
LINUX_DESKTOP = Path.home() / ".local" / "share" / "applications" / "nifti-analyzer.desktop"
WIN_SHORTCUT = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
                / "Start Menu" / "Programs" / "NIfTI Analyzer.lnk")


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def fail(msg: str) -> None:
    sys.exit(f"ERROR: {msg}")


def venv_exe(name: str) -> Path:
    sub = "Scripts" if SYSTEM == "Windows" else "bin"
    return VENV / sub / (name + (".exe" if SYSTEM == "Windows" else ""))


def run_pip(*args: str) -> None:
    subprocess.run([str(venv_exe("python")), "-m", "pip", "install",
                    "--quiet", *args], check=True)


def check_platform_deps(assume_yes: bool) -> None:
    say("Checking platform dependencies")
    if sys.version_info < (3, 10):
        fail("Python 3.10 or newer is required to run this installer")
    if SYSTEM == "Linux":
        probe = ("import gi; gi.require_version('WebKit2', '4.1'); "
                 "from gi.repository import WebKit2")
        missing = []
        if subprocess.run([sys.executable, "-c", "import gi"],
                          capture_output=True).returncode != 0:
            missing.append("python3-gi")
        if subprocess.run([sys.executable, "-c", probe],
                          capture_output=True).returncode != 0:
            missing.append("gir1.2-webkit2-4.1")
        if subprocess.run([sys.executable, "-c", "import ensurepip"],
                          capture_output=True).returncode != 0:
            missing.append("python3-venv")
        if missing:
            cmd = f"sudo apt-get install -y {' '.join(missing)}"
            if not shutil.which("apt-get"):
                fail(f"missing system packages (Debian/Ubuntu names): "
                     f"{' '.join(missing)} — install their equivalents for "
                     f"your distribution, then re-run")
            print(f"Missing system packages: {' '.join(missing)}")
            if not assume_yes:
                answer = input(f"Run '{cmd}' now? [y/N] ").strip().lower()
                if answer != "y":
                    fail("install the packages above, then re-run")
            subprocess.run(cmd, shell=True, check=True)
    elif SYSTEM == "Windows":
        # pywebview uses the Edge WebView2 runtime, preinstalled on current
        # Windows 10/11. Nothing to install here; leave a pointer just in case.
        print("Note: if the window fails to open later, install the "
              "'Microsoft Edge WebView2 Runtime' from Microsoft.")
    # macOS: pywebview uses the system WebKit via pyobjc; pip handles it.


def make_venv() -> None:
    say("Setting up the virtual environment")
    if not VENV.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    if SYSTEM == "Linux":
        # pywebview needs the system PyGObject/WebKit2GTK, which pip cannot
        # provide, so the venv must see system site packages.
        cfg = VENV / "pyvenv.cfg"
        cfg.write_text(cfg.read_text().replace(
            "include-system-site-packages = false",
            "include-system-site-packages = true"))


def install_package() -> None:
    say("Installing NIfTI Analyzer")
    run_pip("--upgrade", "pip")
    # Without an NVIDIA GPU, CPU-only torch saves ~1.5 GB (Linux/Windows;
    # the default PyPI wheel is already CPU-based on macOS).
    if SYSTEM in ("Linux", "Windows") and not shutil.which("nvidia-smi"):
        say("No NVIDIA GPU detected: installing CPU-only torch")
        run_pip("torch", "--index-url", "https://download.pytorch.org/whl/cpu")
    run_pip(f"{APP_DIR}[phase]")
    if not (Path.home() / ".totalsegmentator").exists():
        print("Note: the first contrast-phase run downloads model weights "
              "(~1.5 GB) to ~/.totalsegmentator.")


def add_launcher() -> None:
    say("Adding the launcher")
    exe = venv_exe("nifti-analyzer")
    if SYSTEM == "Linux":
        LINUX_BIN.parent.mkdir(parents=True, exist_ok=True)
        if LINUX_BIN.is_symlink() or LINUX_BIN.exists():
            LINUX_BIN.unlink()
        LINUX_BIN.symlink_to(exe)
        LINUX_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        LINUX_DESKTOP.write_text(
            "[Desktop Entry]\nType=Application\nName=NIfTI Analyzer\n"
            "Comment=Geometry, HU statistics, and contrast phase for NIfTI CT volumes\n"
            f"Exec={exe}\nTerminal=false\nCategories=Science;\n")
    elif SYSTEM == "Windows":
        ps = (f"$s=(New-Object -ComObject WScript.Shell)."
              f"CreateShortcut('{WIN_SHORTCUT}');"
              f"$s.TargetPath='{exe}';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    else:
        print(f"Start the app with: {exe}")


def self_check() -> None:
    say("Running the self-check")
    r = subprocess.run([str(venv_exe("python")), "-c",
                        "from nifti_analyzer import desktop, runner, analysis"])
    if r.returncode != 0:
        fail("self-check failed: the installed package does not import")


def uninstall() -> None:
    say("Uninstalling")
    shutil.rmtree(VENV, ignore_errors=True)
    for p in (LINUX_BIN, LINUX_DESKTOP, WIN_SHORTCUT):
        try:
            p.unlink()
        except OSError:
            pass
    say("Removed venv and launcher. The cloned repo stays.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install NIfTI Analyzer")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the venv and the launcher")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="answer yes to prompts (system package install)")
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
        return

    check_platform_deps(args.yes)
    make_venv()
    install_package()
    add_launcher()
    self_check()
    launch = "nifti-analyzer" if SYSTEM == "Linux" else str(venv_exe("nifti-analyzer"))
    menu = ("the application menu" if SYSTEM == "Linux"
            else "the Start Menu" if SYSTEM == "Windows" else "the command above")
    say(f"Done. Start with '{launch}' or from {menu}.")


if __name__ == "__main__":
    main()
