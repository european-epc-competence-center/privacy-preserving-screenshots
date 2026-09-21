"""Linux portals against a fake portal on the real session bus, so the exact
D-Bus messages - nested a(sa{sv}) included - are what eecc-redact sends to a desktop."""

import os
import threading
import uuid

import pytest

from eecc_redact import APP_ID, platforms, portal
from eecc_redact.capture import wayland
from eecc_redact.desktop import linux as desktop
from eecc_redact.hotkeys import linux as hotkeys
from eecc_redact.hotkeys import parse_hotkey

pytestmark = [
    pytest.mark.skipif(not platforms.LINUX, reason="Linux portals"),
    pytest.mark.skipif(
        not os.environ.get("DBUS_SESSION_BUS_ADDRESS"), reason="needs a D-Bus session bus"
    ),
]


class FakePortal:
    """Answers portal requests like a desktop that says yes, or as told."""

    def __init__(self, *, bind_code=0, trigger="Ctrl+Shift+Print", missing=(), uri=""):
        from jeepney import message_bus
        from jeepney.io.blocking import open_dbus_connection

        self.name = f"{APP_ID}.test.t{uuid.uuid4().hex[:8]}"
        self.bind_code, self.trigger, self.missing, self.uri = bind_code, trigger, set(missing), uri
        self.calls: list[tuple[str, tuple]] = []
        self.connection = open_dbus_connection(bus="SESSION")
        self.connection.send_and_get_reply(message_bus.RequestName(self.name))
        self.running = True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        from jeepney import HeaderFields, MessageType, new_error, new_method_return

        while self.running:
            try:
                message = self.connection.receive(timeout=0.1)
            except TimeoutError:
                continue
            if message.header.message_type != MessageType.method_call:
                continue
            member = message.header.fields[HeaderFields.member]
            self.calls.append((member, message.body))
            if member in self.missing:
                self.connection.send(
                    new_error(
                        message,
                        "org.freedesktop.DBus.Error.UnknownMethod",
                        "s",
                        (f"No such method {member}",),
                    )
                )
            elif member == "Register":
                self.connection.send(new_method_return(message))
            elif member == "Get":
                self.connection.send(new_method_return(message, "v", (("u", 5),)))
            else:
                token = message.body[-1]["handle_token"][1]
                sender = message.header.fields[HeaderFields.sender].lstrip(":").replace(".", "_")
                request = f"{portal.PATH}/request/{sender}/{token}"
                self.connection.send(new_method_return(message, "o", (request,)))
                self.answer(member, message.body, request)

    def answer(self, member, body, request):
        from jeepney import DBusAddress, new_signal

        def emit(path, interface, name, signature, args):
            self.connection.send(
                new_signal(DBusAddress(path, interface=interface), name, signature, args)
            )

        if member == "CreateSession":
            code, results = 0, {"session_handle": ("s", f"{portal.PATH}/session/1_1/eecc_redact")}
        elif member == "BindShortcuts":
            code, results = (
                self.bind_code,
                {
                    "shortcuts": (
                        "a(sa{sv})",
                        [
                            (
                                hotkeys.SHORTCUT_ID,
                                {
                                    "description": ("s", hotkeys.DESCRIPTION),
                                    "trigger_description": ("s", self.trigger),
                                },
                            )
                        ],
                    )
                },
            )
        elif member == "RequestBackground":
            code, results = 0, {"background": ("b", True), "autostart": body[-1]["autostart"]}
        elif member == "Screenshot":
            # GNOME answers 2 ("ended in some other way"), not 1, when its picker is closed.
            code, results = (0, {"uri": ("s", self.uri)}) if self.uri else (2, {})
        else:
            code, results = 2, {}
        emit(request, portal.REQUEST, "Response", "ua{sv}", (code, results))
        if member == "BindShortcuts" and code == 0:  # the user presses the key
            emit(
                portal.PATH,
                hotkeys.INTERFACE,
                "Activated",
                "osta{sv}",
                (body[0], hotkeys.SHORTCUT_ID, 0, {}),
            )

    def body_of(self, member):
        return next(body for name, body in self.calls if name == member)

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        self.connection.close()


@pytest.fixture
def fake(monkeypatch):
    fakes = []

    def make(**kwargs):
        instance = FakePortal(**kwargs)
        fakes.append(instance)
        monkeypatch.setattr(portal, "SERVICE", instance.name)
        monkeypatch.setattr(platforms, "flatpak", lambda: False)
        return instance

    yield make
    for instance in fakes:
        instance.close()


def bind(preferred="ctrl+shift+print"):
    pressed, changed = threading.Event(), threading.Event()
    hotkey = hotkeys.PortalHotkey(pressed.set, changed.set)
    hotkey.start(parse_hotkey(preferred))
    assert changed.wait(10), "the binding never finished"
    return hotkey, pressed


