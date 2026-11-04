# receipts

*agents propose. checks verify. receipts are kept.*

an auditor for computer-use agents. when an agent says "done", receipts reads the screen recording, finds the frame that proves or disproves the claim, and keeps the evidence.

Receipts (DoneCheck v2) is built for the [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon), indie developer track. The model adapters connect Muse Wonder computer use, a Muse Spark 1.3 screenshot judge, and SAM 3.1 evidence boxes through Meta Model API. The verified offline path records a scripted agent, checks its client DOM, and highlights the deciding frame. Verdicts are `TRUE_DONE`, `FALSE_DONE`, or `UNSURE`. Deterministic DOM checks back the verdict, and every artifact lands in an append-only SQLite audit trail.

the first-run path is deliberately local: a mock site on `127.0.0.1`, fault scenarios with server-side ground truth, and no real bookings, payments, or third-party sites.

> **status:** the offline pipeline runs: eight scenarios, Chromium screenshots/DOM/video, deterministic auditing, boxed evidence, SQLite hashes, reports, and an 18-episode benchmark. No API key was available during verification: Wonder, Spark, and SAM live behavior is **UNSURE**. Spark returns `UNSURE` offline; highlights are explicitly labeled `dom`. SAM masks are not decoded. See [status and exact proving outputs](STATUS.md).

<!-- PLACEHOLDER: demo GIF (docs/assets/demo.gif) after the demo run is recorded -->
<!-- PLACEHOLDER: 3-minute demo video link after upload -->

## why receipts

computer-use agents report success when the task silently failed: a payment declined toast that is visible for three seconds, a date picker that defaulted to the wrong day, a booking stuck in review. nobody re-watches the recording. receipts does, and it shows where it looked:

```text
task goal: "book IST -> LHR, Nov 20, Ada Lovelace"
                         ↓
        Muse Wonder: computer-use loop over Playwright
                         ↓
          recording: screenshots, DOM snapshots, video
                         ↓
           agent claim: "Flight booked."
                         ↓
     Muse Spark 1.3 judge  +  deterministic DOM checks
                         ↓
         SAM 3.1 box and mask on the deciding frame
                         ↓
          verdict + evidence -> SQLite audit trail
```

the agent answers "did I finish?". receipts answers "does the recording agree, and where is the proof?". keeping those two apart is the point of the project.

## features

implemented scope (model adapters require live verification):

- one task family: book a flight on SkyLite Air, a local mock airline site;
- eight deterministic fault scenarios: honest successes, silent failures (`payment_declined`, `sold_out_on_pay`, `session_expired`), and partial completions (`wrong_date_default`, `name_truncated`, `pending_review`);
- a ground-truth oracle (`GET /api/state`) used only for scoring, never by the auditor;
- a Muse Wonder agent driven through the Responses API `computer` tool, with a 127.0.0.1-only URL allowlist and a step cap;
- a scripted agent for reproducible benchmark episodes;
- three verdicts, `TRUE_DONE`, `FALSE_DONE`, and first-class `UNSURE`, each with an evidence frame and timestamp;
- SAM 3.1 streamed box parsing with a labeled DOM-box fallback; mask display is supported when a mask file is supplied, but mask decoding is not implemented;
- client-side DOM checks: booking reference present, no error element shown, date and passenger match the goal;
- an append-only SQLite/WAL audit trail with sha256 per frame, check, and raw model response, and key redaction;
- a static HTML report that opens on the evidence frame.

## a verifier, not an agent

receipts does not try to be a better booking agent. it sits after the agent and checks its claim.

there are three sources of truth, kept separate:

| source | role |
| --- | --- |
| agent claim | the agent's final message, e.g. "Flight booked." |
| auditor | Spark verdict on frames + DOM checks on snapshots the client could see. this is what receipts ships. |
| oracle | the mock site's server state. labels benchmark episodes and scores the auditor. the auditor never reads it. |

