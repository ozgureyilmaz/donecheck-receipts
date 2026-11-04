# ROADMAP.md: tonight, hour by hour (Istanbul, UTC+3)

Hard deadline: **Nov 5 05:00 Istanbul = Nov 4 9:00 PM ET.** Internal: video recorded by **03:00**, form submitted by **04:00**.

| Istanbul time | Codex (core) | Cursor Cloud (parallel) | Ozgur / muse | Done when |
|---|---|---|---|---|
| 19:00-19:30 | Scaffold package, `pyproject.toml`, `config.py`, `audit/db.py`, API smoke test (`GET /v1/status`, one Spark text call) | CC-1 mock site starts | Ozgur confirms repo name, creates repo, pushes docs; muse updates STATUS | smoke test prints model reply |
| 19:30-21:30 | `wonder_driver.py` (computer-use loop, recorder, step log), `scripted_agent.py` | CC-1 mock site done; CC-4 tests start | Ozgur reviews CC-1 PR | Wonder completes `happy` once end to end; scripted agent runs all 8 scenarios |
| 21:30-23:00 | `keyframes.py`, `spark_judge.py`, `dom_checks.py`, `verdict.py`, `python -m receipts.run` | CC-2 benchmark runner + episodes.yaml | muse drafts SUBMISSION answers v2 | `payment_declined` run returns FALSE_DONE with an evidence frame |
| 23:00-00:30 | `sam_evidence.py` (box overlay first, mask if time) | CC-3 HTML report | Ozgur: dry-run demo flow | report shows the declined toast boxed |
| 00:30-01:30 | Run benchmark (18), fix bugs; live Wonder set if time | CC-4 tests green in CI | muse fills README results | `runs/bench/results.md` exists with real numbers |
| 01:30-02:00 | **Feature freeze.** README quickstart verified on a clean clone | none | Ozgur records screen captures | fresh clone runs quickstart |
| 02:00-03:00 | Bug fixes only | none | **Ozgur records 3-min video** (DEMO.md) | video file exists, <= 3:00 |
| 03:00-03:30 | Tag `v0.1.0` | none | Upload video (YouTube unlisted or Loom: TODO confirm accepted hosts), README link + GIF | public video URL works logged out |
| 03:30-04:00 | none | none | **Submit form** with SUBMISSION.md answers | confirmation email/screen |
| 04:00-05:00 | Buffer only. No new features. | none | Fix broken links only | n/a |

## Cut lines (drop in this order if behind)
1. **At 21:30 Wonder loop not working** -> demo uses the scripted agent for the action and Wonder only as the claim narrator; keep Spark judge + SAM (still 3 Meta models). Say so honestly in README.
2. **At 23:00 Spark structured output flaky** -> JSON-only prompt + `json.loads` + 1 retry; else `UNSURE`.
3. **At 00:30 SAM not working** -> draw the evidence highlight from DOM bounding box of `[data-receipt="error"]` and say "SAM: TODO". Masks -> boxes first anyway.
4. **At 00:30 benchmark not running** -> 10 episodes (EVAL.md cut list), Spark-only + DOM-only columns.
5. Drop: live Wonder set, video input to Spark, Muse Code export, CI.
6. Never drop: public repo, README with honest results, the one caught false-done on camera, the video, the form.
