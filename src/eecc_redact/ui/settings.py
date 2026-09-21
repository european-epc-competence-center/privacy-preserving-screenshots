"""Setup and settings: one window for the key, the hotkey and starting at login.

First run and later changes are the same window, so each thing has one place.
A key is checked against ShinrAI before it is stored, and Finish stays disabled
until one works. Setup runs until the user finishes it, not merely until some
key exists: one left in the keychain by an earlier install is pointed out, so
nobody suspects a key was baked into the build.
"""

import html
from collections.abc import Callable

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from eecc_redact import APP_NAME, __version__, desktop, keystore, platforms
from eecc_redact.config import Config
from eecc_redact.errors import AppError
from eecc_redact.hotkeys import DEFAULT_HOTKEY, parse_hotkey
from eecc_redact.shinrai import Shinrai
from eecc_redact.ui import Task, ensure_app, icon

SIGNUP_URL = "https://shinrai.innovius.io"
#: Windows keeps Print Screen to itself: pressing it never reaches the recorder.
PRINT_SCREEN_CHOICES = ("Print", "Ctrl+Print", "Shift+Print", "Alt+Print", "Ctrl+Shift+Print")


def store_name() -> str:
    if platforms.WINDOWS:
        return "Windows Credential Manager"
    if platforms.MACOS:
        return "the macOS Keychain"
    return "your keyring"


def _error(text: str) -> str:
    return f'<span style="color:#b3261e">{html.escape(text)}</span>'


def _label(text: str = "", name: str = "") -> QLabel:
    label = QLabel(text, objectName=name)
    label.setWordWrap(True)
    label.setOpenExternalLinks(True)
    return label


