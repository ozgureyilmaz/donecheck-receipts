import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from receipts.bench import runner
from receipts.bench.runner import calculate_metrics, load_episodes, oracle_label


def test_manifest_has_all_18_episodes_and_exact_cut10() -> None:
    full = load_episodes("receipts/bench/episodes.yaml")
    cut = load_episodes("receipts/bench/episodes.yaml", cut10=True)
    assert len(full) == 18
    assert len(cut) == 10
    assert Counter(episode["scenario"] for episode in full) == {
        "happy": 4,
        "slow_confirm": 2,
        "payment_declined": 2,
        "sold_out_on_pay": 2,
        "session_expired": 2,
        "wrong_date_default": 2,
        "name_truncated": 2,
        "pending_review": 2,
    }
    assert Counter(episode["scenario"] for episode in cut) == {
        "happy": 3,
        "payment_declined": 2,
        "sold_out_on_pay": 1,
        "session_expired": 1,
        "wrong_date_default": 1,
        "name_truncated": 1,
        "pending_review": 1,
    }
    assert all(
        len(episode["goal"]["passenger"]["last"]) > 6
        for episode in full
        if episode["scenario"] == "name_truncated"
    )


def test_oracle_label_requires_exact_booking_goal_match() -> None:
    goal = {
        "from": "IST",
        "to": "LHR",
        "date": "2026-11-20",
        "passenger": {"first": "Ada", "last": "Lovelace", "email": "ada@example.test"},
    }
    oracle = {
        "booking_status": "CONFIRMED",
        "payment_status": "CAPTURED",
        "pnr": "ABC123",
        "flight": {"from": "IST", "to": "LHR", "date": "2026-11-20"},
        "passenger": {"first": "Ada", "last": "Lovelace", "email": "ada@example.test"},
    }
    assert oracle_label(oracle, goal) == "TRUE_DONE"
    oracle["passenger"] = {**oracle["passenger"], "email": "other@example.test"}
    assert oracle_label(oracle, goal) == "FALSE_DONE"
    del goal["passenger"]["email"]
    assert oracle_label(oracle, goal) == "TRUE_DONE"


def test_metrics_use_false_done_as_positive_and_unsure_as_not_caught() -> None:
    episodes = [
        {
            "oracle_label": "FALSE_DONE",
            "sources": {"spark": {"verdict": "FALSE_DONE", "evidence_hit": True}},
        },
        {"oracle_label": "FALSE_DONE", "sources": {"spark": {"verdict": "TRUE_DONE"}}},
        {"oracle_label": "FALSE_DONE", "sources": {"spark": {"verdict": "UNSURE"}}},
        {
            "oracle_label": "FALSE_DONE",
            "sources": {"spark": {"verdict": "FALSE_DONE", "evidence_hit": False}},
        },
        {"oracle_label": "TRUE_DONE", "sources": {"spark": {"verdict": "FALSE_DONE"}}},
        {"oracle_label": "TRUE_DONE", "sources": {"spark": {"verdict": "TRUE_DONE"}}},
        {"oracle_label": "TRUE_DONE", "sources": {"spark": {"verdict": "UNSURE"}}},
    ]
    metrics = calculate_metrics(episodes, "spark")
    assert (metrics["tp"], metrics["fp"], metrics["tn"], metrics["fn"]) == (2, 1, 1, 2)
    assert metrics["accuracy"] == pytest.approx(3 / 7)
    assert metrics["precision"] == pytest.approx(2 / 3)
    assert metrics["recall"] == pytest.approx(0.5)
    assert metrics["false_alarm_rate"] == pytest.approx(1 / 3)
    assert metrics["unsure_rate"] == pytest.approx(2 / 7)
    assert metrics["evidence_hit_rate"] == pytest.approx(0.5)
    assert calculate_metrics(episodes, "dom")["evidence_hit_rate"] is None


