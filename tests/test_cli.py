import io
import subprocess
import tomllib
from pathlib import Path

import pytest
from PIL import Image

import eecc_redact.pipeline
from conftest import png
from eecc_redact import APP_NAME, __version__
from eecc_redact.cli import main
from eecc_redact.models import Box, Detection, Finding


class FakeClient:
    def __init__(self, detection):
        self.detection = detection

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def detect(self, png):
        return self.detection


def test_a_key_on_the_command_line_is_refused_without_echoing_it(capsys):
    secret = "shr_live_NOT_A_REAL_KEY_987654"
    assert main(["key", "set", secret]) == 2
    out = capsys.readouterr()
    assert secret not in out.out + out.err
    assert f"{APP_NAME} key set" in out.err


def test_version_and_key_status(capsys, isolated):
    assert main(["key"]) == 0
    assert "No key stored" in capsys.readouterr().out
    isolated["key"] = "shr_test_abc"
    assert main(["key"]) == 0
    assert "sandbox key in the OS keychain" in capsys.readouterr().out
    assert main(["key", "delete"]) == 0
    assert isolated["key"] == ""


def test_doctor_without_a_key_says_what_to_do(capsys):
    assert main(["doctor"]) == 2
    assert f"{APP_NAME} key set" in capsys.readouterr().err


def test_redact_burns_the_reported_boxes_into_a_new_file(tmp_path, monkeypatch, capsys, isolated):
    source = tmp_path / "shot.png"
    source.write_bytes(png((100, 50)))
    detection = Detection(
        findings=(Finding("EMAIL_ADDRESS", (Box(10, 10, 30, 10),)),), records_remaining=41
    )
    isolated["key"] = "shr_test_x"
    monkeypatch.setattr(eecc_redact.pipeline, "Shinrai", lambda *a, **k: FakeClient(detection))
    assert main(["redact", str(source)]) == 0
    out = capsys.readouterr().out
    assert "1 findings, 1 boxes" in out and "records left: 41" in out
    with Image.open(io.BytesIO((tmp_path / "shot_redacted.png").read_bytes())) as image:
        assert image.getpixel((20, 15)) == (0, 0, 0)
        assert image.getpixel((8, 8)) == (0, 0, 0)  # 2 px default padding
        assert image.getpixel((7, 7)) == (255, 255, 255)


def test_redact_without_a_key_fails_closed(tmp_path, capsys):
    source = tmp_path / "shot.png"
    source.write_bytes(png())
    assert main(["redact", str(source)]) == 1
    assert "No ShinrAI key" in capsys.readouterr().err
    assert not (tmp_path / "shot_redacted.png").exists()


def test_capture_without_a_tray_runs_the_pipeline_in_a_gui_application(monkeypatch, qapp):
    import uuid

    import eecc_redact.instance
    import eecc_redact.pipeline

    seen = {}

    def fake_capture_once(config, *, hold_clipboard):
        from PySide6.QtWidgets import QApplication

        seen["app"] = type(QApplication.instance()).__name__  # widgets need a QApplication
        seen["hold"] = hold_clipboard
        return 0

    monkeypatch.setattr(eecc_redact.pipeline, "capture_once", fake_capture_once)
    monkeypatch.setattr(eecc_redact.instance, "server_name", lambda: f"t-{uuid.uuid4().hex[:8]}")
    assert main(["capture"]) == 0
    assert seen == {"app": "QApplication", "hold": True}


@pytest.fixture
def uninstall_env(tmp_path, monkeypatch):
    """The uninstall command with everything that touches the system replaced."""
    import eecc_redact.cli
    import eecc_redact.config
    import eecc_redact.desktop
    import eecc_redact.instance

    done = []
    monkeypatch.setattr(eecc_redact.config, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(eecc_redact.instance, "request_quit", lambda: done.append("quit") or True)
    monkeypatch.setattr(eecc_redact.desktop, "remove_integration", lambda: done.append("desktop"))
    monkeypatch.setattr(eecc_redact.cli, "_uv_tool", lambda: None)
    monkeypatch.setattr(
        eecc_redact.cli.subprocess, "run", lambda cmd, **kw: done.append(tuple(cmd))
    )
    return done


@pytest.mark.parametrize("forget", [True, False])
def test_uninstall_undoes_the_apps_traces_and_leaves_the_key_unless_told(
    tmp_path, monkeypatch, capsys, isolated, uninstall_env, forget
):
    import eecc_redact.config

    folder = tmp_path / APP_NAME
    folder.mkdir()
    (folder / "config.toml").write_text("hotkey = 'alt+f9'\n")
    monkeypatch.setattr(eecc_redact.config, "config_path", lambda: folder / "config.toml")
    isolated["key"] = "shr_live_stored"

    assert main(["uninstall", "--forget-key"] if forget else ["uninstall"]) == 0
    out = capsys.readouterr().out
    assert uninstall_env == ["quit", "desktop"]
    assert not folder.exists()
    assert isolated["key"] == ("" if forget else "shr_live_stored")
    assert ("Removed the key" if forget else "Kept the key") in out
    assert "left in place" in out  # not a uv tool install: nothing to remove


def test_uninstall_removes_a_uv_tool_install_too(monkeypatch, capsys, uninstall_env):
    import eecc_redact.cli

    monkeypatch.setattr(eecc_redact.cli, "_uv_tool", lambda: "/usr/bin/uv")
    monkeypatch.setattr(eecc_redact.cli.platforms, "WINDOWS", False)
    assert main(["uninstall"]) == 0
    assert uninstall_env[-1] == ("/usr/bin/uv", "tool", "uninstall", APP_NAME)

    monkeypatch.setattr(eecc_redact.cli.platforms, "WINDOWS", True)
    assert main(["uninstall"]) == 0
    assert uninstall_env[-1] != ("/usr/bin/uv", "tool", "uninstall", APP_NAME)
    assert f"uv tool uninstall {APP_NAME}" in capsys.readouterr().out


def test_uninstall_removes_the_appimage_file_itself(tmp_path, monkeypatch, capsys, uninstall_env):
    image = tmp_path / "eecc-redact.AppImage"
    image.write_bytes(b"AI\x02")
    monkeypatch.setenv("APPIMAGE", str(image))
    assert main(["uninstall"]) == 0
    assert not image.exists()
    assert f"Removed {image}" in capsys.readouterr().out
    assert not any(
        cmd[1:] == ("tool", "uninstall", APP_NAME)
        for cmd in uninstall_env
        if isinstance(cmd, tuple)
    )


def test_uninstall_never_deletes_a_folder_that_is_not_the_apps(tmp_path, capsys, uninstall_env):
    (tmp_path / "config.toml").write_text("")  # config_path's parent is tmp_path, not ours
    assert main(["uninstall"]) == 0
    assert tmp_path.is_dir() and "Removed the settings" not in capsys.readouterr().out


def test_uv_tool_detection_looks_at_the_interpreter_prefix(monkeypatch, tmp_path):
    import eecc_redact.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{tmp_path}/tools\n"),
    )
    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path / "tools" / APP_NAME))
    assert cli._uv_tool() == "/usr/bin/uv"
    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path / "checkout" / ".venv"))
    assert cli._uv_tool() is None
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli._uv_tool() is None


def test_the_version_is_the_package_version():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    version = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert __version__ == version
