"""The one place that asks which operating system this is.

Code that only runs on one OS lives in a module named after it (`windows.py`,
`linux.py`, `wayland.py`) and defers its OS-only imports, so every module
imports everywhere. Everything else branches on the names here, never on
`sys.platform`, so searching for `platforms.` finds every OS difference.

    feature            Windows              Linux Wayland       Linux X11 / macOS
    region capture     freeze + overlay     screenshot portal   freeze + overlay
    global hotkey      registered by tray   shortcuts portal    shortcuts portal / none
    start at login     HKCU Run key         background portal   background portal / none
    clipboard          flushed to Windows   held until pasted   held until pasted
    key storage        Credential Manager   Secret Service      Secret Service / Keychain
"""

import os
import sys

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")


def wayland() -> bool:
    """A Wayland session, where applications may not read the screen or grab keys."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return os.environ.get("XDG_SESSION_TYPE", "wayland") != "x11"
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def flatpak() -> bool:
    """Inside a Flatpak sandbox, which identifies the app to the portals itself."""
    return LINUX and os.path.exists("/.flatpak-info")
