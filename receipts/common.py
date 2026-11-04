"""Artifact serialization and task normalization."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    from receipts.audit.db import redact

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(value), indent=2, ensure_ascii=False) + "\n")


def image_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


DEFAULT_GOAL = {
    "from": "IST",
    "to": "LHR",
    "date": "2026-11-20",
    "passenger": {"first": "Ada", "last": "Lovelace", "email": "ada@example.test"},
}


def normalize_goal(goal: dict | None = None) -> dict:
    data = {**DEFAULT_GOAL, **(goal or {})}
    passenger = data["passenger"]
    if isinstance(passenger, str):
        first, _, last = passenger.partition(" ")
        passenger = {"first": first, "last": last, "email": "traveler@example.test"}
    data["passenger"] = {**DEFAULT_GOAL["passenger"], **passenger}
    return data
