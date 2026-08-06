"""Shared widgets, built to the mockup's visual bar."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import (QMimeData, QPoint, QRect, QRectF, QSize, Qt,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDrag, QDragEnterEvent, QDropEvent, QFont,
                           QFontMetrics, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap, QSyntaxHighlighter,
                           QTextCharFormat)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .strings import text

STAGES = ("plan", "build", "review", "test", "save")

# One icon language: a 24-unit grid, 1.8 stroke, round caps and joins.
ICONS: dict[str, str] = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "wrench": ('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l'
               '3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l'
               '6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>'),
    "history": ('<path d="M2.5 5v6h6"/>'
                '<path d="M5 15.5a8.5 8.5 0 1 0 2-8.86L2.5 11"/>'),
    "folder": ('<path d="M3.5 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 '
               '1-2 2h-13a2 2 0 0 1-2-2z"/>'),
    "sliders": ('<path d="M4 6.5h5.6M13.6 6.5H20"/><circle cx="11.6" cy="6.5" '
                'r="2.1"/><path d="M4 12h2.4M10.4 12H20"/><circle cx="8.4" '
                'cy="12" r="2.1"/><path d="M4 17.5h9.6M17.6 17.5H20"/>'
                '<circle cx="15.6" cy="17.5" r="2.1"/>'),
    "play": ('<circle cx="12" cy="12" r="8.6"/>'
             '<path d="M10.4 8.9 15.4 12l-5 3.1z" fill="{color}" '
             'stroke="none"/>'),
    "cloud": ('<path d="M18 10.5h-1.3A6.7 6.7 0 1 0 9.3 19h8.7a4.25 4.25 0 0 0 '
              '0-8.5z"/>'),
    "file-text": ('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 '
                  '0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>'),
    "globe": ('<circle cx="12" cy="12" r="8.6"/><path d="M3.4 12h17.2"/>'
              '<path d="M12 3.4a13.4 13.4 0 0 1 0 17.2M12 3.4a13.4 13.4 0 0 0 '
              '0 17.2"/>'),
    "box": ('<path d="M21 8.2 12 3.4 3 8.2v7.6l9 4.8 9-4.8z"/>'
            '<path d="M3 8.2l9 4.8 9-4.8"/><path d="M12 13v7.6"/>'),
    "check-circle": ('<circle cx="12" cy="12" r="8.6"/>'
                     '<path d="M8.2 12.4l2.7 2.7 4.9-5.7"/>'),
    "check": '<path d="M5 13l4.2 4.2L19 7"/>',
    "bug": ('<rect x="8.5" y="8" width="7" height="11" rx="3.5"/>'
            '<path d="M12 8v11"/><path d="M10 8 8.5 5.5M14 8l1.5-2.5"/>'
            '<path d="M8.5 11H5M8.5 15H5.5M15.5 11H19M15.5 15h3"/>'),
    "search": ('<circle cx="11" cy="11" r="6.5"/>'
               '<path d="M15.8 15.8 20 20"/>'),
    "message": ('<path d="M20 6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8.5a2 2 0 0 0 '
                '2 2h2.3L12 20l3.7-3.5H18a2 2 0 0 0 2-2z"/>'
                '<path d="M8 8.8h8M8 12h5.5"/>'),
    "film": ('<rect x="3.5" y="5" width="17" height="14" rx="2"/>'
             '<path d="M7.5 5v14M16.5 5v14"/>'
             '<path d="M3.5 9.5h4M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4"/>'),
    "download": '<path d="M12 4.5v10.5M7.2 11.2 12 16l4.8-4.8M5 19.5h14"/>',
    "upload": '<path d="M12 16V5.5M7.2 9.3 12 4.5l4.8 4.8M5 19.5h14"/>',
    "zip": ('<path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 '
            '2H6a2 2 0 0 1-2-2z"/><path d="M12 10v1m0 2v1m0 2v1"/>'),
    "file": ('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 '
             '2-2V8z"/><path d="M14 3v5h5"/>'),
    "package": ('<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 '
                '1-2 2H5a2 2 0 0 1-2-2z"/><path d="M11.5 9v1.2m0 1.8v1.2m0 '
                '1.8v1.2"/><path d="M15 12.5h3M15 15h3" opacity=".55"/>'),
}


def icon_svg(name: str, color: str) -> bytes:
    body = ICONS[name].replace("{color}", color)
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="{color}" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            f"{body}</svg>").encode()


class Icon(QWidget):
    """One themed icon. It reads the palette at paint time, so a theme
    change recolors it without a rebuild."""

    def __init__(self, name: str, size: int = 18,
                 color_key: str = "accent") -> None:
        super().__init__()
        self._name = name
        self._color_key = color_key
        self.setFixedSize(size, size)
        self._renderer: QSvgRenderer | None = None
        self._rendered = ""

    def set_icon(self, name: str, color_key: str | None = None) -> None:
        self._name = name
        if color_key is not None:
            self._color_key = color_key
        self._rendered = ""
        self.update()

    def _svg(self, color: str) -> QSvgRenderer:
        key = f"{self._name}:{color}"
        if self._renderer is None or self._rendered != key:
            self._renderer = QSvgRenderer(icon_svg(self._name, color))
            self._rendered = key
        return self._renderer

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = getattr(palette(), self._color_key)
        self._svg(color).render(painter, QRectF(self.rect()))


ICON_KINDS = {"accent": ("accent", "accent_soft"), "warn": ("warn", "warn_soft"),
              "ok": ("ok", "ok_soft"), "bad": ("bad", "bad_soft"),
              "neutral": ("dim", "chip")}


class IconSquare(QWidget):
    """A rounded soft-color square (or circle) with a themed icon inside."""

    def __init__(self, name: str, kind: str = "accent", size: int = 38,
                 icon_size: int = 20, circle: bool = False) -> None:
        super().__init__()
        self._name = name
        self._kind = kind
        self._icon_size = icon_size
        self._circle = circle
        self.setFixedSize(size, size)
        self._renderer: QSvgRenderer | None = None
        self._rendered = ""

    def set_icon(self, name: str = "", kind: str = "") -> None:
        """Change the symbol or its color in place; a state change on
        the owning card must not rebuild the card."""
        self._name = name or self._name
        self._kind = kind or self._kind
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = palette()
        color_key, soft_key = ICON_KINDS.get(self._kind, ICON_KINDS["accent"])
        color = getattr(colors, color_key)
        radius = self.width() / 2 if self._circle else 9
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.fillPath(path, QColor(getattr(colors, soft_key)))
        key = f"{self._name}:{color}"
        if self._renderer is None or self._rendered != key:
            self._renderer = QSvgRenderer(icon_svg(self._name, color))
            self._rendered = key
        offset = (self.width() - self._icon_size) / 2
        self._renderer.render(painter, QRectF(
            offset, offset, self._icon_size, self._icon_size))


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


class ElidedLabel(QLabel):
    """A one-line label that elides long text in the middle, such as a path."""

    def __init__(self, content: str, name: str = "") -> None:
        super().__init__(content)
        if name:
            self.setObjectName(name)
        self._full = content
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.setToolTip(content)

    def resizeEvent(self, event) -> None:  # noqa: N802
        metrics = QFontMetrics(self.font())
        self.setText(metrics.elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width())))
        super().resizeEvent(event)


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
    """A large choice card: an icon square, a bold title, and a sub line.

    A notice card carries a close mark in its corner. What the close
    means belongs to the owner: some notices go away for now, some go
    away for good.
    """

    clicked = Signal()
    dismissed = Signal()

    def __init__(self, icon_name: str, title: str, sub: str,
                 accent_kind: str = "accent", closable: bool = False) -> None:
        super().__init__()
        self.setObjectName("Choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 13, 16, 13)
        row.setSpacing(14)
        self.icon_square = IconSquare(icon_name, kind=accent_kind)
        row.addWidget(self.icon_square)
        column = QVBoxLayout()
        column.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("ChoiceTitle")
        # User content lands here too — a long issue title or explain
        # goal wraps instead of widening the page.
        self.title_label.setWordWrap(True)
        self.sub_label = QLabel(sub)
        self.sub_label.setObjectName("ChoiceSub")
        self.sub_label.setWordWrap(True)
        column.addWidget(self.title_label)
        column.addWidget(self.sub_label)
        row.addLayout(column, 1)
        self.close_button: QPushButton | None = None
        if closable:
            self.close_button = QPushButton("✕")
            self.close_button.setObjectName("CardClose")
            self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.close_button.setFixedSize(22, 22)
            self.close_button.clicked.connect(self.dismissed.emit)
            row.addWidget(self.close_button,
                          alignment=Qt.AlignmentFlag.AlignTop)

    def set_texts(self, title: str, sub: str) -> None:
        self.title_label.setText(title)
        self.sub_label.setText(sub)

    def set_active(self, active: bool) -> None:
        """The accent border marks the step whose turn it is."""
        self.setProperty("active", "true" if active else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # The close mark is its own control. A press on it must not
        # also open the card behind it.
        if (self.close_button is not None
                and self.close_button.geometry().contains(
                    event.position().toPoint())):
            super().mouseReleaseEvent(event)
            return
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
        kind = "zip" if name.lower().endswith(".zip") else "file"
        row.addWidget(Icon(kind, 15))
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
    """A wrapping row of removable file chips.

    A large set — the include-code sweep can add hundreds — shows only
    the first chips plus one "+N more" chip that expands the rest
    (FR-P10). A fresh set_files starts folded again.
    """

    removed = Signal(int)
    VISIBLE_LIMIT = 12

    def __init__(self) -> None:
        super().__init__()
        self._flow = FlowLayout()
        self.setLayout(self._flow)
        self._names: list[str] = []
        self._removable = True
        self._expanded = False

    def set_files(self, names: list[str], removable: bool = True) -> None:
        self._names = list(names)
        self._removable = removable
        self._expanded = False
        self._render()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._render()

    def _render(self) -> None:
        from maintain.ui.strings import text
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        hidden = len(self._names) - self.VISIBLE_LIMIT
        folded = not self._expanded and hidden > 1
        shown = self._names[:self.VISIBLE_LIMIT] if folded else self._names
        for index, name in enumerate(shown):
            chip = Chip(name, removable=self._removable)
            chip.removed.connect(lambda i=index: self.removed.emit(i))
            self._flow.addWidget(chip)
        if folded:
            more = QPushButton(text("chips.more", count=hidden))
        elif self._expanded:
            more = QPushButton(text("chips.fewer"))
        else:
            more = None
        if more is not None:
            more.setObjectName("Ghost")
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.clicked.connect(self._toggle)
            self._flow.addWidget(more)
        self.setVisible(bool(self._names))
        self.updateGeometry()


class DropZone(QFrame):
    """A file drop target with a click alternative. Emits local file paths."""

    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self, main: str, sub: str = "", slim: bool = False) -> None:
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        column = QVBoxLayout(self)
        pad = 10 if slim else 22
        column.setContentsMargins(16, pad, 16, pad)
        column.setSpacing(3)
        if not slim:
            icon_row = QHBoxLayout()
            icon_row.addStretch(1)
            icon_row.addWidget(Icon("download", 22))
            icon_row.addStretch(1)
            column.addLayout(icon_row)
            column.addSpacing(4)
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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)


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
        self.icon = Icon("package", 40)
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


class ToastStack(QWidget):
    """Floating toast chips above the foot bar. At most two at a time."""

    SHOW_MILLISECONDS = 4000

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(6)
        self.column.addStretch(1)

    def push(self, message: str) -> None:
        while self.count() >= 2:
            self._remove(self._chips()[0])
        chip = QFrame(self)
        chip.setObjectName("Toast")
        row = QHBoxLayout(chip)
        row.setContentsMargins(14, 8, 14, 8)
        text_label = QLabel(message)
        text_label.setObjectName("ToastText")
        # Mirror the QLabel#ToastText style in the widget font, so the
        # size math in reposition() measures what actually renders.
        font = text_label.font()
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        text_label.setFont(font)
        text_label.setWordWrap(True)
        row.addWidget(text_label)
        self.column.addWidget(chip, 0, Qt.AlignmentFlag.AlignHCenter)
        QTimer.singleShot(self.SHOW_MILLISECONDS,
                          lambda: self._remove(chip))
        self.show()
        self.reposition()
        QTimer.singleShot(0, self.reposition)

    def _chips(self) -> list[QFrame]:
        return [self.column.itemAt(index).widget()
                for index in range(self.column.count())
                if isinstance(self.column.itemAt(index).widget(), QFrame)]

    def count(self) -> int:
        return len(self._chips())

    def _remove(self, chip: QFrame | None) -> None:
        # The eviction in push() can delete a chip before its own
        # dismissal timer fires; a dead C++ object must stay untouched.
        from shiboken6 import isValid
        if chip is not None and isValid(chip) and chip.parent() is not None:
            chip.setParent(None)
            chip.deleteLater()
        self.reposition()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(440, parent.width() - 40)
        # Height must follow the chosen width: a wrapped message needs
        # more room than the natural-width hint, or its last line
        # clips. Layout hints lag the wrap, so measure the text.
        chips = self._chips()
        height = max(0, len(chips) - 1) * self.column.spacing()
        for chip in chips:
            label = chip.findChild(QLabel)
            if label is None:
                continue
            label.ensurePolished()   # the styled font, not the default
            metrics = label.fontMetrics()
            flags = int(Qt.TextFlag.TextWordWrap)
            natural = max(metrics.horizontalAdvance(label.text()),
                          metrics.boundingRect(label.text()).width()) + 6
            chip_width = min(natural + 28, width)
            rect = metrics.boundingRect(
                QRect(0, 0, chip_width - 28, 1000), flags, label.text())
            chip.setFixedSize(chip_width, rect.height() + 16)
            height += chip.height()
        self.setGeometry((parent.width() - width) // 2,
                         max(0, parent.height() - height - 56),
                         width, height)
        self.raise_()


def app_icon() -> QIcon:
    """The window and taskbar icon, painted here so nothing ships as a file."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0b6fb8"))
        radius = size * 0.22
        painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
        painter.setPen(QPen(QColor("#ffffff")))
        typeface = QFont("Segoe UI", max(6, int(size * 0.56)))
        typeface.setBold(True)
        painter.setFont(typeface)
        painter.drawText(QRectF(0, -size * 0.04, size, size),
                         Qt.AlignmentFlag.AlignCenter, "M")
        painter.end()
        icon.addPixmap(pixmap)
    return icon


