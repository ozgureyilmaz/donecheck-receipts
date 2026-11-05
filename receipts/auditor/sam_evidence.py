"""SAM 3.1 box tokens and accurately labeled DOM fallback overlays."""

import re
from collections.abc import Mapping
from math import ceil, floor, isfinite
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from receipts.auditor.dom_checks import receipt_values
from receipts.common import image_url, write_json
from receipts.config import Config, model_client
from receipts.usage import record_model_result

BOX_RE = re.compile(
    r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=(?:\d+(?:\.\d+)?))?\|>"
)
RESPONSE_ID_RE = re.compile(r"resp_[A-Za-z0-9_-]{1,240}\Z")


def parse_boxes(text: str) -> list[dict]:
    boxes = []
    for match in BOX_RE.finditer(text):
        box = dict(zip(("x1", "y1", "x2", "y2", "w", "h"), map(int, match.groups()), strict=True))
        if 0 <= box["x1"] < box["x2"] <= box["w"] and 0 <= box["y1"] < box["y2"] <= box["h"]:
            boxes.append(box)
    return boxes


def draw_overlay(source: Path, target: Path, boxes: list[dict]) -> None:
    with Image.open(source) as original:
        image = original.convert("RGB")
        draw = ImageDraw.Draw(image)
        for box in boxes:
            scale_x, scale_y = image.width / box["w"], image.height / box["h"]
            rect = (
                box["x1"] * scale_x,
                box["y1"] * scale_y,
                box["x2"] * scale_x,
                box["y2"] * scale_y,
            )
            draw.rectangle(rect, outline="#f43f5e", width=5)
        image.save(target)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _response_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if RESPONSE_ID_RE.fullmatch(candidate) else None


def _usage_dict(value: Any) -> dict | None:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except (TypeError, ValueError):
            dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return None


def _record_response(raw: dict, response: Any) -> None:
    response_id = _response_id(_field(response, "id"))
    if response_id:
        raw["response_id"] = response_id
    usage = _usage_dict(_field(response, "usage"))
    if usage is not None:
        raw["usage"] = usage
        total_tokens = usage.get("total_tokens")
        raw["tokens"] = (
            total_tokens
            if isinstance(total_tokens, int) and not isinstance(total_tokens, bool)
            else None
        )
    response_model = _field(response, "model")
    if isinstance(response_model, str) and response_model.strip():
        raw["model"] = response_model.strip()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _declined_toast_target(
    run_dir: Path, steps: list[dict], preferred_frame: int | None = None
) -> dict | None:
    """Find the first visible declined-toast marker and map it to PNG pixels."""
    preferred = next((step for step in steps if step.get("idx") == preferred_frame), None)
    ordered_steps = ([preferred] if preferred is not None else []) + [
        step for step in steps if step is not preferred
    ]
    for step in ordered_steps:
        marker = step.get("receipt_boxes", {}).get("error")
        if not isinstance(marker, dict) or marker.get("visible") is not True:
            continue
        dom_name = step.get("dom")
        if not isinstance(dom_name, str):
            continue
        try:
            values = receipt_values((run_dir / dom_name).read_text(encoding="utf-8"))
        except OSError:
            continue
        error_text = values.get("error", "")
        if "payment declined" not in error_text.casefold():
            continue

        screenshot_name = step.get("screenshot")
        if not isinstance(screenshot_name, str):
            continue
        try:
            with Image.open(run_dir / screenshot_name) as image:
                width, height = image.size
        except OSError:
            continue

        marker_width, marker_height = marker.get("w"), marker.get("h")
        coordinates = [marker.get(key) for key in ("x1", "y1", "x2", "y2")]
        if (
            not _finite_number(marker_width)
            or not _finite_number(marker_height)
            or marker_width <= 0
            or marker_height <= 0
            or not all(_finite_number(value) for value in coordinates)
        ):
            continue
        x1, y1, x2, y2 = coordinates
        if not (0 <= x1 < x2 <= marker_width and 0 <= y1 < y2 <= marker_height):
            continue

        # Recorder geometry is in CSS pixels; SAM prompts use actual PNG pixels.
        scale_x, scale_y = width / marker_width, height / marker_height
        box = {
            "x1": floor(x1 * scale_x),
            "y1": floor(y1 * scale_y),
            "x2": ceil(x2 * scale_x) - 1,
            "y2": ceil(y2 * scale_y) - 1,
            "w": width,
            "h": height,
        }
        if not (0 <= box["x1"] < box["x2"] < width and 0 <= box["y1"] < box["y2"] < height):
            continue
        return {"step": step, "box": box, "error_text": error_text}
    return None


