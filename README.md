# Software Maintainer Agent

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-38BDF8)](https://www.python.org/)
[![CLI](https://img.shields.io/badge/interface-terminal-34D399)](#start-a-workflow)

```text
  █       █       SOFTWARE MAINTENANCE AGENT
   █     █        { MAINTAIN }
   █████        PLAN > BUILD > REVIEW > VERIFY
  █░░░░░█
  █░■░■░█
  █░░░░░█
   █████
  ███████
 ██ ███ ██
   █   █
  ██   ██
```

Software Maintainer Agent is a focused command-line workflow for changing an
existing software project with an AI assistant. It can add or change a feature,
fix an issue, review the implementation, run local checks, and retain an audit
record of every package and response.

The installed command is `maintain`.

## What it does

- Selects only the code needed for the requested change.
- Creates explicit, self-contained task packages using no more than three files.
- Combines focused source files into one indexed `CODEBASE.md` document.
- Receives implementation files in a checked, repository-ready ZIP.
- Uses an isolated Git worktree and branch for every run.
- Implements, independently reviews, and locally verifies each task.
- Requires human acceptance before it creates a commit or updates the project branch.
- Saves requests, responses, diffs, checks, decisions, and delivery evidence.
- Resumes saved work after an interruption or required human action.

Microsoft 365 Copilot and ChatGPT integrations use visible browser automation.
They do not use Copilot or ChatGPT APIs. Browser credentials remain in the local
browser profile.

## Requirements

- Python 3.11 or later
- Git
- A Git repository for the project you want to maintain
- Chromium when using a browser provider
- The selected assistant account or local assistant CLI

## Install

Clone this repository and create a dedicated virtual environment:

```sh
git clone https://github.com/tim-a-wood/sw-maintainer-agent.git
cd sw-maintainer-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[browser]'
python -m playwright install chromium
maintain --version
```

If you do not need ChatGPT or Microsoft 365 Copilot browser automation, install
without the browser extra:

```sh
python -m pip install -e .
```

On Windows, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## Set up an existing project

The tool keeps its source and audit data separate from the project that it
maintains. The simplest setup is interactive:

```sh
maintain --repo /path/to/project
```

Choose `S`, select Microsoft 365 Copilot, ChatGPT, or Codex, and follow the
on-screen sign-in step. Browser setup retrieves the models available to the
signed-in account and asks which model to use. The setup creates `.maintain.json` in the target project.
It does not add that file to Git, and the file can remain untracked.

Use the steps below when you want to inspect or customize setup before the first
run.

### 1. Prepare the project

The target must have at least one Git commit. Commit or stash existing source
changes first. Maintain permits its own `.maintain.json` to remain untracked.

```sh
git -C /path/to/project status
```

### 2. Create the project configuration

Choose one provider preset:

```sh
# ChatGPT through browser automation
maintain init /path/to/project --provider chatgpt-browser

# Microsoft 365 Copilot through browser automation
maintain init /path/to/project --provider m365-browser

# A locally installed Codex CLI
maintain init /path/to/project --provider codex

# File packages exchanged by another automated process
maintain init /path/to/project --provider file-exchange
```

This shows the proposed `.maintain.json` and asks before writing it. Add `--yes`
for non-interactive setup after you have inspected the proposal.

### 3. Review the detected project settings

Confirm these items in `.maintain.json`:

- `project.name` and `project.default_branch`
- `repository.source_roots`, `repository.test_roots`, and excluded paths
- generated and protected paths
- the provider assigned to each workflow role
- local verification commands and time limits; add a focused pre-fix reproduction command when one exists
- change limits, deletion rules, and dependency-change policy

Browser workspace, tenant, and identity checks are optional. Configure them only
when your organization needs an explicit visible-page check and you have stable
selectors for those labels. Do not put passwords, tokens, cookies, or API keys
in the configuration.

### 4. Validate the setup

```sh
maintain --repo /path/to/project config validate
maintain --repo /path/to/project provider list
maintain --repo /path/to/project doctor
```

For the first browser-provider setup, open the controlled browser and sign in:

```sh
maintain --repo /path/to/project provider login chatgpt
# or
maintain --repo /path/to/project provider login m365
```

Then verify the selected profile:

```sh
maintain --repo /path/to/project provider doctor chatgpt
```

Use the profile name shown by `maintain provider list` if you renamed it.

To view, refresh, or change the models for a browser profile:

```sh
maintain --repo /path/to/project provider models chatgpt
maintain --repo /path/to/project provider models chatgpt --refresh
maintain --repo /path/to/project provider model chatgpt
maintain --repo /path/to/project provider model chatgpt "MODEL NAME"
```

The interactive home screen provides the same controls under `Assistant settings`.
Maintain saves the preference in `.maintain.json` and selects it at the start of
every browser conversation. Refresh the list when the account's available models change.
For Microsoft 365 Copilot, refresh enables the new design when its opt-in toggle
is present and opens the nested `More` or `GPT models` list. This includes named
GPT models as well as the default Copilot response modes.

## Start a workflow

Open the interactive interface:

```sh
maintain --repo /path/to/project
```

Or start directly:

```sh
maintain --repo /path/to/project feature "Add the requested behavior"
maintain --repo /path/to/project issue "Describe the observed problem"
```

Maintain prepares an isolated workspace, selects focused context, creates a
change plan, implements it, reviews it in a separate conversation, and runs the
configured checks. When all gates pass, it asks whether to inspect, revise, save,
or accept the change. The guided default creates the verified commit and
fast-forwards the source branch if the source checkout is still unchanged.

For each browser exchange, Maintain uploads `TASK.md`, `CODEBASE.md`, and
`MANIFEST.json`. The codebase document contains only the selected context, with
an index and exact repository paths. Implementation returns a ZIP containing
complete changed files at those paths. Maintain validates and applies the ZIP in
the isolated worktree before review and local verification.

After attaching a package, Maintain confirms that all three files are visible and
that upload activity has stopped. It requires that state to remain stable, checks
that Send is enabled, clicks Send, and confirms the outgoing request. This avoids
submitting a request while Copilot is still attaching files.

```sh
maintain --repo /path/to/project diff RUN_ID
maintain --repo /path/to/project accept RUN_ID
maintain --repo /path/to/project deliver RUN_ID
```

Acceptance approves the verified tree. Delivery creates the commit only after
that approval. Direct commands keep the commit on the maintenance branch unless
you explicitly add `--current-branch BRANCH --confirm-current-branch` to
`maintain deliver`.

## Resume and inspect work

```sh
maintain --repo /path/to/project runs
maintain --repo /path/to/project status RUN_ID
maintain --repo /path/to/project resume RUN_ID
maintain --repo /path/to/project evidence RUN_ID
maintain --repo /path/to/project audit verify RUN_ID
maintain --repo /path/to/project audit export RUN_ID --output run-audit.zip
```

Audit data is stored outside the target repository under `~/.maintain/runs` by
default.

## Operating boundaries

- The primary project tree is not edited before review, local verification, and acceptance.
- The assistant receives focused code packages, not unrestricted repository access.
- Implementation and review use separate conversations.
- Local verification results are authoritative.
- Assistants are instructed not to use internet tools for task execution.
- MATLAB checks run only on the trusted local machine. If MATLAB is required but
  unavailable, the run pauses.
- Expected failures produce a clear action instead of a Python stack trace.
- Machine-readable output is available with `--json`.

Run `maintain --help` for the full command list.
