"""Find a newer released version of the tool itself.

A release is a git tag in the form v1.2.3 on the tool's own
repository. The check runs git ls-remote with the person's saved
credentials, so it works on a private repository with no API token
and no rate limit. Every failure is quiet: no network, no git, or
no tags all mean "no update found".
"""

from __future__ import annotations

import re
import subprocess

from . import __version__
from .proc import hidden

REPOSITORY_URL = "https://github.com/tim-a-wood/sw-maintainer-agent.git"

_RELEASE_TAG = re.compile(r"v\d+(\.\d+)*")


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(x) for x in numbers[:4]) or (0,)


def newest_tag(ls_remote_output: str) -> str:
    """The highest release tag in git ls-remote --tags output.

    Annotated tags appear twice, once with a ^{} suffix; both name
    the same release. Tags outside the v1.2.3 form are not releases.
    """
    best = ""
    for line in ls_remote_output.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/tags/"):
            continue
        tag = parts[1][len("refs/tags/"):]
        if tag.endswith("^{}"):
            tag = tag[:-3]
        if not _RELEASE_TAG.fullmatch(tag):
            continue
        if not best or version_tuple(tag) > version_tuple(best):
            best = tag
    return best


def update_available(installed: str = "", *,
                     repository: str = REPOSITORY_URL,
                     timeout: int = 30) -> str:
    """The newest release tag ahead of this build, or empty."""
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--tags", repository],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False, **hidden())
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    tag = newest_tag(completed.stdout)
    current = installed or __version__
    if tag and version_tuple(tag) > version_tuple(current):
        return tag
    return ""
