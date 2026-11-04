# STATUS.md: checklist for status checks

Update the box, the time (Istanbul) and a one-line proof (command output, PR link) when ticking. No proof, no tick.
Deadline: Nov 5 05:00 Istanbul = Nov 4 9:00 PM ET.

Last updated: Nov 04 19:16 Istanbul

## Setup
- [x] Repo name chosen: `donecheck-receipts` (free on GitHub at 18:59 Istanbul)
- [x] Docs pack written (AGENTS, README, PLAN, ROADMAP, HACKATHON, LINKS, EVAL, DEMO, SUBMISSION, STATUS, TASKS, PROMPT)
- [ ] Ozgur confirmed repo name
- [ ] Public repo created, docs pushed
- [ ] `MODEL_API_KEY` in local `.env`; `GET /v1/status` OK; one Spark call OK
- [x] Main developer started from the supplied brief; Luna xhigh workers running
- [x] Mock site worker completed

## Build
- [x] Mock site: 8 scenarios + `/api/state` oracle (CC-1)
- [x] Scripted agent runs all 8 scenarios
- [ ] Wonder computer-use loop completes `happy`
- [ ] Wonder run on `payment_declined` ends with a done claim
- [ ] Spark judge returns verdict + evidence frame
- [x] DOM checks + combine rule
- [ ] SAM box on evidence frame (mask optional)
- [x] SQLite audit trail with sha256
- [x] HTML report (CC-3)
- [x] Benchmark runner + 18 episodes (CC-2)
- [x] Tests green (CC-4)

## Ship
- [ ] Feature freeze (01:30)
- [ ] README results table filled with real numbers
- [ ] Quickstart verified on a fresh clone
- [ ] Demo GIF in README
- [ ] Video recorded, <= 3:00 (target 03:00)
- [ ] Video uploaded, link works logged out
- [ ] Repo public, license file present (MIT unless rules say otherwise)
- [ ] Real form questions pasted into SUBMISSION.md
- [ ] Ozgur OK to submit
- [ ] Form submitted (target 04:00), confirmation saved

## Blockers / decisions
- Form question text unknown (login required).
- Accepted video hosts / license rules: check Discord or Official Rules.
- micro1, Apart, Wilo URLs: Ozgur to provide.

## Verification log

### Step 0 — Nov 04 19:16 Istanbul
- [x] Python 3.11 package installed: `.venv/bin/python -m pip install -e ".[dev]"` -> Successfully installed receipts-0.1.0 and dependencies.
- [x] `.venv/bin/python -m playwright install chromium` -> Chromium, headless shell, FFmpeg installed.
- [x] `.venv/bin/pytest -q tests/test_config.py` -> `2 passed in 0.01s`.
- `.venv/bin/python scripts/smoke_api.py` -> `GET /v1/status: HTTP 200`; `UNSURE: no MODEL_API_KEY or META_API_KEY; Spark and model list skipped.` (exit 2). No model call occurred.
- Host Python venv ensurepip failed; generated environment repaired with `uv venv --python 3.11 --seed --clear .venv`, then normal pip install succeeded.
- Dependency reasons: python-multipart parses local mock-site form posts; httpx fetches oracle/status and powers TestClient; Pillow draws evidence PNG box overlays. Other dependencies are the fixed stack.
- Pending: authenticated Spark, Wonder, SAM and model-list proof; no key available.

### Step 1 — Nov 04 19:23 Istanbul
- [x] `.venv/bin/pytest -q tests/test_mocksite.py` -> `9 passed, 1 warning in 6.30s` (TestClient deprecation only).
- [x] `.venv/bin/ruff check receipts/mocksite tests/test_mocksite.py` -> `All checks passed!`; format check -> `6 files already formatted`.
- [x] `MOCKSITE_PORT=8001 .venv/bin/python -m receipts.mocksite` plus curl -> HTTP 200 text/html; browser declined flow reached `/summary`, screenshot `/tmp/donecheck-mocksite-summary.png`. Process stopped afterward.
- All eight scenarios implemented; no model calls in this step.

### Step 2 — Nov 04 19:23 Istanbul
- [x] `.venv/bin/pytest -q tests/test_audit_db.py` -> `5 passed in 0.04s`.
- [x] `.venv/bin/ruff check receipts/audit tests/test_audit_db.py` -> `All checks passed!`.
- WAL, foreign keys, atomic intent-before-derived writes, hash storage, recursive secret redaction and UPDATE/DELETE rejection on events tested.
- Integration fixes: step event payload restored; evidence frame/boxes/source aliases supported. No live API proof claimed.

