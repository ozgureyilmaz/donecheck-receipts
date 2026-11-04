"""Bounded sampling that preserves transient client-visible errors."""

import json
from pathlib import Path

from receipts.auditor.dom_checks import receipt_values


def read_steps(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "steps.jsonl").read_text().splitlines()
        if line.strip()
    ]


def pick_keyframes(run_dir: Path, steps: list[dict] | None = None, limit: int = 20) -> list[dict]:
    if limit < 2:
        raise ValueError("At least two keyframes required")
    steps = read_steps(run_dir) if steps is None else steps
    if len(steps) <= limit:
        return steps
    important = [steps[-1], steps[0]]
    for step in steps:
        values = receipt_values((run_dir / step["dom"]).read_text())
        if any(key in values for key in ("error", "pending", "pnr")):
            important.append(step)
    chosen = {step["idx"]: step for step in important[:2]}
    for step in important[2:]:
        if len(chosen) >= limit:
            break
        chosen[step["idx"]] = step
    for i in range(limit):
        if len(chosen) >= limit:
            break
        step = steps[round(i * (len(steps) - 1) / (limit - 1))]
        chosen.setdefault(step["idx"], step)
    return sorted(chosen.values(), key=lambda step: step["idx"])
