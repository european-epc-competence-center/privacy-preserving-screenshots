"""A region of the screen, whatever the desktop allows.

Freeze and select (Windows, X11, macOS): grab every screen, select on the still
frame, crop. Or hand it to the desktop (Wayland): the Screenshot portal shows
the desktop's own picker and returns the region. Both end in one PNG.
"""

from eecc_redact import platforms


def backend() -> str:
    return "portal" if platforms.wayland() else "overlay"


def acquire_region() -> bytes:
    """A PNG of the region the user picked. Raises Cancelled or AppError."""
    if backend() == "portal":
        from eecc_redact.capture.wayland import request_region
        from eecc_redact.ui import run_blocking

        # Blocks until the user is done in the desktop's picker; keep Qt alive meanwhile.
        return run_blocking(request_region)
    from eecc_redact.capture.overlay import select_region

    return select_region()
