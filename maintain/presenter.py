"""Terminal presentation for Maintain.

The theme, wordmark and robot are carried over from the 0.9 interface so the
tool still looks like itself; everything else is scoped to this workflow.

Styling is applied by message prefix, so callers keep printing plain strings
and the text stays byte-identical when colour is unavailable — piping to a
file or a non-terminal gives exactly the same characters, which matters
because Maintain's own output is quoted back into handoff packages.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional

from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "brand": "bold #F8FAFC",
        "accent": "bold #4BF77D",
        "muted": "#94A3B8",
        "success": "bold #34D399",
        "warning": "bold #FBBF24",
        "danger": "bold #FB7185",
        "label": "bold #E2E8F0",
        "line": "#334155",
    }
)

ROBOT = (
    "  G     G  ",
    "   G   G   ",
    "   GGGGG   ",
    "  GDDDDDG  ",
    "  GDWDWDG  ",
    "  GDDDDDG  ",
    "   GGGGG   ",
    "  GGGGGGG  ",
    " GG GGG GG ",
    "   G   G   ",
    "  GG   GG  ",
)

ROBOT_PALETTE = {"G": "#4BF77D", "D": "#123B25", "W": "#EAFFF0"}

# The four phases a task moves through, for the progress trail.
PHASES = ("SCOPE", "IMPLEMENT", "TEST", "REVIEW")


class Presenter:
    def __init__(self, stream=None, width: Optional[int] = None,
                 no_color: bool = False, max_width: int = 96) -> None:
        file = stream or sys.stdout
        is_tty = bool(getattr(file, "isatty", lambda: False)())
        colors_disabled = no_color or os.environ.get("NO_COLOR") is not None or not is_tty
        if width:
            self.width = width
        elif is_tty:
            terminal = shutil.get_terminal_size((100, 24)).columns
            self.width = max(48, min(max_width, terminal))
        else:
            # Not a terminal: a fixed, sane width so rules and tables stay
            # readable. Message lines are printed with soft wrapping, so they
            # are never broken regardless of this value.
            self.width = 88
        self.console = Console(
            file=file,
            width=self.width,
            force_terminal=is_tty and not colors_disabled,
            no_color=colors_disabled,
            highlight=False,
            # Wrap tidily on a real terminal; never touch captured output.
            soft_wrap=not is_tty,
            theme=THEME,
        )
        self.plain = colors_disabled
        self.interactive = is_tty

    # -- primitives ---------------------------------------------------

    def line(self, message: str = "") -> None:
        """Print one message, styled by its prefix.

        Call sites pass plain strings; the prefix decides the colour, so no
        markup ever appears in the text itself.
        """
        if not message:
            self.console.print()
            return
        stripped = message.lstrip()
        indent = " " * (len(message) - len(stripped))
        style, marker = self._classify(stripped)
        if self.plain or not marker:
            self.console.print(Text(message, style=style or ""))
            return
        text = Text(indent)
        text.append(marker, style=style)
        text.append(stripped[len(marker):] if stripped.startswith(marker) else stripped)
        self.console.print(text)

    @staticmethod
    def _classify(text: str):
        """Return (style, leading marker to colour) for a message."""
        for prefix, style in (
            ("Error:", "danger"),
            ("Next:", "accent"),
            ("warning:", "warning"),
            ("note:", "muted"),
        ):
            if text.startswith(prefix):
                return style, prefix
        if text.startswith("Tests: PASSED") or text.startswith("Review verdict: APPROVE"):
            return "success", ""
        if text.startswith("Tests: FAILED"):
            return "danger", ""
        if text.startswith(
            ("Tests: NOT_CONFIGURED", "Review verdict: CHANGES_REQUIRED",
             "Review verdict: RESCOPE")
        ):
            return "warning", ""
        if text.startswith(("Package:", "Patch:", "Full output:", "Created task:", "Task:")):
            return "label", ""
        return "", ""

    def rule(self) -> None:
        self.console.print(Rule(style="line"))

    def ask(self, label: str, default: str = "") -> str:
        prompt = Text(label, style="label")
        if default:
            prompt.append(f"  [{default}]", style="muted")
        prompt.append("  ❯ ", style="accent")
        self.console.print(prompt, end="")
        try:
            return input("").strip() or default
        except EOFError:
            return default

    # -- brand --------------------------------------------------------

    def brand(self, project: str = "", branch: str = "", version: str = "") -> None:
        self.console.print()
        if self.width < 62:
            title = Text("◆  ", style="accent")
            title.append("{ MAINTAIN }", style="brand")
            if version:
                title.append(f"  v{version}", style="muted")
            self.console.print(title)
            if project:
                self.console.print(Text(project, style="muted"))
            self.rule()
            return
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=13)
        grid.add_column(ratio=1)
        details = Text()
        details.append("CHATBOT MAINTENANCE WORKFLOW\n", style="muted")
        details.append("{ MAINTAIN }", style="brand")
        if version:
            details.append(f"  v{version}", style="muted")
        details.append("\n")
        details.append("SCOPE  >  IMPLEMENT  >  TEST  >  REVIEW\n", style="accent")
        context = "  •  ".join(item for item in (project, branch) if item)
        details.append(context or "No repository", style="label" if project else "warning")
        grid.add_row(self._robot(), details)
        self.console.print(grid)
        self.rule()

    @staticmethod
    def _robot() -> Text:
        art = Text()
        rows = list(ROBOT)
        if len(rows) % 2:
            rows.append(" " * len(rows[0]))
        for index in range(0, len(rows), 2):
            for top, bottom in zip(rows[index], rows[index + 1]):
                if top == bottom == " ":
                    art.append(" ")
                elif bottom == " ":
                    art.append("▀", style=ROBOT_PALETTE[top])
                elif top == " ":
                    art.append("▄", style=ROBOT_PALETTE[bottom])
                else:
                    art.append("▀", style=f"{ROBOT_PALETTE[top]} on {ROBOT_PALETTE[bottom]}")
            if index < len(rows) - 2:
                art.append("\n")
        return art

    # -- workflow trail -----------------------------------------------

    def trail(self, phase: str, done: tuple = (), note: str = "") -> None:
        """Show the four workflow phases with the current one marked."""
        text = Text("  ")
        for index, name in enumerate(PHASES):
            if index:
                text.append(" ── ", style="line")
            if name == phase:
                text.append("● ", style="accent")
                text.append(name, style="brand")
            elif name in done:
                text.append("✓ ", style="success")
                text.append(name, style="muted")
            else:
                text.append("  ")
                text.append(name, style="line")
        self.console.print(text)
        if note:
            self.console.print(Text(f"  {note}", style="muted"))

    def field(self, label: str, value: str, style: str = "label") -> None:
        text = Text(f"  {label:<20}", style="muted")
        text.append(value, style=style)
        self.console.print(text)

    def heading(self, title: str) -> None:
        self.console.print(Text(f"  {title}", style="brand"))

    def menu(self, key: str, title: str, detail: str = "") -> None:
        text = Text("  ")
        text.append(f"{key:<3}", style="accent")
        text.append(f"{title:<26}", style="label")
        if detail:
            text.append(detail, style="muted")
        self.console.print(text)
