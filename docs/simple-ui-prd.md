# Maintain Simple UI — Product Requirements

- Status: PRD v1.1 — implemented on this branch (see section 10; M1, M2,
  and M3 are complete; the suite is verified on Windows — section 14.38)
- Branch: `maintain-simple-ui`
- Date: 2026-07-28
- Interactive reference: `docs/mockup/simple-ui-mockup.html` (snapshot at
  PRD v1.0; illustrative, not maintained with every PRD change)
- Architecture background: `docs/simple-ui-concept.md`

## 1. Product statement

Maintain Simple UI is a small Windows desktop application. It guides one
person through an AI-assisted change to a software project: plan, build,
review, test, save. The person moves one ZIP package to Microsoft 365
Copilot and brings the reply back. The tool prepares, validates, checks,
and records everything else. The tool never edits the project before the
person accepts the result.

This document is the authoritative specification. The interactive mockup
is a reference for the flow, the layout, and the expected level of
polish. It is a snapshot; where the two differ, this document wins. All
engine behavior reuses the existing `maintain` codebase as mapped in the
concept document.

## 2. Users and problem

One developer or maintainer with a Microsoft 365 Copilot license and no
Copilot API access. Browser automation is fragile and not permitted in
some environments. The person is willing to move files by hand, but wants
every other step prepared, guided, checked, and recorded.

## 3. Scope principles

- Build only what these requirements state. No speculative options.
- Reuse the existing engine, packaging, validation, checks, and audit
  store. The UI is a thin layer over them.
- One screen, one decision. The stage header is the only persistent
  navigation during a run.
- Every user-visible string obeys ASD-STE100 and lives in one catalog.

## 4. The loop

```
Describe -> Plan -> [approve | ask for changes -> Plan again]
         -> Build -> Review -> [approved | 1..n points -> Repair -> Review]
         -> Test  -> [passed | repair | scope again]
         -> Save  -> [accept | ask for changes | discard]
```

Each Plan, Build, Repair, and Review step is one packet exchange:
the tool builds one ZIP, the person gives it to Copilot, the person
brings the reply back, the tool validates it. Test runs locally. Save
creates the commit on a maintenance branch.

## 5. Functional requirements

Each requirement is testable. "Packet" means one outbound ZIP for one
exchange. "Task type" means one of Plan, Build, Repair, Review.

### 5.1 Home and projects

- FR-P1. Home shows the project name, path, and branch, and offers:
  Change software, Repair a fault, History, Settings.
- FR-P2. When a run is stopped or waiting, Home shows one Continue card
  with the run number, the stage, and what the tool waits for.
- FR-P3. The UI opens the active recent project from the existing
  project registry. The CLI project commands manage projects; a project
  picker in the UI is not in this version (section 9).

### 5.2 Describe

- FR-D1. The Describe screen has one text field for the request.
- FR-D2. The person can add files to the run on this screen: by drop
  (one or many, any type) and by an Import file dialog.
- FR-D3. Added files appear as removable chips.
- FR-D4. Start creates the run, the isolated workspace, and the first
  Plan packet.

### 5.3 Packets

- FR-K1. Every packet is one ZIP that contains: `TASK.md` (task
  instruction and output contract), `GLOBAL.md` (global prompt),
  `CODEBASE.md` (focused code with index and hashes), `MANIFEST.json`
  (identifiers and hashes), `documents/` (configured standards and
  reference documents), `attachments/` (files added by the person).
- FR-K2. `TASK.md` uses the boilerplate prompt for the packet's task
  type (see FR-G3) and instructs Copilot to read `GLOBAL.md` and
  `documents/` first.
- FR-K3. `documents/` contains the project-level documents plus the
  task-type documents (additive, FR-G4).
- FR-K4. `attachments/` contains the run files from Describe plus the
  packet files from FR-T6, minus any the person removed.
- FR-K5. The Send screen lists the packet contents on request, and can
  show the effective `GLOBAL.md` and prompt text.

### 5.4 Transport

- FR-T1. Primary route: Copy OneDrive link. The tool copies the ZIP to
  the configured OneDrive folder, watches the synchronization state,
  then puts the file link in the clipboard and says so. On timeout it
  shows the File Explorer fallback instruction. (Mechanism: concept §8.)
- FR-T2. The package card is a drag source that carries the real ZIP
  file, so a drop on the Copilot window attaches it.
- FR-T3. Copy file puts the ZIP on the clipboard as a file. Export…
  saves it with a file dialog.
- FR-T4. The Receive screen accepts the reply by drop, by Import file
  dialog, and — for JSON replies — by Paste reply from the clipboard.
- FR-T5. Continue to the Receive screen is enabled after the first
  outbound action.
- FR-T6. The Send screen has an Attachments row: the person can add
  files to this packet (drop or Add files…) and remove them, in the
  same window, before sending. The package is rebuilt on change.

### 5.5 Validation and safety

- FR-V1. Every reply is validated against the run, task, and role
  identifiers and the expected reply kind. Implementation replies are
  validated as the existing `maintain-output.zip` artifact contract.
- FR-V2. A stale, foreign, or wrong-kind reply is refused with the exact
  reason and the next step. The screen stays open.
- FR-V3. A dropped file that is not the reply joins the open packet
  as an attachment at once — the packet is rebuilt — and the tool
  says so.
- FR-V4. Applied files go only to the isolated workspace. The project
  tree changes only at Save, after the person accepts.

### 5.6 Plan approval and rescope

- FR-L1. After a valid Plan reply, the Plan check screen lists the
  proposed tasks in plain language, with files and checks on request.
- FR-L2. Accept the plan continues to Build. Ask for changes collects
  one note and runs Plan again with the note in the packet.
- FR-L3. Scope again is available from Test, from Review findings, and
  from Save (as Ask for changes). It collects one note and re-enters the
  matching loop point. Counters never rewind; the history records the
  loop.

### 5.7 Build, review, repair

- FR-L4. Build and Repair packets expect the ZIP artifact; on success
  the tool applies the files in the isolated workspace and continues to
  Review.
- FR-L5. A Review reply with findings shows each finding (severity,
  file, line, evidence, remediation) and offers Repair with Copilot and
  Scope again.
- FR-L6. An approving Review continues to Test. The repair limit and
  pause behavior reuse the engine's existing rules.

### 5.8 Test

- FR-C1. The Test screen runs the configured checks locally and shows
  one row per check with pass or fail and the bounded output on request.
- FR-C2. Run the checks again repeats them. A failing check offers
  Repair with Copilot and Scope again.

### 5.9 Save

- FR-S1. The Save screen shows the changed files with added and removed
  line counts, and the diff on request.
- FR-S2. Accept and save creates the commit on the maintenance branch
  (existing accept and deliver behavior). Discard asks for confirmation
  and keeps the audit record. Ask for changes enters a repair round.

### 5.10 Iteration history and revert

An "iteration" is one recorded step of the run: run started, plan
proposed, plan changed, plan approved, build applied, review found
points, repair applied, review approved, checks passed, saved, went
back. Each iteration stores its timestamp and, where it changes the
workspace, the workspace state identifier (tree hash) the engine
already records.

- FR-H1. History (from Home) lists the runs: number, request, state
  (In work, Waiting, Saved, Discarded, Stopped), date, changed files.
- FR-H2. Selecting a run shows its iteration timeline in order, with a
  clear marker for the current position.
- FR-H3. During a run, the footer shows a History control that opens the
  live timeline of the current run.
- FR-H4. For the active run, every anchor iteration offers Go back to
  here. The tool asks for confirmation, returns the workspace and the
  screen to the state directly after that iteration, marks the later
  iterations as superseded, and appends a "Went back to iteration N"
  event. Nothing is deleted; the audit record only grows.
- FR-H5. Undo the last iteration is a shortcut for Go back to the
  previous anchor.
- FR-H6. A saved or discarded run's timeline is read-only. The screen
  says: to change a saved result, start a new change. (Reversal of a
  saved commit is not in this version; see section 9.)
- FR-H7. After a revert, packet and attempt counters continue to count
  up. The same loop then continues from the restored point.

Engine mapping: the audit store already records every state move with a
tree hash and every artifact per attempt. Revert is one new engine
operation: reset the isolated worktree to the recorded tree hash, move
the run state to the matching checkpoint, and append a `human_revert`
audit event. It refuses runs in state Saved or Discarded. The primary
project tree is never touched.

### 5.11 Settings and configuration

All settings are edited in plain-language screens with safe defaults.

