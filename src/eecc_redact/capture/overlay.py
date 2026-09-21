"""Freeze the screen, then select on the still image. Windows, Linux X11, macOS.

Capturing first means the selection is exactly what the user saw, content that
changes mid-drag cannot slip in, and the overlay itself never appears in the
capture. The drag happens in logical pixels and the crop in physical ones, so
each screen is scaled by its own ratio: a 150% monitor beside a 100% one would
otherwise crop the wrong area.
"""

from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap, QScreen
from PySide6.QtWidgets import QWidget

from eecc_redact.errors import AppError, Cancelled

#: Drags smaller than this (logical px) are accidental clicks.
MIN_SIDE = 8


def grab_screens() -> list[tuple[QScreen, QPixmap]]:
    """One frozen frame per screen, in physical pixels."""
    frames = []
    for screen in QGuiApplication.screens():
        pixmap = screen.grabWindow(0)
        if pixmap.isNull() or pixmap.width() == 0:
            raise AppError("This desktop did not allow a screen grab.")
        frames.append((screen, pixmap))
    if not frames:
        raise AppError("No screens found.")
    return frames


class _Selection:
    """Shared by the overlays: the first one to finish ends the selection."""

    def __init__(self) -> None:
        self.loop = QEventLoop()
        self.overlays: list[QWidget] = []
        self.png = b""
        self.done = False

    def finish(self, png: bytes = b"") -> None:
        if self.done:
            return
        self.done = True
        self.png = png
        # close() only hides a widget, so nothing ever emits `destroyed`:
        # waiting on it hung the capture forever. End the loop explicitly.
        for overlay in self.overlays:
            overlay.close()
        self.loop.quit()


class Overlay(QWidget):
    def __init__(self, screen: QScreen, pixmap: QPixmap, selection: _Selection) -> None:
        super().__init__()
        self._pixmap = pixmap
        self._selection = selection
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        geometry = screen.geometry()
        # Physical per logical pixel, taken from the grab itself rather than
        # from what the platform reports.
        self._ratio = pixmap.width() / geometry.width() if geometry.width() else 1.0
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(geometry)

    def _rect(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        # From width and height, not QRect(topLeft, bottomRight): Qt counts the
        # bottom-right point as inside, so a 200 px drag became a 201 px crop.
        x0, y0, x1, y1 = self._origin.x(), self._origin.y(), self._current.x(), self._current.y()
        return QRect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def _physical(self, rect: QRect) -> QRect:
        r = self._ratio
        return QRect(
            int(rect.x() * r), int(rect.y() * r), int(rect.width() * r), int(rect.height() * r)
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        rect = self._rect()
        if rect.isValid():
            painter.drawPixmap(rect, self._pixmap, self._physical(rect))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._origin = self._current = event.position().toPoint()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        rect = self._rect()
        if rect.width() < MIN_SIDE or rect.height() < MIN_SIDE:
            self._origin = self._current = None
            self.update()
            return
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self._pixmap.copy(self._physical(rect)).save(buffer, "PNG")
        self._selection.finish(bytes(buffer.data()))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._selection.finish()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closed some other way (Alt+F4, the window manager) counts as cancel,
        # or the loop would be left waiting.
        self._selection.finish()
        event.accept()


def select_region() -> bytes:
    """Show the overlay on every screen and return the cropped PNG."""
    selection = _Selection()
    for screen, pixmap in grab_screens():
        overlay = Overlay(screen, pixmap, selection)
        selection.overlays.append(overlay)
        overlay.showFullScreen()
        overlay.raise_()
        overlay.activateWindow()
    selection.loop.exec()
    if not selection.png:
        raise Cancelled()
    return selection.png
