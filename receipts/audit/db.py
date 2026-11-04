"""SQLite-backed audit trail with intent events and artifact hashes."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|(?:model|meta)?[_-]?api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|secret|password|credential)",
    re.IGNORECASE,
)
_AUTHORIZATION_VALUE = re.compile(r"(?i)(\bauthorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;\"'<>]+)")
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;\"'<>]+")
_SK_VALUE = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]+")


def _configured_secrets() -> tuple[str, ...]:
    """Read configured API keys at call time, after dotenv loading if present."""
    values = {os.environ.get(name, "") for name in ("MODEL_API_KEY", "META_API_KEY")}
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _redact_string(value: str) -> str:
    redacted = _AUTHORIZATION_VALUE.sub(lambda match: match.group(1) + _REDACTED, value)
    redacted = _BEARER_VALUE.sub("Bearer " + _REDACTED, redacted)
    redacted = _SK_VALUE.sub(_REDACTED, redacted)
    for secret in _configured_secrets():
        redacted = redacted.replace(secret, _REDACTED)
    return redacted


def redact(value: Any) -> Any:
    """Recursively redact API credentials and bearer-like values.

    Mapping values under credential-shaped keys are replaced wholesale. Strings
    are scrubbed for Authorization headers, bearer tokens, ``sk-`` values, and
    the configured ``MODEL_API_KEY`` / ``META_API_KEY`` values.
    """
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Path):
        return _redact_string(value.as_posix())
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = redact(key) if isinstance(key, str) else key
            if isinstance(key, str) and _SENSITIVE_KEY.search(key):
                result[safe_key] = _REDACTED
            else:
                result[safe_key] = redact(item)
        return result
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, set):
        return [redact(item) for item in sorted(value, key=repr)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return redact(dataclasses.asdict(value))
    return value


def sha256_file(path: Path | str) -> str:
    """Return the SHA-256 hex digest of a file's actual bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return _redact_string(value.as_posix())
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if hasattr(value, "value"):
        return redact(value.value)
    if isinstance(value, bytes):
        return "[BINARY]"
    return str(value)


