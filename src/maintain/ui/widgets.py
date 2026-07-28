"""Shared widgets, built to the mockup's visual bar."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import (QMimeData, QPoint, QRect, QSize, Qt, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import (QColor, QDrag, QDragEnterEvent, QDropEvent, QFont,
                           QPainter, QPen, QPixmap, QSyntaxHighlighter,
                           QTextCharFormat)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .strings import text

STAGES = ("plan", "build", "review", "test", "save")

ZIP_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8">'
    '<path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1'
    '-2-2Z"/><path d="M12 10v1m0 2v1m0 2v1" stroke-linecap="round"/></svg>')
PKG_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5">'
    '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1'
    '-2-2Z"/><path d="M11.5 9v1.2m0 1.8v1.2m0 1.8V16.2" stroke-linecap="round"/>'
    '<path d="M15 12.5h3M15 15h3" stroke-linecap="round" opacity=".55"/></svg>')


def svg_pixmap(svg: str, size: int, color: str) -> QPixmap:
    renderer = QSvgRenderer(svg.format(color=color).encode())
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def palette() -> theme.Palette:
    return theme.ACTIVE


def button(label_text: str, kind: str = "Secondary",
           on_click: Callable[[], None] | None = None) -> QPushButton:
    control = QPushButton(label_text)
    control.setObjectName(kind)
    control.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        control.clicked.connect(on_click)
    return control


def label(content: str, name: str = "") -> QLabel:
    control = QLabel(content)
    if name:
        control.setObjectName(name)
    if name == "Eyebrow":
        font = control.font()
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        font.setBold(True)
        control.setFont(font)
    control.setWordWrap(True)
    return control


class FlowLayout(QLayout):
    """Chips wrap onto the next line, like the mockup."""

    def __init__(self, spacing: int = 6) -> None:
        super().__init__()
        self._items: list = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect: QRect, apply: bool) -> int:
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and row_height > 0:
                x = rect.x()
                y += row_height + self.spacing()
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y()


class Spinner(QWidget):
    """A small rotating arc, like the mockup's spinner."""

    def __init__(self, diameter: int = 14) -> None:
        super().__init__()
        self._angle = 0
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.update()

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = palette()
        pen = QPen(QColor(colors.chip_edge), 2.4)
        painter.setPen(pen)
        margin = 2
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        painter.drawEllipse(rect)
        pen.setColor(QColor(colors.accent))
        painter.setPen(pen)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)


class StatusLine(QWidget):
    """One status message with a spinner, a check, or a cross before it."""

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.spinner = Spinner()
        self.icon = QLabel("")
        self.icon.setFixedWidth(14)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        row.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.message, 1)
        self.set_state("plain", "")

    def text(self) -> str:
        return self.message.text()

    def set_state(self, kind: str, message: str) -> None:
        colors = palette()
        self.message.setText(message)
        self.setVisible(bool(message))
        if kind == "busy":
            self.spinner.start()
            self.icon.hide()
            self.message.setStyleSheet(f"color: {colors.dim};")
            return
        self.spinner.stop()
        glyph, color, weight = {
            "ok": ("✓", colors.ok, "600"),
            "bad": ("✕", colors.bad, "600"),
            "warn": ("◆", colors.warn, "600"),
        }.get(kind, ("", colors.dim, "400"))
        self.icon.setText(glyph)
        self.icon.setVisible(bool(glyph))
        self.icon.setStyleSheet(f"color: {color}; font-weight: 700;")
        self.message.setStyleSheet(f"color: {color}; font-weight: {weight};")