def _region_prompt(box: dict) -> str:
    return (
        f"<0f>0<|box;x1={box['x1']};y1={box['y1']};x2={box['x2']};"
        f"y2={box['y2']};w={box['w']};h={box['h']}|>"
    )


def _point_prompt(box: dict) -> str:
    width, height = box["w"], box["h"]
    center_x = (box["x1"] + box["x2"]) // 2
    center_y = (box["y1"] + box["y2"]) // 2
    positive = f"<|point;x={center_x};y={center_y};w={width};h={height}|>"
    candidates = (
        (center_x, box["y1"] - 1),
        (center_x, box["y2"] + 1),
        (box["x1"] - 1, center_y),
        (box["x2"] + 1, center_y),
    )
    for x, y in candidates:
        if (
            0 <= x < width
            and 0 <= y < height
            and not (box["x1"] <= x <= box["x2"] and box["y1"] <= y <= box["y2"])
        ):
            return f"<0f>0{positive}-<|point;x={x};y={y};w={width};h={height}|>"
    return f"<0f>0{positive}"


def _marker_candidate(frame: dict, keys: tuple[str, ...]) -> dict | None:
    markers = frame.get("receipt_boxes", {})
    return next(
        (markers[key] for key in keys if isinstance(markers.get(key), dict)),
        None,
    )


def _overlap_ratio(box: dict, target: dict) -> tuple[float, float]:
    """Return target coverage and IoU using inclusive provider box coordinates."""
    left = max(box["x1"], target["x1"])
    top = max(box["y1"], target["y1"])
    right = min(box["x2"] + 1, target["x2"] + 1)
    bottom = min(box["y2"] + 1, target["y2"] + 1)
    intersection = max(0, right - left) * max(0, bottom - top)
    box_area = (box["x2"] - box["x1"] + 1) * (box["y2"] - box["y1"] + 1)
    target_area = (target["x2"] - target["x1"] + 1) * (target["y2"] - target["y1"] + 1)
    union = box_area + target_area - intersection
    return intersection / target_area, intersection / union if union else 0.0


def _adequate_to_marker(boxes: list[dict], target: dict) -> dict | None:
    candidates = []
    for box in boxes:
        if box["w"] != target["w"] or box["h"] != target["h"]:
            continue
        coverage, iou = _overlap_ratio(box, target)
        if coverage >= 0.5 and iou >= 0.2:
            candidates.append((coverage * iou, box))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


async def _sam_attempt(
    run_dir: Path,
    frame: dict,
    config: Config,
    prompt_text: str,
    provenance: dict,
    raw_name: str,
    prompt_name: str,
) -> dict:
    raw = {
        "response_id": None,
        "usage": None,
        "tokens": None,
        "model": config.sam_model,
        "output_text": "",
        "completed": False,
        "status": "not_started",
        "error_type": None,
        "prompt_provenance": provenance,
    }
    write_json(run_dir / prompt_name, provenance)
    chunks: list[str] = []
    try:
        async with model_client(config) as client:
            stream = await client.responses.create(
                model=config.sam_model,
                stream=True,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt_text},
                            {
                                "type": "input_image",
                                "image_url": image_url(run_dir / frame["screenshot"]),
                            },
                        ],
                    }
                ],
            )
            raw["status"] = "streaming"
            async with stream:
                async for event in stream:
                    event_type = _field(event, "type")
                    response = _field(event, "response")
                    if response is not None:
                        _record_response(raw, response)
                    event_response_id = _response_id(_field(event, "response_id"))
                    if event_response_id and not raw["response_id"]:
                        raw["response_id"] = event_response_id
                    if event_type == "response.output_text.delta":
                        delta = _field(event, "delta")
                        if isinstance(delta, str):
                            chunks.append(delta)
                    elif event_type == "response.completed":
                        raw["completed"] = True
                        raw["status"] = "completed"
                        break
                    elif event_type in {"error", "response.failed", "response.incomplete"}:
                        raw["status"] = {
                            "response.failed": "failed",
                            "response.incomplete": "incomplete",
                            "error": "error",
                        }[event_type]
                        raw["error_type"] = {
                            "response.failed": "ProviderResponseFailed",
                            "response.incomplete": "ProviderResponseIncomplete",
                            "error": "ProviderStreamError",
                        }[event_type]
                        break
            if raw["status"] == "streaming":
                raw["status"] = "incomplete"
                raw["error_type"] = "MissingCompletion"
    except Exception as exc:
        if not raw["completed"]:
            raw["status"] = "stream_error"
            raw["error_type"] = type(exc).__name__
    finally:
        raw["output_text"] = "".join(chunks)
        record_model_result(run_dir, raw_name, raw)
    raw["boxes"] = parse_boxes(raw["output_text"]) if raw["completed"] else []
    return raw


