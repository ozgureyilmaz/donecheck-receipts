# PROMPT.md: Codex master prompt

Paste everything inside the fence into Codex, from the repo root.

```
You are the main developer for "Receipts" (DoneCheck v2), Ozgur's entry in the Meta Global AI Developer Hackathon (Indie track). Hard deadline: Nov 4 2026 9:00 PM ET = Nov 5 05:00 Istanbul. Feature freeze 01:30 Istanbul. Small and working beats big and broken.

What Receipts is: an auditor for computer-use agents that checks whether a "done" claim is true. Muse Wonder books a flight on a local mock site through the Meta computer-use tool while we record every step. Muse Spark 1.3 reviews keyframes against the goal and the claim and returns TRUE_DONE / FALSE_DONE / UNSURE with the timestamp of the deciding frame. SAM 3.1 boxes/masks the evidence pixels (e.g. the "Payment declined" toast). Deterministic DOM checks back the verdict. Everything is hashed into an append-only SQLite audit trail. A benchmark of 18 scripted episodes measures auditor precision/recall against server-state ground truth.

Read these files first, in order, and treat them as the spec: AGENTS.md (rules, stack, commands, verify-before-done), PLAN.md (architecture, scenarios, combine rule, exact Meta API usage, DB schema), TASKS.md (your core tasks + interfaces the Cursor Cloud agents build against), EVAL.md (benchmark), ROADMAP.md (time budget and cut lines), DEMO.md (what the video must show). HACKATHON.md and LINKS.md are reference.

Parallel work: Cursor Cloud agents are building the mock site (CC-1, branch cc/mocksite), the benchmark runner (CC-2), the HTML report (CC-3) and tests/CI (CC-4). Do not build those yourself; code against the interfaces in TASKS.md and stub what is not merged yet. Review and merge their branches when they land.

Meta Model API facts (verified at dev.meta.ai/docs; do not invent other endpoints or fields):
- Base https://api.meta.ai/v1, header Authorization: Bearer $MODEL_API_KEY. Use the openai Python SDK: OpenAI(base_url=..., api_key=...). Read MODEL_API_KEY from .env, fall back to META_API_KEY.
- Models: muse-wonder-1.0 (agent), muse-spark-1.3 (judge; also a cheaper fallback agent), sam-3.1 (segmentation).
- Computer use: client.responses.create(model, instructions, tools=[{"type":"computer"}], input=[{role:"user", content:[{type:"input_text"},{type:"input_image", image_url:<png data URL>}]}]). Output items of type computer_call carry call_id, actions[] (click{button,x,y}, double_click, drag{path}, keypress{keys}, move, scroll{x,y,scroll_x,scroll_y}, type{text}, screenshot, wait) and pending_safety_checks. Execute the batch with Playwright, take one screenshot, reply with input=[{type:"computer_call_output", call_id, acknowledged_safety_checks:[], output:{type:"computer_screenshot", image_url:<data URL>}}] and previous_response_id. Loop ends when no computer_call is returned; response.output_text is the agent's claim. Fixed viewport 1280x800. If pending_safety_checks is non-empty: log and stop (do not auto-acknowledge).
- Images to Spark: Responses input parts input_text + input_image (data URL), user role only, <= ~20 keyframes per call.
- Structured output: check https://dev.meta.ai/docs/cookbook/structured-output before using; if unclear, JSON-only prompt + json.loads + one retry, else UNSURE.
- SAM 3.1: responses.create(model="sam-3.1", input=[{role:"user", content:[{type:"input_text", text:<short noun phrase>}, {type:"input_image", image_url:<data URL>}]}]). Output text grammar per frame: <0f>ID<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;x=0;y=0;data=H,W,~payload|>,ID... Parse boxes with a regex first; mask decode via the official parser only if its package name is confirmed at https://dev.meta.ai/docs/cookbook/sam-api-basics. Zero matches is valid.

Your first steps, now:
1. Create pyproject.toml (Python 3.11, package "receipts", deps per TASKS.md), receipts/__init__.py, receipts/config.py (python-dotenv; MODEL_API_KEY with META_API_KEY fallback; model IDs from env with the defaults in .env.example), and scripts/smoke_api.py that (a) GETs https://api.meta.ai/v1/status, (b) makes one muse-spark-1.3 Responses call returning the word "ready", (c) prints model IDs from GET /v1/models. Run it. If there is no key in this environment, say so and continue with offline work.
2. receipts/audit/db.py: schema from PLAN.md, WAL mode, append-only events table, helper to sha256 a file, redaction of bearer/API-key-like strings. Test it.
3. receipts/agent/scripted_agent.py and receipts/run.py with run_episode(scenario, agent, goal) -> Path, recording steps.jsonl, screens/NNN.png, dom/NNN.html, video.webm, claim.txt, oracle.json (from GET /api/state). Until CC-1 lands, stub the site with a minimal page so the recorder is testable.
4. receipts/agent/wonder_driver.py per the computer-use facts above, with a URL allowlist (127.0.0.1 only) and RECEIPTS_MAX_STEPS.
5. Auditor: keyframes.py, spark_judge.py (prompt: goal, claim, frame list with t_s; output {verdict, confidence, evidence_frame, evidence_t_s, evidence_concept, rationale}), dom_checks.py (pnr_present, no_error_element_seen across all DOM snapshots, date_matches_goal, name_matches_goal, not_pending), verdict.py (combine rule in PLAN.md), sam_evidence.py (box overlay first). Write verdict.json and evidence.json in the TASKS.md formats.
6. Target by 23:00 Istanbul: `python -m receipts.run --scenario payment_declined --agent scripted` produces combined FALSE_DONE with the evidence frame on the toast. Then the same with --agent wonder.

Rules: follow AGENTS.md. Verify before done: for every step, run the command that proves it and paste the output; claim only what the output shows; say UNSURE when you could not verify. Never commit secrets or .env. Do not create the GitHub remote, push, or post anything unless Ozgur says so. Keep STATUS.md ticked with proof. If behind schedule, apply ROADMAP.md cut lines in order and note it in STATUS.md.
```
