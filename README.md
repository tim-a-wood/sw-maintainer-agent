# Software Maintainer Agent

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-38BDF8)](https://www.python.org/)
[![CLI](https://img.shields.io/badge/interface-terminal-34D399)](#start-a-workflow)

Software Maintainer Agent is a focused command-line workflow for changing an
existing software project with an AI assistant. It can add or change a feature,
fix an issue, review the implementation, run local checks, and retain an audit
record of every package and response.

The installed command is `maintain`.

## What it does

- Selects only the code needed for the requested change.
- Creates explicit, self-contained task packages for the assistant.
- Uses an isolated Git worktree and branch for every run.
- Implements, independently reviews, and locally verifies each task.
- Requires human acceptance before it creates a commit.
- Saves requests, responses, diffs, checks, decisions, and delivery evidence.
- Resumes saved work after an interruption or required human action.

Microsoft 365 Copilot and ChatGPT integrations use visible browser automation.
They do not use Copilot or ChatGPT APIs. Browser credentials remain in the local
browser profile.

## Requirements

- Python 3.11 or later
- Git
- A clean Git repository for the project you want to maintain
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

The tool keeps its own source separate from the project that it maintains. Run
the following commands from this repository's activated virtual environment.

### 1. Prepare the project

The target must be a Git repository with a clean working tree. Commit or stash
existing work first.

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

This creates `.maintain.json` inside the target project. The generated file is a
starting point. Review it before the first run.

### 3. Review the detected project settings

Confirm these items in `.maintain.json`:

- `project.name` and `project.default_branch`
- `repository.source_roots` and `repository.test_roots`
- excluded, generated, and protected paths
- the provider assigned to each workflow role
- local verification commands and time limits
- a reproduction command for the issue workflow
- change limits, deletion rules, and dependency-change policy

For a browser provider, replace the generated setup markers with the exact
signed-in workspace or tenant and identity that the tool must verify. Do not put
passwords, tokens, cookies, or API keys in the configuration.

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

Maintain will prepare the isolated workspace, select focused context, create a
change plan, implement it, review it in a separate conversation, and run the
configured checks. When all gates pass, it pauses for acceptance.

```sh
maintain --repo /path/to/project diff RUN_ID
maintain --repo /path/to/project accept RUN_ID
maintain --repo /path/to/project deliver RUN_ID
```

Acceptance approves the verified tree. Delivery creates the commit only after
that approval.

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

- The primary project tree is not edited during a workflow.
- The assistant receives focused code packages, not unrestricted repository access.
- Implementation and review use separate conversations.
- Local verification results are authoritative.
- Assistants are instructed not to use internet tools for task execution.
- MATLAB checks run only on the trusted local machine. If MATLAB is required but
  unavailable, the run pauses.
- Expected failures produce a clear action instead of a Python stack trace.
- Machine-readable output is available with `--json`.

Run `maintain --help` for the full command list.
