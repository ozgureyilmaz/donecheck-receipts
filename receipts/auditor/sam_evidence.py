"""SAM 3.1 box tokens and accurately labeled DOM fallback overlays."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from receipts.common import image_url
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


async def make_evidence(
    run_dir: Path, verdict: dict, steps: list[dict], config: Config, offline: bool = False
) -> dict:
    frame = next(
        (step for step in steps if step["idx"] == verdict.get("evidence_frame")),
        steps[-1] if steps else None,
    )
    evidence = {
        "frame": frame["idx"] if frame else None,
        "concept": verdict.get("evidence_concept", "booking details"),
        "boxes": [],
        "mask_svg": None,
        "source": "dom",
        "sam_status": "not_called",
        "overlay": None,
    }
    if frame is None:
        return evidence
    boxes = []
    if not offline and config.api_key:
        raw = {
            "response_id": None,
            "usage": None,
            "tokens": None,
            "model": config.sam_model,
            "output_text": "",
            "completed": False,
            "status": "not_started",
            "error_type": None,
        }
        chunks: list[str] = []
        try:
            async with model_client(config) as client:
                # SAM's output is a streamed special-token lane, per reading-segmentation docs.
                stream = await client.responses.create(
                    model=config.sam_model,
                    stream=True,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": evidence["concept"] or "booking details",
                                },
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
            record_model_result(run_dir, "sam_raw.json", raw)
        if raw["completed"]:
            boxes = parse_boxes(raw["output_text"])
            evidence["sam_status"] = "completed" if boxes else "zero_matches"
            if boxes:
                evidence["source"] = config.sam_model
        else:
            evidence["sam_status"] = f"unavailable:{raw['error_type'] or 'IncompleteStream'}"
    if not boxes:
        markers = frame.get("receipt_boxes", {})
        marker = verdict.get("evidence_marker")
        keys = (
            (marker,)
            if "evidence_marker" in verdict
            else ("error", "pending", "pnr", "date", "last")
        )
        candidate = next((markers[key] for key in keys if markers.get(key)), None)
        if candidate:
            boxes = [candidate]
        evidence["source"] = "dom"
    evidence["boxes"] = boxes
    target = run_dir / "evidence_overlay.png"
    draw_overlay(run_dir / frame["screenshot"], target, boxes)
    evidence["overlay"] = target.name
    return evidence
