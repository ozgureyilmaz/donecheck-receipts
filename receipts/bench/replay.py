"""Judge an immutable saved benchmark set without rerunning its agents."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import shutil
import sqlite3
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

from receipts.auditor.keyframes import pick_keyframes
from receipts.auditor.verdict import combine
from receipts.bench.runner import (
    SOURCES,
    _evidence_hit,
    calculate_metrics,
    claims_done,
    load_episodes,
    oracle_label,
)
from receipts.common import normalize_goal, write_json
from receipts.usage import summarize_usage


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


def _inventory(run_dir: Path) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"source run contains a symlink: {path.relative_to(run_dir)}")
        if path.is_file():
            relative = path.relative_to(run_dir).as_posix()
            inventory[relative] = _hash_file(path)
    return inventory


def _safe_source_file(run_dir: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("step screenshot/DOM path must be a non-empty string")
    path = Path(relative)
    if path.is_absolute():
        raise ValueError(f"step path must be relative: {relative}")
    resolved = (run_dir / path).resolve()
    if not resolved.is_relative_to(run_dir.resolve()):
        raise ValueError(f"step path escapes its run directory: {relative}")
    if not resolved.is_file():
        raise ValueError(f"recorded file is missing: {relative}")
    return resolved


def _verify_artifact_rows(
    db_path: Path | str, run_id: str, run_dir: Path, inventory: Mapping[str, Mapping[str, Any]]
) -> None:
    database = Path(db_path).resolve()
    uri = f"file:{quote(str(database))}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT path, sha256, size FROM artifacts WHERE run_id=?", (run_id,)
        ).fetchall()
    by_path: dict[str, set[tuple[str, int]]] = {}
    for stored_path, sha256, size in rows:
        candidate = Path(stored_path)
        if candidate.is_absolute():
            try:
                key = candidate.resolve().relative_to(run_dir.resolve()).as_posix()
            except ValueError:
                continue
        else:
            key = candidate.as_posix()
        by_path.setdefault(key, set()).add((sha256, int(size)))
    for relative, info in inventory.items():
        if (info["sha256"], info["size"]) not in by_path.get(relative, set()):
            raise ValueError(f"SQLite artifact hash does not match saved file: {run_id}/{relative}")


def _preflight(
    results_path: Path,
    episodes_path: Path,
    artifact_db: Path | str | None,
) -> list[dict[str, Any]]:
    prior = _json(results_path)
    episodes = load_episodes(episodes_path)
    expected_by_id = {str(episode["id"]): episode for episode in episodes}
    saved = prior.get("episodes") if isinstance(prior, Mapping) else None
    if (
        prior.get("n") != 18
        or prior.get("offline") is not True
        or prior.get("expected_mismatch_count") != 0
        or not isinstance(saved, list)
        or len(saved) != 18
    ):
        raise ValueError(
            "replay source must be the complete, mismatch-free offline 18-episode result"
        )
    saved_ids = [str(item.get("id", "")) for item in saved if isinstance(item, Mapping)]
    if len(saved_ids) != 18 or len(set(saved_ids)) != 18 or set(saved_ids) != set(expected_by_id):
        raise ValueError("saved results do not contain the 18 unique manifest episode IDs")
    if prior.get("agent") != "scripted":
        raise ValueError("replay source must use the scripted agent")

    runs_root = Path("runs").resolve()
    run_dirs = [Path(item["run_dir"]).resolve() for item in saved]
    if len(set(run_dirs)) != 18:
        raise ValueError("each replay episode must reference a unique saved run directory")
    if any(not path.is_relative_to(runs_root) for path in run_dirs):
        raise ValueError("all source runs must be inside runs/")

    prepared: list[dict[str, Any]] = []
    for saved_item in saved:
        episode_id = str(saved_item["id"])
        episode = expected_by_id[episode_id]
        run_dir = Path(saved_item["run_dir"]).resolve()
        run_id = run_dir.name
        if not run_id.endswith(f"-{episode['scenario']}-scripted"):
            raise ValueError(f"run ID does not match scenario for {episode_id}")
        required = (
            "goal.json",
            "claim.txt",
            "steps.jsonl",
            "oracle.json",
            "verdict.json",
            "metadata.json",
            "evidence.json",
        )
        if any(not (run_dir / name).is_file() for name in required):
            raise ValueError(f"saved recording is incomplete for {episode_id}")
        goal = _json(run_dir / "goal.json")
        if goal != normalize_goal(dict(episode["goal"])):
            raise ValueError(f"saved goal differs from manifest for {episode_id}")
        claim = (run_dir / "claim.txt").read_text(encoding="utf-8").strip()
        if claim != str(episode["claim"]):
            raise ValueError(f"saved claim differs from manifest for {episode_id}")
        metadata = _json(run_dir / "metadata.json")
        if metadata.get("offline") is not True or metadata.get("agent") != "scripted":
            raise ValueError(f"run is not a saved offline scripted episode: {episode_id}")
        verdict = _json(run_dir / "verdict.json")
        if verdict.get("spark", {}).get("verdict") != "UNSURE":
            raise ValueError(f"offline source unexpectedly contains a Spark judgment: {episode_id}")
        oracle = _json(run_dir / "oracle.json")
        actual_label = oracle_label(oracle, goal)
        if actual_label != episode["expected_label"] or actual_label != saved_item["oracle_label"]:
            raise ValueError(f"independent oracle label mismatch for {episode_id}")
        steps = [
            json.loads(line)
            for line in (run_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not steps or len({_step_id(step, index) for index, step in enumerate(steps)}) != len(
            steps
        ):
            raise ValueError(f"saved steps are empty or have duplicate frame IDs: {episode_id}")
        recording_files: set[str] = set()
        for step in steps:
            for key in ("screenshot", "dom"):
                file_path = _safe_source_file(run_dir, step.get(key))
                recording_files.add(file_path.relative_to(run_dir).as_posix())
        inventory = _inventory(run_dir)
        if artifact_db is not None:
            _verify_artifact_rows(artifact_db, run_id, run_dir, inventory)
        frames = pick_keyframes(run_dir, steps)
        if not frames:
            raise ValueError(f"no recorded keyframes are available for {episode_id}")
        started_at = metadata.get("started_at")
        if not started_at:
            raise ValueError(f"episode start timestamp missing for {episode_id}")
        prepared.append(
            {
                "episode": episode,
                "run_id": run_id,
                "run_dir": run_dir,
                "goal": goal,
                "claim": claim,
                "steps": steps,
                "oracle": oracle,
                "oracle_label": actual_label,
                "verdict": verdict,
                "metadata": metadata,
                "started_at": started_at,
                "frames": frames,
                "claimed_done": claims_done(claim),
                "inventory": inventory,
                "recording_files": sorted(recording_files),
            }
        )
    return prepared


def _step_id(step: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(step.get("idx", fallback))
    except (TypeError, ValueError):
        return fallback


def _copy_inputs(item: Mapping[str, Any], episode_dir: Path) -> list[dict[str, Any]]:
    run_dir: Path = item["run_dir"]
    recording_dir = episode_dir / "recording"
    recording_dir.mkdir(parents=True)
    fixed = (
        "goal.json",
        "claim.txt",
        "steps.jsonl",
        "oracle.json",
        "verdict.json",
        "metadata.json",
        "evidence.json",
    )
    files = set(fixed) | set(item["recording_files"])
    for relative in sorted(files):
        source = _safe_source_file(run_dir, relative)
        destination = recording_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    frames: list[dict[str, Any]] = []
    for frame in item["frames"]:
        frames.append(
            {
                "idx": frame["idx"],
                "t_s": frame["t_s"],
                "screenshot": f"recording/{frame['screenshot']}",
            }
        )
    write_json(episode_dir / "selected_frames.json", frames)
    return frames


def _default_judge() -> Callable[..., Any]:
    from receipts.auditor.spark_judge import judge

    return judge


async def _judge_one(
    item: Mapping[str, Any],
    episode_dir: Path,
    frames: list[dict[str, Any]],
    config: Any,
    semaphore: asyncio.Semaphore,
    judge_fn: Callable[..., Any],
) -> dict[str, Any]:
    async with semaphore:
        episode = item["episode"]
        write_json(
            episode_dir / "request_started.json",
            {
                "episode_id": episode["id"],
                "provider": "spark",
                "model": config.judge_model,
                "retry_invalid": False,
                "invocations": 1,
            },
        )
        started = time.perf_counter()
        try:
            spark = await judge_fn(
                episode_dir,
                item["goal"],
                item["claim"],
                frames,
                config,
                offline=False,
                retry_invalid=False,
            )
            if not isinstance(spark, Mapping):
                raise TypeError("judge returned a non-object result")
            spark = dict(spark)
        except Exception as exc:
            spark = {
                "verdict": "UNSURE",
                "confidence": 0.0,
                "evidence_frame": None,
                "evidence_t_s": None,
                "evidence_concept": "",
                "rationale": f"Replay request failed ({type(exc).__name__}); no retry",
                "model": config.judge_model,
                "available": False,
                "latency_s": round(time.perf_counter() - started, 3),
                "tokens": None,
            }
            error_path = episode_dir / "spark_error.json"
            if not error_path.exists():
                write_json(
                    error_path,
                    {"type": type(exc).__name__, "model": config.judge_model, "retry": False},
                )
        elapsed = round(time.perf_counter() - started, 3)
        spark["latency_s"] = elapsed
        usage = summarize_usage(episode_dir)
        write_json(episode_dir / "usage.json", usage)
        call_rows = [call for call in usage["calls"] if call["provider"] == "spark"]
        if len(call_rows) > 1:
            raise RuntimeError(f"more than one Spark response/error artifact for {episode['id']}")
        spark_call = call_rows[0] if call_rows else None
        if spark_call is not None and isinstance(spark_call.get("total_tokens"), int):
            spark["tokens"] = spark_call["total_tokens"]
        dom = dict(item["verdict"]["dom"])
        combined = combine(spark, dom)
        for source, verdict in (("spark", spark), ("dom", dom), ("combined", combined)):
            if source == "dom":
                verdict["evidence_hit"] = None
            else:
                verdict["evidence_hit"] = (
                    _evidence_hit(verdict, item["oracle"], item["steps"], item["started_at"])
                    if verdict.get("verdict") == "FALSE_DONE"
                    and item["oracle_label"] == "FALSE_DONE"
                    else None
                )
        response_path = episode_dir / "spark_raw_0.json"
        error_path = episode_dir / "spark_error.json"
        call_status = (
            "error"
            if error_path.exists()
            else "response_received"
            if response_path.exists()
            else "attempt_unobserved"
        )
        record = {
            "episode_id": episode["id"],
            "source_run_id": item["run_id"],
            "source_run_dir": str(item["run_dir"]),
            "source_files_sha256": {
                name: value["sha256"] for name, value in item["inventory"].items()
            },
            "oracle_label": item["oracle_label"],
            "expected_label": episode["expected_label"],
            "expected_mismatch": False,
            "claim": item["claim"],
            "call_status": call_status,
            "judge_invocations": 1,
            "retry_invalid": False,
            "elapsed_s": elapsed,
            "spark": spark,
            "spark_usage": spark_call,
            "dom": dom,
            "combined": combined,
        }
        write_json(episode_dir / "result.json", record)
        return {
            "id": str(episode["id"]),
            "scenario": str(episode["scenario"]),
            "run_dir": str(item["run_dir"]),
            "expected_label": str(episode["expected_label"]),
            "oracle_label": item["oracle_label"],
            "expected_mismatch": False,
            "claimed_done": item["claimed_done"],
            "sources": {"spark": spark, "dom": dom, "combined": combined},
            "call_status": call_status,
            "judge_invocations": 1,
            "elapsed_s": elapsed,
            "usage": spark_call,
        }


def _write_replay_csv(path: Path, episodes: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "id",
        "scenario",
        "expected_label",
        "oracle_label",
        "claimed_done",
        "call_status",
        "spark_verdict",
        "spark_evidence_frame",
        "spark_evidence_hit",
        "spark_input_tokens",
        "spark_cached_input_tokens",
        "spark_output_tokens",
        "spark_total_tokens",
        "spark_cost_usd",
        "dom_verdict",
        "combined_verdict",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for episode in episodes:
            sources = episode["sources"]
            usage = episode.get("usage") or {}
            writer.writerow(
                {
                    "id": episode["id"],
                    "scenario": episode["scenario"],
                    "expected_label": episode["expected_label"],
                    "oracle_label": episode["oracle_label"],
                    "claimed_done": episode["claimed_done"],
                    "call_status": episode["call_status"],
                    "spark_verdict": sources.get("spark", {}).get("verdict"),
                    "spark_evidence_frame": sources.get("spark", {}).get("evidence_frame"),
                    "spark_evidence_hit": sources.get("spark", {}).get("evidence_hit"),
                    "spark_input_tokens": (usage or {}).get("input_tokens"),
                    "spark_cached_input_tokens": (usage or {}).get("cached_input_tokens"),
                    "spark_output_tokens": (usage or {}).get("output_tokens"),
                    "spark_total_tokens": (usage or {}).get("total_tokens"),
                    "spark_cost_usd": (usage or {}).get("estimated_cost_usd"),
                    "dom_verdict": sources.get("dom", {}).get("verdict"),
                    "combined_verdict": sources.get("combined", {}).get("verdict"),
                }
            )


def _markdown(result: Mapping[str, Any]) -> str:
    from receipts.bench.runner import _format_percent

    lines = [
        "# Spark replay results",
        "",
        f"- Run: {result['timestamp_utc']}",
        f"- Source: {result['source_results']}",
        "- Agent: scripted recordings; no actor/browser rerun",
        f"- Spark judge calls: {result['judge_invocations']} (one per episode; invalid-response retry disabled)",
        f"- Episodes: {result['n']}",
        f"- Agent false-done rate: {_format_percent(result['agent_false_done_rate']['rate'])} "
        f"({result['agent_false_done_rate']['false_done_claims']}/"
        f"{result['agent_false_done_rate']['done_claims']} done claims)",
        "",
        "| Auditor | Accuracy | Precision | Recall | False-alarm rate | UNSURE | Evidence hit | n |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {"spark": "Spark 1.3 only", "dom": "DOM checks only", "combined": "Combined"}
    for source in SOURCES:
        metric = result["metrics"][source]
        evidence = (
            "n/a"
            if metric["evidence_hit_rate"] is None
            else f"{metric['evidence_hits']}/{metric['evidence_n']} "
            f"({_format_percent(metric['evidence_hit_rate'])})"
        )
        lines.append(
            f"| {labels[source]} | {_format_percent(metric['accuracy'])} | "
            f"{_format_percent(metric['precision'])} | {_format_percent(metric['recall'])} | "
            f"{_format_percent(metric['false_alarm_rate'])} | "
            f"{_format_percent(metric['unsure_rate'])} | {evidence} | {metric['n']} |"
        )
    cost = result["judge_usage"]
    lines.extend(
        [
            "",
            f"Judge tokens: {cost['total_tokens_known_sum']} known across "
            f"{cost['total_tokens_known_calls']}/18 responses; mean "
            f"{cost['mean_total_tokens']} known-response tokens.",
            f"Estimated Spark cost: ${cost['known_estimated_cost_usd']} known subtotal; "
            f"{cost['unknown_cost_calls']} call(s) with unknown cost.",
            f"Original input integrity: {'PASS' if result['source_integrity']['unchanged'] else 'FAIL'} "
            f"({result['source_integrity']['files_checked']} files checked).",
            f"Expected/oracle label mismatches: {result['expected_mismatch_count']}.",
            "",
            "| Episode | Oracle | Spark | DOM | Combined | Evidence hit | Tokens | Cost USD | Call |",
            "|---|---|---|---|---|---|---:|---:|---|",
        ]
    )
    for episode in result["episodes"]:
        sources = episode["sources"]
        usage = episode.get("usage") or {}
        lines.append(
            f"| {episode['id']} | {episode['oracle_label']} | "
            f"{sources['spark']['verdict']} | {sources['dom']['verdict']} | "
            f"{sources['combined']['verdict']} | "
            f"{sources['spark'].get('evidence_hit') if sources['spark'].get('evidence_hit') is not None else 'n/a'} | "
            f"{usage.get('total_tokens', 'n/a')} | {usage.get('estimated_cost_usd', 'n/a')} | "
            f"{episode['call_status']} |"
        )
    return "\n".join(lines) + "\n"


def _make_output_dir(output_dir: Path | str | None) -> Path:
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=False)
        return destination
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path("runs/bench")
    candidate = root / f"live-replay-{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"live-replay-{stamp}-{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


async def replay_snapshot(
    results_path: Path | str,
    *,
    config: Any,
    episodes_path: Path | str = Path("receipts/bench/episodes.yaml"),
    output_dir: Path | str | None = None,
    workers: int = 3,
    artifact_db: Path | str | None = None,
    judge_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Replay exactly the 18 offline recordings with a single Spark judge call each."""
    if not 1 <= workers <= 3:
        raise ValueError("workers must be between 1 and 3")
    prepared = _preflight(Path(results_path), Path(episodes_path), artifact_db)
    destination = _make_output_dir(output_dir)
    episode_dirs: dict[str, Path] = {}
    episode_frames: dict[str, list[dict[str, Any]]] = {}
    for item in prepared:
        episode_dir = destination / "episodes" / str(item["episode"]["id"])
        episode_dir.mkdir(parents=True, exist_ok=False)
        episode_dirs[str(item["episode"]["id"])] = episode_dir
        episode_frames[str(item["episode"]["id"])] = _copy_inputs(item, episode_dir)
    write_json(
        destination / "source_snapshot.json",
        {item["run_id"]: item["inventory"] for item in prepared},
    )
    write_json(
        destination / "replay_manifest.json",
        {
            "source_results": str(Path(results_path)),
            "source_episode_count": len(prepared),
            "episode_ids": [str(item["episode"]["id"]) for item in prepared],
            "judge_model": config.judge_model,
            "api_base": config.api_base,
            "workers": workers,
            "retry_invalid": False,
            "sam_calls": 0,
            "agent_or_browser_calls": 0,
            "status": "in_progress",
        },
    )
    semaphore = asyncio.Semaphore(workers)
    actual_judge = judge_fn or _default_judge()

    async def run_one(item: Mapping[str, Any]) -> dict[str, Any]:
        episode_id = str(item["episode"]["id"])
        item_with_label = dict(item)
        item_with_label["oracle_label"] = item["oracle_label"]
        item_with_label["claimed_done"] = item["claimed_done"]
        return await _judge_one(
            item_with_label,
            episode_dirs[episode_id],
            episode_frames[episode_id],
            config,
            semaphore,
            actual_judge,
        )

    episode_results = await asyncio.gather(*(run_one(item) for item in prepared))
    episode_results.sort(key=lambda item: item["id"])

    integrity_diffs: list[dict[str, Any]] = []
    files_checked = 0
    for item in prepared:
        current = _inventory(item["run_dir"])
        files_checked += len(item["inventory"])
        if current != item["inventory"]:
            integrity_diffs.append(
                {
                    "run_id": item["run_id"],
                    "missing": sorted(set(item["inventory"]) - set(current)),
                    "added": sorted(set(current) - set(item["inventory"])),
                    "changed": sorted(
                        name
                        for name in set(current) & set(item["inventory"])
                        if current[name] != item["inventory"][name]
                    ),
                }
            )
    source_integrity = {
        "unchanged": not integrity_diffs,
        "files_checked": files_checked,
        "diffs": integrity_diffs,
    }

    done_claims = sum(bool(item["claimed_done"]) for item in episode_results)
    false_done_claims = sum(
        item["oracle_label"] == "FALSE_DONE" and item["claimed_done"] for item in episode_results
    )
    metrics = {source: calculate_metrics(episode_results, source) for source in SOURCES}
    spark_calls = [item.get("usage") or {} for item in episode_results]
    spark_tokens = [
        call["total_tokens"] for call in spark_calls if isinstance(call.get("total_tokens"), int)
    ]
    known_costs = [
        Decimal(call["estimated_cost_usd"])
        for call in spark_calls
        if isinstance(call.get("estimated_cost_usd"), str)
    ]
    from receipts.usage import _decimal_text

    token_sum = sum(spark_tokens)
    judge_usage = {
        "response_count": len(spark_calls),
        "total_tokens_known_sum": token_sum,
        "total_tokens_known_calls": len(spark_tokens),
        "mean_total_tokens": round(statistics.mean(spark_tokens), 3) if spark_tokens else None,
        "input_tokens_known_sum": sum(call.get("input_tokens") or 0 for call in spark_calls),
        "cached_input_tokens_known_sum": sum(
            call.get("cached_input_tokens") or 0 for call in spark_calls
        ),
        "output_tokens_known_sum": sum(call.get("output_tokens") or 0 for call in spark_calls),
        "known_estimated_cost_usd": _decimal_text(sum(known_costs, Decimal(0))),
        "unknown_cost_calls": sum(call.get("estimated_cost_usd") is None for call in spark_calls),
        "pricing_basis": "Meta Model API published per-million token rates for muse-spark-1.3",
    }
    mismatches = [
        {
            "id": item["id"],
            "expected_label": item["expected_label"],
            "oracle_label": item["oracle_label"],
        }
        for item in episode_results
        if item["expected_label"] != item["oracle_label"]
    ]
    elapsed = [float(item["elapsed_s"]) for item in episode_results]
    result = {
        "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_results": str(Path(results_path)),
        "output_dir": str(destination),
        "agent": "scripted recordings",
        "offline": False,
        "n": len(episode_results),
        "sources": list(SOURCES),
        "judge_invocations": sum(item["judge_invocations"] for item in episode_results),
        "failed_calls": sum(item["call_status"] != "response_received" for item in episode_results),
        "metrics": metrics,
        "agent_false_done_rate": {
            "false_done_claims": false_done_claims,
            "done_claims": done_claims,
            "rate": false_done_claims / done_claims if done_claims else None,
        },
        "judge_cost": {
            "latency_n": len(elapsed),
            "mean_latency_s": round(statistics.mean(elapsed), 3) if elapsed else None,
            "tokens_n": len(spark_tokens),
            "mean_tokens": round(statistics.mean(spark_tokens), 3) if spark_tokens else None,
        },
        "judge_usage": judge_usage,
        "expected_mismatch_count": len(mismatches),
        "expected_mismatches": mismatches,
        "source_integrity": source_integrity,
        "episodes": episode_results,
    }
    markdown = _markdown(result)
    result["markdown"] = markdown
    write_json(destination / "results.json", result)
    (destination / "results.md").write_text(markdown, encoding="utf-8")
    _write_replay_csv(destination / "results.csv", episode_results)
    write_json(
        destination / "replay_manifest.json",
        {
            "source_results": str(Path(results_path)),
            "source_episode_count": len(prepared),
            "episode_ids": [str(item["episode"]["id"]) for item in prepared],
            "judge_model": config.judge_model,
            "api_base": config.api_base,
            "workers": workers,
            "retry_invalid": False,
            "sam_calls": 0,
            "agent_or_browser_calls": 0,
            "status": "complete" if result["failed_calls"] == 0 else "partial",
            "failed_calls": result["failed_calls"],
            "source_integrity_unchanged": source_integrity["unchanged"],
        },
    )
    return result
