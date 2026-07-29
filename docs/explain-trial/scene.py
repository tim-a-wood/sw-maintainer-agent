"""Maintain — how issue capture keeps one record per finding.

Explains: src/maintain/issues.py (fingerprint, capture, close_for_run).
Manim Community 0.20.1. No LaTeX, no external assets, no network.
"""

from manim import (
    config, Scene, Text, RoundedRectangle, VGroup, Arrow, Line,
    FadeIn, FadeOut, Indicate, ReplacementTransform, Create,
    UP, DOWN, LEFT, RIGHT, ORIGIN,
)

config.background_color = "#10151f"

INK = "#e6edf6"
DIM = "#9aa9bd"
ACCENT = "#4cbdff"
OK = "#3bd694"
BAD = "#ff8577"
WARN = "#f0c052"
EDGE = "#32405a"


def card(title, sub, color, width=3.9):
    box = RoundedRectangle(corner_radius=0.12, width=width, height=1.15)
    box.set_stroke(color, width=3).set_fill("#1b2230", opacity=1.0)
    head = Text(title, font_size=24, color=INK, weight="BOLD")
    tail = Text(sub, font_size=19, color=DIM)
    text = VGroup(head, tail).arrange(DOWN, buff=0.12)
    text.move_to(box.get_center())
    return VGroup(box, text)


def chip(label, color):
    box = RoundedRectangle(corner_radius=0.18, width=2.6, height=0.5)
    box.set_stroke(color, width=2).set_fill("#1d3247", opacity=1.0)
    text = Text(label, font_size=19, color=color, weight="BOLD")
    text.move_to(box.get_center())
    return VGroup(box, text)


