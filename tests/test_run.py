import hashlib
import json
import socket
import sqlite3

import pytest

from receipts.run import run_episode


@pytest.mark.browser
def test_declined_episode_keeps_transient_evidence_and_final_hashes(tmp_path, monkeypatch):
    # Choose a free loopback port without touching another developer's server.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    database = tmp_path / "audit.sqlite"
    monkeypatch.setenv("MOCKSITE_HOST", "127.0.0.1")
    monkeypatch.setenv("MOCKSITE_PORT", str(port))
    monkeypatch.setenv("RECEIPTS_DB", str(database))
    from receipts.auditor.spark_judge import judge

    async def isolated_judge(run_dir, goal, claim, frames, config, offline):
        assert not (run_dir / "oracle.json").exists(), "Oracle must not be an auditor input"
        assert goal["from"] == "IST"
        return await judge(run_dir, goal, claim, frames, config, offline)

    monkeypatch.setattr("receipts.run.judge", isolated_judge)
    run_dir = run_episode("payment_declined", "scripted", offline=True, runs_dir=tmp_path / "runs")
    verdict = json.loads((run_dir / "verdict.json").read_text())
    evidence = json.loads((run_dir / "evidence.json").read_text())
    steps = [json.loads(line) for line in (run_dir / "steps.jsonl").read_text().splitlines()]
    frame = next(step for step in steps if step["idx"] == evidence["frame"])
    assert verdict["spark"]["verdict"] == "UNSURE"
    assert verdict["combined"]["verdict"] == "FALSE_DONE"
    assert evidence["source"] == "dom" and evidence["boxes"]
    assert "Payment declined" in (run_dir / frame["dom"]).read_text()
    assert "Payment declined" not in (run_dir / steps[-1]["dom"]).read_text()
    assert "Trip summary" in (run_dir / steps[-1]["dom"]).read_text()
    assert (run_dir / "video.webm").stat().st_size > 1000
    assert (run_dir / "evidence_overlay.png").exists()
    assert (run_dir / "report.html").exists()
    assert "4242424242424242" not in (run_dir / "steps.jsonl").read_text()
    assert 'type="password"' in (run_dir / "dom/010.html").read_text()
    with sqlite3.connect(database) as conn:
        rows = conn.execute(
            "SELECT path,sha256 FROM artifacts WHERE run_id=?", (run_dir.name,)
        ).fetchall()
        for path, expected in rows:
            assert hashlib.sha256((run_dir / path).read_bytes()).hexdigest() == expected, path
        assert len(rows) == len([p for p in run_dir.rglob("*") if p.is_file()])


@pytest.mark.browser
@pytest.mark.parametrize("failed_stage", ["wonder", "spark_verification"])
def test_strict_live_stops_before_next_provider_call(tmp_path, monkeypatch, failed_stage):
    from receipts.agent.scripted_agent import run_scripted
    from receipts.auditor.sam_evidence import make_evidence

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("MOCKSITE_PORT", str(port))
    monkeypatch.setenv("RECEIPTS_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("MODEL_API_KEY", "strict-mode-test-placeholder")
    monkeypatch.setenv("META_API_BASE", "https://api.meta.ai/v1")
    monkeypatch.setenv("RECEIPTS_MAX_STEPS", "40")
    calls = []

    async def wonder(page, recorder, goal, config, blocked):
        calls.append("wonder")
        assert config.max_steps == 30
        assert config.agent_model == "muse-spark-1.3"
        if failed_stage == "wonder":
            raise RuntimeError("simulated provider failure")
        return await run_scripted(page, recorder, goal)

    async def spark(run_dir, goal, claim, frames, config, *, retry_invalid):
        calls.append("spark")
        assert not retry_invalid
        assert not (run_dir / "oracle.json").exists()
        return {
            "available": True,
            "verdict": "FALSE_DONE",
            "evidence_frame": frames[0]["idx"],
            "evidence_t_s": frames[0]["t_s"],
            "evidence_concept": "a frame before payment",
        }

    async def evidence(run_dir, verdict, steps, config, offline):
        assert offline, "A failed gate must skip paid segmentation"
        return await make_evidence(run_dir, verdict, steps, config, offline)

    monkeypatch.setattr("receipts.run.run_wonder", wonder)
    monkeypatch.setattr("receipts.run.judge", spark)
    monkeypatch.setattr("receipts.run.make_evidence", evidence)
    run = run_episode(
        "payment_declined",
        "wonder",
        strict_live=True,
        actor_model="muse-spark-1.3",
        runs_dir=tmp_path / "runs",
    )
    failure = json.loads((run / "live_failure.json").read_text())
    assert failure["stage"] == failed_stage
    assert calls == (["wonder"] if failed_stage == "wonder" else ["wonder", "spark"])
    assert (run / "report.html").is_file()
    assert "Actor muse-spark-1.3" in (run / "report.html").read_text()
    assert (run / "oracle.json").is_file()
