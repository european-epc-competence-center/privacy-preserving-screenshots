"""eecc-redact's command line.

eecc-redact                   open eecc-redact: setup on first run, then the tray
eecc-redact capture           one capture; handed to the running tray if there is one
eecc-redact redact FILE       headless: file in, redacted file out
eecc-redact doctor            what this deployment serves; read-only, spends nothing
eecc-redact key [set|delete]  show, store or remove the ShinrAI key
eecc-redact uninstall         undo everything above for this account, and remove the program
"""

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from eecc_redact import APP_NAME, __version__, keystore, platforms
from eecc_redact.config import Config
from eecc_redact.errors import AppError
from eecc_redact.keystore import ENV_VAR, environment, key_source, looks_like_key, scrub


def load_dotenv() -> None:
    """Development only: a .env in the working tree or the checkout. Frozen builds ignore it."""
    if getattr(sys, "frozen", False):
        return
    from dotenv import find_dotenv
    from dotenv import load_dotenv as load

    path = Path(find_dotenv(usecwd=True) or Path(__file__).resolve().parents[2] / ".env")
    if path.is_file():
        load(path, override=False)  # a real environment variable still wins


def cmd_doctor(_args: argparse.Namespace, config: Config) -> int:
    key, source = key_source()
    if not key:
        print(f"No key. Run `{APP_NAME} key set` or set {ENV_VAR}.", file=sys.stderr)
        return 2
    from eecc_redact.capture import backend
    from eecc_redact.pipeline import client_for

    with client_for(config) as client:
        caps = client.capabilities()
    rows = [
        ("deployment", config.base_url),
        ("key", f"{environment(key)}, from the {source}"),
        ("plan", caps.plan or "unknown"),
        ("records", f"{caps.records:,} left" if caps.records is not None else "unknown"),
        ("models", (", ".join(caps.models) or "none reported") + "  (usable with this key)"),
        (
            "PII API v2",
            ("served, images " + ("served" if caps.image_v2 else "not served"))
            if caps.pii_api_v2
            else "not served",
        ),
        ("OCR languages", ", ".join(caps.ocr_languages) or "not reported"),
        ("image:redact", "served" if caps.image_redact else "MISSING"),
        ("info types", f"{len(caps.info_types)} kinds of personal data detected"),
        (
            "route",
            {
                "v2": "PII API v2 (POST /v2/detect)",
                "google": "Google-compatible image:redact",
                "none": "NONE",
            }[caps.route if config.api == "auto" else ("v2" if config.api == "v2" else "google")]
            + f"  (api = {config.api})",
        ),
    ]
    if backend() == "portal":
        rows += [("capture", "the desktop's own picker (Wayland)"), ("portals", _portal_report())]
    else:
        rows.append(("capture", "frozen screen with a selection overlay"))
    for name, value in rows:
        print(f"{name:<14}{value}")
    if not caps.image_redact and not caps.image_v2:
        print(
            f"\nThis deployment serves neither the PII API v2 with images nor image:redact; "
            f"{APP_NAME} cannot redact against it.",
            file=sys.stderr,
        )
        return 1
    return 0


def _portal_report() -> str:
    from eecc_redact.capture.wayland import SCREENSHOT
    from eecc_redact.hotkeys.linux import INTERFACE as SHORTCUTS
    from eecc_redact.portal import Portal

    try:
        with Portal() as portal:
            versions = {
                "screenshot": portal.version(SCREENSHOT),
                "global shortcuts": portal.version(SHORTCUTS),
            }
    except AppError as exc:
        return f"unavailable ({exc.message})"
    return ", ".join(f"{name} {'v' + str(v) if v else 'missing'}" for name, v in versions.items())


def cmd_redact(args: argparse.Namespace, config: Config) -> int:
    """Headless path: no capture, no Qt. For testing and scripting."""
    from eecc_redact.imaging import burn, to_png
    from eecc_redact.pipeline import client_for

    png = to_png(args.image.read_bytes())
    with client_for(config) as client:
        detection = client.detect(png)
    target = args.out or args.image.with_name(f"{args.image.stem}_redacted.png")
    target.write_bytes(burn(png, detection.boxes, padding=config.box_padding))
    print(f"{len(detection.findings)} findings, {len(detection.boxes)} boxes -> {target}")
    for finding in detection.findings:
        print(f"  {finding.info_type:<20}{len(finding.boxes)} box(es)")
    if detection.records_remaining is not None:
        print(f"  records left: {detection.records_remaining:,}")
    return 0


def cmd_capture(_args: argparse.Namespace, config: Config) -> int:
    from eecc_redact import desktop, instance
    from eecc_redact.pipeline import capture_once
    from eecc_redact.ui import ensure_app

    desktop.install_launcher()
    ensure_app()
    if instance.send("capture"):
        return 0  # the tray captures, and keeps the clipboard afterwards
    return capture_once(config, hold_clipboard=True)


