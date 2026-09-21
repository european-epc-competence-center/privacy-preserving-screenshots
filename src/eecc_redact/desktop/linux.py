"""Linux: the launcher entry, and starting at login through the Background portal.

A Flatpak brings its own desktop entry and identity. Installed any other way,
the app writes a launcher entry for itself on every start: it gives the app a
place in the app grid, and it is what lets the portals accept the app ID (see
eecc_redact.portal.Portal). An AppImage is mounted somewhere new on every
start, so the entry points at the AppImage file, not at the running program.
"""

import os
import shutil
import sys
from pathlib import Path

from platformdirs import user_data_path

from eecc_redact import APP_ID, APP_NAME, MODULE, platforms
from eecc_redact.portal import Portal

BACKGROUND = "org.freedesktop.portal.Background"


def launcher() -> list[str]:
    """How a shortcut or a terminal starts the app."""
    if platforms.flatpak():
        return ["flatpak", "run", APP_ID]
    if appimage := os.environ.get("APPIMAGE"):
        return [appimage]
    executable = shutil.which(APP_NAME)
    return [executable] if executable else [sys.executable, "-m", MODULE]


def desktop_entry(command: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Screenshots with the personal data already covered\n"
        f"Exec={command}\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Utility;Graphics;\n"
    )


def desktop_entry_path() -> Path:
    return user_data_path() / "applications" / f"{APP_ID}.desktop"


def icon_path() -> Path:
    return user_data_path() / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"


def install_desktop_entry() -> Path:
    path = desktop_entry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Desktop entries quote with double quotes, not shell quoting.
    command = " ".join(f'"{arg}"' if " " in arg else arg for arg in launcher())
    path.write_text(desktop_entry(command), "utf-8")
    if appdir := os.environ.get("APPDIR"):  # the AppImage carries the icon
        icon_path().parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(appdir) / f"{APP_ID}.png", icon_path())
    return path


def set_autostart(enabled: bool) -> bool:
    """Ask the desktop to start the tray at login. Blocks until it answers.

    GNOME asks the user only if background apps are restricted. Returns
    whether the app now starts at login.
    """
    # Inside a sandbox the desktop adds `flatpak run` itself.
    command = [APP_NAME] if platforms.flatpak() else launcher()
    with Portal() as portal:
        results = portal.request(
            BACKGROUND,
            "RequestBackground",
            "sa{sv}",
            ("",),
            {
                "reason": ("s", f"Start {APP_NAME} at login, so the hotkey works right away"),
                "autostart": ("b", enabled),
                "commandline": ("as", command),
                "dbus-activatable": ("b", False),
            },
        )
    return bool(results.get("autostart", False))
