# Maintain Simple UI — Concept

- Status: concept for review
- Branch: `maintain-simple-ui`
- Date: 2026-07-28
- Applies to: sw-maintainer-agent 0.9.x

## 1. Summary

Maintain Simple UI is a small desktop application for Windows. It guides one
person through the existing Maintain work loop: scope, implement, review, test,
accept. It does not automate the browser. The person moves the files between
the tool and Microsoft 365 Copilot. The tool prepares one ZIP package for each
exchange, and it validates each reply. The tool masks the internal steps. Each
screen shows one action.

The current CLI already contains the full work loop, the packaging rules, the
reply validation, the local checks, and the audit trail. This concept reuses
all of that. The new work is a thin UI layer, a manual transport, and OneDrive
support.

## 2. Requirements

Each requirement has an identifier for traceability. Section 12 maps each
requirement to the design.

| ID  | Requirement |
|-----|-------------|
| R1  | The tool has a simple UI. |
| R2  | The user can drag one file or many files onto the UI. The UI accepts ZIP, Markdown, and all other file types. The UI packages them for Copilot. The UI also has a file import control. |
| R3  | The user can drag the package from the UI into Copilot. The UI also has file export and copy-to-clipboard controls. |
| R4  | The UI guides the user through the scope, implement, review, and test loop as implemented today. |
| R5  | The export file is one ZIP. It contains the role prompt, the task instruction, the global prompt, and the dependencies, for example the focused codebase. |
| R6  | The tool copies the ZIP into a configurable OneDrive folder. When OneDrive completes the synchronization, the tool copies a OneDrive link to the clipboard. |
| R7  | The package contains a global prompt with each task. The global prompt grounds the LLM in the project scope and goal. It prevents drift and over-engineering. It enforces the project standards. |
| R8  | The global prompt, the OneDrive folder, and the other options are configurable in a user-friendly way. |
| R9  | The UI masks internal complexity. Each view has low content density. |
| R10 | All user-visible text obeys ASD-STE100. |
| R11 | The tool runs on Windows. |
| R12 | The user can go back and scope again at any decision point. |
| R13 | The user can run the local tests from the UI. |
| R14 | The tool stays minimal. It contains only what these requirements need. |
| R15 | The work goes on a new branch `maintain-simple-ui`. The design reuses the existing code. |

## 3. Concept of operation

The person is the transport. Copilot stays in its own window, signed in as
usual. The tool never touches the browser.

```
+------------------+        drag ZIP / OneDrive link        +-----------+
|                  | -------------------------------------> |           |
|  Maintain UI     |                                        |  Copilot  |
|  (this concept)  | <------------------------------------- |  (chat)   |
+------------------+     drag reply file / paste reply      +-----------+
        |
        |  unchanged internal loop
        v
  scope -> implement -> review -> test -> accept
  (WorkflowEngine, checks, audit, git worktree)
```

One full exchange:

1. The tool builds one ZIP package for the current stage.
2. The user moves the package to Copilot. The primary way is the OneDrive
   link from section 8: the user pastes the link into the chat. Two direct
   ways stay available: drag the ZIP into the chat, or paste the copied
   file.
3. Copilot replies. For implementation, the reply is `maintain-output.zip`.
   For scope and review, the reply is one JSON envelope in the chat.
4. The user moves the reply back. Two ways are available: drop the file on the
   tool, or select **Paste reply** after a copy from the chat.
5. The tool validates the reply against the run, task, and role identifiers.
   A wrong or stale reply is refused with a clear message.

The loop then continues exactly as in the current engine: the review runs in a
separate conversation, the local checks run on this machine, and the user
accepts the result before any commit.

## 4. Reuse map

The concept keeps the engine untouched where possible. The provider interface
is the seam: the engine calls `provider.exchange(request)` and waits for a
validated response. A new manual provider fills that seam with the UI.

