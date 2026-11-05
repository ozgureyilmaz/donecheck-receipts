import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from receipts.bench.replay import replay_snapshot
from receipts.bench.runner import load_episodes
from receipts.common import normalize_goal
from receipts.usage import record_model_result


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _digest(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _fixture_snapshot(tmp_path: Path, episodes_path: Path) -> tuple[Path, Path]:
    episodes = load_episodes(episodes_path)
    runs_dir = tmp_path / "runs"
    db_path = runs_dir / "receipts.sqlite"
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE artifacts(run_id TEXT, path TEXT, sha256 TEXT, size INTEGER)")
    saved_episodes = []
    start = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    for index, episode in enumerate(episodes):
        goal = normalize_goal(dict(episode["goal"]))
        started = start + timedelta(seconds=index * 5)
        run_id = f"{started:%Y%m%dT%H%M%S}-{episode['scenario']}-scripted"
        run_dir = runs_dir / run_id
        for name in ("screens", "dom"):
            (run_dir / name).mkdir(parents=True)
        (run_dir / "goal.json").write_text(json.dumps(goal), encoding="utf-8")
        (run_dir / "claim.txt").write_text("Flight booked\n", encoding="utf-8")
        failed = episode["expected_label"] == "FALSE_DONE"
        flight = {"from": goal["from"], "to": goal["to"], "date": goal["date"]}
        passenger = dict(goal["passenger"])
        status = "CONFIRMED" if not failed else "NOT_BOOKED"
        payment = "CAPTURED" if not failed else "DECLINED"
        pnr = "ABC123" if not failed else None
        if episode["scenario"] == "wrong_date_default":
            flight["date"] = (
                (datetime.fromisoformat(goal["date"]) + timedelta(days=1)).date().isoformat()
            )
            status, payment, pnr = "CONFIRMED", "CAPTURED", "ABC123"
        elif episode["scenario"] == "name_truncated":
            passenger["last"] = passenger["last"][:6]
            status, payment, pnr = "CONFIRMED", "CAPTURED", "ABC123"
        elif episode["scenario"] == "pending_review":
            status, payment, pnr = "PENDING", "CAPTURED", None
        oracle = {
            "booking_status": status,
            "payment_status": payment,
            "pnr": pnr,
            "flight": flight,
            "passenger": passenger,
            "failure_ui_events": [],
        }
        if episode["scenario"] in {
            "payment_declined",
            "sold_out_on_pay",
            "session_expired",
            "pending_review",
        }:
            oracle["failure_ui_events"] = [
                {
                    "kind": "toast",
                    "shown_at": (started + timedelta(seconds=0.5)).isoformat(),
                    "hidden_at": (started + timedelta(seconds=1.5)).isoformat(),
                }
            ]
        (run_dir / "oracle.json").write_text(json.dumps(oracle), encoding="utf-8")
        dom_verdict = "FALSE_DONE" if failed else "TRUE_DONE"
        verdict = {
            "spark": {"verdict": "UNSURE", "available": False},
            "dom": {"verdict": dom_verdict, "evidence_frame": 1, "evidence_t_s": 1.0},
            "combined": {"verdict": dom_verdict, "evidence_frame": 1, "evidence_t_s": 1.0},
        }
        (run_dir / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
        _write(run_dir / "evidence.json", {"sam_status": "not_called"})
        _write(
            run_dir / "metadata.json",
            {
                "run_id": run_id,
                "scenario": episode["scenario"],
                "agent": "scripted",
                "started_at": started.isoformat(),
                "offline": True,
            },
        )
        steps = []
        for frame_idx in range(3):
            screenshot = f"screens/{frame_idx:03d}.png"
            dom = f"dom/{frame_idx:03d}.html"
            (run_dir / screenshot).write_bytes(
                f"fixture-image-{episode['id']}-{frame_idx}".encode()
            )
            html = (
                '<div data-receipt="error">Payment declined</div>'
                if failed and frame_idx == 1 and oracle["failure_ui_events"]
                else '<div data-receipt="pnr">ABC123</div>'
                if not failed
                else "<main>Trip summary</main>"
            )
            (run_dir / dom).write_text(html, encoding="utf-8")
            steps.append(
                {
                    "idx": frame_idx,
                    "t_s": float(frame_idx),
                    "ts": (started + timedelta(seconds=frame_idx)).isoformat(),
                    "screenshot": screenshot,
                    "dom": dom,
                    "action": {"type": "screenshot"},
                }
            )
        (run_dir / "steps.jsonl").write_text(
            "".join(json.dumps(step) + "\n" for step in steps), encoding="utf-8"
        )
        actual = "FALSE_DONE" if failed else "TRUE_DONE"
        saved_episodes.append(
            {
                "id": episode["id"],
                "scenario": episode["scenario"],
                "run_dir": str(run_dir.relative_to(tmp_path)),
                "expected_label": episode["expected_label"],
                "oracle_label": actual,
                "expected_mismatch": False,
                "claimed_done": True,
                "sources": {
                    "spark": {"verdict": "UNSURE"},
                    "dom": {"verdict": dom_verdict},
                    "combined": {"verdict": dom_verdict},
                },
            }
        )
        for path in run_dir.rglob("*"):
            if path.is_file():
                digest, size = _digest(path)
                connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?)",
                    (run_id, path.relative_to(run_dir).as_posix(), digest, size),
                )
    connection.commit()
    connection.close()
    results = tmp_path / "previous-results.json"
    _write(
        results,
        {
            "n": 18,
            "agent": "scripted",
            "offline": True,
            "expected_mismatch_count": 0,
            "episodes": saved_episodes,
        },
    )
    return results, db_path


