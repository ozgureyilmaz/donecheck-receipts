"""Render a static, evidence-linked HTML report for a Receipts run."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_VERDICTS = {"TRUE_DONE", "FALSE_DONE", "UNSURE"}


def _is_report_file(path: Path) -> bool:
    return path.name == "report.html" or (
        path.name.startswith("report-") and path.suffix == ".html"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_steps(path: Path) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return steps
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            steps.append(value)
    return steps


def _safe_asset(root: Path, value: Any) -> str | None:
    """Return an encoded, run-local URL for an existing asset, never an outside path."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    path = Path(value.strip())
    try:
        resolved = (path if path.is_absolute() else root / path).resolve(strict=True)
        relative = resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return quote(relative.as_posix(), safe="/-_.~")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _verdict(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("verdict")
    if isinstance(value, str) and value.upper() in _VERDICTS:
        return value.upper()
    return "UNSURE"


def _goal_from_database(db_path: Path, run_id: str) -> dict[str, Any]:
    if not db_path.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{quote(str(db_path.resolve()))}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
            if not table:
                return {}
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            if "run_id" not in columns:
                return {}
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return {}
            for column in ("goal", "goal_json"):
                value = row[column] if column in row.keys() else None
                if isinstance(value, str):
                    try:
                        decoded = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(decoded, dict):
                        return decoded
                elif isinstance(value, dict):
                    return value
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return {}


def _database_safety_checks(db_path: Path, run_id: str) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{quote(str(db_path.resolve()))}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT payload_json FROM events WHERE run_id = ? AND kind = 'safety_check' ORDER BY seq",
                (run_id,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]
        finally:
            connection.close()
    except (sqlite3.Error, json.JSONDecodeError):
        return []


def _database_artifacts(db_path: Path, run_id: str) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{quote(str(db_path.resolve()))}?mode=ro", uri=True)
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='artifacts'"
            ).fetchone()
            if not table:
                return []
            columns = {row[1] for row in connection.execute("PRAGMA table_info(artifacts)")}
            needed = {"run_id", "path", "sha256"}
            if not needed.issubset(columns):
                return []
            select_columns = ["path", "sha256"]
            if "size" in columns:
                select_columns.append("size")
            rows = connection.execute(
                f"SELECT {', '.join(select_columns)} FROM artifacts WHERE run_id = ? ORDER BY path",
                (run_id,),
            ).fetchall()
            return [
                {
                    "path": str(row[0]),
                    "sha256": str(row[1]),
                    "size": row[2] if len(row) > 2 else None,
                }
                for row in rows
                if not _is_report_file(Path(str(row[0]).replace("\\", "/")))
            ]
        finally:
            connection.close()
    except sqlite3.Error:
        return []


def _computed_artifacts(root: Path) -> list[dict[str, Any]]:
    import hashlib

    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if _is_report_file(path) or not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(root.resolve())
            data = resolved.read_bytes()
        except (OSError, RuntimeError, ValueError):
            continue
        artifacts.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    return artifacts


def _image_dimensions(path: Path | None) -> tuple[float, float] | None:
    if path is None:
        return None
    try:
        from PIL import Image

        with Image.open(path) as image:
            return float(image.width), float(image.height)
    except (ImportError, OSError, ValueError):
        return None


def _prepare_boxes(
    boxes: Any, dimensions: tuple[float, float] | None
) -> tuple[dict[str, Any], ...]:
    if not isinstance(boxes, list):
        return ()
    first = next((box for box in boxes if isinstance(box, dict)), None)
    box_width = _number(first.get("w")) if first else None
    box_height = _number(first.get("h")) if first else None
    width, height = dimensions or (box_width or 1280.0, box_height or 800.0)
    prepared: list[dict[str, Any]] = []
    for box in boxes:
        if not isinstance(box, dict):
            continue
        x1, y1 = _number(box.get("x1")), _number(box.get("y1"))
        x2, y2 = _number(box.get("x2")), _number(box.get("y2"))
        source_w, source_h = _number(box.get("w")) or width, _number(box.get("h")) or height
        if None in (x1, y1, x2, y2) or source_w <= 0 or source_h <= 0:
            continue
        left = max(0.0, min(width, x1 * width / source_w))
        top = max(0.0, min(height, y1 * height / source_h))
        right = max(0.0, min(width, x2 * width / source_w))
        bottom = max(0.0, min(height, y2 * height / source_h))
        if right <= left or bottom <= top:
            continue
        prepared.append({"x": left, "y": top, "width": right - left, "height": bottom - top})
    return tuple(prepared)


