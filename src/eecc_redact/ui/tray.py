"""The tray: first-run setup, the hotkey, settings, and one capture at a time.

`eecc-redact` with no arguments is the launcher, the login entry and the terminal
command alike. When a tray already runs it opens that one's settings instead,
so clicking the launcher always shows something.

On Windows the tray registers the hotkey itself. On Linux the desktop binds it
through the GlobalShortcuts portal. Either way it works while the tray runs.
"""

from contextlib import suppress

from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from eecc_redact import APP_NAME, instance, keystore, platforms
from eecc_redact.config import Config
from eecc_redact.errors import AppError
from eecc_redact.hotkeys import DEFAULT_HOTKEY, parse_hotkey
from eecc_redact.pipeline import capture_once
from eecc_redact.ui import ensure_app, icon, main_thread
from eecc_redact.ui.settings import show_settings

WARNING = QSystemTrayIcon.MessageIcon.Warning


class Tray:
    def __init__(self, config: Config) -> None:
        self.app = ensure_app()
        self.config = config
        self.busy = self.settings_open = False
        self.announced = True  # how to capture is announced once, right after setup
        self.listener = None  # Windows: eecc_redact.hotkeys.windows.HotkeyListener
        self.portal_hotkey = None  # Linux: eecc_redact.hotkeys.linux.PortalHotkey
        self.server = None

        self.icon = QSystemTrayIcon(icon())
        self.menu = QMenu()
        self.capture_action = self.menu.addAction("Capture region")
        self.capture_action.triggered.connect(lambda: self.capture())
        self.menu.addAction("Settings…").triggered.connect(lambda: self.open_settings())
        self.menu.addSeparator()
        self.menu.addAction(f"Quit {APP_NAME}").triggered.connect(self.quit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(
            lambda reason: (
                self.capture() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )

    def run(self) -> int:
        self.server = instance.claim(self.on_message)
        if self.server is None:
            instance.send("show")  # bring up the running tray instead
            return 0
        if not QSystemTrayIcon.isSystemTrayAvailable():
            message = (
                f"This desktop has no system tray, so {APP_NAME} cannot stay in the "
                f"background. Start captures with `{APP_NAME} capture` instead."
            )
            print(message, flush=True)
            if not platforms.terminal():  # started from a launcher: nobody reads stdout
                QMessageBox.information(None, APP_NAME, message)
            return 1
        self.icon.show()
        # Setup runs until the user has finished it here, not merely until some
        # key exists, which may have been left by an earlier install.
        if not self.config.setup_complete or not keystore.get_key():
            if not self.open_settings(first_run=True):
                return 0
            self.announced = False
        self.start_hotkey()
        if platforms.terminal():
            print(
                f"{APP_NAME} is running in the tray, and this terminal is busy until you quit it "
                f"(Ctrl-C). To keep the terminal, start it with `{APP_NAME} &` instead.",
                flush=True,
            )
        try:
            return self.app.exec()
        finally:
            self.stop_hotkey()

    def on_message(self, message: str) -> None:
        if message == "show":
            self.open_settings()
        elif message == "capture":
            self.capture()
        elif message == "quit":
            self.quit()

    def capture(self) -> None:
        # The overlay and the review run nested loops; a second press meanwhile
        # would open a second overlay.
        if self.busy or self.settings_open:
            return
        if not keystore.get_key():
            self.open_settings()
            return
        self.busy = True
        try:
            capture_once(self.config)
        finally:
            self.busy = False

    def open_settings(self, first_run: bool = False) -> bool:
        if self.settings_open:
            return False
        self.settings_open = True
        try:
            open_hotkey_settings = None
            if platforms.LINUX:
                from eecc_redact.hotkeys.linux import open_desktop_settings

                open_hotkey_settings = open_desktop_settings
            return show_settings(
                self.config,
                first_run=first_run,
                apply_hotkey=self.apply_hotkey if platforms.WINDOWS else None,
                hotkey_note=self.hotkey_note() if platforms.LINUX else "",
                open_hotkey_settings=open_hotkey_settings,
            )
        finally:
            self.settings_open = False
            self.refresh()

    def quit(self) -> None:
        self.stop_hotkey()
        self.app.quit()

    # -- hotkey ----------------------------------------------------------------

    def start_hotkey(self) -> None:
        if platforms.WINDOWS:
            if problem := self.apply_hotkey(self.config.hotkey or DEFAULT_HOTKEY):
                self.icon.showMessage(APP_NAME, f"Hotkey not active: {problem}", WARNING)
            else:
                self.announce(f"Press {self.hotkey_text()} to capture.")
        elif platforms.LINUX:
            from eecc_redact.hotkeys.linux import PortalHotkey

            try:
                preferred = parse_hotkey(self.config.hotkey or DEFAULT_HOTKEY)
            except ValueError:
                preferred = parse_hotkey(DEFAULT_HOTKEY)
            self.portal_hotkey = PortalHotkey(
                main_thread(self.capture), main_thread(self.hotkey_changed)
            )
            self.portal_hotkey.start(preferred)
        else:
            self.announce("Click the tray icon to capture.")
        self.refresh()

    def stop_hotkey(self) -> None:
        if self.listener is not None:
            self.listener.stop()
        if self.portal_hotkey is not None:
            self.portal_hotkey.stop()

    def apply_hotkey(self, text: str) -> str | None:
        """Windows: switch to `text`. Returns the problem, in which case the
        previous hotkey is active again."""
        from eecc_redact.hotkeys.windows import HotkeyListener

        previous, self.listener = self.listener, None
        if previous is not None:
            previous.stop()
        if not text:
            return None
        try:
            listener = HotkeyListener(parse_hotkey(text), main_thread(self.capture))
            listener.start()
        except (ValueError, AppError) as exc:
            if previous is not None:
                with suppress(AppError):
                    previous.start()
                    self.listener = previous
            return str(exc)
        self.listener = listener
        self.refresh()
        return None

    def hotkey_changed(self) -> None:
        """Linux: the desktop bound the key, refused, or the user changed it."""
        self.refresh()
        hotkey = self.portal_hotkey
        if hotkey.problem:
            self.icon.showMessage(APP_NAME, f"No hotkey: {hotkey.problem}", WARNING)
        elif hotkey.description:
            self.announce(f"Press {hotkey.description} to capture.")

    def hotkey_text(self) -> str:
        if self.listener is not None:
            return self.listener.hotkey.text
        if self.portal_hotkey is not None:
            return self.portal_hotkey.description
        return ""

    def hotkey_note(self) -> str:
        """Linux: what the settings window says instead of offering a recorder."""
        from eecc_redact.hotkeys.linux import where_to_change

        # On Linux the desktop owns global shortcuts; the app can only ask for one.
        hotkey = self.portal_hotkey
        if hotkey is None:
            suggested = parse_hotkey(self.config.hotkey or DEFAULT_HOTKEY).text
            return (
                "When setup is finished, your desktop asks you to confirm the hotkey "
                f"(suggested: {suggested}); you can pick another in that dialog. It "
                f"remembers your choice, which is changed later in {where_to_change()}."
            )
        if hotkey.description:
            return (
                f"{hotkey.description} starts a capture. Your desktop holds this shortcut, "
                f"so it is changed in {where_to_change()}, not here."
            )
        if hotkey.problem:
            return f"No hotkey: {hotkey.problem}"
        return "Waiting for your desktop to confirm the hotkey…"

    # -- presentation ----------------------------------------------------------

    def announce(self, hint: str) -> None:
        if not self.announced:
            self.announced = True
            self.icon.showMessage(f"{APP_NAME} is running", hint)

    def refresh(self) -> None:
        shown = self.hotkey_text()
        self.capture_action.setText(f"Capture region\t{shown}" if shown else "Capture region")
        self.icon.setToolTip(
            f"{APP_NAME} - {shown} to capture" if shown else f"{APP_NAME} - redacted screenshots"
        )


def run_tray(config: Config) -> int:
    return Tray(config).run()
