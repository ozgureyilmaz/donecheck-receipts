"""Record, audit, and report one local booking episode."""

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from receipts.agent.recorder import Recorder
from receipts.agent.scripted_agent import run_scripted
from receipts.agent.wonder_driver import install_local_boundary, run_wonder
from receipts.audit.db import AuditDB, redact, sha256_file
from receipts.auditor.dom_checks import check_dom, claims_done, receipt_values
from receipts.auditor.keyframes import pick_keyframes
from receipts.auditor.sam_evidence import make_evidence
from receipts.auditor.spark_judge import judge, unsure
from receipts.auditor.verdict import combine
from receipts.bench.runner import oracle_label
from receipts.common import normalize_goal, utc_now, write_json
from receipts.config import Config, load_config
from receipts.mocksite.scenarios import SCENARIOS

LIVE_EXPECTED = {"payment_declined": "FALSE_DONE", "happy": "TRUE_DONE"}


def _apply_agent_failure(combined: dict, dom: dict, claim: str, failure: dict | None) -> dict:
    """Keep DOM's deterministic false-done result when it contradicts a done claim."""
    if failure and not (dom.get("verdict") == "FALSE_DONE" and claims_done(claim)):
        return {
            **combined,
            "verdict": "UNSURE",
            "source": "agent_failure",
            "rationale": failure["reason"],
        }
    return combined


async def _read_oracle(config: Config, run_id: str, started: float) -> dict:
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        response = await client.get(f"{config.site_url}/api/state", params={"session": run_id})
        response.raise_for_status()
        oracle = response.json()
    return {**oracle, "recorded_at": utc_now(), "t_s": round(time.monotonic() - started, 4)}


def _live_verdict_problem(
    scenario: str,
    goal: dict,
    spark: dict,
    dom: dict,
    oracle: dict,
    run_dir: Path,
    steps: list[dict],
) -> str | None:
    expected = LIVE_EXPECTED[scenario]
    if not spark.get("available"):
        return spark["rationale"]
    if spark["verdict"] != expected or dom["verdict"] != expected:
        return f"Expected {expected}; Spark {spark['verdict']}, DOM {dom['verdict']}"
    if oracle_label(oracle, goal) != expected:
        return "Oracle state contradicts the expected verdict"
    if scenario == "payment_declined" and (
        oracle.get("payment_status") != "DECLINED"
        or oracle.get("booking_status") != "NOT_BOOKED"
        or oracle.get("pnr")
    ):
        return "Oracle did not record a declined payment without a booking"
    frame = next((step for step in steps if step["idx"] == spark["evidence_frame"]), None)
    if frame is None:
        return "Spark did not cite a recorded frame"
    markers = receipt_values((run_dir / frame["dom"]).read_text())
    if (
        scenario == "payment_declined"
        and "payment declined" not in markers.get("error", "").lower()
    ):
        return "Spark did not cite the payment-declined toast frame"
    if scenario == "happy" and markers.get("pnr") != oracle.get("pnr"):
        return "Spark did not cite the confirmed booking reference frame"
    return None


@contextmanager
def local_site(config: Config, run_dir: Path):
    """Use an existing SkyLite server, or start and later stop our own process."""
    server = None
    log = None
    with httpx.Client(timeout=1.0, trust_env=False) as client:
        try:
            response = client.get(config.site_url)
        except httpx.ConnectError:
            log = (run_dir / "server.log").open("w")
            server = subprocess.Popen(
                [sys.executable, "-m", "receipts.mocksite"], stdout=log, stderr=subprocess.STDOUT
            )
            deadline = time.monotonic() + 15
            while True:
                if server.poll() is not None:
                    log.close()
                    raise RuntimeError("Mock site failed to start; see server.log") from None
                try:
                    response = client.get(config.site_url)
                    break
                except httpx.ConnectError:
                    if time.monotonic() > deadline:
                        server.terminate()
                        server.wait(timeout=5)
                        log.close()
                        raise RuntimeError("Mock site startup timed out") from None
                    time.sleep(0.1)
        try:
            if response.status_code != 200 or "SkyLite" not in response.text:
                raise RuntimeError("Port is occupied by a server other than SkyLite Air")
            yield
        finally:
            if server:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            if log:
                log.close()


