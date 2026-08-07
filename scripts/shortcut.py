"""Write a Windows shortcut (.lnk) with nothing but the standard library.

Every other way to make a shortcut runs something the machine may
refuse. WScript.Shell needs Windows Script Host; the COM route through
PowerShell needs a PowerShell that will run an unsigned script. On a
managed machine both can be closed, and then the installer fails at the
last step with the app already installed and no way to start it.

The file format is documented (MS-SHLLINK), and the part a shortcut
needs is small: a header, a LinkInfo block naming the target, and a few
strings. Writing the bytes here keeps the installer to Python, and lets
a test read the shortcut back on any machine.
"""

from __future__ import annotations

import struct
from pathlib import Path

# ShellLinkHeader
HEADER_SIZE = 0x0000004C
LINK_CLSID = bytes.fromhex("01140200000000000000000000000046")

# LinkFlags
HAS_LINK_INFO = 0x00000002
HAS_NAME = 0x00000004
HAS_WORKING_DIR = 0x00000010
HAS_ARGUMENTS = 0x00000020
HAS_ICON_LOCATION = 0x00000040
IS_UNICODE = 0x00000080

FILE_ATTRIBUTE_NORMAL = 0x00000080
SHOW_NORMAL = 1
DRIVE_FIXED = 3


def _string_data(value: str) -> bytes:
    """One StringData block: a character count, then UTF-16, no null."""
    encoded = value.encode("utf-16-le")
    return struct.pack("<H", len(value)) + encoded


def _link_info(target: str) -> bytes:
    """The block that names the target on a local drive."""
    local_base = target.encode("utf-8", "replace")
    # VolumeID: size, drive type, serial, label offset, empty label.
    volume_id = struct.pack("<IIII", 0x00000011, DRIVE_FIXED, 0, 0x00000010)
    volume_id += b"\x00"
    header_size = 0x1C
    volume_id_offset = header_size
    local_base_path_offset = volume_id_offset + len(volume_id)
    common_path_suffix_offset = local_base_path_offset + len(local_base) + 1
    size = common_path_suffix_offset + 1
    return (struct.pack("<IIIIIII", size, header_size, 0x00000001,
                        volume_id_offset, local_base_path_offset, 0,
                        common_path_suffix_offset)
            + volume_id + local_base + b"\x00" + b"\x00")


def shortcut_bytes(target: str, *, working_dir: str = "", arguments: str = "",
                   icon: str = "", description: str = "") -> bytes:
    """The complete .lnk file for one target."""
    flags = HAS_LINK_INFO | IS_UNICODE
    if description:
        flags |= HAS_NAME
    if working_dir:
        flags |= HAS_WORKING_DIR
    if arguments:
        flags |= HAS_ARGUMENTS
    if icon:
        flags |= HAS_ICON_LOCATION

    header = struct.pack(
        "<I16sIIQQQIIIHHII",
        HEADER_SIZE, LINK_CLSID, flags, FILE_ATTRIBUTE_NORMAL,
        0, 0, 0,            # creation, access, write times: unset
        0,                  # file size: unset
        0,                  # icon index
        SHOW_NORMAL,
        0,                  # hot key
        0, 0, 0,            # reserved
    )
    body = _link_info(target)
    if description:
        body += _string_data(description)
    if working_dir:
        body += _string_data(working_dir)
    if arguments:
        body += _string_data(arguments)
    if icon:
        body += _string_data(icon)
    # A terminal block of zero size closes the file.
    return header + body + struct.pack("<I", 0)


def write_shortcut(path: Path, target: str, *, working_dir: str = "",
                   arguments: str = "", icon: str = "",
                   description: str = "") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(shortcut_bytes(
        target, working_dir=working_dir, arguments=arguments, icon=icon,
        description=description))
    return path


def read_shortcut(path: Path) -> dict:
    """Read back what write_shortcut wrote. For tests, and for a person
    checking an install without Windows."""
    raw = Path(path).read_bytes()
    (header_size, clsid, flags) = struct.unpack("<I16sI", raw[:24])
    if header_size != HEADER_SIZE or clsid != LINK_CLSID:
        raise ValueError("This is not a Windows shortcut.")
    offset = HEADER_SIZE
    result: dict = {"flags": flags, "target": "", "strings": []}
    if flags & HAS_LINK_INFO:
        size, _head, _info_flags, _volume, base_offset = struct.unpack(
            "<IIIII", raw[offset:offset + 20])
        start = offset + base_offset
        end = raw.index(b"\x00", start)
        result["target"] = raw[start:end].decode("utf-8", "replace")
        offset += size
    while offset + 2 <= len(raw):
        (count,) = struct.unpack("<H", raw[offset:offset + 2])
        if count == 0:
            break
        start = offset + 2
        result["strings"].append(
            raw[start:start + count * 2].decode("utf-16-le"))
        offset = start + count * 2
    return result
