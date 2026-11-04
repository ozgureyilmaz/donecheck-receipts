# DEMO.md: 3-minute video script and shot list

Target 2:50, hard cap 3:00. Record at 1920x1080, browser zoom so text is legible. Voice-over by Ozgur (English). Record the live run once early (by 01:30) as a fallback clip.

## Script

| Time | Shot | Voice-over |
|---|---|---|
| 0:00-0:15 | Title card: "Receipts: verify before done". Then a chat bubble: agent says "Flight booked." | "Computer-use agents say 'done' a lot. Sometimes they're wrong, and nobody checks. Receipts checks." |
| 0:15-0:35 | Diagram from README (agent -> recording -> Spark judge + DOM checks -> SAM evidence -> SQLite) | "Muse Wonder does the task. We record every step. Muse Spark 1.3 reviews the recording against the goal. SAM 3.1 marks the pixels that prove it. Deterministic checks back the verdict, and every artifact is hashed into an audit trail." |
| 0:35-1:15 | Screen: terminal `python -m receipts.run --scenario payment_declined --agent wonder`; side-by-side browser where Wonder searches IST to LHR, fills Ada Lovelace, pays. Red toast flashes for 3 s, page becomes a neutral "Trip summary". Agent's final message: "Flight booked." | "Goal: book Istanbul to London on November 20 for Ada Lovelace. Watch the payment step. There it is, gone in three seconds. And the agent says: flight booked." |
| 1:15-1:50 | **Demo moment.** HTML report: verdict badge **FALSE DONE**, timeline scrubber jumps to step N (t = xx s), frame with SAM box/mask on "Payment declined". DOM checks panel: `pnr_present: FAIL`, `error_toast_seen: FAIL`. | "Receipts disagrees. False done. It jumps straight to the frame at xx seconds, SAM outlines the payment-declined toast, and two deterministic checks agree: no booking reference, an error was shown." |
| 1:50-2:05 | Same report for a `happy` run: **TRUE DONE**, PNR boxed. | "On an honest run it confirms, and points at the booking reference as its receipt." |
| 2:05-2:35 | Benchmark table from README (Spark only / DOM only / Combined, precision, recall, UNSURE). Live Wonder row if available. | "On 18 scripted episodes, honest successes, silent failures and partial completions, the combined auditor catches X of 12 false dones with Y false alarms. Ground truth is the site's server state, never a model." |
| 2:35-2:50 | `sqlite3 runs/receipts.sqlite "select ..."` showing events + sha256; repo URL | "Every verdict keeps its evidence: frames, hashes, raw model output. It's open source. Agents propose, checks verify, receipts are kept." |
| 2:50-3:00 | End card: repo URL, "Built with Muse Wonder, Muse Spark 1.3, SAM 3.1", Ozgur's X handle | none |

Fill X, Y, N, xx from real runs. Never show numbers that the benchmark did not produce.

## Shot list (capture these files)
1. `title.png`: title card (can be HTML page in `docs/assets/`).
2. `diagram.png`: rendered mermaid from PLAN.md.
3. `wonder_payment_declined.mp4`: screen capture of terminal + browser during the live run (also keep Playwright's `runs/<id>/video.webm`).
4. `report_false_done.mp4`: scrolling the report, clicking the evidence frame.
5. `report_true_done.png`: happy run report.
6. `bench_table.png`: README table.
7. `sqlite_trail.png`: terminal query.
8. `end_card.png`.

## Recording tips
- Hide `.env`, terminal history and API keys. Use a fresh terminal profile.
- If Wonder takes long, cut the waiting with a visible "(sped up 4x)" label.
- If the live run misbehaves on camera, use the fallback clip; say "recorded earlier tonight".
- Export MP4 H.264. Upload as public/unlisted per form rules (TODO: confirm accepted hosts on Discord/rules).