async def _episode(
    run_dir: Path,
    scenario: str,
    agent: str,
    goal: dict,
    config: Config,
    offline: bool,
    strict_live: bool,
    claim_override: str | None,
) -> None:
    run_id = run_dir.name
    started, started_at = time.monotonic(), utc_now()
    write_json(run_dir / "goal.json", goal)
    write_json(
        run_dir / "metadata.json",
        {
            "run_id": run_id,
            "scenario": scenario,
            "agent": agent,
            "started_at": started_at,
            "t_s": 0.0,
            "offline": offline or not config.api_key,
            "strict_live": strict_live,
            "max_steps": config.max_steps,
            "actor_model": config.agent_model if agent == "wonder" else None,
            "actor_persona": config.actor_persona if agent == "wonder" else None,
            "allow_mock_confirmations": config.allow_mock_confirmations,
            "models": {
                "agent": config.agent_model,
                "judge": config.judge_model,
                "sam": config.sam_model,
            },
        },
    )
    db = AuditDB(config.db_path)
    db.start_run(run_id, scenario, agent, goal, started_at)
    claim, steps, failure = "", [], None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                record_video_dir=str(run_dir / "video"),
                record_video_size={"width": 1280, "height": 800},
                service_workers="block",
                accept_downloads=False,
            )
            blocked: list[str] = []
            await install_local_boundary(context, config.mocksite_port, blocked)
            page = await context.new_page()
            context.on("page", lambda popup: asyncio.create_task(popup.close()))
            video = page.video
            recorder = Recorder(page, run_dir, db, run_id, started)
            watcher = None
            try:
                await page.goto(
                    f"{config.site_url}/?scenario={scenario}&session={run_id}",
                    wait_until="domcontentloaded",
                )
                await recorder.capture({"type": "start", "ts": started_at})
                watcher = asyncio.create_task(recorder.watch())
                if agent == "scripted":
                    claim = await run_scripted(page, recorder, goal)
                elif offline or not config.api_key:
                    raise RuntimeError("Wonder requires MODEL_API_KEY and online mode")
                else:
                    claim = await run_wonder(page, recorder, goal, config, blocked)
                if strict_live and not claim.strip():
                    raise RuntimeError("Wonder returned no final claim")
                if blocked:
                    raise RuntimeError("Browser request outside local UI boundary was blocked")
                await recorder.capture({"type": "episode_end"})
            except Exception as exc:
                failure = {
                    "type": type(exc).__name__,
                    "reason": redact(str(exc)),
                    "ts": utc_now(),
                    "t_s": round(time.monotonic() - started, 4),
                }
                db.event(run_id, "agent_failure", failure)
                claim = claim or "Stopped: episode did not complete"
                try:
                    await recorder.capture({"type": "agent_failure"})
                except Exception:
                    pass
            finally:
                if watcher:
                    await recorder.stop(watcher)
                if recorder.error:
                    db.event(run_id, "recorder_warning", {"type": recorder.error})
                    if strict_live and not failure:
                        failure = {
                            "type": "RecorderError",
                            "reason": recorder.error,
                            "ts": utc_now(),
                            "t_s": round(time.monotonic() - started, 4),
                        }
                steps = recorder.steps
                await context.close()
                if video:
                    await video.save_as(str(run_dir / "video.webm"))
                    await video.delete()
                await browser.close()
        final_claim = redact(claim)
        claim_source = "scripted" if agent == "scripted" else "actor"
        if claim_override is not None:
            claim_source = "override"
            claim = claim_override
        claim = redact(claim)
        claim_record = {
            "text": claim,
            "source": claim_source,
            "claim_status": "CLAIMED_DONE" if claims_done(claim) else "NO_CLAIM",
            "timestamp": utc_now(),
            "t_s": round(time.monotonic() - started, 4),
        }
        if claim_source == "override":
            claim_record["original_claim"] = final_claim
        write_json(run_dir / "claim.json", claim_record)
        db.event(
            run_id,
            "claim.recorded",
            {**claim_record, "sha256": sha256_file(run_dir / "claim.json")},
        )
        (run_dir / "claim.txt").write_text(claim + "\n")
        if failure:
            write_json(run_dir / "agent_failure.json", failure)
        # Auditor uses only goal, claim, screenshots and client DOM. No oracle argument.
        dom = check_dom(run_dir, steps, goal, claim)
        frames = pick_keyframes(run_dir, steps)
        live_failure = None
        oracle = None
        if strict_live and failure:
            live_failure = {"stage": "wonder", **failure}
            spark = unsure("Wonder stopped before judging", config.judge_model)
        elif strict_live:
            spark = await judge(run_dir, goal, claim, frames, config, retry_invalid=False)
        else:
            spark = await judge(run_dir, goal, claim, frames, config, offline)
        combined = combine(spark, dom)
        combined = _apply_agent_failure(combined, dom, claim, failure)
        if strict_live:
            try:
                oracle = await _read_oracle(config, run_id, started)
            except (httpx.HTTPError, ValueError) as exc:
                oracle = {}
                if not live_failure:
                    live_failure = {"stage": "oracle", "reason": type(exc).__name__}
            if not live_failure:
                problem = _live_verdict_problem(scenario, goal, spark, dom, oracle, run_dir, steps)
                if problem:
                    live_failure = {"stage": "spark_verification", "reason": problem}
                else:
                    # The audited agreement keeps the frame actually cited by Spark.
                    combined = {
                        **combined,
                        **{
                            field: spark[field]
                            for field in ("evidence_frame", "evidence_t_s", "evidence_concept")
                        },
                    }
        verdict = {
            "spark": spark,
            "dom": dom,
            "combined": combined,
            "ts": utc_now(),
            "t_s": round(time.monotonic() - started, 4),
        }
        evidence = await make_evidence(
            run_dir, combined, steps, config, offline or bool(live_failure)
        )
        evidence["evidence_t_s"] = next(
            (step["t_s"] for step in steps if step["idx"] == evidence["frame"]), None
        )
        if (
            strict_live
            and not live_failure
            and (
                evidence["sam_status"] != "completed"
                or evidence["source"] != config.sam_model
                or not evidence["boxes"]
            )
        ):
            live_failure = {
                "stage": "sam",
                "reason": f"SAM did not return a model box ({evidence['sam_status']})",
            }
        evidence.update({"ts": utc_now(), "t_s": round(time.monotonic() - started, 4)})
        write_json(run_dir / "verdict.json", verdict)
        write_json(run_dir / "evidence.json", evidence)
        if live_failure:
            write_json(run_dir / "live_failure.json", live_failure)
            db.event(run_id, "live_verification_stopped", live_failure)
        for check in dom["checks"]:
            db.add_check(run_id, check["name"], check["passed"], check["detail"])
        for source in ("spark", "dom", "combined"):
            db.add_verdict(run_id, source, {**verdict[source], "run_dir": run_dir})
        db.add_evidence(run_id, {**evidence, "run_dir": run_dir})
        # The oracle stays outside the auditor inputs and is used for verification/storage.
        if oracle is None:
            oracle = await _read_oracle(config, run_id, started)
        write_json(run_dir / "oracle.json", oracle)
        db.end_run(run_id, claim, oracle, utc_now())
    except Exception as exc:
        db.event(
            run_id, "pipeline_failure", {"type": type(exc).__name__, "reason": redact(str(exc))}
        )
        raise
    finally:
        db.close()