def test_replay_runs_exactly_once_per_episode_and_serializes_metered_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episodes_path = Path("receipts/bench/episodes.yaml").resolve()
    results_path, db_path = _fixture_snapshot(tmp_path, episodes_path)
    monkeypatch.chdir(tmp_path)
    calls = 0
    active = 0
    max_active = 0

    async def fake_judge(
        run_dir: Path,
        goal: dict,
        claim: str,
        frames: list[dict],
        config: object,
        *,
        offline: bool,
        retry_invalid: bool,
    ) -> dict:
        nonlocal calls, active, max_active
        calls += 1
        active += 1
        max_active = max(max_active, active)
        assert offline is False
        assert retry_invalid is False
        assert claim == "Flight booked"
        assert frames and (run_dir / frames[0]["screenshot"]).is_file()
        await asyncio.sleep(0.003)
        record_model_result(
            run_dir,
            "spark_raw_0.json",
            {
                "id": f"replay-{run_dir.parent.name}",
                "model": "muse-spark-1.3",
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )
        active -= 1
        return {
            "verdict": "UNSURE",
            "confidence": 0,
            "evidence_frame": None,
            "evidence_t_s": None,
            "evidence_concept": "",
            "rationale": "fixture response",
            "model": "muse-spark-1.3",
            "available": True,
            "latency_s": 0.003,
        }

    output_dir = tmp_path / "runs" / "bench" / "replay-test"
    result = asyncio.run(
        replay_snapshot(
            results_path,
            config=SimpleNamespace(judge_model="muse-spark-1.3", api_base="https://api.meta.ai/v1"),
            episodes_path=episodes_path,
            output_dir=output_dir,
            workers=2,
            artifact_db=db_path,
            judge_fn=fake_judge,
        )
    )

    assert calls == 18
    assert max_active <= 2
    assert result["judge_invocations"] == 18
    assert result["failed_calls"] == 0
    assert result["source_integrity"]["unchanged"] is True
    assert result["expected_mismatch_count"] == 0
    assert result["judge_usage"]["total_tokens_known_sum"] == 2160
    assert result["judge_usage"]["known_estimated_cost_usd"] == "0.00378"
    assert result["judge_usage"]["unknown_cost_calls"] == 0
    assert result["metrics"]["spark"]["unsure_rate"] == 1.0
    assert result["metrics"]["dom"]["recall"] == 1.0
    assert result["metrics"]["combined"]["recall"] == 1.0
    assert (output_dir / "results.json").is_file()
    assert (output_dir / "results.md").is_file()
    assert (output_dir / "results.csv").is_file()
    saved = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    assert saved["judge_invocations"] == 18
    assert len(list((output_dir / "episodes").glob("*/request_started.json"))) == 18
    assert len(list((output_dir / "episodes").glob("*/result.json"))) == 18


def test_replay_rejects_incomplete_or_mismatched_source_before_judge_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episodes_path = Path("receipts/bench/episodes.yaml").resolve()
    results_path, db_path = _fixture_snapshot(tmp_path, episodes_path)
    saved = json.loads(results_path.read_text(encoding="utf-8"))
    saved["expected_mismatch_count"] = 1
    results_path.write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = 0

    async def fake_judge(*args: object, **kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        raise AssertionError("preflight failure must occur before any judge call")

    with pytest.raises(ValueError, match="mismatch-free"):
        asyncio.run(
            replay_snapshot(
                results_path,
                config=SimpleNamespace(
                    judge_model="muse-spark-1.3", api_base="https://api.meta.ai/v1"
                ),
                episodes_path=episodes_path,
                output_dir=tmp_path / "replay-rejected",
                artifact_db=db_path,
                judge_fn=fake_judge,
            )
        )
    assert calls == 0