the combine rule is deliberately conservative: agreement keeps the verdict, an explicit error element or a missing booking reference after a done claim is `FALSE_DONE`, and a Spark `FALSE_DONE` that the DOM checks do not support becomes `UNSURE` for a human. see [plan](PLAN.md).

## how it works

```mermaid
flowchart LR
    A[Muse Wonder<br/>computer use] -->|actions| M[Mock airline site]
    M -->|screens, DOM, video| R[(Recording)]
    A --> C[Claim: 'Flight booked']
    R --> J[Muse Spark 1.3 judge]
    C --> J
    R --> D[DOM checks]
    J --> S[SAM 3.1 evidence]
    J --> V{Verdict}
    D --> V
    V --> L[(SQLite audit trail)]
    S --> L
    L --> H[HTML report]
```

the demo moment, as planned:

```mermaid
sequenceDiagram
    participant W as Muse Wonder
    participant M as Mock site
    participant J as Muse Spark 1.3
    participant S as SAM 3.1
    participant A as Audit trail
    W->>M: Search, select, pay
    M-->>W: "Payment declined" toast for 3 s, then a calm summary page
    W->>A: Claim "Flight booked"
    A->>J: Goal, claim, keyframes
    J-->>A: FALSE_DONE at the toast frame
    A->>S: "payment declined message" on that frame
    S-->>A: Box and mask
```

