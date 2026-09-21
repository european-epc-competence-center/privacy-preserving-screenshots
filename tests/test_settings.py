"""The setup and settings window, driven headlessly with a fake ShinrAI and an
in-memory key store, each case in its own process."""

from conftest import child

PRELUDE = """
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QKeySequenceEdit, QLabel,
                                   QLineEdit, QMessageBox, QPushButton)
    import eecc_redact.desktop as desktop
    import eecc_redact.keystore as keystore
    from eecc_redact.config import Config
    from eecc_redact.errors import AppError
    from eecc_redact.shinrai import Capabilities
    from eecc_redact.ui import ensure_app
    from eecc_redact.ui.settings import SettingsDialog

    app = ensure_app()
    store = {"key": ""}
    keystore._read_keychain = lambda: store["key"]
    keystore.set_key = lambda key: store.__setitem__("key", key)
    keystore.delete_key = lambda: store.__setitem__("key", "")
    desktop.set_autostart = lambda enabled: enabled          # never touch the OS
    desktop.autostart_enabled = lambda config: False
    QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
    config = Config()
    config.save = lambda: None                                # never touch the real config

    class GoodClient:
        def __init__(self, key, **kwargs):
            self.key = key
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def capabilities(self):
            return Capabilities(image_redact=True, plan="starter", records=49566)

    class RejectingClient(GoodClient):
        def capabilities(self):
            raise AppError("ShinrAI rejected your key.")

    class UselessClient(GoodClient):
        def capabilities(self):
            return Capabilities()

    def find(dialog, kind, name):
        return dialog.findChild(kind, name)

    def when(condition, then):
        def tick():
            if condition():
                then()
            else:
                QTimer.singleShot(50, tick)
        QTimer.singleShot(50, tick)
"""


def test_first_run_checks_the_key_before_saving_it_and_then_finishes():
    out = child(
        PRELUDE,
        """
        dialog = SettingsDialog(config, first_run=True, client_factory=GoodClient)
        def start():
            print("FINISH_BEFORE", find(dialog, QPushButton, "finish").isEnabled())
            find(dialog, QLineEdit, "keyField").setText("shr_test_FAKE_FOR_TESTS_123")
            find(dialog, QPushButton, "checkKey").click()
            when(lambda: "saved" in find(dialog, QLabel, "keyResult").text(), after)
        def after():
            print("RESULT", find(dialog, QLabel, "keyResult").text())
            find(dialog, QPushButton, "finish").click()
        QTimer.singleShot(100, start)
        finished = dialog.exec() == QDialog.DialogCode.Accepted
        print("FINISHED", finished, "STORED", store["key"], "COMPLETE", config.setup_complete)
    """,
    )
    assert "FINISH_BEFORE False" in out  # no key, no way past setup
    assert "Sandbox key saved. Plan: starter. 49,566 records left." in out
    assert "FINISHED True STORED shr_test_FAKE_FOR_TESTS_123 COMPLETE True" in out


def test_a_rejected_or_useless_key_is_not_saved():
    out = child(
        PRELUDE,
        """
        for factory in (RejectingClient, UselessClient):
            dialog = SettingsDialog(config, first_run=True, client_factory=factory)
            def start():
                find(dialog, QLineEdit, "keyField").setText("shr_live_WRONG_KEY_000")
                find(dialog, QPushButton, "checkKey").click()
                when(lambda: "not saved" in find(dialog, QLabel, "keyResult").text()
                     or "rejected" in find(dialog, QLabel, "keyResult").text(), after)
            def after():
                print("FINISH_ENABLED", find(dialog, QPushButton, "finish").isEnabled())
                dialog.reject()
            QTimer.singleShot(100, start)
            dialog.exec()
            print("STORED", repr(store["key"]))
    """,
    )
    assert out.count("FINISH_ENABLED False") == 2
    assert out.count("STORED ''") == 2


