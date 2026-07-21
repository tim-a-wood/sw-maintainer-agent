"""A restrained, responsive terminal presentation for Maintain."""

from __future__ import annotations

import os
import shutil
import sys
import time
from contextlib import contextmanager
from typing import Iterable

from rich import box
from rich.console import Console
from rich.panel import Panel
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


class Presenter:
    def __init__(self, stream=None, animate: bool = True, width: int | None = None,
                 no_color: bool = False) -> None:
        file = stream or sys.stdout
        terminal_width = width or shutil.get_terminal_size((100, 24)).columns
        self.width = max(48, min(88, terminal_width))
        colors_disabled = no_color or os.environ.get("NO_COLOR") is not None
        is_tty = bool(getattr(file, "isatty", lambda: False)())
        self.animate = animate and is_tty and not colors_disabled
        self.console = Console(
            file=file,
            width=self.width,
            force_terminal=is_tty and not colors_disabled,
            no_color=colors_disabled,
            highlight=False,
            soft_wrap=False,
            theme=THEME,
        )
        self._elapsed: dict[str, float] = {}

    def brand(self, project: str = "", provider: str = "") -> None:
        self.console.print()
        content = Table.grid(expand=True)
        content.add_column(ratio=1)
        content.add_column(justify="right", no_wrap=True)
        product = Text()
        product.append((project or "PROJECT").upper(), style="brand")
        product.append("  /  ", style="muted")
        product.append("MAINTAIN", style="accent")
        assistant = Text(f" {provider.upper()} ", style="bold #082F49 on #38BDF8") if provider else Text()
        content.add_row(product, assistant)
        content.add_row(Text("From request to reviewed, tested code", style="muted"), Text())
        self.console.print(Panel(content, box=box.ROUNDED, border_style="accent", padding=(1, 2)))

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

    def home(self, project: str = "", provider: str = "") -> None:
        self.brand(project, provider)
        self.console.print()
        self.console.print("START NEW WORK", style="muted")
        self.console.print()
        primary = Table.grid(expand=True, padding=(0, 1))
        primary.add_column(ratio=1)
        primary.add_column(ratio=1)
        primary.add_row(
            self._action_card("1", "BUILD A FEATURE", "Add or change product behavior"),
            self._action_card("2", "FIX AN ISSUE", "Find and correct a problem"),
        )
        self.console.print(primary)
        self.console.print()
        self.console.print("SAVED WORK", style="muted")
        self.console.print()
        secondary = Text("  ")
        secondary.append_text(self._key("3"))
        secondary.append("  Continue a run", style="label")
        secondary.append("      ")
        secondary.append_text(self._key("4"))
        secondary.append("  View recent runs", style="label")
        self.console.print(secondary)
        quit_line = Text("  ")
        quit_line.append_text(self._key("q", quiet=True))
        quit_line.append("  Quit", style="muted")
        self.console.print(quit_line)
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
            table = Table.grid(padding=(0, 2))
            table.add_column(width=12, style="muted", no_wrap=True)
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

    def saved_runs(self, rows: Iterable[dict[str, str]]) -> None:
        items = list(rows)
        self.section("SAVED WORK", "Recent maintenance runs",
                     "Choose Continue a run from the main menu to resume one.")
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
            heading = Text("●  ", style=state_style)
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

    @staticmethod
    def _action_card(key: str, title: str, description: str) -> Panel:
        content = Text()
        content.append(f" {key} ", style="bold #082F49 on #38BDF8")
        content.append(f"  {title}\n", style="brand")
        content.append(f"    {description}", style="muted")
        return Panel(content, box=box.ROUNDED, border_style="line", padding=(1, 1))


class QuietPresenter:
    def header(self, title: str) -> None:
        pass

    def complete(self, label: str, message: str) -> None:
        pass

    def failed(self, label: str, message: str) -> None:
        pass

    @contextmanager
    def progress(self, label: str, message: str):
        yield
