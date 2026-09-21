"""Linux: the hotkey is bound by the desktop, through the GlobalShortcuts portal.

The first time, GNOME shows a dialog where the user confirms the suggested key
or picks another; it remembers the answer and binds silently on later starts.
The shortcut belongs to eecc-redact's portal session, so it works exactly while the
tray runs: quitting turns it off. The key is changed in the desktop's settings.

Three other designs were tried and dropped: a gsettings custom shortcut (2.7 s
per press, silently lost from a bundle), a shortcut calling the tray over
D-Bus (GNOME-only, impossible from a sandbox), and the portal, which stayed.
"""

import os
import shlex
import shutil
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable

from eecc_redact import APP_ID, APP_NAME, MODULE
from eecc_redact.errors import AppError, Cancelled
from eecc_redact.hotkeys import Hotkey
from eecc_redact.portal import MISSING, Portal, PortalError

INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
SHORTCUT_ID = "capture"
DESCRIPTION = "Capture a region with personal data covered"


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def where_to_change() -> str:
    if "GNOME" in _desktop():
        return f"GNOME Settings > Apps > {APP_NAME} > Global Shortcuts"
    if "KDE" in _desktop():
        return "System Settings > Keyboard > Shortcuts"
    return "your desktop's keyboard settings"


def settings_command() -> list[str] | None:
    """How to open the desktop's page for this app's shortcuts, where we know it."""
    if "GNOME" in _desktop():
        return ["gnome-control-center", "applications", f"{APP_ID}.desktop"]
    if "KDE" in _desktop():
        return ["systemsettings", "kcm_keys"]
    return None


def host_environment() -> dict[str, str]:
    """The environment for a program that is not ours.

    A frozen build points LD_LIBRARY_PATH at its own libraries; a desktop program
    started with that would load them instead of the system's.
    """
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", "")
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def open_desktop_settings() -> bool:
    """Open the desktop's shortcut settings for this app. False where we cannot."""
    command = settings_command()
    if command is None or shutil.which(command[0]) is None:
        return False
    subprocess.Popen(
        command,
        env=host_environment(),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def describe(shortcuts) -> str:
    """The key bound to capture, as the desktop writes it, from an a(sa{sv})."""
    for shortcut_id, properties in shortcuts or ():
        if shortcut_id == SHORTCUT_ID:
            return str(properties.get("trigger_description", ("s", ""))[1])
    return ""


def explain(exc: AppError) -> str:
    """Why there is no hotkey, and what the user can do about it."""
    from eecc_redact.desktop.linux import desktop_entry_path, launcher

    if isinstance(exc, Cancelled):
        return f"the shortcut was not confirmed. {APP_NAME} asks again the next time it starts."
    if "app id is required" in exc.message.lower():
        return (
            f"the desktop did not recognise {APP_NAME}'s launcher entry "
            f"({desktop_entry_path()}). Log out and in, then start {APP_NAME} again."
        )
    if isinstance(exc, PortalError) and exc.name in MISSING:
        command = shlex.join([*launcher(), "capture"])
        return (
            "this desktop has no global shortcuts portal. Add a keyboard shortcut in your "
            f"desktop's settings that runs: {command}"
        )
    return exc.message


class PortalHotkey:
    """eecc-redact's capture shortcut, held for as long as it is started.

    Both callbacks run on a background thread; wrap them with
    eecc_redact.ui.main_thread before touching Qt.
    """

    def __init__(self, on_press: Callable[[], None], on_change: Callable[[], None]) -> None:
        self._on_press = on_press
        self._on_change = on_change
        self._portal: Portal | None = None
        self._session = ""
        #: The key as the desktop describes it, such as "Ctrl+Shift+Print".
        self.description = ""
        #: Why no key is bound, for the user; "" while binding or once bound.
        self.problem = ""

    def start(self, preferred: Hotkey) -> None:
        """Bind in the background. `on_change` runs once the outcome is known, and
        again whenever the user changes the key in the desktop's settings."""
        threading.Thread(
            target=self._bind, args=(preferred,), daemon=True, name=f"{APP_NAME}-shortcuts"
        ).start()

    def _bind(self, preferred: Hotkey) -> None:
        try:
            portal = self._portal = Portal()
            portal.subscribe(INTERFACE, "Activated", self._activated)
            portal.subscribe(INTERFACE, "ShortcutsChanged", self._changed)
            self._session = portal.request(
                INTERFACE,
                "CreateSession",
                "a{sv}",
                (),
                {
                    "session_handle_token": ("s", f"{MODULE}_{uuid.uuid4().hex[:12]}"),
                },
            )["session_handle"]
            # No timeout: the first time, this waits for the user to answer the dialog.
            results = portal.request(
                INTERFACE,
                "BindShortcuts",
                "oa(sa{sv})sa{sv}",
                (
                    self._session,
                    [
                        (
                            SHORTCUT_ID,
                            {
                                "description": ("s", DESCRIPTION),
                                "preferred_trigger": ("s", preferred.portal),
                            },
                        )
                    ],
                    "",
                ),
                {},
                timeout=None,
            )
            self.description = describe(results.get("shortcuts"))
            if not self.description:
                self.problem = f"no key is assigned. Choose one in {where_to_change()}."
        except AppError as exc:
            self.problem = explain(exc)
        self._on_change()

    def _activated(self, body: tuple) -> None:
        session, shortcut_id, *_rest = body
        if session == self._session and shortcut_id == SHORTCUT_ID:
            self._on_press()

    def _changed(self, body: tuple) -> None:
        session, shortcuts = body
        if session == self._session:
            self.description = describe(shortcuts)
            self.problem = (
                ""
                if self.description
                else (f"no key is assigned. Choose one in {where_to_change()}.")
            )
            self._on_change()

    def stop(self) -> None:
        portal, self._portal = self._portal, None
        if portal is not None:
            portal.close()
