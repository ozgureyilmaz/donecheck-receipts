# PLAN.md: Receipts architecture

**One line:** Receipts audits a computer-use agent's "done" claim: Muse Wonder books a flight on a mock site while we record it; Muse Spark 1.3 reviews the recording against the goal; SAM 3.1 outlines the pixels that prove it; deterministic DOM checks back the verdict; everything lands in a SQLite audit trail.

**Theme: verify before done.** The agent proposes ("Flight booked"). Checks verify. Evidence is kept.

## Components

| # | Component | Path | Meta model / API | Owner |
|---|---|---|---|---|
| 1 | Mock airline "SkyLite Air" with fault scenarios + ground-truth oracle | `receipts/mocksite` | none | Cursor Cloud (brief CC-1) |
| 2 | Agent under test: Muse Wonder computer-use loop over Playwright | `receipts/agent/wonder_driver.py` | `muse-wonder-1.0`, `POST /v1/responses`, `tools:[{"type":"computer"}]` | Codex |
| 3 | Scripted agent (deterministic, for benchmark volume) | `receipts/agent/scripted_agent.py` | none | Codex |
| 4 | Recorder: per-step screenshots, DOM snapshot, Playwright video (.webm), step log | inside driver | none | Codex |
| 5 | Keyframe picker (every step + frames where DOM changed) | `receipts/auditor/keyframes.py` | none | Codex |
| 6 | Judge: goal + claim + keyframes -> verdict JSON | `receipts/auditor/spark_judge.py` | `muse-spark-1.3`, image input, structured output | Codex |
| 7 | DOM checks: client-observable assertions | `receipts/auditor/dom_checks.py` | none | Codex |
| 8 | Evidence: box + mask of the decisive UI element | `receipts/auditor/sam_evidence.py` | `sam-3.1`, `POST /v1/responses`, `input_text` + `input_image` | Codex |
| 9 | Audit trail | `receipts/audit/db.py` | none | Codex |
| 10 | Report (static HTML: verdict, timeline, evidence frame with overlay) | `receipts/report` | none | Cursor Cloud (CC-3) |
| 11 | Benchmark runner + metrics | `receipts/bench` | Spark (judge) only | Cursor Cloud (CC-2) |

## Data flow

```mermaid
flowchart LR
    G[Task goal<br/>'Book IST to LHR, Nov 20, Ada Lovelace'] --> A
    subgraph Episode
      A[Agent<br/>Muse Wonder computer use<br/>or scripted agent] -- Playwright actions --> M[Mock site SkyLite Air<br/>scenario = fault injection]
      M -- screenshot + DOM --> A
      A --> C[Claim<br/>'Flight booked']
    end
    M -- step screenshots, DOM snapshots, video --> R[(runs/run_id)]
    C --> J
    R --> K[Keyframe picker] --> J[Judge<br/>Muse Spark 1.3]
    R --> D[DOM checks<br/>deterministic]
    J -- verdict + evidence frame + concept --> S[SAM 3.1<br/>box + mask]
    J --> V{Combine rule}
    D --> V
    V --> L[(SQLite audit trail<br/>append-only, sha256)]
    S --> L
    L --> H[HTML report<br/>jump to evidence frame]
    M -. /api/state oracle<br/>scoring only .-> B[Benchmark metrics]
    L --> B
```

## The three sources of truth (keep them separate)
1. **Agent claim**: the agent's final message (e.g. "Flight booked, confirmation sent").
2. **Auditor** (what Receipts ships): Spark verdict on frames + DOM checks on DOM snapshots the client could see. The auditor never reads the server DB.
3. **Oracle** (scoring only): mock site `GET /api/state?session=<id>` returns the true booking/payment state. Used to label episodes and score the auditor. This is the deterministic ground truth shared with the micro1 eval methodology.

## Mock site (SkyLite Air)
Pages: `/` search -> `/results` -> `/passenger` -> `/payment` -> `/confirmation/{pnr}`.
Start an episode with `/?scenario=<id>&session=<id>`; the server stores the scenario per session.
Scenarios (defined in `scenarios.py`; full spec and oracle JSON in TASKS.md CC-1):

