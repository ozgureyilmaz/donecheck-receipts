"""Record, audit, and report one local booking episode."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from receipts.agent.recorder import Recorder
from receipts.agent.scripted_agent import run_scripted
from receipts.agent.wonder_driver import install_local_boundary, run_wonder
from receipts.audit.db import AuditDB, redact
from receipts.auditor.dom_checks import check_dom, claims_done
from receipts.auditor.keyframes import pick_keyframes
from receipts.auditor.sam_evidence import make_evidence
from receipts.auditor.spark_judge import judge
from receipts.auditor.verdict import combine
from receipts.common import normalize_goal, utc_now, write_json
from receipts.config import Config, load_config
from receipts.mocksite.scenarios import SCENARIOS


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
    run_dir: Path, scenario: str, agent: str, goal: dict, config: Config, offline: bool
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
                steps = recorder.steps
                await context.close()
                if video:
                    await video.save_as(str(run_dir / "video.webm"))
                    await video.delete()
                await browser.close()
        (run_dir / "claim.txt").write_text(redact(claim) + "\n")
        if failure:
            write_json(run_dir / "agent_failure.json", failure)
        # Auditor uses only goal, claim, screenshots and client DOM. No oracle argument.
        dom = check_dom(run_dir, steps, goal, claim)
        frames = pick_keyframes(run_dir, steps)
        spark = await judge(run_dir, goal, claim, frames, config, offline)
        combined = combine(spark, dom)
        combined = _apply_agent_failure(combined, dom, claim, failure)
        verdict = {
            "spark": spark,
            "dom": dom,
            "combined": combined,
            "ts": utc_now(),
            "t_s": round(time.monotonic() - started, 4),
        }
        evidence = await make_evidence(run_dir, combined, steps, config, offline)
        evidence.update({"ts": utc_now(), "t_s": round(time.monotonic() - started, 4)})
        write_json(run_dir / "verdict.json", verdict)
        write_json(run_dir / "evidence.json", evidence)
        for check in dom["checks"]:
            db.add_check(run_id, check["name"], check["passed"], check["detail"])
        for source in ("spark", "dom", "combined"):
            db.add_verdict(run_id, source, {**verdict[source], "run_dir": run_dir})
        db.add_evidence(run_id, {**evidence, "run_dir": run_dir})
        # Oracle is fetched only after auditing and only for scoring/storage.
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            response = await client.get(f"{config.site_url}/api/state", params={"session": run_id})
            response.raise_for_status()
            oracle = response.json()
        oracle.update({"recorded_at": utc_now(), "t_s": round(time.monotonic() - started, 4)})
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
    runs_dir: Path | str = "runs",
) -> Path:
    if scenario not in SCENARIOS or agent not in {"scripted", "wonder"}:
        raise ValueError("Unknown scenario or agent")
    config = load_config()
    run_root = Path(runs_dir)
    run_root.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{scenario}-{agent}"
        run_dir = run_root / run_id
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            time.sleep(0.05)
    with local_site(config, run_dir):
        asyncio.run(_episode(run_dir, scenario, agent, normalize_goal(goal), config, offline))
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
    parser.add_argument(
        "--offline",
        action="store_true",
        help="No model calls; Spark is UNSURE and evidence is from DOM",
    )
    parser.add_argument(
        "--goal", help="Task goal as JSON; defaults to IST to LHR, Nov 20, Ada Lovelace"
    )
    args = parser.parse_args()
    try:
        directory = run_episode(
            args.scenario,
            args.agent,
            json.loads(args.goal) if args.goal else None,
            offline=args.offline,
        )
        verdict = json.loads((directory / "verdict.json").read_text())
        evidence = json.loads((directory / "evidence.json").read_text())
        print(
            json.dumps(
                {
                    "run_dir": str(directory),
                    "spark": verdict["spark"]["verdict"],
                    "dom": verdict["dom"]["verdict"],
                    "combined": verdict["combined"]["verdict"],
                    "evidence_frame": evidence["frame"],
                    "evidence_source": evidence["source"],
                    "report": str(directory / "report.html"),
                }
            )
        )
        return 2 if (directory / "agent_failure.json").exists() else 0
    except Exception as exc:
        print(
            f"UNSURE: pipeline failed ({type(exc).__name__}): {redact(str(exc))}", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
