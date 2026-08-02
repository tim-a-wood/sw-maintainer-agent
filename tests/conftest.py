"""Suite-wide hygiene.

Every UI test builds real MainWindows; leaving them alive until
interpreter exit made PySide6 crash with an access violation during
shutdown on Windows (exit 0xC0000005 after "300 passed"). Each test
now reaps its top-level widgets so the interpreter exits clean.
"""

from __future__ import annotations

import os
import sys

import pytest

_EXIT_STATUS = 0


def pytest_sessionfinish(session, exitstatus):
    global _EXIT_STATUS
    _EXIT_STATUS = int(exitstatus)


def pytest_unconfigure(config):
    """On Windows, PySide6 crashes with an access violation inside the
    interpreter's own shutdown (after the summary prints), turning an
    all-passed run into exit 0xC0000005. Once pytest has finished and
    reported, leave with the true status before that teardown runs."""
    if os.name != "nt":
        return
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    if QApplication.instance() is None:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_EXIT_STATUS)


@pytest.fixture(autouse=True)
def _reap_qt_windows():
    yield
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    application = QApplication.instance()
    if application is None:
        return
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()
    application.processEvents()
