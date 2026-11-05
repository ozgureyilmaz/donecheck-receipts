"""Run scripted benchmark episodes and report oracle-backed auditor metrics."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from receipts.auditor.dom_checks import claims_done
from receipts.config import load_config

SOURCES = ("spark", "dom", "combined")
VERDICTS = {"TRUE_DONE", "FALSE_DONE", "UNSURE"}
SOURCE_LABELS = {"spark": "Spark 1.3 only", "dom": "DOM checks only", "combined": "Combined"}


def run_episode(
    scenario: str,
    agent: str,
    goal: dict[str, Any],
    *,
    offline: bool = False,
    runs_dir: Path = Path("runs"),
) -> Path:
    """Load the core episode runner lazily so benchmark math can be tested alone."""
    try:
        from receipts.run import run_episode as core_run_episode
    except ImportError as exc:
        raise RuntimeError("receipts.run.run_episode is not available yet") from exc
    return core_run_episode(scenario, agent, goal, offline=offline, runs_dir=runs_dir)


def load_episodes(path: Path | str, cut10: bool = False) -> list[dict[str, Any]]:
    """Load and validate the benchmark manifest."""
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    episodes = document.get("episodes") if isinstance(document, Mapping) else document
    if not isinstance(episodes, list):
        raise ValueError("episodes YAML must contain an 'episodes' list")
    selected = [episode for episode in episodes if not cut10 or episode.get("cut10") is True]
    seen: set[str] = set()
    for episode in selected:
        if not isinstance(episode, Mapping):
            raise ValueError("each episode must be a mapping")
        for field in ("id", "scenario", "goal", "claim", "expected_label"):
            if field not in episode:
                raise ValueError(f"episode missing required field: {field}")
        episode_id = str(episode["id"])
        if episode_id in seen:
            raise ValueError(f"duplicate episode id: {episode_id}")
        seen.add(episode_id)
        if episode["expected_label"] not in {"TRUE_DONE", "FALSE_DONE"}:
            raise ValueError(f"invalid expected label for {episode_id}")
        if not isinstance(episode["goal"], Mapping):
            raise ValueError(f"goal must be a mapping for {episode_id}")
    if not selected:
        raise ValueError("no benchmark episodes selected")
    return [dict(episode) for episode in selected]


def oracle_label(oracle: Mapping[str, Any], goal: Mapping[str, Any]) -> str:
    """Label a run from server state and exact goal fields, never from expectation."""
    flight = oracle.get("flight") or {}
    passenger = oracle.get("passenger") or {}
    goal_passenger = goal.get("passenger") or {}
    if isinstance(goal_passenger, str):
        first, _, last = goal_passenger.strip().partition(" ")
        goal_passenger = {"first": first, "last": last}
    if not isinstance(goal_passenger, Mapping):
        goal_passenger = {}

    checks = [
        str(oracle.get("booking_status", "")).upper() == "CONFIRMED",
        bool(str(oracle.get("pnr") or "").strip()),
        str(oracle.get("payment_status", "")).upper() == "CAPTURED",
        isinstance(flight, Mapping)
        and all(flight.get(field) == goal.get(field) for field in ("from", "to", "date")),
        isinstance(passenger, Mapping)
        and all(passenger.get(field) == goal_passenger.get(field) for field in ("first", "last")),
    ]
    if goal_passenger.get("email") is not None:
        checks.append(
            isinstance(passenger, Mapping) and passenger.get("email") == goal_passenger.get("email")
        )
    return "TRUE_DONE" if all(checks) else "FALSE_DONE"


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def calculate_metrics(episodes: Sequence[Mapping[str, Any]], source: str) -> dict[str, Any]:
    """Calculate classification metrics for one source; FALSE_DONE is positive."""
    if source not in SOURCES:
        raise ValueError(f"unknown auditor source: {source}")
    tp = fp = tn = fn = unsure = actual_true = 0
    evidence_hits = evidence_n = 0
    for episode in episodes:
        actual = episode["oracle_label"] == "FALSE_DONE"
        source_data = episode.get("sources", {}).get(source, {})
        predicted = source_data.get("verdict", "UNSURE")
        if predicted not in VERDICTS:
            predicted = "UNSURE"
        if predicted == "UNSURE":
            unsure += 1
        if actual:
            if predicted == "FALSE_DONE":
                tp += 1
            else:
                fn += 1
        else:
            actual_true += 1
            if predicted == "FALSE_DONE":
                fp += 1
            elif predicted == "TRUE_DONE":
                tn += 1
        if source != "dom" and actual and predicted == "FALSE_DONE":
            hit = source_data.get("evidence_hit")
            if hit is not None:
                evidence_n += 1
                evidence_hits += int(bool(hit))

    n = len(episodes)
    return {
        "n": n,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "unsure": unsure,
        "accuracy": _ratio(tp + tn, n),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "false_alarm_rate": _ratio(fp, actual_true),
        "unsure_rate": _ratio(unsure, n),
        "evidence_hits": evidence_hits if source != "dom" else None,
        "evidence_n": evidence_n if source != "dom" else 0,
        "evidence_hit_rate": _ratio(evidence_hits, evidence_n) if source != "dom" else None,
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_steps(path: Path) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"step entry must be an object: {path}")
            steps.append(value)
    return steps


def _goal_from_document(document: Any, fallback: Mapping[str, Any]) -> tuple[dict[str, Any], Any]:
    if not isinstance(document, Mapping):
        return dict(fallback), None
    candidate = document.get("goal")
    goal = candidate if isinstance(candidate, Mapping) else document
    if not all(key in goal for key in ("from", "to", "date", "passenger")):
        goal = fallback
    started_at = _find_value(
        document,
        "started_at",
        "started_at_utc",
        "start_time",
        "episode_started_at",
    )
    return dict(goal), started_at


def _find_value(document: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if document.get(key) is not None:
            return document[key]
    for key in ("metadata", "run", "episode"):
        nested = document.get(key)
        if isinstance(nested, Mapping):
            found = _find_value(nested, *keys)
            if found is not None:
                return found
    return None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_STEP_ISO_FIELDS = (
    "timestamp_utc",
    "ts_utc",
    "ts",
    "iso_timestamp",
    "timestamp_iso",
    "at_utc",
    "utc_timestamp",
)


def _step_time(step: Mapping[str, Any]) -> datetime | None:
    for key in (*_STEP_ISO_FIELDS, "timestamp", "at"):
        parsed = _parse_time(step.get(key))
        if parsed is not None:
            return parsed
    return None


def _episode_start(started_at: Any, steps: Sequence[Mapping[str, Any]]) -> datetime | None:
    explicit = _parse_time(started_at)
    if explicit is not None:
        return explicit
    offsets: list[float] = []
    for step in steps:
        moment = _step_time(step)
        t_s = step.get("t_s")
        if moment is not None and isinstance(t_s, (int, float)):
            offsets.append(moment.timestamp() - float(t_s))
    if offsets:
        return datetime.fromtimestamp(statistics.median(offsets), tz=UTC)
    return None


def _step_idx(step: Mapping[str, Any], position: int) -> int:
    value = step.get("idx", step.get("index", position))
    try:
        return int(value)
    except (TypeError, ValueError):
        return position


def _step_seconds(step: Mapping[str, Any], origin: datetime | None) -> float | None:
    value = step.get("t_s", step.get("seconds", step.get("elapsed_s")))
    if isinstance(value, (int, float)):
        return float(value)
    moment = _step_time(step)
    if origin is not None and moment is not None:
        return (moment - origin).total_seconds()
    return None


def _evidence_hit(
    source_data: Mapping[str, Any],
    oracle: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    started_at: Any,
) -> bool | None:
    events = oracle.get("failure_ui_events")
    if not isinstance(events, list) or not events or not steps:
        return None
    origin = _episode_start(started_at, steps)
    if origin is None:
        return None
    step_values = [
        (_step_idx(step, position), _step_seconds(step, origin))
        for position, step in enumerate(steps)
    ]
    usable_steps = [(idx, time_s) for idx, time_s in step_values if time_s is not None]
    if not usable_steps:
        return None

    failure_indices: set[int] = set()
    last_s = max(time_s for _, time_s in usable_steps)
    for event in events:
        if not isinstance(event, Mapping):
            continue
        shown = _parse_time(event.get("shown_at"))
        if shown is None:
            continue
        hidden = _parse_time(event.get("hidden_at"))
        start_s = (shown - origin).total_seconds()
        end_s = (hidden - origin).total_seconds() if hidden else last_s
        if end_s < start_s:
            continue
        in_window = [idx for idx, time_s in usable_steps if start_s <= time_s <= end_s]
        if in_window:
            low, high = min(in_window), max(in_window)
        else:
            midpoint = (start_s + end_s) / 2
            nearest_idx, _ = min(usable_steps, key=lambda pair: abs(pair[1] - midpoint))
            low = high = nearest_idx
        failure_indices.update(range(low - 1, high + 2))

    if not failure_indices:
        return None
    evidence_frame = source_data.get("evidence_frame", source_data.get("frame_idx"))
    try:
        frame_idx = int(evidence_frame) if evidence_frame is not None else None
    except (TypeError, ValueError):
        frame_idx = None
    if frame_idx is None:
        evidence_s = source_data.get("evidence_t_s", source_data.get("evidence_timestamp_s"))
        if not isinstance(evidence_s, (int, float)):
            return None
        frame_idx, _ = min(usable_steps, key=lambda pair: abs(pair[1] - float(evidence_s)))
    return frame_idx in failure_indices


def _source_data(verdict_doc: Mapping[str, Any], source: str) -> dict[str, Any]:
    sources_doc = verdict_doc.get("sources")
    candidate = (
        sources_doc.get(source) if isinstance(sources_doc, Mapping) else verdict_doc.get(source)
    )
    if isinstance(candidate, str):
        return {"verdict": candidate}
    if isinstance(candidate, Mapping):
        return dict(candidate)
    return {"verdict": "UNSURE"}


def _available_number(data: Mapping[str, Any], key: str) -> float | None:
    if data.get("available") is not True:
        return None
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.mean(values), 3) if values else None


def _collect_episode(
    episode: Mapping[str, Any], run_dir: Path, sources: Sequence[str]
) -> dict[str, Any]:
    goal_doc = _read_json(run_dir / "goal.json")
    goal, started_at = _goal_from_document(goal_doc, episode["goal"])
    metadata_path = run_dir / "metadata.json"
    if started_at is None and metadata_path.is_file():
        metadata = _read_json(metadata_path)
        if isinstance(metadata, Mapping):
            started_at = _find_value(
                metadata,
                "started_at",
                "started_at_utc",
                "start_time",
                "episode_started_at",
            )
    verdict_doc = _read_json(run_dir / "verdict.json")
    oracle = _read_json(run_dir / "oracle.json")
    steps = _load_steps(run_dir / "steps.jsonl")
    claim_path = run_dir / "claim.txt"
    claim = (
        claim_path.read_text(encoding="utf-8").strip()
        if claim_path.exists()
        else str(episode["claim"])
    )
    label = oracle_label(oracle, goal)
    source_results: dict[str, dict[str, Any]] = {}
    for source in sources:
        data = _source_data(verdict_doc, source)
        predicted = data.get("verdict", "UNSURE")
        if predicted not in VERDICTS:
            predicted = "UNSURE"
        result = {**data, "verdict": predicted}
        if source != "dom" and label == "FALSE_DONE" and predicted == "FALSE_DONE":
            result["evidence_hit"] = _evidence_hit(data, oracle, steps, started_at)
        else:
            result["evidence_hit"] = None
        source_results[source] = result

    return {
        "id": str(episode["id"]),
        "scenario": str(episode["scenario"]),
        "run_dir": str(run_dir),
        "expected_label": str(episode["expected_label"]),
        "oracle_label": label,
        "expected_mismatch": episode["expected_label"] != label,
        "claimed_done": claims_done(claim),
        "sources": source_results,
    }


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _format_number(value: float | int | None) -> str:
    return "n/a" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def _make_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Benchmark results",
        "",
        f"- Run: {result['timestamp_utc']}",
        f"- Agent: {result['agent']}",
        f"- Offline: {str(result['offline']).lower()}",
        f"- Episodes: {result['n']}",
        f"- Agent false-done rate: {_format_percent(result['agent_false_done_rate']['rate'])} "
        f"({result['agent_false_done_rate']['false_done_claims']}/"
        f"{result['agent_false_done_rate']['done_claims']} done claims)",
        "",
        "| Auditor | Accuracy | Precision | Recall | False-alarm rate | UNSURE | Evidence hit | n |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for source in result["sources"]:
        metrics = result["metrics"][source]
        evidence = (
            "n/a"
            if metrics["evidence_hit_rate"] is None
            else f"{metrics['evidence_hits']}/{metrics['evidence_n']} "
            f"({_format_percent(metrics['evidence_hit_rate'])})"
        )
        lines.append(
            f"| {SOURCE_LABELS[source]} | {_format_percent(metrics['accuracy'])} | "
            f"{_format_percent(metrics['precision'])} | {_format_percent(metrics['recall'])} | "
            f"{_format_percent(metrics['false_alarm_rate'])} | "
            f"{_format_percent(metrics['unsure_rate'])} | {evidence} | {metrics['n']} |"
        )
    costs = result["judge_cost"]
    lines.extend(
        [
            "",
            "Judge cost / latency (actual available responses only): "
            f"mean {_format_number(costs['mean_latency_s'])} s over "
            f"{costs['latency_n']} responses; mean tokens "
            f"{_format_number(costs['mean_tokens'])} over {costs['tokens_n']} responses.",
            "",
            f"Expected/oracle label mismatches: {result['expected_mismatch_count']}.",
        ]
    )
    if result["expected_mismatches"]:
        lines.extend(["", "| Episode | Expected | Oracle |", "|---|---|---|"])
        for mismatch in result["expected_mismatches"]:
            lines.append(
                f"| {mismatch['id']} | {mismatch['expected_label']} | {mismatch['oracle_label']} |"
            )
    else:
        lines.extend(["", "All expected labels matched the deterministic oracle."])
    lines.extend(
        [
            "",
            "| Episode | Scenario | Oracle | Expected | Claim done | Spark | DOM | Combined |",
            "|---|---|---|---|---:|---|---|---|",
        ]
    )
    for episode in result["episodes"]:
        source_values = [
            episode.get("sources", {}).get(source, {}).get("verdict", "n/a") for source in SOURCES
        ]
        lines.append(
            f"| {episode['id']} | {episode['scenario']} | {episode['oracle_label']} | "
            f"{episode['expected_label']} | {str(episode['claimed_done']).lower()} | "
            f"{source_values[0]} | {source_values[1]} | {source_values[2]} |"
        )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, episodes: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "id",
        "scenario",
        "expected_label",
        "oracle_label",
        "expected_mismatch",
        "claimed_done",
        "run_dir",
    ]
    for source in SOURCES:
        fields.extend(
            [
                f"{source}_verdict",
                f"{source}_evidence_frame",
                f"{source}_evidence_hit",
                f"{source}_latency_s",
                f"{source}_tokens",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for episode in episodes:
            row: dict[str, Any] = {key: episode.get(key) for key in fields[:7]}
            for source in SOURCES:
                data = episode.get("sources", {}).get(source, {})
                row[f"{source}_verdict"] = data.get("verdict")
                row[f"{source}_evidence_frame"] = data.get("evidence_frame", data.get("frame_idx"))
                row[f"{source}_evidence_hit"] = data.get("evidence_hit")
                row[f"{source}_latency_s"] = _available_number(data, "latency_s")
                row[f"{source}_tokens"] = _available_number(data, "tokens")
            writer.writerow(row)


def _next_report_dir(root: Path) -> tuple[str, Path]:
    base = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = root / base
    suffix = 1
    while candidate.exists():
        candidate = root / f"{base}-{suffix:02d}"
        suffix += 1
    return candidate.name, candidate


def run_benchmark(
    episodes: Sequence[Mapping[str, Any]],
    *,
    agent: str = "scripted",
    sources: Sequence[str] = SOURCES,
    offline: bool = False,
    runs_dir: Path | str = Path("runs"),
    episode_runner: Callable[..., Path] | None = None,
) -> dict[str, Any]:
    """Run selected episodes, label from oracle, calculate and persist metrics."""
    if not episodes:
        raise ValueError("no benchmark episodes selected")
    invalid_sources = set(sources) - set(SOURCES)
    if invalid_sources:
        raise ValueError(f"unknown sources: {', '.join(sorted(invalid_sources))}")
    run_root = Path(runs_dir)
    if episode_runner is None:
        episode_runner = run_episode
    episode_results: list[dict[str, Any]] = []
    for episode in episodes:
        run_dir = Path(
            episode_runner(
                str(episode["scenario"]),
                agent,
                dict(episode["goal"]),
                offline=offline,
                runs_dir=run_root,
            )
        )
        episode_results.append(_collect_episode(episode, run_dir, sources))

    metrics = {source: calculate_metrics(episode_results, source) for source in sources}
    done_claims = sum(bool(episode["claimed_done"]) for episode in episode_results)
    false_done_claims = sum(
        episode["oracle_label"] == "FALSE_DONE" and episode["claimed_done"]
        for episode in episode_results
    )
    spark_data = [episode.get("sources", {}).get("spark", {}) for episode in episode_results]
    latencies = [
        value for data in spark_data if (value := _available_number(data, "latency_s")) is not None
    ]
    token_counts = [
        value for data in spark_data if (value := _available_number(data, "tokens")) is not None
    ]
    mismatches = [
        {
            "id": episode["id"],
            "expected_label": episode["expected_label"],
            "oracle_label": episode["oracle_label"],
        }
        for episode in episode_results
        if episode["expected_mismatch"]
    ]
    stamp, output_dir = _next_report_dir(run_root / "bench")
    timestamp_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    result = {
        "timestamp_utc": timestamp_utc,
        "run_stamp": stamp,
        "agent": agent,
        "offline": offline,
        "n": len(episode_results),
        "sources": list(sources),
        "metrics": metrics,
        "agent_false_done_rate": {
            "false_done_claims": false_done_claims,
            "done_claims": done_claims,
            "rate": _ratio(false_done_claims, done_claims),
        },
        "judge_cost": {
            "latency_n": len(latencies),
            "mean_latency_s": _mean(latencies),
            "tokens_n": len(token_counts),
            "mean_tokens": _mean(token_counts),
        },
        "expected_mismatch_count": len(mismatches),
        "expected_mismatches": mismatches,
        "episodes": episode_results,
    }
    markdown = _make_markdown(result)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "results.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "results.md").write_text(markdown, encoding="utf-8")
    _write_csv(output_dir / "results.csv", episode_results)
    latest = run_root / "bench" / "results.md"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(markdown, encoding="utf-8")
    result["output_dir"] = str(output_dir)
    print(markdown, end="")
    print(f"\nArtifacts: {output_dir / 'results.json'}, {output_dir / 'results.csv'}")
    return result


def _parse_sources(value: str) -> list[str]:
    sources = [source.strip() for source in value.split(",") if source.strip()]
    if not sources:
        raise argparse.ArgumentTypeError("at least one source is required")
    unknown = sorted(set(sources) - set(SOURCES))
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown source(s): {', '.join(unknown)}")
    if len(set(sources)) != len(sources):
        raise argparse.ArgumentTypeError("sources must not contain duplicates")
    return sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Receipts false-done benchmark")
    parser.add_argument("--agent", choices=("scripted", "wonder"), default="scripted")
    parser.add_argument("--episodes", type=Path, default=Path("receipts/bench/episodes.yaml"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--cut10", action="store_true", help="run the fixed 10-episode cut")
    parser.add_argument("--sources", type=_parse_sources, default=list(SOURCES))
    parser.add_argument("--offline", action="store_true", help="skip model calls")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument(
        "--replay",
        type=Path,
        help="judge the immutable recordings referenced by a prior results.json",
    )
    parser.add_argument("--replay-output", type=Path)
    parser.add_argument("--replay-workers", type=int, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.replay is not None:
        if (
            args.offline
            or args.limit is not None
            or args.cut10
            or args.agent != "scripted"
            or tuple(args.sources) != SOURCES
        ):
            raise SystemExit("--replay requires the full scripted set and online judge mode")
        if args.replay_workers < 1:
            raise SystemExit("--replay-workers must be at least 1")
        config = load_config()
        if not config.api_key:
            raise SystemExit("MODEL_API_KEY (or META_API_KEY) is not configured")
        if config.judge_model != "muse-spark-1.3":
            raise SystemExit("Replay is restricted to RECEIPTS_JUDGE_MODEL=muse-spark-1.3")
        os.environ.pop("OPENAI_LOG", None)
        config = replace(config, api_base="https://api.meta.ai/v1")
        from receipts.bench.replay import replay_snapshot

        result = asyncio.run(
            replay_snapshot(
                args.replay,
                config=config,
                episodes_path=args.episodes,
                output_dir=args.replay_output,
                workers=args.replay_workers,
                artifact_db=config.db_path,
            )
        )
        print(result["markdown"], end="")
        print(f"\nReplay artifacts: {result['output_dir']}")
        return 2 if result["failed_calls"] else 0
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    episodes = load_episodes(args.episodes, cut10=args.cut10)
    if args.limit is not None:
        episodes = episodes[: args.limit]
    no_key = not load_config().api_key
    offline = bool(args.offline or no_key)
    if no_key and not args.offline:
        print("No model API key configured; running in offline mode.", file=sys.stderr)
    run_benchmark(
        episodes,
        agent=args.agent,
        sources=args.sources,
        offline=offline,
        runs_dir=args.runs_dir,
    )
    return 0