def test_a_left_over_key_is_pointed_out_and_can_be_replaced_or_removed():
    out = child(
        PRELUDE,
        """
        store["key"] = "shr_live_LEFT_BY_AN_EARLIER_INSTALL_444"
        dialog = SettingsDialog(config, first_run=True, client_factory=GoodClient)
        def start():
            print("STATUS", find(dialog, QLabel, "keyStatus").text())
            print("FINISH_ENABLED", find(dialog, QPushButton, "finish").isEnabled())
            find(dialog, QPushButton, "replaceKey").click()
            find(dialog, QLineEdit, "keyField").setText("shr_live_REPLACEMENT_888")
            find(dialog, QPushButton, "checkKey").click()
            when(lambda: "saved" in find(dialog, QLabel, "keyResult").text(), replaced)
        def replaced():
            print("AFTER", find(dialog, QLabel, "keyStatus").text())
            find(dialog, QPushButton, "removeKey").click()
            print("REMOVED", find(dialog, QLabel, "keyStatus").text(), repr(store["key"]))
            dialog.reject()
        QTimer.singleShot(100, start)
        dialog.exec()
    """,
    )
    assert "already stored" in out and "earlier eecc-redact" in out
    assert "FINISH_ENABLED True" in out  # keeping the existing key is a valid choice
    assert "AFTER A production key is stored in" in out and "earlier" not in out.split("AFTER")[1]
    assert "REMOVED No key stored yet. ''" in out


def test_a_recorded_hotkey_is_applied_and_saved_and_a_refused_one_is_not():
    out = child(
        PRELUDE,
        """
        store["key"] = "shr_live_ALREADY_STORED_111"
        applied = []
        def apply(text):
            applied.append(text)
            return "Ctrl+Alt+K is already taken" if text == "ctrl+alt+k" else None
        dialog = SettingsDialog(config, apply_hotkey=apply, client_factory=GoodClient)
        def start():
            edit = find(dialog, QKeySequenceEdit, "hotkeyEdit")
            edit.setKeySequence(QKeySequence("Ctrl+Alt+K"))
            find(dialog, QPushButton, "useHotkey").click()
            print("REFUSED", find(dialog, QLabel, "hotkeyResult").text(), repr(config.hotkey))
            edit.setKeySequence(QKeySequence("Alt+F9"))
            find(dialog, QPushButton, "useHotkey").click()
            print("USED", find(dialog, QLabel, "hotkeyResult").text(), config.hotkey)
            find(dialog, QPushButton, "finish").click()
        QTimer.singleShot(100, start)
        dialog.exec()
        print("APPLIED", applied)
    """,
    )
    assert "already taken" in out and "REFUSED" in out and "''" in out
    assert "USED Alt+F9 starts a capture. alt+f9" in out
    assert "APPLIED ['ctrl+alt+k', 'alt+f9']" in out


def test_the_key_from_the_environment_is_shown_but_left_alone():
    out = child(
        PRELUDE,
        """
        import os
        os.environ["SHINRAI_API_KEY"] = "shr_test_FROM_ENV"
        opened = []
        def open_settings():
            opened.append(1)
            return len(opened) < 2  # the first attempt succeeds, the second cannot
        dialog = SettingsDialog(config, hotkey_note="Ctrl+Shift+Print starts a capture.",
                                open_hotkey_settings=open_settings, client_factory=GoodClient)
        def start():
            print("STATUS", find(dialog, QLabel, "keyStatus").text())
            print("REMOVE", find(dialog, QPushButton, "removeKey").isEnabled())
            print("NOTE", find(dialog, QLabel, "hotkeyNote").text())
            print("EDIT", find(dialog, QKeySequenceEdit, "hotkeyEdit"))
            find(dialog, QPushButton, "openHotkeySettings").click()  # opens: no message
            print("FIRST", repr(find(dialog, QLabel, "hotkeyResult").text()))
            find(dialog, QPushButton, "openHotkeySettings").click()  # cannot: says so
            print("SECOND", find(dialog, QLabel, "hotkeyResult").text())
            find(dialog, QPushButton, "finish").click()
        QTimer.singleShot(100, start)
        dialog.exec()
        print("OPENED", len(opened))
    """,
    )
    assert "Using the sandbox key from the environment" in out
    assert "REMOVE False" in out
    assert "NOTE Ctrl+Shift+Print starts a capture." in out
    assert "EDIT None" in out  # nothing to record: the desktop owns the key
    assert "FIRST ''" in out and "Could not open them" in out.split("SECOND")[1]
    assert "OPENED 2" in out