def _make_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_report(
    run_dir: Path | str,
    db_path: Path | str | None = None,
    *,
    sam_evidence_file: Path | None = None,
) -> Path:
    """Render the original report or a separate SAM evidence follow-up."""
    root = Path(run_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {root}")
    database = Path(db_path).expanduser() if db_path is not None else Path("runs/receipts.sqlite")
    verdict_data = _read_json(root / "verdict.json")
    evidence = _read_json(sam_evidence_file or root / "evidence.json")
    metadata = _read_json(root / "metadata.json")
    claim_record = _read_json(root / "claim.json")
    steps = _read_steps(root / "steps.jsonl")
    run_id = root.name

    goal = _read_json(root / "goal.json") or _goal_from_database(database, run_id)
    try:
        claim = (root / "claim.txt").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        claim = ""

    combined = verdict_data.get("combined", {})
    if not isinstance(combined, dict):
        combined = {}
    evidence_frame = evidence.get("frame")
    if evidence_frame is None and sam_evidence_file is None:
        evidence_frame = combined.get("evidence_frame")
    try:
        evidence_frame = int(evidence_frame) if evidence_frame is not None else None
    except (TypeError, ValueError, OverflowError):
        evidence_frame = None
    evidence_t_s = _number(evidence.get("evidence_t_s"))
    if evidence_t_s is None and evidence_frame == combined.get("evidence_frame"):
        evidence_t_s = _number(combined.get("evidence_t_s"))

    prepared_steps: list[dict[str, Any]] = []
    for step in sorted(
        steps, key=lambda item: (_number(item.get("idx")) is None, _number(item.get("idx")) or 0)
    ):
        idx = step.get("idx")
        try:
            idx = int(idx)
        except (TypeError, ValueError, OverflowError):
            idx = len(prepared_steps)
        candidates = [step.get("screenshot"), f"screens/{idx:03d}.png", f"screens/{idx}.png"]
        screenshot = next(
            (asset for candidate in candidates if (asset := _safe_asset(root, candidate))), None
        )
        t_s = _number(step.get("t_s"))
        if evidence_t_s is None and idx == evidence_frame:
            evidence_t_s = t_s
        prepared_steps.append(
            {
                "idx": idx,
                "t_s": t_s,
                "time_label": f"{t_s:.1f}s" if t_s is not None else "time unknown",
                "action": str(step.get("action") or "Step recorded"),
                "screenshot": screenshot,
                "is_evidence": evidence_frame == idx,
            }
        )

    evidence_step = next((step for step in prepared_steps if step["idx"] == evidence_frame), None)
    if evidence_step is None and evidence_frame is not None:
        # Some recorders number frames independently from the step idx. Keep the evidence explicit.
        for step in prepared_steps:
            if str(step["idx"]) == str(evidence_frame):
                evidence_step = step
                evidence_step["is_evidence"] = True
                break
    if evidence_t_s is None and evidence_step:
        evidence_t_s = evidence_step["t_s"]

    evidence_image_path: Path | None = None
    if evidence_step and evidence_step["screenshot"]:
        evidence_image_url = evidence_step["screenshot"]
        evidence_image_path = (root / unquote(evidence_image_url)).resolve()
    else:
        evidence_image_url = None
    dimensions = _image_dimensions(evidence_image_path)
    if not dimensions:
        first_box = next((b for b in evidence.get("boxes", []) if isinstance(b, dict)), {})
        dimensions = (_number(first_box.get("w")) or 1280.0, _number(first_box.get("h")) or 800.0)
    overlay_boxes = _prepare_boxes(evidence.get("boxes"), dimensions)
    mask_asset = _safe_asset(root, evidence.get("mask_svg"))
    video_base = _safe_asset(root, "video.webm")
    video_asset = video_base
    if video_asset and evidence_t_s is not None:
        video_asset = f"{video_asset}#t={evidence_t_s:.3f}"

    dom = verdict_data.get("dom", {})
    if not isinstance(dom, dict):
        dom = {}
    raw_checks = dom.get("checks", verdict_data.get("checks", []))
    checks: list[dict[str, Any]] = []
    if isinstance(raw_checks, list):
        for check in raw_checks:
            if not isinstance(check, dict):
                continue
            passed = check.get("passed") is True
            checks.append(
                {
                    "name": str(check.get("name") or "Unnamed check"),
                    "passed": passed,
                    "status": "PASS" if passed else "FAIL",
                    "detail": str(check.get("detail") or "No detail supplied."),
                }
            )
    dom_value = dom.get("verdict")
    if dom_value is None and "passed" in dom:
        dom_value = "TRUE_DONE" if dom.get("passed") is True else "FALSE_DONE"

    sources = [
        {"name": "Spark", "verdict": _verdict(verdict_data.get("spark"))},
        {"name": "DOM", "verdict": _verdict(dom_value)},
        {"name": "Combined", "verdict": _verdict(combined)},
    ]
    artifacts = _database_artifacts(database, run_id)
    audit_label = "Recorded in SQLite audit trail"
    if not artifacts:
        artifacts = _computed_artifacts(root)
        audit_label = "Computed now from run files · not SQLite audit entries"

    template = _make_env().get_template("report.html")
    context = dict(
        run_id=run_id,
        actor_model=(metadata.get("models") or {}).get("agent")
        if metadata.get("agent") == "wonder"
        else None,
        safety_checks=_database_safety_checks(database, run_id),
        evidence_followup=sam_evidence_file is not None,
        goal=goal,
        claim=claim or "No agent claim captured.",
        claim_source=claim_record.get("source"),
        actor_persona=metadata.get("actor_persona"),
        claim_status=claim_record.get("claim_status"),
        overlay_asset=_safe_asset(root, evidence.get("overlay")),
        sources=sources,
        combined_verdict=_verdict(combined),
        rationale=str(combined.get("rationale") or "No combined rationale recorded."),
        checks=checks,
        steps=prepared_steps,
        evidence_step=evidence_step,
        evidence_frame=evidence_frame,
        evidence_t_s=evidence_t_s,
        evidence_image=evidence_image_url,
        evidence_concept=str(evidence.get("concept") or "Selected evidence frame"),
        evidence_source=str(evidence.get("source") or "Unspecified source"),
        overlay_boxes=overlay_boxes,
        overlay_width=dimensions[0],
        overlay_height=dimensions[1],
        mask_asset=mask_asset,
        video_asset=video_asset,
        video_base=video_base,
        artifacts=artifacts,
        audit_label=audit_label,
    )
    report_name = (
        f"report-{sam_evidence_file.parent.name}.html" if sam_evidence_file else "report.html"
    )
    output_path = root / report_name
    output_path.write_text(template.render(**context), encoding="utf-8")
    return output_path


__all__ = ["render_report"]
