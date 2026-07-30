# Usability review — every scenario walked end to end

> Outcome: P1–P9 were all accepted and are implemented, together with
> the owner's additions: the two marked Send/Receive regions, the
> downloadable reply contract, and the newest-download pickup with a
> configurable Downloads folder. See PRD section 14.

Method: each scenario below was driven click-by-click in the real
application (offscreen, real engine, scripted Copilot replies), counting
user actions and noting friction. Steps marked ● are user clicks or
keystrokes; ○ is something the tool does alone. The goal of the
proposals is fewer actions and fewer screens for the same guarantees —
nothing here weakens a gate, a validation, or the audit trail.

## 1. Change software (the main loop, happy path)

Walked: describe → plan → build → review → test → save → done.

| Step | Actions | Friction |
|---|---|---|
| Home → Change software | ● 1 | None. |
| Describe, add files, Start | ● 2–3 | The drop zone is not clickable; adding files without drag needs the separate Import… button. |
| Send (plan packet) | ● 1 Copy OneDrive link, wait for sync | The tool knows the dominant next act the moment the packet exists — it could publish and copy the link alone. |
| Continue | ● 1 | A gating click that exists only to reach the next screen. The reply validator already refuses wrong input; the gate protects nothing. |
| Receive → Paste reply | ● 1 | A whole screen whose content is one drop zone and one button. |
| Busy flash | ○ | The busy screen flashes between every transition even when the next packet is ready in under a second. Screen-flicker with no information. |
| Plan check → Accept | ● 1 | Good gate. Keep. |
| Send/Continue/Receive ×3 more (build, review, repair when needed) | ● 3 per exchange | The same three-click ceremony repeats for every exchange. |
| Test | ○ | Good: live check rows. |
| Save → diff → Accept | ● 2 | Good gate. Keep. |
| Done | ○ | Good. |

**Count: a clean two-exchange run (plan, build, review) costs 9 clicks of
pure transport ceremony (3 per exchange) on top of the 4 real decisions.**
A run with one repair round costs 12.

## 2. Stop, then continue

Walked: stop at the plan packet → home → Continue run.

- The Continue card is clear and works. ●1 to resume.
- **After resume the Send screen returns with Continue disabled and an
  empty status line** — but the person may have already pasted the link
  into Copilot before stopping. They must redo a transport action (or
  re-copy a link they already have) purely to unlock the Continue
  button. The tool forgot what they had done.

## 3. Ask for changes at Plan; Scope again from Review/Test

- Both work and land back at a fresh plan packet with the note included
  and an AGAIN marker. The note dialog is fine.
- Friction: the full three-click transport ceremony repeats for the new
  plan round (see 1).

## 4. Test failure

Walked with a genuinely failing check.

- The failed row expands with the check output. Clear.
- **The only actions are "Repair with Copilot" and "Scope again". A
  flaky or environmental failure (locked file, transient network, wrong
  interpreter) forces a pointless Copilot repair round or a rescope.**
  The Save screen already has "Run the checks again"; the failure
  screen — where it is most needed — does not.

## 5. Save-screen alternatives

- Ask for changes (note → repair packet): good.
- Run the checks again: good.
- Discard (confirm → closed, project untouched): good.
- No friction found beyond the exchange ceremony it leads back into.

## 6. History, run detail, go back / undo

- History rows with mode icons and state chips: clear.
- Go back to here → confirm → run resumes with superseded marks: this
  walked cleanly, and the confirmation wording carries the guarantee
  ("The tool does not delete work"). No changes proposed.

## 7. Issues: add, change, close, reopen, remove

- List, filters, and severity/status chips: clear.
- Add and edit share the detail screen: fine.
- **Close… opens a combo-box dialog** for the reason — a heavyweight
  interaction for five known options, and the only combo box in the
  application.
- **On the detail screen the actions (Repair, Discuss, Close, Remove)
  sit below the notes**, so on a long issue they are off-screen; the
  fields the person rarely edits sit on top.

## 8. Scan with Copilot

- Entry (Issues → Scan): right place.
- **The focus question is a modal dialog before anything is visible.**
  The person answers "where should Copilot look" before seeing the
  packet or being able to attach the spreadsheet the question refers to.
- The Exchange ceremony applies as in 1 (link, Continue, paste).
- The accept gate with verification flags: exactly right. Keep.

## 9. Discuss an issue

- Question dialog → packet with attachments → paste reply → note +
  severity confirm. Works well; the attachment area is where the
  reference files go, as designed.
- Same transport ceremony cost as every exchange.

## 10. Repair from an issue

- One click pre-fills Describe; Start begins the fault run; the issue
  links and closes on delivery. Good. Minor: Start could take keyboard
  focus so the bridge is Enter-to-go.

## 11. Projects, Settings, Theme

- Projects (create/add/open/remove, state chips): no friction found.
- Settings pages (save → toast → hub): consistent; no changes.
- OneDrive page: fine, and the natural home for the new auto-link
  setting (proposal 2).
- Theme toggle: fine.

---

## Proposals

**P1 — One Exchange screen instead of Send + Receive.**
The packet ("Give this package to Copilot") and the reply ("Bring the
reply here") become one screen, reply zone always active. Removes the
Continue gate, the Receive screen, and the stop/continue re-unlock
problem (scenario 2) outright. The reply validator remains the gate
that matters.

**P2 — The link copies itself.**
When a packet appears, the tool publishes to OneDrive and puts the link
in the clipboard alone, showing the status inline ("In sync. The link
is in the clipboard."). A setting on the OneDrive page (default on);
Copy file and Export… remain as the manual alternatives.

**P3 — No busy flash between steps.**
Instant transitions happen silently; a thin inline progress line covers
the sub-second work. The busy screen remains only for genuinely long
operations that have no screen of their own.

**Net effect of P1–P3: a clean run drops from 9 transport clicks to 2
(one paste per exchange, with the plan exchange's paste being the
second); a run with a repair round drops from 12 to 3.**

**P4 — Drop zones are also buttons.**
Click a drop zone to browse. The separate Import…/Add files… buttons
disappear; their strings become the zones' sub-lines.

**P5 — "Run the checks again" on the failure screen.**
Alongside Repair and Scope again, for flaky checks. (Small engine
addition: re-enter TESTING from TEST_FAILED.)

**P6 — Issue detail: decisions first.**
Actions row (Repair · Discuss · Close · Remove) directly under the
title chips; Close becomes an inline five-option chooser, not a combo
dialog; fields and notes follow.

**P7 — Scan focus moves onto the Exchange screen.**
A one-line focus field on the scan exchange replaces the modal; typing
a focus and changing attachments updates the same packet. See the
package, then aim it.

**P8 — Say what was captured.**
A toast when review/test findings enter the issue list ("Added 1 issue
from the review."), and when delivery closes them.

**P9 — Keyboard path.**
Enter triggers the screen's primary action, Esc goes back, throughout.

Not proposed: any change to the plan gate, review findings gate, scan
accept gate, save gate, validation messages, or audit behavior.
