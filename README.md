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

Software Maintainer Agent is a focused command-line workflow for creating or
changing a software project with an AI assistant. It can add or change a
feature, fix an issue, review the implementation, run local checks, and retain
an audit record of every package and response.

The installed command is `maintain`.

## What it does

- Selects only the code needed for the requested change.
- Creates explicit, self-contained three-file task packages.
- Combines focused source files into one indexed `CODEBASE.md` document.
- Can provide one local file or HTTPS reference directly to Microsoft 365 Copilot.
- Remembers recent projects, switches between them, and creates blank projects.
- Receives complete implementation files inline and stages them in a checked,
  repository-ready ZIP.
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
- A Git repository for an existing project, or a folder in which to create one
- Chromium when using a browser provider
- The selected assistant account or local assistant CLI

## Install

### Windows

Install [Python 3.11 or later](https://www.python.org/downloads/windows/) and
[Git for Windows](https://git-scm.com/download/win) first. The installer checks
both prerequisites before changing the private Maintain runtime.

Download and extract this repository, then double-click:

```text
install-or-update-windows.cmd
```

The script resolves the current `main` branch to one immutable Git commit,
checks out that exact commit, and verifies that the installed private runtime
reports the version declared by that source. It will stop with an error instead
of silently reinstalling an older extracted copy when the online update is
unavailable. The installer also installs Chromium, adds `maintain` to the user
PATH, and creates desktop and Start Menu shortcuts with the Maintain robot icon.
It asks Windows to pin the shortcut to the taskbar. Some company policies block
automatic taskbar pinning; if that happens, the installer gives the single
manual step required.

Run the same script whenever you want to update. To remove the CLI and its
shortcuts, double-click:

```text
uninstall-windows.cmd
```

Uninstall keeps run history, settings, browser sign-in data, and runtime logs under
`%USERPROFILE%\.maintain`.

If installation or startup fails, the shortcut keeps the error visible. The
installer and runtime also keep logs at:

```text
%LOCALAPPDATA%\Programs\Maintain\install.log
%USERPROFILE%\.maintain\logs\maintain-runtime.log
```

Running the installer again repairs an unusable private Python environment. It
does not remove run history, settings, or browser profiles.

For a shareable support bundle, double-click `collect-maintain-diagnostics.cmd`.
It creates `maintain-diagnostics.zip` in the current folder with Windows and
PowerShell versions, command resolution, private-runtime versions, installed-file
metadata, the install log, and up to 20 control-only browser failure or transport
JSON files. It does not collect process command lines, prompts, responses,
screenshots, `run.json`, or `audit.jsonl`. The bundle can still contain local
paths, browser control labels, attachment filenames, and model names, so review
it before sharing.

### Manual installation

On macOS or Linux, clone this repository and create a dedicated virtual
environment:

```sh
git clone https://github.com/tim-a-wood/sw-maintainer-agent.git
cd sw-maintainer-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[browser]'
python -m playwright install chromium
maintain --version
```

If you do not need ChatGPT or Microsoft 365 Copilot browser automation, install
without the browser extra:

```sh
python -m pip install .
```

On Windows PowerShell, use the Windows Python launcher and activation command:

```powershell
git clone https://github.com/tim-a-wood/sw-maintainer-agent.git
cd sw-maintainer-agent
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install '.[browser]'
python -m playwright install chromium
maintain --version
```

Follow-up commands displayed by Maintain on Windows use PowerShell syntax.

## Maintain Simple UI (desktop)

Maintain Simple UI is a small desktop application over the same engine. It
guides one person through the loop — plan, build, review, test, save — with a
manual packet exchange: the tool builds one ZIP packet per step, the person
moves it to Microsoft 365 Copilot and brings the reply back. No browser
automation runs in this mode.

- Start it with `maintain-ui` (installed by the `ui` extra:
  `python -m pip install '.[ui]'`). The Windows installer also creates the
  **Maintain UI** shortcut.
- Set up a project for it with `maintain init /path/to/project --provider
  manual-ui`, or let the UI create the configuration on first open.
- The primary transport is one Markdown file: the tool compiles the whole
  packet — prompts, code, extracted reference text — into a single `.md`
  and puts it in the clipboard the moment it is ready. One paste attaches
  it in Copilot. Drag and drop and export stay available; a one-ZIP
  fallback style remains in Settings.
- Every run records an iteration timeline. You can go back to any anchor
  iteration; later iterations stay in the history as superseded.
- Boilerplate prompts and standards documents are configurable per project
  and per task type in Settings. Attachments can be added to every packet.
- Try one packet without the UI: `maintain package "Describe the change"`.

The requirements are in `docs/simple-ui-prd.md`; the interactive mockup in
`docs/mockup/simple-ui-mockup.html` shows the intended screens.

## Open, switch, and create projects

Launch without a path to reopen the most recently used project:

```sh
maintain
```

The home screen shows the absolute project path and current branch. Choose `P`
to switch between recent projects or browse for another Git repository, and
choose `N` to create a blank project. Missing projects remain visible until you
forget them, so a moved or deleted folder is not silently replaced.

The same controls are available as direct commands:

```sh
maintain project list
maintain project open 2
maintain project open "Project name"
maintain project add /path/to/existing/repository
maintain project forget /path/to/old/repository
maintain project new /path/to/new-project --provider m365-browser --name "New Project"
```

`project new` requires a destination that does not exist. It creates the folder,
initializes a `main` branch, adds a README and `.gitignore`, creates a validated
`.maintain.json`, and makes the initial commit. The Microsoft 365 Copilot browser
profile is shared across projects, so an existing local sign-in can be reused.

Recent projects and each project's optional default reference are stored in the
per-user Maintain settings file, not in the repository.

## Set up an existing project

The tool keeps its source and audit data separate from the project that it
maintains. The simplest setup is interactive:

```sh
maintain --repo /path/to/project
```

You only need `--repo` when choosing or changing projects. After a successful
use, `maintain` opens the active recent project automatically. On the first
launch, Maintain offers to browse for an existing repository or create a new one.

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

### 4. Validate the project configuration

```sh
maintain --repo /path/to/project config validate
maintain --repo /path/to/project provider list
```

For the first browser-provider setup, open the controlled browser and sign in
before running browser readiness checks:

```sh
maintain --repo /path/to/project provider login chatgpt
# or
maintain --repo /path/to/project provider login m365
```

Refresh the available models, select one, check the visible controls, and only
then run the local preflight:

```sh
maintain --repo /path/to/project provider model chatgpt --refresh
maintain --repo /path/to/project provider check chatgpt
maintain --repo /path/to/project doctor
```

`doctor` checks the repository, provider preflight, configured command
executables, storage, and audit path. It does not run the project's verification
commands. Its report calls out when the detected coverage is only
`git diff --check` and no project test command was found.

Use the profile name shown by `maintain provider list` if you renamed it.
The compatibility check finds the message, attachment, Send, and model controls
without attaching files or sending a message. It reports the detected layout and
stops safely if the page is unfamiliar. Initial model setup and each model
refresh run the same compatibility inspection automatically.
On ChatGPT, it distinguishes the general attachment input from photo-only inputs.
It briefly enters and clears an unsent draft when ChatGPT hides Send until text
is present.

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
GPT models as well as the default Copilot response modes. Discovery follows up to
three nested menu levels and saves the observed menu paths in the browser evidence
directory.

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

### Give Microsoft 365 Copilot a reference

For a Microsoft 365 Copilot project, add one local file or HTTPS link:

```sh
maintain feature "Implement the approved design" --reference ./design-spec.pdf
maintain issue "Match the behavior in this example" --reference https://example.sharepoint.com/...
```

A local reference must be a readable, non-empty file no larger than 10 MB.
Maintain snapshots it into the run evidence and attaches that unchanged snapshot
as a fourth file in every Copilot scope, implementation, and review conversation.
The task explicitly marks it as read-only background material; the maintenance
request and repository policy take precedence if they conflict.

For an HTTPS link, Maintain puts the exact link in each Copilot request. Maintain
does not open or verify the linked content, so Copilot must already have access
through the signed-in Microsoft 365 session.

To remember a reference for future runs in the current project:

```sh
maintain feature "Use the project brief" \
  --reference ./project-brief.docx \
  --save-reference

# Keep the saved default, but skip it for this run
maintain feature "Make an unrelated change" --no-reference
```

The interactive workflow offers the saved default, lets you skip or clear it,
and provides a native file picker on Windows. Version 0.9 supports one reference
per run.

Maintain prepares an isolated workspace, selects focused context, creates a
change plan, implements it, reviews it in a separate conversation, and runs the
configured checks. When all gates pass, it asks whether to inspect, revise, save,
or accept the change. The guided default creates the verified commit and
fast-forwards the source branch if the source checkout is still unchanged.

For each browser exchange, Maintain uploads `TASK.md`, `CODEBASE.md`, and
`MANIFEST.json`, plus the optional Copilot reference file. The codebase document
contains only the selected context, with an index and exact repository paths.
Microsoft 365 Copilot implementation returns one self-describing downloadable
`maintain-output.zip`; it does not return JSON or source code in chat. The ZIP
contains `IMPLEMENTATION.toml` and complete added or modified files under
`files/` at their exact repository-relative paths. Maintain validates the
manifest identifiers and every archive member against the authorized task,
derives the internal provider result itself, and applies the files in the
isolated worktree before review and local verification. Browser profiles can
set `implementation_transport` to `inline` when downloadable artifacts are
unavailable.

After attaching a package, Maintain confirms that all three standard files, and
the optional fourth reference, are visible and that upload activity has stopped.
Filename matching is case-insensitive and does not depend on Copilot's current
attachment-chip markup. Maintain also confirms the exact browser file count,
requires the visible state to remain stable, checks that Send is enabled, clicks
Send, and confirms the outgoing request. This avoids submitting a request while
Copilot is still attaching files.
The permanent Microsoft 365 notice about copying device uploads to OneDrive is
informational and does not block submission.

Maintain recognizes a completed JSON response by its run, task, and role fields,
not only by Microsoft-specific page markup. This lets a visible valid response
complete the exchange even when the Copilot message element changes. Browser
failure evidence identifies the stage that stopped.

Browser controls are matched by purpose and proximity to the message field. The
tool confirms the selected model, every attachment filename, the complete prompt,
submission, response start, and response completion. It retries Send once only
when the complete prompt is still present and there is clear evidence that
nothing was submitted. Ambiguous controls stop without a click. Failure evidence
contains a screenshot, state trail, and sanitized control inventory; it does not
record cookies, tokens, message-field values, or general page text.
After every model click, Maintain waits for the main model selector itself to show
the preferred model. A matching item that remains visible in the open menu does
not count, and an unchanged selector stops the exchange before files are sent.
Redirects to unapproved hosts stop before page recognition. If generation is
interrupted, Maintain uses one visible **Continue generating** control. If the
response still does not finish, it stops instead of sending a repair request
while the assistant is working.

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

In the interactive interface, **View history** lets you select any run without
changing it. The detail view shows the original request, state, last error,
evidence gates, and compact local-command results without dumping command output.
Continuing is a separate explicit action. For an **Action needed** run, Maintain
shows the saved error and corrective guidance first, then asks for confirmation
before it reopens an assistant or changes the saved state.

Audit data is stored outside the target repository under `~/.maintain/runs` by
default. On Windows this is `%USERPROFILE%\.maintain\runs`. Browser exchanges are
under `<RUN-ID>\artifacts\browser\exchanges`; model-discovery evidence is under
`%USERPROFILE%\.maintain\browser`. Browser failures print the exact evidence path.

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
