"""Windows: starting at sign-in through the current user's Run key.

No administrator rights are needed. The tray starts without a console window:
as the installed, windowless executable, or through pythonw.exe from source.
"""

import subprocess
import sys
from pathlib import Path

from eecc_redact import APP_NAME, MODULE

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def background_command(executable: str | None = None, frozen: bool | None = None) -> list[str]:
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:  # an installed, windowless exe; it cannot run `-m`
        return [executable or sys.executable]
    python = Path(executable or sys.executable)
    windowless = python.with_name("pythonw.exe")
    return [str(windowless if windowless.exists() else python), "-m", MODULE]


def _winreg():
    import winreg

    return winreg


def set_autostart(enabled: bool, registry=None) -> None:
    reg = registry or _winreg()
    if enabled:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            reg.SetValueEx(
                key, APP_NAME, 0, reg.REG_SZ, subprocess.list2cmdline(background_command())
            )
        return
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def autostart_enabled(registry=None) -> bool:
    reg = registry or _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
            value, _kind = reg.QueryValueEx(key, APP_NAME)
    except FileNotFoundError:
        return False
    return bool(value)
