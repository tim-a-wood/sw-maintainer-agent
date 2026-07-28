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
    "app.footer": "Run {run} · isolated workspace",

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
    "home.settings": "Settings",
    "home.settings.sub": "OneDrive, prompts, documents, package, checks.",
    "home.continue": "Continue run {run}",
    "home.continue.sub": "{stage}. The tool waits for you.",

    # Describe
    "describe.title": "What do you want to change?",
    "describe.fault.title": "What is the fault?",
    "describe.placeholder": "Write the change in one or two sentences.",
    "describe.drop.main": "Drop files here to add them to the run.",
    "describe.drop.sub": "All file types are permitted. They go into every packet.",
    "describe.import": "Import…",
    "describe.start": "Start",
    "describe.empty": "First write the change.",

    # Send
    "send.plan.title": "Copilot makes the plan.",
    "send.build.title": "Copilot writes the code.",
    "send.repair.title": "Copilot repairs the code.",
    "send.review.title": "Copilot examines the change.",
    "send.lead": "Give this package to Copilot. Use one of the ways below.",
    "send.drag": "Drag this package into Copilot.",
    "send.copy_link": "Copy OneDrive link",
    "send.copy_link.sub": "Recommended. Copilot opens the package from OneDrive.",
    "send.copy_file": "Copy file",
    "send.export": "Export…",
    "send.attachments": "Attachments — go into this packet",
    "send.attach.add": "Add files…",
    "send.attach.drop": "Drop files here, or select Add files…",
    "send.contents": "What is in the package?",
    "send.continue": "Continue",
    "send.continue.before": "First give the package to Copilot.",
    "send.continue.after": "Select Continue after you send the package in Copilot.",
    "send.link.copying": "Copy the package to OneDrive…",
    "send.link.syncing": "OneDrive synchronizes…",
    "send.link.done": "In sync. The link is in the clipboard.",
    "send.link.paste": "Paste the link into Copilot. Then send the message.",
    "send.link.manual": "Look at the file in File Explorer. When you see the check mark, "
                        "paste the link.",
    "send.file.copied": "The package is in the clipboard. Paste it into Copilot.",
    "send.exported": "Saved {name}.",
    "send.updated": "The package is updated.",

    # Receive
    "receive.title": "Bring the Copilot reply here.",
    "receive.lead.zip": "Copilot attaches one file. Its name is maintain-output.zip. "
                        "Drag it here.",
    "receive.lead.json": "Copilot replies with one JSON text. Copy it in Copilot. "
                         "Then select Paste reply.",
    "receive.drop": "Drop the reply here.",
    "receive.drop.sub.zip": "Or download it in Copilot and select Import.",
    "receive.drop.sub.json": "A file is also accepted.",
    "receive.paste": "Paste reply",
    "receive.import": "Import…",
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
    "save.diff": "Show the diff",
    "save.accept": "Accept and save",
    "save.accept.sub": "The tool makes the commit on a new branch. "
                       "Your project does not change before this point.",
    "save.feedback": "Ask for changes",
    "save.discard": "Discard",

    # Done
    "done.title": "The change is saved.",
    "done.branch": "Branch: {branch}",
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
    "settings.onedrive": "OneDrive",
    "settings.onedrive.sub": "The folder and the link for packages.",
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

    "onedrive.folder": "Package folder",
    "onedrive.folder.hint": "The tool copies each package into this folder.",
    "onedrive.browse": "Browse…",
    "onedrive.link": "Link address of the folder",
    "onedrive.link.hint": "Open the folder in OneDrive on the web. Copy the address. "
                          "Paste it here.",
    "onedrive.example": "Example link: {link}",
    "onedrive.timeout": "Synchronization wait limit (seconds)",
    "onedrive.timeout.hint": "After this time, the tool asks you to check File Explorer.",

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

    "package.zip": "One ZIP (standard)",
    "package.zip.sub": "The tool sends one ZIP. Copilot opens it from the OneDrive link.",
    "package.folder": "ZIP + open folder (fallback)",
    "package.folder.sub": "The tool also expands the files into a folder. "
                          "Use this only when Copilot cannot open the ZIP members.",

    "checks.lead": "The tool runs these commands on this computer in the Test step.",
    "checks.add": "Add a check",
    "checks.name": "Name",
    "checks.command": "Command",

    # Errors and state
    "error.title": "The tool needs your attention.",
    "paused.title": "The run is stopped.",
    "paused.body": "Continue the run from the home screen.",
    "working.plan": "Copilot makes the plan…",
    "working.checks": "The checks run…",
    "working.busy": "The tool works…",
}


def text(key: str, **values: object) -> str:
    """One catalog lookup. Unknown keys fail loudly in development."""
    template = STR[key]
    return template.format(**values) if values else template