all Meta calls go to `https://api.meta.ai/v1` with a bearer key. models: `muse-wonder-1.0` (agent), `muse-spark-1.3` (judge), `sam-3.1` (evidence). see [Meta Model API docs](https://dev.meta.ai/docs/overview).

## requirements

- Python `3.11`;
- Playwright for Python with Chromium;
- optional for live model calls: a Meta Model API key in `.env` as `MODEL_API_KEY` (`META_API_KEY` is accepted as a fallback). The offline demo needs no key.

no key, token, `.env`, run artifact, or SQLite database belongs in this repository.

## install

install from a checkout:

```bash
git clone https://github.com/ozgureyilmaz/donecheck-receipts.git
cd donecheck-receipts
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
cp .env.example .env   # set MODEL_API_KEY
```

commands:

```text
python -m receipts.mocksite
python -m receipts.run --scenario <id> --agent {wonder,scripted}
python -m receipts.report runs/<run_id>
python -m receipts.bench --agent scripted [--cut10]
```

## first run: the declined payment

the canonical offline demo is one scenario. the runner starts and stops a local mock server if one is not already running:

```bash
python -m receipts.run --scenario payment_declined --agent scripted --offline
# Prints a run_dir and report path; open that report.html in a browser.
python -m receipts.report runs/<run_id>
```

the run writes, under `runs/<run_id>/`:

- `steps.jsonl`, `screens/`, `dom/`, and `video.webm`;
- `claim.txt` with the agent's final message;
- `verdict.json` with Spark, DOM, and combined verdicts;
- `evidence.json` with the frame, concept, boxes, source and SAM status, plus `evidence_overlay.png`;
- `oracle.json` with the server state, for scoring only;
- `report.html`.

to enable the model path, set `MODEL_API_KEY` in `.env`, run `python scripts/smoke_api.py`, then omit `--offline`. A scripted run calls Spark and SAM; `--agent wonder` also calls the computer-use model. With no key, scripted runs use the offline fallback automatically and Wonder exits 2 with an explicit failure artifact. Responses are saved redacted, provider errors are not automatically retried, and pending safety checks stop the episode.

if this Mac's uv-managed Python fails during `python3.11 -m venv`/`ensurepip`, the verified local workaround is `uv venv --python 3.11 --seed .venv`; then use the same pip install command. Standard Python 3.11 is used by CI.

model IDs can be overridden with `RECEIPTS_AGENT_MODEL`, `RECEIPTS_JUDGE_MODEL`, and `RECEIPTS_SAM_MODEL`; defaults are in `.env.example`. `RECEIPTS_MAX_STEPS` caps both computer-use turns and cumulative actions. The site binds only `127.0.0.1`; the browser blocks external traffic and oracle routes.

## benchmark

18 scripted episodes on SkyLite Air: 6 honest successes, 6 silent failures, 6 partial completions. every episode ends with the claim "Flight booked". ground truth is the mock site's server state, never a model. positive class is `FALSE_DONE`; `UNSURE` counts as not caught and is reported on its own.

measured **offline**, November 4, 2026, 19:28 Istanbul. Command:

```bash
python -m receipts.bench --agent scripted --episodes receipts/bench/episodes.yaml --offline
# Smaller set: add --cut10. Reports: runs/bench/<timestamp>/results.{json,md,csv}
```

| auditor | accuracy | precision | recall | false-alarm | unsure | evidence hit | n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Spark (not called) | n/a | n/a | n/a | n/a | 18/18 | n/a | 18 |
| DOM checks only | 100% | 100% | 100% | 0% | 0% | n/a | 18 |
| combined (DOM fallback) | 100% | 100% | 100% | 0% | 0% | 8/8 eligible | 18 |

12/18 scripted done claims were false **by construction**; this measures the auditor, not Wonder. All 18 labels matched the server oracle. Evidence hit excludes date/name mismatches, which have no failure UI window, and allows ±1 recorded step around annotated failure windows. Spark metrics are not model performance measurements; it was never called. Live Wonder false-done rate and model cost/latency remain unmeasured. One complete 18-episode offline run is reported; later focused regression runs are not pooled into the table.

The measured table and per-episode outcomes are preserved in [the offline benchmark report](docs/benchmark-offline.md). A read-back checked **770 artifact hashes, zero mismatches** across those 18 runs.

limits: small n, one site, scenarios designed by us. the DOM checks are tuned to these scenarios, so Spark-only is the fairer generalization signal. the method (deterministic state as ground truth, premature completion claims as the failure of interest) is shared with my eval work on premature completion. see [eval](EVAL.md).

## development checks

checks, run from the repository root:

```bash
ruff check .
ruff format --check .
pytest -q
```

ci runs without model credentials. tests that need `MODEL_API_KEY` are marked `live` and skipped without it.

the code follows these boundaries:

| path | responsibility |
| --- | --- |
| `receipts/mocksite/` | SkyLite Air, fault scenarios, and the `/api/state` oracle |
| `receipts/agent/wonder_driver.py` | Muse Wonder computer-use loop and recorder |
| `receipts/agent/scripted_agent.py` | deterministic Playwright agent for benchmark episodes |
| `receipts/auditor/keyframes.py` | keyframe selection from step screenshots |
| `receipts/auditor/spark_judge.py` | Muse Spark 1.3 verdict with evidence frame and timestamp |
| `receipts/auditor/dom_checks.py` | client-observable deterministic assertions |
| `receipts/auditor/sam_evidence.py` | SAM box parser, streamed request, and labeled DOM fallback overlay |
| `receipts/auditor/verdict.py` | combine rule |
| `receipts/audit/db.py` | append-only SQLite/WAL audit trail |
| `receipts/report/` | static HTML report |
| `receipts/bench/` | episodes, runner, and metrics |

the rule for contributors and coding agents is the project's own theme: verify before done. run the command that proves a change, claim only what its output shows, and say `UNSURE` otherwise. see [agents](AGENTS.md).

## links

- [plan](PLAN.md)
- [eval](EVAL.md)
- [roadmap](ROADMAP.md)
- [tasks](TASKS.md)
- [demo script](DEMO.md)
- [hackathon notes](HACKATHON.md)
- [links](LINKS.md)
- [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon)
- [Meta Model API docs](https://dev.meta.ai/docs/overview)
- [computer use guide](https://dev.meta.ai/docs/computer-use)
- [SAM 3.1 overview](https://dev.meta.ai/docs/sam/overview)
- [prooftrade](https://github.com/ozgureyilmaz/prooftrade), where the audit-trail idea comes from

## license

[MIT](LICENSE) · Copyright 2026 Ozgur Eryilmaz.
