# SUBMISSION.md: draft answers for the Cerebral Valley form

Form: https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit
**Status of question text: NOT VERIFIED.** The form requires login; the public fetch returned only the event overview. Every question below is **ASSUMED** from typical Cerebral Valley hackathon forms. Ozgur: open the form now, paste the real questions into this file (or send them to muse), and we will re-fit the answers. Submit by **04:00 Istanbul** (deadline 05:00 Istanbul = 9:00 PM ET).

Nothing gets submitted without Ozgur's explicit OK.

---

**[ASSUMED] Project name**
Receipts (DoneCheck v2)

**[ASSUMED] Track**
Indie developer

**[ASSUMED] Theme**
Push the Frontier (software: giving computer-use agents a verifiable notion of "done").

**[ASSUMED] One-line description / tagline**
Receipts audits computer-use agents: when an agent says "done", it checks the screen recording, points to the exact frame that proves or disproves it, and keeps the receipt.

**[ASSUMED] Description (what it does)**
Computer-use agents report "done" even when the task silently failed: a payment declined toast that vanished, a date picker that defaulted to the wrong day, a booking stuck in review. Receipts is an auditor for those claims. Muse Wonder performs a task on a mock airline site through the computer-use tool while every step is recorded. Muse Spark 1.3 reviews keyframes against the goal and the agent's claim and returns TRUE DONE, FALSE DONE or UNSURE with the timestamp of the deciding frame. SAM 3.1 outlines the evidence pixels in that frame. Deterministic DOM checks back the verdict, and every frame, check and model response is hashed into an append-only SQLite audit trail. A small benchmark of 18 scripted episodes (honest successes, silent failures, partial completions) with server-state ground truth measures auditor precision and recall.

**[ASSUMED] How did you use Meta's models / tools?**
- Muse Wonder (`muse-wonder-1.0`) via the Responses API computer tool, driven by our Playwright driver: the agent under audit.
- Muse Spark 1.3 (`muse-spark-1.3`): multimodal judge over keyframes, structured verdict with evidence timestamp and a noun phrase for the evidence.
- SAM 3.1 (`sam-3.1`): segments the judge's evidence phrase in the decisive frame to produce a box and mask shown in the report.
- Muse Code: [fill if used] used as a coding agent during the build.
All calls go to Meta Model API at https://api.meta.ai/v1.

**[ASSUMED] How it's built (tech stack)**
Python 3.11, FastAPI mock site with fault-injection scenarios and a server-state oracle, Playwright for driving and recording, OpenAI-compatible SDK against Meta Model API, SQLite (WAL, append-only events, sha256 per artifact), static HTML report, pytest.

**[ASSUMED] Challenges**
Keeping three sources of truth separate (agent claim, auditor, oracle) so the benchmark is honest; transient UI failures that last ~3 s; mapping computer-use coordinates to a stable viewport; parsing SAM's token grammar into overlays.

**[ASSUMED] What's next**
Run the auditor on real sites in a sandbox, a live "verify before done" hook that blocks the agent's final message until the auditor agrees, and folding the benchmark into Ozgur's premature-completion eval work.

**[ASSUMED] Results / does it work?**
[Fill from runs/bench/results.md: Combined precision X, recall Y on 18 episodes; live Wonder false-done rate Z/n.] Limitations stated in README.

**[ASSUMED] GitHub repo URL (must be public)**
https://github.com/ozgureyilmaz/donecheck-receipts (create and make public before submitting)

**[ASSUMED] Demo video URL (3 minutes)**
TODO: fill after upload (~03:30 Istanbul).

**[ASSUMED] Team members**
Ozgur Eryilmaz (solo). GitHub https://github.com/ozgureyilmaz, X https://x.com/ozgur_back, LinkedIn https://www.linkedin.com/in/ozgure/

**[ASSUMED] Did you build on prior work?**
Continuation of my approved application idea "DoneCheck". The audit-trail idea comes from my earlier project prooftrade (Rust, SQLite audit trail); Receipts' code is new and written during the hackathon. The benchmark method mirrors my micro1 Eval Hackathon entry on premature completion claims.

**[ASSUMED] Anything else for judges**
Run `python -m receipts.run --scenario payment_declined --agent scripted` to reproduce the demo verdict without a computer-use run; only the judge and SAM need `MODEL_API_KEY`.
