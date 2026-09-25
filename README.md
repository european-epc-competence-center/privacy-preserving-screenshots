# eecc-redact

Screenshots with the personal data already covered. Press a hotkey, pick a
region, and get it back with names, email addresses, phone numbers, IBANs and
addresses blacked out. Runs in the tray on Windows and Linux.

```
hotkey → region → RGB PNG → ShinrAI detection → review → boxes burned locally → copy / save
```

## ShinrAI

Detection is done by [ShinrAI](https://shinrai.innovius.io), a BERT-based
service that finds personal data in text and images
([API documentation](https://shinrai.innovius.io/docs?lang=en)). eecc-redact
sends the captured region, as an RGB PNG with all metadata stripped, to the
[ShinrAI PII API v2](https://shinrai.innovius.io/public-docs/pii-api-v2.md)
(`POST /v2/detect` with an image input) and gets back every finding the model
offers with its boxes in the pixels of the capture. The boxes are drawn
locally, on the original frame. A deployment that does not serve images on
the v2 API yet is used through its Google-compatible `image:redact` endpoint;
`api = "v2"` or `api = "google"` in the settings file forces one route, the
default `auto` decides from `GET /v2/capabilities` once per run.

## Develop

Requires [uv](https://docs.astral.sh/uv/); it fetches Python 3.13 itself.

```sh
uv sync                               # environment and dependencies
uv run eecc-redact key set            # once: a sandbox key (shr_test_…) into the OS keychain
uv run pytest                         # the suite; dialogs run headless in child processes
uv run ruff check . && uv run ruff format --check .
```

`uv run` runs the program from the checkout without installing it. Every
command in the table below works this way, prefixed with `uv run`:

```sh
uv run eecc-redact                    # setup window on first run, then the tray; Ctrl-C quits
uv run eecc-redact capture            # one capture, review, copy or save
uv run eecc-redact redact shot.png    # headless: writes shot_redacted.png
uv run eecc-redact doctor             # what your key and deployment allow; spends nothing
uv run eecc-redact key                # which key is in use, and where it comes from
uv run eecc-redact key set            # store a key in the OS keychain (prompted)
uv run eecc-redact key delete         # remove it from the keychain
uv run eecc-redact uninstall          # undo what the app did to this account
```

The key lives in the OS keychain only; the checkout and an installed
`eecc-redact` share the same entry.

## Commands

To have `eecc-redact` as a command in any terminal, install the working tree
for your user:

```sh
uv tool install .                     # later: uv tool install --reinstall .
```

From then on every command below runs as written, without `uv run`, and uses
the same settings and keychain entry as the checkout. `eecc-redact uninstall`
removes all of it again, the program included.

| Command | What it does |
|---|---|
| `eecc-redact` | Setup on first run, then the tray. If a tray already runs, opens its settings. Ctrl-C quits; `eecc-redact &` keeps the terminal free. |
| `eecc-redact capture` | One capture. Handed to the running tray if there is one; the command to bind to a key on desktops without a shortcuts portal. |
| `eecc-redact redact FILE [-o OUT]` | Headless: file in, redacted file out. No Qt. |
| `eecc-redact doctor` | Checks the key against ShinrAI (plan, records left, models, whether `image:redact` is served) and the desktop (capture method, portals). Read-only, spends nothing. |
| `eecc-redact key [set\|delete]` | Show, store (prompted, never as an argument) or remove the key. |
| `eecc-redact uninstall [--forget-key]` | Stop the tray, turn off starting at login, remove the settings and the launcher entry, ask about the key, then remove the program if it was installed with `uv tool`. |

## Layout

```
src/eecc_redact/
├── cli.py          the commands above
├── pipeline.py     capture → detect → review → release, shared by tray and CLI
├── shinrai.py      API client: image:redact and the read-only capability check
├── imaging.py      PNG normalization and drawing the boxes
├── models.py       Box, Finding, Detection
├── config.py       settings file (never the key)
├── keystore.py     the key in the OS keychain; scrubbing
├── errors.py       AppError, Cancelled
├── platforms.py    the one place that asks which OS this is
├── instance.py     one tray per user, and messages to it
├── portal.py       Linux: xdg-desktop-portal over jeepney
├── capture/        overlay.py (freeze and select), wayland.py (Screenshot portal)
├── hotkeys/        parsing; windows.py (RegisterHotKey), linux.py (GlobalShortcuts portal)
├── desktop/        start at login and launcher entry; windows.py, linux.py
└── ui/             Qt: tray, settings, review gate, clipboard, thread crossing
tests/              unit tests, headless dialog tests, a fake portal on the session bus
```

Code that only runs on one OS lives in a module named after it (`windows.py`,
`linux.py`, `wayland.py`) and defers its OS-only imports. Everything else asks
`eecc_redact.platforms`, never `sys.platform`. `tests/test_platforms.py`
enforces this and imports every module on every OS.

| | Windows | Linux (Wayland) | Linux (X11) |
|---|---|---|---|
| Region capture | Freeze the screen, select on an overlay | The desktop's own picker (Screenshot portal) | Freeze and select |
| Global hotkey | Recorded in Settings; registered by the tray | Bound by the desktop (GlobalShortcuts portal): confirmed once in its dialog, changed in the desktop's settings, which the app's Settings window opens | GlobalShortcuts portal |
| Start at login | HKCU Run key | Background portal | Background portal |
| Clipboard after Copy | Flushed to Windows | Held by the tray, or by a one-shot capture until pasted | Held |
| Key storage | Credential Manager | Secret Service | Secret Service |

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
