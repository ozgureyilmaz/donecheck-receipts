# STATUS.md: checklist for status checks

Update the box, the time (Istanbul) and a one-line proof (command output, PR link) when ticking. No proof, no tick.
Deadline: Nov 5 05:00 Istanbul = Nov 4 9:00 PM ET.

Last updated: Nov 05 14:55 Istanbul

## Setup
- [x] Repo name chosen: `donecheck-receipts` (free on GitHub at 18:59 Istanbul)
- [x] Docs pack written (AGENTS, README, PLAN, ROADMAP, HACKATHON, LINKS, EVAL, DEMO, SUBMISSION, STATUS, TASKS, PROMPT)
- [x] Ozgur confirmed repo name at 19:02 Istanbul (confirmed by user)
- [x] Public repo confirmed; code/docs pushed to origin/main
- [x] `MODEL_API_KEY` in local `.env`; `GET /v1/status` returned HTTP 200 and Spark returned `ready`
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
- [x] README results table filled with real offline numbers and model limitations
- [x] Quickstart verified on a fresh clone (documented Mac venv workaround)
- [ ] Demo GIF in README
- [ ] Video recorded, <= 3:00 (target 03:00)
- [ ] Video uploaded, link works logged out
- [x] Repo public, MIT license file present
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

### Step 9 / delivery — Nov 04 19:40 Istanbul
- [x] README quickstart, model availability, scope limits, benchmark table and MIT license updated. No mask-decoding or live-model success is claimed.
- [x] Final full regression: `.venv/bin/python -m receipts.bench --agent scripted --episodes receipts/bench/episodes.yaml --offline` -> exit 0; 18 episodes, 12/18 constructed false-dones, zero oracle mismatches, DOM/combined 100% precision/recall with zero false alarms; Spark 18/18 UNSURE, not called.
- Final run `runs/bench/20261104T163650Z/results.{json,md,csv}`; all 754 artifacts matched registered SHA-256 and byte size; no missing/unregistered files. All 18 step logs excluded full test-card number. Two complete offline runs were performed; README shows the final run, not pooled results.
- [x] Fresh remote clone at `b219a4f` -> editable install, Chromium install, Ruff lint/format, `83 passed, 1 warning in 15.25s`, declined demo `FALSE_DONE`, clean Git status. Proving log: `runs/verification/fresh-clone.log`. macOS venv workaround from README used; no credential copied.
- [x] Hosted CI on code commit `8a90483`: `83 passed, 1 warning in 12.37s`, Ruff passed. https://github.com/ozgureyilmaz/donecheck-receipts/actions/runs/41869039473
- [x] Exact default command `.venv/bin/python -m receipts.run --scenario payment_declined --agent scripted` -> Spark UNSURE, DOM/combined FALSE_DONE, evidence frame 13, source dom; `runs/20261104T163838-payment_declined-scripted/report.html`. All 43 hashes match; report rerender hash stable. Preview: `runs/demo-report-final.png`.

## Remaining work / not performed
- Authenticated smoke passed. Live judging, SAM evidence, and the happy path remain unverified because Meta rejected the first Wonder computer-use request. See the live verification log below.
- SAM mask decoding omitted; current offline highlights come from DOM boxes. Live Wonder set and measured model cost/latency are unmeasured.
- Demo GIF/video recording/upload, actual submission-question verification, form submission, and release tag were not performed. The submission form and social channels were not touched.
- Hosted CI has one non-failing Starlette TestClient/httpx deprecation warning.

## Live verification

- Nov 05 14:50 Istanbul: `.venv/bin/python scripts/smoke_api.py` exited 0. Status HTTP 200, Spark `ready`, and models included `muse-spark-1.3`, `muse-wonder-1.0`, and `sam-3.1`. The smoke script does not persist token usage.
- Nov 05 14:51 Istanbul. `RECEIPTS_MAX_STEPS=30 .venv/bin/python -m receipts.run --scenario payment_declined --agent wonder --strict-live` exited 2 at the first Wonder request. `POST /v1/responses` returned HTTP 400, `Unsupported tool type: 'computer'`, parameter `tools`. Provider request ID `5a60a5ff-fe7e-42af-863a-96e5ad8b26e8`.
- The [computer-use guide](https://dev.meta.ai/docs/computer-use) documents the exact `tools=[{"type":"computer"}]` request that the mocked SDK transport verifies. The provider rejection preceded any `computer_call`. The ordered run stopped before Spark judging, SAM, and the happy path.
- Run `runs/20261105T115143-payment_declined-wonder` recorded Spark, DOM, and combined `UNSURE`. Oracle booking and payment were both `NONE`. The recorded browser frame is `screens/001.png` within the run. The report and all 19 artifacts matched their SQLite SHA-256 and size. An exact credential scan found zero matches in the artifacts and database.
- Usage is unknown for the rejected Wonder request and the initial Spark smoke. Wonder returned no usage counters and the smoke script did not persist its counters. `usage.json` records one unpriced Wonder attempt. No complete token or spending total is available.
- The strict-run gates, request/action cap, usage accounting, and SAM stream metadata passed offline checks. `.venv/bin/pytest -q -m 'not live'` reported `98 passed, 1 warning in 19.90s`. Ruff lint and format checks passed. These edits are local at the step-2 stop point.
