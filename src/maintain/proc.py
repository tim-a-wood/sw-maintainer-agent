"""Shared subprocess options.

A windowed app on Windows opens a console window for every child
process unless the creation flags say otherwise. Every subprocess call
in the application passes `**hidden()` so git, PowerShell, the checks,
and the render never flash command windows at the person.
"""

from __future__ import annotations

import subprocess


def hidden() -> dict:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}