async def make_evidence(
    run_dir: Path, verdict: dict, steps: list[dict], config: Config, offline: bool = False
) -> dict:
    frame = next(
        (step for step in steps if step["idx"] == verdict.get("evidence_frame")),
        steps[-1] if steps else None,
    )
    toast = _declined_toast_target(run_dir, steps, verdict.get("evidence_frame"))
    if toast:
        frame = toast["step"]
    evidence = {
        "frame": frame["idx"] if frame else None,
        "concept": verdict.get("evidence_concept", "booking details"),
        "boxes": [],
        "mask_svg": None,
        "source": "dom",
        "sam_status": "not_called",
        "overlay": None,
        "attempts": [],
    }
    if frame is None:
        return evidence
    provider_box = None
    if not offline and config.api_key:
        if toast:
            point_prompt = _point_prompt(toast["box"])
            prompts = [
                ("region", _region_prompt(toast["box"])),
                ("text", evidence["concept"] or "payment declined toast"),
                ("point", point_prompt),
            ]
        else:
            prompts = [("text", evidence["concept"] or "booking details")]

        for attempt_index, (kind, prompt_text) in enumerate(prompts):
            if toast:
                raw_name = f"sam_raw_{attempt_index:02d}_{kind}.json"
                prompt_name = f"sam_prompt_{attempt_index:02d}_{kind}.json"
            else:
                raw_name = "sam_raw.json"
                prompt_name = "sam_prompt.json"
            provenance = {
                "attempt": attempt_index,
                "kind": kind,
                "text": prompt_text,
                "frame": frame["idx"],
                "screenshot": frame["screenshot"],
                "source_marker": "error" if toast and kind in {"region", "point"} else None,
                "marker_box": toast["box"] if toast and kind in {"region", "point"} else None,
            }
            raw = await _sam_attempt(
                run_dir, frame, config, prompt_text, provenance, raw_name, prompt_name
            )
            boxes = raw["boxes"]
            selected_box = (
                _adequate_to_marker(boxes, toast["box"]) if toast else (boxes[0] if boxes else None)
            )
            evidence["attempts"].append(
                {
                    **provenance,
                    "response_artifact": raw_name,
                    "prompt_artifact": prompt_name,
                    "status": raw["status"],
                    "completed": raw["completed"],
                    "response_id": raw["response_id"],
                    "tokens": raw["tokens"],
                    "box_count": len(boxes),
                    "adequate_box": selected_box is not None,
                }
            )
            if not raw["completed"]:
                evidence["sam_status"] = f"unavailable:{raw['error_type'] or 'IncompleteStream'}"
                break
            if selected_box is not None:
                provider_box = selected_box
                evidence["sam_status"] = "completed"
                break
            evidence["sam_status"] = "inadequate_box" if boxes else "zero_matches"

    if provider_box is not None:
        evidence["boxes"] = [provider_box]
        evidence["source"] = config.sam_model
    else:
        marker_name = verdict.get("evidence_marker")
        fallback_keys = (
            (marker_name,)
            if isinstance(marker_name, str)
            else ("error", "pending", "pnr", "date", "last")
        )
        candidate = _marker_candidate(frame, fallback_keys)
        if candidate:
            evidence["boxes"] = [candidate]
        evidence["source"] = "dom"

    target = run_dir / "evidence_overlay.png"
    draw_overlay(run_dir / frame["screenshot"], target, evidence["boxes"])
    evidence["overlay"] = target.name
    return evidence
