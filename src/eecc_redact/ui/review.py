"""The gate: nothing is copied or saved except through this window.

Everything detected starts covered; revealing a false positive takes a click.
Findings are grouped by kind, because a page of personal data produces dozens
and a flat list is unusable. Selecting an item outlines it in the preview, and
a revealed item that another finding still covers says so: detectors overlap,
and unticking one would otherwise look like nothing happened.
"""

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

from platformdirs import user_pictures_path
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from eecc_redact import APP_NAME
from eecc_redact.config import Config
from eecc_redact.errors import AppError
from eecc_redact.imaging import burn
from eecc_redact.keystore import scrub
from eecc_redact.models import Box, Detection
from eecc_redact.ui import ensure_app, icon

MAX_PREVIEW = (840, 700)
HIGHLIGHT = QColor("#00B4D8")
INDEX = Qt.ItemDataRole.UserRole
#: Never title-cased, or IBAN_CODE reads as "Iban Code".
ACRONYMS = frozenset(
    {"IBAN", "IP", "US", "UK", "SSN", "ID", "URL", "VIN", "BSN", "MICR", "SWIFT", "NHS"}
)


def pretty(info_type: str) -> str:
    return " ".join(w if w in ACRONYMS else w.title() for w in info_type.split("_"))


def still_covering(boxes: tuple[Box, ...], active: list[tuple[str, Box]]) -> set[str]:
    """The kinds whose active boxes still hide every one of `boxes`; empty otherwise."""
    names: set[str] = set()
    for box in boxes:
        covering = {name for name, other in active if other.contains(box)}
        if not covering:
            return set()
        names |= covering
    return names


def summarize(detection: Detection) -> str:
    counts = Counter(finding.info_type for finding in detection.findings)
    return ", ".join(
        f"{pretty(name)} ×{count}" if count > 1 else pretty(name)
        for name, count in sorted(counts.items())
    )


