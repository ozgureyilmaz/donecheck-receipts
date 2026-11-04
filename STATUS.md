# STATUS.md: checklist for status checks

Update the box, the time (Istanbul) and a one-line proof (command output, PR link) when ticking. No proof, no tick.
Deadline: Nov 5 05:00 Istanbul = Nov 4 9:00 PM ET.

Last updated: Nov 4 19:00 Istanbul, by muse

## Setup
- [x] Repo name chosen: `donecheck-receipts` (free on GitHub at 18:59 Istanbul)
- [x] Docs pack written (AGENTS, README, PLAN, ROADMAP, HACKATHON, LINKS, EVAL, DEMO, SUBMISSION, STATUS, TASKS, PROMPT)
- [ ] Ozgur confirmed repo name
- [ ] Public repo created, docs pushed
- [ ] `MODEL_API_KEY` in local `.env`; `GET /v1/status` OK; one Spark call OK
- [ ] Codex kicked off with PROMPT.md
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
