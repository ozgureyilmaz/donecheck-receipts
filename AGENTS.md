# AGENTS.md: rules for Codex, Cursor Cloud agents, and Muse Code

Read this first. Then `PLAN.md` (architecture), `TASKS.md` (who does what), `ROADMAP.md` (time budget). Deadline: **Nov 4 2026 9:00 PM ET = Nov 5 05:00 Istanbul**. Ship small and working over big and broken.

## Roles
- **Codex**: main developer. Owns the core: `receipts/agent`, `receipts/auditor`, `receipts/audit`, wiring, and merges to `main`.
- **Cursor Cloud agents**: scoped helpers on their own branches (`cc/<task>`): mock site, benchmark runner, report UI, tests. Touch only the paths named in your brief.
- **muse (Grok Bot)**: plans, docs, briefs, status. Does not push code.
- **Ozgur**: decides, reviews, records the video, submits the form.

## Stack (fixed; do not swap)
Python 3.11, FastAPI + Jinja2 (mock site), Playwright for Python (Chromium driver + video recording), `openai` Python SDK pointed at `https://api.meta.ai/v1`, SQLite (stdlib `sqlite3`, WAL), static HTML report (Jinja2, no JS framework), pytest.
Why Python: the Meta computer-use and SAM docs ship Python examples, and Playwright-Python gives driver, screenshots and video in one library.

## Layout
```
receipts/
  config.py            # env loading (.env), model IDs
  mocksite/            # FastAPI app: SkyLite Air flight booking + fault scenarios + /api/state oracle
  agent/
    wonder_driver.py   # Muse Wonder computer-use loop over Playwright
    scripted_agent.py  # deterministic Playwright agent for benchmark episodes
  auditor/
    keyframes.py       # pick frames from step screenshots
    spark_judge.py     # Muse Spark 1.3 verdict (TRUE_DONE / FALSE_DONE / UNSURE + timestamps)
    dom_checks.py      # deterministic client-side assertions on DOM snapshots
    sam_evidence.py    # SAM 3.1 box/mask on the evidence frame
    verdict.py         # combine rule
  audit/db.py          # SQLite audit trail (append-only, sha256 of every artifact)
  report/              # static HTML report renderer
  bench/               # episodes.yaml + runner + metrics table
tests/
runs/                  # gitignored artifacts
```

## Commands
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && python -m playwright install chromium
cp .env.example .env            # then put MODEL_API_KEY in .env

python -m receipts.mocksite                                   # http://127.0.0.1:8000
python -m receipts.run --scenario payment_declined --agent wonder   # live demo episode
python -m receipts.run --scenario payment_declined --agent scripted # offline, no model calls for the agent
python -m receipts.bench --agent scripted --episodes receipts/bench/episodes.yaml
python -m receipts.report runs/<run_id>                       # writes runs/<run_id>/report.html
pytest -q
ruff check . && ruff format --check .
```
If a command above does not exist yet, create it with exactly that name. Keep README quickstart in sync.

## Conventions
- Verdict enum strings exactly: `TRUE_DONE`, `FALSE_DONE`, `UNSURE` (display as "TRUE DONE" etc. in UI only).
- Scenario IDs are snake_case and defined once in `receipts/mocksite/scenarios.py`.
- Every run gets `run_id = <utc yyyymmddThhmmss>-<scenario>-<agent>`; all artifacts under `runs/<run_id>/`.
- Timestamps in artifacts: seconds from episode start (float) plus ISO-8601 UTC.
- Model IDs come from env (`RECEIPTS_AGENT_MODEL`, `RECEIPTS_JUDGE_MODEL`, `RECEIPTS_SAM_MODEL`), never hard-coded in logic.
- Small PRs, one concern each. Type hints. No new dependency without a one-line reason in the PR.
- Meta API: only use endpoints and fields documented at dev.meta.ai/docs. If unsure, leave `# TODO: confirm in docs <url>` and a fallback path; never guess silently.

## Verify before done (applies to you, the coding agent)
This project audits agents that claim "done" falsely. Do not do it yourself.
1. Before saying a task is done, run the exact command that proves it (test, script, curl) and paste the output in the PR/summary.
2. Claim only what the output shows. "Should work" is not done. If you could not run it (no key, no network), say UNSURE and why.
3. Add or update a test for every behavior you add when feasible in < 10 minutes.
4. List what you did not do.

## Secrets and safety
- Never commit secrets. Key lives in `.env` as `MODEL_API_KEY` (code also accepts `META_API_KEY` as a fallback). `.env` is gitignored; `.env.example` holds names only.
- Never print the key; redact `Authorization` headers and `sk-`/bearer-like strings from logs and the SQLite trail.
- The mock site is local only (`127.0.0.1`). The Wonder driver must refuse navigation outside `http://127.0.0.1:<port>` and cap steps (`RECEIPTS_MAX_STEPS`).
- Computer-use `pending_safety_checks`: decline by default. The explicit `--allow-mock-confirmations` flag allows only the configured local mock browser and allowlisted check codes. Log each decision before acting and echo approved checks unchanged. Decline and stop for other checks.
- Do not create the GitHub repo, push to a remote, submit forms, or post anywhere unless Ozgur says so.