- FR-G1. OneDrive: package folder (with Browse), folder link address
  (with a live example link), synchronization wait limit.
- FR-G2. Global prompt: one editable text, packaged into every packet as
  `GLOBAL.md`, with Reset to the template.
- FR-G3. Task prompts: for each task type (Plan, Build, Repair, Review)
  the boilerplate prompt can be overridden at the task level; otherwise
  the built-in prompt is used. The screen shows which one is active and
  offers Use the built-in prompt again.
- FR-G4. Standards and reference documents: one project-level document
  list included in every packet, plus one list per task type. Both are
  edited on the same screen. Packet content is the union (FR-K3).
- FR-G5. Package: style `zip` (default) or `folder` (fallback, concept
  §7).
- FR-G6. Checks: the ordered list of local check commands (name and
  command), with add and remove.
- FR-G7. Storage: per-user settings in the existing settings file;
  per-project values (prompts, documents, package, checks, global
  prompt) in `.maintain.json`:

```json
"package": {
  "style": "zip",
  "global_prompt": "GLOBAL.md",
  "documents": ["docs/standards/python-style.md"],
  "tasks": {
    "plan":   {"prompt": null, "documents": []},
    "build":  {"prompt": "prompts/build.md", "documents": ["docs/api-rules.md"]},
    "repair": {"prompt": null, "documents": []},
    "review": {"prompt": null, "documents": ["docs/review-checklist.md"]}
  }
}
```

`"prompt": null` means: use the built-in prompt.

### 5.12 Stop and resume

- FR-R1. Stop is available during a run. It confirms, keeps all state,
  and returns Home. The Continue card resumes at the exact screen.
- FR-R2. Interrupted runs (closed app, error) resume the same way from
  the existing checkpoint machinery.

## 6. Non-functional requirements

- NFR-1. Windows 10/11. Installed with the existing installer scripts;
  one Start Menu and desktop shortcut; no console window.
- NFR-2. The UI is at least as polished as the reference mockup: the
  same layout discipline, spacing, state feedback, and light and dark
  themes. Where this document and the mockup differ, this document wins.
- NFR-3. All user-visible text obeys ASD-STE100, lives in one string
  catalog, and is enforced by the catalog unit test (concept §12).
- NFR-4. Packaging one packet completes in under 5 seconds for a
  focused context of up to 2 MB.
- NFR-5. No network calls except the OneDrive folder on the local disk.
  No Copilot API, no Graph API, no telemetry.
- NFR-6. Full keyboard operation: every action reachable by Tab and
  Enter; drag and drop always has a click alternative.
- NFR-7. Every run remains auditable with the existing audit store;
  history and revert only append events.

## 7. Screen inventory

The reference mockup shows every screen below as of PRD v1.0; its
Screens control jumps to each one with a plausible state. The rows
below, together with section 5, are the authoritative screen
definitions.

| Screen | Purpose | Primary action | Requirements |
|---|---|---|---|
| Home | Choose work | Change software | FR-P1, FR-P2 |
| Describe | State the request, add run files | Start | FR-D1..D4 |
| Send | Give one packet to Copilot | Copy OneDrive link | FR-K*, FR-T1..T3, T5, T6 |
| Receive | Bring the reply back | Paste reply / Import | FR-T4, FR-V1..V3 |
| Plan check | Approve or change the plan | Accept the plan | FR-L1, FR-L2 |
| Review findings | See the points, choose repair | Repair with Copilot | FR-L5, FR-L3 |
| Test | Run the local checks | Continue | FR-C1, FR-C2 |
| Save | Accept the verified change | Accept and save | FR-S1, FR-S2 |
| Done | Confirm the saved change | Start a new change | FR-S2 |
| History | List the runs | Open a run | FR-H1 |
| Run timeline | See iterations, go back | Go back to here | FR-H2..H7 |
| Settings | Reach the setting groups | — | FR-G* |
| OneDrive | Folder, link, wait limit | Save | FR-G1 |
| Task prompts & documents | Prompts and documents, two levels | Save | FR-G3, FR-G4 |
| Global prompt | Edit the ground rules | Save | FR-G2 |
| Package | Choose the package style | Save | FR-G5 |
| Checks | Edit the check commands | Save | FR-G6 |

## 8. Acceptance criteria (release gate)

1. One full feature run on Windows, moving files by hand, ends in a
   verified commit on a maintenance branch, with every screen behaving
   as sections 5 and 7 define, at the polish bar of NFR-2.
2. A stale reply, a wrong-kind reply, and a non-reply file each produce
   the specified refusal or classification message.
3. The OneDrive route ends with the correct link in the clipboard, and
   the timeout path shows the fallback instruction.
4. Ask for changes at Plan check produces a new plan packet that
   contains the note. Scope again works from Test and Review findings.
5. Go back to an earlier iteration restores the workspace to the
   recorded tree hash, the timeline shows the revert event and the
   superseded marks, and the run continues to a successful save.
6. Task-level prompt override and task-level documents appear in the
   next packet for that task type only; project documents appear in all
   packets.
7. Files added on the Send screen appear in that packet's
   `attachments/`; removed files do not.
8. The STE catalog test passes; no user-visible string bypasses the
   catalog.
9. All existing engine tests stay green.

## 9. Not in this version

Stated to keep the scope firm:

- Reversal of a saved run from the UI. The saved branch and audit trail
  make a manual `git revert` possible; a guided "Reverse this change"
  can be a later slice.
- Per-plan-task (wind-filter, filter-tests, …) prompt or document
  overrides. Task-type level only.
- History search and filters beyond the recent list.
- A diff viewer beyond the Save screen's summary and inline diff.
- Editing prompt text inside the Send screen. Settings only.
- Browser automation, Copilot API, Graph API, telemetry, macOS/Linux
  packaging.
- For issues (section 12): dependency links between issues, a priority
  axis separate from severity, labels, assignees, CSV import wizards,
  SARIF export, and any multi-user or sync machinery. Closing an issue
  when a scan stops reporting it (machine absence proves nothing with
  an LLM scanner).

## 10. Release slices

1. **M1 — Packet and loop.** Packaging with documents and attachments,
   Send/Receive, validation, Plan check, Build/Review/Repair, Test,
   Save. CLI `maintain package` ships first for early testing.
2. **M2 — History and configuration.** History list, run timeline,
   go back / undo, Continue card, Task prompts & documents settings,
   OneDrive settings, package style, checks editor.
3. **M3 — Polish and compliance.** STE catalog and test, installer
   shortcut, keyboard pass, dark theme pass, polish review against
   NFR-2.
4. **M4 — Issues.** Section 12 in three slices: store + screens +
   auto-capture + repair bridge; the scan loop with its accept gate;
   the discuss loop.
5. **M5 — Explain.** Section 13, after the manual trial passes.
6. **M6 — Exchange rework.** Section 14: the one Exchange screen, the
   downloadable reply contract, and the small usability set P1–P9.

## 11. Open points

1. Confirm the phase-0 spike result (concept §13) in the target tenant
   before M1 freezes the `zip` default.
2. Confirm the iteration anchor set (section 5.10) is enough, or name
   additional anchors you want to go back to.

## 12. Issue tracker (M4)

Each project keeps a small issue list: the tool's memory of known
faults and points to repair. One person, one machine, one writer — no
sync, no merge machinery.

### 12.1 Storage and record

- One JSON file per project beside the runs
  (`<runtime>/issues/<repository-key>.json`), read and written whole.
  Never inside the working tree: runs require a clean repository.
- An issue has: `id` (short random hash, like `a3f2c1`), `title`,
  `detail`, `severity` (high / medium / low — the review loop's
  values), `status` (Open → In work → Closed), `closed_reason` (Fixed,
  Will not fix, Duplicate, Not a fault, No longer applies), `source`
  (you / review / test / scan / import), a location (`file`, `line`,
  quoted `snippet`), a `fingerprint`, linked run ids, an optional
  external reference (for spreadsheet rows), notes, and a small
  append-only event list (when, who, what changed).
- The fingerprint hashes kind + file + the whitespace-normalized
  snippet (first ~100 significant characters), GitHub-code-scanning
  style. Identity lives in content, not line numbers.

### 12.2 Workflows

1. **Add, change, remove.** A list screen (filters: All / Open / In
   work / Closed) and a detail screen with editable title, detail, and
   severity. Close-with-reason is the primary removal; a hard Remove
   exists for mistakes, behind a confirm.
