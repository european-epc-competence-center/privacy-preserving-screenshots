"""Desktop integration that can be tested without the desktop: the Windows Run
key against a fake registry, and the Linux launcher entry in a temp dir."""

import subprocess

import pytest

from eecc_redact import APP_ID, APP_NAME, MODULE
from eecc_redact.desktop import linux, windows


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1
    KEY_SET_VALUE = 2
    KEY_READ = 3

    def __init__(self):
        self.values = {}

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def CreateKey(self, root, path):  # noqa: N802 - winreg names
        return self._Key(path)

    def OpenKey(self, root, path, reserved, access):  # noqa: N802
        return self._Key(path)

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[(key.path, name)] = value

    def QueryValueEx(self, key, name):  # noqa: N802
        if (key.path, name) not in self.values:
            raise FileNotFoundError(name)
        return self.values[(key.path, name)], 1

    def DeleteValue(self, key, name):  # noqa: N802
        if (key.path, name) not in self.values:
            raise FileNotFoundError(name)
        del self.values[(key.path, name)]


def test_windows_background_command_prefers_the_windowless_interpreter(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    assert windows.background_command(str(tmp_path / "python.exe"), frozen=False) == [
        str(tmp_path / "pythonw.exe"),
        "-m",
        MODULE,
    ]
    exe = r"C:\x\eecc-redact.exe"
    assert windows.background_command(exe, frozen=True) == [exe]


def test_windows_autostart_writes_one_quoted_command_and_removes_it(monkeypatch):
    registry = FakeRegistry()
    command = [r"C:\Users\Some One\.venv\Scripts\pythonw.exe", "-m", MODULE]
    monkeypatch.setattr(windows, "background_command", lambda: command)
    windows.set_autostart(True, registry)
    assert registry.values[(windows.RUN_KEY, APP_NAME)] == subprocess.list2cmdline(command)
    assert windows.autostart_enabled(registry) is True
    windows.set_autostart(False, registry)
    assert windows.autostart_enabled(registry) is False
    windows.set_autostart(False, registry)  # already gone is not an error


def test_linux_launcher_is_the_appimage_file_or_the_installed_command(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(linux.shutil, "which", lambda name: f"/home/u/.local/bin/{name}")
    assert linux.launcher() == [f"/home/u/.local/bin/{APP_NAME}"]
    monkeypatch.setattr(linux.shutil, "which", lambda name: None)
    assert linux.launcher()[1:] == ["-m", MODULE]
    # The AppImage is mounted somewhere new on every start; the file is what to run.
    monkeypatch.setenv("APPIMAGE", "/home/u/Apps/eecc-redact.AppImage")
    assert linux.launcher() == ["/home/u/Apps/eecc-redact.AppImage"]


@pytest.fixture
def launcher_files(tmp_path, monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.setattr(linux.shutil, "which", lambda name: "/opt/some dir/eecc-redact")
    entry = tmp_path / "applications" / f"{APP_ID}.desktop"
    icon = tmp_path / "icons" / f"{APP_ID}.png"
    monkeypatch.setattr(linux, "desktop_entry_path", lambda: entry)
    monkeypatch.setattr(linux, "icon_path", lambda: icon)
    return entry, icon


def test_linux_desktop_entry_names_the_app_id_and_the_launcher(launcher_files):
    entry, icon = launcher_files
    assert linux.install_desktop_entry() == entry
    text = entry.read_text()
    assert "[Desktop Entry]" in text and f"Name={APP_NAME}" in text
    assert 'Exec="/opt/some dir/eecc-redact"' in text and f"Icon={APP_ID}" in text
    assert not icon.exists()  # nothing to offer outside an AppImage


def test_an_appimage_installs_its_icon_beside_the_entry(tmp_path, monkeypatch, launcher_files):
    entry, icon = launcher_files
    appdir = tmp_path / "AppDir"
    appdir.mkdir()
    (appdir / f"{APP_ID}.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setenv("APPDIR", str(appdir))
    monkeypatch.setenv("APPIMAGE", "/home/u/Apps/eecc-redact.AppImage")
    linux.install_desktop_entry()
    assert "Exec=/home/u/Apps/eecc-redact.AppImage" in entry.read_text()
    assert icon.read_bytes() == b"\x89PNG fake"
