# Manim scene pitfalls

Rules from faults this project has seen. Obey each rule.

1. Text wider than its card. Cause: `Text()` does not wrap. Rule: after
   you group text with a card, add the guard:
   `if body.width > box.width - 0.35: body.scale_to_fit_width(box.width - 0.35)`.
2. Text leaves the frame. Cause: absolute shifts near the edge. Rule:
   keep every object inside x -6.9 to 6.9 and y -3.85 to 3.85. Place
   text with `to_edge` or `next_to`, not with raw coordinates.
3. Text too fast to read. Cause: short waits after text appears. Rule:
   keep each text on screen for three seconds or more. Twenty
   characters each second is the top limit.
4. Two leads on screen at once. Cause: a new lead fades in before the
   old lead fades out. Rule: fade the old text out in the same play
   call, or before it.
5. LaTeX use. Cause: `MathTex` and `Tex` need LaTeX, which is absent.
   Rule: use `Text()` only.
6. Copy that is not simplified English. Rule: short sentences, active
   voice, one idea per sentence, no metaphor. Quote code output
   verbatim and mark it with `output:`.
