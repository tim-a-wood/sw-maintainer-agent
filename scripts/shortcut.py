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
HAS_RELATIVE_PATH = 0x00000008
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


# FR-V17: the identity that ties the running window to this shortcut.
#
# An app that calls SetCurrentProcessExplicitAppUserModelID gets its own
# taskbar button, and Windows then looks for a shortcut carrying the
# same id to take the icon and the name from. With no such shortcut it
# falls back to the icon of the process image — which for a console
# script is the Python launcher stub. Setting the id in the app alone
# therefore does not fix the Python icon; it has to be on both sides.
PROPERTY_STORE_SIGNATURE = 0xA0000009
PROPERTY_STORAGE_VERSION = 0x53505331
# System.AppUserModel.ID: {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, PID 5.
APP_USER_MODEL_FORMAT_ID = (
    struct.pack("<IHH", 0x9F4C2855, 0x9F79, 0x4B39)
    + bytes.fromhex("A8D0E1D42DE1D5F3"))
APP_USER_MODEL_PID = 5
VT_LPWSTR = 0x001F


def _property_store(app_id: str) -> bytes:
    """The extra-data block that names the application on a shortcut."""
    text = app_id.encode("utf-16-le") + b"\x00\x00"
    # TypedPropertyValue: type, padding, character count with the null.
    typed = struct.pack("<HHI", VT_LPWSTR, 0, len(text) // 2) + text
    value = struct.pack("<IIB", 0, APP_USER_MODEL_PID, 0) + typed
    value = struct.pack("<I", len(value)) + value[4:]

    storage = (struct.pack("<I", PROPERTY_STORAGE_VERSION)
               + APP_USER_MODEL_FORMAT_ID + value + struct.pack("<I", 0))
    storage = struct.pack("<I", len(storage) + 4) + storage

    payload = storage + struct.pack("<I", 0)
    block = struct.pack("<II", len(payload) + 8, PROPERTY_STORE_SIGNATURE)
    return block + payload


def shortcut_bytes(target: str, *, working_dir: str = "", arguments: str = "",
                   icon: str = "", description: str = "",
                   app_id: str = "") -> bytes:
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
    if app_id:
        body += _property_store(app_id)
    # A terminal block of zero size closes the file.
    return header + body + struct.pack("<I", 0)


def write_shortcut(path: Path, target: str, *, working_dir: str = "",
                   arguments: str = "", icon: str = "",
                   description: str = "", app_id: str = "") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(shortcut_bytes(
        target, working_dir=working_dir, arguments=arguments, icon=icon,
        description=description, app_id=app_id))
    return path


def read_app_id(path: Path) -> str:
    """The application id stamped on a shortcut, or empty."""
    raw = Path(path).read_bytes()
    marker = struct.pack("<I", PROPERTY_STORE_SIGNATURE)
    at = raw.find(marker)
    if at < 0:
        return ""
    at = raw.find(APP_USER_MODEL_FORMAT_ID, at)
    if at < 0:
        return ""
    cursor = at + len(APP_USER_MODEL_FORMAT_ID)
    size, pid, _reserved = struct.unpack("<IIB", raw[cursor:cursor + 9])
    if pid != APP_USER_MODEL_PID:
        return ""
    kind, _pad, count = struct.unpack("<HHI", raw[cursor + 9:cursor + 17])
    if kind != VT_LPWSTR:
        return ""
    start = cursor + 17
    return raw[start:start + count * 2].decode("utf-16-le").rstrip("\x00")


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
    # Read exactly the string blocks the flags declare, in the order the
    # format fixes. Reading until a zero count would run on into the
    # extra-data blocks that follow and try to decode them as text.
    for flag in (HAS_NAME, HAS_RELATIVE_PATH, HAS_WORKING_DIR,
                 HAS_ARGUMENTS, HAS_ICON_LOCATION):
        if not flags & flag or offset + 2 > len(raw):
            continue
        (count,) = struct.unpack("<H", raw[offset:offset + 2])
        start = offset + 2
        result["strings"].append(
            raw[start:start + count * 2].decode("utf-16-le"))
        offset = start + count * 2
    return result
