# Explain — Phase 0 trial record

Date: 2026-07-29. Environment: Linux container, Python 3.11, Manim
Community 0.20.1, ffmpeg 6.1, Cairo and Pango present.

## Substitution

The operator has no access to the owner's Microsoft 365 Copilot from
this environment. The assistant played the Copilot part and wrote the
scene from the packet, under the EXPLAIN-TASK contract. All other
steps followed the handover specification. One real-Copilot run
remains open as a data point.

## Inputs

- `EXPLAIN-TASK.md` — the explanation contract.
- `CODEBASE.md` — generated from the true sources: the fingerprint,
  the capture path, close on delivery, and the three grounding tests.
- Explained module: `src/maintain/issues.py`.

## Steps and results

1. Packet preparation: both files written and checked. ~6 minutes.
2. Scene authoring (Copilot part, played): one complete `scene.py`,
   one fenced file, one Scene class. ~10 minutes.
3. Pre-render check (`check_scene.py`, PRD 13.3): PASS. No forbidden
   imports, calls, or paths.
4. Draft render (`manim -ql`): rendered at the first try.
5. 1080p render (`manim -qh`): rendered. Duration 25.7 seconds.
6. Frame review found one layout fault: the outcome group centered
   under a moved chip, not under the screen. One local code repair
   (centering). Re-render: correct.

## Section 9 measures

| Measure | Value |
|---|---|
| Preparation time | ~6 minutes |
| Copilot generation time | Not measured — the part was played |
| Repair prompts | 0 |
| Local code repairs | 1 (layout centering) |
| Render result | Draft and 1080p both rendered; 25.7 s |
| Reviewer assessment | Checklist below — all points pass |

## Section 8 checklist

- Matches the implementation and the tests: yes. Every claim maps to
  `fingerprint`, `capture`, `DISMISSAL_REASONS`, or `close_for_run`,
  and to the three cited tests.
- Understandable without the source: yes.
- Important state changes visible: yes — new, update, drop, reopen,
  close on delivery.
- Rejected and failure states visually distinct: yes — color and a
  strike mark.
- No invented behavior: none found.
- Text legible at 1080p: yes.
- Duration 20–30 seconds: yes, 25.7 s.
- Renders without manual repair: after one layout repair, yes.

## Verdict

The trial passes all five success criteria, with the played-Copilot
substitution declared above. The mechanical pipeline is proven:
packet, contract, pre-render check, render, review. The open point
is Copilot's own scene quality in the owner's tenant. One real run
answers it. The video file is not committed; it is delivered in the
conversation. Render again with:

    /opt/manim-venv/bin/manim -qh scene.py IssueCaptureScene