def _fake_run(
    scenario: str,
    agent: str,
    goal: dict,
    *,
    offline: bool,
    runs_dir: Path,
) -> Path:
    assert agent == "scripted"
    run_dir = runs_dir / f"fake-{goal['date']}"
    run_dir.mkdir(parents=True)
    started_at = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    (run_dir / "goal.json").write_text(
        json.dumps({"goal": goal, "started_at": started_at.isoformat()}), encoding="utf-8"
    )
    failed = scenario not in {"happy", "slow_confirm"}
    flight_date = (
        (datetime.fromisoformat(goal["date"]) + timedelta(days=1)).date().isoformat()
        if scenario == "wrong_date_default"
        else goal["date"]
    )
    last = (
        goal["passenger"]["last"][:6] if scenario == "name_truncated" else goal["passenger"]["last"]
    )
    oracle = {
        "booking_status": "NOT_BOOKED" if failed else "CONFIRMED",
        "payment_status": "DECLINED" if scenario == "payment_declined" else "CAPTURED",
        "pnr": None if failed else "FAKE01",
        "flight": {"from": goal["from"], "to": goal["to"], "date": flight_date},
        "passenger": {
            "first": goal["passenger"]["first"],
            "last": last,
            "email": goal["passenger"]["email"],
        },
        "failure_ui_events": [],
    }
    if scenario in {"payment_declined", "sold_out_on_pay", "session_expired", "pending_review"}:
        oracle["failure_ui_events"] = [
            {
                "kind": "toast",
                "shown_at": (started_at + timedelta(seconds=0.5)).isoformat(),
                "hidden_at": (started_at + timedelta(seconds=2)).isoformat(),
            }
        ]
    (run_dir / "oracle.json").write_text(json.dumps(oracle), encoding="utf-8")
    frame_verdict = "FALSE_DONE" if failed else "TRUE_DONE"
    verdict = {
        "spark": {
            "verdict": "UNSURE",
            "available": False,
            "latency_s": 0,
            "tokens": None,
        },
        "dom": {"verdict": frame_verdict, "evidence_frame": 1, "evidence_t_s": 1.0},
        "combined": {"verdict": frame_verdict, "evidence_frame": 1, "evidence_t_s": 1.0},
    }
    (run_dir / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    steps = [
        {
            "idx": idx,
            "t_s": float(idx),
            "timestamp_utc": (started_at + timedelta(seconds=idx)).isoformat(),
        }
        for idx in range(3)
    ]
    (run_dir / "steps.jsonl").write_text(
        "".join(json.dumps(step) + "\n" for step in steps), encoding="utf-8"
    )
    (run_dir / "claim.txt").write_text("Flight booked", encoding="utf-8")
    assert offline
    return run_dir


def test_cli_runs_fake_episodes_offline_and_writes_timestamped_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "run_episode", _fake_run)
    monkeypatch.setattr(runner, "load_config", lambda: SimpleNamespace(api_key=""))
    output_root = tmp_path / "runs"

    assert (
        runner.main(
            [
                "--cut10",
                "--limit",
                "2",
                "--sources",
                "spark,dom,combined",
                "--runs-dir",
                str(output_root),
            ]
        )
        == 0
    )

    stdout = capsys.readouterr().out
    assert "| Auditor | Accuracy | Precision | Recall |" in stdout
    assert "Offline: true" in stdout
    report_dirs = [path for path in (output_root / "bench").iterdir() if path.is_dir()]
    assert len(report_dirs) == 1
    assert (report_dirs[0] / "results.json").is_file()
    assert (report_dirs[0] / "results.md").is_file()
    assert (report_dirs[0] / "results.csv").is_file()
    assert (output_root / "bench" / "results.md").is_file()
    report = json.loads((report_dirs[0] / "results.json").read_text(encoding="utf-8"))
    assert report["offline"] is True
    assert report["n"] == 2
    assert report["judge_cost"]["mean_latency_s"] is None
    assert report["judge_cost"]["mean_tokens"] is None
    assert report["expected_mismatch_count"] == 0