| Existing module | Role in this concept | Change |
|---|---|---|
| `engine.py` (`WorkflowEngine`) | The full loop, gates, repair, resume, accept, deliver | None planned; small additions only if the scope gate needs a hook |
| `exchange_package.py` | Builds `TASK.md`, `CODEBASE.md`, `MANIFEST.json` and the role contracts | Reused; a new wrapper adds `GLOBAL.md` and zips the set |
| `providers/command.py` (`FileExchangeProvider`, `parse_response`) | Pattern for request/response file exchange; envelope validation | `parse_response` reused as-is; new `ManualUiProvider` follows the same contract |
| `artifacts/` (implementation, review, validation) | Validates `maintain-output.zip` and review envelopes | Reused as-is |
| `verification/runner.py` and `engine._test` | Local check execution with evidence | Reused as-is; the UI shows the results |
| `workflows/state.py`, `audit.py` | Checkpoints, resume, audit trail | Reused as-is |
| `context.py`, `repository/pack.py`, `workspace.py` | Focused context, isolated worktree | Reused as-is |
| `config.py`, `repository_memory.py` | Project config `.maintain.json`, per-user settings | Extended with the new keys in section 8 |
| `references.py` | Validation of user-supplied files | Extended to accept more than one file |
| `prompts/*.md` | Role contracts documentation | Reused; packaged into each ZIP |
| `scripts/install-windows.ps1`, `*.cmd` | Windows install, shortcuts, icon | Extended with one UI shortcut |
| `cli.py` | Project list, open, create | Logic reused through shared functions; the UI does not shell out to the CLI |

Not used in this mode: `copilot/browser.py`, `providers/browser.py`, and the
Playwright dependency. The browser providers stay in the codebase for the CLI.

## 5. New components

Keep the list short. This is the complete set of new code.

| Component | Purpose |
|---|---|
| `src/maintain/ui/` | The Qt application: screens, drag and drop, clipboard |
| `src/maintain/ui/strings.py` | One catalog of all user-visible text, STE-controlled |
| `src/maintain/providers/manual_ui.py` | `ManualUiProvider`: bridges `engine.exchange` to the UI and back |
| `src/maintain/zip_package.py` | Wraps `build_exchange_package`, adds `GLOBAL.md` and references, writes one ZIP |
| `src/maintain/onedrive.py` | Copy to the OneDrive folder, watch the sync state, compose the link |
| `docs/global-prompt-template.md` | Starter content for the global prompt |

## 6. Technology decision — UI toolkit

The hard requirements are: drag files in, drag files out to another
application, copy files and text to the clipboard, run on Windows, reuse the
Python codebase.

| Option | Drag in | Drag out to Copilot | File clipboard | Verdict |
|---|---|---|---|---|
| Tkinter (+tkinterdnd2) | Yes | Weak, no reliable OLE drag source | Text only | Rejected |
| PySide6 (Qt for Python, LGPL) | Yes | Yes (`QDrag` with file URLs → OLE `CF_HDROP`) | Yes (`QMimeData` URLs) | **Selected** |
| Web UI (Tauri, Electron) | Yes | Limited; new language stack | Partial | Rejected, not minimal |
| WinForms via pythonnet | Yes | Yes | Yes | Rejected, Windows-only code and a second object model |

Decision: **PySide6**, installed as the optional extra `.[ui]`, with a
`gui_scripts` entry point `maintain-ui` so no console window opens. The
pattern matches the existing optional `browser` extra. Drag-out with real
file URLs drops the ZIP into the Copilot attachment area of Edge or Chrome,
and into the Microsoft 365 Copilot app, because Windows carries it as a
normal file drop.

Thread model: the `WorkflowEngine` runs in one worker thread. The
`ManualUiProvider.exchange()` call blocks that thread on a queue. The UI
thread shows the Send and Receive screens, collects the reply, validates it,
and posts the `ProviderResponse` to the queue. Stop from the UI raises a
`ProviderError`; the engine then pauses the run with its normal
`NEEDS_HUMAN` path, and the existing resume machinery continues it later.

