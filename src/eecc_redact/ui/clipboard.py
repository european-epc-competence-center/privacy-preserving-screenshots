"""Making a copy outlive the process that made it.

On X11 and Wayland the copying process serves the clipboard, so a one-shot
`eecc-redact capture` that exits at once leaves nothing to paste: it stays alive until
another application takes the clipboard, or the hold expires. On Windows, Qt
places the data lazily; OleFlushClipboard hands it to the system and the
process may exit. The tray is long-lived, so this only matters for one-shots.
"""

from contextlib import suppress

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from eecc_redact import platforms

POLL_MS = 500


def hold_until_taken(seconds: int) -> None:
    app = QApplication.instance()
    clipboard = QGuiApplication.clipboard()
    if app is None or seconds <= 0 or not clipboard.ownsClipboard():
        return
    if platforms.WINDOWS:
        from eecc_redact.ui.windows import flush_clipboard

        if flush_clipboard():
            return  # Windows owns the data now
    # Flushed: under Git Bash stdout is a pipe, and this line would otherwise
    # appear only after the wait it announces.
    print(
        f"The redacted image is on the clipboard. Holding it for up to {seconds}s so you "
        "can paste it (Ctrl-C to stop).",
        flush=True,
    )
    poll = QTimer()
    poll.setInterval(POLL_MS)
    poll.timeout.connect(lambda: None if clipboard.ownsClipboard() else app.quit())
    poll.start()
    limit = QTimer()
    limit.setSingleShot(True)
    limit.timeout.connect(app.quit)
    limit.start(seconds * 1000)
    with suppress(KeyboardInterrupt):
        app.exec()