def _button(caption: str, name: str) -> QPushButton:
    button = QPushButton(caption, objectName=name)
    button.setAutoDefault(False)  # Enter in the key field checks the key, not closes
    return button


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: Config,
        *,
        first_run: bool = False,
        apply_hotkey: Callable[[str], str | None] | None = None,
        hotkey_note: str = "",
        open_hotkey_settings: Callable[[], bool] | None = None,
        client_factory=Shinrai,
    ) -> None:
        """`apply_hotkey` switches the live hotkey and returns a problem or None;
        the recorder appears only when it is given (Windows). Where the desktop
        binds the key instead (Linux), `hotkey_note` says what it is and
        `open_hotkey_settings` opens the desktop's page for changing it."""
        super().__init__()
        self.config = config
        self.first_run = first_run
        self.apply_hotkey = apply_hotkey
        self.client_factory = client_factory
        self.replacing = self.checking = False
        self.candidate = ""
        key, source = keystore.key_source()
        # Only a key already there when setup opened is "from an earlier install".
        self.inherited = first_run and source == "keychain" and bool(key)

        self.setWindowTitle(f"Set up {APP_NAME}" if first_run else f"{APP_NAME} settings")
        self.setWindowIcon(icon())
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(14)
        if first_run:
            layout.addWidget(
                _label(
                    f"<b>{APP_NAME} covers personal data in your screenshots before you share "
                    "them.</b><br>Screenshots are checked by ShinrAI, so it needs your own ShinrAI "
                    f'key. No key yet? <a href="{SIGNUP_URL}">Get a ShinrAI subscription</a>.'
                )
            )

        # -- key
        key_box = QGroupBox("ShinrAI key")
        key_layout = QVBoxLayout(key_box)
        self.key_status = _label(name="keyStatus")
        key_layout.addWidget(self.key_status)
        self.entry = QWidget()
        entry_row = QHBoxLayout(self.entry)
        entry_row.setContentsMargins(0, 0, 0, 0)
        self.key_field = QLineEdit(objectName="keyField")
        self.key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_field.setPlaceholderText("Paste your key (shr_live_… or shr_test_…)")
        show = QCheckBox("Show")
        show.toggled.connect(
            lambda on: self.key_field.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        self.check_button = _button("Check and save", "checkKey")
        entry_row.addWidget(self.key_field, 1)
        entry_row.addWidget(show)
        entry_row.addWidget(self.check_button)
        key_layout.addWidget(self.entry)
        self.manage = QWidget()
        manage_row = QHBoxLayout(self.manage)
        manage_row.setContentsMargins(0, 0, 0, 0)
        self.replace_button = _button("Replace key…", "replaceKey")
        self.remove_button = _button("Remove key", "removeKey")
        manage_row.addWidget(self.replace_button)
        manage_row.addWidget(self.remove_button)
        manage_row.addStretch(1)
        key_layout.addWidget(self.manage)
        self.key_result = _label(name="keyResult")
        key_layout.addWidget(self.key_result)
        layout.addWidget(key_box)

        # -- hotkey
        self.hotkey_edit: QKeySequenceEdit | None = None
        if apply_hotkey is not None:
            box = QGroupBox("Hotkey")
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(
                _label(
                    "Click the box, then press the keys that should start a capture. Print Screen "
                    "can't be recorded by pressing it; pick it from the Print Screen menu."
                )
            )
            row = QHBoxLayout()
            try:
                current = parse_hotkey(config.hotkey or DEFAULT_HOTKEY)
            except ValueError:
                current = parse_hotkey(DEFAULT_HOTKEY)
            self.hotkey_edit = QKeySequenceEdit(
                QKeySequence(current.qt_text), objectName="hotkeyEdit"
            )
            self.hotkey_edit.setMaximumSequenceLength(1)
            print_screen = QToolButton(objectName="printScreenMenu")
            print_screen.setText("Print Screen")
            print_screen.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(print_screen)
            for choice in PRINT_SCREEN_CHOICES:
                menu.addAction(choice).triggered.connect(
                    lambda _checked=False, choice=choice: self.hotkey_edit.setKeySequence(
                        QKeySequence(choice)
                    )
                )
            print_screen.setMenu(menu)
            use = _button("Use this hotkey", "useHotkey")
            use.clicked.connect(self.use_hotkey)
            row.addWidget(self.hotkey_edit, 1)
            row.addWidget(print_screen)
            row.addWidget(use)
            box_layout.addLayout(row)
            self.hotkey_result = _label(name="hotkeyResult")
            box_layout.addWidget(self.hotkey_result)
            layout.addWidget(box)
        elif hotkey_note:
            box = QGroupBox("Hotkey")
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(_label(html.escape(hotkey_note), "hotkeyNote"))
            if open_hotkey_settings is not None:
                self.hotkey_result = _label(name="hotkeyResult")

                def open_settings() -> None:
                    if not open_hotkey_settings():
                        self.hotkey_result.setText(
                            _error("Could not open them; use your desktop's keyboard settings.")
                        )

                open_button = _button("Open desktop settings…", "openHotkeySettings")
                open_button.clicked.connect(open_settings)
                row = QHBoxLayout()
                row.addWidget(open_button)
                row.addStretch(1)
                box_layout.addLayout(row)
                box_layout.addWidget(self.hotkey_result)
            layout.addWidget(box)

        # -- start at login
        self.startup: QCheckBox | None = None
        if desktop.supports_autostart():
            self.startup = QCheckBox(f"Start {APP_NAME} when I log in", objectName="startup")
            self.startup.setChecked(True if first_run else desktop.autostart_enabled(config))
            if not first_run:
                self.startup.toggled.connect(self._toggle_startup)
            self.startup_note = _label(name="startupNote")
            layout.addWidget(self.startup)
            layout.addWidget(self.startup_note)

        # -- footer
        footer = QHBoxLayout()
        footer.addWidget(_label(f"{APP_NAME} {html.escape(__version__)}"))
        footer.addStretch(1)
        if first_run:
            quit_button = _button("Quit", "quit")
            quit_button.clicked.connect(self.reject)
            footer.addWidget(quit_button)
        self.finish_button = _button("Finish" if first_run else "Close", "finish")
        self.finish_button.clicked.connect(self.finish)
        footer.addWidget(self.finish_button)
        layout.addLayout(footer)

        self.check_button.clicked.connect(self.check_key)
        self.key_field.returnPressed.connect(self.check_key)
        self.replace_button.clicked.connect(self.start_replacing)
        self.remove_button.clicked.connect(self.remove_key)
        self.refresh_key()

    # -- key -------------------------------------------------------------------

    def refresh_key(self) -> None:
        key, source = keystore.key_source()
        kind = {"production": "production ", "sandbox": "sandbox "}.get(
            keystore.environment(key), ""
        )
        self.entry.setVisible(not key or self.replacing)
        self.manage.setVisible(bool(key) and not self.replacing)
        if not key:
            self.key_status.setText("No key stored yet.")
        elif source == "keychain" and self.inherited and not self.replacing:
            self.key_status.setText(
                f"A {kind}key is already stored in {store_name()}, probably from an earlier "
                f"{APP_NAME} on this computer. Keep it, or replace or remove it."
            )
        elif source == "keychain":
            self.key_status.setText(f"A {kind}key is stored in {store_name()}.")
        else:
            self.key_status.setText(
                f"Using the {kind}key from the environment ({keystore.ENV_VAR}). {APP_NAME} "
                "can only replace or remove a key it stored itself."
            )
        own = source in ("keychain", "")
        self.replace_button.setEnabled(own)
        self.remove_button.setEnabled(own and bool(key))
        self.key_field.setEnabled(not self.checking)
        self.check_button.setEnabled(not self.checking)
        self.finish_button.setEnabled(bool(key) or not self.first_run)

    def check_key(self) -> None:
        key = self.key_field.text().strip()
        if not key:
            self.key_result.setText(_error("Paste a key first."))
            return
        self.checking = True
        self.candidate = key
        self.key_result.setText("Checking the key with ShinrAI…")
        self.refresh_key()
        config = self.config

        def work():
            with self.client_factory(
                key, base_url=config.base_url, project=config.project, location=config.location
            ) as client:
                return client.capabilities()

        task = Task(work)
        task.done.connect(self._key_checked, Qt.ConnectionType.QueuedConnection)
        task.start()

    @Slot(object, object)
    def _key_checked(self, capabilities, error) -> None:
        self.checking = False
        key = self.candidate
        if error is not None:
            message = (
                error.message
                if isinstance(error, AppError)
                else (f"The key could not be checked ({type(error).__name__}).")
            )
            self.key_result.setText(_error(message))
        elif not capabilities.image_redact:
            self.key_result.setText(
                _error(
                    "The key works, but this ShinrAI deployment offers no image redaction, so "
                    f"{APP_NAME} cannot use it. The key was not saved."
                )
            )
        else:
            keystore.set_key(key)
            self.key_field.clear()
            self.replacing = self.inherited = False
            parts = [
                {"production": "Production key saved.", "sandbox": "Sandbox key saved."}.get(
                    keystore.environment(key), "Key saved."
                )
            ]
            if capabilities.plan:
                parts.append(f"Plan: {capabilities.plan}.")
            if capabilities.records is not None:
                parts.append(f"{capabilities.records:,} records left.")
            self.key_result.setText(html.escape(" ".join(parts)))
        self.refresh_key()

    def start_replacing(self) -> None:
        self.replacing = True
        self.inherited = False
        self.key_result.setText("")
        self.refresh_key()
        self.key_field.setFocus()

    def remove_key(self) -> None:
        answer = QMessageBox.question(
            self,
            "Remove key",
            f"Remove the ShinrAI key from {store_name()}?\n\n"
            f"{APP_NAME} can't cover anything in screenshots until you add a key again.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        keystore.delete_key()
        self.replacing = self.inherited = False
        self.key_result.setText("Key removed.")
        self.refresh_key()

    # -- hotkey ----------------------------------------------------------------

    def use_hotkey(self) -> bool:
        if self.hotkey_edit is None or self.apply_hotkey is None:
            return True
        sequence = self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        chord = sequence.split(",")[0].strip()
        if not chord:
            self.hotkey_result.setText(_error("Press a key combination first."))
            return False
        try:
            hotkey = parse_hotkey(chord)
        except ValueError as exc:
            self.hotkey_result.setText(_error(str(exc)))
            return False
        if problem := self.apply_hotkey(hotkey.config_value):
            self.hotkey_result.setText(_error(problem))
            return False
        self.config.hotkey = hotkey.config_value
        self.config.save()
        self.hotkey_result.setText(f"{html.escape(hotkey.text)} starts a capture.")
        return True

    # -- start at login --------------------------------------------------------

    def _toggle_startup(self, enabled: bool) -> None:
        self.startup.setEnabled(False)
        self.startup_note.setText("")
        task = Task(lambda: desktop.set_autostart(enabled))
        task.done.connect(self._startup_applied, Qt.ConnectionType.QueuedConnection)
        task.start()

    @Slot(object, object)
    def _startup_applied(self, state, error) -> None:
        self.startup.setEnabled(True)
        if error is not None:
            reason = error.message if isinstance(error, AppError) else type(error).__name__
            self.startup_note.setText(_error(f"Starting at login was not changed: {reason}"))
            state = not self.startup.isChecked()
        elif state != self.startup.isChecked():
            self.startup_note.setText(
                _error(
                    f"Your desktop did not allow that. Check {APP_NAME}'s background permission."
                )
            )
        self.startup.blockSignals(True)
        self.startup.setChecked(bool(state))
        self.startup.blockSignals(False)
        _remember_autostart(self.config, state, error)

    # -- finishing -------------------------------------------------------------

    def finish(self) -> None:
        if self.first_run:
            if not keystore.get_key():
                self.key_result.setText(_error("Check and save a key first."))
                return
            if not self.use_hotkey():
                return
            if self.startup is not None and self.startup.isChecked():
                config = self.config
                task = Task(lambda: desktop.set_autostart(True))
                task.done.connect(
                    lambda state, error: _remember_autostart(config, state, error),
                    Qt.ConnectionType.QueuedConnection,
                )
                task.start()
            self.config.setup_complete = True
            self.config.save()
        self.accept()


def _remember_autostart(config: Config, state, error) -> None:
    """Linux cannot read the autostart state back, so the answer is kept in the config."""
    if platforms.LINUX and error is None:
        config.start_at_login = bool(state)
        config.save()


def show_settings(config: Config, **kwargs) -> bool:
    """True when the user finished setup or closed the window normally."""
    ensure_app()
    dialog = SettingsDialog(config, **kwargs)
    return dialog.exec() == QDialog.DialogCode.Accepted
