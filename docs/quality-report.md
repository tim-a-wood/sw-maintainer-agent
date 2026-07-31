# Quality report — static analysis, mutation testing, coupling, live exchange

- Date: 2026-07-31
- Scope: the maintain-ui application (the same scope as the coverage
  metric in `pyproject.toml`; the legacy CLI and the browser-automation
  providers are out of scope).
- Companion sections: PRD §14.12 (the coverage pass), PRD §14.13 (this
  report's batch).

## 1. Test-suite state

- 253 tests pass (2 skipped, 1 pre-existing CLI failure deselected).
- Line coverage over the application scope: 87%.
- End-to-end journeys cover: the full feature loop, the settings
  round-trip through every page, repair rounds, failed checks with
  retry, stop at a gate and continue, run-checks-again, feedback,
  discard and workspace cleanup, rescope from the plan gate, the
  findings gate, and the failed-check gate, issue mode with a
  reproduction check (both the reproduced and the not-reproduced
  outcome), the launch entry point, and a paint pass over every screen
  in both themes.

## 2. Static analysis

Tools: ruff 0.15 (pyflakes, pycodestyle E4/E7/E9, bugbear) and
mypy 1.19, both run over `src/maintain` and `tests`.

Fixed from the findings:

- Six unused imports removed (application modules and tests) and one
  missing re-export (`ManualUiProvider`) added to `providers.__all__`.
- `exchange.check(path=...)` received a `str` from the import-reply
  dialog where every other caller passes `Path` — now converted at the
  call site.
- Three `subprocess.run` calls in `render.py` and `scene_probe.py` made
  their manual return-code handling explicit with `check=False`.
- One `getattr(process, "_handle")` replaced with direct attribute
  access.

Accepted, with the ruleset codified in `pyproject.toml` so the tree
stays clean-or-red: the compact one-statement-per-line style of the
parser modules (`artifacts/`, `verification/`, `workflows/`), and the
out-of-scope automation modules' style findings.

mypy reports 68 errors across 15 files. Triage: one was a real
in-scope defect (the `str`/`Path` argument above, fixed); the rest are
annotation gaps (`var-annotated`, lambda inference), Windows-only
ctypes attributes the Linux typeshed lacks, and unions that runtime
guards already narrow. The `cli.py` cluster (`.valid`/`.path` on
`Path | None`) was read and confirmed to be inference noise, not a
defect. The codebase is not annotated strictly enough for mypy to
gate the build; adopting that is a separate decision.

## 3. Mutation testing

Method: an AST-based mutator flips one operator per run — comparison
operators, `and`/`or`, `not` removal, and boolean constants — then
runs the test files mapped to that module (fast unit sets, plus the
settings journey for the configuration store). A mutant "survives"
when every mapped test still passes. Slow modules were sampled
(`issues.py` 54 of 200 sites, `scene_quality.py` 20).

First pass: 149 of 211 mutants killed (71%). The survivors were
triaged; twelve pointed at real assertion gaps, and killer tests were
added for them. Second pass over the re-tested modules:

| Module | First pass | After killer tests |
|---|---|---|
| policy.py | 10/13 | 13/13 |
| downloads.py | 2/3 | 3/3 |
| ui/config_store.py | 12/20 | 16/20 |
| audit.py | 30/39 | 32/39 |
| history.py | 12/16 | 13/16 |
| onedrive.py | 18/24 | 19/24 (module since removed with the OneDrive transport) |

The killer tests now pin, among others: CANCELLED is reachable from
every active state except DELIVERING; VERIFIED demands review and
tests on the same tree, each side separately; the revert target itself
is never marked superseded; a Downloads file changed exactly at the
packet time still counts; audit export creates nested destination
folders; retention accepts its one-day minimum; `set_checks` preserves
reproduce-phase commands (protecting issue mode); the absolute
global-prompt path and the missing-override-file branches of the
configuration store.

Survivors accepted as equivalent or out of reach, with reasons:
`frozen=True` flips on dataclass decorators (nothing mutates those
objects, so freezing is untestable cheaply), `delete=False` flips on
temporary files that `os.replace` has already moved, subprocess
keyword flips shadowed by the test's fake `subprocess.run`, near-
equivalent branches whose difference is absorbed by an adjacent
error handler, and `ui/bridge.py` stop-path flips that the journey
tests kill but the fast mapped set does not reach. `issues.py`
(38/54) and `scene_quality.py` (13/20) keep their sampled first-pass
scores; their survivors are recorded in the session artifacts and are
future work, not defects.

## 4. Data and control coupling

Import graph over the application scope: 57 modules, 149 intra-package
import edges.

- One import cycle exists: `engine → provider_factory → providers →
  providers.manual_ui → zip_package → engine`. The closing edge is a
  deliberately deferred function-level import of
  `PROVIDER_SAFETY_HEADER` inside `zip_package.py`, so imports resolve
  at runtime; moving that constant into a leaf module (for example
  `models.py`) would remove the cycle structurally.
- Dependency direction is healthy: the stable leaves have instability
  0 with high fan-in (`errors` 26 in / 0 out, `models` 16/0,
  `config` 11 in) while the volatile tops depend outward
  (`ui.app` I=0.95, `ui.screens` I=0.92, `ui.controller` I=0.92,
  `engine` I=0.79). High-level modules depend on low-level ones,
  never the reverse.
- Runtime control coupling beyond imports: 124 Qt signal connections
  (93 wired in `ui/app.py`, 27 in `ui/screens.py`, 4 in
  `ui/widgets.py`). The main window is the single mediator; screens
  never call each other.
- Cross-thread coupling: the engine worker thread and the UI thread
  meet only at `UiBridge` (one answer queue; `_STOP` as the release
  sentinel; `GateStop`/`ManualExchangeCancelled` as the control
  exceptions) and at queued signal emissions from the controller. The
  stop/continue, gate, and teardown journeys exercise this boundary.
- Shared data channels, named so they stay deliberate: the
  `RunRecord.evidence` dictionary (the run's data bus between engine
  phases, persisted in `run.json`), the settings JSON file behind
  `repository_memory` (projects, theme, Downloads path, recents),
  `theme.ACTIVE` (one process-wide palette read by painting
  widgets), and `os.environ` copied into check subprocesses.

## 5. Live exchange with a real assistant

A Windows machine with a live Microsoft 365 Copilot session is not
reachable from this environment, so the specified substitute ran: the
real application, offscreen, drove a real run end to end while a live
Claude agent played Copilot. The agent received only what Copilot
would receive — the packet contents — and was instructed to follow
the packet's own instructions exactly. Replies came back through the
real validation path as downloadable files.

Run: "Add a knots_to_metres_per_second function to units.py with
input validation, and extend test_units.py to cover it" against a
two-file project with a real check command (`python test_units.py`).

Round trip: plan (two dependency-ordered tasks) → build task one
(implementation ZIP, correct 1852/3600 factor, mirrored validation) →
review approve → build task two (test extension) → review approve →
local checks ran the agent's real test against the agent's real code
→ save → deliver. Every reply validated on the first try; no refusal
loop was needed.

Findings from the correspondent's side of the contract, worth fixing
in the packet templates:

- The packet never names the reply file. The assistant fell back to
  `maintain-reply.md` on its own; a named file would remove the guess.
- The envelope template carries `conversation_id:
  "assigned-by-maintain"` with no instruction; the assistant copied
  the placeholder verbatim (harmless — the tool ignores it — but it
  reads as an unanswered question).

## 6. Verdict

The application's logic is covered by journeys that mirror real use,
the assertions bite (100% mutation kill on the transition policy, the
strongest guard in the system), the dependency structure points the
right way with one managed cycle, and a live model can complete the
whole loop from the packets alone. The remaining risk sits where only
the real machine can look: Windows and the real Copilot's file
handling. (The OneDrive sync-attribute risk retired with the OneDrive
transport itself.)
