"""The one catalog of user-visible text. ASD-STE100 style.

Rules, enforced by tests/test_ui_strings.py:
- One instruction per sentence. Active voice. Present tense.
- Procedural sentences: 20 words or fewer. Descriptive: 25 or fewer.
- One name per thing: package, reply, check, change, fault, link, run.
- No banned synonyms (upload, submit, artifact, execute, terminate, abort).
"""

from __future__ import annotations

STR: dict[str, str] = {
    # Application
    "app.title": "Maintain",
    "app.footer": "Isolated workspace",
    "app.footer.side": "Read-only exchange",
    "app.footer.tip": "Run {run}. The work happens in an isolated copy "
                      "of the repository.",
    "app.waiting": "Maintain — waiting: {step}",
    "wait.step.plan": "the plan reply",
    "wait.step.build": "the code reply",
    "wait.step.repair": "the repair reply",
    "wait.step.review": "the review reply",
    "wait.step.scan": "the scan reply",
    "wait.step.discuss": "the answer",
    "wait.step.explain": "the scene",

    # Stages
    "stage.plan": "Plan",
    "stage.build": "Build",
    "stage.review": "Review",
    "stage.test": "Test",
    "stage.save": "Save",

    # Home
    "home.change": "Change software",
    "home.change.sub": "Add or change a function.",
    "home.fault": "Repair a fault",
    "home.fault.sub": "Fix a problem in the software.",
    "home.history": "History",
    "home.history.sub": "Runs, iterations, and go-back.",
    "home.issues": "Issues",
    "home.issues.sub.none": "No open issues.",
    "home.issues.sub.count": "{count} open. Scan, discuss, repair.",
    "home.issues.sub.allclear": "All clear. {count} closed.",
    "home.explain": "Explain code",
    "home.explain.sub": "Copilot writes a short video scene. Manim renders it here.",
    "home.projects": "Projects",
    "home.projects.sub": "Create, open, or remove projects.",
    "home.settings": "Settings",
    "home.settings.sub": "Prompts, documents, package, checks, downloads.",
    "home.momentum": "{count} saved changes. The last: {when}.",
    "home.momentum.one": "1 saved change. The last: {when}.",
    "when.today": "today",
    "when.yesterday": "yesterday",
    "home.continue": "Continue run {run}",
    "home.continue.named": "Continue: {name}",
    "home.continue.sub": "{activity} — {phase}. The tool waits for you.",
    "activity.feature": "Change",
    "activity.issue": "Repair",
    "home.explain.continue": "Continue the explanation",
    "home.explain.continue.sub": "The tool waits for the scene reply.",
    "foot.home": "Home",
    "theme.symbol.light": "☀",
    "theme.symbol.dark": "☾",
    "foot.name.unset": "Add a name…",
    "foot.name.tip": "This is the name of this work. Select it to change "
                     "the name.",
    "stop.name.title": "Name this work",
    "stop.name.body": "The name shows on the home screen. An empty name "
                      "changes nothing.",

    # Describe
    "describe.title": "What do you want to change?",
    "describe.fault.title": "What is the fault?",
    "describe.issues.head": "Open issues",
    "describe.issues.more": "+{count} more issues…",
    "describe.issues.more.one": "+1 more issue…",
    "describe.issues.together": "Repair {count} together",
    "issues.related.title": "Repair related issues together?",
    "issues.related.body": "These open issues share {label}:",
    "issues.related.only": "Only this one",
    "describe.issues.hint": "Select one to repair it. Check several to "
                            "repair them together. A new fault goes into "
                            "the issue list.",
    "issues.search.placeholder": "Filter by title, file, source, group, "
                                 "or reference.",
    "describe.placeholder": "Write the change in one or two sentences.",
    "describe.drop.main": "Drop files here, or click to select them.",
    "describe.drop.sub": "All file types are permitted. They go into every packet.",
    "describe.import": "Import…",
    "describe.start": "Start",
    "describe.empty": "First write the change.",
    "describe.checks.hint": "Checks now: diff-check only. Add real checks in Settings.",

    # Send
    "send.plan.title": "Copilot makes the plan.",
    "send.build.title": "Copilot writes the code.",
    "send.repair.title": "Copilot repairs the code.",
    "send.review.title": "Copilot examines the change.",
    "send.lead": "Give this package to Copilot. Use one of the ways below.",
    "send.drag": "Drag this package into Copilot.",
    "send.copy_file": "Copy the package",
    "send.export": "Export…",
    "send.attachments": "Attachments — go into this packet",
    "send.attach.add": "Add files…",
    "send.attach.drop": "Drop files here, or click to select them.",
    "send.contents": "What is in the package?",
    "send.file.copied": "The package is in the clipboard. Paste it into Copilot.",
    "send.exported": "Saved {name}.",
    "send.updated": "The package is updated.",

    # Receive
    "exchange.send.head": "Send to Copilot",
    "exchange.receive.head": "Receive from Copilot",
    "receive.lead.zip": "Copilot replies with one Markdown file, or with "
                        "maintain-output.zip. Download it in Copilot, then "
                        "select Open the newest download. Or paste the "
                        "reply.",
    "receive.lead.json": "Copilot replies with one reply file. Download "
                         "it in Copilot, then select Open the newest "
                         "download.",
    "exchange.accepted.plan": "The plan is in.",
    "exchange.accepted.build": "The code is in.",
    "exchange.accepted.repair": "The repair is in.",
    "exchange.accepted.review": "The review is in.",
    "exchange.waiting": "The tool waits for the reply. {time}",
    "exchange.copy.key": "Ctrl+C copies the package again.",
    "exchange.sent": "Sent · {name}",
    "exchange.sent.show": "Show",
    "exchange.newest": "Open the newest download",
    "exchange.newest.none": "No new file is in the Downloads folder.",
    "exchange.newest.wrong": "The newest download is not the reply: {name}",
    "exchange.drop.sub": "Or click to select the file.",
    "receive.drop": "Drop the reply here.",
    "receive.paste": "Paste reply",
    "receive.checking": "Check the reply…",
    "receive.valid": "The reply matches run {run}.",
    "receive.applied": "Applied. Your project does not change before you accept.",
    "receive.clipboard.empty": "First copy the reply in Copilot.",
    "receive.kept": "This file is not the reply. The tool keeps it for the next packet.",
    "receive.back": "Back",

    # Plan check
    "plan.title.one": "Copilot proposes 1 task.",
    "plan.title.many": "Copilot proposes {count} tasks.",
    "plan.files": "Files: {files}",
    "plan.done_when": "Done when: {text}",
    "plan.verification": "Verification: {text}",
    "plan.accept": "Accept the plan",
    "plan.rescope": "Ask for changes",
    "plan.show": "Show the checks",

    # Review findings
    "findings.title.one": "Copilot found 1 point to repair.",
    "findings.title.many": "Copilot found {count} points to repair.",
    "findings.evidence": "Evidence.",
    "findings.repair": "Repair.",
    "findings.repair.button": "Repair with Copilot",
    "findings.repair.sub": "The tool makes a repair package with these points.",
    "findings.rescope": "Scope again",

    # Test
    "test.title": "The tool runs the checks on this computer.",
    "test.passed": "All checks passed.",
    "test.failed": "A check failed.",
    "test.run_again": "Run the checks again",
    "test.repair": "Repair with Copilot",
    "test.rescope": "Scope again",
    "test.continue": "Continue",
    "test.show": "Show the result",

    # Save
    "save.title": "All checks passed. {count} files changed.",
    "save.title.one": "All checks passed. 1 file changed.",
    "save.diff": "Show the diff",
    "save.accept": "Accept and save",
    "save.accept.sub": "The tool makes the commit on a new branch. "
                       "Your project does not change before this point.",
    "save.feedback": "Ask for changes",
    "save.discard": "Discard",

    # Done
    "done.title": "The change is saved.",
    "done.branch": "Branch: {branch}",
    "done.files": "{count} files changed",
    "done.files.one": "1 file changed",
    "done.checks": "{count} checks passed",
    "done.checks.one": "1 check passed",
    "done.steps": "{count} build rounds · {time} from start to save",
    "done.steps.one": "1 build round · {time} from start to save",
    "done.issues": "Closed {count} issues in the tracker.",
    "done.issues.one": "Closed 1 issue in the tracker.",
    "done.merge": "Copy the merge command",
    "done.merge.done": "The merge command is in the clipboard.",
    "done.note": "Copy the change note",
    "done.note.done": "The change note is in the clipboard.",
    "done.explain": "Explain this change",
    "done.first": "Your first saved change in this project.",
    "explain.change.goal": "Explain this change: {request}",
    "done.audit": "The audit record is complete.",
    "done.new": "Start a new change",
    "done.history": "View the history",

    # History
    "history.title": "History",
    "history.empty": "No runs yet. Start a change from the home screen.",
    "history.back": "Back",
    "run.title": "Run {run}",
    "run.readonly": "This run is {state}. The timeline is read-only. "
                    "To change a saved result, start a new change.",
    "run.undo": "Undo the last iteration",
    "run.goback": "Go back to here",
    "run.superseded": "superseded",
    "run.confirm.title": "Go back to iteration {n}?",
    "run.confirm.body": "The tool returns the work to the state after: {label}. "
                        "The later iterations stay in the history as superseded. "
                        "The tool does not delete work.",
    "run.confirm.yes": "Go back",
    "run.confirm.no": "Stay here",
    "run.went_back": "Went back. The run continues from the restored point.",

    # Projects
    "projects.title": "Projects",
    "projects.new": "New project…",
    "projects.add": "Add a folder…",
    "projects.empty": "No projects yet. Create one, or add a folder.",
    "projects.state.ready": "Ready",
    "projects.state.setup": "Needs setup",
    "projects.state.no_git": "No source control",
    "projects.state.missing": "Missing",
    "projects.new.title": "New project",
    "projects.new.body": "Write the project name. The tool creates a plain "
                         "folder. Source control does not start here.",
    "projects.created": "Created {name}. Add source control before you start "
                        "a change.",
    "projects.added": "Added {name} to the list.",
    "projects.no_git.open": "This folder has no source control. The tool "
                            "needs Git before a change can start.",
    "projects.missing.open": "The folder is not on this computer. Remove it "
                             "from the list, or restore the folder.",
    "projects.setup.title": "Set up this project?",
    "projects.setup.body": "The tool creates .maintain.json for the manual "
                           "packet exchange. Your project files do not change.",
    "projects.setup.yes": "Set up",
    "projects.remove.title": "Remove {name} from the list?",
    "projects.remove.body": "The folder and its files stay on the computer. "
                            "Only the list entry goes away.",
    "projects.remove.yes": "Remove",
    "projects.removed": "Removed {name} from the list.",
    "projects.opened": "Opened {name}.",
    "projects.busy": "First stop the run. Then change the project.",
    "projects.all": "All projects…",
    "include.code": "Include the project code and tests",
    "code.added": "Added {count} code files to the package.",
    "code.added.one": "Added 1 code file to the package.",
    "chips.more": "+{count} more…",
    "chips.fewer": "Show fewer",
    "package.markdown": "One Markdown file",
    "package.markdown.sub": "The whole packet in one readable file. "
                            "The tool extracts the text from PDF and "
                            "Office files into it.",
    "explain.install.title": "Install the video feature?",
    "explain.install.body": "Manim is not installed. The tool installs "
                            "it into its own environment. This can "
                            "take some minutes.",
    "explain.install.yes": "Install",
    "explain.installing": "The tool installs the video feature.",
    "explain.install.done": "The video feature is ready.",
    "explain.install.failed": "The install failed. Run scripts/setup.ps1 "
                              "again.",
    "step.scene.check": "Check the scene",
    "step.render.probe": "Check the geometry",
    "step.render.video": "Render the video",
    "foot.project.tip": "This is the open project. Select it to open a "
                        "different project.",

    # Issues
    "issues.title": "Issues",
    "issues.empty": "No issues yet. Add one, or scan with Copilot.",
    "issues.allclear": "All clear. {count} closed in this project.",
    "issues.add": "Add an issue…",
    "issues.scan": "Scan with Copilot",
    "issues.filter.all": "All",
    "issues.filter.open": "Open",
    "issues.filter.in_work": "In work",
    "issues.filter.closed": "Closed",
    "issues.severity.high": "High",
    "issues.severity.medium": "Medium",
    "issues.severity.low": "Low",
    "issues.status.open": "Open",
    "issues.status.in_work": "In work",
    "issues.status.closed": "Closed",
    "issues.reason.fixed": "Fixed",
    "issues.reason.wont_fix": "Will not fix",
    "issues.reason.duplicate": "Duplicate",
    "issues.reason.not_a_fault": "Not a fault",
    "issues.reason.gone": "No longer applies",
    "issues.source.human": "You",
    "issues.source.review": "Review",
    "issues.source.test": "Test",
    "issues.source.scan": "Scan",
    "issues.source.import": "Import",
    "issues.source.described": "Described",
    "issues.busy": "First stop the run. Then work on the issues.",

    # Issue detail
    "issue.eyebrow": "Issue {id} — {source}",
    "issue.new.title": "New issue",
    "issue.field.title": "Title",
    "issue.field.detail": "Detail",
    "issue.field.severity": "Severity",
    "issue.reference": "Reference",
    "issue.field.reference": "Reference",
    "issue.reference.placeholder": "A row or ticket in your external tracker. Optional.",
    "issue.return.open": "Return to open",
    "scan.select.all": "Select all",
    "scan.select.none": "Select none",
    "issue.location": "Location",
    "issue.snippet": "The cited code",
    "issue.notes": "Notes",
    "issue.runs": "Runs: {files}",
    "issue.save": "Save",
    "issue.saved": "Saved.",
    "issue.title.empty": "First write the title.",
    "issue.repair": "Repair with Copilot",
    "issue.run.fixed": "Fixed by run {run}",
    "issue.run.linked": "Run {run}",
    "issue.repair.sub": "The tool starts a repair run from this issue.",
    "issue.discuss": "Discuss with Copilot",
    "issue.close": "Close…",
    "issue.close.title": "Close issue {id}?",
    "issue.close.body": "Select the reason. The issue stays in the list as closed.",
    "issue.reopen": "Reopen",
    "issue.remove": "Remove",
    "issue.remove.title": "Remove issue {id}?",
    "issue.remove.body": "The record goes away. To keep the record, close the issue.",
    "issue.removed": "Removed issue {id}.",
    "issue.closed": "Closed issue {id}.",
    "issue.reopened": "Reopened issue {id}.",

    # Scan
    "send.scan.title": "Copilot scans the project.",
    "send.discuss.title": "Copilot examines the issue.",
    "scan.ask.title": "Scan with Copilot",
    "scan.ask.body": "Tell Copilot where to look. Empty means a full scan.",
    "scan.update": "Update the package",
    "scan.check.title.one": "Copilot proposes 1 issue.",
    "scan.check.title.many": "Copilot proposes {count} issues.",
    "scan.check.known": "{count} known issues are not shown again.",
    "scan.check.known.one": "1 known issue is not shown again.",
    "scan.check.unverified": "The cited code is not in the file.",
    "scan.check.add": "Add the selected issues",
    "scan.check.none": "First select one issue.",
    "scan.added": "Added {count} issues.",
    "scan.added.one": "Added 1 issue.",
    "scan.discard": "Discard",
    "scan.discarded": "The scan is discarded.",

    # Explain
    "send.explain.title": "Copilot writes the scene.",
    "receive.lead.scene": "Copilot replies with one scene file. Download "
                          "it in Copilot, then select Open the newest "
                          "download. "
                          "Copy the full reply. Then select Paste reply.",
    "explain.last": "Last video: {when}. Open the folder.",
    "explain.title": "Explain code",
    "explain.goal": "What must the video explain?",
    "explain.goal.placeholder": "Write the goal in one or two sentences.",
    "explain.audience": "Who is the audience?",
    "explain.audience.placeholder": "For example: a developer new to this project.",
    "explain.files": "The files to explain",
    "explain.drop.main": "Drop project files here, or click to select them.",
    "explain.drop.sub": "Only files inside the project go into the packet.",
    "explain.start": "Start",
    "explain.files.empty": "First add one project file.",
    "explain.goal.empty": "First write the goal.",
    "explain.outside": "This file is outside the project: {name}",
    "explain.result.title": "The scene and the render",
    "explain.check.name": "Scene check",
    "explain.render.name": "Render",
    "explain.render.running": "Manim renders the scene…",
    "explain.render.passed": "The render is complete.",
    "explain.render.failed": "The render failed.",
    "explain.video.enable": "Run the setup script again for the video in "
                            "this window.",
    "video.play": "Play",
    "video.pause": "Pause",
    "explain.open.video": "Open the video",
    "explain.open.folder": "Open the folder",
    "explain.repair": "Repair with Copilot",
    "explain.repair.sub": "The tool makes a repair package with the render "
                          "error and the check findings.",
    "explain.done": "Done",
    "explain.discarded": "The explanation is discarded.",
    "explain.past": "Finished explanations",
    "explain.past.sub": "{when} — select for the video.",
    "explain.past.stale": "{when} — the files changed since this video.",
    "explain.update": "Update",
    "explain.resume.gone": "The package for this explanation is gone. "
                           "Start a new one.",
    "explain.findings.head": "The local checks found faults. Repair sends "
                             "them to Copilot.",
    "explain.saved.note": "The scene, the video, and the frame sheet stay "
                          "in the folder.",

    # Explain settings
    "settings.explain": "Explain",
    "settings.explain.sub": "The Manim command for the local render.",
    "explain.set.command": "Manim command",
    "explain.set.command.hint": "The command or the full path of Manim on this "
                                "computer. This value is for you, not for the "
                                "project.",
    "explain.set.found": "The tool found the render command: {name}",
    "explain.set.absent": "The tool cannot find this command. The render "
                          "step will say so.",
    "explain.set.install": "Install one time: pip install maintain[explain] — "
                           "and: winget install ffmpeg",

    # Discuss
    "discuss.ask.title": "Discuss with Copilot",
    "discuss.ask.body": "Write your question about this issue.",
    "discuss.applied": "The reply is in the issue notes.",
    "discuss.severity.title": "Change the severity?",
    "discuss.severity.body": "Copilot proposes severity {severity}. Apply it?",
    "discuss.severity.yes": "Apply",
    "discuss.discarded": "The discussion is discarded.",

    # Theme
    "theme.to_light": "Light mode",
    "theme.to_dark": "Dark mode",

    # Stop / pause
    "stop.button": "Stop",
    "stop.title": "Stop the run?",
    "stop.body": "The run keeps its state. You can continue from the home screen.",
    "stop.yes": "Stop",
    "stop.no": "Go back",

    # Notes
    "note.title.plan": "Ask for changes",
    "note.body.plan": "Tell Copilot what to change in the plan.",
    "note.title.rescope": "Scope again",
    "note.body.rescope": "The tool starts a new plan with your note. "
                         "The current work stays in the history.",
    "note.title.feedback": "Ask for changes",
    "note.body.feedback": "Tell Copilot what to change. The tool makes a repair package.",
    "beat.plan.accepted": "Plan accepted.",
    "beat.repair.starts": "Repair round starts.",
    "note.send": "Send the note",
    "note.cancel": "Go back",

    # Discard
    "discard.title": "Discard the change?",
    "discard.body": "The tool closes run {run}. Your project does not change. "
                    "The history keeps the record.",
    "discard.yes": "Discard",
    "discard.no": "Go back",
    "discard.done": "The change is discarded.",

    # Settings
    "settings.title": "Settings",
    "settings.downloads": "Downloads",
    "settings.downloads.sub": "The folder where the tool finds the Copilot replies.",
    "settings.tasks": "Task prompts & documents",
    "settings.tasks.sub": "Boilerplate prompts and documents, per task or for the project.",
    "settings.global": "Global prompt",
    "settings.global.sub": "The ground rules for every task.",
    "settings.package": "Package",
    "settings.package.sub": "The package style.",
    "settings.checks": "Checks",
    "settings.checks.sub": "The local test commands.",
    "settings.save": "Save",
    "settings.saved": "Saved.",
    "settings.back": "Back",

    "tasks.project": "Project",
    "tasks.docs.project": "Documents for every packet",
    "tasks.docs.project.hint": "These documents go into every packet, for all task types.",
    "tasks.docs.task": "Documents for {task} packets",
    "tasks.docs.none": "No documents yet.",
    "tasks.docs.add": "Add a document…",
    "tasks.prompt": "Boilerplate prompt — {task}",
    "tasks.prompt.builtin": "This task uses the built-in prompt.",
    "tasks.prompt.own": "This task uses its own prompt.",
    "tasks.prompt.change": "Change the prompt for this task",
    "tasks.prompt.reset": "Use the built-in prompt again",

    "global.lead": "The tool puts this text into every package as GLOBAL.md. "
                   "It keeps Copilot inside the project scope.",
    "global.reset": "Reset to the template",
    "global.reset.done": "The template is restored. Select Save to keep it.",

    "package.zip": "One ZIP (fallback)",
    "package.zip.sub": "The tool sends one ZIP file. Use this only when the "
                       "Markdown file does not work.",

    "checks.lead": "The tool runs these commands on this computer in the Test step.",
    "checks.add": "Add a check",
    "checks.name": "Name",
    "checks.command": "Command",

    # Errors and state
    "error.title": "The tool needs your attention.",
    "paused.title": "The run is stopped.",
    "paused.body": "Continue the run from the home screen.",
    "test.retry": "Run the checks again",
    "issue.close.pick": "Why is it closed?",
    "issues.captured": "Added {count} to the issue list.",
    "issues.autoclosed": "Closed {count} on delivery.",
    "issues.closed.one": "Closed: {title}.",
    "issues.closed.more": "Closed: {title} and {count} more.",
    "exchange.downloads": "Downloads folder",
    "exchange.downloads.hint": "The tool takes the newest reply file from this folder.",
    "downloads.browse": "Browse…",
    "downloads.missing": "The folder does not exist.",
    "working.plan": "Copilot makes the plan…",
    "working.checks": "The checks run…",
    "working.busy": "The tool works…",
}


def text(key: str, **values: object) -> str:
    """One catalog lookup. Unknown keys fail loudly in development."""
    template = STR[key]
    return template.format(**values) if values else template