## 7. The package (R2, R5, R7)

One ZIP per exchange, built in the run's staging directory:

```
maintain-<run>-<task>-<role>.zip
├── START-HERE.md        # one page: read order and the output contract
├── TASK.md              # role prompt + task instruction (existing generator)
├── GLOBAL.md            # the global grounding prompt (new)
├── CODEBASE.md          # focused code with index and hashes (existing)
├── MANIFEST.json        # identifiers, hashes, payload (existing)
└── references/          # files the user imported or dropped (optional)
    └── <original names>
```

- `TASK.md` keeps the existing role contracts and JSON/ZIP output rules from
  `exchange_package.py`. It gains one line: "Read `GLOBAL.md` first. Obey its
  limits."
- `GLOBAL.md` is the anti-drift prompt (R7). It states the project goal, the
  scope limits, the standards, and the rules against over-engineering, for
  example: make the smallest change, add no new dependency without approval,
  follow the named style guide. The user edits it in Settings; a template
  ships with the tool.
- `references/` holds the imported files (R2). Any file type is accepted.
  Each file is size-checked and hashed into `MANIFEST.json` by the extended
  `references.py`. A dropped ZIP is kept as one file; the tool does not
  unpack it.
- Inbound direction (R2) also accepts the Copilot reply; section 9 defines
  how drops are classified.

Transport note: chat attachments are not the primary route for the ZIP.
Some Copilot tenants read chat-attached archives poorly. This is why R6
routes the package through OneDrive: Copilot opens the ZIP from the pasted
link with its normal Microsoft 365 file access. The spike in section 13
confirms this in the target tenant. If the tenant cannot open ZIP members
through a link, the fallback keeps the same workflow: the setting
`package.style = folder` makes the tool also expand the package into a run
folder next to the ZIP and link that folder. Default: `package.style = zip`
(R5).

## 8. Configuration (R6, R8)

Per-user settings (`~/.maintain/settings.json`, existing file, new keys):

```json
{
  "ui": {
    "onedrive_folder": "C:/Users/tim/OneDrive/MaintainOutbox",
    "onedrive_link_base": "https://contoso-my.sharepoint.com/personal/tim/Documents/MaintainOutbox",
    "sync_timeout_seconds": 120
  }
}
```

Per-project config (`.maintain.json`, existing file, new keys):

```json
{
  "provider": {"workflow": "manual-ui"},
  "package": {"style": "zip", "global_prompt": "GLOBAL.md"}
}
```

Every key has a Settings screen with plain language, a browse button for
folders, and a safe default (R8). The link base is set once: the user opens
the OneDrive folder in the browser, copies the address, and pastes it. The
tool composes `link_base` + `/` + URL-encoded ZIP name. This link works for
the signed-in owner in Copilot. Automatic share links need the Graph API and
are out of scope (section 11).

OneDrive flow (R6):

1. Copy the ZIP into `onedrive_folder`.
2. Watch the file's sync state. Primary probe: the Windows shell status
   property of the file (the same value File Explorer shows as the sync
   column), read through one small PowerShell call. Fallback: if the value is
   not readable within `sync_timeout_seconds`, the UI shows: "Look at the
   file in File Explorer. When you see the check mark, select Continue."
3. On success, put the link in the clipboard and show: "The link is in the
   clipboard. Paste it into Copilot."

The shell status text is locale-dependent, so the probe compares state codes,
not display strings, and the manual fallback always exists. This keeps the
feature robust without a OneDrive API.

## 9. Transport in and out (R2, R3)

Out (tool → Copilot):

- **Copy link.** The OneDrive flow from section 8. This is the primary
  route. It avoids the chat attachment limits, because Copilot opens the
  file from OneDrive.
- **Drag out.** The package card is a drag source. The drag carries the real
  ZIP path as a file URL. Dropping on the Copilot chat attaches the file.
- **Copy file.** Puts the ZIP on the clipboard as a file. Paste into Copilot
  or Teams attaches it.