2. **Auto-capture.** Review findings and failed checks become issues
   automatically (`source: review` / `test`), linked to the run, with
   the snippet read from the worktree. A fingerprint match updates the
   existing issue instead of duplicating; a match on an issue closed as
   Will-not-fix, Not-a-fault, or Duplicate is dropped silently
   (dismissal persists); a match on Fixed or No-longer-applies reopens
   it. When the run delivers, its linked issues close as Fixed — except
   issues the final approving review still cites.
3. **Scan.** A `scan` packet (task-type prompt and documents
   configurable like the others) carries the codebase and any
   attachments — for example an exported spreadsheet of an external
   tracker, which Copilot cross-references; there is no CSV mapping
   wizard, Copilot is the mapper. The reply is a JSON issue list; every
   entry must quote the offending code. The tool verifies each quote
   against the file, drops fingerprint matches of known issues (open or
   closed), and shows the rest on an accept screen with check boxes.
   Only what the person accepts enters the list. Nothing enters
   without the gate.
4. **Discuss.** From an issue: write a question, and the tool builds a
   `discuss` packet with the issue record, the cited code, and any
   files the person drags in (xlsx or any reference file — the Send
   screen's attachment area). The reply becomes an attributed note on
   the issue; a proposed severity change applies only after a confirm.
5. **Repair bridge.** Repair with Copilot on an issue pre-fills the
   Repair-a-fault flow, links issue and run both ways, and rides the
   auto-Fixed rule in workflow 2.

### 12.3 Acceptance criteria

1. A review with findings and a failed check add issues once, and a
   repeat of the same finding updates instead of duplicating.
2. A delivered run closes its linked issues as Fixed, except issues
   the final review still cites.
3. A scan proposal that matches a closed Will-not-fix issue never
   reaches the accept screen.
4. A scan entry whose quoted code is not in the named file is marked
   on the accept screen.
5. The discuss reply lands as a note with author and time; a severity
   proposal changes nothing until confirmed.
6. Every new string passes the STE catalog test.
3. Confirm that reversal of saved runs stays out of this version.

## 13. Explain (M5)

Purpose: turn one code area into a short Manim video that explains it.
Copilot writes the scene file. Manim renders it on this computer. The
source specification is the Copilot Chat Code Explainer handover
document.

### 13.1 Accepted risk

The Explain reply is a program, not data. The render runs
Copilot-written Python on this computer. The owner accepts this risk:
the tool runs in a controlled and recoverable environment. Decision
recorded 2026-07-29. The controls in 13.3 stay required.

### 13.2 Flow

1. Phase 0 — manual trial, no application changes. Build the packet
   with `maintain package` and the explain prompt. Move the files by
   hand. Record the section 9 measures from the handover document.
   The trial code lives in another repository.
2. Phase 1 — the feature, built after the trial passed. A new task type
   `explain` with the prompt and documents configurable like the
   others. A Home entry "Explain code": select the files, write the
   goal and the audience. The Exchange screen sends the packet. The
   reply is one fenced Python block, nothing else. The tool extracts
   it, applies the checks in 13.3, runs the configured render
   command, and shows the result with "Open the video" and "Repair
   with Copilot". A render fault becomes a repair packet with the
   error text.

The built-in explain prompt sets the copy and pace rules: every
sentence on screen follows ASD-STE100, the animation runs 30 to 45
seconds, each text stays on screen for three seconds or more, and one
thing moves at a time. Output text quoted from the code is verbatim.
The scene starts with a literal BEATS list of (text, seconds) pairs,
and text goes only in three named zones: the title band, the content,
and the note band. Each explain packet ships two guides as
attachments: PITFALLS.md and EXAMPLE-SCENE.md.

### 13.3 Controls

- The tool refuses network modules, process calls, and paths outside
  the work folder, before the render.
- The render runs in a separate folder.
- The person reads the scene before the render. The review checklist
  names the untrusted-code risk.
- The tool does not install Manim. A plain message shows when Manim
  is absent.
- Local checks run on each scene reply, and never block: the BEATS
  manifest; the copy (20 words for one sentence, active voice, a
  banned-word list); the pace (three seconds or more for each text,
  20 characters each second or less, 25 to 50 seconds in total); and
  a geometry probe that runs the scene without output and reports
  text outside the frame or text wider than its card. Findings show
  on the result screen and travel in the repair packet as
  payload.lint_findings.
- After a good render the tool makes sheet.png: one frame each three
  seconds, for a fast review of every beat.

### 13.4 Install and update

- One idempotent script does the first install and every update:
  `scripts/setup.ps1` (documented in `docs/install.md`). It verifies
  Python, prepares pipx, installs Maintain with the `ui` and `explain`
  extras, installs ffmpeg with winget when absent, and verifies the
  result. The `explain` extra pins Manim Community 0.20.1 beside the
  prompt contract, so the two move together.
- The Manim command the app runs is a per-user setting
  (Settings → Explain, default `manim`). The version pin belongs to
  the project prompts; the location belongs to the computer.

### 13.5 Deferred

- "Explain this change" from a saved run.
- All items in section 11 of the handover document: browser control,
  batch generation, voice, publishing, agents, plugins.

### 13.6 Lent concepts

- The geometry probe follows SGA (arXiv 2607.18116): run the scene,
  read the boxes, report conflicts. No vision model.
- The named zones and the fit-to-card guard follow Code2Video
  (showlab) and ManimAgentPrompts (mathifylabs).
- The BEATS manifest follows manimator and TheoremExplainAgent: plan
  the text and the time first, then the code.
- The contact sheet follows makefinks/manim-generator: review frames,
  not the full video.
- The pace numbers follow the BBC and Netflix caption rules and the
  Szarkowska 2018 eye-tracking study.
- The pitfalls file follows ManimAgent: keep known faults as rules in
  every packet.

## 14. Exchange rework (M6)

Source: the usability review (docs/usability-review.md, P1–P9,
all accepted) plus three direct requirements from the owner.

### 14.1 One Exchange screen

Send and Receive are one screen with two marked regions: "Send to
Copilot" (accent border) and "Receive from Copilot" (green border).
The reply region is always active; the Continue gate is gone. The
reply validator stays the real gate. A resumed run returns to the
same screen with nothing to unlock.

### 14.2 The reply is a downloadable file

Every packet tells Copilot to return its reply as one downloadable
file: `maintain-output.zip` for implementation, one Markdown file
with one fenced block for everything else (the JSON envelope, or the
scene). The person clicks download in Copilot, then selects "Open
the newest download". The tool takes the newest .zip/.md/.json/.txt
file from the Downloads folder that is newer than the packet, and
checks it with the same validator as every other path. The Downloads
folder is a setting on the OneDrive page. Drop, click-to-select, and
paste stay as alternatives; a fenced envelope in pasted text or in a
file is unwrapped before validation.

### 14.3 The link copies itself

When a packet appears and the OneDrive folder is set, the tool
publishes the packet and puts the link in the clipboard alone
(setting "auto link", default on, OneDrive page). Without a folder,
a plain line points to Settings.

### 14.4 The rest of the accepted set

- The busy screen shows only after 600 ms; fast transitions are
  silent (P3).
- Every drop zone is also a button; the separate Import buttons are
  gone (P4).
- The check-failure screen has "Run the checks again" for flaky
  checks. The engine allows TEST_FAILED → TESTING and writes the
  retry evidence as `tests-N.json`, keeping the audit append-only
  (P5).
- Issue detail: the decisions sit on top, and Close is an inline
  five-reason row, not a dialog (P6).
- The scan focus is a field on the Exchange screen; "Update the
  package" rebuilds the packet in place (P7).
- A toast reports engine-captured and delivery-closed issues (P8).
- Enter fires the screen's primary action; Esc goes back (P9).

### 14.5 The reward pass (D1–D9)

A second walkthrough scored every beat for reward, momentum, and
effort. The nine accepted items, all UI-layer:

- D1 The Done screen lands the win: files, checks, iterations,
  duration, and "Copy the merge command" for the saved branch.
- D2 Each accepted reply gets a named toast ("The plan is in.").
- D3 Home shows one momentum line from the run history.
- D4 The all-green Test screen holds 600 ms before Save.
- D5 A live "waits for the reply" timer sits in the receive region,
  and the window title names the awaited step for the taskbar.
- D6 History rows lead with the request; the run id is the subline.
- D7 The explain result shows the frame sheet inline; a click opens
  the video.
- D8 Describe offers the last five requests as one-click chips.
- D9 Ctrl+C on the Exchange screen copies the link (or file) again.

Rejected on purpose: streaks, badges, confetti, sounds. Real numbers
over decoration.

### 14.6 The third pass (E1–E6)

- E1 Toasts are chips: a bordered card above the foot bar, four
  seconds, at most two at a time. The status bar is gone.
- E2 Inline note panels replace the note dialogs on the plan,
  findings, test, and save screens and for the discuss question. The
  content stays visible while the person writes; the text survives a
  cancel.
- E3 When a project has only diff-check, the Describe screen says so
  and opens the checks editor on click.
- E4 The findings eyebrow names the round from round two on.
- E5 The explain screen remembers the last audience.
- E6 "Plan accepted." and "Repair round starts." toast as beats.

### 14.7 The fourth pass (F1–F6)

- F1 The recents row on Describe can no longer widen the page: three
  chips, hard-elided, full text in the tooltip. (Defect fix.)
- F2 After the packet leaves — link copied, file copied, exported, or
  dragged out — the send region folds to one summary row and the
  receive region rises above the fold. Show expands it again.
- F3 When the window is in the background and input is needed — a
  packet is ready, the render ends, a check fails, the save or done
  screen arrives — the tool asks the operating system for attention
  (the taskbar flash on Windows).
- F4 The toast chip uses the soft accent background in both themes.
- F5 The issue list orders by severity, then the newest change.
- F6 The busy screen shows the elapsed seconds after five seconds.
- Also: the window close waits for a running engine thread to settle.

### 14.8 The delighter pass (G1–G5)

The rule for this pass: delight through what the tool already knows,
never through decoration.

- G1 The window catches the reply anywhere. While an exchange waits,
  a file dropped on any screen — or Ctrl+V outside a text field —
  goes to the reply validator, exactly as on the Exchange screen.
- G2 The Done screen offers "Explain this change": the explain flow
  opens pre-filled with the changed files and the goal.
- G3 "Copy the change note" puts a paste-ready summary in the
  clipboard: request, files, checks, iterations, duration, branch.
  Also on every saved run in the history.
- G4 An issue with a linked run shows it: "Fixed by run …" opens
  that run's timeline. The audit trail becomes a walkable story.
- G5 The first delivered run in a project gets one extra line on the
  Done screen, once: "Your first saved change in this project."

### 14.9 The issue-arc pass (H1–H4)

A focused pass over the issue tracker and the explain arc, after the
owner asked whether those use cases got the same attention.

- H1 The external reference is visible: on the issue detail
  ("Reference: …") and in the list row. The spreadsheet round-trip
  no longer needs a search by title.
- H2 The all-clear moment: when the open list is empty but closed
  issues exist, the list says "All clear. {n} closed in this
  project." and the Home card says "All clear. {n} closed."
- H3 The delivery close toast names the issue ("Closed: {title}.",
  with "and {n} more" when several).
- H4 The explain screen shows "Last video: {date}. Open the folder."
  for the newest rendered video.

### 14.10 The issue follow-up (I1–I5)

- I1 The all-clear message shows only when no open or in-work issue
  remains. (Defect fix.)
- I2 An in-work issue offers "Return to open", so a stopped or
  discarded repair run cannot strand the status.
- I3 The scan gate has Select all and Select none.
- I4 The issue form has an editable Reference field; the store
  records reference changes as events.
- I5 The filter tabs carry counts when issues exist.

### 14.11 The project chip

The foot bar starts with a project chip that names the open project on
every screen (long names elide at 24 characters; the tooltip keeps the
full name). One click pops a menu of the known projects, most recent
first, with a check mark on the open one and each non-ready state
labeled. One more click switches through the same guarded path as the
Projects screen (busy runs refuse with a toast; setup and missing
states keep their prompts). The last entry, "All projects…", opens the
full Projects screen for create, add, and remove.

### 14.12 The coverage pass

End-to-end journeys now cover the settings round-trip through every
page, the long way home (repair round, failed checks, stop and
continue, run checks again, feedback, discard), the launch entry
point, and a paint pass over every screen. Unit tests fill the gaps
the journeys cannot reach: audit export and retention, tamper
detection, OneDrive sync probing, and reply validation. The journeys
caught two real defects, both fixed: the package-style radios were
not exclusive (after a switch to folder, zip could never be selected
again), and a corrupt or non-ZIP file dropped during a build step
raised an unhandled error instead of a message or an attachment.

### 14.13 The verification batch

Four verification activities beyond coverage, recorded in full in
`docs/quality-report.md`:

- The rescope and issue-mode journeys close the last worthwhile
  coverage gaps: rescope from all three gates, and issue mode with a
  reproduction check for both outcomes.
- Static analysis (ruff, mypy) is codified in `pyproject.toml`; the
  real findings are fixed.
- Mutation testing measured whether the tests bite; twelve killer
  tests were added, and the transition policy now kills 100% of its
  mutants.
- A live assistant (a Claude agent standing in for Copilot, which this
  environment cannot reach) completed a real two-task run end to end
  from the packets alone; the delivered branch passes its own tests.

### 14.14 The Windows shakedown, first finding

The first run of `setup.ps1` on the real computer failed: the machine
runs Python 3.14, Manim's native dependencies (moderngl, glcontext)
publish no wheels for it, and pip tried to compile them without the
C++ build tools — so the whole install died for the sake of the video
feature. Three fixes, all testable now:

- The `explain` extra carries a `python_version < '3.14'` marker, so
  an install on 3.14 succeeds without Manim instead of failing.
- The setup logic moved from PowerShell into `scripts/setup.py`
  (the .ps1 is a thin bootstrap): it picks the newest installed
  Python 3.11–3.13 for the app when the default is newer, falls back
  to a ui-only install when the full one fails, and explains how to
  enable the video feature later. `tests/test_setup_script.py` drives
  every branch with a fake command runner.
- The render step's "Manim is absent" message names the Python 3.14
  cause when that is the reason.

### 14.15 The performance pass

The first manual test on the real computer read as sluggish; the
earlier passes had audited flow and logic, never perceived latency, so
a profiling harness now times every UI-thread operation against an
aged project (sixty runs, thirty issues, a 120-file repository). What
it found, and what changed:

- The Home screen scanned the run list twice on every visit; it now
  scans once and feeds the continue card and the momentum line from
  the same read.
- The Save screen ran `git diff` on the UI thread at the exact moment
  the person waits for it; it now reads the diff from the newest
  recorded `actual.diff` audit artifact (git stays as the fallback),
  and the diff also outlives workspace cleanup.
- Switching to the already-open project rebuilt every screen for
  nothing; it now just navigates home.
- The two inherently heavy actions — the theme change (~0.5 s: the
  whole widget tree re-polishes) and a real project switch (screen
  rebuild) — now freeze repaints and show the wait cursor, so the
  person sees processing instead of a hang. The wait cursor also
  covers packet rebuilds, exports, the projects and history lists,
  and the scan packet build.
- Describe and Explain gained one checkbox: "Include the project code
  and tests." It loads every source and test file (the context
  selector's own walk: configured roots, excludes, secret and binary
  files skipped, the size cap applied) into the packet without one
  hundred chips, and a toast counts what was added. Explain no longer
  demands a manual file when the choice is on.

