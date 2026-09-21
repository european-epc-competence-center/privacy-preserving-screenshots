"""How eecc-redact is wired into the desktop session: the launcher entry, start at login.

* windows - the current user's Run key, and a console-less start command
* linux   - the .desktop entry, and the Background portal for autostart
"""

from contextlib import suppress

from eecc_redact import platforms
from eecc_redact.config import Config


def install_launcher() -> None:
    """Linux outside a sandbox: write the launcher entry the desktop knows the app by.

    Before Qt starts: it registers the app ID with the desktop's portal during
    startup, and the portal only accepts an ID it can find a launcher entry for.
    """
    if platforms.LINUX and not platforms.flatpak():
        from eecc_redact.desktop import linux

        with suppress(OSError):
            linux.install_desktop_entry()


def supports_autostart() -> bool:
    return platforms.WINDOWS or platforms.LINUX


def autostart_enabled(config: Config) -> bool:
    if platforms.WINDOWS:
        from eecc_redact.desktop import windows

        return windows.autostart_enabled()
    return config.start_at_login


def set_autostart(enabled: bool) -> bool:
    """Ask the OS to start the tray at login, or to stop. Returns the resulting state.

    On Linux this goes through a portal and blocks until the desktop answers.
    """
    if platforms.WINDOWS:
        from eecc_redact.desktop import windows

        windows.set_autostart(enabled)
        return enabled
    if platforms.LINUX:
        from eecc_redact.desktop import linux

        return linux.set_autostart(enabled)
    return False


def remove_integration() -> None:
    """Undo starting at login and the launcher entry, for uninstalling."""
    set_autostart(False)
    if platforms.LINUX and not platforms.flatpak():
        from eecc_redact.desktop import linux

        linux.desktop_entry_path().unlink(missing_ok=True)
        linux.icon_path().unlink(missing_ok=True)
