# EXPLAIN-TASK

## Goal

Explain the issue-capture behavior of the Maintain tool as one short
Manim animation. The audience is a developer who has not read the
source. Duration: 20 to 30 seconds.

## What the animation must explain

- The problem: machine findings arrive again and again. Copies must
  not pile up.
- The input: one finding (kind, file, code snippet).
- The transformation: a fingerprint made from content, not from line
  numbers.
- The four outcomes: new record; update; drop (a dismissal persists);
  reopen (a regression returns).
- The output at delivery: linked records close as Fixed, except records
  the final review still cites.
- The invariant: one finding, one record.

Do not animate source code line by line. Animate relationships and
state changes.

## Output contract

Return one complete Python file in one fenced code block. Nothing else
in the block. Name the scene class clearly.

- Use Manim Community 0.20.1.
- Use no external images, LaTeX, voice, plugins, network resources, or
  additional files.
- The scene renders without user input.
- Use no secrets and no environment paths.
- Show the explained module path in the animation.
- End with the main invariant.

Ground every claim in CODEBASE.md. Do not invent behavior. If the
supplied code is insufficient, return a short list of missing files
instead of a scene.
