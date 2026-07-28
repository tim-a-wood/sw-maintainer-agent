# Global prompt template

This is the starter content for `GLOBAL.md`. The tool copies it into a new
project. The user edits it in Settings. The tool puts the edited file into
every package, next to `TASK.md`. Replace each bracketed item. Keep the
section names.

---

# Project ground rules

Read this file first. These rules apply to every task in this project. If a
task conflicts with these rules, follow these rules and say so in your reply.

## Project goal

[One or two sentences. What the software must do, and for whom.]

## Scope limits

- Work only on the files that the task authorizes.
- Make the smallest change that satisfies the task.
- Do not add a dependency. If a dependency is necessary, stop and say why.
- Do not restructure code that the task does not name.
- Do not add options, layers, or abstractions for possible future needs.

## Standards

- [Named style guide, for example: PEP 8, MISRA C, company standard X.]
- [Test rule, for example: each change includes a unit test.]
- [Documentation rule, for example: update the manual for user-visible changes.]

## Definition of done

- The change satisfies the task's `done_when` items.
- The change passes the verification named in the task.
- The reply follows the output contract in `TASK.md` exactly.
