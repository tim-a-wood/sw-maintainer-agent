# Maintain

A small local command-line tool that coordinates a controlled
software-maintenance workflow across separate chatbot conversations.

Maintain generates one Markdown handoff file for each workflow stage, captures
the chatbot response from the clipboard, validates implementation patches,
applies them through Git, runs local tests, and supports bounded correction
and rescope loops.

Maintain is **not** an AI agent and does not communicate with a chatbot
automatically. You upload each package to a chatbot yourself (Microsoft 365
Copilot, or any chatbot that accepts Markdown file uploads and returns text),
copy the reply, and let Maintain do the bookkeeping, validation, and Git work.

```text
Scope
  ↓
Implement
  ↓
Apply and test
  ↓
Review
  ├── Approve → Complete
  ├── Changes required → Fix → Test → Review
  └── Rescope → Revise scope → Continue
```

> This branch (`simple-maintain`) is a deliberate restart. The previous
> browser-automation agent (v0.9) remains on `main`. The MVP here follows the
> "Maintain MVP" PRD in [docs/maintain-mvp-prd.md](docs/maintain-mvp-prd.md):
> local execution, file-based state, manual chatbot handoffs, and nothing
> more.

## Requirements

- Python 3.9+
- Git
- [Repomix](https://github.com/yamadashy/repomix) on PATH
  (`npm install -g repomix`)
- A clipboard mechanism: `pyperclip` (installed automatically), or the
  platform tools `pbpaste` / `wl-paste` / `xclip` / `xsel` /
  `powershell.exe Get-Clipboard`, or the `MAINTAIN_CLIPBOARD_CMD` override
  described below

## Install

You run `maintain` **inside the project you are maintaining**, not inside
this repository, so the command has to be on your PATH.

### Windows: double-click

```text
install-or-update-windows.cmd
```

It checks Python and Git, pulls this clone up to date when the working tree
is clean, installs Maintain into a private environment under
`%LOCALAPPDATA%\Programs\Maintain`, adds that folder to your user PATH, and
creates or refreshes the desktop and Start Menu shortcuts with the Maintain
icon. **Run the same file again to update** — it replaces the runtime and
repoints the existing shortcut, so there is nothing to clean up first.

It also takes ownership of the `maintain` command: any older copy found on
your PATH — such as a 0.9 `pip install` in your Python's `Scripts` folder —
is uninstalled, and the install folder is moved to the front of your user
PATH. A `maintain` it cannot identify as this tool is reported and left
alone.

It installs [Repomix](https://github.com/yamadashy/repomix) too, which
Maintain shells out to for every handoff package. That needs Node.js: if
Node is missing the installer says so and finishes, and you can install Node
then run the installer again.

To remove the command and its shortcuts, double-click
`uninstall-windows.cmd`. Task history stays with each project, under its
`.maintain` folder.

### macOS and Linux: one script

```sh
./scripts/install-unix.sh
```

Installs into `~/.local/share/maintain`, links the command into
`~/.local/bin`, and installs Repomix when Node.js is available. Re-run it to
update.

### Manual alternatives

<details>
<summary>pipx or a virtual environment</summary>

#### pipx, editable

[pipx](https://pipx.pypa.io) keeps the tool in its own environment but puts
the command on your PATH. `--editable` means a `git pull` updates the
installed command immediately, with no reinstall step.

```sh
git clone -b simple-maintain https://github.com/tim-a-wood/sw-maintainer-agent.git
cd sw-maintainer-agent
pipx install --editable .
pipx ensurepath          # once, then reopen the terminal
maintain --version
```

On Windows, use `py -3 -m pip install --user pipx` first if you do not have
pipx, then the same commands.

#### A virtual environment

```sh
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e .
maintain --version
```

The command then lives at `.venv/bin/maintain` (Windows:
`.venv\Scripts\maintain.exe`). To use it in other projects without
activating the environment each time, add that directory to your PATH — or
use pipx above, which does it for you.

#### Without installing anything

```sh
python3 /path/to/sw-maintainer-agent/maintain/maintain.py --help
```

This works from any directory, but you must install `rich` and `pyperclip`
yourself.

</details>

## Update

On Windows, double-click `install-or-update-windows.cmd` again. On macOS or
Linux, run `./scripts/install-unix.sh` again. Both pull this clone up to
date first (when the working tree is clean), reinstall the runtime, and
leave your shortcuts pointing at the new version.

### Updating a manual install

```sh
cd /path/to/sw-maintainer-agent
git pull
maintain --version
```

With an editable install (`pipx install --editable .` or `pip install -e .`)
that is the whole update: the pulled code is what runs. Reinstall only if a
release changes dependencies:

```sh
pipx reinstall maintain          # or: python -m pip install -e .
```

If you installed **without** `--editable`, every update needs
`python -m pip install .` again after pulling.

To check the tool and its prerequisites are all present:

```sh
maintain --version
git --version
repomix --version
```

## Commands

| Command | Responsibility |
| --- | --- |
| `maintain` | Show where the task stands and run the next step |
| `maintain init` | Create Maintain configuration and project context |
| `maintain new "<request>"` | Create a task and generate its scope package |
| `maintain harden ["notes"]` | Create a test-hardening task for completed work |
| `maintain paste` | Store the chatbot's reply from the clipboard |
| `maintain next` | Generate the next appropriate handoff package |
| `maintain apply` | Validate, confirm, apply and test a patch |
| `maintain status` | Display current task state and next action |

That is the whole interface. `maintain next` always knows what comes next;
`maintain status` always tells you where you are.

Running `maintain` with no arguments opens the home screen.

Outside a repository it asks which project to open. On the first launch
there is nothing to list, so it offers to **link** a repository you already
have or **create** a new one — a folder, a Git repository, a README, a
`.gitignore` and a first commit. Later launches list every project you have
configured, most recently opened first, each with its branch and what its
task is waiting for. Pick a number to open it.

Inside a repository it skips the picker and works on that repository,
remembering it for next time.

Resuming is automatic: if the package for the current stage is not on disk
— for example the first attempt failed because Repomix was missing — it is
rebuilt when you open the project, rather than pointing you at a file that
was never written.

Once a project is open the home screen shows its task, a progress trail
across scope, implement, test and review, the current stage and test result,
the file to upload next, and the single next action — press Enter to run it.
Choose `p` to switch project.

The list of projects is stored per user in `~/.maintain/projects.json`
(Windows: `%USERPROFILE%\.maintain\projects.json`), never inside the
repositories being maintained. Forgetting a project only removes it from
that list; the folder is untouched.

`capture`, `continue` and `start` are accepted as aliases for `paste`,
`next` and `new`, so earlier spellings keep working.

Colour is used when the output is a terminal and dropped otherwise, so piped
or redirected output stays byte-identical plain text — which matters because
Maintain's own output gets quoted back into handoff packages.

## Walkthrough

### 1. Initialise the repository you want to maintain

```sh
cd /path/to/your/project      # must be a Git repository with at least one commit
maintain init
```

This creates:

```text
.maintain/
├── config.json          # test command, round limit, extra Repomix arguments
├── project-context.md   # your project rules — included in every package
├── current-task
└── tasks/
```

Edit `project-context.md` to describe project-specific rules and conventions,
and set the test command in `config.json`:

```json
{
  "test_command": "pytest",
  "maximum_rounds": 3,
  "repomix_args": []
}
```

If no test command is configured, test results are recorded as
`NOT_CONFIGURED` and you may still proceed to review after a warning.
`repomix_args` is passed through to every Repomix invocation (for example
`["--compress"]` to shrink large repositories).

Consider adding `.maintain/` to the project's `.gitignore`.

### 2. Create a task

```sh
maintain new "Correct the greeting shown at startup"
```

```text
Created task: 20260728-001
Package: .maintain/tasks/20260728-001/exports/maintain-20260728-001-scope.md
Next: Upload the package to a fresh chatbot conversation, copy the complete reply, then run `maintain paste`.
```

The scope package contains the scoping role instructions, your request, the
project context, the repository structure, broad Repomix repository context,
and the exact response format the chatbot must follow.

### 3. Capture each response

Upload the package to a **fresh** chatbot conversation, copy the chatbot's
entire reply to the clipboard, then:

```sh
maintain paste
```

Maintain knows from the workflow state which response type to expect, stores
the raw reply before parsing it, validates the required markers and sections,
and tells you what to do next. Invalid replies are preserved next to the
expected response file (`*-rejected-NN.md`) so nothing is ever lost.

### 4. Advance and apply

```sh
maintain next     # generates the next package (implementation, fix, review, rescope)
maintain apply    # after an implementation/fix response: validate, confirm, apply, test
```

`maintain apply`:

1. Extracts the single unified Git diff from the captured response.
2. Identifies the modified files.
3. Rejects files outside the approved scope (from the scope response).
4. Runs `git apply --check`.
5. Shows a patch summary and asks for confirmation.
6. Applies the patch to the working tree (never commits).
7. Runs the configured test command and records the complete output.

Failed validation, failed tests, and `CHANGES_REQUIRED` reviews all lead to
the same place: `maintain next` generates a correction package containing the
failure details, the cumulative diff, and the current file contents. After
three implementation rounds (configurable), Maintain stops for manual
intervention instead of looping forever.

### 5. Review and finish

When tests pass, `maintain next` generates an independent review package.
Upload it to a **fresh** conversation — the reviewer must not share context
with the implementer. The reviewer returns `VERDICT: APPROVE`,
`VERDICT: CHANGES_REQUIRED`, or `VERDICT: RESCOPE`.

On `APPROVE`, `maintain next` closes the task after a final confirmation. The
applied changes remain **uncommitted** in your working tree: reviewing,
committing, and pushing stay in your hands.

## Rescoping

When the problem is the task definition rather than the implementation —
required files outside the approved list, contradictory acceptance criteria,
the change cannot work within the approved architecture — either role can
escalate:

- the implementer replies `STATUS: RESCOPE_REQUIRED`
- the reviewer replies `VERDICT: RESCOPE`

`maintain next` then generates a rescope package containing the original
scope, the discovered issue, and the current cumulative diff. The rescope
response carries `STATUS: RESCOPED` plus an `EXISTING_WORK:
RETAIN|PARTIAL|DISCARD` recommendation. Maintain shows the recommendation and
asks whether to keep the current working-tree changes or reset them to the
task's base commit — the decision is always yours. Implementation then
continues under the revised scope with a fresh round allowance. A scope
revision does not consume an implementation retry.

## Hardening

After work has passed review (and you have committed it), `maintain harden`
starts a test-hardening task over everything your completed tasks touched:

```sh
maintain harden "focus on the parser error paths"   # notes are optional
```

The hardening scope conversation is asked for a plan that reaches 100% line
and branch coverage of the target files, makes assertions
mutation-resistant (exact values, boundaries, error paths), and adds
end-to-end tests of the real entry points — while changing no behaviour:
the allowed files should be tests, e2e tests, and coverage configuration
only, and the anti-gaming rules (no logic pragmas, no assertion-free tests)
ride in the acceptance criteria so the independent reviewer enforces them.

During a hardening task, `maintain apply` runs `harden_command` from
`config.json` instead of `test_command` — configure it as your strict gate,
for example:

```json
{
  "harden_command": "python3 -m pytest -q --cov=mymodule --cov-branch --cov-fail-under=100"
}
```

Everything else is the ordinary workflow: correction rounds when the gate
fails, independent review when it passes, your confirmation to close.
Coverage proves the tests execute the code; it cannot prove they pin its
behaviour. `experiments/mutation_check.py` measures that directly — it
applies small source mutations to a throwaway copy of the repository and
reports which ones the suite fails to catch:

```sh
python3 experiments/mutation_check.py --gate
```

Run it after a hardening task to check the result, or chain it into
`harden_command` once you trust its results in your project. External
mutation tools (for example `mutmut`) work the same way when their exit
codes are reliable in your environment.

## Task storage

Everything is preserved under `.maintain/tasks/<task-id>/` — packages,
responses, patches, test results, and review results. Previous responses and
attempts are never overwritten; superseded or rejected captures are kept
under numbered names.

```text
.maintain/tasks/20260728-001/
├── state.json
├── request.md
├── scope/            # package.md + response.md
├── rounds/01/        # implementation-package.md, implementation-response.md,
│                     # implementation.patch, test-results.txt,
│                     # review-package.md, review-response.md
├── rounds/02/        # fix-package.md, ...
├── rescopes/01/      # package.md + response.md
└── exports/          # the upload files: maintain-<id>-scope.md, -implement-01.md, ...
```

Upload the files under `exports/`; the rest is the audit trail.

## Clipboard configuration

Capture tries, in order:

1. `MAINTAIN_CLIPBOARD_CMD` — a shell command that prints the clipboard to
   stdout. Useful under WSL:
   `export MAINTAIN_CLIPBOARD_CMD='powershell.exe -NoProfile -Command Get-Clipboard -Raw'`
2. `pyperclip`
3. `pbpaste` (macOS), `wl-paste`, `xclip`, `xsel` (Linux), or
   `powershell.exe Get-Clipboard` (Windows/WSL)

Captured text is normalised to LF line endings before storage and parsing.

## Failure behaviour

Maintain fails safely with a clear next manual action when: there is no Git
repository, Repomix is missing, the clipboard is empty, a response marker is
missing, an implementation contains zero or multiple diff blocks, a patch
touches unapproved files, `git apply --check` fails, the working tree no
longer matches the recorded base commit, the test command cannot run, or the
correction-round limit is reached. Captured chatbot responses and repository
changes are never silently discarded.

## Development

```sh
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/
```

The test suite drives the real CLI end-to-end in temporary Git repositories,
with a stub Repomix and a simulated clipboard.
