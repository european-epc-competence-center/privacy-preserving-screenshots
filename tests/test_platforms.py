"""The rules for platform-specific code, from eecc_redact/platforms.py, enforced."""

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import eecc_redact
from eecc_redact import platforms

SRC = Path(eecc_redact.__file__).parent
MODULES = sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_only_platforms_py_reads_sys_platform(path):
    if path.name == "platforms.py":
        return
    tree = ast.parse(path.read_text("utf-8"))
    reads = any(
        isinstance(node, ast.Attribute)
        and node.attr == "platform"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        for node in ast.walk(tree)
    )
    assert not reads, f"{path.name}: ask eecc_redact.platforms instead of reading sys.platform"


def test_every_module_imports_on_this_os():
    # OS-only modules must defer their OS-only imports, or eecc-redact breaks elsewhere.
    for info in pkgutil.walk_packages(eecc_redact.__path__, f"{eecc_redact.MODULE}."):
        if info.name != f"{eecc_redact.MODULE}.__main__":
            importlib.import_module(info.name)


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"}, True),
        ({"WAYLAND_DISPLAY": "wayland-0"}, True),
        ({"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "x11"}, False),
        ({"XDG_SESSION_TYPE": "wayland"}, True),
        ({}, False),
    ],
)
def test_wayland_detection(monkeypatch, env, expected):
    for variable in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in env.items():
        monkeypatch.setenv(variable, value)
    assert platforms.wayland() is expected


def test_a_flatpak_is_recognised_by_its_sandbox_info(monkeypatch):
    monkeypatch.setattr(platforms, "LINUX", True)
    monkeypatch.setattr(platforms.os.path, "exists", lambda path: path == "/.flatpak-info")
    assert platforms.flatpak() is True
    monkeypatch.setattr(platforms, "LINUX", False)
    assert platforms.flatpak() is False
