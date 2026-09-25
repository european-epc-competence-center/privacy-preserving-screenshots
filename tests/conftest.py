"""Isolation for every test: no real config, keychain or display.

Dialog tests run in child processes (see `child`) so that a regression that
hangs fails one test instead of freezing the suite.
"""

import io
import os
import subprocess
import sys
import textwrap

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    import eecc_redact.config
    import eecc_redact.desktop
    import eecc_redact.keystore

    monkeypatch.setattr(eecc_redact.config, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(eecc_redact.desktop, "install_launcher", lambda: None)  # not into ~
    for variable in ("APPIMAGE", "APPDIR"):  # never mistake the test run for an AppImage
        monkeypatch.delenv(variable, raising=False)
    store = {"key": ""}
    monkeypatch.setattr(eecc_redact.keystore, "get_key", lambda: store["key"])
    monkeypatch.setattr(eecc_redact.keystore, "set_key", lambda key: store.__setitem__("key", key))
    monkeypatch.setattr(eecc_redact.keystore, "delete_key", lambda: store.__setitem__("key", ""))
    return store


@pytest.fixture(scope="session")
def qapp():
    from eecc_redact.ui import ensure_app

    return ensure_app()


def png(size=(120, 40), colour="white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as image:
        return image.size


def source(*scripts: str) -> str:
    """Join separately indented script fragments into one program."""
    return "\n".join(textwrap.dedent(script) for script in scripts)


def child(*scripts: str, args: tuple[str, ...] = (), timeout: float = 60) -> str:
    """Run the scripts in a fresh offscreen Qt process; return stdout, or fail."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-c", source(*scripts), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-1500:]
    return result.stdout