def _json(value: Any) -> str:
    return json.dumps(
        redact(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    raise TypeError(f"expected a mapping-like value, got {type(value).__name__}")


def _first(mapping: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class AuditDB:
    """Append-only SQLite event trail plus materialized audit tables."""

    def __init__(self, path: Path | str):
        self.path = Path(path) if str(path) != ":memory:" else None
        db_path = str(path)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(self.path)
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                scenario TEXT,
                agent TEXT,
                goal TEXT,
                started_at TEXT,
                ended_at TEXT,
                claim_text TEXT,
                oracle_state_json TEXT
            );

            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                idx INTEGER NOT NULL,
                t_s REAL,
                action_json TEXT,
                screenshot_path TEXT,
                screenshot_sha256 TEXT,
                dom_path TEXT,
                dom_sha256 TEXT,
                PRIMARY KEY (run_id, idx)
            );

            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                name TEXT NOT NULL,
                passed INTEGER NOT NULL,
                detail TEXT
            );

            CREATE TABLE IF NOT EXISTS verdicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                source TEXT NOT NULL,
                verdict TEXT,
                confidence REAL,
                evidence_frame INTEGER,
                evidence_t_s REAL,
                rationale TEXT,
                model TEXT,
                raw_response_path TEXT
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                frame_idx INTEGER,
                concept TEXT,
                box_json TEXT,
                mask_path TEXT,
                model TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id)
                    DEFERRABLE INITIALLY DEFERRED,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size INTEGER NOT NULL,
                UNIQUE (run_id, path, sha256)
            );

            CREATE TRIGGER IF NOT EXISTS events_no_update
            BEFORE UPDATE ON events BEGIN
                SELECT RAISE(ABORT, 'events are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete
            BEFORE DELETE ON events BEGIN
                SELECT RAISE(ABORT, 'events are append-only');
            END;
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            try:
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def _insert_event(self, run_id: str, kind: str, payload: Any) -> int:
        cursor = self._conn.execute(
            "INSERT INTO events(run_id, ts, kind, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, _utc_now(), _redact_string(kind), _json(payload)),
        )
        return int(cursor.lastrowid)

    def event(self, run_id: str, kind: str, payload: Any) -> int:
        """Append a redacted event and return its monotonically increasing seq."""
        with self._transaction():
            return self._insert_event(run_id, kind, payload)

    @staticmethod
    def _artifact_path(path: Path | str, run_dir: Path | str | None) -> tuple[Path, str]:
        supplied = Path(path)
        root = Path(run_dir).resolve() if run_dir is not None else None
        if supplied.is_absolute():
            candidate = supplied
        elif root is not None:
            candidate = root / supplied
            if not candidate.exists() and supplied.exists():
                candidate = supplied
        else:
            candidate = supplied
        resolved = candidate.resolve()
        if root is not None:
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                relative = resolved.as_posix()
        else:
            relative = supplied.as_posix()
        return resolved, _redact_string(relative)

    @classmethod
    def _artifact_info(cls, path: Path | str, run_dir: Path | str | None) -> tuple[str, str, int]:
        resolved, relative = cls._artifact_path(path, run_dir)
        if not resolved.is_file():
            raise FileNotFoundError(f"artifact does not exist or is not a file: {relative}")
        digest = sha256_file(resolved)
        size = resolved.stat().st_size
        return relative, digest, size

    def _insert_artifact(self, run_id: str, info: tuple[str, str, int]) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO artifacts(run_id, path, sha256, size) VALUES (?, ?, ?, ?)",
            (run_id, *info),
        )

    def start_run(
        self,
        run_id: str,
        scenario: str,
        agent: str,
        goal: Any,
        started_at: str | float | int,
    ) -> None:
        """Create a run; the root row and start event commit atomically."""
        with self._transaction():
            # The deferred foreign key lets the intent event be written first;
            # the complete root row follows in this same transaction.
            self._insert_event(
                run_id,
                "run.started",
                {
                    "scenario": scenario,
                    "agent": agent,
                    "goal": goal,
                    "started_at": started_at,
                },
            )
            self._conn.execute(
                """INSERT INTO runs
                   (run_id, scenario, agent, goal, started_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    run_id,
                    _redact_string(scenario),
                    _redact_string(agent),
                    _json(goal) if not isinstance(goal, str) else _redact_string(goal),
                    _redact_string(str(started_at)),
                ),
            )

    def end_run(
        self,
        run_id: str,
        claim_text: str,
        oracle_state: Any,
        ended_at: str | float | int,
    ) -> None:
        with self._transaction():
            self._insert_event(
                run_id,
                "run.ended",
                {
                    "claim_text": claim_text,
                    "oracle_state": oracle_state,
                    "ended_at": ended_at,
                },
            )
            self._conn.execute(
                "UPDATE runs SET claim_text=?, oracle_state_json=?, ended_at=? WHERE run_id=?",
                (
                    _redact_string(claim_text),
                    _json(oracle_state),
                    _redact_string(str(ended_at)),
                    run_id,
                ),
            )

    def add_step(self, run_id: str, step: Any, run_dir: Path | str) -> None:
        data = _as_mapping(step)
        idx_value = _first(data, "idx", "index", "step_idx", "step", default=None)
        t_s = _first(data, "t_s", "seconds", "timestamp_s", "elapsed_s")
        action = _first(data, "action", "action_json", "actions")
        screenshot = _first(data, "screenshot_path", "screenshot", "screenshot_file")
        dom = _first(data, "dom_path", "dom", "dom_snapshot_path", "dom_file")
        screenshot_info = self._artifact_info(screenshot, run_dir) if screenshot else None
        dom_info = self._artifact_info(dom, run_dir) if dom else None
        screenshot_path = screenshot_info[0] if screenshot_info else None
        dom_path = dom_info[0] if dom_info else None
        with self._transaction():
            if idx_value is None:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(idx), -1) + 1 AS next_idx FROM steps WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                idx = int(row["next_idx"])
            else:
                idx = int(idx_value)
            self._insert_event(
                run_id,
                "step.added",
                {
                    "idx": idx,
                    "t_s": t_s,
                    "action": action,
                    "screenshot": screenshot_path,
                    "dom": dom_path,
                    "screenshot_sha256": screenshot_info[1] if screenshot_info else None,
                    "dom_sha256": dom_info[1] if dom_info else None,
                },
            )
            if screenshot_info:
                self._insert_artifact(run_id, screenshot_info)
            if dom_info:
                self._insert_artifact(run_id, dom_info)
            self._conn.execute(
                """INSERT INTO steps
                   (run_id, idx, t_s, action_json, screenshot_path,
                    screenshot_sha256, dom_path, dom_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    idx,
                    t_s,
                    _json(action) if action is not None else None,
                    screenshot_path,
                    screenshot_info[1] if screenshot_info else None,
                    dom_path,
                    dom_info[1] if dom_info else None,
                ),
            )

    def add_check(self, run_id: str, name: str, passed: bool, detail: Any) -> None:
        with self._transaction():
            self._insert_event(
                run_id,
                "check.added",
                {"name": name, "passed": passed, "detail": detail},
            )
            self._conn.execute(
                "INSERT INTO checks(run_id, name, passed, detail) VALUES (?, ?, ?, ?)",
                (
                    run_id,
                    _redact_string(name),
                    int(bool(passed)),
                    _json(detail) if detail is not None else None,
                ),
            )

    def add_verdict(self, run_id: str, source: str, verdict_dict: Any) -> None:
        data = _as_mapping(verdict_dict)
        response_path = _first(data, "raw_response_path", "response_path")
        run_dir = _first(data, "run_dir", "artifact_dir")
        artifact_info = self._artifact_info(response_path, run_dir) if response_path else None
        response_db_path = artifact_info[0] if artifact_info else None
        evidence_t_s = _first(data, "evidence_t_s", "evidence_timestamp_s", "evidence_timestamp")
        evidence_frame = _first(data, "evidence_frame", "frame_idx")
        with self._transaction():
            self._insert_event(
                run_id,
                "verdict.added",
                {"source": source, "verdict": data},
            )
            if artifact_info:
                self._insert_artifact(run_id, artifact_info)
            self._conn.execute(
                """INSERT INTO verdicts
                   (run_id, source, verdict, confidence, evidence_frame,
                    evidence_t_s, rationale, model, raw_response_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    _redact_string(source),
                    _redact_string(str(_first(data, "verdict", "label", default=""))) or None,
                    _first(data, "confidence"),
                    evidence_frame,
                    evidence_t_s,
                    _redact_string(str(data["rationale"]))
                    if data.get("rationale") is not None
                    else None,
                    _redact_string(str(data["model"])) if data.get("model") is not None else None,
                    response_db_path,
                ),
            )

    def add_evidence(self, run_id: str, evidence_dict: Any) -> None:
        data = _as_mapping(evidence_dict)
        mask_path = _first(data, "mask_path", "mask_file", "mask_svg")
        run_dir = _first(data, "run_dir", "artifact_dir")
        artifact_info = self._artifact_info(mask_path, run_dir) if mask_path else None
        stored_mask_path = artifact_info[0] if artifact_info else None
        box = _first(data, "box", "box_json", "boxes")
        with self._transaction():
            self._insert_event(run_id, "evidence.added", {"evidence": data})
            if artifact_info:
                self._insert_artifact(run_id, artifact_info)
            self._conn.execute(
                """INSERT INTO evidence
                   (run_id, frame_idx, concept, box_json, mask_path, model)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    _first(data, "frame_idx", "evidence_frame", "frame"),
                    _redact_string(str(data["concept"]))
                    if data.get("concept") is not None
                    else None,
                    _json(box) if box is not None else None,
                    stored_mask_path,
                    _redact_string(str(data.get("model") or data.get("source")))
                    if data.get("model") or data.get("source")
                    else None,
                ),
            )

    def register_artifact(
        self,
        run_id: str,
        path: Path | str,
        run_dir: Path | str | None = None,
    ) -> str:
        info = self._artifact_info(path, run_dir)
        with self._transaction():
            self._insert_event(
                run_id,
                "artifact.registered",
                {"path": info[0], "sha256": info[1], "size": info[2]},
            )
            self._insert_artifact(run_id, info)
        return info[1]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AuditDB:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