- **Export…** A save-as dialog for the ZIP, for any other route.

In (Copilot → tool):

- **Drop zone.** Accepts one file or many files, any type.
- **Import…** A standard open-file dialog, multi-select.
- **Paste reply.** Reads the clipboard text and parses it as the JSON
  envelope. This covers scope and review replies that arrive as chat text.

Classification of inbound files, in order:

1. A ZIP whose root contains `IMPLEMENTATION.toml` → implementation artifact;
   validated by the existing artifact validation.
2. A `.json` file that parses as a response envelope → validated by the
   existing `parse_response` (run, task, and role must match).
3. Anything else → a reference file for the next package (R2). The UI says
   what it did: "Added 2 files to the package."

A reply that fails validation is refused with the exact reason and the next
step, and the Receive screen stays open. Nothing is applied outside the
isolated worktree, exactly as today.

## 10. UI design (R1, R4, R9, R12, R13)

Design rules:

- One screen, one decision. No screen shows more than one primary action.
- The stage header is always visible: `Plan · Build · Review · Test · Save`.
  Internal states, retries, and checkpoints stay hidden (R9).
- Every screen has **Back** where a loop-back is allowed (R12) and **Stop**
  to pause the run safely (the existing resume covers continuation).
- All text comes from the STE catalog (section 12).

Screens:

```
[1 Home]                          [2 Describe]
 Project: flight-tools             What do you want to change?
 ( Change software )               +-----------------------------+
 ( Repair a fault )                | text                        |
 ( Continue: run 0142 )            +-----------------------------+
 ( Settings )                      Drop files here to add them
                                   (references, any type)
                                   ( Start )

[3 Send — shown per exchange]     [4 Receive]
 Step 1 of 4 — Plan                Step 1 of 4 — Plan
 Package: maintain-0143-plan.zip   Drop the Copilot reply here
 [ ZIP card — drag me ]            +-----------------------------+
 ( Copy OneDrive link )            |        drop zone            |
 ( Copy file ) ( Export… )         +-----------------------------+
 OneDrive: check mark shown        ( Paste reply ) ( Back )

[5 Plan check — scope gate]       [6 Test]
 Copilot proposes 3 tasks:         Checks:
  1. Add the input filter           pytest .......... PASS
  2. Extend the unit tests          ruff ............ PASS
  3. Update the manual              ( Run the checks again )
 ( Accept the plan )               ( Repair with Copilot )
 ( Ask for changes )  <- rescope   ( Scope again )   <- rescope

[7 Save]
 All checks passed.
 3 files changed.  ( Show the diff )
 ( Accept and save )  ( Ask for changes )  ( Discard )
```

Loop-backs (R12):

- **Plan check** (new, one gate after scope): "Ask for changes" collects one
  note and runs scope again with that note in the payload.
- **Test** and **Save**: "Ask for changes" maps to the existing
  `engine.feedback()`, which re-enters the repair cycle. "Scope again"
  cancels the current tasks and restarts scope for the same run request with
  the collected note; the audit trail records the loop.
- The existing repair limit and `NEEDS_HUMAN` pause behavior stay unchanged.

Test execution (R13): the Test screen calls the existing `_test` stage
through the engine. It lists each configured check with pass or fail, and it
expands to the bounded output on request. "Run the checks again" repeats the
stage. No check output is dumped unrequested (R9).

## 11. Out of scope (R14)

- No browser automation in this mode. No Playwright dependency.
- No Microsoft Graph API, no app registration, no automatic share links.
- No polling of Copilot, no scraping. The person moves every message.
- No new audit store, config format, or state machine. Existing ones only.
- No macOS or Linux UI work beyond what PySide6 gives for free.
- No installer rework: the existing Windows scripts gain one shortcut.
- No theming beyond Qt defaults.

## 12. ASD-STE100 approach (R10)

