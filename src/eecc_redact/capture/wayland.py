"""Wayland: the desktop's own region picker, through the Screenshot portal.

An application may not read the screen on Wayland. The portal's interactive
mode shows the desktop's picker (on GNOME, the Print Screen tool the user
already knows) and returns the region as a file: the one raw capture that
touches disk, read and deleted at once.
"""

from contextlib import suppress
from pathlib import Path
from urllib.parse import unquote, urlparse

from eecc_redact.errors import AppError, Cancelled
from eecc_redact.portal import Portal

SCREENSHOT = "org.freedesktop.portal.Screenshot"


def request_region(*, interactive: bool = True, timeout: float | None = 300) -> bytes:
    """Ask the desktop to let the user pick a region. Blocks until they have.

    `interactive=False` grabs the whole screen with no picker, so the path can
    be exercised without a person.
    """
    with Portal() as portal:
        results = portal.request(
            SCREENSHOT,
            "Screenshot",
            "sa{sv}",
            ("",),
            {"interactive": ("b", interactive)},
            timeout=timeout,
        )
    uri = str(results.get("uri", ""))
    if not uri:
        raise Cancelled()
    path = Path(unquote(urlparse(uri).path))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AppError(
            "Could not read the screenshot the desktop returned.", detail=str(exc)
        ) from exc
    finally:
        with suppress(OSError):
            path.unlink()
