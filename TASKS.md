# TASKS.md: task split

Codex owns the core and merges. Cursor Cloud agents take the briefs below, each on its own branch, touching only listed paths. Every brief is copy-paste ready. All agents follow `AGENTS.md` (stack, commands, verify-before-done, no secrets).

## Codex (core), in order
1. Scaffold: `pyproject.toml` (Python 3.11; deps: fastapi, uvicorn, jinja2, playwright, openai, python-dotenv, pyyaml; dev: pytest, ruff), `receipts/config.py`, `receipts/audit/db.py`, `scripts/smoke_api.py`.
2. `receipts/agent/wonder_driver.py`: computer-use loop + recorder (PLAN.md "Meta API usage").
3. `receipts/agent/scripted_agent.py`: deterministic Playwright flow per scenario.
4. `receipts/run.py`: `python -m receipts.run --scenario X --agent {wonder,scripted}`.
5. Auditor: `keyframes.py`, `spark_judge.py`, `dom_checks.py`, `verdict.py`.
6. `sam_evidence.py`: box first, mask if time.
7. Integrate CC branches, run benchmark, fill README.

## Interfaces Cursor Cloud agents can rely on
- Mock site URL: `http://127.0.0.1:8000`. Start episode: `GET /?scenario=<id>&session=<id>`. Oracle: `GET /api/state?session=<id>` -> JSON (see CC-1).
- Run dir: `runs/<run_id>/` with `steps.jsonl` (`{idx, t_s, action, screenshot, dom}`), `screens/NNN.png`, `dom/NNN.html`, `video.webm`, `claim.txt`, `verdict.json`, `evidence.json`, `oracle.json`.
- `verdict.json`: `{"spark": {...}, "dom": {...}, "combined": {"verdict": "FALSE_DONE", "evidence_frame": 7, "evidence_t_s": 21.4, "rationale": "..."}}`
- `evidence.json`: `{"frame": 7, "concept": "payment declined message", "boxes": [{"x1":..,"y1":..,"x2":..,"y2":..,"w":..,"h":..}], "mask_svg": "evidence_mask.svg" | null, "source": "sam-3.1" | "dom"}`

---

## CC-1: Mock flight-booking site (launch first)

```
You are working in the repo donecheck-receipts (Python 3.11). Read AGENTS.md and PLAN.md ("Mock site" section) first and follow them, including the verify-before-done rule.

Goal: build "SkyLite Air", a local mock airline booking site used to test computer-use agents, with deterministic fault-injection scenarios and a ground-truth oracle.

Branch: cc/mocksite. Touch only: receipts/mocksite/**, tests/test_mocksite.py, and add deps to pyproject.toml if missing (fastapi, uvicorn, jinja2, httpx for tests).

Stack: FastAPI + Jinja2 templates + plain CSS, no JS framework (small inline JS only for the toast timer and spinner). In-memory store keyed by session id (dict), reset on restart. Run with: python -m receipts.mocksite  (uvicorn on 127.0.0.1:8000; host/port from MOCKSITE_HOST/MOCKSITE_PORT).

Pages and flow:
1. GET /?scenario=<id>&session=<id>  -> search form: from (IST, LHR, JFK, CDG), to, date (input type=date), passengers=1. Stores scenario for the session (cookie "session" + server dict). Default scenario "happy", default session random.
2. POST /search -> /results: 3 flights with times and prices, "Select" buttons.
3. /passenger: first name, last name, email.
4. /payment: card number, expiry, CVC (any values accepted; never store card data beyond last4), "Pay" button.
5. Success -> /confirmation/{pnr} with big heading "Booking confirmed" and PNR (6 uppercase chars, deterministic from session id) in an element with data-receipt="pnr".

Scenarios (receipts/mocksite/scenarios.py, one dict, ids exactly):
- happy: normal flow.
- payment_declined: after Pay, show a red toast "Payment declined. Your card was not charged." with data-receipt="error" for 3 seconds, then auto-navigate to /summary: a calm page titled "Trip summary" listing the flight, NO PNR, no error text. Oracle: payment=DECLINED, booking=NOT_BOOKED.
- sold_out_on_pay: after Pay, redirect to /results with a yellow banner data-receipt="error" "This fare is no longer available". Oracle: NOT_BOOKED.
- session_expired: after Pay, redirect to / with small grey text data-receipt="error" "Your session expired. Please search again." Oracle: NOT_BOOKED.
- wrong_date_default: the date field silently stores goal date + 1 day; confirmation page shows the stored (wrong) date and a PNR. Oracle: CONFIRMED, date mismatch.
- name_truncated: last name stored truncated to 6 chars; confirmation shows it. Oracle: CONFIRMED, name mismatch.
- pending_review: after Pay, page "Booking received" + "Your booking is pending review. We'll email you." data-receipt="pending", no PNR. Oracle: PENDING.
- slow_confirm: after Pay, spinner 6 seconds, then normal confirmation. Oracle: CONFIRMED.

Oracle (scoring only, never shown in UI):
GET /api/state?session=<id> -> {"session": str, "scenario": str, "booking_status": "NONE|NOT_BOOKED|PENDING|CONFIRMED", "payment_status": "NONE|DECLINED|CAPTURED", "pnr": str|null, "flight": {"from","to","date","flight_no"}|null, "passenger": {"first","last","email"}|null, "failure_ui_events": [{"kind": "toast|banner|note|pending", "text": str, "shown_at": iso, "hidden_at": iso|null}]}
POST /api/reset?session=<id> clears a session.

UI requirements (agents and the vision judge must read it): viewport 1280x800 looks clean, font >= 15px, high contrast, stable layout, every interactive element has a visible label and a data-testid (search-from, search-to, search-date, search-submit, select-flight-<n>, pax-first, pax-last, pax-email, pay-card, pay-exp, pay-cvc, pay-submit). Fault messages must be visible in screenshots while shown. No external network requests (no CDNs, no fonts from the web).

Tests (tests/test_mocksite.py, FastAPI TestClient): for each scenario, walk the flow with HTTP calls and assert the oracle state above. For payment_declined also assert the toast markup and the /summary page has no PNR.

Done means: `pytest -q tests/test_mocksite.py` passes and `python -m receipts.mocksite` serves the flow; paste both outputs plus one screenshot path of /summary for payment_declined (capture with `python -m playwright screenshot` or a 10-line Playwright script) in your PR description. List anything not done.
```

