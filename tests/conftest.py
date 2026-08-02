"""Suite-wide hygiene.

Every UI test builds real MainWindows; leaving them alive until
interpreter exit made PySide6 crash with an access violation during
shutdown on Windows (exit 0xC0000005 after "300 passed"). Each test
now reaps its top-level widgets so the interpreter exits clean.
"""

from __future__ import annotations

import pytest


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
