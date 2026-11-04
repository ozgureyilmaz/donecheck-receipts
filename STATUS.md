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
- [ ] CC-1 (mock site) launched

## Build
- [ ] Mock site: 8 scenarios + `/api/state` oracle (CC-1)
- [ ] Scripted agent runs all 8 scenarios
- [ ] Wonder computer-use loop completes `happy`
- [ ] Wonder run on `payment_declined` ends with a done claim
- [ ] Spark judge returns verdict + evidence frame
- [ ] DOM checks + combine rule
- [ ] SAM box on evidence frame (mask optional)
- [ ] SQLite audit trail with sha256
- [ ] HTML report (CC-3)
- [ ] Benchmark runner + 18 episodes (CC-2)
- [ ] Tests green (CC-4)

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