### 14.16 The second improvement pass

- A project switch no longer rebuilds the screens. The stores, the
  controller, and the project-bound texts rebind in place; the switch
  fell from ~280 ms to ~75 ms on the profiling harness, and the
  Windows multiplier applies to the removed part.
- Paste accepts a copied file. A reply downloaded and copied in File
  Explorer (Ctrl+C, Ctrl+V) now routes exactly like a drop — on the
  exchange screen's Paste button and anywhere in the window.
- The app has its own icon (painted at start, no shipped file) and a
  Windows AppUserModelID, so the taskbar shows Maintain instead of a
  generic Python entry. The setup script adds a Start Menu shortcut
  on Windows, so the app starts like an app, not a terminal command.
- The exchange waiting counter stops ticking when the screen is not
  visible and resumes when it returns.

### 14.17 The step checklist and the fitted exchange

Two observations from the real computer:

- Long waits showed one spinner and one status line. The Busy screen
  and the Explain render screen now carry a step checklist in the CLI
  style: braille-dots animation on the running step, a green check
  when it completes, a red cross when it fails. The engine's own
  progress events drive the run checklist; the render worker reports
  its stages (check the scene, check the geometry, render the video).
- The exchange screen's reply zone was cut off at the bottom: its
  content wanted 846 px against a 720 px viewport, hidden on Linux by
  smaller fonts. The content now measures 682 px: the reply zone is
  slim, the two receive actions share one row, the regions and the
  column are tighter, and the default window grew to 640×830. A
  regression test pins the content height under 700 px so it cannot
  quietly grow past the window again.