def test_the_hotkey_is_registered_suggested_bound_and_pressed(fake):
    desktop_ = fake(trigger="Ctrl+Alt+K")
    hotkey, pressed = bind("ctrl+alt+k")
    try:
        assert (hotkey.description, hotkey.problem) == ("Ctrl+Alt+K", "")
        assert pressed.wait(5)
        assert desktop_.body_of("Register") == (APP_ID, {})
        _session, shortcuts, _parent, _options = desktop_.body_of("BindShortcuts")
        assert shortcuts == [
            (
                hotkeys.SHORTCUT_ID,
                {
                    "description": ("s", hotkeys.DESCRIPTION),
                    "preferred_trigger": ("s", "CTRL+ALT+k"),
                },
            )
        ]
    finally:
        hotkey.stop()


def test_a_dismissed_dialog_and_a_missing_portal_are_explained(fake):
    for code in (1, 2):  # cancelled, or "ended in some other way"
        fake(bind_code=code)
        hotkey, pressed = bind()
        hotkey.stop()
        assert hotkey.description == "" and "not confirmed" in hotkey.problem
        assert not pressed.is_set()

    fake(missing={"CreateSession"})
    hotkey, _ = bind()
    hotkey.stop()
    assert "no global shortcuts portal" in hotkey.problem and "capture" in hotkey.problem


def test_login_start_asks_the_desktop_to_run_the_tray(fake, monkeypatch):
    desktop_ = fake()
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/home/u/.local/bin/eecc-redact")
    assert desktop.set_autostart(True) is True
    options = desktop_.body_of("RequestBackground")[-1]
    assert options["autostart"] == ("b", True)
    assert options["commandline"] == ("as", ["/home/u/.local/bin/eecc-redact"])


def test_the_screenshot_portal_returns_a_file_that_is_read_and_deleted(fake, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG fake")
    fake(uri=shot.as_uri())
    assert wayland.request_region(interactive=False, timeout=10) == b"\x89PNG fake"
    assert not shot.exists()


def test_a_dismissed_picker_is_a_cancel_not_a_failure(fake):
    from eecc_redact.errors import Cancelled

    fake()  # answers Screenshot with response code 2, as GNOME does on Esc
    with pytest.raises(Cancelled):
        wayland.request_region(timeout=10)


def test_portal_versions_are_readable(fake):
    fake()
    with portal.Portal() as connection:
        assert connection.version(wayland.SCREENSHOT) == 5


def test_a_key_changed_in_the_desktop_settings_is_picked_up():
    changes = []
    hotkey = hotkeys.PortalHotkey(lambda: None, lambda: changes.append(hotkey.description))
    hotkey._session = "/session/eecc-redact"
    shortcut = lambda key: [(hotkeys.SHORTCUT_ID, {"trigger_description": ("s", key)})]  # noqa: E731
    hotkey._changed(("/session/other", shortcut("F9")))
    hotkey._changed(("/session/eecc-redact", shortcut("Alt+F9")))
    assert changes == ["Alt+F9"]
    hotkey._changed(("/session/eecc-redact", []))
    assert hotkey.description == "" and "no key is assigned" in hotkey.problem


def test_only_our_own_session_starts_a_capture():
    presses = []
    hotkey = hotkeys.PortalHotkey(lambda: presses.append(1), lambda: None)
    hotkey._session = "/session/eecc-redact"
    hotkey._activated(("/session/other", hotkeys.SHORTCUT_ID, 0, {}))
    hotkey._activated(("/session/eecc-redact", "something-else", 0, {}))
    hotkey._activated(("/session/eecc-redact", hotkeys.SHORTCUT_ID, 0, {}))
    assert presses == [1]


@pytest.mark.parametrize(
    "desktop_name, fragment",
    [
        ("ubuntu:GNOME", "GNOME Settings > Apps > eecc-redact > Global Shortcuts"),
        ("KDE", "System Settings"),
        ("XFCE", "keyboard settings"),
    ],
)
def test_the_user_is_sent_to_their_own_desktops_settings(monkeypatch, desktop_name, fragment):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop_name)
    assert fragment in hotkeys.where_to_change()


def test_the_desktops_shortcut_settings_open_where_known(monkeypatch):
    launched = []
    monkeypatch.setattr(hotkeys.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw)))
    monkeypatch.setattr(hotkeys.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    assert hotkeys.open_desktop_settings()
    assert launched[-1][0] == ["gnome-control-center", "applications", f"{APP_ID}.desktop"]
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert hotkeys.open_desktop_settings()
    assert launched[-1][0][0] == "systemsettings"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "XFCE")
    assert not hotkeys.open_desktop_settings()
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setattr(hotkeys.shutil, "which", lambda name: None)
    assert not hotkeys.open_desktop_settings() and len(launched) == 2


def test_a_frozen_build_does_not_pass_its_library_path_to_the_desktop(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_x/usr/lib/eecc-redact/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert "LD_LIBRARY_PATH" in hotkeys.host_environment()  # from source: untouched
    monkeypatch.setattr(hotkeys.sys, "frozen", True, raising=False)
    assert "LD_LIBRARY_PATH" not in hotkeys.host_environment()
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")
    env = hotkeys.host_environment()
    assert env["LD_LIBRARY_PATH"] == "/opt/lib" and "LD_LIBRARY_PATH_ORIG" not in env
