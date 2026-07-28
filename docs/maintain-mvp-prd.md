# Maintain MVP — Product Requirements Document

- Status: Approved for implementation
- Target user: Single internal user
- Primary chatbot: Microsoft 365 Copilot
- Compatible with: Any chatbot that accepts Markdown file uploads and returns text
- Target: Usable end-to-end workflow by tomorrow

---

## 1. Product summary

Maintain is a small local command-line tool that coordinates a controlled
software-maintenance workflow across separate chatbot conversations.

It generates one Markdown handoff file for each workflow stage, captures the
chatbot response from the clipboard, validates implementation patches, applies
them through Git, runs local tests, and supports bounded correction and rescope
loops.

Maintain is not an AI agent and does not communicate with a chatbot
automatically.

### Core workflow

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

---

## 2. Problem

General-purpose chatbots can help scope, implement, and review software
changes, but manually coordinating these stages is inconvenient and
error-prone.

The user currently has to:

- assemble repository context manually
- repeat task details across conversations
- maintain separation between implementation and review
- copy changes between stages
- verify which files the chatbot modified
- manage failed tests and failed reviews
- preserve previous attempts
- restart the workflow when the original scope is incomplete

Existing tools provide parts of this workflow:

- BMAD provides useful role and workflow patterns.
- Repomix packages repository context.
- Git validates and applies changes.

No agent framework is required. The missing capability is a small local
handoff and workflow layer.

---

## 3. Product objective

Allow one user to complete a software-maintenance task using separate chatbot
conversations without manually assembling each prompt or losing control of the
workflow.

The tool must support:

1. Initial scope.
2. Implementation.
3. Local patch validation.
4. Automated test execution.
5. Independent review.
6. Correction rounds following failed tests or reviews.
7. Rescoping when the original task definition is insufficient.

---

## 4. Guiding constraints

The MVP must remain deliberately minimal.

### Required constraints

- Local execution only.
- One active task at a time.
- One user.
- One repository.
- File-based state.
- Markdown handoff files.
- Manual upload to the chatbot.
- Clipboard-based response capture.
- Git-based patch application.
- Repomix-based code packaging.
- Maximum of three implementation or correction rounds by default.

### Design principle

Maintain coordinates artifact exchange. It does not become an agent platform.

---

## 5. Key product decisions

### 5.1 BMAD is not a runtime dependency

Maintain will not fork, install, or execute the full BMAD framework.

It will use simplified BMAD-inspired concepts:

- scope role
- implementation role
- independent review role
- persistent project context
- fresh chatbot conversation for each stage

Maintain will own its own small prompt templates.

### 5.2 Repomix is an external dependency

Maintain will invoke the installed Repomix command-line tool to generate
repository context.

Maintain will not reimplement:

- repository traversal
- ignore handling
- source-file formatting
- code-context generation

### 5.3 ZIP files are not chatbot handoff artifacts

Each stage will produce one Markdown file whenever practical.

Example:

```text
maintain-0042-scope.md
maintain-0042-implement.md
maintain-0042-review.md
maintain-0042-fix-02.md
maintain-0042-rescope-01.md
```

Internal task artifacts remain stored as ordinary files and directories.

---

## 6. User workflow

### 6.1 Initialise a repository

```sh
maintain init
```

Creates:

```text
.maintain/
├── config.json
├── project-context.md
├── current-task
└── tasks/
```

The user edits `project-context.md` to describe project-specific rules and
conventions.

### 6.2 Create a task

```sh
maintain new "Correct the greeting shown at startup"
```

Maintain:

1. Creates a task ID.
2. Records the current Git commit.
3. Stores the original request.
4. Creates the first scope package.
5. Marks the task as waiting for a scope response.

Example output:

```text
Created task: 20260727-001
Package: .maintain/tasks/20260727-001/exports/scope.md
Next: Upload scope.md to a fresh chatbot conversation.
```

### 6.3 Capture a response

The user copies the entire chatbot response and runs:

```sh
maintain capture
```

Maintain reads the clipboard and determines the expected response type from
the current workflow state.

It stores the raw response before parsing it.

### 6.4 Advance the workflow

```sh
maintain next
```

Maintain examines the current task state and performs the next appropriate
packaging action.

Examples:

- scope captured → generate implementation package
- tests failed → generate fix package
- implementation passed → generate review package
- review requested changes → generate fix package
- review requested rescope → generate rescope package
- review approved → close task

### 6.5 Apply an implementation

After capturing an implementation or fix response:

```sh
maintain apply
```

Maintain:

1. Extracts the unified Git patch.
2. Identifies modified files.
3. Rejects files outside the approved scope.
4. Runs `git apply --check`.
5. Displays a patch summary.
6. Requests confirmation.
7. Applies the patch.
8. Runs the configured test command.
9. Records the complete output.

### 6.6 Check task status

```sh
maintain status
```

Example:

```text
Task: 20260727-001
Stage: Review failed
Implementation round: 1 of 3
Scope revision: 1
Tests: Passed
Review verdict: CHANGES_REQUIRED
Next action: Run `maintain next` to generate a correction package.
```

