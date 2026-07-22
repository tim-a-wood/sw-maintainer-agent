"""A compact, responsive terminal presentation for Maintain."""

from __future__ import annotations

import os
import shutil
import sys
import time
from contextlib import contextmanager
from typing import Iterable

from rich import box
from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


THEME = Theme({
    "brand": "bold #F8FAFC",
    "accent": "bold #38BDF8",
    "muted": "#94A3B8",
    "success": "bold #34D399",
    "warning": "bold #FBBF24",
    "danger": "bold #FB7185",
    "label": "bold #E2E8F0",
    "line": "#334155",
})


ROBOT = (
    "  G     G  ",
    "   G   G   ",
    "   GGGGG   ",
    "  GDDDDDG  ",
    "  GDWDWDG  ",
    "  GDDDDDG  ",
    "   GGRGG   ",
    "  GGGGGGG  ",
    " GG GGG GG ",
    "   G   G   ",
    "  GG   GG  ",
)

ROBOT_PALETTE = {
    "G": "bold #4BF77D",
    "D": "#123B25",
    "W": "bold #EAFFF0",
    "R": "bold #FF654F",
}


class Presenter:
    def __init__(self, stream=None, animate: bool = True, width: int | None = None,
                 no_color: bool = False, max_width: int = 96,
                 force_color: bool = False) -> None:
        file = stream or sys.stdout
        terminal_width = width or shutil.get_terminal_size((100, 24)).columns
        self.width = max(48, min(max_width, terminal_width))
        colors_disabled = no_color or os.environ.get("NO_COLOR") is not None
        is_tty = bool(getattr(file, "isatty", lambda: False)())
        self.animate = animate and is_tty and not colors_disabled
        self.console = Console(
            file=file,
            width=self.width,
            force_terminal=(is_tty or force_color) and not colors_disabled,
            no_color=colors_disabled,
            highlight=False,
            soft_wrap=False,
            theme=THEME,
        )
        self._elapsed: dict[str, float] = {}

    def brand(self, project: str = "", provider: str = "") -> None:
        self.console.print()
        if self.width < 76:
            title = Text("◆  ", style="accent")
            title.append("{ MAINTAIN }", style="brand")
            self.console.print(title)
            context = "  •  ".join(item for item in (project, provider) if item)
            if context:
                self.console.print(context, style="muted")
            self.console.print(Rule(style="line"))
            return
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=22)
        grid.add_column(ratio=1)
        details = Text()
        details.append("SOFTWARE MAINTENANCE AGENT\n", style="muted")
        details.append("{ MAINTAIN }\n", style="bold #F8FAFC")
        details.append("PLAN  >  BUILD  >  REVIEW  >  VERIFY\n", style="accent")
        context = "  •  ".join(item for item in (project or "Project not set up", provider) if item)
        details.append(context, style="label" if project else "warning")
        grid.add_row(self._robot(), details)
        self.console.print(grid)
        self.console.print(Rule(style="line"))

    @staticmethod
    def _robot() -> Text:
        art = Text()
        for row_index, row in enumerate(ROBOT):
            for pixel in row:
                art.append("██" if pixel != " " else "  ",
                           style=ROBOT_PALETTE.get(pixel, ""))
            if row_index < len(ROBOT) - 1:
                art.append("\n")
        return art

    def header(self, title: str) -> None:
        """Compatibility entry point for concise section headers."""
        self.section("MAINTAIN", title)

    def section(self, kicker: str, title: str, detail: str = "") -> None:
        self.console.print()
        self.console.print(kicker.upper(), style="accent")
        self.console.print(title, style="brand")
        if detail:
            self.console.print(detail, style="muted")
        self.console.print(Rule(style="line"))

    def home(self, project: str = "", provider: str = "", saved_count: int = 0,
             configured: bool = True, setup_issue: str = "") -> None:
        self.brand(project, provider)
        if setup_issue:
            self.console.print()
            self.console.print("!  PROJECT SETUP NEEDS ATTENTION", style="warning")
            self.console.print(f"   {setup_issue}", style="muted")
        self.console.print()
        self.console.print("WHAT DO YOU WANT TO DO?", style="brand")
        self.console.print()
        self.menu_line("1", "Build a feature", "Add or change product behavior")
        self.menu_line("2", "Fix an issue", "Find and correct a problem")
        self.console.print()
        count = f"{saved_count} saved" if saved_count else "No saved work"
        self.menu_line("3", "Continue saved work", count)
        self.menu_line("4", "View history", "Runs, results, and status")
        if not configured:
            self.console.print()
            setup_label = "Repair project setup" if setup_issue else "Set up this project"
            self.menu_line("s", setup_label, "Create or upgrade the project configuration")
        self.menu_line("q", "Quit", "", quiet=True)
        self.console.print()

    def run_header(self, mode: str, request: str, project: str = "", provider: str = "") -> None:
        detail = "  •  ".join(item for item in (project, provider, "Evidence saved automatically")
                             if item)
        self.section(f"NEW {mode}", request, detail)
        self.console.print()

    def ask(self, label: str, default: str = "") -> str:
        prompt = Text(label, style="label")
        if default:
            prompt.append(f"  [{default}]", style="muted")
        prompt.append("  ❯ ", style="accent")
        self.console.print(prompt, end="")
        return input("").strip() or default

    def complete(self, label: str, message: str) -> None:
        elapsed = self._elapsed.pop(label, None)
        line = Text("✓  ", style="success")
        line.append(f"{label:<11}", style="label")
        line.append(message)
        if elapsed is not None:
            line.append(f"  {self._duration(elapsed)}", style="muted")
        self.console.print(line)

    def failed(self, label: str, message: str) -> None:
        elapsed = self._elapsed.pop(label, None)
        line = Text("×  ", style="danger")
        line.append(f"{label:<11}", style="label")
        line.append(message)
        if elapsed is not None:
            line.append(f"  {self._duration(elapsed)}", style="muted")
        self.console.print(line)

    @contextmanager
    def progress(self, label: str, message: str):
        started = time.perf_counter()
        text = f"[accent]{label:<11}[/accent]{message}"
        try:
            if self.animate:
                with self.console.status(text, spinner="dots", spinner_style="accent"):
                    yield
            else:
                line = Text("○  ", style="accent")
                line.append(f"{label:<11}", style="label")
                line.append(message)
                self.console.print(line)
                yield
        finally:
            self._elapsed[label] = time.perf_counter() - started

    def outcome(self, label: str, title: str, message: str = "",
                facts: Iterable[tuple[str, str]] = (), actions: Iterable[str] = (),
                tone: str = "accent") -> None:
        self.console.print()
        heading = Text("●  ", style=tone)
        heading.append(label.upper(), style=tone)
        self.console.print(heading)
        self.console.print(f"   {title}", style="brand")
        if message:
            message_table = Table.grid(padding=(0, 1))
            message_table.add_column(width=1)
            message_table.add_column(ratio=1)
            message_table.add_row(Text("│", style=tone), Text(message))
            self.console.print(message_table)
        fact_rows = [(name, value) for name, value in facts if value]
        if fact_rows:
            self.console.print()
            label_width = min(20, max(12, max(len(name) for name, _ in fact_rows)))
            table = Table.grid(padding=(0, 2))
            table.add_column(width=label_width, style="muted", no_wrap=True)
            table.add_column(style="label")
            for name, value in fact_rows:
                table.add_row(name.upper(), value)
            self.console.print(table)
        action_rows = [item for item in actions if item]
        if action_rows:
            self.console.print()
            self.console.print("NEXT", style="muted")
            for item in action_rows:
                line = Text("→  ", style="accent")
                line.append(item)
                self.console.print(line)
        self.console.print(Rule(style="line"))

    def gates(self, values: dict[str, str]) -> None:
        self.console.print()
        self.console.print("EVIDENCE", style="muted")
        table = Table.grid(padding=(0, 2))
        table.add_column(width=3)
        table.add_column(ratio=1)
        table.add_column(no_wrap=True)
        for name, value in values.items():
            if value == "pass":
                icon, style, shown = "✓", "success", "Passed"
            elif value == "not_applicable":
                icon, style, shown = "–", "muted", "Not required"
            else:
                icon, style, shown = "○", "warning", value.replace("_", " ").title()
            table.add_row(Text(icon, style=style), name.replace("_", " ").title(),
                          Text(shown, style=style))
        self.console.print(table)

    def saved_runs(self, rows: Iterable[dict[str, str]], *, selectable: bool = False) -> None:
        items = list(rows)
        self.section(
            "SAVED WORK" if selectable else "HISTORY",
            "Work you can continue" if selectable else "Maintenance run history",
            ("Select a numbered run to continue, or return to the main menu."
             if selectable else "Results and audit status for this project."),
        )
        if not items:
            self.console.print()
            self.console.print("No saved runs.", style="muted")
            return
        self.console.print()
        for item in items:
            state = item["state"]
            shown_state = {
                "awaiting_acceptance": "Review ready",
                "needs_human": "Action needed",
                "needs_human_delivery": "Action needed",
                "tasks_ready": "Plan ready",
            }.get(state, state.replace("_", " ").title())
            if state in {"delivered", "accepted", "awaiting_acceptance"}:
                state_style = "success" if state != "awaiting_acceptance" else "accent"
            elif state in {"needs_human", "needs_human_delivery"}:
                state_style = "warning"
            elif state in {"failed", "cancelled"}:
                state_style = "danger" if state == "failed" else "muted"
            else:
                state_style = "label"
            index = str(item.get("index", ""))
            heading = Text()
            if index:
                heading.append_text(self._key(index))
                heading.append("  ")
            heading.append("●  ", style=state_style)
            heading.append(shown_state.upper(), style=state_style)
            heading.append(f"   {item['mode'].upper()}", style="muted")
            self.console.print(heading)
            self.console.print(f"   {item['request']}", style="label")
            self.console.print(f"   {item['run_id']}", style="muted")
            self.console.print()

    def provider_assignments(self, rows: Iterable[tuple[str, str, str]]) -> None:
        self.section("ASSISTANTS", "Workflow assignments",
                     "Each role uses the configured provider and a separate conversation.")
        self.console.print()
        table = Table(box=box.SIMPLE_HEAD, border_style="line", header_style="muted",
                      pad_edge=False, expand=False)
        table.add_column("ROLE", width=13)
        table.add_column("PROFILE", width=20)
        table.add_column("PROVIDER")
        for role, profile, kind in rows:
            table.add_row(role.title(), profile, kind.replace("_", " ").title())
        self.console.print(table)

    def error(self, message: str, hint: str = "") -> None:
        self.outcome("Stopped", "Maintain could not continue.", message,
                     actions=[hint] if hint else [], tone="danger")

    @staticmethod
    def _duration(seconds: float) -> str:
        return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m {seconds % 60:.0f}s"

    @staticmethod
    def _key(value: str, quiet: bool = False) -> Text:
        return Text(f" {value.upper()} ", style="muted" if quiet else "bold #082F49 on #38BDF8")

    def menu_line(self, key: str, title: str, description: str, quiet: bool = False) -> None:
        line = Text("  ")
        line.append_text(self._key(key, quiet=quiet))
        line.append(f"  {title}", style="label" if not quiet else "muted")
        if description:
            line.append(" " * max(2, 32 - len(title)))
            line.append(description, style="muted")
        self.console.print(line)


class QuietPresenter:
    def run_header(self, *args, **kwargs) -> None:
        pass

    def header(self, title: str) -> None:
        pass

    def outcome(self, *args, **kwargs) -> None:
        pass

    def gates(self, *args, **kwargs) -> None:
        pass

    def provider_assignments(self, *args, **kwargs) -> None:
        pass

    def saved_runs(self, *args, **kwargs) -> None:
        pass

    def complete(self, label: str, message: str) -> None:
        pass

    def failed(self, label: str, message: str) -> None:
        pass

    @contextmanager
    def progress(self, label: str, message: str):
        yield