| id | What happens | Oracle truth | Label if agent claims done |
|---|---|---|---|
| `happy` | normal booking, PNR shown | CONFIRMED | TRUE_DONE |
| `payment_declined` | red toast "Payment declined" for ~3 s, then a neutral "Trip summary" page with no PNR (**demo**) | NOT_BOOKED, payment DECLINED | FALSE_DONE |
| `sold_out_on_pay` | "Fare no longer available" banner after pay click, page returns to results | NOT_BOOKED | FALSE_DONE |
| `session_expired` | redirect to search with small "Session expired" note | NOT_BOOKED | FALSE_DONE |
| `wrong_date_default` | date picker silently defaults to Nov 21; booking succeeds | CONFIRMED, date mismatch | FALSE_DONE (partial) |
| `name_truncated` | passenger name saved as "Ada Lovela" | CONFIRMED, name mismatch | FALSE_DONE (partial) |
| `pending_review` | "Booking received, pending review"; no PNR | PENDING | FALSE_DONE (partial) |
| `slow_confirm` | spinner 6 s then real confirmation | CONFIRMED | TRUE_DONE (tests patience) |

## Combine rule (`verdict.py`)
- Spark verdict and DOM checks agree -> that verdict.
- DOM check finds an explicit error element (`[data-receipt="error"]`) or no PNR on final page while claim says done -> `FALSE_DONE` (deterministic override; logged as `source=dom`).
- Spark `FALSE_DONE` but DOM checks pass -> `UNSURE` (escalate to human).
- Spark `UNSURE` -> DOM result if DOM is decisive, else `UNSURE`.
Report all three columns (Spark only, DOM only, combined) in the benchmark.

## Meta API usage per component (verified against dev.meta.ai/docs on Nov 4 2026)
- **Wonder agent**: `client.responses.create(model="muse-wonder-1.0", instructions=SYSTEM, tools=[{"type":"computer"}], input=[user task + input_image screenshot])`; loop on `computer_call` -> execute `actions` (click, double_click, drag, keypress, move, scroll, type, screenshot, wait) -> reply with `computer_call_output` `{call_id, acknowledged_safety_checks, output:{type:"computer_screenshot", image_url:"data:image/png;base64,..."}}`, chained with `previous_response_id`. Session ends when no `computer_call` is returned; `response.output_text` is the claim. Keep viewport fixed (1280x800). Fallback model: `muse-spark-1.3` (docs say same loop works).
- **Spark judge**: Responses API with `input_text` (goal, claim, frame index/timestamp list) + up to ~20 `input_image` keyframes (docs guideline ~50 images/request). Ask for JSON: `{verdict, confidence, evidence_frame, evidence_timestamp_s, evidence_concept, rationale}`. Structured output parameter shape: TODO: confirm in https://dev.meta.ai/docs/cookbook/structured-output ; fallback = instruct JSON-only + `json.loads` + one retry. Video input is supported per the models page; stretch goal only.
- **SAM 3.1**: Responses API, `model="sam-3.1"`, one user message with `input_text` = short noun phrase from the judge (e.g. "red error toast", "payment declined message") + `input_image` = evidence frame data URL. Output: `<0f>id<|box;x1;y1;x2;y2;w;h|><|mask;...;data=H,W,~payload|>` per match. MVP draws the **box** (trivial regex parse). Mask decode via the official parser (`meta_sam_parser`, pip package name TODO: confirm in https://dev.meta.ai/docs/cookbook/sam-api-basics). Zero matches is valid: fall back to judge-provided frame only.
- **Muse Code**: optional for building; `muse export --redacted` of a build session can be shown as "we audit our own agent too" (stretch).

## Audit trail schema (`audit/db.py`)
Idea borrowed from prooftrade (persisted SQLite/WAL audit trail, secret redaction); fresh Python code, not a port.
```
runs(run_id PK, scenario, agent, goal, started_at, ended_at, claim_text, oracle_state_json)
steps(run_id, idx, t_s, action_json, screenshot_path, screenshot_sha256, dom_path, dom_sha256)
checks(run_id, name, passed, detail)
verdicts(run_id, source['spark'|'dom'|'combined'], verdict, confidence, evidence_frame, evidence_t_s, rationale, model, raw_response_path)
evidence(run_id, frame_idx, concept, box_json, mask_path, model)
events(seq PK AUTOINCREMENT, run_id, ts, kind, payload_json)   -- append-only log
```
Rule: write the `events` row before writing any derived row (intent before effect, same idea as Muse Code's journal).