---

## 7. Workflow stages

### 7.1 Scope

The scope package contains:

- scope-role instructions
- original request
- project context
- repository structure
- broad Repomix repository context
- required response structure

The scope response must contain:

```text
STATUS: SCOPE_COMPLETE
```

Required sections:

```markdown
## Understanding
## Allowed Files
- path/to/file
## Proposed Changes
## Acceptance Criteria
## Risks and Unknowns
```

Maintain extracts:

- allowed files
- acceptance criteria
- proposed change summary

The user remains responsible for reviewing whether the scope is sensible
before proceeding.

### 7.2 Implementation

The implementation package contains:

- implementation-role instructions
- original request
- approved scope
- acceptance criteria
- allowed file list
- Repomix context for relevant files
- required response structure

The response must begin with one of:

```text
STATUS: IMPLEMENTATION_COMPLETE
```

or:

```text
STATUS: RESCOPE_REQUIRED
```

A completed implementation must contain exactly one fenced unified-diff block:

````markdown
```diff
diff --git a/path/to/file b/path/to/file
...
```
````

The patch must target the repository state included in the package.

### 7.3 Local validation

Before applying a patch, Maintain must:

- verify that the repository remains compatible with the recorded base state
- extract the changed file paths
- reject paths outside the approved list
- run `git apply --check`
- require explicit confirmation

After applying the patch, Maintain runs the configured test command.

Example configuration:

```json
{
  "test_command": "pytest",
  "maximum_rounds": 3
}
```

If no test command is configured, Maintain records the result as:

```text
NOT_CONFIGURED
```

It may still proceed to review after warning the user.

### 7.4 Review

The review package contains:

- independent reviewer instructions
- original request
- approved scope
- acceptance criteria
- cumulative Git diff from the task base commit
- current contents of changed files
- latest test results
- implementation summaries

The reviewer must return one of:

```text
VERDICT: APPROVE
VERDICT: CHANGES_REQUIRED
VERDICT: RESCOPE
```

Required sections:

```markdown
## Findings
## Acceptance-Criteria Coverage
## Risks
```

The review must evaluate the complete cumulative implementation, not only the
latest correction patch.

---

## 8. Correction loop

A correction round occurs when:

- tests fail
- patch validation fails in a way the chatbot can correct
- the reviewer returns CHANGES_REQUIRED

Maintain generates a correction package containing:

- original request
- current approved scope
- acceptance criteria
- latest relevant file contents
- cumulative task diff
- test output
- reviewer feedback
- previous implementation summary

The correction response follows the same patch format as the original
implementation.

### Correction rules

- The correction patch applies to the current working tree.
- Existing changes are not automatically reverted.
- Tests run again after every correction.
- Review runs again after tests pass.
- Each correction increments the implementation round.
- The default maximum is three implementation rounds.
- When the limit is reached, Maintain stops for manual intervention.

Example:

```text
Maximum implementation rounds reached.
The task requires manual intervention.
No further package has been generated.
```

---

## 9. Rescope workflow

A rescope occurs when the problem is with the approved task definition rather
than the implementation.

Examples:

- required files are outside the approved list
- acceptance criteria are incomplete or contradictory
- the requested behaviour depends on an excluded subsystem
- the implementation cannot work within the approved architecture
- the task is materially larger than originally understood

A rescope can be requested by either the implementer or reviewer.

### Rescope response markers

From implementation:

```text
STATUS: RESCOPE_REQUIRED
```

From review:

```text
VERDICT: RESCOPE
```

### Maintain behaviour

Maintain must:

1. Stop the implementation correction loop.
2. Preserve all current changes as provisional.
3. Generate a rescope package.
4. Include the original scope, discovered issue, current cumulative diff, and
   latest feedback.
5. Capture a revised scope response.
6. Require the user to choose whether current changes should be retained or
   reset.
7. Continue implementation under the revised scope.

The rescope response must contain:

```text
STATUS: RESCOPED
```

And one of:

```text
EXISTING_WORK: RETAIN
EXISTING_WORK: PARTIAL
EXISTING_WORK: DISCARD
```

Required sections:

```markdown
## Revised Understanding
## Revised Allowed Files
## Revised Acceptance Criteria
## Revised Plan
## Existing Work Assessment
```

A scope revision does not increment the implementation retry count.

For the MVP, PARTIAL is handled manually. Maintain shows the recommendation
and asks whether to continue from the current working tree or reset the task
changes.

---

## 10. Minimal command interface

The MVP exposes only:

```text
maintain init
maintain new "<request>"
maintain capture
maintain next
maintain apply
maintain status
```

No additional workflow commands are required.

### Command responsibilities

| Command | Responsibility |
| --- | --- |
| `init` | Create Maintain configuration and project context |
| `new` | Create a task and generate its scope package |
| `capture` | Read and store the expected chatbot response from the clipboard |
| `next` | Generate the next appropriate handoff package |
| `apply` | Validate, confirm, apply and test a patch |
| `status` | Display current task state and next action |