### 14.18 The one-file packet and the visible OneDrive stages

Findings from the real computer's OneDrive attempt:

- Command windows flashed for every child process: the sync probe every
  two seconds, and every git call behind a run. A windowed app on
  Windows opens a console per subprocess unless told otherwise; every
  subprocess call now passes shared no-window flags (`maintain.proc`).
- The link flow gave no location: publish now shows its stages in the
  send region — "Copy the package to OneDrive" then "Wait for the
  synchronization", dots while running, a check when done — and the
  link button re-enables only at the final state, with the link
  already in the clipboard on success and an honest fallback line on
  timeout.
- New package style, the default for new projects: one Markdown file.
  The whole packet renders as one readable file — `## FILE: <name>`
  sections in packet order, the JSON manifest in a four-backtick fence
  so inner fences survive, an index up front, and reading instructions
  in the header. Copilot ingests it directly; nothing needs unpacking.
  Binary attachments are never embedded (an assistant reads text, not
  encoded bytes): they are declared under "Attached separately" and
  ride as their own attachments, where Copilot reads formats like PDF
  natively. The ZIP is still built underneath — the audit trail and
  the reply contract (maintain-output.zip for build steps) are
  unchanged; the Markdown is how the packet travels. The Package
  settings page offers all three styles.

### 14.19 Manim finds itself, and asks to be installed

The real computer showed "Manim is absent" while setup had verified
Manim present. The cause: pipx exposes only the application's own
commands on PATH, so the manim script installed by the explain extra
sits beside the app's interpreter where `which` cannot see it. Three
changes:

- The default manim command now resolves to the app environment's own
  script first (`resolve_manim_command`); a custom command passes
  through untouched. On the affected computer this fixes the render
  with no install at all.
- When Manim is genuinely missing and the Python supports it, the
  Explain start offers the install: one confirm, the busy screen while
  pip installs into the app's environment, then the flow resumes by
  itself. On Python 3.14 the version message stays instead.
- setup.py's verification step now offers the same install
  interactively ("Install it now? [Y/n]") when the app environment
  supports the video feature; a non-interactive run keeps the plain
  note.

### 14.20 The one-file handoff, complete

Copilot accepts few attachments per message, so several binary
references would break the single-handoff promise. Embedding encoded
bytes does not solve it — an assistant reads text and cannot decode
base64 into a document — so the tool now extracts the text on this
computer at packet-build time: PDF through pypdf (now part of the ui
extra), Word, PowerPoint, and Excel through their own XML with the
standard library. Extracted text joins the one Markdown packet as
"FILE: name (extracted text)" sections; extraction routes by format,
never by byte-sniffing, so an all-ASCII PDF still extracts as a PDF.
Only files with nothing to extract — images, scanned documents — stay
under "Attached separately", each with its reason. One request, one
file, whatever the number of references.

### 14.21 One progress story at a time

The staged OneDrive view briefly told two stories: the step list ticked
"Copy the package to OneDrive" complete while the old status line still
said "Copy the package to OneDrive…" — is it copied, or still copying?
During a publish only the step list speaks now; the status line stays
empty and returns with a single final outcome (link ready, look in File
Explorer, or the error). Rule of the pattern: when a step list narrates
the progress, no second widget repeats it.

### 14.22 The video plays where the render finished

The result screen showed four check marks and then sent the person
away to an external player. Now the finished scene plays inside the
view itself: it starts on its own, with Play/Pause, a position bar,
and the time. Qt Multimedia comes with PySide6-Addons, added to the
explain extra; the in-app Manim install brings it too, pinned to the
Qt version that already runs so pip never touches locked files. When
the module is absent the screen keeps the old buttons and one hint
names the setup script; a file that does not decode falls back to the
frame sheet, quietly. Leaving the view releases the video file —
a minimize only pauses. The still frame sheet no longer shows next to
a playing video.

The same pass rewrote the sync-not-confirmed outcome on the exchange
screen. It said "Look at the file in File Explorer" — but the link was
already composed and in the clipboard. The line now leads with that,
and File Explorer is the fallback advice, not the instruction.

### 14.23 OneDrive is abandoned; the Markdown file is the route

The OneDrive link transport never earned its keep on the real machine:
a sync watch that timed out, a probe that popped windows, an outcome
line that needed three rewrites. Decision: abandon it. The whole
transport left the product — the publish module, the staged ticker,
the link button, the settings page, the auto-link switch, the folder
package style that existed only to serve it, and every string.

The main route is now the one Markdown packet, handed over directly:
when a packet appears, the file is already in the clipboard and the
status line says so — paste into Copilot, send, done. Ctrl+C copies it
again; Export stays for a copy on disk. The ZIP style remains as the
one fallback. The Downloads folder setting, which lived on the
OneDrive page, moved to its own small Settings page. Stored configs
with the retired folder style load as ZIP.

Parked, not forgotten: the OneDrive module lives in the git history
(v0.9.1 and earlier) if a link transport ever returns.

### 14.24 Named work, folded chips, and explanations that survive

Four findings from the same real-machine session.

**The chip wall (FR-P10).** Include-code swept 157 files into the
packet, and 157 removable chips filled the send region. The chip row
now shows the first twelve and one "+N more" chip; a click expands,
"Show fewer" folds. One hidden file is not worth a fold, so thirteen
files still show plainly.

**Named runs (FR-N1).** A returning person read "Continue run
f-20260731-103304-94fd" and learned nothing. Stopping a run — by the
Stop button or by closing the window mid-run — now asks one line:
"Name this work". The name lands on the run record after the engine
settles (never racing its final write), and the home card leads with
it: "Continue: Wire the loader" over "Change — Plan. The tool waits
for you." The activity comes from the run mode, the phase from the
state — a paused run splits Plan from Build by whether planned tasks
exist yet. An empty or cancelled prompt changes nothing.

**MP4s out of git.** Rendered videos never belong in a repository;
`*.mp4` joined the tool repository's own .gitignore.

**Explanations recover (FR-X2, FR-X3).** The explain exchange lived
only in memory: a crash or a quit lost it, and finished videos hid in
a runtime folder. Each explain run now keeps a state.json beside its
packets — waiting, passed, failed, or discarded. A waiting one returns
on the home screen ("Continue the explanation") and reopens with its
original run and task ids, so a reply Copilot already wrote for that
packet still validates. Finished ones list on the Explain screen under
"Finished explanations", each one click from its video (folder as the
fallback). A discarded exchange stops offering itself.

### 14.25 Stale videos know it, and update themselves (FR-X4)

A video explains files as they stood at packet time; the files move
on. The saved packet request already names every explained file with
its sha256 — that manifest is the fingerprint set, so staleness costs
no extra bookkeeping. When the Explain screen lists the finished
explanations, it hashes each recorded file in the working tree
(directly, never through the committed-tree cache, so an uncommitted
edit counts): any changed or deleted file marks the row stale — a warn
icon, "the files changed since this video", and an Update button. New
files are outside the check by design: the video explained these
files, and these files are what it tracks.

Update redoes the original workflow over the current files: same goal,
same audience, same source list, a fresh packet and run. When the
update's render passes — through any repair rounds — the old run gains
superseded_by and leaves the list; until then, the old video stays,
still true to the files it explained. An update that is discarded or
fails retires nothing.

The build surfaced a latent fault worth its own line: the context
inventory cache was keyed on the committed tree alone, so a packet
built after a save-without-commit carried the cached old content — the
"updated" packet would have shipped the stale text it was updating.
Dirty paths now join the cache key with their stamp and size; every
packet builder benefits, not only the update.

### 14.26 One question, editable ever after; updates in seconds

Two frictions from daily use. The naming prompt asked on every stop
and every close, named or not. The question now comes after the engine
settles — when the record is in hand and knows whether a name exists —
so named work stops and closes with no question, and unnamed work gets
exactly one.

The name lives on the open workflow, not the home screen. The foot
bar — visible on every screen of a run — labels the run's name beside
its id ("Add a name…" until one exists), and a click edits it,
prefilled. A rename made while the engine is busy shows at once but
writes only at the next settle, so it never races the engine's own
run.json writes; closing first applies it on the way out. Run-less
side flows (scan, discuss, explain) carry no name chip. The home
continue card still leads with the name; its Rename button — one
review round old — is gone.