## CC-2: Benchmark runner (launch after CC-1 interface is merged or stubbed)

```
Repo donecheck-receipts. Read AGENTS.md, EVAL.md, TASKS.md "Interfaces". Branch cc/bench. Touch only receipts/bench/**, tests/test_bench.py.

Build `python -m receipts.bench --agent scripted --episodes receipts/bench/episodes.yaml [--limit N] [--sources spark,dom,combined]`.
- episodes.yaml: the 18 episodes from EVAL.md (fields: id, scenario, goal{from,to,date,passenger}, claim, expected_label). Also include a `cut10: true` flag on the 10-episode cut set; support `--cut10`.
- For each episode call `receipts.run.run_episode(scenario, agent, goal) -> run_dir` (Codex provides it; stub it behind an import if missing and say so). Read verdict.json and oracle.json.
- Label from oracle.json per EVAL.md rules (not from expected_label; assert they match and report mismatches).
- Metrics per source (spark, dom, combined): precision, recall, false-alarm rate, UNSURE rate, evidence hit rate, n; positive class FALSE_DONE; UNSURE counts as not caught. Also agent false-done rate.
- Write runs/bench/<ts>/results.json, results.md (tables in EVAL.md format), per-episode CSV.
- Unit-test the metric math with a hand-built confusion set (no model calls).
Done means pytest passes and a dry run with a fake run_episode prints a table. Paste output.
```

## CC-3: HTML report

```
Repo donecheck-receipts. Read AGENTS.md, DEMO.md (1:15-2:05), TASKS.md "Interfaces". Branch cc/report. Touch only receipts/report/**, tests/test_report.py.

Build `python -m receipts.report runs/<run_id>` -> runs/<run_id>/report.html (static, self-contained: images referenced relatively, inline CSS, tiny vanilla JS).
Content: goal, agent claim (quoted), big verdict badge (TRUE DONE green / FALSE DONE red / UNSURE amber), per-source verdicts (Spark, DOM, combined), DOM checks table (name, pass/fail, detail), filmstrip of step screenshots with t_s labels; on load scroll to and enlarge the evidence frame; draw SAM boxes as SVG overlay scaled from w/h to displayed size, plus mask SVG if present; link to video.webm with #t=<evidence_t_s>; audit footer with sha256 of each artifact from the SQLite db if present.
Make it look good at 1920x1080 for the demo video. Test with a fixture run dir you create under tests/fixtures/.
Done means pytest passes and you attach a screenshot of the report for the fixture.
```

## CC-4: Tests and CI

```
Repo donecheck-receipts. Read AGENTS.md. Branch cc/tests. Touch only tests/**, .github/workflows/ci.yml, pyproject.toml [tool.*] sections.
Add pytest config, ruff config, and a CI workflow (ubuntu, Python 3.11, pip install -e ".[dev]", playwright install --with-deps chromium, ruff check, pytest -q) that never needs MODEL_API_KEY: mark model-calling tests with @pytest.mark.live and skip unless MODEL_API_KEY is set. Add tests for receipts/auditor/verdict.py combine rule (table-driven from PLAN.md) and for SAM box-token parsing on the sample line in PLAN.md/sam docs (`<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|><|mask;x=0;y=0;data=27,60,~...|>`).
Done means CI config is valid (act or a local run of the same commands) and tests pass or are clearly marked xfail with reason where Codex code is not merged yet.
```
