"""Entry point for the maintain-ui desktop application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from maintain.config import ProjectConfig, find_config
from maintain.errors import MaintainError
from maintain.repository_memory import load_last_repository, remember_repository


def _pick_repository(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    remembered = load_last_repository()
    if remembered is not None:
        return remembered
    from PySide6.QtWidgets import QFileDialog
    selected = QFileDialog.getExistingDirectory(None, "Open a project folder")
    return Path(selected) if selected else None


def _ensure_config(repository: Path) -> Path:
    existing = find_config(repository)
    if existing is not None:
        return existing
    from PySide6.QtWidgets import QMessageBox
    from .strings import text
    answer = QMessageBox.question(
        None, text("projects.setup.title"),
        f"{repository}\n\n" + text("projects.setup.body"),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    if answer != QMessageBox.StandardButton.Yes:
        raise SystemExit(0)
    from .projects import ensure_config
    return ensure_config(repository)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maintain-ui")
    parser.add_argument("--repo", help="Project repository path")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Maintain")

    from .app import saved_theme
    from .theme import palette_for, qt_palette, stylesheet
    palette = palette_for(saved_theme() == "dark")
    app.setPalette(qt_palette(palette))
    app.setStyleSheet(stylesheet(palette))

    repository = _pick_repository(args.repo)
    if repository is None:
        return 0
    if not (repository / ".git").exists():
        QMessageBox.warning(None, "Maintain",
                            f"This folder is not a Git repository:\n{repository}")
        return 1
    try:
        config_path = _ensure_config(repository)
        config = ProjectConfig.load(config_path)
    except MaintainError as exc:
        QMessageBox.warning(None, "Maintain", str(exc))
        return 1
    remember_repository(repository, config_path=config_path)

    from .app import MainWindow
    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