---

## 11. Task storage

```text
.maintain/tasks/20260727-001/
├── state.json
├── request.md
├── scope/
│   ├── package.md
│   └── response.md
├── rounds/
│   ├── 01/
│   │   ├── implementation-package.md
│   │   ├── implementation-response.md
│   │   ├── implementation.patch
│   │   ├── test-results.txt
│   │   ├── review-package.md
│   │   └── review-response.md
│   └── 02/
│       ├── fix-package.md
│       ├── implementation-response.md
│       ├── implementation.patch
│       ├── test-results.txt
│       ├── review-package.md
│       └── review-response.md
├── rescopes/
│   └── 01/
│       ├── package.md
│       └── response.md
└── exports/
    └── current-package.md
```

Previous responses and attempts must not be overwritten.

---

## 12. Minimal state model

Example:

```json
{
  "task_id": "20260727-001",
  "stage": "waiting_for_review",
  "base_commit": "a74b1c9",
  "implementation_round": 1,
  "maximum_rounds": 3,
  "scope_revision": 1,
  "allowed_files": [
    "src/greeting.py",
    "tests/test_greeting.py"
  ],
  "test_status": "passed",
  "review_verdict": null,
  "provisional_changes": true
}
```

The state machine will be implemented with straightforward conditional logic.

A generic workflow engine is not required.

---

## 13. Prompt templates

Maintain contains five small templates:

```text
templates/
├── scope.md
├── implement.md
├── review.md
├── fix.md
└── rescope.md
```

Each generated package combines:

1. role instructions
2. original request
3. project context
4. approved scope or current feedback
5. relevant repository context
6. required response format

Simple string replacement is sufficient.

A templating framework is not required.

---

## 14. Technical constraints

### Required

- Python 3.
- Git.
- Repomix.
- Standard-library implementation wherever practical.
- Clipboard integration through pyperclip or a small platform-specific
  wrapper.
- File-based JSON state.
- subprocess for Git, Repomix and tests.
- pathlib for filesystem operations.
- regular expressions for extracting response markers and diff blocks.

### Suggested source structure

```text
maintain/
├── maintain.py
└── templates/
    ├── scope.md
    ├── implement.md
    ├── review.md
    ├── fix.md
    └── rescope.md
```

The implementation may remain a single Python file for the MVP.

---

## 15. Error handling

Maintain must fail safely when:

- no Git repository is present
- Repomix is not installed
- the clipboard is empty
- the response marker is missing
- no diff block is present in an implementation response
- more than one diff block is present
- the patch changes unapproved files
- `git apply --check` fails
- the working tree changed incompatibly
- the test command cannot run
- the correction-round limit is reached

Errors must preserve all captured content and display the next manual action.

Maintain must not silently discard chatbot responses or repository changes.

---

## 16. Explicitly out of scope

The MVP will not include:

- a BMAD fork
- the BMAD runtime
- chatbot API integration
- browser automation
- autonomous agents
- automated prompt submission
- automated response retrieval
- agent-to-agent communication
- ZIP-based chatbot handoffs
- a desktop UI
- a web interface
- a database
- authentication
- multiple users
- multiple concurrent tasks
- cloud storage
- GitHub or Azure DevOps integration
- automatic branches or pull requests
- Git worktrees
- automatic commits
- semantic code search
- vector storage
- prompt marketplaces
- plugin systems
- configurable workflow designers
- sprint or project-management features
- arbitrary role creation
- package cryptographic signing
- complex manifest schemas
- automatic acceptance of review approval
- indefinite retry loops
- automatic partial rollback during rescope

These capabilities must not be added while implementing the MVP unless they
are required to complete the core end-to-end workflow.

---

## 17. MVP acceptance criteria

The MVP is complete when the user can perform one real repository change
through the following workflow:

1. Initialise Maintain in a Git repository.
2. Create a maintenance task.
3. Generate one scope Markdown file.
4. Upload it to a chatbot.
5. Capture the scope response from the clipboard.
6. Generate one implementation Markdown file.
7. Upload it to a fresh chatbot conversation.
8. Capture a unified patch.
9. Reject changes outside the approved file list.
10. Preview and apply the patch through Git.
11. Run a configured local test command.
12. Generate a fix package when tests fail.
13. Generate one independent review package when tests pass.
14. Capture APPROVE, CHANGES_REQUIRED, or RESCOPE.
15. Run another correction round after CHANGES_REQUIRED.
16. Run a scope revision after RESCOPE.
17. Stop after the configured maximum correction rounds.
18. Preserve every package, response, patch, test result, and review result
    under `.maintain/tasks`.

---

## 18. MVP success condition

The product succeeds when it removes the repetitive package-assembly work
while retaining human control over:

- scope approval
- chatbot selection
- conversation separation
- patch application
- test interpretation
- review acceptance
- rescope decisions

The final product is:

> A small local state machine that combines BMAD-inspired workflow templates,
> Repomix-generated repository context, clipboard response capture, Git patch
> validation, and a bounded review-and-correction loop.
