"""The Qt layer: the application object, the icon, and crossing threads safely.

Widgets may only be touched from the thread the QApplication runs on. Two
primitives cover every case here:

* `run_blocking(fn)` runs a slow call (network, a portal that waits for the
  user) on a worker thread while the UI stays alive, and returns its result.
* `main_thread(callback)` hands a callback to code that fires on another thread
  (the Windows hotkey loop, jeepney's receiver) so it runs on the UI thread.
"""

import signal
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEventLoop, QObject, QRect, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QProgressDialog

from eecc_redact import APP_ID, APP_NAME


def ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        # Both names before construction. On Wayland, Qt registers the app with
        # the desktop's portal registry while starting up, under the desktop file
        # name it has at that moment; a name set afterwards makes it register a
        # second time, which the portal refuses ("Connection already associated
        # with an application ID").
        QApplication.setApplicationName(APP_NAME)
        QApplication.setDesktopFileName(APP_ID)  # windows belong to the desktop entry
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        # Python handles SIGINT only while running Python code, and an idle Qt
        # loop runs none, so Ctrl-C in a terminal would seem ignored. End at once.
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    return app


def icon(size: int = 64) -> QIcon:
    """A redaction bar, drawn: no asset to ship."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#15181D"))
    painter.setPen(QPen(QColor("#FFFFFF"), max(2, size // 16)))  # visible on a dark taskbar
    painter.drawRoundedRect(
        QRect(int(size * 0.08), int(size * 0.34), int(size * 0.84), int(size * 0.32)), 3, 3
    )
    painter.end()
    return QIcon(pixmap)


class Task(QThread):
    """Run `fn` on a worker thread; `done(result, error)` is emitted when it returns.

    Owned by the application, so it outlives any dialog that started it, and
    deleted by Qt once finished.
    """

    done = Signal(object, object)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__(QApplication.instance())
        self._fn = fn
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            self.done.emit(self._fn(), None)
        except Exception as exc:  # reported to the caller, who shows or re-raises it
            self.done.emit(None, exc)
        finally:
            self._fn = None  # type: ignore[assignment]


def run_blocking(fn: Callable[[], Any], label: str = "") -> Any:
    """Run `fn` off the UI thread and wait for it, keeping Qt responsive.

    With a label, a small busy dialog is shown meanwhile. Exceptions from `fn`
    are re-raised here.
    """
    loop = QEventLoop()
    outcome: dict[str, Any] = {}

    def finished(value: Any, error: Exception | None) -> None:
        outcome["value"], outcome["error"] = value, error
        loop.quit()

    task = Task(fn)
    task.done.connect(finished, Qt.ConnectionType.QueuedConnection)
    progress = None
    if label:
        progress = QProgressDialog(label, "", 0, 0)
        progress.setWindowTitle(APP_NAME)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.show()
    task.start()
    loop.exec()
    task.wait()
    if progress is not None:
        progress.close()
        progress.deleteLater()
    if outcome["error"] is not None:
        raise outcome["error"]
    return outcome["value"]


class _Relay(QObject):
    fired = Signal()

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__(QApplication.instance())
        self._callback = callback
        self.fired.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _run(self) -> None:
        self._callback()


def main_thread(callback: Callable[[], None]) -> Callable[[], None]:
    """Return a function any thread may call; `callback` then runs on the UI thread."""
    return _Relay(callback).fired.emit
