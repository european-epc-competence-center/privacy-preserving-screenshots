"""The packaging files agree with each other and with the app."""

from pathlib import Path

from eecc_redact import APP_NAME

PACKAGING = Path(__file__).parents[1] / "packaging"
SPEC = PACKAGING / f"{APP_NAME}.spec"
ISS = PACKAGING / "windows" / f"{APP_NAME}.iss"


def test_both_build_scripts_run_the_shared_spec_and_icon():
    windows = (PACKAGING / "windows" / "build.ps1").read_text()
    linux = (PACKAGING / "linux" / "build.sh").read_text()
    assert SPEC.is_file() and (PACKAGING / "icon.py").is_file()
    assert f"packaging\\{APP_NAME}.spec" in windows and "packaging\\icon.py" in windows
    assert f"packaging/{APP_NAME}.spec" in linux and "packaging/icon.py" in linux
    assert f"{APP_NAME}-setup-$Version.exe" in windows and f"{APP_NAME}.iss" in windows
    assert "AppRun" in linux and "appimagetool" in linux and ".sha256" in linux


def test_the_spec_bundles_what_the_frozen_app_reads_and_leaves_host_libraries_out():
    spec = SPEC.read_text()
    assert 'copy_metadata("keyring")' in spec  # or keyring silently stores nothing
    assert f'copy_metadata("{APP_NAME}")' in spec  # __version__
    assert 'collect_submodules("keyring.backends")' in spec
    assert 'collect_data_files("eecc_redact")' in spec  # the logos
    assert "console=False" in spec
    for library in ("libfontconfig", "libglib-2.0", "libxkbcommon", "libstdc++"):
        assert f'"{library}"' in spec, library  # the desktop's own, on Linux


def test_the_installer_shows_the_wizard_images():
    iss = ISS.read_text()
    for line in ("WizardImageFile=wizard-panel.png", "WizardSmallImageFile=wizard-corner.png"):
        assert line in iss
        assert (ISS.parent / line.partition("=")[2]).is_file()


def test_the_windows_uninstaller_runs_the_apps_own_uninstall_command():
    iss = ISS.read_text()
    assert "AppId={{" in iss
    assert "PrivilegesRequired=lowest" in iss  # per user, no admin prompt
    assert "Parameters := 'uninstall';" in iss
    assert "--forget-key" in iss
    assert "usUninstall" in iss  # before any file is removed
