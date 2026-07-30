# A known-good example scene

This scene passed the checks and the render. Copy its shape: the
BEATS list, the three zones, the card guard, and the pace. This
example is short on purpose; a real scene needs 30 to 45 seconds
in total.

```python
from manim import (
    config, Scene, Text, RoundedRectangle, VGroup,
    FadeIn, FadeOut,
    UP, DOWN, RIGHT,
)

config.background_color = "#10151f"

INK = "#e6edf6"
DIM = "#9aa9bd"
ACCENT = "#4cbdff"

BEATS = [
    ("src/example.py — the safety checks", 3.5),
    ("The code does two checks in this sequence.", 6.0),
    ("The code accepts this record.", 4.2),
    ("Only correct records go to the plan.", 4.5),
]


def check_card(name, rule):
    box = RoundedRectangle(corner_radius=0.12, width=5.6, height=0.62)
    box.set_stroke(DIM, width=2).set_fill("#1b2230", opacity=1.0)
    head = Text(name, font_size=20, color=ACCENT, weight="BOLD")
    tail = Text(rule, font_size=17, color=DIM)
    body = VGroup(head, tail).arrange(RIGHT, buff=0.28)
    if body.width > box.width - 0.35:
        body.scale_to_fit_width(box.width - 0.35)
    body.move_to(box.get_center())
    return VGroup(box, body)


class ModuleExplainScene(Scene):
    def construct(self):
        # Beat 1 — title band: the module path.
        title = Text("src/example.py — the safety checks", font_size=36,
                     color=INK, weight="BOLD")
        self.play(FadeIn(title, shift=UP * 0.3), run_time=1.0)
        self.wait(2.0)
        self.play(FadeOut(title), run_time=0.5)

        # Beat 2 — title band lead, content zone cards.
        lead = Text("The code does two checks in this sequence.",
                    font_size=25, color=INK)
        lead.to_edge(UP, buff=0.5)
        checks = VGroup(
            check_card("bounds", "0 to 150"),
            check_card("format", "three fields"),
        ).arrange(DOWN, buff=0.17)
        self.play(FadeIn(lead), run_time=0.8)
        self.play(FadeIn(checks, lag_ratio=0.2), run_time=1.2)
        self.wait(4.0)

        # Beat 3 — note band: the outcome.
        note = Text("The code accepts this record.", font_size=22,
                    color=ACCENT)
        note.to_edge(DOWN, buff=0.45)
        self.play(FadeIn(note), run_time=0.6)
        self.wait(3.0)
        self.play(FadeOut(VGroup(lead, checks, note)), run_time=0.6)

        # Beat 4 — the main invariant, alone on screen.
        one = Text("Only correct records go to the plan.", font_size=32,
                   color=INK, weight="BOLD")
        self.play(FadeIn(one, shift=UP * 0.3), run_time=1.0)
        self.wait(3.0)
        self.play(FadeOut(one), run_time=0.5)
```