Setup reinstalled every dependency on every run: `pipx install
--force` rebuilt the whole environment, PySide6 and Manim downloads
included, to deliver a one-line code change. The script now probes
`pipx list` for the existing install and updates through pip inside
the app's own environment — Maintain rebuilds, satisfied dependencies
stay in place, only missing or outdated ones download. The full
--force chain remains the fallback for the first install and for a
broken environment. The probe also fixed a wrong name: verification
ran `pipx runpip maintain`, but the environment is named after the
project — every runpip call now uses the detected name.

### 14.27 The settings walkthrough

"A lot of them don't seem to function very well." A scripted walk
drove every page end to end and found eight faults; all are fixed and
pinned by tests.

The worst: editing any task prompt (or saving the global prompt) wrote
Maintain's own files into the repository, the repository turned dirty,
and every later run refused with "The source repository has
uncommitted changes" — Settings bricked the tool. The engine's clean
check now excludes Maintain's own files (.maintain.json,
.maintain-prompts, the configured global prompt), and the context
sweep skips them too, so a prompt file never rides in a packet as
candidate code.

The rest, in walkthrough order. The home Continue card was a dead
click while the engine waited — a walk to Settings and back could not
return to the run; Continue now returns to the exact screen the run
waits on (exchange, plan, findings, or test). The documents picker
accepted many files and silently kept only the first; every selected
file lands now. Prompt edits were lost when leaving with Back instead
of Save; Back flushes them, the same as Save, because the rest of the
page already applied changes at once. A package style change with a
packet open left the old file on screen and in the clipboard; the
open packet re-shows and re-copies in the new style, and an unchanged
style is a no-op. The Downloads page accepted a folder that does not
exist, silently breaking the reply pickup — it now refuses with a
plain message and carries a Browse button. The Explain page saved any
command with no feedback; it now says at open and after save whether
the render command resolves. And the project-documents explanation no
longer vanishes behind the "No documents yet" note.

### 14.28 The name heads the run; the foot bar breathes

The real machine showed the run name squashed to "esting a workflov"
in a foot bar carrying six things. The name now heads the workflow
screens: a slim row above the stage header, full width, elided at 42
characters instead of 24, still one click from the rename dialog, and
"Add a name…" until a name exists. It appears on the run screens
alone — side flows and the home screen carry no head — and the window
grew 36 pixels so the exchange content keeps its guaranteed fit.

The foot bar slimmed to match: the name chip left, the theme toggle
is one symbol (☀ in the dark theme, ☾ in the light; the words moved
to the tooltip), and a Home button joined — one click back from any
screen, pairing with the Continue card that returns to the exact
waiting screen with the head, the stage header, and Stop restored.

### 14.29 The build reply: Markdown or ZIP, both roads home (FR-V4)

The real machine hit the wall the ZIP contract was built over:
Copilot's "maintain-output.zip" would not open — "The tool cannot
read this ZIP file" — because Copilot writes one Markdown file far
more reliably than a real binary ZIP. The build step now accepts two
shapes of the same implementation. The Markdown reply carries the
JSON envelope with `content.files` (every added or modified file,
complete) and `content.deleted_files`; the ZIP stays as before. The
packet asks for the Markdown shape first.

One code path validates and applies both: a Markdown reply is
synthesized into the same maintain-output.zip — manifest and members —
that a real ZIP would be, then walks the existing validation
(authorized paths, layout, issue root cause) and the engine's
apply-and-diff unchanged. The receive screen takes the reply as a
paste, a downloaded Markdown file, or a file — and a text reply that
Copilot misnames ".zip" is read as text before it is refused. The
paste button now shows at the build step, the lead names both shapes,
and the main-journey test drives the Markdown route while the repair
journey keeps the ZIP route covered.

### 14.30 The fault flow and the issue list are one thing (FR-I6)

"Repair a fault" opened a blank page while the issue list sat one
screen away holding the very faults worth repairing. The fault screen
now ties in directly. Under "What is the fault?" it lists the open
tracked issues — severity first, at most four — and selecting one
walks the existing linked-repair path: the description prefills from
the issue, the started run links to it, the issue turns in-work, and
delivery closes it.

Describing a new fault joins the same loop: the description is
captured as a tracked issue (source "described") before the run
starts, linked the same way, closed by the same delivery. The same
words described again reuse the same tracked issue — its fingerprint
matches — so a stopped repair picked back up later never duplicates
the entry, and a closed fault described afresh reopens rather than
forks. Every repair now has a tracker entry; the issue list is the
one honest ledger of fault work.

### 14.31 Many issues (FR-I7)

Volume is the scan loop's natural product, so both surfaces scale.
The fault screen keeps its four severity-first cards and adds one
line past them — "+N more issues…" — that opens the whole tracker;
Repair there returns to the fault screen prefilled, so the long way
round still ends at the same Start button. The tracker itself gains
a filter line under the status tabs: type, and the list narrows live
over title, file, and source, while the tab counts stay whole. A
fresh visit always starts unfiltered. And card titles wrap now —
user words land in those cards (issue titles, explain goals), and a
long sentence must never widen the page.

### 14.32 Related issues repair together (FR-I8)

Related faults often share one fix, and closing them one run at a
time would ask three round trips for one change. Each issue card on
the fault screen now carries a check box. A click on the card stays
the fast path — one issue, prefilled, start. Checking two or more
shows one button, "Repair {count} together": it composes a single
numbered description ("Repair these N related faults together." with
each title, detail, location, and cited code), and the started run
links to every checked issue. Delivery closes them all — the close
mechanism and its "Closed: {title} and {count} more" toast already
spoke plural. Stopping the run leaves each issue in work, exactly as
a single repair would.

### 14.33 The scanner names the groups; the pick offers them (FR-I9)

Check boxes ask the person to know which faults are related — but the
model that found the faults already knew. Each scan issue may now
carry a short group label ("group", one to three words, at most 40
characters): the scan instructions ask for one label shared across
issues with one cause or one area of functionality, and none for an
unrelated issue. The label rides the candidate through the accept
gate into the store, shows on the tracker row's sub-line, and joins
the filter haystack, so typing a group name gathers its issues.

Picking one issue to repair — from a fault-screen card or the issue
detail's Repair — now looks up its relatives first: open issues in
the same group or, when the pick is ungrouped, ungrouped issues in
the same file (a plausible stand-in for functionality), at most four
so the offer stays a decision. When any exist, one question — "Repair
related issues together?" with the shared label and the relative
titles — offers the combined run; "Only this one" keeps the fast
single path. Accepting composes the same numbered together-description
as checked boxes do, and links every issue. The check boxes remain as
the manual override for relations the scanner never named.

Landing this surfaced a real race: an operation's settled signal
queues from the worker thread, so stop-then-start-at-once could let
the stale settle land after the next run began — clearing the new
run's pending issue links and yanking its screen home. A settle or
failure that arrives while a newer operation is in flight now keeps
its record bookkeeping (names, renames) and leaves the screen and the
pending links alone.