class ReviewDialog(QDialog):
    def __init__(self, png: bytes, detection: Detection, config: Config) -> None:
        super().__init__()
        self.png = png
        self.detection = detection
        self.padding = config.box_padding
        self.enabled = [True] * len(detection.findings)
        self.outcome = "discarded"
        self._quiet = False  # while check states are set by code, not the user
        self._queued = False
        self.setWindowTitle(f"{APP_NAME} - review before sharing")
        self.setWindowIcon(icon())

        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        canvas = QScrollArea()
        canvas.setWidget(self.preview)
        canvas.setWidgetResizable(False)  # sized to the pixmap; the area centres it
        canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        canvas.setMinimumWidth(460)

        self.tree = QTreeWidget(objectName="findings")
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._fill_tree()
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self.schedule)

        side = QWidget()
        side.setMinimumWidth(300)
        side.setMaximumWidth(400)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(self._heading())
        heading.setWordWrap(True)
        side_layout.addWidget(heading)
        if detection.findings:
            row = QHBoxLayout()
            cover = QPushButton("Cover all", objectName="coverAll")
            reveal = QPushButton("Reveal all", objectName="revealAll")
            cover.clicked.connect(lambda: self.set_all(True))
            reveal.clicked.connect(lambda: self.set_all(False))
            row.addWidget(cover)
            row.addWidget(reveal)
            side_layout.addLayout(row)
            hint = QLabel("Select an item to outline it in the preview.")
            hint.setStyleSheet("color: palette(mid);")
            side_layout.addWidget(hint)
            side_layout.addWidget(self.tree, 1)
        else:
            side_layout.addStretch(1)
        notes = []
        if detection.records_remaining is not None:
            notes.append(f"{detection.records_remaining:,} records left")
        if detection.warnings:
            notes.append(detection.warnings)
        if notes:
            note = QLabel(" · ".join(notes))
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(mid); font-size: 11px;")
            side_layout.addWidget(note)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(canvas)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setCollapsible(0, False)
        split.setSizes([820, 360])

        buttons = QHBoxLayout()
        discard = QPushButton("Discard", objectName="discard")
        save = QPushButton("Save…", objectName="save")
        copy = QPushButton("Copy", objectName="copy")
        copy.setDefault(True)
        discard.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        copy.clicked.connect(self.copy)
        buttons.addWidget(discard)
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(copy)

        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addLayout(buttons)
        self.refresh()
        self.resize(1180, 780)

    # -- the image -------------------------------------------------------------

    def export(self) -> bytes:
        """The image that leaves: composed locally from the original frame."""
        boxes = [
            box
            for index, finding in enumerate(self.detection.findings)
            if self.enabled[index]
            for box in finding.boxes
        ]
        return burn(self.png, boxes, padding=self.padding)

    def copy(self) -> None:
        QGuiApplication.clipboard().setImage(QImage.fromData(self.export(), "PNG"))
        self.outcome = "copied"
        self.accept()

    def save(self) -> None:
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default = str(user_pictures_path() / f"{APP_NAME}_{stamp}.png")
        target, _ = QFileDialog.getSaveFileName(
            self, "Save redacted screenshot", default, "PNG image (*.png)"
        )
        if target:
            Path(target).write_bytes(self.export())
            self.outcome = "saved"
            self.accept()

    # -- the list --------------------------------------------------------------

    def _fill_tree(self) -> None:
        groups: dict[str, list[int]] = defaultdict(list)
        for index, finding in enumerate(self.detection.findings):
            groups[finding.info_type].append(index)
        self._quiet = True
        for info_type, indices in sorted(groups.items()):
            group = QTreeWidgetItem(self.tree, [f"{pretty(info_type)}  ({len(indices)})"])
            group.setFlags(
                group.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
            )
            group.setCheckState(0, Qt.CheckState.Checked)
            for index in indices:
                item = QTreeWidgetItem(group)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setData(0, INDEX, index)
                item.setCheckState(0, Qt.CheckState.Checked)
        self.tree.expandAll()
        self._quiet = False

    def _items(self) -> Iterator[QTreeWidgetItem]:
        root = self.tree.invisibleRootItem()
        for g in range(root.childCount()):
            group = root.child(g)
            for c in range(group.childCount()):
                yield group.child(c)

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._quiet:
            return
        index = item.data(0, INDEX)
        if index is None:
            return  # a group: its children each report their own change
        self.enabled[index] = item.checkState(0) == Qt.CheckState.Checked
        self.schedule()

    def set_all(self, covered: bool) -> None:
        state = Qt.CheckState.Checked if covered else Qt.CheckState.Unchecked
        self._quiet = True
        root = self.tree.invisibleRootItem()
        for g in range(root.childCount()):
            root.child(g).setCheckState(0, state)
        for item in self._items():
            item.setCheckState(0, state)
            self.enabled[item.data(0, INDEX)] = covered
        self._quiet = False
        self.schedule()

    def selected(self) -> set[int]:
        chosen: set[int] = set()
        for item in self.tree.selectedItems():
            index = item.data(0, INDEX)
            if index is None:
                chosen.update(item.child(c).data(0, INDEX) for c in range(item.childCount()))
            else:
                chosen.add(index)
        return chosen

    # -- redrawing -------------------------------------------------------------

    def schedule(self) -> None:
        """Coalesce bursts of changes (a group toggle fires once per child)."""
        if not self._queued:
            self._queued = True
            QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        self._queued = False
        self._annotate()
        self._render()

    def _annotate(self) -> None:
        """Say which other kind still covers a revealed item."""
        active = [
            (finding.info_type, box)
            for index, finding in enumerate(self.detection.findings)
            if self.enabled[index]
            for box in finding.boxes
        ]
        self._quiet = True
        for item in self._items():
            index = item.data(0, INDEX)
            finding = self.detection.findings[index]
            label = f"{len(finding.boxes)} box(es)"
            if finding.boxes:
                label += f" · y={min(box.y for box in finding.boxes)}"
            tip = ""
            if not self.enabled[index] and finding.boxes:
                covering = still_covering(finding.boxes, active)
                if covering:
                    names = ", ".join(sorted(pretty(name) for name in covering))
                    label += f"  — still hidden by {names}"
                    tip = (
                        f"The same pixels were also reported as {names}. "
                        "Untick that item too to reveal this area."
                    )
            item.setText(0, label)
            item.setToolTip(0, tip)
        self._quiet = False

    def _render(self) -> None:
        image = QImage.fromData(self.export(), "PNG")
        if highlight := self.selected():
            painter = QPainter(image)
            pen = QPen(HIGHLIGHT)
            pen.setWidth(max(2, image.width() // 500))
            painter.setPen(pen)
            for index in highlight:
                for box in self.detection.findings[index].boxes:
                    painter.drawRect(QRect(box.x, box.y, box.w, box.h))
            painter.end()
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > MAX_PREVIEW[0] or pixmap.height() > MAX_PREVIEW[1]:
            pixmap = pixmap.scaled(
                *MAX_PREVIEW,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.preview.setPixmap(pixmap)
        self.preview.setFixedSize(pixmap.size())

    def _heading(self) -> str:
        detection = self.detection
        if detection.findings:
            return f"<b>{len(detection.findings)} items covered</b><br>{summarize(detection)}"
        return (
            "<b>Nothing detected.</b><br>Check the image yourself before sharing: an "
            "empty result can also mean the text could not be read."
        )


def show_review(png: bytes, detection: Detection, config: Config) -> str:
    """Returns "copied", "saved" or "discarded"."""
    ensure_app()
    dialog = ReviewDialog(png, detection, config)
    dialog.exec()
    return dialog.outcome


def show_failure(exc: AppError) -> str:
    """Fail closed: Try again or Discard, never the original. Returns "retry" or "discard"."""
    ensure_app()
    box = QMessageBox()
    box.setWindowIcon(icon())
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(f"{APP_NAME} - not redacted")
    box.setText(exc.message)
    box.setInformativeText(
        (scrub(exc.detail) + "\n\n" if exc.detail else "")
        + "The screenshot was not redacted, so it has been discarded."
    )
    retry = box.addButton("Try again", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Discard", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return "retry" if box.clickedButton() is retry else "discard"
