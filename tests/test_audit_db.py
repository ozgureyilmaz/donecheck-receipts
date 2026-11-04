import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from receipts.audit.db import AuditDB, redact, sha256_file


def _run(db: AuditDB, run_id: str = "run-1") -> None:
    db.start_run(run_id, "happy", "scripted", "Book a flight", "2026-11-04T12:00:00Z")


def test_wal_foreign_keys_and_event_rows_are_append_only(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite"
    with AuditDB(db_path) as db:
        db._conn.execute(
            """CREATE TEMP TRIGGER run_requires_start_event BEFORE INSERT ON runs
               WHEN NOT EXISTS (
                   SELECT 1 FROM events WHERE run_id=NEW.run_id AND kind='run.started'
               )
               BEGIN SELECT RAISE(ABORT, 'start intent event must come first'); END"""
        )
        _run(db)
        assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        seq = db.event("run-1", "note", {"message": "kept"})
        assert seq > 0
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db._conn.execute("UPDATE events SET kind='changed' WHERE seq=?", (seq,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db._conn.execute("DELETE FROM events WHERE seq=?", (seq,))


def test_intent_event_precedes_derived_write_and_failure_rolls_back(tmp_path: Path) -> None:
    with AuditDB(tmp_path / "audit.sqlite") as db:
        _run(db)
        db._conn.execute(
            """CREATE TEMP TRIGGER check_requires_event BEFORE INSERT ON checks
               WHEN NOT EXISTS (
                   SELECT 1 FROM events WHERE run_id=NEW.run_id AND kind='check.added'
               )
               BEGIN SELECT RAISE(ABORT, 'intent event must come first'); END"""
        )
        db.add_check("run-1", "pnr_present", True, {"pnr": "PNR123"})
        assert db._conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 1

        db._conn.execute(
            """CREATE TEMP TRIGGER fail_check BEFORE INSERT ON checks
               BEGIN SELECT RAISE(ABORT, 'forced failure'); END"""
        )
        events_before = db._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
            db.add_check("run-1", "second_check", False, "fault")
        assert db._conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 1
        assert db._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before


def test_step_and_registered_artifacts_store_relative_paths_and_actual_hashes(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    screenshot = run_dir / "steps" / "000.png"
    dom = run_dir / "steps" / "000.json"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"fake-png-bytes")
    dom.write_text('{"title":"confirmation"}', encoding="utf-8")

    with AuditDB(tmp_path / "audit.sqlite") as db:
        _run(db)
        db.add_step(
            "run-1",
            {
                "idx": 0,
                "t_s": 1.25,
                "action": {"type": "click", "x": 20},
                "screenshot_path": "steps/000.png",
                "dom_path": "steps/000.json",
            },
            run_dir,
        )
        row = db._conn.execute("SELECT * FROM steps WHERE run_id='run-1'").fetchone()
        assert row["screenshot_path"] == "steps/000.png"
        assert row["dom_path"] == "steps/000.json"
        assert row["screenshot_sha256"] == hashlib.sha256(screenshot.read_bytes()).hexdigest()
        assert row["dom_sha256"] == sha256_file(dom)
        artifacts = db._conn.execute(
            "SELECT path, sha256, size FROM artifacts ORDER BY path"
        ).fetchall()
        assert [(row["path"], row["sha256"]) for row in artifacts] == [
            ("steps/000.json", sha256_file(dom)),
            ("steps/000.png", sha256_file(screenshot)),
        ]
        assert all(row["size"] > 0 for row in artifacts)


def test_secrets_are_recursively_redacted_before_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_key = "private-meta-key-123456"
    monkeypatch.setenv("MODEL_API_KEY", configured_key)
    monkeypatch.setenv("META_API_KEY", "fallback-meta-key-654321")

    redacted = redact(
        {
            "goal": f"Book flight with note {configured_key}",
            "headers": {"Authorization": "Bearer top-secret-token"},
            "nested": ["sk-test_secret_12345678", {"ordinary": "safe"}],
            "model_api_key": configured_key,
        }
    )
    serialized = json.dumps(redacted)
    assert configured_key not in serialized
    assert "fallback-meta-key-654321" not in serialized
    assert "top-secret-token" not in serialized
    assert "sk-test_secret_12345678" not in serialized
    assert redacted["headers"]["Authorization"] == "[REDACTED]"

    with AuditDB(tmp_path / "audit.sqlite") as db:
        db.start_run("run-secret", "happy", "scripted", f"Book with {configured_key}", "start")
        db.end_run(
            "run-secret",
            f"Booked {configured_key}; Authorization: Bearer another-secret-token",
            {"access_token": "third-secret-token", "status": "CONFIRMED"},
            "end",
        )
        db.event("run-secret", "debug", redacted)
        db.add_check("run-secret", "claim", True, "sk-response_abcdefghijk")
        all_text = "\n".join(
            str(tuple(row))
            for table in ("runs", "events", "checks")
            for row in db._conn.execute(f"SELECT * FROM {table}").fetchall()
        )
        for secret in (
            configured_key,
            "fallback-meta-key-654321",
            "top-secret-token",
            "another-secret-token",
            "third-secret-token",
            "sk-response_abcdefghijk",
        ):
            assert secret not in all_text


def test_pan_redaction_preserves_last_four_and_does_not_rewrite_hashes_or_run_ids(
    tmp_path: Path,
) -> None:
    run_id = "20261104T162815-payment_declined-scripted"
    digest = "0" * 64
    value = {
        "action": "fill card 4242424242424242 then 4242 4242-4242 4242",
        "nested": [{"card_number": "4242424242424242", "cvc": "123"}],
        "run_id": run_id,
        "sha256": digest,
        "long_number": "12345678901234567890",
        "embedded": "x4111111111111111x",
    }

    redacted = redact(value)
    assert redacted["action"] == "fill card [CARD ending 4242] then [CARD ending 4242]"
    assert redacted["nested"] == [{"card_number": "[REDACTED]", "cvc": "[REDACTED]"}]
    assert redacted["run_id"] == run_id
    assert redacted["sha256"] == digest
    assert redacted["long_number"] == "12345678901234567890"
    assert redacted["embedded"] == "x4111111111111111x"

    with AuditDB(tmp_path / "audit.sqlite") as db:
        db.start_run(run_id, "happy", "scripted", "Book flight", "2026-11-04T12:00:00Z")
        db.event(run_id, "action", value)
        event_payload = db._conn.execute(
            "SELECT payload_json FROM events WHERE kind='action'"
        ).fetchone()[0]
        assert "4242424242424242" not in event_payload
        assert "[CARD ending 4242]" in event_payload
        assert digest in event_payload
        assert run_id in event_payload


def test_payment_card_input_is_masked_in_browser() -> None:
    template = Path("receipts/mocksite/templates/payment.html").read_text(encoding="utf-8")
    assert 'id="pay-card" name="card_number" type="password"' in template


def test_run_end_verdict_evidence_and_artifact_writes(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    raw_response = run_dir / "judge.json"
    mask = run_dir / "mask.png"
    raw_response.write_text('{"verdict":"TRUE_DONE"}', encoding="utf-8")
    mask.write_bytes(b"mask")

    with AuditDB(tmp_path / "audit.sqlite") as db:
        _run(db)
        db.end_run("run-1", "Flight booked", {"status": "CONFIRMED"}, "done")
        db.add_verdict(
            "run-1",
            "spark",
            {
                "verdict": "TRUE_DONE",
                "confidence": 0.92,
                "evidence_frame": 2,
                "evidence_timestamp_s": 4.5,
                "rationale": "PNR visible",
                "model": "muse-spark-1.3",
                "raw_response_path": "judge.json",
                "run_dir": run_dir,
            },
        )
        db.add_evidence(
            "run-1",
            {
                "frame_idx": 2,
                "concept": "confirmation PNR",
                "box": {"x1": 10, "y1": 20, "x2": 40, "y2": 60},
                "mask_path": "mask.png",
                "model": "sam-3.1",
                "run_dir": run_dir,
            },
        )
        assert db.register_artifact("run-1", raw_response, run_dir) == sha256_file(raw_response)
        run = db._conn.execute("SELECT * FROM runs WHERE run_id='run-1'").fetchone()
        verdict = db._conn.execute("SELECT * FROM verdicts WHERE source='spark'").fetchone()
        evidence = db._conn.execute("SELECT * FROM evidence").fetchone()
        assert run["claim_text"] == "Flight booked"
        assert json.loads(run["oracle_state_json"]) == {"status": "CONFIRMED"}
        assert verdict["verdict"] == "TRUE_DONE"
        assert verdict["evidence_t_s"] == 4.5
        assert verdict["raw_response_path"] == "judge.json"
        assert evidence["mask_path"] == "mask.png"
        assert db._conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 2
