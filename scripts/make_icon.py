"""Render assets/maintain.ico.b64 from the app's own icon geometry.

The shortcuts need a .ico file; the window needs a QIcon. Both are
painted from src/maintain/ui/icon.py, so they cannot drift apart —
which they had, a blue letter in the window and a robot on the
desktop.

Each size is drawn at its own scale rather than shrunk from one big
render: a 16 pixel icon made by downsampling a 256 pixel one loses the
rail. Supersampling by four gives clean edges without that loss.

Run it after changing the geometry:

    python scripts/make_icon.py
"""

from __future__ import annotations

import base64
import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from maintain.ui import icon as mark  # noqa: E402

SUPERSAMPLE = 4


def _rounded_rect(draw, box, radius, fill, outline, width):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                           width=max(1, int(round(width))))


def render(size: int) -> Image.Image:
    """One size of the mark, drawn at that size and then smoothed."""
    scale = SUPERSAMPLE
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def rect(x, y, width, height, radius, fill, stroke=None, stroke_width=0.0):
        _rounded_rect(draw,
                      [x * scale, y * scale,
                       (x + width) * scale - 1, (y + height) * scale - 1],
                      radius * scale, fill, stroke, stroke_width * scale)

    def ellipse(cx, cy, radius, fill):
        draw.ellipse([(cx - radius) * scale, (cy - radius) * scale,
                      (cx + radius) * scale, (cy + radius) * scale], fill=fill)

    def line(x1, y1, x2, y2, colour, width):
        draw.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale],
                  fill=colour, width=max(1, int(round(width * scale))),
                  joint="curve")
        # Round caps: Pillow has none, so the ends are capped by hand.
        radius = width * scale / 2
        for x, y in ((x1, y1), (x2, y2)):
            draw.ellipse([x * scale - radius, y * scale - radius,
                          x * scale + radius, y * scale + radius], fill=colour)

    def polyline(points, colour, width):
        stroke = max(1, int(round(width * scale)))
        scaled = [(x * scale, y * scale) for x, y in points]
        draw.line(scaled, fill=colour, width=stroke, joint="curve")
        radius = stroke / 2
        for x, y in scaled:
            draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                         fill=colour)

    mark.draw(size, rect=rect, ellipse=ellipse, line=line, polyline=polyline)
    return canvas.resize((size, size), Image.LANCZOS)


def ico_bytes(images: list[Image.Image]) -> bytes:
    """A multi-size .ico with a PNG for each size.

    Pillow's own ICO writer takes one image and shrinks it. Writing the
    container here keeps each size the one that was drawn for it.
    """
    payloads = []
    for image in images:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payloads.append(buffer.getvalue())

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory = b""
    for image, payload in zip(images, payloads, strict=True):
        width = 0 if image.width >= 256 else image.width
        height = 0 if image.height >= 256 else image.height
        directory += struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32,
                                 len(payload), offset)
        offset += len(payload)
    return header + directory + b"".join(payloads)


def main() -> int:
    images = [render(size) for size in mark.SIZES]
    raw = ico_bytes(images)
    target = ROOT / "assets" / "maintain.ico.b64"
    target.write_text(base64.b64encode(raw).decode("ascii") + "\n",
                      encoding="utf-8")
    print(f"{target.relative_to(ROOT)}: {len(raw):,} bytes, "
          f"{len(images)} sizes {', '.join(str(s) for s in mark.SIZES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