DOTS_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class StepTicker(QWidget):
    """One row per step: the CLI dots animation while it runs, a check
    mark when it completes, a cross when it fails."""

    def __init__(self) -> None:
        super().__init__()
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(4)
        self._active: QLabel | None = None
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._advance)

    def reset(self) -> None:
        self._timer.stop()
        self._active = None
        while self._column.count():
            item = self._column.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def begin(self, message: str) -> None:
        self.complete()
        row = QHBoxLayout()
        row.setSpacing(8)
        glyph = QLabel(DOTS_FRAMES[0])
        glyph.setStyleSheet(
            f"color: {theme.ACTIVE.accent}; font-weight: 700;")
        glyph.setFixedWidth(16)
        step = QLabel(message)
        step.setObjectName("Dim")
        step.setWordWrap(True)
        row.addWidget(glyph, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(step, 1)
        holder = QWidget()
        holder.setLayout(row)
        self._column.addWidget(holder)
        self._active = glyph
        self._frame = 0
        if self.isVisible():
            self._timer.start()

    def complete(self, message: str = "") -> None:
        self._settle("✓", theme.ACTIVE.ok, message)

    def fail(self, message: str = "") -> None:
        self._settle("✗", theme.ACTIVE.bad, message)

    def _settle(self, mark: str, color: str, message: str) -> None:
        if self._active is None:
            return
        self._active.setText(mark)
        self._active.setStyleSheet(f"color: {color}; font-weight: 700;")
        if message:
            row = self._active.parentWidget().layout()
            step = row.itemAt(1).widget()
            if step is not None:
                step.setText(message)
        self._active = None
        self._timer.stop()

    def _advance(self) -> None:
        if self._active is None:
            self._timer.stop()
            return
        self._frame = (self._frame + 1) % len(DOTS_FRAMES)
        self._active.setText(DOTS_FRAMES[self._frame])

    def hideEvent(self, event) -> None:  # noqa: N802
        # Teardown can deliver one last hide after the Python side is
        # cleared; a missing timer must not print a traceback.
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        timer = getattr(self, "_timer", None)
        if getattr(self, "_active", None) is not None and timer is not None:
            timer.start()
        super().showEvent(event)
