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

## Mutation check

`mutation_check.py` is a standalone advisory tool for any Maintain-managed
repository — useful after a `maintain harden` task, where coverage alone
cannot tell you whether the new assertions actually pin behaviour:

```sh
cd /path/to/project
python3 /path/to/experiments/mutation_check.py            # uses test_command
python3 /path/to/experiments/mutation_check.py --gate     # uses harden_command
```

Targets default to the non-test files of completed Maintain tasks (the same
derivation `maintain harden` uses); pass file paths to override. Mutations
are applied to a throwaway copy, never the working tree, so an interrupted
run cannot strand a mutation in your source. Docstring lines are excluded
because mutating prose produces equivalent mutants. Exit status is 0 when
every sampled mutant is caught, 1 when any survives, 2 on a usage error —
so it can be chained into `harden_command` once you trust its results.

Run it with the project's environment active: it executes the repository's
own configured command, so `python3` must resolve to the interpreter that
has the test dependencies installed.

Some survivors are equivalent mutants (a change with no observable effect).
Read each before treating it as a missing test.

## Prompt experiment harness

Scenario cells, sample counts, and grading parameters live in the manifest
that `build_fixtures.py` emits; results and decisions to date are in
`RESULTS.md`. When adding a template variant, generate the A package with
the real tool and derive B by a targeted, asserted string edit so the two
differ by exactly the change under test.