### Steps 3-5 — Nov 04 19:27 Istanbul
- [x] `.venv/bin/python -m receipts.run --scenario happy --agent scripted --offline` -> Spark `UNSURE`, DOM/combined `TRUE_DONE`, frame 14; run `20261104T162333-happy-scripted`.
- [x] `.venv/bin/python -m receipts.run --scenario payment_declined --agent scripted --offline` -> Spark `UNSURE`, DOM/combined `FALSE_DONE`, frame 14, source `dom`; run `20261104T162336-payment_declined-scripted`.
- [x] Hash read-back against actual bytes -> happy `all_hashes_match=True artifacts=41`, declined `all_hashes_match=True artifacts=45`; videos 238071 and 401937 bytes. Evidence DOM contains transient toast; final DOM contains calm summary with no toast.
- [x] `.venv/bin/pytest -q` -> `66 passed, 1 warning in 12.33s` at this milestone. Tests include a real Chromium declined episode with all artifact hashes checked and oracle absent during judging.
- [x] DOM checks + combine rule; Spark adapter schema validation/JSON retry; SAM streamed box parser + labeled DOM fallback PNG.
- `.venv/bin/python -m receipts.run --scenario happy --agent wonder` -> all sources `UNSURE`, exit 2; `agent_failure.json` says Wonder requires key. No live request occurred.
- Wonder computer-use adapter has local-origin/encoded-oracle-path blocking, bounded turns, action execution and safety-stop tests. Live happy/declined completion remains UNSURE; no masks decoded, no SAM model proof.

### Step 7 (parallel with benchmark) — Nov 04 19:27 Istanbul
- [x] `.venv/bin/pytest -q tests/test_report.py` -> `6 passed`; Ruff check/format passed.
- [x] `python -m receipts.report runs/20261104T162336-payment_declined-scripted` generated HTML; Chromium screenshot `runs/report-payment-declined.png` inspected at 1920x1080.
- [x] Source badges, frame filmstrip, scaled boxes, escaped claims, local safe paths, video evidence link and audit hashes implemented. Report excludes its own hash so rerender is stable; regression proves equal SHA-256.
- Report can show masks supplied as safe local files, but this build does not decode SAM masks. Fixture video is synthetic test media; real episodes have Playwright video.

### Step 6 — Nov 04 19:29 Istanbul
- [x] `.venv/bin/pytest -q tests/test_bench.py` -> `4 passed`; scoped Ruff check/format passed.
- [x] `.venv/bin/python -m receipts.bench --agent scripted --episodes receipts/bench/episodes.yaml --offline` -> 18 episodes, zero expected/oracle mismatches; artifacts `runs/bench/20261104T162815Z/results.{json,md,csv}`, latest `runs/bench/results.md`.
- Scripted false-done rate = 12/18 (66.7%), fixed by design. DOM and combined: accuracy/precision/recall 100%, false-alarm 0%, UNSURE 0%. Combined evidence hit = 8/8 eligible failures (date/name mismatches have no failure-window annotation).
- Spark: no calls; 18/18 UNSURE. This is an offline DOM benchmark, not a measured Spark/Wonder/SAM score. Model tokens/latency unavailable.
- Independent hash read-back across all 18 benchmark run directories -> `hashes_checked: 770 mismatches: 0`.
- Manifest corrected so both truncated-name examples actually exceed six surname characters.

### Step 8 / final integration fixes — Nov 04 19:36 Istanbul
- [x] `.venv/bin/ruff check .` -> `All checks passed!`; `.venv/bin/ruff format --check .` -> `54 files already formatted`; `.venv/bin/pytest -q` -> `83 passed, 1 warning in 14.26s`; `git diff --check` clean.
- GitHub Actions configured for Python 3.11, editable install, Chromium, Ruff and pytest without model credentials; hosted execution pending at this point.
- Review fixes: card field masked and stored PAN/CVC redacted; action budget enforced cumulatively; deterministic false-done retained after recording failure; matching DOM/screenshot capture retried when a transient UI changes; date/name failures highlight mismatched fields; standalone site reads .env config.
- Repeated full offline benchmark is running after these changes; it will be reported separately as a regression, not pooled to inflate n.