def cmd_tray(_args: argparse.Namespace, config: Config) -> int:
    from eecc_redact import desktop
    from eecc_redact.ui.tray import run_tray

    desktop.install_launcher()
    return run_tray(config)


def cmd_key(args: argparse.Namespace, _config: Config) -> int:
    if args.action == "set":
        value = getpass.getpass("ShinrAI API key (not echoed): ").strip()
        if not value:
            print("Nothing entered.", file=sys.stderr)
            return 2
        keystore.set_key(value)
        print(f"Stored in the OS keychain ({environment(value)} key).")
        return 0
    key, source = key_source()
    if args.action == "delete":
        if source == "environment":
            print(f"The key comes from {ENV_VAR}; unset it there.", file=sys.stderr)
            return 1
        keystore.delete_key()
        print("Key removed from the OS keychain." if key else "No key was stored.")
        return 0
    print(f"{environment(key)} key, from the {source}" if key else "No key stored.")
    return 0


def _uv_tool() -> str | None:
    """The uv executable, if this program runs from a `uv tool install` environment."""
    uv = None if getattr(sys, "frozen", False) else shutil.which("uv")
    if not uv:
        return None
    try:
        tools = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    installed = Path(sys.prefix).resolve().is_relative_to(Path(tools.stdout.strip()).resolve())
    return uv if installed else None


def cmd_uninstall(args: argparse.Namespace, _config: Config) -> int:
    """Everything the app did to this account, then the program itself where we can.

    The Windows uninstaller runs this too, without a console: `--forget-key` carries
    its answer about the key, and nothing here may wait for input.
    """
    from PySide6.QtCore import QCoreApplication

    from eecc_redact import desktop, instance
    from eecc_redact.config import config_path

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841 - for the socket
    if instance.request_quit():
        print("Stopped the running tray.")
    try:
        desktop.remove_integration()
        print("Turned off starting at login.")
    except AppError as exc:
        print(f"Could not turn off starting at login: {exc.message}", file=sys.stderr)
    folder = config_path().parent
    if folder.name == APP_NAME and folder.is_dir():  # only ever the app's own folder
        shutil.rmtree(folder, ignore_errors=True)
        print("Removed the settings.")
    key, source = key_source()
    if key and source == "keychain":
        forget = args.forget_key or (
            sys.stdin is not None
            and sys.stdin.isatty()
            and input("Remove the ShinrAI key from the OS keychain? [y/N] ").strip().lower() == "y"
        )
        if forget:
            keystore.delete_key()
            print("Removed the key from the OS keychain.")
        else:
            print(f"Kept the key in the OS keychain; `{APP_NAME} key delete` removes it.")
    if appimage := os.environ.get("APPIMAGE"):
        try:
            Path(appimage).unlink()  # the mounted image stays alive until we exit
            print(f"Removed {appimage}.")
        except OSError as exc:
            print(f"Delete the program file yourself: {appimage} ({exc.strerror}).")
    elif (uv := _uv_tool()) is None:
        print("The program itself was not installed with `uv tool`, so it is left in place.")
    elif platforms.WINDOWS:  # a running program cannot be deleted on Windows
        print(f"Now remove the program: uv tool uninstall {APP_NAME}")
    else:
        # Fine while running: Linux and macOS keep open files alive until we exit.
        sys.stdout.flush()  # our lines before uv's, even through a pipe
        subprocess.run([uv, "tool", "uninstall", APP_NAME], check=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.add_parser("capture", help="capture a region, redact it, review it")
    redact = commands.add_parser("redact", help="redact an image file, no capture")
    redact.add_argument("image", type=Path)
    redact.add_argument("-o", "--out", type=Path)
    commands.add_parser("doctor", help="what this deployment serves (read-only)")
    key = commands.add_parser("key", help="show, store or remove the ShinrAI key")
    key.add_argument(
        "action",
        nargs="?",
        choices=["set", "delete"],
        help="`set` prompts for the key; never pass it as an argument",
    )
    uninstall = commands.add_parser(
        "uninstall", help="stop the tray, undo login start, remove settings, then the program"
    )
    uninstall.add_argument(
        "--forget-key", action="store_true", help="also remove the key without asking"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Before argparse, which would echo an unknown argument - the key - to the terminal.
    if any(looks_like_key(arg) for arg in raw):
        print(
            "That looks like a ShinrAI key. Don't pass it as an argument: it lands in your "
            f"shell history and on screen. Run `{APP_NAME} key set` and paste it at the "
            "prompt instead.",
            file=sys.stderr,
        )
        return 2
    args = build_parser().parse_args(raw)
    load_dotenv()
    config = Config.load()
    handlers = {
        None: cmd_tray,
        "capture": cmd_capture,
        "redact": cmd_redact,
        "doctor": cmd_doctor,
        "key": cmd_key,
        "uninstall": cmd_uninstall,
    }
    try:
        return handlers[args.command](args, config)
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        if exc.detail:
            print(f"  {scrub(exc.detail)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)  # end the prompt line Ctrl-C interrupted
        return 130
