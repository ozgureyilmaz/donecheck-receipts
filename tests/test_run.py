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
    with sqlite3.connect(database) as conn:
        rows = conn.execute(
            "SELECT path,sha256 FROM artifacts WHERE run_id=?", (run_dir.name,)
        ).fetchall()
        for path, expected in rows:
            assert hashlib.sha256((run_dir / path).read_bytes()).hexdigest() == expected, path
        assert len(rows) == len([p for p in run_dir.rglob("*") if p.is_file()])
