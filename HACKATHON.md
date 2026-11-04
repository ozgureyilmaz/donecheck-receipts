# HACKATHON.md: Meta Global AI Developer Hackathon facts

Source: public event page https://cerebralvalley.ai/e/meta-ai-developer-hackathon, read Nov 4 2026 ~19:00 Istanbul (UTC+3). Items marked **(email)** come from Cerebral Valley host emails Ozgur received and could not be re-checked on the public page. Items marked **UNCONFIRMED** are third-party or assumed.

## Deadline

| | Time |
|---|---|
| Submissions due (official) | **Wed Nov 4 2026, 9:00 PM ET** (EST, UTC-5) |
| Same moment in UTC | Thu Nov 5 2026, 02:00 UTC |
| Same moment in Istanbul | **Thu Nov 5 2026, 05:00 Istanbul (UTC+3)** |
| Our internal target: video recorded | Nov 5 ~03:00 Istanbul |
| Our internal target: form submitted | Nov 5 ~04:00 Istanbul (1 h buffer) |

## Key dates (public page)
- Oct 26: hackathon kickoff livestream
- Oct 27 to Nov 4: live workshops from Meta and partners, office hours on Discord
- **Nov 4: project submissions due by 9:00 PM ET**
- Nov 4 to Nov 8: judging
- Nov 9: closing ceremony, live finals, winners announced
- "Dates may be subject to change at Meta's sole discretion."

## Format
- Remote, worldwide, ten days, $1,000,000 total cash prize pool across two tracks.
- Models in scope (public page): Muse Spark series, Muse Wonder, Muse Image and SAM 3.1 through Meta Model API; Meta open-weights models; Muse Code coding agent.
- Inference partners: Baseten, Modal, Fireworks ($100 credits each to select participants, for Meta open-weights models).

## Track: Indie developer (ours)
- "For individual builders and students. Judged on **creativity, technical ambition, and whether it works**." (public page)
- Startup track (not ours): technical execution and whether it holds up in production.
- Every project enters one track.

## Themes (pick one in the write-up)
- **Build for Your World**: bring Meta's models into your domain; solve a problem you know firsthand.
- **Push the Frontier**: use Meta's models on challenges in software, robotics, etc.; "give a machine a new capability".
- Receipts fits **Push the Frontier** (software: making computer-use agents verifiable) and can cite "Build for Your World" (Ozgur builds and evaluates agents daily).

## Submission requirements
- **Open-source, public GitHub repository** (email). Mandatory.
- **3-minute demo video** (email). Mandatory.
- Submission form at https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit has "many questions" (email). The form is behind login; the public fetch only returned the event overview. Draft answers live in `SUBMISSION.md` and are marked ASSUMED where the question text is unknown.
- Official Rules: published to accepted participants (not on the public page). TODO: Ozgur to check the rules/Discord for video length limit, license requirement, and whether pre-existing code is allowed.

## Judging weights
- UNCONFIRMED (third-party, not on the official page): Impact 30%, Meta tooling 25%, Demo 25%, Technical 20%. Use as a heuristic only.
- Official wording for Indie: creativity, technical ambition, whether it works.

## Our entry
- Project: **Receipts** (DoneCheck v2). Continuation of Ozgur's approved application idea "DoneCheck".
- Repo name: `donecheck-receipts` under https://github.com/ozgureyilmaz (name was free as of Nov 4 2026 18:59 Istanbul). Not created yet; Ozgur confirms first.
- Scope guard: one task (book a flight on a mock site), one caught false-done on camera, a 10 to 20 episode benchmark, the video, the README.

## Meta Model API facts we rely on (verified at dev.meta.ai/docs, Nov 4 2026)
- Base URL `https://api.meta.ai/v1`, `Authorization: Bearer $MODEL_API_KEY`.
- Endpoints: `POST /v1/responses`, `POST /v1/chat/completions`, `POST /v1/messages`, `POST/GET /v1/files`, `GET /v1/models`, `GET /v1/status` (unauthenticated).
- Model IDs: `muse-wonder-1.0` (preview), `muse-spark-1.3`, `sam-3.1`.
- Computer use: Responses API with `tools: [{"type": "computer"}]`; model returns `computer_call` items; we return `computer_call_output` with a screenshot. No hosted VM: we supply the driver (Playwright).
- SAM 3.1: Responses API, `input_text` noun phrase plus `input_image` or video; output is a box + mask per match in a special-token text grammar.
- Muse Code: the `muse` CLI coding agent; `muse export` produces a byte-deterministic session JSON (useful as our own build-process receipt).
