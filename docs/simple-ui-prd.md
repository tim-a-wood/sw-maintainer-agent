# Maintain Simple UI — Product Requirements

- Status: PRD v1.1 — implemented on this branch (see section 10; M1 and M2
  are complete, M3 is complete except the on-Windows verification pass)
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
- FR-V3. A dropped file that is not the reply is kept as an attachment
  for the next packet, and the tool says so.
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
