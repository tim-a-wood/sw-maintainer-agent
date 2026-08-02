"""FR-U1: the release check — git tags in, one newer version out."""

from __future__ import annotations

import subprocess
from pathlib import Path

from maintain.update_check import (newest_tag, update_available,
                                   version_tuple)


def test_version_tuple_orders_numerically():
    assert version_tuple("v0.9.10") > version_tuple("v0.9.9")
    assert version_tuple("v1.0") > version_tuple("v0.99.99")
    assert version_tuple("0.9.1") == version_tuple("v0.9.1")
    assert version_tuple("nonsense") == (0,)


def test_newest_tag_reads_ls_remote_output():
    output = "\n".join([
        "1111111111111111111111111111111111111111\trefs/tags/v0.9.1",
        "2222222222222222222222222222222222222222\trefs/tags/v0.9.10",
        "3333333333333333333333333333333333333333\trefs/tags/v0.9.10^{}",
        "4444444444444444444444444444444444444444\trefs/tags/v0.9.9",
        "5555555555555555555555555555555555555555\trefs/tags/nightly",
        "6666666666666666666666666666666666666666\trefs/tags/v1.0.0-rc1",
        "not a tag line",
    ])
    # The peeled ^{} twin names the same release; nightly and rc tags
    # are not releases.
    assert newest_tag(output) == "v0.9.10"
    assert newest_tag("") == ""


def _release_repository(tmp_path: Path, *tags: str) -> Path:
    repository = tmp_path / "releases"
    repository.mkdir()

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repository), *args],
                       check=True, capture_output=True)

    _git("init", "-b", "main")
    _git("config", "user.name", "T")
    _git("config", "user.email", "t@example.invalid")
    (repository / "readme.md").write_text("releases\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-m", "initial")
    for tag in tags:
        _git("tag", "-a", tag, "-m", tag)
    return repository


def test_update_available_against_a_real_repository(tmp_path):
    repository = _release_repository(tmp_path, "v0.9.1", "v9.9.9")
    assert update_available("0.9.1",
                            repository=str(repository)) == "v9.9.9"
    # The running build is already the newest: no update.
    assert update_available("9.9.9", repository=str(repository)) == ""
    # No reachable repository: quiet, never an error.
    assert update_available("0.9.1",
                            repository=str(tmp_path / "absent")) == ""
