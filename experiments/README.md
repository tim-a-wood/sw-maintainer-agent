# Prompt experiments

A small harness for measuring how well Maintain's handoff packages steer a
chatbot, using the real CLI end to end:

- `build_fixtures.py <work-dir>` creates one Git repository per scenario,
  drives `maintain init/new/capture/next/apply` to the stage under test with
  authored chatbot responses, and writes the generated packages plus a
  grading manifest.
- Each package is then answered by independent, context-isolated chatbot
  conversations (any chatbot works — save each full reply as
  `<work-dir>/replies/<cell>/<n>.md`).
- `grade.py <work-dir>` scores every reply mechanically: Maintain's own
  marker/section/diff parsers, raw `git apply --recount --check` in a
  pristine repository copy, real application, and real test runs.

Scenario cells, sample counts, and grading parameters live in the manifest
that `build_fixtures.py` emits; results and decisions to date are in
`RESULTS.md`. When adding a template variant, generate the A package with
the real tool and derive B by a targeted, asserted string edit so the two
differ by exactly the change under test.
