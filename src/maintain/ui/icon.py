"""The Maintain mark: the step rail, in the interface's own colours.

One description of the geometry, drawn two ways. The app paints it with
Qt for its window and taskbar entry; scripts/make_icon.py paints the
same numbers with Pillow for the .ico the shortcuts use. Before this
they were different pictures — a blue letter M in the window, a robot
on the desktop — and the taskbar showed neither.

The shape is the stepper that runs down the left of every screen: a
node that is lit, a rail, and behind it a step that passed. Two shapes
and a line is all that survives 16 pixels.
"""

from __future__ import annotations

# The theme's own values. The mark carries its own dark tile so a light
# Windows taskbar cannot swallow it.
GROUND = "#10151f"
EDGE = "#2b3648"
ACCENT = "#4cbdff"
DONE = "#3bd694"
CHECK = "#0d2a1e"

# Everything below is in a 256 unit square and scales from there.
UNIT = 256
CORNER = 58
EDGE_INSET = 4
EDGE_WIDTH = 3
RAIL_X = 128
RAIL_TOP = 78
RAIL_BOTTOM = 178
RAIL_WIDTH = 14
NODE_Y = 72
NODE_R = 30
DONE_Y = 184
DONE_R = 34
CHECK_WIDTH = 11
# The tick inside the lower node, as points in the same square.
CHECK_POINTS = ((113, 184), (124, 196), (144, 172))

# Small sizes are hinted, not merely shrunk. Below EDGE_MINIMUM the
# hairline edge is a smudge; below TICK_MINIMUM the tick fills in and
# reads as a dark blob, so the lower node stays a solid green disc. The
# rail and the nodes grow a little to hold the silhouette together.
EDGE_MINIMUM = 32
TICK_MINIMUM = 24
SMALL = 32
SMALL_RAIL = 1.45
SMALL_NODE = 1.12
SIZES = (16, 24, 32, 48, 64, 128, 256)


def scaled(value: float, size: int) -> float:
    """One geometry number at a given pixel size."""
    return value * size / UNIT


def draw(size: int, *, rect, ellipse, line, polyline) -> None:
    """Paint the mark with whatever primitives the caller has.

    The four callables take pixel coordinates, so a Qt painter and a
    Pillow draw object can both satisfy them and produce the same
    picture.
    """
    def s(value: float) -> float:
        return scaled(value, size)

    small = size < SMALL
    rail_width = RAIL_WIDTH * (SMALL_RAIL if small else 1.0)
    node = NODE_R * (SMALL_NODE if small else 1.0)
    done = DONE_R * (SMALL_NODE if small else 1.0)

    rect(0, 0, size, size, s(CORNER), GROUND)
    if size >= EDGE_MINIMUM:
        rect(s(EDGE_INSET), s(EDGE_INSET),
             size - s(EDGE_INSET) * 2, size - s(EDGE_INSET) * 2,
             s(CORNER - EDGE_INSET), None, EDGE, s(EDGE_WIDTH))
    line(s(RAIL_X), s(RAIL_TOP), s(RAIL_X), s(RAIL_BOTTOM),
         ACCENT, s(rail_width))
    ellipse(s(RAIL_X), s(NODE_Y), s(node), ACCENT)
    ellipse(s(RAIL_X), s(DONE_Y), s(done), DONE)
    if size >= TICK_MINIMUM:
        polyline([(s(x), s(y)) for x, y in CHECK_POINTS], CHECK,
                 s(CHECK_WIDTH))


def qt_icon():
    """The QIcon the window and the taskbar use."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    icon = QIcon()
    for size in SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        def rect(x, y, width, height, radius, fill,
                 stroke=None, stroke_width=0.0, _p=painter):
            _p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
            if stroke:
                pen = QPen(QColor(stroke))
                pen.setWidthF(max(1.0, stroke_width))
                _p.setPen(pen)
            else:
                _p.setPen(Qt.PenStyle.NoPen)
            _p.drawRoundedRect(QRectF(x, y, width, height), radius, radius)

        def ellipse(cx, cy, radius, fill, _p=painter):
            _p.setPen(Qt.PenStyle.NoPen)
            _p.setBrush(QColor(fill))
            _p.drawEllipse(QPointF(cx, cy), radius, radius)

        def line(x1, y1, x2, y2, colour, width, _p=painter):
            pen = QPen(QColor(colour))
            pen.setWidthF(max(1.0, width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            _p.setPen(pen)
            _p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        def polyline(points, colour, width, _p=painter):
            pen = QPen(QColor(colour))
            pen.setWidthF(max(1.0, width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            _p.setPen(pen)
            _p.setBrush(Qt.BrushStyle.NoBrush)
            for first, second in zip(points, points[1:], strict=False):
                _p.drawLine(QPointF(*first), QPointF(*second))

        draw(size, rect=rect, ellipse=ellipse, line=line, polyline=polyline)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