class IssueCaptureScene(Scene):
    def construct(self):
        self.title_card()
        self.fingerprint_beat()
        self.outcome_beat()
        self.delivery_beat()
        self.invariant_card()

    def title_card(self):
        title = Text("Maintain — issue capture", font_size=44,
                     color=INK, weight="BOLD")
        module = Text("src/maintain/issues.py", font_size=26, color=ACCENT)
        group = VGroup(title, module).arrange(DOWN, buff=0.35)
        self.play(FadeIn(group, shift=UP * 0.3), run_time=1.2)
        self.wait(1.2)
        self.play(FadeOut(group), run_time=0.6)

    def fingerprint_beat(self):
        finding = card("Review finding", "loader.py — bad speed bound", WARN)
        finding.to_edge(LEFT, buff=0.8)
        machine = card("fingerprint()", "kind | file | code", ACCENT)
        machine.move_to(ORIGIN)
        digest = chip("a3f2c1", OK)
        digest.to_edge(RIGHT, buff=1.2)
        arrow1 = Arrow(finding.get_right(), machine.get_left(), buff=0.15,
                       color=DIM, stroke_width=3)
        arrow2 = Arrow(machine.get_right(), digest.get_left(), buff=0.15,
                       color=DIM, stroke_width=3)
        line1 = Text("A finding becomes a content fingerprint.",
                     font_size=26, color=INK)
        line1.to_edge(UP, buff=0.6)
        self.play(FadeIn(line1), FadeIn(finding), run_time=0.9)
        self.play(Create(arrow1), FadeIn(machine), run_time=0.9)
        self.play(Create(arrow2), FadeIn(digest), run_time=0.9)
        self.wait(0.6)

        note = Text("Code identity — not line numbers. Spacing is ignored.",
                    font_size=24, color=DIM)
        note.to_edge(DOWN, buff=0.9)
        snippet_a = Text('return [r for r in records]', font_size=21,
                         color=INK, font="DejaVu Sans Mono")
        snippet_b = Text('return [ r   for r in records ]', font_size=21,
                         color=INK, font="DejaVu Sans Mono")
        pair = VGroup(snippet_a, snippet_b).arrange(DOWN, buff=0.18)
        pair.next_to(note, UP, buff=0.35)
        self.play(FadeIn(note), FadeIn(pair), run_time=0.9)
        self.play(Indicate(digest, color=OK, scale_factor=1.12), run_time=1.0)
        self.wait(0.7)
        self.play(FadeOut(VGroup(line1, finding, machine, arrow1, arrow2,
                                 note, pair)),
                  digest.animate.move_to(UP * 3.35),
                  run_time=0.8)
        self.digest = digest

    def outcome_beat(self):
        lead = Text("The store answers with one of four outcomes.",
                    font_size=26, color=INK)
        lead.next_to(self.digest, DOWN, buff=0.4)
        lead.set_x(0)
        self.play(FadeIn(lead), run_time=0.7)

        new_card = card("New issue", "status: Open", OK)
        upd_card = card("Same fingerprint", "updated — run linked", ACCENT)
        drop_card = card("Closed: not a fault", "dropped — stays closed", BAD)
        reopen_card = card("Closed: fixed", "regression — reopens", WARN)
        grid = VGroup(new_card, upd_card, drop_card, reopen_card)
        grid.arrange_in_grid(rows=2, cols=2, buff=0.5)
        grid.next_to(lead, DOWN, buff=0.5)
        grid.set_x(0)

        self.play(FadeIn(new_card, shift=UP * 0.2), run_time=0.8)
        self.wait(0.4)
        self.play(FadeIn(upd_card, shift=UP * 0.2),
                  Indicate(upd_card, color=ACCENT), run_time=1.1)
        self.wait(0.4)
        self.play(FadeIn(drop_card, shift=UP * 0.2), run_time=0.8)
        shield = Line(drop_card.get_corner(DOWN + LEFT),
                      drop_card.get_corner(UP + RIGHT),
                      color=BAD, stroke_width=5)
        self.play(Create(shield), run_time=0.6)
        self.wait(0.4)
        self.play(FadeIn(reopen_card, shift=UP * 0.2), run_time=0.8)
        reopened = card("Open again", "the fault came back", WARN)
        reopened.move_to(reopen_card.get_center())
        self.play(ReplacementTransform(reopen_card, reopened), run_time=0.9)
        self.wait(0.8)
        self.play(FadeOut(VGroup(lead, new_card, upd_card, drop_card,
                                 shield, reopened, self.digest)),
                  run_time=0.7)

    def delivery_beat(self):
        lead = Text("The run delivers. Linked issues close as Fixed.",
                    font_size=26, color=INK)
        lead.to_edge(UP, buff=0.7)
        fixed_a = card("Bad speed bound", "Fixed", OK)
        fixed_b = card("Check failed", "Fixed", OK)
        cited = card("Style point", "still cited — stays Open", WARN)
        row = VGroup(fixed_a, fixed_b, cited).arrange(RIGHT, buff=0.45)
        row.next_to(lead, DOWN, buff=0.8)
        note = Text("Only points the final review still cites stay open.",
                    font_size=24, color=DIM)
        note.next_to(row, DOWN, buff=0.6)
        self.play(FadeIn(lead), run_time=0.7)
        self.play(FadeIn(fixed_a), FadeIn(fixed_b), run_time=0.9)
        self.play(FadeIn(cited), FadeIn(note),
                  Indicate(cited, color=WARN), run_time=1.2)
        self.wait(0.8)
        self.play(FadeOut(VGroup(lead, row, note)), run_time=0.6)

    def invariant_card(self):
        one = Text("One finding, one record.", font_size=40,
                   color=INK, weight="BOLD")
        two = Text("Dismissals persist. Regressions reopen.",
                   font_size=28, color=ACCENT)
        group = VGroup(one, two).arrange(DOWN, buff=0.4)
        self.play(FadeIn(group, shift=UP * 0.3), run_time=1.0)
        self.wait(1.8)
        self.play(FadeOut(group), run_time=0.6)