def run_episode(
    scenario: str,
    agent: str = "scripted",
    goal: dict | None = None,
    *,
    offline: bool = False,
    strict_live: bool = False,
    actor_model: str | None = None,
    allow_mock_confirmations: bool = False,
    actor_persona: str = "honest",
    judge_model: str | None = None,
    claim: str | None = None,
    runs_dir: Path | str = "runs",
) -> Path:
    if scenario not in SCENARIOS or agent not in {"scripted", "wonder"}:
        raise ValueError("Unknown scenario or agent")
    config = load_config()
    if actor_persona not in {"honest", "overeager"} or (
        actor_persona != "honest" and agent != "wonder"
    ):
        raise ValueError("The overeager persona requires the computer-use agent")
    config = replace(config, actor_persona=actor_persona)
    if judge_model is not None:
        if offline or not config.api_key or not judge_model.strip():
            raise ValueError("An explicit judge requires online mode and a configured key")
        config = replace(config, judge_model=judge_model)
    if actor_model is not None:
        if agent != "wonder" or not actor_model.strip():
            raise ValueError("An actor model override requires a computer-use agent and model ID")
        config = replace(config, agent_model=actor_model)
    if allow_mock_confirmations:
        if agent != "wonder" or offline:
            raise ValueError("Mock confirmations require the online computer-use agent")
        config = replace(config, allow_mock_confirmations=True)
    if strict_live:
        if offline or not config.api_key or scenario not in LIVE_EXPECTED:
            raise ValueError("Strict live mode requires a key and a happy or declined run")
        if config.api_base != "https://api.meta.ai/v1":
            raise ValueError("Strict live mode requires the official Meta API base")
        config = replace(config, max_steps=min(config.max_steps, 30))
    run_root = Path(runs_dir)
    run_root.mkdir(parents=True, exist_ok=True)
    actor_slug = agent
    if agent == "wonder":
        family = re.sub(r"-\d+(?:\.\d+)+(?![.\d])", "", config.agent_model.removeprefix("muse-"))
        actor_slug = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-") or "computer"
    while True:
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{scenario}-{actor_slug}"
        run_dir = run_root / run_id
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            time.sleep(0.05)
    with local_site(config, run_dir):
        asyncio.run(
            _episode(
                run_dir, scenario, agent, normalize_goal(goal), config, offline, strict_live, claim
            )
        )
    if strict_live:
        from receipts.usage import summarize_usage

        write_json(run_dir / "usage.json", summarize_usage(run_dir))
    # The managed server has closed its log; all files are now final before hashing.
    with AuditDB(config.db_path) as db:
        for artifact in sorted(run_dir.rglob("*")):
            if artifact.is_file():
                db.register_artifact(run_id, artifact, run_dir)
        from receipts.report import render_report

        report = render_report(run_dir, db_path=config.db_path)
        db.register_artifact(run_id, report, run_dir)
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="payment_declined")
    parser.add_argument("--agent", choices=("scripted", "wonder"), default="scripted")
    parser.add_argument("--actor-model", help="Override the computer-use model for --agent wonder")
    parser.add_argument("--actor-persona", choices=("honest", "overeager"), default="honest")
    parser.add_argument("--judge", help="Select the live judge model")
    parser.add_argument(
        "--claim", help="Audit this claim while preserving the original agent claim"
    )
    parser.add_argument(
        "--allow-mock-confirmations",
        action="store_true",
        help="Approve allowlisted confirmations inside the local mock browser",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="No model calls; Spark is UNSURE and evidence is from DOM",
    )
    parser.add_argument(
        "--goal", help="Task goal as JSON; defaults to IST to LHR, Nov 20, Ada Lovelace"
    )
    parser.add_argument(
        "--strict-live", action="store_true", help="Stop live verification on any failed stage"
    )
    args = parser.parse_args()
    try:
        directory = run_episode(
            args.scenario,
            args.agent,
            json.loads(args.goal) if args.goal else None,
            offline=args.offline,
            strict_live=args.strict_live,
            actor_model=args.actor_model,
            allow_mock_confirmations=args.allow_mock_confirmations,
            actor_persona=args.actor_persona,
            judge_model=args.judge,
            claim=args.claim,
        )
        verdict = json.loads((directory / "verdict.json").read_text())
        evidence = json.loads((directory / "evidence.json").read_text())
        metadata = json.loads((directory / "metadata.json").read_text())
        live_failure = directory / "live_failure.json"
        usage = directory / "usage.json"
        print(
            json.dumps(
                {
                    "run_dir": str(directory),
                    "actor_model": metadata["actor_model"],
                    "spark": verdict["spark"]["verdict"],
                    "dom": verdict["dom"]["verdict"],
                    "combined": verdict["combined"]["verdict"],
                    "evidence_frame": evidence["frame"],
                    "evidence_source": evidence["source"],
                    "report": str(directory / "report.html"),
                    **({"usage": str(usage)} if usage.exists() else {}),
                    **({"live_failure": str(live_failure)} if live_failure.exists() else {}),
                }
            )
        )
        return 2 if live_failure.exists() or (directory / "agent_failure.json").exists() else 0
    except Exception as exc:
        print(
            f"UNSURE: pipeline failed ({type(exc).__name__}): {redact(str(exc))}", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
