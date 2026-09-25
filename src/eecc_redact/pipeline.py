"""Capture, detect, review, release. The tray and `eecc-redact capture` run the same steps.

If detection does not succeed for any reason, the user sees the failure with
Retry and Discard. The original capture is never offered.
"""

from functools import partial

from eecc_redact import APP_NAME, keystore
from eecc_redact.config import Config
from eecc_redact.errors import AppError, Cancelled
from eecc_redact.imaging import to_png
from eecc_redact.shinrai import Shinrai

#: How long a one-shot capture keeps serving the clipboard after Copy.
CLIPBOARD_HOLD_SECONDS = 300


def client_for(config: Config) -> Shinrai:
    key = keystore.get_key()
    if not key:
        raise AppError(f"No ShinrAI key. Run `{APP_NAME} key set`.")
    return Shinrai(
        key,
        base_url=config.base_url,
        project=config.project,
        location=config.location,
        api=config.api,
    )


def capture_once(config: Config, *, hold_clipboard: bool = False) -> int:
    from eecc_redact.capture import acquire_region
    from eecc_redact.ui import run_blocking
    from eecc_redact.ui.review import show_failure, show_review

    while True:
        try:
            png = to_png(acquire_region())
            with client_for(config) as client:
                detection = run_blocking(partial(client.detect, png), "Checking with ShinrAI…")
        except Cancelled:
            return 0
        except AppError as exc:
            if show_failure(exc) == "retry":
                continue
            return 1
        outcome = show_review(png, detection, config)
        if outcome == "copied" and hold_clipboard:
            from eecc_redact.ui.clipboard import hold_until_taken

            hold_until_taken(CLIPBOARD_HOLD_SECONDS)
        return 0
