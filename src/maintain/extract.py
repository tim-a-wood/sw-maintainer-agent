"""Text extraction from binary reference files, at packet-build time.

An assistant reads text; it cannot decode embedded bytes. So the packet
builder extracts the text here, on this computer, and the one Markdown
packet carries the words instead of the encoding. PDF text comes
through pypdf; Office files are ZIP archives of XML, read with the
standard library alone.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

CHARACTER_LIMIT = 400_000
TRUNCATED_NOTE = "\n\n[The text is truncated at the size limit.]"

_TAG = re.compile(r"<[^>]+>")
_DOCX_TEXT = re.compile(r"<w:t(?: [^>]*)?>(.*?)</w:t>", re.DOTALL)
_DOCX_BREAK = re.compile(r"</w:p>")
_PPTX_TEXT = re.compile(r"<a:t>(.*?)</a:t>", re.DOTALL)
_XLSX_TEXT = re.compile(r"<t(?: [^>]*)?>(.*?)</t>", re.DOTALL)


@dataclass(frozen=True)
class ExtractResult:
    ok: bool
    text: str = ""
    note: str = ""


def _unescape(value: str) -> str:
    return (value.replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'")
            .replace("&amp;", "&"))


def _capped(text: str) -> str:
    text = text.strip()
    if len(text) > CHARACTER_LIMIT:
        return text[:CHARACTER_LIMIT] + TRUNCATED_NOTE
    return text


def _pdf(raw: bytes) -> ExtractResult:
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - the extra may be absent
        return ExtractResult(False, note="pypdf is not installed")
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:  # noqa: BLE001 - a broken file is not an error here
        return ExtractResult(False, note="the file could not be read")
    text = "\n\n".join(part.strip() for part in pages if part.strip())
    if not text.strip():
        return ExtractResult(False, note="no text layer (a scanned file?)")
    return ExtractResult(True, _capped(text))


def _office(raw: bytes, members: str, pattern: re.Pattern,
            paragraph: re.Pattern | None = None) -> ExtractResult:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            parts = []
            for name in sorted(archive.namelist()):
                if not re.fullmatch(members, name):
                    continue
                xml = archive.read(name).decode("utf-8", errors="replace")
                if paragraph is not None:
                    xml = paragraph.sub("\n", xml)
                found = [_unescape(_TAG.sub("", item))
                         for item in pattern.findall(xml)]
                if found:
                    parts.append("".join(found) if paragraph is None
                                 else "".join(found))
    except (zipfile.BadZipFile, OSError, KeyError):
        return ExtractResult(False, note="the file could not be read")
    text = "\n\n".join(part.strip() for part in parts if part.strip())
    if not text.strip():
        return ExtractResult(False, note="no text found")
    return ExtractResult(True, _capped(text))


def _docx(raw: bytes) -> ExtractResult:
    # Paragraph ends become line ends so the text keeps its shape.
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml = archive.read("word/document.xml").decode(
                "utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError, KeyError):
        return ExtractResult(False, note="the file could not be read")
    lines = []
    for block in _DOCX_BREAK.split(xml):
        runs = [_unescape(item) for item in _DOCX_TEXT.findall(block)]
        if runs:
            lines.append("".join(runs))
    text = "\n".join(lines).strip()
    if not text:
        return ExtractResult(False, note="no text found")
    return ExtractResult(True, _capped(text))


EXTRACTORS = {
    ".pdf": _pdf,
    ".docx": _docx,
    ".pptx": lambda raw: _office(raw, r"ppt/slides/slide\d+\.xml",
                                 _PPTX_TEXT),
    ".xlsx": lambda raw: _office(raw, r"xl/sharedStrings\.xml", _XLSX_TEXT),
}


def extract_text(name: str, raw: bytes) -> ExtractResult:
    """The text inside a binary reference file, or why there is none."""
    extractor = EXTRACTORS.get(PurePosixPath(name).suffix.lower())
    if extractor is None:
        return ExtractResult(False, note="not a text-carrying format")
    return extractor(raw)
