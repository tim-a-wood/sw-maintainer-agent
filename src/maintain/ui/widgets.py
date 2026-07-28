"""Shared widgets: stage header, drop zone, file chips, packet card."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QDrag, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from .strings import text

STAGES = ("plan", "build", "review", "test", "save")


def button(label: str, kind: str = "Secondary",
           on_click: Callable[[], None] | None = None) -> QPushButton:
    control = QPushButton(label)
    control.setObjectName(kind)
    control.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        control.clicked.connect(on_click)
    return control


def label(content: str, name: str = "") -> QLabel:
    control = QLabel(content)
    if name:
        control.setObjectName(name)
    control.setWordWrap(True)
    return control


class StageHeader(QWidget):
    """Plan · Build · Review · Test · Save with done and current marks."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("StageBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(22, 12, 22, 0)
        row.setSpacing(6)
        self._dots: list[QLabel] = []
        for index, key in enumerate(STAGES):
            dot = QLabel(f"{index + 1}  {text('stage.' + key)}")
            dot.setObjectName("StateWait")
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(dot)
            self._dots.append(dot)
        row.addStretch(1)

    def set_stage(self, current: int) -> None:
        for index, dot in enumerate(self._dots):
            key = STAGES[index]
            if index < current:
                dot.setObjectName("StatePass")
                dot.setText(f"✓  {text('stage.' + key)}")
            elif index == current:
                dot.setObjectName("StateWarn")
                dot.setText(f"{index + 1}  {text('stage.' + key)}")
            else:
                dot.setObjectName("StateWait")
                dot.setText(f"{index + 1}  {text('stage.' + key)}")
            dot.style().unpolish(dot)
            dot.style().polish(dot)


class DropZone(QFrame):
    """A file drop target with a click alternative. Emits local file paths."""

    files_dropped = Signal(list)

    def __init__(self, main: str, sub: str = "") -> None:
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("active", False)
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 18, 18, 18)
        title = label(main)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)
        if sub:
            subtitle = label(sub, "Hint")
            subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(subtitle)

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self._set_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_active(False)
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()
                 if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class FileChips(QWidget):
    """Removable chips for attached files."""

    removed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)
        self._items: list[str] = []

    def set_files(self, names: list[str], removable: bool = True) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._items = list(names)
        for index, name in enumerate(names):
            chip = QLabel(name)
            chip.setObjectName("Chip")
            self._row.addWidget(chip)
            if removable:
                remove = QPushButton("✕")
                remove.setObjectName("Danger")
                remove.setFixedWidth(28)
                remove.setAccessibleName(f"Remove {name}")
                remove.clicked.connect(lambda _=False, i=index: self.removed.emit(i))
                self._row.addWidget(remove)
        self._row.addStretch(1)
        self.setVisible(bool(names))


class PacketCard(QFrame):
    """The draggable packet: dragging it carries the real ZIP file."""

    drag_started = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PacketCard")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._path: Path | None = None
        column = QVBoxLayout(self)
        column.setContentsMargins(16, 14, 16, 14)
        self.name_label = label("", "Mono")
        self.sub_label = label("", "Hint")
        column.addWidget(self.name_label)
        column.addWidget(self.sub_label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_packet(self, path: Path, size_bytes: int) -> None:
        self._path = Path(path)
        self.name_label.setText(self._path.name)
        kib = max(1, round(size_bytes / 1024))
        self.sub_label.setText(f"{kib} KB · {text('send.drag')}")
        self.setAccessibleName(f"Package {self._path.name}. {text('send.drag')}")

    @property
    def packet_path(self) -> Path | None:
        return self._path

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton
                and self._path is not None and self._path.is_file()):
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(self._path))])
            drag = QDrag(self)
            drag.setMimeData(mime)
            self.drag_started.emit()
            drag.exec(Qt.DropAction.CopyAction)
        super().mousePressEvent(event)
