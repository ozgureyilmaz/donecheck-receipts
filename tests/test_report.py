from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from receipts.audit.db import AuditDB
from receipts.report import render_report

FIXTURE = Path(__file__).parent / "fixtures" / "report" / "demo-run"


def copied_fixture(tmp_path: Path) -> Path:
    run_dir = tmp_path / "20261104T190000-payment_declined-scripted"
    shutil.copytree(FIXTURE, run_dir)
    return run_dir


def test_report_renders_verdict_evidence_and_escapes_untrusted_text(tmp_path: Path) -> None:
    run_dir = copied_fixture(tmp_path)
    report = render_report(run_dir, tmp_path / "missing.sqlite")
    html = report.read_text(encoding="utf-8")

    assert report == run_dir / "report.html"
    assert "FALSE DONE" in html
    assert "IST → LHR" in html
    assert "Flight booked." in html
    assert "&lt;script&gt;window.claimRan = true&lt;/script&gt;" in html
    assert "<script>window.claimRan = true</script>" not in html
    assert "Spark" in html and "DOM" in html and "Combined" in html
    assert "pnr_present" in html and "error_toast_seen" in html
    assert 'id="evidence-frame"' in html
    assert 'x="704.000" y="88.000" width="506.000" height="108.000"' in html
    assert 'href="evidence_mask.svg"' in html
    assert 'href="video.webm#t=8.400"' in html
    assert 'src="screens/002.png"' in html
    assert "Computed now from run files" in html
    assert "https://" not in html


def test_safety_check_is_hashed_after_redaction_and_rendered(tmp_path, monkeypatch):
    run = copied_fixture(tmp_path)
    db_path = tmp_path / "audit.sqlite"
    monkeypatch.setenv("MODEL_API_KEY", "test-private-token")
    with AuditDB(db_path) as db:
        db.start_run(run.name, "payment_declined", "wonder", {}, "2026-11-04T19:00:00Z")
        db.safety_check(
            run.name,
            {
                "id": "s1",
                "code": "computer_action_requires_confirmation",
                "message": "Review <script> test-private-token",
                "decision": "approved",
                "rule": "allowlisted_mock_confirmation",
                "url": "http://127.0.0.1:8000/payment",
                "step": 2,
                "timestamp": "2026-11-04T19:00:01Z",
                "call_id": "c1",
            },
        )
    with sqlite3.connect(db_path) as connection:
        record = json.loads(
            connection.execute(
                "SELECT payload_json FROM events WHERE kind='safety_check' AND run_id=?",
                (run.name,),
            ).fetchone()[0]
        )
    digest = record.pop("sha256")
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canonical.encode()).hexdigest() == digest
    assert record["message"] == "Review <script> [REDACTED]"
    html = render_report(run, db_path).read_text()
    assert "Safety checks" in html and "APPROVED" in html
    assert "allowlisted_mock_confirmation" in html and digest in html
    assert "Review &lt;script&gt; [REDACTED]" in html


def test_sam_followup_selects_its_frame_and_preserves_original_report(tmp_path):
    run = copied_fixture(tmp_path)
    db_path = tmp_path / "missing.sqlite"
    original = render_report(run, db_path)
    before = original.read_bytes()
    followup = run / "sam-point" / "evidence.json"
    followup.parent.mkdir()
    followup.write_text(json.dumps({"frame": 1, "source": "sam-3.1", "concept": "Observed toast"}))
    report = render_report(run, db_path, sam_evidence_file=followup)
    html = report.read_text()
    assert report.name == "report-sam-point.html"
    assert 'id="evidence-image" src="screens/001.png"' in html
    assert "Evidence follow-up" in html and "Observed toast" in html
    assert "FALSE DONE" in html
    assert original.read_bytes() == before


def test_boxes_scale_from_detector_dimensions_to_frame_dimensions(tmp_path: Path) -> None:
    run_dir = copied_fixture(tmp_path)
    evidence_path = run_dir / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["source"] = "dom"
    evidence["boxes"] = [{"x1": 160, "y1": 20, "x2": 300, "y2": 60, "w": 320, "h": 200}]
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    html = render_report(run_dir, tmp_path / "missing.sqlite").read_text(encoding="utf-8")
    assert 'x="640.000" y="80.000" width="560.000" height="160.000"' in html
    assert "Evidence source: dom" in html