- One catalog module holds every user-visible string. No literals in views.
- Writing rules enforced by a unit test on the catalog:
  - One instruction per sentence. Active voice. Present tense.
  - Procedural sentences: 20 words or fewer. Descriptive: 25 or fewer.
  - One name per thing, used everywhere: *package*, *reply*, *check*,
    *change*, *fault*, *link*. A banned-synonym list guards this
    (for example *upload*, *submit*, *artifact* in UI text).
  - An approved-verb list for instructions: select, drag, drop, copy, paste,
    start, stop, show, continue, accept.
- The official STE dictionary is licensed material; the test enforces the
  checkable rule subset plus the project word lists. A human STE review of
  the catalog closes the gap before release. This document already follows
  the same style.

## 13. Delivery plan

Phase 0 — spike (half a day, no product code):
- Copy a ZIP into OneDrive by hand. Paste the path link into Copilot chat
  in the target tenant. Confirm Copilot opens the ZIP and reads the members
  through the link.
- Also test the two direct routes: drag the ZIP into the chat, and paste it
  as a file.
- If the tenant cannot read ZIP members through the link, test the folder
  fallback from section 7.
- Result confirms the transport order and the `package.style` default.

Phase 1 — package first (usable without the UI):
- `zip_package.py`, `GLOBAL.md` support, multi-reference `references.py`,
  config keys, and a CLI command `maintain package` that emits the ZIP.
- Acceptance: the CLI produces a valid ZIP for each role; existing tests
  stay green; new unit tests cover the ZIP layout and hashes.

Phase 2 — UI shell and manual transport:
- `ui/` with Home, Describe, Send, Receive; `ManualUiProvider`; worker
  thread; drag in and out; clipboard file and text; import and export.
- Acceptance: one full feature run end-to-end on Windows with a human
  moving the files; a stale or foreign reply is refused with the exact
  reason.

Phase 3 — OneDrive:
- `onedrive.py`: copy, sync watch, link to clipboard, manual fallback.
- Acceptance: link lands in the clipboard after sync; timeout path shows
  the fallback instruction; no OneDrive API used.

Phase 4 — full loop in the UI:
- Plan check gate, rescope paths, Test screen, Save screen, resume of an
  interrupted run from Home.
- Acceptance: rescope from Plan check and from Test both work and are
  audited; checks run and re-run from the UI.

Phase 5 — STE and Windows polish:
- String catalog with the STE unit test; installer shortcut `Maintain`;
  README section; this document updated to "as built".

## 14. Traceability

| Req | Design answer |
|---|---|
| R1 | Section 10: seven small screens, one decision each |
| R2 | Sections 7 and 9: drop zone, Import…, `references/` in the ZIP |
| R3 | Section 9: drag-out, Copy file, Copy link, Export… |
| R4 | Sections 3, 4, 10: existing engine loop behind a five-stage header |
| R5 | Section 7: single ZIP with prompts, task, and codebase |
| R6 | Section 8: OneDrive copy, sync watch, link to clipboard |
| R7 | Section 7: `GLOBAL.md` in every package, template shipped |
| R8 | Section 8: Settings screens, plain language, safe defaults |
| R9 | Section 10: design rules; states and retries hidden |
| R10 | Section 12: STE catalog plus enforcement test |
| R11 | Sections 6, 13: PySide6 on Windows, installer shortcut |
| R12 | Section 10: Plan check gate, "Scope again", `feedback()` |
| R13 | Section 10: Test screen runs the existing checks |
| R14 | Sections 4, 5, 11: six new files, engine untouched |
| R15 | This branch; section 4 is the reuse map |

## 15. Open points for review

1. Confirm the phase-0 spike result: Copilot in the target tenant reads the
   ZIP members through the OneDrive link. This keeps `package.style = zip`
   as the default.
2. Confirm that the path-based OneDrive link is acceptable, or approve the
   Graph API for real share links (larger footprint).
3. Confirm the five stage names shown to the user: Plan, Build, Review,
   Test, Save.
4. Confirm that the CLI keeps the browser providers unchanged next to this
   mode.
