# PyInstaller spec, shared by the Windows installer and the Linux AppImage.
# Run by packaging/windows/build.ps1 and packaging/linux/build.sh.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - PyInstaller global
LINUX = sys.platform.startswith("linux")

# On Linux these come from the user's desktop, as they do for the PySide6 wheel
# itself. Bundling the build machine's copies breaks fonts, keymaps, GLib
# modules and the C++ runtime on other distributions.
HOST_LIBRARIES = (
    "libfontconfig", "libfreetype", "libglib-2.0", "libgthread-2.0", "libgio-2.0",
    "libgobject-2.0", "libgmodule-2.0", "libdbus-1", "libX11", "libXau", "libXdmcp",
    "libxkbcommon", "libstdc++", "libgcc_s", "libatomic", "libsystemd", "libz.so",
)  # fmt: skip

analysis = Analysis(  # noqa: F821
    # A frozen app cannot run `-m eecc_redact`; __main__.py is a plain script.
    [os.path.join(ROOT, "src", "eecc_redact", "__main__.py")],
    pathex=[os.path.join(ROOT, "src")],
    # keyring finds its backends through package metadata; without it the frozen
    # app silently falls back to a keyring that stores nothing. The app reads
    # its own version from its metadata too.
    datas=copy_metadata("keyring") + copy_metadata("eecc-redact"),
    hiddenimports=collect_submodules("keyring.backends"),
    excludes=["pytest", "tkinter"],
)
if LINUX:
    analysis.binaries = [
        entry for entry in analysis.binaries if not entry[0].startswith(HOST_LIBRARIES)
    ]
pyz = PYZ(analysis.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="eecc-redact",
    console=False,
    icon=None if LINUX else os.path.join(ROOT, "build", "eecc-redact.ico"),
)
COLLECT(exe, analysis.binaries, analysis.datas, name="eecc-redact")  # noqa: F821