Screenshotting the offer caught the dialog dropping its words: every
confirm in the app passes wordy choices ("Repair 3 together" / "Only
this one", "Stop the run" / "Keep working") and the message box
showed stock Yes/No buttons instead. The confirm dialog now builds
its two buttons from the caller's words, affirmative as the default.
Two copy gaps closed at the same time: the fault-screen cards show
the group label beside the file, previewing the offer a pick will
make, and the tracker's filter placeholder names group among its
fields.

### 14.34 End-to-end walkthrough: nine faults from one user's day

One sitting drove the whole app as a user would — first launch, a
feature change delivered end to end, a stop and a named continue,
the tracker, a scan, explain, projects, both themes — reading every
screen and every toast. Nine faults fell out; all are fixed.

- A third toast within the show window crashed the dismissal timer:
  the eviction in push() had already deleted the chip, and touching
  the dead object raised. The timer now checks the object is alive.
- A wrapped toast clipped its last line. The overlay sized itself
  from natural-width hints, so a message that wrapped at the capped
  width got a one-line chip. The overlay now measures each message
  in its real font at the real width and sizes every chip exactly.
- The foot bar cut its run line mid-word ("isolated worksp"). The
  run id was noise there anyway — runs carry names now — so the foot
  says "Isolated workspace" with the id in the tooltip, and a scan
  or discuss says "Read-only exchange" instead of a raw exchange id.
- The done screen counted raw timeline events as "7 iterations" for
  a straight-through run. It now counts applied builds — "1 build
  round" — and the paste-ready change note says the same.
- Stopping your own run toasted the engine's third-person halt text
  ("The person stopped the exchange…"). Your own stop now speaks in
  the app's voice: "Continue the run from the home screen."
- Four count strings broke at one: "Added 1 issues.", "+1 more
  issues…", "1 known issues", "Added 1 code files". Each has a
  singular form now.
- The issue detail screen never showed the scan group; it now joins
  the location line ("src/parse.py:88 · parser bounds").

The projects list looking empty mid-session turned out to be the
test driver skipping the launcher (which records every opened
project) — not a product fault. Verified, not fixed.

### 14.35 The tracker and the repair loop, walked to the end

A focus round drove the whole issue lifecycle as one user's story: a
scan seeded the tracker; a detail edit set a reference and severity;
a grouped pick accepted the related offer and delivered one run that
closed both issues; a dismissed finding stayed out of the next scan;
a repair was discarded; a discuss round raised a severity. Six
faults and gaps came out of it; all are fixed.

- A discarded run left its issues "in work" forever with nothing
  working on them. The engine now releases a cancelled run's in-work
  issues back to open, with a "released" event on each issue.
- A fault card gave no sign its issue was already held by a paused
  repair, inviting a duplicate run. The card sub-line now ends with
  "In work" when a run holds the issue.
- The tracker search could not find "T-42": the filter haystack
  missed the external reference the rows display. Reference joined
  the haystack and the placeholder.
- The delivery toast ("Closed: … and 1 more.") faded in seconds and
  the done screen said nothing about issues. The stats card now
  keeps the win: "Closed 2 issues in the tracker."
- The issue detail's run link showed only the raw run id. It now
  shows the run's name when the person gave one.
- Composed tracker repairs were remembered as recent-request chips;
  replaying one would capture the whole numbered block as a single
  nonsense described issue. Only hand-written requests join the
  recent chips now.

The rest of the lifecycle held under the walk, end to end: capture
with group labels through the accept gate, reference and severity
edits, the related-repair offer, delivery closing every linked issue
and keeping review-cited ones open, dismissal suppression before the
gate, reopen paths, and the discuss round writing both notes and the
new severity onto the issue.

### 14.36 Second pass: the corners the first walk missed

A repeat walkthrough drove the surfaces no earlier round reached: the
reply-validation errors, plan "Ask for changes" with its note, the
findings gate into a repair round, a failing local check retried to
green, the save diff, history and the run timeline, manual issue
entry, the close-reasons row, and a rename read back later. Four
faults; all fixed.

- The stale-reply refusal spoke engine language: "manual returned an
  envelope for a different task." — the provider's internal name, the
  word "envelope", and no next step. The exchange screen now says:
  "This reply belongs to a different run or step. In Copilot, copy
  the reply for this package." An unreadable envelope gets the same
  treatment.
- The build-round count missed repair rounds: the timeline logs a
  repair as repair_applied, so a build plus one repair still read
  "1 build round". Both kinds count now.
- History rows ignored run names: a run renamed "Value correction"
  still titled itself by its request. The name leads the row now,
  with the request and the id one line below.
- The run timeline's head had the same fault — "Run f-…" for a named
  run. The name heads it now; the id stays in the tooltip.

The rest of the pass held: the junk-paste refusal already spoke
plainly, the rescope note rode into the second plan packet and its
timeline entry, the findings gate captured its point into the
tracker, the failed check named itself with both commands visible,
the retry went green, the diff colored its lines, the close-reasons
row offered every reason, and both themes painted every walked
screen.

### 14.37 Round three: the explain lifecycle and the last corners

A third walk drove the explain feature end to end with a stub
renderer — packet, scene reply, render pass, the finished list, a
stale detection after a source edit, the one-click update, and the
supersede — plus the keep-as-attachment path, the save screen's
"Ask for changes" round, go-back from a live timeline, a project
switch, and a narrow window. The lifecycle held: stale showed the
moment the file changed, the update reran the same goal over the
current files, and the old entry marked itself superseded. Three
faults; all fixed.

- A file dropped into the reply zone that was not the reply was kept
  silently: no message, no packet rebuild, the packet card still
  said "attachments/ — 0" (FR-V3 demands the tool says so). The drop
  now joins the open packet at once — rebuilt like a send-zone drop —
  and the receive zone's status line says "This is not the reply. It
  is added to the package."
- A passed render with lint findings contradicted itself: PASS chips
  over "The local checks found faults" and a primary Repair button.
  After a pass the findings now read as suggestions — "The video is
  ready. The local checks suggest these improvements." — and Repair
  drops to secondary beside Open the video. A failed render keeps
  the original urgent framing.
- The home Continue card fell back to the raw run id for unnamed
  work. It now falls back to the person's request words ("Continue:
  Rename the constant.") and shows the id only when both are empty.

Held under the walk: the update packet carried the saved goal and
audience; the save note rode into its repair packet; go-back asked
with its labeled confirm, reissued the packet, and superseded the
skipped steps; each project kept its own tracker and foot chip
across a switch; and the narrow window scrolled every screen with
the foot bar pinned.

### 14.38 The Windows pass: nine rounds to a green machine

M3 had shipped everything but its proof: the suite had never run on
Windows. A dispatch loop against the Windows smoke workflow on
`windows-latest` — run, read the failure, fix, dispatch again —
closed that gap in nine rounds. The first finding was the workflow
itself: it installed the browser extra only, so every UI test had
been skipping silently on every past run. With `[browser,ui]`
installed, an offscreen Qt platform, and a git identity for the
worktree commits, the real suite ran — and seventeen tests failed.

Five of the faults were product bugs, real differences in how the
tool behaved on Windows:

- The patch pipe corrupted every diff. Text-mode stdin turns `\n`
  into `\r\n` on Windows, so `git apply` refused every hunk of
  every Copilot reply. The patch now feeds the pipe as bytes.
- An LF patch still missed worktrees checked out with CRLF line
  endings. Apply now runs with `--ignore-whitespace`.
- The artifact path guard leaned on `is_absolute()`, which is False
  on Windows for rooted paths like `/x` and drive-relative paths
  like `C:x`. The guard now rejects any anchored path and checks
  the resolved path stays inside the artifacts folder.
- The checks editor ate backslashes: the POSIX shell splitter
  mangled `C:\tools\python.exe` on the way in. Windows now splits
  without POSIX rules and joins with Windows quoting rules.
- Non-ASCII project paths came back mojibaked (`cafÃ©`): every
  text-mode subprocess read git's UTF-8 output through the console
  code page. Every such call now decodes UTF-8 with replacement.

The rest was harness portability — shell-script test stubs became
`.cmd` files on Windows, fixtures pinned their newlines, generated
test code stopped interpolating `C:\Users\...` into string literals
where `\U` reads as an escape — and one long tail: with all 300
tests passing, the process still exited red. PySide6 6.11 crashes
with an access violation while Windows unloads its DLLs, after the
summary prints. Closing every window did not stop it; `os._exit`
did not either, because `ExitProcess` still runs the DLL-detach
callbacks where Qt dies. The suite now ends the process through
`TerminateProcess`, which skips them — passing the handle through
declared 64-bit types, because the first attempt let ctypes
truncate the pseudo-handle to 32 bits and the guard fell through.

The ninth round came back green end to end: 300 passed, the CLI
answered, and the installer, update repair, launcher, and
diagnostics steps all exercised clean. The on-Windows verification
pass that M3 was waiting on is done.

### 14.39 The scan becomes a sweep (FR-W1..W4)

A real scan on a real project returned one issue — found in a file
whose name contains the word "defect". That was the tell: the scan
disclosed only files that scored against the focus words, and with
no focus it fell back to a built-in list of fault-flavored words
("defect fault error bug ..."), capped at 60 files. Copilot was
then told "do not report code you were not given", and it obeyed.
The screen's promise — "Empty means a full scan" — was false: an
unfocused scan was a keyword lottery, and quiet, ordinary files
never entered the draw.

- FR-W1. A scan is a sweep of the whole project inventory. The
  files that fit one packet budget go now; the rest go in later
  waves. A per-project coverage cursor records what a reply has
  proven delivered, a changed file re-enters the queue by content
  hash, and a finished cycle starts the sweep over.
- FR-W2. The focus text orders the queue — matching files go first.
  It never shrinks coverage.
- FR-W3. The tool says where the sweep stands, in the packet's
  send region ("This scan holds 42 of 120 project files. 78 files
  stay for later scans.") and again on the proposal screen, so the
  person knows to run the next part.
- FR-W4. Issue text stands alone for a codebase newcomer, in
  ASD-STE100: what the code does now, why that is a fault, the
  effect it can cause, and the repair direction, with each code
  name explained in a few words. The scan instructions demand it,
  the packet's output contract repeats it, and the instructions
  now say out loud: report every distinct fault, do not stop after
  the first findings.

The coverage cursor advances only when a reply comes back — a
packet that was built but never carried to Copilot claims nothing.

### 14.40 Discuss the project (FR-B1..B4)

The issue-level discussion existed; what was missing was the wide
one — brainstorm over the whole codebase, or talk through an
entire group of issues at once. The first cut looped every reply
back into an in-app thread, one packet per message. The person
corrected it: the conversation belongs in Copilot. The tool's job
is one handover packet out and one outcome in.

- FR-B1. A "Discuss the project" card on the home screen opens a
  small compose step: an optional subject, one "Create the
  package" button. The packet carries the open issues with their
  groups, the repository map, and as much project code as one
  packet holds — subject-matching files first. Naming a group of
  issues as the subject lands its issues and its code together.
- FR-B2. The discussion itself happens in Copilot, at any length,
  with no round trips through the tool. The packet tells Copilot
  to hold the talk in chat — grounded in the supplied code and
  issues, in plain words for a codebase newcomer — and to produce
  the output file only when the person ends the discussion or
  asks for the outcome.
- FR-B3. The discussion ends in exactly one of four outcomes, and
  the closing envelope routes each one: issues raised go through
  the same accept gate as a scan (snippet-verified, deduplicated,
  recorded with the Discussion source); a repair request lands in
  the fault description with any named tracker issues linked, so
  the run that follows closes them; a feature request lands in
  the change description; no request goes home with a word. A
  routed request starts nothing by itself — the person reviews
  the description and starts the run.
- FR-B4. A talk shares the discuss packet policy and documents,
  and never returns code changes; repairs stay in repair runs.

### 14.41 The tracker that said "Open 20" and showed nothing

From the field: a scan accepted twenty issues, the tab row counted
them, and the list stayed empty. Reproduced on the first walk: the
status tab persisted across visits. The search box already reset
on a fresh visit — the code called a stale query "a mystery-empty
list" — but the tab kept whatever the person last chose, so the
visit after the scan rendered the old tab's nothing under counts
that said Open 20.

Three repairs, one policy:

- An entry from outside the tracker — home, a scan's accept, the
  fault screen's bridge — starts on the open work. A round trip
  from an issue's detail (back, close, remove) keeps the tab the
  person chose.
- The tracker re-reads the issue file on every idle visit. Every
  mutation saves at once, so the file is the truth; the re-read
  heals the list after any other writer — a second window, another
  app version, a hand edit. A running engine shares the store, so
  the swap stays hands-off while a run is live.
- The words on a row tolerate the file: a source, status, or close
  reason written by another app version renders as itself instead
  of killing the render loop mid-list.

### 14.42 The app finds its own updates (FR-U1..U4)

A release is a git tag in the form v1.2.3 on the tool's own
repository. The Release workflow guards the ritual: on a tag push
it checks that the tag matches the project version, runs the full
suite on windows-latest, and publishes a GitHub release with a
source zip. The zip serves the first install; the update path does
not need it.

- FR-U1. The app looks for a newer release quietly: soon after the
  start and then every six hours, a background git ls-remote reads
  the repository's tags and compares the highest v-tag with the
  running version. The check uses the person's saved git
  credentials, so it works on a private repository with no API
  token. Every failure is silent. A settings switch turns the
  look off.
- FR-U2. A found release shows as one card on the home screen:
  "Version 1.2.3 is ready." Nothing installs by itself.
- FR-U3. "Skip this version" hides the card and remembers the
  skipped tag; only a newer release shows again. "Not now" on the
  confirm keeps the card for later.
- FR-U4. Accepting hands over to a detached helper, because the
  app cannot rebuild the environment it runs from. The helper
  waits for the app to close, downloads the release, runs that
  release's own installer — which resolves, verifies, and installs
  the pinned tag — and starts the app again. The helper ships
  inside the package, and the Windows workflow exercises it
  end to end against the checked-out source.

### 14.43 The lag: build once, reuse everywhere

From the field: the project-management surfaces felt very laggy.
Measured with a heavy fixture — 180 past runs, 60 issues, six
projects — the causes were all one habit: rebuild everything on
every look.

- The history screen rebuilt one row widget per run on every
  visit: 420 to 540 ms each time.
- The tracker rebuilt every row on every visit, every tab click,
  and every search keystroke: 120 to 200 ms per event — typing a
  six-letter filter froze the screen six times.
- The home screen re-read and re-parsed every run record on every
  navigation, with two path resolutions per record.

One repair pattern, three places. Run summaries now cache against
each record file's stamp, so a home visit re-parses only changed
records (18 ms to 2 ms warm). History and tracker rows now live in
a cache keyed by id and pinned to the fields they render: a visit,
a tab, or a keystroke detaches and re-attaches the same widgets,
rebuilds only rows whose fields changed, and drops rows whose
items vanished (history revisit 450 ms to 4 ms; tracker tab click
155 ms to 0.7 ms). The first build of a big list still costs its
construction once; every look after that is close to free.
Deterministic guards pin the behavior: unchanged records return
the same summary objects, and a filter round trip returns the
same row widgets.

### 14.44 Round four: every scenario, one story, one gap

A full usability pass drove every known scenario. The three
earlier walkthrough rounds ran again on the current code — the
core run loop with its gates, stops, resumes, and go-backs; the
tracker lifecycles; the explain lifecycle; the error and settings
corners — all clean. A fourth round drove what this branch added:
a real two-wave scan at the true packet budget with a focus
change, the coverage lines, and the cycle restart; all four
discussion outcomes; the update card with skip and re-offer; the
tracker and history caches under edits, outside renames, searches
typed one key at a time, theme switches with detached rows, and a
narrow window; and project isolation across a switch (each project
its own tracker, discussion, and sweep position).

Then one story end to end, crossing every seam at once: a scan
raised the issue, the discussion returned a repair request naming
it, the fault flow arrived prefilled and linked, the run repaired
it through plan, build, review, and save, the delivery closed the
issue with reason fixed, the done screen said "Closed 1 issue in
the tracker.", and the issue's detail linked the run that fixed
it. Every step held.

One gap found and repaired: after accepting a wave's issues, the
"files not scanned" reminder lived only on the accept screen, so
the sweep lost its thread the moment the person moved on. The
accept now also says it as a toast: "2 project files are not
scanned. Select Scan again for the next part."

### 14.45 The first start after an update says what changed (FR-U5)

The update loop closed silently: the app came back on the new
version and nothing named what the person just received. Now one
modal does, once.

- FR-U5. Release notes ship inside the package, one short ASD-STE100
  section per version, so the modal needs no network. The app
  remembers the last version the person saw; on the first start
  after the version changed, one modal lists every noted version
  between — an update that jumped versions catches the person up —
  with one Continue button. A fresh install records the version
  and stays quiet: there is nothing "new" for a first-time person.
  An update from a build older than this feature shows the current
  version's notes alone.

A style test enforces the ASD-STE100 rules on every note line, and
a ritual guard fails the suite — and therefore the release — when
a version bump ships without its notes.

### 14.46 No console windows, enforced on the source

Command windows flashed again during the start and during project
work. The rule was old — every child process passes `**hidden()`,
which carries the no-window flag — but the rule lived only in a
docstring, so new code kept missing it. A source audit found
eleven calls without the flag, on exactly the paths the person
touches most: the repository probe that runs at every start and
every project action, the project creation commands, the branch
detection in project setup, the Windows folder picker, the
worktree add and remove, and the engine's tool probe. The check
runner had its own version of the fault: it set the process group
flag and no window flag, so every check flashed.

All eleven are repaired. The rule is now enforced where it cannot
rot: a test reads every subprocess call in the source and fails
when one neither passes `**hidden()` nor appears in a short
allowance list with its reason. A second test keeps the
allowances honest — each must still point at code that exists and
still sets its own flags. Three allowances stand: the check runner
(which adds the no-window flag to its process group flag), its
stop path, and the updater, which shows its console on purpose
because the app is closing and a silent reinstall would look like
nothing happened.