class StageHeader(QWidget):
    """Plan · Build · Review · Test · Save as painted dots with connectors."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("StageBar")
        self.setFixedHeight(64)
        self._current = -1
        self.setAutoFillBackground(True)

    def set_stage(self, current: int) -> None:
        self._current = current
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = palette()
        painter.fillRect(self.rect(), QColor(colors.surface))
        margin, radius = 34, 12
        top = 18
        width = self.width() - 2 * margin
        step = width / (len(STAGES) - 1)
        centers = [QPoint(int(margin + index * step), top + radius)
                   for index in range(len(STAGES))]
        for index in range(len(STAGES) - 1):
            done = index < self._current
            pen = QPen(QColor(colors.ok if done else colors.chip_edge), 2)
            painter.setPen(pen)
            painter.drawLine(centers[index].x() + radius + 3, top + radius,
                             centers[index + 1].x() - radius - 3, top + radius)
        for index, key in enumerate(STAGES):
            center = centers[index]
            circle = QRect(center.x() - radius, center.y() - radius,
                           2 * radius, 2 * radius)
            font = painter.font()
            if index < self._current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(colors.ok))
                painter.drawEllipse(circle)
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.drawLine(center.x() - 4, center.y(),
                                 center.x() - 1, center.y() + 3)
                painter.drawLine(center.x() - 1, center.y() + 3,
                                 center.x() + 4, center.y() - 3)
            elif index == self._current:
                glow = QColor(colors.accent)
                glow.setAlpha(40)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(glow)
                painter.drawEllipse(circle.adjusted(-4, -4, 4, 4))
                painter.setBrush(QColor(colors.surface))
                painter.setPen(QPen(QColor(colors.accent), 2))
                painter.drawEllipse(circle)
                painter.setPen(QColor(colors.accent))
                font.setBold(True)
                font.setPointSizeF(8.5)
                painter.setFont(font)
                painter.drawText(circle, Qt.AlignmentFlag.AlignCenter,
                                 str(index + 1))
            else:
                painter.setBrush(QColor(colors.surface))
                painter.setPen(QPen(QColor(colors.chip_edge), 2))
                painter.drawEllipse(circle)
                painter.setPen(QColor(colors.faint))
                font.setBold(False)
                font.setPointSizeF(8.5)
                painter.setFont(font)
                painter.drawText(circle, Qt.AlignmentFlag.AlignCenter,
                                 str(index + 1))
            label_font = painter.font()
            label_font.setPointSizeF(7.5)
            label_font.setBold(index == self._current)
            painter.setFont(label_font)
            painter.setPen(QColor(colors.accent if index == self._current
                                  else colors.dim))
            painter.drawText(QRect(center.x() - 30, top + 2 * radius + 3, 60, 14),
                             Qt.AlignmentFlag.AlignCenter, text("stage." + key))


class StateChip(QLabel):
    """A small state pill: PASS, FAIL, WAIT, or a run state."""

    KINDS = {"pass": "StatePass", "fail": "StateFail", "wait": "StateWait",
             "warn": "StateWarn", "accent": "StateAccent"}

    def __init__(self, content: str = "", kind: str = "wait") -> None:
        super().__init__(content)
        self.set_state(content, kind)

    def set_state(self, content: str, kind: str) -> None:
        self.setText(content)
        self.setObjectName(self.KINDS.get(kind, "StateWait"))
        self.style().unpolish(self)
        self.style().polish(self)


def run_state_chip(display_state: str) -> StateChip:
    kind = {"Saved": "pass", "In work": "accent", "Waiting": "warn",
            "Discarded": "wait", "Failed": "fail"}.get(display_state, "wait")
    return StateChip(display_state.upper(), kind)


class ChoiceButton(QFrame):
    """A large choice card: an icon square, a bold title, and a sub line."""

    clicked = Signal()

    def __init__(self, glyph: str, title: str, sub: str,
                 accent_kind: str = "accent") -> None:
        super().__init__()
        self.setObjectName("Choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 13, 16, 13)
        row.setSpacing(14)
        icon = QLabel(glyph)
        icon.setObjectName("ChoiceIconWarn" if accent_kind == "warn"
                           else "ChoiceIcon")
        icon.setFixedSize(38, 38)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(icon)
        column = QVBoxLayout()
        column.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("ChoiceTitle")
        self.sub_label = QLabel(sub)
        self.sub_label.setObjectName("ChoiceSub")
        self.sub_label.setWordWrap(True)
        column.addWidget(self.title_label)
        column.addWidget(self.sub_label)
        row.addLayout(column, 1)

    def set_texts(self, title: str, sub: str) -> None:
        self.title_label.setText(title)
        self.sub_label.setText(sub)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class Chip(QFrame):
    """A file chip: zip icon, mono name, optional size, optional remove."""

    removed = Signal()

    def __init__(self, name: str, size_text: str = "",
                 removable: bool = False) -> None:
        super().__init__()
        self.setObjectName("ChipFrame")
        row = QHBoxLayout(self)
        row.setContentsMargins(9, 5, 6 if removable else 9, 5)
        row.setSpacing(7)
        icon = QLabel()
        icon.setPixmap(svg_pixmap(ZIP_SVG, 15, palette().accent))
        icon.setFixedSize(15, 15)
        row.addWidget(icon)
        name_label = QLabel(name)
        name_label.setObjectName("ChipName")
        row.addWidget(name_label)
        if size_text:
            size_label = QLabel(size_text)
            size_label.setObjectName("ChipSize")
            row.addWidget(size_label)
        if removable:
            remove = QPushButton("×")
            remove.setObjectName("ChipRemove")
            remove.setFixedSize(18, 18)
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.setAccessibleName(f"Remove {name}")
            remove.clicked.connect(self.removed.emit)
            row.addWidget(remove)


class FileChips(QWidget):
    """A wrapping row of removable file chips."""

    removed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._flow = FlowLayout()
        self.setLayout(self._flow)

    def set_files(self, names: list[str], removable: bool = True) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, name in enumerate(names):
            chip = Chip(name, removable=removable)
            chip.removed.connect(lambda i=index: self.removed.emit(i))
            self._flow.addWidget(chip)
        self.setVisible(bool(names))
        self.updateGeometry()


class DropZone(QFrame):
    """A file drop target with a click alternative. Emits local file paths."""

    files_dropped = Signal(list)

    def __init__(self, main: str, sub: str = "", slim: bool = False) -> None:
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("active", False)
        column = QVBoxLayout(self)
        pad = 10 if slim else 22
        column.setContentsMargins(16, pad, 16, pad)
        column.setSpacing(3)
        title = QLabel(main)
        title.setObjectName("DropMain" if not slim else "DropSlim")
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)
        if sub:
            subtitle = QLabel(sub)
            subtitle.setObjectName("Hint")
            subtitle.setWordWrap(True)
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


class PacketCard(QFrame):
    """The draggable packet: dragging it carries the real ZIP file."""

    drag_started = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PacketCard")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._path: Path | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 13, 14, 13)
        row.setSpacing(14)
        self.icon = QLabel()
        self.icon.setPixmap(svg_pixmap(PKG_SVG, 40, palette().accent))
        self.icon.setFixedSize(40, 40)
        row.addWidget(self.icon)
        column = QVBoxLayout()
        column.setSpacing(1)
        self.name_label = QLabel("")
        self.name_label.setObjectName("PacketName")
        self.name_label.setWordWrap(True)
        self.sub_label = QLabel("")
        self.sub_label.setObjectName("Hint")
        column.addWidget(self.name_label)
        column.addWidget(self.sub_label)
        row.addLayout(column, 1)
        grip = QLabel("⁞⁞")
        grip.setObjectName("PacketGrip")
        row.addWidget(grip)
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


class TimelineDot(QWidget):
    """The numbered circle with connector segments for one timeline row."""

    def __init__(self, number: int, kind: str, first: bool, last: bool) -> None:
        super().__init__()
        self.number, self.kind, self.first, self.last = number, kind, first, last
        self.setFixedWidth(30)
        self.setMinimumHeight(44)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = palette()
        center_x, center_y, radius = 13, 21, 11
        line = QPen(QColor(colors.chip_edge), 2)
        painter.setPen(line)
        if not self.first:
            painter.drawLine(center_x, 0, center_x, center_y - radius - 2)
        if not self.last:
            painter.drawLine(center_x, center_y + radius + 2, center_x,
                             self.height())
        circle = QRect(center_x - radius, center_y - radius, 2 * radius, 2 * radius)
        outline = {"current": colors.accent, "revert": colors.warn,
                   "super": colors.chip_edge}.get(self.kind, colors.chip_edge)
        if self.kind == "current":
            glow = QColor(colors.accent)
            glow.setAlpha(40)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(circle.adjusted(-3, -3, 3, 3))
        painter.setBrush(QColor(colors.surface))
        painter.setPen(QPen(QColor(outline), 2))
        painter.drawEllipse(circle)
        painter.setPen(QColor({"current": colors.accent,
                               "revert": colors.warn}.get(self.kind, colors.dim)))
        font = painter.font()
        font.setPointSizeF(7.5)
        font.setBold(True)
        painter.setFont(font)
        glyph = "↻" if self.kind == "revert" else str(self.number)
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, glyph)


class NumberBadge(QLabel):
    """A small accent circle with a task number."""

    def __init__(self, number: int) -> None:
        super().__init__(str(number))
        self.setObjectName("NumberBadge")
        self.setFixedSize(24, 24)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


# The diff colors from the mockup's code block, which is dark in both themes.
DIFF_ADDED = "#7ce0a3"
DIFF_REMOVED = "#ff9b8f"
DIFF_HEADER = "#8fb8e8"

_DIFF_HEADER_STARTS = ("diff --git", "index ", "@@", "new file mode",
                       "deleted file mode", "old mode", "new mode",
                       "similarity index", "rename from", "rename to",
                       "Binary files")
_DIFF_FILE_TARGETS = ("a/", "b/", '"a/', '"b/', "/dev/null")


def diff_line_kind(line: str) -> str:
    """Classify one unified-diff line: header, added, removed, or context.

    A file header (``--- a/x``) and a removed line that itself starts with
    two dashes (``--- comment``) are told apart by the header's target.
    """
    if line.startswith(_DIFF_HEADER_STARTS):
        return "header"
    if line.startswith(("+++ ", "--- ")):
        target = line[4:]
        if target.startswith(_DIFF_FILE_TARGETS):
            return "header"
        return "added" if line[0] == "+" else "removed"
    if line in ("+++", "---"):
        return "header"
    if line.startswith("+"):
        return "added"
    if line.startswith("-"):
        return "removed"
    return "context"


class DiffHighlighter(QSyntaxHighlighter):
    """Colors added, removed, and header lines in a unified diff view."""

    def highlightBlock(self, block_text: str) -> None:  # noqa: N802
        kind = diff_line_kind(block_text)
        if kind == "context":
            return
        line_format = QTextCharFormat()
        if kind == "header":
            line_format.setForeground(QColor(DIFF_HEADER))
        elif kind == "added":
            line_format.setForeground(QColor(DIFF_ADDED))
            line_format.setBackground(QColor(124, 224, 163, 26))
        else:
            line_format.setForeground(QColor(DIFF_REMOVED))
            line_format.setBackground(QColor(255, 155, 143, 26))
        self.setFormat(0, len(block_text), line_format)
