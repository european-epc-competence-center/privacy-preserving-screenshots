# UI surfaces and branding

## App icon

- `icon()` in `src/eecc_redact/ui/__init__.py` draws the icon in code (a redaction bar, `#15181D`).
- The same drawing is the tray icon, every window icon, and, through `packaging/icon.py`, the Windows `.ico` (exe, installer, Apps entry) and the AppImage PNG. Changing the icon means changing `icon()` only.

## Surfaces

- `ui/tray.py`: tray icon, menu (Capture region, Settings…, Quit), tooltip, balloon messages.
- `ui/settings.py`: one dialog for first-run setup and later settings. Header: EECC logo (plus the intro text on first run). Footer label `credits`: version and slogan.
- `ui/review.py`: the gate before copy/save. `export()` is the only image that leaves the app; nothing may be drawn onto it.
- `packaging/windows/eecc-redact.iss`: Inno Setup (6.5.2+ required for PNG wizard images), modern wizard. Welcome, directory, group and ready pages are off: the user sees License, Installing, Finished.

## Branding

- Logos in `src/eecc_redact/ui/assets/`, loaded with `importlib.resources`; shipped by `uv_build` automatically and by `collect_data_files("eecc_redact")` in the PyInstaller spec (AppImage included).
  - `eecc-logo-on-dark.png`: 600×526, white wordmark.
  - `eecc-logo-on-light.png`: 213×182, dark wordmark, transparent (the supplied file had a white background). Low resolution: soft above 200 % scaling. Ask for an SVG or larger file if needed.
- `ui/settings.py`: `_logo()` picks the variant from the palette's window lightness once, when the dialog is built; `SLOGAN` is rich text with a red text heart (`♥`, not the emoji) and an `EECC` link to https://eecc.info.
- Installer: `packaging/windows/wizard-panel.png` (dark `#15181D` panel, logo, slogan) and `wizard-corner.png` (transparent logo; Inno Setup wants it square), static at the 175 % area size of Inno Setup 6.6+ (403×772 and 116×116; 200 % would be 430×824 and 124×124, see the [WizardImageFile docs](https://jrsoftware.org/ishelp/topic_setup_wizardimagefile.htm)). Inno Setup scales them for other DPI settings. Regenerate by hand if the logo or slogan changes.

## Offscreen rendering on Windows

- Qt's `offscreen` platform finds no fonts on Windows (text renders as boxes). Set `QT_QPA_FONTDIR=%WINDIR%\Fonts` before the app starts.
- Screenshots of dialogs: `dialog.grab().save(...)` under `QT_QPA_PLATFORM=offscreen`; `QT_SCALE_FACTOR=2` for HiDPI.