def test_report_uses_sqlite_goal_and_audit_hashes_when_available(tmp_path: Path) -> None:
    run_dir = copied_fixture(tmp_path)
    (run_dir / "goal.json").unlink()
    database = tmp_path / "audit.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, goal TEXT);"
        "CREATE TABLE artifacts (run_id TEXT, path TEXT, sha256 TEXT, size INTEGER);"
    )
    run_id = run_dir.name
    connection.execute(
        "INSERT INTO runs VALUES (?, ?)",
        (run_id, json.dumps({"from": "JFK", "to": "CDG", "date": "2026-11-21"})),
    )
    digest = hashlib.sha256((run_dir / "claim.txt").read_bytes()).hexdigest()
    connection.execute(
        "INSERT INTO artifacts VALUES (?, ?, ?, ?)",
        (run_id, "claim.txt", digest, (run_dir / "claim.txt").stat().st_size),
    )
    connection.commit()
    connection.close()

    html = render_report(run_dir, database).read_text(encoding="utf-8")
    assert "JFK → CDG" in html
    assert digest in html
    assert "Recorded in SQLite audit trail" in html
    assert "Computed now from run files" not in html


def test_report_output_is_stable_when_sqlite_records_its_own_hash(tmp_path: Path) -> None:
    run_dir = copied_fixture(tmp_path)
    database = tmp_path / "audit.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE artifacts (run_id TEXT, path TEXT, sha256 TEXT, size INTEGER)")
    claim_bytes = (run_dir / "claim.txt").read_bytes()
    connection.execute(
        "INSERT INTO artifacts VALUES (?, ?, ?, ?)",
        (
            run_dir.name,
            "claim.txt",
            hashlib.sha256(claim_bytes).hexdigest(),
            len(claim_bytes),
        ),
    )
    connection.commit()
    connection.close()

    first_report = render_report(run_dir, database)
    first_bytes = first_report.read_bytes()
    first_hash = hashlib.sha256(first_bytes).hexdigest()
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO artifacts VALUES (?, ?, ?, ?)",
        (run_dir.name, str(first_report), first_hash, len(first_bytes)),
    )
    connection.commit()
    connection.close()

    second_report = render_report(run_dir, database)
    second_bytes = second_report.read_bytes()
    assert second_bytes == first_bytes
    assert hashlib.sha256(second_bytes).hexdigest() == first_hash
    assert "report.html" not in second_bytes.decode("utf-8")


def test_untrusted_mask_string_and_escaping_paths_are_not_inlined(tmp_path: Path) -> None:
    run_dir = copied_fixture(tmp_path)
    evidence_path = run_dir / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["mask_svg"] = '<svg onload="alert(1)"><script>bad()</script></svg>'
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    steps_path = run_dir / "steps.jsonl"
    steps = [json.loads(line) for line in steps_path.read_text(encoding="utf-8").splitlines()]
    steps[2]["screenshot"] = "../../outside.png"
    steps_path.write_text("\n".join(json.dumps(step) for step in steps), encoding="utf-8")

    html = render_report(run_dir, tmp_path / "missing.sqlite").read_text(encoding="utf-8")
    assert 'onload="alert(1)"' not in html
    assert "../../outside.png" not in html
    assert 'src="screens/002.png"' in html  # Safe in-run fallback is used.
    assert "javascript:" not in html


def test_missing_run_artifacts_render_as_unsure_and_cli_prints_output(tmp_path: Path) -> None:
    run_dir = tmp_path / "empty-run"
    run_dir.mkdir()
    output = subprocess.run(
        [
            sys.executable,
            "-m",
            "receipts.report",
            str(run_dir),
            "--db",
            str(tmp_path / "absent.sqlite"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert output.stdout.strip() == str(run_dir / "report.html")
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "UNSURE" in html
    assert "No agent claim captured." in html
    assert "Evidence screenshot is unavailable" in html
    assert "No DOM checks were recorded." in html
